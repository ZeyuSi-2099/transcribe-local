"""pp_runner 执行链单测（monkeypatch 全部外部依赖：blobstore/postprocess/accounts/claude）。

不连真实 DB/R2/Claude：_invoke_claude 打桩为按 prompt 写产物文件的假实现，
claude 闸打桩为内存开关。验证契约：逐步执行、撞顶回 queued、硬错重试 1 次、
产物/QC 上 R2、qc_fix_count 解析、price>0 才结账、失败不扣。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import redact_diff
from pipeline import pp_runner
from pipeline.pp_runner import parse_qc_fix_count, segments_to_qa


# ── 段落转问答体 ──

def test_segments_to_qa_speaker_lines_no_timestamps():
    segs = [{"t": "00:00:01", "sp": "主持人", "s": "你好"},
            {"t": "00:00:05", "sp": "被访者", "s": "在的"},
            {"t": "00:00:09", "sp": None, "s": "（无人声）"}]
    out = segments_to_qa(segs)
    assert out == "主持人：你好\n\n被访者：在的\n\n（无人声）"
    assert "00:00" not in out   # 无时间戳


# ── QC 报告解析「修复 N 处」──

def test_parse_qc_prefers_total_line():
    txt = "C1 发现 2 处并修复\nN1 修复 3 处\n\n共修复 5 处\n"
    assert parse_qc_fix_count(txt) == 5


def test_parse_qc_takes_last_total():
    assert parse_qc_fix_count("共修复 2 处\n……\n共修复 4 处") == 4


def test_parse_qc_falls_back_to_summing():
    assert parse_qc_fix_count("质检修复 3 处；另修复 2 处") == 5


def test_parse_qc_unparseable_is_zero():
    assert parse_qc_fix_count("全部通过") == 0
    assert parse_qc_fix_count("") == 0
    assert parse_qc_fix_count(None) == 0


# ── 执行链（打桩装配）──

class FakeGate:
    def __init__(self, allow=True):
        self.allow = allow
        self.acquired = 0
        self.released = 0

    def acquire(self):
        self.acquired += 1
        return self.allow

    def release(self):
        self.released += 1


class FakePP:
    """内存版 postprocess 状态机：记录调用轨迹。"""

    def __init__(self):
        self.calls = []
        self.done = None
        self.failed = None
        self.requeued = False
        self.degraded = []
        self.ds_cost = 0.0

    def update_step(self, job_id, step, idx):
        self.calls.append(("step", step, idx))

    def set_done(self, job_id, products, qc_fix_count, has_qc):
        self.done = dict(products=products, qc=qc_fix_count, has_qc=has_qc)
        return True

    def set_failed(self, job_id, failed_step, error, public=None):
        self.failed = dict(step=failed_step, error=error)
        return True

    def requeue_delayed(self, job_id, delay_min=None):
        self.requeued = True
        return True

    def mark_degraded(self, job_id, step):
        self.degraded.append(step)

    def add_ds_cost(self, job_id, cny):
        self.ds_cost += cny


class FakeBlob:
    def __init__(self, store=None):
        self.store = store or {}

    def get_bytes(self, key):
        return self.store[key]

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.store[key] = data

    def put_file(self, key, local_path):
        self.store[key] = Path(local_path).read_bytes()


def _pp_job(steps, price=0, ui_lang="de"):
    # ui_lang 默认取德语（不是中文也不是英文）：中文那份产物本来就是中文，用它做判据
    # 分不出「翻了」和「本来就是」；英文是回落值，也分不出。
    return {"job_id": "0f4b3f00-0000-0000-0000-00000000abcd", "user_email": "a@b.com",
            "steps": steps, "price_cents": price, "profile_name": None, "list_name": None,
            "ui_lang": ui_lang}


def _wire(monkeypatch, steps, price=0, rlist=None, gate=None):
    """装配一次 run() 的全部假依赖，返回 (pp_job, fakes)。"""
    pp = _pp_job(steps, price)
    jid = pp["job_id"]
    blob = FakeBlob({
        f"postprocess/{jid}/inputs.json": json.dumps({
            "steps": steps, "redactList": rlist,
        }).encode(),
        f"results_edited/{jid}.json": json.dumps(
            [{"t": "00:00:01", "sp": "主持人", "s": "你好"},
             {"t": "00:00:03", "sp": "被访者", "s": "在的"}]).encode(),
    })
    fake_pp = FakePP()
    settled = []
    monkeypatch.setattr(pp_runner, "blobstore", blob)
    # 转录稿的加载搬到了 app.redact_diff（api 侧的改动对比要算出同一份输入，两处不许分家），
    # 所以那条路上的 blobstore/jobstore 也得在**那个模块里**换掉——只 patch pp_runner 会真打 R2。
    monkeypatch.setattr(redact_diff, "blobstore", blob)
    monkeypatch.setattr(pp_runner, "postprocess", fake_pp)
    monkeypatch.setattr(pp_runner, "accounts", SimpleNamespace(
        settle_postprocess=lambda email, job_id, cents, file_name=None:
            settled.append((email, job_id, cents)) or True))
    js = SimpleNamespace(get_job=lambda j: SimpleNamespace(file_name="访谈.m4a", result_key="result/x.json"))
    monkeypatch.setattr(pp_runner, "jobstore", js)
    monkeypatch.setattr(redact_diff, "jobstore", js)
    g = gate or FakeGate()
    monkeypatch.setattr(pp_runner.claude_gate, "DbClaudeGate",
                        lambda slot, engine="claude": g)
    return pp, blob, fake_pp, settled, g


def _ok_claude(qc_by_step=None):
    """假 claude：按 prompt 解析输出路径并写产物 +（可选）QC 文件。"""
    qc_by_step = qc_by_step or {}

    def invoke(prompt):
        # ⚠️ 按**位置**取路径，不能用 parts[-1]：prompt 后面还跟着语言指令
        # （`/pp-<step> <输入> <输出> [清单]` + 换行 + 指令正文），取末尾会拿到指令里的字。
        parts = prompt.split()
        step = parts[0].removeprefix("/pp-")
        out = Path(pp_runner.VENDOR_ROOT) / parts[2]
        out.write_text(f"{step} 产物", encoding="utf-8")
        if step in qc_by_step:
            suffix = pp_runner._QC_SUFFIX[step]
            (out.parent / f"{step}{suffix}").write_text(qc_by_step[step], encoding="utf-8")
        return {"state": "ok", "window": None, "rate_limits": [], "message": ""}
    return invoke


def test_run_two_steps_uploads_products_and_qc_and_settles(monkeypatch):
    pp, blob, fake_pp, settled, gate = _wire(monkeypatch, ["narrate", "redact"], price=200)
    monkeypatch.setattr(pp_runner, "_invoke_claude", _ok_claude(
        {"narrate": "……\n共修复 3 处", "redact": "……\n共修复 2 处"}))
    pp_runner.run(pp)
    jid = pp["job_id"]
    assert blob.store[f"postprocess/{jid}/narrate.md"] == "narrate 产物".encode()
    assert blob.store[f"postprocess/{jid}/redact.md"] == "redact 产物".encode()
    assert f"postprocess/{jid}/qc.md" in blob.store
    assert fake_pp.done == {"products": ["narrate", "redact"], "qc": 5, "has_qc": True}
    assert fake_pp.calls == [("step", "narrate", 1), ("step", "redact", 2)]
    assert settled == [("a@b.com", jid, 200)]
    assert gate.acquired == 2 and gate.released == 2   # 并发闸按步取放


def test_中间产物留档_含送进去的输入稿(monkeypatch):
    """后处理的输入稿（segments_to_qa 的产物）此前跑完就删。输出不对时，
    第一件要看的就是**喂进去的到底长什么样**——不留档就查不了。"""
    pp, blob, _, _, _ = _wire(monkeypatch, ["narrate"], price=100)
    monkeypatch.setattr(pp_runner, "_invoke_claude", _ok_claude())
    pp_runner.run(pp)
    jid = pp["job_id"]
    keys = {k for k in blob.store if f"postprocess/{jid}/pipeline/" in k}
    assert f"postprocess/{jid}/pipeline/transcript.md" in keys, "送进第一步的输入稿没留"
    assert f"postprocess/{jid}/pipeline/narrate.md" in keys
    assert "你好" in blob.store[f"postprocess/{jid}/pipeline/transcript.md"].decode()


def test_失败时已跑完那几步的产物也要留(monkeypatch):
    """两步勾选、第二步炸了：此前直接 set_failed 返回，工作目录连同第一步的成果一起删掉，
    界面上只剩「失败了」三个字。**失败单最需要中间产物**。"""
    pp, blob, fake_pp, _, _ = _wire(monkeypatch, ["narrate", "redact"], price=200)
    ok = _ok_claude()

    def invoke(prompt):
        if prompt.startswith("/pp-redact"):
            return {"state": "error", "window": None, "rate_limits": [], "message": "炸了"}
        return ok(prompt)

    monkeypatch.setattr(pp_runner, "_invoke_claude", invoke)
    pp_runner.run(pp)
    jid = pp["job_id"]
    assert fake_pp.failed, "前提变了：第二步没被判失败"
    assert f"postprocess/{jid}/pipeline/narrate.md" in blob.store, "第一步的成果被删光了"
    assert f"postprocess/{jid}/pipeline/transcript.md" in blob.store


def test_留档挂了不许改变后处理的成败(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=100)
    monkeypatch.setattr(pp_runner, "_invoke_claude", _ok_claude())
    monkeypatch.setattr(blob, "put_file",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("R2 挂了")))
    pp_runner.run(pp)
    assert fake_pp.done and settled, "留档失败把成功单拖下水了"


def test_run_free_promo_price_zero_no_ledger(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    monkeypatch.setattr(pp_runner, "_invoke_claude", _ok_claude())
    pp_runner.run(pp)
    assert fake_pp.done is not None
    assert settled == []          # 促销期快照 0 → 不走账本


def test_run_no_qc_files_has_qc_false(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    monkeypatch.setattr(pp_runner, "_invoke_claude", _ok_claude())   # 不写 QC
    pp_runner.run(pp)
    assert fake_pp.done == {"products": ["narrate"], "qc": 0, "has_qc": False}
    assert f"postprocess/{pp['job_id']}/qc.md" not in blob.store


def test_run_capped_requeues_without_charge_or_fail(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate", "redact"], price=200)
    monkeypatch.setattr(pp_runner, "_invoke_claude",
                        lambda prompt: {"state": "capped", "window": "five_hour",
                                        "rate_limits": [], "message": ""})
    monkeypatch.setattr(pp_runner.pp_deepseek, "narrate", lambda i, o, directive="", ui_lang=None: "failed")
    pp_runner.run(pp)
    # 撞顶 → 先试降级；降级也没成 → 回 queued 延后重试（行为与接降级路之前完全一致）
    assert fake_pp.requeued is True
    assert fake_pp.failed is None and fake_pp.done is None
    assert settled == []                     # 没跑完不扣钱
    assert fake_pp.degraded == []            # 没成功就不许留痕


def test_run_capped_warning_with_output_counts_as_success(monkeypatch):
    # 订阅 allowed_warning 时调用其实成功了：产物在就照收，不许丢弃成功产物无限重跑
    # （2026-07-24 真机 95% 额度实测：narrate 成功却被判 capped 反复延后）
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    ok = _ok_claude()

    def warn_but_done(prompt):
        ok(prompt)   # 产物照写（调用实际成功）
        return {"state": "capped", "window": "five_hour", "rate_limits": [], "message": ""}

    monkeypatch.setattr(pp_runner, "_invoke_claude", warn_but_done)
    pp_runner.run(pp)
    assert fake_pp.done is not None and fake_pp.requeued is not True


def test_gate_full_without_degrade_path_requeues(monkeypatch):
    """闸满 + 该步没有 Flash 版 → 才回队列延后。有降级路的步一律直接降级，
    见 test_gate_full_also_degrades。

    ⚠️ 归类下架（2026-08-17）后，现存每一步都有降级路，所以这里**清空 _DEGRADE 造出**
    「没有降级路的步」。看着绕，但这条分支删不得——它同时是「降级本身失败」的退路
    （见 test_degrade_failure_falls_back_to_requeue），没测试守着会在下次重构里被当死代码删掉。"""
    monkeypatch.setattr(pp_runner, "_DEGRADE", {})
    pp, blob, fake_pp, settled, gate = _wire(monkeypatch, ["narrate"], price=200,
                                             gate=FakeGate(allow=False))
    called = []
    monkeypatch.setattr(pp_runner, "_invoke_claude", lambda p: called.append(p))
    pp_runner.run(pp)
    assert fake_pp.requeued is True and called == []   # 闸满：连 claude 都不调


def test_run_hard_error_retries_once_then_fails_unpaid(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=200)
    calls = []
    monkeypatch.setattr(pp_runner, "_invoke_claude",
                        lambda prompt: calls.append(prompt) or
                        {"state": "error", "window": None, "rate_limits": [], "message": ""})
    pp_runner.run(pp)
    assert len(calls) == 2                   # 首跑 + 硬错重试 1 次（契约）
    assert fake_pp.failed and fake_pp.failed["step"] == "narrate"
    assert settled == []                     # 失败不扣


def test_run_hard_error_then_success_on_retry(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    attempts = {"n": 0}
    ok = _ok_claude()

    def flaky(prompt):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"state": "error", "window": None, "rate_limits": [], "message": ""}
        return ok(prompt)

    monkeypatch.setattr(pp_runner, "_invoke_claude", flaky)
    pp_runner.run(pp)
    assert attempts["n"] == 2 and fake_pp.done is not None


def test_run_timeout_fails_without_retry(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=200)
    calls = []
    monkeypatch.setattr(pp_runner, "_invoke_claude",
                        lambda prompt: calls.append(prompt) or
                        {"state": "timeout", "window": None, "rate_limits": [], "message": ""})
    pp_runner.run(pp)
    assert len(calls) == 1                   # 超时不重试（再试又是一整个超时窗）
    assert fake_pp.failed and settled == []


def test_run_ok_but_missing_output_counts_as_hard_error(monkeypatch):
    # claude 报 ok 但产物没落盘（写错路径等）→ 按硬错处理：重试 1 次仍无 → failed
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    calls = []
    monkeypatch.setattr(pp_runner, "_invoke_claude",
                        lambda prompt: calls.append(prompt) or
                        {"state": "ok", "window": None, "rate_limits": [], "message": ""})
    pp_runner.run(pp)
    assert len(calls) == 2 and fake_pp.failed is not None


def test_run_prefers_edited_transcript(monkeypatch):
    # 修订版优先：results_edited/ 存在时吃修订稿（复核后的稿才是用户认可的稿）
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    seen = {}

    def spy(prompt):
        workdir_rel = prompt.split()[1]
        seen["input"] = (Path(pp_runner.VENDOR_ROOT) / workdir_rel).read_text(encoding="utf-8")
        return _ok_claude()(prompt)

    monkeypatch.setattr(pp_runner, "_invoke_claude", spy)
    pp_runner.run(pp)
    assert seen["input"] == "主持人：你好\n\n被访者：在的"


def test_run_missing_inputs_snapshot_fails(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    del blob.store[f"postprocess/{pp['job_id']}/inputs.json"]
    pp_runner.run(pp)
    assert fake_pp.failed is not None and fake_pp.done is None


def test_run_cleans_workdir(monkeypatch):
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=0)
    monkeypatch.setattr(pp_runner, "_invoke_claude", _ok_claude())
    pp_runner.run(pp)
    leftovers = list((pp_runner.VENDOR_ROOT / "Output").glob(f"pp_{pp['job_id'][:8]}_*"))
    assert leftovers == []   # 工作目录整个删掉，不在 vendor/Output 留垃圾


def test_step_prompt_shapes():
    assert pp_runner._step_prompt("narrate", "a.md", "b.md", None) == "/pp-narrate a.md b.md"
    assert pp_runner._step_prompt("redact", "a.md", "b.md", "l.md") == "/pp-redact a.md b.md l.md"
    assert pp_runner._step_prompt("redact", "a.md", "b.md", None) == "/pp-redact a.md b.md"


def test_capped_narrate_falls_back_to_deepseek_and_marks_it(monkeypatch):
    """撞顶 + 视角转换 → 走 DeepSeek 兜底，任务照常完成，**且库里留痕**。
    留痕是给运营查的：用户回头说「这份不如上次」时要能一眼看出这单降过级。"""
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=200)
    monkeypatch.setattr(pp_runner, "_invoke_claude",
                        lambda prompt: {"state": "capped", "window": "five_hour",
                                        "rate_limits": [], "message": ""})

    def fake_narrate(in_path, out_path, directive="", ui_lang=None):
        # ⚠️ 降级路也必须收到语言指令。只送 Claude 那条的症状是「有时候是对的」——
        # 而降级只在撞顶时才走，平时永远测不出来。
        assert "German" in directive, "降级路没收到语言指令"
        assert ui_lang == "de", "降级路没收到界面语言（报告模板是代码拼的，靠它出 8 门）"
        out_path.write_text("降级产出的正文", encoding="utf-8")
        Path(str(out_path)[:-3] + "_issues.md").write_text("共修复 3 处\n", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(pp_runner.pp_deepseek, "narrate", fake_narrate)
    pp_runner.run(pp)
    assert fake_pp.done is not None and fake_pp.requeued is False   # 降级成功 → 任务完成，不回队列
    assert fake_pp.degraded == ["narrate"]                          # 留痕
    assert fake_pp.done["qc"] == 3                                  # 降级路的问题清单也要能计数
    assert settled and settled[0][2] == 200                         # 跑完了就该收钱


def test_gate_full_also_degrades(monkeypatch):
    """**闸满与撞顶一样直接降级**（2026-08-13 改）：不让用户干等。

    此前闸满是「回队列等几分钟」，理由是「闸满≠额度耗尽，值得等」。推翻它的是两点：
    ① 加工是用户点了按钮在等着的，等几分钟拿 Opus 稿不如立刻拿 Flash 稿；
    ② Claude 订阅根本没有并发墙，这把闸拦的是烧额度速率——闸满与撞顶本就是
       同一件事的两种程度，没有分开处置的道理。"""
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=200,
                                          gate=FakeGate(allow=False))

    def fake_narrate(in_path, out_path, directive="", ui_lang=None):
        # ⚠️ 降级路也必须收到语言指令。只送 Claude 那条的症状是「有时候是对的」——
        # 而降级只在撞顶时才走，平时永远测不出来。
        assert "German" in directive, "降级路没收到语言指令"
        assert ui_lang == "de", "降级路没收到界面语言（报告模板是代码拼的，靠它出 8 门）"
        out_path.write_text("降级产出的正文", encoding="utf-8")
        Path(str(out_path)[:-3] + "_issues.md").write_text("共修复 1 处\n", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(pp_runner.pp_deepseek, "narrate", fake_narrate)
    pp_runner.run(pp)
    assert fake_pp.done is not None and fake_pp.requeued is False
    assert fake_pp.degraded == ["narrate"]


def test_capped_redact_falls_back_to_flash(monkeypatch):
    """撞顶 + 脱敏 → 走 pp_redact_ds 兜底（2026-08-13 上游验证：12 处 = Opus 12 处）。
    **保留词清单要一路传到降级路**——传丢了它就按纯智能识别跑，用户勾的词会被脱掉。"""
    pp, blob, fake_pp, settled, _ = _wire(
        monkeypatch, ["redact"], price=200,
        rlist={"id": "l1", "name": "清单", "content": "华为云\n昇腾"})
    monkeypatch.setattr(pp_runner, "_invoke_claude",
                        lambda prompt: {"state": "capped", "window": "week",
                                        "rate_limits": [], "message": ""})
    seen = {}

    def fake_redact(in_path, out_path, keep_path, directive="", ui_lang=None):
        assert "German" in directive, "降级路没收到语言指令"
        assert ui_lang == "de", "降级路没收到界面语言（报告模板是代码拼的，靠它出 8 门）"
        seen["keep"] = Path(keep_path).read_text(encoding="utf-8") if keep_path else None
        out_path.write_text("脱敏后的正文", encoding="utf-8")
        Path(str(out_path)[:-3] + "_QC报告.md").write_text("共修复 2 处\n", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(pp_runner.pp_redact_ds, "redact", fake_redact)
    monkeypatch.setattr(pp_runner.pp_deepseek, "usage_cny", lambda: 0.0954)
    pp_runner.run(pp)
    assert fake_pp.ds_cost == 0.0954      # 降级路的花费必须记账（运营舱唯一数据源）
    assert seen["keep"] == "华为云\n昇腾"
    assert fake_pp.done is not None and fake_pp.degraded == ["redact"]
    assert fake_pp.done["qc"] == 2


def test_degrade_failure_falls_back_to_requeue(monkeypatch):
    """降级路也没成功 → 退回延后重试，**不判任务失败**：兜底挂了不该比没兜底更糟。"""
    pp, blob, fake_pp, settled, _ = _wire(monkeypatch, ["narrate"], price=200,
                                          gate=FakeGate(allow=False))
    monkeypatch.setattr(pp_runner.pp_deepseek, "narrate", lambda i, o, directive="", ui_lang=None: "failed")
    monkeypatch.setattr(pp_runner.pp_deepseek, "usage_cny", lambda: 0.02)
    pp_runner.run(pp)
    assert fake_pp.requeued is True and fake_pp.failed is None and fake_pp.degraded == []
    assert fake_pp.ds_cost == 0.02        # **降级失败也要记账**——钱已经花出去了

from pathlib import Path

from pipeline import merge


def test_find_latest_output_picks_newest_non_report(tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    (out / "foo_P3_Merge_0602_1200.md").write_text("old")
    newest = out / "foo_P3_Merge_0602_1300.md"
    newest.write_text("new")
    (out / "foo_P3_Merge_0602_1300_report.md").write_text("report")
    (out / "foo_P3_Merge_0602_1300_raw.md").write_text("raw")   # 失败残骸，须排除
    got = merge.find_latest_output(out, "_P3_Merge_")
    assert Path(got) == newest


def test_run_merge_invokes_subprocess_and_returns_output(monkeypatch, tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    produced = out / "foo_P3_Merge_0602_1300.md"

    calls = {}

    def fake_run(cmd, cwd, env, **kw):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        produced.write_text("[00:01.0 - 00:02.0] M: 合并稿")

        class R:  # 模拟 CompletedProcess
            returncode = 0

        return R()

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    result, cost = merge.run_merge(
        match_file=str(tmp_path / "foo_P2_Match_0602_1200.md"),
        vendor_root=tmp_path,
    )
    assert Path(result) == produced and cost == 0.0   # 桩无 stdout → 成本 0
    assert calls["cwd"] == str(tmp_path)
    assert calls["cmd"][1:] == [merge.P3_SCRIPT, str(tmp_path / "foo_P2_Match_0602_1200.md"),
                                *merge.P3_ARGS]


def test_parse_p3_cost_usd():
    assert merge._parse_p3_cost_usd("…\n__P3_DS_USAGE__ out=1234 cost=0.0192\n完成") == 0.0192
    assert merge._parse_p3_cost_usd("无标记") == 0.0
    assert merge._parse_p3_cost_usd("") == 0.0


def test_run_merge_parses_p3_cost_from_stdout(monkeypatch, tmp_path):
    # P3 子进程 stdout 含 __P3_DS_USAGE__ ... cost=<USD> → run_merge 第二个返回值为该 USD
    out = tmp_path / "Output"
    out.mkdir()
    (out / "foo_P3_Merge_0602_1300.md").write_text("稿")

    def fake_run(cmd, cwd, env, **kw):
        class R:
            returncode = 0
            stdout = "转录…\n__P3_DS_USAGE__ out=1234 cost=0.0192\n完成"
            stderr = ""
        return R()

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    _, cost = merge.run_merge(match_file="x.md", vendor_root=tmp_path)
    assert cost == 0.0192


def test_run_merge_raises_on_nonzero_exit(monkeypatch, tmp_path):
    (tmp_path / "Output").mkdir()

    def fake_run(cmd, cwd, env, **kw):
        class R:
            returncode = 2

        return R()

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    import pytest

    with pytest.raises(RuntimeError, match="DeepSeek"):
        merge.run_merge(match_file="x.md", vendor_root=tmp_path)


def test_run_merge_injects_glossary_via_env_file(monkeypatch, tmp_path):
    # 用户术语库 → 写临时文件 + env USER_TERM_FILE 传子进程；用完即删
    out = tmp_path / "Output"
    out.mkdir()
    produced = out / "foo_P3_Merge_0602_1300.md"
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        tf = env.get("USER_TERM_FILE")
        seen["term_file"] = tf
        seen["content"] = Path(tf).read_text(encoding="utf-8") if tf else None
        produced.write_text("[00:01.0 - 00:02.0] M: 稿")

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    merge.run_merge(match_file="x.md", vendor_root=tmp_path, glossary_text="FD ｜ 履约分销")
    assert seen["content"] == "FD ｜ 履约分销"
    assert not Path(seen["term_file"]).exists()  # 用完删


def test_run_merge_without_glossary_sets_no_env(monkeypatch, tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    produced = out / "foo_P3_Merge_0602_1300.md"
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        seen["term_file"] = env.get("USER_TERM_FILE")
        produced.write_text("[00:01.0 - 00:02.0] M: 稿")

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    merge.run_merge(match_file="x.md", vendor_root=tmp_path, glossary_text="")
    assert seen["term_file"] is None


def test_run_merge_timeout_raises_clear_error(monkeypatch, tmp_path):
    # DeepSeek 路是最后兜底，无处再降级：子进程必须带 timeout，超时抛清晰错误
    # → orchestrator/worker 走正常失败链（judge failed + 退预扣），杜绝真挂死永不回收
    import pytest as _pytest

    def fake_run(cmd, cwd, env, **kw):
        assert kw.get("timeout"), "subprocess.run 必须带 timeout"
        raise merge.subprocess.TimeoutExpired(cmd=cmd, timeout=kw["timeout"])

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    with _pytest.raises(RuntimeError, match="超时"):
        merge.run_merge(match_file="x.md", vendor_root=tmp_path)


# ---- 分批版接进生产的四条契约（2026-08-10）----
# 这四条都是「错了不会报错、只会静默出问题」的那类，所以必须由测试守着。
import re as _re
from pathlib import Path as _Path

_BATCHED = _Path(__file__).resolve().parents[1] / "pipeline/vendor/Workflow/Phase3_Merge_DeepSeek_Batched.py"
_SINGLE = _Path(__file__).resolve().parents[1] / "pipeline/vendor/Workflow/Phase3_Merge_DeepSeek.py"


def _code(path):
    """只取真代码：注释里正解释着「为什么不能这么写」，连注释一起 grep 会自己撞自己。"""
    return "\n".join(_re.sub(r"#.*$", "", ln) for ln in path.read_text(encoding="utf-8").split("\n"))


def test_分批版产物名能被生产的查找契约命中():
    """产物名必须含公共标记 `_P3_Merge_`，否则 find_latest_output 找不到 → 每单 FileNotFoundError。

    上游那套 `_P3_MergeBatched_` 里 "Merge" 后面接的是 "B" 不是 "_"，正是这条要挡的。
    """
    src = _code(_BATCHED)
    assert "_P3_MergeBatched_" not in src, "产物名破了 `_P3_Merge_` 公共标记"
    # 按脚本的命名规则造两个真实文件名，验证 glob 模式确实命中
    for tag in ("PRO", "FLASH"):
        name = f"zh_x_P3_Merge_{tag}_0810_1200.md"
        assert _Path(name).match("*_P3_Merge_*.md"), f"{name} 命不中查找契约"


def test_分批版会读用户术语库(tmp_path):
    """术语库经 env USER_TERM_FILE 注入，不能像上游那样写死 build_system("")——写死等于
    用户在术语库页维护的内容对分批版完全不生效，而且不会有任何报错。"""
    src = _code(_BATCHED)
    assert 'build_system("")' not in src, "术语库被写死成空，用户术语库不生效"
    assert "USER_TERM_FILE" in src


def test_分批版会吐成本标记():
    """生产靠 stdout 的 `__P3_DS_USAGE__ ... cost=<USD>` 记账；少了它成本静默记 0。
    这里连解析一起验，确保格式与 _parse_p3_cost_usd 的正则真的对得上。"""
    src = _code(_BATCHED)
    assert "__P3_DS_USAGE__" in src
    assert merge._parse_p3_cost_usd("__P3_DS_USAGE__ model=deepseek-v4-pro out=123 cost=0.2800") == 0.28


def test_计价按模型分档():
    """flash 的单价是 pro 的 1/3；只有 pro 一档时 flash 成本会被虚报 3 倍。"""
    src = _code(_SINGLE)
    assert "_PRICE" in src and '"flash"' in src, "计价表仍是单档"


def test_生产走分批版且带定档参数():
    """P3_SCRIPT 必须指向分批版，且带上实测定下的四个参数——漏一个就是另一套行为。"""
    assert merge.P3_SCRIPT.endswith("Phase3_Merge_DeepSeek_Batched.py")
    for flag in ("--round-tokens", "2100", "--conc", "40", "--conflicts", "--overlap", "1"):
        assert flag in merge.P3_ARGS, f"缺参数 {flag}"


def test_三档引擎都能从产物名解析出来():
    """metrics.cost.p3_engine 靠产物名解析。三条路的命名都要能认出来，认不出记 unknown。"""
    pat = r"_P3_Merge_([A-Za-z0-9]+)_"
    cases = {"zh_a_P3_Merge_OPUS_0810_1200.md": "opus",
             "zh_a_P3_Merge_PRO_0810_1200.md": "pro",
             "zh_a_P3_Merge_FLASH_0810_1200.md": "flash",
             "zh_a_P3_Merge_DS_0810_1200.md": "ds"}
    for name, want in cases.items():
        m = _re.search(pat, name)
        assert m and m.group(1).lower() == want, name


# ── 内置术语库禁令（2026-08-22）─────────────────────────────────────────────
# vendor 根目录下曾有一本 `Config/Config_Term.md`（为某个具体项目建的行业专名库）。
# claude -p 的工作目录正是 vendor 根、Read 工具放开，而 skill 第一句是「先查本次任务的
# 术语库」——它自己读了。于是**用户一本库都没选，却吃到一份跟他无关的行业词表**，
# 近音词被悄悄拽向那个行业的写法，界面上还显示成「引擎证据」。
# 这类文件放回来不会有任何报错、也不会有测试变红——所以把判据钉在这里。
def test_vendor_has_no_builtin_glossary():
    """vendor 里不许再出现「不属于任何用户、却会被 P3 读到」的术语库。

    判据是**内容**不是文件名：改名成 Terms.md / 专名表.md 照样会被读到。
    凡 Config/ 下的 md 里出现术语库的行格式（`- 词 ｜ 释义`）即判失败。"""
    from pathlib import Path
    import re
    cfg = Path(__file__).resolve().parents[1] / "pipeline" / "vendor" / "Config"
    row = re.compile(r"^- .+｜.+$", re.M)
    offenders = [p.name for p in cfg.rglob("*.md") if len(row.findall(p.read_text(encoding="utf-8"))) >= 5]
    assert offenders == [], f"vendor/Config 下出现了内置术语库：{offenders}——术语库只许由用户提供"


def test_skill_prompt_does_not_point_at_a_builtin_glossary():
    """提示词里不许再提任何内置术语库文件——提了就是在告诉模型「还有另一本可以读」。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "pipeline" / "skill_merge.py").read_text(encoding="utf-8")
    body = src.split("def _build_prompt", 1)[1].split("\ndef ", 1)[0]
    prompt_lines = [ln for ln in body.splitlines() if "prompt" in ln and '"' in ln]
    assert not any("Config_Term" in ln for ln in prompt_lines), "提示词仍在指向内置术语库"


def test_分批版的存疑围栏是必答项():
    """「本批没有存疑」与「本批忘了写」必须在输出里长得不一样（2026-08-26）。

    此前提示词允许「没有 `[❓]` 就连围栏一起省略」，于是两者在输出里一模一样、脚本分不出来——
    实测某单终稿打了 3 个 `[❓]`、报告只登记 2 条，少的那一处交付前标记又被剥掉，两头落空。
    现在围栏必须每批都出，没有就写「无」；缺围栏而正文有 `[❓]` 时打印告警。
    只报不重跑：整轮重跑是按段计价里最亏的做法（台账实测能烧掉全单 22% 的输出），
    而漏掉的那一处已由 pipeline/review.py 的存疑对账兜住。
    """
    raw = _BATCHED.read_text(encoding="utf-8")
    assert "本批没有 `[❓]` 就连围栏一起省略" not in raw, "旧措辞回来了：'没有'与'忘了'又分不开"
    assert "都必须**用下面这行围栏" in raw, "围栏不再是必答项"
    src = _code(_BATCHED)
    assert '"===DOUBT===" not in out' in src, "缺围栏的告警没了"


# ── token 用量埋点（2026-08-31，批次 A·H-2）──────────────────────────────────
# 判据都盯着「缓存命中率算不算得出来」，不是「有没有打印这一行」：cost= 一直都在，
# 少的是 hit/miss。少了它们，成本面板照常显示金额，却回答不了「为什么是这个数」。

def test_两个版本都吐出_hit_miss_明细():
    """分批版与它的回滚路（单次版）必须报同一组字段。
    只改一边的症状是「一回滚，成本面板少半边」——而回滚当天没人会想到这件事。"""
    for path in (_BATCHED, _SINGLE):
        src = _code(path)
        i = src.find("__P3_DS_USAGE__")
        assert i > 0, f"{path.name} 不再打印用量行"
        # print 可能折行，所以取到该语句的 flush=True 收尾为止，别按行切
        stmt = src[i:src.index("flush=True)", i)]
        for field in ("model=", "hit=", "miss=", "out=", "cost="):
            assert field in stmt, f"{path.name} 的用量行缺 {field}"


def test_解析_token_明细():
    got = merge.parse_p3_usage(
        "…\n__P3_DS_USAGE__ model=deepseek-v4-flash hit=812000 miss=43000 out=91000 cost=0.1900\n完成")
    assert got == {"hit": 812000, "miss": 43000, "out": 91000}
    # 成本解析不受影响（老正则、老契约）
    assert merge._parse_p3_cost_usd(
        "__P3_DS_USAGE__ model=x hit=1 miss=2 out=3 cost=0.5000") == 0.5


def test_解析不到就返回空_不补零():
    """「没这个数」和「这个数是 0」是两回事：补零会让命中率算出一个假的 0%。"""
    assert merge.parse_p3_usage("什么都没有") == {}
    assert merge.parse_p3_usage("") == {}
    # 老格式（还没补字段的存量日志）：能拿几个是几个，不整行作废
    assert merge.parse_p3_usage("__P3_DS_USAGE__ out=123 cost=0.1") == {"out": 123}


def test_用量是进程级的_必须能清零(monkeypatch, tmp_path):
    """走 Claude 的单根本不进 run_merge。不清零的话它会读到**上一单 DeepSeek** 的数字——
    账面不是「未知」而是「别人的」（同 P1LLM_STATUS_FILE 那个坑的形状）。"""
    out = tmp_path / "Output"
    out.mkdir()
    (out / "a_P3_Merge_FLASH_0831_1200.md").write_text("终稿")

    class P:
        returncode = 0
        stdout = "__P3_DS_USAGE__ model=m hit=7 miss=8 out=9 cost=0.1"
        stderr = ""
    monkeypatch.setattr(merge.subprocess, "run", lambda *a, **k: P())
    merge.run_merge(match_file="x.md", vendor_root=tmp_path)
    assert merge.last_usage() == {"hit": 7, "miss": 8, "out": 9}
    merge.reset_usage()
    assert merge.last_usage() == {}


def test_orchestrator_在调_p3_之前清零():
    """顺序错了照常出稿、只是数字悄悄归到别人头上——所以只能用源码顺序钉。"""
    src = _code(_Path(__file__).resolve().parents[1] / "pipeline/orchestrator.py")
    i_reset, i_call = src.find("merge_mod.reset_usage()"), src.find("merged = p3_merge(")
    assert i_reset >= 0, "orchestrator 没有清零 token 用量"
    assert i_call >= 0, "找不到 P3 调用点（改了写法就把这条守卫一起改）"
    assert i_reset < i_call, "reset_usage 必须在 p3_merge 之前调用"
    assert "merge_mod.last_usage()" in src, "orchestrator 没有把 token 明细记进 metrics"

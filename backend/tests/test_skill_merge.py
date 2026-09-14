"""T1–T4：P3·Claude+Skill 引擎 + 撞顶分类 + 四形态派单兜底。

撞顶检测两形态都认：本机/claude.ai 登录态靠 `rate_limit_event` 三态；
容器/token 模式（T7.1 实测无 rate_limit_event）靠 is_error+api_error_status(429)+关键词。
两路只差模型；默认引擎 claude，撞顶/并发/硬错全部降级 DeepSeek。
"""
import json
from pathlib import Path

import pytest

from pipeline import skill_merge


# ---- 构造 claude -p --output-format json 的真实形态（数组：system/assistant/rate_limit_event/result）----
def _claude_json(is_error=False, rate_limits=None, cost=0.05):
    arr = [{"type": "system", "subtype": "init"}, {"type": "assistant"}]
    for rl in (rate_limits or []):
        arr.append({
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": rl["status"],
                "rateLimitType": rl["window"],
                "resetsAt": rl.get("resets_at", 1782021600),
                "isUsingOverage": False,
            },
        })
    arr.append({
        "type": "result", "subtype": "error" if is_error else "success",
        "is_error": is_error, "api_error_status": None, "total_cost_usd": cost,
    })
    return json.dumps(arr, ensure_ascii=False)


# ---- 容器 token 模式形态（T7.1 实测：单个 result 对象，无 rate_limit_event）----
def _claude_container(is_error=False, api_error_status=None, result_text="融合完成", cost=0.35):
    return json.dumps({
        "type": "result", "subtype": "error" if is_error else "success",
        "is_error": is_error, "api_error_status": api_error_status,
        "result": result_text, "total_cost_usd": cost,
    }, ensure_ascii=False)


# ============ T2 · classify_claude_result ============

def test_classify_ok_five_hour_allowed():
    out = _claude_json(rate_limits=[{"status": "allowed", "window": "five_hour"}])
    r = skill_merge.classify_claude_result(out, 0)
    assert r["state"] == "ok"
    assert {"window": "five_hour", "status": "allowed", "resets_at": 1782021600} in r["rate_limits"]


def test_classify_capped_five_hour_warning():
    # 5h 临近 → 预判性撞顶（保守护住 5h 窗口）
    out = _claude_json(rate_limits=[{"status": "allowed_warning", "window": "five_hour"}])
    r = skill_merge.classify_claude_result(out, 0)
    assert r["state"] == "capped" and r["window"] == "five_hour"


def test_classify_capped_five_hour_rejected():
    out = _claude_json(is_error=True, rate_limits=[{"status": "rejected", "window": "five_hour"}])
    r = skill_merge.classify_claude_result(out, 1)
    assert r["state"] == "capped" and r["window"] == "five_hour"


def test_classify_capped_seven_day_rejected():
    out = _claude_json(is_error=True, rate_limits=[{"status": "rejected", "window": "seven_day"}])
    r = skill_merge.classify_claude_result(out, 1)
    assert r["state"] == "capped" and r["window"] == "seven_day"


def test_classify_seven_day_warning_is_ok():
    # 周额度要尽量用满：warning 不算撞顶
    out = _claude_json(rate_limits=[{"status": "allowed_warning", "window": "seven_day"}])
    r = skill_merge.classify_claude_result(out, 0)
    assert r["state"] == "ok"


def test_classify_both_windows_week_rejected_wins():
    out = _claude_json(is_error=True, rate_limits=[
        {"status": "allowed", "window": "five_hour"},
        {"status": "rejected", "window": "seven_day"},
    ])
    r = skill_merge.classify_claude_result(out, 1)
    assert r["state"] == "capped" and r["window"] == "seven_day"
    # 两个窗口都进 rate_limits（供缓存）
    assert {x["window"] for x in r["rate_limits"]} == {"five_hour", "seven_day"}


def test_classify_hard_error_when_is_error_no_cap():
    out = _claude_json(is_error=True, rate_limits=[{"status": "allowed", "window": "five_hour"}])
    r = skill_merge.classify_claude_result(out, 1)
    assert r["state"] == "error"


def test_classify_non_json_is_error():
    assert skill_merge.classify_claude_result("乱七八糟不是JSON", 1)["state"] == "error"
    assert skill_merge.classify_claude_result("", 1)["state"] == "error"


# ---- T7.1 实测：容器 token 模式无 rate_limit_event，撞顶靠 is_error+429+关键词 ----

def test_classify_container_ok_single_result():
    # 容器正常：单 result 对象、is_error=false → ok
    out = _claude_container(is_error=False)
    assert skill_merge.classify_claude_result(out, 0)["state"] == "ok"


def test_classify_container_capped_429():
    # 容器撞顶：is_error + api_error_status 含 429 → capped（不能误判 error 走重试）
    out = _claude_container(is_error=True, api_error_status="429 Too Many Requests")
    assert skill_merge.classify_claude_result(out, 1)["state"] == "capped"


def test_classify_container_capped_usage_limit_text():
    # 容器撞顶：错误文本含 usage limit → capped
    out = _claude_container(is_error=True, result_text="Error: usage limit reached, please try later")
    assert skill_merge.classify_claude_result(out, 1)["state"] == "capped"


def test_classify_container_capped_weekly_text_window():
    # 文本提到 weekly → window=seven_day
    out = _claude_container(is_error=True, result_text="weekly rate limit exceeded")
    r = skill_merge.classify_claude_result(out, 1)
    assert r["state"] == "capped" and r["window"] == "seven_day"


def test_classify_container_hard_error_non_cap_stays_error():
    # 容器非撞顶硬错（500，无撞顶关键词）→ error（走重试）
    out = _claude_container(is_error=True, api_error_status="500 Internal Server Error")
    assert skill_merge.classify_claude_result(out, 1)["state"] == "error"


# ============ T1 · build_claude_cmd / run_skill_merge ============

def test_build_claude_cmd_locks_model_effort_and_keeps_websearch():
    cmd = skill_merge.build_claude_cmd("/multi-asr-merge x.md")
    s = " ".join(cmd)
    assert cmd[:5] == ["env", "-u", "ANTHROPIC_API_KEY", "-u", "ANTHROPIC_AUTH_TOKEN"]  # 强制订阅 OAuth
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "opus"          # Opus 4.8
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "medium"      # 锁 medium
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    assert "--strict-mcp-config" in cmd                                          # 去 MCP 省额度
    assert "WebSearch" in s                                                      # 联网核实保留


def test_run_skill_merge_ok_returns_new_output(monkeypatch, tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    (out / "old_P3_Merge_0101_0000.md").write_text("旧稿")  # 已存在，不该被当成本次产物
    produced = out / "foo_P3_Merge_0620_1720.md"

    def fake_run(cmd, cwd, env, **kw):
        produced.write_text("[00:01.0 - 00:02.0] M: Claude 合并稿")

        class R:
            returncode = 0
            stdout = _claude_json(rate_limits=[{"status": "allowed", "window": "five_hour"}])
            stderr = ""
        return R()

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    path, cls = skill_merge.run_skill_merge(
        match_file=str(tmp_path / "foo_P2_Match_0620_1700.md"), vendor_root=tmp_path)
    assert Path(path) == produced and cls["state"] == "ok"


def test_run_skill_merge_capped_returns_none(monkeypatch, tmp_path):
    (tmp_path / "Output").mkdir()

    def fake_run(cmd, cwd, env, **kw):
        class R:
            returncode = 1
            stdout = _claude_json(is_error=True, rate_limits=[{"status": "rejected", "window": "five_hour"}])
            stderr = ""
        return R()

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    path, cls = skill_merge.run_skill_merge(match_file="x.md", vendor_root=tmp_path)
    assert path is None and cls["state"] == "capped" and cls["window"] == "five_hour"


def test_run_skill_merge_injects_glossary_into_prompt(monkeypatch, tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        seen["cmd"] = cmd
        (out / "foo_P3_Merge_0620_1720.md").write_text("稿")

        class R:
            returncode = 0
            stdout = _claude_json(rate_limits=[{"status": "allowed", "window": "five_hour"}])
            stderr = ""
        return R()

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    skill_merge.run_skill_merge(match_file="x.md", vendor_root=tmp_path, glossary_text="FD ｜ 履约分销")
    prompt = next(a for a in seen["cmd"] if a.startswith("/multi-asr-merge"))
    assert "FD ｜ 履约分销" in prompt   # 用户术语库进了提示词


def test_run_skill_merge_no_glossary_clean_prompt(monkeypatch, tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        seen["cmd"] = cmd
        (out / "foo_P3_Merge_0620_1720.md").write_text("稿")

        class R:
            returncode = 0
            stdout = _claude_json(rate_limits=[{"status": "allowed", "window": "five_hour"}])
            stderr = ""
        return R()

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    skill_merge.run_skill_merge(match_file="x.md", vendor_root=tmp_path, glossary_text="")
    prompt = next(a for a in seen["cmd"] if a.startswith("/multi-asr-merge"))
    # ⚠️ 判据是**注入块的抬头**，不是「出现了『术语库』三个字」（2026-08-30 订正）：
    # 语言指令里本来就要提「靠术语库命中定的字」那一档，按字面搜会把一条正确的指令判红，
    # 逼人去改指令措辞——那样这条守卫就废了。它要守的一直是「空库时不注入库正文」。
    assert "【本任务用户术语库" not in prompt   # 空库不注入


# ============ T3 · p3_merge 派单 + 四形态兜底 ============

@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    skill_merge.reset_state()
    yield
    skill_merge.reset_state()


def _patch_engines(monkeypatch, skill_ret=None, skill_exc=None):
    """打桩两路引擎，返回调用记账。"""
    calls = {"skill": 0, "deepseek": 0}

    def fake_skill(match_file, vendor_root, glossary_text="", ui_lang=None):
        calls["skill"] += 1
        if skill_exc:
            raise skill_exc
        return skill_ret

    def fake_deepseek(match_file, vendor_root, glossary_text="", ui_lang=None):
        calls["deepseek"] += 1
        return ("DS_终稿.md", 0.007)

    monkeypatch.setattr(skill_merge, "run_skill_merge", fake_skill)
    monkeypatch.setattr(skill_merge.merge, "run_merge", fake_deepseek)
    return calls


def test_p3_merge_engine_deepseek_skips_claude(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "deepseek")
    calls = _patch_engines(monkeypatch, skill_ret=("不该用.md", {"state": "ok"}))
    path, cost = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md" and cost == 0.007
    assert calls["skill"] == 0 and calls["deepseek"] == 1


def test_p3_merge_claude_ok(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    cls = {"state": "ok", "rate_limits": [{"window": "five_hour", "status": "allowed", "resets_at": 9999999999}]}
    calls = _patch_engines(monkeypatch, skill_ret=("CL_终稿.md", cls))
    path, cost = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "CL_终稿.md" and cost == 0.0
    assert calls["skill"] == 1 and calls["deepseek"] == 0


def test_p3_merge_claude_capped_falls_back_and_counts(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    cls = {"state": "capped", "window": "five_hour",
           "rate_limits": [{"window": "five_hour", "status": "rejected", "resets_at": 9999999999}]}
    calls = _patch_engines(monkeypatch, skill_ret=(None, cls))
    path, cost = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"                       # 降级 DeepSeek
    assert calls["skill"] == 1 and calls["deepseek"] == 1
    assert skill_merge.get_counters()["cap_5h"] == 1


def test_p3_merge_claude_hard_error_retries_then_falls_back(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    monkeypatch.setenv("P3_CLAUDE_RETRY", "2")
    skill_merge.reset_state()
    cls = {"state": "error", "rate_limits": []}
    calls = _patch_engines(monkeypatch, skill_ret=(None, cls))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 3                         # 1 + 2 次重试
    assert calls["deepseek"] == 1
    assert skill_merge.get_counters()["hard_error"] == 1


def test_p3_merge_claude_timeout_falls_back_without_retry(monkeypatch):
    # 超时≠一般硬错：重试一次又是一整个超时窗（P3_TIMEOUT_SEC），直降 DeepSeek 不重试
    monkeypatch.setenv("P3_ENGINE", "claude")
    monkeypatch.setenv("P3_CLAUDE_RETRY", "2")
    skill_merge.reset_state()
    cls = {"state": "timeout", "window": None, "rate_limits": [], "message": "超时"}
    calls = _patch_engines(monkeypatch, skill_ret=(None, cls))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 1
    assert calls["deepseek"] == 1


def test_run_skill_merge_timeout_returns_timeout_state(monkeypatch, tmp_path):
    # 子进程必须带 timeout（P3 心跳喂狗后，看门狗对真挂死失效，超时是唯一回收手段）；
    # 超时 → (None, state=timeout)，交 p3_merge 降级
    def fake_run(*a, **kw):
        assert kw.get("timeout"), "subprocess.run 必须带 timeout"
        raise skill_merge.subprocess.TimeoutExpired(cmd="claude", timeout=kw["timeout"])

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    path, cls = skill_merge.run_skill_merge("m.md", vendor_root=tmp_path)
    assert path is None
    assert cls["state"] == "timeout"


def test_p3_merge_preempts_when_cache_capped(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    # 缓存里 5h 已 rejected 且未到重置 → 预判降级，连 claude 都不调
    skill_merge.prime_cache([{"window": "five_hour", "status": "rejected", "resets_at": 9999999999}])
    calls = _patch_engines(monkeypatch, skill_ret=("不该用.md", {"state": "ok"}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 0 and calls["deepseek"] == 1
    assert skill_merge.get_counters()["cap_5h"] == 1


def test_p3_merge_preempt_expires_after_reset(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    # resets_at 已过去 → 缓存失效，乐观重试 claude
    skill_merge.prime_cache([{"window": "five_hour", "status": "rejected", "resets_at": 1}])
    cls = {"state": "ok", "rate_limits": [{"window": "five_hour", "status": "allowed", "resets_at": 9999999999}]}
    calls = _patch_engines(monkeypatch, skill_ret=("CL_终稿.md", cls))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "CL_终稿.md"
    assert calls["skill"] == 1


def test_p3_merge_concurrency_gate_overflows_to_deepseek(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state(concurrency=1)
    calls = _patch_engines(monkeypatch, skill_ret=("CL.md", {"state": "ok", "rate_limits": []}))
    # 占满唯一名额，模拟已有 1 路在飞
    assert skill_merge._sem.acquire(blocking=False)
    try:
        path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    finally:
        skill_merge._sem.release()
    assert path == "DS_终稿.md"
    assert calls["skill"] == 0 and calls["deepseek"] == 1
    assert skill_merge.get_counters()["cap_concurrency"] == 1


# ============ T7.2b · 注入全局 Claude 闸（多机：DB gate 替代进程内信号量）============

class _FakeGate:
    """模拟跨机全局闸：allow 控制 acquire 是否给名额；记账 acquire/release 次数。"""
    def __init__(self, allow=True):
        self.allow = allow
        self.acquired = 0
        self.released = 0

    def acquire(self):
        self.acquired += 1
        return self.allow

    def release(self):
        self.released += 1


def test_p3_merge_uses_injected_gate_and_releases(monkeypatch):
    # 注入全局闸：用它（不用进程内 _sem），成功后释放
    monkeypatch.setenv("P3_ENGINE", "claude")
    gate = _FakeGate(allow=True)
    skill_merge.set_claude_gate(gate)
    calls = _patch_engines(monkeypatch, skill_ret=("CL.md", {"state": "ok", "rate_limits": []}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "CL.md"
    assert calls["skill"] == 1
    assert gate.acquired == 1 and gate.released == 1


def test_p3_merge_injected_gate_full_falls_back(monkeypatch):
    # 全局闸满（跨机在飞≥上限）→ 降级 DeepSeek，不调 Claude，不释放（没拿到）
    monkeypatch.setenv("P3_ENGINE", "claude")
    gate = _FakeGate(allow=False)
    skill_merge.set_claude_gate(gate)
    calls = _patch_engines(monkeypatch, skill_ret=("不该用.md", {"state": "ok"}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 0 and calls["deepseek"] == 1
    assert gate.acquired == 1 and gate.released == 0
    assert skill_merge.get_counters()["cap_concurrency"] == 1


def test_p3_merge_injected_gate_releases_on_capped(monkeypatch):
    # 撞顶降级时也要释放全局闸名额（否则名额泄漏）
    monkeypatch.setenv("P3_ENGINE", "claude")
    gate = _FakeGate(allow=True)
    skill_merge.set_claude_gate(gate)
    cls = {"state": "capped", "window": "five_hour", "rate_limits": []}
    calls = _patch_engines(monkeypatch, skill_ret=(None, cls))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert gate.acquired == 1 and gate.released == 1   # 拿了就要还


# ============ 2026-08-07 · 跨机撞顶冷却 + 健康度埋点（闸扩展 capped_reason/report）============

class _HealthGate(_FakeGate):
    """带健康度能力的闸：capped_reason 报全局撞顶冷却，report 收埋点。
    `_FakeGate`（无这两个方法）仍被上面的老用例覆盖 —— 那正是向后兼容的断言。"""
    def __init__(self, allow=True, capped="", capped_exc=None, report_exc=None):
        super().__init__(allow=allow)
        self.capped = capped
        self.capped_exc = capped_exc
        self.report_exc = report_exc
        self.events = []

    def capped_reason(self):
        if self.capped_exc:
            raise self.capped_exc
        return self.capped

    def report(self, outcome, **kw):
        self.events.append((outcome, kw))
        if self.report_exc:
            raise self.report_exc


def test_global_cap_preempts_without_touching_claude(monkeypatch):
    # 别的机器已撞顶 → 本机直接降级，连名额都不占（省下一趟白撞墙的订阅调用）
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    gate = _HealthGate(capped="cap_5h")
    skill_merge.set_claude_gate(gate)
    calls = _patch_engines(monkeypatch, skill_ret=("不该用.md", {"state": "ok"}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 0 and gate.acquired == 0
    assert skill_merge.get_counters()["cap_5h"] == 1
    assert gate.events == [("preempt", {"note": "cap_5h"})]


def test_cap_lookup_failure_does_not_block_transcription(monkeypatch):
    # 查不到全局状态（DB 抖动）→ 按未撞顶继续，绝不能因为监控坏了就不转录
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    gate = _HealthGate(capped_exc=RuntimeError("db down"))
    skill_merge.set_claude_gate(gate)
    calls = _patch_engines(monkeypatch, skill_ret=("CL.md", {"state": "ok", "rate_limits": []}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "CL.md" and calls["skill"] == 1


def test_report_failure_does_not_break_merge(monkeypatch):
    # 埋点写库失败同理：日志留痕，转录照常出稿
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    gate = _HealthGate(report_exc=RuntimeError("insert failed"))
    skill_merge.set_claude_gate(gate)
    _patch_engines(monkeypatch, skill_ret=("CL.md", {"state": "ok", "rate_limits": []}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "CL.md"


def test_ok_and_capped_and_concurrency_are_reported(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")

    skill_merge.reset_state()
    ok_gate = _HealthGate()
    skill_merge.set_claude_gate(ok_gate)
    _patch_engines(monkeypatch, skill_ret=("CL.md", {"state": "ok", "rate_limits": []}))
    skill_merge.p3_merge("m.md", vendor_root=".")
    assert ok_gate.events == [("ok", {})]

    # 撞顶：带窗口与恢复时刻（后者是下一台机器算冷却到期的依据）
    skill_merge.reset_state()
    cap_gate = _HealthGate()
    skill_merge.set_claude_gate(cap_gate)
    cls = {"state": "capped", "window": "five_hour", "message": "429",
           "rate_limits": [{"window": "five_hour", "status": "rejected", "resets_at": 1900000000}]}
    _patch_engines(monkeypatch, skill_ret=(None, cls))
    skill_merge.p3_merge("m.md", vendor_root=".")
    assert cap_gate.events == [("capped", {"window": "five_hour",
                                           "resets_at": 1900000000, "note": "429"})]

    # 并发闸满：是我们自己的限流，不是订阅不健康 —— 单独一种 outcome
    skill_merge.reset_state()
    full_gate = _HealthGate(allow=False)
    skill_merge.set_claude_gate(full_gate)
    _patch_engines(monkeypatch, skill_ret=("不该用.md", {"state": "ok"}))
    skill_merge.p3_merge("m.md", vendor_root=".")
    assert full_gate.events == [("concurrency", {})]


def test_repeated_subprocess_crash_reports_error(monkeypatch):
    # 每轮都抛异常 → 循环结束时 cls 从未赋值，兜底出口读它不能炸（NameError 会把降级链一起带走）
    monkeypatch.setenv("P3_ENGINE", "claude")
    monkeypatch.setenv("P3_CLAUDE_RETRY", "1")
    skill_merge.reset_state()
    gate = _HealthGate()
    skill_merge.set_claude_gate(gate)
    calls = _patch_engines(monkeypatch, skill_exc=OSError("boom"))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 2                       # 首次 + 1 次重试
    assert gate.events == [("error", {"note": "boom"})]


def test_timeout_is_reported_separately(monkeypatch):
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    gate = _HealthGate()
    skill_merge.set_claude_gate(gate)
    _patch_engines(monkeypatch, skill_ret=(None, {"state": "timeout", "message": "超时（>3600s）"}))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert gate.events == [("timeout", {"note": "超时（>3600s）"})]


# ============ 2026-08-07 · 认证/订阅失效与撞顶分开（处置相反：一个自愈，一个要人修）============

def test_auth_failure_classified_apart_from_generic_error():
    # 401/登录过期不能混进「报错」筐——运营看着「报错 3」不会想到要去续订
    for blob in ("401", "Unauthorized", "Login expired · Please run /login",
                 "OAuth token invalid", "authentication_error"):
        out = skill_merge.classify_claude_result(
            json.dumps({"type": "result", "is_error": True, "result": blob}), 1)
        assert out["state"] == "auth", blob
        assert blob.lower()[:6] in out["message"].lower()   # 原话带出去，便于定位


def test_cap_still_wins_over_auth():
    # 429 同时含 forbidden 之类的词时仍判撞顶：撞顶更具体，且处置是「等」而不是「修」
    out = skill_merge.classify_claude_result(
        json.dumps({"type": "result", "is_error": True,
                    "api_error_status": 429, "result": "rate limit exceeded"}), 1)
    assert out["state"] == "capped"


def test_plain_error_stays_error():
    out = skill_merge.classify_claude_result(
        json.dumps({"type": "result", "is_error": True, "result": "segmentation fault"}), 1)
    assert out["state"] == "error"


def test_auth_failure_falls_back_without_retrying(monkeypatch):
    # 认证坏了重试一百次也一样：直接降级，只调一次
    monkeypatch.setenv("P3_ENGINE", "claude")
    monkeypatch.setenv("P3_CLAUDE_RETRY", "2")
    skill_merge.reset_state()
    gate = _HealthGate()
    skill_merge.set_claude_gate(gate)
    cls = {"state": "auth", "message": "401 Unauthorized", "rate_limits": []}
    calls = _patch_engines(monkeypatch, skill_ret=(None, cls))
    path, _ = skill_merge.p3_merge("m.md", vendor_root=".")
    assert path == "DS_终稿.md"
    assert calls["skill"] == 1                       # 没有重试
    assert gate.acquired == 1 and gate.released == 1
    assert gate.events == [("auth", {"note": "401 Unauthorized"})]
    assert skill_merge.get_counters()["auth"] == 1


def test_auth_failure_does_not_enter_cap_cooldown(monkeypatch):
    # 冷却是给额度用的（会自愈）；认证失效要保持每单都试，人一修好下一单立刻恢复
    monkeypatch.setenv("P3_ENGINE", "claude")
    skill_merge.reset_state()
    gate = _HealthGate()
    skill_merge.set_claude_gate(gate)
    _patch_engines(monkeypatch, skill_ret=(None, {"state": "auth", "message": "401", "rate_limits": []}))
    skill_merge.p3_merge("m.md", vendor_root=".")
    assert [e[0] for e in gate.events] == ["auth"]    # 没有写 capped，故不会触发跨机冷却


# ---- T5：三档阶梯 Claude → DeepSeek Pro → DeepSeek Flash（2026-08-10）----
class _Gate:
    """可编程的假闸：acquire 按 allow 决定给不给名额，并记录 release 次数。"""

    def __init__(self, allow: bool):
        self.allow, self.acquired, self.released = allow, 0, 0

    def acquire(self):
        self.acquired += 1
        return self.allow

    def release(self):
        self.released += 1

    def capped_reason(self):
        return ""

    def report(self, outcome, **kw):
        pass


def _ladder(monkeypatch, claude_allow):
    """Claude 闸换成假的、run_merge 换成只记模型名的桩，返回选中的模型名。

    ⚠️ 2026-08-26 起只剩两档（Claude → Flash）：Pro 档已彻底摘除，
    此前这里还带着 `pro_allow` / `pro_enabled` 两个参数验三档阶梯。"""
    seen = {}

    def fake_run_merge(match_file, vendor_root, glossary_text="", model="", ui_lang=None):
        seen["model"] = model
        return "/tmp/out_P3_Merge_x.md", 0.12

    monkeypatch.setattr(skill_merge.merge, "run_merge", fake_run_merge)
    skill_merge.set_claude_gate(_Gate(claude_allow))
    skill_merge.set_flash_model("ds-flash")
    try:
        skill_merge.p3_merge("/tmp/x_P2_Match.md", vendor_root="/tmp")
    finally:
        skill_merge.set_claude_gate(None)
        skill_merge.set_flash_model("")
    return seen.get("model")


def test_claude拿不到名额就落flash(monkeypatch):
    """两档阶梯的唯一分支：Claude 闸满 → DeepSeek·Flash（无闸，不排队）。

    这条同时钉住「拿不到 Claude 名额是**降级**不是排队」——它决定了 Claude 那道 5
    不是吞吐天花板（2026-08-26 盘点时我一度把它当成了天花板，是错的）。"""
    assert _ladder(monkeypatch, claude_allow=False) == "ds-flash"


def test_不注入flash模型名时保持单档(monkeypatch):
    """本地/回滚态（FLY_DISPATCH=0）不注入模型名 → 不传 model，沿用子进程缺省，老行为不变。

    ⚠️ 摘 Pro 时最容易顺手删掉的就是这条注入——`set_pro_gate` 同时干着两件事
    （注入 Pro 闸 + 注入两档模型名），后一件与 Pro 无关。删过头的话 Flash 会悄悄
    回落到子进程自己的缺省模型，而且不报任何错。"""
    seen = {}

    def fake_run_merge(match_file, vendor_root, glossary_text="", **kw):
        seen["kw"] = kw            # 未注入时**连 model 都不该出现**，所以收 **kw 才验得出
        return "/tmp/out_P3_Merge_x.md", 0.12

    monkeypatch.setattr(skill_merge.merge, "run_merge", fake_run_merge)
    skill_merge.set_claude_gate(_Gate(False))
    skill_merge.set_flash_model("")
    try:
        skill_merge.p3_merge("/tmp/x_P2_Match.md", vendor_root="/tmp")
    finally:
        skill_merge.set_claude_gate(None)
    # 只钉「没有 model」这一条。别写成 `== {}`：ui_lang 之类与本条无关的参数以后还会加，
    # 而这条守卫要守的是「未注入模型名时不传 model，让子进程用它自己的缺省」。
    assert "model" not in seen["kw"]


def test_pro档已彻底摘除():
    """Pro 不该以任何形式回来：没有闸、没有注入口、没有强制档、没有上限登记。

    2026-08-11 起它就停用了（`_PRO_ENABLED=False`），但代码路径一直留着；
    2026-08-26 Duner 定「不会再用」，整条摘掉。留着一个停用的分支的代价是：
    它没人走、最容易在别的改动里悄悄烂掉，而运营舱上那个旋钮还在诱导人去调它。"""
    from app import p3_config

    assert not hasattr(skill_merge, "set_pro_gate")
    assert not hasattr(skill_merge, "_PRO_ENABLED")
    assert "pro" not in p3_config.LIMITS
    assert "pro" not in p3_config.FORCE_MACHINES
    assert "pro_concurrency" not in p3_config._FIELDS
    assert p3_config.validate({"force_engine": "pro"}), "强制引擎还认 pro"


# ---- 2026-09-03：终稿形状不受我们控制，收稿时先用 P4 的解析器读一遍 ----

def test_run_skill_merge_unparsable_output_is_error(monkeypatch, tmp_path):
    """Claude 写出一份解析器一行都认不出的终稿 → 当硬错（走重试→降级），不把它交给 P4 去整单失败。"""
    out = tmp_path / "Output"
    out.mkdir()
    produced = out / "foo_P3_Merge_0903_0844.md"

    def fake_run(cmd, cwd, env, **kw):
        produced.write_text("# 融合终稿\n\n主持人说：这样的，就是我……\n\n受访者说：嗯。\n")

        class R:
            returncode = 0
            stdout = _claude_json(rate_limits=[{"status": "allowed", "window": "five_hour"}])
            stderr = ""
        return R()

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    path, cls = skill_merge.run_skill_merge(
        match_file=str(tmp_path / "foo_P2_Match_0903_0837.md"), vendor_root=tmp_path)
    assert path is None and cls["state"] == "error"
    assert "0 行可解析" in cls["message"]


def test_run_skill_merge_start_only_timestamps_are_accepted(monkeypatch, tmp_path):
    """生产实见的那种形状（只有起点时间码）解析器现在认得，不该被形状检查拦下。"""
    out = tmp_path / "Output"
    out.mkdir()
    produced = out / "foo_P3_Merge_0903_0844.md"

    def fake_run(cmd, cwd, env, **kw):
        produced.write_text("[00:00.1] M：这样的，就是我。\n\n[00:09.9] R：嗯。\n")

        class R:
            returncode = 0
            stdout = _claude_json(rate_limits=[{"status": "allowed", "window": "five_hour"}])
            stderr = ""
        return R()

    monkeypatch.setattr(skill_merge.subprocess, "run", fake_run)
    path, cls = skill_merge.run_skill_merge(
        match_file=str(tmp_path / "foo_P2_Match_0903_0837.md"), vendor_root=tmp_path)
    assert Path(path) == produced and cls["state"] == "ok"


def test_both_p3_paths_state_the_timestamp_pair_rule():
    """「时间区间两端都要」这句契约必须同时写在两条路的提示里。
    08-10 只补了 DeepSeek 分批版，Claude 路的 SKILL 只写「时间戳原样保留」，09-03 生产实见整篇只写起点。"""
    root = Path(__file__).resolve().parents[1] / "pipeline" / "vendor"
    skill = (root / ".claude" / "skills" / "multi-asr-merge" / "SKILL.md").read_text(encoding="utf-8")
    batched = (root / "Workflow" / "Phase3_Merge_DeepSeek_Batched.py").read_text(encoding="utf-8")
    for name, text in (("SKILL.md", skill), ("Batched.py", batched)):
        assert "不许只写起始时间码" in text, name


def test_skill_states_the_two_2026_09_04_rules():
    """Duner 2026-09-04 定的两条：① 联网不能推翻各路一致的读音（标 [❓] 交用户）；② 型号阿拉伯数字且全篇统一。
    评测台（server/scripts/p3eval）实测：五路一致的「ES9」被一次联网改成「ET9」；主轨的「M 九」被老版本原样抄 40 处。"""
    root = Path(__file__).resolve().parents[1] / "pipeline" / "vendor" / ".claude" / "skills" / "multi-asr-merge" / "SKILL.md"
    skill = root.read_text(encoding="utf-8")
    assert "不能推翻各路一致的读音" in skill
    assert "型号里的数字一律阿拉伯数字" in skill

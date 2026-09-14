"""P3 探活：在 Fly 机器上真跑一次最小 claude -p，结果落 p3_events（运营舱 P3 健康卡）。

探活机器跑完即自毁，**唯一的输出就是那条事件**——所以每条路径（含异常/超时/闸满）
都必须留下一条记录，否则运营舱永远停在「探活中…」。这组用例守的就是这一点。
"""
import subprocess

import pytest

from app import p3_probe


class _Gate:
    def __init__(self, allow=True):
        self.allow = allow
        self.released = 0

    def acquire(self):
        return self.allow

    def release(self):
        self.released += 1


@pytest.fixture
def rec(monkeypatch):
    """收集 p3_health.record 的调用，并默认给一个能拿到名额的闸。"""
    events = []
    monkeypatch.setattr(p3_probe.p3_health, "record",
                        lambda outcome, **kw: events.append((outcome, kw)))
    return events


def _patch_gate(monkeypatch, gate):
    monkeypatch.setattr(p3_probe.claude_gate, "DbClaudeGate", lambda *a, **k: gate)
    return gate


def _patch_run(monkeypatch, stdout="PONG", returncode=0, exc=None):
    def fake_run(cmd, **kw):
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    monkeypatch.setattr(p3_probe.subprocess, "run", fake_run)


def _claude_ok(text="PONG"):
    return '{"type":"result","is_error":false,"result":"%s"}' % text


def test_probe_ok(monkeypatch, rec):
    gate = _patch_gate(monkeypatch, _Gate())
    _patch_run(monkeypatch, stdout=_claude_ok())
    assert p3_probe.run_probe("probe-1") is True
    assert rec[0][0] == "ok"
    assert rec[0][1]["source"] == "probe" and rec[0][1]["job_id"] == "probe-1"
    assert gate.released == 1                      # 拿了名额就要还


def test_probe_success_but_wrong_answer_counts_as_error(monkeypatch, rec):
    # 调通了却答非所问 → 模型/配置层面有问题，不算健康
    _patch_gate(monkeypatch, _Gate())
    _patch_run(monkeypatch, stdout=_claude_ok("我不明白"))
    p3_probe.run_probe("probe-2")
    assert rec[0][0] == "error"


def test_probe_capped_records_window(monkeypatch, rec):
    # 撞顶：这条事件同时是全局撞顶冷却的依据，窗口必须落库
    _patch_gate(monkeypatch, _Gate())
    _patch_run(monkeypatch, stdout='{"type":"result","is_error":true,'
                                   '"api_error_status":429,"result":"rate limit"}', returncode=1)
    p3_probe.run_probe("probe-3")
    assert rec[0][0] == "capped" and rec[0][1]["window"] == "five_hour"


def test_probe_gate_full_records_and_skips_claude(monkeypatch, rec):
    # 闸满：让位给真实转录，但仍要留痕（「当时正忙」本身就是运营要看的信息）
    gate = _patch_gate(monkeypatch, _Gate(allow=False))
    called = {"n": 0}
    monkeypatch.setattr(p3_probe.subprocess, "run",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert p3_probe.run_probe("probe-4") is False
    assert called["n"] == 0
    assert rec[0][0] == "concurrency"
    assert gate.released == 0                      # 没拿到就不还


def test_probe_timeout_records(monkeypatch, rec):
    gate = _patch_gate(monkeypatch, _Gate())
    _patch_run(monkeypatch, exc=subprocess.TimeoutExpired("claude", 180))
    p3_probe.run_probe("probe-5")
    assert rec[0][0] == "timeout"
    assert gate.released == 1


def test_probe_unexpected_exception_still_records(monkeypatch, rec):
    # 机器跑完就没了：不留痕 = 运营舱永远转圈
    gate = _patch_gate(monkeypatch, _Gate())
    _patch_run(monkeypatch, exc=OSError("claude not found"))
    p3_probe.run_probe("probe-6")
    assert rec[0][0] == "error" and "claude not found" in rec[0][1]["note"]
    assert gate.released == 1


def test_probe_timeout_is_short_not_the_hour_long_p3_window(monkeypatch):
    # 探活超时独立于 P3_TIMEOUT_SEC（1 小时）——卡住本身就是不健康，不该让人等一小时
    monkeypatch.setenv("P3_TIMEOUT_SEC", "3600")
    monkeypatch.delenv("P3_PROBE_TIMEOUT_SEC", raising=False)
    assert p3_probe._timeout_sec() == 180
    monkeypatch.setenv("P3_PROBE_TIMEOUT_SEC", "不是数字")
    assert p3_probe._timeout_sec() == 180


def test_probe_reports_auth_failure_distinctly(monkeypatch, rec):
    # 探活撞上订阅/令牌失效：必须落成 auth 而不是笼统的 error，
    # 否则面板只会说「探活失败」，运营不知道该去续订还是等额度
    _patch_gate(monkeypatch, _Gate())
    _patch_run(monkeypatch, stdout='{"type":"result","is_error":true,'
                                   '"result":"Login expired · Please run /login"}', returncode=1)
    p3_probe.run_probe("probe-auth")
    assert rec[0][0] == "auth"
    assert "login expired" in (rec[0][1]["note"] or "").lower()

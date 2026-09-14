"""P3 健康度埋点 + 跨机撞顶冷却（DB 级）。需 postgres（@pytest.mark.infra）。

重点守 cap_state 的两条安全线：
① 令牌模式拿不到恢复时刻 → 必须按兜底窗自动解除，否则一次撞顶＝永久禁用 Claude；
② 撞顶后一旦有成功事件 → 立即解除（手动探活的出口，不必干等兜底窗）。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import db, p3_health


@pytest.fixture(autouse=True)
def _clean():
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM p3_events")
    yield


def _insert(outcome, *, minutes_ago=0, window=None, resets_at=None, source="job"):
    """直接写库以便指定事件时刻（record() 只会写 now()）。"""
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO p3_events (at, source, job_id, outcome, window_kind, resets_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (at, source, "j1", outcome, window, resets_at),
        )


@pytest.mark.infra
def test_no_events_means_not_capped():
    assert p3_health.cap_state() is None


@pytest.mark.infra
def test_recent_cap_without_reset_time_uses_fallback_window(monkeypatch):
    # 令牌模式：拿不到 resetsAt → 按「撞顶时刻 + 兜底窗」判定仍在冷却
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_COOLDOWN_MIN", 30)
    _insert("capped", minutes_ago=5, window="five_hour")
    st = p3_health.cap_state()
    assert st and st["reason"] == "cap_5h"


@pytest.mark.infra
def test_fallback_window_expires(monkeypatch):
    # 没有这条自动解除，一次撞顶就等于永久禁用 Claude
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_COOLDOWN_MIN", 30)
    _insert("capped", minutes_ago=31, window="five_hour")
    assert p3_health.cap_state() is None


@pytest.mark.infra
def test_week_window_uses_longer_cooldown(monkeypatch):
    # 周额度撞顶恢复慢得多：5h 的窗早过了，周窗还得等
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_COOLDOWN_MIN", 30)
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_WEEK_COOLDOWN_MIN", 360)
    _insert("capped", minutes_ago=60, window="seven_day")
    st = p3_health.cap_state()
    assert st and st["reason"] == "cap_week"


@pytest.mark.infra
def test_real_reset_time_wins_over_fallback(monkeypatch):
    # 本机登录态能拿到 resetsAt：以它为准，兜底窗不参与
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_COOLDOWN_MIN", 1)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    _insert("capped", minutes_ago=10, window="five_hour", resets_at=future)
    st = p3_health.cap_state()
    assert st and st["until"] == future


@pytest.mark.infra
def test_success_after_cap_clears_cooldown(monkeypatch):
    # 探活通了 = 额度恢复的最硬证据，立刻解除，不必干等兜底窗
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_COOLDOWN_MIN", 30)
    _insert("capped", minutes_ago=10, window="five_hour")
    _insert("ok", minutes_ago=1, source="probe")
    assert p3_health.cap_state() is None


@pytest.mark.infra
def test_success_before_cap_does_not_clear(monkeypatch):
    # 顺序要紧：撞顶**之前**的成功不算恢复证据
    monkeypatch.setattr(p3_health.config, "CLAUDE_CAP_COOLDOWN_MIN", 30)
    _insert("ok", minutes_ago=20)
    _insert("capped", minutes_ago=10, window="five_hour")
    assert p3_health.cap_state() is not None


@pytest.mark.infra
def test_success_rate_denominator_excludes_our_own_throttling():
    # preempt/concurrency 是我们主动没打 Claude，算进分母会把「限流生效」显示成「Claude 变差」
    _insert("ok", minutes_ago=5)
    _insert("ok", minutes_ago=4)
    _insert("error", minutes_ago=3)
    _insert("preempt", minutes_ago=2)
    _insert("concurrency", minutes_ago=1)
    h = p3_health.health(hours=24)
    assert h["attempted"] == 3
    assert h["successRate"] == pytest.approx(2 / 3)
    assert h["counts"]["preempt"] == 1


@pytest.mark.infra
def test_no_samples_gives_none_not_zero():
    # 没样本时返回 None，前端好显示「近 N 小时无样本」而不是刺眼的 0%
    _insert("ok", minutes_ago=60 * 48)      # 窗口外
    h = p3_health.health(hours=24)
    assert h["attempted"] == 0 and h["successRate"] is None


@pytest.mark.infra
def test_record_roundtrip_and_last_fields():
    p3_health.record("capped", source="probe", job_id="probe-x",
                     window="five_hour", resets_at=1900000000, note="429 rate limit")
    h = p3_health.health()
    assert h["last"]["source"] == "probe" and h["last"]["outcome"] == "capped"
    assert h["lastCappedWindow"] == "five_hour"
    assert h["capped"]["reason"] == "cap_5h"


@pytest.mark.infra
def test_auth_failing_flag_and_self_clear():
    # 认证失效不会自愈，所以只看「最后一次真打到引擎的结果」，不设时间窗：
    # 三天没任务也不代表令牌就修好了
    _insert("auth", minutes_ago=10)
    h = p3_health.health()
    assert h["authFailing"] is True

    # 人修好后下一单成功 → 自动翻篇，不需要任何手工清除
    _insert("ok", minutes_ago=1)
    assert p3_health.health()["authFailing"] is False


@pytest.mark.infra
def test_our_own_throttling_does_not_mask_auth_failure():
    # preempt/concurrency 是我们没打引擎，不能算「最后一次结果」把 auth 冲掉
    _insert("auth", minutes_ago=10)
    _insert("preempt", minutes_ago=5)
    _insert("concurrency", minutes_ago=1)
    assert p3_health.health()["authFailing"] is True


@pytest.mark.infra
def test_auth_counts_into_failure_rate():
    _insert("ok", minutes_ago=5)
    _insert("auth", minutes_ago=1)
    h = p3_health.health()
    assert h["attempted"] == 2 and h["successRate"] == pytest.approx(0.5)


@pytest.mark.infra
def test_auth_note_surfaces_engine_message():
    # 面板要显示「引擎原话」才能定位是订阅到期还是令牌坏了
    p3_health.record("auth", source="probe", job_id="p1", note="401 Unauthorized")
    assert p3_health.health()["authNote"] == "401 Unauthorized"


@pytest.mark.infra
def test_last_attempt_reflects_now_not_the_rolling_average():
    # 窗口成功率是滚动平均，故障修好后还会把面板按在「异常」上好几个小时（2026-08-07 实测）：
    # 「此刻好不好」看最后一次真打到引擎的结果
    _insert("auth", minutes_ago=30)
    _insert("auth", minutes_ago=25)
    _insert("ok", minutes_ago=5)
    h = p3_health.health()
    assert h["lastAttempt"] == "ok"
    assert h["successRate"] < 0.7        # 窗口仍难看，但那是历史
    assert h["authFailing"] is False


@pytest.mark.infra
def test_last_attempt_ignores_our_own_throttling():
    # 冷却跳过/并发让路都没打到引擎，不能顶替「最后一次结果」
    _insert("error", minutes_ago=10)
    _insert("preempt", minutes_ago=2)
    _insert("concurrency", minutes_ago=1)
    assert p3_health.health()["lastAttempt"] == "error"

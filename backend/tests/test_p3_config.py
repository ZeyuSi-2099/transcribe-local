"""P3 运行时配置的校验与派生逻辑（运营舱可改的那几个数）。

重点不是「能不能存」，是**能不能拦住设错的值**——这些数设错了不会报错，
只会表现为撞供应商并发墙后的退避重试（莫名变慢），线上很难查。
"""
import datetime as dt

import pytest

from app import p3_config as C


def test_force_machines_derived_not_configurable():
    """强制态机器数由引擎派生，不接受外部设定——少一个能设错的地方。"""
    assert set(C.FORCE_MACHINES) == {"flash"}
    assert "force_max_machines" not in C._FIELDS


@pytest.mark.parametrize("engine,ratio_max", [("flash", 0.8)])
def test_force_machines_within_account_cap(engine, ratio_max):
    """派生的机器数 × 每机路数不得超过该账号并发上限（flash 留两成余量）。

    这条测试的意义在于：将来谁改 FORCE_MACHINES 或 PER_MACHINE_CONC，撞穿了会立刻红。
    """
    u = C.usage_of(engine, C.FORCE_MACHINES[engine])
    assert u["used"] == C.FORCE_MACHINES[engine] * C.PER_MACHINE_CONC
    assert u["ratio"] <= ratio_max


def test_claude_cannot_be_forced():
    """claude 本就是第一档，强制它只会让任务挤在 5 并发的订阅墙上排队。"""
    assert C.validate({"force_engine": "claude"})
    assert C.validate({"force_engine": "opus"})
    assert C.validate({"force_engine": "pro"})      # Pro 档 2026-08-26 已摘除，不该还能强制
    assert not C.validate({"force_engine": "flash"})


@pytest.mark.parametrize("patch,should_fail", [
    ({"claude_concurrency": 5}, False),  # 额度速率闸（不是并发墙，见 LIMITS 注释）
    ({"claude_concurrency": 6}, True),
    ({"fly_max_machines": 50}, False),   # Fly 组织配额
    ({"fly_max_machines": 51}, True),
    # 转录任务数（2026-08-26 搬进运营舱）：全系统真正的吞吐天花板
    ({"max_transcribe_jobs": 40}, False),
    ({"max_transcribe_jobs": 51}, True),                              # 超 Fly 配额
    ({"max_transcribe_jobs": 0}, True),
    # 转录数 > 机器数 = 多出来的名额永远派不出去（两个数是不同指标，但有从属关系）
    ({"fly_max_machines": 20, "max_transcribe_jobs": 40}, True),
    ({"fly_max_machines": 40, "max_transcribe_jobs": 40}, False),
])
def test_validate_by_product_not_by_single_number(patch, should_fail):
    """并发按**乘积**校验：只卡单个数会放过「看着不大、实际撞墙」的值。"""
    assert bool(C.validate(patch)) is should_fail


def test_expired_force_is_treated_as_unset():
    """过期判定在读取侧完成，不依赖任何清扫任务——没有定时器也能自动失效。"""
    now = dt.datetime.now(dt.timezone.utc)
    past = {"force_engine": "flash", "force_expires_at": now - dt.timedelta(hours=1)}
    future = {"force_engine": "flash", "force_expires_at": now + dt.timedelta(hours=1)}
    assert C.drop_if_expired(past)["force_engine"] is None
    assert C.drop_if_expired(future)["force_engine"] == "flash"
    # 无到期时间（历史数据/手工写库）不该被当成过期而静默失效
    assert C.drop_if_expired({"force_engine": "flash", "force_expires_at": None})["force_engine"] == "flash"


def test_naive_timestamp_treated_as_utc():
    """库里取出的时间戳可能不带时区，直接比较会 TypeError 让整张配置读不出来。"""
    past_naive = dt.datetime.utcnow() - dt.timedelta(hours=1)
    assert C.drop_if_expired(
        {"force_engine": "flash", "force_expires_at": past_naive})["force_engine"] is None


def test_unreadable_db_falls_back_to_env(monkeypatch):
    """配置读不到必须回落 env 继续跑——绝不能因为配置表出问题就让转录停摆。"""
    monkeypatch.setattr(C.db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = C.get()
    assert cfg["fly_max_machines"] and cfg["max_transcribe_jobs"] and cfg["claude_concurrency"]
    assert cfg["force_engine"] is None          # 回落时绝不能凭空强制某引擎
    assert C.effective_max_machines() == cfg["fly_max_machines"]


def test_effective_max_machines_switches_with_force(monkeypatch):
    monkeypatch.setattr(C, "get", lambda: {"force_engine": "flash", "fly_max_machines": 50})
    assert C.effective_max_machines() == C.FORCE_MACHINES["flash"]
    monkeypatch.setattr(C, "get", lambda: {"force_engine": None, "fly_max_machines": 50})
    assert C.effective_max_machines() == 50

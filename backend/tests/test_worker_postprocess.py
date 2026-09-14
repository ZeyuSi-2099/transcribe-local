"""worker 的后处理挂点单测：main_one_job 按 JOB_KIND 路由 / 同池轮询 pp 队列 / 看门狗回收。"""
import time

import pytest

import app.worker as worker


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch):
    # 缺省全部打桩空转，单测零外部依赖；各用例按需覆盖
    monkeypatch.setattr(worker.postprocess, "claim_next_queued", lambda: None)
    monkeypatch.setattr(worker.postprocess, "claim_specific", lambda jid: None)
    monkeypatch.setattr(worker.postprocess, "requeue_stale_running", lambda *a, **k: 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: None)
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)


def test_main_one_job_routes_postprocess_by_env(monkeypatch):
    # Fly 机器入口：JOB_KIND=postprocess → 后处理链，不碰转录 claim
    monkeypatch.setenv("JOB_ID", "pp-1")
    monkeypatch.setenv("JOB_KIND", "postprocess")
    seen = {}
    monkeypatch.setattr(worker, "run_one_pp_job", lambda jid: seen.update(id=jid) or True)
    monkeypatch.setattr(worker, "run_one_job",
                        lambda jid: (_ for _ in ()).throw(AssertionError("不该走转录链")))
    assert worker.main_one_job() == 0
    assert seen["id"] == "pp-1"


def test_main_one_job_defaults_to_transcription(monkeypatch):
    # 不带 JOB_KIND（或为空）→ 转录链，行为不变
    monkeypatch.setenv("JOB_ID", "t-1")
    monkeypatch.delenv("JOB_KIND", raising=False)
    seen = {}
    monkeypatch.setattr(worker, "run_one_job", lambda jid: seen.update(id=jid) or True)
    monkeypatch.setattr(worker, "run_one_pp_job",
                        lambda jid: (_ for _ in ()).throw(AssertionError("不该走后处理链")))
    assert worker.main_one_job() == 0
    assert seen["id"] == "t-1"


def test_main_one_job_pp_not_claimed_returns_1(monkeypatch):
    monkeypatch.setenv("JOB_ID", "pp-x")
    monkeypatch.setenv("JOB_KIND", "postprocess")
    monkeypatch.setattr(worker, "run_one_pp_job", lambda jid: False)
    assert worker.main_one_job() == 1


def test_run_one_pp_job_claims_and_runs(monkeypatch):
    claimed = {"job_id": "pp-2", "steps": ["narrate"]}
    monkeypatch.setattr(worker.postprocess, "claim_specific",
                        lambda jid: claimed if jid == "pp-2" else None)
    ran = {}
    monkeypatch.setattr(worker.pp_runner, "run", lambda pp: ran.update(pp=pp))
    assert worker.run_one_pp_job("pp-2") is True
    assert ran["pp"] is claimed


def test_run_one_pp_job_skips_when_not_queued(monkeypatch):
    called = {}
    monkeypatch.setattr(worker.pp_runner, "run", lambda pp: called.update(x=1))
    assert worker.run_one_pp_job("missing") is False
    assert "x" not in called


def test_process_one_pp_returns_false_when_empty(monkeypatch):
    assert worker.process_one_pp() is False


def test_run_loop_polls_pp_queue_after_transcription(monkeypatch):
    # 同池轮询：转录队列空 → 轮到 pp 队列；处理完 pp 任务
    pps = [{"job_id": "pp-3", "steps": ["redact"]}]
    done = {}
    monkeypatch.setattr(worker.postprocess, "claim_next_queued",
                        lambda: pps.pop(0) if pps else None)
    monkeypatch.setattr(worker.pp_runner, "run", lambda pp: done.update(id=pp["job_id"]))
    threads, stop = worker.start_in_thread()
    for _ in range(60):
        if done:
            break
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert done.get("id") == "pp-3"
    assert not any(t.is_alive() for t in threads)


def test_watchdog_recovers_stale_pp_jobs(monkeypatch):
    # 看门狗周期回收卡死的后处理任务（与转录同款 stale 语义）
    calls = {"n": 0}
    monkeypatch.setattr(worker.config, "WATCHDOG_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(worker, "check_alerts", lambda: None)
    monkeypatch.setattr(worker, "refresh_balances", lambda: None)
    monkeypatch.setattr(worker.accounts, "sweep_unrefunded_failures", lambda: 0)
    monkeypatch.setattr(worker.jobstore, "fail_stale_queued", lambda *a, **k: 0)
    monkeypatch.setattr(worker.postprocess, "requeue_stale_running",
                        lambda mins, cap: calls.update(n=calls["n"] + 1, args=(mins, cap)) or 0)
    threads, stop = worker.start_in_thread(with_workers=False)
    for _ in range(150):
        if calls["n"] >= 2:
            break
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert calls["n"] >= 2   # 启动回收 1 次 + 周期至少 1 次
    assert calls["args"] == (worker.config.WATCHDOG_STALE_MIN, worker.config.WATCHDOG_MAX_ATTEMPTS)

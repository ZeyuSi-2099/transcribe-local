import json
import time

import pytest

import app.worker as worker
from app.jobstore import Job
from pipeline.transcript import Segment


@pytest.fixture(autouse=True)
def _isolate_postprocess(monkeypatch):
    # 本文件只测转录 worker：后处理挂点（同池轮询 pp 队列 + 看门狗 pp 回收）一律打桩空转，
    # 单测不许碰真实 DB。pp 侧行为在 test_worker_postprocess.py 单独测。
    monkeypatch.setattr(worker.postprocess, "claim_next_queued", lambda: None)
    monkeypatch.setattr(worker.postprocess, "requeue_stale_running", lambda *a, **k: 0)


def test_run_loop_in_thread_processes_queued_job(monkeypatch):
    # 进程内合一模式：start_in_thread 起的后台线程能取走队列作业并处理完
    jobs = [Job(id="jt", status="running", phase=None, progress=0, lang="zh",
                audio_key="audio/jt.m4a", result_key=None, error=None, recording_type="meeting")]
    done = {}
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: jobs.pop(0) if jobs else None)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda jid, key, metrics: done.update(jid=jid))
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None: ([Segment(t="00:00:01", s="x", sp=None)], []))
    threads, stop = worker.start_in_thread()
    for _ in range(60):           # 等线程处理掉队列（首轮即处理，无需等轮询间隔）
        if done:
            break
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert done.get("jid") == "jt"
    assert not any(t.is_alive() for t in threads)        # stop 后线程干净退出


def test_pool_concurrency_fuse_forces_single_worker_in_process_mode(monkeypatch):
    # 保险丝（硬约束）：进程内模式（FLY_DISPATCH≠1）下 MAX_CONCURRENT_JOBS>1 不安全——
    # 同进程任务共享同一个 P1LLM 分片状态文件，会互相污染护栏（误判整单/漏判有洞稿）。
    # 启动时必须强制降为 1，不再依赖注释提醒。两个排队作业仍会被这 1 个线程先后处理完。
    import threading as _th
    jobs = [Job(id=f"jp{i}", status="running", phase=None, progress=0, lang="zh",
                audio_key=f"audio/jp{i}.m4a", result_key=None, error=None, recording_type="meeting")
            for i in range(2)]
    lock = _th.Lock()
    done = []
    # 本机版（与线上不同）：没有 MAX_CONCURRENT_JOBS / FLY_DISPATCH 这两个旋钮，恒为 1 个工作线程

    def claim():
        with lock:
            return jobs.pop(0) if jobs else None

    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", claim)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda jid, key, metrics: done.append(jid))
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None: ([Segment(t="00:00:01", s="x", sp=None)], []))
    threads, stop = worker.start_in_thread()
    for _ in range(60):
        if len(done) >= 2:
            break
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert sum(1 for t in threads if t.name.startswith("worker-")) == 1  # 保险丝：只许 1 个工作线程
    assert sorted(done) == ["jp0", "jp1"]  # 串行照样都处理完、各一次


def test_run_loop_requeues_stale_on_start(monkeypatch):
    # 启动即回收上次崩溃留下的孤儿 running 作业（Render 免费层重启常见）
    called = {}
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running",
                        lambda *a, **k: called.update(n=1) or 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: None)
    threads, stop = worker.start_in_thread()
    for _ in range(60):
        if called:
            break
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert called.get("n") == 1   # 启动时调用了回收，且整个池只回收一次


def test_startup_recover_sweeps_unrefunded_failures(monkeypatch):
    # 启动回收后必须跑一次返还清扫：看门狗硬判失败的任务 + 上次崩溃窗漏返的任务全靠它补钱
    swept = []
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 1)
    monkeypatch.setattr(worker.accounts, "sweep_unrefunded_failures",
                        lambda: swept.append(1) or 1)
    worker._startup_recover()
    assert swept == [1]


def test_startup_recover_survives_sweep_failure(monkeypatch):
    # 清扫抛错（DB 瞬断）不许拖垮启动回收——原语幂等，下一轮看门狗会再扫
    def boom():
        raise RuntimeError("db 瞬断")

    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 1)
    monkeypatch.setattr(worker.accounts, "sweep_unrefunded_failures", boom)
    worker._startup_recover()   # 不应抛出


def test_run_loop_survives_claim_exception(monkeypatch):
    # 循环自身抛错（如 DB 瞬断）不许杀死线程——否则免费层 api 进程的 worker 会静默罢工
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise RuntimeError("DB 瞬断")

    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", flaky)
    threads, stop = worker.start_in_thread()
    for _ in range(60):
        if calls["n"] >= 1:
            break
        time.sleep(0.02)
    alive = any(t.is_alive() for t in threads)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert calls["n"] >= 1 and alive   # 抛错后线程仍存活


def test_watchdog_requeues_stale_periodically(monkeypatch):
    # 看门狗周期性回收卡死任务（不止启动那一次）——把 D「卡死一夜」变成「N 分钟内自愈」的核心
    calls = {"n": 0}

    def count_requeue(*a, **k):
        calls["n"] += 1
        return 0

    monkeypatch.setattr(worker.config, "WATCHDOG_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(worker, "check_alerts", lambda: None)   # 本测试只验周期回收，隔离告警的真实 DB 调用
    monkeypatch.setattr(worker, "refresh_balances", lambda: None)   # 同上：隔离余额刷新的真实外部调用
    monkeypatch.setattr(worker.jobstore, "fail_stale_queued", lambda *a, **k: 0)    # 同上
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", count_requeue)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: None)
    threads, stop = worker.start_in_thread()
    for _ in range(750):        # 上限 15s：正常 0.2s 内就 break，放宽只影响真失败时的等待
        if calls["n"] >= 3:        # 启动 1 次 + 周期至少 2 次
            break
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert calls["n"] >= 3                            # 周期性运行，不止启动那次
    assert not any(t.is_alive() for t in threads)     # stop 后全部线程（含看门狗）干净退出


def test_watchdog_releases_stale_queued_periodically(monkeypatch):
    # 看门狗周期释放排队超时任务（queued 超 QUEUED_MAX_HOURS → 判失败，预扣随清扫返还）——
    # 否则派单通道永久坏死时，用户的冻结款无限期卡住
    calls = {"n": 0}
    monkeypatch.setattr(worker.config, "WATCHDOG_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(worker, "check_alerts", lambda: None)
    monkeypatch.setattr(worker, "refresh_balances", lambda: None)
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    monkeypatch.setattr(worker.jobstore, "fail_stale_queued",
                        lambda hours: calls.update(n=calls["n"] + 1, hours=hours) or 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: None)
    threads, stop = worker.start_in_thread()   # 本机版没有「只起看门狗」的模式；工作线程领不到单，空转
    for _ in range(750):        # 上限 15s：正常 0.2s 内就 break，放宽只影响真失败时的等待
        if calls["n"] >= 1:
            break
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert calls["n"] >= 1
    assert calls["hours"] == worker.config.QUEUED_MAX_HOURS   # 用配置旋钮，不是写死值


def test_process_one_runs_pipeline_and_stores_result(monkeypatch):
    job = Job(id="j1", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j1.m4a", result_key=None, error=None,
              recording_type="phonecall")
    events, saved, blobbed, seen = [], {}, {}, {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress",
                        lambda jid, ph, pr, mx: events.append((jid, ph, pr, mx)))
    monkeypatch.setattr(worker.jobstore, "set_done", lambda jid, key, metrics: saved.update(done=(jid, key, metrics)))
    monkeypatch.setattr(worker.jobstore, "set_failed", lambda jid, err: saved.update(failed=(jid, err)))
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"AUDIO"))
    monkeypatch.setattr(worker.blobstore, "put_bytes",
                        lambda key, data, content_type="application/json": blobbed.update({key: data}))

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        seen["lang"] = lang
        on_phase("P1", 15, {"chars": 100})
        return [Segment(t="00:00:01", s="你好", sp="主持人")], [{"type": "entity", "term": "你好"}]

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)

    assert worker.process_one() is True
    assert saved["done"][0] == "j1"
    assert json.loads(blobbed[saved["done"][1]])[0]["s"] == "你好"
    assert ("j1", "P1", 15, {"chars": 100}) in events
    # 录音类型 2026-08-18 起不再传进管线（字段仍在 DB 里，只是不影响选路）
    assert saved["done"][2]["chars"] == 100      # 末次 metrics 随 set_done 落库
    assert "elapsedSec" in saved["done"][2]        # 转录用时已记
    assert json.loads(blobbed["review/j1.json"])[0]["term"] == "你好"   # 复核清单已存


def test_p3_report_is_archived_alongside_the_review(monkeypatch):
    """P3 报告必须留档：复核清单整个是从它解析出来的，卡片一旦不对（原因串了、少了几处），
    它是唯一能对质的原件。机器跑完自毁、workdir 也在 finally 里删——不上传就永远查不了
    （2026-08-12 排查「存疑卡列出全篇同词」时就卡在这一步：报告已灰飞烟灭，只能靠推断）。
    """
    job = Job(id="jr", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jr.m4a", result_key=None, error=None, recording_type="meeting")
    blobbed = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes",
                        lambda key, data, content_type="application/json": blobbed.update({key: data}))

    def fake_transcribe(p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        on_report("# P3 Merge 报告\n\n## 存疑\n| 时间码 | 终稿写法 | 原因 |\n")
        return [Segment(t="00:00:01", s="x", sp=None)], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    # 挂 review/ 前缀 = 复用它 30 天的 R2 生命周期，与复核清单同寿
    assert "P3 Merge 报告" in blobbed["review/jr.report.md"].decode("utf-8")


def _dump_env(monkeypatch, tmp_path, job):
    """把 vendor/Output 指到临时目录，造几份「本单跑出来的」中间稿。→ (上传记录 dict)"""
    out = tmp_path / "Output"
    (out / "_p3in").mkdir(parents=True)
    for rel in ("zh_a_P1_ELV_1.md", "zh_a_P1_GEM_1.md", "zh_a_P2_Match_1.md",
                "_p3in/zh_a_P2_Match_1.md", "zh_a_P3_Merge_OPUS_1.md"):
        (out / rel).write_text("稿", encoding="utf-8")
    monkeypatch.setattr(worker, "VENDOR_ROOT", tmp_path)
    put = {}
    monkeypatch.setattr(worker.blobstore, "put_file", lambda key, path: put.update({key: path}))
    monkeypatch.setattr(worker.blobstore, "put_bytes",
                        lambda key, data, content_type="application/json": put.update({key: data}))
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    return put


def test_中间产物留档到review前缀下(monkeypatch, tmp_path):
    """P1/P2/P3 各阶段稿必须留档，否则跑完就永远查不到（Fly 一机一任务、跑完自毁）。
    键挂在 `review/<id>/` 下 = 复用它 30 天的 R2 生命周期，不必另加规则。"""
    job = Job(id="jd", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jd.m4a", result_key=None, error=None, recording_type="meeting")
    put = _dump_env(monkeypatch, tmp_path, job)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, **k: ([Segment(t="00:00:01", s="x", sp=None)], []))

    assert worker.process_one() is True
    keys = {k for k in put if k.startswith("review/jd/pipeline/")}
    assert "review/jd/pipeline/zh_a_P1_GEM_1.md" in keys
    assert "review/jd/pipeline/_p3in/zh_a_P2_Match_1.md" in keys, "P3 输入副本没留，分歧点清单就查不到"
    manifest = json.loads(put["review/jd/pipeline/manifest.json"])
    assert manifest["jobId"] == "jd" and len(manifest["files"]) == 5


def test_失败的单也要留档(monkeypatch, tmp_path):
    """**失败的单最需要中间产物**。只在成功分支留档 = 只有不需要诊断的时候才留证据。"""
    job = Job(id="jf", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jf.m4a", result_key=None, error=None, recording_type="meeting")
    put = _dump_env(monkeypatch, tmp_path, job)
    monkeypatch.setattr(worker.jobstore, "set_failed", lambda jid, err, public=None: True)
    monkeypatch.setattr(worker.jobstore, "set_done",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应 done")))

    def boom(p, **k):
        raise RuntimeError("P3 炸了")

    monkeypatch.setattr(worker, "transcribe", boom)
    assert worker.process_one() is True
    assert "review/jf/pipeline/zh_a_P2_Match_1.md" in put, "失败单没留档"


def test_留档挂了不许改变这一单的成败(monkeypatch, tmp_path):
    """留档是旁路：R2 抽风不该把一单成功的转录变成失败。"""
    job = Job(id="jx", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jx.m4a", result_key=None, error=None, recording_type="meeting")
    _dump_env(monkeypatch, tmp_path, job)
    done = {}
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: done.update(v=1) or True)
    monkeypatch.setattr(worker.blobstore, "put_file",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("R2 挂了")))
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, **k: ([Segment(t="00:00:01", s="x", sp=None)], []))
    assert worker.process_one() is True
    assert done, "留档失败把成功单拖下水了"


def test_开关关掉就不留档(monkeypatch, tmp_path):
    job = Job(id="jo", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jo.m4a", result_key=None, error=None, recording_type="meeting")
    put = _dump_env(monkeypatch, tmp_path, job)
    monkeypatch.setenv("PIPELINE_DUMP", "0")
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, **k: ([Segment(t="00:00:01", s="x", sp=None)], []))
    assert worker.process_one() is True
    assert not [k for k in put if "/pipeline/" in k]


def test_process_one_returns_false_when_no_job(monkeypatch):
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: None)
    assert worker.process_one() is False


def test_process_one_marks_failed_on_exception(monkeypatch):
    job = Job(id="j2", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j2.m4a", result_key=None, error=None,
              user_email="a@b.com", reserved_cents=100)
    fail = {}
    refunded = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a: None)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"X"))
    monkeypatch.setattr(worker.jobstore, "set_failed",
                        lambda jid, err, public=None: fail.update(v=(jid, err, public)) or True)
    monkeypatch.setattr(worker.jobstore, "set_done",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应 done")))

    def boom(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        raise RuntimeError("pipeline 炸了")

    monkeypatch.setattr(worker, "transcribe", boom)
    assert worker.process_one() is True
    assert fail["v"][0] == "j2" and "pipeline 炸了" in fail["v"][1]
    # E2 脱敏：内部 traceback（含 "pipeline 炸了"）不得混进用户话术
    # 本机版：不传话术时由 jobstore 落默认话术；本机不收费，没有返还
    public = fail["v"][2] or worker.jobstore._DEFAULT_ERROR_PUBLIC
    assert public and "pipeline 炸了" not in public
    assert refunded == {}


def test_process_one_no_refund_when_set_failed_loses_race(monkeypatch):
    # 双跑门控：set_failed 返回 False（本次未真从 running 转 failed，如另一侧已先落终态）→
    # 不调返还，省一次注定 no-op 的调用（真正防双退的是原语的 refunded_cents 幂等闸）
    job = Job(id="j2c", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j2c.m4a", result_key=None, error=None,
              user_email="a@b.com", reserved_cents=100)
    refunded = []
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a: None)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"X"))
    monkeypatch.setattr(worker.jobstore, "set_failed", lambda jid, err, public=None: False)   # 输了这局
    monkeypatch.setattr(worker.accounts, "refund_job_reservation", lambda *a, **k: refunded.append(a))

    def boom(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        raise RuntimeError("pipeline 炸了")

    monkeypatch.setattr(worker, "transcribe", boom)
    assert worker.process_one() is True
    assert refunded == []


def test_process_one_settles_user_on_done(monkeypatch):
    # 结算：done 后按真实时长算实际价（40s → round(40/60*150)=100 分），带 job.reserved_cents 结算
    job = Job(id="j3", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j3.m4a", result_key=None, error=None,
              recording_type="meeting", user_email="a@b.com", file_name="访谈.m4a",
              reserved_cents=120)
    settled = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda gid: "")
    monkeypatch.setattr(worker.accounts, "settle_job",
                        lambda email, jid, fn, lang, dur, actual_cents, reserved_cents: settled.update(
                            email=email, jid=jid, fn=fn, dur=dur, actual_cents=actual_cents,
                            reserved_cents=reserved_cents))

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        on_phase("P3", 80, {"durationSec": 40})
        return [Segment(t="00:00:01", s="你好", sp="主持人")], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert settled == {"email": "a@b.com", "jid": "j3", "fn": "访谈.m4a", "dur": 40,
                        "actual_cents": 100, "reserved_cents": 120}


def test_process_one_settles_with_zero_reserved_when_none(monkeypatch):
    # 老任务无预扣记录（reserved_cents=None）→ 结算时兜底传 0，不崩、等价旧「按完成扣」行为
    job = Job(id="j3b", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j3b.m4a", result_key=None, error=None,
              recording_type="meeting", user_email="a@b.com", file_name="访谈.m4a",
              reserved_cents=None)
    settled = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda gid: "")
    monkeypatch.setattr(worker.accounts, "settle_job",
                        lambda email, jid, fn, lang, dur, actual_cents, reserved_cents: settled.update(
                            reserved_cents=reserved_cents))

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        on_phase("P3", 80, {"durationSec": 40})
        return [Segment(t="00:00:01", s="你好", sp="主持人")], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert settled["reserved_cents"] == 0


def test_process_one_no_settle_when_set_done_loses_race(monkeypatch):
    # 双跑门控：set_done 返回 False（本次未真从 running 转 done，如慢的一方棋输一着）→
    # settle_job 不该被调，防重复结算/白退
    job = Job(id="j3c", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j3c.mp4", result_key=None, error=None,
              recording_type="meeting", user_email="a@b.com", file_name="访谈.mp4",
              reserved_cents=120)
    called = []
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: False)   # 输了这局
    monkeypatch.setattr(worker.jobstore, "set_audio_key", lambda *a, **k: called.append("set_audio_key"))
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "put_file", lambda key, path: called.append("put_file"))
    monkeypatch.setattr(worker.blobstore, "delete", lambda key: called.append("delete"))
    monkeypatch.setattr(worker, "_transcode_to_m4a", lambda flac, out: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda gid: "")
    # 本机版：没有结算与讯飞扣减；set_done 输掉的对手是「用户点了取消」

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        if on_flac:
            on_flac("/tmp/out.flac")   # 产生 audio_key，验证 won=False 时连它也不该切换/删原文件
        on_phase("P3", 80, {"durationSec": 40, "engines": {"XF": {"status": "done"}}})
        return [Segment(t="00:00:01", s="你好", sp="主持人")], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    # put_file 是 P0 处理管线自身的一步（转录过程中就发生，与 won 无关，双跑双方都会各存一份
    # m4a，无害、不重复扣费）；但 won=False 后，「切换指针 / 删原文件 / 结算 / 讯飞扣减」这些
    # 只该赢家做一次的动作，全部不该发生
    assert called == ["put_file"]


def test_process_one_no_duration_falls_back_to_job_duration(monkeypatch):
    # metrics 里没 durationSec（P0 探测失败等）→ 兜底 job.duration_sec（上传时 ffprobe 实测，
    # 新任务必有）照常结算——否则预扣款既不结算也不退，冻死无痕
    job = Job(id="j4a", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j4a.m4a", result_key=None, error=None, user_email="a@b.com",
              file_name="访谈.m4a", duration_sec=40, reserved_cents=120)
    settled = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda gid: "")
    monkeypatch.setattr(worker.accounts, "settle_job",
                        lambda email, jid, fn, lang, dur, actual_cents, reserved_cents: settled.update(
                            dur=dur, actual_cents=actual_cents, reserved_cents=reserved_cents))
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None: ([Segment(t="00:00:01", s="x", sp=None)], []))
    assert worker.process_one() is True
    assert settled == {"dur": 40, "actual_cents": 100, "reserved_cents": 120}


def test_process_one_no_duration_anywhere_refunds_reservation(monkeypatch):
    # metrics 和 job.duration_sec 都拿不到（老任务/极端）→ 无法计费：退回预扣，
    # 宁可白送这单也不冻死用户的钱；settle 不该被调（不乱算）
    job = Job(id="j4b", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j4b.m4a", result_key=None, error=None, user_email="a@b.com",
              duration_sec=None, reserved_cents=120)
    called, refunded = [], []
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda gid: "")
    monkeypatch.setattr(worker.accounts, "settle_job", lambda *a, **k: called.append(a))
    monkeypatch.setattr(worker.accounts, "refund_job_reservation", lambda jid: refunded.append(jid) or 120)
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None: ([Segment(t="00:00:01", s="x", sp=None)], []))
    assert worker.process_one() is True
    assert called == []
    assert refunded == ["j4b"]


def test_process_one_stays_done_even_if_set_audio_key_raises(monkeypatch):
    # 赢家杂务（切回放指针/删原文件）任何一步抛异常都不该把已 done 的单拖进失败分支。
    # 本机版（与线上不同）：线上这条守的是「杂务抛错不许吞掉结算」，本机不收费，守剩下的那半。
    job = Job(id="j4c", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/j4c.mp4", result_key=None, error=None, user_email="a@b.com",
              file_name="访谈.mp4", reserved_cents=120)
    failed = []
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.jobstore, "set_audio_key", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db 瞬断")))
    monkeypatch.setattr(worker.jobstore, "set_failed", lambda *a, **k: failed.append(a) or False)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "put_file", lambda key, path: None)
    monkeypatch.setattr(worker.blobstore, "delete", lambda key: None)
    monkeypatch.setattr(worker, "_transcode_to_m4a", lambda flac, out: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda gid: "")

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        if on_flac:
            on_flac("/tmp/out.flac")   # 产生 audio_key，触发 set_audio_key 杂务路径
        on_phase("P3", 80, {"durationSec": 40})
        return [Segment(t="00:00:01", s="你好", sp="主持人")], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert failed == []             # 杂务抛错没有把成功单判成失败


def test_process_one_stores_m4a_and_deletes_raw(monkeypatch):
    # 转录成功后：P0 的 FLAC 转成小体积 m4a 作回放、audio_key 切到 .m4a、删原始上传（视频）
    job = Job(id="jf", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jf.mp4", result_key=None, error=None, recording_type="meeting")
    moves = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.jobstore, "set_audio_key", lambda jid, key: moves.update(switched=(jid, key)))
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"VIDEO"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "put_file", lambda key, path: moves.update(put_file=key))
    monkeypatch.setattr(worker.blobstore, "delete", lambda key: moves.update(deleted=key))
    monkeypatch.setattr(worker, "_transcode_to_m4a", lambda flac, out: None)  # 不真跑 ffmpeg

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        if on_flac:
            on_flac("/tmp/out.flac")   # 模拟 P0 抽出完整 FLAC
        return [Segment(t="00:00:01", s="你好", sp="主持人")], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert moves["put_file"] == "audio/jf.m4a"           # 回放 m4a 已存
    assert moves["switched"] == ("jf", "audio/jf.m4a")   # 回放切到 m4a
    assert moves["deleted"] == "audio/jf.mp4"            # 原视频已删


def test_process_one_flac_upload_also_transcoded_to_m4a(monkeypatch):
    # 原始上传本就是 .flac：仍转成 .m4a 回放（更省空间），原 .flac 与新 m4a 不同 key → 删原 .flac
    job = Job(id="jg", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jg.flac", result_key=None, error=None, recording_type="meeting")
    moves = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.jobstore, "set_audio_key", lambda jid, key: moves.update(switched=(jid, key)))
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"FLAC"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "put_file", lambda key, path: moves.update(put_file=key))
    monkeypatch.setattr(worker.blobstore, "delete", lambda key: moves.update(deleted=key))
    monkeypatch.setattr(worker, "_transcode_to_m4a", lambda flac, out: None)

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        if on_flac:
            on_flac("/tmp/out.flac")
        return [Segment(t="00:00:01", s="x", sp=None)], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert moves["put_file"] == "audio/jg.m4a"           # 回放 m4a
    assert moves["deleted"] == "audio/jg.flac"           # 原 .flac 已删


def test_process_one_cleans_workdir(monkeypatch):
    # 转完（无论成败）整个任务工作目录都要删掉——防中间文件堆满 worker 临时盘
    import os as _os
    job = Job(id="jw", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jw.m4a", result_key=None, error=None, recording_type="meeting")
    seen = {}
    real_mkdtemp = worker.tempfile.mkdtemp

    def spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        seen["dir"] = d
        return d

    monkeypatch.setattr(worker.tempfile, "mkdtemp", spy_mkdtemp)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)

    def fake_transcribe(audio_path, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        # 模拟 pipeline 在工作目录里吐中间文件
        open(_os.path.join(_os.path.dirname(audio_path), "junk.md"), "w").write("x")
        return [Segment(t="00:00:01", s="x", sp=None)], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert seen["dir"] and not _os.path.exists(seen["dir"])   # 工作目录已整个清掉


def test_process_one_injects_user_glossary(monkeypatch):
    # P3 注入：worker 按 job.glossary_id 取该本术语库，透传给 transcribe（再进 P3）
    job = Job(id="jgl", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jgl.m4a", result_key=None, error=None,
              recording_type="meeting", user_email="a@b.com", glossary_id="gl-1")
    seen = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content",
                        lambda gid: "FD ｜ 履约分销" if gid == "gl-1" else "")

    def fake_transcribe(p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        seen["glossary"] = glossary_text
        return [Segment(t="00:00:01", s="x", sp=None)], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert seen["glossary"] == "FD ｜ 履约分销"


def test_process_one_consumes_xunfei_hours_on_meeting(monkeypatch):
    # 讯飞用量倒扣：用了 XF 引擎(meeting)的任务按音频时长扣讯飞小时（3600s → 1 小时）
    job = Job(id="jx", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jx.m4a", result_key=None, error=None, recording_type="meeting")
    consumed = {}
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.balances, "consume", lambda v, amt: consumed.update(vendor=v, amt=amt))

    def fake_transcribe(p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        on_phase("P1", 20, {"durationSec": 3600, "engines": {"G25F": {"status": "done"}, "XF": {"status": "done"}}})
        return [Segment(t="00:00:01", s="x", sp=None)], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert consumed == {"vendor": "讯飞", "amt": 1.0}


def test_process_one_no_xunfei_consume_without_xf(monkeypatch):
    # 没用 XF（如电话访谈 3 路）→ 不扣讯飞
    job = Job(id="jp", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jp.m4a", result_key=None, error=None, recording_type="phonecall")
    consumed = []
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: job)
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.balances, "consume", lambda v, amt: consumed.append((v, amt)))

    def fake_transcribe(p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None):
        on_phase("P1", 20, {"durationSec": 1800, "engines": {"G25F": {"status": "done"}, "DB": {"status": "done"}, "FA": {"status": "done"}}})
        return [Segment(t="00:00:01", s="x", sp=None)], []

    monkeypatch.setattr(worker, "transcribe", fake_transcribe)
    assert worker.process_one() is True
    assert consumed == []   # 无 XF → 不扣讯飞


def _quiet_others(monkeypatch):
    """把 check_alerts 里**除被测信号之外**的来源全静音。

    每加一个新信号源都要往这里补一行——漏了的话，那些「这一轮不该发告警」的用例
    会因为别的信号真的响了而变红，看起来像被测的那条坏了，其实是隔壁在响。
    （备份检查那一条尤其要静音：不打桩它会去真连 R2。）"""
    monkeypatch.setattr(worker.balances, "check_low_balances", lambda: [])
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 0.5)
    monkeypatch.setattr(worker.alerts, "purge_old", lambda *a, **k: 0)


def test_check_alerts_fires_on_high_failure_rate(monkeypatch):
    # 近窗口失败率 ≥ 阈值且样本足 → 发失败率告警
    monkeypatch.setattr(worker.jobstore, "recent_failure_stats", lambda m: (2, 8))  # 8/10 = 80%
    monkeypatch.setattr(worker.config, "ALERT_FAILRATE_MIN_SAMPLE", 5)
    monkeypatch.setattr(worker.config, "ALERT_FAILRATE_PCT", 40)
    _quiet_others(monkeypatch)
    sent = []
    monkeypatch.setattr(worker.alerts, "send_admin_alert", lambda *a, **k: sent.append(k.get("key")) or True)
    worker.check_alerts()
    assert "failrate" in sent


def test_check_alerts_quiet_below_threshold(monkeypatch):
    monkeypatch.setattr(worker.jobstore, "recent_failure_stats", lambda m: (9, 1))  # 10%
    monkeypatch.setattr(worker.config, "ALERT_FAILRATE_MIN_SAMPLE", 5)
    monkeypatch.setattr(worker.config, "ALERT_FAILRATE_PCT", 40)
    _quiet_others(monkeypatch)
    sent = []
    monkeypatch.setattr(worker.alerts, "send_admin_alert", lambda *a, **k: sent.append(1) or True)
    worker.check_alerts()
    assert sent == []


def test_check_alerts_quiet_on_small_sample(monkeypatch):
    # 失败率 100% 但样本 2 < 5 → 不告警（防偶发误报）
    monkeypatch.setattr(worker.jobstore, "recent_failure_stats", lambda m: (0, 2))
    monkeypatch.setattr(worker.config, "ALERT_FAILRATE_MIN_SAMPLE", 5)
    _quiet_others(monkeypatch)
    sent = []
    monkeypatch.setattr(worker.alerts, "send_admin_alert", lambda *a, **k: sent.append(1) or True)
    worker.check_alerts()
    assert sent == []


def test_check_alerts_skips_balance_email_by_default(monkeypatch):
    # 余额预警改面板优先：BALANCE_ALERT_EMAIL 缺省关 → 看门狗不查低余额、不发邮件（省 Resend）
    monkeypatch.setattr(worker.jobstore, "recent_failure_stats", lambda m: (0, 0))
    monkeypatch.setattr(worker.config, "BALANCE_ALERT_EMAIL", False)
    called = []
    monkeypatch.setattr(worker.balances, "check_low_balances", lambda: called.append(1) or [])
    monkeypatch.setattr(worker.alerts, "send_admin_alert", lambda *a, **k: True)
    worker.check_alerts()
    assert called == []   # 默认不触发低余额邮件路径（面板红标已足够）


def test_check_alerts_balance_email_when_enabled(monkeypatch):
    # 显式开 BALANCE_ALERT_EMAIL 才走低余额邮件检查
    monkeypatch.setattr(worker.jobstore, "recent_failure_stats", lambda m: (0, 0))
    monkeypatch.setattr(worker.config, "BALANCE_ALERT_EMAIL", True)
    called = []
    monkeypatch.setattr(worker.balances, "check_low_balances", lambda: called.append(1) or [])
    monkeypatch.setattr(worker.alerts, "send_admin_alert", lambda *a, **k: True)
    worker.check_alerts()
    assert called == [1]


def test_refresh_balances_calls_refresh_all(monkeypatch):
    # 看门狗周期里的余额刷新 = 调 balances.refresh_all
    called = []
    monkeypatch.setattr(worker.balances, "refresh_all",
                        lambda: called.append(1) or [{"vendor": "X", "amountCny": 1}])
    worker.refresh_balances()
    assert called == [1]


def test_watchdog_refreshes_balances_periodically(monkeypatch):
    # 看门狗按 BALANCE_REFRESH_MIN 周期拉余额（设 0 → 每轮都到期）；mock 掉真实拉取
    monkeypatch.setattr(worker.config, "WATCHDOG_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(worker.config, "BALANCE_REFRESH_MIN", 0)
    monkeypatch.setattr(worker, "check_alerts", lambda: None)
    monkeypatch.setattr(worker.jobstore, "fail_stale_queued", lambda *a, **k: 0)    # 隔离真实 DB
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    monkeypatch.setattr(worker.jobstore, "claim_next_queued", lambda: None)
    cnt = {"n": 0}
    monkeypatch.setattr(worker, "refresh_balances", lambda: cnt.update(n=cnt["n"] + 1))
    threads, stop = worker.start_in_thread()
    for _ in range(750):        # 上限 15s：正常 0.2s 内就 break，放宽只影响真失败时的等待
        if cnt["n"] >= 2:
            break
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert cnt["n"] >= 2   # 周期性刷新，不止一次


class _FakeJob:
    def __init__(self, jid):
        self.id = jid


def test_run_one_job_injects_gate_claims_and_processes(monkeypatch):
    # 任务级机器入口：注入 slot_id=job_id 的全局闸 + claim 指定 job + 处理
    from pipeline import skill_merge
    gate_set = {}
    monkeypatch.setattr(skill_merge, "set_claude_gate", lambda g: gate_set.update(slot=getattr(g, "slot_id", None)))
    monkeypatch.setattr(worker.jobstore, "claim_specific", lambda jid: _FakeJob(jid))
    processed = {}
    monkeypatch.setattr(worker, "_process_job", lambda job: processed.update(id=job.id))
    assert worker.run_one_job("job-123") is True
    assert gate_set["slot"] == "job-123"       # 注入了 slot_id=job_id 的 DB 闸
    assert processed["id"] == "job-123"


def test_run_one_job_skips_when_not_queued(monkeypatch):
    # 指定 job 非 queued（已被领/不存在）→ 不处理，返回 False
    from pipeline import skill_merge
    monkeypatch.setattr(skill_merge, "set_claude_gate", lambda g: None)
    monkeypatch.setattr(worker.jobstore, "claim_specific", lambda jid: None)
    called = {}
    monkeypatch.setattr(worker, "_process_job", lambda job: called.update(x=1))
    assert worker.run_one_job("missing") is False
    assert "x" not in called


def test_main_one_job_runs_job_from_env(monkeypatch):
    # Fly 机器入口：读 env JOB_ID 跑该 job，成功 → 退出码 0
    monkeypatch.setenv("JOB_ID", "job-xyz")
    seen = {}
    monkeypatch.setattr(worker, "run_one_job", lambda jid: seen.update(id=jid) or True)
    assert worker.main_one_job() == 0
    assert seen["id"] == "job-xyz"


def test_main_one_job_missing_env_returns_2(monkeypatch):
    # 缺 JOB_ID → 退出码 2，不调 run_one_job
    monkeypatch.delenv("JOB_ID", raising=False)
    called = {}
    monkeypatch.setattr(worker, "run_one_job", lambda jid: called.update(x=1) or True)
    assert worker.main_one_job() == 2
    assert "x" not in called


def test_main_one_job_not_claimed_returns_1(monkeypatch):
    # 指定 job 没领到（已被领/不存在）→ 退出码 1
    monkeypatch.setenv("JOB_ID", "taken")
    monkeypatch.setattr(worker, "run_one_job", lambda jid: False)
    assert worker.main_one_job() == 1


def test_start_in_thread_dispatch_mode_only_watchdog(monkeypatch):
    # Fly 派单模式（with_workers=False）：只起看门狗，不起处理线程（转录在 Fly 机器上跑）
    monkeypatch.setattr(worker.config, "WATCHDOG_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(worker.config, "FLY_DISPATCH", False)   # 隔离派单，只验线程构成
    monkeypatch.setattr(worker, "check_alerts", lambda: None)
    monkeypatch.setattr(worker, "refresh_balances", lambda: None)
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    threads, stop = worker.start_in_thread(with_workers=False)
    try:
        names = [t.name for t in threads]
        assert "watchdog" in names                              # 有看门狗
        assert not any(n.startswith("worker-") for n in names)  # 无处理线程
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=3)


def test_watchdog_dispatches_when_fly_enabled(monkeypatch):
    # FLY_DISPATCH 开 → 看门狗每轮顺带派单（机器自毁腾名额后补起下一台）
    from app import fly_machines
    monkeypatch.setattr(worker.config, "WATCHDOG_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(worker.config, "FLY_DISPATCH", True)
    monkeypatch.setattr(worker, "check_alerts", lambda: None)
    monkeypatch.setattr(worker, "refresh_balances", lambda: None)
    monkeypatch.setattr(worker.accounts, "sweep_unrefunded_failures", lambda: 0)   # 隔离真实 DB
    monkeypatch.setattr(worker.jobstore, "fail_stale_queued", lambda *a, **k: 0)    # 同上
    monkeypatch.setattr(worker.jobstore, "requeue_stale_running", lambda *a, **k: 0)
    calls = {"n": 0}
    monkeypatch.setattr(fly_machines, "dispatch_pending", lambda: calls.update(n=calls["n"] + 1) or 0)
    threads, stop = worker.start_in_thread(with_workers=False)
    for _ in range(750):        # 上限 15s：正常 0.2s 内就 break，放宽只影响真失败时的等待
        if calls["n"] >= 2:
            break
        time.sleep(0.02)
    stop.set()
    for t in threads:
        t.join(timeout=3)
    assert calls["n"] >= 2   # 周期性派单，不止一次


def _settle_probe(monkeypatch, job, duration_sec):
    """跑一遍 _process_job，把 settle_job 收到的 actual_cents 截下来。"""
    got = {}
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.jobstore, "set_done", lambda *a, **k: True)
    monkeypatch.setattr(worker.jobstore, "set_audio_key", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker.blobstore, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "delete", lambda *a, **k: None)
    monkeypatch.setattr(worker.glossary, "get_glossary_content", lambda *a, **k: "")
    monkeypatch.setattr(worker, "_transcode_to_m4a", lambda *a, **k: None)
    monkeypatch.setattr(worker, "transcribe",
                        lambda p, on_phase=None, on_flac=None, glossary_text="", lang="zh", on_report=None, ui_lang=None:
                        ([Segment(t="00:00:01", s="x", sp=None)], []))
    monkeypatch.setattr(worker.accounts, "settle_job",
                        lambda email, jid, fn, lang, dur, actual_cents, reserved_cents: got.update(cents=actual_cents))
    monkeypatch.setattr(worker.accounts, "refund_job_reservation", lambda *a, **k: None)
    job.duration_sec = duration_sec
    worker._process_job(job)
    return got.get("cents")


def _billable_job(lang, rate_cents_per_min):
    return Job(id="js", status="running", phase=None, progress=0, lang=lang,
               audio_key="audio/js.m4a", result_key=None, error=None, recording_type="interview",
               user_email="u@x.com", reserved_cents=999, rate_cents_per_min=rate_cents_per_min)


def test_settles_at_the_rate_snapshotted_on_the_job_not_the_current_global_price(monkeypatch):
    """结算认本单上传时钉死的费率，不认当下的全局价——促销到期动不了已上传的单。"""
    cents = _settle_probe(monkeypatch, _billable_job("zh", 10), duration_sec=120)
    assert cents == 20          # 2 分钟 × 10 分/分钟（中文促销快照）


def test_settles_a_pricier_language_higher_for_the_same_duration(monkeypatch):
    cents = _settle_probe(monkeypatch, _billable_job("ru", 100), duration_sec=120)
    assert cents == 200         # 同样 2 分钟，小语种档快照 100 分/分钟


def test_legacy_job_without_a_snapshot_settles_at_the_old_uniform_price(monkeypatch):
    """多价制之前建的 job 没有费率快照 → 兜底当年的全语种统一价，不能白送也不能乱扣。"""
    cents = _settle_probe(monkeypatch, _billable_job("zh", None), duration_sec=120)
    assert cents == 2 * worker.pricing.LEGACY_RATE_CENTS


def test_settles_only_the_paid_seconds_after_free_quota(monkeypatch):
    """免费额度（定价 V2 §2）：结算只按付费秒 = 时长 - 上传时锁定的免费秒。"""
    job = _billable_job("zh", 100)
    job.free_seconds = 60
    cents = _settle_probe(monkeypatch, job, duration_sec=120)
    assert cents == 100         # (120-60)s ÷ 60 × 100 分/分钟


def test_fully_free_job_settles_at_zero(monkeypatch):
    """全免费单结算 0 分：仍要写 charge 行占住「已结算」语义（防返还原语误退）。"""
    job = _billable_job("zh", 100)
    job.free_seconds = 600      # 免费秒 ≥ 时长（管线实测略短也不许出负数）
    cents = _settle_probe(monkeypatch, job, duration_sec=120)
    assert cents == 0


# ── 备份停摆检查（2026-08-14）────────────────────────────────────────────────
# 备份的三种停法全是静默的（额度耗尽 / 仓库 60 天无活动被停用 / secret 过期），
# 你只会在真要恢复的那天才发现最新一份是三周前的。所以这一条必须是 act 档。

def _catch_alerts(monkeypatch):
    got = []
    monkeypatch.setattr(worker.alerts, "send_admin_alert",
                        lambda subj, body, **k: got.append((subj, k.get("tier"))))
    return got


def _catch_alert_bodies(monkeypatch):
    """连正文一起收。告警**正文写的排查方向**本身就是要守的东西——
    2026-08-27 之前那三条成因（额度/60天/secret）全不对，真凶是 GitHub 排期跳过，
    照着排查会一路走偏。"""
    got = []
    monkeypatch.setattr(worker.alerts, "send_admin_alert",
                        lambda subj, body, **k: got.append(body))
    return got


def test_stale_backup_alerts_as_act(monkeypatch):
    got = _catch_alerts(monkeypatch)
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 40.0)
    worker.check_backup_freshness()
    assert len(got) == 1
    assert "40 小时" in got[0][0]
    assert got[0][1] == "act"        # 不动手它永远不会好 → 进待办条


def test_fresh_backup_is_quiet(monkeypatch):
    got = _catch_alerts(monkeypatch)
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 1.2)
    worker.check_backup_freshness()
    assert got == []


def test_unknown_age_does_not_alert(monkeypatch):
    """查不到年龄是**监控自己**的故障，不是备份的故障。报出来只会稀释这条的可信度，
    而下一轮还会再查——真停了不会漏。"""
    got = _catch_alerts(monkeypatch)
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: None)
    worker.check_backup_freshness()
    assert got == []


def test_blobstore_failure_never_breaks_watchdog(monkeypatch):
    """监控坏了不许连累看门狗——它还得去回收卡死任务、返还预扣款。"""
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours",
                        lambda p: (_ for _ in ()).throw(RuntimeError("R2 挂了")))
    worker.check_backup_freshness()      # 不抛就算过


# ── 漏跑自愈（2026-08-27）───────────────────────────────────────────────
# 起因不是「备份失败」——80 次运行 conclusion 全是 success，一次没失败过。
# 是 GitHub 的 schedule 整段跳过：cron 写 :12，实际触发点在 :05–:58 随机漂，
# 79 个间隔里 10 个被跳过，最坏一次空了 10 小时。所以补的是**触发**，不是重试。

def _catch_dispatch(monkeypatch, status=204):
    """拦住真实的 GitHub 调用，记下发了几次。"""
    calls = []

    class _Resp:
        status_code = status
        text = ""

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(worker.requests, "post", fake_post)
    monkeypatch.setattr(worker.config, "BACKUP_DISPATCH_TOKEN", "tok")
    worker._last_backup_dispatch = 0.0        # 清掉上一条用例留下的节流
    return calls


def test_漏跑先补触发_还没到告警阈值就不惊动人(monkeypatch):
    """2–6 小时是排期抖动的常态。补一次就好了，不该为此发信——
    每次抖动都告警的话，这条告警很快就会被当成噪音忽略掉，真出事那次也一起漏了。"""
    got = _catch_alerts(monkeypatch)
    calls = _catch_dispatch(monkeypatch)
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 3.0)
    worker.check_backup_freshness()
    assert len(calls) == 1
    assert "db-backup.yml/dispatches" in calls[0]
    assert got == []                      # 补触发了，但没吵人


def test_真出事时补触发与告警都要有(monkeypatch):
    """超过告警阈值说明补触发也救不回来（或压根没配令牌），两件事都得做。
    ⚠️ 顺序上补触发在前、告警在后，但**补触发失败绝不能吃掉告警**。"""
    got = _catch_alerts(monkeypatch)
    calls = _catch_dispatch(monkeypatch)
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 12.0)
    worker.check_backup_freshness()
    assert len(calls) == 1
    assert len(got) == 1 and got[0][1] == "act"


def test_没配令牌时自愈整个关掉_但告警照发(monkeypatch):
    """令牌缺省为空＝行为与加这段之前一模一样。**新功能没配好，不许让老功能变哑**。"""
    got = _catch_alerts(monkeypatch)
    calls = _catch_dispatch(monkeypatch)
    monkeypatch.setattr(worker.config, "BACKUP_DISPATCH_TOKEN", "")
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 12.0)
    worker.check_backup_freshness()
    assert calls == []
    assert len(got) == 1                  # 告警一条不少


def test_补触发有节流_令牌失效时不会每轮都打GitHub(monkeypatch):
    """看门狗按分钟跑。没有节流的话，令牌一失效就是每分钟一次无效请求，
    会把这个仓库的 API 配额烧掉，还可能被判成滥用。"""
    _catch_alerts(monkeypatch)
    calls = _catch_dispatch(monkeypatch)
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 12.0)
    worker.check_backup_freshness()
    worker.check_backup_freshness()
    worker.check_backup_freshness()
    assert len(calls) == 1                # 只有第一次出去了


def test_GitHub_报错不许连累看门狗(monkeypatch):
    """补触发是锦上添花。它挂了，告警和后面的清扫都得照常。"""
    got = _catch_alert_bodies(monkeypatch)
    monkeypatch.setattr(worker.config, "BACKUP_DISPATCH_TOKEN", "tok")
    worker._last_backup_dispatch = 0.0
    monkeypatch.setattr(worker.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GitHub 挂了")))
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 12.0)
    worker.check_backup_freshness()       # 不抛
    assert len(got) == 1                  # 且告警照发
    assert "没有自动补触发" in got[0]      # 且如实说没补上，别让人以为已经自愈了


def test_告警正文把最可能的成因排在第一条(monkeypatch):
    """改这条文案的理由是实测：80 次运行 conclusion 全是 success，从没失败过。
    旧文案列的三条（额度用尽 / 60 天无提交 / secret 失效）**一条都不是**真凶，
    而它们都会让人去改配置——照着排查会一路走偏，最后什么也没改对。"""
    got = _catch_alert_bodies(monkeypatch)
    monkeypatch.setattr(worker.config, "BACKUP_DISPATCH_TOKEN", "")
    monkeypatch.setattr(worker.blobstore, "newest_key_age_hours", lambda p: 12.0)
    worker.check_backup_freshness()
    body = got[0]
    assert "排期整段跳过" in body
    # 「全是 success 但就是不触发」是这个故障最反直觉的地方，必须写进去
    assert "success" in body and "间隔" in body
    # 缺 secret 会显示成 success（走优雅跳过 exit 0）——这条也容易看漏
    assert "exit 0" in body or "只跑了几秒" in body


# ── 主轨排队超时的两条分支（2026-08-25）────────────────────────────────
# 排不到供应商并发名额 ≠ 这单有问题，只是「当时别人在用」。所以默认退回队列重派；
# 但重排必须封顶，否则会**无限循环烧机器**：requeue_delayed 刷新 updated_at，而
# 「排队 24 小时判失败」那道兜底正是按 updated_at 算的，每重排一次就把兜底时钟归零。

def _gate_timeout_env(monkeypatch, requeue_ok: bool):
    job = Job(id="jg", status="running", phase=None, progress=0, lang="zh",
              audio_key="audio/jg.m4a", result_key=None, error=None, recording_type="meeting")
    seen = {"requeued": False, "failed": None, "refunded": None}

    def fake_requeue(jid, delay_min=None):
        seen["requeued"] = True
        return requeue_ok
    monkeypatch.setattr(worker.jobstore, "requeue_delayed", fake_requeue)
    monkeypatch.setattr(worker.jobstore, "set_failed",
                        lambda jid, err, public=None: seen.update(failed=jid) or True)
    monkeypatch.setattr(worker.accounts, "refund_job_reservation",
                        lambda jid: seen.update(refunded=jid))
    monkeypatch.setattr(worker.jobstore, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(worker.blobstore, "download_to", lambda key, path: open(path, "wb").write(b"A"))
    monkeypatch.setattr(worker, "_dump_pipeline", lambda *a, **k: None)

    def boom(p, **kw):
        raise worker.orchestrator.PrimaryGateTimeout("elv 等满了")
    monkeypatch.setattr(worker, "transcribe", boom)
    return job, seen


def test_主轨排队超时_默认退回队列不判失败不返还(monkeypatch):
    """判失败会给用户一句「转录失败」还得自己重传，而他什么都没做错。
    ⚠️ 也**不能返还预扣**——这单还活着，返了下次派单会重扣。"""
    job, seen = _gate_timeout_env(monkeypatch, requeue_ok=True)
    worker._process_job(job)
    assert seen["requeued"] is True
    assert seen["failed"] is None, "退回队列的单不该被判失败"
    assert seen["refunded"] is None, "退回队列的单不该返还预扣（这单还活着）"


def test_主轨排队超时_重排到顶必须判失败并返还(monkeypatch):
    """到顶还不判失败的话，这单既不结束也不失败，预扣永远卡着、机器每 10 分钟白起一台。"""
    job, seen = _gate_timeout_env(monkeypatch, requeue_ok=False)
    worker._process_job(job)
    assert seen["failed"] == "jg"
    assert seen["refunded"] == "jg", "判失败就必须返还预扣，否则钱扣着活儿没干"

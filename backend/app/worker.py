"""Worker：取一个排队作业 → 读音频 → 跑 transcribe（回写进度）→ 存结果。

`jobstore`/`blobstore` 为模块级名字，便于测试 monkeypatch。

本机版（与线上不同）：一次一单。去掉了线上的结算与返还、付款对账、云机器派单、
增长周报、数据库备份检查。转录入口是本地识别层 `pipeline.local_orchestrator.transcribe`
（与线上 orchestrator.transcribe 同签名），用到时才导入 —— 它会拉起识别引擎，接口进程平时用不到。

每一单在**单独的子进程**里跑（TRANSCRIBE_ISOLATE_JOBS=0 关掉，调试用）：四台引擎在原生线程里解码，
没法中途叫停；用户点「取消」时直接结束子进程，内存和算力立刻放出来，也不会留下半截稿子。
"""
import importlib
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

from pipeline import pp_runner
from pipeline.transcript import to_json

from . import alerts, balances, blobstore, config, glossary, jobstore, local, postprocess

ISOLATE = os.environ.get("TRANSCRIBE_ISOLATE_JOBS", "1") != "0"
CANCEL_POLL_SEC = 0.5


def _transcode_to_m4a(flac_path: str, out_path: str) -> None:
    """FLAC → AAC(.m4a) 回放音频：16kHz 单声道 32kbps，体积约 FLAC 的 1/5，且全浏览器可播
    （Safari/iPhone 也行，Opus 在 Safari 有坑故不用）。

    ⚠️ `-movflags +faststart`（2026-08-26）：不加的话 m4a 的索引（moov）写在**文件末尾**，
    浏览器要开始播必须先拿到索引 → 先读文件头、发现没有、再回头读文件尾，白白多一趟往返。
    码率不再往下压：这份音频存在的唯一理由是让人在拿不准时听一下那句到底说的啥——
    它是复核的证据，不拿清晰度换存储。"""
    subprocess.run(
        ["ffmpeg", "-y", "-i", flac_path, "-c:a", "aac", "-b:a", "32k",
         "-ar", "16000", "-ac", "1", "-movflags", "+faststart", out_path],
        capture_output=True, check=True,
    )


def process_one() -> bool:
    """领最老 queued → 处理。取到并处理返回 True，无作业返回 False。"""
    job = jobstore.claim_next_queued()
    if job is None:
        return False
    (_run_isolated if ISOLATE else _process_job)(job)
    return True


def process_one_pp() -> bool:
    """领最老的可跑后处理任务 → 处理。取到返回 True，无则 False。"""
    pp = postprocess.claim_next_queued()
    if pp is None:
        return False
    pp_runner.run(pp)
    return True


def _child_main(job, database_path: str, blob_dir: str, workdir: str) -> None:
    """子进程入口。spawn 出来是一个全新的解释器，存储位置从父进程带过来（测试会改它们）。"""
    config.DATABASE_PATH, config.BLOB_DIR = database_path, blob_dir
    _process_job(job, workdir)


def _run_isolated(job) -> None:
    """在子进程里跑一单；期间每半秒看一眼有没有人点「取消」。"""
    workdir = tempfile.mkdtemp(prefix=f"job_{job.id}_")
    proc = multiprocessing.get_context("spawn").Process(
        target=_child_main, args=(job, config.DATABASE_PATH, config.BLOB_DIR, workdir), name=f"job-{job.id[:8]}")
    proc.start()
    canceled = False
    try:
        while proc.is_alive():
            proc.join(CANCEL_POLL_SEC)
            if proc.is_alive() and local.cancel_requested(job.id):
                proc.terminate()
                proc.join(5)
                if proc.is_alive():
                    proc.kill()
                    proc.join()
                canceled = local.finish_canceled(job.id)
        if canceled:
            # 不留半截：回放音频可能已经写了；稿子与复核只在最后一步才写，没到那一步就没有
            if blobstore.exists(f"audio/{job.id}.m4a"):
                blobstore.delete(f"audio/{job.id}.m4a")
            print(f"已取消 job={job.id}", flush=True)
        elif proc.exitcode != 0:
            # 子进程被系统杀掉（比如内存不够）时它自己来不及写失败，这里补上
            jobstore.set_failed(job.id, f"转录子进程异常退出（exitcode={proc.exitcode}）")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _process_job(job, workdir: str | None = None) -> None:
    """处理一个**已领取**的作业：读音频→转录→存结果。异常吞掉落 set_failed。"""
    suffix = os.path.splitext(job.audio_key)[1] or ".bin"
    # 每任务专属临时目录：输入 + 转码与切块 + 各阶段稿 + 回放 m4a 全进这里，转完无论成败整目录删掉
    workdir = workdir or tempfile.mkdtemp(prefix=f"job_{job.id}_")
    input_path = os.path.join(workdir, f"input{suffix}")
    audio_key = None
    try:
        # 换转录入口用 TRANSCRIBE_ORCHESTRATOR（测试里换成一个假的，验证取消）
        transcribe = importlib.import_module(
            os.environ.get("TRANSCRIBE_ORCHESTRATOR", "pipeline.local_orchestrator")).transcribe

        blobstore.download_to(job.audio_key, input_path)
        t0 = time.time()
        last_metrics: dict = {}

        def on_phase(phase, progress, metrics):
            last_metrics.clear()
            last_metrics.update(metrics)
            jobstore.update_progress(job.id, phase, progress, metrics)

        def on_flac(flac_path):
            # 转码出的完整音频 → 转小体积 AAC(.m4a) 作回放音频
            nonlocal audio_key
            m4a_path = os.path.join(workdir, f"{job.id}.m4a")
            _transcode_to_m4a(flac_path, m4a_path)
            audio_key = f"audio/{job.id}.m4a"
            blobstore.put_file(audio_key, m4a_path)

        # 定字注入：取该任务指定的那本术语库（无库 / 取不到则空；删库不致命）
        user_glossary = glossary.get_glossary_content(job.glossary_id)

        def on_report(text: str):
            blobstore.put_bytes(f"review/{job.id}.report.md", text.encode("utf-8"),
                                content_type="text/markdown; charset=utf-8")

        segs, review = transcribe(input_path,
                                  on_phase=on_phase, on_flac=on_flac, glossary_text=user_glossary,
                                  lang=job.lang or "zh", on_report=on_report,
                                  # 上传那一刻的界面语言：决定报告里「我们写的说明」用哪门语言
                                  ui_lang=job.ui_lang)
        final_metrics = {**last_metrics, "elapsedSec": round(time.time() - t0)}
        result_key = f"result/{job.id}.json"
        blobstore.put_bytes(result_key, json.dumps(to_json(segs), ensure_ascii=False).encode("utf-8"),
                            content_type="application/json")
        blobstore.put_bytes(f"review/{job.id}.json", json.dumps(review, ensure_ascii=False).encode("utf-8"),
                            content_type="application/json")
        won = jobstore.set_done(job.id, result_key, final_metrics)
        # 转录成功的杂务：回放切到 m4a，删原始上传文件（视频/原音频）——隐私优先 + 省空间。
        # 整段兜异常：指针切失败只是回放退化用原文件，不许把已 done 的成功单拖进失败分支。
        if won and audio_key and audio_key != job.audio_key:
            try:
                jobstore.set_audio_key(job.id, audio_key)
                blobstore.delete(job.audio_key)
            except Exception:  # noqa: BLE001
                print(f"回放指针切换/删原始文件失败 job={job.id}: {traceback.format_exc()[-200:]}", flush=True)
    except Exception:
        err = traceback.format_exc()
        print(f"转录失败 job={job.id}: {err[-800:]}", flush=True)
        jobstore.set_failed(job.id, err[-2000:])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _startup_recover() -> None:
    """启动时回收上次崩溃留下的孤儿 running 作业，重置回 queued 让其重跑。DB 不通则跳过，不挡轮询。"""
    try:
        n = jobstore.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
        if n:
            print(f"启动回收 {n} 个卡死的 running 作业", flush=True)
        np = postprocess.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
        if np:
            print(f"启动回收 {np} 个卡死的后处理任务", flush=True)
    except Exception:  # noqa: BLE001
        print(f"回收孤儿作业失败（跳过）: {traceback.format_exc()[-200:]}", flush=True)


def check_alerts() -> None:
    """周期告警检查：① 失败率过高 ② 服务商余额低水位（仅 BALANCE_ALERT_EMAIL 开时）。
    告警落库，运行面板看得见；异常吞掉、不拖垮看门狗。"""
    try:
        done, failed = jobstore.recent_failure_stats(config.ALERT_FAILRATE_WINDOW_MIN)
        total = done + failed
        if total >= config.ALERT_FAILRATE_MIN_SAMPLE:
            pct = round(failed / total * 100)
            if pct >= config.ALERT_FAILRATE_PCT:
                alerts.send_admin_alert(
                    f"⚠️ 转录失败率偏高：近 {config.ALERT_FAILRATE_WINDOW_MIN} 分钟 {failed}/{total}（{pct}%）",
                    f"近 {config.ALERT_FAILRATE_WINDOW_MIN} 分钟内 {total} 个任务终态、{failed} 个失败（{pct}%）。"
                    f"请检查模型文件 / 定字后端 / 密钥。",
                    key="failrate", tier="watch",
                )
    except Exception:  # noqa: BLE001
        print(f"失败率告警检查异常（跳过）: {traceback.format_exc()[-200:]}", flush=True)
    if config.BALANCE_ALERT_EMAIL:
        try:
            balances.check_low_balances()
        except Exception:  # noqa: BLE001
            print(f"低余额告警检查异常（跳过）: {traceback.format_exc()[-200:]}", flush=True)
    alerts.purge_old()   # 过期告警清理；带 WHERE、失败自己吞掉


def refresh_balances() -> None:
    """周期拉取服务商余额（只读账单接口）。缺钥匙的厂商自动跳过；异常吞掉。"""
    try:
        res = balances.refresh_all()
        if res:
            print(f"余额自动刷新：{len(res)} 家（{', '.join(r['vendor'] for r in res)}）", flush=True)
    except Exception:  # noqa: BLE001
        print(f"余额刷新异常（跳过）: {traceback.format_exc()[-200:]}", flush=True)


def run_watchdog(stop: threading.Event) -> None:
    """看门狗线程：每 WATCHDOG_INTERVAL_SEC 秒回收一次卡死任务——updated_at 超 STALE_MIN
    分钟不动的 running 作业，未到重试上限则重排、到上限判失败（防毒任务无限重排）。"""
    last_bal = 0.0   # 上次余额刷新的单调时钟；0 = 启动后首轮即刷一次
    while not stop.is_set():
        if stop.wait(config.WATCHDOG_INTERVAL_SEC):
            break
        try:
            n = jobstore.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
            if n:
                print(f"看门狗：回收/判失败 {n} 个卡死作业", flush=True)
            nq = jobstore.fail_stale_queued(config.QUEUED_MAX_HOURS)
            if nq:
                print(f"看门狗：释放 {nq} 个排队超时作业", flush=True)
            npp = postprocess.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
            if npp:
                print(f"看门狗：回收/判失败 {npp} 个卡死后处理任务", flush=True)
        except Exception:  # noqa: BLE001
            print(f"看门狗异常（已跳过）: {traceback.format_exc()[-200:]}", flush=True)
        check_alerts()
        if time.monotonic() - last_bal >= config.BALANCE_REFRESH_MIN * 60:
            last_bal = time.monotonic()
            refresh_balances()


def run_loop(stop: threading.Event) -> None:
    """工作线程的轮询循环：claim → 处理 → 再 claim，stop 置位即退出。
    单作业异常已在 process_one 内吞掉并落 set_failed，这里再包一层防循环本身抛错时整条线程死掉。"""
    while not stop.is_set():
        try:
            # 同一线程轮询两条队列：转录优先，空了再看后处理
            if not process_one() and not process_one_pp():
                stop.wait(2)
        except Exception:  # noqa: BLE001
            print(f"worker 循环异常（已跳过，2s 后重试）: {traceback.format_exc()[-300:]}", flush=True)
            stop.wait(2)


def _start_pool(stop: threading.Event) -> list[threading.Thread]:
    """回收孤儿作业一次，再起一个工作线程 + 看门狗。

    本机一次只跑一单：一单转录就会占满电脑（四台识别引擎并行），两单同时跑只会都变慢、还可能内存不够。"""
    _startup_recover()
    print("worker 启动（一次一单），轮询作业…", flush=True)
    threads = [threading.Thread(target=run_loop, args=(stop,), daemon=True, name="worker-0"),
               threading.Thread(target=run_watchdog, args=(stop,), daemon=True, name="watchdog")]
    for t in threads:
        t.start()
    return threads


def start_in_thread() -> tuple[list[threading.Thread], threading.Event]:
    """在接口进程里起后台。返回 (线程列表, 共享 stop)。"""
    stop = threading.Event()
    return _start_pool(stop), stop


def main() -> None:
    stop = threading.Event()
    for t in _start_pool(stop):
        t.join()


if __name__ == "__main__":
    main()

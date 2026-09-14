"""Worker：取一个排队作业 → 下载音频 → 跑 transcribe（回写进度）→ 存结果。

`jobstore`/`blobstore`/`transcribe` 为模块级名字，便于测试 monkeypatch。
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

import requests

from pipeline import debug_dump, orchestrator, pp_runner
from pipeline.orchestrator import VENDOR_ROOT, transcribe
from pipeline.transcript import to_json

from . import accounts, alerts, balances, blobstore, config, glossary, jobstore, postprocess, pricing

try:  # 可选：装了且 api 进程里 init 过 Sentry 才上报；否则 capture 是 no-op
    import sentry_sdk
except ImportError:
    sentry_sdk = None

# 主轨分段进度上报文件（ELV/GEM 同机制，per-part 看板 + 看门狗心跳）：worker 进程内转录时设此 env，主轨引擎导入时
# 读取，转录各 Part 时写入，orchestrator 轮询它落库。并发=1 下单文件即可；并发>1 会串（各任务
# 共用一文件），届时需改 per-job（见监控平台 ROADMAP）。setdefault：批处理已设则不覆盖。
os.environ.setdefault("P1LLM_STATUS_FILE", os.path.join(tempfile.gettempdir(), "p1llm_status"))


def _capture() -> None:
    # worker 的异常都被吞掉只 print，Sentry 默认只插桩 HTTP——主动上报当前异常，
    # 否则免费层最易失败的转录/结账路径对监控完全不可见。未 init Sentry 时为 no-op。
    if sentry_sdk is not None:
        sentry_sdk.capture_exception()


def _transcode_to_m4a(flac_path: str, out_path: str) -> None:
    """FLAC → AAC(.m4a) 回放音频：16kHz 单声道 32kbps，体积约 FLAC 的 1/5，且全浏览器可播
    （Safari/iPhone 也行，Opus 在 Safari 有坑故不用）。

    ⚠️ `-movflags +faststart`（2026-08-26）：不加的话 m4a 的索引（moov）写在**文件末尾**，
    浏览器要开始播必须先拿到索引 → 先读文件头、发现没有、再回头读文件尾，白白多一趟往返。
    加上之后索引在前，点开就能播。**只对新转的单生效**，存量单仍是旧格式（能播，只是慢一点）。
    码率不再往下压：32kbps 已经是很省的档（原始上传约 66kbps），而这份音频存在的唯一理由
    是让人在拿不准时听一下那句到底说的啥——它是复核的证据，不拿清晰度换存储费。"""
    subprocess.run(
        ["ffmpeg", "-y", "-i", flac_path, "-c:a", "aac", "-b:a", "32k",
         "-ar", "16000", "-ac", "1", "-movflags", "+faststart", out_path],
        capture_output=True, check=True,
    )


def process_one() -> bool:
    """线程池模式：领最老 queued → 处理。取到并处理返回 True，无作业返回 False。"""
    job = jobstore.claim_next_queued()
    if job is None:
        return False
    _process_job(job)
    return True


def run_one_job(job_id: str) -> bool:
    """任务级机器模式（Fly：每台机器跑一个 job）：注入全局 Claude 闸 → 领指定 job → 处理。
    非 queued（已被领/不存在）返回 False。slot_id=job_id 注入 DB Claude 闸（跨机并发≤上限，
    超出 P3 降 DeepSeek）。Render 起机器时传 env JOB_ID，机器入口（main_one_job）调本函数。"""
    from pipeline import skill_merge

    from . import claude_gate
    from . import config
    skill_merge.set_claude_gate(claude_gate.DbClaudeGate(job_id))
    # 供应商并发闸（ELV/讯飞）：挂在**引擎调用**上，跑完那一路就还名额，不等整单结束。
    # 只在任务级机器模式注入——本地/回滚态不注入 = 没有闸，行为与改造前一致。
    orchestrator.set_engine_gate(claude_gate.DbEngineGate(job_id))
    # 第二/三档：Pro 有闸（账号并发 500），Flash 无闸兜底。只在任务级机器模式注入——
    # 本地/回滚态不注入就是老的单档行为。
    skill_merge.set_flash_model(config.DS_MODEL_FLASH)
    job = jobstore.claim_specific(job_id)
    if job is None:
        print(f"job {job_id} 非 queued（已被领/不存在），跳过", flush=True)
        return False
    _process_job(job)
    return True


def run_one_pp_job(job_id: str) -> bool:
    """任务级机器模式（后处理）：领指定 pp 任务 → pp_runner 跑三步链。非 queued 返回 False。
    Claude 并发闸在 pp_runner 内按步 acquire（无 DeepSeek 降级，拿不到闸即延后重试）。"""
    pp = postprocess.claim_specific(job_id)
    if pp is None:
        print(f"pp job {job_id} 非 queued（已被领/不存在），跳过", flush=True)
        return False
    pp_runner.run(pp)
    return True


def process_one_pp() -> bool:
    """线程池模式：领最老的可跑后处理任务 → 处理。取到返回 True，无则 False。"""
    pp = postprocess.claim_next_queued()
    if pp is None:
        return False
    pp_runner.run(pp)
    return True


def main_one_job() -> int:
    """任务级机器入口（Fly：每台机器跑一个 job）：读 env JOB_ID 跑该 job，进程退出后机器
    由 Fly auto_destroy 自毁（不主动删机器，连 OOM 被 SIGKILL 也会触发自毁）。
    env JOB_KIND=postprocess → 走后处理链，probe → 走 P3 探活（都在派单时随机器注入），否则转录。
    返回退出码：0=处理了，1=没领到（已被领/不存在），2=缺 JOB_ID。"""
    job_id = os.environ.get("JOB_ID", "").strip()
    if not job_id:
        print("main_one_job：缺 env JOB_ID，无法启动任务", flush=True)
        return 2
    kind = os.environ.get("JOB_KIND", "").strip()
    if kind == "postprocess":
        return 0 if run_one_pp_job(job_id) else 1
    if kind == "probe":
        # 探活：不碰 jobs 表，只跑一次最小 claude -p 把结果写进 p3_events（运营舱 P3 健康卡）
        from . import p3_probe
        return 0 if p3_probe.run_probe(job_id) else 1
    return 0 if run_one_job(job_id) else 1


def _fail_and_refund(job, error: str) -> None:
    """判失败 + 返还上传时预扣的冻结额。两条失败路（普通异常 / 排队重排到顶）共用一份，
    分开写迟早漏掉返还那一半。

    返还原语自身幂等（jobs.refunded_cents 闸），不再依赖 won 自律；won 只用来省一次注定
    no-op 的调用。就算这里进程被杀没返成，看门狗的 sweep_unrefunded_failures 下一轮也会
    补上（崩溃窗自愈）。"""
    won = jobstore.set_failed(job.id, error, public="转录失败，请重试；本次不计费")
    if won:
        try:
            accounts.refund_job_reservation(job.id)
        except Exception:  # noqa: BLE001
            print(f"失败返还失败 job={job.id}（留给看门狗清扫补返）: {traceback.format_exc()[-300:]}", flush=True)


def _process_job(job) -> None:
    """处理一个**已领取**的作业：下载→转录→存结果→结账。异常吞掉落 set_failed。
    供 process_one（线程池）与 run_one_job（任务级机器）共用。"""
    suffix = os.path.splitext(job.audio_key)[1] or ".bin"
    # 每任务专属临时目录：输入 + P0 的 FLAC/切片 + P1~P3 的 .md + 回放 m4a 全进这里，
    # 转完无论成败 finally 里整目录删掉——否则中间文件堆满 worker 临时盘致后续任务失败。
    workdir = tempfile.mkdtemp(prefix=f"job_{job.id}_")
    input_path = os.path.join(workdir, f"input{suffix}")
    audio_key = None
    # 中间产物留档的时间起点：**必须早于 P0**。vendor/Output 没人清理（Fly 上随机器消失，
    # 本地回滚态跨任务累积），靠「本单开跑之后写的」把上一单的稿隔开。
    # 与下面的 t0 分开两个变量：t0 量的是 elapsedSec（不含下载），语义不同，别合并。
    dump_since = time.time()
    try:
        blobstore.download_to(job.audio_key, input_path)
        t0 = time.time()
        last_metrics: dict = {}

        def on_phase(phase, progress, metrics):
            last_metrics.clear()
            last_metrics.update(metrics)
            jobstore.update_progress(job.id, phase, progress, metrics)

        def on_flac(flac_path):
            # P0 抽出完整 FLAC → 转小体积 AAC(.m4a) 作回放音频（省 R2 ~5 倍、全浏览器可播）
            nonlocal audio_key
            m4a_path = os.path.join(workdir, f"{job.id}.m4a")
            _transcode_to_m4a(flac_path, m4a_path)
            audio_key = f"audio/{job.id}.m4a"
            blobstore.put_file(audio_key, m4a_path)

        # P3 注入：取该任务指定的那本术语库（job.glossary_id）透传进 transcribe→merge→P3
        # （无库 / 取不到则空，P3 不带术语；防御式：删库不致命）
        user_glossary = glossary.get_glossary_content(job.glossary_id)
        def on_report(text: str):
            # 挂 review/ 前缀 = 复用其 30 天 R2 生命周期，与它解析出的复核清单同寿
            blobstore.put_bytes(f"review/{job.id}.report.md", text.encode("utf-8"),
                                content_type="text/markdown; charset=utf-8")

        segs, review = transcribe(input_path,
                                  on_phase=on_phase, on_flac=on_flac, glossary_text=user_glossary,
                                  lang=job.lang or "zh", on_report=on_report,
                                  # 上传那一刻的界面语言。**与 lang 是两回事**：lang 决定跑哪几条轨，
                                  # ui_lang 决定 P3 报告里「我们写的说明」用哪门语言。
                                  ui_lang=job.ui_lang)
        final_metrics = {**last_metrics, "elapsedSec": round(time.time() - t0)}
        result_key = f"result/{job.id}.json"
        blobstore.put_bytes(
            result_key,
            json.dumps(to_json(segs), ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        blobstore.put_bytes(
            f"review/{job.id}.json",
            json.dumps(review, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        # won=True 才是本次真的把状态从 running 转成 done（终态持有守卫）；双跑场景（如看门狗
        # 误判重排+新机器领取，两个尝试并发处理同一 job）下慢的一方拿到 False，下面这些「赢家才
        # 该做一次」的副作用（删原始文件/结算/讯飞扣减）全部跳过——防重复结算、白退。
        won = jobstore.set_done(job.id, result_key, final_metrics)
        if won:
            # 结算第一时间办完：钱的事必须先于一切杂务（切回放指针/删原文件/讯飞扣减）——
            # 旧序 set_audio_key 无保护地跑在结算前，DB 瞬断即跳外层 except，而 set_failed 对
            # 已 done 的作业是 no-op → 既不结算也不退款，预扣款冻死无痕。
            # 时长优先取管线实测（final_metrics），拿不到兜底上传时 ffprobe 实测的
            # job.duration_sec（新任务必有）；两处都没有 → 无法计费：退回预扣，宁可白送这一单
            # 也不冻死用户的钱。老任务无预扣（reserved_cents=None）→ 兜底 0，等价旧「按完成扣」。
            dur = final_metrics.get("durationSec") or job.duration_sec
            if job.user_email:
                if isinstance(dur, (int, float)) and dur > 0:
                    # 按本单上传时钉死的费率快照结算（不是当下的全局价）——促销到期、日后调价
                    # 都影响不到已上传的单。老任务无快照 → 兜底当年的全语种统一价。
                    rate = job.rate_cents_per_min or pricing.LEGACY_RATE_CENTS
                    # 免费额度（定价 V2 §2）：按付费秒结算 = 实测时长 - 上传时锁定的免费秒。
                    # 管线实测略短于上传实测时 max(0,·) 兜底——免费秒不重算（上传时已按实测锁定）
                    actual_cents = round(max(0, dur - (job.free_seconds or 0)) / 60 * rate)
                    try:
                        accounts.settle_job(job.user_email, job.id, job.file_name, job.lang, int(dur),
                                            actual_cents, reserved_cents=(job.reserved_cents or 0))
                    except Exception:  # noqa: BLE001  结算失败不拖垮转录结果
                        print(f"结算失败 job={job.id}: {traceback.format_exc()[-300:]}", flush=True)
                elif job.reserved_cents:
                    try:
                        accounts.refund_job_reservation(job.id)
                    except Exception:  # noqa: BLE001
                        print(f"无时长返预扣失败 job={job.id}: {traceback.format_exc()[-300:]}", flush=True)
            # 转录成功的杂务：回放切到 m4a，删原始上传文件（视频/原音频）——隐私优先 + 省空间。
            # 整段兜异常：指针切失败只是回放退化用原文件，不许把已 done 的成功单拖进失败分支。
            # 删失败留给 R2 生命周期兜底。
            if audio_key and audio_key != job.audio_key:
                try:
                    jobstore.set_audio_key(job.id, audio_key)
                    blobstore.delete(job.audio_key)
                except Exception:  # noqa: BLE001
                    print(f"回放指针切换/删原始文件失败 job={job.id}: {traceback.format_exc()[-200:]}", flush=True)
            # 讯飞按时长扣减（无余额 API → 用量倒扣）：本任务用了 XF 引擎（仅现场面访）就扣 dur 小时
            if isinstance(dur, (int, float)) and dur > 0 and "XF" in (final_metrics.get("engines") or {}):
                try:
                    balances.consume("讯飞", dur / 3600)
                except Exception:  # noqa: BLE001  扣减失败不拖垮转录结果
                    print(f"讯飞用量扣减失败 job={job.id}: {traceback.format_exc()[-200:]}", flush=True)
    except orchestrator.PrimaryGateTimeout as e:
        # 主轨排不到供应商并发名额 ≠ 这单有问题，只是「当时别人在用」。
        # **必须在 set_failed 之前接住**：走判失败那条路会返还预扣 + 给用户一句「转录失败」，
        # 而他什么都没做错，还得自己重传。退回队列则是机器自毁、过一会儿自动重来，用户无感。
        # ⚠️ 预扣不动也不返还——这单还活着，返了下次派单会重扣。
        if jobstore.requeue_delayed(job.id):
            print(f"主轨排队超时，退回队列延后重派 job={job.id}: {e}", flush=True)
        else:
            # 重排已到上限（或状态已不是 running）→ **不能再无限排下去**，按失败处理走返还。
            # 走到这里说明供应商侧长时间给不出名额，不是个别单的运气问题。
            print(f"主轨排队超时且重排已到上限，判失败 job={job.id}: {e}", flush=True)
            _fail_and_refund(job, f"主轨排队超时且重排已到上限：{e}")
    except Exception:
        _capture()
        _fail_and_refund(job, traceback.format_exc()[-2000:])
    finally:
        # 中间产物留档放在 finally：**失败的单最需要它**。放在成功分支等于「只有不需要诊断的
        # 时候才留证据」。整段吞异常——留档坏了不许改变这一单的成败。
        try:
            _dump_pipeline(job, dump_since)
        finally:
            # 清理必须无条件执行（同 pp_runner）：留档抛出时若跳过这行，临时盘会被逐单撑满
            shutil.rmtree(workdir, ignore_errors=True)  # 整目录清：输入+FLAC+切片+所有 .md+m4a


def _dump_pipeline(job, since: float) -> None:
    """把这一单 P1/P2/P3 的中间稿传到 `review/<job_id>/pipeline/`。

    挂 `review/` 前缀是为了**复用它的 30 天生命周期**（见 scripts/set_r2_lifecycle.py），
    与它要诊断的那份复核清单同寿，不必另加 R2 规则——同 report.md 的做法。
    注销账号时靠 `list_keys("review/<id>/")` 一并删（见 account_delete._blob_keys）。

    开关 `PIPELINE_DUMP=0` 可停（默认开）：诊断价值的前提是「出事时它已经在了」，
    要等出事再打开就永远晚一步。
    """
    if os.environ.get("PIPELINE_DUMP", "1") == "0":
        return
    try:
        files, dropped = debug_dump.collect(since, VENDOR_ROOT / "Output")
        # ⚠️ 不写 status：finally 里 job 这个对象还是领取时的快照，失败的单它也说 running。
        # 状态以 DB 为准，这里宁可不给也不给错（同「后端不编造」）。
        manifest = {"jobId": job.id, "lang": job.lang, "fileName": job.file_name,
                    "files": [], "dropped": dropped}
        for name, path in files:
            try:
                blobstore.put_file(f"review/{job.id}/pipeline/{name}", str(path))
                manifest["files"].append({"name": name, "bytes": path.stat().st_size})
            except Exception as e:  # noqa: BLE001  单个文件传不上去，不该连累其余的
                dropped.append(f"{name}: 上传失败 {type(e).__name__} {e}")
        blobstore.put_bytes(f"review/{job.id}/pipeline/manifest.json",
                            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                            content_type="application/json; charset=utf-8")
        # 丢了什么必须说出来：「没收到」和「本来就没有」长得一样，不报出来就查不出区别
        if dropped:
            print(f"中间产物留档 job={job.id}: {len(manifest['files'])} 个已传，"
                  f"{len(dropped)} 个未传 → {dropped[:3]}", flush=True)
    except Exception:  # noqa: BLE001
        print(f"中间产物留档失败 job={job.id}（不影响本单）: {traceback.format_exc()[-300:]}", flush=True)


def _sweep_refunds() -> None:
    """精确对账兜底：补返「failed 且有预扣但未标记返还」的漏网单（进程崩溃窗/看门狗硬判失败
    的任务都靠这条路返钱）。原语幂等，重复扫无害；失败不拖垮调用方循环。"""
    try:
        n = accounts.sweep_unrefunded_failures()
        if n:
            print(f"清扫补返 {n} 个失败任务的预扣款", flush=True)
    except Exception:  # noqa: BLE001
        print(f"返还清扫失败（下一轮再试）: {traceback.format_exc()[-200:]}", flush=True)
    # 充值退款是另一条路：只把滞留的 pending 退款单告警暴露出来，**不自动作废**
    # （万一钱其实已经退出去了，作废等于把已付的钱又算作可用余额）。告警自带 24h 冷却。
    try:
        stale = accounts.sweep_stale_refunds()
        if stale:
            print(f"发现 {stale} 笔滞留的 pending 退款单（已告警，待人工到 provider 后台核实）", flush=True)
    except Exception:  # noqa: BLE001
        print(f"退款单滞留清扫失败（下一轮再试）: {traceback.format_exc()[-200:]}", flush=True)


def _startup_recover() -> None:
    """启动时回收上次崩溃/休眠（Render 免费层重启常见）留下的孤儿 running 作业，
    重置回 queued 让其重跑。整个池只需做一次；DB 不通则跳过，不挡轮询。"""
    try:
        n = jobstore.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
        if n:
            print(f"启动回收 {n} 个卡死的 running 作业", flush=True)
        np = postprocess.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
        if np:
            print(f"启动回收 {np} 个卡死的后处理任务", flush=True)
        _sweep_refunds()
    except Exception:  # noqa: BLE001
        _capture()
        print(f"回收孤儿作业失败（跳过）: {traceback.format_exc()[-200:]}", flush=True)


def _reconcile_topups() -> None:
    """未入账的充值意向逐笔问 Paddle；completed 就入账（与回调共用交易号锁）。没接 Paddle 时不做事。"""
    if not os.environ.get("PADDLE_API_KEY"):
        return
    try:
        from . import paddle_pay
        n = paddle_pay.reconcile_open()
        if n:
            print(f"看门狗：主动对账补入账 {n} 笔充值", flush=True)
    except Exception:  # noqa: BLE001
        _capture()
        print(f"看门狗对账异常（已跳过）: {traceback.format_exc()[-200:]}", flush=True)


def check_alerts() -> None:
    """周期告警检查（看门狗线程内每轮调）：① 失败率过高（邮件）② 服务商余额低水位
    （仅 BALANCE_ALERT_EMAIL 开时发邮件；缺省关，余额走驾驶舱面板红标、看门狗不碰余额 DB）。
    各自带去重冷却，dev 模式（未配 Resend）只打印不真发；异常吞掉、不拖垮看门狗。"""
    try:
        done, failed = jobstore.recent_failure_stats(config.ALERT_FAILRATE_WINDOW_MIN)
        total = done + failed
        if total >= config.ALERT_FAILRATE_MIN_SAMPLE:
            pct = round(failed / total * 100)
            if pct >= config.ALERT_FAILRATE_PCT:
                alerts.send_admin_alert(
                    f"⚠️ 转录失败率偏高：近 {config.ALERT_FAILRATE_WINDOW_MIN} 分钟 {failed}/{total}（{pct}%）",
                    f"近 {config.ALERT_FAILRATE_WINDOW_MIN} 分钟内 {total} 个任务终态、{failed} 个失败（{pct}%）。"
                    f"请检查引擎 / 密钥 / 余额。",
                    key="failrate", tier="watch",
                )
    except Exception:  # noqa: BLE001
        _capture()
        print(f"失败率告警检查异常（跳过）: {traceback.format_exc()[-200:]}", flush=True)
    if config.BALANCE_ALERT_EMAIL:   # 缺省关：余额走驾驶舱面板红标，不发邮件（省 Resend）
        try:
            balances.check_low_balances()
        except Exception:  # noqa: BLE001
            _capture()
            print(f"低余额告警检查异常（跳过）: {traceback.format_exc()[-200:]}", flush=True)
    check_backup_freshness()
    # 增长周报（周一 09:00 Asia/Shanghai）：挂在这里是因为看门狗本来就在按分钟跑，
    # 而「发过没有」记在告警表里，重启不重发。任何异常在 growth 里吞掉。
    try:
        from . import growth
        growth.maybe_send_weekly()
    except Exception:  # noqa: BLE001
        _capture()
    alerts.purge_old()   # 过期告警清理；带 WHERE、失败自己吞掉，不必再包一层


_last_backup_dispatch = 0.0


def _dispatch_backup_workflow() -> bool:
    """漏跑了就自己补触发一次 GitHub Actions 备份，返回是否真的发出去了。

    **为什么需要它**（2026-08-27 复盘 80 次运行得出）：db-backup 的 conclusion 全是
    success，一次没失败过；问题出在**触发**——cron 写的是 :12，实际触发点在 :05–:58 之间
    随机漂，79 个间隔里有 10 个被整段跳过，最坏一次空了 10 小时（8/26 23:27 → 8/27 09:36）。
    GitHub 的 schedule 是尽力而为的，高负载时整段窗口不调度。
    **提高 cron 频率治不了**：它不是随机丢单次，翻倍频率只是让同一段空窗里多丢几次。
    ⇒ 唯一的真修是换一个我们自己控制的触发源，而看门狗本来就在按分钟跑。

    没配令牌 = 直接返回 False（自愈关掉，只剩告警，行为与加这段之前一致）。
    节流用进程内时间戳就够：Render 是单进程，重启后重来一次也无害；
    没有它的话，令牌一失效就会每轮看门狗都去打一次 GitHub。
    任何异常都吞掉——**自愈失败不许连累看门狗，更不许挡住后面那条告警**。"""
    global _last_backup_dispatch
    if not config.BACKUP_DISPATCH_TOKEN:
        return False
    now = time.time()
    if now - _last_backup_dispatch < config.BACKUP_DISPATCH_COOLDOWN_MIN * 60:
        return False
    _last_backup_dispatch = now
    try:
        r = requests.post(
            f"https://api.github.com/repos/{config.BACKUP_REPO}"
            f"/actions/workflows/{config.BACKUP_WORKFLOW_FILE}/dispatches",
            headers={"Authorization": f"Bearer {config.BACKUP_DISPATCH_TOKEN}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"ref": "main"}, timeout=20,
        )
        ok = r.status_code == 204          # dispatches 成功就是 204 No Content
        print(f"备份补触发 → {r.status_code}{'' if ok else ' ' + r.text[:120]}", flush=True)
        return ok
    except Exception:  # noqa: BLE001
        _capture()
        return False


def check_backup_freshness() -> None:
    """备份停了要有人知道（2026-08-14），漏跑了先自己补一次（2026-08-27）。

    备份的停法**全是静默的**：GitHub 排期整段跳过、免费额度耗尽、仓库 60 天无活动被自动
    停用定时任务、secret 过期。它们不报错、不发信，你只会在真要恢复的那天才发现最新的一份
    是三周前的。所以这一条是 act 档——**不动手它永远不会好**。

    两级阈值，因为两件事：
      SELF_HEAL_HOURS（2h）→ 补触发一次。绝大多数漏跑是排期抖动，补一次就好了，不该惊动人。
      STALE_HOURS（6h）    → 才告警。到这一步说明补触发也没用（或没配令牌），是真出事了。

    查不到年龄（R2 抖动 / 权限问题）时两件都不做：那是监控自己的故障，报出来只会稀释这条的
    可信度。真出事的话下一轮还会查。"""
    try:
        age = blobstore.newest_key_age_hours("backups/db/")
        if age is None:
            return
        healed = False
        if age > config.BACKUP_SELF_HEAL_HOURS:
            healed = _dispatch_backup_workflow()
        if age > config.BACKUP_STALE_HOURS:
            alerts.send_admin_alert(
                f"🔴 数据库备份已 {age:.0f} 小时没有新的（阈值 {config.BACKUP_STALE_HOURS}h）",
                f"R2 的 backups/db/ 里最新一份是 {age:.1f} 小时前的，而备份本该每小时一次。\n"
                + (f"已自动补触发一次 db-backup，若十分钟后仍无新备份，再按下面排查。\n"
                   if healed else
                   f"**没有自动补触发**（未配 BACKUP_DISPATCH_TOKEN 或刚补过），请手动排查。\n")
                + f"按可能性从高到低：\n"
                f"① GitHub 排期整段跳过——**最常见，且不会失败、只是不触发**。\n"
                f"   判据：Actions 页 db-backup 最近几次全是 success，但两次之间隔了几小时。\n"
                f"   处置：Actions 页手动 Run workflow 一次即可，不用改任何配置。\n"
                f"② 备份用的 secret 失效（BACKUP_DATABASE_URL / R2_*）——**会显示成 success**，\n"
                f"   因为缺 secret 时脚本走的是「优雅跳过 exit 0」。判据：那几次运行只跑了几秒。\n"
                f"③ GitHub Actions 免费额度用尽（私有仓库 2000 分钟/月，备份约占 37%）。\n"
                f"④ 仓库连续 60 天无提交，GitHub 自动停用了定时任务。\n"
                f"→ 先看最近几次运行的**间隔**和**耗时**，比看 conclusion 有用。",
                tier="act", key="backup-stale", cooldown_sec=6 * 3600,
            )
    except Exception:  # noqa: BLE001  监控坏了不许连累看门狗
        _capture()


def refresh_balances() -> None:
    """周期拉取各服务商余额（看门狗按 BALANCE_REFRESH_MIN 调）：调只读账单接口写库。
    缺钥匙的厂商自动跳过；异常吞掉、不拖垮看门狗。worker 在跑时会真调外部只读接口。"""
    try:
        res = balances.refresh_all()
        if res:
            print(f"余额自动刷新：{len(res)} 家（{', '.join(r['vendor'] for r in res)}）", flush=True)
    except Exception:  # noqa: BLE001
        _capture()
        print(f"余额刷新异常（跳过）: {traceback.format_exc()[-200:]}", flush=True)


def run_watchdog(stop: threading.Event) -> None:
    """看门狗线程：每 WATCHDOG_INTERVAL_SEC 秒回收一次卡死任务——updated_at 超 STALE_MIN
    分钟不动的 running 作业，未到重试上限则重排、到上限判失败（防毒任务无限重排）。与启动回收
    同一逻辑，区别仅在「周期运行」：进程被 UptimeRobot 保活、却有线程静默卡死时（任务 D 的死法），
    启动回收不会再触发，只有周期看门狗能救。DB 瞬断不杀线程；stop 置位即退出。"""
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
                print(f"看门狗：释放 {nq} 个排队超时作业（预扣款随清扫返还）", flush=True)
            npp = postprocess.requeue_stale_running(config.WATCHDOG_STALE_MIN, config.WATCHDOG_MAX_ATTEMPTS)
            if npp:
                print(f"看门狗：回收/判失败 {npp} 个卡死后处理任务（无预扣，不涉钱）", flush=True)
            _sweep_refunds()   # 补返所有漏网失败单（刚硬判失败的 + 排队超时的 + 崩溃窗遗留的）
        except Exception:  # noqa: BLE001
            _capture()
            print(f"看门狗异常（已跳过）: {traceback.format_exc()[-200:]}", flush=True)
        _reconcile_topups()   # 充值主动对账（2026-09-06）：回调没来的，24 小时内自动补入账
        if config.FLY_DISPATCH:   # Fly 派单模式：周期补派 queued（机器自毁腾出名额后补起下一台）
            try:
                from . import fly_machines
                started = fly_machines.dispatch_pending()
                if started:
                    print(f"看门狗：派单起 {started} 台 Fly 机器", flush=True)
            except Exception:  # noqa: BLE001
                _capture()
                print(f"看门狗派单异常（已跳过）: {traceback.format_exc()[-200:]}", flush=True)
        check_alerts()   # 同周期顺带告警检查（失败率 + 低余额）
        if time.monotonic() - last_bal >= config.BALANCE_REFRESH_MIN * 60:
            last_bal = time.monotonic()
            refresh_balances()   # 按 BALANCE_REFRESH_MIN 周期拉各家余额（缺钥匙者跳过）


def run_loop(stop: threading.Event) -> None:
    """单个工作线程的轮询循环：claim → 处理 → 再 claim，stop 置位即退出。多个此循环并发
    跑即并发池——各线程独立领取，claim_next_queued 用 SKIP LOCKED 保证不会领到同一作业。
    单作业异常已在 process_one 内吞掉并落 set_failed，这里再包一层防 claim/循环本身
    抛错（如 DB 瞬断）时整条线程死掉。"""
    while not stop.is_set():
        try:
            # 同池轮询两条队列：转录优先，空了再看后处理（本地线程池模式；派单态不走这里）
            if not process_one() and not process_one_pp():
                stop.wait(2)
        except Exception:  # noqa: BLE001  循环自身异常（如 DB 瞬断）也不许杀死线程
            _capture()
            print(f"worker 循环异常（已跳过，2s 后重试）: {traceback.format_exc()[-300:]}", flush=True)
            stop.wait(2)


def _start_pool(stop: threading.Event, with_workers: bool = True) -> list[threading.Thread]:
    """回收孤儿作业一次，再起线程：with_workers=True 起 N=config.MAX_CONCURRENT_JOBS 个工作
    线程组成并发池（每线程独立 claim→处理→refill，始终最多 N 个在跑）+ 看门狗；
    with_workers=False（Fly 派单模式）只起看门狗——转录在 Fly 任务机器上跑，本进程不处理，
    看门狗负责回收卡死作业 + 周期补派 queued。"""
    _startup_recover()
    threads = []
    if with_workers:
        n = max(1, config.MAX_CONCURRENT_JOBS)
        if n > 1 and not config.FLY_DISPATCH:
            # 保险丝（硬约束，2026-07-12 复检）：进程内并发 >1 时，同进程的多个任务共享同一个
            # P1LLM 分片状态文件（本模块顶部 per-process 单一路径），任务 A 会读到 B 的失败标记
            # 误判整单、或 A 的失败标记被 B 清掉 → 有洞终稿静默交付并计费。回滚/本地态一律
            # 强制并发 1；生产并发靠 Fly 1机1任务外包（FLY_DISPATCH=1 时本进程不跑处理池）。
            print(f"MAX_CONCURRENT_JOBS={n} 在进程内模式下不安全（分片状态文件跨任务串台），强制降为 1", flush=True)
            n = 1
        print(f"worker 启动，并发池大小 {n}，轮询作业…", flush=True)
        for i in range(n):
            t = threading.Thread(target=run_loop, args=(stop,), daemon=True, name=f"worker-{i}")
            t.start()
            threads.append(t)
    else:
        print("Fly 派单模式：仅起看门狗（转录在 Fly 任务机器上跑）+ 周期补派…", flush=True)
    wd = threading.Thread(target=run_watchdog, args=(stop,), daemon=True, name="watchdog")
    wd.start()
    threads.append(wd)   # 看门狗也随 stop 退出；放进列表供 main/调用方一并 join
    return threads


def start_in_thread(with_workers: bool = True) -> tuple[list[threading.Thread], threading.Event]:
    """在后台跑 worker（供 api 进程内合一模式用，Render 免费层只能起一个服务）。
    with_workers=False = Fly 派单模式（只看门狗+补派）。返回 (线程列表, 共享 stop)。"""
    stop = threading.Event()
    return _start_pool(stop, with_workers), stop


def main() -> None:
    """worker 进程入口。env RUN_ONE_JOB 设了 → 任务级单任务模式（Fly 机器）：跑完即按退出码
    退出，进程退出触发 Fly auto_destroy 自毁。否则起常驻并发池（本地 docker 独立 worker），
    跑到进程退出（stop 永不置位）。"""
    if os.environ.get("RUN_ONE_JOB"):
        raise SystemExit(main_one_job())
    stop = threading.Event()
    for t in _start_pool(stop):
        t.join()


if __name__ == "__main__":
    main()

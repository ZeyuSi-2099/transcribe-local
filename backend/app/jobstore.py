"""jobs 表操作：建作业、查、回写进度、原子取下一个排队作业。"""
import json
from dataclasses import dataclass
from typing import Optional

from . import config, db
from .db import Jsonb   # 本机版：线上是 psycopg.types.json.Jsonb


@dataclass
class Job:
    id: str
    status: str
    phase: Optional[str]
    progress: int
    lang: Optional[str]
    audio_key: str
    result_key: Optional[str]
    error: Optional[str]
    recording_type: str = "meeting"
    metrics: Optional[dict] = None
    user_email: Optional[str] = None
    file_name: Optional[str] = None
    duration_sec: Optional[int] = None
    attempts: int = 0
    glossary_id: Optional[str] = None   # 该转录用哪本术语库（可空 = 不注入）
    error_public: Optional[str] = None  # 面向用户的脱敏失败话术；error 是内部 traceback，仅库/admin 看
    reserved_cents: Optional[int] = None  # 上传时预扣冻结额；结算/退款按它算差额（老任务无预扣 = None）
    rate_cents_per_min: Optional[int] = None  # 上传时钉死的费率快照（分/分钟）；结算只认它。老任务为空 → 兜底 LEGACY_RATE_CENTS
    free_seconds: Optional[int] = None  # 本单吃掉的免费秒数（定价 V2 §2）；结算按付费秒 = 时长 - 它
    ui_lang: Optional[str] = None       # 上传那一刻的界面语言；P3 报告里我们写的说明按它出。老单为空 → 回落英文


_COLS = ("id, status, phase, progress, lang, audio_key, result_key, error, recording_type, metrics, "
         "user_email, file_name, duration_sec, attempts, glossary_id, error_public, reserved_cents, "
         "rate_cents_per_min, free_seconds, ui_lang")


def _row_to_job(row) -> Job:
    return Job(
        id=str(row[0]), status=row[1], phase=row[2], progress=row[3],
        lang=row[4], audio_key=row[5], result_key=row[6], error=row[7],
        recording_type=row[8], metrics=row[9],
        user_email=row[10], file_name=row[11], duration_sec=row[12],
        attempts=row[13],
        glossary_id=str(row[14]) if row[14] else None,
        error_public=row[15],
        reserved_cents=row[16],
        rate_cents_per_min=row[17],
        free_seconds=row[18],
        ui_lang=row[19],
    )


def create_job(audio_key: str, lang: str, recording_type: str, user_email: str,
               file_name: Optional[str] = None, duration_sec: Optional[int] = None,
               glossary_id: Optional[str] = None, reserved_cents: int = 0) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO jobs (audio_key, lang, recording_type, user_email, file_name, duration_sec, glossary_id, reserved_cents) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (audio_key, lang, recording_type, user_email, file_name, duration_sec, glossary_id, reserved_cents),
        ).fetchone()
    return str(row[0])


def get_job(job_id: str) -> Optional[Job]:
    with db.connect() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM jobs WHERE id = %s", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def update_progress(job_id: str, phase: str, progress: int, metrics: Optional[dict] = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET phase=%s, progress=%s, metrics=%s, updated_at=now() WHERE id=%s",
            (phase, progress, Jsonb(metrics) if metrics is not None else None, job_id),
        )


def set_done(job_id: str, result_key: str, metrics: Optional[dict] = None) -> bool:
    """终态持有守卫：WHERE 限 status='running'——只从 running 转 done。若该作业已被别的
    机器/线程转成终态（done/failed），本次 no-op、不覆盖，防误判双跑互相覆盖对方结果。
    返回 True=本次真的完成了 running→done 转移；False=no-op（状态已非 running）。
    调用方（worker）据此门控 settle_job 等「只该赢家做一次」的副作用，防双跑重复结算/退款。"""
    with db.connect() as conn:
        row = conn.execute(
            "UPDATE jobs SET status='done', phase='done', progress=100, "
            "result_key=%s, metrics=COALESCE(%s, metrics), updated_at=now() "
            "WHERE id=%s AND status='running' RETURNING id",
            (result_key, Jsonb(metrics) if metrics is not None else None, job_id),
        ).fetchone()
    return row is not None


def set_audio_key(job_id: str, audio_key: str) -> None:
    """把作业的回放音频指向新 key（转录后切到 FLAC，原始上传文件随后删除）。"""
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET audio_key=%s, updated_at=now() WHERE id=%s",
            (audio_key, job_id),
        )


_DEFAULT_ERROR_PUBLIC = "转录失败，请重试；本次不计费"


def set_failed(job_id: str, error: str, public: Optional[str] = None) -> bool:
    """同 set_done 的终态持有守卫：只从 running 转 failed，不覆盖已终态的作业。
    返回 True=本次真的完成了 running→failed 转移；False=no-op。调用方据此省掉注定 no-op 的
    返还调用（真正防双退的是 accounts.refund_job_reservation 的 refunded_cents 幂等闸）。"""
    with db.connect() as conn:
        row = conn.execute(
            "UPDATE jobs SET status='failed', error=%s, error_public=%s, updated_at=now() "
            "WHERE id=%s AND status='running' RETURNING id",
            (error, public or _DEFAULT_ERROR_PUBLIC, job_id),
        ).fetchone()
    return row is not None


def requeue_delayed(job_id: str, delay_min: int | None = None) -> bool:
    """主轨排不到供应商并发名额：running → queued 延后重派（not_before=now+delay）。

    **不是判失败**：失败的理由只是「当时别人在用」，这单本身没问题；判失败会让用户看到
    「转录失败」还得自己重传。退回队列则是机器自毁（不再计费）、过一会儿自动重来，用户无感。
    做法照搬后处理撞顶（postprocess.requeue_delayed），是验证过的路子。

    ⚠️ **预扣不动、也不返还**——这单还活着，返了下次派单会重扣。

    ⚠️ **重排次数必须封顶**（`attempts < WATCHDOG_MAX_ATTEMPTS`），否则会无限循环烧机器：
    本函数刷新 `updated_at`，而「排队超时 24 小时判失败」那道兜底（fail_stale_queued）正是
    按 `updated_at` 算的——每重排一次就把兜底时钟归零，于是一个永远排不上的单会每
    JOB_RETRY_DELAY_MIN 分钟起一台机器、等满 30 分钟、再退回，**永远不结束也永远不失败**。
    到顶返回 False，调用方据此改走判失败（带返还）。"""
    d = config.JOB_RETRY_DELAY_MIN if delay_min is None else delay_min
    with db.connect() as conn:
        r = conn.execute(
            "UPDATE jobs SET status='queued', not_before=now() + %s * interval '1 minute', "
            "updated_at=now() WHERE id=%s AND status='running' AND attempts < %s RETURNING id",
            (d, job_id, config.WATCHDOG_MAX_ATTEMPTS),
        ).fetchone()
    return r is not None


def requeue_stale_running(minutes: int = 30, max_attempts: int = 3) -> int:
    """回收卡死的 running 作业（updated_at 早于阈值 = 卡死/孤儿）：未到重试上限 → 重排回
    queued 重跑（还要重跑，冻结款不退）；已到上限 → 判失败（防「毒任务」每次都卡却被无限重排、
    白烧 ASR 钱）。返回处理总数。

    被判失败任务的预扣款返还不在这里做（本模块不依赖 accounts，避免循环依赖）：调用方
    （worker 看门狗）随后跑 accounts.sweep_unrefunded_failures 统一补返——按「failed +
    有预扣 + 未标记返还」清扫，返还原语自身幂等（jobs.refunded_cents 闸），与 worker
    失败路径的即时返还天然不冲突，且任何崩溃窗都会被下一轮清扫自愈（精确对账）。

    用于 ① worker 启动回收上次进程崩溃/休眠（Render 免费层）留下的孤儿；② 看门狗周期性回收
    运行中静默卡死的作业——进程被保活却有线程卡死时（任务 D 的死法），启动回收不再触发，必须
    靠周期看门狗。活跃作业经 update_progress 持续刷新 updated_at 不会被误判。
    注意 psycopg 陷阱：interval 不在引号内参数化。"""
    with db.connect() as conn:
        failed = conn.execute(
            "UPDATE jobs SET status='failed', error=%s, error_public=%s, updated_at=now() "
            "WHERE status='running' AND updated_at < now() - %s * interval '1 minute' "
            "AND attempts >= %s RETURNING id",
            (f"看门狗：任务卡死无响应超过 {minutes} 分钟，已重试 {max_attempts} 次仍失败，判失败。",
             _DEFAULT_ERROR_PUBLIC, minutes, max_attempts),
        ).fetchall()
        requeued = conn.execute(
            "UPDATE jobs SET status='queued', phase=NULL, progress=0, updated_at=now() "
            "WHERE status='running' AND updated_at < now() - %s * interval '1 minute' "
            "AND attempts < %s RETURNING id",
            (minutes, max_attempts),
        ).fetchall()
    return len(failed) + len(requeued)


def fail_stale_queued(hours: int = 24) -> int:
    """释放排队超时的任务：queued 超过 hours 小时（Fly 派单永久性坏死、FLY_API_TOKEN 失效
    且无人看驾驶舱等）→ 判失败。否则这些单的预扣冻结款会无限期卡住（看门狗只回收 running）。
    话术复用全站唯一的 _DEFAULT_ERROR_PUBLIC（不新增话术种类——前端 EN 映射依赖这一点）。
    返还不在这里做：判失败后由 accounts.sweep_unrefunded_failures（看门狗每轮跑）按
    「failed+有预扣+未标记」统一补返，与其他失败路径同一条返还路。
    正常排队（并发满等机器）远短于此阈值，不会误杀。"""
    with db.connect() as conn:
        rows = conn.execute(
            "UPDATE jobs SET status='failed', error=%s, error_public=%s, updated_at=now() "
            "WHERE status='queued' AND updated_at < now() - %s * interval '1 hour' "
            "RETURNING id",
            (f"排队超时：等待派单超过 {hours} 小时未开始（派单通道疑似坏死），判失败释放预扣款。",
             _DEFAULT_ERROR_PUBLIC, hours),
        ).fetchall()
    return len(rows)


def admin_overview() -> dict:
    """运营驾驶舱总览（仅 admin 端点调用）：进行中任务（含实时 metrics/分片）+ 最近 7 天失败
    + 当日汇总。返回纯 JSON 可序列化 dict。"""
    with db.connect() as conn:
        jobs = conn.execute(
            "SELECT id, file_name, user_email, status, phase, progress, attempts, metrics, "
            "recording_type, EXTRACT(EPOCH FROM (now() - created_at))::int, "
            "to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'), lang "
            "FROM jobs WHERE status IN ('queued','running') ORDER BY created_at"
        ).fetchall()
        failures = conn.execute(
            "SELECT id, file_name, user_email, error, attempts, "
            "to_char(updated_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') "
            "FROM jobs WHERE status='failed' AND updated_at > now() - interval '7 days' "
            "ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        # attempts 也要给：**最终成功了但重跑过**是唯一还留在库里的看门狗回收痕迹。
        # 不给的话这类单在列表上跟一次过的单长得一模一样，而「谁在悄悄地要跑两遍」
        # 正是质量信号——出问题时它先于失败出现。
        recent = conn.execute(
            "SELECT id, file_name, user_email, metrics, to_char(updated_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'), "
            "recording_type, to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'), lang, attempts "
            "FROM jobs WHERE status='done' ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
        # 后处理（收费功能，此前运营页完全看不到）：进行中的全给，终态的取最近 20 条。
        # join jobs 只为取文件名——后处理表里没有，光看 UUID 认不出是哪一单。
        pp = conn.execute(
            "SELECT p.job_id, j.file_name, p.user_email, p.status, p.steps, p.current_step, "
            "p.step_index, p.price_cents, p.qc_fix_count, p.has_qc, p.failed_step, p.error_public, "
            "p.degraded_steps, p.attempts, p.ds_cost_cny, "
            "to_char(p.updated_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') "
            "FROM postprocess_jobs p LEFT JOIN jobs j ON j.id = p.job_id "
            "WHERE p.status IN ('queued','running') "
            "   OR p.updated_at > now() - interval '7 days' "
            "ORDER BY (p.status IN ('queued','running')) DESC, p.updated_at DESC LIMIT 30"
        ).fetchall()
        s = conn.execute(
            "SELECT count(*) FILTER (WHERE status='running'), "
            "count(*) FILTER (WHERE status='queued'), "
            "count(*) FILTER (WHERE status='done'   AND updated_at >= date_trunc('day', now())), "
            "count(*) FILTER (WHERE status='failed' AND updated_at >= date_trunc('day', now())) "
            "FROM jobs"
        ).fetchone()
    return {
        "jobs": [
            {"id": str(j[0]), "fileName": j[1], "userEmail": j[2], "status": j[3],
             "phase": j[4], "progress": j[5], "attempts": j[6], "metrics": j[7],
             "recordingType": j[8], "elapsedSec": j[9], "createdAt": j[10], "lang": j[11]}
            for j in jobs
        ],
        "failures": [
            {"id": str(f[0]), "fileName": f[1], "userEmail": f[2], "error": f[3],
             "attempts": f[4], "failedAt": f[5]}
            for f in failures
        ],
        "recent": [
            {"id": str(r[0]), "fileName": r[1], "userEmail": r[2], "metrics": r[3], "doneAt": r[4],
             "recordingType": r[5], "createdAt": r[6], "lang": r[7], "attempts": r[8]}
            for r in recent
        ],
        "postprocess": [
            {"jobId": str(q[0]), "fileName": q[1], "userEmail": q[2], "status": q[3],
             "steps": json.loads(q[4] or "[]"), "currentStep": q[5], "stepIndex": q[6],
             "priceCents": q[7], "qcFixCount": q[8], "hasQc": bool(q[9]),
             "failedStep": q[10], "errorPublic": q[11],
             # 走了降级路的步（Claude 撞顶 → DeepSeek）。**空数组和 null 要能分辨**：
             # null 是老单（那时还没这一列），空数组是「跑过、没降级」。
             "degradedSteps": json.loads(q[12]) if q[12] else ([] if q[12] == "[]" else None),
             "dsCostCny": float(q[14]) if q[14] is not None else None,
             "attempts": q[13], "updatedAt": q[15]}
            for q in pp
        ],
        "summary": {"running": s[0], "queued": s[1], "doneToday": s[2], "failedToday": s[3]},
    }


def recent_failure_stats(minutes: int = 60) -> tuple[int, int]:
    """近 minutes 分钟内终态作业的 (完成数, 失败数)，供失败率告警。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) FILTER (WHERE status='done'), count(*) FILTER (WHERE status='failed') "
            "FROM jobs WHERE updated_at > now() - %s * interval '1 minute' AND status IN ('done','failed')",
            (minutes,),
        ).fetchone()
    return int(row[0]), int(row[1])


def claim_next_queued() -> Optional[Job]:
    """原子地取一个 queued 作业并置 running。无则返回 None。
    同 list_queued_ids：跳过 not_before 未到点的延后单（本地 worker 池模式走这条）。"""
    with db.connect() as conn:
        row = conn.execute(
            f"""
            UPDATE jobs SET status='running', attempts = attempts + 1, updated_at=now()
            WHERE id = (
                SELECT id FROM jobs WHERE status='queued'
                AND (not_before IS NULL OR not_before <= now())
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING {_COLS}
            """
        ).fetchone()
    return _row_to_job(row) if row else None


def list_queued_ids(limit: int) -> list:
    """取最老的 limit 个可派的 queued 作业 id（Fly 派单用）。**不改状态**——只是挑出待派的 job，
    真正领取由机器启动后的 claim_specific 原子完成。limit<=0 返回空。

    跳过 not_before 未到点的延后单（主轨排不到供应商名额而退回队列的那种）——
    不跳的话会立刻重派、立刻又排不到，空转烧机器。"""
    if limit <= 0:
        return []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' "
            "AND (not_before IS NULL OR not_before <= now()) "
            "ORDER BY created_at LIMIT %s",
            (limit,),
        ).fetchall()
    return [str(r[0]) for r in rows]


def claim_specific(job_id: str) -> Optional[Job]:
    """原子领**指定** job 并置 running（任务级机器：每台只处理派给它的那个）。
    非 queued（已被领/已完成）或不存在 → None。配合 Render 起机器时传 JOB_ID。"""
    with db.connect() as conn:
        row = conn.execute(
            f"UPDATE jobs SET status='running', attempts = attempts + 1, updated_at=now() "
            f"WHERE id = %s AND status='queued' RETURNING {_COLS}",
            (job_id,),
        ).fetchone()
    return _row_to_job(row) if row else None

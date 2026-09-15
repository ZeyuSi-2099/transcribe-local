"""后处理：脱敏清单 CRUD + pp_jobs 状态机（契约 docs/postprocess-implementation-contract.md）。

配置 CRUD 口径参照 glossary.py：成功返回 dict / 违规返回错误字符串（上层转 409/422）/
不存在或非本人返回 None（上层转 404）。任务状态机与 jobstore 同款终态持有守卫
（只从 running 转终态，双跑慢方 no-op）。清单内容在发起那一刻由 API 快照进
R2 postprocess/{job_id}/inputs.json，本表只留名字供显示。

⚠️ 2026-08-17 下架「归类」：它一度是三步里的第二步。验证结论是**错了指不出来**——
错误稳定、系统、模型自己也不知道自己错了，与「复核优先」的产品主张相反。
完整实测与数据集存档在 0.工作文档/归类产品预研存档-2026-08/，将来另立产品时启用。
**存量任务的 profile_id/profile_name 仍然读得出来**（历史页要显示老单），只是不再写入。
"""
import json

from . import config, db

STEPS = ("narrate", "redact")   # 固定顺序：视角转换 → 脱敏
MAX_LISTS = 20        # 脱敏清单数上限/账户
MAX_NAME = 40         # 名称长度上限（trim 后码点数，与术语库同口径）

# 面向用户的失败话术（单句原则，同全站转录话术风格；EN 靠前端查表）
ERROR_PUBLIC = "后处理失败，请重试"   # 本机版：不收费，去掉线上的「本次不计费」


def normalize_steps(steps) -> list | None:
    """校验并按固定顺序归一 steps：非空、去重、⊂ STEPS。非法返回 None。

    存量任务的 steps 里有 `categorize`，重跑时会走到这里。**不做兼容放行**——
    放行等于要 pp_runner 也留着归类那条执行链，那就没拆干净。前端在预选重试步骤时
    已把不认识的步骤滤掉，所以用户点重试拿到的是合法子集；真送来 `categorize` 就是 422。"""
    if not isinstance(steps, list) or not steps:
        return None
    if any(s not in STEPS for s in steps):
        return None
    out = [s for s in STEPS if s in steps]
    return out or None


def validate_name(name: str) -> str | None:
    """名称校验（与术语库同口径）：trim 后 1–40 字、无控制字符。合法返回 None。"""
    n = (name or "").strip()
    if not n:
        return "name required"
    if len(n) > MAX_NAME:
        return f"name too long: {len(n)} > {MAX_NAME}"
    if any(ord(c) < 32 for c in n):
        return "name has control characters"
    return None


def _validate_content(content: str, max_chars: int, label: str) -> str | None:
    total = sum(1 for c in (content or "") if c != "\n")
    if total > max_chars:
        return f"{label} too long: {total} > {max_chars}"
    return None


def validate_list(content: str) -> str | None:
    return _validate_content(content, config.PP_LIST_MAX_CHARS, "list")


# ============ 配置 CRUD（表名与内容列参数化）============
# 表名/列名是模块内常量（非用户输入），f-string 拼接无注入面。
#
# ⚠️ 这套泛型现在只剩「脱敏清单」一个使用者（归类方案下架前是两个）。**故意不内联展开**：
# 后处理再加别的用户自配内容时它还能直接用，而拆掉它要动 CRUD 全链，风险白担。
_KINDS = {
    "list": {"table": "postprocess_redact_lists", "col": "content", "key": "content",
             "cap": MAX_LISTS, "validate": validate_list},
}


def _row(kind: str, r) -> dict:
    return {"id": str(r[0]), "name": r[1], _KINDS[kind]["key"]: r[2], "updatedAt": r[3].isoformat()}


def _list(kind: str, email: str) -> list[dict]:
    k = _KINDS[kind]
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT id, name, {k['col']}, updated_at FROM {k['table']} "
            f"WHERE user_email=%s ORDER BY updated_at DESC", (email,)
        ).fetchall()
    return [_row(kind, r) for r in rows]


def _create(kind: str, email: str, name: str, content: str):
    k = _KINDS[kind]
    if (e := validate_name(name)) is not None:
        return e
    if (e := k["validate"](content)) is not None:
        return e
    with db.connect() as conn:
        n = conn.execute(f"SELECT count(*) FROM {k['table']} WHERE user_email=%s", (email,)).fetchone()[0]
        if n >= k["cap"]:
            return f"too many: limit {k['cap']}"
        r = conn.execute(
            f"INSERT INTO {k['table']} (user_email, name, {k['col']}) VALUES (%s,%s,%s) "
            f"RETURNING id, name, {k['col']}, updated_at",
            (email, name.strip(), content or ""),
        ).fetchone()
    return _row(kind, r)


def _update(kind: str, email: str, rid: str, name: str, content: str):
    k = _KINDS[kind]
    if (e := validate_name(name)) is not None:
        return e
    if (e := k["validate"](content)) is not None:
        return e
    try:
        with db.connect() as conn:
            r = conn.execute(
                f"UPDATE {k['table']} SET name=%s, {k['col']}=%s, updated_at=now() "
                f"WHERE id=%s AND user_email=%s RETURNING id, name, {k['col']}, updated_at",
                (name.strip(), content or "", rid, email),
            ).fetchone()
    except Exception:  # 非法 UUID 串在 psycopg 插 UUID 列会抛异常 → 视为不存在（同 glossary.owns 口径）
        return None
    return _row(kind, r) if r else None


def _delete(kind: str, email: str, rid: str) -> bool:
    k = _KINDS[kind]
    try:
        with db.connect() as conn:
            r = conn.execute(
                f"DELETE FROM {k['table']} WHERE id=%s AND user_email=%s RETURNING id", (rid, email)
            ).fetchone()
    except Exception:
        return False
    return r is not None


def _get(kind: str, email: str, rid: str):
    """归属校验取单条：不存在/非本人/非法 id → None。"""
    k = _KINDS[kind]
    try:
        with db.connect() as conn:
            r = conn.execute(
                f"SELECT id, name, {k['col']}, updated_at FROM {k['table']} WHERE id=%s AND user_email=%s",
                (rid, email),
            ).fetchone()
    except Exception:
        return None
    return _row(kind, r) if r else None


def list_redact_lists(email):
    return _list("list", email)


def create_redact_list(email, name, content):
    return _create("list", email, name, content)


def update_redact_list(email, rid, name, content):
    return _update("list", email, rid, name, content)


def delete_redact_list(email, rid):
    return _delete("list", email, rid)


def get_redact_list(email, rid):
    return _get("list", email, rid)


# ============ pp_jobs 状态机 ============

# profile_id / profile_name 只读不写：归类下架前发起的存量任务还挂着它们，历史页要显示。
_JOB_COLS = ("job_id, user_email, steps, profile_id, redact_list_id, profile_name, list_name, "
             "status, current_step, step_index, price_cents, qc_fix_count, products, has_qc, "
             "failed_step, error_public, attempts, updated_at, ui_lang")


def _job_row(r) -> dict:
    return {
        "job_id": str(r[0]), "user_email": r[1], "steps": json.loads(r[2] or "[]"),
        "profile_id": str(r[3]) if r[3] else None, "redact_list_id": str(r[4]) if r[4] else None,
        "profile_name": r[5], "list_name": r[6],
        "status": r[7], "current_step": r[8], "step_index": r[9],
        "price_cents": r[10], "qc_fix_count": r[11],
        "products": json.loads(r[12] or "[]"), "has_qc": bool(r[13]),
        "failed_step": r[14], "error_public": r[15], "attempts": r[16],
        "updated_at": r[17].isoformat(), "ui_lang": r[18],
    }


def get_pp_job(job_id: str) -> dict | None:
    try:
        with db.connect() as conn:
            r = conn.execute(
                f"SELECT {_JOB_COLS} FROM postprocess_jobs WHERE job_id=%s", (job_id,)
            ).fetchone()
    except Exception:
        return None
    return _job_row(r) if r else None


def create_or_reset(job_id: str, email: str, steps: list, price_cents: int,
                    redact_list_id: str | None = None, list_name: str | None = None,
                    rate_cents_per_min: int | None = None, audio_seconds: int | None = None,
                    ui_lang: str | None = None) -> bool:
    """发起：新建一行，或对 failed/done 同行重置重跑。返回 True=成功排队；
    False=该 job 已有在途任务（queued/running，上层转 409）——条件 DO UPDATE 保证并发安全。
    rate/audio_seconds 是发起时刻的计费快照（2026-08-02 改价），free_seconds 每次发起清零重算。

    profile_id/profile_name 不再写入（归类已下架）；重置老单时**显式置 NULL**——
    不置的话老单重跑完还挂着方案名，历史页会显示一个这次根本没跑的步骤。"""
    with db.connect() as conn:
        r = conn.execute(
            "INSERT INTO postprocess_jobs (job_id, user_email, steps, redact_list_id, "
            "list_name, status, price_cents, pp_rate_cents_per_min, audio_seconds, ui_lang) "
            "VALUES (%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s) "
            "ON CONFLICT (job_id) DO UPDATE SET "
            "  steps=EXCLUDED.steps, redact_list_id=EXCLUDED.redact_list_id, "
            "  profile_id=NULL, profile_name=NULL, "
            "  list_name=EXCLUDED.list_name, status='queued', "
            "  current_step=NULL, step_index=0, price_cents=EXCLUDED.price_cents, qc_fix_count=0, "
            "  pp_rate_cents_per_min=EXCLUDED.pp_rate_cents_per_min, audio_seconds=EXCLUDED.audio_seconds, "
            "  ui_lang=EXCLUDED.ui_lang, "
            "  free_seconds=NULL, "
            "  products=NULL, has_qc=FALSE, failed_step=NULL, error=NULL, error_public=NULL, "
            "  attempts=0, not_before=NULL, updated_at=now() "
            "WHERE postprocess_jobs.status IN ('done','failed') "
            "RETURNING job_id",
            (job_id, email, json.dumps(steps), redact_list_id,
             list_name, price_cents, rate_cents_per_min, audio_seconds, ui_lang),
        ).fetchone()
    return r is not None


def claim_specific(job_id: str) -> dict | None:
    """原子领指定 pp 任务并置 running（任务级机器：JOB_KIND=postprocess）。非 queued → None。"""
    with db.connect() as conn:
        r = conn.execute(
            f"UPDATE postprocess_jobs SET status='running', attempts=attempts+1, updated_at=now() "
            f"WHERE job_id=%s AND status='queued' RETURNING {_JOB_COLS}",
            (job_id,),
        ).fetchone()
    return _job_row(r) if r else None


def claim_next_queued() -> dict | None:
    """线程池模式：领最老的可跑 queued（跳过 not_before 未到点的延后单）。无则 None。"""
    with db.connect() as conn:
        r = conn.execute(
            f"""
            UPDATE postprocess_jobs SET status='running', attempts=attempts+1, updated_at=now()
            WHERE job_id = (
                SELECT job_id FROM postprocess_jobs WHERE status='queued'
                  AND (not_before IS NULL OR not_before <= now())
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING {_JOB_COLS}
            """
        ).fetchone()
    return _job_row(r) if r else None


def list_queued_ids(limit: int) -> list:
    """Fly 派单用：最老的 limit 个可跑 queued 的 job_id（不改状态，同 jobstore.list_queued_ids）。"""
    if limit <= 0:
        return []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT job_id FROM postprocess_jobs WHERE status='queued' "
            "AND (not_before IS NULL OR not_before <= now()) ORDER BY created_at LIMIT %s",
            (limit,),
        ).fetchall()
    return [str(r[0]) for r in rows]


def update_step(job_id: str, step: str, index: int) -> None:
    """进入第 index 步（1 起）。顺带刷新 updated_at 喂看门狗。"""
    with db.connect() as conn:
        conn.execute(
            "UPDATE postprocess_jobs SET current_step=%s, step_index=%s, updated_at=now() WHERE job_id=%s",
            (step, index, job_id),
        )


def add_ds_cost(job_id: str, cny: float) -> None:
    """累加本单降级路的 DeepSeek 花费（¥）。**埋点失败一律吞掉**——记账坏了不许连累出稿。

    累加而不是覆盖：一单可能有多步都降级（视角转换 + 脱敏），各记各的会互相盖掉。
    """
    if not cny:
        return
    try:
        with db.connect() as conn:
            conn.execute("UPDATE postprocess_jobs "
                         "SET ds_cost_cny = COALESCE(ds_cost_cny, 0) + %s WHERE job_id = %s",
                         (round(float(cny), 4), job_id))
    except Exception:  # noqa: BLE001
        pass


def mark_degraded(job_id: str, step: str) -> None:
    """记下这一步走了降级路（Claude 撞顶 → DeepSeek 兜底）。幂等：同一步重复记只留一条。

    **失败一律吞掉**：留痕是给运营查的，记不上顶多查的时候少条线索；
    要是让它把已经跑成功的降级产出连累成失败，那就是本末倒置（同 p3_health 的埋点原则）。
    """
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT degraded_steps FROM postprocess_jobs WHERE job_id=%s",
                               (job_id,)).fetchone()
            cur = json.loads((row[0] if row else None) or "[]")
            if step in cur:
                return
            conn.execute("UPDATE postprocess_jobs SET degraded_steps=%s WHERE job_id=%s",
                         (json.dumps(cur + [step]), job_id))
    except Exception as exc:  # noqa: BLE001
        print(f"[PP] 降级留痕失败（不影响任务）：{type(exc).__name__}: {exc}", flush=True)


def set_done(job_id: str, products: list, qc_fix_count: int, has_qc: bool) -> bool:
    """终态持有守卫：只从 running 转 done（同 jobstore.set_done）。返回 True=本次真赢得转移，
    调用方据此门控 settle_postprocess（防双跑重复扣款；库层 uq_ledger_ppcharge_job 再兜一层）。"""
    with db.connect() as conn:
        r = conn.execute(
            "UPDATE postprocess_jobs SET status='done', products=%s, qc_fix_count=%s, has_qc=%s, "
            "updated_at=now() WHERE job_id=%s AND status='running' RETURNING job_id",
            (json.dumps(products), qc_fix_count, has_qc, job_id),
        ).fetchone()
    return r is not None


def set_failed(job_id: str, failed_step: str | None, error: str, public: str | None = None) -> bool:
    """只从 running 转 failed。后处理无预扣 → 失败不涉钱（不计费=什么都不用退）。"""
    with db.connect() as conn:
        r = conn.execute(
            "UPDATE postprocess_jobs SET status='failed', failed_step=%s, error=%s, error_public=%s, "
            "updated_at=now() WHERE job_id=%s AND status='running' RETURNING job_id",
            (failed_step, error, public or ERROR_PUBLIC, job_id),
        ).fetchone()
    return r is not None


def requeue_delayed(job_id: str, delay_min: int | None = None) -> bool:
    """撞顶/并发满：running → queued 延后重试（not_before=now+delay），重跑从头开始。
    attempts 不清零——反复撞顶到看门狗上限会判失败，防无限循环烧机器。"""
    d = config.PP_RETRY_DELAY_MIN if delay_min is None else delay_min
    with db.connect() as conn:
        r = conn.execute(
            "UPDATE postprocess_jobs SET status='queued', current_step=NULL, step_index=0, "
            "not_before=now() + %s * interval '1 minute', updated_at=now() "
            "WHERE job_id=%s AND status='running' RETURNING job_id",
            (d, job_id),
        ).fetchone()
    return r is not None


def requeue_stale_running(minutes: int = 30, max_attempts: int = 3) -> int:
    """看门狗：回收卡死的 running pp 任务（同 jobstore.requeue_stale_running 语义）：
    未到重试上限 → 重排 queued；到上限 → 判失败。无预扣，失败不涉钱。"""
    with db.connect() as conn:
        failed = conn.execute(
            "UPDATE postprocess_jobs SET status='failed', error=%s, error_public=%s, updated_at=now() "
            "WHERE status='running' AND updated_at < now() - %s * interval '1 minute' "
            "AND attempts >= %s RETURNING job_id",
            (f"看门狗：后处理卡死无响应超过 {minutes} 分钟，已重试 {max_attempts} 次仍失败，判失败。",
             ERROR_PUBLIC, minutes, max_attempts),
        ).fetchall()
        requeued = conn.execute(
            "UPDATE postprocess_jobs SET status='queued', current_step=NULL, step_index=0, updated_at=now() "
            "WHERE status='running' AND updated_at < now() - %s * interval '1 minute' "
            "AND attempts < %s RETURNING job_id",
            (minutes, max_attempts),
        ).fetchall()
    return len(failed) + len(requeued)


def job_summaries(email: str) -> dict:
    """历史页行内摘要：{job_id: {status, stepIndex, totalSteps, currentStep, qcFixCount, products}}。
    GET /api/jobs 每行附带；无后处理的 job 不在 dict 里（前端拿 None）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT job_id, status, step_index, steps, current_step, qc_fix_count, products "
            "FROM postprocess_jobs WHERE user_email=%s", (email,)
        ).fetchall()
    out = {}
    for r in rows:
        steps = json.loads(r[3] or "[]")
        out[str(r[0])] = {
            "status": r[1], "stepIndex": r[2], "totalSteps": len(steps),
            "currentStep": r[4], "qcFixCount": r[5],
            "products": json.loads(r[6] or "[]"),
        }
    return out

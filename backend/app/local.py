"""本机版独有：单用户下，替代线上账户模块（accounts）里本机还用得到的那两件事。

线上这两件事和余额、免费额度、项目绑在一起（预扣冻结、费率快照、按账本算花费）。
本机不收费，只剩「建一单」和「列出我的单」。列表字段与线上 accounts.list_jobs 逐个同名，界面不用改就能读。
"""
from . import db

# 本机唯一的用户。线上各表按邮箱归属，本机沿用这一列，统一填它。
EMAIL = "local@transcribe.local"


def create_job(audio_key: str, lang: str, recording_type: str, *, file_name: str | None = None,
               duration_sec: int | None = None, glossary_id: str | None = None,
               audio_sha256: str | None = None, ui_lang: str | None = None) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO jobs (audio_key, lang, recording_type, user_email, file_name, duration_sec, "
            "glossary_id, audio_sha256, ui_lang) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (audio_key, lang, recording_type, EMAIL, file_name, duration_sec, glossary_id, audio_sha256, ui_lang),
        ).fetchone()
    return str(row[0])


# 取消的单记成 failed（界面不用多认一种状态，想重来点「重试」即可），话术固定这一句
CANCELED_PUBLIC = "已取消"


def cancel_job(job_id: str) -> str:
    """排队中的直接判取消；在跑的立一个标记，后台看到就结束子进程。
    返回 canceled（已取消）/ requested（已通知后台）/ not_active（已经结束，不用取消）。"""
    with db.connect() as conn:
        row = conn.execute(
            "UPDATE jobs SET status='failed', error='canceled', error_public=%s, updated_at=now() "
            "WHERE id=%s AND status='queued' RETURNING id", (CANCELED_PUBLIC, job_id)).fetchone()
        if row:
            return "canceled"
        row = conn.execute(
            "UPDATE jobs SET cancel_requested=1 WHERE id=%s AND status='running' RETURNING id",
            (job_id,)).fetchone()
    return "requested" if row else "not_active"


def cancel_requested(job_id: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT cancel_requested FROM jobs WHERE id=%s", (job_id,)).fetchone()
    return bool(row and row[0])


def finish_canceled(job_id: str) -> bool:
    """子进程已结束后调用：running → failed（已取消）。已经跑完（done）的不改。"""
    with db.connect() as conn:
        row = conn.execute(
            "UPDATE jobs SET status='failed', error='canceled', error_public=%s, cancel_requested=0, "
            "updated_at=now() WHERE id=%s AND status='running' RETURNING id", (CANCELED_PUBLIC, job_id)).fetchone()
    return row is not None


def list_jobs(email: str) -> list[dict]:
    """同线上：最近 200 单。costCents 恒为 0（本机不收费），projectId 恒为空（项目已下架）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, status, progress, lang, file_name, duration_sec, metrics, created_at, error_public, phase "
            "FROM jobs WHERE user_email = %s ORDER BY created_at DESC LIMIT 200",
            (email,),
        ).fetchall()
    out = []
    for r in rows:
        metrics = r[6] or {}
        out.append({
            "id": str(r[0]), "status": r[1], "progress": r[2], "lang": r[3],
            "fileName": r[4], "durationSec": r[5] or metrics.get("durationSec"),
            "createdAt": r[7].isoformat(), "error": r[8], "phase": r[9],
            "costCents": 0, "projectId": None,
        })
    return out

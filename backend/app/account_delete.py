"""账户注销（2026-08-15）。

条款承诺的是「删除全部内容、不可恢复」（Terms §7 / Privacy 保留与删除），
所以这里是**真删**，不是打个标记藏起来。

三条设计取舍，都不是随手定的：

**① 先删文件，再删记录。**
记录是找到文件的**唯一索引**——R2 的键全部由 job_id 拼出来，而 job_id 只存在 jobs 表里。
先删记录再删文件的话，中间一崩，那些文件就永远没人能再定位、只能等生命周期规则过期
（音频 7 天、稿 30 天）。反过来崩，只是「文件没了但记录还在」，用户看到几条打不开的历史，
再点一次注销就收干净了。前者不可挽回，后者可重试。

**② 防滥用记录留下来，但认不出是谁。**
免费额度四道闸全挂在「这个邮箱 / 这个 IP / 这段音频见过没有」上。
把行删干净 = 给「注销 → 重注册 → 再领 300 分钟」开一条无限循环，而且三张表各漏一次：
  users.free_minutes_granted 归零（再领一次）
  signup_ips 删行（同 IP 第 3 号的计数回退）
  audio_hashes 删行（同一段音频可以再吃一次免费额度）
所以这三处一律**保留行、把 email 换成不可逆指纹**。闸继续有效，记录里认不回是谁。

**③ 账本不删，改匿名。**
Privacy 写的是「账单记录按财务法规要求的年限留存」。删掉等于自己违背自己写的条款，
也让往后的对账凭空缺一段。所以 ledger / refunds 的 email 换成指纹，金额与时间原样留着。
"""
import hashlib
import json
import secrets

from . import blobstore, db


class DeleteRejected(Exception):
    """不满足注销前置条件（有余额 / 有任务在跑）。文案直接给用户看。"""


def _salt(conn) -> str:
    """账户指纹的盐：单行表，首次访问时生成，此后永不变。

    生成走 ON CONFLICT DO NOTHING 再回读——两个请求同时首次注销时，
    先到的那个写入、后到的读回同一个值，不会各自生成一个（那会让指纹分成两批、闸漏一半）。
    """
    conn.execute(
        "INSERT INTO app_secrets (id, account_hash_salt) VALUES (1, %s) ON CONFLICT (id) DO NOTHING",
        (secrets.token_hex(32),),
    )
    return conn.execute("SELECT account_hash_salt FROM app_secrets WHERE id = 1").fetchone()[0]


def email_fingerprint(conn, email: str) -> str:
    """邮箱 → 不可逆指纹。加盐是为了让**部分泄露**（一张表的导出、一段日志、一份单表备份）
    不能用字典跑回邮箱——邮箱空间小且可枚举，不加盐的 sha256 等于明文。"""
    return hashlib.sha256(f"{_salt(conn)}:{email.strip().lower()}".encode()).hexdigest()


def was_deleted(conn, email: str) -> bool:
    """这个邮箱注销过吗——免费额度发放前问一次（闸⑤）。"""
    return conn.execute(
        "SELECT 1 FROM deleted_accounts WHERE email_hash = %s", (email_fingerprint(conn, email),)
    ).fetchone() is not None


def preflight(email: str) -> dict:
    """注销前的体检：能不能注销，以及会删掉什么。前端拿它渲染确认框。

    **拦截理由要具体**（差多少钱、几个任务在跑）——「暂时不能注销」这种话
    只会让人再点一次，然后再看一遍同样的话。
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT balance_cents FROM users WHERE email = %s", (email,)
        ).fetchone()
        if row is None:
            raise DeleteRejected("账户不存在。")
        balance = row[0] or 0
        running = conn.execute(
            "SELECT count(*) FROM jobs WHERE user_email = %s AND status IN ('queued', 'running')",
            (email,),
        ).fetchone()[0]
        pp_running = conn.execute(
            "SELECT count(*) FROM postprocess_jobs WHERE user_email = %s "
            "AND status IN ('queued', 'running')",
            (email,),
        ).fetchone()[0]
        jobs = conn.execute(
            "SELECT count(*) FROM jobs WHERE user_email = %s", (email,)
        ).fetchone()[0]
        books = conn.execute(
            "SELECT count(*) FROM glossaries WHERE email = %s", (email,)
        ).fetchone()[0]
    blockers = []
    if balance > 0:
        # 条款第 4 条写的就是这条路径：超 14 天的充值 Paddle 已退不了，只能人工办。
        # 这里给的是**条款承诺的动作**，不是推诿。
        blockers.append(
            f"账户还有 ${balance / 100:.2f} 余额。按服务条款第 4 条，"
            "请先来信 hello@transcribe.solutions 办理退回，我们处理完你再回来注销。"
        )
    if running or pp_running:
        n = running + pp_running
        # 转录跑在另一台机器上，删记录不会让它停下来——它跑完还会往回写，
        # 写进一个已经不存在的账户，于是产生一堆没人认领的数据。
        blockers.append(f"还有 {n} 个任务在进行中，等它跑完再注销（通常几分钟）。")
    return {
        "canDelete": not blockers,
        "blockers": blockers,
        "balanceCents": balance,
        "jobs": jobs,
        "glossaries": books,
    }


def purge_transcripts(email: str) -> dict:
    """删掉全部转录与音频，**账户留着**（术语库、余额、账单记录都不动）。

    与注销的区别只在范围：这里删的是「东西」，注销删的是「人」。所以：
    - 防滥用三张表一律不动。**audio_hashes 尤其**：它记的是「这段音频吃过一次免费额度」，
      跟着删的话，「免费传一次 → 删掉 → 再免费传一次」就是无限循环；
    - ledger 也不动。钱是真花了的，删掉稿不等于没消费过，账单该照旧对得上。
    只有在途任务要拦——转录跑在另一台机器上，删记录不会让它停下来，
    它跑完还会往回写，写进一个已经不存在的任务。
    """
    with db.connect() as conn:
        # 本机版：单用户、没有 users 表，不查「账户存不存在」
        n = conn.execute(
            "SELECT count(*) FROM jobs WHERE user_email = %s AND status IN ('queued','running')",
            (email,),
        ).fetchone()[0] + conn.execute(
            "SELECT count(*) FROM postprocess_jobs WHERE user_email = %s "
            "AND status IN ('queued','running')",
            (email,),
        ).fetchone()[0]
        if n:
            raise DeleteRejected(f"还有 {n} 个任务在进行中，等它跑完再删（通常几分钟）。")
        keys = _blob_keys(conn, email)

    deleted_files = blobstore.delete_many(keys) if keys else 0

    with db.connect() as conn:
        with conn.transaction():
            # postprocess_jobs 靠 jobs 的 ON DELETE CASCADE 跟着走；显式先删只是让顺序看得见
            conn.execute("DELETE FROM postprocess_jobs WHERE user_email = %s", (email,))
            n_jobs = conn.execute(
                "DELETE FROM jobs WHERE user_email = %s RETURNING 1", (email,)
            ).rowcount
    return {"jobs": n_jobs, "files": deleted_files}


def _blob_keys(conn, email: str) -> list[str]:
    """这个账户在对象存储里的全部键。

    除音频外都能从 job_id 精确拼出来，不必列举。音频例外——**多扫一次**：
    转录成功后原始上传会被删、只留转码后的 .m4a，但失败/中断的单里原文件还在，
    而它的扩展名跟着用户上传的文件走（audio_key 只记得转码后那个）。
    音频是最敏感的一件，宁可多一次 list 也不留。
    """
    keys: list[str] = []
    for (jid, audio_key) in conn.execute(
        "SELECT id, audio_key FROM jobs WHERE user_email = %s", (email,)
    ).fetchall():
        if audio_key:
            keys.append(audio_key)
        keys += [
            f"result/{jid}.json",
            f"results_edited/{jid}.json",
            f"review/{jid}.json",
            f"review/{jid}.state.json",
            f"review/{jid}.report.md",
        ]
        try:
            keys += blobstore.list_keys(f"audio/{jid}")
        except Exception:  # noqa: BLE001  列不出来不该挡住其余的删除
            pass
        # 中间产物留档（P1/P2/P3 各阶段稿，见 worker._dump_pipeline）。**只能靠列举**：
        # 文件名由 vendor 的产物命名决定（带时间戳、随语言与引擎变），拼不出来。
        try:
            keys += blobstore.list_keys(f"review/{jid}/")
        except Exception:  # noqa: BLE001
            pass
    for (jid, products) in conn.execute(
        "SELECT job_id, products FROM postprocess_jobs WHERE user_email = %s", (email,)
    ).fetchall():
        keys += [f"postprocess/{jid}/inputs.json", f"postprocess/{jid}/qc.md"]
        try:
            keys += [f"postprocess/{jid}/{k}.md" for k in json.loads(products or "[]")]
        except (ValueError, TypeError):
            pass
        # 中间产物留档（见 pipeline/pp_runner._dump_workdir）。同转录侧：**只能靠列举**——
        # 失败单的 products 是空的，拼不出它已经跑完那几步的产物名。
        try:
            keys += blobstore.list_keys(f"postprocess/{jid}/pipeline/")
        except Exception:  # noqa: BLE001
            pass
    return sorted(set(keys))


# 真删的表。**顺序是外键顺序**：postprocess_jobs 挂在 jobs 上（ON DELETE CASCADE 会
# 兜住，但显式先删更清楚），其余互不依赖。
_PURGE = [
    ("postprocess_jobs", "user_email"),
    ("postprocess_profiles", "user_email"),
    ("postprocess_redact_lists", "user_email"),
    ("projects", "user_email"),
    ("jobs", "user_email"),
    ("glossaries", "email"),
    ("glossary", "email"),
    ("sessions", "email"),
    ("login_codes", "email"),
    ("users", "email"),
]

# 留行、换指纹的表：前两张是防滥用闸（删了闸就漏），后两张是财务留存（Privacy 明写要留）。
_ANONYMIZE = [
    ("signup_ips", "email"),
    ("audio_hashes", "email"),
    ("ledger", "email"),
    ("refunds", "email"),
]


def delete_account(email: str) -> dict:
    """执行注销。返回删掉了什么（给前端确认，也给日志）。

    分两段提交，中间隔着删文件那一步（见模块头取舍①）。第一段先把会话和验证码清掉——
    人立刻登出、也发不了新验证码，于是**中间那段时间里不会再有新任务进来**，
    否则第二段删记录时刚上传的那单会变成孤儿（记录被删、机器还在跑）。
    """
    pre = preflight(email)
    if not pre["canDelete"]:
        raise DeleteRejected(pre["blockers"][0])

    with db.connect() as conn:
        with conn.transaction():
            # 先断入口，再取键。顺序反了的话，取键与删记录之间上传的那单会被漏掉。
            conn.execute("DELETE FROM sessions WHERE email = %s", (email,))
            conn.execute("DELETE FROM login_codes WHERE email = %s", (email,))
            keys = _blob_keys(conn, email)

    deleted_files = blobstore.delete_many(keys) if keys else 0

    with db.connect() as conn:
        with conn.transaction():
            fp = email_fingerprint(conn, email)
            for table, col in _ANONYMIZE:
                conn.execute(f"UPDATE {table} SET {col} = %s WHERE {col} = %s", (fp, email))
            for table, col in _PURGE:
                conn.execute(f"DELETE FROM {table} WHERE {col} = %s", (email,))
            conn.execute(
                "INSERT INTO deleted_accounts (email_hash) VALUES (%s) "
                "ON CONFLICT (email_hash) DO NOTHING",
                (fp,),
            )
    return {"jobs": pre["jobs"], "files": deleted_files, "requestedFiles": len(keys)}

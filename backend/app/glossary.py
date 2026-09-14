"""用户术语库：每用户多本，纯文本存储。供 API 读写、worker 注入 P3。

校验口径与前端 src/lib/glossary.ts 必须一致：
  - 总字数 = 全文除换行符外的字符数（保留空格）
  - 单条含义 = 全角竖线 ｜ 后、trim 两端空格后的码点数
"""
from . import config, db

PIPE = "｜"  # U+FF5C 全角竖线
MAX_GLOSSARIES = 20      # 每用户库数上限（管理闸；一次只加载一本，多本不增加转录成本）
MAX_NAME = 40            # 库名长度上限（trim 后码点数）

_COLS = "id, name, language, content, updated_at"


def _row(r) -> dict:
    return {"id": str(r[0]), "name": r[1], "language": r[2],
            "content": r[3], "updated_at": r[4].isoformat()}


def validate(content: str) -> str | None:
    """内容校验：合法返回 None，超限返回错误信息（上层转 422）。"""
    total = sum(1 for c in content if c != "\n")
    if total > config.GLOSSARY_MAX_CHARS:
        return f"glossary too long: {total} > {config.GLOSSARY_MAX_CHARS}"
    for line in content.split("\n"):
        if PIPE in line:
            meaning = line.split(PIPE, 1)[1].strip()
            if len(meaning) > config.GLOSSARY_MAX_MEANING:
                return f"a meaning is too long: {len(meaning)} > {config.GLOSSARY_MAX_MEANING}"
    return None


def validate_name(name: str) -> str | None:
    """库名校验：trim 后 1–40 字、无换行/控制字符。合法返回 None。"""
    n = name.strip()
    if not n:
        return "name required"
    if len(n) > MAX_NAME:
        return f"name too long: {len(n)} > {MAX_NAME}"
    if any(ord(c) < 32 for c in n):
        return "name has control characters"
    return None


def list_glossaries(email: str) -> list[dict]:
    """该用户全部库，按 updated_at 倒序（最近编辑在前）。"""
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM glossaries WHERE email=%s ORDER BY updated_at DESC", (email,)
        ).fetchall()
    return [_row(r) for r in rows]


def _name_taken(conn, email: str, name: str, exclude_id: str | None) -> bool:
    q = "SELECT 1 FROM glossaries WHERE email=%s AND lower(name)=lower(%s)"
    params: list = [email, name.strip()]
    if exclude_id:
        q += " AND id<>%s"
        params.append(exclude_id)
    return conn.execute(q, tuple(params)).fetchone() is not None


def create_glossary(email: str, name: str, language: str | None, content: str):
    """新建一本。成功返回新行 dict；违规返回错误字符串（重名/超上限/校验）。"""
    if (e := validate_name(name)) is not None:
        return e
    if (e := validate(content)) is not None:
        return e
    with db.connect() as conn:
        n = conn.execute("SELECT count(*) FROM glossaries WHERE email=%s", (email,)).fetchone()[0]
        if n >= MAX_GLOSSARIES:
            return f"too many glossaries: limit {MAX_GLOSSARIES}"
        if _name_taken(conn, email, name, None):
            return "name already exists"
        r = conn.execute(
            f"INSERT INTO glossaries (email, name, language, content) "
            f"VALUES (%s,%s,%s,%s) RETURNING {_COLS}",
            (email, name.strip(), language, content),
        ).fetchone()
    return _row(r)


def update_glossary(email: str, gid: str, name: str, language: str | None, content: str):
    """改名/改语言/改内容。成功返回 dict；违规返回错误字符串；不存在/非本人返回 None。"""
    if (e := validate_name(name)) is not None:
        return e
    if (e := validate(content)) is not None:
        return e
    with db.connect() as conn:
        owns = conn.execute("SELECT 1 FROM glossaries WHERE id=%s AND email=%s", (gid, email)).fetchone()
        if not owns:
            return None
        if _name_taken(conn, email, name, gid):
            return "name already exists"
        r = conn.execute(
            f"UPDATE glossaries SET name=%s, language=%s, content=%s, updated_at=now() "
            f"WHERE id=%s AND email=%s RETURNING {_COLS}",
            (name.strip(), language, content, gid, email),
        ).fetchone()
    return _row(r)


def delete_glossary(email: str, gid: str) -> bool:
    with db.connect() as conn:
        r = conn.execute(
            "DELETE FROM glossaries WHERE id=%s AND email=%s RETURNING id", (gid, email)
        ).fetchone()
    return r is not None


def owns(email: str, gid: str) -> bool:
    """该 gid 是否属于 email 名下某本术语库？供 create_job 校验归属，杜绝知道/撞对别人
    库 UUID 就能蹭用（进而经复核结果间接读出）。非法 UUID 字符串在 psycopg 插 UUID 列会抛异常
    ——这里 try/except 归 False，别让它冒泡成 500（此时若在 R2 落库后校验，异常会致孤儿）。"""
    try:
        with db.connect() as conn:
            r = conn.execute("SELECT 1 FROM glossaries WHERE id=%s AND email=%s", (gid, email)).fetchone()
        return r is not None
    except Exception:
        return False


def strip_unfinished(content: str) -> str:
    """去掉「只有术语名、释义还空着」的行——**注入转录引擎前必须过滤**。

    这些行是待办层：用户还没填的坑，界面上要显眼地留着，但它们对 P3 毫无用处。
    库全文是作为「必须服从的硬证据」原样注入的，一条没有释义的词条进去，
    融合脑只会拿到一个不知道该怎么办的名字。

    只掐「有竖线但右侧为空」的行。没有竖线的行（用户随手写的注释）本来就不是条目，
    保持现有行为不动。
    """
    out = []
    for line in (content or "").split("\n"):
        if PIPE in line and not line.split(PIPE, 1)[1].strip():
            continue
        out.append(line)
    return "\n".join(out)


def get_glossary_content(gid: str | None) -> str:
    """worker 注入用：按 id 取内容；id 空或取不到返回 ""（防御式：删库不致命）。

    ⚠️ 这里出去的东西会直接变成 P3 的硬证据，所以待办层在这一步被摘掉
    （见 strip_unfinished）。别在调用方补这道过滤——worker 有多条取库路径，
    放在源头才不会漏。
    """
    if not gid:
        return ""
    with db.connect() as conn:
        r = conn.execute("SELECT content FROM glossaries WHERE id=%s", (gid,)).fetchone()
    return strip_unfinished(r[0]) if r else ""


# ── 缺口（待办层）──────────────────────────────────────────────────────────
# 与库正文**分表**存储，理由同上：正文会被原样注入转录引擎，而缺口是给人看的。
# 存进 content 里迟早会被某条注入路径带出去。

GAP_MAX_CHARS = 60
GAP_MAX_COUNT = 8


def list_gaps(gid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, text, status FROM glossary_gaps WHERE glossary_id=%s "
            "ORDER BY created_at, id", (gid,)
        ).fetchall()
    return [{"id": str(r[0]), "text": r[1], "status": r[2]} for r in rows]


def _norm_gap(t) -> str:
    return " ".join(str(t or "").split())[:GAP_MAX_CHARS]


def add_gaps(gid: str, texts: list[str]) -> None:
    """把「这一轮对这本库的判断」落成缺口列表。

    两件事，顺序要紧：

    1) **退役**：上一轮留下、这一轮没再提的 open 缺口收进「已处理」。缺口说的是
       「这本库还缺哪一类」，是**针对当时那版正文**的判断；用户后来把那类补上了、
       或干脆重写了整本库，旧判断就不成立了。判断依据只用「最新一轮还提不提」，
       不看正文改了多少——正文差异算不出「这条缺口还成不成立」，硬算只会误伤。
       ⚠️ 只在本轮**确有产出**时退役：一轮 0 缺口更可能是模型这次没答好，
       不能拿它把用户攒着的待办清空。退役后仍可在「已处理」里「恢复」。

    2) **追加**：按文本去重——重跑时同一条不该冒出第二份，而且**已经 handled 的
       不会因为重跑就复活**（去重看的是全部历史，不只 open 的）。
    """
    incoming = [t for t in (_norm_gap(x) for x in texts) if t]
    if not incoming:
        return
    before = list_gaps(gid)
    retire = [g["id"] for g in before if g["status"] == "open" and g["text"] not in set(incoming)]
    seen = {g["text"] for g in before}
    fresh = []
    for t in incoming:
        if t not in seen:
            seen.add(t)
            fresh.append(t)
    # 上限按「**退役之后**还剩几条未处理」算：处理掉的不占位，否则用久了永远加不进新的。
    # ⚠️ 退役数要从 open 里减掉、不是再减一次余量——写成 `MAX - open - retire` 的话，
    # 上一轮 5 条这一轮全退役时余量成了 8-5-5=-2，新缺口一条都进不来，
    # 界面上就是「旧的清空了、新的没出现」（2026-08-05 生产实测踩到）。
    open_after = sum(1 for g in before if g["status"] == "open") - len(retire)
    room = GAP_MAX_COUNT - open_after
    with db.connect() as conn:
        for gap_id in retire:
            conn.execute(
                "UPDATE glossary_gaps SET status='handled' WHERE id=%s AND glossary_id=%s",
                (gap_id, gid))
        for t in fresh[:max(room, 0)]:
            conn.execute(
                "INSERT INTO glossary_gaps (glossary_id, text, status) VALUES (%s, %s, 'open')",
                (gid, t))


def set_gap_status(gid: str, gap_id: str, status: str) -> bool:
    if status not in ("open", "handled"):
        return False
    try:
        with db.connect() as conn:
            r = conn.execute(
                "UPDATE glossary_gaps SET status=%s WHERE id=%s AND glossary_id=%s RETURNING id",
                (status, gap_id, gid)).fetchone()
        return r is not None
    except Exception:
        return False        # 非法 UUID 不该冒泡成 500，与 owns() 同口径

"""本机数据库：SQLite。对外保持线上 psycopg 的用法 —— `with db.connect() as conn: conn.execute(sql, params).fetchone()`。

**为什么在这里翻译，而不是去改各模块的 SQL**：本地版以线上为底本，业务模块要原样同步。
逐个文件把 Postgres 写法改成 SQLite，线上每改一次就得人工合并一次；集中在连接层翻译，
那些模块一行都不用动。

翻译只覆盖保留模块里**实际出现过**的写法（2026-09-14 逐条核过，见 `_RULES`）。
没覆盖到的写法 SQLite 会直接报语法错 —— 不会静默跑出错的结果。

在这一层抹平的差别：
- 占位符 `%s` → `?`，`%%` → `%`
- 时间一律存 UTC 定宽文本 `YYYY-MM-DD HH:MM:SS.ffffff+00:00`（按字符串比较就是按时间比较）。
  精度到微秒，与 Postgres 一致：只到毫秒的话，同一毫秒写入的行按时间排序不稳，写进去的时刻读回来也对不上。
  `now()`、`now() ± %s * interval '1 minute'`、`now() - interval '7 days'`、
  `to_char(x AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI')`、`EXTRACT(EPOCH FROM (a - b))`、
  `date_trunc('day', x)` 由下面注册的函数实现。
  读出来的时间文本转回带时区的 datetime —— 线上代码会对它调 `.isoformat()`、做加减。
- `metrics` 列（线上是 JSONB）读出来解析成 dict；写入的 dict / list / `Jsonb(...)` 一律存 JSON 文本
- `= ANY(%s)` → `IN (SELECT value FROM json_each(?))`；`GREATEST / LEAST` → `max / min`；
  `::text / ::uuid / ::jsonb` 去掉；`(expr)::float / ::int / ::bigint` → `CAST`；
  `FOR UPDATE [SKIP LOCKED]` 去掉（本机单进程，SQLite 的写锁本身就是串行的）
- `conn.transaction()` → `BEGIN IMMEDIATE … COMMIT / ROLLBACK`

线上这个文件还有一道「连远程库时拒绝无 WHERE 的整表写」守卫。本机只有一个本地文件、没有远程可连，故不带。
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"

# 线上是 JSONB 的列。保留下来的表里只有这一个；读出时按列名解析。
_JSON_COLUMNS = {"metrics"}
_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")


class Jsonb:
    """线上 `psycopg.types.json.Jsonb` 的替身：写入时存 JSON 文本。"""

    def __init__(self, obj):
        self.obj = obj


# ───────────── 时间 ─────────────

def _fmt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f+00:00")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _now() -> str:
    return _fmt(datetime.now(timezone.utc))


_UNITS = {"second": "seconds", "minute": "minutes", "hour": "hours", "day": "days"}


def _shift(ts: str | None, sign: str, n, unit: str) -> str | None:
    if ts is None or n is None:
        return None
    d = timedelta(**{_UNITS[unit.lower()]: float(n)})
    t = _parse(ts)
    return _fmt(t + d if sign == "+" else t - d)


def _epoch(ts: str | None) -> float | None:
    return None if ts is None else _parse(ts).timestamp()


# Postgres to_char 模板 → strftime。顺序有讲究：HH24 要先于 HH，MM 要先于 MI。
_PG_FMT = [("YYYY", "%Y"), ("HH24", "%H"), ("MM", "%m"), ("DD", "%d"), ("MI", "%M"), ("SS", "%S")]


def _to_char(ts: str | None, tz: str, fmt: str) -> str | None:
    if ts is None:
        return None
    for pg, py in _PG_FMT:
        fmt = fmt.replace(pg, py)
    return _parse(ts).astimezone(ZoneInfo(tz)).strftime(fmt)


def _date_trunc(unit: str, ts: str | None) -> str | None:
    if ts is None:
        return None
    t = _parse(ts)
    if unit == "day":
        t = t.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "hour":
        t = t.replace(minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"date_trunc 只支持 day / hour，收到 {unit}")
    return _fmt(t)


_FUNCS = [("now", 0, _now), ("tx_shift", 4, _shift), ("tx_epoch", 1, _epoch),
          ("tx_to_char", 3, _to_char), ("date_trunc", 2, _date_trunc)]


# ───────────── SQL 翻译 ─────────────

def _cast(m: re.Match) -> str:
    return f"CAST(({m.group(1)}) AS {'REAL' if m.group(2).lower() == 'float' else 'INTEGER'})"


_RULES: list[tuple[re.Pattern, object]] = [
    (re.compile(r"\s+FOR UPDATE(?:\s+SKIP LOCKED)?", re.I), ""),
    (re.compile(r"EXTRACT\s*\(\s*EPOCH\s+FROM\s+\(\s*([\w.()]+)\s*-\s*([\w.()]+)\s*\)\s*\)(?:::int)?", re.I),
     r"CAST(tx_epoch(\1) - tx_epoch(\2) AS INTEGER)"),
    (re.compile(r"to_char\(\s*([\w.]+)\s+AT TIME ZONE\s+'([^']+)'\s*,\s*'([^']+)'\s*\)", re.I),
     r"tx_to_char(\1, '\2', '\3')"),
    (re.compile(r"now\(\)\s*([-+])\s*(%s|\d+)\s*\*\s*interval\s*'1 (second|minute|hour|day)'", re.I),
     r"tx_shift(now(), '\1', \2, '\3')"),
    (re.compile(r"now\(\)\s*([-+])\s*interval\s*'(\d+) (second|minute|hour|day)s?'", re.I),
     r"tx_shift(now(), '\1', \2, '\3')"),
    (re.compile(r"=\s*ANY\s*\(\s*%s\s*\)", re.I), "IN (SELECT value FROM json_each(%s))"),
    (re.compile(r"\bGREATEST\(", re.I), "max("),
    (re.compile(r"\bLEAST\(", re.I), "min("),
    (re.compile(r"\(([^()]*)\)::(float|int|bigint)\b", re.I), _cast),
    (re.compile(r"::(text|uuid|jsonb)\b", re.I), ""),
    (re.compile(r"\bjsonb_each\(", re.I), "json_each("),
]


def translate(sql: str) -> str:
    for rx, rep in _RULES:
        sql = rx.sub(rep, sql)
    return sql.replace("%s", "?").replace("%%", "%")


def _in(v):
    if isinstance(v, Jsonb):
        return json.dumps(v.obj, ensure_ascii=False)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, datetime):
        return _fmt(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _out(name: str, v):
    if isinstance(v, str):
        if name in _JSON_COLUMNS:
            return json.loads(v)
        if _TS.match(v):
            return _parse(v)
    return v


# ───────────── 连接 ─────────────

class _Cursor:
    """结果一次取完：本机查询量小，这样 rowcount 对带 RETURNING 的语句也可靠。"""

    def __init__(self, cur: sqlite3.Cursor, sql: str):
        names = [d[0] for d in (cur.description or [])]
        self._rows = [tuple(_out(n, v) for n, v in zip(names, r)) for r in cur.fetchall()] if names else []
        self.description = cur.description
        self.rowcount = len(self._rows) if re.search(r"\bRETURNING\b", sql, re.I) else cur.rowcount

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    def __init__(self, raw: sqlite3.Connection):
        self._raw = raw

    def execute(self, sql: str, params=()) -> _Cursor:
        q = translate(sql)
        return _Cursor(self._raw.execute(q, tuple(_in(p) for p in (params or ()))), q)

    @contextmanager
    def transaction(self):
        self._raw.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except BaseException:
            self._raw.execute("ROLLBACK")
            raise
        self._raw.execute("COMMIT")

    def close(self) -> None:
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def connect() -> _Conn:
    path = Path(config.DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None：逐句自动提交，与线上 autocommit=True 一致；要成组提交走 conn.transaction()
    raw = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    raw.execute("PRAGMA foreign_keys = ON")
    for name, n, fn in _FUNCS:
        raw.create_function(name, n, fn)
    return _Conn(raw)


def init_schema() -> None:
    with connect() as conn:
        conn._raw.execute("PRAGMA journal_mode = WAL")    # 接口线程与后台线程同时读写
        conn._raw.executescript(_SCHEMA.read_text(encoding="utf-8"))
        # 后加的列给老库补上：CREATE TABLE IF NOT EXISTS 不会往已有的表里加列
        cols = {r[1] for r in conn._raw.execute("PRAGMA table_info(jobs)")}
        if "cancel_requested" not in cols:
            conn._raw.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")

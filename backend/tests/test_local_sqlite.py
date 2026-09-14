"""本机 SQLite 翻译层：线上模块里出现过的 Postgres 写法，逐类在真库上跑一遍。

这是本地独有的测试（线上没有这个文件）。线上那几组连库测试（任务、术语库、后处理、告警……）
改连本机库后照样通过，才是翻译层真正的验收；这里只钉住每条翻译规则本身。
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import config, db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "t.db"))
    db.init_schema()
    with db.connect() as c:
        yield c


def _job(c, **cols):
    cols = {"audio_key": "audio/x.m4a", **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return c.execute(f"INSERT INTO jobs ({names}) VALUES ({marks}) RETURNING id", tuple(cols.values())).fetchone()[0]


def test_translate_rules():
    t = db.translate
    assert t("UPDATE jobs SET x=%s WHERE id=%s") == "UPDATE jobs SET x=? WHERE id=?"
    assert "tx_shift(now(), '+', ?, 'minute')" in t("not_before=now() + %s * interval '1 minute'")
    assert "tx_shift(now(), '-', 7, 'day')" in t("updated_at > now() - interval '7 days'")
    assert "tx_shift(now(), '-', 15, 'minute')" in t("acquired_at > now() - interval '15 minutes'")
    assert t("to_char(p.updated_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI')") == \
        "tx_to_char(p.updated_at, 'Asia/Shanghai', 'MM-DD HH24:MI')"
    assert t("EXTRACT(EPOCH FROM (now() - created_at))::int") == "CAST(tx_epoch(now()) - tx_epoch(created_at) AS INTEGER)"
    assert t("WHERE node = ANY(%s)") == "WHERE node IN (SELECT value FROM json_each(?))"
    assert t("GREATEST(amount_cny - %s, 0)") == "max(amount_cny - ?, 0)"
    assert t("(j.metrics->>'durationSec')::float") == "CAST((j.metrics->>'durationSec') AS REAL)"
    assert t("sum((metrics->'cost'->>'hit')::bigint)") == "sum(CAST((metrics->'cost'->>'hit') AS INTEGER))"
    assert t("COALESCE(%s::text,'manual')") == "COALESCE(?,'manual')"
    assert t("ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1") == "ORDER BY created_at LIMIT 1"
    assert t("LIKE '%%x%%'") == "LIKE '%x%'"


def test_insert_returning_uuid_and_defaults(conn):
    jid = _job(conn)
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", jid)
    status, created = conn.execute("SELECT status, created_at FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert status == "queued"
    assert isinstance(created, datetime) and created.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - created).total_seconds()) < 5


def test_metrics_json_roundtrip_and_jsonb_wrapper(conn):
    jid = _job(conn)
    conn.execute("UPDATE jobs SET metrics=%s, updated_at=now() WHERE id=%s", (db.Jsonb({"a": 1, "说": "话"}), jid))
    assert conn.execute("SELECT metrics FROM jobs WHERE id=%s", (jid,)).fetchone()[0] == {"a": 1, "说": "话"}
    got = conn.execute("UPDATE jobs SET metrics=COALESCE(%s, metrics) WHERE id=%s RETURNING metrics", (None, jid))
    assert got.fetchone()[0] == {"a": 1, "说": "话"}
    assert conn.execute("SELECT metrics->>'a' FROM jobs WHERE id=%s", (jid,)).fetchone()[0] == 1


def test_interval_shift_compares_as_time(conn):
    jid = _job(conn, status="running")
    conn.execute("UPDATE jobs SET updated_at=%s WHERE id=%s", (datetime.now(timezone.utc) - timedelta(minutes=40), jid))
    stale = conn.execute("SELECT id FROM jobs WHERE status='running' AND updated_at < now() - %s * interval '1 minute'", (30,))
    assert [r[0] for r in stale.fetchall()] == [jid]
    fresh = conn.execute("SELECT id FROM jobs WHERE updated_at < now() - %s * interval '1 minute'", (60,))
    assert fresh.fetchall() == []
    row = conn.execute("UPDATE jobs SET not_before=now() + %s * interval '1 minute' WHERE id=%s RETURNING not_before", (10, jid))
    nb = row.fetchone()[0]
    assert timedelta(minutes=9) < nb - datetime.now(timezone.utc) < timedelta(minutes=11)


def test_claim_with_skip_locked_subquery(conn):
    first = _job(conn)
    _job(conn)
    row = conn.execute(
        """UPDATE jobs SET status='running', attempts = attempts + 1, updated_at=now()
           WHERE id = (SELECT id FROM jobs WHERE status='queued'
                       AND (not_before IS NULL OR not_before <= now())
                       ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING id, attempts""").fetchone()
    assert row == (first, 1)


def test_to_char_extract_filter_and_date_trunc(conn):
    _job(conn, status="done")
    _job(conn, status="failed")
    label, age = conn.execute(
        "SELECT to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'), "
        "EXTRACT(EPOCH FROM (now() - created_at))::int FROM jobs LIMIT 1").fetchone()
    assert re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", label) and 0 <= age < 5
    done, failed = conn.execute(
        "SELECT count(*) FILTER (WHERE status='done' AND updated_at >= date_trunc('day', now())), "
        "count(*) FILTER (WHERE status='failed') FROM jobs").fetchone()
    assert (done, failed) == (1, 1)


def test_any_with_list_param_and_greatest(conn):
    conn.execute("INSERT INTO node_events (node, outcome) VALUES (%s, %s), (%s, %s), (%s, %s)",
                 ("a", "ok", "b", "ok", "c", "error"))
    rows = conn.execute("SELECT node FROM node_events WHERE node = ANY(%s) ORDER BY node", (["a", "c"],)).fetchall()
    assert rows == [("a",), ("c",)]
    conn.execute("UPDATE vendor_balances SET amount_cny = 3 WHERE vendor = 'DeepSeek'")
    left = conn.execute("UPDATE vendor_balances SET amount_cny = GREATEST(amount_cny - %s, 0) "
                        "WHERE vendor = 'DeepSeek' RETURNING amount_cny", (5,)).fetchone()[0]
    assert left == 0


def test_transaction_rolls_back_and_rowcount_with_returning(conn):
    _job(conn, user_email="a@x.com")
    _job(conn, user_email="a@x.com")
    with pytest.raises(RuntimeError):
        with conn.transaction():
            conn.execute("DELETE FROM jobs WHERE user_email = %s", ("a@x.com",))
            raise RuntimeError("中途出错")
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2
    with conn.transaction():
        n = conn.execute("DELETE FROM jobs WHERE user_email = %s RETURNING 1", ("a@x.com",)).rowcount
    assert n == 2


def test_foreign_key_cascade(conn):
    jid = _job(conn, user_email="a@x.com")
    conn.execute("INSERT INTO postprocess_jobs (job_id, user_email, steps) VALUES (%s, %s, %s)", (jid, "a@x.com", '["narrate"]'))
    conn.execute("DELETE FROM jobs WHERE id = %s", (jid,))
    assert conn.execute("SELECT count(*) FROM postprocess_jobs").fetchone()[0] == 0


def test_schema_is_rerunnable(conn):
    db.init_schema()
    assert conn.execute("SELECT count(*) FROM vendor_balances").fetchone()[0] == 2

"""后处理 DB 真逻辑集成测（infra 标记，需本地 Postgres）。
跑法：DATABASE_URL=...localhost... python3 -m pytest -m infra tests/test_postprocess_db_integration.py
受 server/conftest.py 守卫：DATABASE_URL host 非本地则硬退出。"""
import uuid

import pytest

from app import db, postprocess

_EMAIL = "pp_db_test@example.com"
_OTHER = "pp_other@example.com"


def _mk_job(email=_EMAIL) -> str:
    with db.connect() as c:
        row = c.execute(
            "INSERT INTO jobs (audio_key, lang, recording_type, user_email, status) "
            "VALUES ('audio/t.m4a','zh','meeting',%s,'done') RETURNING id", (email,)
        ).fetchone()
    return str(row[0])


@pytest.fixture(autouse=True)
def _clean():
    db.init_schema()
    def wipe():
        with db.connect() as c:
            c.execute("DELETE FROM postprocess_jobs WHERE user_email IN (%s,%s)", (_EMAIL, _OTHER))
            c.execute("DELETE FROM postprocess_redact_lists WHERE user_email IN (%s,%s)", (_EMAIL, _OTHER))
            c.execute("DELETE FROM jobs WHERE user_email IN (%s,%s)", (_EMAIL, _OTHER))
    wipe()
    yield
    wipe()


# ── 配置 CRUD ──

@pytest.mark.infra
def test_redact_list_crud_and_cap():
    r = postprocess.create_redact_list(_EMAIL, "清单", "词A\n词B")
    assert isinstance(r, dict) and r["content"] == "词A\n词B"
    import app.postprocess as m
    old = m._KINDS["list"]["cap"]
    m._KINDS["list"]["cap"] = 1
    try:
        err = postprocess.create_redact_list(_EMAIL, "清单2", "")
        assert isinstance(err, str) and "too many" in err
    finally:
        m._KINDS["list"]["cap"] = old


# ── pp_jobs 状态机 ──

@pytest.mark.infra
def test_create_claim_done_lifecycle():
    jid = _mk_job()
    assert postprocess.create_or_reset(jid, _EMAIL, ["narrate", "redact"], 200) is True
    # queued 在途再发起 → False（上层 409）
    assert postprocess.create_or_reset(jid, _EMAIL, ["narrate"], 200) is False
    pp = postprocess.claim_specific(jid)
    assert pp["status"] == "running" and pp["steps"] == ["narrate", "redact"] and pp["attempts"] == 1
    assert postprocess.claim_specific(jid) is None      # 已被领
    postprocess.update_step(jid, "redact", 2)
    got = postprocess.get_pp_job(jid)
    assert got["current_step"] == "redact" and got["step_index"] == 2
    assert postprocess.set_done(jid, ["narrate", "redact"], 5, True) is True
    assert postprocess.set_done(jid, [], 0, False) is False   # 终态持有守卫：done 后 no-op
    got = postprocess.get_pp_job(jid)
    assert got["status"] == "done" and got["products"] == ["narrate", "redact"]
    assert got["qc_fix_count"] == 5 and got["has_qc"] is True
    # done 重发 = 同行重置
    assert postprocess.create_or_reset(jid, _EMAIL, ["redact"], 0, list_name="清单") is True
    got = postprocess.get_pp_job(jid)
    assert got["status"] == "queued" and got["steps"] == ["redact"] and got["products"] == []


@pytest.mark.infra
def test_failed_and_requeue_delayed():
    jid = _mk_job()
    postprocess.create_or_reset(jid, _EMAIL, ["narrate"], 0)
    postprocess.claim_specific(jid)
    assert postprocess.set_failed(jid, "narrate", "boom") is True
    got = postprocess.get_pp_job(jid)
    assert got["status"] == "failed" and got["failed_step"] == "narrate"
    assert got["error_public"] == postprocess.ERROR_PUBLIC
    # failed 重发 = 重置重跑；撞顶 requeue 延后 → not_before 挡住派单/领取
    assert postprocess.create_or_reset(jid, _EMAIL, ["narrate"], 0) is True
    postprocess.claim_specific(jid)
    assert postprocess.requeue_delayed(jid, delay_min=30) is True
    assert postprocess.get_pp_job(jid)["status"] == "queued"
    assert postprocess.claim_next_queued() is None          # not_before 未到点不领
    assert postprocess.list_queued_ids(10) == []            # 派单也不取
    with db.connect() as c:                                  # 到点后恢复可派
        c.execute("UPDATE postprocess_jobs SET not_before=now() - interval '1 minute' WHERE job_id=%s", (jid,))
    assert postprocess.list_queued_ids(10) == [jid]
    assert postprocess.claim_next_queued()["job_id"] == jid


@pytest.mark.infra
def test_requeue_stale_running_and_attempt_cap():
    jid = _mk_job()
    postprocess.create_or_reset(jid, _EMAIL, ["narrate"], 0)
    postprocess.claim_specific(jid)
    with db.connect() as c:
        c.execute("UPDATE postprocess_jobs SET updated_at=now() - interval '2 hours' WHERE job_id=%s", (jid,))
    assert postprocess.requeue_stale_running(30, max_attempts=3) == 1
    assert postprocess.get_pp_job(jid)["status"] == "queued"   # 未到上限 → 重排
    with db.connect() as c:
        c.execute("UPDATE postprocess_jobs SET status='running', attempts=3, "
                  "updated_at=now() - interval '2 hours' WHERE job_id=%s", (jid,))
    assert postprocess.requeue_stale_running(30, max_attempts=3) == 1
    assert postprocess.get_pp_job(jid)["status"] == "failed"   # 到上限 → 判失败（防毒任务）


@pytest.mark.infra
def test_job_summaries_scoped():
    jid = _mk_job()
    other_jid = _mk_job(email=_OTHER)
    postprocess.create_or_reset(jid, _EMAIL, ["narrate"], 0)
    postprocess.create_or_reset(other_jid, _OTHER, ["redact"], 0)
    s = postprocess.job_summaries(_EMAIL)
    assert set(s) == {jid}
    assert s[jid]["totalSteps"] == 1 and s[jid]["status"] == "queued"



@pytest.mark.infra
def test_get_pp_job_missing_and_bad_uuid():
    assert postprocess.get_pp_job(str(uuid.uuid4())) is None
    assert postprocess.get_pp_job("not-a-uuid") is None

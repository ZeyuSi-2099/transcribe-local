# -*- coding: utf-8 -*-
"""降级留痕的**真打库**验证（infra）：单元测试的假对象照不出这一层。

单测里 postprocess/jobstore 都是内存假件，列名拼错、JSON 序列化不对、
SQL 写错字段全都测不出来——只有真连 Postgres 跑一遍才知道
「写进去的东西读得回来、而且读成了前端要的形状」。

跑法（DATABASE_URL 必须显式指向本地，否则 conftest 硬退出）：
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/transcribe \
    python3 -m pytest -m infra tests/test_pp_degraded_integration.py
"""
import json
import uuid

import pytest

from app import db, jobstore, postprocess


def _mk_job(email: str) -> str:
    """建一条最小的 jobs 行（后处理表外键指向它，且总览要 join 它取文件名）。"""
    job_id = str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, user_email, file_name, audio_key, status, lang) "
            "VALUES (%s, %s, %s, %s, 'done', 'zh')",
            (job_id, email, "降级留痕验证.flac", f"audio/{job_id}.m4a"),
        )
    return job_id


@pytest.fixture
def pp_job():
    email = f"degr-{uuid.uuid4().hex[:8]}@example.com"
    job_id = _mk_job(email)
    postprocess.create_or_reset(job_id, email, ["narrate"], price_cents=200)
    yield job_id, email
    with db.connect() as conn:
        conn.execute("DELETE FROM postprocess_jobs WHERE job_id=%s", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=%s", (job_id,))


@pytest.mark.infra
def test_degraded_steps_column_exists_and_defaults_null(pp_job):
    """新加的列真在表上，且新单默认 NULL（＝「无从得知」，不是「没降级」）。"""
    job_id, _ = pp_job
    with db.connect() as conn:
        row = conn.execute("SELECT degraded_steps FROM postprocess_jobs WHERE job_id=%s",
                           (job_id,)).fetchone()
    assert row is not None and row[0] is None


@pytest.mark.infra
def test_mark_degraded_writes_and_is_idempotent(pp_job):
    """写得进去、读得回来，且重复标记不会写成 ["narrate","narrate"]。"""
    job_id, _ = pp_job
    postprocess.mark_degraded(job_id, "narrate")
    postprocess.mark_degraded(job_id, "narrate")      # 重跑/重试时会再调一次
    with db.connect() as conn:
        row = conn.execute("SELECT degraded_steps FROM postprocess_jobs WHERE job_id=%s",
                           (job_id,)).fetchone()
    assert json.loads(row[0]) == ["narrate"]


@pytest.mark.infra
def test_mark_degraded_accumulates_across_steps(pp_job):
    """多步各自降级要能叠加——存数组不存布尔的理由就在这。"""
    job_id, _ = pp_job
    postprocess.mark_degraded(job_id, "narrate")
    postprocess.mark_degraded(job_id, "redact")
    with db.connect() as conn:
        row = conn.execute("SELECT degraded_steps FROM postprocess_jobs WHERE job_id=%s",
                           (job_id,)).fetchone()
    assert json.loads(row[0]) == ["narrate", "redact"]


@pytest.mark.infra
def test_mark_degraded_on_missing_job_does_not_raise():
    """留痕失败绝不能连累已经跑成功的降级产出（同 p3_health 的埋点原则）。"""
    postprocess.mark_degraded(str(uuid.uuid4()), "narrate")   # 不存在的单
    # 没抛异常即通过


@pytest.mark.infra
def test_admin_overview_returns_postprocess_in_frontend_shape(pp_job):
    """总览接口真能把后处理查出来，且**形状就是前端 AdminPostprocess 那一套**。
    形状对不上前端会白屏或漏显示，而单测的假件永远是对的——这条只有真查库才有意义。"""
    job_id, email = pp_job
    postprocess.mark_degraded(job_id, "narrate")
    ov = jobstore.admin_overview()
    assert "postprocess" in ov
    mine = [r for r in ov["postprocess"] if r["jobId"] == job_id]
    assert len(mine) == 1, "刚建的后处理单没被总览查出来"
    r = mine[0]
    assert r["fileName"] == "降级留痕验证.flac"        # join jobs 取到了文件名
    assert r["userEmail"] == email
    assert r["steps"] == ["narrate"]                   # steps 是 JSON 文本，要解析成数组
    assert r["degradedSteps"] == ["narrate"]           # 降级步同理
    assert r["priceCents"] == 200
    assert isinstance(r["updatedAt"], str) and len(r["updatedAt"]) >= 8   # 已格式化成 MM-DD HH:MM
    # 前端按这几个键渲染，缺一个就是列空着
    for k in ("status", "currentStep", "stepIndex", "qcFixCount", "hasQc",
              "failedStep", "errorPublic", "attempts"):
        assert k in r, f"总览少下发 {k}"


@pytest.mark.infra
def test_admin_overview_distinguishes_never_degraded_from_unknown(pp_job):
    """没降级过的单 degradedSteps 是 None（老单同样是 None）——**不能变成空数组**，
    否则前端分不清「跑过没降级」和「那时还没这一列」。"""
    job_id, _ = pp_job
    ov = jobstore.admin_overview()
    r = [x for x in ov["postprocess"] if x["jobId"] == job_id][0]
    assert r["degradedSteps"] is None

"""取消任务：排队中的直接取消；在跑的结束子进程，不留半截。本地独有的测试（线上没有这个文件）。

在跑的那条是真起一个子进程（假转录入口，一直「算」下去），点取消后几秒内必须结束。
"""
import glob
import os
import tempfile
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app import blobstore, db, jobstore, local, worker

pytestmark = pytest.mark.infra


@pytest.fixture
def client():
    return TestClient(api.app, base_url="http://127.0.0.1:8765")


def _status(jid):
    with db.connect() as conn:
        return conn.execute("SELECT status, error_public FROM jobs WHERE id=%s", (jid,)).fetchone()


def test_cancel_queued_job(client):
    jid = local.create_job("audio/q.wav", "zh", "all", file_name="q.wav")
    r = client.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 200 and r.json() == {"state": "canceled"}
    assert tuple(_status(jid)) == ("failed", local.CANCELED_PUBLIC)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 409          # 已经结束的不用再取消


def test_cancel_running_job_kills_the_child_and_leaves_nothing(client, monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_ORCHESTRATOR", "fake_orchestrator")
    monkeypatch.setattr(worker, "ISOLATE", True)
    blobstore.put_bytes("audio/r.wav", b"RIFF")
    jid = local.create_job("audio/r.wav", "zh", "all", file_name="r.wav")

    t = threading.Thread(target=worker.process_one, daemon=True)
    t.start()
    deadline = time.time() + 60
    while time.time() < deadline:                        # 等子进程起来、报出第一格进度
        job = jobstore.get_job(jid)
        if job.status == "running" and job.phase == "P1":
            break
        time.sleep(0.2)
    else:
        pytest.fail("子进程没有跑起来")

    t0 = time.time()
    assert client.post(f"/api/jobs/{jid}/cancel").json() == {"state": "requested"}
    t.join(15)
    assert not t.is_alive(), "取消后后台没有结束这一单"
    assert time.time() - t0 < 10
    assert tuple(_status(jid)) == ("failed", local.CANCELED_PUBLIC)
    assert not blobstore.exists(f"result/{jid}.json") and not blobstore.exists(f"review/{jid}.json")
    assert glob.glob(os.path.join(tempfile.gettempdir(), f"job_{jid}_*")) == []   # 临时目录清掉了

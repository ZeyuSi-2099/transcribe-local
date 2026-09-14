"""本机接口冒烟：单用户、不收费、只接受本机访问。本地独有的测试（线上没有这个文件）。"""
import io
import shutil
import wave

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app import blobstore, db, local

pytestmark = pytest.mark.infra   # 连本机临时库（conftest 自动建表）


@pytest.fixture
def client():
    return TestClient(api.app, base_url="http://127.0.0.1:8765")


def _wav(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def test_worker_imports():
    """后台模块要能导入 —— 它连着定字、后处理一串线上模块，少搬一个文件，服务一启动就报错（接口测试照绿）。"""
    import importlib
    importlib.import_module("app.worker")


def test_me_is_local_admin(client):
    assert client.get("/api/me").json() == {"email": local.EMAIL, "isAdmin": True}


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="需要 ffprobe 测时长")
def test_upload_creates_queued_job_and_lists_it(client):
    r = client.post("/api/jobs", files={"file": ("访谈.wav", _wav(1.5), "audio/wav")}, data={"lang": "zh"})
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    # 录音的键用上传时现生成的 id，与库里的任务 id 不是同一个（同线上）——所以只数个数
    keys = blobstore.list_keys("audio/")
    assert len(keys) == 1 and keys[0].endswith(".wav")
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "queued"
    jobs = client.get("/api/jobs").json()["jobs"]
    assert [j["fileName"] for j in jobs] == ["访谈.wav"]
    assert jobs[0]["costCents"] == 0
    assert jobs[0]["durationSec"] == 2          # 服务端 ffprobe 实测 1.5 秒，四舍六入五成双


def test_unsupported_upload_rejected(client):
    r = client.post("/api/jobs", files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415


def test_glossary_crud(client):
    r = client.post("/api/glossaries", json={"name": "烘焙", "content": "## 工艺\n烫种 ｜ 用热水预先糊化部分面粉"})
    assert r.status_code == 200, r.text
    names = [g["name"] for g in client.get("/api/glossaries").json()["glossaries"]]
    assert names == ["烘焙"]


def test_purge_transcripts_removes_jobs_and_files(client):
    jid = local.create_job("audio/x.wav", "zh", "all", file_name="x.wav")
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status='done' WHERE id=%s", (jid,))
    blobstore.put_bytes("audio/x.wav", b"-")
    blobstore.put_bytes(f"result/{jid}.json", b"[]")
    r = client.delete("/api/transcripts")
    assert r.status_code == 200, r.text
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert not blobstore.exists("audio/x.wav") and not blobstore.exists(f"result/{jid}.json")


def test_purge_refuses_while_a_job_is_in_flight(client):
    local.create_job("audio/y.wav", "zh", "all", file_name="y.wav")        # 还在排队
    r = client.delete("/api/transcripts")
    assert r.status_code == 422 and "进行中" in r.json()["detail"]


@pytest.mark.parametrize("method,path", [("post", "/api/auth/request-code"), ("get", "/api/ledger"),
                                          ("post", "/api/topups"), ("get", "/api/projects"),
                                          ("delete", "/api/account"), ("get", "/api/admin/growth")])
def test_removed_routes_are_gone(client, method, path):
    assert getattr(client, method)(path).status_code in (404, 405)


def test_rejects_foreign_host(client):
    r = client.get("/api/me", headers={"host": "attacker.example"})
    assert r.status_code == 403


def test_rejects_cross_site_write_but_allows_local_origin(client):
    assert client.delete("/api/transcripts", headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.delete("/api/transcripts", headers={"origin": "http://localhost:5173"}).status_code == 200   # 没有任务，删除成功
    assert client.get("/api/me", headers={"origin": "https://attacker.example"}).status_code == 200   # 读不带副作用，靠浏览器同源策略

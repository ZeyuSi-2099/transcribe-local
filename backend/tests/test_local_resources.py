"""运行面板「资源」页签的本机版：模型、磁盘、内存、模型后端。本地独有的测试（线上没有这个文件）。"""
import json

from fastapi.testclient import TestClient

import app.api as api
from app import config, local_resources

FAKE_MODELS = {"models": [{"id": "m1", "sizeMb": 100, "installed": True},
                          {"id": "m2", "sizeMb": 50, "installed": False}],
               "ready": False, "missingMb": 50, "cacheDir": "/tmp/models", "download": {}}


def test_snapshot_reports_models_disk_memory_and_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / "a.bin").write_bytes(b"x" * 2 * 1048576)
    monkeypatch.setattr(local_resources.local_models, "status", lambda: FAKE_MODELS)
    s = local_resources.snapshot()
    assert s["models"] == {"ready": False, "items": FAKE_MODELS["models"], "installedMb": 100,
                           "missingMb": 50, "cacheDir": "/tmp/models"}
    assert s["disk"]["path"] == str(tmp_path)
    assert 0 < s["disk"]["freeGb"] <= s["disk"]["totalGb"]
    assert s["disk"]["dataMb"] == 2.0
    assert s["memory"]["parallel"] >= 1 and s["memory"]["parallelWhy"]
    assert s["backend"]["model"]
    assert {f["what"] for f in s["dataFlow"]} >= {"audio", "transcript"}


def test_one_broken_block_does_not_blank_the_page(monkeypatch):
    """模型清单读不出来，磁盘、内存照样给——资源页是出事时去看的地方，不能跟着一起塌。"""
    monkeypatch.setattr(local_resources.local_models, "status", lambda: (_ for _ in ()).throw(OSError("坏了")))
    s = local_resources.snapshot()
    assert s["models"] is None
    assert s["disk"] is not None and s["memory"] is not None


def test_key_value_never_leaves_the_server(monkeypatch):
    """⛔ 密钥只报设没设：值不能出现在接口返回里。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-never-shown-0123456789")
    monkeypatch.setattr(local_resources.local_models, "status", lambda: FAKE_MODELS)
    body = TestClient(api.app).get("/api/admin/local-resources").text
    assert "sk-test-never-shown" not in body
    assert json.loads(body)["backend"] is not None

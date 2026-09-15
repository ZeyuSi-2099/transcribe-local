"""首次启动下载模型：缺哪些、进度怎么报、下完就绪。本地独有的测试（线上没有这个文件）。

下载器换成假的（不联网）：只按清单造出「已装好」的样子。
"""
import time

import pytest
import yaml
from fastapi.testclient import TestClient

import app.api as api
from app import local_models
from transcribe_local import models


@pytest.fixture(autouse=True)
def empty_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_LOCAL_MODELS", str(tmp_path / "models"))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"engines": {"enabled": ["paraformer_2023"]}}), encoding="utf-8")
    monkeypatch.setenv("TRANSCRIBE_CONFIG", str(cfg))
    local_models._state.update(running=False, current=None, doneBytes=0, totalBytes=0, error=None)


def _fake_download(model_id, on_progress=None):
    e = models.manifest()[model_id]
    models.cache_dir().mkdir(parents=True, exist_ok=True)
    if e.archive == "none":
        models.model_path(model_id).write_bytes(b"x")
    else:
        models.model_dir(model_id).mkdir(parents=True)
    if on_progress:
        on_progress(e.size_mb << 20, e.size_mb << 20)
    return models.model_path(model_id)


def _wait_idle():
    for _ in range(100):
        if not local_models._state["running"]:
            return
        time.sleep(0.05)
    pytest.fail("下载线程没有结束")


def test_status_lists_needed_models_and_pull_makes_ready(monkeypatch):
    st = local_models.status()
    assert [m["id"] for m in st["models"]][0] == "paraformer_2023"
    assert st["ready"] is False and st["missingMb"] == sum(m["sizeMb"] for m in st["models"])
    monkeypatch.setattr(models, "download", _fake_download)
    assert local_models.start_pull() is True
    _wait_idle()
    st = local_models.status()
    assert st["ready"] is True and st["missingMb"] == 0 and st["download"]["error"] is None
    assert local_models.start_pull() is False                     # 什么都不缺，不再下


def test_download_error_is_reported_and_can_retry(monkeypatch):
    def boom(model_id, on_progress=None):
        raise RuntimeError("HTTP 503")
    monkeypatch.setattr(models, "download", boom)
    local_models.start_pull()
    _wait_idle()
    err = local_models.status()["download"]["error"]
    assert "HTTP 503" in err
    monkeypatch.setattr(models, "download", _fake_download)
    assert local_models.start_pull() is True                      # 出错之后可以重新点
    _wait_idle()
    assert local_models.status()["ready"] is True


def test_models_api():
    c = TestClient(api.app, base_url="http://127.0.0.1:8765")
    body = c.get("/api/local/models").json()
    assert body["ready"] is False and body["models"]

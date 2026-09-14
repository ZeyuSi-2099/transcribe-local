"""全局 pytest 夹具（本机版）。

与线上不同的地方：
① 线上要防「测试连到生产 Supabase / R2」，本机没有远程可连 —— 改为**每个测试各给一个临时的
   数据库文件和存储文件夹**。连库测试（infra）因此不再需要 docker，也不会互相污染、不碰用户数据。
   非 infra 测试拿到的是一个没建表的空库：线上那些「读不到配置就回落默认值」的路径照样走默认分支。
② 线上只拦单元测试调大模型、放行 infra 测试。本机**一律拦**：本地开发不跑任何付费接口的测试
   （2026-09-14 定）。真要打真实接口，显式设 TRANSCRIBE_ALLOW_LLM=1。
"""
import os

import pytest

from app import config


@pytest.fixture(autouse=True)
def _isolated_storage(request, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "transcribe.db"))
    monkeypatch.setattr(config, "BLOB_DIR", str(tmp_path / "blobs"))
    # 线上的连库测试默认库已由 docker 建好表；本机每个测试是全新的空库，替它们先建表
    if request.node.get_closest_marker("infra") is not None:
        from app import db
        db.init_schema()


@pytest.fixture(autouse=True)
def _testclient_counts_as_local(monkeypatch):
    """线上的接口测试用 TestClient 默认地址（Host: testserver）。本机访问检查只认本机地址，
    测试里把这个名字当本机 —— 只放宽测试，不放宽产品代码。「外来 Host 被拒」由 test_local_api 用别的域名守着。"""
    from app import api
    monkeypatch.setattr(api, "_LOCAL_HOSTNAMES", api._LOCAL_HOSTNAMES | {"testserver"})


@pytest.fixture(autouse=True)
def _no_llm_calls(monkeypatch):
    """测试一律不许出网调大模型。要测降级路径请显式 monkeypatch 被测的调用点。"""
    if os.environ.get("TRANSCRIBE_ALLOW_LLM") == "1":
        return
    from pipeline import pp_deepseek

    def _blocked(*_a, **_kw):
        raise AssertionError(
            "测试试图真的调用 DeepSeek API。要测降级路径请显式 monkeypatch "
            "pp_deepseek.narrate（被测的是 pp_runner 怎么用它，不是 DeepSeek 本身）。"
        )

    monkeypatch.setattr(pp_deepseek, "call", _blocked)

    # 本地识别层的定字：同样一律拦（导入 local_orchestrator 顺带把 src/ 加进 sys.path）
    from pipeline import local_orchestrator  # noqa: F401
    from transcribe_local import fuse

    def _blocked_fuse(*_a, **_kw):
        raise AssertionError("测试试图真的调用定字接口（DeepSeek / 博查）。请 monkeypatch fuse._call 回放编好的产出。")

    monkeypatch.setattr(fuse, "_call", _blocked_fuse)
    monkeypatch.setattr(fuse, "_bocha", _blocked_fuse)

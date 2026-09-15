"""模型后端设置与「数据去哪」。本地独有的测试（线上没有这个文件）。

不调任何接口：只测设置怎么存、怎么读，以及「数据去哪」怎么随设置变。
"""
import pytest
import yaml
from fastapi.testclient import TestClient

import app.api as api
from pipeline import model_backend as mb


@pytest.fixture(autouse=True)
def user_config(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"engines": {"num_threads": 3}}), encoding="utf-8")
    monkeypatch.setenv("TRANSCRIBE_CONFIG", str(p))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    return p


def _flow(what):
    return next((f for f in mb.data_flow() if f["what"] == what), None)


def test_default_is_deepseek_and_text_goes_to_its_host():
    cur = mb.current()
    assert cur["preset"] == "deepseek" and cur["backend"] == "openai"
    assert _flow("audio") == {"what": "audio", "dest": "local", "host": ""}       # 音频永远不出本机
    assert _flow("transcript")["dest"] == "remote" and _flow("transcript")["host"] == "api.deepseek.com"
    assert _flow("glossary")["host"] == "api.deepseek.com" and _flow("postprocess")["host"] == "api.deepseek.com"
    assert _flow("search") is None                                                # 没有博查钥匙就不会发


def test_search_terms_listed_only_when_bocha_key_set(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "x")
    assert _flow("search") == {"what": "search", "dest": "remote", "host": "api.bochaai.com"}
    mb.save("deepseek", web_search=False)
    assert _flow("search") is None


def test_switch_to_local_model_keeps_text_local_and_turns_search_off(user_config, monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "x")
    mb.save("ollama")
    cur = mb.current()
    assert cur["preset"] == "ollama" and cur["web_search"] is False
    assert {f["what"]: f["dest"] for f in mb.data_flow()} == {
        "audio": "local", "transcript": "local", "glossary": "local", "postprocess": "local"}
    url, headers, model, extra = mb.openai_endpoint()
    assert url == "http://127.0.0.1:11434/v1/chat/completions" and headers == {} and model == "qwen3:8b"
    # 配置文件里别的段原样保留
    doc = yaml.safe_load(user_config.read_text(encoding="utf-8"))
    assert doc["engines"] == {"num_threads": 3}


def test_claude_subscription_only_does_fuse():
    mb.save("claude")
    assert _flow("transcript")["dest"] == "claude"
    assert _flow("glossary")["dest"] == "off" and _flow("postprocess")["dest"] == "off"
    with pytest.raises(mb.BackendUnavailable):
        mb.openai_endpoint()


def test_api_backend_needs_key_env_and_never_returns_the_key(monkeypatch):
    with pytest.raises(mb.BackendUnavailable, match="DEEPSEEK_API_KEY"):
        mb.openai_endpoint()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-value")
    url, headers, model, extra = mb.openai_endpoint()
    assert headers["Authorization"] == "Bearer sk-secret-value" and extra == {"reasoning_effort": "high"}
    assert mb.key_status() == {"env": "DEEPSEEK_API_KEY", "set": True}


def test_custom_model_name_and_unknown_preset():
    assert mb.save("ollama", model="qwen3:14b")["model"] == "qwen3:14b"
    with pytest.raises(ValueError):
        mb.save("no-such-preset")


@pytest.mark.infra
def test_settings_api_roundtrip_hides_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-value")
    c = TestClient(api.app, base_url="http://127.0.0.1:8765")
    body = c.get("/api/local/backend").json()
    assert body["current"]["preset"] == "deepseek" and body["key"] == {"env": "DEEPSEEK_API_KEY", "set": True}
    assert "sk-secret-value" not in str(body)
    r = c.put("/api/local/backend", json={"preset": "ollama"})
    assert r.status_code == 200 and r.json()["current"]["preset"] == "ollama"
    assert [f["dest"] for f in r.json()["dataFlow"]] == ["local"] * 4
    assert c.put("/api/local/backend", json={"preset": "nope"}).status_code == 400

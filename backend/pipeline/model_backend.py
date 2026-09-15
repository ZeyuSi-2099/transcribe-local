"""模型后端设置（本地独有）：定字、术语库起草、后处理三处共用这一份。

存在用户配置文件（`local_orchestrator.config_path()`）的 p3 段里——命令行 `run` 与界面读同一份，
改一处两边都生效。线上这三处各自把 DeepSeek 地址写死在代码里，本机版统一收到这里。

⛔ 密钥只从环境变量读：这里只存**变量名**，不存值，也不把值写进任何返回、日志、报错。

「数据去哪」由 `data_flow()` 按当前设置算，界面只负责显示——判断规则只有这一份，
免得界面和后端各写一套、写着写着对不上。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .local_orchestrator import config_path, tl_config

# id 是界面翻译用的键；名字、说明由界面按界面语言写，这里不放给人看的字
PRESETS: list[dict] = [
    {"id": "deepseek", "kind": "api", "backend": "openai",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
     "api_key_env": "DEEPSEEK_API_KEY", "extra": {"reasoning_effort": "high"}},
    {"id": "ollama", "kind": "local", "backend": "openai",
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b", "api_key_env": "", "extra": {}},
    {"id": "lmstudio", "kind": "local", "backend": "openai",
     "base_url": "http://127.0.0.1:11435/v1", "model": "mlx-community/Qwen3-8B-4bit", "api_key_env": "", "extra": {}},
    # Claude 订阅：只有定字走得通（本地 fuse 的 claude_p 路）。术语库起草与后处理要 OpenAI 协议的后端。
    {"id": "claude", "kind": "subscription", "backend": "claude_p",
     "base_url": "", "model": "opus", "api_key_env": "", "extra": {}},
]
_KEYS = ("backend", "base_url", "model", "api_key_env", "extra", "web_search")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
BOCHA_HOST = "api.bochaai.com"


class BackendUnavailable(RuntimeError):
    """当前后端做不了这件事（例如 Claude 订阅做不了术语库起草），或密钥环境变量没设。"""


def _p3() -> dict:
    return tl_config.load(config_path())["p3"]


def is_local(base_url: str) -> bool:
    return (urlparse(base_url or "").hostname or "") in _LOCAL_HOSTS


def current() -> dict:
    p3 = _p3()
    cur = {k: p3.get(k) for k in _KEYS}
    cur["backend"] = (cur["backend"] or "openai").strip()
    cur["extra"] = cur["extra"] or {}
    cur["web_search"] = bool(cur["web_search"])
    # 认出是哪个预设：后端种类 + 地址相同即算（模型名允许改）
    cur["preset"] = next((p["id"] for p in PRESETS
                          if p["backend"] == cur["backend"]
                          and (p["backend"] == "claude_p" or p["base_url"] == cur["base_url"])), "custom")
    return cur


def key_status(cur: dict | None = None) -> dict:
    """密钥变量设没设——只报「设了 / 没设」，不报值。"""
    cur = cur or current()
    env = cur.get("api_key_env") or ""
    return {"env": env, "set": bool(env and os.environ.get(env))}


def save(preset_id: str, *, model: str | None = None, web_search: bool | None = None) -> dict:
    """换成某个预设（可改模型名、开关联网核实），写回用户配置文件；配置里别的段原样保留。"""
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if preset is None:
        raise ValueError(f"没有这个预设：{preset_id}")
    path = Path(config_path())
    doc = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
    p3 = doc.setdefault("p3", {})
    for k in ("backend", "base_url", "model", "api_key_env", "extra"):
        p3[k] = preset[k]
    if model and model.strip():
        p3["model"] = model.strip()
    if web_search is not None:
        p3["web_search"] = bool(web_search)
    # ⚠️ 换到本机模型必须关联网核实：开着的话搜索词仍会发给博查，与「全程本机」相悖
    if preset["kind"] == "local":
        p3["web_search"] = False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return current()


def openai_endpoint() -> tuple[str, dict, str, dict]:
    """术语库起草、后处理用：(chat/completions 地址, 请求头, 模型名, 额外参数)。做不了就抛 BackendUnavailable。"""
    cur = current()
    if cur["backend"] != "openai":
        raise BackendUnavailable("当前模型后端是 Claude 订阅，术语库起草与后处理需要 API 或本机模型")
    env = cur["api_key_env"] or ""
    key = os.environ.get(env, "") if env else ""
    if env and not key and not is_local(cur["base_url"]):
        raise BackendUnavailable(f"环境变量 {env} 没设")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return cur["base_url"].rstrip("/") + "/chat/completions", headers, cur["model"], dict(cur["extra"])


def _dest(cur: dict) -> dict:
    if cur["backend"] == "claude_p":
        return {"dest": "claude", "host": "Anthropic"}
    if is_local(cur["base_url"]):
        return {"dest": "local", "host": ""}
    return {"dest": "remote", "host": urlparse(cur["base_url"]).hostname or cur["base_url"]}


def data_flow() -> list[dict]:
    """按当前设置，列出每样东西去哪。dest：local 不出本机 / remote 发给 host / claude 发给 Anthropic / off 这一步用不了。"""
    cur = current()
    text = _dest(cur)
    side = {"dest": "off", "host": ""} if cur["backend"] == "claude_p" else text
    flow = [
        {"what": "audio", "dest": "local", "host": ""},          # 识别全在本机：音频永远不出这台电脑
        {"what": "transcript", **text},                          # 定字：稿子文字
        {"what": "glossary", **side},                            # 术语库起草与检查：大纲与词条
        {"what": "postprocess", **side},                         # 后处理：稿子文字
    ]
    # 联网核实：只有 API 那条定字路会用；开着且真设了钥匙才会发出去
    if cur["backend"] == "openai" and cur["web_search"] and os.environ.get("BOCHA_API_KEY"):
        flow.append({"what": "search", "dest": "remote", "host": BOCHA_HOST})
    return flow


def probe(timeout: float = 60) -> dict:
    """真发一次最小请求，看这个后端此刻能不能用。Claude 订阅只查命令在不在（真跑一次要几十秒并消耗额度）。"""
    cur = current()
    if cur["backend"] == "claude_p":
        ok = shutil.which("claude") is not None
        return {"ok": ok, "why": "" if ok else "这台电脑上找不到 claude 命令"}
    try:
        url, headers, model, extra = openai_endpoint()
    except BackendUnavailable as e:
        return {"ok": False, "why": str(e)}
    try:
        import httpx
        payload = {"model": model, "temperature": 0, "max_tokens": 8,
                   "messages": [{"role": "user", "content": "回复 ok 两个字"}], **extra}
        r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return {"ok": False, "why": f"HTTP {r.status_code}"}
        return {"ok": True, "why": ""}
    except Exception as e:  # noqa: BLE001  探活失败只报类型，不带响应正文（可能夹着请求头回显）
        # why 只写原因本身，「连不上：」由界面按界面语言加
        return {"ok": False, "why": type(e).__name__}

# -*- coding: utf-8 -*-
"""本机服务：界面在浏览器里开，识别始终在这个 Python 进程里跑。

⚠️ 这不是「网页版转录」。模型 2.8 GB、长音频要自回归解码，浏览器里跑不动 ——
浏览器只是界面，音频和权重一步都不出这台机器。

只用标准库。项目最好的工程运气是「一行 pip install、四个依赖」，
为了一个本机单人用的界面去加 FastAPI + uvicorn，把那句话变贵了不值得。
"""
from __future__ import annotations

import json
import mimetypes
import queue
import shutil
import socket
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import paths

WEB = paths.root() / "web"
MAXBODY = 2 * 1024 * 1024 * 1024          # 2 G，够长录音了；再大让人先转码


class Job:
    """一次转录。事件排进队列，界面用 SSE 拉走。"""

    def __init__(self, jid: str, audio: Path, out: Path, cfg: dict, no_fuse: bool):
        self.id, self.audio, self.out, self.cfg, self.no_fuse = jid, audio, out, cfg, no_fuse
        self.events: list[dict] = []
        self.subs: list[queue.Queue] = []                       # 每个订阅者一条队列，支持多开标签页
        self.lock = threading.Lock()
        self.state = "running"
        self.result: dict | None = None
        self.error: str | None = None

    def subscribe(self) -> queue.Queue:
        """补历史 + 接着听。历史和注册必须在同一把锁里做 ——
        否则这中间来的事件要么漏掉、要么发两遍（实测就是发了两遍）。"""
        q: queue.Queue = queue.Queue()
        with self.lock:
            for ev in self.events:
                q.put(ev)
            if self.state == "running":
                self.subs.append(q)
            else:
                q.put(None)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def _emit(self, kind: str, **kw) -> None:
        ev = dict(kind=kind, **kw)
        with self.lock:
            self.events.append(ev)
            for q in self.subs:
                q.put(ev)

    def start(self) -> None:
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        from . import pipeline
        try:
            r = pipeline.run(self.audio, self.out / self.id, self.cfg,
                             no_fuse=self.no_fuse, emit=self._emit)
            self.result = dict(
                stem=r.stem, minutes=round(r.minutes, 1), blocks=r.blocks, segments=r.segments,
                engines=r.engines, qc=r.qc, substantive=r.substantive, fillers=r.fillers,
                uncertain=r.uncertain, seconds=round(r.seconds), merged=r.merged, ledger=r.ledger,
                exports=[p.name for p in r.exports],
            )
            self.state = "done"
        except Exception as e:                                  # 失败也要让界面看见，不许静默
            self.error = f"{type(e).__name__}: {e}"
            self.state = "failed"
            self._emit("error", message=self.error, trace=traceback.format_exc()[-2000:])
        finally:
            with self.lock:
                for q in self.subs:
                    q.put(None)
                self.subs.clear()


JOBS: dict[str, Job] = {}


class Handler(BaseHTTPRequestHandler):
    server_version = "transcribe-local"
    cfg: dict = {}
    out: Path = Path("out")
    cfg_path: Path = Path("config.yaml")

    def log_message(self, *a):                                  # 默认那套访问日志太吵
        pass

    # ── 回包小工具 ──
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):         # 用户关了标签页，正常
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    # ── 路由 ──
    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path.startswith("/api/"):
            return self._api_get(u.path[5:], q)
        return self._static(u.path)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAXBODY:
            return self._json({"error": "文件太大"}, 413)
        body = self.rfile.read(n) if n else b""
        try:
            return self._api_post(u.path[5:], q, body)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _static(self, path: str) -> None:
        rel = unquote(path.lstrip("/")) or "index.html"
        f = (WEB / rel).resolve()
        if not str(f).startswith(str(WEB.resolve())) or not f.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def _api_get(self, route: str, q: dict) -> None:
        from . import audio, config, mem, models

        if route == "state":
            cfg = self.cfg
            need = _needed(cfg)
            mf = models.manifest()
            return self._json({
                "engines": [{"id": e, "tag": _tag(e), "route": _route(e)} for e in cfg["engines"]["enabled"]],
                "models": [{"id": m, "installed": models.installed(m),
                            "size_mb": mf[m].size_mb if m in mf else 0} for m in need],
                "ffmpeg": audio.have_ffmpeg(),
                "p3": {"model": cfg["p3"]["model"], "base_url": cfg["p3"]["base_url"],
                       "reachable": _reachable(cfg["p3"]["base_url"]),
                       "key_env": cfg["p3"].get("api_key_env", ""),
                       "local": cfg["p3"]["base_url"].startswith(("http://127.0.0.1", "http://localhost"))},
                "terms": cfg["terms"].get("files") or [],
                "mem": dict(zip(("parallel", "why"), mem.plan(cfg, cfg["engines"]["enabled"])),
                            total_mb=mem.total_mb(), available_mb=mem.available_mb(),
                            setting=cfg["engines"].get("parallel", "auto")),
                "jobs": [{"id": j.id, "state": j.state, "stem": j.audio.stem} for j in JOBS.values()],
            })

        if route == "p3/presets":
            return self._json({"presets": P3_PRESETS, "current": self.cfg["p3"],
                               "config_path": str(self.cfg_path)})

        if route == "p3/test":
            return self._json(_probe(self.cfg["p3"]))

        if route == "config":
            return self._json({"text": config.DEFAULT_PATH.read_text(encoding="utf-8"),
                               "provenance": config.PROVENANCE})

        if route.startswith("job/"):
            j = JOBS.get(route[4:])
            if not j:
                return self._json({"error": "没有这个任务"}, 404)
            return self._json({"id": j.id, "state": j.state, "error": j.error,
                               "events": j.events, "result": j.result})

        if route == "events":
            return self._sse(q.get("job", [""])[0])

        return self._json({"error": "没有这个接口"}, 404)

    def _sse(self, jid: str) -> None:
        j = JOBS.get(jid)
        if not j:
            return self._json({"error": "没有这个任务"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = j.subscribe()
        try:
            while True:
                ev = q.get()
                if ev is None:
                    self._push({"kind": "closed", "state": j.state})
                    return
                if not self._push(ev):
                    return
        finally:
            j.unsubscribe(q)

    def _push(self, ev: dict) -> bool:
        try:
            self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _api_post(self, route: str, q: dict, body: bytes) -> None:
        if route == "upload":
            name = q.get("name", ["audio"])[0]
            dst = self.out / "uploads" / Path(name).name           # 只取文件名，不认路径
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
            return self._json({"path": str(dst), "bytes": len(body)})

        if route == "run":
            req = json.loads(body or b"{}")
            src = Path(req["path"]).expanduser()
            if not src.is_file():
                return self._json({"error": f"找不到文件：{src}"}, 400)
            cfg = json.loads(json.dumps(self.cfg))                 # 每个任务一份快照
            if req.get("engines"):
                cfg["engines"]["enabled"] = req["engines"]
            if req.get("terms") is not None:
                cfg["terms"]["files"] = req["terms"]
            jid = uuid.uuid4().hex[:8]
            j = Job(jid, src, self.out, cfg, bool(req.get("no_fuse")))
            JOBS[jid] = j
            j.start()
            return self._json({"job": jid})

        if route == "p3":
            req = json.loads(body or b"{}")
            p3 = self.cfg["p3"]
            for k in ("base_url", "model", "api_key_env"):
                if k in req:
                    p3[k] = req[k]
            # ⚠️ 换后端必须连 extra 一起换：reasoning_effort 是 DeepSeek 专属，
            #    原样带去本机模型那边多半直接 400。这正是手改配置最容易漏的一步。
            p3["extra"] = req.get("extra", {})
            _save_p3(self.cfg_path, p3)
            return self._json({"ok": True, "p3": p3, "saved_to": str(self.cfg_path),
                               "probe": _probe(p3)})

        if route == "terms":
            req = json.loads(body or b"{}")
            p = Path(req["path"]).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(req["text"], encoding="utf-8")
            return self._json({"ok": True, "path": str(p)})

        return self._json({"error": "没有这个接口"}, 404)


def _tag(e: str) -> str:
    from .engines import TAG
    return TAG.get(e, e)


def _route(e: str) -> str:
    from .engines import ROUTE
    return ROUTE.get(e, "")


def _needed(cfg: dict) -> list[str]:
    need = list(cfg["engines"]["enabled"])
    need += [cfg["diarize"]["segmentation"], cfg["diarize"]["embedding"]]
    if cfg["vad"]["enabled"]:
        need.append(cfg["vad"]["model"])
    return need


P3_PRESETS = [
    {"id": "deepseek-flash", "name": "DeepSeek Flash", "kind": "api",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
     "api_key_env": "DEEPSEEK_API_KEY", "extra": {"reasoning_effort": "high"},
     "note": "默认。我们自己的云端生产线用的就是这一档，一份 38 分钟访谈几毛钱"},
    {"id": "deepseek-pro", "name": "DeepSeek V4 Pro", "kind": "api",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-pro",
     "api_key_env": "DEEPSEEK_API_KEY", "extra": {"reasoning_effort": "high"},
     "note": "更贵一档，但我们实测它更差：该拍板时照抄主引擎"},
    {"id": "ollama", "name": "本机 Ollama", "kind": "local",
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b",
     "api_key_env": "", "extra": {},
     "note": "全程离线。先 ollama serve + ollama pull qwen3:8b"},
    {"id": "mlx", "name": "本机 mlx_lm / LM Studio", "kind": "local",
     "base_url": "http://127.0.0.1:11435/v1", "model": "mlx-community/Qwen3-8B-4bit",
     "api_key_env": "", "extra": {},
     "note": "全程离线。Qwen3 系记得启动时关思考模式"},
]


def _save_p3(path: Path, p3: dict) -> None:
    """把 p3 的三件套 + extra 写回用户配置，其余键原样保留。"""
    import yaml
    cur = {}
    if path.exists():
        cur = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cur.setdefault("p3", {})
    for k in ("base_url", "model", "api_key_env", "extra"):
        cur["p3"][k] = p3.get(k)
    path.write_text(yaml.safe_dump(cur, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _probe(p3: dict) -> dict:
    """试一下这个后端到底能不能用 —— 不光看端口通不通，真发一次最小请求。"""
    import os as _os
    local = p3["base_url"].startswith(("http://127.0.0.1", "http://localhost"))
    env = p3.get("api_key_env") or ""
    key = _os.environ.get(env, "") if env else ""
    if not local and env and not key:
        return {"ok": False, "why": f"环境变量 {env} 没设 —— 密钥只从环境变量读，不会写进配置文件"}
    try:
        import httpx
        payload = {"model": p3["model"], "temperature": 0, "max_tokens": 8,
                   "messages": [{"role": "user", "content": "回复 ok 两个字"}]}
        payload.update(p3.get("extra") or {})
        r = httpx.post(p3["base_url"].rstrip("/") + "/chat/completions", json=payload,
                       headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=60)
        if r.status_code != 200:
            return {"ok": False, "why": f"HTTP {r.status_code}：{r.text[:160]}"}
        return {"ok": True, "why": "通了，能正常回话"}
    except Exception as e:
        return {"ok": False, "why": f"{type(e).__name__}：{str(e)[:120]}"}


def _reachable(base_url: str) -> bool:
    try:
        import httpx
        httpx.get(base_url.rstrip("/") + "/models", timeout=2)
        return True
    except Exception:
        return False


def serve(cfg: dict, out: Path, port: int = 0, open_browser: bool = True,
          cfg_path: Path | None = None) -> int:
    if not WEB.is_dir():
        print(f"找不到界面文件（{WEB}）。从源码跑的话确认仓库完整。")
        return 2
    Handler.cfg, Handler.out = cfg, out
    Handler.cfg_path = Path(cfg_path or "config.yaml")
    out.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)      # ⛔ 只听本机，不对局域网开放
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"界面开在 {url}")
    print("识别在本机这个进程里跑，音频和模型一步都不出这台机器。按 Ctrl-C 停。")
    if not shutil.which("ffmpeg"):
        print("⚠️ 没装 ffmpeg —— 只能喂 16 kHz 单声道 .wav")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停了。")
    finally:
        httpd.server_close()
    return 0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

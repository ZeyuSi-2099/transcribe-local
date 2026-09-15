"""首次启动下载模型（本地独有）。

要下哪些、下到哪、每个多大，全按识别层自己的清单（models/manifest.toml）和用户配置里启用的引擎算，
下载本身也用识别层的下载器（分块续传、校验和、解压到临时目录再改名）——这里只管在后台跑、报进度。
同一时间只跑一轮下载；进程重启后进度从头报，但已经下完的模型不会重下，下了一半的会接着下。
"""
from __future__ import annotations

import threading

from pipeline.local_orchestrator import config_path, tl_config
from transcribe_local import models

_lock = threading.Lock()
_state: dict = {"running": False, "current": None, "doneBytes": 0, "totalBytes": 0, "error": None}


def needed(cfg: dict) -> list[str]:
    """启用的引擎 + 声纹分段 + 声纹嵌入 + VAD。"""
    need = list(cfg["engines"]["enabled"])
    need += [cfg["diarize"]["segmentation"], cfg["diarize"]["embedding"]]
    if cfg["vad"]["enabled"]:
        need.append(cfg["vad"]["model"])
    return need


def status() -> dict:
    cfg = tl_config.load(config_path())
    mf = models.manifest()
    items = [{"id": m, "sizeMb": mf[m].size_mb, "installed": models.installed(m)} for m in needed(cfg) if m in mf]
    return {
        "models": items,
        "ready": all(i["installed"] for i in items),
        "missingMb": sum(i["sizeMb"] for i in items if not i["installed"]),
        "cacheDir": str(models.cache_dir()),
        "download": dict(_state),
    }


def start_pull() -> bool:
    """开始下缺的模型。已经在下、或者什么都不缺，返回 False。"""
    with _lock:
        if _state["running"]:
            return False
        miss, total_mb = models.plan(needed(tl_config.load(config_path())))
        if not miss:
            return False
        _state.update(running=True, current=None, doneBytes=0, totalBytes=total_mb << 20, error=None)
    threading.Thread(target=_pull, args=(miss,), daemon=True, name="models-pull").start()
    return True


def _pull(miss) -> None:
    done = 0
    try:
        for e in miss:
            _state["current"] = e.id

            def progress(got: int, _total: int, base: int = done) -> None:
                _state["doneBytes"] = base + got

            models.download(e.id, on_progress=progress)
            done += e.size_mb << 20
            _state["doneBytes"] = done
    except Exception as ex:  # noqa: BLE001  报给界面，让人能点「重试」；已下完的不会丢
        _state["error"] = f"{_state['current']}：{str(ex)[:240]}"
    finally:
        _state.update(running=False, current=None)

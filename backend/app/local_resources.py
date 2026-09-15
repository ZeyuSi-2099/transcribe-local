"""运行面板「资源」页签的本机版（本地独有）：模型、磁盘、内存、模型后端与数据去向。

线上这一页看的是云供应商余额、证书到期、云机器容量——那些本机都没有。本机要回答的是
「这台电脑现在跑不跑得动、东西存在哪、定字发给了谁」，所以看这几样。

每一块独立取，取不到那一块给 None，不让整页打不开（同 health_view）。
⛔ 密钥只报「设没设」，不报值。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import config, local_models


def _dir_mb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1048576


def _models() -> dict | None:
    try:
        s = local_models.status()
    except Exception:  # noqa: BLE001
        return None
    return {
        "ready": s["ready"],
        "items": s["models"],
        "installedMb": sum(m["sizeMb"] for m in s["models"] if m["installed"]),
        "missingMb": s["missingMb"],
        "cacheDir": s["cacheDir"],
    }


def _disk() -> dict | None:
    data = Path(config.DATA_DIR)
    try:
        probe = data if data.exists() else data.parent
        usage = shutil.disk_usage(probe)
    except OSError:
        return None
    return {
        "path": str(data),
        "freeGb": round(usage.free / 1024 ** 3, 1),
        "totalGb": round(usage.total / 1024 ** 3, 1),
        "dataMb": round(_dir_mb(data), 1) if data.exists() else 0.0,
    }


def _memory() -> dict | None:
    try:
        from pipeline.local_orchestrator import config_path, tl_config
        from transcribe_local import mem

        cfg = tl_config.load(config_path())
        parallel, why = mem.plan(cfg, list(cfg["engines"]["enabled"]))
        return {"totalMb": mem.total_mb(), "availableMb": mem.available_mb(), "reserveMb": mem.RESERVE_MB,
                "parallel": parallel, "parallelWhy": why}
    except Exception:  # noqa: BLE001
        return None


def snapshot() -> dict:
    from pipeline import model_backend

    from . import workflow_view

    try:
        backend, flow = workflow_view._backend(), model_backend.data_flow()
    except Exception:  # noqa: BLE001
        backend, flow = None, []
    return {"models": _models(), "disk": _disk(), "memory": _memory(), "backend": backend, "dataFlow": flow}

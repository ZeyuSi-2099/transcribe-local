# -*- coding: utf-8 -*-
"""配置：默认值 + 用户覆盖，外加「这个默认值从哪来的」。

来源标注写在 config.default.yaml 的注释里，供人读；这里只解析出机器要用的值，
再把注释里的方括号标记抽出来供 `config --explain` 显示。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import paths

DEFAULT_PATH = paths.root() / "config.default.yaml"

PROVENANCE = {
    "定档": "有全长实测支撑",
    "未验证": "照搬上游或凭常识定的，没有实测证据",
    "有更好的": "已经量出更好的做法，但还没落地",
    "缺陷": "已知有问题，尚未修",
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(user_path: str | Path | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))
    if user_path:
        p = Path(user_path)
        if p.exists():
            cfg = _deep_merge(cfg, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    return cfg


def explain(key: str) -> list[tuple[str, str, str]]:
    """从默认配置的注释里抽出某个键的来源标注与理由。

    返回 [(行, 标记, 说明), ...]。键用点号路径的末段匹配，例如 `chop.strategy` 或 `strategy`。
    """
    leaf = key.rsplit(".", 1)[-1]
    lines = DEFAULT_PATH.read_text(encoding="utf-8").splitlines()
    hits: list[tuple[str, str, str]] = []
    for i, ln in enumerate(lines):
        if not re.match(rf"^\s*{re.escape(leaf)}\s*:", ln):
            continue
        block = [ln]
        for nxt in lines[i + 1:]:                       # 续行的注释也算这个键的说明
            if nxt.strip().startswith("#") and nxt.startswith(" " * 20):
                block.append(nxt)
            elif nxt.strip().startswith("#") and not nxt.strip("# \t"):
                continue
            else:
                break
        text = "\n".join(block)
        mark = next((m for m in PROVENANCE if f"[{m}" in text), "")
        hits.append((ln.strip(), mark, text))
    return hits

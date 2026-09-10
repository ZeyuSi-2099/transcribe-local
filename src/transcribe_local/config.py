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


STARTER = """# Transcribe Local · 你自己的配置
#
# 只写你想改的键，其余自动用 config.default.yaml 的默认值。
# 每个默认值「从哪来的」：transcribe-local config --explain <键>

engines:
  enabled:                       # 想少跑几路就删几行（少一路 = 快，但融合的票少一张）
{engines}
p3:
  base_url: {base_url}           # 换后端只改这两行；说 OpenAI 协议的都能接
  model: {model}
  # api_key_env: TRANSCRIBE_LOCAL_API_KEY   # 接云端 API 时打开，密钥只从环境变量读

terms:
  files: []                      # 术语库路径，例如 ["terms/zh_我的行业.md"]
                                 # 实测：挂对了能突破四路投票的天花板；写错的词形会被模型当权威照抄
"""


def starter(cfg: dict) -> str:
    """生成一份用户配置模板 —— 只放常改的那几个键，不把 160 行默认值抄一遍。"""
    return STARTER.format(
        engines="".join(f"    - {e}\n" for e in cfg["engines"]["enabled"]),
        base_url=cfg["p3"]["base_url"], model=cfg["p3"]["model"],
    )

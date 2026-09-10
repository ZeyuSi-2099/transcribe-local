# -*- coding: utf-8 -*-
"""仓库根目录的定位。

开发时从源码树跑，配置 / 提示词 / 模型清单都在仓库根；装成 wheel 之后它们不在包旁边，
所以往上找一个标记文件，找不到就退回当前目录。
"""
from __future__ import annotations

from pathlib import Path

MARKER = "config.default.yaml"


def root() -> Path:
    for p in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (p / MARKER).exists():
            return p
    return Path.cwd()

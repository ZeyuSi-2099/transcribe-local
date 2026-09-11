# -*- coding: utf-8 -*-
"""稿子的一行长什么样，只在这里定义一次。

融合、导出、判分三处以前各写各的正则，遇到模型按提示词写出的 `M[❓]:`（说话人存疑）
只是碰巧能过。这里统一：

    [起始 - 结束] 说话人: 文本

说话人可以带 `[❓]`（模型按内容口吻判断标签可疑），文本里也可以带 `[❓]`（某几个字定不下来）。
两种存疑都保留，导出时是否剥掉进复核卡片，由导出层决定，不在这里做。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MARK = "[❓]"

# 时间码宽松：分钟可以超过 59，秒可带小数。说话人段不含冒号（半角或全角）。
LINE = re.compile(
    r"^\s*\[(?P<a>\d{1,3}:\d{2}(?:\.\d+)?)\s*-\s*(?P<b>\d{1,3}:\d{2}(?:\.\d+)?)\]"
    r"\s*(?P<spk>[^:：\]]*(?:\[❓\])?)\s*[:：]\s*(?P<text>.*?)\s*$"
)


@dataclass(frozen=True)
class Line:
    a: str
    b: str
    spk: str            # 原样，可能是 "M" / "R" / "SPK0" / "M[❓]"
    text: str

    @property
    def spk_base(self) -> str:
        return self.spk.replace(MARK, "").strip()

    @property
    def spk_uncertain(self) -> bool:
        return MARK in self.spk

    @property
    def head(self) -> str:
        """时间码 + 说话人 + 冒号，用来在保留前缀的前提下换掉正文。"""
        return f"[{self.a} - {self.b}] {self.spk}: "


def parse_line(s: str) -> Line | None:
    m = LINE.match(s)
    if not m:
        return None
    return Line(m["a"], m["b"], m["spk"].strip(), m["text"])


def has_words(text: str) -> bool:
    """去掉 [❓] 和标点之后还剩不剩字。不剩 = 这块话丢了，不是标存疑。"""
    return bool(re.search(r"[0-9A-Za-z一-鿿]", text.replace(MARK, "")))

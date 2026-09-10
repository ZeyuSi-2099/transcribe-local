# -*- coding: utf-8 -*-
"""分歧册 —— 替代多路对齐的那一步。

没有 Phase 2：四路吃同一份切块 + 同一份声纹分段，第 N 块在四路里是同一段音频，
对齐是恒等的。云端那套模糊对齐在这里无事可做 —— 顺带也没有它「裁掉读音不近的证据」
那个已知损耗，四路的候选一个都不会丢。

这里做两件事：
  1. 出融合输入：按块堆叠各路写法。
  2. 出分歧册：用 difflib 穷举定位各路写法不一致的**片段**（不是整块）。

设计依据：把「找分歧」这件确定性的事交给代码穷举，模型只负责「定字」。
上游做过对照实验，结论是**全量点名有益、做减法有害** —— 所以这里只折叠语气词，不做别的筛选。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .engines import TAG, Row, ts

PUNCT = "，。？！、：；…—–·.,?!;:\"'“”‘’()（）《》〈〉「」【】~～-_ \t　"


def detok(s: str) -> str:
    """剥掉模型吐出来的标记（<sil> 之类）。"""
    return re.sub(r"<[a-z/|]{1,12}>", "", s)


def norm(s: str) -> str:
    return "".join(c for c in detok(s) if c not in PUNCT)


def bone(x: str, fillers: str) -> str:
    """剥掉语气词、折叠连续重复字 —— 剩下的才是「实质」。"""
    out: list[str] = []
    for c in x:
        if c in fillers:
            continue
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


@dataclass
class Divergence:
    block: int
    at: float
    role: str
    substantive: list[list[tuple[str, str]]]     # 每处一组 [(引擎, 写法), ...]
    filler_count: int


def assign_roles(rows_by_engine: dict[str, list[Row]], mode: str) -> dict[int | None, str]:
    """说话人编号 → M / R。这一层来自共用的声纹分段，不属于任何一路引擎。"""
    speakers = {r.speaker for r in next(iter(rows_by_engine.values()))}
    speakers.discard(None)
    if mode != "talk_time" or len(speakers) != 2:
        return {s: f"SPK{s}" for s in speakers}
    rows = next(iter(rows_by_engine.values()))
    talk = {s: sum(r.end - r.start for r in rows if r.speaker == s) for s in speakers}
    host, guest = sorted(talk, key=lambda s: talk[s])        # 说得少的是提问方
    return {host: "M", guest: "R"}


def _idxmap(pivot: str, other: str) -> dict[int, int]:
    """pivot 字符位 → other 字符位（只取 equal 段）。"""
    mp: dict[int, int] = {}
    for op, i1, i2, j1, _ in difflib.SequenceMatcher(None, pivot, other, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                mp[i1 + k] = j1 + k
    return mp


def _spans(pivot: str, others: list[str]) -> list[tuple[int, int]]:
    """各路与 pivot 不一致的 pivot 区间，合并重叠后返回。"""
    rs: list[list[int]] = []
    for o in others:
        for op, i1, i2, _, _ in difflib.SequenceMatcher(None, pivot, o, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            a, b = i1, i2
            if a == b:                                      # 纯插入，左右各借一字才看得出位置
                a, b = max(0, a - 1), min(len(pivot), b + 1)
            rs.append([a, b])
    rs.sort()
    out: list[list[int]] = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _cut(text: str, mp: dict[int, int], a: int, b: int) -> str:
    """按 pivot 区间 [a, b) 在另一路上取对应片段。"""
    lo = next((mp[p] for p in range(a, b) if p in mp), None)
    if lo is None:                                          # 整段都对不上，用左右最近的锚点夹出来
        prev = [mp[p] for p in range(a - 1, -1, -1) if p in mp]
        nxt = [mp[p] for p in range(b, len(mp) + b) if p in mp]
        lo = prev[0] + 1 if prev else 0
        hi = (nxt[0] - 1) if nxt else len(text) - 1
    else:
        hi = max(mp[p] for p in range(a, b) if p in mp)
    return text[max(0, lo):hi + 1]


def build(rows_by_engine: dict[str, list[Row]], cfg: dict) -> tuple[str, str, list[Divergence]]:
    """返回（融合输入, 分歧册, 分歧列表）。"""
    dv = cfg["divergence"]
    fillers = dv["fillers"] if dv.get("fold_fillers") else ""
    strip = dv.get("strip_tags", True)
    ids = list(rows_by_engine)
    roles = assign_roles(rows_by_engine, cfg["diarize"].get("role_assign", "talk_time"))

    by_key: dict[str, dict[tuple[float, float], Row]] = {
        e: {(round(r.start, 1), round(r.end, 1)): r for r in rows} for e, rows in rows_by_engine.items()
    }
    keys = sorted(by_key[ids[0]])

    body: list[str] = []
    ledger: list[str] = []
    found: list[Divergence] = []
    n_sub = n_fill = 0

    for i, k in enumerate(keys, 1):
        role = roles.get(by_key[ids[0]][k].speaker, "X")
        texts = {e: (by_key[e].get(k).text if by_key[e].get(k) else "") for e in ids}
        if strip:
            texts = {e: detok(t) for e, t in texts.items()}

        body.append(f"[{ts(k[0])} - {ts(k[1])}] {role}: {texts[ids[0]]}")
        for e in ids[1:]:
            body.append(" " * 18 + f"[{TAG.get(e, e)}] {texts[e] or '/'}")
        body.append("")

        pivot = norm(texts[ids[0]])
        others = {e: norm(texts[e]) for e in ids[1:]}
        if not pivot and not any(others.values()):
            continue
        sp = _spans(pivot, list(others.values()))
        if not sp:
            continue
        mps = {e: _idxmap(pivot, others[e]) for e in ids[1:]}

        sub: list[list[tuple[str, str]]] = []
        fill = 0
        for a, b in sp:
            raw = [pivot[a:b]] + [_cut(others[e], mps[e], a, b) for e in ids[1:]]
            if len(set(raw)) == 1:
                continue
            if len({bone(v, fillers) for v in raw}) == 1:    # 剥掉语气词后骨架相同 = 不影响意思
                fill += 1
            else:
                sub.append([(TAG.get(e, e), v) for e, v in zip(ids, raw)])

        if not sub and not fill:
            continue
        n_sub += len(sub)
        n_fill += fill
        found.append(Divergence(i, k[0], role, sub, fill))

        seg = [f"#{i} [{ts(k[0])}] {role}"]
        seg += ["  · " + " ｜ ".join(f"{t}={v or '∅'}" for t, v in grp) for grp in sub]
        if fill:
            seg.append(f"  （语气词差异 {fill} 处，不影响意思，可略过）")
        ledger.append("\n".join(seg))

    head = (
        f"# 分歧册 · {len(keys)} 块 · 实质分歧 {n_sub} 处"
        f"（另有语气词差异 {n_fill} 处，已折叠）· 引擎 {' '.join(TAG.get(e, e) for e in ids)}\n"
        "各路写法不一致的片段，逐处列出。格式：引擎=写法 ｜ 引擎=写法（∅ = 这一路该处没有对应内容）\n"
    )
    return "\n".join(body), head + "\n" + "\n\n".join(ledger), found

# -*- coding: utf-8 -*-
"""按换人拆块 —— 给引擎的块照旧切长，一块里每个字归谁按声纹分段逐字贴。

要解决的问题：切块按静音和长度切，一块 18 秒里常常主持人问、受访者答都在；按块贴一个标签，
混进来的那个人整句记错（基线全长说话人 84.4%，34:34 前后 11 块只有 26%）。
在换人的地方下刀（chop.strategy: turns）能把说话人拉到 97%，但块切碎了引擎丢上下文、
四路全错 13 → 21，已否决。所以不改刀法，改「文字归谁」这一步。

做法（Duner 2026-09-14 定「路 A」）：
  1. Paraformer 解码时顺带报每个字的时间（engines.STAMPED）。四台里 Zipformer-CTC 也报，
     但它的 token 是字节级 BPE，对不回单字；FireRed / Qwen3 不报。所以只用 Paraformer 当基准。
  2. 每个字按它的时间落进声纹分段的哪一段，就贴那一段的人；连续同一人的字成一个子块。
  3. 另外三路与 Paraformer 逐字对齐（与分歧册同一个 _idxmap），切点照搬过去。
  4. 子块时间码 = 切点前后两个字时间的中点。

纪律：
  · 内容不能丢。每一路拆开后首尾相接必须与原文逐字相同，否则这一块不拆并喊一声。
  · 不重新识别，只改「这几个字归谁」。
  · 这一块 Paraformer 没出字（静音 / 漏识） → 不拆，保持原样。

实测（2026-09-14，基线录音全长，tools/bench.py --control 的说话人一列；四路内容成绩不变）：
  18 s 84.4% → 98.4% · 24 s 83.6% → 97.8% · 30 s 80.2% → 97.8%
"""
from __future__ import annotations

import re

from .divergence import PUNCT, _idxmap
from .engines import Row

PIVOT = "paraformer_2023"
MARKUP = re.compile(r"<[a-z/|]{1,12}>")           # 与 divergence.detok 同一条


def _norm_index(raw: str) -> tuple[str, list[int]]:
    """归一文本（剥标记、去标点、小写）+ 每个归一字在原文里的位置。"""
    keep, idx, i = [], [], 0
    while i < len(raw):
        m = MARKUP.match(raw, i)
        if m:
            i = m.end()
            continue
        if raw[i] not in PUNCT:
            keep.append(raw[i].lower())
            idx.append(i)
        i += 1
    return "".join(keep), idx


def speaker_at(t: float, segs, near: float = 1.0) -> int | None:
    """t 秒是谁在说。落在几段重叠处取时长最长的那段；不在任何段里就取 near 秒内最近的段。"""
    hit = [g for g in segs if g.start <= t <= g.end]
    if hit:
        return max(hit, key=lambda g: g.end - g.start).speaker
    best, d = None, near
    for g in segs:
        dd = min(abs(t - g.start), abs(t - g.end))
        if dd <= d:
            best, d = g.speaker, dd
    return best


def _cut_positions(pivot: str, other: str, cuts: list[int]) -> list[int]:
    """pivot 上的切点（归一字位）→ other 上的切点。对不上的切点找左边最近的锚点。"""
    mp = _idxmap(pivot, other)
    out: list[int] = []
    for c in cuts:
        if c in mp:
            q = mp[c]
        else:
            left = next((mp[k] + 1 for k in range(c - 1, -1, -1) if k in mp), None)
            right = next((mp[k] for k in range(c + 1, len(pivot)) if k in mp), None)
            q = left if left is not None else (right if right is not None else round(c / max(1, len(pivot)) * len(other)))
        out.append(min(len(other), max(q, out[-1] if out else 0)))
    return out


def _split_block(rows: dict[str, Row], segs, near: float) -> list[dict[str, Row]] | None:
    """一块 → 若干子块（每个子块是各路的 Row）。不需要拆或不能拆返回 None。"""
    p = rows[PIVOT]
    chars, times = [], []
    for tok, t in p.stamps:
        if MARKUP.fullmatch(tok):
            continue
        for c in tok.replace("@@", "").lower():
            if c not in PUNCT:
                chars.append(c)
                times.append(t)
    if not chars:
        return None

    who, last = [], p.speaker
    for t in times:
        w = speaker_at(t, segs, near)
        last = last if w is None else w                  # 贴不到的字沿用前一个字的人
        who.append(last)
    runs = [[0, who[0]]]
    for i in range(1, len(who)):
        if who[i] != runs[-1][1]:
            runs.append([i, who[i]])

    a, b = p.start, p.end
    bounds = [a]
    kept = [runs[0]]
    for i, spk in runs[1:]:
        mid = round((times[i - 1] + times[i]) / 2, 2)
        # 时间码按 0.1 秒显示、也按 0.1 秒当键 —— 太挤的切点会撞键，撞了就不在这里切
        if round(bounds[-1], 1) < round(mid, 1) < round(b, 1):
            bounds.append(mid)
            kept.append([i, spk])
        # 不切的话这几个字留在前一段里，归前一段的人
    bounds.append(b)
    if len(kept) == 1 and kept[0][1] == p.speaker:
        return None

    pivot = "".join(chars)
    cuts = [i for i, _ in kept[1:]]
    pieces: dict[str, list[str]] = {}
    for e, r in rows.items():
        nrm, idx = _norm_index(r.text)
        raw_cuts = [idx[q] if q < len(idx) else len(r.text) for q in _cut_positions(pivot, nrm, cuts)]
        parts, prev = [], 0
        for rc in raw_cuts:
            parts.append(r.text[prev:rc])
            prev = rc
        parts.append(r.text[prev:])
        if "".join(parts) != r.text:                     # 理论上不会发生；发生了宁可不拆
            return None
        pieces[e] = parts
    return [{e: Row(bounds[k], bounds[k + 1], spk, pieces[e][k].strip()) for e in rows}
            for k, (_, spk) in enumerate(kept)]


def split(rows_by_engine: dict[str, list[Row]], segs, near: float = 1.0) -> tuple[dict[str, list[Row]], dict]:
    """返回（拆过的各路结果, 统计）。各路的第 N 行必须是同一块（P1 保证）。"""
    ids = list(rows_by_engine)
    n = len(rows_by_engine[ids[0]])
    st = dict(blocks=n, split=0, sub=n, no_stamps=0, reason="")
    if PIVOT not in rows_by_engine:
        st["reason"] = "没开 Paraformer 这一路，拿不到逐字时间，不拆"
        return rows_by_engine, st
    out: dict[str, list[Row]] = {e: [] for e in ids}
    for i in range(n):
        blk = {e: rows_by_engine[e][i] for e in ids}
        if not blk[PIVOT].stamps:
            st["no_stamps"] += 1
        subs = _split_block(blk, segs, near) if blk[PIVOT].stamps else None
        if subs is None:
            for e in ids:
                out[e].append(blk[e])
            continue
        if len(subs) > 1:
            st["split"] += 1
        for sub in subs:
            for e in ids:
                out[e].append(sub[e])
    st["sub"] = len(out[ids[0]])
    return out, st

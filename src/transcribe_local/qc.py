# -*- coding: utf-8 -*-
"""P1 质检闸：四路 ASR 跑完、进分歧册之前，先体检一遍。不用模型，纯规则。

搬自生产线的 Phase1_ASR_QC.py（六项检查），按本地链的结构改了三处：
  · 本地没有主轨，四路平等 —— 每一路都按同一套规则查，不分主参。
  · 四路吃同一份切块，所以「中段空洞」不看时间戳跳变，看**某一路连续多少秒的块都是空的、
    而别的路在同一段有字** —— 那是这一路丢了内容，不是现场静默。
  · 「覆盖率」是切块层的事：末块之后若还有一大段音频、且那段有能量，是漏切，四路一起漏。

⚠️ 本地版**不硬停**。生产线的 QC 退出码 1 会把整单暂停，因为主轨挂了整单就废；
本地四路平等、融合本来就能补一路的缺，所以这里只喊、不拦 —— 但要喊得够响：
逐条打印 + 落 work/qc.json，让人知道哪一路在哪一段不可信。静默才是踩红线的那一种。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import numpy as np

from .audio import SR
from .diarize import Block
from .engines import Row, TAG, ts

MIN_ROWS = 5                  # 段数下限（只对 ≥ 10 分钟的音频算失败；短独白 1–2 段是正常形态）
STRICT_MIN_DUR = 600
LEN_MIN_RATIO = 0.5           # 每路字数 ≥ 全体中位数 × 此比例
GAP_MAX_SEC = 120             # 某一路连续空块累计超过这个秒数 → 疑丢段
GAP_OTHERS_RATE = 0.5         # 同一区间别的路字数 ≥ 秒数 × 此值(字/秒) 才算「别人有字」
TAIL_MAX_SEC = 120            # 末块之后还剩这么多秒音频才去查尾巴
TAIL_ENERGY_RATIO = 0.3       # 尾巴能量 ≥ 人声块中位能量 × 此比例 → 疑漏切
RESIDUE = re.compile(r"<[a-z/|]{1,12}>")   # <sil> <unk> 之类模型标记


@dataclass
class Report:
    fail: list[str] = field(default_factory=list)      # 这一路 / 这一段不可信
    warn: list[str] = field(default_factory=list)      # 要留意，但不一定是错
    stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"fail": self.fail, "warn": self.warn, "stats": self.stats}


def _energy(x: np.ndarray, a: float, b: float) -> float:
    seg = x[int(a * SR):int(b * SR)]
    return float(np.sqrt(np.mean(seg * seg))) if len(seg) else 0.0


def check(rows_by_engine: dict[str, list[Row]], blocks: list[Block], x: np.ndarray | None,
          bad: list[dict] | None = None) -> Report:
    r = Report()
    dur = (len(x) / SR) if x is not None else None
    tags = {e: TAG.get(e, e) for e in rows_by_engine}

    # Q1 非空 / 段数 ————————————————————————————————
    for e, rows in rows_by_engine.items():
        n = sum(1 for q in rows if q.text.strip())
        if n == 0:
            r.fail.append(f"[{tags[e]}] 整路空转录（0 块有字）")
        elif n < MIN_ROWS and (dur is None or dur >= STRICT_MIN_DUR):
            r.fail.append(f"[{tags[e]}] 有字的块只有 {n} 个（< {MIN_ROWS}），疑转录失败")

    # Q2 字数 ≥ 中位数 × 0.5 ————————————————————————
    lens = {tags[e]: sum(len(q.text) for q in rows) for e, rows in rows_by_engine.items()}
    r.stats["chars"] = lens
    if len(lens) >= 2:
        med = statistics.median(lens.values())
        for t, n in lens.items():
            if n < med * LEN_MIN_RATIO:
                r.fail.append(f"[{t}] 字数 {n} 远低于四路中位数 {med:.0f}（< {LEN_MIN_RATIO:.0%}），疑丢内容")

    # Q3 某一路的连续空块（别的路同一段有字） ——————————————
    empties: dict[str, int] = {}
    for e, rows in rows_by_engine.items():
        others = [o for o in rows_by_engine if o != e]
        run_start = run_end = None
        n_empty = 0

        def flush():
            if run_start is None:
                return
            span = run_end - run_start
            if span <= GAP_MAX_SEC:
                return
            other_chars = max(
                sum(len(q.text) for q in rows_by_engine[o] if q.start >= run_start and q.end <= run_end)
                for o in others) if others else 0
            if other_chars >= span * GAP_OTHERS_RATE:
                r.fail.append(f"[{tags[e]}] {ts(run_start)}→{ts(run_end)}（{span:.0f}s）整段空白，"
                              f"而别的路同一段有 {other_chars} 字，疑这一路丢段")
            else:
                r.warn.append(f"[{tags[e]}] {ts(run_start)}→{ts(run_end)}（{span:.0f}s）整段空白，"
                              f"别的路也几乎没字（最多 {other_chars}），疑现场静默")

        for q in rows:
            if q.text.strip():
                flush()
                run_start = run_end = None
            else:
                if (q.end - q.start) >= 2.0:
                    n_empty += 1
                if run_start is None:
                    run_start = q.start
                run_end = q.end
        flush()
        empties[tags[e]] = n_empty
    r.stats["empty_blocks_ge2s"] = empties
    for t, n in empties.items():
        if n:
            r.warn.append(f"[{t}] {n} 个 ≥2 秒的块这一路没出字（熔断器已重试过的在 qc_warnings 里）")

    # Q4 尾部覆盖（切块层，四路共用） ————————————————————
    if x is not None and blocks:
        last = max(b.end for b in blocks)
        tail = dur - last
        r.stats["coverage"] = {"last_block_end": round(last, 1), "audio": round(dur, 1)}
        if tail > TAIL_MAX_SEC:
            voiced = statistics.median(_energy(x, b.start, b.end) for b in blocks[:200]) or 1e-9
            e_tail = _energy(x, last, dur)
            if e_tail >= voiced * TAIL_ENERGY_RATIO:
                r.fail.append(f"[切块] 末块止于 {ts(last)}，之后还有 {tail:.0f}s 音频且有能量"
                              f"（{e_tail / voiced:.0%} 于人声中位），疑漏切，四路一起漏")
            else:
                r.warn.append(f"[切块] 末块止于 {ts(last)}，之后 {tail:.0f}s 几乎无能量，按尾部静音放行")

    # Q5 模型标记残渣 ———————————————————————————
    residue = {}
    for e, rows in rows_by_engine.items():
        hits = [m.group(0) for q in rows for m in RESIDUE.finditer(q.text)]
        if hits:
            top = statistics.mode(hits)
            residue[tags[e]] = {"count": len(hits), "most": top}
            r.warn.append(f"[{tags[e]}] {len(hits)} 处模型标记（最多的是 {top}），分歧册会剥掉，"
                          f"但 <unk> 多说明这一路吐不出那些词")
    r.stats["residue"] = residue

    # Q6 说话人标签 ———————————————————————————
    n_none = sum(1 for b in blocks if b.speaker is None)
    if n_none:
        r.warn.append(f"[声纹] {n_none} 块贴不到说话人（会显示为 X），多在开头")

    # 熔断器重试用尽的块，并进来一起报 ————————————————
    if bad:
        r.stats["circuit_breaker"] = bad
        for d in bad:
            r.fail.append(f"[{d['engine']}] 第 {d['block']} 块 [{ts(d['at'])}] {d['why']}，重试用尽，这一路这一块不可信")
    return r


def dump(rep: Report) -> str:
    lines = []
    for f in rep.fail:
        lines.append("❌ " + f)
    for w in rep.warn:
        lines.append("⚠️ " + w)
    if not lines:
        lines.append("✅ P1 质检通过")
    return "\n".join(lines)

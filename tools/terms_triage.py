#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""挂了术语库之后，库里的词在终稿里到底怎么样了 —— 逐词三态。

    修回    金标里出现这个词的地方，终稿也写对了（写的是库里的词形）
    标疑    终稿没写对，但那一处打了 [❓]（举手说了「这儿我没把握」）
    静默错  既没写对、也没打问号 —— 这才是问题

前两种都算合格（Duner 2026-09-11 定）。用法：
    python3 tools/terms_triage.py 金标.md 术语库.md 终稿1.md [终稿2.md ...]

多份终稿一起给时，按词汇总每一态出现了几次，方便看「三遍里稳不稳」。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("score", ROOT / "tools" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)

ROW = re.compile(r"^-\s*(.+?)\s*[｜|]")


def load_terms(p: Path) -> list[str]:
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip() == "---":
            break
        m = ROW.match(ln.strip())
        if m:
            out += [t.strip() for t in m.group(1).split("/") if t.strip()]
    return out


def triage(gold: list[dict], merged_path: Path, terms: list[str]) -> dict[str, Counter]:
    eng_rows = score.parse(merged_path)
    eng = "".join(score.nrm(x["raw"]) for x in eng_rows)
    eng_raw = "".join(x["raw"] for x in eng_rows)                 # 带 [❓] 的原文，用来判「标疑」
    units = score.align(gold, eng)
    # 对齐给的是归一后的片段；要判有没有问号得回到原文 —— 用片段在归一文里的位置映射回原文位置
    pos_map, j = [], 0
    for ch in eng_raw:
        if ch not in score.PUNCT and ch != "❓" and ch not in "[]":
            pos_map.append(j)
        j += 1
    res: dict[str, Counter] = {t: Counter() for t in terms}
    for t in terms:
        kt = score.key(t)
        for u in units:
            if kt not in score.key(u["raw"]):
                continue
            if kt in score.key(u["eng"]):
                res[t]["修回"] += 1
                continue
            i = eng.find(u["eng"]) if u["eng"] else -1
            if i >= 0 and i < len(pos_map):
                a = pos_map[i]
                b = pos_map[min(len(pos_map) - 1, i + max(len(u["eng"]) - 1, 0))] + 1
                if "❓" in eng_raw[max(0, a - 3):b + 3]:
                    res[t]["标疑"] += 1
                    continue
            res[t]["静默错"] += 1
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", type=Path)
    ap.add_argument("terms", type=Path)
    ap.add_argument("merged", nargs="+", type=Path)
    a = ap.parse_args()
    gold = score.gold_units(a.gold)
    terms = load_terms(a.terms)
    total: dict[str, Counter] = {t: Counter() for t in terms}
    for mp in a.merged:
        for t, c in triage(gold, mp, terms).items():
            total[t].update(c)
    print(f"术语库 {len(terms)} 条 · 终稿 {len(a.merged)} 份 · 金标里出现过的才计\n")
    print("{:<14} {:>5} {:>5} {:>7}".format("词", "修回", "标疑", "静默错"))
    sums = Counter()
    for t in terms:
        c = total[t]
        if not sum(c.values()):
            print(f"{t:<14} {'—':>5}   金标里没出现")
            continue
        print("{:<14} {:>5} {:>5} {:>7}".format(t, c["修回"], c["标疑"], c["静默错"]))
        sums.update(c)
    n = sum(sums.values()) or 1
    print("\n合计  修回 {} · 标疑 {} · 静默错 {}  （合格率 {:.0%}）".format(
        sums["修回"], sums["标疑"], sums["静默错"], (sums["修回"] + sums["标疑"]) / n))


if __name__ == "__main__":
    sys.exit(main())

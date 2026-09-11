#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查一份术语库：格式、重复、疑似错写形。零依赖，一个文件。

用法：
    python3 tools/terms_check.py 术语库.md [--p1 out/xxx/work]

规矩见 docs/terms.md。--p1 给一个跑过的 work/ 目录时，会把库里的词逐个到四路原始输出里找：
一个词在某一路原样出现、而库里另一条的含义与它相近，多半是把引擎的错写形也收进来了 —— 这种要人看一眼。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROW = re.compile(r"^-\s*(.+?)\s*[｜|]\s*(.*)$")


def load(p: Path) -> tuple[list[tuple[int, str, str]], list[str]]:
    rows, problems = [], []
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        ln = raw.strip()
        if ln == "---":                                    # 分隔线之后是给人看的备注，不是词条
            break
        if not ln or ln.startswith("#") or ln.startswith(">") or not ln.startswith("-"):
            continue
        m = ROW.match(ln)
        if not m:
            problems.append(f"第 {i} 行不是「- 术语 ｜ 含义」的格式：{ln[:40]}")
            continue
        if "|" in ln and "｜" not in ln:
            problems.append(f"第 {i} 行用了半角竖线 |，应为全角 ｜：{ln[:40]}")
        term, meaning = m.group(1).strip(), m.group(2).strip()
        if not meaning:
            problems.append(f"第 {i} 行「{term}」没写含义")
        for t in term.split("/"):
            t = t.strip()
            if t:
                rows.append((i, t, meaning))
    return rows, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("terms", type=Path)
    ap.add_argument("--p1", type=Path, default=None, help="跑过的 work/ 目录（含 p1_*.md）")
    a = ap.parse_args()

    rows, problems = load(a.terms)
    cnt = Counter(t for _, t, _ in rows)
    for t, n in cnt.items():
        if n > 1:
            problems.append(f"「{t}」出现了 {n} 次")
    short = [t for _, t, _ in rows if len(t) < 2]
    if short:
        problems.append("单字条目（几乎一定误伤）：" + "、".join(short))

    if a.p1 and a.p1.is_dir():
        raw = "\n".join(q.read_text(encoding="utf-8") for q in sorted(a.p1.glob("p1_*.md")))
        by_meaning: dict[str, list[str]] = {}
        for _, t, m in rows:
            by_meaning.setdefault(m, []).append(t)
        for m, ts in by_meaning.items():
            if len(ts) < 2:
                continue
            seen = [t for t in ts if t in raw]
            if len(seen) >= 2:
                problems.append(f"同一含义下有多个写法都在引擎原始输出里出现过（{' / '.join(seen)}）—— "
                                f"其中很可能有引擎的错写形，库只该收正确那一个")

    print(f"{a.terms.name}：{len(rows)} 条")
    if not problems:
        print("✅ 没发现问题")
        return 0
    for x in problems:
        print("⚠️ " + x)
    return 1


if __name__ == "__main__":
    sys.exit(main())

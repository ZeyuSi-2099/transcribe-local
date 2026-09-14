#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给终稿打分 —— README 里那些数字就是这么来的。

我们的评测跑台（十几台引擎的批量对照、逐条人工判定台）不开源，但**判据必须能复现**，
否则 README 上那张表就只是我们单方面的说法。所以这一个文件是自带的、能跑的：
它做两件事 —— 把金标的每个语义块对到机器稿的对应位置，然后按五条口径决定哪些算问题。

用法：
    python3 tools/score.py 金标.md 终稿.md [--terms 术语库.md ...] [--list]

金标格式：每行 `[分:秒] 说话人: 内容`。终稿用本工具的导出即可（`[时:分.秒] M: 内容`）。

口径（2026-09-11 定，五条都不算问题）：
    ① 只差语气词 / 附和词 / 儿化
    ② 数字体例不同（23 年 ↔ 二三年）
    ③ 的地得 / 您你
    ④ 机器自己标了 [❓] —— 它举手说了「这儿我没把握」
    ⑤ 机器写的是术语库里的词形，金标记的是同字不同序的口语说法

⚠️ **不要用相似度阈值。** 我们第一版用 0.88，结果一个两字术语被听成同音单字、夹在
   三十多字的长句里，整句相似度仍有 0.9+，被判成「一致」放过了。归一之后金标那句
   必须**原样出现**在机器稿里才算过。相似度阈值会让自家产品看起来比实际好。
"""
from __future__ import annotations

import argparse
import difflib
import re
from pathlib import Path

PUNCT = "，。？！、：；…—–·.,?!;:\"'“”‘’()（）《》〈〉「」【】~～-_ \t　"
FILL = set("嗯呃啊哦额诶欸唉哎呀吧嘛呢啦哈唔哟喔噢儿")        # ① 语气词 + 儿化
BC = set("嗯呃啊哦额诶欸唉哎呀吧嘛呢啦哈唔哟喔噢对是好行")     # 纯附和块，不计分
HOMO = {"地": "的", "得": "的", "您": "你"}                   # ③
CN = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
      "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "两": "2"}
TARGET = 32                                                   # 长句再按逗号切到约这个字长

nrm = lambda s: "".join(c for c in s if c not in PUNCT)


def _cn2ar(s: str) -> str:
    """② 「二三年十二月二十八号」→「23年12月28号」。只做体例归一，不算数值。"""
    def one(m):
        t = m.group(0)
        try:
            if "十" not in t:
                return "".join(CN[c] for c in t)
            a, b = t.split("十")
            return (CN[a] if a else "1") + (CN[b] if b else "0")
        except (KeyError, ValueError):
            return t                                          # 认不出就别动
    return re.sub(r"[零一二两三四五六七八九十]+", one, s)


def key(s: str) -> str:
    """归一到「只剩实质内容」。①②③ 在这一步抹平。"""
    s = nrm(s).replace("❓", "").replace("[]", "")
    s = _cn2ar(s)
    s = "".join(HOMO.get(c, c) for c in s)
    s = "".join(c for c in s if c not in FILL)
    return re.sub(r"(.{1,3}?)\1{1,}", r"\1", s)               # 「我我」「他打过他打过」压一次


def split_units(t: str) -> list[str]:
    """一行 → 语义块：先按句末标点切，过长的再按逗号聚成约 TARGET 字。"""
    out = []
    for s in re.split(r"(?<=[。？！])", t):
        s = s.strip()
        if not nrm(s):
            continue
        if len(nrm(s)) <= 45:
            out.append(s)
            continue
        buf = ""
        for c in re.split(r"(?<=[，、；：])", s):
            if not c:
                continue
            if buf and len(nrm(buf)) + len(nrm(c)) > TARGET:
                out.append(buf)
                buf = c
            else:
                buf += c
        if nrm(buf):
            out.append(buf)
    return out


# 一行 = `[时间] 说话人: 内容`。说话人只认「字母 / 数字 / 下划线」，**后面必须紧跟冒号**才算说话人；
# 允许说话人后面挂一个 `[❓]`（「这句话可能不是这个人说的」），它属于说话人、不属于内容。
# ⚠️ 2026-09-14 修：旧写法「说话人可以只有一个字符、冒号可有可无」，遇到 `SPK1: 内容` 只拿走 `S`，
#    把 `PK1: 内容` 当正文；遇到 `M[❓]: 内容` 把 `[❓]:` 当正文，还让这一行第一句按口径 ④ 被豁免。
LINE = re.compile(r"^\s*\[([\d:.\s\-]+)\]\s*(?:([A-Za-z0-9_]{1,12})\s*(?:\[❓\])?\s*[:：])?\s*(.*)$")


def parse(p: Path) -> list[dict]:
    """读 `[时间] 说话人: 内容` 这种行。时间戳格式宽松，说话人可有可无。"""
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        m = LINE.match(ln.strip())
        if not m or not m.group(3).strip():
            continue
        out.append(dict(ts=m.group(1).split("-")[0].strip(), role=(m.group(2) or "").strip(),
                        raw=m.group(3).strip()))
    return out


def gold_units(p: Path) -> list[dict]:
    g, i = [], 0
    for ln in parse(p):
        for u in split_units(ln["raw"]):
            i += 1
            g.append(dict(i=i, ts=ln["ts"], role=ln["role"], raw=u, n=nrm(u)))
    return g


def align(gold: list[dict], eng_text: str) -> list[dict]:
    """把每个金标语义块对到机器稿的对应位置。纯 difflib，没有别的魔法。"""
    G, Gu = "", []
    for g in gold:
        G += g["n"]
        Gu += [g["i"]] * len(g["n"])
    sm = difflib.SequenceMatcher(None, G, eng_text, autojunk=False)
    g2e = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                g2e[i1 + k] = j1 + k
    span = {}
    for p, ui in enumerate(Gu):
        span.setdefault(ui, [p, p])[1] = p
    units = []
    for g in gold:
        if g["i"] not in span:
            continue
        a, b = span[g["i"]][0], span[g["i"]][1] + 1
        hit = [g2e[p] for p in range(a, b) if p in g2e]
        # 最长的一段「金标有、机器稿里没对上」的字（纯语气词的段不算）
        runs, cur = [], []
        for p in range(a, b):
            if p in g2e:
                if cur:
                    runs.append(cur)
                    cur = []
            else:
                cur.append(G[p])
        if cur:
            runs.append(cur)
        gap = max((len(x) for x in runs if any(c not in FILL for c in x)), default=0)
        ratio = len(hit) / max(len(g["n"]), 1)
        if hit:
            lo, hi = min(hit), max(hit)
        else:
            prev = [g2e[p] for p in range(a - 1, -1, -1) if p in g2e]
            nxt = [g2e[p] for p in range(b, len(G)) if p in g2e]
            lo = (prev[0] + 1) if prev else 0
            hi = (nxt[0] - 1) if nxt else len(eng_text) - 1
        units.append(dict(**g, ratio=ratio, gap=gap,
                          eng=eng_text[max(0, lo):hi + 1] if hi >= lo else ""))
    return units


def terms(paths: list[str]) -> set[str]:
    """术语库里的正确词形。只取全角竖线左边那一截。"""
    out = set()
    for p in paths:
        for ln in Path(p).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^-\s*(.+?)\s*[｜|]", ln.strip())
            if m:
                out |= {w.strip() for w in m.group(1).split("/") if w.strip()}
    return out


def _anagram_fix(g: str, e: str, term: set[str]) -> tuple[str, str | None]:
    """⑤ 机器用了术语库里的正确词形，金标记的是同字不同序的口语说法。

    只认「字完全一样、只是顺序不同」。不认近义替换 —— 那会把真听错也一起赦免。
    """
    hit = None
    for t in term:
        kt = key(t)
        if len(kt) < 2 or kt not in e or kt in g:
            continue
        i, out = 0, ""
        while i <= len(g) - len(kt):
            if sorted(g[i:i + len(kt)]) == sorted(kt):        # 一句里可能说好几遍，全换
                out, i, hit = out + kt, i + len(kt), t
            else:
                out, i = out + g[i], i + 1
        g = out + g[i:]
    return g, hit


def verdict(gold: str, eng: str, term: set[str]) -> tuple[bool, str]:
    """这一处算不算问题。返回 (算不算, 为什么)。"""
    if "❓" in eng:
        return False, "④ 机器标了 [❓]"
    g, e = key(gold), key(eng)
    if not g:
        return False, "① 金标这块只剩语气词"
    if g in e:
        return False, "①②③ 归一后金标原样都在"
    g2, hit = _anagram_fix(g, e, term)
    if hit and g2 in e:
        return False, f"⑤ 术语库词形：{hit}"
    return True, "算"


def main() -> None:
    ap = argparse.ArgumentParser(description="按五条口径给终稿打分")
    ap.add_argument("gold", type=Path)
    ap.add_argument("merged", type=Path)
    ap.add_argument("--terms", nargs="*", default=[], help="术语库，可多份")
    ap.add_argument("--list", action="store_true", help="把算问题的逐条列出来")
    a = ap.parse_args()

    gold = gold_units(a.gold)
    eng = "".join(nrm(x["raw"]) for x in parse(a.merged))
    tb = terms(a.terms)
    bc = [g for g in gold if set(g["n"]) <= BC]
    units = align(gold, eng)
    # 两道闸。第一道看**对齐质量**：金标这块几乎原样出现在机器稿里、且没有一段连续
    # 两字以上对不上，就直接算过，不必再逐条读。第二道才是那五条口径。
    # ⚠️ 第一道的 0.88 是**筛选**阈值，不是判定阈值 —— 它必须和「最长未命中段 < 2 字」
    #    一起用。只看相似度会放过长句里的单词误听（那正是我们踩过的坑）。
    pend = [u for u in units if not set(u["n"]) <= BC and not (u["ratio"] >= 0.88 and u["gap"] < 2)]
    bad = [u for u in pend if verdict(u["raw"], u["eng"], tb)[0]]

    print(f"金标 {len(gold)} 个语义块（纯附和 {len(bc)} 个不计分，实质 {len(gold) - len(bc)} 个）")
    print(f"术语库 {len(tb)} 条")
    print(f"\n对齐就过了的：{len(gold) - len(bc) - len(pend)}  要人读的：{len(pend)}"
          f"  → 按五条口径算问题的：{len(bad)}")
    if a.list:
        for u in bad:
            print(f"\n#{u['i']} [{u['ts']}] {u['role']}\n  金 {u['raw']}\n  机 {u['eng']}")


if __name__ == "__main__":
    main()

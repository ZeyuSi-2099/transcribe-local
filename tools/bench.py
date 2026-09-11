#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三遍取均值的回归台 —— 固定一份「听」的产物，只让「定字」那一步跑 N 遍，然后打分。

为什么要有它：P3 同配置跑三次修回数出过 0 / 3 / 3，单跑一次分不清「改好了」还是「运气」。
所以任何提示词 / 后端 / 参数的结论，都从这里出：均值、极差、两列数、回退数，一条命令。

用法：
    python3 tools/bench.py 工作目录 --gold 金标.md [--terms 术语库.md] [--runs 3] [--config config.yaml]
    python3 tools/bench.py 工作目录 --gold 金标.md --merged 终稿1.md 终稿2.md   # 只打分，不调 P3
    python3 tools/bench.py 工作目录 --gold 金标.md --control                     # 控制组，必须得 0

工作目录 = 上次 run 的 work/（blocks.json + p1_*.md），分歧册由当前代码重建。
控制组 = 把顶格那一路原样当终稿打分：拿回必须是 0、回退必须是 0，否则尺子不准，后面的数都别信。

两列数：「算问题」= 标了 [❓] 的块不算错；「一处不豁免」= 把 [❓] 抹掉再判。
只看前一列会高估 —— 模型多标存疑就能把它压下去。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from transcribe_local import config, divergence, fuse  # noqa: E402
from transcribe_local.engines import Row, TAG, ts  # noqa: E402

_spec = importlib.util.spec_from_file_location("score", ROOT / "tools" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)

P1_LINE = re.compile(r"^\[([\d:.]+) - ([\d:.]+)\] SPK(\d+): (.*)$")


def load_work(work: Path, enabled: list[str]) -> tuple[list, dict[str, list[Row]]]:
    blocks = json.loads((work / "blocks.json").read_text(encoding="utf-8"))
    rows: dict[str, list[Row]] = {}
    for e in enabled:
        p = work / f"p1_{TAG[e]}.md"
        if not p.exists():
            continue
        got = {}
        for ln in p.read_text(encoding="utf-8").splitlines():
            m = P1_LINE.match(ln)
            if m:
                got[(m[1], m[2])] = m[4]
        rows[e] = [Row(a, b, s, got.get((ts(a), ts(b)), "")) for a, b, s in blocks]
    return blocks, rows


SCORE_LINE = re.compile(r"^\s*\[([\d:.\s\-]+)\]\s*(\S+?)?\s*[:：]?\s*(.*)$")   # 与 score.parse 同一条


def _parse_text(text: str) -> list[dict]:
    """score.parse 只收文件路径；这里对内存里的终稿做同样的事。"""
    out = []
    for ln in text.splitlines():
        m = SCORE_LINE.match(ln.strip())
        if m and m.group(3).strip():
            out.append(dict(raw=m.group(3).strip()))
    return out


def bad_units(gold: list[dict], text_lines: str, tb: set[str], strict: bool) -> set[int]:
    """返回算问题的语义块编号。strict = 把 [❓] 抹掉再判（一处不豁免）。"""
    eng = "".join(score.nrm(x["raw"]) for x in _parse_text(text_lines))
    if strict:
        eng = eng.replace("❓", "").replace("[]", "")
    units = score.align(gold, eng)
    pend = [u for u in units if not set(u["n"]) <= score.BC and not (u["ratio"] >= 0.88 and u["gap"] < 2)]
    return {u["i"] for u in pend if score.verdict(u["raw"], u["eng"], tb)[0]}


GOLD_ROLE = {"M": "M", "1": "R", "R": "R", "2": "R"}          # 金标写法：主持人 M:、受访者 1:


def speaker_accuracy(gold: list[dict], text: str) -> tuple[int, int]:
    """说话人准确率，按文字对齐口径（时间轴口径会把 1 秒偏差误判成整句错）。
    把每个金标语义块对到终稿里的位置（用对齐命中位置的中位数，不用文本查找 —— 「嗯」这种短块
    查找会落到全篇第一个「嗯」上），看落在哪一行、那一行标的是谁。返回 (对上的块数, 参与比较的块数)。"""
    import difflib
    spans, eng, pos = [], "", 0
    for ln in text.splitlines():
        m = SCORE_LINE.match(ln.strip())
        if not m or not m.group(3).strip():
            continue
        t = score.nrm(m.group(3).strip())
        spk = (m.group(2) or "").replace("[❓]", "").replace("❓", "").strip(" :：")
        spans.append((pos, pos + len(t), spk))
        eng += t
        pos += len(t)
    G, Gu = "", []
    for g in gold:
        G += g["n"]
        Gu += [g["i"]] * len(g["n"])
    g2e = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, G, eng, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                g2e[i1 + k] = j1 + k
    span = {}
    for q, ui in enumerate(Gu):
        span.setdefault(ui, [q, q])[1] = q
    ok = n = 0
    for g in gold:
        if set(g["n"]) <= score.BC or g["i"] not in span:
            continue                                       # 附和块不参与
        a, b = span[g["i"]]
        hits = sorted(g2e[q] for q in range(a, b + 1) if q in g2e)
        if len(hits) < max(2, len(g["n"]) // 2):           # 没对上一半的块不参与
            continue
        mid = hits[len(hits) // 2]
        spk = next((s for x, y, s in spans if x <= mid < y), None)
        if spk is None:
            continue
        want = GOLD_ROLE.get(g["role"].strip(" :："), g["role"])
        n += 1
        ok += spk == want
    return ok, n


def route_text(rows: list[Row]) -> str:
    return "\n".join(f"[{ts(r.start)} - {ts(r.end)}] SPK{r.speaker}: {r.text}" for r in rows if r.text)


def main() -> None:
    ap = argparse.ArgumentParser(description="固定 P1 产物，P3 跑 N 遍，打分取均值")
    ap.add_argument("work", type=Path)
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--terms", nargs="*", default=[])
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--merged", nargs="*", type=Path, default=None, help="只给现成终稿打分")
    ap.add_argument("--control", action="store_true", help="控制组：顶格那一路原样当终稿")
    ap.add_argument("--out", type=Path, default=None, help="终稿落盘目录（默认 工作目录/bench）")
    ap.add_argument("--blocks", default=None, metavar="a-b",
                    help="只取第 a 到 b 块（含），做定点 A/B 用；打分也只算这段时间内的金标块")
    ap.add_argument("--candidates", nargs="*", type=Path, default=None,
                    help="候选引擎的 p1_<TAG>.md（tools/try_engine.py 的产物）：报单台成绩，以及加进来后「全错」还剩几块")
    a = ap.parse_args()

    cfg = config.load(a.config)
    if a.terms:
        cfg["terms"]["files"] = [str(t) for t in a.terms]
    enabled = list(cfg["engines"]["enabled"])
    blocks, rows = load_work(a.work, enabled)
    enabled = [e for e in enabled if e in rows]
    rows = {e: rows[e] for e in enabled}
    if a.blocks:
        lo, hi = (int(v) for v in a.blocks.split("-"))
        blocks = blocks[lo - 1:hi]
        rows = {e: r[lo - 1:hi] for e, r in rows.items()}
    print(f"工作目录 {a.work} · {len(blocks)} 块 · 路 {' '.join(TAG[e] for e in enabled)}")

    gold = score.gold_units(a.gold)
    if a.blocks:                                             # 只算这段时间里的金标块
        t_lo, t_hi = blocks[0][0], blocks[-1][1]

        def _sec(t):
            mm, ss = t.split(":")[:2]
            return int(mm) * 60 + float(ss)
        gold = [g for g in gold if t_lo - 1 <= _sec(g["ts"]) <= t_hi + 1]
        print(f"定点窗口 {ts(t_lo)}–{ts(t_hi)}，金标 {len(gold)} 块")
    tb = score.terms([str(t) for t in a.terms])
    n_bc = sum(1 for g in gold if set(g["n"]) <= score.BC)
    print(f"金标 {len(gold)} 块（纯附和 {n_bc}，实质 {len(gold) - n_bc}）· 术语库 {len(tb)} 条")

    # 单台 + 天花板
    single = {TAG[e]: bad_units(gold, route_text(rows[e]), tb, strict=False) for e in enabled}
    ceiling = set.intersection(*single.values()) if single else set()
    print("\n单台（算问题）： " + "  ".join(f"{t} {len(s)}" for t, s in single.items())
          + f"   ｜ 四路全错（天花板）{len(ceiling)}")
    top = TAG[enabled[0]]

    if a.candidates:
        print("\n候选引擎（单台 · 与现有四路一起错的块数 · 四路 + 它 = 五路全错）")
        for cp in a.candidates:
            got = {}
            for ln in cp.read_text(encoding="utf-8").splitlines():
                m = P1_LINE.match(ln)
                if m:
                    got[(m[1], m[2])] = m[4]
            crow = [Row(b[0], b[1], b[2], got.get((ts(b[0]), ts(b[1])), "")) for b in blocks]
            cb = bad_units(gold, route_text(crow), tb, strict=False)
            five = ceiling & cb
            best_pair = min(((t, len(s & cb)) for t, s in single.items()), key=lambda x: x[1])
            print(f"  {cp.stem.replace('p1_', ''):8s} 单台 {len(cb):3d} ｜ 五路全错 {len(five):3d}（四路 {len(ceiling)}）"
                  f" ｜ 与最不重合的那台（{best_pair[0]}）同时错 {best_pair[1]}")
        return

    p3in, ledger, found = divergence.build(rows, cfg)
    out = a.out or (a.work / "bench")
    out.mkdir(parents=True, exist_ok=True)

    merged_texts: list[tuple[str, str, float]] = []      # (名字, 终稿文本, 耗时)
    if a.control:
        roles = divergence.assign_roles(rows, cfg["diarize"].get("role_assign", "talk_time"))   # 谁是 M 谁是 R 按主链的判法
        ctl = route_text(rows[enabled[0]])
        for spk, role in roles.items():
            ctl = ctl.replace(f"SPK{spk}:", f"{role}:")
        merged_texts.append(("control(" + top + ")", ctl, 0.0))
    elif a.merged:
        for p in a.merged:
            merged_texts.append((p.name, p.read_text(encoding="utf-8"), 0.0))
    else:
        for k in range(1, a.runs + 1):
            t0 = time.time()
            print(f"\nP3 第 {k}/{a.runs} 遍 · {cfg['p3']['model']} @ {cfg['p3']['base_url']}")
            text = fuse.fuse(p3in, found, cfg, on_batch=lambda n, N: print(f"    批次 {n}/{N}", end="\r"))
            sec = time.time() - t0
            p = out / f"run{k}.merged.md"
            p.write_text(text, encoding="utf-8")
            merged_texts.append((p.name, text, sec))
            print(f"    落盘 {p} ({sec:.0f}s)")

    # 打分
    print("\n{:<28} {:>6} {:>8} {:>6} {:>6} {:>9} {:>7}".format("终稿", "算问题", "不豁免", "拿回", "回退", "说话人", "耗时s"))
    cols: list[tuple[int, int, int, int]] = []
    for name, text, sec in merged_texts:
        b1 = bad_units(gold, text, tb, strict=False)
        b2 = bad_units(gold, text, tb, strict=True)
        regress = {u for u in b1 if all(u not in s for s in single.values())}   # 四路都对、终稿错
        gain = len(single[top]) - len(b1)
        sp_ok, sp_n = speaker_accuracy(gold, text)
        sp = f"{sp_ok / sp_n:.1%}" if sp_n else "—"
        cols.append((len(b1), len(b2), gain, len(regress)))
        print("{:<28} {:>6} {:>8} {:>6} {:>6} {:>9} {:>7.0f}".format(name[:28], len(b1), len(b2), gain, len(regress), sp, sec))
        if regress:
            print("    回退块：" + " ".join(f"#{u}" for u in sorted(regress)))
    if len(cols) > 1:
        import statistics
        m = [statistics.mean(c[i] for c in cols) for i in range(4)]
        rg = [max(c[i] for c in cols) - min(c[i] for c in cols) for i in range(4)]
        print("{:<28} {:>6.1f} {:>8.1f} {:>6.1f} {:>6.1f}".format(f"均值 (n={len(cols)})", *m))
        print("{:<28} {:>6} {:>8} {:>6} {:>6}".format("极差", *rg))
    if a.control:
        ok = cols and cols[0][2] == 0 and cols[0][3] == 0
        print("\n控制组 " + ("✅ 拿回 0、回退 0，尺子准" if ok else "❌ 拿回或回退不为 0，尺子有问题，别信后面的数"))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

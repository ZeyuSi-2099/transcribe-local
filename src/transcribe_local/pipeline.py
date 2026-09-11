# -*- coding: utf-8 -*-
"""整条链跑一遍。命令行和网页界面调的是这一个函数。

抽出来的理由很实在：两个入口各写一份流水线，迟早会走偏 ——
到时候「命令行跑出来的成绩」和「界面跑出来的成绩」对不上，而我们整个项目是靠成绩说话的。
所以这里只暴露一个 run()，进度靠回调往外报，谁调谁自己决定怎么显示。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Result:
    out: Path
    stem: str
    minutes: float = 0.0
    segments: int = 0
    blocks: int = 0
    engines: dict[str, int] = field(default_factory=dict)      # 引擎 tag → 字数
    qc: list[dict] = field(default_factory=list)               # 重试用尽仍跑飞的块
    substantive: int = 0
    fillers: int = 0
    uncertain: int = 0                                          # 终稿里 [❓] 的处数
    merged: str = ""
    ledger: str = ""          # 分歧册原文，结果页拿它把 [❓] 展开成各路候选
    exports: list[Path] = field(default_factory=list)
    seconds: float = 0.0


def run(audio: Path, out: Path, cfg: dict, *, no_fuse: bool = False, emit=None) -> Result:
    """跑完整条链。emit(kind, **kw) 是进度回调，kind 见下面各处调用。"""
    from . import audio as au, diarize, divergence, engines, export, fuse

    say = emit or (lambda *a, **k: None)
    out.mkdir(parents=True, exist_ok=True)
    (out / "work").mkdir(exist_ok=True)
    r = Result(out=out, stem=audio.stem)
    t0 = time.time()

    say("stage", name="P0", text="转码 · 声纹 · 切块")
    wav = au.to_wav(audio, out / f"{r.stem}.16k.wav", cfg["audio"]["sample_rate"], cfg["audio"]["channels"])
    x = au.load(wav)
    r.minutes = len(x) / au.SR / 60
    say("audio", minutes=round(r.minutes, 1))

    segs = diarize.diarize(x, cfg)
    blocks = diarize.chop(x, segs, cfg)
    diarize.save_blocks(blocks, segs, out / "work")
    r.segments, r.blocks = len(segs), len(blocks)
    say("chop", segments=r.segments, blocks=r.blocks, seconds=round(time.time() - t0))

    rows = {}
    for e in cfg["engines"]["enabled"]:
        tag = engines.TAG.get(e, e)
        t = time.time()
        say("engine_start", engine=tag, route=engines.ROUTE.get(e, ""), blocks=r.blocks)
        rows[e], bad = engines.transcribe(
            e, x, blocks, cfg,
            on_block=lambda i, n, _t=tag: say("engine_block", engine=_t, done=i, total=n))
        (out / "work" / f"p1_{tag}.md").write_text(engines.dump(rows[e]), encoding="utf-8")
        r.engines[tag] = sum(len(row.text) for row in rows[e])
        r.qc += bad
        say("engine_done", engine=tag, chars=r.engines[tag], seconds=round(time.time() - t), bad=len(bad))

    if r.qc:
        (out / "work" / "qc_warnings.json").write_text(
            json.dumps(r.qc, ensure_ascii=False, indent=1), encoding="utf-8")
        say("qc", count=len(r.qc))

    p3in, ledger, found = divergence.build(rows, cfg)
    (out / "work" / "p3_input.md").write_text(p3in, encoding="utf-8")
    (out / "work" / "divergence.md").write_text(ledger, encoding="utf-8")
    r.ledger = ledger
    r.substantive = sum(len(d.substantive) for d in found)
    r.fillers = sum(d.filler_count for d in found)
    say("divergence", substantive=r.substantive, fillers=r.fillers)

    if no_fuse:
        r.seconds = time.time() - t0
        say("done", seconds=round(r.seconds), fused=False)
        return r

    t = time.time()
    say("stage", name="P3", text=f"{cfg['p3']['model']} @ {cfg['p3']['base_url']}")
    r.merged = fuse.fuse(p3in, found, cfg,
                         on_batch=lambda n, N: say("batch", done=n, total=N))
    (out / f"{r.stem}.merged.md").write_text(r.merged, encoding="utf-8")
    r.uncertain = r.merged.count("[❓]")
    say("fused", lines=len(r.merged.splitlines()), uncertain=r.uncertain, seconds=round(time.time() - t))

    r.exports = list(export.write(r.merged, out / r.stem, cfg))
    r.seconds = time.time() - t0
    say("done", seconds=round(r.seconds), fused=True, exports=[p.name for p in r.exports])
    return r

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选引擎横评：拿基线那份切块喂一台还没进清单的引擎，出一份 p1_<TAG>.md，交给 bench.py 打分。

不碰 models/manifest.toml —— 候选先在这儿跑，赢了（把「四路全错」的块数压下去）再进清单补 sha256。
模型目录直接指本机解压好的包（默认在 TRANSCRIBE_LOCAL_MODELS 或 ~/.cache/sherpa-onnx-models）。

用法：
    python3 tools/try_engine.py funasr_nano  --model-dir ~/.cache/sherpa-onnx-models/sherpa-onnx-funasr-nano-int8-2025-12-30 \\
        --work 基线目录 --audio 音频.16k.wav [--out 目录] [--threads 2] [--blocks a-b]

复读检测 + 三档重试 + 弃用，和正式四路走同一段代码（engines.transcribe）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sherpa_onnx  # noqa: E402

from transcribe_local import audio, config, engines  # noqa: E402
from transcribe_local.diarize import Block  # noqa: E402
from transcribe_local.engines import TAG  # noqa: E402


def build(kind: str, d: Path, threads: int):
    R = sherpa_onnx.OfflineRecognizer
    tok = str(d / "tokens.txt")
    if kind == "funasr_nano":
        return R.from_funasr_nano(
            encoder_adaptor=str(_pick(d, "encoder_adaptor")), llm=str(_pick(d, "llm")),
            embedding=str(_pick(d, "embedding")), tokenizer=str(next((q for q in d.iterdir() if q.is_dir() and (q / "tokenizer.json").exists()), d / "tokenizer")),
            num_threads=threads, language="zh")
    if kind in ("sense_voice", "sense_voice_nano"):
        return R.from_sense_voice(model=str(_pick(d, "model")), tokens=tok, num_threads=threads,
                                  language="zh", use_itn=True)
    if kind == "cohere_transcribe":
        return R.from_cohere_transcribe(encoder=str(_pick(d, "encoder")), decoder=str(_pick(d, "decoder")),
                                        tokens=tok, num_threads=threads, language="zh")
    if kind == "omnilingual_ctc":
        return R.from_omnilingual_asr_ctc(model=str(_pick(d, "model")), tokens=tok, num_threads=threads)
    return engines.build(kind, threads)


def _pick(d: Path, stem: str) -> Path:
    """包里同名文件可能有 .int8.onnx / .onnx / .fp16.onnx，按这个顺序取。"""
    for suf in (".int8.onnx", ".onnx", ".fp16.onnx"):
        for p in sorted(d.glob(f"{stem}*{suf}")):
            return p
    raise FileNotFoundError(f"{d} 里找不到 {stem}*.onnx")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=sorted(TAG))
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True, help="含 blocks.json 的基线目录")
    ap.add_argument("--audio", type=Path, required=True, help="16 kHz 单声道 wav")
    ap.add_argument("--out", type=Path, default=None, help="默认写回 --work")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--blocks", default=None, metavar="a-b")
    a = ap.parse_args()

    cfg = config.load(None)
    blocks = [Block(*b) for b in json.loads((a.work / "blocks.json").read_text(encoding="utf-8"))]
    if a.blocks:
        lo, hi = (int(v) for v in a.blocks.split("-"))
        blocks = blocks[lo - 1:hi]
    x = audio.load(a.audio)
    tag = TAG[a.kind]
    print(f"{tag} · {a.kind} · {len(blocks)} 块 · 模型 {a.model_dir.name}")
    t0 = time.time()
    rec = build(a.kind, a.model_dir, a.threads)
    print(f"  加载 {time.time() - t0:.0f}s")
    t1 = time.time()
    rows, bad = engines.transcribe(a.kind, x, blocks, cfg, rec=rec,
                                   on_block=lambda i, n: print(f"  {i}/{n}", end="\r", flush=True))
    sec = time.time() - t1
    out = a.out or a.work
    out.mkdir(parents=True, exist_ok=True)
    (out / f"p1_{tag}.md").write_text(engines.dump(rows), encoding="utf-8")
    if bad:
        (out / f"qc_{tag}.json").write_text(json.dumps(bad, ensure_ascii=False, indent=1), encoding="utf-8")
    chars = sum(len(r.text) for r in rows)
    print(f"  完成 {sec:.0f}s · {chars} 字 · 弃用 {len(bad)} 块 · 写到 {out / f'p1_{tag}.md'}")
    (out / f"perf_{tag}.json").write_text(json.dumps(dict(
        kind=a.kind, model=a.model_dir.name, blocks=len(blocks), seconds=round(sec, 1),
        chars=chars, bad=len(bad), threads=a.threads), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

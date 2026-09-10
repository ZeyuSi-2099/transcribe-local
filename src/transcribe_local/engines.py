# -*- coding: utf-8 -*-
"""Phase 1：多路 ASR。每一路吃同一份切块，只换识别模型。

选这四台不是因为成绩最好，是因为**解码路线互不相同** —— 两两坏块重合度 32.6%–45.8%。
同源的引擎加进来对组合天花板零增益（第五台 SeACo 与 Paraformer 重合 88.1%，天花板 19 → 19）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import sherpa_onnx

from .audio import SR
from .diarize import Block
from .models import model_dir

ROUTE = {
    "firered_asr2": "AED 自回归",
    "zipformer_ctc": "CTC 逐帧",
    "paraformer_2023": "非自回归",
    "qwen3_asr": "LLM 式",
    "seaco_paraformer": "非自回归 + 热词",
}

# AED 系自回归解码，跑两次不是同一份稿子，会复读也会吐空 —— 必须配熔断器。
# CTC 系逐帧对齐，跑十次同一份稿子。
NONDETERMINISTIC = {"firered_asr2", "qwen3_asr"}

TAG = {
    "firered_asr2": "FRED2",
    "zipformer_ctc": "ZIPC",
    "paraformer_2023": "PARA",
    "qwen3_asr": "QWEN3",
    "seaco_paraformer": "SEACO",
}


@dataclass
class Row:
    start: float
    end: float
    speaker: int | None
    text: str


def build(engine_id: str, num_threads: int = 2):
    d = model_dir(engine_id)
    tok = str(d / "tokens.txt")
    R = sherpa_onnx.OfflineRecognizer
    if engine_id in ("paraformer_2023", "seaco_paraformer"):
        return R.from_paraformer(paraformer=str(d / "model.int8.onnx"), tokens=tok, num_threads=num_threads)
    if engine_id == "zipformer_ctc":
        return R.from_zipformer_ctc(model=str(d / "model.int8.onnx"), tokens=tok, num_threads=num_threads)
    if engine_id == "firered_asr2":
        return R.from_fire_red_asr(encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
                                   tokens=tok, num_threads=num_threads)
    if engine_id == "qwen3_asr":
        return R.from_qwen3_asr(conv_frontend=str(d / "conv_frontend.onnx"),
                                encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
                                tokenizer=str(d / "tokenizer"), num_threads=num_threads,
                                max_total_len=2048, max_new_tokens=1024)
    raise ValueError(f"未知引擎：{engine_id}")


def _decode(rec, x: np.ndarray, a: float, b: float) -> str:
    st = rec.create_stream()
    st.accept_waveform(SR, x[int(a * SR):int(b * SR)])
    rec.decode_stream(st)
    return st.result.text.strip()


def _runaway(text: str, threshold: int) -> bool:
    """同一字符连续重复到阈值 = 解码跑飞了。实测见过把「嗯」复读 99 次、吞掉三个语义块。"""
    return bool(re.search(rf"(.)\1{{{threshold - 1},}}", text))


def transcribe(engine_id: str, x: np.ndarray, blocks: list[Block], cfg: dict,
               on_block=None) -> list[Row]:
    eng = cfg["engines"]
    cb = eng.get("circuit_breaker", {})
    rec = build(engine_id, int(eng.get("num_threads", 2)))
    rows: list[Row] = []
    for i, blk in enumerate(blocks):
        text = _decode(rec, x, blk.start, blk.end)
        # 熔断：跑飞或吐空就重跑这一块。AED 系不确定，重跑大概率就好了。
        if engine_id in NONDETERMINISTIC:
            tries = int(cb.get("max_retry", 2))
            while tries > 0:
                bad_repeat = cb.get("repeat_detect") and _runaway(text, int(cb.get("repeat_threshold", 10)))
                bad_empty = cb.get("empty_block_retry") and not text and (blk.end - blk.start) >= 2.0
                if not (bad_repeat or bad_empty):
                    break
                text = _decode(rec, x, blk.start, blk.end)
                tries -= 1
        rows.append(Row(blk.start, blk.end, blk.speaker, text))
        if on_block:
            on_block(i + 1, len(blocks))
    return rows


def ts(x: float) -> str:
    return f"{int(x) // 60:02d}:{x - (int(x) // 60) * 60:04.1f}"


def dump(rows: list[Row]) -> str:
    return "\n".join(f"[{ts(r.start)} - {ts(r.end)}] SPK{r.speaker}: {r.text}" for r in rows if r.text)

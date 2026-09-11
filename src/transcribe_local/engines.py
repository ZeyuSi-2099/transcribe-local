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

# 单台跑起来的峰值常驻内存（MB），用来算能同时跑几台。
# [未验证] 现在取的是这台引擎权重文件的字节数。onnxruntime 多半把权重 mmap 进来，
# 真实常驻会低于这个数 —— 所以它是个**上界**，拿它当分母偏保守，宁可少跑一台。
# 发布前要逐台实测峰值（ru_maxrss）替换掉，届时改标 [定档]。
PEAK_MB = {
    "firered_asr2": 1235,
    "zipformer_ctc": 367,
    "paraformer_2023": 243,
    "qwen3_asr": 983,
    "seaco_paraformer": 953,
}

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


def _qc(text: str, blk: Block, engine_id: str, cb: dict) -> str | None:
    """这一块这一路有没有跑飞。没问题返回 None。"""
    if engine_id not in NONDETERMINISTIC:
        return None                                  # CTC 系逐帧对齐，没这个毛病
    if cb.get("repeat_detect") and _runaway(text, int(cb.get("repeat_threshold", 10))):
        return "复读"
    if cb.get("empty_block_retry") and not text and (blk.end - blk.start) >= 2.0:
        return "空块"
    return None


def transcribe(engine_id: str, x: np.ndarray, blocks: list[Block], cfg: dict,
               on_block=None) -> tuple[list[Row], list[dict]]:
    """返回（逐块结果, 重试用尽仍未消除的坏块）。

    ⚠️ 坏块**不改写、不丢弃**，原样留在输出里 —— 改写等于把问题藏起来，
    而另外三路对同一块有自己的版本，融合那一步本来就能把它救回来。
    但必须喊出来：静默接受坏块会踩「内容不能丢」这条红线。
    """
    eng = cfg["engines"]
    cb = eng.get("circuit_breaker", {})
    retry = int(cb.get("max_retry", 2))
    rec = build(engine_id, int(eng.get("num_threads", 2)))
    rows: list[Row] = []
    bad: list[dict] = []
    tag = TAG.get(engine_id, engine_id)
    for i, blk in enumerate(blocks):
        text = _decode(rec, x, blk.start, blk.end)
        why = _qc(text, blk, engine_id, cb)
        for _ in range(retry):                       # AED 系不确定，重跑大概率就好了
            if not why:
                break
            text = _decode(rec, x, blk.start, blk.end)
            why = _qc(text, blk, engine_id, cb)
        if why:
            bad.append(dict(block=i + 1, at=round(blk.start, 1), engine=tag, why=why, text=text[:60]))
            print(f"⚠️ {tag} 第 {i + 1} 块 [{ts(blk.start)}] {why}，重试 {retry} 次仍未消除。"
                  f"该块这一路不可信，交给融合时请留意。")
        rows.append(Row(blk.start, blk.end, blk.speaker, text))
        if on_block:
            on_block(i + 1, len(blocks))
    return rows, bad


def ts(x: float) -> str:
    return f"{int(x) // 60:02d}:{x - (int(x) // 60) * 60:04.1f}"


def dump(rows: list[Row]) -> str:
    return "\n".join(f"[{ts(r.start)} - {ts(r.end)}] SPK{r.speaker}: {r.text}" for r in rows if r.text)

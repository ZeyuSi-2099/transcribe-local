# -*- coding: utf-8 -*-
"""Phase 1：多路 ASR。每一路吃同一份切块，只换识别模型。

选这四台不是因为成绩最好，是因为**解码路线互不相同** —— 两两坏块重合度 32.6%–45.8%。
同源的引擎加进来对组合天花板零增益（第五台 SeACo 与 Paraformer 重合 88.1%，天花板 19 → 19）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    # ── 候选池（模块 2 盘点，2026-09-12）：只在 tools/try_engine.py 里跑横评，不在默认清单里 ──
    "funasr_nano": "LLM 式",
    "sense_voice": "非自回归",
    "sense_voice_nano": "非自回归",
    "cohere_transcribe": "AED 自回归",
    "omnilingual_ctc": "CTC 逐帧",
}

# AED / LLM 式自回归解码，跑两次不是同一份稿子，会复读也会吐空 —— 必须配熔断器。
# CTC / 非自回归逐帧对齐，跑十次同一份稿子。
NONDETERMINISTIC = {"firered_asr2", "qwen3_asr", "funasr_nano", "cohere_transcribe"}

# 解码时顺带留下逐字时间的引擎 —— speaker_split 拿它判「这几个字是谁说的」。
# 四台里 Zipformer-CTC 也报时间，但 token 是字节级 BPE、对不回单字；FireRed / Qwen3 不报。
STAMPED = {"paraformer_2023"}

# 单台跑起来的峰值常驻内存（MB），用来算能同时跑几台。
# [未验证] 现在取的是这台引擎权重文件的字节数。onnxruntime 多半把权重 mmap 进来，
# 真实常驻会低于这个数 —— 所以它是个**上界**，拿它当分母偏保守，宁可少跑一台。
# 发布前要逐台实测峰值（ru_maxrss）替换掉，届时改标 [定档]。
PEAK_MB = {
    "firered_asr2": 1235,
    "zipformer_ctc": 367,
    "paraformer_2023": 243,
    "qwen3_asr": 983,
}

TAG = {
    "firered_asr2": "FRED2",
    "zipformer_ctc": "ZIPC",
    "paraformer_2023": "PARA",
    "qwen3_asr": "QWEN3",
    "funasr_nano": "FNANO",
    "sense_voice": "SENSE",
    "sense_voice_nano": "SVNANO",
    "cohere_transcribe": "COHERE",
    "omnilingual_ctc": "OMNI",
}


@dataclass
class Row:
    start: float
    end: float
    speaker: int | None
    text: str
    stamps: list[tuple[str, float]] = field(default_factory=list)   # (token, 绝对秒)，只有 STAMPED 里的引擎有


def build(engine_id: str, num_threads: int = 2):
    d = model_dir(engine_id)
    tok = str(d / "tokens.txt")
    R = sherpa_onnx.OfflineRecognizer
    if engine_id == "paraformer_2023":
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


# ── 复读（Doom Loop）检测：字符法 + 词法两道都跑，任一命中即判 ─────────────────────
# 搬自生产线 Gemini 适配器（Workflow/engines/gemini.py::find_repetition_loop），阈值 10 是
# 113 份产物上定的线。两道并存是因为各自只在一半语种上有效：字符法覆盖无空格分词的中日
# （「嗯嗯嗯…」「可能可能可能…」），词法覆盖有空格的语种（芬兰语 `Vihollinen! ` ×71 字符法
# 一次都抓不到，2026-08-26 事故）。本地版现在只跑中文，词法先带着 —— 英文 profile 迟早要用。
REPEAT_MIN_REPS = 10


def _find_char_loop(text: str, min_reps: int):
    """长度 1–6 的短串连续重复 ≥ min_reps 次。返回 (重复串, 次数) 或 None。"""
    n = len(text)
    if n < min_reps:
        return None
    for plen in (1, 2, 3, 4, 5, 6):
        i = 0
        while i + plen <= n:
            phrase = text[i:i + plen]
            if not phrase.strip():
                i += 1
                continue
            reps, pos = 1, i + plen
            while text[pos:pos + plen] == phrase:
                reps += 1
                pos += plen
            if reps >= min_reps:
                return (phrase, reps)
            i = pos if reps > 1 else i + 1
    return None


def _find_word_loop(text: str, min_reps: int):
    """按空格切词，同一个词 / 同一组 2–3 词短语连续重复 ≥ min_reps 次。"""
    toks = text.split()
    if len(toks) < min_reps:
        return None
    for plen in (1, 2, 3):
        i = 0
        while i + plen <= len(toks):
            phrase = toks[i:i + plen]
            reps, pos = 1, i + plen
            while toks[pos:pos + plen] == phrase:
                reps += 1
                pos += plen
            if reps >= min_reps:
                return (" ".join(phrase), reps)
            i = pos if reps > 1 else i + 1
    return None


def find_repetition_loop(text: str, min_reps: int = REPEAT_MIN_REPS):
    """这一块的文本有没有跑飞。返回 (重复串, 次数) 或 None。"""
    for finder in (_find_char_loop, _find_word_loop):
        hit = finder(text, min_reps)
        if hit:
            return hit
    return None


# ── 重试梯子 ────────────────────────────────────────────────────────────────────
# ⚠️ **原样重跑是没用的** —— sherpa-onnx 的离线解码是贪心的，同一段音频喂进去逐字相同
#   （2026-09-11 实测：FireRed 同一块连跑三遍，输出长度都是 122、一个字不差）。
# 本地引擎也没有「随机性」可调，所以打断循环只剩两样：**挪块的起止点**、**切半各听一遍**。
#   · 挪：只往外扩、不往里切（往里切会丢音频，踩「内容不能丢」）；扩多少不是越大越好
#     （同一块 +120 ms 反而又跑飞了），两档是实测出来的，改它请重新实测。
#   · 切半：在块中点 ±2 秒内最安静的一刻下刀，两半各解一次再拼回来 —— 这是生产线 Gemini
#     适配器的招（那边给主轨用；参考轨已关掉，因为「跑不出来就少一路，远好过整单多等几小时」）。
# 一共最多 3 次（Duner 2026-09-11 定：次数别太多）。三次都不行 → **这一路这一块弃用**：
#   文本置空（分歧册显示 ∅、P3 输入不带这一路、坐标轴换到下一路），原文留在 qc_warnings.json 里。
#   不是丢内容 —— 另外三路对同一块有自己的版本，融合本来就能救；喂一路复读的毒进去才是害。
LADDER = [("nudge", -0.05, 0.05), ("nudge", -0.15, 0.15), ("split", 0.0, 0.0)]


def _decode(rec, x: np.ndarray, a: float, b: float) -> str:
    st = rec.create_stream()
    st.accept_waveform(SR, x[int(a * SR):int(b * SR)])
    rec.decode_stream(st)
    return st.result.text.strip()


def _decode_stamped(rec, x: np.ndarray, a: float, b: float) -> tuple[str, list[tuple[str, float]]]:
    """同 _decode，外加每个 token 的绝对时间（秒）。"""
    st = rec.create_stream()
    st.accept_waveform(SR, x[int(a * SR):int(b * SR)])
    rec.decode_stream(st)
    r = st.result
    return r.text.strip(), [(tok, a + t) for tok, t in zip(r.tokens, r.timestamps)]


def _quietest(x: np.ndarray, t: float, window: float = 2.0, probe: float = 0.2) -> float:
    """在 t ± window 内找能量最低的 probe 秒，返回它的中点（与 diarize._quietest 同一算法）。"""
    lo, hi = max(0.0, t - window), min(len(x) / SR, t + window)
    if hi - lo < probe:
        return t
    step, half = 0.02, probe / 2
    best, best_e = t, float("inf")
    p = lo + half
    while p <= hi - half:
        seg = x[int((p - half) * SR):int((p + half) * SR)]
        e = float(np.mean(np.abs(seg))) if len(seg) else float("inf")
        if e < best_e:
            best_e, best = e, p
        p += step
    return best


def _attempt(rec, x: np.ndarray, blk: Block, step: tuple, span: float) -> str:
    kind, ds, de = step
    a, b = max(0.0, blk.start + ds), min(span, blk.end + de)
    if kind == "split":
        if b - a < 3.0:                                       # 太短的块切不开，这一档等于跳过
            return _decode(rec, x, a, b)
        mid = _quietest(x, (a + b) / 2, window=min(2.0, (b - a) / 4))
        left, right = _decode(rec, x, a, mid), _decode(rec, x, mid, b)
        return (left + right).strip()
    return _decode(rec, x, a, b)


def _qc(text: str, blk: Block, engine_id: str, cb: dict) -> str | None:
    """这一块这一路有没有跑飞。没问题返回 None。"""
    if engine_id not in NONDETERMINISTIC:
        return None                                  # CTC 系逐帧对齐，没这个毛病
    if cb.get("repeat_detect"):
        hit = find_repetition_loop(text, int(cb.get("repeat_threshold", REPEAT_MIN_REPS)))
        if hit:
            return f"复读「{hit[0]}」×{hit[1]}"
    if cb.get("empty_block_retry") and not text and (blk.end - blk.start) >= 2.0:
        return "空块"
    return None


def transcribe(engine_id: str, x: np.ndarray, blocks: list[Block], cfg: dict,
               on_block=None, rec=None) -> tuple[list[Row], list[dict]]:
    """返回（逐块结果, 重试用尽被弃用的块）。

    弃用 = 这一路这一块文本置空。原文（每一次尝试的输出）都记在返回的坏块清单里，
    不删、不改写；只是不再当作一票喂给融合。必须喊出来：静默才是踩红线的那一种。
    """
    eng = cfg["engines"]
    cb = eng.get("circuit_breaker", {})
    retry = min(int(cb.get("max_retry", len(LADDER))), len(LADDER))
    rec = rec or build(engine_id, int(eng.get("num_threads", 2)))     # 横评时可以传一个自己建的识别器进来
    rows: list[Row] = []
    bad: list[dict] = []
    tag = TAG.get(engine_id, engine_id)
    span = len(x) / SR
    for i, blk in enumerate(blocks):
        stamps: list[tuple[str, float]] = []
        if engine_id in STAMPED:
            text, stamps = _decode_stamped(rec, x, blk.start, blk.end)
        else:
            text = _decode(rec, x, blk.start, blk.end)
        why = _qc(text, blk, engine_id, cb)
        tries = [dict(step="原样", why=why, text=text)]
        for step in LADDER[:retry]:
            if not why:
                break
            text = _attempt(rec, x, blk, step, span)
            why = _qc(text, blk, engine_id, cb)
            tries.append(dict(step=step[0] + (f"{step[1]:+.2f}/{step[2]:+.2f}" if step[0] == "nudge" else ""),
                              why=why, text=text))
        if why:
            bad.append(dict(block=i + 1, at=round(blk.start, 1), engine=tag, why=why,
                            attempts=len(tries) - 1, raw=tries[0]["text"], tries=tries))
            print(f"⚠️ {tag} 第 {i + 1} 块 [{ts(blk.start)}] {why}，重试 {len(tries) - 1} 次仍未消除。"
                  f"这一路这一块弃用（原文在 qc_warnings.json），交给另外几路。")
            text = ""
        elif len(tries) > 1:
            print(f"ℹ️ {tag} 第 {i + 1} 块 [{ts(blk.start)}] {tries[0]['why']}，第 {len(tries) - 1} 次重试"
                  f"（{tries[-1]['step']}）救回。")
        rows.append(Row(blk.start, blk.end, blk.speaker, text,
                        stamps if len(tries) == 1 else []))        # 重试过的文字与首次时间对不上，不留
        if on_block:
            on_block(i + 1, len(blocks))
    return rows, bad


def ts(x: float) -> str:
    return f"{int(x) // 60:02d}:{x - (int(x) // 60) * 60:04.1f}"


def dump(rows: list[Row]) -> str:
    return "\n".join(f"[{ts(r.start)} - {ts(r.end)}] SPK{r.speaker}: {r.text}" for r in rows if r.text)

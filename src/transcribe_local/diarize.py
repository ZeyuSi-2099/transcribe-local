# -*- coding: utf-8 -*-
"""说话人分段 + VAD 补漏 + 切块。

这一层是四路 ASR 共用的底盘 —— 四路吃的是同一份分段、同一份切块，
所以第 N 块在四路里是同一段音频、同一个说话人标签。正因如此，后面不需要做多路对齐。

两条实测纪律：
  · 归堆人数写死 2。多给名额不会去找第三人，会去劈主说话人。
  · VAD 不是可选项。声纹分段单用会漏 215 个真字，取并集后只漏 4 个。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx

from .audio import SR
from .models import model_path


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class Block:
    start: float
    end: float
    speaker: int | None


def diarize(x: np.ndarray, cfg: dict) -> list[Segment]:
    d = cfg["diarize"]
    c = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(model_path(d["segmentation"]))
            )
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_path(d["embedding"]))
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=int(d["num_clusters"])),
        min_duration_on=float(d["min_duration_on"]),
        min_duration_off=float(d["min_duration_off"]),
    )
    if not c.validate():
        raise RuntimeError("声纹分段配置无效，检查模型路径与参数")
    r = sherpa_onnx.OfflineSpeakerDiarization(c).process(x).sort_by_start_time()
    return [Segment(float(s.start), float(s.end), int(s.speaker)) for s in r]


def vad_spans(x: np.ndarray, cfg: dict) -> list[tuple[float, float]]:
    """独立判断每一刻有没有人声。声纹分段没认出说话人的地方（重叠、音量低、串音）
    根本不在它的输出里，调门槛也变不出来 —— 只能靠这一路补。"""
    v = cfg["vad"]
    c = sherpa_onnx.VadModelConfig()
    c.silero_vad.model = str(model_path(v["model"]))
    c.silero_vad.min_silence_duration = float(v["min_silence_duration"])
    c.silero_vad.min_speech_duration = float(v["min_speech_duration"])
    c.silero_vad.max_speech_duration = float(v["max_speech_duration"])
    c.sample_rate = SR
    det = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=100)
    out: list[tuple[float, float]] = []

    def drain() -> None:
        while not det.empty():
            seg = det.front
            out.append((seg.start / SR, seg.start / SR + len(seg.samples) / SR))
            det.pop()

    for i in range(0, len(x), 512):
        det.accept_waveform(x[i:i + 512])
        drain()
    det.flush()
    drain()
    return out


def _union(spans: list[tuple[float, float]], gap: float = 0.05) -> list[list[float]]:
    merged: list[list[float]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _quietest(x: np.ndarray, t: float, window: float, probe: float = 0.2) -> float:
    """在 t ± window 内找能量最低的 probe 秒，返回它的中点。"""
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


def _turn_points(segs: list[Segment], s: float, e: float, min_piece: float) -> list[float]:
    """并集段 [s, e] 内的换人点：相邻两段说话人不同，取两段之间的中点。
    离段首尾不足 min_piece 的不要（切出来的碎片太短，附和块会被劈碎、说话人归属反而更差）。"""
    inside = sorted((g for g in segs if g.end > s and g.start < e), key=lambda g: g.start)
    pts = []
    for a, b in zip(inside, inside[1:]):
        if a.speaker == b.speaker:
            continue
        t = (a.end + b.start) / 2
        if t - s >= min_piece and e - t >= min_piece and (not pts or t - pts[-1] >= min_piece):
            pts.append(t)
    return pts


def chop(x: np.ndarray, segs: list[Segment], cfg: dict) -> list[Block]:
    """并集后的长块切成 ASR 吃得下的小块，再给每块贴上说话人。

    strategy:
      even     等分（默认）。
      quietest 等分后把刀口挪到 ±2 秒内最安静的一刻。
      turns    **换人的地方优先下刀**：先按声纹分段里的换人点把并集段切开（碎片至少 min_block 秒），
               每一片再按 max_length 等分。目的是别让主持人的问和受访者的答落在同一块里 ——
               实测一个 15 秒的块里两人混说时，说话人按块贴标签只能对一半。
    """
    ch, dur = cfg["chop"], len(x) / SR
    spans = [(s.start, s.end) for s in segs]
    if cfg["vad"]["enabled"]:
        spans += vad_spans(x, cfg)
    pad, maxlen = float(ch["pad"]), float(ch["max_length"])
    strategy = ch.get("strategy", "even")
    quietest = strategy == "quietest"
    limit = maxlen + 2.0 if quietest else maxlen
    min_block = float(ch.get("min_block", 1.5))

    cuts: list[tuple[float, float]] = []
    for s, e in _union(spans):
        s, e = max(0.0, s - pad), min(dur, e + pad)
        pieces = [(s, e)]
        if strategy == "turns":
            pts = _turn_points(segs, s, e, min_block)
            bounds = [s] + pts + [e]
            pieces = list(zip(bounds[:-1], bounds[1:]))
        for ps, pe in pieces:
            n = max(1, int(np.ceil((pe - ps) / maxlen)))
            edges = [ps + i * (pe - ps) / n for i in range(n + 1)]
            if quietest and n > 1:                      # 刀口挪到窗口里最安静的一刻
                for i in range(1, n):
                    moved = _quietest(x, edges[i], window=2.0)
                    if moved - edges[i - 1] <= limit and edges[i + 1] - moved <= limit:
                        edges[i] = moved
            cuts += list(zip(edges[:-1], edges[1:]))

    blocks = [Block(a, b, _speaker_of(a, b, segs)) for a, b in cuts]
    return _fill_forward(blocks)


def _speaker_of(s: float, e: float, segs: list[Segment]) -> int | None:
    """按重叠时长取归属；重叠不够就退到离中点最近的那一段（1.5 秒内）。"""
    dur = max(e - s, 1e-6)
    best, best_ov = None, 0.0
    for g in segs:
        ov = min(e, g.end) - max(s, g.start)
        if ov > best_ov:
            best_ov, best = ov, g
    if best is not None and best_ov >= min(0.3, dur * 0.2):
        return best.speaker
    mid, near, nearest = (s + e) / 2, 1e9, None
    for g in segs:
        d = 0.0 if g.start <= mid <= g.end else min(abs(mid - g.start), abs(mid - g.end))
        if d < near:
            near, nearest = d, g
    return nearest.speaker if nearest is not None and near <= 1.5 else None


def _fill_forward(blocks: list[Block]) -> list[Block]:
    """贴不到说话人的块沿用上一块的 —— 说话人不明就继承，不留空。"""
    out, last = [], None
    for b in blocks:
        if b.speaker is None:
            out.append(Block(b.start, b.end, last))
        else:
            last = b.speaker
            out.append(b)
    return out


def save_blocks(blocks: list[Block], segs: list[Segment], out_dir: Path) -> None:
    import json
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "blocks.json").write_text(
        json.dumps([[b.start, b.end, b.speaker] for b in blocks]), encoding="utf-8")
    (out_dir / "segments.json").write_text(
        json.dumps([{"start": s.start, "end": s.end, "spk": s.speaker} for s in segs]), encoding="utf-8")

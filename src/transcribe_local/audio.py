# -*- coding: utf-8 -*-
"""P0：任意格式 → 16 kHz 单声道 WAV。唯一的非 pip 依赖是 ffmpeg。"""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 16000


class FFmpegMissing(RuntimeError):
    pass


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def to_wav(src: Path, dst: Path, sample_rate: int = SR, channels: int = 1) -> Path:
    """转成模型要的格式。已经是对的格式就直接用，不白转一遍。"""
    if src.suffix.lower() == ".wav" and _probe_wav(src) == (sample_rate, channels):
        return src
    if not have_ffmpeg():
        raise FFmpegMissing(
            "需要 ffmpeg 来转码。装一个（macOS: brew install ffmpeg），"
            "或者直接喂一个 16 kHz 单声道的 .wav。"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ac", str(channels), "-ar", str(sample_rate), "-vn", str(dst)],
        check=True,
    )
    return dst


def _probe_wav(p: Path) -> tuple[int, int] | None:
    try:
        with wave.open(str(p)) as f:
            return f.getframerate(), f.getnchannels()
    except Exception:
        return None


def load(p: Path) -> np.ndarray:
    """读成 float32 [-1, 1]。"""
    with wave.open(str(p)) as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"只支持 16-bit PCM，这个是 {f.getsampwidth() * 8}-bit：{p}")
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

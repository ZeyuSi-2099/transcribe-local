"""端到端：真实四路 ASR + 融合精修。需真 key、网络与一段短中文音频。
手动触发：在容器内 `pytest -m integration tests/integration -v`。
环境变量 E2E_AUDIO 指向容器内可见的音频路径（如 /data/sample_zh.m4a）。
"""
import os

import pytest

from pipeline.orchestrator import transcribe
from pipeline.transcript import Segment


@pytest.mark.integration
def test_real_chinese_audio_yields_speaker_labeled_transcript():
    audio = os.environ.get("E2E_AUDIO")
    assert audio and os.path.exists(audio), "请设置 E2E_AUDIO 指向短中文音频"

    segs, _ = transcribe(audio)

    assert len(segs) >= 1, "应至少解析出一段转录"
    assert all(isinstance(x, Segment) for x in segs)
    assert all(x.s.strip() for x in segs), "每段应有非空文本"
    # 说话人映射应命中（访谈场景核心价值）
    assert any(x.sp in ("主持人", "被访者", "其他") for x in segs)
    # 时间戳格式 HH:MM:SS
    assert all(len(x.t) == 8 and x.t[2] == ":" and x.t[5] == ":" for x in segs)

# -*- coding: utf-8 -*-
"""复读（Doom Loop）检测：从生产线 test_gemini_repeat_loop.py 搬来的九条 + 本地两条。

锁两件事：① 词法必须存在（只有字符法时，长于 6 字符的「词+空格」复读整个漏掉）；
② 阈值 10，字符法词法统一：9 次不判、10 次判。
"""
import json
from pathlib import Path

import numpy as np
import pytest

from transcribe_local import engines
from transcribe_local.diarize import Block

find = engines.find_repetition_loop


@pytest.mark.parametrize("name,text,want", [
    ("长词 ≥10 次判循环", "Vihollinen! " * 11, True),
    ("长词 9 次不判", "Vihollinen! " * 9, False),
    ("多词短语复读", "ja ja ok " * 11, True),
    ("单字 ≥10 次判循环", "是" * 12, True),
    ("单字 9 次不判", "是" * 9, False),
    ("短串 ≥10 次", " là" * 20, True),
    ("口吃重复不判", "no no no no", False),
    ("正常英文不判", "Hello there how are you doing today my friend", False),
    ("正常中文不判", "对对对，我觉得这个方案挺好的，我们可以试试看", False),
    ("中文双字复读", "然后" + "可能" * 10 + "是这样", True),
])
def test_cases(name, text, want):
    assert (find(text) is not None) == want, name


def test_threshold_is_ten():
    assert engines.REPEAT_MIN_REPS == 10


def test_real_block_179_is_caught():
    """09-11 全长产物里 FireRed 那一块（34:34）。"""
    qc = Path(__file__).resolve().parents[1] / ".run/out/49062920/work/qc_warnings.json"
    if not qc.exists():
        pytest.skip("本机没有那次全长产物")
    raw = json.loads(qc.read_text(encoding="utf-8"))[0]["text"]
    hit = find(raw)
    assert hit and hit[0] == "嗯" and hit[1] >= 10


def test_ladder_drops_route_when_all_attempts_loop(monkeypatch):
    """三次都复读 → 文本置空、坏块清单里留原文与每次尝试。"""
    calls = []

    def fake_decode(rec, x, a, b):
        calls.append((round(a, 2), round(b, 2)))
        return "嗯" * 30

    monkeypatch.setattr(engines, "build", lambda *a, **k: object())
    monkeypatch.setattr(engines, "_decode", fake_decode)
    x = np.zeros(int(60 * engines.SR), dtype=np.float32)
    cfg = {"engines": {"num_threads": 1, "circuit_breaker": {
        "repeat_detect": True, "repeat_threshold": 10, "empty_block_retry": True, "max_retry": 3}}}
    rows, bad = engines.transcribe("firered_asr2", x, [Block(10.0, 25.0, 0)], cfg)
    assert rows[0].text == ""
    assert len(bad) == 1 and bad[0]["attempts"] == 3 and bad[0]["raw"] == "嗯" * 30
    assert [t["step"] for t in bad[0]["tries"]] == ["原样", "nudge-0.05/+0.05", "nudge-0.15/+0.15", "split"]
    assert len(calls) == 1 + 1 + 1 + 2          # 原样 + 两档挪窗 + 切半（两次解码）


def test_ladder_keeps_text_when_retry_recovers(monkeypatch):
    seq = iter(["嗯" * 30, "这是救回来的正常内容"])
    monkeypatch.setattr(engines, "build", lambda *a, **k: object())
    monkeypatch.setattr(engines, "_decode", lambda rec, x, a, b: next(seq))
    x = np.zeros(int(60 * engines.SR), dtype=np.float32)
    cfg = {"engines": {"num_threads": 1, "circuit_breaker": {
        "repeat_detect": True, "repeat_threshold": 10, "empty_block_retry": True, "max_retry": 3}}}
    rows, bad = engines.transcribe("firered_asr2", x, [Block(10.0, 25.0, 0)], cfg)
    assert rows[0].text == "这是救回来的正常内容" and bad == []


def test_ctc_routes_are_not_checked(monkeypatch):
    monkeypatch.setattr(engines, "build", lambda *a, **k: object())
    monkeypatch.setattr(engines, "_decode", lambda rec, x, a, b: "嗯" * 30)
    x = np.zeros(int(60 * engines.SR), dtype=np.float32)
    cfg = {"engines": {"num_threads": 1, "circuit_breaker": {"repeat_detect": True, "max_retry": 3}}}
    rows, bad = engines.transcribe("zipformer_ctc", x, [Block(10.0, 25.0, 0)], cfg)
    assert rows[0].text == "嗯" * 30 and bad == []

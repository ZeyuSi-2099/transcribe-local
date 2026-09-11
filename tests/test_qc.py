# -*- coding: utf-8 -*-
"""P1 质检闸：每一条规则一个最小用例。"""
import numpy as np

from transcribe_local import qc
from transcribe_local.audio import SR
from transcribe_local.diarize import Block
from transcribe_local.engines import Row


def _rows(texts, step=10.0):
    return [Row(i * step, (i + 1) * step, 0, t) for i, t in enumerate(texts)]


def _blocks(n, step=10.0):
    return [Block(i * step, (i + 1) * step, 0) for i in range(n)]


def test_empty_route_fails():
    rows = {"firered_asr2": _rows(["有字"] * 6), "zipformer_ctc": _rows([""] * 6)}
    rep = qc.check(rows, _blocks(6), None)
    assert any("ZIPC" in f and "整路空" in f for f in rep.fail)


def test_short_route_fails_on_median():
    rows = {"firered_asr2": _rows(["十个字十个字十个字十" ] * 6),
            "zipformer_ctc": _rows(["十个字十个字十个字十"] * 6),
            "paraformer_2023": _rows(["一"] * 6)}
    rep = qc.check(rows, _blocks(6), None)
    assert any("PARA" in f and "字数" in f for f in rep.fail)


def test_gap_in_one_route_while_others_speak_fails():
    n = 20                                   # 20 块 × 10 秒；ZIPC 在 3–17 块（140 秒）全空
    z = ["字" * 8] * n
    for i in range(3, 17):
        z[i] = ""
    rows = {"firered_asr2": _rows(["字" * 8] * n), "zipformer_ctc": _rows(z)}
    rep = qc.check(rows, _blocks(n), None)
    assert any("ZIPC" in f and "疑这一路丢段" in f for f in rep.fail)


def test_gap_everyone_silent_is_only_a_warning():
    n = 20
    z = ["字" * 8] * n
    for i in range(3, 17):
        z[i] = ""
    rows = {"firered_asr2": _rows(z), "zipformer_ctc": _rows(z)}
    rep = qc.check(rows, _blocks(n), None)
    assert not any("丢段" in f for f in rep.fail)
    assert any("疑现场静默" in w for w in rep.warn)


def test_tail_with_energy_fails_and_silent_tail_passes():
    # 60 秒有块 + 200 秒尾巴
    blocks = _blocks(6)
    rng = np.random.default_rng(0)
    x = np.zeros(int(260 * SR), dtype=np.float32)
    x[: int(60 * SR)] = rng.normal(0, 0.1, int(60 * SR)).astype(np.float32)   # 人声段
    rows = {"firered_asr2": _rows(["字"] * 6)}
    rep = qc.check(rows, blocks, x)
    assert any("尾部静音放行" in w for w in rep.warn)
    x[int(60 * SR):] = rng.normal(0, 0.1, len(x) - int(60 * SR)).astype(np.float32)
    rep = qc.check(rows, blocks, x)
    assert any("疑漏切" in f for f in rep.fail)


def test_residue_and_breaker_are_reported():
    rows = {"firered_asr2": _rows(["<sil>字", "字<unk>", "字", "字", "字", "字"])}
    bad = [dict(block=3, at=20.0, engine="FRED2", why="复读", text="嗯嗯嗯")]
    rep = qc.check(rows, _blocks(6), None, bad=bad)
    assert rep.stats["residue"]["FRED2"]["count"] == 2
    assert any("复读" in f and "第 3 块" in f for f in rep.fail)
    assert "❌" in qc.dump(rep)


def test_turn_points_split_between_speakers_and_respect_min_piece():
    from transcribe_local.diarize import Segment, _turn_points
    segs = [Segment(0.0, 6.0, 0), Segment(6.2, 12.0, 1), Segment(12.1, 12.8, 0), Segment(12.9, 20.0, 1)]
    pts = _turn_points(segs, 0.0, 20.0, min_piece=1.5)
    assert len(pts) == 2 and 6.0 <= pts[0] <= 6.2 and 12.0 <= pts[1] <= 12.1   # 12.8–12.9 那处离上一刀太近，不下
    assert _turn_points([Segment(0.0, 20.0, 0)], 0.0, 20.0, 1.5) == []

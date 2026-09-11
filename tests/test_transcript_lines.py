# -*- coding: utf-8 -*-
"""稿子一行的解析、空块兜底、分歧册顶格行选法。

这三处以前各写各的正则，遇到 `M[❓]:` 这种模型按提示词写出来的行只是碰巧能过；
四路全空的块还把占位符「/」当成候选写进过终稿（2026-09-11 全长实测，块 21）。
"""
from transcribe_local import divergence, export, fuse
from transcribe_local.engines import Row
from transcribe_local.transcript import MARK, has_words, parse_line


# ── 行解析 ──────────────────────────────────────────────

def test_plain_line():
    ln = parse_line("[00:02.6 - 00:05.1] M: 你好。")
    assert ln and (ln.a, ln.b, ln.spk, ln.text) == ("00:02.6", "00:05.1", "M", "你好。")
    assert not ln.spk_uncertain and ln.spk_base == "M"


def test_speaker_uncertain_line():
    ln = parse_line("[04:00.6 - 04:18.0] M[❓]: 不是特别高。")
    assert ln and ln.spk == "M[❓]" and ln.spk_base == "M" and ln.spk_uncertain
    assert ln.text == "不是特别高。"


def test_text_uncertain_line():
    ln = parse_line("[08:42.4 - 08:52.9] R: 因为汤底在锅里[❓]，嗯。")
    assert ln and MARK in ln.text and has_words(ln.text)


def test_only_mark_is_not_words():
    ln = parse_line("[04:18.2 - 04:20.8] R: [❓]")
    assert ln and not has_words(ln.text)


def test_fullwidth_colon_and_spk_id():
    ln = parse_line("[12:00.0 - 12:03.0] SPK0： 好的")
    assert ln and ln.spk == "SPK0" and ln.text == "好的"


def test_missing_end_time_is_not_a_line():
    assert parse_line("[12:00.0] R: 好的") is None


def test_head_rebuilds_prefix():
    ln = parse_line("[01:00.0 - 01:02.0] R[❓]: x")
    assert ln.head == "[01:00.0 - 01:02.0] R[❓]: "


# ── 空块兜底 ────────────────────────────────────────────

def test_revive_ignores_slash_placeholder():
    """块 21 的原样输入：顶格空、其余三路都是占位符「/」。"""
    src = ("[02:53.6 - 02:54.4] R: \n"
           "                  [ZIPC] /\n"
           "                  [PARA] /\n"
           "                  [QWEN3] /")
    line, had = fuse._revive(src)
    assert not had
    assert line == f"[02:53.6 - 02:54.4] R: {MARK}"
    assert "/" not in line


def test_revive_picks_majority():
    src = ("[00:10.0 - 00:12.0] M: \n"
           "                  [ZIPC] 你好\n"
           "                  [PARA] 你好\n"
           "                  [QWEN3] 您好")
    line, had = fuse._revive(src)
    assert had and line == f"[00:10.0 - 00:12.0] M: 你好 {MARK}"


def test_blank_detects_mark_only_and_keeps_words():
    assert fuse._blank("[00:10.0 - 00:12.0] M: [❓]")
    assert fuse._blank("[00:10.0 - 00:12.0] M[❓]: 。")
    assert not fuse._blank("[00:10.0 - 00:12.0] M[❓]: 嗯[❓]")


# ── 分歧册：谁非空谁顶格 ───────────────────────────────

def _cfg():
    return {"divergence": {"fold_fillers": True, "fillers": "嗯呃啊哦", "strip_tags": True},
            "diarize": {"role_assign": "none"}}


def test_pivot_is_first_nonempty_route():
    rows = {
        "firered_asr2": [Row(0.0, 1.0, 0, ""), Row(1.0, 2.0, 0, "加盐")],
        "zipformer_ctc": [Row(0.0, 1.0, 0, "你好"), Row(1.0, 2.0, 0, "加言")],
        "paraformer_2023": [Row(0.0, 1.0, 0, "你好"), Row(1.0, 2.0, 0, "加盐")],
    }
    body, ledger, found = divergence.build(rows, _cfg())
    top_lines = [ln for ln in body.splitlines() if ln.startswith("[")]
    # 块 1：FRED2 为空，顶格换成 ZIPC 的「你好」，FRED2 落到缩进行显示「/」
    assert top_lines[0].endswith("SPK0: 你好")
    assert "[FRED2] /" in body
    # 块 2：FRED2 非空，照旧顶格
    assert top_lines[1].endswith("SPK0: 加盐")
    # 分歧册里块 2 的差异按顶格顺序列出，FRED2 仍在第一位
    assert found and found[-1].block == 2
    assert found[-1].substantive[0][0][0] == "FRED2"


def test_all_empty_block_has_no_ledger_entry():
    rows = {
        "firered_asr2": [Row(0.0, 0.8, 1, "")],
        "zipformer_ctc": [Row(0.0, 0.8, 1, "")],
    }
    body, _, found = divergence.build(rows, _cfg())
    assert found == []
    assert body.splitlines()[0].endswith("SPK1: ")


# ── 导出解析走同一份 ───────────────────────────────────

def test_export_parse_keeps_speaker_mark():
    rows = export.parse("[00:01.0 - 00:02.0] M[❓]: 哎\n垃圾行\n[00:02.0 - 00:03.0] R: 好")
    assert rows == [("00:01.0", "00:02.0", "M[❓]", "哎"), ("00:02.0", "00:03.0", "R", "好")]

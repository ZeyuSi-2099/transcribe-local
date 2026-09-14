# -*- coding: utf-8 -*-
"""判分尺的行解析：说话人与内容必须分得开，说话人的写法不许漏进内容。"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("score", Path(__file__).resolve().parents[1] / "tools" / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def _p(line):
    m = score.LINE.match(line)
    return (m.group(2) or "", m.group(3)) if m else None


def test_engine_label_is_not_split():
    assert _p("[00:02.6 - 00:13.2] SPK1: 您好哎") == ("SPK1", "您好哎")


def test_single_letter_and_digit_labels():
    assert _p("[00:01] M: 喂，周老师您好。") == ("M", "喂，周老师您好。")
    assert _p("[00:02] 1: 你好。") == ("1", "你好。")


def test_speaker_question_mark_belongs_to_label():
    assert _p("[03:08.2 - 03:16.2] R[❓]: 能有时候") == ("R", "能有时候")


def test_full_width_colon_and_no_label():
    assert _p("[00:01] M：你好") == ("M", "你好")
    assert _p("[00:01] 没有说话人的一行") == ("", "没有说话人的一行")


def test_content_mark_stays_in_content():
    assert _p("[00:01] M: 这个[❓]词") == ("M", "这个[❓]词")

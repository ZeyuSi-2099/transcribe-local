# -*- coding: utf-8 -*-
"""按换人拆块（路 A）：拆点落在换人处、四路同步拆、一个字不丢、不能拆就原样。"""
import re

from transcribe_local import speaker_split
from transcribe_local.diarize import Segment
from transcribe_local.engines import Row

PARA = "paraformer_2023"


def _blk(a, b, spk, texts, stamps):
    return {e: [Row(a, b, spk, t, stamps if e == PARA else [])] for e, t in texts.items()}


def _flat(s):
    return re.sub(r"\s", "", s)


def test_splits_at_turn_and_all_routes_follow():
    segs = [Segment(0.0, 5.0, 0), Segment(5.2, 9.0, 1)]
    para = "甲乙丙丁戊己庚辛壬子丑寅卯"                      # 前 9 字第一个人说，后 4 字第二个人说
    times = [0.5 + 0.5 * i for i in range(9)] + [5.5, 6.0, 6.5, 7.0]
    texts = {PARA: para,
             "firered_asr2": "甲乙，丙丁戊己庚辛壬？子丑，寅卯。",
             "zipformer_ctc": "甲乙丙丁戊已庚辛壬子丑寅卯"}   # 有一个字写法不同，切点仍要对上
    new, st = speaker_split.split(_blk(0.0, 9.0, 0, texts, list(zip(para, times))), segs)
    assert st["split"] == 1 and st["sub"] == 2
    assert [r.speaker for r in new[PARA]] == [0, 1]
    assert new["firered_asr2"][0].text == "甲乙，丙丁戊己庚辛壬？"
    assert new["firered_asr2"][1].text == "子丑，寅卯。"
    assert new["zipformer_ctc"][1].text == "子丑寅卯"
    assert new[PARA][0].end == new[PARA][1].start == 5.0          # 切点前后两个字时间的中点
    for e, t in texts.items():
        assert _flat("".join(r.text for r in new[e])) == _flat(t)     # 内容不能丢


def test_block_without_stamps_is_left_alone():
    texts = {PARA: "", "firered_asr2": "有字"}
    rows = _blk(0.0, 3.0, 1, texts, [])
    new, st = speaker_split.split(rows, [Segment(0.0, 3.0, 0)])
    assert new == rows and st["split"] == 0 and st["no_stamps"] == 1


def test_no_paraformer_route_means_no_split():
    rows = {"firered_asr2": [Row(0.0, 3.0, 0, "有字")]}
    new, st = speaker_split.split(rows, [Segment(0.0, 3.0, 1)])
    assert new is rows and st["reason"]


def test_overlap_goes_to_the_longer_segment():
    segs = [Segment(0.0, 10.0, 0), Segment(4.0, 5.0, 1)]            # 长段里夹一小段重叠
    para = "甲乙丙"
    new, st = speaker_split.split(_blk(3.0, 6.0, 0, {PARA: para}, list(zip(para, [3.5, 4.5, 5.5]))), segs)
    assert st["split"] == 0 and [r.speaker for r in new[PARA]] == [0]


def test_whole_block_relabelled_when_every_char_is_the_other_speaker():
    segs = [Segment(0.0, 1.0, 0), Segment(1.0, 9.0, 1)]
    para = "甲乙丙"
    new, st = speaker_split.split(_blk(0.0, 9.0, 0, {PARA: para}, list(zip(para, [2.0, 3.0, 4.0]))), segs)
    assert st["split"] == 0 and new[PARA][0].speaker == 1 and new[PARA][0].text == para


def test_crowded_cut_is_not_made():
    """切点和块起点落在同一个 0.1 秒里 → 时间码会撞键，不切。"""
    segs = [Segment(5.0, 5.03, 0), Segment(5.03, 10.0, 1)]
    para = "甲乙丙丁"
    new, st = speaker_split.split(_blk(5.0, 10.0, 0, {PARA: para}, list(zip(para, [5.01, 5.08, 6.0, 7.0]))), segs)
    assert st["split"] == 0 and len(new[PARA]) == 1 and new[PARA][0].text == para

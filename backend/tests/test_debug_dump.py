"""P0–P3 中间产物留档的守卫（见 pipeline/debug_dump.py 文件头）。

判据都是「只在规则正确时才成立」的特征：把 mtime 筛选去掉、把相对路径拍平、
或把超限文件静默跳过不报，都会有用例变红（三种改法各造回一次验证过）。
"""
import json
import time
from pathlib import Path

import pytest

from pipeline import debug_dump


def _md(root: Path, rel: str, mtime: float, size: int = 100) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    import os
    os.utime(p, (mtime, mtime))
    return p


def test_只收本单开跑之后写的稿(tmp_path):
    """Output 没人清理，本地回滚态会跨任务累积——上一单的稿不许混进来。"""
    since = time.time()
    _md(tmp_path, "zh_old_P1_ELV_0101_0000.md", since - 3600)   # 上一单
    _md(tmp_path, "zh_new_P1_ELV_0825_1200.md", since + 1)
    files, dropped = debug_dump.collect(since, tmp_path)
    assert [n for n, _ in files] == ["zh_new_P1_ELV_0825_1200.md"]
    assert dropped == []


def test_p3输入副本保留目录层(tmp_path):
    """P3 输入副本与 P2 原稿**同名**（产物名由输入 base 决定），拍平会互相覆盖。"""
    since = time.time()
    _md(tmp_path, "zh_x_P2_Match_0825_1200.md", since + 1, size=50)
    _md(tmp_path, "_p3in/zh_x_P2_Match_0825_1200.md", since + 2, size=80)
    files, _ = debug_dump.collect(since, tmp_path)
    names = sorted(n for n, _ in files)
    assert names == ["_p3in/zh_x_P2_Match_0825_1200.md", "zh_x_P2_Match_0825_1200.md"]
    assert len(set(names)) == 2, "两份稿的键撞车了，留档里会互相覆盖"


def test_收齐各阶段(tmp_path):
    since = time.time()
    for rel in ("zh_a_P1_ELV_1.md", "zh_a_P1_DB_1.md", "zh_a_P1_GEM_1.md",
                "zh_a_P2_Match_1.md", "zh_a_P3_Merge_OPUS_1.md",
                "zh_a_P3_Merge_OPUS_1_report.md", "_p3in/zh_a_P2_Match_1.md"):
        _md(tmp_path, rel, since + 1)
    files, dropped = debug_dump.collect(since, tmp_path)
    assert len(files) == 7 and dropped == []


def test_超限的文件要报出来不许静默跳过(tmp_path, monkeypatch):
    """「没收到」和「本来就没有」长得一样——丢了什么必须说出来。

    上限改小了跑（真值 32MB/128MB 写进临时盘太重）；判据是**行为**不是那两个数字。
    """
    monkeypatch.setattr(debug_dump, "MAX_FILE_MB", 1 / 1024)      # 1 KB
    since = time.time()
    _md(tmp_path, "huge.md", since + 1, size=2048)
    _md(tmp_path, "small.md", since + 2, size=10)
    files, dropped = debug_dump.collect(since, tmp_path)
    assert [n for n, _ in files] == ["small.md"]
    assert len(dropped) == 1 and "huge.md" in dropped[0]


def test_总量封顶也要报(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_dump, "MAX_TOTAL_MB", 3 / 1024)     # 3 KB
    since = time.time()
    for i in range(3):
        _md(tmp_path, f"f{i}.md", since + i, size=1200)
    files, dropped = debug_dump.collect(since, tmp_path)
    assert len(files) == 2, "总量上限没生效"
    assert dropped and "f2.md" in dropped[0], "撞总量上限却没报出来是哪个文件起没留"


def test_目录不存在或异常时返回空不抛(tmp_path):
    """留档是旁路，坏了不许连累转录。"""
    files, dropped = debug_dump.collect(time.time(), tmp_path / "nope")
    assert files == [] and dropped


def test_不收音频(tmp_path):
    """P0 的 FLAC/切片是大头也最敏感，且回放音频已另存 7 天。"""
    since = time.time()
    _md(tmp_path, "zh_a.flac", since + 1)
    _md(tmp_path, "zh_a_Part1.flac", since + 1)
    _md(tmp_path, "zh_a_P1_ELV_1.md", since + 1)
    files, _ = debug_dump.collect(since, tmp_path)
    assert [n for n, _ in files] == ["zh_a_P1_ELV_1.md"]

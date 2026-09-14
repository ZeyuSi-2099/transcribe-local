"""本地转录入口：进度与线上同名同比例；终稿 + 定字报告交给线上的解析，出逐句稿与复核卡。

识别层换成假的（不跑引擎）；定字走真的两阶段分批代码，模型回答用编好的产出回放（不调任何接口）。
例子一律是编的厨房对话。本地独有的测试（线上没有这个文件）。
"""
import re

import pytest

from pipeline import local_orchestrator as lo
from transcribe_local import fuse
from transcribe_local import pipeline as tl_pipeline
from transcribe_local.divergence import Divergence

P3IN = """[00:00.0 - 00:05.0] M: 你好王师傅
                  [ZIPC] 你好王师傅
                  [PARA] 你好王师傅
                  [QWEN3] 你好，王师傅。

[00:05.0 - 00:12.0] R: 我们这边是清汤锅底辣度可以调
                  [ZIPC] 我们这边是青汤锅底拉度可以调
                  [PARA] 我们这边是清汤锅地辣度可以调
                  [QWEN3] 我们这边是清汤锅底，拉度可以调。

[00:13.0 - 00:20.0] M: 明白那通常多长时间出餐
                  [ZIPC] 明白那通常多长时间出餐
                  [PARA] 明白那通常多长时间出参
                  [QWEN3] 明白，那通常多长时间出餐？
"""

TABLE = ("## 实体定字（硬证据）\n| 时间码 | 各轨候选 | 终稿 | 依据 |\n|---|---|---|---|\n"
         "| 00:05.0 | 清汤锅底/青汤锅底 | 清汤锅底 | 术语库 |\n\n## 联网核实\n共 0 次，无联网搜索")
BATCH = ("[00:00.0 - 00:05.0] M: 你好，王师傅。\n"
         "[00:05.0 - 00:12.0] R: 我们这边是清汤锅底，辣度可以[❓]调。\n"
         "[00:13.0 - 00:20.0] M[❓]: 明白，那通常多长时间出[❓]餐？\n"
         "===DOUBT===\n| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n| 00:05.0 | 辣度可以调 | 两路听成拉度 |\n")
TAGS = ["FRED2", "ZIPC", "PARA", "QWEN3"]


def _replay_call(cfg, sysmsg, user, usage, online, max_turns=12):
    usage.add({"prompt_tokens": 1000, "completion_tokens": 100})
    return (TABLE if "本次只做第一步" in user else BATCH), "stop"


@pytest.fixture
def seen(monkeypatch, tmp_path):
    """假识别层：按真实顺序发进度事件，定字走真的 fuse（模型回答回放）。"""
    monkeypatch.setenv("TRANSCRIBE_CONFIG", str(tmp_path / "没有这个文件.yaml"))
    monkeypatch.setattr(fuse, "_call", _replay_call)
    box = {"merged": None}

    def fake_run(audio, out, cfg, *, no_fuse=False, emit=None):
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{audio.stem}.16k.wav").write_bytes(b"RIFF")
        box["cfg"] = cfg
        emit("stage", name="P0", text="转码 · 声纹 · 切块")
        emit("audio", minutes=0.5)
        emit("chop", segments=3, blocks=3, seconds=1)
        for t in TAGS:
            emit("engine_start", engine=t, route="", blocks=3)
            emit("engine_done", engine=t, chars=40, seconds=1, bad=0)
        emit("p1qc", fail=[], warn=[])
        emit("divergence", substantive=1, fillers=0)
        emit("stage", name="P3", text="m @ x")
        found = [Divergence(2, 5.0, "R", [[("FRED2", "清汤"), ("ZIPC", "青汤")]], 0)]
        merged = fuse.fuse(P3IN, found, cfg) if box["merged"] is None else box["merged"]
        return tl_pipeline.Result(out=out, stem=audio.stem, blocks=3, merged=merged, report=fuse.report(cfg))

    monkeypatch.setattr(tl_pipeline, "run", fake_run)
    return box


def _audio(tmp_path):
    p = tmp_path / "input.wav"
    p.write_bytes(b"RIFF")
    return str(p)


def test_phases_and_progress_match_saas(seen, tmp_path):
    calls = []
    lo.transcribe(_audio(tmp_path), on_phase=lambda ph, pr, m: calls.append((ph, pr, m)))
    steps = []
    for ph, pr, _ in calls:                        # 心跳会重发同一格，去掉相邻重复
        if not steps or steps[-1] != (ph, pr):
            steps.append((ph, pr))
    assert steps == [("P0", 5), ("P1", 6), ("P1", 17), ("P1", 28), ("P1", 39), ("P1", 50),
                     ("P2", 50), ("P3", 61), ("P4", 99), ("done", 100)]
    m = calls[-1][2]
    assert set(m["engines"]) == set(TAGS) and all(v["status"] == "done" for v in m["engines"].values())
    assert m["durationSec"] == 30 and m["chars"] == 40 and m["alignedSegs"] == 3
    assert m["finalSegs"] == 3 and m["speakerDoubts"] == 1
    assert m["cost"]["p1"] == {} and m["cost"]["p3"] >= 0


def test_segments_and_review_cards_from_replayed_fuse(seen, tmp_path):
    flacs, reports = [], []
    segs, review = lo.transcribe(_audio(tmp_path), on_flac=flacs.append, on_report=reports.append,
                                 glossary_text="清汤锅底 ｜ 锅底的一种")
    assert [s.t for s in segs] == ["00:00:00", "00:00:05", "00:00:13"]
    assert all("❓" not in s.s for s in segs)                        # 正文标记已剥，存疑靠卡片
    assert segs[1].s == "我们这边是清汤锅底，辣度可以调。"
    assert flacs and flacs[0].endswith("input.16k.wav")
    assert "## 实体定字" in reports[0] and "## 存疑 [❓]" in reports[0]
    assert re.search(r"辣度可以调 \| 两路听成拉度", reports[0])
    # 术语库写成文件交给定字层
    files = seen["cfg"]["terms"]["files"]
    assert len(files) == 1 and "清汤锅底" in open(files[0], encoding="utf-8").read()

    by = {}
    for it in review:
        by.setdefault(it["type"], []).append(it)
    ent = by["entity"][0]
    assert ent["term"] == "清汤锅底" and ent["evidence"]["basis"] and ent["occurrences"][0]["t"] == "00:00:05"
    doubts = by["doubt"]
    assert any(d["term"] == "辣度可以调" and not d.get("unreported") for d in doubts)
    assert any(d.get("unreported") and d["occurrences"][0]["t"] == "00:00:13" for d in doubts)   # 报告漏登记的补卡
    assert by["speaker"]                                               # M[❓] 出说话人卡
    for it in review:                                                  # 线上复核卡的公共字段
        assert {"type", "term", "occurrences"} <= set(it)


def test_open_end_filled_block_still_parses(seen, tmp_path):
    seen["merged"] = "[00:00.0 - 00:05.0] M: 你好，王师傅。\n[00:05.0 - ?] X: [❓]\n"
    segs, review = lo.transcribe(_audio(tmp_path))
    assert len(segs) == 2                                              # 四路皆空那块不许解析丢
    assert any(d.get("unreported") for d in review)


def test_empty_final_fails_and_unsupported_lang_rejected(seen, tmp_path):
    seen["merged"] = "（模型没按格式写）\n"
    with pytest.raises(RuntimeError):
        lo.transcribe(_audio(tmp_path))
    with pytest.raises(ValueError):
        lo.transcribe(_audio(tmp_path), lang="en")

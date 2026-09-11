# -*- coding: utf-8 -*-
"""融合层（与 SaaS 同款的两阶段分批）：切轮、解析产出、归并与补漏、兜底、Claude 路的命令。"""
import json

import pytest

from transcribe_local import fuse
from transcribe_local.divergence import Divergence
from transcribe_local.transcript import MARK

P3IN = """[00:00.0 - 00:05.0] M: 你好王师傅
                  [ZIPC] 你好王师傅
                  [PARA] 你好王师傅
                  [QWEN3] 你好，王师傅。

[00:05.0 - 00:12.0] R: 我们这边是清汤锅底
                  [ZIPC] 我们这边是青汤锅底
                  [PARA] 我们这边是清汤锅地
                  [QWEN3] 我们这边是清汤锅底。

[00:12.0 - 00:13.0] R:
                  [ZIPC] /
                  [PARA] /
                  [QWEN3] /

[00:13.0 - 00:20.0] M: 明白那通常多长时间出餐
                  [ZIPC] 明白那通常多长时间出餐
                  [PARA] 明白那通常多长时间出参
                  [QWEN3] 明白，那通常多长时间出餐？
"""


def _cfg(**over):
    p3 = {"backend": "openai", "base_url": "http://x/v1", "model": "m", "api_key_env": "",
          "round_tokens": 2100, "overlap": 1, "concurrency": 2, "web_search": False,
          "temperature": 0.0, "max_tokens": 1000, "timeout": 5, "max_retry": 0, "extra": {}}
    p3.update(over)
    return {"p3": p3, "terms": {"files": [], "inject_p3": True}}


def test_split_blocks_and_keys():
    blocks = fuse.split_blocks(P3IN)
    assert [b["k"] for b in blocks] == ["00:00.0-00:05.0", "00:05.0-00:12.0", "00:12.0-00:13.0", "00:13.0-00:20.0"]
    assert fuse.key_of("[00:05.0 - 00:12.0] R[❓]: 文本") == "00:05.0-00:12.0"
    assert fuse.key_of("随便一行") is None


def test_plan_rounds_never_splits_a_block_and_merges_tiny_tail():
    blocks = [{"text": "字" * 200, "k": str(i), "sec": i} for i in range(10)]
    spans = fuse.plan_rounds(blocks, round_tokens=int(200 * fuse.TOK_PER_CHAR * 3) + 1, min_last=0)
    assert spans[0] == (0, 0, 3) and spans[-1][2] == 10
    assert all(b - a >= 1 for _, a, b in spans)
    spans2 = fuse.plan_rounds(blocks, round_tokens=int(200 * fuse.TOK_PER_CHAR * 9) + 1, min_last=10 ** 6)
    assert spans2 == [(0, 0, 10)]                                   # 末轮太小并进前一轮


def test_parse_output_keeps_only_transcript_lines_and_doubt_rows():
    text = ("前言不要\n[00:00.0 - 00:05.0] M: 你好，王师傅。\n[00:05.0 - 00:12.0] R: 我们这边是清汤锅底[❓]。\n"
            "===DOUBT===\n| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n| 00:05.0 | 清汤锅底 | 三路同音不同字 |\n")
    got, drows = fuse.parse_output(text)
    assert set(got) == {"00:00.0-00:05.0", "00:05.0-00:12.0"}
    assert drows == ["| 00:05.0 | 清汤锅底 | 三路同音不同字 |"]


def test_finish_prefers_owned_then_spare_then_missing():
    blocks = fuse.split_blocks(P3IN)
    owned = {blocks[0]["k"]: "A", blocks[1]["k"]: "B"}
    spare = {blocks[1]["k"]: "B-spare", blocks[3]["k"]: "D-spare"}
    lines, miss = fuse._finish(blocks, owned, spare)
    assert lines == ["A", "B", None, "D-spare"] and miss == [2]


def test_two_stage_end_to_end_with_mocked_calls(monkeypatch):
    """三块一轮 + 重叠 + 一块丢了走补漏；空块走兜底。"""
    calls = []

    def fake_call(cfg, sysmsg, user, usage, online, max_turns=12):
        calls.append(user)
        if "本次只做第一步" in user:
            return "## 实体定字（硬证据）\n| 00:05.0 | 清汤/青汤/轻汤 | 清汤锅底 | 术语库 |\n## 联网核实\n共 0 次搜索", "stop"
        # 分批：找出要求的范围，按块回，故意漏掉第 4 块、把第 3 块写空
        import re
        i0, i1 = map(int, re.search(r"第 (\d+) 块到第 (\d+) 块", user).groups())
        blocks = fuse.split_blocks(P3IN)
        out = []
        for i in range(i0 - 1, i1):
            b = blocks[i]
            a, z = b["k"].split("-")
            if i == 3 and "分歧册" in user:          # 正常轮里故意丢掉第 4 块；补漏那轮不带分歧册，才给
                continue
            txt = "" if i == 2 else f"融合{i + 1}"
            out.append(f"[{a} - {z}] M: {txt}")
        return "\n".join(out) + "\n===DOUBT===\n无", "stop"

    monkeypatch.setattr(fuse, "_call", fake_call)
    monkeypatch.setattr(fuse, "system_prompt", lambda cfg, online: "SYS")
    cfg = _cfg(round_tokens=int(len(P3IN) * fuse.TOK_PER_CHAR / 2))
    found = [Divergence(2, 5.0, "R", [[("FRED2", "清汤"), ("ZIPC", "青汤"), ("PARA", "轻汤"), ("QWEN3", "清汤")]], 0)]
    out = fuse.fuse(P3IN, found, cfg)
    lines = out.strip().splitlines()
    assert len(lines) == 4
    assert lines[0].endswith("融合1") and lines[1].endswith("融合2")
    assert lines[2] == f"[00:12.0 - 00:13.0] R: {MARK}"            # 四路皆空 → 只留时间码 + [❓]
    assert lines[3].endswith("融合4")                                 # 补漏或重叠救回
    assert any("分歧册" in c for c in calls)                           # 分歧册随批带上
    assert cfg["p3"]["_last"]["rounds"] >= 2


def test_claude_cmd_matches_saas_and_blocks_user_settings():
    cmd = fuse.claude_cmd("/multi-asr-merge x.md", "opus", "medium")
    assert cmd[:5] == ["env", "-u", "ANTHROPIC_API_KEY", "-u", "ANTHROPIC_AUTH_TOKEN"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "opus"
    assert cmd[cmd.index("--effort") + 1] == "medium"
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--setting-sources") + 1] == "project"


@pytest.mark.parametrize("stdout,rc,want", [
    (json.dumps([{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "window": "five_hour"}},
                 {"type": "result", "is_error": True}]), 1, "capped(five_hour)"),
    (json.dumps({"type": "result", "is_error": True, "result": "401 unauthorized"}), 1, "auth"),
    (json.dumps({"type": "result", "is_error": False, "result": "ok"}), 0, "ok"),
    ("not json", 0, "ok"),
])
def test_classify_claude_result(stdout, rc, want):
    assert fuse._classify(stdout, rc) == want


def test_unknown_backend_rejected():
    with pytest.raises(RuntimeError):
        fuse.fuse(P3IN, [], _cfg(backend="nope"))

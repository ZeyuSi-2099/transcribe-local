"""saas_scan：清点归类与敏感扫描的判据。例子一律用与转录无关的领域编的。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import saas_scan as S  # noqa: E402

RULES = [
    {"glob": ["kitchen/secret/*"], "as": "skip"},
    {"glob": ["kitchen/menu.py"], "as": "modify"},
    {"glob": ["kitchen/*"], "as": "same"},
]


def rules_of(hits):
    return [h.rule for h in hits]


def test_classify_first_match_wins_and_unmatched_is_none():
    assert S.classify("kitchen/secret/sauce.md", RULES)["as"] == "skip"
    assert S.classify("kitchen/menu.py", RULES)["as"] == "modify"
    assert S.classify("kitchen/deep/oven.py", RULES)["as"] == "same"     # * 跨目录
    assert S.classify("garden/rose.py", RULES) is None


def test_email_reserved_domains_pass_others_flagged():
    assert S.scan_text("a", "chef@bistro.example 与 x@cook.local 与 a@b.com", []) == []
    assert rules_of(S.scan_text("a", "联系 N1@some-restaurant.cn", [])) == ["邮箱"]


def test_mobile_placeholder_passes_real_shape_flagged():
    assert S.scan_text("a", "示例 13800138000", []) == []
    assert rules_of(S.scan_text("a", "call 13912345678", [])) == ["手机号"]
    assert S.scan_text("a", "version 1.13912345678", []) == []           # 小数不是手机号


def test_transcript_shape_needs_enough_cjk():
    assert S.scan_text("a", "[00:01.0 - 00:02.0] M: 好的", []) == []
    hit = S.scan_text("a", "[00:01.0 - 00:02.0] M: 今天的汤底要先熬足四个小时再下料", [])
    assert rules_of(hit) == ["转录形状"]
    obj = S.scan_text("a", '{ t: "00:00:03", sp: "主厨", s: "今天的汤底要先熬足四个小时再下料" }', [])
    assert rules_of(obj) == ["转录形状"]
    assert S.scan_text("a", '{ t: "00:00:03", s: "好的" }', []) == []


def test_secret_path_and_cloud_host():
    assert "密钥" in rules_of(S.scan_text("a", 'API_KEY = "abcdefghijklmnopqrstuv"', []))
    assert "本机路径" in rules_of(S.scan_text("a", "open('/Users/someone/notes.txt')", []))
    assert "云端地址" in rules_of(S.scan_text("a", "https://menu-api.fly.dev/v1", []))


def test_private_words_case_insensitive_and_allowlist_by_fingerprint(tmp_path):
    hits = S.scan_text("kitchen/menu.py", "# 来自 Project-Saffron 的菜单", ["project-saffron"])
    assert rules_of(hits) == ["私有词表"]
    allow = tmp_path / "allow.txt"
    allow.write_text(hits[0].key + "  # 看过，可以公开\n", encoding="utf-8")
    assert hits[0].key in S.load_allow(allow)
    # 行一改，指纹就变，得重新看
    again = S.scan_text("kitchen/menu.py", "# 来自 Project-Saffron 的新菜单", ["project-saffron"])
    assert again[0].key not in S.load_allow(allow)


def test_manifest_loads_and_every_rule_has_reason():
    for r in S.load_rules():
        assert r.get("why"), r["glob"]


def test_short_ascii_words_match_whole_word_and_case(tmp_path):
    words = tmp_path / "w.txt"
    words.write_text("BBQ\n2000\nSaffron\n藏红花\n", encoding="utf-8")
    ws = S.load_words(str(words))
    assert "2000" not in ws                                             # 纯数字不收
    assert S.scan_text("a", "BBQs = bbq", ws) == []                     # 不是整词、大小写不同
    assert rules_of(S.scan_text("a", "今晚 BBQ 两桌", ws)) == ["私有词表"]
    assert rules_of(S.scan_text("a", "saffron rice", ws)) == ["私有词表"]  # 长词不分大小写
    assert rules_of(S.scan_text("a", "一撮藏红花", ws)) == ["私有词表"]

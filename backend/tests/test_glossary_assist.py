"""术语库协助建库的**解析与护栏**测试。

模型那一跳（真实 DeepSeek 输出质量）测不了也不该在单测里测——这里把 _post 换掉，
盯的是我们自己那段：模型返回什么脏东西时，我们能不能不把它交给前端。
真实端到端（模型真的会给出可用的库吗）只能在有外网的环境里跑，见文件末的说明。
"""
import json

import pytest

from app import glossary_assist as ga


def _fake(monkeypatch, payload):
    """把模型那一跳换成固定返回（payload 可以是 dict 或字符串）。"""
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(ga, "_post", lambda *a, **k: raw)


# ── draft ────────────────────────────────────────────────────────────────

def test_draft_keeps_order_of_categories_and_entries(monkeypatch):
    _fake(monkeypatch, {"items": [
        {"cat": "渠道角色"},
        {"term": "FD", "def": "Fulfillment Distributor，厂商省级直控的履约分销平台"},
        {"cat": "内部行话"},
        {"term": "包销", "def": "承包某批货销售以换取配额（非「报销」）"},
    ]})
    out = ga.draft("大纲")
    assert [list(x)[0] for x in out] == ["cat", "term", "cat", "term"]
    assert out[0]["cat"] == "渠道角色"


def test_draft_strips_full_width_pipe_from_term_and_def(monkeypatch):
    # 全角竖线是行分隔符：漏进术语或释义会让整本库的解析串行
    _fake(monkeypatch, {"items": [{"term": "A｜B", "def": "含义｜带竖线"}]})
    out = ga.draft("大纲")
    assert "｜" not in out[0]["term"]
    assert "｜" not in out[0]["def"]


def test_draft_truncates_over_long_meaning(monkeypatch):
    _fake(monkeypatch, {"items": [{"term": "X", "def": "长" * 400}]})
    out = ga.draft("大纲")
    assert len(out[0]["def"]) == ga.MEANING_MAX


def test_draft_dedupes_terms(monkeypatch):
    _fake(monkeypatch, {"items": [
        {"term": "KA", "def": "重点客户"},
        {"term": "KA", "def": "重复的一条"},
    ]})
    assert len(ga.draft("大纲")) == 1


def test_draft_stops_before_total_char_limit(monkeypatch):
    # 释义先被截到 MEANING_MAX(150)，所以每条 ≈ 3+150+1 = 154 字；
    # 给满 MAX_ENTRIES(60) 条 ≈ 9240 字，必须在 8000 前停——别把注定存不下的稿丢给前端。
    n = ga.MAX_ENTRIES
    _fake(monkeypatch, {"items": [{"term": f"术语{i}", "def": "长" * 200} for i in range(n)]})
    out = ga.draft("大纲")
    total = sum(len(x["term"]) + len(x["def"]) + 1 for x in out if "term" in x)
    assert total <= ga.TOTAL_MAX
    assert len(out) < n            # 确实被上限截断了，而不是碰巧全塞得下


def test_draft_returns_empty_when_only_categories(monkeypatch):
    # 只有分类没有条目 = 等于没起草出来，前端据此显示「这份材料里没有需要收的词」
    _fake(monkeypatch, {"items": [{"cat": "空分类"}]})
    assert ga.draft("大纲") == []


def test_draft_survives_non_json_and_fenced_json(monkeypatch):
    _fake(monkeypatch, "这不是 JSON")
    assert ga.draft("大纲") == []
    _fake(monkeypatch, '```json\n{"items":[{"term":"A","def":"甲"}]}\n```')
    assert ga.draft("大纲") == [{"term": "A", "def": "甲"}]


def test_draft_skips_entries_missing_term_or_def(monkeypatch):
    _fake(monkeypatch, {"items": [
        {"term": "", "def": "没有术语"},
        {"term": "只有术语", "def": ""},
        {"term": "好的", "def": "有释义"},
    ]})
    assert ga.draft("大纲") == [{"term": "好的", "def": "有释义"}]


def test_draft_empty_outline_makes_no_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("空大纲不该调用模型")
    monkeypatch.setattr(ga, "_post", _boom)
    assert ga.draft("   ") == []


def test_draft_propagates_unavailable(monkeypatch):
    def _raise(*a, **k):
        raise ga.AssistUnavailable("key 没配")
    monkeypatch.setattr(ga, "_post", _raise)
    with pytest.raises(ga.AssistUnavailable):
        ga.draft("大纲")


# ── check ────────────────────────────────────────────────────────────────

def test_check_returns_three_buckets(monkeypatch):
    _fake(monkeypatch, {
        "add": [{"term": "窜货", "def": "货品流向非授权区域", "cat": "内部行话"}],
        "edit": [{"term": "FD", "def": "更清楚的释义", "why": "释义太短"}],
        "del": [{"term": "手机", "why": "常规词，不会被听错"}],
    })
    out = ga.check("## 分类\nFD ｜ 短\n手机 ｜ 通信设备")
    assert list(out) == ["add", "edit", "del", "gaps"]
    assert out["add"][0]["term"] == "窜货"
    assert out["edit"][0]["def"] == "更清楚的释义"
    assert out["del"][0]["why"].startswith("常规词")


def test_check_empty_content_makes_no_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("空库不该调用模型")
    monkeypatch.setattr(ga, "_post", _boom)
    assert ga.check("") == {"add": [], "edit": [], "del": [], "gaps": []}


def test_check_drops_rows_without_term(monkeypatch):
    _fake(monkeypatch, {"add": [{"def": "没有 term"}], "edit": [], "del": []})
    assert ga.check("内容")["add"] == []


def test_check_tolerates_wrong_shapes(monkeypatch):
    _fake(monkeypatch, {"add": "不是数组", "edit": None})
    out = ga.check("内容")
    assert out == {"add": [], "edit": [], "del": [], "gaps": []}


def test_check_caps_each_bucket_at_eight(monkeypatch):
    _fake(monkeypatch, {"del": [{"term": f"词{i}", "why": "常规词"} for i in range(20)]})
    lib = "\n".join(f"词{i} ｜ 释义{i}" for i in range(20))
    assert len(ga.check(lib)["del"]) == 8


# ── check 的服务端过滤：模型爱把「不用动」的也列出来，这几条是拦它的 ──
# 2026-08-05 生产实测：18 条建议里 17 条是废的（add 全是库里已有的词，
# edit 全是原样照抄 + why 写「释义清晰，无需修改」）。光靠提示词拦不住。

def test_check_drops_add_of_terms_already_in_library(monkeypatch):
    _fake(monkeypatch, {"add": [{"term": "FD", "def": "重复推荐", "cat": "X"},
                                {"term": "新词", "def": "库里没有", "cat": "X"}]})
    out = ga.check("FD ｜ 已有释义")
    assert [x["term"] for x in out["add"]] == ["新词"]


def test_check_drops_edit_that_does_not_change_anything(monkeypatch):
    _fake(monkeypatch, {"edit": [
        {"term": "FD", "def": "原释义", "why": "释义清晰，无需修改"},   # 一字未改
        {"term": "KA", "def": "改过的释义", "why": "原来太短"},
    ]})
    out = ga.check("FD ｜ 原释义\nKA ｜ 短")
    assert [x["term"] for x in out["edit"]] == ["KA"]


def test_check_drops_edit_and_del_of_terms_not_in_library(monkeypatch):
    _fake(monkeypatch, {"edit": [{"term": "查无此词", "def": "新", "why": "x"}],
                        "del": [{"term": "也没有", "why": "y"}]})
    out = ga.check("FD ｜ 释义")
    assert out["edit"] == [] and out["del"] == []


def test_existing_terms_skips_headings_and_malformed_lines():
    got = ga.existing_terms("## 分类\nFD ｜ 释义\n没有竖线的行\n- KA ｜ 带前缀的行\n\n")
    assert got == {"FD": "释义", "KA": "带前缀的行"}


# ── 流式起草 ─────────────────────────────────────────────────────────────
# 流式是为了消灭「等 100 秒白屏」，所以这里最要紧的是：分块边界不能吃掉条目。
# 真实的流按 token 切，一个对象常被切成好几块，`{`、`｜`、收尾的 `}` 可能各在一块里。

def _scan(chunks):
    sc = ga._ObjScanner()
    return [x for c in chunks for x in sc.feed(c)]


def test_scanner_pulls_objects_across_chunk_boundaries():
    whole = '{"items": [{"cat": "组织"}, {"term": "FD", "def": "释义"}]}'
    # 逐字符喂 = 最坏的分块情况
    got = _scan(list(whole))
    assert got == ['{"cat": "组织"}', '{"term": "FD", "def": "释义"}']


def test_scanner_ignores_braces_and_quotes_inside_strings():
    whole = '{"items": [{"term": "A", "def": "含 { 和 } 和 \\" 引号"}]}'
    got = _scan([whole])
    assert len(got) == 1 and "引号" in got[0]


def test_draft_stream_yields_one_by_one_and_stops_at_entry_cap(monkeypatch):
    items = [{"term": f"词{i}", "def": "释义"} for i in range(ga.MAX_ENTRIES + 5)]
    body = json.dumps({"items": items}, ensure_ascii=False)
    monkeypatch.setattr(ga, "_post_stream", lambda m: iter([body]))
    out = list(ga.draft_stream("大纲"))
    assert len(out) == ga.MAX_ENTRIES
    assert out[0] == {"term": "词0", "def": "释义"}


def test_draft_stream_keeps_terms_that_have_no_definition(monkeypatch):
    """只有名字、没有释义的条目**要留下**——它是给用户的坑，不是畸形数据。

    这是 2026-08-05 的契约变更：上一版把这种条目直接丢掉，用户根本不知道模型
    看见过这个词，而它们往往正是机构内部最要紧、外人猜不到的说法。
    不会污染转录——注入前由 glossary.strip_unfinished 摘掉整行。
    """
    body = '{"items": [{"term": "A", "def": "甲"}, {"term": "B"}, {"term": "C", "def": "丙"}]}'
    monkeypatch.setattr(ga, "_post_stream", lambda m: iter([body]))
    got = list(ga.draft_stream("大纲"))
    assert [x["term"] for x in got] == ["A", "B", "C"]
    assert got[1]["def"] == ""


def test_draft_stream_still_drops_entries_without_a_term(monkeypatch):
    """没有 term 的才是真畸形——照旧丢掉，别把上面那条放宽读成「什么都收」。"""
    body = '{"items": [{"term": "A", "def": "甲"}, {"def": "没有词"}, {"term": "", "def": "空词"}]}'
    monkeypatch.setattr(ga, "_post_stream", lambda m: iter([body]))
    assert [x["term"] for x in ga.draft_stream("大纲")] == ["A"]


def test_draft_stream_emits_gaps_separately(monkeypatch):
    """缺口作为独立项出来，由调用方分表落库——它不进编辑器正文。"""
    body = ('{"items": [{"cat": "角色"}, {"term": "A", "def": "甲"}, '
            '{"gap": "缺分级代号这一类"}]}')
    monkeypatch.setattr(ga, "_post_stream", lambda m: iter([body]))
    got = list(ga.draft_stream("大纲"))
    assert got[-1] == {"gap": "缺分级代号这一类"}
    assert not any("gap" in x for x in got[:-1])


def test_draft_stream_empty_outline_makes_no_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("空大纲不该调用模型")
    monkeypatch.setattr(ga, "_post_stream", _boom)
    assert list(ga.draft_stream("   ")) == []


# ── check 的 SSE 保活路由 ────────────────────────────────────────────────
# 存在的理由是生产 502：普通 JSON 响应要等模型跑完（一百多秒）才发响应头，网关等不及。
# 所以这里要守的是「响应头立刻出来 + 心跳把连接撑住 + 结果最后照常送到」。

def test_check_stream_sends_heartbeats_then_the_result(monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from app import api

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(api, "_current_email", lambda r: "u@example.com")

    def _slow(content, **kw):
        time.sleep(6)                      # 跨过一次 5 秒心跳
        return {"add": [], "edit": [], "del": [{"term": "手机", "why": "常规词"}]}
    monkeypatch.setattr(api.glossary_assist, "check", _slow)

    with TestClient(api.app) as c:
        r = c.post("/api/glossaries/check/stream", json={"content": "手机 ｜ 通信设备"})
        assert r.status_code == 200
        body = r.text
    assert ": ping" in body, "没有心跳＝连接会被网关按空闲掐掉，502 就会回来"
    assert '"term": "\u624b\u673a"' in body or "手机" in body
    assert body.rstrip().endswith("data: [DONE]")


def test_check_stream_reports_engine_failure_inside_the_stream(monkeypatch):
    from fastapi.testclient import TestClient
    from app import api

    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(api, "_current_email", lambda r: "u@example.com")

    def _boom(content, **kw):
        raise ga.AssistUnavailable("引擎挂了")
    monkeypatch.setattr(api.glossary_assist, "check", _boom)

    with TestClient(api.app) as c:
        r = c.post("/api/glossaries/check/stream", json={"content": "手机 ｜ 通信设备"})
    # 响应头早发出去了，改不了状态码 —— 错误只能走流内 error 事件
    assert r.status_code == 200
    assert '"error"' in r.text and "引擎挂了" in r.text


# ── 待办层：注入前必须把「只有名字」的行摘掉 ─────────────────────────────
# 库全文是作为 P3 的硬证据原样注入的。一条没有释义的词条进去，融合脑只会拿到
# 一个不知道怎么办的名字；而这些行在界面上又必须留着（那是给用户的坑）。

from app import glossary as gl   # noqa: E402


def test_strip_unfinished_drops_only_empty_meanings():
    src = "## 分类\nFD ｜ 有释义\n金种子 ｜ \n随手写的注释\n商 ｜"
    got = gl.strip_unfinished(src)
    assert "FD ｜ 有释义" in got
    assert "金种子" not in got and "商 ｜" not in got
    assert "## 分类" in got
    assert "随手写的注释" in got        # 没有竖线 = 本来就不是条目，不归这道过滤管


def test_strip_unfinished_keeps_whitespace_only_meaning_out():
    assert gl.strip_unfinished("X ｜    ").strip() == ""


def test_strip_unfinished_on_empty_input():
    assert gl.strip_unfinished("") == ""
    assert gl.strip_unfinished(None) == ""


def test_check_returns_gaps_as_plain_direction_strings(monkeypatch):
    """检查也产缺口（场景 B：点了检查，右栏落下「还缺这些」）。缺口是方向不是词。"""
    _fake(monkeypatch, {"gaps": ["缺经销层级的内部叫法", "  ", "缺系统名"]})
    out = ga.check("FD ｜ 释义")
    assert out["gaps"] == ["缺经销层级的内部叫法", "缺系统名"]   # 空白条被丢掉


def test_check_truncates_long_gaps(monkeypatch):
    _fake(monkeypatch, {"gaps": ["缺" * 200]})
    assert len(ga.check("FD ｜ 释义")["gaps"][0]) == ga.GAP_MAX


# ── 语言指令（2026-08-30 界面语言一致性第三批）───────────────────────────
# 哪一处漏拼，症状都是「分类名和释义悄悄变回中文」且不报错——只能钉在 prompt 上。


def _capture(monkeypatch, payload):
    """换掉模型那一跳，并录下发给它的 messages。"""
    raw = json.dumps(payload, ensure_ascii=False)
    calls: list = []

    def fake(messages, **k):
        calls.append(messages)
        return raw

    monkeypatch.setattr(ga, "_post", fake)
    return calls


def test_起草把语言指令拼进了system(monkeypatch):
    calls = _capture(monkeypatch, {"items": []})
    ga.draft("大纲", ui_lang="ja")
    sys = calls[0][0]["content"]
    assert "本次的语言" in sys
    assert "日本語" in sys


def test_体检把语言指令拼进了system(monkeypatch):
    calls = _capture(monkeypatch, {"add": [], "edit": [], "del": [], "gaps": []})
    ga.check("FD ｜ 释义", ui_lang="ja")
    sys = calls[0][0]["content"]
    assert "本次的语言" in sys
    assert "日本語" in sys


def test_流式起草与非流式同一份prompt来源():
    # draft_stream 走 _draft_messages；这条钉住「流式那条路也带指令」
    msgs = ga._draft_messages("大纲", "", "de")
    assert "本次的语言" in msgs[0]["content"]
    assert "Deutsch" in msgs[0]["content"]


def test_术语照抄禁令在语言指令里():
    # 术语是拿来在稿子里逐字比对的资产；这句被删掉的症状是术语被翻译、整本库悄悄失效
    note = ga._lang_note("ja")
    assert "绝不翻译" in note
    assert "term" in note


def test_未放量语言回落英文不回落中文():
    assert "一律用 English 书写" in ga._lang_note("ko")
    assert "一律用 English 书写" in ga._lang_note(None)


def test_中文也要发指令(monkeypatch):
    # 「只在非中文时才拼」的话这条路径平时没人跑——八门都发，中文也发
    calls = _capture(monkeypatch, {"items": []})
    ga.draft("大纲", ui_lang="zh")
    assert "简体中文" in calls[0][0]["content"]

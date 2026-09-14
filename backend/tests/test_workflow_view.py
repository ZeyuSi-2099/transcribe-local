"""工作流视图 + 凭证到期。

守的是**一条纪律，不是一堆数值**：workflow_view 的存在意义是「不誊写任何一个数字」，
所以这里逐项断言「接口给的 == 真实常量」。有人哪天图省事把值抄进这个模块（或抄进前端），
这些用例就会红——而如果只断言「等于 5」，抄一份反而照样绿，那种测试等于没写。
"""
import datetime
import hashlib

from app import expiries, workflow_view


def test_params_come_from_real_constants_not_copies():
    from pipeline import merge, orchestrator, pp_deepseek, pp_redact_ds, skill_merge

    from app import config, glossary_assist

    p = workflow_view.snapshot()["params"]
    assert p["p3"]["model"] == skill_merge.CLAUDE_MODEL
    assert p["p3"]["effort"] == skill_merge.CLAUDE_EFFORT
    assert p["p3"]["args"] == merge.P3_ARGS
    assert p["p3"]["script"] == merge.P3_SCRIPT
    assert p["ppFlash"]["model"] == pp_deepseek.MODEL
    assert p["ppFlash"]["narrateBudget"] == pp_deepseek.STEP1_BUDGET
    assert p["ppFlash"]["dropFatal"] == pp_deepseek.DROP_FATAL
    assert p["ppFlash"]["redactBudget"] == pp_redact_ds.BUDGET
    assert p["ppFlash"]["redactPasses"] == pp_redact_ds.PASSES
    assert p["pp"]["concurrency"] == config.PP_CLAUDE_MAX_CONCURRENCY
    assert p["pp"]["listMaxChars"] == config.PP_LIST_MAX_CHARS
    assert "sceneTopN" not in p["p1"], "场景分流已取消，参数块不该再有它"
    assert p["p1"]["rateCnyPerMin"] == orchestrator.RATE_CNY_PER_MIN
    assert p["glossary"]["model"] == glossary_assist.MODEL
    assert p["glossary"]["maxEntries"] == glossary_assist.MAX_ENTRIES


def test_local_script_constants_are_read_from_source():
    """P0/P2 的常量是从脚本源码读的（那两个脚本不适合在 api 进程里 import）。
    读不出来宁可缺这一格——所以这里只断言「读出来了、且是真值」。"""
    p = workflow_view.snapshot()["params"]
    assert p["p0"]["AUDIO_FORMAT"] == "flac"
    assert p["p0"]["TARGET_DURATION_MIN"] > 0
    assert p["p0"]["SILENCE_THRESH_DB"] < 0          # 阈值是负分贝，正数说明解析串了
    assert "zh" in p["p2"]["NO_SPACE_LANGS"]
    assert p["p2"]["MAX_CANDIDATES"] > 0


def test_prompt_fingerprint_matches_the_file_we_serve():
    """名片上的 sha 必须就是全文的 sha。**指纹和内容对不上比没有指纹更糟**——
    它会让人以为核对过了。"""
    snap = workflow_view.snapshot()
    for pid, card in snap["prompts"].items():
        full = workflow_view.prompt_full(pid)
        assert full is not None, pid
        assert full["sha"] == card["sha"], pid
        assert hashlib.sha256(full["text"].encode()).hexdigest().startswith(card["sha"])
        assert full["text"].strip(), pid


def test_prompt_scope_is_declared():
    """指纹取自派单前台这一份，不是 Fly 上跑的那一份。这个口径必须随数据一起出去，
    不能只写在注释里——前端要靠它决定怎么措辞。"""
    assert workflow_view.snapshot()["promptScope"] == "dispatcher"


def test_unknown_prompt_id_is_none_not_empty():
    """未知 id 返回 None（调用方转 404）。**不能回空串**：空文本会被显示成
    「这份提示词是空的」，那是个比 404 难查得多的假象。"""
    assert workflow_view.prompt_full("no-such-skill") is None
    assert workflow_view.prompt_full("") is None


def test_lang_plans_cover_every_shipped_language():
    """27 门语种的编排要全在，且主轨非空——少一门就是那门语言在界面上无从排查。"""
    plans = workflow_view.snapshot()["langPlans"]
    langs = {p["lang"] for p in plans}
    assert len(plans) >= 27
    assert {"zh", "en", "ja", "ar"} <= langs
    assert all(p["primary"] for p in plans)
    zh = next(p for p in plans if p["lang"] == "zh")
    assert zh["primary"] == "ELV"
    assert zh["refs"][:2] == ["DB", "FA"]      # 有序：电话取前 2，现场再加讯飞


def test_expiries_countdown_and_warn_flag():
    items = expiries.list_expiries()
    assert {c["id"] for c in items} == {"paddle_api_key", "claude_oauth_token"}
    today = datetime.datetime.now(datetime.timezone.utc).date()
    for c in items:
        expected = (datetime.date.fromisoformat(c["expires"]) - today).days
        assert c["daysLeft"] == expected
        assert c["warn"] is (expected <= expiries.WARN_DAYS)
        # 后果与修法都得写清楚：待办条上只给「快到期了」而不说会发生什么，人不会动手
        assert c["impact"] and c["fix"]
        assert c["source"] == "manual"          # 没有自动校验，别让人当成系统查证过的事实


def test_expiries_sorted_by_urgency():
    items = expiries.list_expiries()
    days = [c["daysLeft"] for c in items]
    assert days == sorted(days)


def test_bad_date_does_not_hide_the_row(monkeypatch):
    """日期写错一个字，那一行也要照样出现（daysLeft=None）。
    整栏消失比显示「日期有误」危险得多——没人会发现少了一行。"""
    monkeypatch.setattr(expiries, "CREDENTIALS",
                        [{"id": "x", "vendor": "v", "what": "W", "expires": "2027-13-99",
                          "impact": "i", "fix": "f", "source": "manual"}])
    items = expiries.list_expiries()
    assert len(items) == 1
    assert items[0]["daysLeft"] is None
    assert items[0]["warn"] is False

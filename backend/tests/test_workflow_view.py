"""工作流视图 + 凭证到期。

守的是**一条纪律，不是一堆数值**：workflow_view 的存在意义是「不誊写任何一个数字」，
所以这里逐项断言「接口给的 == 真实常量」。有人哪天图省事把值抄进这个模块（或抄进前端），
这些用例就会红——而如果只断言「等于 5」，抄一份反而照样绿，那种测试等于没写。
"""
import datetime
import hashlib

from app import workflow_view

expiries = None   # 本机版：证书与域名到期模块不搬（云端专属）；只有登记为不适用的用例用到它


def test_params_come_from_real_constants_not_copies():
    # 本机版（与线上不同）：本机流水线的参数在识别层配置里（默认配置叠上用户配置），后处理与术语库助手的在代码常量里
    from pipeline import pp_deepseek, pp_redact_ds
    from pipeline.local_orchestrator import config_path, tl_config

    from app import config, glossary_assist

    cfg = tl_config.load(config_path())
    p = workflow_view.snapshot()["params"]
    assert [e["id"] for e in p["p1"]["engines"]] == cfg["engines"]["enabled"]
    assert p["p1"]["repeatThreshold"] == cfg["engines"]["circuit_breaker"]["repeat_threshold"]
    assert p["p0"]["segmentation"] == cfg["diarize"]["segmentation"]
    assert p["p0"]["chopMaxLength"] == cfg["chop"]["max_length"]
    assert p["p2"]["fillers"] == cfg["divergence"]["fillers"]
    assert p["p3"]["roundTokens"] == cfg["p3"]["round_tokens"]
    assert p["p3"]["timeoutSec"] == cfg["p3"]["timeout"]
    assert p["pp"]["narrateBudget"] == pp_deepseek.STEP1_BUDGET
    assert p["pp"]["dropFatal"] == pp_deepseek.DROP_FATAL
    assert p["pp"]["redactBudget"] == pp_redact_ds.BUDGET
    assert p["pp"]["redactPasses"] == pp_redact_ds.PASSES
    assert p["pp"]["listMaxChars"] == config.PP_LIST_MAX_CHARS
    assert p["glossary"]["maxEntries"] == glossary_assist.MAX_ENTRIES


def test_local_script_constants_are_read_from_source(tmp_path, monkeypatch):
    """显示的必须是**实际生效**的值。本机版（与线上不同）：线上这条读 vendor 脚本源码里的常量；
    本机参数在配置里——用户配置改了块长，这里要跟着变，只读默认配置的话改完界面还显示旧值。"""
    user = tmp_path / "config.yaml"
    user.write_text("chop:\n  max_length: 24.0\n", encoding="utf-8")
    monkeypatch.setenv("TRANSCRIBE_CONFIG", str(user))
    p = workflow_view.snapshot()["params"]
    assert p["p0"]["chopMaxLength"] == 24.0
    assert p["p0"]["sampleRate"] == 16000


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

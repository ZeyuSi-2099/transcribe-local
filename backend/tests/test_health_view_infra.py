"""节点健康度聚合（需本地 Postgres）。

**为什么必须是 infra 测试而不是纯单测**：方案二的全部价值就在这几条 JSONB 聚合 SQL 上。
拿假数据 mock 掉查询等于什么都没验——空库不报错完全不能说明 `jsonb_each` 展开对不对、
归一除法有没有除零、按步 unnest 是不是把三步分开了。所以这里造真数据、对真库、验真数字。

跑法（守卫要求显式指向本地，见 conftest）：
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/transcribe \\
  S3_ENDPOINT=http://localhost:9000 python3 -m pytest -m infra tests/test_health_view_infra.py
"""
import json
import uuid

import pytest

from app import db, health_view

pytestmark = pytest.mark.infra


@pytest.fixture()
def clean():
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM postprocess_jobs")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM node_events")
        conn.execute("DELETE FROM login_codes")
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM postprocess_jobs")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM node_events")
        conn.execute("DELETE FROM login_codes")


def _job(metrics: dict, status: str = "done", attempts: int = 1) -> str:
    jid = str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, user_email, status, audio_key, metrics, attempts) "
            "VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
            (jid, "u@x.com", status, f"audio/{jid}", json.dumps(metrics), attempts),
        )
    return jid


def _eng(tag_status: dict, duration_sec: int, secs: dict) -> dict:
    return {"durationSec": duration_sec,
            "engines": {t: {"status": s, "sec": secs.get(t)} for t, s in tag_status.items()}}


def test_p1_counts_reference_track_failures(clean):
    """参考轨挂了是「跳过继续」，整单照样 done —— **这种必须计入该引擎的失败**，
    否则那一路永远显示 100%，正是它悄悄坏掉两周没人发现的原因。"""
    _job(_eng({"ELV": "done", "DB": "done", "XF": "failed"}, 600, {"ELV": 20, "DB": 10, "XF": 5}))
    _job(_eng({"ELV": "done", "DB": "done", "XF": "done"}, 600, {"ELV": 20, "DB": 10, "XF": 30}))
    p1 = health_view.p1_engines(24)
    assert p1["ELV"] == {"total": 2, "ok": 2, "secPerAudioMin": 2.0}
    assert p1["XF"]["total"] == 2 and p1["XF"]["ok"] == 1


def test_p1_ignores_engines_still_running(clean):
    """running = 还没结果。算进分母会让刚开跑的任务把成功率拉下来。"""
    _job(_eng({"ELV": "running", "DB": "done"}, 600, {"DB": 10}), status="running")
    p1 = health_view.p1_engines(24)
    assert "ELV" not in p1
    assert p1["DB"]["total"] == 1


def test_p1_duration_normalised_not_raw_seconds(clean):
    """耗时按音频分钟归一：同一个引擎跑长短两条音频，归一后应当一致——
    不归一的话面板会跟着当天上传的文件长度上下漂。"""
    _job(_eng({"DB": "done"}, 600, {"DB": 20}))     # 10 分钟音频 20 秒 → 2.0
    _job(_eng({"DB": "done"}, 1800, {"DB": 60}))    # 30 分钟音频 60 秒 → 2.0
    assert health_view.p1_engines(24)["DB"]["secPerAudioMin"] == 2.0


def test_p1_survives_zero_duration(clean):
    """时长为 0 / 缺失的老单不能让整条查询炸（除零）——那一条不参与耗时，但仍算成败。"""
    _job({"engines": {"DB": {"status": "done", "sec": 9}}})            # 无 durationSec
    _job(_eng({"DB": "done"}, 0, {"DB": 9}))                            # 时长 0
    p1 = health_view.p1_engines(24)
    assert p1["DB"]["total"] == 2 and p1["DB"]["secPerAudioMin"] is None


def test_p3_degrade_ratio_excludes_unknown(clean):
    """解析不出档位的老单说明不了「降没降级」，不进分子也不进分母——
    猜一个只会污染这个数（同 orchestrator「没有引擎缀就记 unknown，不猜」）。"""
    for eng in ("opus", "opus", "flash", "unknown"):
        _job({"cost": {"p3_engine": eng, "total": 1}})
    p3 = health_view.p3_tiers(24)
    assert p3["total"] == 4 and p3["known"] == 3
    assert p3["degradedRatio"] == pytest.approx(1 / 3)


def test_pp_steps_are_counted_separately(clean):
    """必须按步分开：各步的降级路与失败形态不同，
    汇成一个「后处理成功率」会把最要紧的差别抹掉。"""
    jid = _job({})
    jid2 = _job({})
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO postprocess_jobs (job_id, user_email, steps, status, degraded_steps, ds_cost_cny) "
            "VALUES (%s,%s,%s,'done',%s,%s)",
            (jid, "u@x.com", '["narrate","redact"]', '["redact"]', 1.25),
        )
        conn.execute(
            "INSERT INTO postprocess_jobs (job_id, user_email, steps, status, failed_step) "
            "VALUES (%s,%s,%s,'failed',%s)",
            (jid2, "u@x.com", '["narrate"]', "narrate"),
        )
    pp = health_view.pp_steps(24)
    assert pp["narrate"] == {"total": 2, "ok": 1, "failed": 1, "degraded": 0, "dsCostCny": 0.0}
    assert pp["redact"] == {"total": 1, "ok": 1, "failed": 0, "degraded": 1, "dsCostCny": 1.25}


def test_login_sends_read_from_throttle_window(clean):
    """撞上 Resend 免费档 = 用户登不进，是最严重的故障。这个数取自发码限流表本来就维护着的
    24h 滚动计数窗——**不需要任何新埋点**。"""
    with db.connect() as conn:
        for i, n in enumerate((3, 7)):
            conn.execute(
                "INSERT INTO login_codes (email, code_hash, expires_at, window_start, window_count) "
                "VALUES (%s,'h', now() + interval '10 min', now() - interval '1 hour', %s)",
                (f"u{i}@x.com", n),
            )
    assert health_view._login_sends_24h() == 10


def test_engine_alert_uses_baseline_not_absolute_threshold(clean):
    """各引擎常态成功率本来就不同，同一条绝对线会让一半常亮、另一半永远不响。
    所以异常判定是「相对基线掉了多少」。"""
    now = {"XF": {"total": 20, "ok": 15}}          # 75%
    base = {"XF": {"total": 200, "ok": 198}}       # 99%
    got = health_view._engine_alerts(now, base)
    assert got and got[0]["tag"] == "XF" and got[0]["pct"] == 75 and got[0]["basePct"] == 99


def test_engine_alert_needs_enough_samples(clean):
    """2/3 = 67% 说明不了任何事。样本太少不报——报了只会训练人忽略这条。"""
    assert health_view._engine_alerts({"XF": {"total": 3, "ok": 2}},
                                      {"XF": {"total": 200, "ok": 198}}) == []


def test_watchdog_requeues_counted(clean):
    """单行上早就看得见「已重试 N 次」，但汇总才是信号：一天回收十单说明派单那一层在出问题，
    而那不会体现在成功率上（重跑往往成功）。"""
    _job({}, attempts=1)
    _job({}, attempts=2)
    _job({}, attempts=3)
    assert health_view._watchdog_requeues(24) == 2


# ── 月度口径（2026-08-31，批次 A·H-1 / H-2 / H-4）────────────────────────────

def test_monthly_重跑比例_分母只含已落终态的单(clean):
    """running/queued 还没有结论。算进分母的话，刚上传一批的那一刻重跑率会假性骤降。"""
    _job({}, status="done", attempts=1)
    _job({}, status="done", attempts=3)
    _job({}, status="failed", attempts=2)
    _job({}, status="running", attempts=5)   # ← 不该进分母，也不该进分子
    m = health_view.monthly()
    assert m["retry"] == {"total": 3, "retried": 2, "ratio": pytest.approx(2 / 3)}


def test_monthly_缓存命中率只统计有明细的单(clean):
    """Claude 路不写 p3_tokens。把它按 0 计入会把命中率稀释成一个假数，
    而「稀释」这种错永远不会报错，只会让人对着一个偏低的数做决定。"""
    _job({"cost": {"p3_engine": "flash", "p3_tokens": {"hit": 900, "miss": 100, "out": 50}}})
    _job({"cost": {"p3_engine": "opus"}})                       # Claude 路：没有 token 明细
    m = health_view.monthly()
    assert m["p3Cache"]["jobs"] == 1
    assert m["p3Cache"]["hit"] == 900 and m["p3Cache"]["miss"] == 100
    assert m["p3Cache"]["hitRatio"] == pytest.approx(0.9)


def test_monthly_没有样本时返回_None_而不是零(clean):
    """0% 与「没样本」在界面上是两句完全不同的话。"""
    m = health_view.monthly()
    assert m["retry"]["ratio"] is None
    assert m["p3Cache"]["hitRatio"] is None
    assert m["p3Cache"]["jobs"] == 0


def test_monthly_降级率与_24h_同口径(clean):
    """窗口不同、口径必须相同：unknown 既不进分子也不进分母（同 p3_tiers）。"""
    _job({"cost": {"p3_engine": "opus"}})
    _job({"cost": {"p3_engine": "flash"}})
    _job({"cost": {"p3_engine": "unknown"}})
    m = health_view.monthly()
    assert m["p3"]["known"] == 2
    assert m["p3"]["degradedRatio"] == pytest.approx(0.5)

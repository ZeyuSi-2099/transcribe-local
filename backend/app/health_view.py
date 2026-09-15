"""节点健康度（运营驾驶舱「工作流」Tag 的 ⑤ 近况）+ 全局运行信号。

**核心判断：这多半不是一个埋点工程。** 要看的数字里一大半已经在库里——转录每跑一次，各路
引擎的状态与耗时就随进度写进了 `jobs.metrics`（包括后来失败的那些单），P3 走了哪一档写在
`metrics.cost.p3_engine`，后处理每步的降级与花费写在 `postprocess_jobs`。缺的只是查出来。

这条分界很值钱，有两个原因：① 只改读的这一侧，push 即上线，不用重建任务镜像；
② **聚合上线当天就有历史数据**——近 30 天的引擎成功率立刻可查。新埋点做不到这一点，
它得从零开始攒，上线头一周面板是空的，而那正是最想看的一周。

只有真的没别处可查的（术语库助手）才走 node_events。

**所有查询都吞异常**：查不出来那一格显示「—」，不许让整个节点面板打不开（同 p3_health）。

本机版（与线上不同）：
① 线上这几条统计是 Postgres 的 JSONB 聚合 SQL（jsonb_each / percentile_cont / FILTER）。本机库是
   SQLite，没有这些写法——原样搬来会查询出错、被 _fetch 吞掉，面板恒空，而且不报错。本机单机任务量小，
   改成把那几列取出来在 Python 里聚合，口径与线上逐条相同（中位数同 percentile_cont 的线性插值）。
② 运行信号去掉登录验证码用量、云机器台数、Claude 并发闸：本机不登录、不派云机器、不走 Claude 名额闸。
③ 降级待办不报：线上「降级」= 没用上 Claude 第一档；本机定字用哪个模型是「设置」里选的，没有第一档之说。
"""
import json
from datetime import datetime, timedelta, timezone

from . import db, node_events

# 「近况」默认窗口，和基线窗口。基线用同一份数据的更长窗口算，**不另外维护一份配置**——
# 多一份配置就多一处会过期的东西，而且没人会记得去更新它。
DEFAULT_HOURS = 24
BASELINE_HOURS = 24 * 30

# 引擎成功率低于基线多少个百分点算异常。**不是绝对阈值**：各引擎的常态成功率本来就不同
# （国际轨偶发超时是常态，国产轨几乎不失败），拿同一个绝对线卡会一半常亮一半永远不响。
ENGINE_DROP_PCT = 10.0
ENGINE_MIN_SAMPLES = 8      # 样本太少时不报：2/3 = 67% 说明不了任何事

# 降级率高到多少该提醒。Claude 订阅是第一档，落到 Flash 是质量降一档——
# 一半以上的单都在降级，就该考虑升档订阅或调闸了，而不是等用户来说「这份不如上次」。
DEGRADE_WARN_RATIO = 0.5
DEGRADE_MIN_SAMPLES = 10


def _fetch(sql: str, params: tuple) -> list[tuple]:
    try:
        with db.connect() as conn:
            return conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001  监控查不到不许连累页面
        return []


def _since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _json(v):
    """库里的 JSON 列：metrics 连接层已解析成 dict；steps 这类是文本，这里解析。坏数据当没有。"""
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _job_metrics(hours: int) -> list[tuple[dict, str]]:
    rows = _fetch("SELECT metrics, status FROM jobs WHERE updated_at > %s AND metrics IS NOT NULL",
                  (_since(hours),))
    return [(m, st) for m, st in ((_json(m), st) for m, st in rows) if isinstance(m, dict)]


# ── P1：每路引擎的成败与耗时 ─────────────────────────────────────────────────
# 「近况」里最值钱的一格：**它是唯一能提前发现「某一路悄悄坏了」的地方**。参考轨挂掉是
# 「跳过继续」，整单照样成功出稿，所以从任务列表上完全看不出来（讯飞失败两周没人发现就是
# 这个盲区）。
#
# ⚠️ **耗时必须按音频分钟归一**。一小时的录音当然比十分钟的慢，直接给秒数只会跟着当天上传的
# 文件长度上下漂——今天全是长文件，面板就说「变慢了」。归一之后才是引擎本身的快慢。
def p1_engines(hours: int) -> dict[str, dict]:
    acc: dict[str, dict] = {}
    for m, _ in _job_metrics(hours):
        engines = m.get("engines")
        if not isinstance(engines, dict):
            continue
        dur = _num(m.get("durationSec"))
        for tag, e in engines.items():
            st = (e or {}).get("status")
            # 只算已落终态的引擎：running 是「还没结果」，算进分母会让刚开跑的任务拉低成功率
            if st not in ("done", "failed"):
                continue
            a = acc.setdefault(tag, {"total": 0, "ok": 0, "norm": []})
            a["total"] += 1
            if st == "done":
                a["ok"] += 1
                sec = _num(e.get("sec"))
                if dur and dur > 0 and sec is not None:
                    a["norm"].append(sec / (dur / 60))
    out = {}
    for tag, a in acc.items():
        p50 = node_events.percentile(a["norm"], 0.5)
        out[tag] = {"total": a["total"], "ok": a["ok"],
                    "secPerAudioMin": round(p50, 2) if p50 is not None else None}
    return out


# ── P3：走了哪一档 + 降级率 ──────────────────────────────────────────────────
# 现有的 p3_events 管的是「引擎此刻健不健康」；这里管的是「实际出稿走了哪一档」。
# 两者都要：前者能在没有任务时靠探活看，后者才回答「Claude 额度到底够不够用」。
def p3_tiers(hours: int) -> dict:
    tiers: dict[str, int] = {}
    for m, st in _job_metrics(hours):
        cost = m.get("cost")
        if st != "done" or not isinstance(cost, dict) or "p3_engine" not in cost:
            continue
        eng = cost.get("p3_engine") or "unknown"
        tiers[eng] = tiers.get(eng, 0) + 1
    total = sum(tiers.values())
    # opus = Claude 订阅（第一档）；其余都是降级出的。unknown 不算进分子也不算进分母——
    # 解析不出档位的老单说明不了「降没降级」，猜一个只会污染这个数（同 orchestrator 的原则）
    known = total - tiers.get("unknown", 0)
    degraded = known - tiers.get("opus", 0)
    return {"tiers": tiers, "total": total,
            "degradedRatio": (degraded / known) if known else None,
            "known": known}


# ── 后处理：按步分开看 ──────────────────────────────────────────────────────
# **必须按步分开**：各步的降级路与失败形态不同，汇成一个「后处理成功率」
# 会把最要紧的差别抹掉。
def pp_steps(hours: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    rows = _fetch("SELECT steps, status, failed_step, degraded_steps, ds_cost_cny FROM postprocess_jobs "
                  "WHERE updated_at > %s AND status IN ('done', 'failed')", (_since(hours),))
    for steps, status, failed_step, degraded_steps, cost in rows:
        degraded = _json(degraded_steps) or []
        for step in _json(steps) or []:
            s = out.setdefault(step, {"total": 0, "ok": 0, "failed": 0, "degraded": 0, "dsCostCny": 0.0})
            s["total"] += 1
            s["ok"] += status == "done"
            s["failed"] += failed_step == step
            if step in degraded:
                s["degraded"] += 1
                s["dsCostCny"] += _num(cost) or 0.0
    for s in out.values():
        s["dsCostCny"] = round(s["dsCostCny"], 2)
    return out


# ── 全局运行信号 ────────────────────────────────────────────────────────────
# 这一组不属于任何单个节点，但它们说明「整套东西现在健不健康」，而此前一个都没地方看。


def _watchdog_requeues(hours: int) -> int | None:
    """近 N 小时被看门狗回收重跑过的任务数（attempts > 1）。

    单行上早就看得见「已重试 N 次」，但**汇总才是信号**：一天回收一单是偶发，
    一天回收十单说明派单或机器那一层在出问题，而那不会体现在成功率上（重跑往往成功）。"""
    rows = _fetch(
        "SELECT count(*) FROM jobs WHERE updated_at > %s AND attempts > 1", (_since(hours),))
    return int(rows[0][0]) if rows else None


# ── 月度口径：三个「一天看不出来、一个月才看得出来」的数 ──────────────────────
# 上面那些都是 24 小时窗，回答「现在健不健康」。下面这三个回答的是**成本与稳定性的趋势**，
# 24 小时的样本量根本撑不住：一天十几单，重跑一单就是 8%，看着像着火了。
MONTH_DAYS = 30

# H-1 重跑比例。分母只取**已落终态**的单：running/queued 还没有结论，算进去会让
# 刚上传一批的时刻显示成「重跑率骤降」。
_RETRY_SQL = """
SELECT count(*), coalesce(sum(CASE WHEN attempts > 1 THEN 1 ELSE 0 END), 0)
FROM jobs WHERE updated_at > %s AND status IN ('done', 'failed')
"""


def monthly(days: int = MONTH_DAYS) -> dict:
    """近 N 天的成本/稳定性趋势。每块独立取，坏一块不影响其余（同本模块其余查询）。"""
    since = _since(days * 24)
    rows = _fetch(_RETRY_SQL, (since,))
    total, retried = (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)
    # H-2 的读出侧：P3 缓存命中率。⚠️ 只统计**有这个键**的单：不写 p3_tokens 的路按 0 计入会把命中率稀释成假数。
    hit = miss = out = n_tok = 0
    for m, _ in _job_metrics(days * 24):
        tok = (m.get("cost") or {}).get("p3_tokens") if isinstance(m.get("cost"), dict) else None
        if not isinstance(tok, dict):
            continue
        n_tok += 1
        hit += int(_num(tok.get("hit")) or 0)
        miss += int(_num(tok.get("miss")) or 0)
        out += int(_num(tok.get("out")) or 0)
    p3 = p3_tiers(days * 24)
    return {
        "days": days,
        # H-1：本月多少比例的单跑了不止一次
        "retry": {"total": total, "retried": retried,
                  "ratio": (retried / total) if total else None},
        # H-4：本月多少比例的单降级出稿（口径与 24h 那份完全一致，只是窗口不同）
        "p3": {"tiers": p3["tiers"], "known": p3["known"], "degradedRatio": p3["degradedRatio"]},
        # H-2：缓存命中率。样本数一并给出——0 单的时候前端要显示「没样本」而不是 0%
        "p3Cache": {"jobs": n_tok, "hit": hit, "miss": miss, "out": out,
                    "hitRatio": (hit / (hit + miss)) if (hit + miss) else None},
    }


def ops_signals(hours: int) -> dict:
    from . import jobstore
    try:
        done60, failed60 = jobstore.recent_failure_stats(60)
    except Exception:  # noqa: BLE001
        done60 = failed60 = None
    return {
        "watchdogRequeues": _watchdog_requeues(hours),
        "recent60": {"done": done60, "failed": failed60},
    }


# ── 汇总 + 异常判定 ─────────────────────────────────────────────────────────


def _engine_alerts(now: dict[str, dict], base: dict[str, dict]) -> list[dict]:
    """成功率相对基线掉了多少。**用基线而不是绝对阈值**：各引擎常态成功率本来就不同，
    同一条绝对线会让一半常亮、另一半永远不响。样本太少不报——2/3 说明不了任何事。"""
    out = []
    for tag, cur in now.items():
        if cur["total"] < ENGINE_MIN_SAMPLES:
            continue
        b = base.get(tag)
        if not b or b["total"] < ENGINE_MIN_SAMPLES:
            continue
        cur_pct = cur["ok"] / cur["total"] * 100
        base_pct = b["ok"] / b["total"] * 100
        if base_pct - cur_pct >= ENGINE_DROP_PCT:
            out.append({"tag": tag, "pct": round(cur_pct), "basePct": round(base_pct),
                        "ok": cur["ok"], "total": cur["total"]})
    return out


def snapshot(hours: int = DEFAULT_HOURS) -> dict:
    """节点健康度全量。每一块独立取，坏一块不影响其余。"""
    p1 = p1_engines(hours)
    p1_base = p1_engines(BASELINE_HOURS)
    return {
        "windowHours": hours,
        "baselineHours": BASELINE_HOURS,
        "p1": p1,
        "p1Baseline": {k: {"pct": round(v["ok"] / v["total"] * 100) if v["total"] else None,
                           "total": v["total"]} for k, v in p1_base.items()},
        "p3": p3_tiers(hours),
        "pp": pp_steps(hours),
        "glossary": node_events.stats(["glossary_draft", "glossary_check"], hours),
        "ops": ops_signals(hours),
        # 月度趋势（重跑率 / P3 缓存命中率）。**与上面的 24h 窗并存不是重复**：
        # 24h 回答「现在健不健康」，30 天回答「成本与稳定性在往哪个方向走」。
        "monthly": monthly(),
        # 待办条要的在这里算好，前端不重算一遍（同一套规则算两遍必然漂）
        "alerts": {
            "engines": _engine_alerts(p1, p1_base),
            "degrade": None,   # 本机版：定字模型是设置里选的，没有「第一档 / 降级」之分（见模块头 ③）
        },
    }

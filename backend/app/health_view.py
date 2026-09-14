"""节点健康度（运营驾驶舱「工作流」Tag 的 ⑤ 近况）+ 全局运行信号。

**核心判断：这多半不是一个埋点工程。** 要看的数字里一大半已经在库里——转录每跑一次，各路
引擎的状态与耗时就随进度写进了 `jobs.metrics`（包括后来失败的那些单），P3 走了哪一档写在
`metrics.cost.p3_engine`，后处理每步的降级与花费写在 `postprocess_jobs`。缺的只是查出来。

这条分界很值钱，有两个原因：① 只改读的这一侧，push 即上线，不用重建任务镜像；
② **聚合上线当天就有历史数据**——近 30 天的引擎成功率立刻可查。新埋点做不到这一点，
它得从零开始攒，上线头一周面板是空的，而那正是最想看的一周。

只有真的没别处可查的（术语库助手）才走 node_events。

**所有查询都吞异常**：查不出来那一格显示「—」，不许让整个节点面板打不开（同 p3_health）。
"""
from datetime import datetime, timedelta, timezone

from . import claude_gate, config, db, node_events

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


# ── P1：每路引擎的成败与耗时 ─────────────────────────────────────────────────
# 「近况」里最值钱的一格：**它是唯一能提前发现「某一路悄悄坏了」的地方**。参考轨挂掉是
# 「跳过继续」，整单照样成功出稿，所以从任务列表上完全看不出来（讯飞失败两周没人发现就是
# 这个盲区）。
#
# ⚠️ **耗时必须按音频分钟归一**。一小时的录音当然比十分钟的慢，直接给秒数只会跟着当天上传的
# 文件长度上下漂——今天全是长文件，面板就说「变慢了」。归一之后才是引擎本身的快慢。
_P1_SQL = """
SELECT k, count(*), count(*) FILTER (WHERE st = 'done'),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY norm)
         FILTER (WHERE st = 'done' AND norm IS NOT NULL)
FROM (
  SELECT e.key AS k,
         e.value->>'status' AS st,
         CASE WHEN (j.metrics->>'durationSec')::float > 0
                   AND (e.value->>'sec') IS NOT NULL
              THEN (e.value->>'sec')::float / ((j.metrics->>'durationSec')::float / 60)
         END AS norm
  FROM jobs j, jsonb_each(j.metrics->'engines') e
  WHERE j.updated_at > %s
    AND j.metrics ? 'engines'
    -- 只算已落终态的引擎：running 是「还没结果」，算进分母会让刚开跑的任务拉低成功率
    AND e.value->>'status' IN ('done', 'failed')
) t
GROUP BY k
"""


def p1_engines(hours: int) -> dict[str, dict]:
    out = {}
    for tag, total, ok, p50 in _fetch(_P1_SQL, (_since(hours),)):
        out[tag] = {"total": int(total), "ok": int(ok),
                    "secPerAudioMin": round(float(p50), 2) if p50 is not None else None}
    return out


# ── P3：走了哪一档 + 降级率 ──────────────────────────────────────────────────
# 现有的 p3_events 管的是「引擎此刻健不健康」；这里管的是「实际出稿走了哪一档」。
# 两者都要：前者能在没有任务时靠探活看，后者才回答「Claude 额度到底够不够用」。
_P3_SQL = """
SELECT metrics->'cost'->>'p3_engine' AS eng, count(*)
FROM jobs
WHERE updated_at > %s AND status = 'done' AND metrics->'cost' ? 'p3_engine'
GROUP BY eng
"""


def p3_tiers(hours: int) -> dict:
    tiers = {(e or "unknown"): int(n) for e, n in _fetch(_P3_SQL, (_since(hours),))}
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
_PP_SQL = """
SELECT s.step, count(*),
       count(*) FILTER (WHERE p.status = 'done'),
       count(*) FILTER (WHERE p.failed_step = s.step),
       count(*) FILTER (WHERE p.degraded_steps IS NOT NULL
                          AND p.degraded_steps::jsonb ? s.step),
       coalesce(sum(p.ds_cost_cny) FILTER (WHERE p.degraded_steps IS NOT NULL
                                             AND p.degraded_steps::jsonb ? s.step), 0)
FROM postprocess_jobs p, jsonb_array_elements_text(p.steps::jsonb) s(step)
WHERE p.updated_at > %s AND p.status IN ('done', 'failed')
GROUP BY s.step
"""


def pp_steps(hours: int) -> dict[str, dict]:
    out = {}
    for step, total, ok, failed, degraded, cost in _fetch(_PP_SQL, (_since(hours),)):
        out[step] = {"total": int(total), "ok": int(ok), "failed": int(failed),
                     "degraded": int(degraded), "dsCostCny": round(float(cost), 2)}
    return out


# ── 全局运行信号 ────────────────────────────────────────────────────────────
# 这一组不属于任何单个节点，但它们说明「整套东西现在健不健康」，而此前一个都没地方看。


def _login_sends_24h() -> int | None:
    """近 24 小时发出的登录验证码条数。

    **为什么这是最该看的一个数**：Resend 免费档 100 封/天，撞上限 = 新用户和登出的老用户
    都进不来——这是所有故障里最严重的一种，而它此前零预警。
    数字取自发码限流表的 24h 滚动计数窗（本来就为限流维护着），不需要任何新埋点。
    """
    rows = _fetch(
        "SELECT coalesce(sum(window_count), 0) FROM login_codes "
        "WHERE window_start > now() - interval '24 hours'", (),
    )
    return int(rows[0][0]) if rows else None


def _watchdog_requeues(hours: int) -> int | None:
    """近 N 小时被看门狗回收重跑过的任务数（attempts > 1）。

    单行上早就看得见「已重试 N 次」，但**汇总才是信号**：一天回收一单是偶发，
    一天回收十单说明派单或机器那一层在出问题，而那不会体现在成功率上（重跑往往成功）。"""
    rows = _fetch(
        "SELECT count(*) FROM jobs WHERE updated_at > %s AND attempts > 1", (_since(hours),))
    return int(rows[0][0]) if rows else None


def _gate_slots() -> dict[str, int | None]:
    """四把闸各自的在飞数。此前只显示了 claude 那一把，后处理三把看不到——
    而闸满的直接后果就是「用户在等」。"""
    out: dict[str, int | None] = {}
    for eng in ("claude", "pp_narrate", "pp_redact"):
        try:
            out[eng] = claude_gate.active_count(eng)
        except Exception:  # noqa: BLE001
            out[eng] = None
    return out


_machines_cache: dict = {"at": 0.0, "n": None}
_MACHINE_TTL_SEC = 30


def _machines() -> int | None:
    """当前在飞的任务机器数。**要打 Fly API，所以带 30 秒缓存**——这个数会被 30 秒轮询的
    待办条和资源页同时要，不缓存就是每分钟几次外部调用，纯属浪费。取不到返回 None。"""
    import time
    now = time.time()
    if now - _machines_cache["at"] < _MACHINE_TTL_SEC:
        return _machines_cache["n"]
    try:
        from . import fly_machines
        n = fly_machines.count_running_machines()
    except Exception:  # noqa: BLE001  Fly 查不到不该让整页出不来
        n = None
    _machines_cache.update(at=now, n=n)
    return n


# ── 月度口径：三个「一天看不出来、一个月才看得出来」的数 ──────────────────────
# 上面那些都是 24 小时窗，回答「现在健不健康」。下面这三个回答的是**成本与稳定性的趋势**，
# 24 小时的样本量根本撑不住：一天十几单，重跑一单就是 8%，看着像着火了。
MONTH_DAYS = 30

# H-1 重跑比例。分母只取**已落终态**的单：running/queued 还没有结论，算进去会让
# 刚上传一批的时刻显示成「重跑率骤降」。
_RETRY_SQL = """
SELECT count(*), count(*) FILTER (WHERE attempts > 1)
FROM jobs WHERE updated_at > %s AND status IN ('done', 'failed')
"""

# H-2 的读出侧：P3 缓存命中率。分批版「全文常驻 + 每轮只出一小段」这套设计赌的就是
# 命中率高，而 2026-08-17 之后命中与未命中的单价差 20–30 倍（ds_pricing.PRICE_CNY）。
# ⚠️ 只统计**有这个键**的单：Claude 路不写 p3_tokens，把它按 0 计入会把命中率稀释成假数。
_P3_TOKENS_SQL = """
SELECT coalesce(sum((metrics->'cost'->'p3_tokens'->>'hit')::bigint), 0),
       coalesce(sum((metrics->'cost'->'p3_tokens'->>'miss')::bigint), 0),
       coalesce(sum((metrics->'cost'->'p3_tokens'->>'out')::bigint), 0),
       count(*)
FROM jobs
WHERE updated_at > %s AND metrics->'cost' ? 'p3_tokens'
"""


def monthly(days: int = MONTH_DAYS) -> dict:
    """近 N 天的成本/稳定性趋势。每块独立取，坏一块不影响其余（同本模块其余查询）。"""
    since = _since(days * 24)
    rows = _fetch(_RETRY_SQL, (since,))
    total, retried = (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)
    tok = _fetch(_P3_TOKENS_SQL, (since,))
    hit, miss, out, n_tok = (int(x) for x in tok[0]) if tok else (0, 0, 0, 0)
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
        "loginSends24h": _login_sends_24h(),
        "loginSendCapHint": 100,     # Resend 免费档；不是我们设的闸，是供应商的
        "watchdogRequeues": _watchdog_requeues(hours),
        "gateSlots": _gate_slots(),
        "gateLimit": config.PP_CLAUDE_MAX_CONCURRENCY,
        "machinesRunning": _machines(),
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
    p3 = p3_tiers(hours)
    degraded = p3.get("degradedRatio")
    return {
        "windowHours": hours,
        "baselineHours": BASELINE_HOURS,
        "p1": p1,
        "p1Baseline": {k: {"pct": round(v["ok"] / v["total"] * 100) if v["total"] else None,
                           "total": v["total"]} for k, v in p1_base.items()},
        "p3": p3,
        "pp": pp_steps(hours),
        "glossary": node_events.stats(["glossary_draft", "glossary_check"], hours),
        # P0 转码 / P2 对齐：唯一两段没有别处可查的阶段（不写 metrics、不进事件表）。
        # ⚠️ 埋点跑在**任务机器**上，push 只重部署派单前台——重建 Fly 镜像之前这里恒为空。
        # 前端据此分辨「窗口内没样本」和「代码还没上机器」，别让运营对着空格子猜。
        "phases": node_events.stats(["p0", "p2"], hours),
        "ops": ops_signals(hours),
        # 月度趋势（重跑率 / 降级率 / P3 缓存命中率）。**与上面的 24h 窗并存不是重复**：
        # 24h 回答「现在健不健康」，30 天回答「成本与稳定性在往哪个方向走」。
        "monthly": monthly(),
        # 待办条要的两条：都在这里算好，前端不重算一遍（同一套规则算两遍必然漂）
        "alerts": {
            "engines": _engine_alerts(p1, p1_base),
            "degrade": ({"ratio": degraded, "known": p3["known"]}
                        if degraded is not None and p3["known"] >= DEGRADE_MIN_SAMPLES
                        and degraded >= DEGRADE_WARN_RATIO else None),
        },
    }

"""P3 引擎（Claude 无头模式）健康度：埋点 + 统计 + 跨机撞顶冷却。

**为什么需要它**：撞顶/降级的计数原本只活在 skill_merge 的进程内存里，而线上一台 Fly 机器
只跑一个任务、跑完自毁 —— 数字随机器蒸发，运营侧无从判断 Claude 订阅到底还好不好。
每次 P3 落一行 p3_events，运营舱据此出「近 24h 成功率 / 上次撞顶 / 当前是否冷却中」。

**看得到什么、看不到什么**：线上是令牌模式（CLAUDE_CODE_OAUTH_TOKEN），`claude -p` 不吐
rate_limit_event（T7.1 实测），所以拿不到「额度剩余百分比」，也拿不到恢复时刻——
能拿到的只有每次调用的成败。健康度因此是**统计口径**（最近这些次里成功了几次），
不是油表。这是订阅模式的硬约束，不是本模块的偷懒。

**冷却 cap_state() 是第二个用途，也是最需要小心的地方**：进程内老逻辑里 resets_at 为空
等于「本进程内永久撞顶」，Fly 机器短命所以无害；同一句话挪进 DB 会变成「一次撞顶永久
禁用 Claude」。故拿不到 resets_at 时一律按 撞顶时刻 + config 的兜底窗自动解除。
"""
from datetime import datetime, timedelta, timezone

from . import config, db

# 只有这几种结果算「Claude 本身出了状况」。preempt（读冷却直接跳过）与 concurrency（并发闸满）
# 是我们自己的限流，不是订阅不健康 —— 统计与冷却判断都必须把它们摘出去，否则冷却期内
# 每条 preempt 都会被当成新证据，冷却自我延长成永不解除。
_CAP = "capped"
_BAD = ("capped", "error", "timeout", "auth")
# 真打到引擎的结果（与 preempt/concurrency 相对）——成功率的分母，也是「当前是否认证失效」的判据
_ATTEMPTED = ("ok",) + _BAD
_MAX_NOTE = 300


def record(outcome: str, *, source: str = "job", job_id: str | None = None,
           window: str | None = None, resets_at: float | None = None,
           note: str | None = None) -> None:
    """记一次 P3 结果。埋点失败绝不能拖垮转录 —— 调用方（skill_merge 注入的 gate）已包住异常。

    resets_at 传 epoch 秒（claude -p 的 resetsAt 就是这个形态），拿不到传 None。"""
    ts = datetime.fromtimestamp(resets_at, tz=timezone.utc) if resets_at else None
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO p3_events (source, job_id, outcome, window_kind, resets_at, note) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (source, job_id, outcome, window, ts, (note or "")[:_MAX_NOTE] or None),
        )


def _cooldown_min(window: str | None) -> int:
    return (config.CLAUDE_CAP_WEEK_COOLDOWN_MIN if window == "seven_day"
            else config.CLAUDE_CAP_COOLDOWN_MIN)


def cap_state() -> dict | None:
    """当前是否处于撞顶冷却中。返回 {reason, window, until} 或 None（可以走 Claude）。

    只看**最近一条撞顶事件**：它若已过期，更早的必然也过期；它若在冷却中，就是当前状态。
    until = resets_at（拿得到时）或 撞顶时刻 + 兜底窗。

    撞顶之后只要有过一次成功，冷却立即解除 —— 额度恢复最硬的证据就是「真的调通了」。
    冷却期内生产一律跳过 Claude、不会产生成功事件，所以这条实际上是**手动探活的出口**：
    点一次探活若通了，不必干等兜底窗走完。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT at, window_kind, resets_at FROM p3_events "
            "WHERE outcome = %s ORDER BY at DESC LIMIT 1", (_CAP,),
        ).fetchone()
        if row is None:
            return None
        recovered = conn.execute(
            "SELECT 1 FROM p3_events WHERE outcome = 'ok' AND at > %s LIMIT 1", (row[0],),
        ).fetchone()
    if recovered is not None:
        return None
    at, window, resets_at = row[0], row[1], row[2]
    until = resets_at or (at + timedelta(minutes=_cooldown_min(window)))
    if datetime.now(timezone.utc) >= until:
        return None
    return {"reason": "cap_week" if window == "seven_day" else "cap_5h",
            "window": window, "until": until}


def health(hours: int = 24) -> dict:
    """运营舱 P3 健康卡的数据。

    successRate 的分母**只含真正打到 Claude 的调用**（ok + capped + error + timeout）——
    preempt/concurrency 是我们主动没打，算进去会把「限流生效」显示成「Claude 变差了」。
    真打的次数为 0 时 successRate 返回 None，前端显示「近 N 小时没有样本」而不是 0%。"""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT outcome, count(*) FROM p3_events WHERE at > %s GROUP BY outcome", (since,),
        ).fetchall()
        # last 带 job_id：运营舱靠它认领自己刚发起的那次探活（比对时刻要跨客户端/服务器
        # 两个时钟，差几秒就会误判成「结果还没回来」或「拿到了上一次的结果」）
        last = conn.execute(
            "SELECT at, source, outcome, window_kind, note, job_id FROM p3_events "
            "ORDER BY at DESC LIMIT 1"
        ).fetchone()
        last_ok = conn.execute(
            "SELECT at FROM p3_events WHERE outcome = 'ok' ORDER BY at DESC LIMIT 1"
        ).fetchone()
        last_cap = conn.execute(
            "SELECT at, window_kind FROM p3_events WHERE outcome = %s ORDER BY at DESC LIMIT 1",
            (_CAP,),
        ).fetchone()
        # 「此刻是否认证失效」只看最后一次真打到引擎的结果：认证坏了不会自愈，会一直是 auth；
        # 人一修好，下一单成功就自动翻篇 —— 不需要任何手工清除动作。
        # 时间不设窗（不加 since）：三天没任务也不代表令牌就修好了。
        last_att = conn.execute(
            "SELECT outcome, at, note FROM p3_events WHERE outcome = ANY(%s) ORDER BY at DESC LIMIT 1",
            (list(_ATTEMPTED),),
        ).fetchone()
    counts = {o: int(n) for o, n in rows}
    attempted = sum(counts.get(k, 0) for k in _ATTEMPTED)
    cap = cap_state()
    return {
        "windowHours": hours,
        "counts": counts,
        "attempted": attempted,
        "successRate": (counts.get("ok", 0) / attempted) if attempted else None,
        "capped": cap and {"reason": cap["reason"], "window": cap["window"],
                           "until": cap["until"].isoformat()} or None,
        "last": last and {"at": last[0].isoformat(), "source": last[1], "outcome": last[2],
                          "window": last[3], "note": last[4], "jobId": last[5]} or None,
        # 认证失效比撞顶严重：撞顶会自愈，这个不动手永远不会好，所以单独抬成一个顶层字段
        "authFailing": bool(last_att and last_att[0] == "auth"),
        "authNote": (last_att[2] if last_att and last_att[0] == "auth" else None),
        # 最后一次**真打到引擎**的结果。窗口成功率是滚动平均，会被已经修好的故障拖着不放
        # （令牌修复后面板仍显示「异常」，2026-08-07 实测）——「此刻好不好」得看这个。
        "lastAttempt": (last_att[0] if last_att else None),
        "lastOkAt": last_ok and last_ok[0].isoformat() or None,
        "lastCappedAt": last_cap and last_cap[0].isoformat() or None,
        "lastCappedWindow": last_cap and last_cap[1] or None,
    }

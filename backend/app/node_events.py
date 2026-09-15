"""节点事件埋点：只给「没有别处可查」的节点用。

**这不是节点健康度的主力**。P1 各引擎成败与耗时、P3 走了哪一档、后处理每步降级与花费，
都能从 `jobs.metrics` / `postprocess_jobs` 直接聚合（见 health_view）——那些数字在产出时
就写下了，聚合上线当天就有近 30 天历史，而埋点得从零攒，头一周正是最想看的一周却是空的。
所以这里只覆盖残余：调用发生在别处、库里没留任何痕迹的那些。

**埋点失败一律吞掉**（同 p3_health）：监控坏了不许连累主流程。这条纪律没有例外——
术语库助手那条链路碰不到账本，最坏是「这次没生成出来」，更不该因为记一笔失败而变成 500。

本机版（与线上不同）：线上 stats 用 Postgres 的 percentile_cont / FILTER 聚合；本机库是 SQLite，
没有这些写法，原样搬来会查询出错、被吞掉，面板恒空。改成取出耗时在 Python 里算分位数，口径相同。
"""
from datetime import datetime, timedelta, timezone

from . import db

_MAX_NOTE = 300


def record(node: str, outcome: str, *, ms: int | None = None,
           ref: str | None = None, note: str | None = None) -> None:
    """记一次节点调用。任何异常吞掉——调用方不必包 try。"""
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO node_events (node, outcome, ms, ref, note) VALUES (%s,%s,%s,%s,%s)",
                (node, outcome, ms, ref, (note or "")[:_MAX_NOTE] or None),
            )
    except Exception:  # noqa: BLE001
        pass


def percentile(values: list[float], p: float) -> float | None:
    """分位数，线性插值——与 Postgres 的 percentile_cont 同一算法。没有样本返回 None。"""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    pos = (len(vals) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def stats(nodes: list[str], hours: int) -> dict[str, dict]:
    """近 N 小时各节点的 {ok, total, p50Ms, p95Ms}。查不到返回 {}（那一格显示「—」）。

    **P95 和 P50 一起给**：术语库助手要看的正是尾部——它前一百多秒一个字不吐（在思考），
    用户就那么等着。只看中位数会把这件事平均掉。"""
    if not nodes:
        return {}
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT node, outcome, ms FROM node_events WHERE node = ANY(%s) AND at > %s",
                (nodes, since),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    acc: dict[str, dict] = {}
    for node, outcome, ms in rows:
        a = acc.setdefault(node, {"total": 0, "ok": 0, "ms": []})
        a["total"] += 1
        a["ok"] += outcome == "ok"
        if ms is not None:
            a["ms"].append(ms)
    out = {}
    for node, a in acc.items():
        p50, p95 = percentile(a["ms"], 0.5), percentile(a["ms"], 0.95)
        out[node] = {"total": a["total"], "ok": a["ok"],
                     "p50Ms": round(p50) if p50 is not None else None,
                     "p95Ms": round(p95) if p95 is not None else None}
    return out

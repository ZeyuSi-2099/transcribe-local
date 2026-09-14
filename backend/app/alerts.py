"""运营告警：**先记一笔（进库），再顺便发一封（邮件）**。

2026-08-14 之前这里只有「发邮件」一件事，代价是：漏看一次就永远查不到、发送失败只在日志
里留一行、去重记在进程内存里（线上不止一个进程，各记各的）、没有「处理过了」这个概念。

**最要命的不是不好用，是它在最关键的时刻不工作**：告警邮件与登录验证码共用同一个供应商
额度（免费档 100 封/天）。撞上限时用户登不进，而这时你也收不到任何告警——两个系统同时哑，
还是同一个原因。所以主路改成落库（驾驶舱看得见），邮件降级成旁路。

两条路**互不阻塞**：落库失败照样发邮件，发邮件失败照样留着那条记录并把失败原因写进去。
任何一路的异常都不许抛给调用方——告警本身不该拖垮它在监视的那件事。

档位（tier）是必填的，且只有三个值：
  act   不动手就一直是坏的（收款鉴权失败 / 退款对不上账 / 余额没扣）→ 进待办条 + 给「已处理」
  watch 会自愈，但连续出现说明有问题（失败率偏高 / 余额偏低）→ 只进告警列表
  fyi   不是故障（销售线索）→ 只进告警列表
判据跟待办条一直用的那条一样：**「看到它之后我要做什么」——答不上来的就不该进待办条**。
做成必填而不是给默认值：新加一条告警时漏分档会直接报错，而不是悄悄落进错档——
错档比没档更糟，它会让人以为自己已经在看了。
"""
import json
import os
import urllib.request

from . import config, db

ALERT_COOLDOWN_SEC = 6 * 3600
RETENTION_DAYS = 180      # 主要用途是事后追溯「之前有没有征兆」，而那种回看常跨好几个月

TIERS = ("act", "watch", "fyi")


def alerts_dev_mode() -> bool:
    return not os.environ.get("RESEND_API_KEY")


def _post_resend(admins: list[str], subject: str, text: str) -> bool:
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({
            "from": os.environ.get("EMAIL_FROM", "Transcribe <login@transcribe.solutions>"),
            "to": admins, "subject": subject, "text": text,
        }).encode(),
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                 "Content-Type": "application/json", "User-Agent": "transcribe.solutions/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status < 300


def _suppress(key: str, cooldown_sec: int) -> bool:
    """冷却窗内已经记过同一个键？**记一次数，然后压掉这封信**。

    关键在于压掉的也要计数：旧做法直接 return，于是「这一小时其实报了 40 次」这个事实
    就永远没了——而「偶发一次」和「一直在响」该做的处置完全不同。
    去重挪到库里还顺带解掉跨进程的问题（收上传的和跑看门狗的各是一个进程，
    进程内冷却让同一条告警可能发好几份，重部署后又重新轰炸一轮）。"""
    with db.connect() as conn:
        return bool(conn.execute(
            "UPDATE admin_alerts SET suppressed = suppressed + 1, last_at = now() "
            "WHERE id = (SELECT id FROM admin_alerts WHERE dedup_key = %s "
            "            AND last_at > now() - %s * interval '1 second' "
            "            ORDER BY last_at DESC LIMIT 1) RETURNING id",
            (key, cooldown_sec),
        ).fetchone())


def _record(tier: str, key: str | None, subject: str, text: str) -> int | None:
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO admin_alerts (tier, dedup_key, subject, body) "
            "VALUES (%s,%s,%s,%s) RETURNING id", (tier, key, subject, text),
        ).fetchone()
    return int(row[0]) if row else None


def _mark_mailed(alert_id: int | None, state: str, err: str | None = None) -> None:
    if alert_id is None:
        return
    try:
        with db.connect() as conn:
            conn.execute("UPDATE admin_alerts SET mailed=%s, mail_error=%s WHERE id=%s",
                         (state, (err or "")[:500] or None, alert_id))
    except Exception:  # noqa: BLE001
        pass


def send_admin_alert(subject: str, text: str, *, tier: str, key: str | None = None,
                     cooldown_sec: int = ALERT_COOLDOWN_SEC) -> bool:
    """记一笔告警并尽量发一封邮件。返回值 = **邮件是否真发出去了**（沿用旧语义）。

    注意返回 False 不再等于「什么都没发生」：记录一定已经落库了（除非库也挂了）。
    调用方一处都不必包 try——这里所有异常都吞掉。"""
    if tier not in TIERS:
        raise ValueError(f"未知告警档位 {tier!r}，只能是 {TIERS}")

    if key is not None:
        try:
            if _suppress(key, cooldown_sec):
                return False
        except Exception as e:  # noqa: BLE001  查不到冷却状态就当没冷却：宁可多发一封，不可漏报
            print(f"[alert] 冷却查询失败（按未冷却继续）：{e}", flush=True)

    alert_id = None
    try:
        alert_id = _record(tier, key, subject, text)
    except Exception as e:  # noqa: BLE001  落库失败不拦邮件——两条路互不依赖
        print(f"[alert] 落库失败（仍尝试发信）：{subject}：{e}", flush=True)

    admins = sorted(config.ADMIN_EMAILS)
    if not admins:
        print(f"[alert] 无 ADMIN_EMAILS，跳过发信：{subject}", flush=True)
        _mark_mailed(alert_id, "skipped", "无 ADMIN_EMAILS")
        return False
    if alerts_dev_mode():
        print(f"[alert·dev] {subject} → {admins}", flush=True)
        _mark_mailed(alert_id, "skipped", "开发模式（未配 RESEND_API_KEY）")
        return False
    try:
        ok = _post_resend(admins, subject, text)
    except Exception as e:  # noqa: BLE001
        print(f"[alert] 发送失败：{e}", flush=True)
        _mark_mailed(alert_id, "failed", str(e))
        return False
    _mark_mailed(alert_id, "sent" if ok else "failed", None if ok else "provider 返回非 2xx")
    return ok


# ── 读侧（运营驾驶舱）────────────────────────────────────────────────────────
def has_key(key: str) -> bool:
    """这个去重键记过没有（不看冷却窗）。定时任务用它判「这周发过了」，
    比走 _suppress 干净：_suppress 每查一次都会给那条记录 suppressed +1。查不到当没记过。"""
    try:
        with db.connect() as conn:
            return conn.execute("SELECT 1 FROM admin_alerts WHERE dedup_key = %s LIMIT 1",
                                (key,)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def list_alerts(days: int = 7, unhandled_only: bool = False, limit: int = 100) -> list[dict]:
    """近 N 天的告警。查不到一律回 []（面板显示「—」），绝不让监控自己的故障变成 500。

    ⚠️ **「只看未处理」不限时间窗**（2026-09-03 生产实见）：待办条不看窗口、列表看，
    于是一条 08-20 的未处理告警在待办条里亮着，点到列表默认 7 天却显示「这段时间没有告警」，
    挂了两周没人理。未处理的东西没有「过期」这回事——它要么被处理，要么一直在。"""
    sql = ("SELECT id, tier, subject, body, mailed, mail_error, suppressed, "
           "to_char(last_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'), "
           "handled_at IS NOT NULL, handled_by "
           "FROM admin_alerts WHERE ")
    params: tuple
    if unhandled_only:
        sql += "handled_at IS NULL AND tier = 'act' "
        params = (limit,)
    else:
        sql += "last_at > now() - %s * interval '1 day' "
        params = (days, limit)
    sql += "ORDER BY last_at DESC LIMIT %s"
    try:
        with db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [
        {"id": int(r[0]), "tier": r[1], "subject": r[2], "body": r[3],
         "mailed": r[4], "mailError": r[5], "suppressed": int(r[6]), "at": r[7],
         "handled": bool(r[8]), "handledBy": r[9]}
        for r in rows
    ]


def mark_handled(alert_id: int, by: str) -> bool:
    """标记已处理。**只有 act 档能标**：watch/fyi 自己会消失，让人再点一次纯属多余动作。
    已标过的返回 False（不翻案、不改人名）。"""
    with db.connect() as conn:
        return bool(conn.execute(
            "UPDATE admin_alerts SET handled_at=now(), handled_by=%s "
            "WHERE id=%s AND tier='act' AND handled_at IS NULL RETURNING id",
            (by, alert_id),
        ).fetchone())


def purge_old(days: int = RETENTION_DAYS) -> int:
    """清理过期告警（看门狗每轮调）。带 WHERE，不会撞 db 层的整表写守卫。"""
    try:
        with db.connect() as conn:
            return len(conn.execute(
                "DELETE FROM admin_alerts WHERE last_at < now() - %s * interval '1 day' RETURNING id",
                (days,),
            ).fetchall())
    except Exception:  # noqa: BLE001
        return 0

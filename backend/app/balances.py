"""服务商余额（监控平台三期）：DB 读写 + 低水位告警 + DeepSeek 自动拉取适配器。

手动登记对所有厂商通用、安全（无外部调用）；自动拉取 = DeepSeek/博查/火山(豆包)/阿里(FunASR) 四家。
已查证无余额 API、只能手动的（2026-07-12）：AssemblyAI（/v2/account 为空桩，余额仅 Dashboard 显示）、
Soniox（官方 OpenAPI 无 billing/balance 端点，余额仅 Console；撞零报 organization_balance_exhausted）。
ELV/SPM 为后付费（超额自动计费/月结），无欠费停摆风险，平静卡不接。
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
import uuid

from . import alerts, db

LOW_ALERT_COOLDOWN_HOURS = 12   # 低余额告警去重：同厂商低于阈值后 N 小时内不重复告警


def list_balances() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT vendor, label, amount_cny, threshold_cny, source, note, "
            "to_char(updated_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'), category, pay_mode, sort_order, unit "
            "FROM vendor_balances ORDER BY sort_order NULLS LAST, vendor"
        ).fetchall()
    return [
        {"vendor": r[0], "label": r[1],
         "amountCny": float(r[2]) if r[2] is not None else None,
         "thresholdCny": float(r[3]) if r[3] is not None else None,
         "source": r[4], "note": r[5], "updatedAt": r[6],
         "category": r[7], "payMode": r[8], "sortOrder": r[9], "unit": r[10] or "cny",
         "low": r[2] is not None and r[3] is not None and float(r[2]) < float(r[3]),
         # 预充值·手动却没设阈值 = check_low_balances 的 WHERE 永远筛不到它，余额见底也不会有
         # 任何告警。这跟「余额充足」在界面上长得一样，但它是**没有保护**，不是没有风险。
         # **只算 prepaid_manual**：后付费（ELV/SPM 月结）不会欠费停摆，prepaid_auto（FunASR）
         # 关联了自动充值——按设计这两类本来就不该盯余额，算进来就成了永远清不掉的假警报，
         # 而假警报会逼人学会忽略整条待办条。
         "noThreshold": r[8] == "prepaid_manual" and r[3] is None}
        for r in rows
    ]


def set_balance(vendor: str, label: str | None = None, amount_cny=None,
                threshold_cny=None, note: str | None = None, source: str | None = None) -> None:
    """登记/更新某厂商余额（upsert，部分更新——None = 保留原值）。
    source=None：新建默认 'manual'、更新时保留原 source（避免改阈值把自动查 api 的卡误标手动）；
    显式传 'api'/'manual' 才改 source。"""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO vendor_balances (vendor, label, amount_cny, threshold_cny, note, source, updated_at) "
            "VALUES (%s,%s,%s,%s,%s, COALESCE(%s::text,'manual'), now()) "
            "ON CONFLICT (vendor) DO UPDATE SET "
            "label=COALESCE(EXCLUDED.label, vendor_balances.label), "
            "amount_cny=COALESCE(EXCLUDED.amount_cny, vendor_balances.amount_cny), "
            "threshold_cny=COALESCE(EXCLUDED.threshold_cny, vendor_balances.threshold_cny), "
            "note=COALESCE(EXCLUDED.note, vendor_balances.note), "
            "source=CASE WHEN %s::text IS NOT NULL THEN %s::text ELSE vendor_balances.source END, updated_at=now()",
            (vendor, label, amount_cny, threshold_cny, note, source, source, source),
        )


def consume(vendor: str, amount: float) -> None:
    """按用量扣减某厂商余额（无余额 API 的厂商用量倒扣，如讯飞按转录时长扣小时）。
    原子递减、不低于 0；保留 source 与单位。amount 与该厂商 unit 同单位（cny 传金额、hours 传小时）。"""
    if not amount or amount <= 0:
        return
    with db.connect() as conn:
        conn.execute(
            "UPDATE vendor_balances SET amount_cny = GREATEST(amount_cny - %s, 0), updated_at = now() "
            "WHERE vendor = %s AND amount_cny IS NOT NULL",
            (amount, vendor),
        )


def _fmt_amount(v: float, unit: str | None) -> str:
    """按厂商单位格式化：hours=小时 / usd=$ / 其余 ¥。"""
    if unit == "hours":
        return f"{v:.1f} 小时"
    if unit == "usd":
        return f"${v:.2f}"
    return f"¥{v:.2f}"


def check_low_balances() -> list[str]:
    """对低于阈值且超冷却的厂商发告警，刷新 low_alerted_at（持久去重）。返回告警的厂商列表。"""
    alerted: list[str] = []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT vendor, label, amount_cny, threshold_cny, unit FROM vendor_balances "
            "WHERE amount_cny IS NOT NULL AND threshold_cny IS NOT NULL AND amount_cny < threshold_cny "
            "AND (low_alerted_at IS NULL OR low_alerted_at < now() - %s * interval '1 hour')",
            (LOW_ALERT_COOLDOWN_HOURS,),
        ).fetchall()
        for vendor, label, amount, threshold, unit in rows:
            name = label or vendor
            amt, thr = _fmt_amount(float(amount), unit), _fmt_amount(float(threshold), unit)
            alerts.send_admin_alert(
                f"⚠️ {name} 余额偏低：{amt}（阈值 {thr}）",
                f"服务商 {name} 当前余额 {amt}，已低于阈值 {thr}，请尽快充值。",
                # watch 而非 act：待办条已经按活状态报了这一条（AdminBalance.low），
                # 再从告警记录里报一遍就是同一件事出现两行——那正好毁掉待办条唯一的资产
                tier="watch",
            )  # 去重靠 low_alerted_at（下行 UPDATE），不再叠 alerts 进程级冷却
            conn.execute("UPDATE vendor_balances SET low_alerted_at=now() WHERE vendor=%s", (vendor,))
            alerted.append(vendor)
    return alerted


def refresh_deepseek() -> dict | None:
    """从 DeepSeek 余额接口拉取并登记（source='api'）。需 DEEPSEEK_API_KEY；失败返回 None（不抛）。
    仅此一家有明确余额接口；其余厂商待逐家查证后再加适配器。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                     "User-Agent": "transcribe.solutions/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001  拉取失败不抛
        print(f"[balances] DeepSeek 余额拉取失败：{e}", flush=True)
        return None
    # 响应形如 {"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"88.50",...}]}
    infos = data.get("balance_infos") or []
    cny = next((b for b in infos if b.get("currency") == "CNY"), infos[0] if infos else None)
    if not cny:
        return None
    amount = float(cny.get("total_balance", 0))
    set_balance("DeepSeek", label="DeepSeek", amount_cny=amount, source="api")
    return {"vendor": "DeepSeek", "amountCny": amount}


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def refresh_volc() -> dict | None:
    """火山引擎（豆包）账户余额：QueryBalanceAcct（billing，AK/SK 火山 V4 签名）。
    需 VOLC_ACCESS_KEY/VOLC_SECRET_KEY（只读账单子账号）；缺钥匙或失败返回 None（不抛）。"""
    ak = os.environ.get("VOLC_ACCESS_KEY")
    sk = os.environ.get("VOLC_SECRET_KEY")
    if not (ak and sk):
        return None
    host, region, service = "billing.volcengineapi.com", "cn-north-1", "billing"
    action, version, method, body = "QueryBalanceAcct", "2022-01-01", "POST", ""
    xdate = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    short = xdate[:8]

    def _h(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()

    def _mac(k: bytes, m: str) -> bytes:
        return hmac.new(k, m.encode(), hashlib.sha256).digest()

    ph = _h(body.encode())
    cq = f"Action={action}&Version={version}"
    ct = "application/json"
    canon_headers = f"content-type:{ct}\nhost:{host}\nx-content-sha256:{ph}\nx-date:{xdate}\n"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    creq = "\n".join([method, "/", cq, canon_headers, signed_headers, ph])
    scope = f"{short}/{region}/{service}/request"
    sts = "\n".join(["HMAC-SHA256", xdate, scope, _h(creq.encode())])
    ksign = _mac(_mac(_mac(_mac(sk.encode(), short), region), service), "request")
    sig = hmac.new(ksign, sts.encode(), hashlib.sha256).hexdigest()
    auth = f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={sig}"
    try:
        req = urllib.request.Request(f"https://{host}/?{cq}", data=body.encode(), method=method)
        for k, v in (("Content-Type", ct), ("Host", host), ("X-Date", xdate),
                     ("X-Content-Sha256", ph), ("Authorization", auth)):
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001  拉取失败不抛
        print(f"[balances] 火山余额拉取失败：{e}", flush=True)
        return None
    amt = (data.get("Result") or {}).get("AvailableBalance")
    if amt is None:
        return None
    amount = float(amt)
    set_balance("豆包", amount_cny=amount, source="api")
    return {"vendor": "豆包", "amountCny": amount}


def refresh_aliyun() -> dict | None:
    """阿里云（FunASR）账户余额：QueryAccountBalance（BSS，RPC V1 HMAC-SHA1 签名，国内站）。
    需 ALIYUN_ACCESS_KEY/ALIYUN_SECRET_KEY（RAM 子账号挂 AliyunBSSReadOnlyAccess）；缺/失败返回 None。"""
    ak = os.environ.get("ALIYUN_ACCESS_KEY")
    sk = os.environ.get("ALIYUN_SECRET_KEY")
    if not (ak and sk):
        return None

    def _pe(s) -> str:  # 阿里专用 percent-encode
        return urllib.parse.quote(str(s), safe="").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    params = {
        "Format": "JSON", "Version": "2017-12-14", "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1", "Timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0", "SignatureNonce": uuid.uuid4().hex, "Action": "QueryAccountBalance",
    }
    canon = "&".join(f"{_pe(k)}={_pe(v)}" for k, v in sorted(params.items()))
    sts = "GET&" + _pe("/") + "&" + _pe(canon)
    params["Signature"] = base64.b64encode(
        hmac.new((sk + "&").encode(), sts.encode(), hashlib.sha1).digest()).decode()
    url = "https://business.aliyuncs.com/?" + "&".join(f"{_pe(k)}={_pe(v)}" for k, v in params.items())
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"[balances] 阿里余额拉取失败：{e}", flush=True)
        return None
    amt = (data.get("Data") or {}).get("AvailableCashAmount")
    if amt is None:
        return None
    amount = float(amt)
    set_balance("FunASR", amount_cny=amount, source="api")
    return {"vendor": "FunASR", "amountCny": amount}


def refresh_bocha() -> dict | None:
    """博查账户钱包余额：GET /v1/fund/remaining（用现有 BOCHA_API_KEY，Bearer）。缺/失败返回 None。"""
    key = os.environ.get("BOCHA_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request("https://api.bochaai.com/v1/fund/remaining", method="GET")
        req.add_header("Authorization", f"Bearer {key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"[balances] 博查余额拉取失败：{e}", flush=True)
        return None
    amt = (data.get("data") or {}).get("remaining")
    if amt is None:
        return None
    amount = float(amt)
    set_balance("博查", amount_cny=amount, source="api")
    return {"vendor": "博查", "amountCny": amount}


# 全部自动拉取（看门狗周期调）：各家独立 try，缺钥匙者自动跳过；返回成功拉到的列表。
# 元组在函数内构建（每次按模块全局解析）→ 测试可 monkeypatch 各 refresh_* 函数。
def refresh_all() -> list[dict]:
    """本机版（与线上不同）：只拉 DeepSeek 与博查——本地识别不用火山、阿里的云端引擎。
    线上四家都拉；本机若也拉，环境变量里恰好有那两家钥匙时，看门狗会每隔一阵去查一次云端账单
    （界面走查时实见：「余额自动刷新：1 家（豆包）」），与「识别全程本机」相悖。"""
    out = []
    for fn in (refresh_deepseek, refresh_bocha):
        try:
            r = fn()
            if r:
                out.append(r)
        except Exception as e:  # noqa: BLE001  单家异常不影响其余
            print(f"[balances] {fn.__name__} 异常：{e}", flush=True)
    return out

import types
import re
import inspect
import io
import json
import math

import pytest
from fastapi.testclient import TestClient

import app.api as api
import app.glossary as glossary_mod
from app import config
from app.blobstore import NoSuchKey
from app.jobstore import Job

# 本机版（与线上不同）：账户与计费模块不搬（登录、充值、结算、退款都没有）。只有登记为「不适用」的用例
# （conftest.py 的 NOT_APPLICABLE）会碰到它们；这里放同名占位，免得整份文件一导入就失败、连本机在用的用例也跑不了。
pricing = None
accounts_mod = types.SimpleNamespace(RateLimited=type("RateLimited", (Exception,), {}),
                                     RefundNotAllowed=type("RefundNotAllowed", (Exception,), {}))

class FakeJobStore:
    def __init__(self):
        self.jobs = {}
        self.n = 0

    def create_job(self, audio_key, lang, recording_type, user_email, file_name=None,
                   duration_sec=None, glossary_id=None, reserved_cents=0):
        self.n += 1
        jid = f"job{self.n}"
        self.jobs[jid] = dict(
            id=jid, status="queued", phase=None, progress=0, lang=lang,
            audio_key=audio_key, result_key=None, error=None, error_public=None,
            recording_type=recording_type, user_email=user_email,
            file_name=file_name, duration_sec=duration_sec, glossary_id=glossary_id,
            reserved_cents=reserved_cents,
        )
        return jid

    def get_job(self, jid):
        d = self.jobs.get(jid)
        if not d:
            return None
        return Job(**d)

    def admin_overview(self):
        return {"jobs": list(self.jobs.values()), "failures": [], "recent": [],
                "summary": {"running": 0, "queued": len(self.jobs),
                            "doneToday": 0, "failedToday": 0}}


class FakeBlob:
    NoSuchKey = NoSuchKey  # 与真实 blobstore 同一个异常类，api.py 里 except blobstore.NoSuchKey 能对上

    def __init__(self):
        self.store = {}

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.store[key] = data

    def upload_fileobj(self, key, fileobj, content_type="application/octet-stream"):
        self.store[key] = fileobj.read()

    def get_bytes(self, key):
        if key not in self.store:
            raise NoSuchKey(key)
        return self.store[key]

    def open_range(self, key, start=None, end=None):
        """与真实 blobstore.open_range 同契约：(流, 本次字节数, 对象总长)。

        ⚠️ 桩也要**照着真实语义切**：越界/无 Range 两种形状都得对，否则守卫测的是桩不是代码。"""
        import io

        if key not in self.store:
            raise NoSuchKey(key)
        data = self.store[key]
        total = len(data)
        chunk = data if start is None else data[start: (total if end is None else end + 1)]
        return io.BytesIO(chunk), len(chunk), total

    def delete(self, key):
        self.store.pop(key, None)


class FakeAccounts:
    """内存版账户：tok1 → a@b.com 预登录，便于带 AUTH 头直接测业务端点。"""

    RateLimited = accounts_mod.RateLimited  # api.py 里 except accounts.RateLimited 要能对上真实异常类

    def __init__(self):
        self.codes = {}
        self.sessions = {"tok1": "a@b.com"}
        self.balances = {"a@b.com": 3000}
        self.free_left = {}          # email -> 免费额度剩余秒（2026-08-02：后处理也能抵）
        self.ledger_rows = []

    def request_code(self, email, ip=None, lang="en"):
        self.codes[email] = "654321"
        return "654321"   # 开发模式回显

    def verify_code(self, email, code, ip=None, source=None):
        if self.codes.get(email) != code:
            return None
        tok = f"tok-{email}"
        self.sessions[tok] = email
        self.balances.setdefault(email, 0)
        return tok

    def free_status(self, email):
        # 免费额度（真实现读 users 三列）：本 Fake 默认无额度；个别测试覆盖此方法
        return {"grantedMinutes": 0, "leftSeconds": 0, "limited": False}

    def session_email(self, token):
        return self.sessions.get(token)

    def logout(self, token):
        self.sessions.pop(token, None)

    def balance_cents(self, email):
        return self.balances.get(email, 0)

    def free_status(self, email):
        return {"grantedMinutes": 0, "leftSeconds": self.free_left.get(email, 0), "limited": False}

    def reserve_and_create_job(self, email, amount_cents, audio_key, lang, recording_type,
                               file_name=None, duration_sec=None, glossary_id=None,
                               rate_cents_per_min=None, project_id=None,
                               audio_sha256=None, ui_lang=None):
        self.last_sha256 = audio_sha256
        self.last_ui_lang = ui_lang
        # 与真实实现同契约：不够 → None（不扣不建）；够 → 扣款并经 jobstore 建 job（带预扣额
        # 和费率快照）。self.jobstore 由 _client 装配时指向同一个 FakeJobStore，测试仍可断言 js.jobs。
        if amount_cents > 0:
            if self.balances.get(email, 0) < amount_cents:
                return None
            self.balances[email] -= amount_cents
        self.last_rate = rate_cents_per_min
        self.last_project_id = project_id
        return self.jobstore.create_job(audio_key, lang, recording_type, email,
                                        file_name=file_name, duration_sec=duration_sec,
                                        glossary_id=glossary_id,
                                        reserved_cents=max(amount_cents, 0))

    def email_dev_mode(self):
        return True  # 测试默认开发模式；生产用例里覆盖为 False

    def credit(self, email, cents, source):
        self.balances[email] = self.balances.get(email, 0) + cents
        self.ledger_rows.append({"kind": "topup", "amountCents": cents, "source": source})
        return self.balances[email]

    def list_ledger(self, email):
        return self.ledger_rows

    def list_jobs(self, email):
        return [{"id": "j1", "status": "done", "fileName": "a.m4a"}]

    def mark_invoiced(self, email, ids):
        self.invoiced = ids

    # ── 充值退款（admin 侧发起入口用）：发起=占额，不动余额 ──
    RefundNotAllowed = accounts_mod.RefundNotAllowed   # api.py 的 except 要能对上真实异常类

    def list_topups(self, email):
        return [{"id": 7, "amountCents": 1000, "refundedCents": 0, "source": "stripe",
                 "createdAt": "2026-07-28T00:00:00+00:00", "ageDays": 1.0, "isBonus": False,
                 "refundableCents": 400}] if email == "u@x.com" else []

    def refundable_amount(self, email, ledger_id):
        rows = [t for t in self.list_topups(email) if t["id"] == ledger_id]
        return rows[0]["refundableCents"] if rows else 0

    def create_refund(self, email, topup_ledger_id, net_amount_cents, provider):
        allowed = self.refundable_amount(email, topup_ledger_id)
        if net_amount_cents <= 0 or net_amount_cents > allowed:
            raise accounts_mod.RefundNotAllowed(allowed)
        self.refunds = getattr(self, "refunds", {})
        rid = len(self.refunds) + 1
        self.refunds[rid] = {"status": "pending", "net": net_amount_cents, "provider": provider}
        return rid

    def fail_refund(self, refund_id, reason=""):
        r = getattr(self, "refunds", {}).get(refund_id)
        if not r or r["status"] != "pending":
            return False
        r["status"] = "failed"
        return True

    def set_refund_provider_id(self, refund_id, provider_refund_id):
        getattr(self, "refunds", {})[refund_id]["providerRefundId"] = provider_refund_id

    def topup_provider_txn_id(self, email, ledger_id):
        # 这份假账本里的充值是 stripe 时代的老单，没存 provider 交易号——
        # 正是「拿不到原交易号 → 只能手工退」那条路径
        return None


class FakeGlossary:
    """内存版多术语库；validate/validate_name 委托真模块（保证校验口径一致）、
    返回契约同真模块（dict 成功 / str 违规 / None 不存在 / bool 删除）。"""

    def __init__(self):
        self.rows = {}   # id -> {id,email,name,language,content,updated_at}
        self.n = 0

    def validate(self, content):
        return glossary_mod.validate(content)

    def validate_name(self, name):
        return glossary_mod.validate_name(name)

    def list_glossaries(self, email):
        return [{k: r[k] for k in ("id", "name", "language", "content", "updated_at")}
                for r in self.rows.values() if r["email"] == email]

    def _name_taken(self, email, name, exclude_id):
        for r in self.rows.values():
            if r["email"] == email and r["name"].strip().lower() == name.strip().lower() and r["id"] != exclude_id:
                return True
        return False

    def create_glossary(self, email, name, language, content):
        if (e := glossary_mod.validate_name(name)) is not None:
            return e
        if (e := glossary_mod.validate(content)) is not None:
            return e
        if len([r for r in self.rows.values() if r["email"] == email]) >= glossary_mod.MAX_GLOSSARIES:
            return "too many glossaries: limit 20"
        if self._name_taken(email, name, None):
            return "name already exists"
        self.n += 1
        gid = f"g{self.n}"
        self.rows[gid] = dict(id=gid, email=email, name=name.strip(), language=language,
                              content=content, updated_at="2026-06-23T00:00:00+00:00")
        return {k: self.rows[gid][k] for k in ("id", "name", "language", "content", "updated_at")}

    def update_glossary(self, email, gid, name, language, content):
        if (e := glossary_mod.validate_name(name)) is not None:
            return e
        if (e := glossary_mod.validate(content)) is not None:
            return e
        r = self.rows.get(gid)
        if not r or r["email"] != email:
            return None
        if self._name_taken(email, name, gid):
            return "name already exists"
        r.update(name=name.strip(), language=language, content=content)
        return {k: r[k] for k in ("id", "name", "language", "content", "updated_at")}

    def delete_glossary(self, email, gid):
        r = self.rows.get(gid)
        if not r or r["email"] != email:
            return False
        del self.rows[gid]
        return True

    def get_glossary_content(self, gid):
        r = self.rows.get(gid)
        return r["content"] if r else ""

    def owns(self, email, gid):
        r = self.rows.get(gid)
        return bool(r) and r["email"] == email


class FakeBalances:
    def __init__(self):
        self.store = {}

    def list_balances(self):
        return [{"vendor": v, **d} for v, d in self.store.items()]

    def set_balance(self, vendor, label=None, amount_cny=None, threshold_cny=None, note=None, source="manual"):
        self.store[vendor] = {"label": label, "amountCny": amount_cny, "thresholdCny": threshold_cny,
                              "note": note, "source": source, "low": False}

    def refresh_deepseek(self):
        self.set_balance("DeepSeek", label="DeepSeek", amount_cny=88.5, source="api")
        return {"vendor": "DeepSeek", "amountCny": 88.5}

    def refresh_all(self):
        self.refresh_deepseek()
        return [{"vendor": "DeepSeek", "amountCny": 88.5}]


AUTH = {"Authorization": "Bearer tok1"}


class FakePostprocessStub:
    """本文件不测后处理端点（那些在 test_postprocess_api.py），只需把 /api/jobs 行内摘要
    打桩为空，防单测碰真实 DB。"""

    def job_summaries(self, email):
        return {}


def _client(monkeypatch):
    fake_js, fake_blob, fake_acc = FakeJobStore(), FakeBlob(), FakeAccounts()
    fake_acc.jobstore = fake_js   # reserve_and_create_job 经它建 job，测试仍断言 js.jobs
    monkeypatch.setattr(api, "jobstore", fake_js)
    monkeypatch.setattr(api, "blobstore", fake_blob)
    monkeypatch.setattr(api, "accounts", fake_acc, raising=False)   # 本机版 api 没有 accounts，只给不适用的用例留着
    # 本机版（与线上不同）：单用户不登录——当前用户固定是 local.EMAIL，这里对齐成测试里预登录的 a@b.com；
    # 建单与列单走 local（线上走 accounts.reserve_and_create_job / list_jobs），接到同一个 FakeJobStore。
    monkeypatch.setattr(api.local, "EMAIL", "a@b.com")
    monkeypatch.setattr(api.local, "create_job",
                        lambda audio_key, lang, recording_type, *, file_name=None, duration_sec=None,
                        glossary_id=None, audio_sha256=None, ui_lang=None:
                        fake_js.create_job(audio_key, lang, recording_type, "a@b.com", file_name=file_name,
                                           duration_sec=duration_sec, glossary_id=glossary_id))
    monkeypatch.setattr(api.local, "list_jobs", fake_acc.list_jobs)
    monkeypatch.setattr(api, "glossary", FakeGlossary())
    monkeypatch.setattr(api, "balances", FakeBalances())
    monkeypatch.setattr(api, "postprocess", FakePostprocessStub())
    # create_job 现在会真落临时文件跑 ffprobe——单测不测 ffprobe 本身，默认桩回一个固定时长
    # （=旧测试里常用的 duration_sec=40 表单值，两处对得上，无需逐个改断言）。
    monkeypatch.setattr(api, "_probe_duration_sec", lambda path: 40.0)
    return TestClient(api.app), fake_js, fake_blob


def test_auth_flow_request_verify_me(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/api/auth/request-code", json={"email": "new@user.com"})
    assert r.status_code == 200 and r.json()["devCode"] == "654321"   # 开发模式回显
    assert client.post("/api/auth/verify", json={"email": "new@user.com", "code": "000000"}).status_code == 401
    r = client.post("/api/auth/verify", json={"email": "new@user.com", "code": "654321"})
    tok = r.json()["token"]
    assert r.status_code == 200 and r.json()["email"] == "new@user.com"
    me = client.get("/api/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200 and me.json()["email"] == "new@user.com"


def test_auth_request_code_passes_client_ip(monkeypatch):
    # XFF 从左到右取第一个公网 IP（内网段是链路噪音要跳过）
    client, *_ = _client(monkeypatch)
    captured = {}

    def fake_request_code(email, ip=None, lang="en"):
        captured["ip"] = ip
        return "654321"
    monkeypatch.setattr(api.accounts, "request_code", fake_request_code)
    client.post("/api/auth/request-code", json={"email": "x@y.com"},
                headers={"X-Forwarded-For": "10.0.0.1, 34.12.34.56"})
    assert captured["ip"] == "34.12.34.56"


def test_auth_request_code_prefers_vercel_forwarded_over_cf(monkeypatch):
    client, *_ = _client(monkeypatch)
    captured = {}

    def fake_request_code(email, ip=None, lang="en"):
        captured["ip"] = ip
        return "654321"
    monkeypatch.setattr(api.accounts, "request_code", fake_request_code)
    # 生产链路（浏览器→Vercel 反代→CF→Render）里 CF-Connecting-IP=Vercel 出口机，
    # 不是用户——2026-08-01 生产实测全体用户被记成同一个出口 IP，闸④集体误伤。
    # 取序改为 Vercel 转发头 > XFF 公网首个 > CF 头（详见 api._client_ip 文档串）。
    client.post("/api/auth/request-code", json={"email": "x@y.com"},
                headers={"X-Vercel-Forwarded-For": "34.9.9.9",
                         "CF-Connecting-IP": "13.52.103.55",
                         "X-Forwarded-For": "34.9.9.9, 13.52.103.55"})
    assert captured["ip"] == "34.9.9.9"


def test_auth_request_code_rejects_non_ip_forwarded_value(monkeypatch):
    # XFF 首段客户端可控：任意垃圾串若直接进 login_ip_throttle 主键会被无限灌爆（DB 膨胀），
    # 非法 IP 格式一律弃用、退回 socket 层 client.host
    client, *_ = _client(monkeypatch)
    captured = {}

    def fake_request_code(email, ip=None, lang="en"):
        captured["ip"] = ip
        return "654321"
    monkeypatch.setattr(api.accounts, "request_code", fake_request_code)
    client.post("/api/auth/request-code", json={"email": "x@y.com"},
                headers={"X-Forwarded-For": "<script>not-an-ip" + "x" * 200})
    assert captured["ip"] == "testclient"   # TestClient 的 socket 层 host


def test_auth_request_code_falls_back_to_cf_when_no_public_forwarded(monkeypatch):
    # 直连（无反代）：XFF 缺失/全内网时用 CF-Connecting-IP（此时它就是真实客户端）
    client, *_ = _client(monkeypatch)
    captured = {}

    def fake_request_code(email, ip=None, lang="en"):
        captured["ip"] = ip
        return "654321"
    monkeypatch.setattr(api.accounts, "request_code", fake_request_code)
    client.post("/api/auth/request-code", json={"email": "x@y.com"},
                headers={"CF-Connecting-IP": "74.222.14.74",
                         "X-Forwarded-For": "10.0.0.1, 192.168.1.1"})
    assert captured["ip"] == "74.222.14.74"


def test_auth_request_code_ip_rate_limited_maps_to_429(monkeypatch):
    client, *_ = _client(monkeypatch)

    def boom(email, ip=None, lang="en"):
        raise accounts_mod.RateLimited("ip", 1800)
    monkeypatch.setattr(api.accounts, "request_code", boom)
    r = client.post("/api/auth/request-code", json={"email": "x@y.com"})
    assert r.status_code == 429


def test_auth_request_code_send_failure_502_hides_internal_error(monkeypatch):
    # 发信故障 502 的 detail 不许拼接内部异常串（供应商报错/密钥线索），未鉴权即可见——
    # E2「用户可达错误全脱敏」口径；内部细节只进服务端日志
    client, *_ = _client(monkeypatch)

    def boom(email, ip=None, lang="en"):
        raise RuntimeError("resend 429 secret-internal-detail")
    monkeypatch.setattr(api.accounts, "request_code", boom)
    r = client.post("/api/auth/request-code", json={"email": "x@y.com"})
    assert r.status_code == 502
    assert "secret-internal-detail" not in r.text
    assert "RuntimeError" not in r.text


def test_healthz_no_auth(monkeypatch):
    # Render 健康检查：不鉴权、不碰 DB，纯存活信号
    client, *_ = _client(monkeypatch)
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.head("/healthz").status_code == 200  # UptimeRobot 用 HEAD，别再回 405


def test_gone_tombstone_returns_410(monkeypatch):
    """已下架公开页的墓碑（报价器 2026-08-18 下架，vercel.json 把 /quote 转到这里）。

    必须是 **410 而不是 404**：这 8 个网址已经提交给 GSC 与 Bing，404 会让引擎按
    「可能是临时故障」反复回来重试几个月，410 才是「永久没了」。
    也不鉴权——爬虫不带 token。
    """
    client, *_ = _client(monkeypatch)
    for r in (client.get("/api/gone"), client.head("/api/gone")):
        assert r.status_code == 410
        assert r.headers["content-type"].startswith("text/html")


def test_me_backfills_cookie_for_bearer_only_session(monkeypatch):
    # 迁移兜底：老用户只有 localStorage token 没有 Cookie → /api/me 顺手补发，
    # 否则升级后音频/导出（带不了 Authorization 头）会突然 401
    client, *_ = _client(monkeypatch)
    r = client.get("/api/me", headers=AUTH)
    assert r.status_code == 200 and r.cookies.get("tx_session") == "tok1"


def test_business_endpoints_require_auth(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/ledger").status_code == 401
    assert client.post("/api/jobs", files={"file": ("a.m4a", io.BytesIO(b"X"), "audio/mp4")}).status_code == 401


def test_admin_overview_hidden_from_non_admin(monkeypatch):
    # 驾驶舱：未登录 / 已登录但非管理员，一律 404（不暴露后台存在性，与归属校验同口径）
    client, *_ = _client(monkeypatch)
    assert client.get("/api/admin/overview").status_code == 404            # 未登录
    assert client.get("/api/admin/overview", headers=AUTH).status_code == 404  # a@b.com 不在白名单


def test_is_admin_flag_in_me_and_verify(monkeypatch):
    # 管理员标记随 /me + verify 登录响应一起下发，供前端显示驾驶舱入口；非白名单为 False
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    assert client.get("/api/me", headers=AUTH).json()["isAdmin"] is True   # tok1 → a@b.com
    client.post("/api/auth/request-code", json={"email": "joe@x.com"})
    v = client.post("/api/auth/verify", json={"email": "joe@x.com", "code": "654321"})
    assert v.json()["isAdmin"] is False


def test_admin_overview_returns_data_for_admin(monkeypatch):
    # 白名单内的管理员能拿到总览（任务 + 失败 + 汇总）
    client, fake_js, _ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    fake_js.create_job("audio/x.m4a", "zh", "meeting", "u@x.com", file_name="x.m4a")
    r = client.get("/api/admin/overview", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"jobs", "failures", "summary"}
    assert body["summary"]["queued"] == 1


def test_admin_balances_hidden_from_non_admin(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.get("/api/admin/balances").status_code == 404
    assert client.get("/api/admin/balances", headers=AUTH).status_code == 404   # 非管理员


def test_admin_balances_set_and_list_for_admin(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    assert client.put("/api/admin/balances", headers=AUTH,
                      json={"vendor": "DeepSeek", "amountCny": 50, "thresholdCny": 20}).status_code == 200
    body = client.get("/api/admin/balances", headers=AUTH).json()
    assert any(b["vendor"] == "DeepSeek" and b["amountCny"] == 50 for b in body["balances"])


def test_admin_set_balance_requires_vendor(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    assert client.put("/api/admin/balances", headers=AUTH, json={"amountCny": 50}).status_code == 422


def test_admin_set_balance_rejects_negative_amount(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    r = client.put("/api/admin/balances", headers=AUTH, json={"vendor": "DeepSeek", "amountCny": -1})
    assert r.status_code == 422


def test_admin_set_balance_rejects_negative_threshold(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    r = client.put("/api/admin/balances", headers=AUTH, json={"vendor": "DeepSeek", "thresholdCny": -5})
    assert r.status_code == 422


def test_admin_set_balance_rejects_non_numeric_amount(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    r = client.put("/api/admin/balances", headers=AUTH, json={"vendor": "DeepSeek", "amountCny": "abc"})
    assert r.status_code == 422


def test_admin_set_balance_accepts_valid_positive_amount(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    r = client.put("/api/admin/balances", headers=AUTH,
                   json={"vendor": "DeepSeek", "amountCny": 50, "thresholdCny": 20})
    assert r.status_code == 200


def test_admin_set_balance_anonymous_illegal_body_hidden(monkeypatch):
    # 匿名探测：即使 body 字段非法（如 amountCny=-1），也不能拿到 422 —— 那会暴露
    # 该运营端点存在 + 字段形状。鉴权必须在 body 校验之前，未登录一律 404。
    client, *_ = _client(monkeypatch)
    r = client.put("/api/admin/balances", json={"vendor": "DeepSeek", "amountCny": -1})
    assert r.status_code == 404


def test_admin_set_balance_anonymous_non_dict_body_hidden(monkeypatch):
    # 旁路探测：body 不是 dict（字符串/数组/坏 JSON）时，FastAPI 的 Body(...) 校验会在进
    # handler 之前就发 422 —— 鉴权根本没跑到，端点存在性照样暴露。必须先 404 后 422。
    client, *_ = _client(monkeypatch)
    assert client.put("/api/admin/balances", json="abc").status_code == 404
    assert client.put("/api/admin/balances", json=[1, 2]).status_code == 404
    assert client.put("/api/admin/balances", content=b"{bad json",
                      headers={"Content-Type": "application/json"}).status_code == 404


def test_admin_set_balance_admin_non_dict_body_422(monkeypatch):
    # 已鉴权的管理员发非 dict body → 422（此时暴露校验信息无妨）
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    assert client.put("/api/admin/balances", headers=AUTH, json=[1, 2]).status_code == 422


def test_admin_refresh_balances_for_admin(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    r = client.post("/api/admin/balances/refresh", headers=AUTH)
    assert r.status_code == 200 and any(x["vendor"] == "DeepSeek" for x in r.json()["refreshed"])


def test_admin_refresh_balances_hidden_from_non_admin(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.post("/api/admin/balances/refresh").status_code == 404           # 未登录
    assert client.post("/api/admin/balances/refresh", headers=AUTH).status_code == 404  # 非管理员


def test_topup_dev_mode_credits_and_ledger(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/api/topups", json={"amountCents": 5000}, headers=AUTH)
    assert r.status_code == 200 and r.json()["balanceCents"] == 8000   # 3000 + 5000
    assert client.post("/api/topups", json={"amountCents": 100}, headers=AUTH).status_code == 422
    led = client.get("/api/ledger", headers=AUTH).json()["ledger"]
    assert led[0]["kind"] == "topup" and led[0]["amountCents"] == 5000


def test_topup_failclosed_in_production_without_stripe(monkeypatch):
    # 生产环境（非开发模式）没接 Stripe 时，充值必须 503 拒绝，绝不免费到账
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.accounts, "email_dev_mode", lambda: False)
    r = client.post("/api/topups", json={"amountCents": 5000}, headers=AUTH)
    assert r.status_code == 503
    assert client.get("/api/ledger", headers=AUTH).json()["ledger"] == []  # 没入账


def test_jobs_list_scoped_to_user(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.get("/api/jobs", headers=AUTH)
    assert r.status_code == 200 and r.json()["jobs"][0]["fileName"] == "a.m4a"


def test_create_job_uploads_audio_and_enqueues(monkeypatch):
    client, js, blob = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "recording_type": "meeting", "duration_sec": "40"},
        headers=AUTH,
    )
    assert r.status_code == 200
    jid = r.json()["jobId"]
    assert js.jobs[jid]["status"] == "queued" and js.jobs[jid]["lang"] == "zh"
    assert js.jobs[jid]["recording_type"] == "meeting"
    assert js.jobs[jid]["user_email"] == "a@b.com"            # 任务归属来自会话
    assert js.jobs[jid]["file_name"] == "a.m4a" and js.jobs[jid]["duration_sec"] == 40
    assert blob.store[js.jobs[jid]["audio_key"]] == b"AUDIO"


# ── 上传时预扣余额 + 服务端强制时长上限（B0/批次A）──

def test_create_job_server_probed_duration_over_limit_is_413(monkeypatch):
    # 服务端 ffprobe 实测超限 → 413，即使前端表单报的时长很短（不再只信前端）
    client, js, blob = _client(monkeypatch)
    monkeypatch.setattr(api, "_probe_duration_sec", lambda path: config.MAX_DURATION_SEC + 100)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "duration_sec": "10"},   # 前端谎报/低估
        headers=AUTH,
    )
    assert r.status_code == 413
    assert js.jobs == {}
    assert blob.store == {}


def test_create_job_ffprobe_failure_is_400(monkeypatch):
    # 损坏/非音视频文件测不出时长 → 400，不建 job
    client, js, blob = _client(monkeypatch)
    monkeypatch.setattr(api, "_probe_duration_sec", lambda path: None)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"NOT AUDIO"), "audio/mp4")},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert js.jobs == {}
    assert blob.store == {}


# ── 上传体积硬上限（护派单前台临时盘，与 4h 时长上限是磁盘/引擎两个维度）──

def test_upload_size_middleware_rejects_before_body_parse(monkeypatch):
    # 真护盘必须在 ASGI 层拦：`UploadFile = File(...)` 会让 Starlette 在进 handler 之前就把
    # 整个 multipart spool 落盘，handler 里的"预检"实为马后炮。中间件按 Content-Length 先拒
    # 的证据：连 multipart 都解析不了的垃圾 body（解析会报 400/422），超限时也拿到 413——
    # 说明拒绝发生在解析/落盘之前。
    client, js, blob = _client(monkeypatch)
    monkeypatch.setattr(api.config, "MAX_UPLOAD_BYTES", 10)
    r = client.post(
        "/api/jobs",
        content=b"X" * 100,   # 100 字节垃圾（Content-Length=100 > 10），根本不是 multipart
        headers={**AUTH, "Content-Type": "multipart/form-data; boundary=xxx"},
    )
    assert r.status_code == 413
    assert js.jobs == {} and blob.store == {}

def test_create_job_content_length_over_limit_is_413_before_any_disk_or_probe(monkeypatch):
    # Content-Length 预检：超限直接拒，且不落临时文件、不调 ffprobe/reserve（省磁盘）
    client, js, blob = _client(monkeypatch)
    monkeypatch.setattr(api.config, "MAX_UPLOAD_BYTES", 10)

    probed = []
    monkeypatch.setattr(api, "_probe_duration_sec", lambda path: probed.append(path) or 40.0)
    reserved = []
    monkeypatch.setattr(api.accounts, "reserve_and_create_job",
                        lambda *a, **kw: reserved.append(1) or "jobX")

    ntf_calls = []
    real_ntf = api.tempfile.NamedTemporaryFile
    monkeypatch.setattr(api.tempfile, "NamedTemporaryFile",
                        lambda *a, **kw: ntf_calls.append(1) or real_ntf(*a, **kw))

    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        headers={**AUTH, "content-length": "99999999"},   # 伪造/超限体积（远大于实际 5 字节体）
    )
    assert r.status_code == 413
    assert not probed and not reserved and not ntf_calls   # 没落临时文件、没探测、没预扣
    assert js.jobs == {} and blob.store == {}


def test_create_job_streaming_fallback_rejects_forged_content_length(monkeypatch):
    # Content-Length 缺失/伪造（这里给个非法值让预检解析失败）——靠流式累计计数兜底，
    # 中途超限立即中止并删掉已落的临时文件，不写满盘才判。
    client, js, blob = _client(monkeypatch)
    monkeypatch.setattr(api.config, "MAX_UPLOAD_BYTES", 3)   # body "AUDIO"=5 字节，超限

    probed = []
    monkeypatch.setattr(api, "_probe_duration_sec", lambda path: probed.append(path) or 40.0)

    removed = []
    real_remove = api.os.remove

    def spy_remove(path):
        removed.append(path)
        real_remove(path)

    monkeypatch.setattr(api.os, "remove", spy_remove)

    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        headers={**AUTH, "content-length": "bogus"},   # 伪造/非数字，预检解析失败，落到流式兜底
    )
    assert r.status_code == 413
    assert not probed   # 流式阶段就中止，没走到 ffprobe
    assert len(removed) == 1 and not api.os.path.exists(removed[0])   # 临时文件被删，没有孤儿
    assert js.jobs == {} and blob.store == {}


def test_create_job_small_file_within_upload_limit_passes(monkeypatch):
    # 正常小文件不受体积闸影响，仍走完 ffprobe/reserve 放行（默认 2GB 上限对测试体积无感）
    client, js, blob = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert js.jobs and blob.store


def test_create_job_insufficient_balance_is_402_no_job_no_upload(monkeypatch):
    # 余额不够 → 402，且不建 job；R2 音频对象顺手清掉（新序：先传 R2 再单事务预扣+建 job，
    # 402 后不留孤儿对象）
    client, js, blob = _client(monkeypatch)
    fake_acc = api.accounts
    # 余额 0：预扣 ceil() 保证任何非空音频至少要 1 分钱，必然不够——不绑定当下的具体单价
    fake_acc.balances["a@b.com"] = 0
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        headers=AUTH,
    )
    assert r.status_code == 402
    assert js.jobs == {}
    assert blob.store == {}                          # 已上传的对象被清理
    assert fake_acc.balances["a@b.com"] == 0         # 一分没扣（事务整体回滚语义）


def test_create_job_reserve_and_create_is_one_call_no_partial_state(monkeypatch):
    # 预扣+建 job 已并成单事务原语（真原子性由 infra 测试锁）：R2 上传抛异常发生在扣钱之前
    # → 余额一分不动、无 job（旧「先扣钱、建 job 失败再退」的崩溃窗从结构上消失）
    client, js, blob = _client(monkeypatch)
    fake_acc = api.accounts

    def boom(*a, **kw):
        raise RuntimeError("r2 down")

    monkeypatch.setattr(api.blobstore, "upload_fileobj", boom)
    with pytest.raises(RuntimeError):
        client.post(
            "/api/jobs",
            files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
            headers=AUTH,
        )
    assert fake_acc.balances["a@b.com"] == 3000   # 上传失败在扣钱之前：分文未动
    assert js.jobs == {}


def test_create_job_reserved_amount_matches_probed_duration(monkeypatch):
    # 预扣额按服务端实测时长算，向上取整；不是按前端表单的 duration_sec
    client, js, blob = _client(monkeypatch)
    monkeypatch.setattr(api, "_probe_duration_sec", lambda path: 61.0)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"duration_sec": "5"},   # 前端谎报很短，服务端应无视它
        headers=AUTH,
    )
    assert r.status_code == 200
    jid = r.json()["jobId"]
    # 期望值按「实测 61s × 当下的中文档价」算——谎报的 5s 会得出小得多的数，这里就红
    assert js.jobs[jid]["reserved_cents"] == math.ceil(61 / 60 * pricing.rate_cents_per_min("zh"))
    assert js.jobs[jid]["duration_sec"] == 61   # 服务端实测值落库，不是表单的 5


def test_create_job_temp_file_cleaned_up_on_copy_failure(monkeypatch):
    # 分块拷贝上传流到临时文件中途炸（客户端中断/磁盘满）——文件已创建但拷贝未完成，仍要被清理，
    # 否则每次这种失败都在磁盘留一个孤儿临时文件。
    client, js, blob = _client(monkeypatch)

    real_ntf = api.tempfile.NamedTemporaryFile

    def boom_ntf(*args, **kwargs):
        tmp = real_ntf(*args, **kwargs)

        def boom_write(data):
            raise RuntimeError("write 中断")

        tmp.write = boom_write
        return tmp

    monkeypatch.setattr(api.tempfile, "NamedTemporaryFile", boom_ntf)
    removed = []
    real_remove = api.os.remove

    def spy_remove(path):
        removed.append(path)
        real_remove(path)

    monkeypatch.setattr(api.os, "remove", spy_remove)

    with pytest.raises(RuntimeError):
        client.post(
            "/api/jobs",
            files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
            headers=AUTH,
        )
    assert len(removed) == 1 and not api.os.path.exists(removed[0])
    assert js.jobs == {} and blob.store == {}


# ── 多术语库集合 API（路由 + 状态码映射；DB 真逻辑见 test_glossary_db_integration）──
def test_glossaries_crud_flow(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/api/glossaries", json={"name": "项目A", "content": "FD ｜ 履约"}, headers=AUTH)
    assert r.status_code == 200
    gid = r.json()["id"]
    assert r.json()["name"] == "项目A" and r.json()["content"] == "FD ｜ 履约"
    # 列表含它
    r = client.get("/api/glossaries", headers=AUTH)
    assert r.status_code == 200 and any(g["id"] == gid for g in r.json()["glossaries"])
    # 重名 → 409
    r = client.post("/api/glossaries", json={"name": "项目a", "content": ""}, headers=AUTH)
    assert r.status_code == 409
    # 改名改内容
    r = client.put(f"/api/glossaries/{gid}", json={"name": "项目B", "content": "x ｜ y"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["name"] == "项目B"
    # 删
    r = client.delete(f"/api/glossaries/{gid}", headers=AUTH)
    assert r.status_code == 200
    r = client.get("/api/glossaries", headers=AUTH)
    assert all(g["id"] != gid for g in r.json()["glossaries"])


def test_glossaries_require_auth(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.get("/api/glossaries").status_code == 401
    assert client.post("/api/glossaries", json={"name": "x"}).status_code == 401


def test_create_glossary_rejects_invalid_name(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/api/glossaries", json={"name": "  ", "content": ""}, headers=AUTH)
    assert r.status_code == 422  # 空名 → 校验失败


def test_update_glossary_404_for_missing(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.put("/api/glossaries/nope", json={"name": "X", "content": ""}, headers=AUTH)
    assert r.status_code == 404


def test_create_job_passes_glossary_id(monkeypatch):
    # glossary_id 须是发起用户自己名下的库，才会被存进 job（归属校验，见 E4）
    client, js, _ = _client(monkeypatch)
    gid = client.post("/api/glossaries", json={"name": "项目A", "content": ""}, headers=AUTH).json()["id"]
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "recording_type": "meeting", "glossary_id": gid},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert js.jobs[r.json()["jobId"]]["glossary_id"] == gid


def test_create_job_no_glossary_id_is_none(monkeypatch):
    client, js, _ = _client(monkeypatch)
    r = client.post("/api/jobs", files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")}, headers=AUTH)
    assert js.jobs[r.json()["jobId"]]["glossary_id"] is None


def test_create_job_rejects_other_users_glossary(monkeypatch):
    # 知道/撞对别人术语库 UUID 不能蹭用——422，且不建 job/不落 R2（防孤儿）
    client, js, blob = _client(monkeypatch)
    api.glossary.rows["g-other"] = dict(id="g-other", email="other@x.com", name="别人的库",
                                        language=None, content="FD ｜ 泄露", updated_at="2026-06-23T00:00:00+00:00")
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "recording_type": "meeting", "glossary_id": "g-other"},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert js.jobs == {}
    assert blob.store == {}


def test_create_job_rejects_non_uuid_glossary_id_as_422_not_500(monkeypatch):
    client, js, _ = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "recording_type": "meeting", "glossary_id": "not-a-uuid"},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert js.jobs == {}


def test_create_job_dispatches_to_fly_when_enabled(monkeypatch):
    # FLY_DISPATCH 开：建 job 后即派单起 Fly 机器
    client, js, _ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "FLY_DISPATCH", True)
    dispatched = {"n": 0}
    monkeypatch.setattr(api.fly_machines, "dispatch_pending",
                        lambda: dispatched.update(n=dispatched["n"] + 1) or 1)
    r = client.post("/api/jobs", files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")}, headers=AUTH)
    assert r.status_code == 200 and r.json()["jobId"]
    assert dispatched["n"] == 1


def test_create_job_no_dispatch_when_disabled(monkeypatch):
    # FLY_DISPATCH 关（缺省）：不派单，走本地 worker 池 claim（行为不变）
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "FLY_DISPATCH", False)
    called = {"x": False}
    monkeypatch.setattr(api.fly_machines, "dispatch_pending", lambda: called.update(x=True))
    r = client.post("/api/jobs", files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")}, headers=AUTH)
    assert r.status_code == 200
    assert called["x"] is False


def test_create_job_dispatch_failure_does_not_block(monkeypatch):
    # 派单失败（Fly 挂了）不挡建 job——job 已 queued，看门狗会补派
    client, js, _ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "FLY_DISPATCH", True)

    def boom():
        raise RuntimeError("fly down")

    monkeypatch.setattr(api.fly_machines, "dispatch_pending", boom)
    r = client.post("/api/jobs", files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")}, headers=AUTH)
    assert r.status_code == 200 and r.json()["jobId"]


def test_create_job_passes_phonecall(monkeypatch):
    client, js, _ = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "recording_type": "phonecall"},
        headers=AUTH,
    )
    jid = r.json()["jobId"]
    assert js.jobs[jid]["recording_type"] == "phonecall"


def test_create_job_defaults_recording_type_to_all(monkeypatch):
    """前端 2026-08-18 起不再传 recording_type（场景分流已取消），落库应是 "all"。

    默认值特意不沿用 "meeting"：那会把「用户根本没选过」的新单在运营舱里显示成
    「他选了现场面访」。"all" 让新旧单一眼分得开。字段本身不再影响选路。
    """
    client, js, _ = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh"},   # 不带 recording_type（＝现在前端的行为）
        headers=AUTH,
    )
    jid = r.json()["jobId"]
    assert js.jobs[jid]["recording_type"] == "all"


def test_create_job_accepts_video(monkeypatch):
    # 接收视频：P0 会抽音频，故上传阶段放行 .mp4，原样流式存进对象存储
    client, js, blob = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("interview.mp4", io.BytesIO(b"VIDEO"), "video/mp4")},
        data={"lang": "zh"},
        headers=AUTH,
    )
    assert r.status_code == 200
    jid = r.json()["jobId"]
    assert js.jobs[jid]["audio_key"].endswith(".mp4")
    assert blob.store[js.jobs[jid]["audio_key"]] == b"VIDEO"


def test_create_job_rejects_unsupported_format(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post(
        "/api/jobs",
        files={"file": ("notes.txt", io.BytesIO(b"X"), "text/plain")},
        data={"lang": "zh"},
        headers=AUTH,
    )
    assert r.status_code == 415


def test_create_job_ignores_client_reported_duration_for_limit_check(monkeypatch):
    # 时长上限只信服务端 ffprobe 实测，前端表单谎报再离谱也不再触发 413（见 B0：服务端强制上限）
    client, *_ = _client(monkeypatch)   # 默认桩 _probe_duration_sec 回 40s，远低于上限
    r = client.post(
        "/api/jobs",
        files={"file": ("a.m4a", io.BytesIO(b"AUDIO"), "audio/mp4")},
        data={"lang": "zh", "duration_sec": str(10 ** 9)},   # 前端谎报远超上限
        headers=AUTH,
    )
    assert r.status_code == 200   # 真实（探测）时长才是准绳，不是前端说了算


def test_get_job_status(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="running", phase="P1", progress=15)
    r = client.get(f"/api/jobs/{jid}", headers=AUTH)
    assert r.json() == {"status": "running", "phase": "P1", "progress": 15, "error": None, "metrics": None}


def test_get_job_status_metrics_hide_vendors_and_cost(monkeypatch):
    """metrics 只出进度数字：供应商 tag 与每单成本不得经此接口下发给用户。

    orchestrator 往 metrics 里塞了三样内部数据——engines/primary（各 ASR 供应商逐路状态）、
    cost（人民币成本，用户付 $0.5~1.0/分钟，一减就知道毛利）、cost.p3_engine（opus/flash，
    既是供应商也撞去 AI 化红线）。这个接口是进度轮询用的，前端拿它渲染进度页，
    整块下发等于把供应链和毛利摆进浏览器 devtools。
    """
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", phase="done", progress=100, metrics={
        "durationSec": 600, "elapsedSec": 320, "chars": 9829, "finalSegs": 468,
        "primary": {"tag": "ELV", "done": 4},
        "engines": {"ELV": {"status": "done"}, "XF": {"status": "failed"}},
        "cost": {"p1": {"ELV": 1.2, "XF": 0.3}, "p3": 0.5, "p3_engine": "opus", "total": 2.0},
    })
    m = client.get(f"/api/jobs/{jid}", headers=AUTH).json()["metrics"]
    assert m == {"durationSec": 600, "elapsedSec": 320, "chars": 9829, "finalSegs": 468}
    blob = json.dumps(m)
    for leak in ("ELV", "XF", "opus", "cost", "total"):
        assert leak not in blob, f"metrics 泄漏了 {leak}"


def test_get_job_status_error_is_public_not_internal(monkeypatch):
    # 用户侧只看脱敏话术；内部 traceback（含路径/供应商报错）不得经此接口泄露给用户
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(
        status="failed",
        error="Traceback (most recent call last):\n  File \"/app/pipeline/orchestrator.py\", line 42\norganization_balance_exhausted",
        error_public="转录失败，请重试",
    )
    r = client.get(f"/api/jobs/{jid}", headers=AUTH)
    assert r.json()["error"] == "转录失败，请重试"
    assert "Traceback" not in r.json()["error"]


def test_read_endpoints_require_auth(monkeypatch):
    # 上线安全底线：状态/取稿/复核/音频/导出/修订全部要登录，裸链接一律 401
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "你好", "sp": "主持人"}]).encode())
    blob.put_bytes("audio/x.m4a", b"\x00\x00")
    for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/result", f"/api/jobs/{jid}/review",
                 f"/api/jobs/{jid}/audio", f"/api/jobs/{jid}/export.docx", f"/api/jobs/{jid}/export.txt"):
        assert client.get(path).status_code == 401, path
    assert client.put(f"/api/jobs/{jid}/transcript", json={"segments": []}).status_code == 401


def test_job_endpoints_404_for_other_user(monkeypatch):
    # 归属校验：别人的任务对我等同不存在（404，不暴露存在性）
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "other@x.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "你好", "sp": "主持人"}]).encode())
    blob.put_bytes("audio/x.m4a", b"\x00\x00")
    for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/result", f"/api/jobs/{jid}/review",
                 f"/api/jobs/{jid}/audio", f"/api/jobs/{jid}/export.docx", f"/api/jobs/{jid}/export.txt"):
        assert client.get(path, headers=AUTH).status_code == 404, path
    assert client.put(f"/api/jobs/{jid}/transcript", json={"segments": []}, headers=AUTH).status_code == 404


def test_legacy_job_without_owner_readable_when_signed_in(monkeypatch):
    # P1 之前的旧任务没有 user_email：登录用户可读（本地遗留数据兼容）
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", None)
    js.jobs[jid].update(status="running", phase="P1", progress=15)
    assert client.get(f"/api/jobs/{jid}", headers=AUTH).status_code == 200


def test_cookie_session_allows_audio_and_export(monkeypatch):
    # <audio> 标签和下载链接带不了 Authorization 头 → 登录时下发的 HttpOnly Cookie 要能独立鉴权
    client, js, blob = _client(monkeypatch)
    client.post("/api/auth/request-code", json={"email": "new@user.com"})
    r = client.post("/api/auth/verify", json={"email": "new@user.com", "code": "654321"})
    assert "tx_session" in r.cookies or "tx_session" in client.cookies  # 登录响应种了 Cookie
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "new@user.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "你好", "sp": "主持人"}]).encode())
    blob.put_bytes("audio/x.m4a", b"\x00\x00")
    # 不带 Authorization 头，仅靠 client 留存的 Cookie
    assert client.get(f"/api/jobs/{jid}/audio").status_code == 200
    assert client.get(f"/api/jobs/{jid}/export.txt").status_code == 200
    # 退出后 Cookie 失效
    client.post("/api/auth/logout")
    assert client.get(f"/api/jobs/{jid}/audio").status_code == 401


def test_get_result_when_done(monkeypatch):
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "你好", "sp": "主持人"}]).encode())
    r = client.get(f"/api/jobs/{jid}/result", headers=AUTH)
    assert r.json()[0]["s"] == "你好"


def test_get_result_409_when_not_done(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    assert client.get(f"/api/jobs/{jid}/result", headers=AUTH).status_code == 409


def test_export_docx(monkeypatch):
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "你好", "sp": "主持人"}]).encode())
    r = client.get(f"/api/jobs/{jid}/export.docx", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.content[:2] == b"PK"   # docx 是 zip 容器，PK 开头


def test_export_docx_409_when_not_done(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    assert client.get(f"/api/jobs/{jid}/export.docx", headers=AUTH).status_code == 409


def test_export_docx_sets_attachment_filename(monkeypatch):
    # 文件名必须由响应头下发（浏览器对前端 download 属性不可靠）；中文名走 RFC5987
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "你好", "sp": "主持人"}]).encode())
    cd = client.get(f"/api/jobs/{jid}/export.docx", params={"name": "会议录音"}, headers=AUTH).headers["content-disposition"]
    assert cd.startswith("attachment")
    assert "filename*=UTF-8''" in cd  # 中文名编码


def test_export_txt(monkeypatch):
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([
        {"t": "00:00:01", "s": "你好", "sp": "主持人"},
        {"t": "00:00:05", "s": "在的", "sp": "被访者"},
    ]).encode())
    r = client.get(f"/api/jobs/{jid}/export.txt", params={"name": "demo"}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.content.decode("utf-8")
    assert "主持人：你好" in body and "被访者：在的" in body
    assert 'filename="demo.txt"' in r.headers["content-disposition"]


def test_disposition_sanitizes_header_breaking_chars():
    # name 是用户可控参数；含引号/换行不得破坏或注入 Content-Disposition 头
    from app.api import _disposition
    cd = _disposition('a"b\r\nc.txt')["Content-Disposition"]
    assert "\r" not in cd and "\n" not in cd      # 无 CRLF 注入
    assert cd.count('"') == 2                       # 只剩包裹文件名的一对引号，注入的裸引号已消毒
    assert 'filename="abc.txt"' in cd               # 引号/控制符被剔除
    assert "filename*=UTF-8''" in cd                # RFC5987 原名仍保留（quote 已百分号编码，安全）


def test_export_txt_409_when_not_done(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    assert client.get(f"/api/jobs/{jid}/export.txt", headers=AUTH).status_code == 409


def test_audio_mime_by_extension(monkeypatch):
    # m4a 必须发 audio/mp4，否则浏览器读得到时长却放不动
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    blob.put_bytes("audio/x.m4a", b"\x00\x00")
    r = client.get(f"/api/jobs/{jid}/audio", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4"


def test_audio_expired_returns_410(monkeypatch):
    # 音频 7 天生命周期到期后 R2 删除对象；不能裸 500 刷 Sentry，要 410 让前端明确提示“已过期”
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    r = client.get(f"/api/jobs/{jid}/audio", headers=AUTH)
    assert r.status_code == 410


def test_get_review(monkeypatch):
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes(f"review/{jid}.json", json.dumps([{"type": "entity", "term": "华为"}]).encode())
    r = client.get(f"/api/jobs/{jid}/review", headers=AUTH)
    assert r.status_code == 200 and r.json()[0]["term"] == "华为"


def test_get_review_empty_when_missing(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    r = client.get(f"/api/jobs/{jid}/review", headers=AUTH)
    assert r.status_code == 200 and r.json() == []   # 复核清单缺 → 空


def test_get_review_503_when_blob_broken(monkeypatch):
    # R2 抖动/文件损坏（非“不存在”）不能伪装成“没有要确认的”，要报错让前端重试
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")

    def boom(key):
        raise RuntimeError("R2 抖动")

    monkeypatch.setattr(blob, "get_bytes", boom)
    r = client.get(f"/api/jobs/{jid}/review", headers=AUTH)
    assert r.status_code == 503


def test_review_state_roundtrip(monkeypatch):
    # 复核决策账本：PUT 后 GET 原样读回——否则重登后确认进度归零（修订还在、状态全回退）
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    state = {"occRes": {"doubt:定义": {"0": {"kind": "ok"}}}, "skipped": {}}
    r = client.put(f"/api/jobs/{jid}/review_state", json=state, headers=AUTH)
    assert r.status_code == 200
    assert client.get(f"/api/jobs/{jid}/review_state", headers=AUTH).json() == state
    # 存进 review/ 前缀（复用 30 天生命周期），且不碰复核清单本体
    assert f"review/{jid}.state.json" in blob.store


def test_review_state_empty_when_missing(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    assert client.get(f"/api/jobs/{jid}/review_state", headers=AUTH).json() == {}


def test_review_state_validates(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    # 未完成 → 409
    assert client.put(f"/api/jobs/{jid}/review_state", json={}, headers=AUTH).status_code == 409
    js.jobs[jid].update(status="done", result_key="result/x.json")
    # 非对象 → 422；超大 → 413；不存在的 job → 404
    assert client.put(f"/api/jobs/{jid}/review_state", json=[1, 2], headers=AUTH).status_code == 422
    big = {"occRes": {"k": "x" * 300_000}}
    assert client.put(f"/api/jobs/{jid}/review_state", json=big, headers=AUTH).status_code == 413
    assert client.put("/api/jobs/nope/review_state", json={}, headers=AUTH).status_code == 404


def test_review_state_503_when_blob_broken(monkeypatch):
    # R2 抖动 ≠ 没确认过：伪装成空状态会让前端把旧决策覆盖丢失
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")

    def boom(key):
        raise RuntimeError("R2 抖动")

    monkeypatch.setattr(blob, "get_bytes", boom)
    assert client.get(f"/api/jobs/{jid}/review_state", headers=AUTH).status_code == 503


def test_put_transcript_then_exports_prefer_edited(monkeypatch):
    # P1#7 修订同步：保存修订快照后，导出/取稿都用修订版；原始稿保留不动
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    js.jobs[jid].update(status="done", result_key="result/x.json")
    blob.put_bytes("result/x.json", json.dumps([{"t": "00:00:01", "s": "原始句子", "sp": "主持人"}]).encode())

    r = client.put(f"/api/jobs/{jid}/transcript", json={"segments": [{"t": "00:00:01", "s": "修订后的句子", "sp": "主持人"}]}, headers=AUTH)
    assert r.status_code == 200 and r.json()["segments"] == 1

    assert "修订后的句子" in client.get(f"/api/jobs/{jid}/export.txt", headers=AUTH).text
    assert client.get(f"/api/jobs/{jid}/result", headers=AUTH).json()[0]["s"] == "修订后的句子"
    assert json.loads(blob.store["result/x.json"])[0]["s"] == "原始句子"  # 原始稿未被覆盖


def test_put_transcript_validates(monkeypatch):
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/x", "zh", "meeting", "a@b.com")
    # 未完成 → 409
    assert client.put(f"/api/jobs/{jid}/transcript", json={"segments": []}, headers=AUTH).status_code == 409
    js.jobs[jid].update(status="done", result_key="result/x.json")
    # 缺字段 → 422；不存在的 job → 404
    assert client.put(f"/api/jobs/{jid}/transcript", json={"segments": [{"x": 1}]}, headers=AUTH).status_code == 422
    assert client.put("/api/jobs/nope/transcript", json={"segments": []}, headers=AUTH).status_code == 404


def test_create_glossary_rejects_oversize_content(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/api/glossaries",
                    json={"name": "A", "content": "x" * (config.GLOSSARY_MAX_CHARS + 1)}, headers=AUTH)
    assert r.status_code == 422


def test_glossaries_scoped_to_user(monkeypatch):
    # 多本：A 建的库，另一个用户列表里看不到、也改不动（404）
    client, *_ = _client(monkeypatch)
    gid = client.post("/api/glossaries", json={"name": "我的库", "content": ""}, headers=AUTH).json()["id"]
    api.accounts.sessions["tok2"] = "other@x.com"
    other = {"Authorization": "Bearer tok2"}
    assert client.get("/api/glossaries", headers=other).json()["glossaries"] == []
    assert client.put(f"/api/glossaries/{gid}", json={"name": "X", "content": ""}, headers=other).status_code == 404
    assert client.delete(f"/api/glossaries/{gid}", headers=other).status_code == 404


# ── 充值退款（admin 发起入口；provider adapter 未接入前只到「调用 provider」这一步）──

def test_admin_topups_hidden_from_non_admin(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.get("/api/admin/topups?email=u@x.com").status_code == 404             # 未登录
    assert client.get("/api/admin/topups?email=u@x.com", headers=AUTH).status_code == 404  # 非管理员
    assert client.post("/api/admin/refunds", json={"email": "u@x.com", "ledgerId": 7,
                                                   "amountCents": 100}).status_code == 404


def test_admin_topups_lists_refundable_per_row(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    body = client.get("/api/admin/topups?email=U@X.com", headers=AUTH).json()   # 大小写不敏感
    assert body["topups"][0]["refundableCents"] == 400
    assert client.get("/api/admin/topups", headers=AUTH).status_code == 422     # email 必填


def test_admin_refund_rejects_over_refundable(monkeypatch):
    # 额度在发起时就卡死：不能等 webhook 回来才发现扣不动（那时钱已经退出去了）
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    r = client.post("/api/admin/refunds", headers=AUTH,
                    json={"email": "u@x.com", "ledgerId": 7, "amountCents": 401})
    assert r.status_code == 422 and "400" in r.json()["detail"]


def test_admin_refund_within_quota_reaches_provider_and_never_touches_balance(monkeypatch):
    # adapter 未接入 → 501「没退成」，而不是假装成功；且发起路径一分钱都不许动余额
    client, fake_js, _ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    before = api.accounts.balances.copy()
    r = client.post("/api/admin/refunds", headers=AUTH,
                    json={"email": "u@x.com", "ledgerId": 7, "amountCents": 400})
    assert r.status_code == 501 and "未接入" in r.json()["detail"]
    assert api.accounts.balances == before
    # 发起失败必须把占额释放掉，否则这笔额度被永久占死、以后谁都退不了
    assert all(x["status"] == "failed" for x in api.accounts.refunds.values())


def test_admin_refund_releases_hold_when_provider_explicitly_rejects(monkeypatch):
    # provider 明确回报没受理（4xx 业务错误）= 确定性失败 → 作废、释放占额是安全的
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))

    def _rejected(provider, email, cents, reason="", provider_txn_id=None):
        raise api.payments.RefundRejected("refund amount exceeds charge")

    monkeypatch.setattr(api.payments, "refund", _rejected)
    r = client.post("/api/admin/refunds", headers=AUTH,
                    json={"email": "u@x.com", "ledgerId": 7, "amountCents": 400})
    assert r.status_code == 502 and "已释放" in r.json()["detail"]
    assert all(x["status"] == "failed" for x in api.accounts.refunds.values())


def test_admin_refund_keeps_hold_when_provider_result_is_unknown(monkeypatch):
    """超时 != 失败：请求可能已经到达 provider 并执行成功，只是响应回不来。
    这种**绝不能作废**——作废后成功 webhook 迟到，就成了「钱退了、额度却被释放过」。
    单据保持 pending 等回执，同时告警让人去 provider 后台查。"""
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    alerted = []
    monkeypatch.setattr(api.alerts, "send_admin_alert",
                        lambda subject, text, **kw: alerted.append(subject) or True)

    def _timeout(provider, email, cents, reason="", provider_txn_id=None):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(api.payments, "refund", _timeout)
    r = client.post("/api/admin/refunds", headers=AUTH,
                    json={"email": "u@x.com", "ledgerId": 7, "amountCents": 400})
    assert r.status_code == 502
    assert "状态未知" in r.json()["detail"] and "不要重复发起" in r.json()["detail"]
    assert all(x["status"] == "pending" for x in api.accounts.refunds.values()), "不确定的单必须保持挂起"
    assert alerted, "结果未知必须告警，不能只回一个 502 就算完"


def test_admin_refund_success_path_records_provider_id(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(api.payments, "refund", lambda provider, email, cents, reason="", provider_txn_id=None: "re_123")
    before = api.accounts.balances.copy()
    r = client.post("/api/admin/refunds", headers=AUTH,
                    json={"email": "u@x.com", "ledgerId": 7, "amountCents": 400})
    assert r.status_code == 200 and r.json()["providerRefundId"] == "re_123"
    assert api.accounts.balances == before                     # 发起阶段绝不动余额
    assert api.accounts.refunds[1]["status"] == "pending"      # 等 webhook 落地


def test_admin_refund_rejects_bad_body(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    assert client.post("/api/admin/refunds", headers=AUTH,
                       json={"email": "u@x.com", "ledgerId": 7, "amountCents": 0}).status_code == 422
    assert client.post("/api/admin/refunds", headers=AUTH, json=["nope"]).status_code == 422


# ============ P3 引擎健康度 / 探活（2026-08-07）============

def test_p3_health_hidden_from_non_admin(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.get("/api/admin/p3-health").status_code == 404
    assert client.get("/api/admin/p3-health", headers=AUTH).status_code == 404
    assert client.post("/api/admin/p3-probe").status_code == 404
    assert client.post("/api/admin/p3-probe", headers=AUTH).status_code == 404


def test_p3_health_returns_stats_and_slots(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(api.p3_health, "health", lambda hours=24: {"windowHours": hours, "counts": {}})
    monkeypatch.setattr(api.claude_gate, "active_count", lambda: 2)
    body = client.get("/api/admin/p3-health", headers=AUTH).json()
    assert body["activeSlots"] == 2 and body["slotLimit"] == api.config.CLAUDE_MAX_CONCURRENCY


def test_p3_health_survives_slot_query_failure(monkeypatch):
    # 数不出在飞槽不该让整张健康卡打不开——降级成 null，其余照显
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(api.p3_health, "health", lambda hours=24: {"counts": {}})
    monkeypatch.setattr(api.claude_gate, "active_count",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = client.get("/api/admin/p3-health", headers=AUTH)
    assert r.status_code == 200 and r.json()["activeSlots"] is None


def test_p3_health_clamps_hours(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    seen = {}
    monkeypatch.setattr(api.p3_health, "health", lambda hours=24: seen.setdefault("h", hours) and {})
    client.get("/api/admin/p3-health?hours=0", headers=AUTH)
    assert seen["h"] == 1                      # 0 会让分母恒为 0
    seen.clear()
    monkeypatch.setattr(api.p3_health, "health", lambda hours=24: (seen.__setitem__("h", hours), {})[1])
    client.get("/api/admin/p3-health?hours=99999", headers=AUTH)
    assert seen["h"] == 168


def test_p3_probe_requires_dispatch_mode(monkeypatch):
    # 订阅令牌只发给 Fly 机器：非派单态（Render 本地跑）根本无从探活，明说 501 而不是假装成功
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(api.config, "FLY_DISPATCH", False)
    assert client.post("/api/admin/p3-probe", headers=AUTH).status_code == 501


def test_p3_probe_yields_to_transcription_when_pool_full(monkeypatch):
    # 转录是生意，探活是自查：池满时不许挤掉真实任务
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(api.config, "FLY_DISPATCH", True)
    monkeypatch.setattr(api.config, "FLY_API_TOKEN", "tok")
    monkeypatch.setattr(api.config, "FLY_MAX_MACHINES", 3)
    monkeypatch.setattr(api.fly_machines, "count_running_machines", lambda: 3)
    started = []
    monkeypatch.setattr(api.fly_machines, "start_task_machine",
                        lambda jid, kind=None: started.append(jid))
    assert client.post("/api/admin/p3-probe", headers=AUTH).status_code == 503
    assert started == []


def test_p3_probe_starts_machine_with_probe_kind(monkeypatch):
    client, *_ = _client(monkeypatch)
    monkeypatch.setattr(api.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(api.config, "FLY_DISPATCH", True)
    monkeypatch.setattr(api.config, "FLY_API_TOKEN", "tok")
    monkeypatch.setattr(api.config, "FLY_MAX_MACHINES", 10)
    monkeypatch.setattr(api.fly_machines, "count_running_machines", lambda: 1)
    started = []
    monkeypatch.setattr(api.fly_machines, "start_task_machine",
                        lambda jid, kind=None: started.append((jid, kind)))
    body = client.post("/api/admin/p3-probe", headers=AUTH).json()
    assert body["probeId"].startswith("probe-")
    assert started == [(body["probeId"], "probe")]   # kind 必须是 probe，否则机器会去找 jobs 表


# ══════════════════════════════════════════════════════════════════════════
# 试听音频：按需取字节、边收边发（2026-08-26）
# 此前每来一个 Range 请求就把**整份音频**读进内存再切一段。浏览器拖一次进度条 = 一个
# Range 请求，而复核的用法恰恰是反复点播放、反复定位；4 小时的单音频约 56 MB，
# 后端跑在 Render 免费档、只有 512 MB 内存。
# ══════════════════════════════════════════════════════════════════════════

_AUDIO = bytes(range(256)) * 40        # 10,240 字节，够验各种边界


def _audio_client(monkeypatch):
    client, js, blob = _client(monkeypatch)
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    blob.put_bytes("audio/x.m4a", _AUDIO)
    return client, jid, blob


@pytest.mark.parametrize("header,want_status,want_range,want_len", [
    ("", 200, None, len(_AUDIO)),                                  # 无 Range：200，不带 Content-Range
    ("bytes=0-99", 206, f"bytes 0-99/{len(_AUDIO)}", 100),
    ("bytes=100-", 206, f"bytes 100-{len(_AUDIO)-1}/{len(_AUDIO)}", len(_AUDIO) - 100),
    ("bytes=-50", 206, f"bytes {len(_AUDIO)-50}-{len(_AUDIO)-1}/{len(_AUDIO)}", 50),
    ("bytes=0-999999", 206, f"bytes 0-{len(_AUDIO)-1}/{len(_AUDIO)}", len(_AUDIO)),  # 越界收到末尾
    ("bytes=99999-", 200, None, len(_AUDIO)),                      # 起点越界 → 不可满足，退回整档
    ("banana", 200, None, len(_AUDIO)),                            # 非法头 → 当没给
])
def test_试听音频的range形状(monkeypatch, header, want_status, want_range, want_len):
    """算边界那段代码一个字没改，这里守的是「改了取法之后各种形状仍然对」。"""
    client, jid, _ = _audio_client(monkeypatch)
    r = client.get(f"/api/jobs/{jid}/audio", headers={**AUTH, **({"Range": header} if header else {})})
    assert r.status_code == want_status
    assert r.headers.get("content-range") == want_range
    assert len(r.content) == want_len
    assert r.headers["accept-ranges"] == "bytes"


def test_试听只取要的那几个字节_不再整包读(monkeypatch):
    """核心守卫：一个 Range 请求不许把整个对象读出来。

    判据是**取出来的字节数**，不是「调了哪个函数」——换实现也照样守得住。
    造回 bug 验过：改回 `get_bytes` 整包读，本条即红。
    """
    client, jid, blob = _audio_client(monkeypatch)
    pulled = []
    orig = blob.open_range
    blob.open_range = lambda key, start=None, end=None: (
        pulled.append((start, end)) or orig(key, start, end))
    r = client.get(f"/api/jobs/{jid}/audio", headers={**AUTH, "Range": "bytes=5000-5099"})
    assert r.status_code == 206 and len(r.content) == 100
    # 允许一次 1 字节的探长（要总长才判得了 Range 合法性），但正文那次必须是窄范围
    assert (5000, 5099) in pulled, "没有按请求的范围去取"
    assert not any(s is None for s, _ in pulled), "还在整包读"


def test_试听带私有缓存头_让浏览器自己存一份(monkeypatch):
    """音频一转完就不再变，最适合让浏览器缓存——此前一个缓存头都没发。

    `private` 不能省：`/api/*` 经 Vercel 转发到 Render，不声明的话边缘可能缓存别人的录音。
    有效期要短于对象剩余寿命（7 天后删），免得缓存住一份已经不存在的音频。
    """
    client, jid, _ = _audio_client(monkeypatch)
    cc = client.get(f"/api/jobs/{jid}/audio", headers=AUTH).headers["cache-control"]
    assert "private" in cc
    assert 0 < int(re.search(r"max-age=(\d+)", cc).group(1)) <= 7 * 86400


def test_音频过期仍然回410(monkeypatch):
    """前端靠 410/404 进「优雅只读态」——改取法时最容易顺手改掉的就是这条路径。"""
    client, js, _ = _client(monkeypatch)
    jid = js.create_job("audio/gone.m4a", "zh", "meeting", "a@b.com")
    assert client.get(f"/api/jobs/{jid}/audio", headers=AUTH).status_code == 410


def test_回放音频转码带faststart(monkeypatch):
    """不加的话索引写在文件末尾，浏览器要先读头、再回头读尾，白白多一趟往返。

    ⚠️ 判据是**真实跑出来的命令行**，不是源码文本。第一版写成 `"+faststart" in getsource(...)`，
    结果把 flag 从命令里删掉它照样绿——因为 getsource 连文档字符串一起取，而注释里正好
    解释着这个 flag。造回 bug 时才发现是假绿（2026-08-26）。
    """
    from app import worker

    seen = {}
    monkeypatch.setattr(worker.subprocess, "run",
                        lambda cmd, **kw: seen.update(cmd=cmd) or types.SimpleNamespace(returncode=0))
    worker._transcode_to_m4a("/tmp/in.flac", "/tmp/out.m4a")
    cmd = seen["cmd"]
    assert "-movflags" in cmd and cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert cmd[-1] == "/tmp/out.m4a", "flag 要在输出路径之前，否则 ffmpeg 不认"


def test_retry_failed_job_creates_a_new_job(monkeypatch):
    """「重试」= 用原录音另起一单。2026-09-03 生产实见：端点里用了 db.connect 却从没 import，
    每一次点重试都是 500（从 08-15 上线起 18 天没人能重试，且无测试覆盖）。
    这里把 db 打成桩，钉住「端点能走到建单」这件事。"""
    client, js, blob = _client(monkeypatch)
    row = ("audio/x.m4a", "zh", "phonecall", "a.m4a", 120, None, 5, None, None, "failed", "zh")

    class _Conn:
        def execute(self, *a, **k):
            class _R:
                def fetchone(self_inner):
                    return row
            return _R()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _Db:
        def connect(self):
            return _Conn()

    monkeypatch.setattr(api, "db", _Db())
    blob.put_bytes("audio/x.m4a", b"x")
    if not hasattr(blob, "exists"):
        blob.exists = lambda key: key == "audio/x.m4a"
    r = client.post("/api/jobs/00000000-0000-0000-0000-000000000001/retry?ui_lang=zh", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["jobId"]


def test_pending_topups_endpoint(monkeypatch):
    """/api/topups/pending（2026-09-06）：没接 Paddle 时空列表；接了就问 paddle_pay.pending_for。"""
    client, *_ = _client(monkeypatch)
    client.post("/api/auth/request-code", json={"email": "p@user.com"})
    tok = client.post("/api/auth/verify", json={"email": "p@user.com", "code": "654321"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    r = client.get("/api/topups/pending", headers=h)
    assert r.status_code == 200 and r.json()["items"] == []
    monkeypatch.setenv("PADDLE_API_KEY", "k")
    import app.paddle_pay as pp
    monkeypatch.setattr(pp, "pending_for", lambda email: [{"txnId": "txn_1", "amountCents": 1000, "createdAt": "x", "status": "attempted"}])
    r = client.get("/api/topups/pending", headers=h)
    assert r.status_code == 200 and r.json()["items"][0]["txnId"] == "txn_1"


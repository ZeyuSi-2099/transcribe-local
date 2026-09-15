"""后处理 REST API 单测（路由/状态码/归属/前置/幂等；DB 真逻辑见 test_postprocess_db_integration）。

装配复用 test_api 的 FakeJobStore/FakeBlob/FakeAccounts；postprocess 模块换成内存版
FakePostprocess——校验函数委托真模块（口径一致），CRUD/状态机返回契约同真模块。

归类（2026-08-17 下架）留下的只读兼容面在 test_start_rejects_retired_categorize 里守着。
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.api as api
import app.postprocess as pp_mod
from test_api import AUTH, FakeAccounts, FakeBlob, FakeGlossary, FakeJobStore, pricing  # 本机版：pricing 是占位（计费不搬）


class FakePostprocess:
    """内存版 postprocess：validate/normalize 委托真模块，数据存 dict。"""

    STEPS = pp_mod.STEPS
    ERROR_PUBLIC = pp_mod.ERROR_PUBLIC

    def __init__(self):
        self.lists = {}      # id -> {id,email,name,content,updatedAt}
        self.jobs = {}       # job_id -> pp job dict（模块内部形状，snake_case）
        self.n = 0

    # 委托真模块（保证校验口径一致）
    def normalize_steps(self, steps):
        return pp_mod.normalize_steps(steps)

    # ── redact lists ──
    def list_redact_lists(self, email):
        return [{k: r[k] for k in ("id", "name", "content", "updatedAt")}
                for r in self.lists.values() if r["email"] == email]

    def create_redact_list(self, email, name, content):
        if (e := pp_mod.validate_name(name)) is not None:
            return e
        if (e := pp_mod.validate_list(content)) is not None:
            return e
        if len([r for r in self.lists.values() if r["email"] == email]) >= pp_mod.MAX_LISTS:
            return f"too many: limit {pp_mod.MAX_LISTS}"
        self.n += 1
        rid = f"l{self.n}"
        self.lists[rid] = dict(id=rid, email=email, name=name.strip(), content=content,
                               updatedAt="2026-07-24T00:00:00+00:00")
        return {k: self.lists[rid][k] for k in ("id", "name", "content", "updatedAt")}

    def update_redact_list(self, email, rid, name, content):
        if (e := pp_mod.validate_name(name)) is not None:
            return e
        if (e := pp_mod.validate_list(content)) is not None:
            return e
        r = self.lists.get(rid)
        if not r or r["email"] != email:
            return None
        r.update(name=name.strip(), content=content)
        return {k: r[k] for k in ("id", "name", "content", "updatedAt")}

    def delete_redact_list(self, email, rid):
        r = self.lists.get(rid)
        if not r or r["email"] != email:
            return False
        del self.lists[rid]
        return True

    def get_redact_list(self, email, rid):
        r = self.lists.get(rid)
        if not r or r["email"] != email:
            return None
        return {k: r[k] for k in ("id", "name", "content", "updatedAt")}

    # ── pp jobs ──
    def get_pp_job(self, job_id):
        return self.jobs.get(job_id)

    def create_or_reset(self, job_id, email, steps, price_cents, redact_list_id=None,
                        list_name=None, rate_cents_per_min=None, audio_seconds=None,
                        ui_lang=None):
        self.last_ui_lang = ui_lang
        cur = self.jobs.get(job_id)
        if cur and cur["status"] in ("queued", "running"):
            return False
        self.jobs[job_id] = {
            "job_id": job_id, "user_email": email, "steps": steps,
            "profile_id": None, "redact_list_id": redact_list_id,
            "profile_name": None, "list_name": list_name,
            "status": "queued", "current_step": None, "step_index": 0,
            "price_cents": price_cents, "qc_fix_count": 0, "products": [],
            "pp_rate_cents_per_min": rate_cents_per_min, "audio_seconds": audio_seconds,
            "has_qc": False, "failed_step": None, "error_public": None,
            "attempts": 0, "updated_at": "2026-07-24T00:00:00+00:00",
        }
        return True

    def job_summaries(self, email):
        out = {}
        for jid, r in self.jobs.items():
            if r["user_email"] == email:
                out[jid] = {"status": r["status"], "stepIndex": r["step_index"],
                            "totalSteps": len(r["steps"]), "currentStep": r["current_step"],
                            "qcFixCount": r["qc_fix_count"], "products": r["products"]}
        return out


def _client(monkeypatch):
    fake_js, fake_blob, fake_acc, fake_pp = FakeJobStore(), FakeBlob(), FakeAccounts(), FakePostprocess()
    fake_acc.jobstore = fake_js
    monkeypatch.setattr(api, "jobstore", fake_js)
    monkeypatch.setattr(api, "blobstore", fake_blob)
    monkeypatch.setattr(api, "accounts", fake_acc, raising=False)   # 本机版 api 没有 accounts，只给不适用的用例留着
    monkeypatch.setattr(api.local, "EMAIL", "a@b.com")               # 本机版：当前用户固定是 local.EMAIL
    monkeypatch.setattr(api, "glossary", FakeGlossary())
    monkeypatch.setattr(api, "postprocess", fake_pp)
    return TestClient(api.app), fake_js, fake_blob, fake_pp


def _done_job(js, email="a@b.com", duration_sec=600):
    jid = js.create_job("audio/x.m4a", "zh", "meeting", email, file_name="访谈.m4a")
    js.jobs[jid].update(status="done", result_key=f"result/{jid}.json", duration_sec=duration_sec)
    return jid


# ── 配置 CRUD ──

def test_profile_routes_are_gone(monkeypatch):
    """归类方案 CRUD 整套下线：路由不存在（404），不是「存在但空」。"""
    client, *_ = _client(monkeypatch)
    assert client.get("/api/postprocess/profiles", headers=AUTH).status_code == 404
    assert client.post("/api/postprocess/profiles", json={"name": "x"}, headers=AUTH).status_code == 404


def test_redact_lists_crud_flow(monkeypatch):
    client, *_ = _client(monkeypatch)
    r = client.post("/api/postprocess/redact-lists", json={"name": "竞品", "content": "词A\n词B"},
                    headers=AUTH)
    assert r.status_code == 200
    rid = r.json()["id"]
    lst = client.get("/api/postprocess/redact-lists", headers=AUTH).json()["lists"]
    assert lst[0]["id"] == rid and lst[0]["content"] == "词A\n词B"
    assert client.put(f"/api/postprocess/redact-lists/{rid}",
                      json={"name": "竞品2", "content": "词C"}, headers=AUTH).status_code == 200
    assert client.delete(f"/api/postprocess/redact-lists/{rid}", headers=AUTH).status_code == 200


def test_pp_config_requires_auth(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.get("/api/postprocess/redact-lists").status_code == 401
    assert client.post("/api/postprocess/redact-lists", json={"name": "x"}).status_code == 401


def test_list_cap_409(monkeypatch):
    client, _, _, fake_pp = _client(monkeypatch)
    monkeypatch.setattr(pp_mod, "MAX_LISTS", 1)
    assert client.post("/api/postprocess/redact-lists", json={"name": "l0"}, headers=AUTH).status_code == 200
    assert client.post("/api/postprocess/redact-lists", json={"name": "l1"}, headers=AUTH).status_code == 409


def test_list_validation_422(monkeypatch):
    client, *_ = _client(monkeypatch)
    assert client.post("/api/postprocess/redact-lists", json={"name": "  "}, headers=AUTH).status_code == 422
    big = "x" * (api.config.PP_LIST_MAX_CHARS + 1)
    assert client.post("/api/postprocess/redact-lists",
                       json={"name": "A", "content": big}, headers=AUTH).status_code == 422
    # 允许空清单
    assert client.post("/api/postprocess/redact-lists", json={"name": "B"}, headers=AUTH).status_code == 200


def test_pp_config_scoped_to_user(monkeypatch):
    # 归属校验：别人的清单等同不存在（404）
    client, *_ = _client(monkeypatch)
    rid = client.post("/api/postprocess/redact-lists", json={"name": "我的"}, headers=AUTH).json()["id"]
    api.accounts.sessions["tok2"] = "other@x.com"
    other = {"Authorization": "Bearer tok2"}
    assert client.get("/api/postprocess/redact-lists", headers=other).json()["lists"] == []
    assert client.put(f"/api/postprocess/redact-lists/{rid}",
                      json={"name": "X", "content": ""}, headers=other).status_code == 404
    assert client.delete(f"/api/postprocess/redact-lists/{rid}", headers=other).status_code == 404


# ── 发起前置 ──

def _start(client, jid, body=None, headers=AUTH):
    return client.post(f"/api/jobs/{jid}/postprocess",
                       json=body if body is not None else {"steps": ["narrate"]}, headers=headers)


def test_start_requires_done_job(monkeypatch):
    client, js, *_ = _client(monkeypatch)
    jid = js.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")   # queued
    assert _start(client, jid).status_code == 409


def test_start_404_for_other_users_job(monkeypatch):
    client, js, *_ = _client(monkeypatch)
    jid = _done_job(js, email="other@x.com")
    assert _start(client, jid).status_code == 404


def test_start_requires_auth(monkeypatch):
    client, js, *_ = _client(monkeypatch)
    jid = _done_job(js)
    assert client.post(f"/api/jobs/{jid}/postprocess", json={"steps": ["narrate"]}).status_code == 401


def test_start_rejects_bad_steps(monkeypatch):
    client, js, *_ = _client(monkeypatch)
    jid = _done_job(js)
    assert _start(client, jid, {"steps": []}).status_code == 422
    assert _start(client, jid, {"steps": ["excel"]}).status_code == 422
    assert _start(client, jid, {}).status_code == 422


def test_start_rejects_retired_categorize(monkeypatch):
    """已下架的步骤送进来一律 422——**不做兼容放行**。

    放行等于 pp_runner 还得留着归类那条执行链，就没拆干净；而存量失败单点「重试」时
    前端已把它滤掉（见 PostprocessCard.test.tsx「存量任务」一组），正常路径不会撞到这里。"""
    client, js, *_ = _client(monkeypatch)
    jid = _done_job(js)
    assert _start(client, jid, {"steps": ["categorize"]}).status_code == 422
    assert _start(client, jid, {"steps": ["narrate", "categorize"]}).status_code == 422
    # 滤掉之后是合法子集 → 放行
    assert _start(client, jid, {"steps": ["narrate"]}).status_code == 200


def test_start_rejects_foreign_redact_list(monkeypatch):
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js)
    fake_pp.lists["l-other"] = dict(id="l-other", email="other@x.com", name="别人的",
                                    content="x", updatedAt="2026-07-24T00:00:00+00:00")
    assert _start(client, jid, {"steps": ["redact"], "redactListId": "l-other"}).status_code == 422
    # 不带清单 = 智能识别，放行
    assert _start(client, jid, {"steps": ["redact"]}).status_code == 200


def test_start_writes_inputs_snapshot(monkeypatch):
    # 清单内容在发起那一刻快照进 R2（此后改配置不影响在途任务）
    client, js, blob, _ = _client(monkeypatch)
    jid = _done_job(js)
    rid = client.post("/api/postprocess/redact-lists", json={"name": "清单", "content": "保密词"},
                      headers=AUTH).json()["id"]
    r = _start(client, jid, {"steps": ["narrate", "redact"], "redactListId": rid})
    assert r.status_code == 200 and r.json() == {"ok": True}
    snap = json.loads(blob.store[f"postprocess/{jid}/inputs.json"])
    assert snap["steps"] == ["narrate", "redact"]
    assert snap["redactList"]["content"] == "保密词"
    assert "profile" not in snap


def test_start_conflicts_while_in_flight_and_allows_rerun_after_terminal(monkeypatch):
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js)
    assert _start(client, jid).status_code == 200
    assert _start(client, jid).status_code == 409          # queued 在途 → 409
    fake_pp.jobs[jid]["status"] = "running"
    assert _start(client, jid).status_code == 409          # running → 409
    fake_pp.jobs[jid]["status"] = "failed"
    assert _start(client, jid).status_code == 200          # failed 重发 = 同行重置重跑
    fake_pp.jobs[jid]["status"] = "done"
    assert _start(client, jid, {"steps": ["redact"]}).status_code == 200   # done 重发 = 重跑
    assert fake_pp.jobs[jid]["steps"] == ["redact"]        # 重置后按新勾选


def test_start_snapshots_price_and_checks_balance(monkeypatch):
    # 发起快照 rate + 时长 + 全额；金额从价目表推，不写死（2026-08-31 定价 V3 已改过一次）
    rate = pricing.PP_STEP_RATES_CENTS["narrate"]
    price = pricing.postprocess_price_cents(["narrate"], 600)
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js, duration_sec=600)
    api.accounts.balances["a@b.com"] = price - 1
    assert _start(client, jid).status_code == 402
    assert fake_pp.jobs == {}
    api.accounts.balances["a@b.com"] = price
    assert _start(client, jid).status_code == 200
    assert fake_pp.jobs[jid]["price_cents"] == price
    assert fake_pp.jobs[jid]["pp_rate_cents_per_min"] == rate
    assert fake_pp.jobs[jid]["audio_seconds"] == 600


def test_start_free_quota_covers_balance_gate(monkeypatch):
    # 免费额度也能抵后处理（2026-08-02 决策）：剩余秒覆盖全部时长 → 零余额也能发起；
    # 快照仍是全额（结算时才按当刻额度抵扣）
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js, duration_sec=600)
    api.accounts.free_left["a@b.com"] = 600
    api.accounts.balances["a@b.com"] = 0
    assert _start(client, jid).status_code == 200
    assert fake_pp.jobs[jid]["price_cents"] == pricing.postprocess_price_cents(["narrate"], 600)


def test_start_long_file_can_still_use_the_free_quota(monkeypatch):
    """2026-08-15 去掉「免费额度只能用于 ≤90 分钟」那道闸后，长文件的后处理也能用免费额度。

    此前这里断言的是 402（余额 0 就被拒），而用户明明还有额度——闸把它挡在外面了。"""
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js, duration_sec=3 * 3600)     # 3 小时，远超原来的 90 分钟闸
    api.accounts.free_left["a@b.com"] = 10**6      # 额度充足
    api.accounts.balances["a@b.com"] = 0           # 余额为 0：能过就说明是额度抵的
    assert _start(client, jid).status_code == 200


def test_start_dispatches_when_fly_enabled(monkeypatch):
    client, js, *_ = _client(monkeypatch)
    jid = _done_job(js)
    monkeypatch.setattr(api.config, "FLY_DISPATCH", True)
    called = {"n": 0}
    monkeypatch.setattr(api.fly_machines, "dispatch_pending",
                        lambda: called.update(n=called["n"] + 1) or 1)
    assert _start(client, jid).status_code == 200
    assert called["n"] == 1


def test_start_dispatch_failure_does_not_block(monkeypatch):
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js)
    monkeypatch.setattr(api.config, "FLY_DISPATCH", True)

    def boom():
        raise RuntimeError("fly down")

    monkeypatch.setattr(api.fly_machines, "dispatch_pending", boom)
    assert _start(client, jid).status_code == 200          # 已排队，看门狗补派
    assert jid in fake_pp.jobs


# ── 状态查询 ──

def test_get_postprocess_null_when_none(monkeypatch):
    """没跑过后处理 = 200 + null，不是 404。
    404 会让浏览器控制台每打开一次这类详情页就多一条 error，并污染前端错误上报——
    而「还没跑过」是绝大多数任务的常态，不是错误。"""
    client, js, *_ = _client(monkeypatch)
    jid = _done_job(js)
    r = client.get(f"/api/jobs/{jid}/postprocess", headers=AUTH)
    assert r.status_code == 200
    assert r.json() is None


def test_get_postprocess_contract_shape(monkeypatch):
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js)
    rid = client.post("/api/postprocess/redact-lists", json={"name": "清单A", "content": "张三"},
                      headers=AUTH).json()["id"]
    _start(client, jid, {"steps": ["narrate", "redact"], "redactListId": rid})
    fake_pp.jobs[jid].update(status="running", current_step="redact", step_index=2)
    body = client.get(f"/api/jobs/{jid}/postprocess", headers=AUTH).json()
    assert body["status"] == "running" and body["stepIndex"] == 2 and body["totalSteps"] == 2
    assert body["currentStep"] == "redact" and body["listName"] == "清单A"
    assert body["steps"] == ["narrate", "redact"]   # 本机版（与线上不同）：不收费，不核对 priceCents
    # 完成态：products 带 kind+中文名
    fake_pp.jobs[jid].update(status="done", products=["narrate", "redact"],
                             qc_fix_count=4, has_qc=True)
    body = client.get(f"/api/jobs/{jid}/postprocess", headers=AUTH).json()
    assert body["products"] == [{"kind": "narrate", "name": "叙述稿"},
                                {"kind": "redact", "name": "脱敏稿"}]
    assert body["qcFixCount"] == 4 and body["hasQcReport"] is True


def test_legacy_categorize_product_still_named(monkeypatch):
    """存量任务的归类产物仍要显示中文名——摘掉映射会让老单退成裸英文 kind。"""
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js)
    _start(client, jid, {"steps": ["narrate"]})
    fake_pp.jobs[jid].update(status="done", products=["narrate", "categorize"],
                             profile_name="高管客户")
    body = client.get(f"/api/jobs/{jid}/postprocess", headers=AUTH).json()
    assert body["products"] == [{"kind": "narrate", "name": "叙述稿"},
                                {"kind": "categorize", "name": "归类纪要"}]
    assert body["profileName"] == "高管客户"


def test_get_postprocess_404_other_user(monkeypatch):
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js, email="other@x.com")
    fake_pp.jobs[jid] = {"job_id": jid, "user_email": "other@x.com", "steps": ["narrate"],
                         "status": "done", "current_step": None, "step_index": 1,
                         "price_cents": 0, "qc_fix_count": 0, "products": ["narrate"],
                         "has_qc": False, "failed_step": None, "profile_name": None,
                         "list_name": None, "updated_at": "2026-07-24T00:00:00+00:00"}
    assert client.get(f"/api/jobs/{jid}/postprocess", headers=AUTH).status_code == 404


# ── 产物 / QC 下载 ──

def _done_pp(client, js, blob, fake_pp, steps=("narrate",)):
    jid = _done_job(js)
    _start(client, jid, {"steps": list(steps)})
    fake_pp.jobs[jid].update(status="done", products=list(steps), has_qc=True)
    for s in steps:
        blob.put_bytes(f"postprocess/{jid}/{s}.md", "# 标题\n\n正文段".encode())
    blob.put_bytes(f"postprocess/{jid}/qc.md", "共修复 2 处".encode())
    return jid


def test_product_md_txt_docx(monkeypatch):
    client, js, blob, fake_pp = _client(monkeypatch)
    jid = _done_pp(client, js, blob, fake_pp)
    r = client.get(f"/api/jobs/{jid}/postprocess/product/narrate.md", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "正文段" in r.text
    assert "attachment" in r.headers["content-disposition"]
    r = client.get(f"/api/jobs/{jid}/postprocess/product/narrate.txt", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    r = client.get(f"/api/jobs/{jid}/postprocess/product/narrate.docx", headers=AUTH)
    assert r.status_code == 200 and r.content[:2] == b"PK"   # docx 是 zip 容器
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_product_filename_from_query_param(monkeypatch):
    client, js, blob, fake_pp = _client(monkeypatch)
    jid = _done_pp(client, js, blob, fake_pp)
    cd = client.get(f"/api/jobs/{jid}/postprocess/product/narrate.md",
                    params={"name": "访谈一稿"}, headers=AUTH).headers["content-disposition"]
    assert "filename*=UTF-8''" in cd


def test_product_404_for_unknown_kind_or_ext(monkeypatch):
    client, js, blob, fake_pp = _client(monkeypatch)
    jid = _done_pp(client, js, blob, fake_pp)   # 只有 narrate 产物
    assert client.get(f"/api/jobs/{jid}/postprocess/product/redact.md", headers=AUTH).status_code == 404
    assert client.get(f"/api/jobs/{jid}/postprocess/product/narrate.pdf", headers=AUTH).status_code == 404


def test_product_410_when_expired(monkeypatch):
    client, js, blob, fake_pp = _client(monkeypatch)
    jid = _done_pp(client, js, blob, fake_pp)
    blob.delete(f"postprocess/{jid}/narrate.md")   # 模拟 30 天生命周期删除
    assert client.get(f"/api/jobs/{jid}/postprocess/product/narrate.md", headers=AUTH).status_code == 410


def test_product_requires_auth_and_ownership(monkeypatch):
    client, js, blob, fake_pp = _client(monkeypatch)
    jid = _done_pp(client, js, blob, fake_pp)
    assert client.get(f"/api/jobs/{jid}/postprocess/product/narrate.md").status_code == 401
    api.accounts.sessions["tok2"] = "other@x.com"
    assert client.get(f"/api/jobs/{jid}/postprocess/product/narrate.md",
                      headers={"Authorization": "Bearer tok2"}).status_code == 404


def test_qc_report_download_and_404(monkeypatch):
    client, js, blob, fake_pp = _client(monkeypatch)
    jid = _done_pp(client, js, blob, fake_pp)
    r = client.get(f"/api/jobs/{jid}/postprocess/qc.md", headers=AUTH)
    assert r.status_code == 200 and "共修复 2 处" in r.text
    blob.delete(f"postprocess/{jid}/qc.md")
    assert client.get(f"/api/jobs/{jid}/postprocess/qc.md", headers=AUTH).status_code == 404


# ── 历史页行内摘要 ──

def test_jobs_list_carries_postprocess_summary(monkeypatch):
    client, js, _, fake_pp = _client(monkeypatch)
    jid = _done_job(js)
    _start(client, jid, {"steps": ["narrate", "redact"]})
    fake_pp.jobs[jid].update(status="running", current_step="redact", step_index=2)
    # 换成带本 job 的行以验证合并逻辑（本机版：列单走 local.list_jobs，线上走 accounts.list_jobs）
    monkeypatch.setattr(api.local, "list_jobs", lambda email: [{"id": jid, "status": "done"}, {"id": "no-pp", "status": "done"}])
    rows = client.get("/api/jobs", headers=AUTH).json()["jobs"]
    assert rows[0]["postprocess"] == {"status": "running", "stepIndex": 2, "totalSteps": 2,
                                      "currentStep": "redact", "qcFixCount": 0, "products": []}
    assert rows[1]["postprocess"] is None   # 无后处理的行 = None（契约）


def test_发起时把界面语言钉在这一单上(monkeypatch):
    """质检报告里「我们写的字」跟发起那一刻的界面语言走。存在单上而不是每次请求带：
    报告是跑的时候一次性写的，看的时候已经晚了。"""
    client, js, _blob, pp = _client(monkeypatch)
    jid = _done_job(js)
    assert _start(client, jid, {"steps": ["narrate"], "uiLang": "ja"}).status_code == 200
    assert pp.last_ui_lang == "ja"


def test_前端没传就留空_由跑的时候回落英文(monkeypatch):
    client, js, _blob, pp = _client(monkeypatch)
    jid = _done_job(js)
    assert _start(client, jid).status_code == 200
    # ⚠️ 不能在这里兜成 "zh"——那样非中文用户拿到的报告会是中文，
    # 而回落规则只有一处（pp_lang），兜两次就会有一处说了不算
    assert pp.last_ui_lang is None

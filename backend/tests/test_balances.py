import json

import pytest

from app import balances, db


@pytest.fixture
def clean():
    """infra 测试专用：建表 + 清空 vendor_balances（本地 docker 库，非生产）。"""
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM vendor_balances")
    yield


@pytest.mark.infra
def test_set_and_list(clean):
    balances.set_balance("DeepSeek", label="DeepSeek", amount_cny=50, threshold_cny=20)
    ds = next(r for r in balances.list_balances() if r["vendor"] == "DeepSeek")
    assert ds["amountCny"] == 50 and ds["thresholdCny"] == 20 and ds["low"] is False


@pytest.mark.infra
def test_set_partial_update_keeps_threshold(clean):
    balances.set_balance("X", amount_cny=100, threshold_cny=30)
    balances.set_balance("X", amount_cny=10)   # 只更新余额 → 阈值应保留
    x = next(r for r in balances.list_balances() if r["vendor"] == "X")
    assert x["amountCny"] == 10 and x["thresholdCny"] == 30 and x["low"] is True   # 10 < 30 → 低水位


@pytest.mark.infra
def test_set_balance_keeps_source_when_unspecified(clean):
    # 改阈值不该把自动查(api)的卡误标手动：不传 source → 保留原 source
    balances.set_balance("DeepSeek", amount_cny=50, source="api")
    balances.set_balance("DeepSeek", threshold_cny=20)   # 只改阈值、不传 source
    d = next(r for r in balances.list_balances() if r["vendor"] == "DeepSeek")
    assert d["source"] == "api" and d["thresholdCny"] == 20   # source 仍 api


@pytest.mark.infra
def test_consume_decrements_not_below_zero(clean):
    # 讯飞按用量扣减：原子递减、不低于 0
    balances.set_balance("讯飞", amount_cny=2.0)
    balances.consume("讯飞", 0.5)
    assert next(r for r in balances.list_balances() if r["vendor"] == "讯飞")["amountCny"] == 1.5
    balances.consume("讯飞", 10)   # 扣到负 → 归 0
    assert next(r for r in balances.list_balances() if r["vendor"] == "讯飞")["amountCny"] == 0


@pytest.mark.infra
def test_check_low_balances_alerts_and_dedups(clean, monkeypatch):
    sent = []
    monkeypatch.setattr(balances.alerts, "send_admin_alert", lambda s, t, **k: sent.append(s) or True)
    balances.set_balance("Y", amount_cny=5, threshold_cny=20)    # 低于阈值
    balances.set_balance("Z", amount_cny=99, threshold_cny=20)   # 充足
    assert balances.check_low_balances() == ["Y"]   # 只告警 Y
    assert len(sent) == 1
    assert balances.check_low_balances() == []      # 冷却内不重复（low_alerted_at 持久去重）


def test_refresh_deepseek_parses_and_stores(monkeypatch):
    # 纯解析单测：mock HTTP + set_balance，不连真 API、不碰库（默认套件可跑）
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    body = json.dumps({"is_available": True,
                       "balance_infos": [{"currency": "CNY", "total_balance": "88.50"}]}).encode()

    class R:
        status = 200
        def read(self): return body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(balances.urllib.request, "urlopen", lambda *a, **k: R())
    saved = {}
    monkeypatch.setattr(balances, "set_balance", lambda *a, **k: saved.update({**k, "vendor": a[0] if a else None}))
    out = balances.refresh_deepseek()
    assert out == {"vendor": "DeepSeek", "amountCny": 88.5}
    assert saved.get("amount_cny") == 88.5 and saved.get("source") == "api"


def test_refresh_deepseek_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert balances.refresh_deepseek() is None


@pytest.mark.infra
def test_seed_classifies_vendors():
    # init_schema 幂等预置 + 回填分类/单位：Gemini 归基础设施(postpaid)、讯飞按小时
    db.init_schema()
    rows = {r["vendor"]: r for r in balances.list_balances()}
    assert rows["G25F"]["category"] == "infra" and rows["G25F"]["payMode"] == "postpaid"   # Gemini 归基础设施
    assert rows["DeepSeek"]["category"] == "infra" and rows["DeepSeek"]["payMode"] == "prepaid_manual"
    assert rows["FunASR"]["payMode"] == "prepaid_auto"   # 阿里 FunASR 已关联自动充值
    assert rows["讯飞"]["unit"] == "hours" and rows["豆包"]["unit"] == "cny"   # 讯飞按时长、其余按金额


def _fake_resp(body: bytes):
    class R:
        def read(self): return body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return R()


def test_refresh_bocha_parses_and_stores(monkeypatch):
    # 博查：GET /v1/fund/remaining → data.remaining；用现有 BOCHA_API_KEY（mock HTTP + set_balance）
    monkeypatch.setenv("BOCHA_API_KEY", "k")
    body = json.dumps({"success": True, "data": {"remaining": 9.57}}).encode()
    monkeypatch.setattr(balances.urllib.request, "urlopen", lambda *a, **k: _fake_resp(body))
    saved = {}
    monkeypatch.setattr(balances, "set_balance", lambda *a, **k: saved.update({**k, "vendor": a[0] if a else None}))
    assert balances.refresh_bocha() == {"vendor": "博查", "amountCny": 9.57}
    assert saved["vendor"] == "博查" and saved["amount_cny"] == 9.57 and saved["source"] == "api"


def test_refresh_bocha_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    assert balances.refresh_bocha() is None


def test_refresh_volc_parses_and_stores(monkeypatch):
    # 火山（豆包）：QueryBalanceAcct → Result.AvailableBalance（mock HTTP，不校验签名字节）
    monkeypatch.setenv("VOLC_ACCESS_KEY", "ak")
    monkeypatch.setenv("VOLC_SECRET_KEY", "sk")
    body = json.dumps({"Result": {"AvailableBalance": "37.8", "CashBalance": "37.8"}}).encode()
    monkeypatch.setattr(balances.urllib.request, "urlopen", lambda *a, **k: _fake_resp(body))
    saved = {}
    monkeypatch.setattr(balances, "set_balance", lambda *a, **k: saved.update({**k, "vendor": a[0] if a else None}))
    assert balances.refresh_volc() == {"vendor": "豆包", "amountCny": 37.8}
    assert saved["vendor"] == "豆包" and saved["source"] == "api"


def test_refresh_volc_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("VOLC_ACCESS_KEY", raising=False)
    monkeypatch.delenv("VOLC_SECRET_KEY", raising=False)
    assert balances.refresh_volc() is None


def test_refresh_aliyun_parses_and_stores(monkeypatch):
    # 阿里（FunASR）：QueryAccountBalance → Data.AvailableCashAmount（mock HTTP）
    monkeypatch.setenv("ALIYUN_ACCESS_KEY", "ak")
    monkeypatch.setenv("ALIYUN_SECRET_KEY", "sk")
    body = json.dumps({"Data": {"AvailableCashAmount": "15.53", "Currency": "CNY"}, "Code": "200"}).encode()
    monkeypatch.setattr(balances.urllib.request, "urlopen", lambda *a, **k: _fake_resp(body))
    saved = {}
    monkeypatch.setattr(balances, "set_balance", lambda *a, **k: saved.update({**k, "vendor": a[0] if a else None}))
    assert balances.refresh_aliyun() == {"vendor": "FunASR", "amountCny": 15.53}
    assert saved["vendor"] == "FunASR" and saved["source"] == "api"


def test_refresh_aliyun_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ALIYUN_ACCESS_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_SECRET_KEY", raising=False)
    assert balances.refresh_aliyun() is None


def test_refresh_all_skips_missing_and_swallows_errors(monkeypatch):
    # 缺钥匙的返回 None 跳过、单家异常吞掉，只聚合成功拉到的
    monkeypatch.setattr(balances, "refresh_deepseek", lambda: {"vendor": "DeepSeek", "amountCny": 88.5})
    monkeypatch.setattr(balances, "refresh_bocha", lambda: None)
    monkeypatch.setattr(balances, "refresh_volc", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(balances, "refresh_aliyun", lambda: {"vendor": "FunASR", "amountCny": 15.53})
    out = balances.refresh_all()
    assert {r["vendor"] for r in out} == {"DeepSeek", "FunASR"} and len(out) == 2


@pytest.mark.infra
def test_no_threshold_flags_only_prepaid_manual(clean):
    """没设阈值 = 低余额告警对这家永远不会触发，界面上要能看出来。

    **只有 prepaid_manual 算**：后付费不会欠费停摆、prepaid_auto 关联了自动充值，
    按设计本来就不该盯余额——把它们算进来会造出永远清不掉的假警报，
    而假警报会逼人学会忽略整条待办条。"""
    with db.connect() as conn:
        conn.execute("INSERT INTO vendor_balances (vendor, pay_mode, amount_cny) "
                     "VALUES ('M','prepaid_manual',50), ('A','prepaid_auto',50), ('P','postpaid',50)")
    balances.set_balance("M2", amount_cny=50, threshold_cny=20)
    with db.connect() as conn:
        conn.execute("UPDATE vendor_balances SET pay_mode='prepaid_manual' WHERE vendor='M2'")
    by = {r["vendor"]: r for r in balances.list_balances()}
    assert by["M"]["noThreshold"] is True         # 预充值·手动 + 没阈值 → 无保护
    assert by["A"]["noThreshold"] is False        # 自动充值，不该报
    assert by["P"]["noThreshold"] is False        # 后付费，不该报
    assert by["M2"]["noThreshold"] is False       # 设了阈值，不该报


@pytest.mark.infra
def test_no_threshold_does_not_fire_low_alert(clean):
    """反向确认这条告警确实存在盲区：没阈值的厂商余额再低，check_low_balances 也一条都不发。
    这正是要在界面上单独标出来的原因——它的静默和「一切正常」一模一样。"""
    with db.connect() as conn:
        conn.execute("INSERT INTO vendor_balances (vendor, pay_mode, amount_cny) "
                     "VALUES ('Broke','prepaid_manual',0.01)")
    assert balances.check_low_balances() == []

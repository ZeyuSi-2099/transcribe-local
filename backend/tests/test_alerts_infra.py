"""告警的落库这一路（真库）。邮件那一路在 test_alerts.py。

这一路的纪律是**不许丢**：漏看一次也要能事后查到，被冷却压掉的次数也要留下。
所以每条用例都从库里读回来核对，而不是看返回值——返回值说的是「信发出去没有」，
那已经不是主路了。
"""
import pytest

from app import alerts, db


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM admin_alerts WHERE subject LIKE 'T·%'")
    # 不发真信：这些用例只关心库里留下了什么
    monkeypatch.setattr(alerts.config, "ADMIN_EMAILS", frozenset())
    yield


def _rows(subject_like="T·%"):
    with db.connect() as conn:
        return conn.execute(
            "SELECT id, tier, subject, mailed, mail_error, suppressed, handled_at "
            "FROM admin_alerts WHERE subject LIKE %s ORDER BY id", (subject_like,),
        ).fetchall()


@pytest.mark.infra
def test_alert_is_recorded_even_when_mail_is_skipped():
    """没有管理员邮箱 = 一封信都发不出去，但**记录必须还在**。

    这正是整轮改造的理由：告警邮件与登录验证码共用同一个供应商额度，撞上限时
    用户登不进、你也收不到告警——两个系统同时哑，还是同一个原因。
    落库之后邮件只是旁路，驾驶舱看得见才是主路。"""
    assert alerts.send_admin_alert("T·无管理员", "body", tier="act") is False
    r = _rows()
    assert len(r) == 1
    assert r[0][1] == "act" and r[0][3] == "skipped" and r[0][4]   # 失败原因也要写下来


@pytest.mark.infra
def test_mail_failure_is_recorded_not_just_logged(monkeypatch):
    """发送失败此前只在日志里留一行字——你不会知道有一封告警根本没发出去。"""
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setattr(alerts.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))
    monkeypatch.setattr(alerts, "_post_resend",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("provider 502")))
    alerts.send_admin_alert("T·发信失败", "body", tier="act")
    r = _rows()[0]
    assert r[3] == "failed" and "502" in r[4]


@pytest.mark.infra
def test_cooldown_suppresses_mail_but_keeps_counting():
    """冷却期内不发信，但**那个次数不能丢**。

    旧做法是直接 return，于是「这一小时其实报了 40 次」这个事实就永远没了——
    而「偶发一次」和「一直在响」该做的处置完全不同。"""
    for _ in range(5):
        alerts.send_admin_alert("T·刷屏", "body", tier="watch", key="t-flood")
    r = _rows()
    assert len(r) == 1          # 只留一行，不刷屏
    assert r[0][5] == 4         # 但压掉的 4 次记在这里


@pytest.mark.infra
def test_dedup_is_cross_process():
    """去重记在库里而不是进程内存里。

    线上不止一个进程（收上传的、跑看门狗的各一个），进程内冷却让同一条告警可能发好几份，
    重部署之后又重新轰炸一轮。这里模拟「另一个进程」= 直接再调一次，本进程不持任何状态。"""
    alerts.send_admin_alert("T·跨进程", "body", tier="watch", key="t-cross")
    alerts.send_admin_alert("T·跨进程", "body", tier="watch", key="t-cross")
    assert len(_rows()) == 1


@pytest.mark.infra
def test_expired_cooldown_starts_a_new_row():
    """冷却窗过了就是新的一次，不该并进上一条——否则「今天又响了」会被昨天那行吃掉。"""
    alerts.send_admin_alert("T·过窗", "body", tier="watch", key="t-exp", cooldown_sec=3600)
    alerts.send_admin_alert("T·过窗", "body", tier="watch", key="t-exp", cooldown_sec=0)
    assert len(_rows()) == 2


@pytest.mark.infra
def test_no_key_means_every_one_is_its_own_row():
    """不传冷却键的告警每一笔都单独留一行——对账信号不是噪音，合并了就少看一笔账。"""
    for _ in range(3):
        alerts.send_admin_alert("T·对账", "body", tier="act")
    assert len(_rows()) == 3


@pytest.mark.infra
def test_handled_only_for_act_tier():
    """「已处理」只给 act：watch/fyi 自己会消失（余额充了、失败率降了就不再报），
    让人再点一次纯属多余动作。"""
    alerts.send_admin_alert("T·要动手", "b", tier="act")
    alerts.send_admin_alert("T·会自愈", "b", tier="watch")
    ids = {r[2]: r[0] for r in _rows()}
    assert alerts.mark_handled(ids["T·要动手"], "me@x.com") is True
    assert alerts.mark_handled(ids["T·会自愈"], "me@x.com") is False


@pytest.mark.infra
def test_handled_is_not_reversible():
    """已标过的不翻案、不改人名——第二次点击返回 False，前端据此不必重复提示。"""
    alerts.send_admin_alert("T·幂等", "b", tier="act")
    aid = _rows()[0][0]
    assert alerts.mark_handled(aid, "first@x.com") is True
    assert alerts.mark_handled(aid, "second@x.com") is False
    with db.connect() as conn:
        assert conn.execute("SELECT handled_by FROM admin_alerts WHERE id=%s", (aid,)).fetchone()[0] \
            == "first@x.com"


@pytest.mark.infra
def test_list_unhandled_only_returns_act():
    """待办条只接「未处理的 act」。watch 不进——余额偏低那条待办条已经按活状态报了，
    再从告警记录里报一遍就是同一件事出现两行，而待办条的可信度是它唯一的资产。"""
    alerts.send_admin_alert("T·未处理", "b", tier="act")
    alerts.send_admin_alert("T·会自愈", "b", tier="watch")
    alerts.send_admin_alert("T·线索", "b", tier="fyi")
    subs = {a["subject"] for a in alerts.list_alerts(days=1, unhandled_only=True)}
    assert "T·未处理" in subs
    assert "T·会自愈" not in subs and "T·线索" not in subs


@pytest.mark.infra
def test_purge_keeps_recent():
    """清理只动过期的。**带 WHERE**，撞不上 db 层那道整表写守卫（见 2026-08-14 事故）。"""
    alerts.send_admin_alert("T·新的", "b", tier="fyi")
    alerts.purge_old(days=alerts.RETENTION_DAYS)
    assert len(_rows()) == 1

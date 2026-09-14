"""告警的邮件这一路（不连库）。落库那一路在 test_alerts_infra.py。

这里全程把 `_record`/`_suppress`/`_mark_mailed` 打桩掉，测的是**邮件路径的判定**：
谁能发、发失败怎么办、返回值是什么。分成两个文件是因为它们守的东西不同——
这一路的纪律是「不许抛」，那一路的纪律是「不许丢」。
"""
import pytest

from app import alerts


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """把落库那一路拆掉：这些用例不该需要 Postgres。

    顺带守住一件事——**落库与发信互不依赖**：这里 `_record` 恒返 None（等价于落库失败），
    而下面每一条关于发信的断言都必须照常成立。"""
    monkeypatch.setattr(alerts, "_record", lambda *a, **k: None)
    monkeypatch.setattr(alerts, "_suppress", lambda *a, **k: False)
    monkeypatch.setattr(alerts, "_mark_mailed", lambda *a, **k: None)


def _admin(monkeypatch):
    monkeypatch.setattr(alerts.config, "ADMIN_EMAILS", frozenset({"a@b.com"}))


def test_tier_is_required_and_validated(monkeypatch):
    """档位必填且只能是三个值之一。**这是故意做成会报错的**：新加一条告警时漏分档，
    错档比没档更糟——它会让人以为自己已经在看那一条了。"""
    _admin(monkeypatch)
    with pytest.raises(TypeError):
        alerts.send_admin_alert("S", "T")          # 少了 tier
    with pytest.raises(ValueError, match="未知告警档位"):
        alerts.send_admin_alert("S", "T", tier="urgent")


def test_dev_mode_no_send(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    _admin(monkeypatch)
    assert alerts.send_admin_alert("S", "T", tier="watch") is False


def test_no_admins_no_send(monkeypatch):
    monkeypatch.setattr(alerts.config, "ADMIN_EMAILS", frozenset())
    assert alerts.send_admin_alert("S", "T", tier="act") is False


def test_sends_when_configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    _admin(monkeypatch)
    sent = []
    monkeypatch.setattr(alerts, "_post_resend", lambda admins, s, t: sent.append((admins, s)) or True)
    assert alerts.send_admin_alert("S", "T", tier="act") is True
    assert sent and sent[0][0] == ["a@b.com"]


def test_send_failure_swallowed(monkeypatch):
    """发送异常吞掉、返回 False、不抛——告警不该拖垮它正在监视的那件事。"""
    monkeypatch.setenv("RESEND_API_KEY", "k")
    _admin(monkeypatch)
    monkeypatch.setattr(alerts, "_post_resend", lambda *a: (_ for _ in ()).throw(RuntimeError("net")))
    assert alerts.send_admin_alert("S", "T", tier="act") is False


def test_suppressed_alert_does_not_mail(monkeypatch):
    """冷却窗内被压掉的那次不发信（计数在库里加，见 infra 用例）。"""
    monkeypatch.setenv("RESEND_API_KEY", "k")
    _admin(monkeypatch)
    monkeypatch.setattr(alerts, "_suppress", lambda *a, **k: True)
    monkeypatch.setattr(alerts, "_post_resend", lambda *a: pytest.fail("冷却期内不该发信"))
    assert alerts.send_admin_alert("S", "T", tier="watch", key="dup") is False


def test_cooldown_lookup_failure_falls_back_to_sending(monkeypatch):
    """查不到冷却状态时**宁可多发一封，不可漏报**。

    这个方向不能反：告警系统自己出故障时，代价应该落在「多收一封邮件」上，
    而不是「那条告警从此消失」——后者正是这一整轮要解决的问题。"""
    monkeypatch.setenv("RESEND_API_KEY", "k")
    _admin(monkeypatch)
    monkeypatch.setattr(alerts, "_suppress",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("库挂了")))
    sent = []
    monkeypatch.setattr(alerts, "_post_resend", lambda *a: sent.append(1) or True)
    assert alerts.send_admin_alert("S", "T", tier="act", key="k") is True
    assert len(sent) == 1


def test_record_failure_still_mails(monkeypatch):
    """落库挂了照样发信（autouse 夹具里 _record 恒 None，这里再显式让它抛一次）。"""
    monkeypatch.setenv("RESEND_API_KEY", "k")
    _admin(monkeypatch)
    monkeypatch.setattr(alerts, "_record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("库挂了")))
    sent = []
    monkeypatch.setattr(alerts, "_post_resend", lambda *a: sent.append(1) or True)
    assert alerts.send_admin_alert("S", "T", tier="act") is True
    assert len(sent) == 1

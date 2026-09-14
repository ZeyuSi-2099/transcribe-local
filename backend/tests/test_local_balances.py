"""本机版余额自动拉取只查 DeepSeek 与博查。本地独有的测试（线上没有这个文件）。

线上四家都拉（DeepSeek / 博查 / 火山 / 阿里）。本机识别不用火山、阿里的云端引擎，
环境变量里恰好有那两家钥匙时也不许去查——界面走查时实见过看门狗去拉豆包的账单。
"""
from app import balances


def test_refresh_all_only_queries_deepseek_and_bocha(monkeypatch):
    called = []
    for name in ("refresh_deepseek", "refresh_bocha"):
        monkeypatch.setattr(balances, name, lambda _n=name: called.append(_n) or {"vendor": _n})

    def _cloud(*_a, **_k):
        raise AssertionError("本机版不许去查火山 / 阿里的云端账单")

    monkeypatch.setattr(balances, "refresh_volc", _cloud)
    monkeypatch.setattr(balances, "refresh_aliyun", _cloud)
    out = balances.refresh_all()
    assert called == ["refresh_deepseek", "refresh_bocha"]
    assert [r["vendor"] for r in out] == called

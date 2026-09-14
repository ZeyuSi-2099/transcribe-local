"""DeepSeek 价目表守卫。

两件事：
① `pipeline/ds_pricing.py` 与 vendor 里那份 `_PRICE_CNY` 必须逐项相等。
   两份并存是有意的（vendor 脚本要能脱离本仓库单跑），但**改一处忘了另一处**
   就会让「后处理算的钱」和「P3 算的钱」用两套价——同 pricing.py ↔ pricing.ts 的守法。
② 峰谷与生效日的判定边界。这些边界写错不会报错，只会静默把成本算错 2–4 倍。
"""
import datetime as dt
import re
from pathlib import Path

from pipeline import ds_pricing

VENDOR_SCRIPT = (Path(__file__).resolve().parent.parent
                 / "pipeline" / "vendor" / "Workflow" / "Phase3_Merge_DeepSeek.py")


def _vendor_price_table() -> dict:
    """从 vendor 脚本源码里抠出 _PRICE_CNY 字面量。

    读源码而不是 import：那个脚本一 import 就会读 ~/.secrets、装 DNS 兜底、跑一堆副作用，
    测试里不该碰这些。价目表是纯字面量，正则取出来是安全的。
    """
    src = VENDOR_SCRIPT.read_text(encoding="utf-8")
    body = re.search(r"_PRICE_CNY = \{(.*?)\n\}", src, re.S).group(1)
    out = {}
    for tier, rows in re.findall(r'"(\w+)":\s*\{(.*?)\}', body, re.S):
        out[tier] = {m: tuple(float(x) for x in nums.split(","))
                     for m, nums in re.findall(r'"(\w+)":\s*\(([^)]+)\)', rows)}
    return out


def test_价目表与_vendor_脚本逐项一致():
    assert _vendor_price_table() == ds_pricing.PRICE_CNY


def test_汇率与生效日与_vendor_一致():
    src = VENDOR_SCRIPT.read_text(encoding="utf-8")
    assert f"_CNY_PER_USD = {ds_pricing.CNY_PER_USD}" in src
    d = ds_pricing.NEW_PRICE_FROM
    assert f"_NEW_PRICE_FROM = datetime({d.year}, {d.month}, {d.day})" in src


def test_生效日之前一律旧价():
    """早切新价会把成本报高 2–4 倍，晚切会报低——边界必须精确到当天 00:00。"""
    assert ds_pricing.tier(dt.datetime(2026, 8, 16, 23, 59)) == "old"
    assert ds_pricing.tier(dt.datetime(2026, 8, 17, 0, 0)) == "off"


def test_峰谷时段边界():
    peak = [9, 10, 11, 14, 15, 16, 17]
    for h in range(24):
        want = "peak" if h in peak else "off"
        assert ds_pricing.tier(dt.datetime(2026, 8, 20, h)) == want, h


def test_周末全天空闲价():
    """官方原文高峰只在「周一至周五」。周六、周日的 10 点若判成高峰，flash 成本报高一倍。"""
    for day in (12, 13):                  # 2026-09-12 周六、09-13 周日
        for h in range(24):
            assert ds_pricing.tier(dt.datetime(2026, 9, day, h)) == "off", (day, h)
    assert ds_pricing.tier(dt.datetime(2026, 9, 11, 10)) == "peak"   # 周五照旧


def test_按模型分档且_flash_远便宜于_pro():
    """早先只有 pro 一档、flash 也套 pro 价，把 flash 成本报高了 3 倍（2026-08-09 踩过）。"""
    at = dt.datetime(2026, 8, 20, 10)     # 高峰
    f = ds_pricing.cost_cny("deepseek-flash", 1_000_000, 1_000_000, 1_000_000, at)
    p = ds_pricing.cost_cny("deepseek-v4-pro", 1_000_000, 1_000_000, 1_000_000, at)
    assert f < p
    # 1M 命中 + 1M 未命中 + 1M 输出，高峰 flash（V4.1）= 0.04 + 2.00 + 8.00
    assert round(f, 4) == 10.04
    # 旧名是官方的临时兼容别名、同价，不许因为改了名就掉进 pro 档
    assert ds_pricing.cost_cny("deepseek-v4-flash", 1_000_000, 1_000_000, 1_000_000, at) == f


def test_算不出来不抛异常():
    """埋点坏了不许连累出稿——usage_cny 兜住一切异常返回 0。"""
    from pipeline import pp_deepseek
    pp_deepseek.reset_usage()
    assert pp_deepseek.usage_cny() == 0.0

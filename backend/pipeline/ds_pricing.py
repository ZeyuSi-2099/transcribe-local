"""DeepSeek 价目表（元/百万 tokens）与峰谷判定 —— SaaS 侧的唯一来源。

**为什么与 `vendor/Workflow/Phase3_Merge_DeepSeek.py` 里那份并存**：那个脚本要能脱离本仓库
单跑（P3 是 subprocess 调起的独立进程），不能 import `pipeline.*`；而后处理的降级路是
库内调用，不该反过来去 import vendor。两份都存在是有意的，靠 `test_ds_pricing.py`
逐项比对防漂移——同 `pricing.py` ↔ `src/lib/pricing.ts` 的做法。**改一处必须改另一处。**

2026-08-13 核对 api-docs.deepseek.com 与官方涨价公告：
2026-08-17 起改行峰谷定价，高峰 = 北京时间 9:00–12:00 与 14:00–18:00，其余为空闲。
⚠️ 涨幅最大的是**缓存命中**（flash 5 倍 / pro 12 倍），「命中近似免费」的旧假设已失效。

2026-09-11 再核对官方价目表，订正两处：
① flash 换成 V4.1 Flash 的价（09-10 发布，正式名 `deepseek-flash`；旧名 `deepseek-v4-flash`
   临时路由到同一个模型、同价）。它比 V4 Flash **便宜**，不是更贵。pro 不变。
② 高峰只算**周一至周五**（官方原文），周末全天空闲价。
"""
import datetime as _dt

CNY_PER_USD = 6.897
NEW_PRICE_FROM = _dt.datetime(2026, 8, 17)     # 北京时间生效日（当天 00:00）

# 档位 → 模型 → (未命中输入, 命中输入, 输出)，元/百万 tokens
PRICE_CNY = {
    "old":  {"flash": (1.00, 0.02, 2.00), "pro": (3.00, 0.025, 6.00)},
    "peak": {"flash": (2.00, 0.04, 8.00), "pro": (9.00, 0.30, 27.00)},
    "off":  {"flash": (1.00, 0.02, 4.00), "pro": (4.50, 0.15, 13.50)},
}


def now_bj() -> _dt.datetime:
    """当前北京时间（naive，便于与 NEW_PRICE_FROM 直接比较）。"""
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).replace(tzinfo=None)


def tier(at: _dt.datetime | None = None) -> str:
    """该用哪一档价。**生效日之前一律旧价**——早几天切新价会把成本报高 2–4 倍。"""
    at = at or now_bj()
    if at < NEW_PRICE_FROM:
        return "old"
    if at.weekday() >= 5:              # 周六、周日全天空闲价
        return "off"
    return "peak" if (9 <= at.hour < 12 or 14 <= at.hour < 18) else "off"


def cost_cny(model: str, hit: int, miss: int, out: int,
             at: _dt.datetime | None = None) -> float:
    """按 token 数算这次调用的人民币成本。model 里含 "flash" 走 flash 档，否则 pro 档。"""
    m_miss, m_hit, m_out = PRICE_CNY[tier(at)]["flash" if "flash" in model else "pro"]
    return (miss * m_miss + hit * m_hit + out * m_out) / 1e6

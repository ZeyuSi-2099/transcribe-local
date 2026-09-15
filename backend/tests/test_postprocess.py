"""后处理纯逻辑单测：steps 归一 / 内容与名称校验 / 定价（无 DB）。"""
from app import config, postprocess


# ── normalize_steps：任意子集 × 固定顺序 ──

def test_normalize_steps_orders_fixed():
    assert postprocess.normalize_steps(["redact", "narrate"]) == ["narrate", "redact"]
    assert postprocess.normalize_steps(["redact"]) == ["redact"]


def test_normalize_steps_dedupes():
    assert postprocess.normalize_steps(["narrate", "narrate"]) == ["narrate"]


def test_normalize_steps_rejects_invalid():
    assert postprocess.normalize_steps([]) is None
    assert postprocess.normalize_steps(None) is None
    assert postprocess.normalize_steps("narrate") is None
    assert postprocess.normalize_steps(["narrate", "excel"]) is None
    # 归类已下架：存量任务的 steps 送进来照样判非法，不做兼容放行
    # （放行等于 pp_runner 还得留着归类那条执行链，就没拆干净）
    assert postprocess.normalize_steps(["narrate", "categorize"]) is None


# ── 名称/内容校验 ──

def test_validate_name_rules():
    assert postprocess.validate_name("方案A") is None
    assert postprocess.validate_name("  ") is not None          # 空
    assert postprocess.validate_name("x" * 41) is not None      # 超长
    assert postprocess.validate_name("a\nb") is not None        # 控制字符


def test_validate_list_length_cap():
    assert postprocess.validate_list("词\n" * 100) is None
    assert postprocess.validate_list("x" * (config.PP_LIST_MAX_CHARS + 1)) is not None
    # 换行不计入总字数（与术语库同口径）
    assert postprocess.validate_list(("x" * 100 + "\n") * (config.PP_LIST_MAX_CHARS // 100)) is None


def test_validate_list_allows_empty():
    assert postprocess.validate_list("") is None


# ── 定价（2026-08-02 改价）：每步加价率×分钟、按秒折算，快照原则同转录 ──

def test_postprocess_rate_stacks_per_step():
    from app import pricing   # 本机版：计费模块不搬，只在这两条（登记为不适用）里导入
    # 选几步加几步；步骤名必须与 STEPS 完全一致（改名会让快照对不上）
    assert set(pricing.PP_STEP_RATES_CENTS) == set(postprocess.STEPS)
    assert pricing.postprocess_rate_cents_per_min(["narrate"]) == pricing.PP_STEP_RATES_CENTS["narrate"]
    assert (pricing.postprocess_rate_cents_per_min(list(postprocess.STEPS))
            == sum(pricing.PP_STEP_RATES_CENTS.values()))


def test_postprocess_price_prorates_by_second():
    from app import pricing
    # 60 分钟全选 = 合计费率 × 60；10 分 30 秒单步 = 四舍五入到分
    full = sum(pricing.PP_STEP_RATES_CENTS.values())
    assert pricing.postprocess_price_cents(list(postprocess.STEPS), 3600) == full * 60
    one = pricing.PP_STEP_RATES_CENTS["narrate"]
    assert pricing.postprocess_price_cents(["narrate"], 630) == round(one * 630 / 60)
    assert pricing.postprocess_price_cents(["narrate"], 0) == 0


def test_error_public_is_single_phrase_without_refund_word():
    # 失败话术单句原则 + 全站禁「退款」字样
    assert "退款" not in postprocess.ERROR_PUBLIC
    assert "计费" not in postprocess.ERROR_PUBLIC   # 本机版不收费：线上这里要求带「不计费」

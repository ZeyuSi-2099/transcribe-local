"""限流（429 / 配额）要退避重试，不能当成致命错误（2026-08-26）。

供应商拒绝我们通常有三种额度：每分钟请求数 / 每分钟 token 数 / 并发数——任意一种撞上都回同一类
信号。**这是最该重试的一类错误**（服务端明确拒绝、压根没处理、不计费，所以重试安全），却最容易
被写成致命错误：在此之前 GEM 的重试白名单里没有 429，命中就 `raise`，**整条参考轨没了**。

参数（Duner 2026-08-26 定）：最多 2 次、指数退避 20→40s、±30% 抖动。
故意不激进——我们本来就是多路引擎，一路等不到就少一票，不值得为它把整单拖住。

三条判据，各造回一次 bug 验过：
① 限流认得出，且与「网络抖动」「鉴权错误」分得开（认错了要么白等、要么白丢一路）；
② **限流不消耗温度阶梯**——阶梯是用来打断复读死循环的，为限流烧掉一档，之后真复读就没档可用；
③ **抖动必须在**——多路并行会同时撞限流、同时退避、同时回来（惊群），等于把一次限流变成三次。
"""
import re

import pytest

from pipeline._load import load_phase_module

VENDOR = "pipeline/vendor/Workflow"


@pytest.fixture(scope="module")
def utils():
    return load_phase_module("core/utils.py", "utils_rate_limit_test")


# ── ① 判据分得开 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "429 Too Many Requests",
    "RESOURCE_EXHAUSTED: Quota exceeded for quota metric",
    "Rate limit reached for model",
    "rate_limit_exceeded",
])
def test_限流认得出(utils, msg):
    assert utils._is_rate_limited(Exception(msg)), f"没认出限流：{msg}"


@pytest.mark.parametrize("msg", [
    "Connection reset by peer",
    "nodename nor servname provided",
    "401 Unauthorized",
    "400 invalid argument",
])
def test_不是限流的别误认(utils, msg):
    assert not utils._is_rate_limited(Exception(msg)), f"误认成限流：{msg}"


def test_按状态码也认得出(utils):
    e = type("ApiErr", (Exception,), {})()
    e.status_code = 429
    assert utils._is_rate_limited(e)


def test_限流与网络抖动是两回事(utils):
    """两者的成因与该等多久都不同，预算也必须分开——合成一份要么太激进、要么砍掉网络重试。"""
    rl, net = Exception("429 rate limit"), Exception("Connection reset by peer")
    assert utils._is_rate_limited(rl) and not utils._is_transient_net_error(rl)
    assert utils._is_transient_net_error(net) and not utils._is_rate_limited(net)


# ── ③ 退避与抖动 ──────────────────────────────────────────────────────────────
def test_退避是指数的(utils):
    """两次的中位数应当翻倍。抖动 ±30% ⇒ 第 0 次上界 26s < 第 1 次下界 28s，区间不重叠。"""
    assert utils.RATE_LIMIT_TRIES == 2
    lo0 = utils.RATE_LIMIT_BASE_SEC * (1 - utils.RATE_LIMIT_JITTER)
    hi0 = utils.RATE_LIMIT_BASE_SEC * (1 + utils.RATE_LIMIT_JITTER)
    lo1 = utils.RATE_LIMIT_BASE_SEC * 2 * (1 - utils.RATE_LIMIT_JITTER)
    assert hi0 < lo1, "两档退避区间重叠了，等于没有退避"
    for _ in range(50):
        assert lo0 <= utils.rate_limit_delay(0) <= hi0
        assert lo1 <= utils.rate_limit_delay(1) <= utils.RATE_LIMIT_BASE_SEC * 2 * (1 + utils.RATE_LIMIT_JITTER)


def test_抖动真的在(utils):
    """没有抖动的话，并行的几路会同时退避、同时回来，把一次限流放大成三次。"""
    vals = {round(utils.rate_limit_delay(0), 4) for _ in range(40)}
    assert len(vals) > 30, "40 次取样只出现了 %d 个不同值——抖动没生效" % len(vals)


# ── retry_net：两份预算分开 ───────────────────────────────────────────────────
def test_限流重试用尽后原样抛_且不吃掉网络预算(utils, monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_429():
        calls["n"] += 1
        raise Exception("429 rate limit")

    with pytest.raises(Exception, match="429"):
        utils.retry_net(always_429, tries=5, label="测试")
    assert calls["n"] == utils.RATE_LIMIT_TRIES + 1, "限流应当只重试 2 次（首次 + 2）"


def test_限流退避后成功(utils, monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("RESOURCE_EXHAUSTED")
        return "ok"

    assert utils.retry_net(flaky, label="测试") == "ok"
    assert calls["n"] == 2


def test_网络重试仍是5次_没被限流预算挤掉(utils, monkeypatch):
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_net():
        calls["n"] += 1
        raise ConnectionError("Connection reset by peer")

    with pytest.raises(ConnectionError):
        utils.retry_net(always_net, tries=5, label="测试")
    assert calls["n"] == 5


def test_限流不偷吃网络预算(utils, monkeypatch):
    """**只发一种错误的用例抓不住这条**——两份预算是否真的分开，只有在同一次调用里
    先撞限流、再撞网络抖动时才看得出来。合成一份的话，那次限流会白吃掉一次网络重试机会。

    期望调用次数 = 1（限流）+ 5（网络预算 tries=5）= 6。
    """
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def rl_then_net():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("429 rate limit")
        raise ConnectionError("Connection reset by peer")

    with pytest.raises(ConnectionError):
        utils.retry_net(rl_then_net, tries=5, label="测试")
    assert calls["n"] == 6, f"网络预算被限流吃掉了（实际调用 {calls['n']} 次，应为 6）"


# ── ② GEM：限流不消耗温度阶梯 + 单段异常不带走整轨 ──────────────────────────
def _gem_src() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / VENDOR / "engines/gemini.py").read_text(encoding="utf-8")


def _code() -> str:
    """去掉注释与文档串——判据要看**代码**，不能被解释这件事的注释喂成假绿。"""
    src = re.sub(r'"""[\s\S]*?"""', "", _gem_src())
    return "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))


def test_GEM的限流重试在温度循环内部():
    """限流必须在「同一个温度档内」就地重试。写到外层 except 里就等于推进温度阶梯——
    服务端说慢一点，我们却提高随机性，且把仅剩的一档烧掉，之后真复读没档可用。"""
    code = _code()
    i_ladder = code.index("for attempt, temp in enumerate(temp_schedule)")
    i_rl = code.index("_is_rate_limited(call_err)")
    i_outer = code.index("except Exception as e:", i_ladder)
    assert i_ladder < i_rl < i_outer, "限流重试跑到温度阶梯外面去了"
    # 阶梯只有两档，别指望它兜限流
    assert "WHOLE_LADDER = [0.0, 1.0]" in _gem_src()


def test_GEM的单段异常不会带走整条轨():
    """`future.result()` 必须包 try——否则不可重试错误会冒出线程池、被外层 except 兜成
    `return None`，整条参考轨消失，而不是只丢那一段。"""
    code = _code()
    i = code.index("for future in as_completed(futures)")
    seg = code[i:i + 900]
    assert "try:" in seg and "future.result()" in seg, "收集结果的地方没有兜底"
    assert re.search(r"except Exception as \w+:\s*\n[\s\S]{0,400}?TranscriptResult\(", seg), \
        "异常没有就地转成失败段"

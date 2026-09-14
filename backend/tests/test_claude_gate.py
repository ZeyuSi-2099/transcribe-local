"""Claude 系并发闸的**总量**守卫（纯单测，不需要 DB）。

三把闸各 5、互不挤占，这是 2026-08-13 定的、也是对的：拆开之前后处理一忙就把转录的
Claude 档挤没了。代价是**没人再管总量**——而订阅只有一份，三把各 5 意味着最多 15 个
`claude -p` 同时在烧同一份额度。

这个 15 在 2026-08-31 之前**哪儿都没写**：加第四把闸、或把某把从 5 调到 8，
总量会从 15 悄悄变成 20，没有任何东西会响。本文件把它登记下来。
"""
import inspect
import pathlib

from app import claude_gate, config, postprocess


def test_三把闸的上限之和等于登记值():
    """🔴 这条红了不是「把数字改一下」，是**先想清楚订阅撑不撑得住**再改两处。

    真要调的话：改 `CLAUDE_TOTAL_LIMIT`，并在 `CLAUDE_GATES` 上方的全景表里
    写清楚新的总量是怎么来的。"""
    assert claude_gate.claude_total_limit() == claude_gate.CLAUDE_TOTAL_LIMIT


def test_全景表列全了所有_Claude_系闸():
    """漏登记一把闸的症状是「总量守卫依然绿，而实际总量已经变了」——
    比总量算错更隐蔽，所以判据取自**闸名的真实产生处**，不是抄一份清单。

    转录那把是常量 `claude`；后处理那两把由 `pp_runner` 按 `pp_{step}` 拼，
    step 来自 `postprocess.STEPS`。加一个后处理步骤时这条会红。"""
    expected = {"claude"} | {f"pp_{s}" for s in postprocess.STEPS}
    assert set(claude_gate.CLAUDE_GATES) == expected


def test_闸名拼错不会静默走兜底():
    """`_engine_limit` 对未登记的闸名**按后处理档兜底、不报错**——那是有意的
    （新增一步时忘了登记也不该崩掉转录）。副作用是闸名拼错完全没有信号。

    所以这里正面钉住：全景表里的每一把，都要能从它自己的配置项取到上限。"""
    assert claude_gate._engine_limit("claude") == claude_gate._limit(
        "claude_concurrency", lambda: config.CLAUDE_MAX_CONCURRENCY)
    for step in postprocess.STEPS:
        assert claude_gate._engine_limit(f"pp_{step}") == config.PP_CLAUDE_MAX_CONCURRENCY


def test_账号级校验只覆盖其中一把_这件事要写在代码里():
    """`p3_config.LIMITS["claude"]` 只在运营舱改**转录**那把时校验，
    后处理那两把加起来 10、完全不受它约束。

    这条不是在要求改行为（后处理闸不走运营舱，本来就没有校验入口），
    而是要求**这件事在代码里写着**——它是「账号上限 5」这句话在代码里
    只管三分之一的原因，读闸模块的人必须看得到。"""
    src = pathlib.Path(inspect.getfile(claude_gate)).read_text(encoding="utf-8")
    assert "CLAUDE_GATES" in src
    assert 'p3_config.LIMITS["claude"]' in src or 'LIMITS["claude"]' in src, \
        "全景表里没有交代账号级校验只覆盖一把闸"

"""全局并发闸（DB 级，跨任务机器）。Claude / 后处理三步 / **各路 ASR 引擎**共用。

任务级机器架构下，每台机器各有自己的进程内信号量 → 管不住「跨机」并发，10 台机器
就是 10 个 claude 同时跑。本模块用一张 claude_slots 表做
DB 原子计数：进 P3 前 acquire（在飞<上限才给名额），P3 完 release，拿不到就降 DeepSeek。

⚠️ **这把闸拦的不是 429，是烧额度的速率**（2026-08-13 订正）：
Claude **订阅制没有并发上限**——限制的是 5 小时窗口额度与周额度。此前本模块写着
「撞 Claude 订阅 5 并发墙」，那是误解。所以 acquire 失败的含义是「同时在跑的已经够多了，
再放进来只会更快烧完额度」，处置与撞顶一样：**降级 DS-v4-flash**，不是排队等。

**三把独立的闸**（2026-08-13 按 Duner 定；Pro 那把 2026-08-26 随 Pro 档一并摘除）：转录 P3 与后处理三步各占各的 5 个名额，
互不挤占。此前是一把全局闸，后处理一忙就把转录的 Claude 档挤没了，而用户对
「转录降质」的感知远强于「加工降质」。**engine 字段就是闸的身份**：转录用 `claude`，
后处理用 `pp_<步骤名>`（pp_narrate / pp_redact，由 pp_runner 拼）。

acquire 是**单条 SQL 原子**（INSERT ... SELECT WHERE count<limit），并发安全；只数
LEASE_MIN 分钟内的新鲜槽——机器崩溃没 release 的旧槽自动不计入（= 超时回收，防泄漏）。

worker 单任务模式启动时 `skill_merge.set_claude_gate(DbClaudeGate(job_id))` 注入；
单机/测试不注入，skill_merge 回落进程内信号量。
"""
from . import config, db, p3_health

# ⚠️ **两类闸的语义相反，别按同一套直觉改**（2026-08-25 起本模块兼管 ASR）：
#   · Claude/pp_*     —— 拿不到名额＝「同时在跑的够多了，再放进来只会更快烧完额度」→ **降级**；
#   · ASR（elv/xf）   —— 拿不到名额＝「供应商此刻没并发位」→ **等**，等到有位再跑。
#     判死是错的：用户的单不该因为别人在跑而失败。等待逻辑在 orchestrator.run_engine。
# ASR 闸挂在**引擎调用**上而不是整单上：一个任务跑到 P2/P3 时早就不碰 ElevenLabs 了，
# 按整单算名额等于把大半配额锁在不用它的任务手里。

# 槽租约（分钟）：超此视为泄漏、不再计入并发——机器崩了没 release 的旧槽靠它自动回收。
# ⚠️ **必须长于「这件事最长能跑多久」**，否则名额会在我们还在用的时候被系统收回发给别人：
# 说好只放 5 个，实际同时跑 8 个、10 个，然后被供应商一起拒绝——闸看着在，实际不生效。
# 60 分钟的来历（2026-08-25 Duner 定，两处刻意取同一个数）：
#   · Claude P3   —— 实测最长见过 30 分钟，按 20 分钟设计，留 3 倍余量；
#   · ElevenLabs  —— 4 小时音频整档直传约 30 分钟。
# 这个数恰好也等于两处的硬超时（P3_TIMEOUT_SEC / elevenlabs_main.HTTP_TIMEOUT 都是 3600s），
# 所以槽的存活期不可能明显超过它守的那次调用——改任一处时另一处要跟着看一眼。
LEASE_MIN = 60


def _lease_min(engine: str) -> int:
    """留着这个间接层：将来某把闸要单独调租约时，改这里而不是散落到两条 SQL 里。"""
    return LEASE_MIN


def _limit(key: str, fallback):
    """并发上限：先读 p3_config（运营舱可改、改完立刻生效），读不到回落 env。

    ⚠️ 这两个数生效在 **Fly 机器**上（每台自己读自己的），所以改 Render 的 env 对它们无效——
    这正是挪进 DB 的原因，见 app/p3_config.py 模块头。"""
    try:
        from . import p3_config
        v = p3_config.get().get(key)
        if v:
            return int(v)
    except Exception:  # noqa: BLE001  配置读不到不该让闸门失效，回落 env 继续跑
        pass
    return fallback()


# ⚠️ 只登记**真正会卡住我们**的引擎。DB/FA(20 QPS)、AAI(200)、SPM(2万)、SNX(排队 100)
# 都远高于 FLY_MAX_MACHINES 能造出的并发，给它们加闸只是多几个能设错的旋钮。
# 未登记的引擎在 orchestrator 那边直接放行（不查库、不占槽）。
DEFAULT_LIMIT = {"claude": lambda: _limit("claude_concurrency", lambda: config.CLAUDE_MAX_CONCURRENCY),
                 # ElevenLabs：账号 20 并发 ÷ 整档直传每单恒占 4 = 5（官方 min(4, ceil(时长/480))）
                 "elv": lambda: config.ELV_MAX_CONCURRENCY,
                 # 讯飞套餐一：并发 10（中文参考轨；其余语种不跑它，闸自然空着）
                 "xf": lambda: config.XF_MAX_CONCURRENCY}


# ── Claude 系闸的全景（2026-08-31）──────────────────────────────────────────
# **真正决定「烧额度速率」的数是这三把闸的和，而在此之前它哪儿都没写。**
#
#   闸名          用在哪            上限来源                              拿不到怎么办
#   claude        P3 融合           运营舱 p3_config → env P3_CLAUDE_CONCURRENCY   降 DS-v4-flash
#   pp_narrate    后处理·视角转换   env PP_CLAUDE_CONCURRENCY             降 pp_deepseek.narrate
#   pp_redact     后处理·脱敏       同上（三步共用一个配置项）            降 pp_redact_ds.redact
#   （手动探活借 `claude` 这把闸，闸满只记事件、不挤占转录）
#
# ⚠️ **三把闸独立是有意的，但订阅只有一份**：2026-08-13 拆开之前是一把全局闸，
# 后处理一忙就把转录的 Claude 档挤没了。拆开是对的，代价是**没人再管总量**——
# 三把各 5 意味着最多 15 个 `claude -p` 同时在烧同一份额度。
# ⚠️ 而账号级校验（p3_config.LIMITS["claude"]）**只管得到其中一把**：运营舱改转录那把时
# 会校验，后处理那两把加起来 10，完全不受它约束。「账号上限 5」在代码里管的是三分之一。
#
# `CLAUDE_GATES` 与 `CLAUDE_TOTAL_LIMIT` 就是把那个数登记下来：加第四把闸、或把某把
# 从 5 调到 8，`tests/test_claude_gate.py` 会红，逼人做一次**有意识的决定**，
# 而不是让总量从 15 悄悄变成 20。
CLAUDE_GATES = ("claude", "pp_narrate", "pp_redact")
CLAUDE_TOTAL_LIMIT = 15


def claude_total_limit() -> int:
    """三把 Claude 系闸的上限之和（＝同时能有多少个 `claude -p` 在烧额度）。"""
    return sum(_engine_limit(e) for e in CLAUDE_GATES)


def _engine_limit(engine: str) -> int:
    """该闸的上限。**未登记的闸名按后处理档兜底**——新增一步时忘了登记也不会 KeyError 崩掉。

    后处理三步共用 `PP_CLAUDE_MAX_CONCURRENCY` 一个配置项（闸是各自独立的，只是上限值同源）：
    三步谁也不比谁金贵，分开设只是多三个能设错的旋钮。"""
    fn = DEFAULT_LIMIT.get(engine)
    return fn() if fn else config.PP_CLAUDE_MAX_CONCURRENCY


def acquire_slot(slot_id: str, limit: int = None, engine: str = "claude") -> bool:
    """原子占一个该引擎的槽。该引擎新鲜在飞数 < limit 才成功。返回是否拿到名额。

    interval 用常量 LEASE_MIN 内联（psycopg 不能参数化 interval 内的数；LEASE_MIN 是代码常量、非用户输入，安全）。"""
    limit = _engine_limit(engine) if limit is None else limit
    with db.connect() as conn:
        row = conn.execute(
            "INSERT INTO claude_slots (slot_id, engine) "
            "SELECT %s, %s WHERE (SELECT count(*) FROM claude_slots "
            f"  WHERE engine = %s AND acquired_at > now() - interval '{_lease_min(engine)} minutes') < %s "
            "ON CONFLICT (engine, slot_id) DO NOTHING RETURNING slot_id",
            (slot_id, engine, engine, limit),
        ).fetchone()
    return row is not None


def release_slot(slot_id: str, engine: str = "claude") -> None:
    """释放槽（P3 完成/降级后调）。幂等：不存在也无妨。"""
    with db.connect() as conn:
        conn.execute("DELETE FROM claude_slots WHERE slot_id = %s AND engine = %s",
                     (slot_id, engine))


def active_count(engine: str = "claude") -> int:
    """当前该引擎新鲜在飞的槽数（监控用；忽略超时泄漏的旧槽）。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM claude_slots WHERE engine = %s "
            f"  AND acquired_at > now() - interval '{_lease_min(engine)} minutes'", (engine,)
        ).fetchone()
    return int(row[0])


class DbClaudeGate:
    """skill_merge 注入用的闸对象：固定 slot_id（= job_id / machine_id）的 acquire/release，
    外加两件跨机才有意义的事——读全局撞顶冷却、写健康度埋点（都落在 p3_events）。

    为什么挂在闸上而不是让 skill_merge 直接 import p3_health：`pipeline/` 不依赖 `app/`
    是既有分层，注入是它跨过这条线的唯一口子（同 set_claude_gate 的既有用法）。"""

    def __init__(self, slot_id: str, source: str = "job", engine: str = "claude"):
        self.slot_id = slot_id
        self.source = source
        self.engine = engine

    def acquire(self) -> bool:
        return acquire_slot(self.slot_id, engine=self.engine)

    def release(self) -> None:
        release_slot(self.slot_id, engine=self.engine)

    def capped_reason(self) -> str:
        """全局撞顶冷却中则返回 cap_5h / cap_week，否则空串。异常由调用方兜（查不到按未撞顶继续）。"""
        st = p3_health.cap_state()
        return st["reason"] if st else ""

    def report(self, outcome: str, **kw) -> None:
        """把这次 P3 的结果落 p3_events（运营舱数据源 + 下一台机器的冷却依据）。"""
        p3_health.record(outcome, source=self.source, job_id=self.slot_id, **kw)


class DbEngineGate:
    """ASR 引擎的跨机并发闸（注入 orchestrator.run_engine，一个任务一个 slot_id）。

    与 DbClaudeGate 的唯一差别是**拿不到名额之后干什么**：Claude 降级、ASR 排队等。
    等待逻辑不放这里——放在 orchestrator，因为「等多久、等不到怎么办」是流水线的决定，
    这里只负责原子占位/释放。engine 用小写 tag（elv / xf），与 DEFAULT_LIMIT 对齐。"""

    def __init__(self, slot_id: str):
        self.slot_id = slot_id

    def acquire(self, engine: str) -> bool:
        return acquire_slot(self.slot_id, engine=engine)

    def release(self, engine: str) -> None:
        release_slot(self.slot_id, engine=engine)

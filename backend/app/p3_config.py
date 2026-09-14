"""P3 运行时配置：并发 / 机器数 / 强制引擎，运营舱可改，改完立刻生效。

**为什么值得单开一个模块**：这几个数原本全是 env，而它们生效在两个不同的地方——
`FLY_MAX_MACHINES` 在 Render 派单时读，`P3_*_CONCURRENCY` 在 Fly 机器上读（每台自己读自己的）。
于是「改 Render env」对 Fly 机器无效、得改 fly secrets，很容易改错地方还以为生效了
（2026-08-11 部署时刚踩过一次同类问题）。挪进 DB 后两侧读同一处，顺带修掉那个坑。

## 读取契约

`get()` 永远返回一份可用的配置：DB 里为 NULL 的项回落 env 缺省。
**任何异常都吞掉、回落 env** —— 配置读不到不该让转录停摆，宁可跑在默认值上。

## 上限从哪来（`LIMITS`）

真正撞墙的是**乘积**，不是单个数：每台 Fly 机器内部还开 `PER_MACHINE_CONC` 路并发
（`merge.P3_ARGS` 的 `--conc 40`），所以

    某档的总请求并发 = 该档机器数 × PER_MACHINE_CONC

各档账号上限见 `LIMITS`。校验按乘积做，不是按机器数单独卡——只卡机器数会让人
设出一个「看着不大、实际撞墙」的值，而撞墙的表现是退避重试导致的莫名变慢，很难查。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from . import config
from . import db

# 每台机器内部的并发路数。与 pipeline/merge.py 的 P3_ARGS `--conc` 必须一致——
# 改那边就要改这里，否则乘积校验算出来的余量是假的。故意不做成配置项：
# 它属于「调过一次就定死」的性能参数，暴露出去只会多一个能设错的地方。
# 2026-08-13 从 50 降到 40：Pro 停用后常态兜底是 Flash，按它的账号并发 2500 算八成余量
# → 50 台（Fly 配额上限）× 40 = 2000。
PER_MACHINE_CONC = 40

# 账号级并发天花板。超过就是每次撞墙白跑一趟（有退避重试，代价是延迟不是失败）。
LIMITS = {
    # ⚠️ 这 5 **不是并发墙**（2026-08-13 订正）：Claude 订阅限的是 5 小时窗口与周额度，
    # 没有并发上限。这一档卡的是「同时烧额度的任务数」，一机一路，不乘 PER_MACHINE_CONC。
    # 也**不是吞吐天花板**（2026-08-26 订正）：拿不到名额是**降级 Flash 继续跑**，不是排队。
    "claude": 5,
    "flash": 2500,    # DeepSeek Flash 账号并发（Pro 档 2026-08-26 已摘除）
    "machines": 50,   # Fly 每组织默认机器配额，再高要发邮件申请
}
# 建议余量：压到 100% 时每撞一次墙就是一趟白跑的往返，留两成是既有的工程惯例
SAFE_RATIO = 0.8

# 强制某档时的机器数——**由引擎派生，不让人设**：设了它就是第二个能设错的地方，
# 而它的正确值完全由账号上限 ÷ 每机路数决定，没有判断余地。
#   flash 50 × 40 = 2000，占账号 2500 的 80%（50 = Fly 机器配额上限）
FORCE_MACHINES = {"flash": 50}

_FIELDS = ("fly_max_machines", "max_transcribe_jobs", "claude_concurrency",
           "force_engine", "force_expires_at")


def _defaults() -> dict[str, Any]:
    return {"fly_max_machines": config.FLY_MAX_MACHINES,
            "max_transcribe_jobs": config.MAX_TRANSCRIBE_JOBS,
            "claude_concurrency": config.CLAUDE_MAX_CONCURRENCY,
            "force_engine": None, "force_expires_at": None,
            "updated_by": None, "updated_at": None}


def get() -> dict[str, Any]:
    """当前生效配置。DB 为 NULL 的项回落 env；**读不到一律回落，不抛异常**。"""
    out = _defaults()
    try:
        with db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_FIELDS)}, updated_by, updated_at "
                "FROM p3_config WHERE id = 1").fetchone()
    except Exception:  # noqa: BLE001  配置表没建/连不上库都不该拖垮转录
        return out
    if not row:
        return out
    keys = list(_FIELDS) + ["updated_by", "updated_at"]
    for k, v in zip(keys, row):
        if v is not None:
            out[k] = v
    return drop_if_expired(out)


def drop_if_expired(cfg: dict[str, Any]) -> dict[str, Any]:
    """过期的强制引擎视同没设。

    判定放在**读取侧**而不是靠清扫任务：没有定时器也能自动失效，
    少一个「清扫没跑起来 → 强制引擎永远开着」的失败模式。
    独立成函数是为了让它不依赖 get() 的控制流——get() 有一条读库失败的早退路径，
    过期判定跟在那条路径后面的话，将来谁改动早退顺序就会静默失效。"""
    exp = cfg.get("force_expires_at")
    if cfg.get("force_engine") and exp:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=_dt.timezone.utc)
        if exp <= _dt.datetime.now(_dt.timezone.utc):
            cfg = {**cfg, "force_engine": None}
    return cfg


def forced_engine() -> str | None:
    """强制引擎（已考虑过期）→ 'flash' / None。Fly 机器侧调用。"""
    return get().get("force_engine")


def effective_max_machines() -> int:
    """派单时该用的机器数上限。

    强制某档时用 FORCE_MACHINES 派生值——那一档所有机器都跑同一个引擎，
    总并发 = 机器数 × PER_MACHINE_CONC 必须压在该账号上限内，常态那个 50 会直接撞穿。"""
    c = get()
    fe = c.get("force_engine")
    if fe and fe in FORCE_MACHINES:
        return FORCE_MACHINES[fe]
    return int(c["fly_max_machines"])


def effective_transcribe_jobs() -> int:
    """派单时该用的**转录**任务数上限（成本闸）。与 effective_max_machines 是两回事：
    那个管「所有机器」（转录 + 后处理共用一池），这个只管转录那部分——两者的差额就是
    留给后处理的位置。强制某档时不派生：强制只换引擎，不改转录/后处理的分配。"""
    return int(get()["max_transcribe_jobs"])


def usage_of(engine: str, machines: int) -> dict[str, Any]:
    """某档在给定机器数下的并发占用 → 供前端实时显示，也供保存前校验。"""
    cap = LIMITS.get(engine)
    if not cap:
        return {"engine": engine, "used": None, "cap": None, "ratio": None}
    # Claude 一机一路（它是订阅并发墙，不是 API 并发）；DeepSeek 两档才乘每机路数
    used = machines if engine == "claude" else machines * PER_MACHINE_CONC
    return {"engine": engine, "used": used, "cap": cap, "ratio": used / cap,
            "suggestMax": int(cap * SAFE_RATIO) // (1 if engine == "claude" else PER_MACHINE_CONC)}


def validate(patch: dict[str, Any]) -> list[str]:
    """→ 错误列表，空 = 可保存。**按乘积校验**，不是按单个数（见模块头）。"""
    errs: list[str] = []
    cur = get()
    m = patch.get("fly_max_machines", cur["fly_max_machines"])
    if m is not None and not (1 <= int(m) <= LIMITS["machines"]):
        errs.append(f"常态机器数要在 1–{LIMITS['machines']}（Fly 组织配额上限）")

    # 转录任务数：**这才是全系统真正的吞吐天花板**（2026-08-26 盘点）。此前它只活在代码
    # 缺省值里——Render 上没设、运营舱里也没有，是三道机器闸里唯一改不了的那道，
    # 而恰恰是最紧的那道。搬进来就是为了让它看得见、改得动。
    tj = patch.get("max_transcribe_jobs", cur["max_transcribe_jobs"])
    if tj is not None and not (1 <= int(tj) <= LIMITS["machines"]):
        errs.append(f"转录任务数要在 1–{LIMITS['machines']}（不能超过 Fly 组织配额）")
    if tj is not None and m is not None and int(tj) > int(m):
        errs.append(f"转录任务数（{int(tj)}）不能大于常态机器数（{int(m)}）——多出来的名额永远派不出去")
    if tj is not None:
        # 按**乘积**校验：每台转录机跑 P3 时内部还开 PER_MACHINE_CONC 路，撞的是 DeepSeek 账号并发。
        # 当前配额下（机器 ≤ 50）这条永远不会触发，留着是因为**改 PER_MACHINE_CONC 或提 Fly 配额时
        # 它会先红**——而撞穿账号并发不报错，只表现为退避重试导致的莫名变慢。
        u = usage_of("flash", int(tj))
        if u["ratio"] and u["ratio"] > 1:
            errs.append(f"转录任务数 {int(tj)} 台 × 每机 {PER_MACHINE_CONC} 路 = {u['used']}"
                        f" > DeepSeek 账号并发 {LIMITS['flash']}")

    cc = patch.get("claude_concurrency", cur["claude_concurrency"])
    if cc is not None and not (1 <= int(cc) <= LIMITS["claude"]):
        errs.append(f"Claude 并发要在 1–{LIMITS['claude']}（订阅并发墙，超了每次都撞）")

    fe = patch.get("force_engine", cur["force_engine"])
    if fe is not None and fe not in ("flash",):
        errs.append("强制引擎只能是 flash——claude 本就是第一档，强制它只会让任务挤在订阅墙上排队")
    return errs


def save(patch: dict[str, Any], by: str) -> dict[str, Any]:
    """校验后写入（单行 upsert）。校验不过抛 ValueError，调用方转 400。"""
    errs = validate(patch)
    if errs:
        raise ValueError("；".join(errs))
    cols = [k for k in _FIELDS if k in patch]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO p3_config (id, updated_by) VALUES (1, %s) "
            "ON CONFLICT (id) DO NOTHING", (by,))
        if cols:
            sets = ", ".join(f"{c} = %s" for c in cols)
            conn.execute(f"UPDATE p3_config SET {sets}, updated_by = %s, updated_at = now() "
                         "WHERE id = 1", tuple(patch[c] for c in cols) + (by,))
    return get()

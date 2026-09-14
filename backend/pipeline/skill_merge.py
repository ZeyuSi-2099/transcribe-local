"""P3·Claude+Skill 引擎 + 撞顶四形态派单兜底。

默认引擎 = claude（高质量原则）：`claude -p "/multi-asr-merge <P2_match>"` 走订阅 OAuth
（`env -u ANTHROPIC_API_KEY`），模型锁 Opus 4.8、effort 锁 medium，联网用原生 WebSearch，
`--strict-mcp-config` 去 MCP 省额度。**与 DeepSeek 路只差模型**：规则/术语库/输出/联网核实一致。

撞顶四形态，全部降级 DeepSeek（`merge.run_merge`）+ 计数（升 Max20 信号灯）：
  ① 5h 额度  rate_limit_event five_hour status∈{allowed_warning, rejected}
  ② 周额度   rate_limit_event seven_day status==rejected（warning 不停，尽量用满）
  ③ 并发     在飞 Claude > P3_CLAUDE_CONCURRENCY → 非阻塞信号量挡下，溢出走 DeepSeek
  ④ 硬错     is_error/退出码/异常，重试 P3_CLAUDE_RETRY 次仍败 → DeepSeek
预判门：每次调用把带回的 rate_limit_event 缓存（status+resetsAt），下任务命中
warning/rejected 且未到 resetsAt 则直接 DeepSeek、不试 Claude；过 resetsAt 乐观重试。

撞顶检测基于 `--output-format json` 的 `rate_limit_event`（headless 只给三态、无百分比）。
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import conflicts, merge, p3_lang
from .transcript import parse_transcript_md

# **故意用别名而不是钉死版本**：`opus` 随 CLI 解析到当时最新的 Opus，新版本发布即自动跟上，
# 不需要改代码。2026-08-12 实测 `claude -p --model opus` 返回 `claude-opus-5`
# ——而这行注释此前写的是「Opus 4.8」，代码一个字没动，正是别名自动跟随的证据。
# 所以别把它换成具体版本号（那会让我们停在某一代），也别再往注释里写版本号（写了就会过期）。
CLAUDE_MODEL = "opus"
CLAUDE_EFFORT = "medium"     # 锁 medium
ALLOWED_TOOLS = "Read,Edit,Write,Bash,WebSearch,Skill,Glob,Grep"
_RETRY_DEFAULT = 2


# ============ 撞顶分类 ============

# 撞顶关键词（容器 token 模式无 rate_limit_event 时，从错误文本/状态判撞顶）
_CAP_KEYWORDS = ("429", "rate limit", "rate_limit", "usage limit", "quota", "too many requests")

# 认证/订阅失效关键词。**必须与撞顶分开**：两者都让 Claude 不可用，但处置完全相反——
# 撞顶是等额度回来（自愈），认证失效要人去续订或重新生成令牌（不动手永远不会好）。
# 混在一起报「报错」的话，运营看着面板也不知道该干什么。撞顶优先判（429 更具体）。
_AUTH_KEYWORDS = ("401", "403", "unauthorized", "forbidden", "authentication",
                  "invalid_token", "invalid token", "invalid api key",
                  "login expired", "please run /login", "oauth")


def classify_claude_result(stdout: str, returncode: int) -> dict:
    """解析 `claude -p --output-format json`。两种形态都认：
    - **本机/claude.ai 登录态**：数组 [system, assistant, rate_limit_event, result]，靠 rate_limit 三态。
    - **容器/token 模式（T7.1 实测）**：单个 result 对象，无 rate_limit_event → 撞顶靠 is_error +
      api_error_status(429) + 错误文本关键词。

    返回 {state: ok|capped|error, window: five_hour|seven_day|None,
          rate_limits: [...], message}. 撞顶优先于 error；周 rejected 优先于 5h。"""
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"state": "error", "window": None, "rate_limits": [], "message": "Claude 输出非 JSON"}

    items = parsed if isinstance(parsed, list) else [parsed]
    rate_limits: list[dict] = []
    result_is_error = False
    result_blob = ""   # api_error_status + result 文本：容器模式撞顶关键词检测用
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "rate_limit_event":
            info = it.get("rate_limit_info") or {}
            rate_limits.append({
                "window": info.get("rateLimitType"),
                "status": info.get("status"),
                "resets_at": info.get("resetsAt"),
            })
        elif it.get("type") == "result":
            result_is_error = bool(it.get("is_error"))
            result_blob = " ".join(str(it.get(k) or "") for k in ("api_error_status", "result", "subtype"))

    # 路径①：有 rate_limit_event（本机）→ 三态判定
    windows = {rl["window"]: rl["status"] for rl in rate_limits if rl.get("window")}
    if windows.get("seven_day") == "rejected":
        capped = "seven_day"
    elif windows.get("five_hour") in ("allowed_warning", "rejected"):
        capped = "five_hour"
    else:
        capped = None

    # 路径②：无 rate_limit_event（容器 token 模式）+ is_error → 看错误文本/429 关键词
    if capped is None and (result_is_error or returncode != 0):
        blob = result_blob.lower()
        if any(k in blob for k in _CAP_KEYWORDS):
            capped = "seven_day" if any(w in blob for w in ("week", "7-day", "seven")) else "five_hour"

    if capped:
        state = "capped"
    elif result_is_error or returncode != 0:
        blob = result_blob.lower()
        state = "auth" if any(k in blob for k in _AUTH_KEYWORDS) else "error"
    else:
        state = "ok"
    # auth 把原始报错带出去：运营舱要显示「令牌怎么坏的」，光说「认证失败」不够定位
    message = result_blob.strip()[:200] if state == "auth" else ""
    return {"state": state, "window": capped, "rate_limits": rate_limits, "message": message}


# ============ Claude 执行 ============

def build_claude_cmd(prompt: str) -> list[str]:
    """对齐旧 skills.py：env -u 强制订阅 OAuth；锁 opus/medium；JSON 出额度；去 MCP 省额度。"""
    return [
        "env", "-u", "ANTHROPIC_API_KEY", "-u", "ANTHROPIC_AUTH_TOKEN",
        "claude", "-p", prompt,
        "--model", CLAUDE_MODEL,
        "--effort", CLAUDE_EFFORT,
        "--output-format", "json",
        "--add-dir", ".",
        "--strict-mcp-config",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
    ]


def _build_prompt(match_file: str, glossary_text: str, ui_lang: str | None = None) -> str:
    """用户术语库内联进提示词（与 DeepSeek 路同等作硬证据，优先于联网）。

    ⚠️ **除了这里内联的这一份，不许再有第二本术语库**（2026-08-22）。此前 vendor 根目录下
    躺着一本 `Config/Config_Term.md`（华为渠道零售专名，为某个具体项目建的）——claude -p 的
    工作目录正是 vendor 根、Read 工具又是放开的，而 skill 第一句就是「先查本次任务的术语库」，
    于是它自己读了。后果：**用户一本库都没选，却吃到一份跟他无关的行业词表**，近音词被悄悄
    拽向那个行业的写法，且界面上没有任何交代（依据显示成「引擎证据」）。已删库 + 守卫钉死。"""
    prompt = f"/multi-asr-merge {match_file}"
    if glossary_text and glossary_text.strip():
        prompt += (
            "\n\n【本任务用户术语库（作硬证据，优先于联网核实）】\n"
            + glossary_text.strip()
        )
    # 语言指令拼在最后 → 覆盖 skill 里的默认写法（DeepSeek 路走 env，见 merge.run_merge；
    # **两条路都要送**，只送一条的症状是「有时候是对的」）。
    return prompt + p3_lang.directive(ui_lang)


def run_skill_merge(match_file: str, vendor_root, glossary_text: str = "",
                    ui_lang: str | None = None) -> tuple:
    """跑 claude -p 融合。返回 (终稿路径 or None, classify dict)。

    成功 → (新产物路径, cls{state:ok})；撞顶/硬错 → (None, cls)。
    用 snapshot diff 取本次新产物（避开并发下 glob-by-mtime 取错文件）。"""
    vendor_root = Path(vendor_root)
    out_dir = vendor_root / "Output"
    before = set(out_dir.glob("*_P3_Merge_*.md")) if out_dir.exists() else set()

    # 分歧点清单：数票交给程序，模型只做判断（见 conflicts.py 文件头的实测与三条教训）。
    # 副本放 Output/_p3in/（同名不同目录，产物命名不受影响；原 P2 留给 DeepSeek 兜底路）。
    skill_input = conflicts.augmented_input(match_file, out_dir / "_p3in")
    cmd = build_claude_cmd(_build_prompt(skill_input, glossary_text, ui_lang))
    # 必须带 timeout：P3 心跳会一直喂看门狗（防误判双跑），真挂死（CLI 网络读无限阻塞）时
    # 看门狗永不触发，超时是唯一回收手段——超时即杀子进程，交 p3_merge 降级 DeepSeek。
    try:
        proc = subprocess.run(cmd, cwd=str(vendor_root), env=dict(os.environ),
                              capture_output=True, text=True,
                              timeout=int(os.environ.get("P3_TIMEOUT_SEC", "3600")))
    except subprocess.TimeoutExpired:
        return None, {"state": "timeout", "window": None, "rate_limits": [],
                      "message": f"Claude 超时（>{os.environ.get('P3_TIMEOUT_SEC', '3600')}s）"}
    stdout = getattr(proc, "stdout", "") or ""
    if stdout:
        print(stdout[-2000:], flush=True)   # 透传尾部到 worker 日志
    cls = classify_claude_result(stdout, getattr(proc, "returncode", 1))
    if cls["state"] != "ok":
        return None, cls

    after = set(out_dir.glob("*_P3_Merge_*.md"))
    new = [p for p in (after - before) if not p.name.endswith(("_report.md", "_raw.md"))]
    if new:
        path = str(max(new, key=lambda p: p.stat().st_mtime))
    else:   # 兜底：退回最新（与 DeepSeek 路一致）
        path = merge.find_latest_output(out_dir, "_P3_Merge_")
    # 形状检查（2026-09-03）：模型写的稿，形状不受我们控制。此前只在 P4 最后一步才拿解析器去读，
    # 认不出一行就整单失败——而那时五路 ASR 的钱已经花完、Claude 也已经跑完。在这里用**同一个解析器**
    # 先读一遍：0 行就按硬错处理，走现有的「重试 → 降级 DeepSeek」阶梯（DeepSeek 分批版是逐段照抄
    # 时间区间的，形状稳定）。判据必须是 P4 那个解析器本身，另写一份正则会跟它悄悄分家。
    if not parse_transcript_md(Path(path).read_text(encoding="utf-8")):
        return None, {"state": "error", "window": None, "rate_limits": cls.get("rate_limits", []),
                      "message": f"终稿格式不合规（0 行可解析）：{Path(path).name}"}
    return path, cls


# ============ 派单 + 四形态兜底（共享状态：缓存 + 信号量 + 计数）============

_cache_lock = threading.Lock()
_rl_cache: dict = {}
_counters: dict = {}
_sem: threading.BoundedSemaphore = None  # type: ignore
# 可注入的全局 Claude 闸（多机：worker 单任务模式注入 DB gate；None：用进程内 _sem 单机/测试）。
# gate 接口：acquire() -> bool（拿到名额）, release()。
_claude_gate = None


def set_claude_gate(gate) -> None:
    """注入全局 Claude 并发闸。多机（任务级机器）下进程内信号量管不住跨机并发，
    由 worker 单任务模式注入 DB gate（见 app/claude_gate.py）；单机/测试不注入，回落进程内 _sem。"""
    global _claude_gate
    _claude_gate = gate


# DeepSeek 兜底档的模型名，由 worker 注入；空 = 不传 model 参数，子进程用自己的
# P3_DS_MODEL 缺省（本地 docker / 单机回滚态 FLY_DISPATCH=0 的既有行为，一字不改）。
_flash_model = ""


def set_flash_model(model: str = "") -> None:
    """注入 DeepSeek 兜底档的模型名。

    ⚠️ 2026-08-26 由 `set_pro_gate(gate, pro_model, flash_model)` 简化而来：**Pro 档已彻底下线**
    （2026-08-11 起 `_PRO_ENABLED=False` 停用，2026-08-26 Duner 定「不会再用」，代码一并摘掉）。
    摘的时候要留意：那个函数同时干着两件事——注入 Pro 闸、注入两档模型名——**后一件与 Pro 无关**，
    连着删掉的话 Flash 的模型名就没人注入了，子进程会悄悄回落到它自己的缺省模型。"""
    global _flash_model
    _flash_model = model


def _new_counters() -> dict:
    return {"cap_5h": 0, "cap_week": 0, "cap_concurrency": 0,
            "hard_error": 0, "claude_ok": 0, "auth": 0}


def reset_state(concurrency: int = None) -> None:
    """重置缓存 + 计数 + 信号量 + 清注入的闸（测试用 / 模块加载时初始化）。"""
    global _rl_cache, _counters, _sem, _claude_gate
    with _cache_lock:
        _rl_cache = {}
    _counters = _new_counters()
    n = concurrency if concurrency is not None else int(os.environ.get("P3_CLAUDE_CONCURRENCY", "5"))
    _sem = threading.BoundedSemaphore(max(1, n))
    _claude_gate = None


reset_state()


def get_counters() -> dict:
    return dict(_counters)


def prime_cache(rate_limits: list) -> None:
    _update_cache(rate_limits)


def _update_cache(rate_limits) -> None:
    with _cache_lock:
        for rl in rate_limits or []:
            if rl.get("window"):
                _rl_cache[rl["window"]] = {"status": rl.get("status"), "resets_at": rl.get("resets_at")}


def _preempt_reason() -> str:
    """查缓存：5h warning/rejected → cap_5h；周 rejected → cap_week。已过 resets_at 视为失效。"""
    now = time.time()
    with _cache_lock:
        for window, info in _rl_cache.items():
            ra = info.get("resets_at")
            if ra and now >= ra:
                continue
            st = info.get("status")
            if window == "five_hour" and st in ("allowed_warning", "rejected"):
                return "cap_5h"
            if window == "seven_day" and st == "rejected":
                return "cap_week"
    return ""


def _gate_capped_reason(gate) -> str:
    """问全局闸：当前是否在撞顶冷却中（跨机共享，见 app/p3_health.cap_state）。

    进程内的 _rl_cache 只在本机有效，而线上一台 Fly 机器只跑一个任务 —— 没有这一步，
    A 机器撞的墙 B 机器一无所知，十台机器就要各撞一次，每次都是一趟白跑的订阅调用。
    闸没实现该能力（单机/测试的 fake）或 DB 抖动 → 回落进程内缓存；**查不到状态绝不能挡住
    转录**，故异常一律吞掉当作未撞顶（最坏情况不过是退回旧行为：自己撞一次墙再降级）。"""
    fn = getattr(gate, "capped_reason", None) if gate is not None else None
    if fn is None:
        return ""
    try:
        return fn() or ""
    except Exception as e:  # noqa: BLE001
        print(f"[P3] 读全局撞顶状态失败（按未撞顶继续）：{e}", flush=True)
        return ""


def _report(gate, outcome: str, **kw) -> None:
    """把本次 P3 结果上报给闸（落 p3_events）。埋点失败只打日志——监控坏了不能连累转录。"""
    fn = getattr(gate, "report", None) if gate is not None else None
    if fn is None:
        return
    try:
        fn(outcome, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"[P3] 健康度埋点失败（不影响转录）：{e}", flush=True)


def _resets_at_of(cls: dict, window: str | None):
    """取该窗口的额度恢复时刻（epoch 秒）。令牌模式无 rate_limit_event → None，冷却按兜底窗算。"""
    for rl in cls.get("rate_limits") or []:
        if rl.get("window") == window:
            return rl.get("resets_at")
    return None


def _engine() -> str:
    return os.environ.get("P3_ENGINE", "claude").strip().lower()


def _deepseek(match_file, vendor_root, glossary_text, ui_lang=None):
    """降级到 DeepSeek。**单档 Flash，无闸**（账号并发 2500，够我们造出来的任何并发）。

    这个「汇流点」要留着，别把它拍平回 p3_merge：Claude 那边有七八个出口
    （预判撞顶 / 并发闸满 / capped / auth / timeout / 硬错 / 引擎=deepseek），
    逐条写降级既啰嗦又必然漏一个；它们最终都汇到本函数，在这里处理一次就够。

    ⚠️ **Pro 档 2026-08-26 已彻底摘除**（2026-08-11 起就停用了）。要恢复的话别只把分支加回来：
    它账号并发只有 500、必须配一道闸，而闸的上限、运营舱的强制档、机器数派生值是一整套
    （见 app/p3_config.py 的 LIMITS / FORCE_MACHINES）。"""
    if not _flash_model:   # 未注入：**连 model 参数都不传**，调用形状与单档时代逐字相同
        return merge.run_merge(match_file, vendor_root=vendor_root, glossary_text=glossary_text,
                               ui_lang=ui_lang)
    print("[P3] 降级 DeepSeek·Flash", flush=True)
    return merge.run_merge(match_file, vendor_root=vendor_root,
                           glossary_text=glossary_text, model=_flash_model, ui_lang=ui_lang)


def p3_merge(match_file: str, vendor_root, glossary_text: str = "",
             ui_lang: str | None = None) -> tuple:
    """P3 派单入口。返回 (终稿路径, 成本USD)，与 run_merge 同契约。

    真正的三档阶梯派单在 `_p3_merge_dispatch`；这里只把**强制引擎**这条旁路摘出来，
    因为它的语义与阶梯相反——阶梯是「优先谁、不行就降级」，强制是「就用这个，不降级、
    不过闸」。混在一起写的话，dispatch 里那七八个降级出口每条都得先判一次 forced。"""
    forced = _forced_engine()
    if forced:
        # 强制引擎（运营舱设的测试态）：直接走该档，不走阶梯、不降级。
        # 只认 flash —— claude 本就是第一档，强制它只会让任务挤在订阅墙上排队。
        print(f"[P3] 强制引擎 = {forced}（运营舱设置，跳过阶梯）", flush=True)
        # **不走并发闸**：强制态的总并发由运营舱的「强制模式机器数」控制
        # （保存时按 机器数 × 每机 50 路 ≤ 账号上限 校验过）。再叠一道闸只会让任务
        # 卡在闸上排队，而强制的语义是「就用这个引擎」，不是「优先这个引擎」。
        path, cost = merge.run_merge(match_file, vendor_root=vendor_root,
                                     glossary_text=glossary_text,
                                     model=_model_of(forced), ui_lang=ui_lang)
    else:
        path, cost = _p3_merge_dispatch(match_file, vendor_root, glossary_text, ui_lang)
    return path, cost


def _model_of(engine: str) -> str:
    """档位名 → 模型名。优先用 set_flash_model 注入的值，未注入（本地/回滚态）时回落 config。
    回落是必要的：不给 model 参数的话子进程会用它自己的 P3_DS_MODEL 缺省，那就不是强制了。"""
    if _flash_model:
        return _flash_model
    try:
        from app import config as _cfg
        return _cfg.DS_MODEL_FLASH
    except Exception:  # noqa: BLE001
        return "deepseek-flash"


def _forced_engine():
    """运营舱设的强制引擎（已考虑过期）→ 'flash' / None。

    读不到一律返回 None 走正常阶梯——**配置读失败绝不能让转录停摆**，
    而且回落到阶梯是安全的那一侧（阶梯本身有完整的降级链）。"""
    try:
        from app import p3_config
        return p3_config.forced_engine()
    except Exception:  # noqa: BLE001  本地/回滚态没有 app 包，或配置表还没建
        return None


def _p3_merge_dispatch(match_file: str, vendor_root, glossary_text: str = "",
                       ui_lang: str | None = None) -> tuple:
    """真正的派单逻辑。

    deepseek 引擎：直走 DeepSeek。claude 引擎：预判门 → 并发门 → Claude（撞顶/硬错降级 DeepSeek）。
    Claude 成功成本计 0（订阅边际≈0）。"""
    if _engine() == "deepseek":
        return _deepseek(match_file, vendor_root, glossary_text, ui_lang)

    # 预判门：先问全局闸（跨机撞顶冷却），再回落进程内缓存（单机/测试）。
    gate = _claude_gate
    pre = _gate_capped_reason(gate) or _preempt_reason()
    if pre:
        _counters[pre] += 1
        print(f"[P3] 预判撞顶({pre})→降级 DeepSeek", flush=True)
        _report(gate, "preempt", note=pre)
        return _deepseek(match_file, vendor_root, glossary_text, ui_lang)

    # 并发门：有注入的全局闸（多机 DB）用它，否则用进程内信号量（单机/测试）。
    if gate is not None:
        if not gate.acquire():
            _counters["cap_concurrency"] += 1
            print("[P3] 全局并发闸满(跨机在飞≥上限)→降级 DeepSeek", flush=True)
            _report(gate, "concurrency")
            return _deepseek(match_file, vendor_root, glossary_text, ui_lang)
    elif not _sem.acquire(blocking=False):
        _counters["cap_concurrency"] += 1
        print("[P3] 并发撞顶(在飞>limit)→降级 DeepSeek", flush=True)
        return _deepseek(match_file, vendor_root, glossary_text, ui_lang)

    try:
        retries = int(os.environ.get("P3_CLAUDE_RETRY", str(_RETRY_DEFAULT)))
        cls: dict = {}     # 每轮都抛异常时循环结束仍未绑定，兜底出口要读它
        last_err = ""
        for _ in range(retries + 1):
            try:
                path, cls = run_skill_merge(match_file, vendor_root, glossary_text, ui_lang)
            except Exception as e:  # noqa: BLE001  子进程炸了等
                last_err = str(e)
                print(f"[P3] Claude 调用异常：{e}", flush=True)
                continue
            _update_cache(cls.get("rate_limits"))
            if cls["state"] == "ok":
                _counters["claude_ok"] += 1
                _report(gate, "ok")
                return path, 0.0
            if cls["state"] == "capped":
                window = cls.get("window")
                _counters["cap_5h" if window == "five_hour" else "cap_week"] += 1
                print(f"[P3] 撞顶({window})→降级 DeepSeek", flush=True)
                # 这一行是后续所有机器跳过 Claude 的依据（撞顶冷却）
                _report(gate, "capped", window=window,
                        resets_at=_resets_at_of(cls, window), note=cls.get("message"))
                return _deepseek(match_file, vendor_root, glossary_text, ui_lang)
            if cls["state"] == "auth":
                # 订阅/令牌失效：重试一百次也一样，直接降级。**故意不进撞顶冷却**——冷却是给
                # 额度用的（会自愈），认证坏了得人去修；保持每单都试，人一修好下一单立刻恢复。
                _counters["auth"] += 1
                print("[P3] 认证失效（订阅或令牌）→降级 DeepSeek，不重试", flush=True)
                _report(gate, "auth", note=cls.get("message"))
                return _deepseek(match_file, vendor_root, glossary_text, ui_lang)
            if cls["state"] == "timeout":
                # 超时不进重试循环：再试一次又是一整个超时窗，直降 DeepSeek
                _counters["hard_error"] += 1
                print("[P3] Claude 超时→降级 DeepSeek（不重试）", flush=True)
                _report(gate, "timeout", note=cls.get("message"))
                return _deepseek(match_file, vendor_root, glossary_text, ui_lang)
            # error → 重试
        _counters["hard_error"] += 1
        print("[P3] Claude 硬错重试用尽→降级 DeepSeek", flush=True)
        _report(gate, "error", note=cls.get("message") or last_err or None)
        return _deepseek(match_file, vendor_root, glossary_text, ui_lang)
    finally:
        if gate is not None:
            gate.release()
        else:
            _sem.release()

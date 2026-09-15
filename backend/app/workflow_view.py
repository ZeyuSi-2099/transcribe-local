"""运行面板「工作流」Tag 的静态配置视图：各节点的执行体 / 参数 / 提示词。

**为什么要有这个模块，而不是把数字写进前端**：这些值会漂移。块长、分批参数、丢字护栏
都在配置和代码里随实测调，抄一份到前端就等于埋了个「界面写 6000、实际跑 8000」的坑，而且这种
坑不会报错。所以这里一律读真实配置与常量、或从真实文本里算指纹，**不重新誊写任何一个数字**。

同理，prose（这一环干什么、怎么理解）全部留在前端——那部分不会漂移，且要走 L(中, 英)。
**后端只出事实，前端只出文案。**

本机版（与线上不同）：线上这一页描述的是云端流水线——主轨加参考轨、vendor 下的脚本与 SKILL、
Claude 订阅闸、Fly 任务镜像。本机一样都没有，原样搬来一打开就报错（导入云端编排模块失败）。
本机的流水线是 src/transcribe_local：P0 转码、声纹分段、VAD、切块 → P1 四路本地识别 → 分歧册 →
P3 定字（模型后端按「设置」）→ 后处理。参数读**实际生效的配置**（默认配置叠上用户配置），
提示词读包里真正送出去的那几份。线上「指纹取自派单前台、不证明任务机器上那份」的口径本机没有：只有一份。
"""
import hashlib
from urllib.parse import urlparse

from . import config

GLOSSARY_PROMPT_ID = "glossary-assist"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _prompt_texts() -> dict[str, tuple[str, str]]:
    """提示词清单：id → (出处, 全文)。id 同时是取全文的入参。取不到的那一份跳过（不编造）。"""
    out: dict[str, tuple[str, str]] = {}
    try:
        from pipeline import local_orchestrator  # noqa: F401  顺带把 src/ 加进 sys.path
        from transcribe_local import _merge_zh
        # 两份定字提示词：走 API 且开联网核实时用与 SaaS 同款的那份，本机模型 / 不联网时用离线版（fuse.system_prompt）
        out["merge-saas"] = ("src/transcribe_local/_merge_zh.py · PROMPT_SAAS", _merge_zh.PROMPT_SAAS)
        out["merge-offline"] = ("src/transcribe_local/_merge_zh.py · PROMPT", _merge_zh.PROMPT)
    except Exception:  # noqa: BLE001  取不到就不显示这一条，别让整个接口塌掉
        pass
    try:
        from pipeline import _pp_prompts as pp
        out["pp-narrate"] = ("backend/pipeline/_pp_prompts.py · PP_NARRATE", str(pp.PP_NARRATE))
        out["pp-redact"] = ("backend/pipeline/_pp_prompts.py · PP_REDACT", str(pp.PP_REDACT))
        out["shared-qc"] = ("backend/pipeline/_pp_prompts.py · SHARED_QC", str(pp.SHARED_QC))
    except Exception:  # noqa: BLE001
        pass
    # 术语库助手的提示词**不是单独文件，是代码常量**——而那段文字就是这个功能的产品定义
    # （收多少条、什么算术语全在里面），改它等于改功能。所以它和其余几份一样要能看全文、带指纹。
    try:
        from . import glossary_assist
        out[GLOSSARY_PROMPT_ID] = ("backend/app/glossary_assist.py · _RULES", glossary_assist._RULES)
    except Exception:  # noqa: BLE001
        pass
    return out


def prompt_full(pid: str) -> dict | None:
    """取一份提示词全文（只读）。未知 id / 读不到 → None，调用方转 404。"""
    hit = _prompt_texts().get(pid) if pid else None
    if not hit:
        return None
    path, text = hit
    return {"id": pid, "path": path, "sha": _digest(text), "text": text}


def _prompts() -> dict:
    return {pid: {"path": path, "lines": text.count("\n") + 1, "sha": _digest(text)}
            for pid, (path, text) in _prompt_texts().items()}


def _backend() -> dict:
    """定字、术语库助手、后处理共用的模型后端（「设置」里选的那一个）。只报密钥变量设没设，不报值。"""
    from pipeline import model_backend
    cur = model_backend.current()
    key = model_backend.key_status(cur)
    return {
        "preset": cur["preset"],
        "kind": cur["backend"],                      # openai = OpenAI 协议（API 或本机模型）；claude_p = Claude 订阅
        "model": cur["model"],
        "host": urlparse(cur["base_url"] or "").hostname or "",
        "local": cur["backend"] == "openai" and model_backend.is_local(cur["base_url"]),
        "webSearch": cur["web_search"],
        "keyEnv": key["env"],
        "keySet": key["set"],
    }


def snapshot() -> dict:
    """工作流 Tag 要的全部事实。任何一块取不到就缺那一块，其余照给——
    这是运营页的只读视图，不该因为读不到一个常量就整页空白。"""
    from pipeline import pp_deepseek, pp_redact_ds
    from pipeline.local_orchestrator import config_path, tl_config
    from transcribe_local import engines as tl_engines
    from transcribe_local import mem

    from . import glossary_assist

    cfg = tl_config.load(config_path())
    enabled = list(cfg["engines"]["enabled"])
    try:
        parallel, parallel_why = mem.plan(cfg, enabled)
    except Exception:  # noqa: BLE001
        parallel, parallel_why = None, ""
    cb = cfg["engines"].get("circuit_breaker") or {}
    p3 = cfg["p3"]
    claude = p3.get("claude") or {}
    return {
        "prompts": _prompts(),
        "backend": _backend(),
        "params": {
            "glossary": {
                "timeoutSec": glossary_assist.TIMEOUT_SEC,
                "maxEntries": glossary_assist.MAX_ENTRIES,
                "meaningMax": glossary_assist.MEANING_MAX,
                "totalMax": glossary_assist.TOTAL_MAX,
                "outlineMax": glossary_assist.MAX_OUTLINE_CHARS,
            },
            "p0": {
                "sampleRate": cfg["audio"]["sample_rate"],
                "channels": cfg["audio"]["channels"],
                "segmentation": cfg["diarize"]["segmentation"],
                "embedding": cfg["diarize"]["embedding"],
                "numClusters": cfg["diarize"]["num_clusters"],
                "speakerSplit": bool(cfg["diarize"].get("speaker_split")),
                "vad": cfg["vad"]["model"] if cfg["vad"]["enabled"] else None,
                "chopMaxLength": cfg["chop"]["max_length"],
                "chopStrategy": cfg["chop"]["strategy"],
                "maxDurationSec": config.MAX_DURATION_SEC,
            },
            "p1": {
                "engines": [{"id": e, "tag": tl_engines.TAG.get(e, e), "route": tl_engines.ROUTE.get(e, "")}
                            for e in enabled],
                "numThreads": cfg["engines"].get("num_threads"),
                "parallel": parallel,
                "parallelWhy": parallel_why,
                "repeatThreshold": cb.get("repeat_threshold"),
                "maxRetry": cb.get("max_retry"),
            },
            "p2": {
                "foldFillers": cfg["divergence"]["fold_fillers"],
                "fillers": cfg["divergence"]["fillers"],
                "stripTags": cfg["divergence"]["strip_tags"],
            },
            "p3": {
                "workflow": p3.get("workflow"),
                "roundTokens": p3.get("round_tokens"),
                "overlap": p3.get("overlap"),
                "concurrency": p3.get("concurrency"),
                "timeoutSec": p3.get("timeout"),
                "maxRetry": p3.get("max_retry"),
                "maxTokens": p3.get("max_tokens"),
                "termsInject": bool(cfg["terms"].get("inject_p3", True)),
                "claudeModel": claude.get("model"),
                "claudeEffort": claude.get("effort"),
                "claudeTimeoutSec": claude.get("timeout"),
            },
            "pp": {
                "narrateBudget": pp_deepseek.STEP1_BUDGET,
                "narrateConc": pp_deepseek.CONC,
                "dropWarn": pp_deepseek.DROP_WARN,
                "dropFatal": pp_deepseek.DROP_FATAL,
                "redactBudget": pp_redact_ds.BUDGET,
                "redactConc": pp_redact_ds.CONC,
                "redactPasses": pp_redact_ds.PASSES,
                "redactReview": pp_redact_ds.REVIEW,
                "listMaxChars": config.PP_LIST_MAX_CHARS,
            },
            "job": {"queuedMaxHours": config.QUEUED_MAX_HOURS},
        },
    }

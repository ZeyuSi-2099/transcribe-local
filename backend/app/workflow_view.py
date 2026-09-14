"""运营驾驶舱「工作流」Tag 的静态配置视图：八个节点的执行体 / 参数 / 降级路。

**为什么要有这个模块，而不是把数字写进前端**：这些值会漂移。并发闸、分批参数、丢字护栏
都在代码里随实测调，抄一份到前端就等于埋了个「界面写 6000、实际跑 8000」的坑，而且这种
坑不会报错。所以这里一律 import 真实常量、或从真实文件里读，**不重新誊写任何一个数字**。

同理，prose（这一环干什么、怎么理解）全部留在前端——那部分不会漂移，且要走 L(中, 英)。
**后端只出事实，前端只出文案。**

⚠️ **指纹只能证明「派单前台这一份」**。转录与后处理跑在 Fly 任务镜像上，本进程跑在 Render
派单前台上；push 会重部署 Render、**不会重建 Fly 镜像**，两边因此可能不是同一份——这正是
要显示指纹的原因。这里能给的是「本进程看到的文件指纹」+「当前配置的 Fly 镜像 tag」，
**它们对不对得上本模块无从判断**（真正的核对要等节点埋点把跑的那一份指纹报回来）。
所以 `promptScope` 字段明写 dispatcher，别让人把它当成线上那一份的证明。
"""
import ast
import hashlib
import os
import re
from pathlib import Path

from . import config

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
VENDOR = PIPELINE / "vendor"
SKILLS = VENDOR / ".claude" / "skills"
LANG_DIR = VENDOR / "Config" / "languages"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _prompt_entry(rel_path: str, text: str | None = None) -> dict | None:
    """一份提示词/规则书的名片：路径 + 行数 + 内容指纹。读不到返回 None（不编造）。"""
    if text is None:
        try:
            text = (VENDOR / rel_path).read_text(encoding="utf-8")
        except OSError:
            return None
    return {"path": rel_path, "lines": text.count("\n") + 1, "sha": _digest(text)}


def _prompt_text(rel_path: str) -> str | None:
    try:
        return (VENDOR / rel_path).read_text(encoding="utf-8")
    except OSError:
        return None


# 提示词/规则书清单：id → vendor 相对路径。id 同时是取全文的入参。
SKILL_FILES = {
    "multi-asr-merge": ".claude/skills/multi-asr-merge/SKILL.md",
    "pp-narrate": ".claude/skills/pp-narrate/SKILL.md",
    "pp-redact": ".claude/skills/pp-redact/SKILL.md",
    "shared-qc": ".claude/skills/_shared/共性质检.md",
}
# 术语库助手的提示词**不是 skill 文件，是代码常量**——而那段文字就是这个功能的产品定义
# （收多少条、什么算术语全在里面），改它等于改功能。所以它和 skill 一样要能看全文、带指纹。
GLOSSARY_PROMPT_ID = "glossary-assist"


def _glossary_prompt_text() -> str | None:
    try:
        from . import glossary_assist
        return glossary_assist._RULES
    except Exception:  # noqa: BLE001  取不到就不显示这一条，别让整个接口塌掉
        return None


def prompt_full(pid: str) -> dict | None:
    """取一份提示词全文（只读）。未知 id / 读不到 → None，调用方转 404。"""
    if pid == GLOSSARY_PROMPT_ID:
        text = _glossary_prompt_text()
        if text is None:
            return None
        return {"id": pid, "path": "app/glossary_assist.py · _RULES",
                "sha": _digest(text), "text": text}
    rel = SKILL_FILES.get(pid)
    if not rel:
        return None
    text = _prompt_text(rel)
    if text is None:
        return None
    return {"id": pid, "path": f"pipeline/vendor/{rel}", "sha": _digest(text), "text": text}


def _prompts() -> dict:
    out = {}
    for pid, rel in SKILL_FILES.items():
        e = _prompt_entry(rel)
        if e:
            out[pid] = e
    gt = _glossary_prompt_text()
    if gt is not None:
        out[GLOSSARY_PROMPT_ID] = {"path": "app/glossary_assist.py · _RULES",
                                   "lines": gt.count("\n") + 1, "sha": _digest(gt)}
    return out


# ── vendored 脚本里的常量：读源文件，不誊写 ────────────────────────────────────
# P0/P2 是纯本地脚本，import 它们会连带跑模块级初始化（argparse / 路径推断），在 api 进程里
# 不值得冒这个险。用 literal_eval 读赋值右侧：读得到就是真值，读不到就整条不显示。
_ASSIGN = r"^{name}\s*=\s*([^#\n]+?)\s*(?:#.*)?$"


def _transcribe_cap() -> int:
    """实际生效的转录任务数上限：运营舱优先，读不到回落 env（同派单侧口径）。"""
    try:
        from . import p3_config
        return p3_config.effective_transcribe_jobs()
    except Exception:  # noqa: BLE001  看板读不到配置不该整块塌掉
        return config.MAX_TRANSCRIBE_JOBS


def _consts(path: Path, names: list[str]) -> dict:
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for n in names:
        m = re.search(_ASSIGN.format(name=re.escape(n)), src, re.M)
        if not m:
            continue
        try:
            v = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError):
            continue
        out[n] = sorted(v) if isinstance(v, (set, frozenset)) else v
    return out


def _lang_plans() -> list[dict]:
    """27 门语种的引擎编排（只读）：主轨 + 参考轨有序表 + 规范化规则。
    直接读 yaml，不经 Phase1_driver（那要 import 整个驱动）。读不到的语种跳过。"""
    try:
        import yaml
        from pipeline.orchestrator import ADAPTER_TAG
    except Exception:  # noqa: BLE001
        return []
    plans = []
    for p in sorted(LANG_DIR.glob("*.yaml")):
        if p.stem.startswith("_"):
            continue
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        primary = (d.get("primary") or {}).get("adapter")
        if not primary:
            continue
        plans.append({
            "lang": d.get("lang") or p.stem,
            "primary": ADAPTER_TAG.get(primary, primary.upper()),
            "refs": [ADAPTER_TAG.get(r.get("adapter"), str(r.get("adapter")).upper())
                     for r in (d.get("refs") or []) if r.get("adapter")],
            "normalize": d.get("normalize"),
        })
    return plans


def _image_tag() -> str | None:
    """Fly 任务镜像的 tag（`registry.fly.io/app:tag` 的冒号后半段）。未配置返回 None。"""
    ref = config.FLY_TASK_IMAGE or ""
    return ref.rsplit(":", 1)[-1] if ":" in ref else (ref or None)


def snapshot() -> dict:
    """工作流 Tag 要的全部事实。任何一块取不到就缺那一块，其余照给——
    这是运营页的只读视图，不该因为读不到一个常量就整页空白。"""
    from pipeline import merge, orchestrator, pp_deepseek, pp_redact_ds, skill_merge

    from . import glossary_assist

    p3_timeout = int(os.environ.get("P3_TIMEOUT_SEC", "3600"))
    return {
        "promptScope": "dispatcher",   # 见模块头：指纹是派单前台这一份，不是 Fly 上跑的那一份
        "imageTag": _image_tag(),
        "prompts": _prompts(),
        "langPlans": _lang_plans(),
        "params": {
            "glossary": {
                "model": glossary_assist.MODEL,
                "timeoutSec": glossary_assist.TIMEOUT_SEC,
                "maxEntries": glossary_assist.MAX_ENTRIES,
                "meaningMax": glossary_assist.MEANING_MAX,
                "totalMax": glossary_assist.TOTAL_MAX,
                "outlineMax": glossary_assist.MAX_OUTLINE_CHARS,
            },
            "p0": _consts(VENDOR / "Workflow" / "core" / "Phase0_Audio-Convert.py",
                          ["TARGET_DURATION_MIN", "SEARCH_RANGE_MINUTES", "MIN_LAST_SEGMENT_MIN",
                           "SILENCE_THRESH_DB", "SILENCE_MIN_DUR", "SAMPLE_RATE", "CHANNELS",
                           "AUDIO_FORMAT"]),
            "p1": {
                "pollSec": orchestrator.P1_POLL_SEC,
                # 2026-08-18 起参考轨不再按场景截断（原 SCENE_TOPN / DEFAULT_TOPN 已删）：
                # profile 里配几条就跑几条。**这里不再放第二份条数**——每门语言实际跑哪几条
                # 已经在同一份响应的 `langPlans[].refs` 里，那才是唯一事实源，抄第二份必漂移。
                # 主轨（ELV）2026-08-25 起整档直传、我们不切片——ELV_PARALLEL 已作废。
                # 这里出的是**实际生效的事实**：ELV 内部按官方规则并行，每片占我方一个账号
                # 并发名额；我们能控的只有「同时派几单转录」。
                "elvSliceThresholdSec": 480,
                "elvMaxSlices": 4,
                "elvConcurrency": config.ELV_MAX_CONCURRENCY,
                "xfConcurrency": config.XF_MAX_CONCURRENCY,
                # ⚠️ 2026-08-26 起这个数由运营舱管（p3_config），env 只是回落缺省。
                # 直接读 config 的话，运营舱改完这里还显示旧值——「参数一个都不许在前端写死」
                # 那条纪律防的是同一件事：显示的必须是**实际在跑的**那个数。
                "maxTranscribeJobs": _transcribe_cap(),
                "geminiParallel": int(os.environ.get("GEMINI_PARALLEL", "15")),
                "rateCnyPerMin": orchestrator.RATE_CNY_PER_MIN,
                "maxDurationSec": config.MAX_DURATION_SEC,
            },
            "p2": _consts(VENDOR / "Workflow" / "core" / "Phase2_Align_Match.py",
                          ["MAX_CANDIDATES", "MAX_CONSECUTIVE_FILLER_SKIPS", "NO_SPACE_LANGS"]),
            "p3": {
                "model": skill_merge.CLAUDE_MODEL,
                "effort": skill_merge.CLAUDE_EFFORT,
                "allowedTools": skill_merge.ALLOWED_TOOLS,
                "script": merge.P3_SCRIPT,
                "args": merge.P3_ARGS,
                "timeoutSec": p3_timeout,
            },
            "pp": {
                "concurrency": config.PP_CLAUDE_MAX_CONCURRENCY,
                "timeoutSec": p3_timeout,          # 后处理复用 P3 的超时窗，不是另一个值
                "retryDelayMin": config.PP_RETRY_DELAY_MIN,
                "listMaxChars": config.PP_LIST_MAX_CHARS,
            },
            "ppFlash": {
                "model": pp_deepseek.MODEL,
                "effort": pp_deepseek.EFFORT,
                "narrateBudget": pp_deepseek.STEP1_BUDGET,
                "narrateConc": pp_deepseek.CONC,
                "dropWarn": pp_deepseek.DROP_WARN,
                "dropFatal": pp_deepseek.DROP_FATAL,
                "redactBudget": pp_redact_ds.BUDGET,
                "redactConc": pp_redact_ds.CONC,
                "redactPasses": pp_redact_ds.PASSES,
                "redactReview": pp_redact_ds.REVIEW,
            },
            "job": {"queuedMaxHours": config.QUEUED_MAX_HOURS},
        },
    }

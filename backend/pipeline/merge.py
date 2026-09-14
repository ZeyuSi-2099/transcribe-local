"""P3 融合：子进程调 vendored Phase3_Merge_DeepSeek.py（直连 DeepSeek API，按量计费）。

对齐 ASR-Optimizer/批处理_中文.sh 的 deepseek 分支：
    python3 Workflow/Phase3_Merge_DeepSeek.py <P2_Match.md>
脚本以自身位置定位 PROJECT_ROOT(=vendor/)，读 .claude/skills/multi-asr-merge/SKILL.md
作 system、按用户注入术语库（env USER_TERM_FILE），把产物写到 vendor/Output/：
  <base>_P3_Merge_DS_<ts>.md（终稿，合并+精修一步成）+ <base>_P3_Merge_DS_<ts>_report.md（报告）。
  （DS = DeepSeek 路；Claude/skill 路产物缀 _OPUS_，见 skill_merge.py）
凭证 DEEPSEEK_API_KEY / BOCHA_API_KEY 走环境变量（容器经 env_file 注入）。
旧版 `claude -p` 吃订阅的调用已废弃——容器里读不到钥匙串的认证难题随之消失。
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import p3_lang

# P3·DeepSeek 走**分批版**（2026-08-10 定档）：全文常驻 + 每轮只出一小段。
# 单次版一次吐完整篇 → 输出长度压力下明显退化（冲突段跟多数从 39% 掉到 4.6%），长音频更是必挂。
# 参数是实测定的，改前先看 Output/_调优台账/改动台账.md：
#   --round-tokens 2100  每轮正文预算，约 30 轮，边界只落在段落之间（绝不拆段）
#   --conc 40            单任务内并发轮数。2026-08-13 从 50 降到 40：常态是 Flash 兜底
#                        （Pro 已停用），按 Flash 账号并发 2500 的八成算 → 50 台 × 40 = 2000。
#                        **改这个数必须同改 app/p3_config.PER_MACHINE_CONC**，否则那边算出的余量是假的。
#   --conflicts          由代码算好「主轨 vs 参考轨多数」的分歧点逐轮点名（数票不该交给模型）
#   --overlap 1          每轮向前多要 1 段：丢段全发生在轮尾、轮首零丢失，重叠把它挪出危险位置
# 单次版留在 vendor 里作对照与回滚（把下面两行换成 "Workflow/Phase3_Merge_DeepSeek.py" 与 []）。
P3_SCRIPT = "Workflow/Phase3_Merge_DeepSeek_Batched.py"
P3_ARGS = ["--round-tokens", "2100", "--conc", "40", "--conflicts", "--overlap", "1"]


def _parse_p3_cost_usd(stdout: str) -> float:
    """从 P3 子进程 stdout 解析 DeepSeek 用量成本（USD）：`__P3_DS_USAGE__ ... cost=<USD>`。
    解析不到返回 0（不编造）。折人民币在上层 ×7（见 Config_批处理终端显示）。"""
    m = re.search(r"__P3_DS_USAGE__.*?cost=([\d.]+)", stdout or "")
    return float(m.group(1)) if m else 0.0


# 用量埋点（2026-08-31）。**逐字段单独搜，不写一条把全部字段串起来的长正则**：
# 长正则要求字段顺序与个数完全吻合，而单次版与分批版是两个脚本、字段是后补的——
# 顺序哪天不一致，整行就一个数都解析不出，而症状是「成本面板忽然全空」，不报错。
_USAGE_FIELDS = ("hit", "miss", "out")


def parse_p3_usage(stdout: str) -> dict:
    """解析 `__P3_DS_USAGE__` 行的 token 明细，返回 {"hit","miss","out"}（缺哪个就没哪个键）。

    **为什么要它**：成本本身 cost= 已经有了，但只有金额说明不了「为什么是这个数」。
    DeepSeek 2026-08-17 起缓存命中与未命中差 20–30 倍单价（ds_pricing.PRICE_CNY），
    命中率才是 P3 成本的主变量——分批版「全文常驻、每轮只出一小段」这套设计，
    赌的正是命中率高。**赌没赌赢，此前没有任何地方看得出来。**

    解析不到一律返回空 dict，不编造也不补零：「没这个数」和「这个数是 0」是两回事。"""
    line = re.search(r"__P3_DS_USAGE__[^\n]*", stdout or "")
    if not line:
        return {}
    out = {}
    for f in _USAGE_FIELDS:
        m = re.search(rf"\b{f}=(\d+)\b", line.group(0))
        if m:
            out[f] = int(m.group(1))
    return out


# 本进程最近一次 DeepSeek P3 的 token 明细。**必须由调用方在开跑前 reset_usage()**——
# 同 pp_deepseek 的做法。Claude 路根本不会进 run_merge，若不清零，走 Claude 的那一单
# 会读到**上一单 DeepSeek** 的数字（本地回滚态一个进程串跑多单时就会发生）。
# 这正是 P1LLM_STATUS_FILE 那个坑的形状：一份进程级状态，两个主人。
_LAST_USAGE: dict = {}


def reset_usage() -> None:
    _LAST_USAGE.clear()


def last_usage() -> dict:
    return dict(_LAST_USAGE)


def find_latest_output(output_dir: Path, marker: str) -> str:
    """返回 output_dir 下含 marker 的最新 .md（排除 _report.md / _raw.md）。"""
    cands = [
        p for p in output_dir.glob(f"*{marker}*.md")
        if not p.name.endswith(("_report.md", "_raw.md"))
    ]
    if not cands:
        raise FileNotFoundError(f"{output_dir} 下找不到 *{marker}*.md")
    return str(max(cands, key=lambda p: p.stat().st_mtime))


def run_merge(match_file: str, vendor_root: Path, glossary_text: str = "",
              model: str = "", ui_lang: str | None = None) -> tuple[str, float]:
    """P3·DeepSeek 多路融合（合并+精修一步成终稿），返回 (_P3_Merge_*.md 路径, DeepSeek 成本 USD)。

    glossary_text：该任务所属用户的术语库全文。非空 → 写临时文件、经 env USER_TERM_FILE
    交给 P3 子进程内联进提示词；空 → 不注入（P3 不带任何术语库；写死的华为库已解耦）。

    model：三档阶梯选中的 DeepSeek 模型（pro / flash）。空 = 沿用子进程自己的 P3_DS_MODEL
    缺省，保持老调用方（单档时代）行为不变。
    """
    env = dict(os.environ)
    if model:
        env["P3_DS_MODEL"] = model
    # 语言指令经 env 交给子进程（同 USER_TERM_FILE 的做法）。**不能让 vendor 脚本自己
    # import pipeline.p3_lang**：它是以 vendor/ 为工作目录、用 `python3 Workflow/xxx.py`
    # 起的独立进程，根本看不见我们的包。指令文本在这里生成，那边只负责原样拼进 system。
    env["P3_LANG_DIRECTIVE"] = p3_lang.directive(ui_lang)
    term_path = None
    if glossary_text and glossary_text.strip():
        fd, term_path = tempfile.mkstemp(prefix="userterm_", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(glossary_text)
        env["USER_TERM_FILE"] = term_path
    cmd = [sys.executable, P3_SCRIPT, str(match_file), *P3_ARGS]
    try:
        # 捕获 stdout 以解析成本标记（P3 是子进程，抓 stdout 干净）；同时透传到 worker 日志。
        # getattr 容错：真 CompletedProcess 有 stdout/stderr，测试桩可能没有。
        # 必须带 timeout：DeepSeek 路是最后兜底、无处再降级，网络读挂起时 P3 心跳仍在喂
        # 看门狗（updated_at 永远新鲜），超时抛错走正常失败链（判失败+退预扣）是唯一回收手段。
        timeout_sec = int(os.environ.get("P3_TIMEOUT_SEC", "3600"))
        try:
            proc = subprocess.run(cmd, cwd=str(vendor_root), env=env, capture_output=True,
                                  text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"P3·DeepSeek 融合超时（>{timeout_sec}s），已杀子进程") from e
        out = getattr(proc, "stdout", "") or ""
        if out:
            print(out, end="", flush=True)
        if proc.returncode != 0:
            raise RuntimeError(f"P3·DeepSeek 融合失败，退出码 {proc.returncode}\n{(getattr(proc, 'stderr', '') or '')[-500:]}")
    finally:
        if term_path:  # 用完即删（不留用户术语到 worker 临时盘）
            try:
                os.unlink(term_path)
            except OSError:
                pass
    _LAST_USAGE.update(parse_p3_usage(out))
    return find_latest_output(Path(vendor_root) / "Output", "_P3_Merge_"), _parse_p3_cost_usd(out)

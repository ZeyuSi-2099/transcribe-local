# -*- coding: utf-8 -*-
"""Phase 3：融合定字。与 SaaS 生产线保持一致（Duner 2026-09-11 定），两条路：

  ① openai   —— OpenAI 协议（DeepSeek / 本机 Ollama / LM Studio 都说这个协议）。
                 结构照搬生产线的 Phase3_Merge_DeepSeek_Batched.py：
                   阶段 1 · 全局定字：整篇喂一次，只出「实体定字表」（顺带把 prompt cache 建好）；
                   阶段 2 · 分批出稿：按 token 预算切轮、边界只落在块之间、每轮向前多带一块（丢段全在轮尾）、
                             各轮并发，每轮 = 整篇 + 定字表 + 「只输出第 X–Y 块」+ 本批分歧册；
                   阶段 3 · 缺口补漏：仍缺的块连同前后各 3 块单独再要一次。
                 定不下的专名可走博查联网（web_search 工具循环），与生产线同一个端点、同一套退避。
  ② claude_p —— 生产线的订阅路：`claude -p /multi-asr-merge …`，Opus · medium，整篇一次，
                 命令与 SaaS 的 build_claude_cmd 一字不差。只在测试 / 对比期用，不进默认配置。

提示词由上游同步脚本生成（_merge_zh.py）：PROMPT_SAAS 与生产版只差「没有主轨、说话人来源、块恒等」三处；
PROMPT 是离线版（不联网、不出报告），给本机模型 / 无网环境。

密钥只从环境变量读。⛔ 永不写进配置文件、永不进日志。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .divergence import Divergence
from .transcript import MARK, has_words, parse_line

# ── 字 → token：生产线 2026-08-09 标定（system 6 562 字 + 正文 148 089 字 → 68 864 prompt token）。
#    只用来划分轮次边界，偏一点不影响正确性。
TOK_PER_CHAR = 68864 / 154651

# ── DeepSeek V4.1 Flash 价目（美元 / 百万 token，非高峰；高峰 ×2）。只用来估算，不用来结算。
PRICE = {"miss": 0.15, "hit": 0.003, "out": 0.60}

PHASE1_USER = """以上是同一段录音的多路 ASR 转写（格式同系统提示所述）。

【本次只做第一步 · 覆盖系统提示〈输出〉〈执行步骤〉里「写文件/出终稿」那几步】
**不要输出融合终稿**，只通读全文后按系统提示〈输出〉节的骨架输出**前两个章节**，供后续分批
出稿时统一口径用（同一个词只列一次——避免全文前后写法不一正是它要解决的问题）：

## 实体定字（硬证据）
表格列固定 `时间码 | 各轨候选 | 终稿 | 依据`。逐条列出全文里需要定字的命名实体、产品名、
机构名、专有术语与关键数字；时间码填该词首次出现处。命名实体先查内联术语库{web_hint}。
依据写法遵循系统提示的「克制」示例。

## 联网核实
首行报「共 N 次搜索」；N>0 时表格列固定 `# | 搜索词 | 为定哪个词（时间码）| 结果 | 建议入库?`。

这两节之外不要输出任何别的内容（不要终稿、不要存疑节、不要复查节）。
"""

PHASE2_USER = """以上是同一段录音的多路 ASR 转写（格式同系统提示所述）。请严格按系统提示的
融合原则逐块融合定字、出稿前复查、轻度润色。

【全局定字表 · 已在前一步对全文通读后定好，本批必须沿用，不得另定一套】
{table}

【本次输出范围 · 覆盖系统提示〈输出〉〈执行步骤〉里「写文件/取时间戳/出报告」那几步】
全文都给你了是为了让你看得到上下文，但**本批只输出第 {i0} 块到第 {i1} 块**（即时间戳 {t0} 起、
到 {t1} 止，共 {n} 块）这一段区间的融合终稿，**不要输出这个区间以外的任何块**。
直接输出，不要写文件、不要取时间戳、不要加前言或总结，第一行就是本批第一块。
每块格式：`[起始时间码 - 结束时间码] M/R: 文本`——**时间区间原样照抄输入顶格行的那一对**
（形如 `[08:56.3 - 09:04.6]`，两端都要，不许只写起始时间码），**不带缩进的其余各路行**。
输入里有几块就输出几块，不要把相邻块合并成一块。**每块都必须有文字**，`[❓]` 只标在定不下来的
那几个字旁边，不能拿它替换整块。
{ledger}
终稿之后，**无论本批有没有出现 `[❓]`，都必须**用下面这行围栏（单独成行、原样照抄）收尾：
===DOUBT===
围栏之后：本批凡是打了 `[❓]` 的地方，逐条全列，表格列固定 `时间码 | 终稿写法 | 原因`；
**本批一个 `[❓]` 都没有，就在围栏之后只写「无」这一个字**——不许省略围栏本身。
"""

LEDGER_HINT = """
【本批的分歧册 · 由程序逐块穷举比对算出，不是建议答案】
下列位置各路写法不一致。**每一处都要真判一次**，不许默认采用顶格那一路：按系统提示的融合原则走
（先找硬证据 → 没有则看票数 → 仍定不下标 `[❓]`）。判完采用哪一版都可以，但不能跳过。

{rows}
"""

TOOLS = [{"type": "function", "function": {
    "name": "web_search",
    "description": "联网搜索（博查·中文网页强）。核实命名实体（品牌/产品/机构/人名/地名/术语/缩写）是否存在。",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索词"}},
                   "required": ["query"]}}}]
BOCHA_URL = "https://api.bochaai.com/v1"


# ═══════════════════════════ 公共：提示词、块、分歧册 ═══════════════════════════

def _terms_text(files: list[str]) -> str:
    chunks = []
    for f in files:
        p = Path(f).expanduser()
        if p.exists():
            chunks.append(f"### {p.name}\n{p.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(chunks)


def system_prompt(cfg: dict, online: bool) -> str:
    """默认用内置的；配了 p3.prompt 就用你自己的，但会喊一声。术语库按 SaaS 的方式内联在末尾。"""
    from ._merge_zh import PROMPT, PROMPT_SAAS
    custom = (cfg["p3"].get("prompt") or "").strip()
    if custom:
        prompt = Path(custom).expanduser().read_text(encoding="utf-8").strip()
        print(f"ℹ️ 用的是你自己的提示词（{custom}），不是内置那份 —— 成绩不再与我们发布的数字可比。")
    else:
        prompt = (PROMPT_SAAS if online else PROMPT).strip()
    terms = _terms_text(cfg["terms"].get("files") or []) if cfg["terms"].get("inject_p3", True) else ""
    if terms:
        prompt += ("\n\n---\n\n## 术语库内容（用户术语库 · 已为你内联）\n"
                   "> skill 里说的「先查本次任务的术语库」就查下面这份；**勿尝试 Read 文件**，直接用此处内容。\n\n"
                   + terms)
    return prompt


def _ts(x: float) -> str:
    return f"{int(x) // 60:02d}:{x - (int(x) // 60) * 60:04.1f}"


def _secs(t: str) -> float:
    mm, ss = t.split(":")
    return int(mm) * 60 + float(ss)


def key_of(line: str) -> str | None:
    """产出行 → 块的唯一键 = 「起 - 止」整个区间（只用起点会把共起点的两块弄丢，生产线踩过）。"""
    ln = parse_line(line)
    return f"{ln.a}-{ln.b}" if ln else None


def split_blocks(p3_input: str) -> list[dict]:
    """P3 输入 → [{k, sec, text}]；每块以顶格行起头，其下是缩进的其余各路。"""
    out = []
    for raw in p3_input.split("\n\n"):
        if not raw.strip():
            continue
        top = parse_line(raw.splitlines()[0])
        if not top:
            continue
        out.append({"k": f"{top.a}-{top.b}", "sec": _secs(top.a), "text": raw.strip()})
    return out


def plan_rounds(blocks: list[dict], round_tokens: int, min_last: int = 1000) -> list[tuple[int, int, int]]:
    """按「每轮多少 token」切轮次，边界一律落在块之间；末轮太小就并进倒数第二轮（生产线 2026-08-10 的教训：
    末轮膨胀会稀释注意力、字写糙，末轮零头又白付一次通读）。返回 [(轮号, lo, hi)]。"""
    sizes = [len(b["text"]) * TOK_PER_CHAR for b in blocks]
    spans, lo, acc = [], 0, 0.0
    for i, s in enumerate(sizes):
        if acc and acc + s > round_tokens:
            spans.append((lo, i))
            lo, acc = i, 0.0
        acc += s
    spans.append((lo, len(blocks)))
    if len(spans) > 1 and sum(sizes[spans[-1][0]:spans[-1][1]]) < min_last:
        spans[-2:] = [(spans[-2][0], spans[-1][1])]
    return [(i, a, b) for i, (a, b) in enumerate(spans)]


def ledger_rows(found: list[Divergence], first: int, last: int) -> str:
    segs = []
    for d in found:
        if not (first <= d.block <= last) or not d.substantive:
            continue
        lines = [f"#{d.block} [{_ts(d.at)}] {d.role}"]
        lines += ["  · " + " ｜ ".join(f"{t}={v or '∅'}" for t, v in grp) for grp in d.substantive]
        if d.filler_count:
            lines.append(f"  （语气词差异 {d.filler_count} 处，可略过）")
        segs.append("\n".join(lines))
    return "\n\n".join(segs)


def parse_output(text: str) -> tuple[dict[str, str], list[str]]:
    """一批产出 → ({块键: 行}, 存疑表数据行)。围栏之前只收看起来像终稿的行，宁可少收也不让脏数据进稿。"""
    body, doubt = (text.strip().split("===DOUBT===", 1) + [""])[:2]
    got: dict[str, str] = {}
    for ln in body.splitlines():
        k = key_of(ln)
        if k:
            got.setdefault(k, ln.strip())
    drows = [ln.strip() for ln in doubt.splitlines()
             if ln.strip().startswith("|") and "终稿写法" not in ln
             and not all(set(c.strip()) <= set("-:") for c in ln.strip().strip("|").split("|"))]
    return got, drows


def _blank(line: str) -> bool:
    ln = parse_line(line)
    return not has_words(ln.text if ln else line)


def _revive(block: str) -> tuple[str, bool]:
    """把写空了的那一块按各路票数最多的原文填回去，并标 [❓]。四路都空时行仍保留（只有时间码 + [❓]）。
    提示词是请求、代码才是保证 —— 「内容不能丢」是红线。"""
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return "", False
    top = parse_line(lines[0])
    prefix, main = (top.head, top.text) if top else ("", lines[0])
    cands = [main] + [re.sub(r"^\[[A-Z0-9]+\]\s*", "", ln) for ln in lines[1:]]
    cands = [c.strip() for c in cands if c.strip() and c.strip() != "/"]
    if not cands:
        return f"{prefix}{MARK}", False
    best = max(cands, key=lambda c: (cands.count(c), c == main))
    return f"{prefix}{best} {MARK}", True


def _finish(blocks: list[dict], owned: dict, spare: dict) -> tuple[list[str], list[int]]:
    """按块序归并：本轮负责的优先，缺的从邻轮重叠里补；仍缺的返回下标。"""
    lines, miss = [], []
    for i, b in enumerate(blocks):
        if b["k"] in owned:
            lines.append(owned[b["k"]])
        elif b["k"] in spare:
            lines.append(spare[b["k"]])
        else:
            lines.append(None)
            miss.append(i)
    return lines, miss


# ═══════════════════════════ ① OpenAI 协议 · 两阶段分批 ═══════════════════════════

class _Usage:
    def __init__(self):
        self.hit = self.miss = self.out = self.reasoning = self.calls = self.searches = 0

    def add(self, u: dict):
        hit, miss = u.get("prompt_cache_hit_tokens"), u.get("prompt_cache_miss_tokens")
        if hit is None and miss is None:
            miss, hit = u.get("prompt_tokens", 0), 0
        self.hit += hit or 0
        self.miss += miss or 0
        self.out += u.get("completion_tokens", 0) or 0
        self.reasoning += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        self.calls += 1

    def cost(self) -> float:
        return (self.miss * PRICE["miss"] + self.hit * PRICE["hit"] + self.out * PRICE["out"]) / 1e6

    def line(self) -> str:
        tot = max(1, self.hit + self.miss)
        return (f"调用 {self.calls} 次 · 搜索 {self.searches} 次 · 输入 {self.hit + self.miss:,} tok"
                f"（缓存命中 {self.hit / tot:.0%}）· 输出 {self.out:,} tok（其中推理 {self.reasoning:,}）"
                f"· 估算 ${self.cost():.3f}（非高峰价；高峰约 ×2）")


def _bocha(query: str, count: int = 5) -> str:
    """博查 ai-search，失败退回 web-search；429 必须重试，不能把「搜索失败」原样交给模型
    （它会当成「查无此实体」，照抄错字）。搬自生产线 Phase3_Merge_DeepSeek.py。"""
    key = os.environ.get("BOCHA_API_KEY", "")
    if not key:
        return "(本机没有配置联网搜索密钥，按无结果处理)"
    hdr = {"Authorization": "Bearer " + key}

    def post(path, body, timeout):
        for i in range(4):
            r = httpx.post(f"{BOCHA_URL}/{path}", json=body, headers=hdr, timeout=timeout)
            if r.status_code == 429 and i < 3:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()

    try:
        r = post("ai-search", {"query": query, "count": count, "answer": True}, 45)
        out, pages = [], []
        for m in r.get("messages") or []:
            c, t = m.get("content") or "", m.get("content_type")
            if t == "text" and not c.lstrip().startswith("["):
                out.append("综述：" + c[:600])
            elif t == "webpage":
                try:
                    pages += json.loads(c).get("value", [])
                except json.JSONDecodeError:
                    pass
        out += [f"- {p.get('name', '')}: {(p.get('summary') or p.get('snippet') or '')[:280]} ({p.get('url', '')})"
                for p in pages[:count]]
        if out:
            return "\n".join(out)
    except Exception:
        pass
    try:
        r = post("web-search", {"query": query, "count": count, "summary": True}, 30)
        pages = (((r.get("data") or {}).get("webPages") or {}).get("value")) or []
        lines = [f"- {p.get('name', '')}: {(p.get('summary') or p.get('snippet') or '')[:280]} ({p.get('url', '')})"
                 for p in pages[:count]]
        return "\n".join(lines) if lines else "(无结果)"
    except Exception as e:
        return f"(搜索失败: {type(e).__name__})"


def _call(cfg: dict, sysmsg: str, user: str, usage: _Usage, online: bool, max_turns: int = 12) -> tuple[str, str]:
    """一次融合调用（含工具循环）。返回 (文本, finish_reason)。网络抖动 / 5xx / 429 指数退避。"""
    p3 = cfg["p3"]
    key = os.environ.get(p3.get("api_key_env", ""), "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = p3["base_url"].rstrip("/") + "/chat/completions"
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]
    finish, out = None, ""
    for _turn in range(max_turns):
        payload = {"model": p3["model"], "temperature": float(p3.get("temperature", 0.0)),
                   "max_tokens": int(p3.get("max_tokens", 64000)), "stream": False, "messages": msgs}
        if online:
            payload["tools"] = TOOLS
        payload.update(p3.get("extra") or {})
        last: Exception | None = None
        for i in range(int(p3.get("max_retry", 2)) + 1):
            try:
                r = httpx.post(url, json=payload, headers=headers, timeout=float(p3.get("timeout", 1200)))
                if r.status_code in (429, 500, 502, 503, 529) and i < int(p3.get("max_retry", 2)):
                    time.sleep(8 * (i + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                last = e
                time.sleep(8 * (i + 1))
        else:
            raise RuntimeError(f"Phase 3 调用失败（{p3['base_url']} · {p3['model']}）：{last}")
        ch = data["choices"][0]
        msg = ch["message"]
        usage.add(data.get("usage", {}))
        finish = ch.get("finish_reason")
        if finish == "length":
            raise RuntimeError(f"这一批被 max_tokens({payload['max_tokens']}) 截断了，后半截没生成。"
                               f"不接受残缺稿 —— 把 p3.max_tokens 调大，或把 p3.round_tokens 调小再跑。")
        msgs.append(msg)
        tcs = msg.get("tool_calls")
        if not tcs:
            out = msg.get("content", "") or ""
            break
        for tc in tcs:
            args = (tc.get("function") or {}).get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except Exception:
                    args = {}
            q = ((args or {}).get("query") or "").strip()
            if not q:
                res = "(web_search 调用未带 query 参数 —— 请把要搜的词放进 query 字段后重新调用)"
            else:
                res = _bocha(q)
                usage.searches += 1
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
    return out, finish or ""


def _fuse_openai(p3_input: str, found: list[Divergence], cfg: dict, on_batch=None) -> str:
    p3 = cfg["p3"]
    online = bool(p3.get("web_search", False)) and bool(os.environ.get("BOCHA_API_KEY"))
    if p3.get("web_search", False) and not online:
        print("ℹ️ p3.web_search 开着，但环境变量 BOCHA_API_KEY 没设 —— 这次不联网，定不下的专名会标 [❓]。")
    sysmsg = system_prompt(cfg, online)
    blocks = split_blocks(p3_input)
    if not blocks:
        raise RuntimeError("Phase 3 输入里一块都解析不出来")
    usage = _Usage()
    t0 = time.time()

    # ① 全局定字（正文放最前，指令放最后：DeepSeek 的缓存按前缀匹配，这样阶段 2 全部命中）
    hint = "，库中无果再 web_search 联网核实" if online else "；库中无果、上下文也定不下的直接标 `[❓]`，不联网"
    table, _ = _call(cfg, sysmsg, p3_input + "\n\n" + PHASE1_USER.format(web_hint=hint), usage, online)
    table = table.strip()
    print(f"P3  ① 全局定字表 {len(table)} 字 · {time.time() - t0:.0f}s")

    # ② 分批并发出稿
    spans = plan_rounds(blocks, int(p3.get("round_tokens", 2100)))
    overlap = int(p3.get("overlap", 1))
    conc = max(1, int(p3.get("concurrency", 4)))
    done = [0]

    def run_span(span):
        idx, lo, hi = span
        lo_ext = max(0, lo - overlap)
        sel = blocks[lo_ext:hi]
        rows = ledger_rows(found, lo_ext + 1, hi)
        user = p3_input + "\n\n" + PHASE2_USER.format(
            table=table, i0=lo_ext + 1, i1=hi, t0=_ts(sel[0]["sec"]), t1=_ts(sel[-1]["sec"]), n=len(sel),
            ledger=LEDGER_HINT.format(rows=rows) if rows else "")
        text, _ = _call(cfg, sysmsg, user, usage, online)
        got, drows = parse_output(text)
        own = {b["k"] for b in blocks[lo:hi]}
        if "===DOUBT===" not in text and "❓" in text:
            print(f"    ⚠️ 轮 {idx:02d} 正文有 [❓] 却没给存疑围栏，这一批的原因说明会缺")
        done[0] += 1
        if on_batch:
            on_batch(done[0], len(spans))
        return idx, got, own, drows

    if len(spans) > 1:                                    # 第一轮先串行把缓存焐热，其余并发
        results = [run_span(spans[0])]
        with ThreadPoolExecutor(max_workers=conc) as ex:
            results += list(ex.map(run_span, spans[1:]))
    else:
        results = [run_span(spans[0])]

    owned, spare, doubts = {}, {}, []
    for _, got, own, drows in sorted(results, key=lambda r: r[0]):
        for k, line in got.items():
            (owned if k in own else spare).setdefault(k, line)
        doubts += drows
    lines, miss = _finish(blocks, owned, spare)
    rescued = sum(1 for b in blocks if b["k"] in spare and b["k"] not in owned)

    # ③ 缺口补漏：缺的块连同前后各 3 块单独再要一次（整轮重跑是最亏的做法，生产线实测）
    if miss:
        groups, cur = [], [miss[0]]
        for i in miss[1:]:
            if i - cur[-1] <= 3:
                cur.append(i)
            else:
                groups.append(cur)
                cur = [i]
        groups.append(cur)
        print(f"P3  ③ 缺口补漏 {len(miss)} 块 / {len(groups)} 处")
        for g in groups:
            lo2, hi2 = max(0, g[0] - 3), min(len(blocks), g[-1] + 4)
            sel = blocks[lo2:hi2]
            user = p3_input + "\n\n" + PHASE2_USER.format(
                table=table, i0=lo2 + 1, i1=hi2, t0=_ts(sel[0]["sec"]), t1=_ts(sel[-1]["sec"]), n=len(sel),
                ledger="")
            text, _ = _call(cfg, sysmsg, user, usage, online)
            got, drows = parse_output(text)
            for k, line in got.items():
                spare.setdefault(k, line)
            doubts += drows
        lines, miss = _finish(blocks, owned, spare)

    # 仍缺的块 → 按各路票数最多的原文填回并标 [❓]；写成空块的同样处理（红线：内容不能丢）
    n_fill = 0
    for i, b in enumerate(blocks):
        if lines[i] is None or _blank(lines[i]):
            fixed, had = _revive(b["text"])
            lines[i] = fixed or f"[{_ts(b['sec'])} - ?] X: {MARK}"
            n_fill += 1
            why = "模型没输出这一块" if lines[i] is None else "被写成了空块"
            print(f"    ⚠️ 第 {i + 1} 块{why}，已按各路票数最多的原文填回并标 {MARK}，请复核。"
                  if had else f"    ⚠️ 第 {i + 1} 块四路都没有内容，只留时间码和 {MARK}。")
    print(f"P3  ② 分批 {len(spans)} 轮 · 重叠救回 {rescued} 块 · 补漏后仍缺 {len(miss)} 块（已填回）· "
          f"存疑 {len(doubts)} 条 · {time.time() - t0:.0f}s")
    print(f"P3  用量：{usage.line()}")
    p3["_last"] = {"table": table, "doubts": doubts, "usage": vars(usage), "rounds": len(spans),
                   "rescued": rescued, "filled": n_fill}
    return "\n".join(lines) + "\n"


# ═══════════════════════════ ② claude -p · 生产线订阅路 ═══════════════════════════

CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Bash,WebSearch,Skill,Glob,Grep"


def claude_cmd(prompt: str, model: str, effort: str) -> list[str]:
    """与 SaaS skill_merge.build_claude_cmd 一字不差；多一个 --setting-sources project，
    挡住这台机器用户级 CLAUDE.md 里的私人规则混进融合（SaaS 评测台也是这么做的）。"""
    return ["env", "-u", "ANTHROPIC_API_KEY", "-u", "ANTHROPIC_AUTH_TOKEN",
            "claude", "-p", prompt, "--model", model, "--effort", effort,
            "--output-format", "json", "--add-dir", ".", "--strict-mcp-config",
            "--permission-mode", "acceptEdits", "--allowedTools", CLAUDE_ALLOWED_TOOLS,
            "--setting-sources", "project"]


def _fuse_claude_p(p3_input: str, found: list[Divergence], cfg: dict, on_batch=None) -> str:
    from ._merge_zh import PROMPT_SAAS
    p3 = cfg["p3"]
    cl = p3.get("claude") or {}
    model, effort = cl.get("model", "opus"), cl.get("effort", "medium")
    keep = Path(cl["workdir"]).expanduser() if cl.get("workdir") else None
    work = keep or Path(tempfile.mkdtemp(prefix="tl_claude_"))
    work.mkdir(parents=True, exist_ok=True)
    skill_dir = work / ".claude" / "skills" / "multi-asr-merge"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: multi-asr-merge\ndescription: 多路本地 ASR 转录融合。\n"
        "allowed-tools: Read, Write, Bash, WebSearch\n---\n\n# Multi-ASR Merge\n\n" + PROMPT_SAAS.strip() + "\n",
        encoding="utf-8")
    out_dir = work / "Output"
    out_dir.mkdir(exist_ok=True)
    ledger = ledger_rows(found, 1, 10 ** 9)
    infile = out_dir / "local_P2_Match_0000_0000.md"       # 名字沿用 skill 的 `_P2_Match_` 约定
    infile.write_text(
        (("【分歧册 · 由程序逐块穷举比对算出，不是建议答案】\n每一处都要真判一次，不许默认采用顶格那一路。\n\n"
          + ledger + "\n\n=== 多路转写 ===\n\n") if ledger else "") + p3_input, encoding="utf-8")
    terms = _terms_text(cfg["terms"].get("files") or [])
    prompt = f"/multi-asr-merge {infile.relative_to(work)}"
    if terms:
        prompt += "\n\n【本任务用户术语库（作硬证据，优先于联网核实）】\n" + terms
    before = set(out_dir.glob("*_P3_Merge_*.md"))
    t0 = time.time()
    print(f"P3  claude -p · {model} · {effort} · 工作目录 {work}")
    try:
        proc = subprocess.run(claude_cmd(prompt, model, effort), cwd=str(work), capture_output=True,
                              text=True, timeout=int(cl.get("timeout", 3600)))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude -p 超时（> {cl.get('timeout', 3600)} s）")
    stdout = proc.stdout or ""
    state = _classify(stdout, proc.returncode)
    if state != "ok":
        raise RuntimeError(f"claude -p {state}：{stdout[-400:]}")
    new = [p for p in set(out_dir.glob("*_P3_Merge_*.md")) - before if not p.name.endswith(("_report.md", "_raw.md"))]
    if not new:
        raise RuntimeError(f"claude -p 跑完了但 Output/ 里没有新的终稿（工作目录 {work}）")
    final = max(new, key=lambda p: p.stat().st_mtime)
    text = final.read_text(encoding="utf-8")
    report = final.with_name(final.stem + "_report.md")
    p3["_last"] = {"final": str(final), "report": str(report) if report.exists() else "",
                   "seconds": round(time.time() - t0), "workdir": str(work)}
    got, _ = parse_output(text)
    blocks = split_blocks(p3_input)
    lines, miss = _finish(blocks, got, {})
    for i, b in enumerate(blocks):
        if lines[i] is None or _blank(lines[i]):
            fixed, _had = _revive(b["text"])
            lines[i] = fixed or f"[{_ts(b['sec'])} - ?] X: {MARK}"
    print(f"P3  终稿 {len(got)} 行 / 期望 {len(blocks)} 块 · 缺 {len(miss)} 块已填回 · {time.time() - t0:.0f}s")
    if on_batch:
        on_batch(1, 1)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return "\n".join(lines) + "\n"


def _classify(stdout: str, rc: int) -> str:
    """认两种形态：登录态的事件数组（有 rate_limit_event）与单个 result 对象。撞顶 / 认证失效 / 硬错分开报。"""
    try:
        parsed = json.loads(stdout)
    except Exception:
        return "ok" if rc == 0 and stdout.strip() else "error"
    events = parsed if isinstance(parsed, list) else [parsed]
    blob = json.dumps(events, ensure_ascii=False).lower()
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "rate_limit_event":
            st = (ev.get("rate_limit_info") or ev).get("status")
            if st in ("rejected", "warning"):
                return f"capped({(ev.get('rate_limit_info') or ev).get('window', '?')})"
    res = next((ev for ev in events if isinstance(ev, dict) and ev.get("type") == "result"), None)
    if res is None:
        return "error"
    if res.get("is_error"):
        if any(k in blob for k in ("429", "rate limit", "usage limit", "quota", "too many requests")):
            return "capped"
        if any(k in blob for k in ("401", "403", "unauthorized", "authentication", "login expired", "oauth")):
            return "auth"
        return "error"
    return "ok"


# ═══════════════════════════ 入口 ═══════════════════════════

def fuse(p3_input: str, found: list[Divergence], cfg: dict, on_batch=None) -> str:
    backend = (cfg["p3"].get("backend") or "openai").strip()
    if backend == "claude_p":
        return _fuse_claude_p(p3_input, found, cfg, on_batch)
    if backend != "openai":
        raise RuntimeError(f"未知的 p3.backend：{backend}（可选 openai | claude_p）")
    return _fuse_openai(p3_input, found, cfg, on_batch)

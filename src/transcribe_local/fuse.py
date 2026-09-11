# -*- coding: utf-8 -*-
"""Phase 3：融合定字。

只认一个协议 —— OpenAI 的 /v1/chat/completions。
本机的 Ollama / LM Studio / llama-server 说这个协议，DeepSeek / OpenAI 也说这个协议。
换后端只改 base_url + model，代码一条路径。

密钥只从环境变量读。⛔ 永不写进配置文件、永不进日志。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

from . import paths

from .divergence import Divergence




def _terms_block(files: list[str]) -> str:
    chunks = []
    for f in files:
        p = Path(f)
        if p.exists():
            chunks.append(f"### {p.name}\n{p.read_text(encoding='utf-8').strip()}")
    if not chunks:
        return ""
    return "\n\n## 术语库（本次挂载，命中即作硬证据）\n\n" + "\n\n".join(chunks) + "\n"


def system_prompt(cfg: dict) -> str:
    prompt = (paths.root() / cfg["p3"]["prompt"]).read_text(encoding="utf-8").strip()
    return prompt + _terms_block(cfg["terms"].get("files") or [])


def _batches(blocks: list[str], size: int, overlap: int) -> list[tuple[int, list[str], int]]:
    """返回 [(本批第一块的全局下标, 这一批的块, 这批里要留的行数), ...]。

    overlap > 0 时前面多带几块只作上下文、不重复输出 ——
    跨块的「临近句复述」证据不该在批次边界被切断。"""
    out = []
    i = 0
    while i < len(blocks):
        lo = max(0, i - overlap)
        out.append((lo, blocks[lo:i + size], len(blocks[i:i + size])))
        i += size
    return out


def _ledger_for(found: list[Divergence], first: int, last: int) -> str:
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


def _ts(x: float) -> str:
    return f"{int(x) // 60:02d}:{x - (int(x) // 60) * 60:04.1f}"


MARK = "[❓]"


def _blank(line: str) -> bool:
    """这一行除了标点和 [❓] 之外一个字都没有 —— 那不是标存疑，是这块话丢了。"""
    body = re.sub(r"^\s*\[[^\]]*\]\s*[MR]?\s*[:：]?\s*", "", line).replace(MARK, "")
    return not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", body)


def _revive(block: str) -> str:
    """把写空了的那一块按各路票数最多的原文填回去，并标 [❓] 送进复核。

    ⚠️ 这是兜底，不是功能：提示词里已经写了「每块都必须有文字」，但提示词是请求、
    代码才是保证 —— 「内容不能丢」是红线，不能只靠模型照做。
    """
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return ""
    m = re.match(r"^(\[[^\]]*\]\s*[MR]?\s*[:：]\s*)(.*)$", lines[0])
    prefix, main = (m.group(1), m.group(2)) if m else ("", lines[0])
    cands = [main] + [re.sub(r"^\[[A-Z0-9]+\]\s*", "", ln) for ln in lines[1:]]
    cands = [c.strip() for c in cands if c.strip()]
    if not cands:
        return ""
    best = max(cands, key=lambda c: (cands.count(c), c == main))      # 票数优先，同票取顶格那一路
    return f"{prefix}{best} {MARK}"


def fuse(p3_input: str, found: list[Divergence], cfg: dict, on_batch=None) -> str:
    p3 = cfg["p3"]
    blocks = [b for b in p3_input.split("\n\n") if b.strip()]
    size = int(p3.get("blocks_per_call", 8))
    overlap = int(p3.get("overlap", 0))
    sysmsg = system_prompt(cfg)

    key = os.environ.get(p3.get("api_key_env", ""), "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = p3["base_url"].rstrip("/") + "/chat/completions"

    out: list[str] = []
    batches = _batches(blocks, size, overlap)
    for n, (lo, chunk, keep) in enumerate(batches, 1):
        first, last = lo + 1, lo + len(chunk)
        ledger = _ledger_for(found, first, last)
        user = (
            f"以下是第 {first}–{last} 块。**输出必须是 {keep} 行**"
            f"（只输出最后 {keep} 块，前面带的是上下文）。\n\n"
            + "\n\n".join(chunk)
            + (f"\n\n## 分歧册（本批）\n\n{ledger}\n" if ledger else "")
        )
        text = _call(url, headers, p3, sysmsg, user)
        lines = [ln.strip() for ln in text.splitlines() if re.match(r"^\s*\[\d{2}:", ln)]
        if len(lines) != keep:
            print(f"⚠️ 第 {n}/{len(batches)} 批要 {keep} 行，模型给了 {len(lines)} 行 —— 块数对不上，"
                  f"这一批可能有内容丢失，建议调小 p3.blocks_per_call 重跑")
        kept = lines[-keep:] if len(lines) >= keep else lines
        src = chunk[-keep:]
        if len(kept) == len(src):          # 行数对不上时不敢按位置认块，会把内容贴错人
            for j, ln in enumerate(kept):
                if not _blank(ln):
                    continue
                fixed = _revive(src[j])
                if not fixed:
                    continue
                kept[j] = fixed
                print(f"⚠️ 第 {lo + len(chunk) - keep + j + 1} 块被写成了空块（只有 {MARK}、没有字）。"
                      f"已按各路票数最多的原文填回并标 {MARK}，请复核。")
        out += kept
        if on_batch:
            on_batch(n, len(batches))
    return "\n".join(out) + "\n"


def _call(url: str, headers: dict, p3: dict, sysmsg: str, user: str) -> str:
    payload = {
        "model": p3["model"],
        "temperature": float(p3.get("temperature", 0.0)),
        "max_tokens": int(p3.get("max_tokens", 64000)),
        "stream": False,
        "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}],
    }
    payload.update(p3.get("extra") or {})           # 后端专属字段的逃生口，见 config 里的说明
    last: Exception | None = None
    for _ in range(int(p3.get("max_retry", 2)) + 1):
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=float(p3.get("timeout", 1200)))
            r.raise_for_status()
            ch = r.json()["choices"][0]
            if ch.get("finish_reason") == "length":
                # 被 max_tokens 砍断 = 这一批的后半截根本没生成。上游生产线的规矩是
                # **报错退出、绝不落残缺稿** —— 静默收下半截等于悄悄丢内容，踩红线。
                raise RuntimeError(
                    f"这一批被 max_tokens({payload.get('max_tokens')}) 截断了，后半截没生成。"
                    f"不接受残缺稿 —— 把 p3.max_tokens 调大，或把 p3.blocks_per_call 调小再跑。")
            return ch["message"]["content"]
        except Exception as e:                      # 网络抖动 / 后端没起来 / 超时 / 截断
            last = e
    raise RuntimeError(f"Phase 3 调用失败（{p3['base_url']} · {p3['model']}）：{last}")

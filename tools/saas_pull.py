#!/usr/bin/env python3
"""把线上 SaaS 仓里登记为「要拿」的文件搬进本仓（底本本地化第 2 步起用）。

    TRANSCRIBE_SCAN_WORDS=<内部仓私有词表> python3 tools/saas_pull.py --part backend [--dry-run]

按 sync/saas.yaml：
  same            覆盖本地 —— 原样同步，本地不改它
  modify / defer  本地还没有才复制；本地已有一律不动（那是本地改过的版本，同步时人工合并）
  real_content    一律不复制 —— 带真实内容，得先在本地写好编的版本
  skip            不复制

路径映射见 PARTS。要复制的每个文件先过一遍 saas_scan 的扫描：有没登记的命中就不复制它，退出码 1。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import saas_scan as S  # noqa: E402

# 线上路径前缀 → 本仓路径前缀
PARTS = {"backend": ("server/", "backend/")}


def plan(saas_files: list[str], rules: list[dict], part: str, root: Path = S.ROOT) -> list[tuple[str, Path | None, str]]:
    """每个线上文件 → (线上路径, 本仓目标, 动作)。动作：复制 / 覆盖 / 本地已有 / 真实内容 / 不拿 / 未登记。"""
    src, dst_prefix = PARTS[part]
    out = []
    for p in saas_files:
        if not p.startswith(src):
            continue
        r = S.classify(p, rules)
        if r is None:
            out.append((p, None, "未登记"))
            continue
        dst = root / (dst_prefix + p[len(src):])
        if r["as"] == "skip":
            act = "不拿"
        elif r.get("real_content"):
            act = "真实内容"
        elif r["as"] == "same":
            act = "覆盖"
        elif dst.exists():
            act = "本地已有"
        else:
            act = "复制"
        out.append((p, dst, act))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--part", choices=sorted(PARTS), required=True)
    ap.add_argument("--saas", default=os.environ.get("TRANSCRIBE_SAAS", str(S.ROOT.parent / "Transcribe.solution")))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    words_path = os.environ.get("TRANSCRIBE_SCAN_WORDS")
    if not words_path:
        print("缺私有词表：设 TRANSCRIBE_SCAN_WORDS=<内部仓词表路径>", file=sys.stderr)
        return 2
    words = S.load_words(words_path)
    saas = Path(args.saas)
    allow = S.load_allow()
    rows = plan(S.tracked(saas), S.load_rules(), args.part)

    counts: dict[str, int] = {}
    blocked, changed = [], []
    for p, dst, act in rows:
        if act in ("覆盖", "复制"):
            data = (saas / p).read_bytes()
            try:
                fresh = [h for h in S.scan_text(p, data.decode("utf-8"), words) if h.key not in allow]
            except UnicodeDecodeError:
                fresh = []
            if fresh:
                blocked.append((p, fresh))
                act = "扫描拦下"
            elif dst.exists() and dst.read_bytes() == data:
                act = "未变"
            else:
                changed.append(p)
                if not args.dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(data)
        counts[act] = counts.get(act, 0) + 1

    print(("（演练，未写盘）" if args.dry_run else "") + " · ".join(f"{k} {v}" for k, v in counts.items()))
    for p in changed:
        print(f"  写入  {p}")
    for p, hits in blocked:
        for h in hits:
            print(f"  拦下  {h.key}\t# {h.path}:{h.line}")
    for p, _, act in rows:
        if act == "未登记":
            print(f"  未登记  {p}")
    return 1 if blocked or counts.get("未登记") else 0


if __name__ == "__main__":
    sys.exit(main())

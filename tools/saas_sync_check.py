#!/usr/bin/env python3
"""线上 SaaS 仓 → 本仓：同步巡检（底本本地化第 6 步）。

    python3 tools/saas_sync_check.py [--saas <线上仓路径>]
    python3 tools/saas_sync_check.py --mark-synced      # 跟上线上之后，把基线挪到线上当前提交

照线上仓 server/pipeline/sync-check.sh 的做法：按 sync/saas.yaml 逐个比线上与本地，
预期内的差异标「已登记」，预期外的列出来。流程见 docs/saas-sync.md。

  ⚠️ 未登记差异  登记为原样同步（same），却和线上不一样
                 —— 线上改过就跑 saas_pull；本地改过就还原，或在 saas.yaml 登记成 modify
  ⚠️ 要合并      登记为有意不同（modify / defer），线上自基线后又改过 —— 把线上的改动人工合进本地版
  ⚠️ 缺文件      登记为要拿，本地却没有
  ⚠️ 未登记文件  线上有、登记表一条规则都没匹配上（多半是线上新加的）
  ⚠️ 线上删了    基线时线上还有、现在没了，本地还留着
  [已登记]       登记为有意不同，和线上不一样是预期内的

基线（saas.yaml 里的 base）= 上次跟上线上时线上仓的提交。线上仓没提交的改动也算「线上改过」。
只比文件内容，不读私有词表：要把线上的新内容拿进来，仍然走 saas_pull（它先过敏感扫描）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import saas_pull as P  # noqa: E402
import saas_scan as S  # noqa: E402

ALERTS = ("未登记差异", "要合并", "缺文件", "未登记文件", "线上删了")
BASE_RE = re.compile(r"^base:[ \t]*(\S*)", re.M)


@dataclass
class Row:
    kind: str       # ALERTS 之一，或 一致 / 已登记 / 与线上一致 / 待定
    saas: str       # 线上路径
    local: str      # 本仓路径
    note: str = ""


def _git(saas: Path, *args: str) -> list[str]:
    out = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=saas,
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def read_base(path: Path = S.MANIFEST) -> str | None:
    m = BASE_RE.search(path.read_text(encoding="utf-8"))
    return (m.group(1) or None) if m else None


def write_base(sha: str, path: Path = S.MANIFEST) -> None:
    text = path.read_text(encoding="utf-8")
    new, n = BASE_RE.subn(f"base: {sha}", text, count=1)
    if n != 1:
        raise ValueError(f"{path} 里没有 base: 这一行")
    path.write_text(new, encoding="utf-8")


def changed_since(saas: Path, base: str) -> tuple[set[str], set[str]]:
    """(自基线改过或新加的, 自基线删掉的)。比的是线上仓工作区与基线，所以没提交的改动也算。"""
    touched = set(_git(saas, "diff", "--name-only", "--no-renames", base, "--"))
    deleted = set(_git(saas, "diff", "--name-only", "--no-renames", "--diff-filter=D", base, "--"))
    return touched - deleted, deleted


def local_target(p: str) -> str | None:
    for part in P.PARTS:
        t = P._map(p, part)
        if t is not None:
            return t
    return None


def check(saas: Path, rules: list[dict], base: str | None, root: Path = S.ROOT) -> list[Row]:
    changed, deleted = changed_since(saas, base) if base else (None, set())
    rows: list[Row] = []
    for p in S.tracked(saas):
        target = local_target(p)
        if target is None:
            continue
        r = S.classify(p, rules)
        if r is None:
            rows.append(Row("未登记文件", p, target))
            continue
        if r["as"] == "skip":
            continue
        dst = root / target
        up = None if changed is None else p in changed        # None = 没有基线，分不出是哪边改的
        if not dst.exists():
            rows.append(Row("缺文件", p, target, "线上新加的：跑 saas_pull" if up else ""))
            continue
        same_bytes = dst.read_bytes() == (saas / p).read_bytes()
        if r["as"] == "same":
            if same_bytes:
                rows.append(Row("一致", p, target))
            else:
                note = {True: "线上改过：跑 saas_pull", False: "本地改过：还原，或在 saas.yaml 登记为 modify"}.get(up, "")
                rows.append(Row("未登记差异", p, target, note))
        elif same_bytes:
            rows.append(Row("与线上一致" if r["as"] == "modify" else "待定", p, target,
                            "登记为有意不同，内容却和线上一样：可以改回 same" if r["as"] == "modify" else ""))
        elif up:
            rows.append(Row("要合并", p, target, r.get("why", "")))
        else:
            rows.append(Row("已登记" if r["as"] == "modify" else "待定", p, target, r.get("why", "")))
    for p in sorted(deleted):
        target = local_target(p)
        if target is not None and (root / target).exists():
            rows.append(Row("线上删了", p, target, "本地一并删掉，或登记为本地独有"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--saas", default=os.environ.get("TRANSCRIBE_SAAS", str(S.ROOT.parent / "Transcribe.solution")))
    ap.add_argument("--mark-synced", action="store_true",
                    help="已经把线上的改动都跟上了：把基线改成线上当前提交")
    args = ap.parse_args()

    saas = Path(args.saas)
    base = read_base()
    try:
        head = _git(saas, "rev-parse", "--short=12", "HEAD")[0]
        rows = check(saas, S.load_rules(), base)
    except subprocess.CalledProcessError as e:
        print(f"读线上仓失败（{saas}）：{(e.stderr or '').strip()}", file=sys.stderr)
        return 2

    alerts = [r for r in rows if r.kind in ALERTS]
    count: dict[str, int] = {}
    for r in rows:
        count[r.kind] = count.get(r.kind, 0) + 1
    changed = len(changed_since(saas, base)[0]) if base else 0
    print(f"线上仓 {saas} · 当前 {head} · 基线 {base or '（没有）'}" + (f" · 自基线改过 {changed} 个文件" if base else ""))
    print(" · ".join(f"{k} {v}" for k, v in count.items()))

    print("\n—— 有意不同（预期内）——")
    for r in rows:
        if r.kind == "已登记":
            print(f"  [已登记]  {r.local}")
    print("\n—— 要处理 ——")
    for r in alerts:
        tag = "[已登记] " if r.kind == "要合并" else ""
        print(f"  ⚠️ {r.kind}  {tag}{r.saas} → {r.local}" + (f"\n        {r.note}" if r.note else ""))
    if not alerts:
        print("  （无）")
    for r in rows:
        if r.kind == "与线上一致":
            print(f"  提示  {r.local}：{r.note}")

    if args.mark_synced:
        blocking = [r for r in alerts if r.kind != "要合并"]
        if blocking:
            print(f"\n还有 {len(blocking)} 处没处理完（要合并的以外），不挪基线。", file=sys.stderr)
            return 1
        if _git(saas, "status", "--porcelain", "--untracked-files=no"):
            print("\n线上仓有没提交的改动，基线只能是一个提交：先让线上提交，再挪。", file=sys.stderr)
            return 1
        write_base(head)
        print(f"\n基线已挪到 {head}（sync/saas.yaml）。「要合并」那几项默认你已经合完了。")
        return 0

    print("\n✅ 没有预期外的差异。" if not alerts else f"\n⚠️ {len(alerts)} 处要处理，读法见 docs/saas-sync.md。")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())

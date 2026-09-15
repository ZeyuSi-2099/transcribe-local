#!/usr/bin/env python3
"""线上后处理提示词 → backend/pipeline/_pp_prompts.py（生成的，不要手改）。

    python3 tools/saas_pp_prompts.py [--saas <线上仓路径>] [--check]

Duner 2026-09-14 定（计划页 g4）：后处理、术语辅助的提示词一并公开，做法同定字提示词——
放进代码，不放成单独的 .md。线上是 `server/pipeline/vendor/.claude/skills/` 下三份文件，
本地不搬 vendor 目录，由本脚本原样转成包里的文本；`--check` 只比对、不写，线上改了会报出来。

唯一的改动：示例里一个真实格式的手机号换成占位号（开源仓不留可能打得通的号码）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "pipeline" / "_pp_prompts.py"
SKILLS = "server/pipeline/vendor/.claude/skills"
SOURCES = {"PP_NARRATE": "pp-narrate/SKILL.md", "PP_REDACT": "pp-redact/SKILL.md", "SHARED_QC": "_shared/共性质检.md"}
# 手机号形状的一律换成占位号——按形状换，不在这里写原号码（写了等于把它带进开源仓）
MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PLACEHOLDER = "13800138000"


def render(saas: Path) -> str:
    out = ['# -*- coding: utf-8 -*-',
           '"""后处理提示词（生成的，不要手改：python3 tools/saas_pp_prompts.py）。',
           '',
           '来源：线上仓 server/pipeline/vendor/.claude/skills/ 下 pp-narrate、pp-redact、_shared/共性质检 三份，',
           '逐字相同；只把示例里一个真实格式的手机号换成了占位号。',
           '"""',
           '',
           '',
           'class _Text(str):',
           '    """让线上读 .md 文件的那两行（`SKILL_MD.read_text(encoding=...)`）原样能用。"""',
           '',
           '    def read_text(self, encoding=None):  # noqa: ARG002',
           '        return str(self)',
           '']
    for name, rel in SOURCES.items():
        text = (saas / SKILLS / rel).read_text(encoding="utf-8")
        text = MOBILE.sub(PLACEHOLDER, text)
        out.append('')
        out.append(f'{name} = _Text(')
        out.extend(f'    {line!r}' for line in text.splitlines(keepends=True))
        out.append(')')
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--saas", default=os.environ.get("TRANSCRIBE_SAAS", str(ROOT.parent / "Transcribe.solution")))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = render(Path(args.saas))
    if args.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        print("一致" if same else "线上的后处理提示词改过了：重新生成 backend/pipeline/_pp_prompts.py")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"已写 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

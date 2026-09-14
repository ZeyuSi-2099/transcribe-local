#!/usr/bin/env python3
"""线上 SaaS 仓 → 本仓：去留清点 + 敏感扫描（底本本地化第 1 步）。

    TRANSCRIBE_SCAN_WORDS=<内部仓私有词表> python3 tools/saas_scan.py [--saas <线上仓路径>]

做两件事：
  1. 清点：按 sync/saas.yaml 给线上仓每个受版本管理的文件归类（same / modify / defer / skip），
     列出一条规则都没匹配上的文件 —— 线上新加的文件会从这里冒出来。
  2. 扫描：凡是要拿进来的（same / modify / defer），逐行查邮箱、密钥、手机号、身份证号、
     云端地址、本机绝对路径、像转录稿的行，以及私有词表里的词。
     逐条人工看过、确认可以公开的命中，登记进 sync/saas_scan_allow.txt；没登记的命中，退出码为 1。

⛔ 私有词表（客户专名、录音编号）只放内部仓，永不进本仓。这里只从环境变量读它的路径；
   允许清单里也只记行的指纹，不记命中的原文。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sync" / "saas.yaml"
ALLOW = ROOT / "sync" / "saas_scan_allow.txt"
TAKEN = ("same", "modify", "defer")

# 公开可见、或保留给示例用的邮箱域名
EMAIL_OK = re.compile(r"(^|\.)(example\.(com|org|net)|example|local|invalid|test|transcribe\.solutions|anthropic\.com)$", re.I)
# 测试与注释里编的占位邮箱域名（真实存在与否不重要，重要的是不指向真人）
PLACEHOLDER_EMAIL = {"x.com", "y.com", "b.com", "acme.com", "user.com", "test.com", "xxxx.com"}
PLACEHOLDER_MOBILE = {"13800138000"}
TS = r"\[\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s*-\s*[\d:.?]+)?\]"

RULES: list[tuple[str, re.Pattern]] = [
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")),
    ("密钥", re.compile(r"sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
                        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
                        r"|(?i:(?:api[_-]?key|secret|token|password))\s*[=:]\s*['\"][^'\"\s]{12,}['\"]")),
    ("手机号", re.compile(r"(?<![\d.])1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?![\dA-Za-z])")),
    ("云端地址", re.compile(r"[a-z0-9-]+\.(?:fly\.dev|onrender\.com|supabase\.co|r2\.cloudflarestorage\.com|vercel\.app)"
                          r"|ingest\.[a-z.]*sentry\.io|sentry\.io/\d+")),
    ("本机路径", re.compile(r"/Users/[^/\s'\"]+/|/private/tmp/claude|/home/[a-z][^/\s'\"]*/")),
    ("转录形状", re.compile(TS + r".*?(?:[一-鿿][^一-鿿]{0,2}){8}")),
    # 对象写法：{ t: "00:00:03", s: "……" } / {"t": "00:00:23", "s": "……"}
    ("转录形状", re.compile(r"""["']?t["']?\s*:\s*["']\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?["'].*?(?:[一-鿿][^一-鿿]{0,2}){8}""")),
]


@dataclass
class Hit:
    path: str
    line: int
    rule: str
    text: str

    @property
    def key(self) -> str:
        """允许清单里的指纹：文件 + 规则 + 这一行去空白后的哈希。行改了就要重新看。"""
        h = hashlib.sha1(re.sub(r"\s+", "", self.text).encode("utf-8")).hexdigest()[:12]
        return f"{self.path}\t{self.rule}\t{h}"


def load_rules(path: Path = MANIFEST) -> list[dict]:
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))["rules"]
    for r in rules:
        r["glob"] = [r["glob"]] if isinstance(r["glob"], str) else r["glob"]
        assert r["as"] in (*TAKEN, "skip"), f"未知归类 {r['as']}：{r['glob']}"
    return rules


def classify(path: str, rules: list[dict]) -> dict | None:
    """先匹配先生效；一条都不匹配返回 None。"""
    for r in rules:
        if any(fnmatchcase(path, g) for g in r["glob"]):
            return r
    return None


def load_words(path: str | None) -> list[str]:
    """纯数字的词不收：型号里的数字单拿出来到处都是，命中了也说明不了什么。"""
    if not path:
        return []
    words = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        w = ln.split("#", 1)[0].strip()
        if len(w) >= 2 and not w.isdigit():
            words.append(w)
    return sorted(set(words), key=len, reverse=True)


def word_patterns(words: list[str]) -> list[re.Pattern]:
    """纯字母数字的词按整词匹配（前后不能再接字母数字）；不超过 3 个字符的还区分大小写 ——
    否则两三个字母的缩写会命中代码里的任意单词。含中文的词按子串匹配。"""
    pats = []
    for w in words:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+-]*", w):
            flags = 0 if len(w) <= 3 else re.I
            pats.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", flags))
        else:
            pats.append(re.compile(re.escape(w), re.I))
    return pats


def scan_text(path: str, text: str, words: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    pats = word_patterns(words)
    for no, line in enumerate(text.splitlines(), 1):
        for name, rx in RULES:
            for m in rx.finditer(line):
                if name == "邮箱" and (EMAIL_OK.search(m.group(1)) or m.group(1).lower() in PLACEHOLDER_EMAIL):
                    continue
                if name == "手机号" and m.group(0) in PLACEHOLDER_MOBILE:
                    continue
                hits.append(Hit(path, no, name, line))
                break
        if any(p.search(line) for p in pats):
            hits.append(Hit(path, no, "私有词表", line))
    return hits


def load_allow(path: Path = ALLOW) -> set[str]:
    if not path.exists():
        return set()
    return {ln.split("#", 1)[0].rstrip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


def tracked(saas: Path) -> list[str]:
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"], cwd=saas,
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--saas", default=os.environ.get("TRANSCRIBE_SAAS", str(ROOT.parent / "Transcribe.solution")))
    ap.add_argument("--no-private", action="store_true", help="不加载私有词表（会漏掉客户专名类命中）")
    ap.add_argument("--show-allowed", action="store_true", help="连已登记的命中也列出来")
    args = ap.parse_args()

    words_path = os.environ.get("TRANSCRIBE_SCAN_WORDS")
    if not words_path and not args.no_private:
        print("缺私有词表：设 TRANSCRIBE_SCAN_WORDS=<内部仓词表路径>，或显式加 --no-private", file=sys.stderr)
        return 2
    words = [] if args.no_private else load_words(words_path)

    saas = Path(args.saas)
    rules = load_rules()
    files = tracked(saas)
    counts = {k: 0 for k in (*TAKEN, "skip")}
    unregistered, hits = [], []
    for p in files:
        r = classify(p, rules)
        if r is None:
            unregistered.append(p)
            continue
        counts[r["as"]] += 1
        if r["as"] == "skip":
            continue
        try:
            text = (saas / p).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue          # 图标等二进制文件
        hits += scan_text(p, text, words)

    allow = load_allow()
    fresh = [h for h in hits if h.key not in allow]
    print(f"线上仓 {len(files)} 个文件 · 原样同步 {counts['same']} · 有意不同 {counts['modify']} · "
          f"待定 {counts['defer']} · 不拿 {counts['skip']} · 未登记 {len(unregistered)}")
    print(f"私有词表 {len(words)} 个词 · 命中 {len(hits)} 处 · 已人工确认可公开 {len(hits) - len(fresh)} · "
          f"待看 {len(fresh)}")
    for p in unregistered:
        print(f"  未登记  {p}")
    by_rule: dict[str, int] = {}
    for h in fresh:
        by_rule[h.rule] = by_rule.get(h.rule, 0) + 1
    if by_rule:
        print("  待看按规则：" + " · ".join(f"{k} {v}" for k, v in sorted(by_rule.items(), key=lambda x: -x[1])))
    for h in (hits if args.show_allowed else fresh):
        print(f"{h.key}\t# {h.path}:{h.line} {h.text.strip()[:120]}")
    return 1 if (fresh or unregistered) else 0


if __name__ == "__main__":
    sys.exit(main())

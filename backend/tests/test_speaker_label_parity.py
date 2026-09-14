"""说话人标签的前后端对账守卫。

标签在两处各存一份：前端画界面、后端出导出文件（docx / txt / 后处理产物）。分家的症状是
**「界面上写 Interviewer、下载下来是主持人」**——不报错、不崩，只是同一份稿子两套叫法，
而用户是拿这份稿子去交付的。所以这里直接读前端那个文件逐项比对，改一边忘改另一边就红。

做法照搬价目表的守卫（test_pricing_parity.py）：那边防的是「显示 $0.50 实扣 $1.00」，
这边防的是「屏幕一套、文件一套」。
"""
import re
from pathlib import Path

import pytest

from app import speaker_labels as S

_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = _ROOT / "src" / "lib" / "speakerLabels.ts"

# 这是**构建期**守卫：Fly 的任务镜像只装了 server/，前端文件不在里面。
# ⚠️ 跳过的条件是「整棵前端树都不在」（用 package.json 判），**不是「那个文件不在」**——
#    后者会让「有人把 speakerLabels.ts 删了/改名了」也变成静默跳过，而那正是要红的情况。
_skip_in_image = pytest.mark.skipif(
    not (_ROOT / "package.json").exists(), reason="任务镜像里没有前端树；这是构建期守卫")


def _frontend_table() -> dict[str, dict[str, str]]:
    """从 `export const SPEAKER_LABELS ... = { zh: { 主持人: "…", … }, … };` 抠出同构的表。"""
    src = FRONTEND.read_text(encoding="utf-8")
    m = re.search(r"export const SPEAKER_LABELS[^=]*=\s*\{(.*?)\n\};", src, re.S)
    assert m, "前端 speakerLabels.ts 里找不到 SPEAKER_LABELS"
    out: dict[str, dict[str, str]] = {}
    for lang, body in re.findall(r"(\w+):\s*\{([^}]*)\}", m.group(1)):
        out[lang] = dict(re.findall(r'([^\s,{]+)\s*:\s*"([^"]*)"', body))
    return out


@_skip_in_image
def test_前后端标签表逐条相等():
    fe, be = _frontend_table(), S.SPEAKER_LABELS
    assert set(fe) == set(be), f"语言门数不一致：前端 {sorted(fe)} / 后端 {sorted(be)}"
    for lang in sorted(be):
        assert fe[lang] == be[lang], f"{lang} 的标签对不上：前端 {fe[lang]} / 后端 {be[lang]}"


def test_每门语言都覆盖了全部代号():
    keys = set(S.SPEAKER_CODES) | {S.UNKNOWN_PREFIX}
    for lang, table in S.SPEAKER_LABELS.items():
        assert set(table) == keys, f"{lang} 缺或多了代号：{set(table) ^ keys}"


def test_未知标签原样返回_不被吞掉():
    # 用户自己改过的名字、老任务里存进去的显示文字，都不该被抹成空或兜成某一档
    assert S.speaker_label("张工", "de") == "张工"
    assert S.speaker_label("Interviewer", "zh") == "Interviewer"
    assert S.speaker_label(None, "zh") == "" and S.speaker_label("", "zh") == ""


def test_说话人N走前缀规则():
    assert S.speaker_label("说话人 2", "ja") == "話者 2"
    assert S.speaker_label("说话人 2", "zh") == "说话人 2"
    assert S.speaker_label("说话人3", "de") == "Sprecher 3"


def test_未放量的语言回落英文_不回落中文():
    # 回落中文的话，一个韩语用户会看到汉字而不是他至少认得的英文
    assert S.speaker_label("主持人", "ko") == "Interviewer"
    assert S.speaker_label("主持人", "") == "Interviewer"


def test_分隔符跟着界面语言():
    assert S.speaker_sep("zh") == "："
    assert S.speaker_sep("ja") == ": " and S.speaker_sep("de") == ": "

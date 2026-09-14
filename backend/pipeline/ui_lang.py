"""界面语言 → 写给模型看的语言名。**全流水线唯一一份**。

规则（Duner 2026-08-30 定）：我们写的字跟界面语言走（8 门），录音里的内容原样不翻。
凡是要在提示词里告诉模型「用哪门语言写」的地方（P3 融合报告、后处理质检报告、
术语库助手），都从这里取名字——抄第二份必漂移，而症状是「某一处还在说中文」。

母语名 + 英文名两个都给，歧义最小（只给 `pt` 这种代码，模型会在葡/巴葡之间摇摆）。
⚠️ 与 `app/speaker_labels.py`、`src/lib/appI18n` 同一批 8 门；加语言几处都要动。
"""
from __future__ import annotations

UI_LANG_NAMES = {
    "zh": "简体中文（Simplified Chinese）",
    "en": "English",
    "de": "Deutsch（German）",
    "fr": "Français（French）",
    "es": "Español（Spanish）",
    "it": "Italiano（Italian）",
    "pt": "Português（Portuguese）",
    "ja": "日本語（Japanese）",
}


def norm(ui_lang: str | None) -> str:
    """`de-DE` / ` DE ` / None → `de` / `en`。八门之外的一律回落英文。"""
    code = (ui_lang or "en").split("-")[0].strip().lower()
    return code if code in UI_LANG_NAMES else "en"


def ui_lang_name(ui_lang: str | None) -> str:
    """未放量的界面语言回落**英文**，不回落中文——回落中文的话，
    一个韩语用户会拿到一份他读不懂的报告，而英文他至少还能读。"""
    return UI_LANG_NAMES[norm(ui_lang)]

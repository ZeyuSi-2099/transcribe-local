"""说话人标签：**代号 → 界面语言**的唯一来源（后端侧）。

⚠️ **前端有一份逐条相同的表**（`src/lib/speakerLabels.ts`），两份靠
   `server/tests/test_speaker_label_parity.py` **读前端那个文件逐项比对**钉住。
   分家的症状是「界面上写 Interviewer、下载下来是主持人」——不报错，只是对不上。
   （同一套做法见价目表：`app/pricing.py` ↔ `src/lib/pricing.ts`。）

代号为什么是中文词、为什么不放进 appI18n 对照本，见前端那个文件的文件头。
"""
from __future__ import annotations

import re

SPEAKER_CODES = ("主持人", "被访者", "其他", "多人混合")

# 认不出角色时流水线写的前缀，后面跟一个序号（`说话人 2`）。
UNKNOWN_PREFIX = "说话人"

# 代号 → 各界面语言。**改这里必须同步改 speakerLabels.ts**（对账测试会红）。
SPEAKER_LABELS: dict[str, dict[str, str]] = {
    "zh": {"主持人": "主持人", "被访者": "被访者", "其他": "其他", "多人混合": "多人混合", "说话人": "说话人"},
    "en": {"主持人": "Interviewer", "被访者": "Interviewee", "其他": "Other", "多人混合": "Multiple speakers", "说话人": "Speaker"},
    "de": {"主持人": "Interviewer", "被访者": "Befragte Person", "其他": "Sonstige", "多人混合": "Mehrere Sprecher", "说话人": "Sprecher"},
    "fr": {"主持人": "Intervieweur", "被访者": "Personne interrogée", "其他": "Autre", "多人混合": "Plusieurs locuteurs", "说话人": "Locuteur"},
    "es": {"主持人": "Entrevistador", "被访者": "Persona entrevistada", "其他": "Otro", "多人混合": "Varios hablantes", "说话人": "Hablante"},
    "it": {"主持人": "Intervistatore", "被访者": "Persona intervistata", "其他": "Altro", "多人混合": "Più interlocutori", "说话人": "Interlocutore"},
    "pt": {"主持人": "Entrevistador", "被访者": "Pessoa entrevistada", "其他": "Outro", "多人混合": "Vários locutores", "说话人": "Locutor"},
    "ja": {"主持人": "インタビュアー", "被访者": "回答者", "其他": "その他", "多人混合": "複数話者", "说话人": "話者"},
}


def _table(ui_lang: str | None) -> dict[str, str]:
    return SPEAKER_LABELS.get((ui_lang or "en").split("-")[0].strip().lower(), SPEAKER_LABELS["en"])


def speaker_label(sp: str | None, ui_lang: str | None) -> str:
    """说话人代号 → 界面语言的显示名。

    认不出的值**原样返回**——用户自己改过的名字、老任务里存进去的显示文字，都不该被吞掉。
    """
    if not sp:
        return ""
    t = _table(ui_lang)
    if sp in t:
        return t[sp]
    if sp.startswith(UNKNOWN_PREFIX):                  # 「说话人 2」→「Speaker 2」
        return f"{t[UNKNOWN_PREFIX]} {sp[len(UNKNOWN_PREFIX):].strip()}".strip()
    return sp


def speaker_sep(ui_lang: str | None) -> str:
    """标签与句子之间的分隔符：中文配全角，其余配半角加空格。"""
    return "：" if (ui_lang or "en").split("-")[0].strip().lower() == "zh" else ": "


# 行首的「<代号><冒号>」——后处理产物（脱敏稿）是问答体，每行开头就是这个形状。
# 只认**行首**：正文中间出现「主持人：」的概率极低，而且那种情况本来就是在引用一个标签。
_LEAD_LABEL = re.compile(r"^(主持人|被访者|其他|多人混合|说话人\s*\d*)\s*[:：]\s?")


def localize_transcript_labels(md: str, ui_lang: str | None) -> str:
    """把问答体稿子里行首的说话人代号换成界面语言的显示名。

    **为什么下载时才换**：后处理产物是模型写出来、原样存在 R2 的 markdown，里面的说话人
    沿用输入稿的代号（`redact_diff.segments_to_qa` 拼的就是代号）。存的时候不能定死语言
    ——同一份产物要能被任何界面语言的人下载。⚠️ 在此之前脱敏稿是**原样带着中文标签**下发的，
    德语用户下到的每一行都是 `主持人：…`。

    中文界面原样返回（代号就是中文，换了也一样，少走一遍正则）。
    """
    if not md or (ui_lang or "en").split("-")[0].strip().lower() == "zh":
        return md
    sep = speaker_sep(ui_lang)

    def sub(line: str) -> str:
        m = _LEAD_LABEL.match(line)
        if not m:
            return line
        code = re.sub(r"\s+", " ", m.group(1)).strip()
        return f"{speaker_label(code, ui_lang)}{sep}{line[m.end():]}"

    return "\n".join(sub(ln) for ln in md.split("\n"))


# 行**中**的代号。⚠️ **不能要求后面跟冒号**——2026-08-30 第一版这么写，生产复验只换掉了
# 1/5：模型引用输入稿时的格式是它自己定的，实拍到 `主持人：…` 与 `主持人「…」` 两种并存。
# 所以按「代号本身」匹配，冒号只在**出现时**一并归一成界面语言的分隔符。
#
# 代号分两档，因为误伤代价不同：
#   强档（访谈域的专有说法）——出现即换。非中文报告里出现它们，只可能是抄自我们的输入稿。
#   弱档（`其他` 是常用词、`说话人` 不带序号时也可能是散文）——只在后面真跟着冒号时才换。
_STRONG_CODE = re.compile(r"(主持人|被访者|多人混合|说话人\s*\d+)(\s*[:：]\s?)?")
_WEAK_CODE = re.compile(r"(其他|说话人)(\s*[:：]\s?)")


def localize_quoted_labels(text: str, ui_lang: str | None) -> str:
    """把**引文里**的说话人代号换成界面语言的显示名（质检报告用）。

    与 `localize_transcript_labels` 的区别只有锚点，但那条注释说的「正文中间出现
    『主持人：』的概率极低」在质检报告上不成立——报告要举证「原文是这样、我改成了那样」，
    而输入稿每行开头就是代号（`redact_diff.segments_to_qa` 拼的），于是代号被原样抄进
    行中：`Original: 主持人：…`。2026-08-30 八门实测在 en/it 两份报告里都拍到。

    ⚠️ **引文本身跟录音语种、一个字不翻**；换掉的只是那个代号——它是**我们的字**，
    不是录音里的内容（同一条规则的两半）。中文界面原样返回：代号就是中文。

    ⚠️ 代号后面的标点**保持模型写的样子**（`主持人「…」` 换完仍是 `Interviewer「…」`）：
    那是它的排版选择，我们只换那个词。只有冒号会被归一成界面语言的分隔符。
    """
    if not text or (ui_lang or "en").split("-")[0].strip().lower() == "zh":
        return text
    sep = speaker_sep(ui_lang)

    def sub(m: re.Match) -> str:
        code = re.sub(r"\s+", " ", m.group(1)).strip()
        label = speaker_label(code, ui_lang)
        tail = m.group(2) or ""
        return f"{label}{sep}" if (":" in tail or "：" in tail) else label

    return _WEAK_CODE.sub(sub, _STRONG_CODE.sub(sub, text))

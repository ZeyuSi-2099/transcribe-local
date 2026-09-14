"""转录数据模型 + 流水线 markdown 解析。

流水线末稿（P3 merge·DeepSeek 一步成稿）每行格式：`[MM:SS.d - MM:SS.d] LABEL: 文本`
其中 MM 为「总分钟数」（可 >59），LABEL 为 M/R/X 或原始说话人名。
前端 SampleRow 形状为 {t, s, sp}：t 为 "HH:MM:SS" 起始时间。
"""
import re
from dataclasses import dataclass, asdict
from typing import Optional

SPEAKER_MAP = {"M": "主持人", "R": "被访者", "X": "其他"}
UNKNOWN_SPEAKER = "说话人"

# P3 写进正文的存疑标记。**形状必须与 review.py 认的那套一致**（那边靠它把词剥干净才搜得到
# occurrences）。放在本模块，是因为它既要剥正文（orchestrator）又要剥说话人标签（本文件），
# 而 transcript 是两者共同的下层——放上层就会变成两份正则，改一份不报错。
_MARKER_RE = re.compile(r"\[?\s*❓\s*\]?")


def strip_marker(text: str) -> str:
    """去掉 `[❓]` 内部标记——存疑由前端下划线 + 复核卡表达，用户稿/导出不该带它。"""
    return _MARKER_RE.sub("", text)


def _display_speakers(labels: list[str]) -> dict[str, str]:
    """原始标签 → 给用户看的名字。labels 按出现顺序、可重复。

    ① 认得出的角色（M/R/X）→ 主持人/被访者/其他；
    ② 认不出的（`Speaker 1`/`SPEAKER_00`/引擎自造的名字）→「说话人 N」，N 按首次出现排。
       此前是**原样透出**，于是俄语那单在界面上显示成 `M[❓]`——引擎内部代号漏到了用户眼前；
    ③ ⚠️ **整篇只有一个说话人时一律叫「说话人」，连 M/R 也不例外**：只有一个人的时候
       「主持人」是一句我们支撑不了的角色断言（说话人分离退化成 1 人时正是如此），
       而编号也没有意义。宁可说「说话人」——它至少是真的。
    """
    seen: list[str] = []
    for x in labels:
        if x not in seen:
            seen.append(x)
    if len(seen) <= 1:
        return {x: UNKNOWN_SPEAKER for x in seen}
    out, n = {}, 0
    for x in seen:
        if x in SPEAKER_MAP:
            out[x] = SPEAKER_MAP[x]
        else:
            n += 1
            out[x] = f"{UNKNOWN_SPEAKER} {n}"
    return out

# [MM:SS.d - ...] LABEL: text   —— LABEL 不含冒号；中英文冒号都接受
# ⚠️ **终点时间可省**（2026-09-03 生产事故）：Claude 走 P3 时把整篇 429 行都写成 `[00:00.1] M：…`
# （只有起点），而这里原本要求 `[起点 - 终点]`——一行都认不出 → 「终稿解析为空」整单判失败，
# P1 五路 + P3 十二分钟的钱全白花，而终稿本身是好的（联网定字 3 例、存疑 1 处都在）。
# 交付只用起点（`_seconds_to_hms(total)`），终点从来没被读过，所以放宽不改任何既有行为。
# 守卫 `tests/test_transcript.py::test_只有起点时间的行也要认`——用例就是那份生产产物的原行。
_LINE_RE = re.compile(
    r"^\s*\[\s*(\d+):(\d{1,2}(?:\.\d+)?)\s*(?:-\s*[\d:.]+\s*)?\]\s*"
    r"([^:：]+?)\s*[:：]\s*(.*\S)\s*$"
)


@dataclass
class Segment:
    t: str               # "HH:MM:SS" 起始时间
    s: str               # 文本
    sp: Optional[str]    # 说话人显示名（M/R/X 已映射），未知则原样


def _seconds_to_hms(total: float) -> str:
    # ⚠️ **向下取整，不是四舍五入**。源时间码带一位小数（`[00:50.5 - ...]`），round 会把
    # 一半的句子起点往后挪最多 0.5 秒 —— 试听时前几个字就被切掉了（2026-08-21 用户实测）。
    # 取整方向只能往早：早一点最多多听到半个气口，晚一点就是听不到字。
    s = int(total)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


# 看起来是转录行、却解析不出来的行。**判据必须是「像但不是」，不能是「不匹配」**——
# 标题、空行、模型写的说明都不匹配，把它们算进来这个数就永远不是 0、也就永远没人看。
# 以 `[` 开头（时间码括号）却过不了 _LINE_RE 的，只有一种解释：P3 那一行排版跑偏了。
_LOOKS_LIKE_ROW = re.compile(r"^\s*\[")


def count_unparsed_rows(md: str) -> int:
    """终稿里「像转录行却解析不出」的行数。

    ⚠️ 这是**丢内容的唯一可见信号**：`parse_transcript_md` 解析不出就跳过，
    整篇解析为空有护栏（orchestrator 会判失败不计费），但**丢几行没有任何护栏**——
    稿子照常交付、照常计费，只是少了那几句，谁也不知道。
    """
    return sum(1 for ln in md.splitlines()
               if _LOOKS_LIKE_ROW.match(ln) and not _LINE_RE.match(ln))


def _rows(md: str) -> list[tuple[str, str, str]]:
    """终稿 md → [(时间, 正文, **原始标签**)]，标签未剥 `[❓]`。

    ⚠️ **`parse_transcript_md` 与 `marked_speaker_rows` 的唯一行来源。**
    两边各写一份循环的话，会在「哪些行算数」上悄悄分家（正则改一处、跳过规则改一处都够），
    而症状是复核卡绑到错误的那一行——不报错，只是点开跟说的不是一回事。
    """
    rows: list[tuple[str, str, str]] = []
    for line in md.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        minutes, seconds, label, text = m.groups()
        total = int(minutes) * 60 + float(seconds)
        rows.append((_seconds_to_hms(total), text.strip(), label.strip()))
    return rows


def parse_transcript_md(md: str) -> list[Segment]:
    rows = _rows(md)
    # ⚠️ 标签也要剥 `[❓]`：P3 偶尔会把存疑标记打在说话人上（`M[❓]:`），
    # 而剥之前 `M[❓]` 跟 SPEAKER_MAP 一个键都对不上 → 原样透出到界面。
    labels = [strip_marker(lb).strip() for _t, _s, lb in rows]
    names = _display_speakers(labels)
    return [Segment(t=t, s=text, sp=names.get(lb, lb))
            for (t, text, _raw), lb in zip(rows, labels)]


def marked_speaker_rows(md: str) -> list[dict]:
    """P3 把 `[❓]` 打在**说话人标签**上的行 → [{t, lineText, speaker}]。

    正文里的 `[❓]` 说的是「这几个字拿不准」，标签上的 `[❓]` 说的是**「这段是谁说的拿不准」**——
    两件事，处置也不同（前者改字，后者改归属），所以各出各的卡。

    ⚠️ **只能在 md 上跑**：标签一进 `Segment` 就被剥干净了（见上），拿 segments 再扫是扫不到的。
    ⚠️ `lineText` 必须与最终交付的正文**逐字相同**——前端 `occToRow` 按 `(t, lineText)` 定位行，
    差一个字符就回落成「只按时间码找」，同秒两行时会挑错行。正文的剥法在 orchestrator
    （`s.s = _strip_marker(s.s)`，不额外 strip 空白），这里照抄那一步，别自作主张多 strip。
    """
    rows = _rows(md)
    labels = [strip_marker(lb).strip() for _t, _s, lb in rows]
    names = _display_speakers(labels)
    return [
        {"t": t, "lineText": strip_marker(text), "speaker": names.get(lb, lb)}
        for (t, text, raw), lb in zip(rows, labels)
        if _MARKER_RE.search(raw)
    ]


def to_json(segments: list[Segment]) -> list[dict]:
    return [asdict(x) for x in segments]

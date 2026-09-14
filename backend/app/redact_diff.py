"""脱敏改动清单：对比脱敏前后两稿，逐条列出「原词 → 脱敏后」。

**为什么不解析 QC 报告**（2026-08-20 定）：报告是模型自己写的 markdown，SKILL.md 只要求
「逐条原文→脱敏后」，没约定表格列——这一单能解析，下一单未必。而且它记的是模型**自述**的
改动，漏记一条报告就少一条。逐行对比拿到的是**真实发生**的改动，且两条执行路（Claude /
DeepSeek 降级）一视同仁。副作用是拿不到「为什么改」——那仍然去 QC 报告里看。

只读 R2 上已有的产物，跑在 Render 侧，**不需要重建 Fly 镜像**。

防御式：对不上就返回空，绝不编造（同 review.py）。前端拿到空就不显示这一块。
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from . import blobstore, jobstore

# 一条改动最长多少字仍算「一个词的替换」。超过多半是整句重写——脱敏不该有整句重写，
# 出现即视为对错了稿，整份丢弃比列一堆噪音强。
MAX_SPAN = 40
# 改动行占比上限：脱敏只动少数行；几乎每行都变说明比的不是同一份稿子。
# **只在行数够多时才用**——比例对小样本没有统计意义，一行的稿子改一处就是 100%。
MAX_CHANGED_RATIO = 0.9
RATIO_MIN_LINES = 8
# 收缩到最小差异后至少要留几个字：「十二个人 → 十几个人」收到底是「二 → 几」，
# 逐字对比没错，可放到界面上没人看得懂。不足就把两侧的公共字符补回来（两边补同样的，
# 所以补完仍是一对等价替换），补到看得懂为止。
MIN_SPAN = 2
MAX_PAD = 6
# 补上下文时**先往右**：中文里被换掉的词后面常跟着表类别的字（省/市/公司/项目），
# 前面则多是标点或动词。往左补会把无关字符粘进来——同一个「吉林省 → 本省」被切成
# 「：吉林→：本」「像吉林→像本」…十条各自计数，谁也合并不了（2026-08-20 真实长稿实见）。
# 标点一律跳过：把标点补进词里毫无意义。
_PUNCT = re.compile(r"[\s，。、：；！？…—·「」『』（）《》〈〉“”‘’\",.:;!?()\[\]{}`~-]")

# ── 词级对比（2026-08-30）────────────────────────────────────────────────
# 上面那套逐字符对比 + 收缩/补字参数全是照「一个汉字就是一个词」调的。空格分词的语种
# （法/德/俄/阿…）词内部也有公共字母，SequenceMatcher 会从词中间下刀——生产实见
# 「Schneider Electric → la société XX」被切成「Sc → la soc」「chnei → ci」四张碎纸。
# 所以按行分流：不靠空格分词的文字（中日文假名汉字、泰文）占多数的行走原路（中文上
# 它是对的，零回归）；其余的行按**空格分词**再比，改动段永远落在词边界上。
# 词级的防整句重写闸按词数计（MAX_SPAN 那个 40 字符是照中文调的，六个法语词就能超）。
_NOSPACE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿฀-๿]")
_WORD = re.compile(r"\S+")
MAX_SPAN_WORDS = 6          # 一条改动最多几个词仍算「一个词组的替换」，超过=整句重写
MAX_SPAN_WORD_CHARS = 80    # 词级的字符兜底（六个长词也不该到 80）；改口校验同一上限


def _use_word_diff(line: str) -> bool:
    """这一行该按词比还是按字比。以**不靠空格分词的字符**是否占多数为准——
    问答体的行带中文说话人代号前缀（`主持人：Bonjour…`），只认「含不含中文」会把
    整份外文稿都判回字符级，所以数的是占比不是有无。"""
    nospace = spaced = 0
    for c in line:
        if _NOSPACE.match(c):
            nospace += 1
        elif c.isalpha():
            spaced += 1
    return spaced > nospace


def segments_to_qa(segments: list[dict]) -> str:
    """转录 segments（{t,sp,s}）→ 问答体 md：`说话人：文本` 行，无时间戳；无说话人则裸文本。

    **脱敏/视角转换两步的输入都由它生成**，改这里等于改后处理的输入——
    pipeline/pp_runner.py 与本模块的改动对比都依赖它算出同一份稿子。"""
    lines = []
    for seg in segments:
        sp = seg.get("sp")
        text = seg.get("s", "")
        lines.append(f"{sp}：{text}" if sp else text)
    return "\n\n".join(lines)


# 分类靠**替换词的形态**反推——脱敏产物的标记很规整（XX公司 / A地 / 本省 / XXXXX）。
# 认不出就归「其他」，仍然列出来：用户要的是「改了什么」，分组只是锦上添花。
# 中文以外的稿子标记形态未知，多半会整批落进「其他」，这是有意为之——宁可不分组，不可不显示。
_CONTACT = re.compile(
    r"^[Xx]{5,}$"                                  # 手机号：11 个 X
    r"|^[Xx]{2,}@[Xx]{2,}(\.[A-Za-z]{2,})?$"       # 邮箱：xxxx@xxxx.com
    r"|^[Xx]{2,}[-－][Xx]{4,}$"                     # 固话：XXX-XXXXXXXX
)
_GEO_WORDS = {"当地", "本省", "本市", "本地", "某省", "某市", "该地"}
_GEO_CODE = re.compile(r"^[A-Z]地$")
_PERSON = re.compile(r"^[A-Z]{1,2}(先生|女士|总|经理|工程师|老师|部长|主管)?$")
_NUMWORD = re.compile(r"[0-9０-９一二三四五六七八九十百千万亿]")
_VAGUE = re.compile(r"数|几|多|左右|上千|上万|来个|余")


def classify(orig: str, repl: str) -> str:
    if _CONTACT.match(repl):
        return "contact"
    if repl in _GEO_WORDS or _GEO_CODE.match(repl):
        return "geo"
    if re.match(r"^[A-Z]{1,2}(公司|集团|机构|平台)$", repl) or re.match(r"^XX Compan", repl):
        return "company"
    if re.match(r"^([A-Z]{1,2}|相关|某)(项目|工程)$", repl):
        return "project"
    if _PERSON.match(repl):
        return "person"
    if _NUMWORD.search(orig) and _VAGUE.search(repl):
        return "number"
    return "other"


def _line_spans(src: str, dst: str) -> list[tuple[str, str, int, int, int, int]]:
    """一行内的替换对 + **它在两侧那行里的精确区间**：`(原词, 脱敏后, i1, i2, j1, j2)`，
    满足 `src[i1:i2] == 原词` 且 `dst[j1:j2] == 脱敏后`。

    按行分流（2026-08-30，理由见 _use_word_diff 上方）。⚠️ extract_changes 与
    apply_overrides 都从这里进——分流写在这一处，两边的区间/序号才对得上；
    分开判的话用户改口会按另一套刀口下刀。
    """
    if _use_word_diff(src):
        return _word_spans(src, dst)
    return _char_spans(src, dst)


def _word_spans(src: str, dst: str) -> list[tuple[str, str, int, int, int, int]]:
    """空格分词的行：对**词的序列**跑 SequenceMatcher，改动段=连续的差异词段，
    区间取「首词起点 → 末词终点」，所以 `src[i1:i2]` 连词间空白一起原样保留。

    纯增删（一侧没词）补进**同一个等价邻词**：`la région X → notre région` 里删掉的
    `X` 单独一侧是空串，界面上没法读；两侧各补上等价的 `région`，仍是一对等价替换。
    """
    sw = list(_WORD.finditer(src))
    dw = list(_WORD.finditer(dst))
    ops = SequenceMatcher(None, [m.group() for m in sw], [m.group() for m in dw],
                          autojunk=False).get_opcodes()
    out: list[tuple[str, str, int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        if i1 == i2 or j1 == j2:
            # 补邻词：前一段必是 equal（SequenceMatcher 的差异段两侧都是匹配块），
            # 两侧同减/同加一个下标拿到的就是同一个词
            if i1 > 0 and j1 > 0:
                i1 -= 1
                j1 -= 1
            elif i2 < len(sw) and j2 < len(dw):
                i2 += 1
                j2 += 1
        if i2 - i1 > MAX_SPAN_WORDS or j2 - j1 > MAX_SPAN_WORDS:
            raise ValueError("改动段过长，判定为对错了稿")
        ci1, ci2 = (sw[i1].start(), sw[i2 - 1].end()) if i1 < i2 else \
            ((sw[i1].start(),) * 2 if i1 < len(sw) else (len(src),) * 2)
        cj1, cj2 = (dw[j1].start(), dw[j2 - 1].end()) if j1 < j2 else \
            ((dw[j1].start(),) * 2 if j1 < len(dw) else (len(dst),) * 2)
        a, b = src[ci1:ci2], dst[cj1:cj2]
        if len(a) > MAX_SPAN_WORD_CHARS or len(b) > MAX_SPAN_WORD_CHARS:
            raise ValueError("改动段过长，判定为对错了稿")
        if not a and not b:
            continue
        out.append((a, b, ci1, ci2, cj1, cj2))
    return out


def _char_spans(src: str, dst: str) -> list[tuple[str, str, int, int, int, int]]:
    """不靠空格分词的行（中文为主）：逐字符对比。**2026-08-30 分流前的原实现，一字未动。**

    SequenceMatcher 的匹配块之间即改动段，两端再收一次公共字符——相邻改动挨得近时块会被
    并成一段，不收的话会得到「江苏的团队有12」这种带上下文的原词。

    ⚠️ **区间必须跟着收缩与补齐一起动**。用户改口（把某处换回原词）是按区间下刀的，
    退而求其次的做法是 `line.replace(脱敏后, 原词)` —— 一行里两家不同公司都被换成
    「XX公司」时，改回其中一家会把另一家也改掉，而那正是最不能出错的一类词。
    """
    out: list[tuple[str, str, int, int, int, int]] = []
    ops = SequenceMatcher(None, src, dst, autojunk=False).get_opcodes()
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            continue
        a, b = src[i1:i2], dst[j1:j2]
        # 段内的公共前后缀先剥掉，剥下来的算作紧邻的上下文（补的时候优先用它）
        pre = src[ops[k - 1][1]:ops[k - 1][2]] if k and ops[k - 1][0] == "equal" else ""
        suf = src[ops[k + 1][1]:ops[k + 1][2]] if k + 1 < len(ops) and ops[k + 1][0] == "equal" else ""
        while a and b and a[0] == b[0]:
            pre, a, b = pre + a[0], a[1:], b[1:]
            i1 += 1
            j1 += 1
        while a and b and a[-1] == b[-1]:
            suf, a, b = a[-1] + suf, a[:-1], b[:-1]
            i2 -= 1
            j2 -= 1
        if not a and not b:
            continue
        if len(a) > MAX_SPAN or len(b) > MAX_SPAN:
            raise ValueError("改动段过长，判定为对错了稿")
        # 太短就把上下文补回来：先右后左、跳过标点（理由见 _PUNCT 上方）。
        # 两边补的是同一批字符，所以补完仍是一对等价替换。
        pad = 0
        while min(len(a), len(b)) < MIN_SPAN and pad < MAX_PAD:
            if suf and not _PUNCT.match(suf[0]):
                a, b, suf = a + suf[0], b + suf[0], suf[1:]
                i2 += 1
                j2 += 1
            elif pre and not _PUNCT.match(pre[-1]):
                a, b, pre = pre[-1] + a, pre[-1] + b, pre[:-1]
                i1 -= 1
                j1 -= 1
            else:
                break        # 两边都是标点或已到头：宁可短，也不把标点粘进来
            pad += 1
        out.append((a, b, i1, i2, j1, j2))
    return out


def _line_pairs(src: str, dst: str) -> list[tuple[str, str]]:
    """只要替换对、不要区间（分类与计数用）。"""
    return [(a, b) for a, b, *_ in _line_spans(src, dst)]


# 逐处上下文：改动那一句左右各留多少字、上下句各显示多少字。
# 上限存在的理由是**载荷**：视角转换后的稿子一行就是一整段，不截的话几十处能堆成上百 KB。
SPOT_WIN = 60
CTX_MAX = 60


def _window(text: str, s: int, e: int, win: int = SPOT_WIN) -> dict:
    """把一行裁成「改动处 + 左右各 win 字」，并把高亮区间换算到裁剪后的坐标系。

    前后各补一个省略号；省略号本身占一个字符，所以偏移要跟着加。"""
    a = max(0, s - win)
    b = min(len(text), e + win)
    head = "…" if a > 0 else ""
    tail = "…" if b < len(text) else ""
    off = len(head) - a
    return {"text": head + text[a:b] + tail, "s": s + off, "e": e + off}


def _neighbour(lines: list[str], i: int, step: int) -> str | None:
    """上一句 / 下一句：跳过空行（问答体是 `\n\n` 拼的，奇数行全是空的）。取不到返回 None。"""
    j = i + step
    while 0 <= j < len(lines):
        t = lines[j].strip()
        if t:
            return t[:CTX_MAX] + ("…" if len(t) > CTX_MAX else "")
        j += step
    return None


def extract_changes(src: str, dst: str) -> list[dict]:
    """返回 [{kind, from, to, count, spots:[…]}]，按出现次数降序。对不上一律返回 []。

    一个 **spot** 就是「第几行的第几处替换」，带着它的上下文：
    `{line, idx, before:{text,s,e}, after:{text,s,e}, prev, next}`。

    为什么要连上下文一起给（2026-08-22 Duner）：孤零零一个「山西 → 本省」没法判断改得对不对，
    得看它在哪句话里。而**回到左侧正文**这条路只在「脱敏是第一步」时走得通——跑在视角转换
    之后时原话已被重写成叙述，那一句物理上不存在了。上下文自带，就与前一步跑了什么无关。

    `idx` 是这一处在**该行 spans 里的序号**，逐处改口靠它下刀：同一行里两家不同公司都被
    换成「XX公司」时，只按 (行, 原词, 脱敏后) 认会把两家一起改掉。"""
    # ⚠️ 先剥掉末尾空白再比行数：产物是 Write 写出来的文件，**末尾带一个换行**，
    # 而重算的输入是内存里拼的字符串、没有。2026-08-20 生产实测踩到——63 行 vs 64 行，
    # 内容一字不差却被下面的闸整份丢弃。本地单测与手工构造的数据都造不出这个差异。
    a_lines = src.rstrip("\n").split("\n")
    b_lines = dst.rstrip("\n").split("\n")
    if len(a_lines) != len(b_lines):
        return []          # 只替换不增删是脱敏的铁律；行数不等即不是同一份稿子
    changed = 0
    agg: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    try:
        for i, (a, b) in enumerate(zip(a_lines, b_lines)):
            if a == b:
                continue
            changed += 1
            for n, (frm, to, i1, i2, j1, j2) in enumerate(_line_spans(a, b)):
                pair = (frm, to)
                if pair not in agg:
                    order.append(pair)
                    agg[pair] = []
                agg[pair].append({
                    "line": i, "idx": n,
                    "before": _window(a, i1, i2),
                    "after": _window(b, j1, j2),
                    "prev": _neighbour(b_lines, i, -1),
                    "next": _neighbour(b_lines, i, 1),
                })
    except ValueError:
        return []
    nonblank = sum(1 for x in a_lines if x.strip())
    if nonblank >= RATIO_MIN_LINES and changed / nonblank > MAX_CHANGED_RATIO:
        return []
    idx = {p: i for i, p in enumerate(order)}
    items = [{"kind": classify(o, r), "from": o, "to": r, "count": len(sp), "spots": sp}
             for (o, r), sp in agg.items()]
    items.sort(key=lambda it: (-it["count"], idx[(it["from"], it["to"])]))
    return items


def apply_overrides(src: str, dst: str, overrides: list[dict]) -> str:
    """把用户改口写回脱敏稿：按「行号 + 该行第几处 + 原词→脱敏后」精确定位，换成用户要的字。

    每条 override 形如 `{line, idx, from, to, value}`；`value == from` 即「保留原词」。
    `idx` 缺省（老数据）→ 退回「该行同名替换全都作用」，与加 idx 之前的行为一致。

    **不重跑模型**——重跑要再花一次钱、结果还不保证是用户要的；覆盖是确定的、可撤销的，
    与复核队列「已按证据定字 → 修订」同一个心智。R2 上那份原始脱敏稿保持不动（留档），
    覆盖只在取稿/导出时应用。

    ⚠️ 对不上一律**原样跳过**：区间里的字不是当初那个替换词，说明稿子已不是当初那份，
    此时少改一处用户看得见（清单上仍标着原样），改错一处却是悄悄毁掉一份脱敏稿。
    """
    if not overrides:
        return dst
    want: dict[tuple[int, int | None, str, str], str] = {}
    for o in overrides:
        oi = o.get("idx")
        want[(int(o["line"]), int(oi) if isinstance(oi, int) else None, o["from"], o["to"])] = o["value"]
    a_lines = src.rstrip("\n").split("\n")
    b_lines = dst.rstrip("\n").split("\n")
    if len(a_lines) != len(b_lines):
        return dst
    out = list(b_lines)
    for i, (a, b) in enumerate(zip(a_lines, b_lines)):
        if a == b:
            continue
        try:
            spans = list(enumerate(_line_spans(a, b)))
        except ValueError:
            continue
        line = b
        # **从后往前**替换：先动前面的区间会让后面所有区间的偏移作废
        for n, (frm, to, _i1, _i2, j1, j2) in sorted(spans, key=lambda sp: -sp[1][4]):
            v = want.get((i, n, frm, to))
            if v is None:
                v = want.get((i, None, frm, to))      # 老数据（无 idx）：整行同名替换一起作用
            if v is None or line[j1:j2] != to:
                continue
            line = line[:j1] + v + line[j2:]
        out[i] = line
    text = "\n".join(out)
    return text + "\n" if dst.endswith("\n") else text


_OVR_MAX = 400          # 覆盖条数上限：一份稿子的改动总数在几十量级，400 是防滥用兜底


def overrides_key(job_id: str) -> str:
    # 挂 postprocess/ 前缀 → 复用其 R2 生命周期，与脱敏产物同寿
    return f"postprocess/{job_id}/redact.overrides.json"


def load_overrides(job_id: str) -> list[dict]:
    """取用户改口。取不到一律当没有——覆盖丢了顶多回到脱敏原样，不该让下载失败。"""
    try:
        data = json.loads(blobstore.get_bytes(overrides_key(job_id)))
    except Exception:  # noqa: BLE001
        return []
    return data if isinstance(data, list) else []


def valid_overrides(body: object) -> list[dict] | None:
    """校验前端提交的覆盖列表；不合法返回 None（调用方回 422）。"""
    if not isinstance(body, list) or len(body) > _OVR_MAX:
        return None
    out = []
    for o in body:
        if not isinstance(o, dict):
            return None
        line, frm, to, val = o.get("line"), o.get("from"), o.get("to"), o.get("value")
        oi = o.get("idx")
        if not isinstance(line, int) or line < 0:
            return None
        if oi is not None and (not isinstance(oi, int) or isinstance(oi, bool) or oi < 0):
            return None
        if not all(isinstance(x, str) for x in (frm, to, val)):
            return None
        if max(len(frm), len(to), len(val)) > MAX_SPAN_WORD_CHARS:
            return None      # 上限要罩得住词级 span（法语词组能超过中文的 40），取两者中大的
        rec = {"line": line, "from": frm, "to": to, "value": val}
        if oi is not None:
            rec["idx"] = oi            # 逐处改口；缺省=老数据，按整行同名一起作用
        out.append(rec)
    return out


def load_transcript_segments(job_id: str) -> list[dict]:
    """取转录稿：修订版（results_edited/）优先，否则原始终稿。取不到抛异常。

    **后处理跑的时候取的就是它**——改动对比要重算出同一份输入，两处必须同一个实现。"""
    try:
        return json.loads(blobstore.get_bytes(f"results_edited/{job_id}.json"))
    except Exception:  # noqa: BLE001  无修订版（常态）→ 原始稿
        pass
    job = jobstore.get_job(job_id)
    if job is None or not job.result_key:
        raise RuntimeError(f"job {job_id} 无转录终稿，无法后处理")
    return json.loads(blobstore.get_bytes(job.result_key))


def redact_input_text(job_id: str, steps: list[str]) -> str:
    """脱敏这一步吃进去的是什么：前面跑过视角转换就是它的产物，否则就是转录稿的问答体。

    ⚠️ 后一种要**重算**，而转录稿此后可能被用户改过（results_edited）——重算出来的
    就不再是当初那份。extract_changes 的行数闸负责识破这种情况（改过多半增删行）。"""
    idx = steps.index("redact")
    if idx > 0:
        prev = steps[idx - 1]
        return blobstore.get_bytes(f"postprocess/{job_id}/{prev}.md").decode("utf-8")
    return segments_to_qa(load_transcript_segments(job_id))

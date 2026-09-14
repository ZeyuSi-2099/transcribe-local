"""P3 输入的「分歧点清单」——把数票这件死活儿从模型手里拿走，交给程序。

**为什么要有它**（2026-08-24 实测，素材 EBG-02ZL 光伏访谈 + 30 处人工裁决金标）：
Opus 拿到四轨并排的 P2 对齐稿后，得自己找出哪里写法不一致、自己数几票对几票。
SKILL.md 白纸黑字写着「主引擎平等一票、不享豁免」，但 479 段读下来它就偷懒了——
参考轨 3:1 反对主轨的地方照样跟主轨走（「及时性」被写成「即时性」等 7 处），
只有主轨给出内容、参考轨全空的孤证也直接裸留（「易损」被写成「预审」）。
DeepSeek 兜底路 2026-08-09 就为此加了 `--conflicts`（那次实测记了一句「opus 跟多数约一半」），
**唯独质量最高的 Claude 路一直裸奔**。本模块补上这一环。

实测收益（同一份 P2，opus/medium 各跑一次，29 处金标）：
    静默漏 5 → 2、该标存疑的 1 → 4、修对 17 持平、**零回归**（12 处本来就对的一处没改坏）。
成本为零：纯字符串比对，不多一次模型调用。

**三条已经花钱买过的教训，改本模块前先读**：
1. **不许给倾向**。曾试过在表里标注「这种形态通常该信主轨」（上游 shape 版），实测更差
   （真改写 3.0→5.5/百段、静默漏 3→13）——模型会照着倾向偷懒，把该纠的也放过。
   清单只陈述事实：谁写了什么。判据顺序写在提示里，倾向一个字都不给。
2. **不许做减法**。曾试过只点名「参考轨互相不一致」那两类形态（R3，138 处 vs 全量 178 处），
   实测存疑 4→2、静默漏 2→4。上游按 DeepSeek 调出来的 `DIVERGE_SHAPES` 筛选**不适用于 Opus**，
   别照搬。当前策略=全量点名，只排除「1 路歧」（多半是那一路自己错了）。
3. **提示词治不了「选票上没有正确答案」**。曾试过加「联网证据须能排他」规则（R4），
   模型正确执行了却仍选错——四轨无一听出「数采箱」。那类错归引擎侧（双主轨方案），不归这里。
4. **「可用」只能有一个定义**（2026-08-26 买的教训）。分歧与孤证曾各用各的判据：分歧不计
   回落串的票（对），孤证却把回落串算作「在场」（错）。于是「所有参考轨都是回落」的段落
   **两张表都进不去、被静默丢掉**——而那恰恰是最该给人看的位置。两处判定共用一个 `usable`
   定义之后这个缝就没了。改本模块时，任何一处新增筛选都要问一句：另一处要不要同步？

⚠️ DeepSeek 兜底路（`vendor/.../Phase3_Merge_DeepSeek_Batched.py` 的 `conflict_of`）另有一份实现，
**判据是不同口径、不必也不该对齐**：那份只点「参考轨里≥2 条**一致地**反对主轨」（＝上游 `majority` 模式，
DeepSeek 侧四项指标最优），本模块点「与主轨不同的参考轨≥2 路」（含互异形态）并另列孤证一节
——两条路各自实测调出来的。改本模块只对着 `tests/test_conflicts.py`，别去照抄那边。
"""
from __future__ import annotations

import re
from pathlib import Path

# 主轨行：`[00:01.4 - 00:07.8] [ELV] M: 文本`；参考轨行：缩进的 `[DB] 文本`
_MAIN_TXT = re.compile(r"^\[[^\]]+\]\s*\[([A-Z0-9]+)\]\s*[MRX]?\s*:?\s*(.*)$")
_REF_TXT = re.compile(r"^\[([A-Z0-9]+)\]\s*(.*)$")
_HEAD = re.compile(r"^\[([^\]]+)\]")
_PUNCT = re.compile(r"[，。！？、：；…—\-“”‘’（）()《》\.,!?:;\s]")

# 短句（「嗯」「对」）上的分歧没有判读价值，点名了只会稀释注意力
_MIN_CHARS = 6

# 孤证**另有一道更高的门槛**，而且按「可判读单位」算、不按字符算（2026-08-26 实测加）。
# 两个理由：
# ① **代价不对等**。分歧只是让模型多判一次，点错了成本很小；而孤证的处置指令是
#    「一律不得裸留、必须标 `[❓]`」⇒ 它**直接变成用户面前的复核卡片**。噪音进到这里
#    才是真的稀释注意力（文件头教训 1、2 说的都是这件事）。
# ② **`_MIN_CHARS` 是照中文调的**：6 个汉字是一句话，6 个英文字符只是两个虚词。
#    2026-08-26 在真实 P2 留档上量过：孤证判据统一之后英语单多出 3 处，
#    全是 `Yeah. And-` / `Yes. Sorry.` / `Or it's-` 这类语气词碎片（各 2 个单位）。
# 「单位」= 一个 CJK 字算一个、一串拉丁字母/数字算一个（≈ 一个词）。中文侧不受影响
# （已被 `_MIN_CHARS>=6` 挡在前面，6 字必 ≥6 单位）。
# ⚠️ **这道门槛只加在「新扩的那一类」上**（参考轨在场但全是回落串），原有的「一行参考轨都
# 没有」不受它约束——否则像 `STRABAG and Goldbeck`（3 单位、两个专名）这种短而实的英文孤证
# 会被误杀，那正是孤证最该抓的东西。宁可这道门槛管得窄，也不许它把老行为改坏。
_LONE_MIN_UNITS = 4
_UNIT = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]|[0-9A-Za-z\u00c0-\u024f\u0370-\u04ff]+")

# P2 精修对不上时的回落标记（Phase2_Align_Match.ALIGN_FALLBACK_MARK）。
# 这类参考轨**内容是真的、边界不可信**——串里混着邻居的话。
#
# 【2026-08-26 订正，两处判定改为共用同一个「可用」定义】
# 此前是分开的：分歧不计它的票（对，逐字比必然不同，计进去是造假分歧），
# 但孤证把它**算在场**（错）。于是「所有参考轨都是回落」的那种段落，**既不进分歧表、
# 也不进孤证表，被静默丢掉**——而那正是最该给人看的位置：主轨说了一句，没有任何一路
# 给得出可比对的佐证。
#
# 订正依据就是当初判它「算在场」的那个样本本身（32:37.6）：主轨 ELV 写「他撤，他撤出了」，
# 三路回落串都是「他跳槽了…辞职不干」——**它们不是在佐证主轨，是在反对主轨**。
# 而 CLAUDE.md 记着「跳槽↔他撤出了」正是 ELV 整档直传那轮**稳定改对的三处之一**，
# 即那里主轨确实错了。当年把它判成「假孤证」，判错了。
#
# ⇒ 规则统一为一句话：**不可靠到不能参与计票的证据，也不该被算作佐证。**
_FALLBACK_MARK = "⟨未精确对齐⟩"


def _flat(s: str) -> str:
    """只去标点空白——这里要的是「写法是否逐字相同」，不做语气词/叠字归一。"""
    return _PUNCT.sub("", s or "")


def _units(s: str) -> int:
    """可判读单位数：一个 CJK 字算一个，一串拉丁/西里尔/希腊字母数字算一个（≈ 一个词）。
    「6 个字符」在中文和英文里根本不是一个量级，按单位算才跨语种可比。"""
    return len(_UNIT.findall(s or ""))


def _split_blocks(text: str) -> list[list[str]]:
    """P2 对齐稿 → 段块。每块首行是主轨行（带时间码），其后是缩进的各参考轨行。"""
    blocks: list[list[str]] = []
    for ln in text.split("\n"):
        if re.match(r"^\[\d", ln):
            blocks.append([ln])
        elif ln.strip() and blocks:
            blocks[-1].append(ln)
    return blocks


def _parse(block: list[str]) -> tuple[str | None, str | None, list[tuple[str, str]]]:
    """→ (时间码, 主轨文本, [(参考轨代号, 文本)])。识别不出的返回 (None, None, [])。"""
    head = _HEAD.match(block[0])
    ts = head.group(1).split(" - ")[0] if head else None
    main, refs = None, []
    for ln in block:
        s = ln.strip()
        m = _MAIN_TXT.match(s)
        if m and main is None:
            main = m.group(2)
            continue
        m2 = _REF_TXT.match(s)
        if m2 and m2.group(2) not in ("", "/"):
            refs.append((m2.group(1), m2.group(2)))
    return ts, main, refs


def analyze(text: str) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """→ (分歧行, 孤证行)。

    两者共用同一个「可用」定义：**这一路在该处给得出可比对的证据**
    （非空、且不是 `_FALLBACK_MARK` 回落串）。

    分歧 = 主轨与 ≥2 路**可用**参考轨写法不同（排除「1 路歧」：只有一路反对，多半是那一路自己错了）。
    孤证 = 主轨给出了实内容，而该处**一路可用证据都没有**——这类最危险：
           界面上看不出它只有一路来源，用户会当成定论。
           ⚠️ 「有声音但全是回落」也算孤证（2026-08-26 订正，原委见 `_FALLBACK_MARK` 注释）：
           回落串边界不可信、不能拿来逐字比对，那它就不足以充当佐证。
    """
    conflicts: list[tuple[str, str, str]] = []
    lone: list[tuple[str, str]] = []
    for block in _split_blocks(text):
        ts, main, refs = _parse(block)
        if not ts or main is None or len(_flat(main)) < _MIN_CHARS:
            continue
        # 「可用」= 这一路在该处给得出**可比对**的证据。两处判定共用这一个定义（见文件头教训 4）。
        usable = [(k, v) for k, v in refs if _FALLBACK_MARK not in v and _flat(v)]
        if not usable:
            # 没有任何一路给得出可比对的证据 → 孤证。
            # ⚠️ 门槛只加在**新扩的那一类**（refs 非空但全是回落串）上，原判据（一行参考轨
            # 都没有）一个字不动 ⇒ **零回归**：改动前会被点名的，改动后一处不少。
            # 只给新的一类设门槛是因为它的噪音形态已经实测出来了（见 _LONE_MIN_UNITS）。
            if refs and _units(main) < _LONE_MIN_UNITS:
                continue
            lone.append((ts, main))
            continue
        diff = [(k, v) for k, v in usable if _flat(v) != _flat(main)]
        if len(diff) >= 2:
            conflicts.append((ts, main, " ／ ".join(f"{k}:{v}" for k, v in diff)))
    return conflicts, lone


def build_section(text: str, max_chars: int = 130) -> str:
    """生成贴在 P2 稿最前面的清单节；无分歧无孤证时返回空串（调用方原样用原文件）。

    措辞是实测过的那一版，改动前先读文件头三条教训。开头明写「不是转录内容、禁止进入终稿正文」——
    否则模型可能把这张表当成待融合的素材抄进稿子里。
    """
    conflicts, lone = analyze(text)
    if not conflicts and not lone:
        return ""
    out = ["> 【融合前须知 · 分歧点清单（由程序逐段比对算出，不是转录内容、不是建议答案，禁止进入终稿正文）】", ">"]
    if conflicts:
        out += [
            f"> 一、下列 {len(conflicts)} 个时间码上，主轨写法与至少两路参考引擎不同。**每一处都要真判一次**，",
            "> 判完采用哪一方都可以，但不能跳过、也不能因为主轨读起来通顺就默认沿用它。",
            "> 判据顺序：硬证据（术语库/联网核实/上下文自证）> 全引擎计票（主轨只算平等一票）> 仍定不下标 `[❓]`。",
            ">",
            "> | 时间码 | 主轨写法 | 与之不同的参考引擎 |",
            "> |---|---|---|",
        ]
        out += [f"> | {ts} | {m[:max_chars]} | {d[:max_chars]} |" for ts, m, d in conflicts]
        out.append(">")
    if lone:
        out += [
            f"> 二、下列 {len(lone)} 个时间码上，**只有主轨给出内容，没有任何一路参考引擎给得出可比对的证据**（孤证：为空、或仅有对不齐的回落串）。",
            "> 孤证一律不得裸留：有硬证据才可保留，否则该句整体或关键词必须标 `[❓]`。",
            ">",
            "> | 时间码 | 主轨写法（孤证） |",
            "> |---|---|",
        ]
        out += [f"> | {ts} | {m[:max_chars]} |" for ts, m in lone]
    return "\n".join(out) + "\n\n"


def augmented_input(match_file: str | Path, work_dir: str | Path) -> str:
    """把清单节贴到 P2 稿前面，另存一份交给 claude -p；返回该副本路径。

    **文件名必须与原文件一模一样**——skill 用输入的 base 名命名产物
    （`<base>_P3_Merge_OPUS_<ts>.md`），改名会连带改掉产物名。故只换目录、不换名。
    **不原地改写 P2 稿**：DeepSeek 兜底路读的是同一个文件，它自己算分歧（`--conflicts`），
    多贴一节可能干扰它的分段解析。

    任何一步出问题都回落原文件——监控/增强坏了不许连累转录（同 p3_health 的处置原则）。
    """
    src = Path(match_file)
    try:
        text = src.read_text(encoding="utf-8")
        section = build_section(text)
        if not section:
            return str(src)
        dest_dir = Path(work_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        dest.write_text(section + text, encoding="utf-8")
        return str(dest)
    except Exception as e:  # noqa: BLE001 —— 增强失败绝不能让转录失败
        print(f"[conflicts] 生成分歧清单失败，回落原始 P2：{e}", flush=True)
        return str(src)

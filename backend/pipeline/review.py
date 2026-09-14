"""解析 P3 报告（固定骨架表格）→ 结构化复核清单。

报告三节表格（SKILL.md〈输出〉节定义）：
  ## 实体定字（硬证据）  列: 时间码 | 各轨候选 | 终稿 | 依据
  ## 联网核实            列: # | 搜索词 | 为定哪个词(时间码) | 结果 | 建议入库?
  ## 存疑 [❓]           列: 时间码 | 终稿写法 | 原因
occurrences 从终稿 segments 搜 term 的出现行。解析不出的节静默跳过（不编造）。

⚠️ **报告是模型对自己工作的「自述」，不是它实际交付的东西**（2026-08-26）。
这两者会对不上，而且两条 P3 路都会——不是「分了两次过程」造成的：
  · Claude 路：一次会话里先写终稿、再**凭回忆**写报告。实测某单报告写着「20:06.8 定值我标了
    存疑」，而终稿里那个标记根本不存在（这个方向无害：卡片照出）。
  · DeepSeek 路：每批在**同一次回答里**同时吐终稿与存疑表，已经是「一次过程」了，照样漏——
    因为提示词允许「本批没有存疑就连围栏一起省略」，于是「确实没有」与「忘了写」在输出里
    长得一模一样。实测某单终稿打了 3 个 `[❓]`、报告只登记 2 条。
少的那一处是真丢：`orchestrator._strip_marker` 会把正文里的 `[❓]` 全部剥掉（存疑靠复核卡表达，
不靠正文符号），于是**用户既看不到标记、也拿不到卡片**——转错了却指不出来，正是「复核优先」要堵的。
故 `parse_review` 额外收一份 `marked`（终稿里**实际**打了标记的位置，见 `marked_spots`），
报告没登记的就地补一张卡。同一个道理我们在后处理那边已经用过：脱敏改动清单不解析质检报告，
而是逐行对比前后两稿——**自述可以漏，实际发生的事漏不了**。
"""
import re

from . import p3_lang


# 单元格外面套的方括号：只认「整格就是一个时间码/时间区间」这一种，其余方括号一律不动——
# `[❓]` 是存疑标记、有语义，剥掉就把「拿不准」变成了「已定字」。
_TS_BRACKET = re.compile(r"^\[(\d{1,2}:\d{1,2}(?:\.\d+)?(?:\s*-\s*\d{1,2}:\d{1,2}(?:\.\d+)?)?)\]$")


def _cell(c: str) -> str:
    """把一格规范化：剥掉 markdown 强调符与时间码外层方括号。

    为什么要有这层：报告是**模型写的**，同一套提示词下不同引擎的排版习惯不一样——
    2026-08-10 实测 DeepSeek 分批版把时间码写成 `[00:01.4]`、终稿写成 `**智驾**`，
    而 Claude 写的是 `00:01.4` / `智驾`。终稿那格带了 `**` 就在正文里搜不到 →
    整张〈实体定字〉表一条卡都出不来（32 条掉到 6 条），而且**不报任何错**。
    宁可在解析层容错，也不要指望模型每次排版都一致。
    """
    c = c.strip()
    m = _TS_BRACKET.match(c)
    if m:
        return m.group(1).strip()
    return re.sub(r"\*\*|__|`", "", c).strip()


def _section_rows(md: str, header_kw: str) -> list[list[str]]:
    """取某节（## 含 header_kw）下的表格数据行（去表头/分隔行），每行拆成 cells。"""
    rows: list[list[str]] = []
    in_sec = False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("##"):
            in_sec = header_kw in s
            continue
        if not in_sec or not s.startswith("|"):
            continue
        cells = [_cell(c) for c in s.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):  # 跳过 |---|---| 分隔行
            continue
        rows.append(cells)
    return rows[1:] if rows else []   # 丢掉表头行


def _strip_q(s: str) -> str:
    """去掉词里的 [❓] 内部标记（含可选方括号与紧邻空白）。正文也剥了 [❓]（见
    orchestrator._strip_marker），故词必须同步剥、否则 occurrences 搜不到会丢项。"""
    return re.sub(r"\s*\[?❓\]?\s*", "", s).strip()


# 正文里的存疑标记。**必须与 orchestrator._strip_marker 认同一套形状**，否则这边扫得到的
# 位置那边剥不掉（正文残留符号）、或那边剥掉的这边扫不到（白补一场）。
_Q_IN_TEXT = re.compile(r"\[?\s*❓\s*\]?")
# 补卡时用来当 term 的窗口：标记前后各取多少字。取太短认不出是哪句，取太长整卡像在念全文。
# 取完再往句读处收一次边——不收的话卡片标题会从半个词开始（「么多，去年可能是我没记住数量…」），
# 收完是「去年可能是我没记住数量」。纯展示用，收不动就原样留着。
_Q_WINDOW = 12
_Q_BREAK = "，。！？、；：,.!?;: "


def marked_spots(segments: list[dict]) -> list[dict]:
    """终稿正文里**实际打了 `[❓]` 的位置** → [{t, speaker, lineText, term}]（lineText 已剥标记）。

    ⚠️ **必须在 `orchestrator._strip_marker` 之前调用**——标记一剥就再也找不回来了，
    而现在的顺序恰恰是「先剥、再解析报告」。这是本条改动唯一的实现陷阱：调用点挪到剥之后，
    函数照常返回空列表、测试照常绿、生产照常出稿，**只是永远补不出卡来**（造回验过）。

    `term` 取标记前后各 `_Q_WINDOW` 字：卡上要让人一眼认出是哪一句。**不做全文搜**——
    补出来的卡直接绑定它所在的那一段（位置是已知事实，没必要再去正文里找一遍，
    找出来反而可能命中别的段）。
    """
    spots: list[dict] = []
    for s in segments:
        text = s.get("s") or ""
        m = _Q_IN_TEXT.search(text)
        if not m:
            continue
        clean = _Q_IN_TEXT.sub("", text)
        cut = len(_Q_IN_TEXT.sub("", text[: m.start()]))     # 标记在剥完之后落在第几个字
        left = clean[max(0, cut - _Q_WINDOW): cut]
        pos = max((left.rfind(c) for c in _Q_BREAK), default=-1)
        if 0 <= pos < len(left) - 3:                         # 左边从最近的句读之后起（别只剩两三个字）
            left = left[pos + 1:]
        right = clean[cut: cut + _Q_WINDOW]
        ends = [right.find(c) for c in _Q_BREAK if c in right]
        if ends and min(ends) >= 2:                          # 右边到最近的句读为止
            right = right[: min(ends)]
        term = (left + right).strip()
        spots.append({"t": s.get("t", ""), "speaker": s.get("sp") or "",
                      "lineText": clean, "term": term or clean[:_Q_WINDOW * 2]})
    return spots


# 找词时的放宽（2026-09-05，Duner 定，只有这三条）：① 剥 [❓]（两边本就剥了）；② 忽略空格——「理想 L6」「理想L6」
# 同一个词；③ 型号里的汉字数字与阿拉伯互认——「瑞七」「瑞 7」「瑞7」、「M 九」「M9」同一个词，只限「字母或汉字后紧跟
# 一个汉字数字」这种型号形状，数量词（「三十台」）不动。不做模糊相似度、同义词、前缀匹配。只用来**找**这个词在哪几行，
# 终稿一个字不改；找不到照旧不出卡。
_CN_ONE = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_MODEL_CN = re.compile(r"(?<=[A-Za-z\u4e00-\u9fff])([一二三四五六七八九])(?![零一二三四五六七八九十百千万亿])")


def _match_key(s: str) -> str:
    s = re.sub(r"\s+", "", _strip_q(s or ""))
    return _MODEL_CN.sub(lambda m: _CN_ONE[m.group(1)], s)


def _occurrences(term: str, segments: list[dict]) -> list[dict]:
    key = _match_key(term)
    return [
        {"t": s.get("t", ""), "speaker": s.get("sp") or "", "lineText": s.get("s", "")}
        for s in segments
        if key and key in _match_key(s.get("s", ""))
    ]


# 时间码 → 秒。报告是模型写的、终稿行时间来自管线，两边格式都不止一种：
# `00:12:34`(H:M:S) / `12:34`(M:S) / `00:01.4`(M:S.s) / 裸数字秒。解析不出返回 None（调用方回落）。
_TIME_TOKEN = re.compile(r"\d{1,3}:\d{1,2}(?::\d{1,2})?(?:\.\d+)?")


def _to_sec(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    m = _TIME_TOKEN.search(v)
    if not m:
        return None
    head, _, tail = m.group(0).partition(".")
    parts = [int(p) for p in head.split(":")]
    sec = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
    return sec + (float(f"0.{tail}") if tail else 0.0)


def _anchor_span(cell: str) -> tuple[float, float] | None:
    """存疑表第一列 → (起, 止) 秒。单点则两者相等；区间 `4:18 - 4:25` 取首尾。"""
    ts = [t for t in (_to_sec(x) for x in _TIME_TOKEN.findall(cell)) if t is not None]
    return (ts[0], ts[-1]) if ts else None


# 锚点与终稿行时间的最大容许偏差（秒）：报告时间码来自 P2 对齐，与终稿行起点本就有偏移，
# 但偏出太远说明锚错了行 → 宁可回落全篇（只多不少），也不要精确地指错地方。
# ⚠️ 这个数是保守拍的，等 P3 报告留档后拿真实偏移分布校准（见 docs/ROADMAP.md）。
_ANCHOR_MAX_DRIFT_SEC = 120.0


def _anchor_occurrences(occ: list[dict], span: tuple[float, float] | None) -> list[dict] | None:
    """把「全文搜到的所有出现」收窄到报告锚点指的那一处。

    **这是存疑项与实体项的根本分野**：实体定字的语义是「这个词全篇统一写成 X」，全文搜就是它
    「全部替换」的作用域；而存疑的语义是「**第 X 分那一处**拿不准」——拿词全文搜会把全篇无关的
    句子全捞进来（2026-08-12 生产实测：一处「华为」存疑，卡里列出 17 处，用户点开哪条都对不上
    原因）。收窄不了一律返回 None 由调用方回落全篇：最坏等于修复前，绝不会比现在少。
    """
    if not span:
        return None
    start, end = span
    timed = [(o, s) for o, s in ((o, _to_sec(o.get("t"))) for o in occ) if s is not None]
    if not timed:
        return None
    if end > start:   # 区间：一段含糊音跨了终稿好几行 → 落在区间内的都算这一处（唯一的一卡多行）
        inside = [o for o, s in timed if start - 1 <= s <= end + 1]
        if inside:
            return inside
    # 单点：取「不晚于锚点的最后一处」——锚点落在该行说话期间；锚点早于全部出现则取最早那处
    before = [(o, s) for o, s in timed if s <= start + 1]
    pick, psec = before[-1] if before else timed[0]
    return None if abs(psec - start) > _ANCHOR_MAX_DRIFT_SEC else [pick]


def _candidates(cell: str) -> list[str]:
    """「各轨候选」列 → 候选写法列表（/、｜ 等分隔；'无'/空不算）。"""
    parts = [p.strip() for p in re.split(r"[/、｜|，,]", cell)]
    return [p for p in parts if p and p != "无"]


# 依据列是**模型写的自然语言**，「有没有这项证据」常以否定式出现（「无术语库/联网可证」
# 「联网未查到」）。裸子串匹配会把**否定读成肯定**——2026-08-27 西语实测：
#   「多数3:0（AAI/DB/GEM）；无术语库/联网可证，按多数并前后统一」
# 被判成 web，界面给一个**虚构公司名**盖了绿色的「联网 ✓」（＝对外担保"已核实"），
# 而这一处真正成立的证据是「多数」。且 web 是第一分支，还压掉了本该命中的 acoustic。
#
# ⇒ 匹配前先把**否定片段整段摘掉**，再走原来的判定。
# 摘片段而不是「见否定即整句作废」：一句里可能一半否定一半肯定（「术语库未收录，联网核实为
# 官方写法」），只该否掉被否定的那一个证据词。两种语序都要管：
#   否定在前「无术语库/联网可证」——一个否定词管住后面一串（所以证据词那组要可重复）
#   否定在后「联网未查到」「术语库未收录」
# 窗口卡在 4 字以内，避免误伤「联网查到该公司未上市」这类**否定的是内容、不是证据**的写法。
_EVIDENCE_WORD = r"(?:联网|术语库|声学|多数)"
_NEGATED_EVIDENCE = re.compile(
    rf"(?:无|未|没有|不能|无法|缺乏|查不到|找不到|均无|未能)"
    rf"(?:[^；;。，,\n]{{0,4}}?{_EVIDENCE_WORD}[/、／|｜]?){{1,4}}"
    rf"|{_EVIDENCE_WORD}[^；;。，,\n]{{0,4}}?(?:未|没有|无法|不能|查不到|找不到|均无)"
)


def _basis(reason: str, has_glossary: bool = False, tag: str = "") -> str:
    """依据列 → 前端 evidence.basis：联网 / 声学(+术语库) / 术语库 / 引擎证据。

    **没挂术语库的任务绝不能返回带「术语库」的档**。原先 glossary 是兜底分支——依据列只要
    不含「联网/声学/多数」就标成「术语库」，与本单是否挂库毫无关系（2026-08-08 用户实测：
    未挂库的任务里「以旧换新」依据写的是「DB硬证据」，却显示成「术语库」，纯误导）。
    兜底改成中性的 engine（引擎证据），术语库档必须同时满足「依据里确实提到」+「本单真挂了库」。

    **被否定的证据不算证据**（见上方 _NEGATED_EVIDENCE 的来历）：先摘掉否定片段再判。

    `tag` = 依据列开头那个机读标签（`p3_lang.BASIS_TAGS`，2026-08-30 起模型必填）。
    ⚠️ **中文关键词优先、标签只在关键词落空时兜底**，顺序不能反：这样中文报告的判定
    与改动前逐字相同（15 单生产留档回归验过，119 行分布一模一样），而依据列一改成
    别的语言，关键词必然全部落空、正好由标签接手。反过来写就是拿一条没跑过的新路径
    去覆盖一条跑了两个月的老路径，收益为零。"""
    stripped = _NEGATED_EVIDENCE.sub("", reason)
    if "联网" in stripped:
        return "web"
    if "声学" in stripped or "多数" in stripped:
        return "acoustic+glossary" if has_glossary else "acoustic"
    if has_glossary and "术语库" in stripped:
        return "glossary"
    if tag == "web":
        return "web"
    if tag == "majority":
        return "acoustic+glossary" if has_glossary else "acoustic"
    if tag == "term":
        return "glossary" if has_glossary else "engine"
    return "engine"


# 存疑原因含这些关键词 → 建议删除（疑幻觉孤句 / 整段含糊），前端把「删除这句」升为可见按钮
_DELETE_HINTS = ("幻觉", "无中生有", "整段含糊", "决定去留", "确认删除")


# 说话人卡的标题长度：卡片标题是 16px 粗体一行，太长会把「主持人/被访者/多人混合」三个键挤下去。
_SPK_TITLE = 14


def _speaker_items(speaker_marks: list[dict] | None) -> list[dict]:
    """标签被打了 `[❓]` 的行 → 说话人复核卡（一行一张）。

    与存疑卡的差别在**处置**：存疑卡改的是字，这张卡改的是「这段算谁说的」。
    故 term 取行首一小段（让人认出是哪一句），occurrences 只有它自己那一处——
    位置是已知事实，不做全文搜（搜出来只会命中别的段）。
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in speaker_marks or []:
        t, line = m.get("t", ""), m.get("lineText", "")
        if not line or (t, line) in seen:
            continue
        seen.add((t, line))
        reason = "这一段是谁说的没能定下来——请确认归属；一段里混了两个人就选「多人混合」。"
        out.append({
            "type": "speaker",
            "term": line[:_SPK_TITLE] + ("…" if len(line) > _SPK_TITLE else ""),
            "tag": "",
            "reason": reason, "web": None,
            "occurrences": [{"t": t, "speaker": m.get("speaker", ""),
                             "lineText": line, "reason": reason}],
            "confidence": "low",
        })
    return out


def parse_review(report_md: str, segments: list[dict], has_glossary: bool = False,
                 draft_marks: list[dict] | None = None,
                 speaker_marks: list[dict] | None = None) -> list[dict]:
    """report_md = P3 报告；segments = 终稿（已剥 `[❓]`）；draft_marks = 剥之前扫到的标记位置。

    缺省 None＝不做对账，行为与改动前逐字一致（老调用点、测试桩都不受影响）。

    ⚠️ 参数名**不能叫 `marked`**：下面存疑那段循环里早就有个同名局部变量（`marked = []`），
    参数会被它悄悄覆盖，于是对账拿到的是「最后一行存疑的那几处出现」而不是「终稿里的标记」——
    代码照跑、测试照绿、只是永远补不出卡。造回验过（2026-08-26 写这条时真踩了一次）。"""
    items: list[dict] = []

    # 关键实体（实体定字硬证据）——纯数字消歧不算命名实体；
    # 标 [❓] 的 = 无法定字的实体（如两路引擎系统性分歧）→ entity + mustConfirm 进必须确认队列（v3 收口）
    for cells in _section_rows(report_md, "实体定字"):
        if len(cells) < 4:
            continue
        term = cells[2]
        if not term or term == "无":
            continue
        if re.fullmatch(r"[\d.,/、~\-－()（）\s]+", term):   # 纯数字/数字消歧，跳过
            continue
        # 依据列开头的机读标签（`[web]` / `[term]` …）：判 basis 用它，显示时剥掉。
        # 剥在最前面——`must_confirm` 那行也要看这一格，留着标签不影响它，但两处用同一个
        # 变量才不会日后有人改了一处忘了另一处。
        basis_tag, basis_text = p3_lang.split_basis_tag(cells[3])
        must_confirm = "❓" in term or "❓" in basis_text
        term = _strip_q(term)
        if not term:
            continue
        occ = _occurrences(term, segments)
        if not occ:
            continue  # 终稿搜不到该词 → 无处可标、用户无法确认，丢弃（与前端 Result.tsx 一致）
        item = {
            "type": "entity",
            "term": term, "tag": "",
            "reason": basis_text,
            "web": None,
            "occurrences": occ,
            "confidence": "mid" if must_confirm else "high",
            "evidence": {"candidates": _candidates(cells[1]),
                         "basis": _basis(basis_text, has_glossary, basis_tag)},
        }
        if must_confirm:
            item["mustConfirm"] = True
        items.append(item)

    # 联网核实
    for cells in _section_rows(report_md, "联网核实"):
        if len(cells) < 5:
            continue
        query, forword, result = cells[1], cells[2], cells[3]
        term = _strip_q(re.sub(r"\s*[（(].*?[）)]\s*$", "", forword))  # 去末尾时间码括号 + [❓]
        if not term or term == "无":
            continue
        occ = _occurrences(term, segments)
        if not occ:
            continue  # 终稿搜不到该词 → 丢弃
        items.append({
            "type": "web", "term": term, "tag": "",
            "reason": p3_lang.WEB_CARD_REASON,   # 前端按 type=web 覆盖成界面语言，这里只作兜底
            "web": {"count": 1, "searches": [
                {"query": query, "summary": result, "source": "web"}   # 机器键：两条 P3 路（Claude WebSearch / DeepSeek 博查）同一标签，前端翻译成「网页检索」，不点名供应商
            ]},
            "occurrences": occ,
            "confidence": "high",
        })

    # 存疑（疑幻觉 / 整段含糊 → suggestDelete，前端「删除这句」升为可见按钮）
    #
    # 一行 = 一处存疑（SKILL.md〈存疑〉「逐条全列不省」）。同一个词报告里可能有好几行，各带各的
    # 时间码与原因 → **合并成一张卡、卡内多处**（与实体卡形态一致，队列不膨胀），但 reason 与
    # suggestDelete 必须**跟着每一处走**：挂在卡上的话，你点进第 2 处看到的是第 1 处的原因，
    # 而只要有一处像幻觉，另外几处正常句子旁边也会冒出「删除这句」——在不该删的地方递刀子。
    doubts: dict[str, dict] = {}
    for cells in _section_rows(report_md, "存疑"):
        if len(cells) < 3:
            continue
        term = _strip_q(cells[1])
        if not term or term == "无":
            continue
        all_occ = _occurrences(term, segments)
        if not all_occ:
            continue  # 终稿搜不到该词 → 丢弃
        reason = cells[2]
        span = _anchor_span(cells[0])
        picked = _anchor_occurrences(all_occ, span)
        fallback = picked is None
        marked = []
        for o in (all_occ if fallback else picked):
            oc = dict(o)
            oc["reason"] = reason
            if any(k in reason for k in _DELETE_HINTS):
                oc["suggestDelete"] = True
            if cells[0]:
                oc["anchorT"] = cells[0]        # 报告原始时间码：回落时前端提示「报告标注 X:XX」
            if fallback:
                oc["anchorFallback"] = True
            marked.append(oc)
        it = doubts.get(term)
        if it is None:
            doubts[term] = {
                "type": "doubt", "term": term, "tag": "",
                "reason": reason,               # 卡级 = 首处原因（单处卡与导出仍用它）
                "web": None,
                "occurrences": marked,
                "confidence": "low",
            }
        else:
            # 同词的第 2、3 行：并进已有那张卡（**原先是整条丢弃** → 另外几处存疑连原因都没了）
            seen_occ = {(o["t"], o["lineText"]) for o in it["occurrences"]}
            it["occurrences"].extend(o for o in marked if (o["t"], o["lineText"]) not in seen_occ)
    for it in doubts.values():
        it["occurrences"].sort(key=lambda o: _to_sec(o.get("t")) or 0.0)
        # 卡级 suggestDelete 只在**每一处**都是疑幻觉时才立——卡级标记会把删除按钮递到全卡，
        # 混合卡（一处疑幻觉 + 两处正常）由前端按 occurrence 逐处决定
        if all(o.get("suggestDelete") for o in it["occurrences"]):
            it["suggestDelete"] = True
        items.append(it)

    # ── 存疑对账：终稿标了 `[❓]`、报告〈存疑〉表却没登记的，就地补一张卡 ──
    # 见模块头：报告是模型的自述，会漏；正文里的标记是它**实际交付**的东西，不会漏。
    # 只按「这一段有没有存疑卡」判重，**不与实体卡比**：实体卡讲的是「这个词全篇写成 X」，
    # 与「第 X 分这一处拿不准」是两件事，同一段同时有这两张卡是合理的。
    # 判重用段的时间码而不是词——补出来的 term 是标记周围的窗口文本，与报告里的写法本就不同。
    if draft_marks:
        covered = {o.get("t") for it in items if it["type"] == "doubt" for o in it["occurrences"]}
        for spot in draft_marks:
            if spot["t"] in covered:
                continue
            reason = p3_lang.UNREPORTED_REASON
            items.append({
                # `unreported` = 这句话是**我们**写的、不是模型写的 → 前端据此换成界面语言。
                # 光看 type 分不出来：它和真存疑卡都是 doubt。
                "type": "doubt", "term": spot["term"], "tag": "", "unreported": True,
                "reason": reason, "web": None,
                "occurrences": [{"t": spot["t"], "speaker": spot["speaker"],
                                 "lineText": spot["lineText"], "reason": reason,
                                 "anchorT": spot["t"]}],
                "confidence": "low",
            })
            covered.add(spot["t"])   # 同一段两个标记只补一张卡

    # 去重：同 (type, term) 只留首条
    seen: set = set()
    deduped: list[dict] = []
    for it in items:
        key = (it["type"], it["term"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # 跨表去重：同一个词既被「实体定字」标成必须确认、又被写进「存疑」表——两张表说的是
    # 同一件事，只是措辞不同（2026-08-07 生产实测：「智界」出了两张卡，理由几乎一字不差）。
    # 让用户为同一个判断拍两次板是纯粹的重复劳动，队列计数也会虚高。
    # 只留实体卡：它多一个「全部替换为 X」的批量能力，且带候选词证据；存疑卡的删除建议继承过去。
    must_terms = {it["term"] for it in deduped if it.get("mustConfirm")}
    merged: list[dict] = []
    for it in deduped:
        if it["type"] == "doubt" and it["term"] in must_terms:
            for target in merged:
                if target.get("mustConfirm") and target["term"] == it["term"]:
                    if it.get("suggestDelete"):
                        target["suggestDelete"] = True
                    break
            continue
        merged.append(it)

    # 同一个词既进「实体定字」又进「联网核实」两张表 → 审计区出现**两张卡说同一个决定**，
    # 依据标签还不一样（一个 web、一个靠 basisChip 推断）。2026-08-29 扫 15 单生产留档：
    # 3 单中招，重复的全是品牌名（上能 / VinFast / SANY）——正是最该一眼看明白的那类词。
    # 留实体卡（它带 `实体定字` 表里那条写实的依据），把联网卡的搜索证据**接过来**：
    # 那份 searches 是「点开看它查了什么」的唯一来源，丢了就只剩一句结论。
    ent_terms = {it["term"] for it in merged if it["type"] == "entity"}
    out: list[dict] = []
    for it in merged:
        if it["type"] == "web" and it["term"] in ent_terms:
            for target in out:
                if target["type"] == "entity" and target["term"] == it["term"]:
                    target.setdefault("web", None)
                    if it.get("web") and not target.get("web"):
                        target["web"] = it["web"]
                    break
            continue
        out.append(it)
    merged = out
    # 说话人卡挂在最后。**今天挂在哪儿结果都一样**——上面两道去重的键都带 type，
    # 而 speaker 是独立的 type，跟实体撞不上（这条造回验过：挪到去重之前，测试不红）。
    # 挂在后面纯粹是让它不受那两道逻辑的影响：它的 term 是句首截来的一段，
    # 一旦哪天去重改成只按 term，短句就会跟同名实体撞掉。守卫 test_说话人卡不被实体去重误伤
    # 是**朝前防**的，不是在修一个现存的 bug。它自己按 (t, lineText) 去过重。
    merged.extend(_speaker_items(speaker_marks))
    return merged

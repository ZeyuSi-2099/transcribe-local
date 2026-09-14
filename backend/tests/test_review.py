from pipeline.review import parse_review, _basis

REPORT = """# P3 Merge 报告

- 输入：x_P2_Match.md

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:00:23 | 畅享90max / 畅想90 | 畅享 90 Max | 联网核实=华为畅享系列产品 |
| 00:00:40 | Mate80 / Mate90 | Mate 80 [❓] | 两路引擎系统性分歧，无法定字 |

## 联网核实

共 1 次搜索

| # | 搜索词 | 为定哪个词(时间码) | 结果 | 建议入库? |
|---|---|---|---|---|
| 1 | 畅享 90 Max 华为 | 畅享 90 Max (00:00:23) | 华为畅享系列智能手机，确认存在 | 是 |

## 存疑 [❓]

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:00:33 | ND 省包 | 声学与主引擎不一致，术语库未命中 |
| 00:00:50 | 我说这有意思 | 疑幻觉：声学三路全空，仅主引擎给出孤句 |
"""

SEGMENTS = [
    {"t": "00:00:23", "s": "畅享系列，畅享 90 Max 吗？", "sp": "被访者"},
    {"t": "00:00:33", "s": "主要是那个 ND 省包 渠道", "sp": "被访者"},
    {"t": "00:00:40", "s": "到了 Mate 80 这一代", "sp": "被访者"},
    {"t": "00:00:50", "s": "我说这有意思。", "sp": "被访者"},
]


def test_parse_entity_web_doubt():
    review = parse_review(REPORT, SEGMENTS)
    by_type = {t: [r for r in review if r["type"] == t] for t in ("entity", "web", "doubt")}

    ent = by_type["entity"]
    assert any(r["term"] == "畅享 90 Max" for r in ent)
    e = next(r for r in ent if r["term"] == "畅享 90 Max")
    assert "联网核实" in e["reason"]
    assert e["occurrences"][0]["t"] == "00:00:23"   # occurrences 从 segments 搜出
    # 「各轨候选」列不再丢弃：进 evidence；依据含「联网」→ basis=web
    assert e["evidence"] == {"candidates": ["畅享90max", "畅想90"], "basis": "web"}

    # [❓] 实体 = 无法定字 → entity + mustConfirm（进必须确认队列），不再转存疑
    m = next(r for r in ent if r["term"] == "Mate 80")
    assert m["mustConfirm"] is True and m["confidence"] == "mid"

    # 同词既进「实体定字」又进「联网核实」→ **只出一张卡**，搜索证据接到实体卡上。
    # 出两张的话审计区会拿两个不同的依据标签讲同一个决定（2026-08-29 生产 15 单里 3 单中招，
    # 重复的全是品牌名：上能 / VinFast / SANY）。
    assert not [r for r in by_type["web"] if r["term"] == "畅享 90 Max"], "同词不该再出第二张联网卡"
    assert e["web"]["searches"][0]["query"] == "畅享 90 Max 华为"
    assert "华为畅享系列" in e["web"]["searches"][0]["summary"]

    doubt = by_type["doubt"]
    assert any(r["term"] == "ND 省包" and r["confidence"] == "low" for r in doubt)
    nd = next(r for r in doubt if r["term"] == "ND 省包")
    assert "suggestDelete" not in nd               # 普通存疑不带删除建议
    hall = next(r for r in doubt if r["term"] == "我说这有意思")
    assert hall["suggestDelete"] is True           # 疑幻觉 → 删除升为可见按钮


def test_strips_question_marker_from_terms():
    # 正文已剥 [❓]（orchestrator._strip_marker），故 review 的实体/存疑词必须同步剥 [❓]，
    # 否则在干净正文里搜不到 → occurrences 空 → 丢项。
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:00:05 | Koya / Kaya | Koya Classic [❓] | 两路分歧，无法定字 |

## 联网核实

无

## 存疑 [❓]

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:00:09 | 沃达丰 [❓] | 听不清 |
"""
    segments = [   # 段落是「已剥 [❓]」的干净文本（与 orchestrator 输出一致）
        {"t": "00:00:05", "s": "就是这个 Koya Classic。", "sp": "被访者"},
        {"t": "00:00:09", "s": "你看沃达丰这是官网。", "sp": "被访者"},
    ]
    review = parse_review(md, segments)
    terms = {r["term"] for r in review}
    assert "Koya Classic" in terms          # 实体词剥 [❓] 后搜到干净正文
    assert "沃达丰" in terms                  # 存疑词剥 [❓] 后搜到（不丢项）
    assert all("❓" not in r["term"] for r in review)
    koya = next(r for r in review if r["term"] == "Koya Classic")
    assert koya["mustConfirm"] is True       # [❓] 实体 → 必须确认队列


def test_empty_sections_are_tolerated():
    # 「无」或缺表格不报错、不编造
    md = "# P3 Merge 报告\n\n## 实体定字（硬证据）\n\n无\n\n## 联网核实\n\n共 0 次，无联网搜索\n\n## 存疑 [❓]\n\n无\n"
    assert parse_review(md, []) == []


def test_drops_items_whose_term_not_in_transcript():
    # 终稿搜不到的词（如融合精修后已改写/删除）→ 不进清单：
    # 这种项 occurrences 为空，前端无法定位/确认，留着只会成为空 occurrences 崩源
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:00:10 | 甲 / 乙 | 终稿里没有的词 | 术语库 |

## 联网核实

| # | 搜索词 | 为定哪个词(时间码) | 结果 | 建议入库? |
|---|---|---|---|---|
| 1 | 也搜不到 | 也搜不到 (00:00:15) | 某结果 | 是 |

## 存疑 [❓]

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:00:20 | 同样搜不到 | 声学不一致 |
"""
    segments = [{"t": "00:00:10", "s": "这段终稿里啥相关词都没有", "sp": "A"}]
    assert parse_review(md, segments) == []  # 三项的 term 都不在 segments → 全部丢弃


def test_basis_majority_wording():
    # 上游 SKILL 措辞 声学N:0→多数N:0，两种都要认作声学档
    assert _basis("多数3:0", has_glossary=True) == "acoustic+glossary"
    assert _basis("声学3:0", has_glossary=True) == "acoustic+glossary"   # 兼容历史报告
    # 没挂库就不许带「术语库」后缀（2026-08-08：那是纯误导）
    assert _basis("多数3:0") == "acoustic"


def test_same_term_in_both_tables_yields_one_card():
    """同一个词既进「实体定字」的必须确认、又进「存疑」→ 只出一张卡。

    2026-08-07 生产实测（华为渠道访谈）：「智界」出了两张卡，两条理由几乎一字不差
    （"三轨同误：'智享界'非真实品牌…"），队列显示 8 项其实只有 7 件事要拍板。
    去重键原本是 (type, term)，跨表的同名项挡不住。
    """
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:27:23 | 智界 / 享界 | 智界 [❓] | 三轨同误"智享界"，五界中仅智界/享界近音，无法定字 |

## 联网核实

无

## 存疑

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:27:23 | 智界 | 三轨同误"智享界"，非真实品牌，智界/享界近音无法定 |
"""
    segments = [{"t": "00:27:23", "s": "就是智界的话，一般是税后两千两百五十块钱。", "sp": "被访者"}]
    review = parse_review(md, segments)
    zhijie = [r for r in review if r["term"] == "智界"]
    assert len(zhijie) == 1, f"同一个判断出了 {len(zhijie)} 张卡，用户要拍两次板"
    assert zhijie[0]["type"] == "entity"       # 留实体卡：它多一个「全部替换」的批量能力
    assert zhijie[0]["mustConfirm"] is True


def test_doubt_delete_hint_survives_the_merge():
    """合并时别把存疑卡的删除建议丢了——那是「疑幻觉」段落的唯一出口。"""
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:01:00 | 甲说法 / 乙说法 | 某词 [❓] | 两路分歧，无法定字 |

## 联网核实

无

## 存疑

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:01:00 | 某词 | 疑似幻觉，建议删除整句 |
"""
    segments = [{"t": "00:01:00", "s": "这里有个某词在句子里。", "sp": "A"}]
    review = parse_review(md, segments)
    hit = [r for r in review if r["term"] == "某词"]
    assert len(hit) == 1
    assert hit[0].get("suggestDelete") is True


def test_unrelated_doubt_and_entity_both_kept():
    """只合并「同一个词」——不同词各自成卡，别误伤。"""
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:01:00 | 甲 / 乙 | 词A [❓] | 两路分歧 |

## 联网核实

无

## 存疑

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:02:00 | 词B | 听不清 |
"""
    segments = [
        {"t": "00:01:00", "s": "这句里有词A。", "sp": "A"},
        {"t": "00:02:00", "s": "这句里有词B。", "sp": "B"},
    ]
    review = parse_review(md, segments)
    assert {r["term"] for r in review} == {"词A", "词B"}


def test_存疑按锚点收窄_不再全文捞同词():
    """存疑 = 「第 X 分那一处拿不准」，不是「这个词全篇都拿不准」。

    2026-08-12 生产实测：一处「华为」存疑（四路听成 孩子/华测/化验测），卡里却列出全篇 17 处
    「华为」，用户点开哪条都跟原因对不上。根因＝解析丢掉了报告第一列的时间码，改用词全文搜。
    """
    md = (
        "## 存疑\n"
        "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n"
        "| 00:04:18 | 华为 | ELV+FA→孩子(2票)，DB→华测(1票)，无多数 |\n"
    )
    segs = [
        {"t": "00:00:11", "s": "我们是华为委托的第三方公司。", "sp": "主持人"},
        {"t": "00:04:15", "s": "就同等配置，华为指定是最贵的。", "sp": "被访者"},
        {"t": "00:04:20", "s": "但有些政府单位啊，华为这块要走招投标。", "sp": "被访者"},
        {"t": "00:09:52", "s": "那这个其实应该华为去做一些协调。", "sp": "被访者"},
    ]
    items = parse_review(md, segs)
    assert len(items) == 1
    occ = items[0]["occurrences"]
    assert len(occ) == 1, f"应只锚定一处，实得 {len(occ)} 处（又在全文捞同词）"
    assert occ[0]["t"] == "00:04:15"        # 取「不晚于锚点的最后一处」＝锚点落在这一句期间
    assert occ[0]["reason"] == items[0]["reason"]


def test_同词多处存疑_合并一张卡且每处各带原因():
    """同一个词多处存疑：合并成一张卡（队列不膨胀），但每一处带自己的原因。

    原先去重键是 (type, term) 且**整条丢弃**第 2、3 行 → 另外两处存疑连原因都没了（少），
    同时首行的词被全文搜捞出一堆无关句子（多）。这个用例把「多」和「少」一起钉住。
    """
    md = (
        "## 存疑\n"
        "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n"
        "| 00:01:00 | 某词 | 原因甲：主轨与参考轨分歧 |\n"
        "| 00:03:00 | 某词 | 原因乙：语速过快，声学不可辨 |\n"
    )
    segs = [
        {"t": "00:01:00", "s": "这句里有某词在。", "sp": "A"},
        {"t": "00:02:00", "s": "这句也有某词但没人怀疑它。", "sp": "B"},
        {"t": "00:03:00", "s": "这句里的某词是第二处存疑。", "sp": "A"},
    ]
    items = parse_review(md, segs)
    assert len(items) == 1, "同词应合并成一张卡，不是两张"
    occ = items[0]["occurrences"]
    assert [o["t"] for o in occ] == ["00:01:00", "00:03:00"], "只留被点名的两处，02:00 那句是无辜的"
    assert occ[0]["reason"].startswith("原因甲") and occ[1]["reason"].startswith("原因乙")


def test_删除建议跟着每一处走_不递刀子给正常句():
    """混合卡（一处疑幻觉 + 一处普通存疑）：删除按钮只能出现在疑幻觉那一处。

    suggestDelete 挂在卡级的话，另一处完全正常的句子旁边也会冒出醒目的「删除这句」。
    """
    md = (
        "## 存疑\n"
        "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n"
        "| 00:01:00 | 某词 | 声学不一致，建议复核 |\n"
        "| 00:03:00 | 某词 | 疑幻觉：三路全空，仅主引擎给出孤句 |\n"
    )
    segs = [
        {"t": "00:01:00", "s": "这句里有某词在。", "sp": "A"},
        {"t": "00:03:00", "s": "这句里的某词疑似编的。", "sp": "A"},
    ]
    it = parse_review(md, segs)[0]
    assert "suggestDelete" not in it["occurrences"][0]      # 正常那处：不递刀子
    assert it["occurrences"][1]["suggestDelete"] is True    # 疑幻觉那处：升为可见按钮
    assert "suggestDelete" not in it, "卡级标记会把删除按钮递到全卡，混合卡不许立"


def test_锚点解析不出或偏太远_回落全篇而不是丢卡():
    """保险丝：收窄不了就退回修复前的行为（全文搜），最坏等于现状，绝不比现在少。"""
    no_ts = (
        "## 存疑\n"
        "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n"
        "| — | 某词 | 时间码缺失 |\n"
    )
    far = (
        "## 存疑\n"
        "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n"
        "| 01:30:00 | 某词 | 锚点偏出容差外 |\n"
    )
    segs = [
        {"t": "00:01:00", "s": "这句里有某词在。", "sp": "A"},
        {"t": "00:03:00", "s": "这句里也有某词。", "sp": "A"},
    ]
    for md in (no_ts, far):
        occ = parse_review(md, segs)[0]["occurrences"]
        assert len(occ) == 2, "收窄不了应回落全篇，不是丢卡也不是只留一处"
        assert all(o.get("anchorFallback") for o in occ), "回落要留痕，前端才能提示未精确定位"


def test_区间锚点_覆盖跨行的一段含糊音():
    """报告给区间（一段含糊音跨了终稿好几行）→ 这一处天然是多行，是「一卡多处」的唯一例外。"""
    md = (
        "## 存疑\n"
        "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n"
        "| 00:02:00 - 00:02:30 | 某词 | 整段含糊，需决定去留 |\n"
    )
    segs = [
        {"t": "00:01:00", "s": "区间外的某词。", "sp": "A"},
        {"t": "00:02:05", "s": "区间内第一句某词。", "sp": "A"},
        {"t": "00:02:25", "s": "区间内第二句某词。", "sp": "A"},
        {"t": "00:05:00", "s": "区间外的某词。", "sp": "A"},
    ]
    occ = parse_review(md, segs)[0]["occurrences"]
    assert [o["t"] for o in occ] == ["00:02:05", "00:02:25"]


def test_实体卡不受影响_仍是全篇统一定字():
    """实体定字的全文搜是**特性**不是 bug——「全部替换为 X」的作用域就是它。别一起改了。"""
    md = (
        "## 实体定字（硬证据）\n"
        "| 时间码 | 各轨候选 | 终稿 | 依据 |\n|---|---|---|---|\n"
        "| 00:01:00 | 畅想 / 畅享 | 畅享 90 Max | 术语库命中 |\n"
    )
    segs = [
        {"t": "00:01:00", "s": "畅享 90 Max 还可以。", "sp": "A"},
        {"t": "00:04:00", "s": "县乡这边畅享 90 Max 走得最快。", "sp": "A"},
        {"t": "00:09:00", "s": "拿畅享 90 Max 打头阵。", "sp": "A"},
    ]
    assert len(parse_review(md, segs)[0]["occurrences"]) == 3


def test_时间码格式容错():
    """报告与终稿两边格式都不止一种，解析层必须都认；认不出走回落，不许静默丢卡。"""
    from pipeline.review import _to_sec
    assert _to_sec("00:12:34") == 754.0      # H:M:S
    assert _to_sec("12:34") == 754.0         # M:S
    assert _to_sec("00:01.4") == 1.4         # M:S.s（DeepSeek 分批版写法）
    assert _to_sec(1.4) == 1.4               # 裸秒
    assert _to_sec("无") is None and _to_sec(None) is None


def test_basis_never_claims_glossary_when_none_attached():
    """没挂术语库的任务，证据标签不许出现「术语库」。

    2026-08-08 用户实测：未挂库的任务里「以旧换新」依据写的是「语义=新能源国补政策；DB硬证据」，
    却被标成「术语库」——因为 glossary 原本是兜底分支，依据列不含联网/声学/多数就落进去。
    """
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:00:05 | 以旧换新 / 你就换新 | 以旧换新 | 语义=新能源国补政策；DB硬证据 |

## 联网核实

无

## 存疑

无
"""
    segs = [{"t": "00:00:05", "s": "那个以旧换新的政策。", "sp": "A"}]
    no_lib = parse_review(md, segs, has_glossary=False)
    assert no_lib[0]["evidence"]["basis"] == "engine"      # 中性，不冒充术语库

    # 真挂了库、且依据确实提到术语库时才给这个档
    md_lib = md.replace("DB硬证据", "术语库命中")
    with_lib = parse_review(md_lib, segs, has_glossary=True)
    assert with_lib[0]["evidence"]["basis"] == "glossary"
    # 挂了库但依据没提术语库 → 仍是引擎证据
    assert parse_review(md, segs, has_glossary=True)[0]["evidence"]["basis"] == "engine"


def test_acoustic_basis_drops_glossary_suffix_without_library():
    md = """# P3 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:00:05 | 甲 / 乙 | 某词 | 声学证据 + 三轨多数 |

## 联网核实

无

## 存疑

无
"""
    segs = [{"t": "00:00:05", "s": "这里有某词。", "sp": "A"}]
    assert parse_review(md, segs, has_glossary=False)[0]["evidence"]["basis"] == "acoustic"
    assert parse_review(md, segs, has_glossary=True)[0]["evidence"]["basis"] == "acoustic+glossary"


def test_单元格容错_加粗与时间码方括号():
    """报告是模型写的，排版习惯因引擎而异——解析层必须容错，否则整张表静默失效。

    2026-08-10 实测：DeepSeek 分批版把时间码写成 `[00:01.4]`、终稿写成 `**智驾**`，
    而 Claude 写 `00:01.4` / `智驾`。终稿带 `**` 就在正文里搜不到 → 〈实体定字〉一条卡都不出
    （32 条掉到 6 条），且**不报任何错**。
    """
    md = (
        "## 实体定字（硬证据）\n"
        "| 时间码 | 各轨候选 | 终稿 | 依据 |\n|---|---|---|---|\n"
        "| [00:01.4] | ELV:智驾 / DB:支架 | **智驾** | 语义=访谈主题 |\n"
    )
    segs = [{"s": "智驾这方面是吧？", "t": 1.4, "spk": "M"}]
    items = parse_review(md, segs)
    assert len(items) == 1 and items[0]["term"] == "智驾"


def test_存疑标记不被当成方括号剥掉():
    """`[❓]` 有语义（拿不准），剥掉就把「存疑」变成了「已定字」——只剥时间码那种方括号。"""
    md = (
        "## 实体定字（硬证据）\n"
        "| 时间码 | 各轨候选 | 终稿 | 依据 |\n|---|---|---|---|\n"
        "| 00:01.4 | ELV:中邮 / DB:中油 | **中邮[❓]** | 联网未证实 |\n"
    )
    segs = [{"s": "但中邮这个做得很好。", "t": 1.4, "spk": "R"}]
    items = parse_review(md, segs)
    assert len(items) == 1
    assert items[0]["term"] == "中邮"          # 正文要搜得到，所以 term 本身不带标记
    assert items[0].get("mustConfirm") is True  # 但「拿不准」这件事必须保住


# ══════════════════════════════════════════════════════════════════════════
# 存疑对账（2026-08-26）：终稿标了 [❓]、报告〈存疑〉表没登记的，要补出卡片来。
# 背景见 pipeline/review.py 模块头——报告是模型的「自述」，会漏；正文里的标记是它
# 实际交付的东西，不会漏。少的那一处如果不补，交付前标记就被剥掉了，用户既看不到
# 标记、也拿不到卡片＝转错了却指不出来。
# ══════════════════════════════════════════════════════════════════════════
from pipeline.review import marked_spots  # noqa: E402


_EMPTY_REPORT = "# P3 Merge 报告\n\n## 存疑 [❓]\n\n无\n"


def _report_with(rows: str) -> str:
    return ("# P3 Merge 报告\n\n## 存疑 [❓]\n\n"
            "| 时间码 | 终稿写法 | 原因 |\n|---|---|---|\n" + rows + "\n")


def test_marked_spots_扫出正文里的存疑标记():
    segs = [{"t": "00:10", "sp": "R", "s": "一切正常没有标记。"},
            {"t": "16:37", "sp": "R", "s": "去年数量没这么多，去年可能是我没记住[❓]数量，好像是换了三十多个风扇。"}]
    spots = marked_spots(segs)
    assert [s["t"] for s in spots] == ["16:37"]
    # term 要收到句读边界上——否则卡片标题从半个词开始（「么多，去年可能是我没记住数量」）
    assert spots[0]["term"] == "去年可能是我没记住数量"
    assert "❓" not in spots[0]["lineText"]     # 给前端的正文必须已剥标记


def test_marked_spots_必须在剥标记之前调用():
    """剥完再调＝永远扫不到。这条钉住「已剥的稿子扫出来就是空」这个事实，
    orchestrator 那边的调用顺序由 test_orchestrator 侧的源码顺序守卫兜（见下）。"""
    stripped = [{"t": "16:37", "sp": "R", "s": "去年可能是我没记住数量。"}]
    assert marked_spots(stripped) == []


def test_报告漏登记的标记要补出卡片():
    segs = [{"t": "16:37", "sp": "R", "s": "去年可能是我没记住数量，好像是换了三十多个风扇。"},
            {"t": "24:41", "sp": "R", "s": "这些小件这些都还没有详细的一个明细这种。"}]
    marks = [{"t": "16:37", "speaker": "R", "lineText": segs[0]["s"], "term": "去年可能是我没记住数量"},
             {"t": "24:41", "speaker": "R", "lineText": segs[1]["s"], "term": "这些小件这些都还没有详细的一个明细"}]
    report = _report_with("| 24:41 | 这些小件这些都还没有详细的一个明细 | 无多数、无硬证据 |")

    before = parse_review(report, segs)                       # 不传＝改动前的行为
    after = parse_review(report, segs, False, marks)
    assert {o["t"] for i in before if i["type"] == "doubt" for o in i["occurrences"]} == {"24:41"}
    assert {o["t"] for i in after if i["type"] == "doubt" for o in i["occurrences"]} == {"16:37", "24:41"}
    补 = [i for i in after if i["type"] == "doubt" and i["occurrences"][0]["t"] == "16:37"][0]
    assert "报告里没有写明原因" in 补["reason"]                  # 要说清这张卡是怎么来的
    assert 补["occurrences"][0]["reason"] == 补["reason"]        # 原因跟着每一处走（同存疑卡的既有约定）


def test_报告已登记的标记不再重复补():
    segs = [{"t": "24:41", "sp": "R", "s": "这些小件这些都还没有详细的一个明细这种。"}]
    marks = [{"t": "24:41", "speaker": "R", "lineText": segs[0]["s"], "term": "这些小件这些都还没有详细的一个明细"}]
    report = _report_with("| 24:41 | 这些小件这些都还没有详细的一个明细 | 无多数、无硬证据 |")
    items = parse_review(report, segs, False, marks)
    assert len([i for i in items if i["type"] == "doubt"]) == 1


def test_同一段两个标记只补一张卡():
    segs = [{"t": "08:00", "sp": "R", "s": "前面拿不准后面也拿不准。"}]
    marks = [{"t": "08:00", "speaker": "R", "lineText": segs[0]["s"], "term": "前面拿不准"},
             {"t": "08:00", "speaker": "R", "lineText": segs[0]["s"], "term": "后面也拿不准"}]
    items = parse_review(_EMPTY_REPORT, segs, False, marks)
    assert len([i for i in items if i["type"] == "doubt"]) == 1


def test_不传对账参数时行为与改动前逐字一致():
    segs = [{"t": "24:41", "sp": "R", "s": "这些小件这些都还没有详细的一个明细这种。"}]
    report = _report_with("| 24:41 | 这些小件这些都还没有详细的一个明细 | 无多数、无硬证据 |")
    assert parse_review(report, segs) == parse_review(report, segs, False, None)


def test_basis_否定的证据不算证据():
    """依据列是模型写的自然语言，「无联网可证」不等于「联网可证」。

    2026-08-27 西语生产实测：一个**虚构公司名**（Bodegas Aranchel，主轨其实写对了、
    被 3:0 多数票投掉）的依据写的是「多数3:0（AAI/DB/GEM）；无术语库/联网可证，按多数并
    前后统一」，裸子串匹配命中「联网」→ 界面给它盖了绿色的「联网 ✓」，等于对外担保已核实。

    判据必须只在**错误状态**下才红：所以这里既钉「否定不该命中」，也钉「肯定仍要命中」——
    只删否定分支的话，第二组会红。"""
    # ① 生产原样：联网被否定，真正成立的是「多数」
    assert _basis("多数3:0（AAI/DB/GEM）；无术语库/联网可证，按多数并前后统一") == "acoustic"
    # ② 后置否定（否定词在证据词之后）
    assert _basis("联网未查到对应公司；无多数（2:1:1），按轨数取形态") == "engine"
    assert _basis("术语库未收录该词", has_glossary=True) == "engine"
    # ③ 一句里一半否定一半肯定：只否掉被否定的那个
    assert _basis("术语库未收录，联网核实为官方写法") == "web"
    # ④ 肯定仍要命中（防止「一见否定词就整句作废」的过度修复）
    assert _basis("联网核实：官网写作 XX") == "web"
    assert _basis("多数3:0") == "acoustic"
    assert _basis("术语库已收录该写法", has_glossary=True) == "glossary"
    # ⑤ 否定的是**内容**不是证据——不许误伤
    assert _basis("联网查到该公司未上市，官网写作 XX") == "web"


# ── 说话人卡（2026-08-28）：标签被标疑 → 一行一张卡 ──

_SPK_MARKS = [{"t": "00:00:04", "lineText": "好的，我们大概是从去年三季度开始调整的。", "speaker": "主持人"}]


def test_说话人卡按行出卡且只绑自己那一处():
    """位置是已知事实——不做全文搜（搜出来只会命中别的段）。"""
    items = parse_review("", [], False, None, _SPK_MARKS)
    spk = [i for i in items if i["type"] == "speaker"]
    assert len(spk) == 1
    assert [o["t"] for o in spk[0]["occurrences"]] == ["00:00:04"]
    assert spk[0]["occurrences"][0]["lineText"] == _SPK_MARKS[0]["lineText"]


def test_说话人卡不被实体去重误伤():
    """两道去重的键是 (type, term) / term，而说话人卡的 term 是句首截来的一段——
    跟实体词撞名不是不可能（短句尤其），撞上就整张丢掉。故它挂在所有去重之后。"""
    report = (
        "## 实体定字\n"
        "| 时间 | 原文 | 定字 | 依据 |\n| --- | --- | --- | --- |\n"
        "| 00:00:04 | 好的 | 好的 | 引擎一致 |\n"
    )
    seg = [{"t": "00:00:04", "sp": "主持人", "s": "好的"}]
    items = parse_review(report, seg, False, None,
                         [{"t": "00:00:04", "lineText": "好的", "speaker": "主持人"}])
    assert any(i["type"] == "speaker" for i in items), "说话人卡被同名实体去重吃掉了"


def test_同一行只出一张说话人卡():
    dup = _SPK_MARKS + _SPK_MARKS
    items = parse_review("", [], False, None, dup)
    assert len([i for i in items if i["type"] == "speaker"]) == 1


def test_没有标疑时不多出说话人卡():
    assert [i for i in parse_review("", [], False, None, None) if i["type"] == "speaker"] == []


def test_找词放宽三条_空格_型号汉字数字_存疑标记_只找不改():
    """2026-09-05 Duner 定的三条放宽（review._match_key）：剥 [❓]、忽略空格、型号里的汉字数字与阿拉伯互认。
    不做相似度 / 同义词 / 前缀；数量词不换算。只用来找词在哪几行，终稿一字不改，找不到照旧不出卡。"""
    from pipeline import review
    segs = [{"t": "00:00:01", "sp": "M", "s": "上那个瑞 7 啊，理想L6 我本来就比它贵，M 九能卖三百台"}]
    assert review._occurrences("瑞七", segs) and review._occurrences("瑞7", segs)
    assert review._occurrences("理想 L6", segs)
    assert review._occurrences("M9", segs)
    assert review._occurrences("三百[❓]台", segs)
    assert not review._occurrences("300台", segs)
    assert not review._occurrences("瑞八", segs)
    assert segs[0]["s"] == "上那个瑞 7 啊，理想L6 我本来就比它贵，M 九能卖三百台"

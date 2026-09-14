from pipeline.transcript import parse_transcript_md, to_json, SPEAKER_MAP, UNKNOWN_SPEAKER


SAMPLE = """# 标题随便
[00:05.1 - 00:08.9] M: 大家好，今天聊一个简单的问题。
[01:23.4 - 01:28.0] R: 我先回应一下这个观点。
[72:15.3 - 72:20.0] X: （旁边有人插话）
不是转录行，应该被忽略。
"""


def test_parses_three_segments_ignoring_noise_lines():
    segs = parse_transcript_md(SAMPLE)
    assert len(segs) == 3


def test_start_time_formatted_as_hms():
    segs = parse_transcript_md(SAMPLE)
    assert segs[0].t == "00:00:05"      # 00:05.1 → 5 秒
    assert segs[1].t == "00:01:23"      # 01:23.4 → 83 秒
    assert segs[2].t == "01:12:15"      # 72:15.3 → 4335 秒 = 1h12m15s


def test_speaker_labels_mapped_to_display_names():
    segs = parse_transcript_md(SAMPLE)
    assert segs[0].sp == SPEAKER_MAP["M"]   # 主持人
    assert segs[1].sp == SPEAKER_MAP["R"]   # 被访者
    assert segs[2].sp == SPEAKER_MAP["X"]   # 其他


def test_text_is_stripped():
    segs = parse_transcript_md(SAMPLE)
    assert segs[0].s == "大家好，今天聊一个简单的问题。"


# ── 说话人显示名（2026-08-28）──────────────────────────────────────────
# 三条判据分别对应三种「界面上不该出现的东西」：引擎内部代号、带内部标记的代号、
# 只有一个人时那句支撑不了的角色断言。每条都在改动前造回过 bug 验证会红。

def test_认不出的标签按出现顺序编号成说话人():
    """引擎自造的代号（`Speaker 1`/`嘉宾A`）不许原样透到界面上。"""
    segs = parse_transcript_md(
        "[00:01.0 - 00:02.0] 嘉宾A: 你好\n"
        "[00:03.0 - 00:04.0] Speaker 2: 你也好\n"
        "[00:05.0 - 00:06.0] 嘉宾A: 再说一句"
    )
    assert [x.sp for x in segs] == [f"{UNKNOWN_SPEAKER} 1", f"{UNKNOWN_SPEAKER} 2", f"{UNKNOWN_SPEAKER} 1"]


def test_说话人标签里的存疑标记要剥掉():
    """P3 偶尔把 `[❓]` 打在说话人上。剥之前 `M[❓]` 与 SPEAKER_MAP 一个键都对不上，
    于是俄语那单在界面上显示成 `M[❓]`（2026-08-27 巡检 07）。"""
    segs = parse_transcript_md(
        "[00:01.0 - 00:02.0] M[❓]: 你好\n"
        "[00:03.0 - 00:04.0] R: 你也好"
    )
    assert [x.sp for x in segs] == [SPEAKER_MAP["M"], SPEAKER_MAP["R"]]


def test_整篇只有一个说话人时不许再叫主持人():
    """只有一个人的时候「主持人」是一句我们支撑不了的角色断言——说话人分离退化成
    1 人时正是如此。宁可说「说话人」：它至少是真的，且不带无意义的编号。"""
    segs = parse_transcript_md(
        "[00:00.0 - 00:30.0] M: 前半段\n"
        "[00:30.0 - 01:00.0] M: 后半段"
    )
    assert [x.sp for x in segs] == [UNKNOWN_SPEAKER, UNKNOWN_SPEAKER]


def test_to_json_shape_matches_frontend_samplerow():
    segs = parse_transcript_md(SAMPLE)
    row = to_json(segs)[0]
    assert set(row.keys()) == {"t", "s", "sp"}


def test_start_time_floors_never_rounds_up():
    """起始时间码只许**向下**取整。

    源时间码带一位小数；四舍五入会把一半的句子起点往后挪最多 0.5 秒，
    句级试听就会切掉前几个字的起音（2026-08-21 用户实测）。
    造回 bug（int(round(...))）时 50.5→"00:00:51"，本断言即红。
    """
    segs = parse_transcript_md(
        "[00:50.5 - 00:52.0] M: 尚界这款车\n"
        "[01:09.9 - 01:12.0] R: 尊界也有"
    )
    assert [s.t for s in segs] == ["00:00:50", "00:01:09"]


# ── 格式鲁棒性（2026-08-28）────────────────────────────────────────────
# 解析器一贯是「匹配不上就跳过」。整篇解析不出有护栏（orchestrator 判失败不计费），
# 但**丢几行一直没有任何信号**——稿子照常交付照常计费，只是少了那几句。
# 判据必须是「像转录行却解析不出」，不能是「不匹配的行」：标题、空行、模型写的说明
# 都不匹配，算进来这个数就永远不是 0，也就永远没人看。

def test_丢行对账只数像转录行的那些():
    md = ("# 标题随便\n"
          "[00:01.0 - 00:02.0] M: 好的\n"
          "普通说明行，不该算\n"
          "\n"
          "[00:03.0]  R  时间码没区间、没冒号 —— 像行但解析不出\n"
          "[00:05.0 - 00:06.0] R: 嗯\n")
    from pipeline.transcript import count_unparsed_rows
    assert len(parse_transcript_md(md)) == 2
    assert count_unparsed_rows(md) == 1, "标题/空行/说明行被误算进丢行数"


def test_一行不丢时对账恒为零():
    """反向用例：正常终稿必须报 0，否则这个数会天天冒噪音、很快就没人看了。"""
    from pipeline.transcript import count_unparsed_rows
    assert count_unparsed_rows(SAMPLE) == 0


# ── 说话人标签上的 [❓]：这一段是谁说的没定下来（2026-08-28） ──
from pipeline.transcript import marked_speaker_rows, strip_marker


_SPK_MD = """[00:00.0 - 00:04.2] M: 我们今天想聊聊你们的采购流程。
[00:04.2 - 00:11.8] M[❓]: 好的，我们大概是从去年三季度开始调整的。
[00:11.8 - 00:15.0] R: 那调整之前呢[❓]？
"""


def test_只挑标签被标疑的行_正文标记不算():
    """正文的 [❓] 说「这几个字拿不准」，标签的说「这段是谁说的拿不准」——两件事，别混。"""
    rows = marked_speaker_rows(_SPK_MD)
    assert [r["t"] for r in rows] == ["00:00:04"]


def test_行文本与交付正文逐字相同():
    """occToRow 按 (t, lineText) 定位行；差一个字符就回落成只按时间码找，同秒两行会挑错行。

    ⚠️ 样本里那一行**正文也必须带 [❓]**：不带的话剥不剥都一样，
    这条判据对「忘了剥正文」就毫无反应（第一版就是这么假绿的）。"""
    md = "[00:04.2 - 00:11.8] M[❓]: 这一段是[❓]谁说的都没定。\n"
    segs = parse_transcript_md(md)
    delivered = {s.t: strip_marker(s.s) for s in segs}      # orchestrator 交付前就这么剥
    rows = marked_speaker_rows(md)
    assert rows and "❓" not in rows[0]["lineText"]
    for r in rows:
        assert r["lineText"] == delivered[r["t"]]


def test_标疑的标签仍然照常显示成角色名():
    """卡上要显示「模型当前认为是谁」，所以 M[❓] 得先剥成 M 再映射，不能原样透出。"""
    rows = marked_speaker_rows(_SPK_MD)
    assert rows[0]["speaker"] == SPEAKER_MAP["M"]


def test_没有标疑时不出任何行():
    assert marked_speaker_rows("[00:00.0 - 00:04.2] M: 一切正常。") == []


def test_只有起点时间的行也要认():
    """2026-09-03 生产事故的原行（job 3fd845bf…，P3 走 Claude）：整篇都是 `[MM:SS.d] 标签：正文`，
    没有终点时间。改前 429 行一行不认、整单判失败。终点时间从来没被消费过，放宽不影响带区间的行。"""
    md = (
        "[00:00.1] M：这样的，就是我，我是华为委托第三方，就三月三十一号的时候不是给您打过电话。\n"
        "\n"
        "[00:09.9] R：嗯。\n"
        "[00:38.7] M：呃，就是，都可以，比如说上那个 Z7 啊，然后 M 九、M 六还有 V 九是吧？\n"
        "[00:51.8 - 01:08.8] R：嗯，可能就是，问界的我相对来说要清楚一些。\n"
    )
    segs = parse_transcript_md(md)
    assert [s.t for s in segs] == ["00:00:00", "00:00:09", "00:00:38", "00:00:51"]
    assert segs[2].s.startswith("呃，就是")
    from pipeline.transcript import count_unparsed_rows
    assert count_unparsed_rows(md) == 0

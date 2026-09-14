"""脱敏改动清单的提取与分类。

用例取自 2026-08-20 生产实测（渠道健康度访谈）的真实前后文本——那一单同时命中了
六类里的五类，还包含两处我们当时没预料到的改动（地理、数值年限）。
"""
from app.redact_diff import (apply_overrides, classify, extract_changes,
                             segments_to_qa, valid_overrides)


def _plain(got):
    """去掉逐处上下文，只留「这条改动是什么 + 落在哪几行」——大部分用例关心的就是这些。
    上下文本身另有专门用例。"""
    return [{**{k: v for k, v in c.items() if k != "spots"},
             "at": [sp["line"] for sp in c["spots"]]} for c in got]


def test_segments_to_qa_有无说话人():
    assert segments_to_qa([{"sp": "受访者", "s": "你好"}, {"s": "裸文本"}]) == "受访者：你好\n\n裸文本"


def test_单行单处替换():
    got = extract_changes("我们明辉电器做了十年", "我们XX公司做了十年")
    assert _plain(got) == [{"kind": "company", "from": "明辉电器", "to": "XX公司", "count": 1, "at": [0]}]


def test_同一行两处改动各出一条():
    got = extract_changes("我们在江苏的团队有12个人", "我们在本省的团队有十几个人")
    pairs = {(c["from"], c["to"]): c["kind"] for c in got}
    assert pairs == {("江苏", "本省"): "geo", ("12", "十几"): "number"}


def test_多行同一实体合并计数并按次数降序():
    src = "陈立群带商超\n\n刘俊峰负责专卖店\n\n刘俊峰还管培训"
    dst = "陈立群带商超\n\nXX负责专卖店\n\nXX还管培训"
    got = extract_changes(src, dst)
    assert _plain(got) == [{"kind": "person", "from": "刘俊峰", "to": "XX", "count": 2, "at": [2, 4]}]


def test_没有任何改动返回空():
    assert extract_changes("原样一个字没动", "原样一个字没动") == []


def test_产物末尾多一个换行不算对错了稿():
    """脱敏稿是 Write 写出来的文件、末尾带换行，而重算的输入是内存里拼的字符串、没有。
    2026-08-20 生产实测踩到：63 行 vs 64 行，内容一字不差却被整份丢弃。"""
    got = extract_changes("我在明辉电器上班", "我在XX公司上班\n")
    assert _plain(got) == [{"kind": "company", "from": "明辉电器", "to": "XX公司", "count": 1, "at": [0]}]


def test_行数不等判定对错了稿():
    # 「只替换不增删」是脱敏铁律；行数不等说明比的不是同一份稿（例如用户又改了修订稿）
    assert extract_changes("一行\n\n两行", "一行") == []


def test_整句重写整份丢弃():
    # 单处改动超过 MAX_SPAN → 不是脱敏，是对错了稿
    src = "受访者：" + "甲" * 60
    dst = "受访者：" + "乙" * 60
    assert extract_changes(src, dst) == []


def test_几乎每行都变整份丢弃():
    src = "\n".join(f"第{i}行原文甲" for i in range(10))
    dst = "\n".join(f"第{i}行原文乙" for i in range(10))
    assert extract_changes(src, dst) == []


def test_分类六类():
    assert classify("明辉电器", "XX公司") == "company"
    assert classify("周雅琴", "XX") == "person"
    assert classify("张经理", "X经理") == "person"
    assert classify("13800138000", "XXXXXXXXXXX") == "contact"
    assert classify("a@b.com", "xxxx@xxxx.com") == "contact"
    assert classify("江苏", "本省") == "geo"
    assert classify("上海", "A地") == "geo"
    assert classify("十二个人", "十几个人") == "number"
    assert classify("合作10年", "合作数年") == "number"
    assert classify("智慧园区一期", "XX项目") == "project"


def test_认不出的归其他但仍然列出():
    # 单字改动会把相邻的一个字补回来（见下条）——「甲→乙」显示成「法甲→法乙」
    got = extract_changes("某个说法甲", "某个说法乙")
    assert _plain(got) == [{"kind": "other", "from": "法甲", "to": "法乙", "count": 1, "at": [0]}]


def test_收缩过头的改动补回上下文():
    """「十二个人 → 十几个人」逐字对比的结果是「二 → 几」——没错，但界面上没人看得懂。
    补回一个邻字才成为一条读得懂的记录。这是显示用的，不影响判定。"""
    got = extract_changes("直属团队十二个人", "直属团队十几个人")
    assert _plain(got) == [{"kind": "number", "from": "二个", "to": "几个", "count": 1, "at": [0]}]


def test_补上下文往右不往左_否则同一替换被切成许多条():
    """2026-08-20 生产实见：一份 724 段的稿子里「吉林省 → 本省」被切成十条——
    「：吉林→：本」「像吉林→像本」「在吉林→在本」…每条粘着前面一个不相干的字符，
    各自计数、谁也合并不了。根因是补上下文先往左：而「吉林」右边紧跟的「省」
    正是被剥掉的公共后缀，补回它才成词。往右补 + 跳过标点，十条归一条。"""
    src = "\n\n".join([
        "被访者：我们那边就是吉林省，主要在延吉市。",
        "被访者：像吉林省这种地方，客户少。",
        "被访者：他说吉林省的政策不一样。",
        "被访者：在吉林省做生意，跟敦化那边差不多。",
    ])
    dst = src.replace("吉林省", "本省").replace("延吉市", "B地").replace("敦化", "A地")
    got = extract_changes(src, dst)
    assert {(c["from"], c["to"]): c["count"] for c in got} == {
        ("吉林省", "本省"): 4, ("延吉市", "B地"): 1, ("敦化", "A地"): 1,
    }
    assert all(c["kind"] == "geo" for c in got)   # 补对了上下文，分类也跟着对


def test_标点绝不补进词里():
    # 「：甲 → ：乙」这种条目毫无意义；两边都是标点就宁可保持短
    got = extract_changes("他说：甲。", "他说：乙。")
    assert _plain(got) == [{"kind": "other", "from": "甲", "to": "乙", "count": 1, "at": [0]}]


# ── 用户改口（apply_overrides）──
# 「这一稿脱敏了 N 处」要敢用，前提是改错了能救回来。救的方式是覆盖，不是重跑：
# 重跑要再花一次钱、结果还不保证是用户要的。

def test_保留原词只作用于指定的那一处():
    """同一行里两家不同公司都被换成「XX公司」——把其中一家改回去，绝不能碰另一家。

    这是本功能最危险的一处：退而求其次的 `line.replace(脱敏后, 原词)` 在这里会把
    两家都改回去，等于**悄悄毁掉一份脱敏稿**。造回 replace 写法时本用例即红。
    """
    src = "被访者：我们跟明辉电器和恒达通信都合作过。"
    dst = "被访者：我们跟XX公司和XX公司都合作过。"
    got = apply_overrides(src, dst, [{"line": 0, "from": "明辉电器", "to": "XX公司", "value": "明辉电器"}])
    assert got == "被访者：我们跟明辉电器和XX公司都合作过。"


def test_改成别的写法而不是改回原词():
    src = "我们在江苏做了十年"
    dst = "我们在本省做了十年"
    got = apply_overrides(src, dst, [{"line": 0, "from": "江苏", "to": "本省", "value": "华东"}])
    assert got == "我们在华东做了十年"


def test_同一行多处改口_偏移不串():
    """一行里改两处：先动前面的会让后面的区间作废，所以必须从后往前下刀。"""
    src = "被访者：江苏的明辉电器有12个人"
    dst = "被访者：本省的XX公司有十几个人"
    got = apply_overrides(src, dst, [
        {"line": 0, "from": "江苏", "to": "本省", "value": "江苏"},
        {"line": 0, "from": "明辉电器", "to": "XX公司", "value": "明辉电器"},
    ])
    assert got == "被访者：江苏的明辉电器有十几个人"


def test_稿子对不上就原样返回_绝不乱改():
    # 行数不等 = 已不是当初那份稿；少改一处用户看得见，改错一处却是静默毁稿
    assert apply_overrides("一行\n\n两行", "一行", [{"line": 0, "from": "a", "to": "b", "value": "a"}]) == "一行"
    # 区间里的字不是当初那个替换词 → 跳过这一条
    src, dst = "我在明辉电器上班", "我在XX公司上班"
    assert apply_overrides(src, dst, [{"line": 0, "from": "明辉电器", "to": "YY公司", "value": "明辉电器"}]) == dst


def test_末尾换行保持不变():
    # 产物是 Write 出来的文件、末尾带换行；应用覆盖后仍要带着，否则下一轮对比行数就不等了
    assert apply_overrides("我在明辉电器上班", "我在XX公司上班\n",
                           [{"line": 0, "from": "明辉电器", "to": "XX公司", "value": "明辉电器"}]) == "我在明辉电器上班\n"


def test_覆盖列表校验():
    assert valid_overrides([{"line": 0, "from": "a", "to": "b", "value": "a"}]) == [
        {"line": 0, "from": "a", "to": "b", "value": "a"}]
    assert valid_overrides("不是列表") is None
    assert valid_overrides([{"line": -1, "from": "a", "to": "b", "value": "a"}]) is None
    assert valid_overrides([{"line": 0, "from": "a", "to": "b"}]) is None          # 缺 value
    assert valid_overrides([{"line": 0, "from": "a", "to": "b", "value": "x" * 99}]) is None


# ── 逐处上下文（2026-08-22）──
# 「回到左侧正文」这条路只在脱敏是第一步时走得通；跑在视角转换之后时原话已被重写成叙述，
# 那一句物理上不存在。所以上下文改成**随清单一起给**，与前一步跑了什么无关。

def test_每一处都带着自己的上下文与高亮区间():
    src = "\n\n".join(["主持人：先说说你们公司。",
                       "被访者：我们明辉电器做了十年。",
                       "主持人：规模多大？"])
    dst = src.replace("明辉电器", "XX公司")
    [c] = extract_changes(src, dst)
    [sp] = c["spots"]
    assert sp["line"] == 2 and sp["idx"] == 0
    # 高亮区间必须真的框住那个词——错一位，界面上划亮的就是旁边的字
    assert sp["before"]["text"][sp["before"]["s"]:sp["before"]["e"]] == "明辉电器"
    assert sp["after"]["text"][sp["after"]["s"]:sp["after"]["e"]] == "XX公司"
    # 上下句取的是脱敏后的邻句，且跳过问答体的空行
    assert sp["prev"] == "主持人：先说说你们公司。"
    assert sp["next"] == "主持人：规模多大？"


def test_同一行两处各自带上下文且序号不同():
    src = "被访者：江苏的明辉电器有12个人"
    dst = "被访者：本省的XX公司有十几个人"
    got = extract_changes(src, dst)
    idxs = {(c["from"], c["spots"][0]["idx"]) for c in got}
    assert idxs == {("江苏", 0), ("明辉电器", 1), ("12", 2)}


def test_超长一行只给窗口不给整段():
    """视角转换后一行就是一整段。不截的话几十处能堆成上百 KB 的载荷。"""
    src = "甲" * 400 + "明辉电器" + "乙" * 400
    dst = "甲" * 400 + "XX公司" + "乙" * 400
    [c] = extract_changes(src, dst)
    sp = c["spots"][0]
    assert len(sp["before"]["text"]) < 140 and sp["before"]["text"].startswith("…")
    assert sp["before"]["text"][sp["before"]["s"]:sp["before"]["e"]] == "明辉电器"


def test_逐处改口只动指定的那一处():
    """同一行两家不同公司都成了「XX公司」——只按 (行, 原词, 脱敏后) 认会把两家一起改掉。
    第几处（idx）才是唯一能分开它们的东西。造回「不看 idx」时本用例即红。"""
    src = "被访者：明辉电器和明辉电器是两家。"
    dst = "被访者：XX公司和YY公司是两家。"
    # 两处原词相同、脱敏后不同 → 先确认它们确实被认成两条不同的替换
    got = apply_overrides(src, dst, [{"line": 0, "idx": 0, "from": "明辉电器", "to": "XX公司", "value": "明辉电器"}])
    assert got == "被访者：明辉电器和YY公司是两家。"


def test_同一行同一个词的两处可以分开改口():
    src = "被访者：山西和山西的政策不同。"
    dst = "被访者：本省和本省的政策不同。"
    got = apply_overrides(src, dst, [{"line": 0, "idx": 1, "from": "山西", "to": "本省", "value": "山西"}])
    assert got == "被访者：本省和山西的政策不同。"


def test_老改口没有idx时按整行同名一起作用():
    """加 idx 之前存下的覆盖表不该失效——那会让用户的改口无声消失。"""
    src = "被访者：山西和山西的政策不同。"
    dst = "被访者：本省和本省的政策不同。"
    got = apply_overrides(src, dst, [{"line": 0, "from": "山西", "to": "本省", "value": "山西"}])
    assert got == "被访者：山西和山西的政策不同。"


def test_覆盖列表校验_idx():
    assert valid_overrides([{"line": 0, "idx": 2, "from": "a", "to": "b", "value": "a"}]) == [
        {"line": 0, "from": "a", "to": "b", "value": "a", "idx": 2}]
    assert valid_overrides([{"line": 0, "idx": -1, "from": "a", "to": "b", "value": "a"}]) is None
    assert valid_overrides([{"line": 0, "idx": "x", "from": "a", "to": "b", "value": "a"}]) is None


# ── 词级对比（2026-08-30，外文稿）────────────────────────────────────────────
# 用例取自 2026-08-30 生产实测（interview_fr 法语访谈）：字符级对比把
# 「Schneider Electric → la société XX」切成「Sc → la soc」「chnei → ci」等碎片。
# 判据是**改动段落在词边界上**——只数「切成了几条」的话，碎片凑巧也能凑成对的条数。


def test_法语整词替换不出碎片():
    src = "Je m'appelle Théo Marchand. Je suis directeur chez Schneider Electric depuis six ans."
    dst = "Je m'appelle XX. Je suis directeur chez la société XX depuis six ans."
    got = extract_changes(src, dst)
    pairs = {(c["from"], c["to"]) for c in got}
    assert pairs == {("Théo Marchand.", "XX."), ("Schneider Electric", "la société XX")}


def test_法语多处替换各自落在词边界():
    # 生产碎片「hu → plu」「it → ieurs」「troi → quelque」的原句
    src = "Nous avons embauché huit personnes, dont trois apprentis de l'INSA Lyon."
    dst = "Nous avons embauché plusieurs personnes, dont quelques apprentis d'un institut."
    got = extract_changes(src, dst)
    for c in got:
        # 任何一侧都不许是从词中间剪出来的碎片：区间文本必须等于整词序列
        for side in ("from", "to"):
            assert not c[side] or c[side][0] not in " " and c[side][-1] not in " "
    pairs = {(c["from"], c["to"]) for c in got}
    assert ("huit", "plusieurs") in pairs
    assert ("trois", "quelques") in pairs
    assert not any(f in ("hu", "it", "troi", "Sc", "chnei") for f, _ in pairs)


def test_法语纯删除补进等价邻词():
    src = "Je supervise la logistique pour la région Auvergne-Rhône-Alpes entière."
    dst = "Je supervise la logistique pour la région entière."
    got = extract_changes(src, dst)
    assert [(c["from"], c["to"]) for c in got] == [("région Auvergne-Rhône-Alpes", "région")]


def test_带中文说话人前缀的外文行仍按词比():
    # 问答体是「主持人：Bonjour…」——只认「含不含中文」会把整份外文稿判回字符级
    src = "被访者：Je travaille chez Schneider Electric à Grenoble."
    dst = "被访者：Je travaille chez la société XX à Grenoble."
    got = extract_changes(src, dst)
    assert [(c["from"], c["to"]) for c in got] == [("Schneider", "la"), ("Electric", "société XX")] or \
        [(c["from"], c["to"]) for c in got] == [("Schneider Electric", "la société XX")]


def test_中文行仍走字符级_逐字相同():
    # 分流不许动中文的结果：与分流前的判定逐字相同（原用例的镜像）
    got = extract_changes("我们在江苏的团队有12个人", "我们在本省的团队有十几个人")
    pairs = {(c["from"], c["to"]) for c in got}
    assert pairs == {("江苏", "本省"), ("12", "十几")}


def test_法语整句重写按词数闸整份丢弃():
    src = "Interviewer: Bonjour, merci de prendre le temps pour cet entretien aujourd'hui."
    dst = "Interviewer: Nous avons choisi de tout réécrire complètement autrement sans rien garder."
    assert extract_changes(src, dst) == []


def test_法语改口按区间精确下刀():
    src = "Chez Schneider Electric et chez Schneider Electric encore."
    dst = "Chez la société XX et chez la société XX encore."
    changes = extract_changes(src, dst)
    spots = changes[0]["spots"]
    assert changes[0]["count"] == 2
    # 只把第二处换回原词，第一处不许动
    got = apply_overrides(src, dst, [{"line": 0, "idx": spots[1]["idx"],
                                      "from": "Schneider Electric", "to": "la société XX",
                                      "value": "Schneider Electric"}])
    assert got == "Chez la société XX et chez Schneider Electric encore."


def test_覆盖校验放得下法语词组():
    long_from = "comité de priorisation hebdomadaire"       # 35 字符，超中文的 40 上限没有、但接近
    ok = valid_overrides([{"line": 0, "from": long_from, "to": "un comité interne", "value": long_from}])
    assert ok is not None

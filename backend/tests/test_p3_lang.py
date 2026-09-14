"""P3 报告的语言指令 + 〈依据〉列机读标签。

背景（2026-08-30）：复核卡上的「原因 / 依据」是模型写的，而提示词从头到尾没交代过用哪门
语言 —— 15 单生产留档 139 条依据里 131 条中文、8 条日文，与录音语种无关。要它跟界面语言走，
**不能只说一句「用 X 语写报告」**：2026-08-29 拿生产同款 DeepSeek Flash + 真实西语生产稿实测，
它把章节标题和表头一起翻了（`## 实体定字（硬证据）` → `## Fijación de entidades`；日语那轮更阴，
写成 `## 実体定字`，与简体只差一个字形），`review._section_rows` 一解析**三张表全是 0 行**——
终稿照常交付、照常计费，用户一张卡都没有，哪儿都不报错。

所以这一组守卫钉两件事：① 骨架被逐条点名保住；② 依据列另带一个 ASCII 机读标签，
让「证据标签」这件机读的事不再依赖中文关键词。
"""
import re
from pathlib import Path

import pytest

from pipeline import p3_lang, review

SRC = Path(__file__).resolve().parent.parent / "pipeline"
LANGS = ("zh", "en", "de", "fr", "es", "it", "pt", "ja")


# ── 一、骨架必须在指令里被逐条点名 ──────────────────────────────────

def test_指令点名了review真正在解析的每一个章节关键词():
    """判据**从 review.py 的源码里抠**，不另抄一份字符串。

    抄一份的话，日后有人给报告加第四张表、`parse_review` 跟着改了而指令没改，
    这条守卫照样绿——而那张新表会在非中文界面下被翻掉、静默丢卡。"""
    src = (SRC / "review.py").read_text(encoding="utf-8")
    kws = set(re.findall(r'_section_rows\(report_md,\s*"([^"]+)"\)', src))
    assert kws, "review.py 里一个 _section_rows 调用都没找到——正则该更新了"
    # ⚠️ 比的是 **SKELETON 那份「照抄清单」**，不是整段指令的文本（2026-08-30 订正）：
    # 按整段文本搜的话，`实体定字` 在后面「〈实体定字〉表的依据列」那句里也出现，
    # 于是**把整块照抄清单删掉，守卫照样绿**——造回 bug 时真绿了一次。
    blob = "\n".join(p3_lang.SKELETON)
    missing = [k for k in kws if k not in blob]
    assert not missing, f"这些章节名 review 要解析、照抄清单里却没有：{missing}"


@pytest.mark.parametrize("skeleton", [
    "| 时间码 | 各轨候选 | 终稿 | 依据 |",              # 实体定字表头
    "| # | 搜索词 | 为定哪个词（时间码） | 结果 | 建议入库? |",  # 联网核实表头
    "| 时间码 | 终稿写法 | 原因 |",                    # 存疑表头
    "===DOUBT===",                                     # 分批版的围栏
    "无",                                              # 空节写的那个字，review 拿它判「没有内容」
])
def test_照抄清单里有每一个机读骨架(skeleton):
    assert skeleton in p3_lang.SKELETON


def test_照抄清单真的被渲染进了指令():
    """清单只是数据；没渲染进去等于没说。两条一起才守得住。"""
    d = p3_lang.directive("de")
    for x in p3_lang.SKELETON:
        assert f"`{x}`" in d, x


def test_指令提醒了日文汉字与简体汉字不是同一个字():
    """这是实测踩到的那一脚：`実体定字` 肉眼几乎等于 `实体定字`，解析结果是 0 行。
    没有这一句，日语那轮就会「看起来照抄了」而实际没有。"""
    d = p3_lang.directive("ja")
    assert "実体定字" in d and "实体定字" in d, "得把两个字形并排摆出来，光说「照抄」不够"


# ── 二、八门 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", LANGS)
def test_八门都拼得出指令且写明了语言名(lang):
    from pipeline.ui_lang import UI_LANG_NAMES
    assert UI_LANG_NAMES[lang] in p3_lang.directive(lang)


def test_没放量的界面语言回落英文不回落中文():
    """韩语用户读得懂英文，读不懂中文。回落中文＝把「读不懂」从一门语言扩散到所有未放量语言。"""
    from pipeline.ui_lang import UI_LANG_NAMES
    for probe in ("ko", "ru", "", None, "zz-ZZ"):
        assert UI_LANG_NAMES["en"] in p3_lang.directive(probe), probe


def test_中文也发同一段指令():
    """**八门都发，中文也发**——只在非中文时才拼的话，这条路径生产上一个月也跑不到几次，
    而它恰恰是最需要一直被跑着的那条。"""
    assert "## 本次任务的语言" in p3_lang.directive("zh")
    assert "简体中文" in p3_lang.directive("zh")


# ── 三、依据列的机读标签 ────────────────────────────────────────────

@pytest.mark.parametrize("tag", p3_lang.BASIS_TAGS)
def test_四个标签都剥得下来(tag):
    got, rest = p3_lang.split_basis_tag(f"[{tag}] 说明文字")
    assert (got, rest) == (tag, "说明文字")


def test_没有标签就原样返回():
    assert p3_lang.split_basis_tag("多数3:0（AAI/DB/GEM）") == ("", "多数3:0（AAI/DB/GEM）")


def test_不认识的方括号不许被当成标签剥掉():
    """`[❓]` 是存疑标记、有语义。剥掉它就把「拿不准」变成了「已定字」。"""
    assert p3_lang.split_basis_tag("[❓] 拿不准")[0] == ""
    assert p3_lang.split_basis_tag("[web2] x")[0] == ""


@pytest.mark.parametrize("tag", p3_lang.BASIS_TAGS)
def test_指令里列出了每一个标签(tag):
    assert f"`[{tag}]`" in p3_lang.directive("fr")


# ── 四、_basis：中文关键词优先、标签兜底 ────────────────────────────

def test_中文关键词优先于标签():
    """顺序不能反：中文报告跑了两个月，标签是新路。让新路去盖老路，收益为零、风险全在。"""
    assert review._basis("联网核实=X 为真实品牌", True, "other") == "web"
    assert review._basis("多数3:0", True, "other") == "acoustic+glossary"


def test_关键词落空时由标签接手():
    """依据列一改成日语，中文关键词必然全部落空——这时标签是唯一的信息来源。"""
    ja = "ネット検索で実在の社名と確認"
    assert review._basis(ja, True, "web") == "web"
    assert review._basis(ja, True, "majority") == "acoustic+glossary"
    assert review._basis(ja, False, "majority") == "acoustic"
    assert review._basis(ja, True, "term") == "glossary"
    assert review._basis(ja, True, "other") == "engine"
    assert review._basis(ja, True, "") == "engine"


def test_没挂库的任务标签也不许说术语库():
    """与中文那条同一口径：术语库档要同时满足「说了」+「本单真挂了库」。
    只按标签放行的话，未挂库的单会显示成「术语库定字」——纯误导（2026-08-08 用户实测过）。"""
    assert review._basis("ネット検索", False, "term") == "engine"


# ── 五、两条执行路都要送到 ──────────────────────────────────────────

def test_claude路把指令拼进了prompt():
    from pipeline import skill_merge
    p = skill_merge._build_prompt("x_P2_Match.md", "", "ja")
    assert "## 本次任务的语言" in p and "日本語" in p


def test_deepseek路把指令放进了子进程环境变量(monkeypatch, tmp_path):
    """vendor 脚本是独立进程、看不见我们的包，只能靠 env 把指令递过去。
    漏了这一条的症状是「Claude 出的报告是对的、降级那次不是」——而降级只在撞顶时走。"""
    from pipeline import merge
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        seen["env"] = env
        (tmp_path / "Output").mkdir(exist_ok=True)
        (tmp_path / "Output" / "a_P3_Merge_DS_1.md").write_text("x")

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(merge.subprocess, "run", fake_run)
    merge.run_merge("m.md", vendor_root=tmp_path, ui_lang="pt")
    assert "Português" in seen["env"]["P3_LANG_DIRECTIVE"]


def test_vendor脚本真的会把那个环境变量拼进system():
    """光设 env 不算数——那边得真读。这条按源码钉，因为跑真脚本要连 DeepSeek。"""
    src = (SRC / "vendor/Workflow/Phase3_Merge_DeepSeek.py").read_text(encoding="utf-8")
    body = src[src.index("def build_system"):src.index("def build_user")]
    assert 'os.environ.get("P3_LANG_DIRECTIVE"' in body


# ── 六、review.py：标签只给机器看，不给人看 ─────────────────────────

_REPORT = """# P3 Merge 报告

## 实体定字（硬证据）

| 时间码 | 各轨候选 | 终稿 | 依据 |
|---|---|---|---|
| 00:10 | Aranchel / Arantxel | Aranchel | [majority] Mehrheit 3:1 |

## 存疑 [❓]

| 时间码 | 终稿写法 | 原因 |
|---|---|---|
| 00:20 | Encantado | Nicht eindeutig |
"""

_SEGS = [{"t": "00:10", "sp": "主持人", "s": "Bodegas Aranchel"},
         {"t": "00:20", "sp": "被访者", "s": "Encantado, gracias"}]


def test_机读标签不会漏到用户看到的原因里():
    items = review.parse_review(_REPORT, _SEGS, has_glossary=True)
    ent = next(i for i in items if i["type"] == "entity")
    assert ent["reason"] == "Mehrheit 3:1", "标签是给程序读的，不该出现在卡片上"
    assert ent["evidence"]["basis"] == "acoustic+glossary", "但它得真的被用上"


def test_我们自己写的那句补卡文案带得出标记():
    """`unreported` = 这句话是我们写的、不是模型写的 → 前端据此换成界面语言。
    光看 type 分不出来：它和真存疑卡都是 doubt。"""
    marks = [{"t": "00:20", "speaker": "被访者", "lineText": "Encantado, gracias",
              "term": "Encantado"}]
    items = review.parse_review("# 空报告\n", _SEGS, draft_marks=marks)
    d = next(i for i in items if i["type"] == "doubt")
    assert d.get("unreported") is True
    assert d["reason"] == p3_lang.UNREPORTED_REASON

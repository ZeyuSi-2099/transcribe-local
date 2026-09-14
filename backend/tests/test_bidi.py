"""RTL 导出的守卫 + 说话人标签在导出链路上的落地。

判据全部取「只在正确状态下才成立」的特征：
- 方向：看**用的是哪一组控制字符**（RLM 实测无效，用了就该红），不看「有没有加东西」；
- 标签：看**各门界面各自的产出**，不看「有没有调用翻译函数」。

标签表本身的守卫在 `test_speaker_label_parity.py`（含前后端对账）；这里只管
「导出的文件里到底写了什么」。
"""
from app import bidi
from app.api import _segments_to_txt

SEGS = [
    {"t": "00:00:00", "sp": "主持人", "s": "شكرًا لك على وقتك."},
    {"t": "00:00:10", "sp": "被访者", "s": "بكل سرور، تفضل."},
]


def test_阿拉伯语用RLI隔离而不是RLM提示():
    """RLM 只是「提示」——查看器把方向锁成 LTR 时它完全不起作用（2026-08-29 真 .txt 实测）。
    RLI 是「隔离」，外面锁成什么样都影响不到里面。**换回 RLM 这条必须红。**"""
    out = _segments_to_txt(SEGS, lang="ar", ui="en")
    assert bidi.RLI in out and bidi.PDI in out, "没有用 RLI…PDI 隔离"
    assert "‏" not in out, "用了 RLM——实测无效，别改回去"
    assert "‫" not in out, "用了 RLE——Unicode 已取代，实测被忽略"


def test_每一行都要圈不是整篇圈一次():
    out = _segments_to_txt(SEGS, lang="ar", ui="en")
    assert out.count(bidi.RLI) == 2 and out.count(bidi.PDI) == 2


def test_非RTL语种一个控制字符都不加():
    """26 门 LTR 语种必须零影响——多塞隐形字符会被复制粘贴带走。"""
    out = _segments_to_txt(SEGS, lang="zh", ui="zh")
    assert bidi.RLI not in out and bidi.PDI not in out and "‏" not in out


def test_说话人标签跟界面语言走八门():
    """录音是阿拉伯语，标签仍跟**界面**语言——这正是「我们写的字跟界面走、
    录音内容不翻」那条规则在导出上的样子。"""
    assert "主持人：" in _segments_to_txt(SEGS, lang="ar", ui="zh")
    assert "Interviewer: " in _segments_to_txt(SEGS, lang="ar", ui="en")
    assert "Befragte Person: " in _segments_to_txt(SEGS, lang="ar", ui="de")
    assert "回答者: " in _segments_to_txt(SEGS, lang="ar", ui="ja")
    # 未放量的界面语言回落英文，不回落中文
    assert "Interviewer: " in _segments_to_txt(SEGS, lang="ar", ui="ko")


def test_分隔符跟着标签走():
    """英文名后面挂全角「：」在任何语言下都是错的排版。"""
    assert "Interviewer: " in _segments_to_txt(SEGS, lang="zh", ui="en")
    assert "Interviewer：" not in _segments_to_txt(SEGS, lang="zh", ui="en")


def test_docx的分隔符也跟着标签走():
    """⚠️ docx 那边一直写死全角「：」（2026-08-30 修）。txt 有守卫、docx 没有，
    于是英文用户下载到的每一行都是 `Interviewer：`——同一个 bug 只被挡住一半。"""
    import io as _io
    import zipfile

    from app import export
    xml = zipfile.ZipFile(_io.BytesIO(
        export.segments_to_docx(SEGS, lang="zh", ui="en"))).read("word/document.xml").decode()
    assert "Interviewer: " in xml, "英文界面的 docx 没有用半角分隔符"
    assert "Interviewer：" not in xml, "英文名后面挂了全角「：」"
    xml_zh = zipfile.ZipFile(_io.BytesIO(
        export.segments_to_docx(SEGS, lang="zh", ui="zh"))).read("word/document.xml").decode()
    assert "主持人：" in xml_zh, "中文界面反而丢了全角「：」"


def test_docx用bidi和rtl两个元素():
    """只设 w:bidi 的症状是「对齐对了、字序还是错的」——看起来像修好了，其实没有。"""
    from app import export
    x = export.segments_to_docx(SEGS, lang="ar", ui="en").hex()
    doc = export.segments_to_docx(SEGS, lang="ar", ui="en")
    import zipfile, io
    xml = zipfile.ZipFile(io.BytesIO(doc)).read("word/document.xml").decode()
    assert "<w:bidi/>" in xml, "缺段落级 w:bidi"
    assert "<w:rtl/>" in xml, "缺文字级 w:rtl（只有 bidi 的话字序还是错的）"
    assert x  # noqa


def test_非RTL的docx不加方向元素():
    from app import export
    import zipfile, io
    xml = zipfile.ZipFile(io.BytesIO(export.segments_to_docx(SEGS, lang="zh"))).read("word/document.xml").decode()
    assert "<w:bidi/>" not in xml and "<w:rtl/>" not in xml

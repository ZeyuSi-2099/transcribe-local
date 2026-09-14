"""后处理的语言规则守卫。

规则：**产物正文跟输入稿（＝录音语种）；报告里我们写的字跟界面语言。**
界面西班牙语 + 录音法语 ⇒ 产物法语、报告说明西班牙语、报告里引用的原文仍是法语。

这里守三件事，每件的判据都取「只在正确状态下才成立」的特征：
  ① 三份工作流脚本里**不再有**「把非中文字符换成中文」这类指令——它是会毁稿的那一条；
  ② 语言指令**同时**进 Claude 路的 prompt 与降级路的 system（只进一条平时测不出来）；
  ③ 下载产物时，行首的说话人代号按界面语言换（脱敏稿是问答体，行首就是它）。
"""
import re
from pathlib import Path

from app import speaker_labels
from pipeline import pp_deepseek, pp_lang, pp_redact_ds, pp_runner

SKILLS = Path(__file__).resolve().parents[1] / "pipeline" / "vendor" / ".claude" / "skills"
FILES = [
    SKILLS / "pp-narrate" / "SKILL.md",
    SKILLS / "pp-redact" / "SKILL.md",
    SKILLS / "_shared" / "共性质检.md",
]

# 会毁稿的那一类指令：命令模型把非中文的东西改成中文。
# ⚠️ 判据是**祈使句**（「必须/应/→ 替换为…中文」），不是「出现了『中文』两个字」——
#    后者会把「输入稿是中文时才做」这种正确写法也判红，逼人加豁免，守卫就废了。
_DESTRUCTIVE = [
    re.compile(r"其他语言字符[^\n]*(必须|应|→)[^\n]*中文"),
    re.compile(r"(西里尔|希腊|希伯来|带音调的拉丁)[^\n]*→[^\n]*(替换|修复)为[^\n]*中文"),
    re.compile(r"仅允许标准中文字符和标准英文字符"),
    re.compile(r"仅使用标准字符集[^\n]*\n[^\n]*标准简体中文字符"),
]


def test_三份脚本里没有把非中文字符改成中文的指令():
    """一份西语稿进「视角转换」，照那条执行每个 á é í ó ú ñ 都该被换成中文；
    俄语稿整篇都是「其他语言字符」。而它**不报错**——界面上显示加工成功。"""
    for f in FILES:
        text = f.read_text(encoding="utf-8")
        for pat in _DESTRUCTIVE:
            assert not pat.search(text), f"{f.name} 里还有会毁稿的字符替换指令：{pat.pattern}"


def test_中文专属检查被标成仅中文稿():
    """繁简统一、中文连字符这类检查本身没错，错在无条件执行。标出适用范围即可。"""
    shared = (SKILLS / "_shared" / "共性质检.md").read_text(encoding="utf-8")
    assert "【仅中文稿】" in shared, "共性质检里没有标出哪些条目只对中文稿生效"
    assert shared.count("【仅中文稿】") >= 2, "至少繁简统一与中文连字符两条要标"


def test_指令同时说清两个方向():
    d = pp_lang.directive("es")
    assert "与输入稿" in d and "同一语言" in d, "没说产物跟输入稿"
    assert "Spanish" in d, "没说报告用界面语言"
    assert "引用的原文" in d and "不要翻译" in d, "没把报告里的引文排除在外"


def test_未放量的界面语言回落英文_不回落中文():
    # 回落中文的话，一个韩语用户会拿到一份他读不懂的报告
    assert "English" in pp_lang.directive("ko")
    assert "Simplified Chinese" not in pp_lang.directive("ko")


def test_claude路的prompt带上了语言指令():
    p = pp_runner._step_prompt("narrate", "in.md", "out.md", None, pp_lang.directive("ja"))
    assert p.startswith("/pp-narrate in.md out.md"), "斜杠命令与位置参数被破坏了"
    assert "Japanese" in p


def test_prompt的位置参数仍在前三个词():
    """⚠️ 指令是**追加**在命令之后的。真正的执行方按位置读参数，
    所以命令那一段必须保持 `/pp-<step> <输入> <输出> [清单]` 的形状。"""
    p = pp_runner._step_prompt("redact", "in.md", "out.md", "keep.md", pp_lang.directive("de"))
    assert p.split()[:4] == ["/pp-redact", "in.md", "out.md", "keep.md"]


def test_两条执行路的指令是同一份():
    """Claude 路与降级路各拼各的，用同一个函数拼才不会有一条落后。"""
    d = pp_lang.directive("it")
    assert d in pp_runner._step_prompt("narrate", "a", "b", None, d)


def test_产物里行首的说话人按界面语言换():
    md = "主持人：您好。\n\n被访者：嗯。\n\n说话人 2：还有我。\n\n这一行不是标签。"
    out = speaker_labels.localize_transcript_labels(md, "de")
    assert out.startswith("Interviewer: 您好。")
    assert "Befragte Person: 嗯。" in out
    assert "Sprecher 2: 还有我。" in out
    assert "这一行不是标签。" in out, "没有标签的行被动了"
    assert "主持人" not in out


def test_正文被原样保留_只动标签():
    """产物正文跟录音语种走——换标签不能顺手动正文一个字。"""
    md = "被访者：Sobre todo los plazos de entrega, señor.\n\n主持人：¿Y después?"
    out = speaker_labels.localize_transcript_labels(md, "ja")
    assert "Sobre todo los plazos de entrega, señor." in out
    assert "¿Y después?" in out
    assert out.startswith("回答者: ")


def test_中文界面原样返回():
    md = "主持人：您好。"
    assert speaker_labels.localize_transcript_labels(md, "zh") == md


# ── 降级路的报告：代码拼的模板，语言指令管不到它 ──

_STAGES = [
    {"name": "2 语义补漏", "applied": 2, "ids": [1, 4], "dropped": []},
    {"name": "3 指代消解", "applied": 0, "ids": [], "dropped": []},
    {"name": "4 质检", "applied": 1, "ids": [7], "dropped": []},
]


def test_问题清单跟界面语言走():
    """⚠️ 这份是代码拼的，不是模型写的——skill 后面那段语言指令对它无效。
    2026-08-30 真跑一遍法语稿才发现：产物已经是法语了，清单还是中文。"""
    de = pp_deepseek._issues_md(_STAGES, [], "de")
    assert "Perspektivwechsel" in de and "Korrekturen insgesamt: 3" in de
    assert "共修复" not in de and "问题清单" not in de, "还有中文没换掉"
    ja = pp_deepseek._issues_md(_STAGES, [], "ja")
    assert "語り変換" in ja and "修正 計 3 件" in ja


def test_脱敏报告跟界面语言走_类别名与界面一致():
    md = pp_redact_ds._qc_md("in.md", "out.md", True, [(3, "2019 年", "多年前", 1)],
                             [], "机械体检结果", "", 5, "扫描说明", "", "es")
    assert "Informe de control de anonimización" in md
    assert "Cifras y tamaños" in md, "类别名没走界面上那一套译法"
    assert "Total de correcciones: 5" in md
    assert "共修复" not in md


def test_翻译之后修复数仍然读得出来():
    """⚠️ 这是翻译最容易顺手弄坏的东西：计数原本靠解析末行的「共修复 N 处」，
    报告一翻成德语那个中文正则一条都抓不到，症状是卡片显示「质检已修复 0 处」，不报错。
    判据取**非中文报告**——拿中文报告测的话，旧正则照样能过，等于没测。"""
    for lang in ("de", "ja", "es", "fr"):
        n = pp_runner.parse_qc_fix_count(pp_deepseek._issues_md(_STAGES, [], lang))
        assert n == 3, f"{lang} 的问题清单解析出 {n}，应为 3"
        md = pp_redact_ds._qc_md("i", "o", False, [], [], "m", "", 7, "s", "", lang)
        assert pp_runner.parse_qc_fix_count(md) == 7, f"{lang} 的脱敏报告解析不出 7"


def test_老单没有机读行也还读得出来():
    """Claude 路的报告由模型写、没有那一行；老单的报告里也没有。②③ 两级回落要留着。"""
    assert pp_runner.parse_qc_fix_count("……\n共修复 12 处\n") == 12
    assert pp_runner.parse_qc_fix_count("修复 2 处；又修复 3 处") == 5
    assert pp_runner.parse_qc_fix_count("什么都没有") == 0


def test_机读行优先于文案():
    """两者打架时以机读行为准——它是代码算出来的，文案是拼出来给人看的。"""
    txt = "共修复 99 处\n" + pp_lang.fix_count_line(4)
    assert pp_runner.parse_qc_fix_count(txt) == 4


def test_报告文案八门键集一致():
    keys = set(pp_lang._R["zh"])
    for lang, table in pp_lang._R.items():
        assert set(table) == keys, f"{lang} 缺或多了键：{set(table) ^ keys}"
        assert len(table["kinds"]) == 7, f"{lang} 的脱敏类别不是 7 个"


# ── 指代消解的护栏：只认全角括号会让非中文稿整步失败 ──

def test_指代护栏认半角括号():
    """⚠️ 原版只认全角 `（）`。中文稿的模型吐全角、拉丁语言的吐半角 `()`——于是
    「去括号后逐字相等」永远不成立，整段被丢弃，丢弃率 100% ⇒ **整步判失败**。
    2026-08-30 拿一份法语访谈稿真跑两次：一次因此失败，另一次侥幸吐了全角、
    却在法语句子里留下一个全角括号（排版是错的）。
    判据要看**括号这件事**，不看它是哪种字形。"""
    from pipeline.pp_deepseek import CHECK_PAREN
    assert CHECK_PAREN("Leur interlocuteur a changé.",
                       "Leur (le fournisseur turc) interlocuteur a changé.")
    assert CHECK_PAREN("他们的对接人换了。", "他们（土耳其供应商）的对接人换了。")


def test_指代护栏仍然挡住改写正文():
    """放宽字形不能顺手放宽「只许加括号」这条——它保证的是「去掉括号＝回到原话」。"""
    from pipeline.pp_deepseek import CHECK_PAREN
    assert not CHECK_PAREN("Leur interlocuteur a changé.", "Leur (x) contact a changé.")
    assert not CHECK_PAREN("A (old) B", "A B (new)"), "旧括号被换掉了却放行"
    assert not CHECK_PAREN("甲（旧）乙", "甲（新）乙")
    assert CHECK_PAREN("A (old) B", "A (old) B (new)")


def test_指令要求标点跟着输入稿():
    d = pp_lang.directive("fr")
    assert "标点" in d and "半角" in d


def test_计数文案不受单复数影响():
    """⚠️ `1 correcciones` 是错的西语。拉丁语言有单复数一致，而给八门各加一套复数形式
    换来的只是这一行——所以文案写成**标签式**（`Total de correcciones: 1`），
    数字变形不影响它。判据取 n=1：n=2 时错的写法也是对的，测不出来。"""
    for lang in pp_lang._R:
        one = pp_lang.report(lang)["total"].format(n=1)
        assert "1" in one
        # 标签式的特征：数字在末尾或被冒号引出，而不是「1 个名词复数」那种前置结构
        assert one.rstrip().endswith("1") or "：1" in one or ": 1" in one or " 1 " in one


# ── qc.md 的中文残留（2026-08-30 验收轮拍到的两处）─────────────────────────────
# 生产实况：德语用户下载的 Prüfbericht 主体是德语，但夹着「# 视角转换」「# 脱敏」两个
# 写死的标题和「共修复 N 处」两行中文契约。标题走八门表；契约行存档前拆成
# 「界面语言那句 + <!-- qc-fix-count --> 注释」——解析本来就优先认注释。

_CLAUDE_DE_REPORT = """# Prüfbericht zur Perspektivumwandlung

## I. Behebung von Bedeutungsverlust

1. **Typ: Kontext übernommen** — „Quel a été le plus grand défi ?"

共修复 6 处"""


def test_契约行改写成界面语言加注释():
    got = pp_runner.localize_qc(_CLAUDE_DE_REPORT, "de")
    assert "共修复" not in got
    assert "Korrekturen insgesamt: 6" in got
    assert pp_lang.fix_count_line(6) in got
    # 引文与主体一个字不动
    assert "Quel a été le plus grand défi" in got


def test_契约行改写不改变机读计数():
    for lang in ("de", "ja", "zh", "ko", None):
        assert pp_runner.parse_qc_fix_count(pp_runner.localize_qc(_CLAUDE_DE_REPORT, lang)) \
            == pp_runner.parse_qc_fix_count(_CLAUDE_DE_REPORT) == 6


def test_中文界面契约行逐字不变_只多注释():
    # 八门同一条路径，中文也走——「只在非中文时才转」的路径平时没人跑
    got = pp_runner.localize_qc("正文\n\n共修复 3 处", "zh")
    assert "共修复 3 处" in got
    assert pp_lang.fix_count_line(3) in got


def test_降级路报告已带注释_原样返回():
    text = "Bericht\n\nKorrekturen insgesamt: 2\n" + pp_lang.fix_count_line(2)
    assert pp_runner.localize_qc(text, "de") == text


def test_行中引用的共修复不许被改写():
    # 只动独占一行的契约行；正文里「上一稿共修复 9 处」是给人看的引述
    got = pp_runner.localize_qc("上一稿共修复 9 处，这次如下。\n\n共修复 1 处", "de")
    assert "上一稿共修复 9 处" in got
    assert "Korrekturen insgesamt: 1" in got


def test_qc标题按界面语言_与卡片上产物同名():
    parts = [("narrate", "A"), ("redact", "B")]
    de = pp_runner.qc_md_for(parts, "de")
    assert "# Erzählfassung" in de and "# Anonymisiertes Transkript" in de
    zh = pp_runner.qc_md_for(parts, "zh")
    assert "# 视角转换" in zh and "# 脱敏" in zh          # 中文报告与改动前逐字相同
    ko = pp_runner.qc_md_for(parts, "ko")                  # 未放量 → 回落英文不回落中文
    assert "# Narrative draft" in ko and "视角转换" not in ko


def test_八门表两个步骤名全齐():
    for lang, table in pp_lang._R.items():
        for k in ("step_narrate", "step_redact"):
            assert table.get(k), f"{lang} 缺 {k}"


def test_qc组装与契约改写都真的接在worker上():
    """两处修复都是「函数对了但没人调」就静默失效的形状——症状是德语报告照旧夹中文、
    不报错。钉住 worker 源码里的两处调用点。"""
    src = Path(pp_runner.__file__).read_text(encoding="utf-8")
    assert "localize_qc(qc_file.read_text" in src
    assert "qc_md_for(qc_parts, ui_lang)" in src


# ── 质检报告引文里的说话人代号（2026-08-30 八门实测拍到）─────────────────────────
# 报告举证「原文是这样」时，会把输入稿行首的内部代号原样抄进**行中**：
# `Original: 主持人：…`。en 与 it 两份生产报告都中招——用例取自那两份的真实行。
# 判据：代号变成界面语言、**引文本身一个字不动**（阿语原文按录音语种保留）。

_EN_QUOTE = 'Original: 主持人：ما هو أكبر تحدٍ واجهتموه في العام الماضي؟ / 被访者：بلا تردد'
_IT_QUOTE = '- **Frammento originale del dialogo:** 主持人：هل تعتمدون على ميناء واحد؟'


def test_报告引文里的代号跟界面语言():
    got = pp_runner.localize_qc(_EN_QUOTE + "\n\n共修复 2 处", "en")
    assert "主持人" not in got and "被访者" not in got
    assert "Interviewer: " in got and "Interviewee: " in got
    # 引文本身（阿语）一个字不改
    assert "ما هو أكبر تحدٍ واجهتموه في العام الماضي؟" in got


def test_报告引文的代号八门各归其位():
    for lang, want in [("de", "Interviewer"), ("fr", "Intervieweur"), ("es", "Entrevistador"),
                       ("it", "Intervistatore"), ("pt", "Entrevistador"), ("ja", "インタビュアー")]:
        got = pp_runner.localize_qc(_IT_QUOTE, lang)
        assert want in got, f"{lang} 期望 {want}"
        assert "主持人" not in got


def test_中文报告的引文逐字不变():
    got = pp_runner.localize_qc(_EN_QUOTE + "\n\n共修复 2 处", "zh")
    assert "主持人：" in got and "被访者：" in got      # 代号本来就是中文，不该被动


def test_降级路的报告也要换代号():
    """⚠️ 这条钉住调用顺序：代号替换必须排在 `_FIX_MARK` 早退**之前**。
    降级路的报告自带机读注释，早退在前的话它的引文永远换不掉——而它同样引用输入稿。"""
    text = _EN_QUOTE + "\n\nKorrekturen insgesamt: 2\n" + pp_lang.fix_count_line(2)
    got = pp_runner.localize_qc(text, "de")
    assert "主持人" not in got
    assert "Interviewer: " in got
    assert pp_lang.fix_count_line(2) in got            # 注释仍在，计数没被破坏


def test_换代号不影响机读计数():
    for lang in ("en", "de", "ja", "zh"):
        assert pp_runner.parse_qc_fix_count(pp_runner.localize_qc(_EN_QUOTE + "\n\n共修复 7 处", lang)) == 7


def test_未放量语言的引文代号回落英文():
    got = pp_runner.localize_qc(_IT_QUOTE, "ko")
    assert "Interviewer: " in got and "主持人" not in got


def test_说话人N的代号也换():
    got = pp_runner.localize_qc("Original: 说话人 2：مرحبًا", "de")
    assert "Sprecher 2: " in got and "说话人" not in got


# ⚠️ 模型引用输入稿的**格式是它自己定的**：同一天的生产报告里 `主持人：…` 与
# `主持人「…」` 两种并存。第一版守卫只造了带冒号的用例，于是「要求后面跟冒号」的
# 实现全绿上线，生产复验才发现 5 处只换掉 1 处。⇒ 用例必须覆盖两种形态。
_DE_BRACKET = '- Originaldialog: 主持人「Kênh bán hàng chính là gì?」 / 被访者「Chúng tôi bán chủ yếu qua Thế Giới Di Động.」'


def test_角括号引用的代号也换():
    got = pp_runner.localize_qc(_DE_BRACKET, "de")
    assert "主持人" not in got and "被访者" not in got
    assert "Interviewer「" in got and "Befragte Person「" in got
    # 标点保持模型写的样子，越南语引文一个字不动
    assert "Kênh bán hàng chính là gì?" in got and "Thế Giới Di Động" in got


def test_两种引用形态混在一份报告里都换掉():
    got = pp_runner.localize_qc(_EN_QUOTE + "\n" + _DE_BRACKET, "en")
    assert "主持人" not in got and "被访者" not in got
    assert "Interviewer: " in got        # 冒号形态：归一成界面语言的分隔符
    assert "Interviewer「" in got        # 角括号形态：标点原样


def test_其他与裸说话人只在带冒号时才换():
    """弱档：`其他` 是常用词，`说话人` 不带序号时也可能是散文——误伤代价比漏掉高。"""
    assert "Sonstige: " in pp_runner.localize_qc("其他：xyz", "de")
    assert pp_runner.localize_qc("Es gibt 其他 Gründe", "de") == "Es gibt 其他 Gründe"

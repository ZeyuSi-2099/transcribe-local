"""后处理·视角转换的 DeepSeek 降级路（pp-narrate 的 Flash 版）。

**定位**：Claude 撞顶时的兜底，不是并列选项。对照物是「没有稿」，不是 Opus 稿——
所以验收标尺是**红线为 0**（不丢内容、不改写、不编造），措辞与分段的粗糙可以接受。
2026-08-13 实测：2.2 万字长稿三轮，实质丢失 0（逐处回原文人工核过），
成本 $0.054–0.074 对 Opus 的 $2.21（1/37），耗时 861–1247s 对 Opus 488s。

**规则来源唯一**：system 直接取 `pp-narrate/SKILL.md` 从「# 通用保护规则」到
「# 输出要求（文件形式）」的原文 + 共性质检.md 全文，一个字不改。被剥掉的两头是执行外壳
（讲 Read/Write 哪个文件），由下面的编排替代。**改 SKILL.md 会自动生效，不需要动这里**；
反过来，本文件里凡是重述规则的地方都必须重述完整——2026-08-12 踩过：user 消息里的
压缩重述会盖过 system 里的完整原文，凭空造出「统一产品名」这种原文没有的要求。

**为什么拆成五次调用**（Opus 是三遍连做）：Flash 没有工具、无法自己读写文件回看，
一次调用做完三遍会稳定漏掉「访谈者陈述＋被访者答『对』」这类需要回头对照才能捞回的内容。
拆开后每步只干一件事，且第 2–4 步只报「哪一段改成什么」，由代码按段号替换：
输出量降两个数量级（第 3 步曾因重吐全文撞死在 64000 上限、产出 0 字），
且**没报出的段落模型根本碰不到**，这是结构保证，不是提示词求来的。

**只有第 1 步分批**：长稿的压缩全部发生在第一步（未分批 77% → 分批后 92–101%），
后三步的产出都是小增量、不吃输出长度压力。批次沿第 0 步勘定的话题边界切，
标签不下发——发了它会转去按话题重组行文，实测出过语义反转。
"""
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ds_pricing, model_backend, pp_lang

# 本机版（与线上不同）：提示词不在 vendor 的 skill 目录里（本地不搬），而是生成进包里的文本，
# 同定字提示词的做法（计划页 g4）。见 _pp_prompts.py 与 tools/saas_pp_prompts.py。
from ._pp_prompts import PP_NARRATE as SKILL_MD, SHARED_QC  # noqa: E402

MODEL = os.environ.get("PP_DS_MODEL", "deepseek-flash")
EFFORT = os.environ.get("PP_DS_EFFORT", "high")   # low/high/max（无 medium）
TEMP = 0
# 131072 而非 64000：第 3 步曾重吐全文 + reasoning 撞死在 64k 上（out=63996、产出 0 字）。
# 改成只报改动后已无撞顶风险，上限留着当保险。
MAXTOK = 131072
STEP1_BUDGET = int(os.environ.get("PP_DS_STEP1_BUDGET", "6000"))   # 每批多少字原文
# 第 1 步并发批数。2026-08-13 从 4 提到 6：账号并发 2500 远没到，4 只是当初的保守起步值，
# 它直接决定分批那一步的墙钟时间（脱敏降级路同口径用 6，实测 359s → 240s 的一部分）。
CONC = int(os.environ.get("PP_DS_CONC", "6"))
TIMEOUT = int(os.environ.get("P3_TIMEOUT_SEC", "3600"))
# 丢弃率兜底：某步报出的段落绝大多数都落不了地，说明模型没理解这一步在要什么，
# 产出很可能已经不可信。低于此值只告警不失败——丢弃的代价是「少改几处」，不是「改错」。
DROP_FATAL = float(os.environ.get("PP_DS_DROP_FATAL", "0.5"))
DROP_WARN = 0.2


def _log(msg):
    print(f"[PP-DS] {msg}", flush=True)


# ---------------- 用量埋点 ----------------
# 后处理的两条降级路此前**一分钱都记不到**（P3 有 __P3_DS_USAGE__，这边没有），
# 于是「本月降级烧了多少」在运营舱是个空白。DeepSeek 2026-08-17 涨价后这个盲区更贵。
#
# 累加放在 call() 里而不是各步自己数：脱敏路（pp_redact_ds）直接复用本模块的 call，
# 挂在调用点上就自动覆盖两条路，不会有人新加一步时忘了记账。
# **进程内全局**——一台 Fly 机器一次只跑一个后处理任务（1 机 1 任务），不存在串台。
_USAGE = {"hit": 0, "miss": 0, "out": 0, "calls": 0}


def reset_usage() -> None:
    """每步开跑前清零（pp_runner 调）。"""
    for k in _USAGE:
        _USAGE[k] = 0


def usage_cny() -> float:
    """本步累计的人民币成本。取不到价目/算不出来一律返回 0——埋点不许连累出稿。"""
    try:
        return round(ds_pricing.cost_cny(MODEL, _USAGE["hit"], _USAGE["miss"], _USAGE["out"]), 4)
    except Exception:  # noqa: BLE001
        return 0.0


def usage_snapshot() -> dict:
    return dict(_USAGE)


# ---------------- 规则装配 ----------------

def build_system() -> str:
    """SKILL.md 的规则正文逐字取用，只换掉文件读写那层外壳。

    取「# 通用保护规则」到「# 输出要求（文件形式）」之前——被剥掉的两头都是执行外壳
    （工作流程讲 Read/Write 哪个文件、输出要求讲写哪两个文件），规则本身一个字不动。
    共性质检清单原本靠 Read 工具现读，这里直接内联（Flash 没有工具可读文件）。
    """
    md = SKILL_MD.read_text(encoding="utf-8")
    i = md.index("# 通用保护规则")
    j = md.index("# 输出要求（文件形式）")
    rules = md[i:j].rstrip()
    qc = SHARED_QC.read_text(encoding="utf-8")
    qc = re.sub(r"^---\n.*?\n---\n", "", qc, flags=re.S)      # 去 frontmatter（若有）
    return f"""你是访谈笔录处理专家。总任务：把问答式访谈稿改写成**被访者第一人称叙述**。

总任务分三遍完成：
- 第一遍 视角转换：按【转换规则】把对话式笔录转为被访者第一人称叙述。
- 第二遍 对照原文修复：以审稿人立场，假设第一遍有遗漏，回原文找证据，
  按【修复规则A】补回语义丢失、按【修复规则B】修正指代不明。
- 第三遍 质检：执行【共性质检清单】与【质检规则C】，按各项修复原则直接修复。

⚠️ **本次调用只执行用户消息里指定的那一步**，不要顺手做别的步骤——
后面的步骤有各自的调用负责，提前做只会互相干扰。下面给出三遍共用的全部规则供你查阅，
但你只按本次指定的那一步行事。

下面是三遍共用的全部规则，逐条严格遵守。

{rules}

# 共性质检清单（第三遍先执行本清单，再执行上面的质检规则C）

{qc}
"""


# ── 分段策略「两阶段」用 ──────────────────────────────────────────────
# 思路照搬 P3 分批版的两阶段：先让模型在**看得到全文**的状态下判一次全局，再分批出稿。
# 这里的全局判断不是 P3 的实体定字，而是「哪儿到哪儿是实质内容」＋「话题在哪儿切换」。
# 为什么不用代码判首尾：开场/收尾的轮数每份稿子都不同，写死行数必错；
# 而模型在全文视野下判一次是自适应的，且只判一次、不会各批各判一套。
STAGE1_USER = """以上是完整的问答式访谈笔录，每行前的 `【n】` 是行号（我加的，不是原文内容）。

【本次只做第一步：全局勘定，不要输出任何叙述正文】
通读全文后，按下面的骨架输出两节，供后续分批出稿统一口径用。

## 实质区间
两行，格式固定：
`起=<行号>`　全文第一处**被访者实质内容**所在行。此前的开场寒暄、时长说明、流程交代
（含被访者对这些的应答）都不是实质内容。
`止=<行号>`　全文最后一处**被访者实质内容**所在行。此后的问卷安排、内容确认声明、
致谢与道别（含被访者对这些的应答）都不是实质内容。
判断依据是【转换规则】其他注意事项第 7 条与【质检规则C】A4。

## 话题分段
表格列固定 `段号 | 起行 | 止行 | 话题`。按【转换规则】其他注意事项第 6 条的口径切分
（仅在切换话题处分段）。起行必须落在访谈者提问处，各段首尾相接、不重不漏，
整体覆盖上面的实质区间。段数由话题自然决定，不要凑数。

这两节之外不要输出任何别的内容。
"""

STAGE2_EXTRA = """
【全局勘定 · 已在通读全文后定好，本批必须沿用，不得另判一套】
全文实质内容的范围是第 {lo} 行到第 {hi} 行。**这个范围之外的行一律不产出任何文字**
（那些是开场寒暄与结尾客套，不是被访者的实质发言）。

⚠️ 这个范围**只界定全文的头和尾两处边界，不是让你在范围内部做删减**：第 {lo}–{hi} 行之内，
被访者说过的每一句都必须写进去，一句都不能少。尤其注意：被访者**谈论访谈或谈论自己以往发言的话**
（对自己上次说法的回顾、修正、限定、自谦），形式上像客套，实质上是他的观点与态度，
落在范围之内就必须保留。范围之内该不该留，一律按【修复规则A】的「绝不删减」判，不受本节影响；
本节只管范围之外。
{topics}"""

# 话题标签默认**不下发**（--topic-labels 1 才给）。2026-08-12 实测：把话题标签发给出稿批次后，
# 模型转去「按话题组织行文」，改写幅度明显变大，并因此改错了两处语义
# （「可以不放在开放平台」写成「可以放在开放平台」、整句「我说的可能有点多了」丢失）。
# 话题边界用来**切批**是有价值的（切在话题处不拆问答对），但边界只需进入切分逻辑，不必进提示词。
TOPIC_LABEL_LINE = "本批覆盖的话题：{t}\n"


# ── 第二遍「对照原文补漏」 ──────────────────────────────────────────────
# 由来（2026-08-12 实测）：把 SKILL.md 的三遍压成一次调用后，稳定漏掉一类内容——
# 「访谈者陈述 + 被访者答『对』」的确认。一次调用是顺着原文往下写，走到单字应答处自然跳过，
# 没有回头对照的机会；SKILL.md 原设计的第二遍要的正是这个动作（Opus 就靠它把这类捞回来）。
# 本步曾有一条「只加不改」铁律 + CHECK_INSERT 校验，2026-08-12 拿掉了。经过：
#   · 我给的理由是「防第二遍改坏第一遍」，复核后**不成立**——查遍所有真正跑过第二遍的产物
#     （s7_twopass 两份、s10_pipeline3 四步），第二遍一次都没改坏过，CHECK_INSERT 三轮零拦截。
#     我引用的那次语义反转（「可以不放在开放平台」→「可以放在」）其实发生在**第一步**，
#     且已归因于话题标签下发（见 TOPIC_LABEL_LINE 注释）——同一个案例被我重复引用且归错了因。
#   · 改成整行替换后，「没报出的段落不许动」已由机制保证，不需要提示词；剩下的只是
#     「报出的那一段内部允不允许润色」，用户拍板放开。
# 现在本步只保留 SKILL.md 本来就有的约束（不得编造、不补开场客套、保留口语习惯）。
# ---------------- 替换补丁：二三四步的交付形式 ----------------
# 2026-08-12 改：二三四步原先各自「重吐一遍全文」，代价有三——
#   ① 撞顶：第一步分批把稿写全后，第 3 步 out=63996 正好撞死在 64000，产出 0 字（硬故障）；
#   ② 浪费：拿 5 万 reasoning token 去交付「加了 13 个括号」，占那趟成本两成；
#   ③ 「只加不改」全靠提示词求它守，无法验证（重吐全文时程序没法判它动没动别处）。
# 改成只报「哪一段改成什么」之后，输出量降两个数量级，前两条直接消失；第三条也不再是问题——
# **没报出的段落，模型根本碰不到**，这是结构保证，不需要提示词求它。
# （第 2 步内部还允不允许润色，是另一个问题，2026-08-12 决定放开；
#   第 3 步仍用 CHECK_PAREN 守住「去掉括号=回到原话」的可逆性。）
PATCH_OUT = """
【输出方式 · 只报要改的行，不要重吐全文】
上面那份待处理稿的每一段前面都有一个 `#编号`。**你只需要报「哪一段改成什么」**：

#<段号> <这一段改完之后的完整内容>

1. 一段一行，`#编号` 后面写**这一段改完之后的整段内容**（不是只写改动的部分）。
2. ⚠️ 段号取自**待处理稿的 `#编号`**，不是上面那份笔录的 `【行号】`。两套编号无关，别混。
3. 只报**确实要改的段**。没改动的段一律不要出现在清单里。
4. 一个段号只报一次。同一段里有好几处要改，就在这一条里一次改完。
5. 一段都不用改，就只输出两个字：无改动
6. 不要前言、不要总结、不要代码块围栏。第一行直接是 `#编号`。
"""

PASS2_USER = """
【以上是原文；下面是第一遍的视角转换稿】

{draft}

【本次只做一件事：对照原文，把第一遍丢掉的内容补回去】
按【修复规则A】逐段对照原文与上面这份稿子，找出原文里有、稿子里没有的内容，补进去：
1. **访谈者提出观点或陈述、被访者用「对」「是的」「嗯」「没错」等确认的** —— 必须并入，
   并去掉全部确认词。⚠️ 单字应答「对。」**不是没有内容**：它确认的是访谈者上一句话的全部内容，
   那句话就是被访者的观点，漏掉它就是漏掉一个观点。逐个检查原文里每一处单字应答。
2. 访谈者的客观描述，对上下文衔接有作用的。
3. 被访者提到、但第一遍丢失的观点、补充说明、细节、态度——包括他表达不确定或保留的半句话。
4. 简短回答的语义补全（结合访谈者的问题补出主谓宾）。
   ⚠️ 特别检查**每一段的第一句**：脱离上下文单独读这一段，读者是否知道它在谈什么？
   典型症状是「只有评价、没有被评价的对象」——句子把结论说了，却没交代是在说哪件事。
   这时把访谈者提问里的那个对象补进第一句。这是补漏，不是改写——原有文字仍然一字不动。
5. 列举项完整性（第一/第二/第三项是否齐全）。

【补漏时守住这两条】
- 补回的内容必须能在原文中找到依据，**不得编造**（【修复规则A】核心原则）。
- 原文里属于开场寒暄、结尾客套、流程用语的部分**不要补进来**（第一遍已正确排除）。
- 被访者的口语习惯、方言特色词、语气词一律保留（【修复规则A】修复原则）。
""" + PATCH_OUT + """
报某一段时，把这一段改完之后的完整内容写出来（原有内容 + 补回的内容）。
"""


# ── 第三步「指代消解」 ────────────────────────────────────────────────
# 括号形式（`<原代词>（<具体内容>）`）**已于 2026-08-12 合入正式 SKILL.md**（提交 a02e689），
# 所以本步不再是「覆盖规则」，而是照规则执行，两边一致。
# 此前这里写着一段「本步显式覆盖规则原文」的声明，合入后它就开始撒谎了
# （system 里已是括号形式，user 却说「规则写的是直接替换、本步不照做」）——
# **改上游规则时要同步清掉下游的覆盖声明**，这是本轮规则一致性核对抓到的。
STEP3_USER = """
【以上是原文；下面是已完成视角转换与语义补漏的稿子】

{draft}

【本步只做一件事：按【修复规则B】消解指代不明】
逐句检查稿中的指代词——人称代词（它/他/他们）、指示代词（这个/那个/这些/那些）、
远距离指代（这种/这样/此类/这种情况/这种影响）、定冠词（该/该产品）、「这些+名词」组合、
前者后者上述等——对照原文确定它究竟指向什么。

【本步的输出形式 · 照【修复规则B】第 3 条执行】
规则里已经写明：代词原样保留，把推断出的具体内容写进紧跟其后的圆括号里，
形如 `<原代词>（<具体内容>）`。判断口径（哪些算指代不明、依据什么确定所指）同样照规则原文。

【铁律 · 只加括号，不动别的】
- 稿子里已有的文字**一字不动**：不重写、不润色、不调整语序、不合并或拆分段落、不替换用词。
- 你**只能插入圆括号及其内容**，不得删除任何字，不得新增括号以外的任何文字。
- 括号内容必须能在原文中找到依据，**不得编造**。
- 指代本来就清楚的地方不要加括号，不要为加而加。
- ⚠️ **只在原文能明确定出所指时才加括号**。有些代词在原文里本来就有歧义——往前找也定不下来，
  几种读法都说得通、要靠猜才能选一个——这类**一律不加括号，原样留着**。
  宁可不补，也不要替被访者定一个他并没有说的所指：括号一加就成了断言，而原文只是含混。
- ⚠️ 落到整段重写上就是：**你报出的那一段，去掉圆括号及括号里的字之后，必须和它原来一模一样**。
  程序会逐段这样校验，动了括号以外的任何一个字，整条丢弃。
""" + PATCH_OUT + """
报某一段时，把这一段原样抄全，只在需要的代词后面补上圆括号。同一段有几个代词要补，一次补完。
"""


# ── 第四步「质检」：代码先扫、逐条点名，模型只改被点名处 ──────────────────────
# 由来（2026-08-12 实测）：同一份提示词、同一份输入，第 4 步 v2 修好了疑问句、v3 一个字没改
# （v2 跑 144s/19954 tok，v3 只跑 34s/4740 tok——基本没思考就交卷）。让模型「全文找问题」这件事
# 本身不稳定。这与 P3 `--conflicts` 遇到的是同一个毛病：模型不是判错，是压根没在判。
# 解法同源——**能用代码确定性判定的，从模型手里拿走**，由程序扫出位置逐条点名，
# 模型只负责「在被点名的这一处按该规则改」。判断类（访谈者观点/视角混入/指代是否影响理解）
# 机器判不了，仍留给模型自查。
#
# ⚠️ 扫描器只收**高精度**的项：误报会让模型去「修」本来没问题的地方，比漏报更坏。
# 重复字词（「好好学习」「天天向上」是成语）与数学表达式塌陷都判不准，故意不做扫描器。
_TRAD = "體華業陸續麗們來這樣說對進電話網絡務標準關於問題產應該時間東馬車嗎個"
QC_SCANNERS = [
    ("质检C·E8 疑问句", re.compile(r"[^\n。！？]{0,60}？"),
     "把这一句改写成陈述句表达（这条本来就要求重写该句）"),
    ("共性质检 C4 繁体字", re.compile(r"[" + _TRAD + r"]"), "改成对应的简体字"),
    ("共性质检 C2 非中文字符", re.compile(r"[぀-ヿЀ-ӿͰ-Ͽ]"),
     "替换为语义对应的中文"),
    ("共性质检 C1 中文间连字符", re.compile(r"[一-龥]-[一-龥]"), "删除连字符"),
    ("共性质检 N7 说话人标签残留", re.compile(r"(?:^|\n)\s*(?:M|R|\d+|访谈者|主持人|被访者|说话人(?:\s*\d+)?)\s*[：:]"),
     "删除该标签"),
    ("共性质检 N7 行号残留", re.compile(r"【\d+】"), "删除行号"),
    ("共性质检 N7 格式污染", re.compile(r"```|^\s*#{1,6}\s", re.M), "删除该标记"),
    ("质检C·A2 段首确认词", re.compile(r"(?:^|\n)\s*(?:对|嗯|是的|好的|没错|对的|是啊)[，。]"),
     "删除该确认词，把后面的内容接到前一段或直接起句"),
]


def qc_scan(paras):
    """按段扫描 → 逐条点名表（markdown 行），**每条带段号**。
    带段号是为了让第 4 步不必自己去找行号：行号写错会覆盖掉一整段正确内容，
    而程序扫的时候本来就知道命中在第几段，直接给它就没有写错的余地。"""
    rows, n = [], 0
    for i, para in enumerate(paras, 1):
        for name, rx, how in QC_SCANNERS:
            for m in rx.finditer(para):
                n += 1
                frag = m.group(0).strip().replace("\n", " ")
                rows.append(f"| {n} | #{i} | {name} | `{frag}` | {how} |")
    return rows


STEP4_HITS = """
【程序已扫出的命中项 · 共 {n} 处，逐条改，一处不许漏】
下面每一行是程序按规则确定性扫出来的，**不是建议**：位置和依据都已确定，你只需按「该怎么改」
把那一处改掉。**除这些位置外，本节不授权你改动任何其它文字。**

| # | 段号 | 检查项 | 命中的文字 | 该怎么改 |
|---|---|---|---|---|
{rows}

段号已经给你了，**照着段号报那一段**，不用自己找。

改这些位置时，为达成该项要求可以重写那一处的措辞或句式（有的项本来就要求重写）。
"""

STEP4_NOHITS = """
【程序扫描未发现可机器判定的问题】
字符规范、疑问句、格式污染这几类已由程序确认无命中，本步不必再找它们。
"""

STEP4_USER = """
【以上是原文；下面是已完成视角转换、语义补漏、指代消解的稿子】

{draft}

【本步只做一件事：质检修复】
本步分两部分：先改程序点名的位置，再自查机器判不了的几项。
{hits}
【第二部分 · 机器判不了、需要你自己看的几项】
下面这些没法用规则扫出来，请对照原文逐段自查；**发现了才改，没发现一个字不动**：
- 质检规则C·A3 访谈者观点处理：不该保留的访谈者观点是否混入正文
- 质检规则C·A5 第一人称视角统一：「您当时」「您已经」「你们公司」这类称谓是否已转成第一人称
- 质检规则C·B6 客观描述补充：是否给专业术语/缩写加了不该加的解释或译名（加了要去掉）

- 质检规则C·E9 产品名称统一性、E10 中英文混合修正：**照系统提示里那两条的原文执行**，
  连同它们所属的【谨慎判断类】门槛一起。这里不复述——2026-08-12 两次复述两次失真
  （第一版把它写成「检查前后写法是否一致」，凭空造出了原文没有的「统一」要求；
  第二版收得太死，把正确的转录纠错也堵掉了）。原文就在系统提示里，去读那一份。
- ⚠️ 一并提醒：**「被访者的个人习惯用词」和「转录听错的词」是两回事**。
  他确实那么说、只是不标准 → 保持；他没那么说、是笔录记错了 → 属于「明显的拼写错误」，该订正。
  分辨靠回原文看上下文，尤其看**访谈双方后文有没有复述或确认过这个词**。

⚠️ 稿中的圆括号是上一步指代消解的产物，**不是格式污染**，一律原样保留，不要删、不要改写成别的形式。

【铁律 · 只动命中项；命中处按那一条的原则改到位】
- **没命中任何检查项的地方，一字不动**：不重写、不润色、不调整语序、不合并或拆分段落。
- **命中了某一条的地方，就按那一条的修复原则改到位**，包括为此改写那一处的措辞或句式。
  有的检查项本身就要求重写（比如把疑问句改成陈述句），上一条不限制这种改动——
  上一条管的是「没问题的地方别动」，不是「命中了也不许改」。
- 不得删减有价值信息（【质检修复要求】第 1 条）。
- 不得给专业术语、缩写或英文名词添加解释或译名。
- 被访者的口语习惯、方言特色词、语气词一律保留（【质检修复要求】第 3 条）。
""" + PATCH_OUT + """
本步与二三步不同：**命中处允许重写**（疑问句转陈述句本来就要重写），报出的段落不必逐字保留原文。
但**没被点名的段一条都不要报**。
接缝重复那几组要合并时：报靠前那一段（把两段的信息并进去），再报靠后那一段、`#编号` 后面**留空**表示删掉它。
判定程序点错了，两段就都不要报。
"""

# 2026-08-12：拿掉了原先写在【分段要求】里的两条**SKILL.md 没有的**约束——
#   「段落顺序严格按原文先后、不许调整」与「相隔较远的相似话题也不要并成一段、不做归类」。
# 由来是早期实测「14 个话题被并成 10 个」，但那一轮同时开着话题标签下发（已证实是它导致
# 模型转去按话题重组行文），两个变量混在一起，归因不干净。按用户要求先撤掉观察：
# **若后续测试里「话题被合并」稳定复现，再把这两条加回来。** 保留的那条
# 「分段依据是话题不是问答轮次」＝转换规则注意事项 6，是 SKILL.md 原有的，不属于新增。
STEP1_USER = """以上是完整的问答式访谈笔录，每行前的 `【n】` 是行号（我加的，**不是原文内容，绝不可出现在你的产出里**）。

【本步只做第一遍：视角转换】
按【转换规则】把这份对话式笔录转为被访者第一人称叙述。
本步**不做**指代消解、**不做**质检——后面有专门的步骤负责，提前做会互相干扰。

【分段要求】
- **分段依据是话题，不是问答轮次**：连着好几轮问答如果在谈同一件事，合成一段；
  换了话题才另起一段。一个话题一段，原文谈了几个话题就写几段。
  （＝【转换规则】其他注意事项第 6 条「仅在切换话题时换行」。）
- ⚠️ **每一段都要能脱离上下文独立读懂**。若某个话题被访者答得很短，单独成段后读者不知道
  在说什么，就按【修复规则A】第 4 条，把访谈者问题里的对象补进该段第一句
  （形如「关于<访谈者问的那件事>，……」），补的内容必须出自原文的提问，不得编造。

【输出方式】
直接输出正文，不要写文件、不要加前言/总结/小标题/代码块围栏、不要写「以下是……」。
第一行就是叙述的第一句。
"""

# 第一步分批时追加在最后（各批不同 → 缓存里唯一未命中的那一小段，故必须排在末尾）。
# 语气比 batch/topic 模式的 PER_BATCH 更硬：不只说「本批写 X–Y」，还要说死**边界外已由别人写完**。
# 接缝重复的根因就是模型为了行文连贯，在批次起头先复述一句上一批的主题——落稿即重复段。
STEP1_BATCH_RANGE = """
【本批范围 · 第 {i0}–{i1} 行】
全文都给你了是为了让你看得到上下文（指代要往前找、列举项要跨行核对、专名口径要全文一致），
但**本批只输出第 {i0} 行到第 {i1} 行**这一段的叙述。
{seam}
第一句直接从第 {i0} 行的内容写起。
"""


_SEAM_NOT_HEAD = ("- 第 {i0} 行之前的内容**已经由另一个批次写完了**，你一个字都不要提——"
                  "哪怕只是一句「前面说到……」式的承接也不行，落稿后那就是一段重复。\n")
_SEAM_NOT_TAIL = "- 第 {i1} 行之后的内容同理：不要提前写、不要预告、不要收尾总结全篇。\n"
S1_HEAD = _SEAM_NOT_TAIL                       # 首批
S1_MID = _SEAM_NOT_HEAD + _SEAM_NOT_TAIL       # 中间批
S1_TAIL = _SEAM_NOT_HEAD + "- 本批是全文结尾，按【转换规则】第 7 条处理访谈收尾语句。\n"


# ---------------- 替换补丁：解析与应用（形式说明在上面的 PATCH_OUT）----------------

_LINE_PATCH = re.compile(r"^#\s*(\d+)[ \t\.、:：]*(.*)$")


def number_draft(text):
    """稿子按段落标号 → (下发给模型的带号文本, 段落列表)。
    编号形式 `#n` 与笔录的 `【n】` 明显不同，是为了让模型分得清两份材料。"""
    paras = [x.strip() for x in text.split("\n\n") if x.strip()]
    return "\n\n".join(f"#{i + 1} {p}" for i, p in enumerate(paras)), paras


def parse_line_patches(txt):
    """→ [(段号, 整段新内容)]。逐行匹配，一行一条，格式歪一点不会连累后面。"""
    out = []
    for ln in (txt or "").split("\n"):
        m = _LINE_PATCH.match(ln.strip())
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def apply_line_patches(paras, pats, check=None):
    """按段号整段替换。越界、重复报同一段、校验不过 → 丢弃并记账。
    新内容为空表示删掉该段（第 4 步合并接缝重复时用）。"""
    new, ok, bad, seen, noop = list(paras), 0, [], set(), 0
    for n, content in pats:
        if not 1 <= n <= len(paras):
            bad.append((f"段号 #{n} 越界（稿共 {len(paras)} 段）", content[:40])); continue
        if n in seen:
            bad.append((f"段号 #{n} 报了不止一次，只认第一条", content[:40])); continue
        if content == paras[n - 1]:
            # 报了却一个字没改＝空转。必须单独计数：算进 ok 会让「改段 18/20」这种数字
            # 虚高得离谱——实测有一轮报 18 段、真正动过的只有 4 段（2026-08-12）。
            noop += 1; seen.add(n); continue
        if check and content and not check(paras[n - 1], content):
            bad.append((f"#{n} 违反本步约束", content[:40])); continue
        seen.add(n); new[n - 1] = content; ok += 1
    return [x for x in new if x.strip()], ok, bad, noop


# 2026-08-12 用户拍板去掉第 2 步的 CHECK_INSERT（连同「只加不改」那条铁律）：
# 整行替换已经从机制上保证「没报出的段落一个字不变」，剩下的只是「报出的那一段内部
# 允不允许润色」——放开。代价是第 2 步不再可被代码校验，靠模型自觉；
# 收益是它能顺手把补进去的内容与上下文捋顺。
# 第 3 步的 CHECK_PAREN 保留：它保证的是另一件事——「去掉全部括号 = 回到原话」这个可逆性，
# 那是当初选括号方案的理由，不是防改写。


# 全角与半角圆括号都认。⚠️ **只认全角是个语言 bug**（2026-08-30 修）：中文稿的模型吐全角，
# 法语/西语稿的模型吐半角 `(...)`——于是「去括号后逐字相等」这一条永远不成立，整段被判丢弃，
# 丢弃率 100% ⇒ **整步判失败**。实测一份法语访谈稿两次跑，一次因此失败，另一次侥幸吐了全角、
# 却在法语句子里留下一个全角括号（排版是错的）。判据要看**括号这件事**，不看它是哪种字形。
_PAREN = re.compile(r"[（(][^（）()]*[）)]")


def CHECK_PAREN(a, b):
    """只加括号：两边都去掉圆括号后正文必须逐字相等，且括号只增不减。

    ⚠️ 不能写成 `strip(b) == a`（2026-08-12 的原版就是这么写的，是个 bug）：
    稿中**本来就可能有括号**（第 1 步产出的、或上一轮加的），strip(b) 会把新旧括号一并去掉，
    而右边的 a 那些旧括号还在 → 永远不相等。实测一整段做得完全正确（去括号后逐字相同、
    括号 1→2）却被误拦，连带丢掉该段全部新增括号。
    """
    # ⚠️ 去掉括号之后要**归一空白**：拉丁语言写的是 `Leur (xxx) interlocuteur`，
    # 摘掉括号剩下两个空格，与 `Leur interlocuteur` 逐字比对必然不等——中文没有这个问题
    # （`他们（xxx）的` 摘掉就是 `他们的`），所以这个坑只在非中文稿上出现。
    st = lambda t: re.sub(r"\s+", " ", _PAREN.sub("", t)).strip()
    if st(b) != st(a):
        return False
    # 原有括号必须原样还在（按序），新括号只能是**额外插入**的。
    # 只比个数不行：删掉旧括号、另加一个新的，个数不变却把原有注释换掉了。
    pa, pb, i = _PAREN.findall(a), _PAREN.findall(b), 0
    for x in pb:
        if i < len(pa) and x == pa[i]:
            i += 1
    return i == len(pa)


# ---------------- 接缝重复检测 ----------------
# 用户提的风险（2026-08-12）：同一段原话被两个批次各改写一遍，两份产出**语义相同、字面不同**，
# 字面比对抓不到。所以判据不看措辞，看**它讲的是原文哪几行**——原文行号是不变的锚点，
# 改写怎么变都改不了「这一段讲的是第 47–52 行」这个事实。两个产出段锚到同一行 = 重复。
# 阈值由扫描定档（2026-08-12）：拿未分批那份长稿做负样本（它不该有任何重复）、把某段改写后
# 复制一份做正样本，扫 MIN×COV 网格。这一档是唯一「三种改写强度 12/12 全召回」里误判最少的
# （5 种切点累计 2 组）。**故意偏召回**：两种失败的代价不对称——漏判会把重复段留在成稿里，
# 误判只是让第 4 步多看一眼，而它有拒绝合并的出口（见 STEP4_DUP 最后一段）。
_ROW_MIN = 10        # 该行至少要有这么多 token 才拿来锚：token 太少的行（「对」「嗯」）匹配不可靠
_ROW_COV = 0.45      # 该行有多少（IDF 加权的）token 落进这一段，才算「这一段讲了它」


def _gram(s):
    """中文按 2-gram、英文/数字按**整词**切。

    不用字符级 3-gram：那样一个 9 字母的英文词能产出 7 个 gram，而中文一个词只产出 1 个，
    专名的权重被放大近一个数量级。实测误判（2026-08-12）：某行只有 15 个 3-gram，
    其中 7 个来自「ModelArts」——另一段只是顺带提了一次这个词，覆盖率就到 47%，
    被判成「讲的是同一行」。整词切之后该词只占 1/6，同一对降到 17%，判对。
    """
    s = re.sub(r"[，。！？、：；…—“”‘’（）()《》,.!?:;\"'\s]", "", s)
    out = set()
    for p in re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]+", s):
        if p[0].isascii():
            out.add(p.lower())
        else:
            out |= {p[i:i + 2] for i in range(len(p) - 1)}
    return out


_HOST = re.compile(r"^\s*(?:M|主持人|访谈者)\s*[：:]")


def dup_scan(paras, lines, batch_of):
    """→ [(原文行区间, A段号, B段号)]；batch_of[i] = 第 i 段出自第几批。
    只报**跨批次**的锚点重叠：批内相邻段是同一次生成的，模型不会自己重复自己。

    覆盖率按 **IDF 加权**算，不按 token 计数。不加权的话短套话行会被任意两段命中——
    实测（2026-08-12）「是，基本上能保证，没有问题。」这类行的 token 全是「这个/我们/相对/
    没有/问题」，出现在全文几十行里，任何两段都能覆盖到 45%，误判成片。加权后这些 token
    权重趋近 0，只有稀有词（专名、具体数字、独特搭配）才撑得起覆盖率——而真重复必然共享它们。
    主持人的行一并排除：那不是产出内容的来源，锚它们纯属噪音（上一版误判有一半出在这儿）。
    """
    rows = [l for l in lines if not _HOST.match(l["text"])]
    df = Counter()
    for l in rows:
        df.update(_gram(l["text"]))
    n_doc = max(1, len(rows))

    def w(t):
        return math.log(1 + n_doc / (1 + df.get(t, 0)))

    pg = [_gram(p) for p in paras]
    owner = {}                                  # 原文行 → [锚到它的产出段号]
    for l in rows:
        g = _gram(l["text"])
        if len(g) < _ROW_MIN:
            continue
        tot = sum(w(t) for t in g)
        if tot <= 0:
            continue
        hit = [i for i, q in enumerate(pg) if sum(w(t) for t in g & q) / tot >= _ROW_COV]
        if len(hit) > 1:
            owner[l["n"]] = hit
    pairs = {}
    for n, hit in owner.items():
        for i in range(len(hit)):
            for j in range(i + 1, len(hit)):
                a, b = hit[i], hit[j]
                if batch_of[a] == batch_of[b]:  # 同一批内不算
                    continue
                pairs.setdefault((a, b), []).append(n)
    return [(sorted(v), a, b) for (a, b), v in sorted(pairs.items())]


STEP4_DUP = """
【程序检出的接缝重复 · 共 {n} 组】
第一遍是分批出稿的，下面每一组的两段，程序判定它们讲的是原文**同一批行**——被两个批次各写了一遍。
逐组**合并成一段**，写在靠前那一段的位置上，靠后那一段删掉。

⚠️ 两份改写措辞不同，很可能**各自带了对方没有的细节**。合并时两边有的信息都要保留，
不许只挑一份留下、不许因为「差不多」就丢掉其中一份的内容。这是合并，不是二选一。
（本组属于程序点名的命中项，不受下面「没命中的地方一字不动」约束。）

⚠️ **程序可能判错**：如果你看了两段之后认为它们讲的其实**不是同一件事**（只是话题相近、
用词撞了），就**两段都原样保留、不要合并**——宁可留下一组没合并的，也不要把两件事揉成一件。

{rows}
"""


# ---------------- 分批规划 ----------------

SPK = re.compile(r"^\s*([^：:\s][^：:]{0,20})[：:]")


def parse_lines(text):
    """→ [(行号从1起, 原始行, 是否访谈者行)]。空行与文件头的说明行一并留在流里，
    只有说话人行参与边界判定。"""
    out = []
    for n, ln in enumerate(text.split("\n"), 1):
        m = SPK.match(ln)
        spk = m.group(1).strip() if m else ""
        is_host = spk in ("M", "m", "主持人", "访谈者")
        out.append({"n": n, "text": ln, "host": is_host, "spk": spk})
    return out


def plan(lines, budget):
    """按字符预算切批，**边界只落在「下一个说话人行是访谈者」处**。
    问答对若被切开，「把访谈者提问并入被访者叙述」这条规则就没法执行。"""
    spans, lo, acc = [], 1, 0
    for idx, l in enumerate(lines):
        acc += len(l["text"])
        if acc < budget:
            continue
        nxt = next((x for x in lines[idx + 1:] if x["spk"]), None)
        if nxt is None:
            break
        if nxt["host"]:                       # 下一轮由访谈者起头 → 这里可以切
            spans.append((lo, nxt["n"] - 1))
            lo, acc = nxt["n"], 0
    spans.append((lo, len(lines)))
    return spans


# ---------------- 调用 ----------------

def call(system, user, tries=4):
    # 本机版（与线上不同）：地址、模型、密钥变量、额外参数都取「设置」里那一份模型后端
    # （pipeline/model_backend），线上这里写死 DeepSeek。reasoning_effort 是 DeepSeek 专属，随预设的 extra 走。
    url, auth, model, extra = model_backend.openai_endpoint()
    headers = {**auth, "Content-Type": "application/json"}
    body = {"model": model, "temperature": TEMP,
            "max_tokens": MAXTOK,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}], **extra}
    data = json.dumps(body).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                res = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and i < tries - 1:
                time.sleep(8 * (i + 1)); continue
            raise
        except Exception:
            if i < tries - 1:
                time.sleep(8 * (i + 1)); continue
            raise
    ch = res["choices"][0]
    u = res.get("usage", {})
    hit, miss = u.get("prompt_cache_hit_tokens"), u.get("prompt_cache_miss_tokens")
    if hit is None and miss is None:
        miss, hit = u.get("prompt_tokens", 0), 0
    acc = {"hit": hit or 0, "miss": miss or 0,
           "completion": u.get("completion_tokens", 0),
           "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0}
    try:      # 埋点失败一律吞掉——记账坏了不该让出稿跟着失败（同 p3_health 的纪律）
        _USAGE["hit"] += acc["hit"]
        _USAGE["miss"] += acc["miss"]
        _USAGE["out"] += acc["completion"]
        _USAGE["calls"] += 1
    except Exception:  # noqa: BLE001
        pass
    return (ch["message"].get("content") or "").strip(), ch.get("finish_reason"), acc


_FENCE = re.compile(r"^\s*```.*$")
_PREAMBLE = re.compile(r"^\s*(以下是|下面是|好的|本批|【).*[:：]?\s*$")


def clean(txt):
    """去围栏、去行号残留、去前言行。宁可多留也不误删正文——只砍明确的壳。"""
    lines = [l for l in txt.split("\n") if not _FENCE.match(l)]
    while lines and (not lines[0].strip() or _PREAMBLE.match(lines[0])):
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(re.sub(r"【\d+】", "", l) for l in lines)


_RANGE = re.compile(r"(起|止)\s*[=＝:：]\s*(\d+)")
_ROW = re.compile(r"^\s*\|?\s*\d+\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]*)")


def parse_stage1(txt, n_lines):
    """阶段 1 产出 → (实质区间, [(起, 止, 话题)])。解析不出就退回全篇，绝不因此少写内容。"""
    rng = dict((m.group(1), int(m.group(2))) for m in _RANGE.finditer(txt))
    lo, hi = rng.get("起", 1), rng.get("止", n_lines)
    topics = []
    for ln in txt.split("\n"):
        m = _ROW.match(ln)
        if m and "起行" not in ln:
            topics.append((int(m.group(1)), int(m.group(2)), m.group(3).strip()))
    return (max(1, lo), min(n_lines, hi)), topics


def plan_by_topic(topics, lines, budget, n_lines):
    """话题段 → 批：按字符预算合并相邻话题段，边界只落在话题边界上。
    话题表解析失败时返回 None，由调用方退回字符预算切分。"""
    if not topics:
        return None
    spans, cur, acc = [], None, 0
    for a0, b0, _ in topics:
        n = sum(len(l["text"]) for l in lines[a0 - 1:b0])
        if cur is None:
            cur, acc = [a0, b0], n
        elif acc + n <= budget:
            cur[1], acc = b0, acc + n
        else:
            spans.append(tuple(cur)); cur, acc = [a0, b0], n
    if cur:
        spans.append(tuple(cur))
    spans[0] = (1, spans[0][1])                 # 首批从第 1 行起、末批到末行止：
    spans[-1] = (spans[-1][0], n_lines)         # 非实质区间也要划进来，否则那几行谁都不负责
    return spans


# ---------------- 生产入口 ----------------

def _issues_md(stages, drops, ui_lang=None):
    """问题清单：pp_runner 读 `<输出去 .md>_issues.md`。**计数由代码算**（各步真正落地的段数
    之和），比让模型自己数准——它数的是它想改的，代码数的是实际改成的。

    ⚠️ **这份是代码拼的模板，不是模型写的**，所以 skill 后面那段语言指令对它无效
    （2026-08-30 真跑一遍才发现：产物已经是法语，清单还是中文）。文案走 pp_lang.report()。
    ⚠️ 机读的计数走单独一行 `<!-- qc-fix-count: N -->`，不靠解析「共修复 N 处」——
    翻译之后那个正则抓不到，而症状是「质检已修复 0 处」，不报错。
    """
    t = pp_lang.report(ui_lang)
    n = sum(s["applied"] for s in stages)
    sec = {"2 语义补漏": t["sec_semantic"], "3 指代消解": t["sec_reference"], "4 质检": t["sec_qc"]}
    # ⚠️ 标题不许带引擎名或「降级」字样——这份清单用户能下载（见 pp_redact_ds._qc_md 同款注释）
    out = [f"# {t['issues_title']}", ""]
    for s in stages:
        title = sec.get(s["name"])
        if not title:
            continue
        out.append(f"## {title}")
        out.append("")
        if s["applied"] == 0:
            out.append(t["none"])
        else:
            out.append(t["changed"].format(n=s["applied"], ids="、".join(str(x) for x in s["ids"])))
            if s["dropped"]:
                out.append(t["not_applied"].format(n=len(s["dropped"]))
                           + "；".join(w for w, _ in s["dropped"][:5]))
        out.append("")
    if drops:
        out.append(f"## {t['appendix']}")
        out.append("")
        out += [f"- {w}：{f[:60]}" for w, f in drops]
        out.append("")
    out.append(t["total"].format(n=n))
    out.append(pp_lang.fix_count_line(n))
    return "\n".join(out) + "\n"


def narrate(in_path: Path, out_path: Path, *, directive: str = "", ui_lang: str | None = None) -> str:
    """跑完整五步，写出 out_path 与 `<out_path 去 .md>_issues.md`。
    返回 "ok" | "failed"。异常一律吞成 failed 并打日志——降级路挂掉不该连累主链路。"""
    t_all = time.time()
    try:
        raw = Path(in_path).read_text(encoding="utf-8")
        lines = parse_lines(raw)
        if not lines:
            _log("输入解析不出任何说话人行 → failed")
            return "failed"
        numbered = "\n".join(f"【{l['n']}】{l['text']}" for l in lines)
        system = build_system() + directive   # 语言指令追加在规则之后，所以它覆盖 skill 的默认设定
        _log(f"起：{Path(in_path).name} {len(raw)} 字 / {len(lines)} 行｜{MODEL}/{EFFORT}")

        # ── 步 0 勘定：实质区间 + 话题边界（只判一次，各批沿用，避免各判一套）──
        s0, _, _ = call(system, numbered + "\n\n" + STAGE1_USER)
        (lo, hi), topics = parse_stage1(s0, len(lines))
        region = STAGE2_EXTRA.format(lo=lo, hi=hi, topics="")
        _log(f"步0 勘定：实质区间 {lo}–{hi} 行，话题 {len(topics)} 段")

        # ── 步 1 视角转换：唯一分批的一步 ──
        spans = plan_by_topic(topics, lines, STEP1_BUDGET, len(lines)) or plan(lines, STEP1_BUDGET)

        def one(i, x, y):
            seam = (S1_HEAD if i == 0 else S1_TAIL if i == len(spans) - 1 else S1_MID)
            user = (numbered + "\n\n" + STEP1_USER + region
                    + STEP1_BATCH_RANGE.format(i0=x, i1=y, seam=seam.format(i0=x, i1=y)))
            txt, fin, _ = call(system, user)
            return i, clean(txt), fin

        with ThreadPoolExecutor(max_workers=CONC) as ex:
            got = sorted(ex.map(lambda t: one(t[0], t[1][0], t[1][1]), enumerate(spans)))
        parts = [g[1] for g in got if g[1]]
        if not parts:
            _log("步1 四批全空 → failed")
            return "failed"
        cur = "\n\n".join(parts)
        _log(f"步1 视角转换：{len(spans)} 批并发 → {len(cur)} 字")

        # 接缝重复检测就在此刻做：只有现在「哪一段出自哪一批」才是确凿的
        p1 = [x for p in parts for x in p.split("\n\n") if x.strip()]
        batch_of = [bi for bi, p in enumerate(parts) for x in p.split("\n\n") if x.strip()]
        dups = dup_scan(p1, lines, batch_of)
        drows = "\n".join(
            f"组 {k+1}（原文第 {v[0]}–{v[-1]} 行，共 {len(v)} 行被写了两遍）：\n"
            f"  · 靠前那段 = **#{i+1}**，开头是「{p1[i][:36]}…」\n"
            f"  · 靠后那段 = **#{j+1}**，开头是「{p1[j][:36]}…」"
            for k, (v, i, j) in enumerate(dups))

        stages, all_drops = [], []

        def step(name, mk_user, check=None):
            nonlocal cur
            nd, paras = number_draft(cur)
            out, fin, _ = call(system, mk_user(nd))
            pats = parse_line_patches(out)
            new_paras, ok, bad, noop = apply_line_patches(paras, pats, check)
            drop_rate = len(bad) / max(1, len(pats))
            cur = "\n\n".join(new_paras)
            stages.append({"name": name, "applied": ok, "reported": len(pats),
                           "noop": noop, "dropped": bad,
                           "ids": [n for n, _ in pats][:40]})
            all_drops.extend(bad)
            _log(f"{name}：改段 {ok}/{len(pats)}（丢弃 {len(bad)}｜空报 {noop}）→ {len(cur)} 字")
            if drop_rate > DROP_WARN:
                _log(f"⚠️ {name} 丢弃率 {drop_rate:.0%}（>{DROP_WARN:.0%}）")
            return drop_rate

        r2 = step("2 语义补漏", lambda nd: numbered + PASS2_USER.format(draft=nd))
        r3 = step("3 指代消解", lambda nd: numbered + STEP3_USER.format(draft=nd), CHECK_PAREN)
        _, cur_paras = number_draft(cur)
        rows = qc_scan(cur_paras)
        hits = (STEP4_HITS.format(n=len(rows), rows="\n".join(rows)) if rows else STEP4_NOHITS)
        if dups:
            hits += STEP4_DUP.format(n=len(dups), rows=drows)
        _log(f"步4 程序扫描：命中 {len(rows)} 处｜接缝重复 {len(dups)} 组")
        r4 = step("4 质检", lambda nd: numbered + STEP4_USER.format(draft=nd, hits=hits))

        worst = max(r2, r3, r4)
        if worst > DROP_FATAL:
            _log(f"丢弃率 {worst:.0%} 超过 {DROP_FATAL:.0%} → 判失败（模型多半没理解这一步）")
            return "failed"
        if not cur.strip():
            _log("终稿为空 → failed")
            return "failed"

        Path(out_path).write_text(cur + "\n", encoding="utf-8")
        iss = Path(str(out_path)[:-3] + "_issues.md" if str(out_path).endswith(".md")
                   else str(out_path) + "_issues.md")
        iss.write_text(_issues_md(stages, all_drops, ui_lang), encoding="utf-8")
        _log(f"完成：{len(cur)} 字（原文 {len(raw)} 字）｜{time.time() - t_all:.0f}s"
             f"｜共修复 {sum(s['applied'] for s in stages)} 处")
        return "ok"
    except Exception as exc:  # noqa: BLE001  降级路挂掉不该连累主链路
        _log(f"异常 → failed：{type(exc).__name__}: {exc}")
        return "failed"

"""后处理·脱敏的 DeepSeek 降级路（pp-redact 的 Flash 版）。

**定位与 `pp_deepseek.py` 一致**：Claude 撞顶/闸满时的兜底，不是并列选项。
2026-08-13 上游实测（17781 字访谈稿，对标 `claude -p (Opus-medium)` 的 12 处）：
最难那行（645 字 / 10 处地名）能做到与 Opus 逐字一致，成本约 Opus 的 1/80。
⚠️ **但单轮不是结论**：单遍扫描三轮跑出 12 / 11 / 11 处，漏的还不是同一处——
这正是下面「扫描跑两遍取并集」的由来。拿一轮好成绩当验收结论是错的
（`docs/p3-cost-quality-research.md` 早写过这条纪律，我自己踩了一次）。

**产出形态与视角转换不同，这是本模块所有设计的来源**：

    视角转换：改写全文 → 模型必须重吐段落 → 分批
    脱敏    ：只换几个词 → 模型只报一张**替换单**，程序动手替换

替换单的形式是 `行号 | 原词 | 替换词`。程序按单子替换，**结构上保证**三件事：
  ① 模型没机会改别的——只有单子上的词会被动，其余一字不动
     （SKILL.md 核心约束第 2 条「仅做替换操作，不得增删其他内容」由此变成程序事实）
  ② 报了原稿里没有的词 → 找不到就丢弃，不会乱改
  ③ 长度约束（±5%）天然成立

**规则来源唯一**：system 直接取 `pp-redact/SKILL.md` 的 `# 处理规则` 到文件末尾原文
+ 共性质检.md 全文，一个字不改。被剥掉的只有开头 `# 工作流程`（讲 Read/Write 哪个文件的
执行外壳，Flash 没有文件工具，那层由本模块承担）。**改 SKILL.md 会自动生效，不用动这里。**

**为什么定档「单遍扫描、不做剔除复核」**（2026-08-13，Duner 定）。

判定的优先级是：**① 不能有红线错误（把事实改错）② 不能漏 ③ 多脱敏最轻**。
三种配置各跑三轮的实测：

| 配置 | 漏 | 红线错误（改错事实） |
|---|---|---|
| **单遍（现在这个）** | 每轮漏 1–2 处 | 少 |
| 两遍取并集，不复核 | 三轮全零漏 | 2–3 处：「北京工信部」→「A地工信部」、「我一个人在运维」→「少数人在运维」 |
| 两遍并集 + 剔除复核 | 三轮里一轮漏 2 处 | 少 |

并集对「漏」是单调改善、对「脱错」是单调恶化——两遍里较激进那遍的错判也被一并收下。
加剔除复核能压住脱错，但它**有能力剔掉真该脱的，且剔错时理由写得很像样**：
A4 轮把「我要先协调西安市、协调陕西省」判成「第三方政府机构所在地」剔掉了，
而那正是受访者属地。「指不出依据就保留」这条尺度拦不住它——它自认为有依据。

所以按优先级排下来选单遍：漏由 QC 报告提示人工复核兜，红线错误没法靠人快速发现。
`RD_DS_PASSES=2` / `RD_DS_REVIEW=1` 可以把另两条路开回来（代码与提示词都在），
将来 Flash 换代了照着重测一轮即可。

**为什么必须分批**：2026-08-13 实测整篇一次发只报出 1 条（Opus 基准 12 处）；
把漏掉的那一行单独发，它立刻报出 6 条——是注意力不是判断力，即
`docs/p3-cost-quality-research.md` §5 的清单长度效应（规则本身 382 行，再叠 102 行稿子就顾不过来）。
脱敏分批安全：每处判断基本独立；唯一需要全局的一致性由「代号消解 + 替换后扫残留 + 质检」兜。

⚠️ **按出现处替换，不做全文替换**。看着与 SKILL 的 N8「同实体全文统一」相悖，其实不然：
N8 说的是同一**实体**，不是同一**字符串**。Opus 基准里「北京区域」的北京要换成 A 地、
「北京工信部」的北京必须保留——同字不同实体。无脑全文替换会把国家部委也改掉。

**SKILL 六步全做**，编排上第 4、5 步合成一次调用（省一次全文往返），中间多一步剔除复核：
  1 读入（含保留词清单）→ 2 分批扫描 → 3 程序落地替换
  → 4+5 自检核对 + 质检（一次调用）→ 6 写产物与 QC 报告
"""
import difflib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import pp_lang
from .pp_deepseek import EFFORT, MODEL, call

# 本机版：提示词生成进包里（同 pp_deepseek 与定字提示词，计划页 g4）
from ._pp_prompts import PP_REDACT as SKILL_MD, SHARED_QC  # noqa: E402

# 与视角转换的第 1 步同口径（6000 字/批、6 路并发）。**不要为了快而调大 BUDGET**：
# 上面说的清单长度效应就是它带来的，调大等于把已经修好的漏判重新请回来。
BUDGET = int(os.environ.get("RD_DS_BUDGET", "6000"))
CONC = int(os.environ.get("RD_DS_CONC", "6"))

# 扫描跑几遍取并集 / 并集后要不要剔除复核。**定档 1 遍、不复核**（2026-08-13，理由见模块头）。
# 两条路径的代码与提示词都留着（env 可开），它们各自的失败模式记在下面的注释里——
# 将来 Flash 换代了，照着重测一轮就知道能不能启用，不用从头再推一遍。
PASSES = int(os.environ.get("RD_DS_PASSES", "1"))
REVIEW = os.environ.get("RD_DS_REVIEW", "0") == "1"


def _log(msg):
    print(f"[RD-DS] {msg}", flush=True)


# ---------------- 规则装配 ----------------

def build_system() -> str:
    """SKILL.md 的规则正文逐字取用（`# 处理规则` 到末尾），只换掉文件读写那层外壳。"""
    md = SKILL_MD.read_text(encoding="utf-8")
    rules = md[md.index("# 处理规则"):].rstrip()
    qc = re.sub(r"^---\n.*?\n---\n", "", SHARED_QC.read_text(encoding="utf-8"), flags=re.S)
    return f"""你是访谈笔录处理专家。本次任务：**信息脱敏**。

⚠️ 与常规做法不同：**你不需要输出脱敏后的全文**。
正文由程序按你给出的替换单改写，你只负责判断「哪一处的哪个词该换成什么」。
这样可以保证除你指名的词以外，正文一个字都不会变。

下面是脱敏的全部规则，逐条严格遵守。

{rules}

# 共性质检清单

{qc}
"""


# 用户的脱敏保留词清单（可选）。附在每一批 user 消息末尾——附在 system 里会把
# 缓存前缀打散（system 是唯一命中率 98.9% 的那段），而它每单都不同。
KEEP_TPL = """
【用户保留词清单】以下词**绝不脱敏**（唯一例外：受访者所属公司本身在清单里时，身份保护优先）：
{keep}
"""

SCAN_TPL = """下面是带行号的访谈稿。请按规则找出所有需要脱敏的地方，输出一张**替换单**。

【输出格式】每行一条，用 ` | ` 分隔三段，不要写别的：

行号 | 原词 | 替换词

【示例】
59 | 北京 | A地
1 | 两年多 | 两三年

【必须遵守】
1. **原词必须是该行中一字不差存在的片段**，不要写概括、不要写整句。
2. **原词默认写最小片段**——只写要被替换掉的那个专名/短语本身，不要带上前后的动词、
   助词和标点，更不要把两处合成一条（写「北京」，不写「从北京切换到上海区域」）。
   **只有**当该行里同一个词有的要换、有的要保留时，才写长到刚好能区分为止。
3. **替换词与原词要一一对应**：不要顺手删掉原词里的其他内容，也不要添字。
4. 同一实体在不同行出现，就**逐行各报一条**——不要指望一条通吃全文。
5. 若某处不需要脱敏，就不要出现在单子里。**没有任何需要脱敏的内容时，输出一行 `无`。**
6. 不要输出解释、不要输出脱敏后的正文、不要加代码围栏。

【访谈稿】
{numbered}
"""

# 并集之后的**剔除复核**（2026-08-13 加）。并集对「漏」是单调改善、对「脱错」是单调恶化：
# 两遍里较激进的那一遍的错判也被一并收下了（实测把「北京工信部」当地名脱成「A地工信部」、
# 把「我一个人在运维」改成「少数人在运维」——后者连事实都变了）。
# 所以并集管**召回**、这一步管**准确**，分工清晰。
#
# ⚠️ 这一步在上游做过一次并被禁用，当时它在 3 条和 10 条之间摇摆。真因不是这一步不成立，
# 而是**我在提示词里举的例子把答案指反了**（举了「第三方厂商自身的机房/区域所在地」，
# 而华为云恰好有北京/上海区域 → 模型照着例子把真该脱的剔了）。所以这版**一个具体例子都不举**，
# 只给抽象判据；要判「不脱敏」必须能指出规则里的具体条目，指不出就照脱。
#
# ⚠️ 两段的标签**不能叫「保留/剔除」**（2026-08-13 踩过）：我本意是「保留/剔除清单条目」，
# 但在脱敏语境里对着一个词说「保留」，天然读成「这个词留在原文里不动」——正好相反。
# 模型按后一种读法答得完全正确，是提问方式错了。所以标签直接命名对词的动作：脱敏 / 不脱敏。
#
# 另一处改进：**按行分组**呈现，每行原文只给一次。这样同一行里的同名词会并排出现，
# 而「同字不同实体」的区分正是这一步唯一要做的判断——平铺成一条一行反而看不出来。
REVIEW_TPL = """下面是分批扫描汇总出来的**待判定清单**，按行分组，每组先给出该行原文。
请逐条判定：这个词在**这一处**，按规则该不该脱敏。

⚠️ 判定尺度
1. 依据只能来自你的规则。**能指出具体依据才判「不脱敏」**；指不出依据、或有一分拿不准，
   一律判「脱敏」。漏脱敏会泄露受访者身份，是事故；多脱敏只是稿子不够具体。
2. 同一行里同一个字面词，可能这一处该脱、那一处该留——取决于它在**那一处**指的是什么。
   请逐处判断，不要按字面一刀切。
3. 只判「脱还是不脱」。不要改写正文，**也不要新增清单里没有的条目**。

【输出格式】严格分两段，两段的第三列含义不同，注意区分：

===脱敏===
行号 | 原词 | 替换成什么
（这一处**要**脱敏。原词一字不改——它要用于定位；替换词不当可以改。一条都没有时写 `无`）

===不脱敏===
行号 | 原词 | 依据（规则里的哪一条哪一项）
（这一处**不**脱敏，原词原样留在稿子里。一条都没有时写 `无`）

不要输出别的解释、不要加代码围栏。

【待判定清单】
{items}
"""


# 第 4 步「回头核对」与第 5 步「质检」合成一次调用。
# ⚠️ **不发全文是有意的**（这是 359s → 240s 的大头）：脱敏只动了几行，其余由程序保证
# 一字未动（机械体检已验证）。但「未改动行的摘要」这一块**不能省**——省掉它，
# 扫描阶段整行漏掉的地方自检就看不见了（2026-08-13 实测：12 处掉到 7 处）。
CHECK_TPL = """这是 skill 工作流程第 4–5 步：**回头核对 + 质检**，一次做完。

⚠️ **不发全文是有意的**：脱敏只动了下面列出的那几行，其余部分由程序保证一字未动
（机械体检已验证）。找漏网敏感信息是上一步分批全文扫描的职责，这里不重复劳动。

【程序已做完的机械体检】（这几项你不必再查）
{mech}

【程序扫出的同词残留】被换掉的词在别处还明文出现的位置。
同字不同实体是合法的（受访者资源所在的城市要换、同名的国家部委要留），请逐条判断：
{resid}

【被护栏拦下、尚未落地的条目】这些是上一步报了但程序拒收的，原因附在后面。
**请用最小片段重新报一次**（只写要替换掉的那个词本身，别带标点和前后的字）：
{rejected}

【本次改动的行】改动前 → 改动后。
⚠️ **一行改过 ≠ 这一行改全了**：一行里往往有好几处敏感信息，扫描阶段可能只逮住其中几处。
请把「改动后」当成一份还没检查过的稿子逐处再看一遍，尤其是**地名**。
{diffs}

【完整替换单】
{done}

【尚未做任何脱敏的行】扫描阶段没在这些行里发现敏感信息，请复核一眼有没有漏网：
{untouched}

请做四件事，然后按格式输出：
0. **找漏网**：复核「本次改动的行」的改动后文本、「尚未脱敏的行」、「被拦下的条目」
   三处，把该脱敏而没脱的补上。这是本步最重要的一件事——漏脱敏会泄露受访者身份。
1. 查有没有**该保留的被误脱**——用户保留词清单里的词、第三方厂商/品牌/产品名、技术术语。
2. 判断上面的残留里，哪些该一并处理。
3. 逐项执行【共性质检清单】与【质检规则——脱敏专项】里**机器判不了的语义项**
   （同实体是否统一、受访者公司名及变体是否残留、省市级地名是否残留、
    是否误脱了第三方厂商自身地点）。

【输出格式】严格分两段：

===修复单===
补 | 行号 | 原词 | 替换词        ← 补脱敏（含残留里该处理的）
撤 | 行号 | 当前写法 | 还原成什么   ← 误脱的还原回去
（无需修复则写 `无`）

===质检结果===
逐项写「检查项：通过」或「检查项：发现 N 处并修复」，问题逐条列「原文→修复后」。
"""


# ---------------- 替换单：解析与落地 ----------------

# 行号两种写法都收：模型常写「行1」而不是「1」，只认后者会让整轮自检成果凭空丢掉
# （2026-08-13 实测：自检明明补报了，程序却打出「修正 0 条」——模型是对的，是代码没接住）
_ROW = re.compile(r"^\s*(?:行)?\s*【?(\d+)】?\s*\|\s*(.+?)\s*\|\s*(.*?)\s*$")
_ACT = re.compile(r"^\s*([补撤])\s*\|\s*(?:行)?\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*$")


def parse_subs(txt: str) -> list[tuple[int, str, str]]:
    """解析替换单。解析不出的行直接跳过——宁可少换，不能乱换。"""
    out = []
    for ln in txt.split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith("```") or ln in ("无", "（无）"):
            continue
        m = _ROW.match(ln)
        if m:
            out.append((int(m.group(1)), m.group(2), m.group(3)))
    return out


def parse_actions(txt: str):
    """解析自检输出的「补 |」「撤 |」两类动作 → 统一成 (行号, 原词, 替换词) 三元组。"""
    acts = []
    for ln in txt.split("\n"):
        m = _ACT.match(ln.strip())
        if m:
            acts.append((int(m.group(2)), m.group(3), m.group(4)))
    return acts


def apply_subs(lines: list[str], subs) -> tuple[list[str], list, list]:
    """按出现处替换。→ (新行, 落地清单, 丢弃清单)。丢弃只丢这一条，不影响其余。"""
    new = list(lines)
    ok, drop = [], []
    # **按原词长度降序**：先替换「北京区域」再替换「北京」。反过来的话「北京」先被换成
    # 「A地」，「北京区域」就成了「A地区域」，原词再也匹配不上 → 整条被丢弃
    # （2026-08-13 实测一次丢了 3 条）。
    for no, old, rep in sorted(subs, key=lambda x: -len(x[1])):
        if not (1 <= no <= len(new)) or not old:
            drop.append((no, old, rep, "行号越界" if not (1 <= no <= len(new)) else "原词为空"))
            continue
        if old not in new[no - 1]:
            drop.append((no, old, rep, "该行找不到原词"))
            continue
        # 删减护栏：diff 出的删除块若含标点，说明它把两处合成了一条、顺手删了中间的内容
        # （实测：「将近三年的时间，两年多」→「数年的时间」把「，两年多」删没了，
        #  违反核心约束第 2 条「仅做替换操作，不得增删其他内容」）。
        cut = "".join(old[i1:i2] for t, i1, i2, _, _ in
                      difflib.SequenceMatcher(None, old, rep).get_opcodes() if t == "delete")
        if any(c in cut for c in "，。；、！？,.;!?"):
            drop.append((no, old, rep, f"跨标点删减（丢了「{cut}」）"))
            continue
        cnt = new[no - 1].count(old)
        new[no - 1] = new[no - 1].replace(old, rep)
        ok.append((no, old, rep, cnt))
    return new, ok, drop


def find_residual(lines: list[str], ok: list) -> list:
    """替换后扫残留：被换掉的原词是否还在别处明文出现。

    **只报不改**——同字不同实体是合法的（北京区域 vs 北京工信部），程序判不了，
    交给自检那一步的模型判。
    """
    res = []
    for no, old, rep, _ in ok:
        for i, ln in enumerate(lines, 1):
            if i != no and old in ln:
                res.append((old, rep, i, ln[:60]))
    return res


# ---------------- 代号统一（程序做，不靠模型）----------------

_CODE = re.compile(r"^([A-Z])(地|公司|项目|机构|平台)$")
# 归一用的地名后缀。只剥一层、不做别的加工——多剥会把「西安」剥成「西」
_SUF = ("汽车专区", "数据中心", "区域", "地区", "专区", "省", "市", "区", "县")


def _canon(e: str) -> str:
    """归一到实体本体：上海区域 / 上海市 → 上海。

    不归一的话，同一个地方以「北京」和「北京区域」两种片段被报出来，会被当成两个实体
    各发一个代号——比代号撞车更糟（同一地两个代号）。
    """
    for suf in _SUF:
        if e.endswith(suf) and len(e) > len(suf):
            return e[:-len(suf)]
    return e


def extract_entity_map(subs):
    """从 (原词, 替换词) 对里 diff 出**实体级**映射 [(行, 实体, 代号)]。

    模型报的原词常是复合片段（「北京和上海的」），直接按片段分组看不出
    「北京」和「乌兰察布」拿了同一个代号。字符级 diff 拆开，冲突才浮得出来。
    """
    out = []
    for no, old, new in subs:
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new).get_opcodes():
            if tag == "replace" and old[i1:i2] and new[j1:j2]:
                out.append((no, old[i1:i2], new[j1:j2]))
    return out


def dedupe_codes(subs):
    """消解跨批代号冲突：同一代号被两个实体占用时，按**首次出现顺序**重新发号。

    只动形如 `X地` / `X公司` 的编号型代号（`本市`/`当地`/`两三年` 这类不是编号，不碰）。
    分批扫描各批不知道别批发了什么号，这一步是唯一能看见全局的地方。→ (新 subs, 冲突说明)
    """
    ent = [(no, _canon(e), c) for no, e, c in extract_entity_map(subs)]
    by_code: dict[str, list[str]] = {}
    first: dict[str, int] = {}
    for no, e, c in ent:
        if not _CODE.match(c):
            continue
        first.setdefault(e, no)
        by_code.setdefault(c, [])
        if e not in by_code[c]:
            by_code[c].append(e)

    conflicts = {c: es for c, es in by_code.items() if len(es) > 1}
    if not conflicts:
        return subs, []

    # 同一后缀（地/公司/…）下的全部实体重新排号：首次出现越早，字母越靠前
    remap: dict[tuple[str, str], str] = {}
    notes = []
    suffixes = {m.group(2) for c in by_code if (m := _CODE.match(c))}
    for suf in suffixes:
        ents = sorted({e for c, es in by_code.items()
                       if _CODE.match(c) and _CODE.match(c).group(2) == suf for e in es},
                      key=lambda e: first[e])
        for k, e in enumerate(ents):
            remap[(e, suf)] = f"{chr(ord('A') + k)}{suf}"
    for c, es in conflicts.items():
        suf = _CODE.match(c).group(2)
        notes.append(f"代号「{c}」被 {len(es)} 个实体占用（{'、'.join(es)}）→ "
                     + "、".join(f"{e}={remap[(e, suf)]}" for e in es))

    new_subs = []
    for no, old, new in subs:
        rebuilt, touched = old, False
        for _, e0, c in extract_entity_map([(no, old, new)]):
            m = _CODE.match(c)
            e = _canon(e0)
            if m and (e, m.group(2)) in remap:
                rebuilt = rebuilt.replace(e, remap[(e, m.group(2))])
                touched = True
        new_subs.append((no, old, rebuilt if touched else new))
    return new_subs, notes


# ---------------- 机械体检 / 分批 / 报告 ----------------

def mech_check(src_lines, out_lines, ok) -> str:
    """机械体检：这几项程序判得比模型准，先算好告诉它，省得它瞎猜。

    覆盖率这项尤其重要——共性质检要求查「内容有没有丢」，而按行替换的结构下
    「除替换处外逐字相同」是可验证的事实，不该让模型去估。
    """
    rows = [f"- 行数：原 {len(src_lines)} → 现 {len(out_lines)}"
            + ("（一致 ✓）" if len(src_lines) == len(out_lines) else "（**不一致，异常**）")]
    a, b = "".join(src_lines), "".join(out_lines)
    rows.append(f"- 字数：原 {len(a)} → 现 {len(b)}"
                f"（{(len(b) / max(1, len(a)) - 1) * 100:+.2f}%，规则要求 ±5% 内）")
    # 把替换处按 (替换词→原词) 逆向还原，应与原文逐字相同
    restored = list(out_lines)
    for no, old, rep, _ in ok:
        if 1 <= no <= len(restored) and rep:
            restored[no - 1] = restored[no - 1].replace(rep, old)
    same = "".join(restored) == a
    rows.append(f"- 除脱敏处外是否逐字未动：{'是 ✓' if same else '**否，有额外改动**'}")
    rows.append(f"- 本次替换：{len(ok)} 条 / {sum(c for *_, c in ok)} 处")
    return "\n".join(rows)


def plan_batches(lines: list[str]) -> list[tuple[int, int]]:
    """按字数贪心切批，逐行不切开。→ [(起行, 止行)]（1 基，闭区间）"""
    out, start, acc = [], 1, 0
    for i, ln in enumerate(lines, 1):
        acc += len(ln)
        if acc >= BUDGET and i < len(lines):
            out.append((start, i))
            start, acc = i + 1, 0
    out.append((start, len(lines)))
    return out


def split_two(txt: str, a: str, b: str) -> tuple[str, str]:
    """拆 `===a===` / `===b===` 两段。缺段返回空串，不报错。"""
    m = re.search(rf"===\s*{a}\s*===(.*?)(?:===\s*{b}\s*===(.*))?$", txt, re.S)
    if not m:
        return "", ""
    return (m.group(1) or "").strip(), (m.group(2) or "").strip()


def group_by_line(subs, lines) -> str:
    """待判定清单按行分组呈现：每行原文只给一次，其下列出该行的全部条目。

    同一行里的同名词并排出现，「同字不同实体」的区分才看得出来——
    平铺成一条一行时，判断者根本不知道这一行还有别的同名词要区别对待。
    """
    order, by = [], {}
    for no, old, rep in subs:
        if no not in by:
            by[no] = []
            order.append(no)
        by[no].append((old, rep))
    out = []
    for no in sorted(order):
        src = lines[no - 1] if 1 <= no <= len(lines) else "（行号越界）"
        out.append(f"—— 行{no} 原文 ——\n{src}\n本行待判定：")
        out += [f"  行{no} | {o} | {r}" for o, r in by[no]]
    return "\n".join(out)


def split_qc(txt: str) -> tuple[str, str]:
    """拆 `===修复单===` 与 `===质检结果===`。整段都没有标记时，全文当质检结果收下。"""
    a, b = split_two(txt, "修复单", "质检结果")
    return (a, b) if (a or b) else ("", txt)


def _qc_md(src_name, dst_name, has_keep, ok, drop, mech, qc_txt, n_fix, scan_note, cut_txt,
           ui_lang=None) -> str:
    """QC 报告。

    ⚠️ **这份是代码拼的模板，不是模型写的**，所以 skill 后面那段语言指令对它无效
    （2026-08-30）。文案走 pp_lang.report()；类别名直接取界面上已在用的那七个词
    （RedactChanges.tsx + appI18n），同一个类别在界面和报告里不该叫两个名字。
    ⚠️ 机读的计数走单独一行 `<!-- qc-fix-count: N -->`——原本靠解析末行的「共修复 N 处」，
    翻译之后那个正则抓不到，而症状是「质检已修复 0 处」，不报错。
    """
    t = pp_lang.report(ui_lang)
    K = t["kinds"]      # 公司 / 人名 / 联系方式 / 地点 / 数字与规模 / 项目 / 其他
    kinds = {k: [] for k in K}
    for n, o, r, c in ok:
        k = (K[4] if re.search(r"[年月]", o) and not re.search(r"[市省区]", o)
             else K[3] if re.search(r"[市省区地]", o) or re.match(r"^[A-Z][地区]", r)
             else K[0] if "公司" in r else K[6])
        kinds[k].append(f"| {t['line'].format(n=n)} | {o} | {r} | ×{c} |")
    head = f"| {t['th_pos']} | {t['th_orig']} | {t['th_after']} | {t['th_count']} |"
    rec = "\n".join(f"\n### {k}{t['count'].format(n=len(v))}\n\n{head}\n|---|---|---|---|\n"
                    + "\n".join(v) for k, v in kinds.items() if v)
    dropped = (f"\n### {t['sec_dropped']}\n\n"
               + "\n".join(f"- {t['line'].format(n=d[0])} 「{d[1]}」→「{d[2]}」：{d[3]}" for d in drop)) if drop else ""
    cut = f"\n### {t['sec_kept']}\n\n{cut_txt}\n" if cut_txt else ""
    # ⚠️ **报告是用户可下载的产物，一个字都不许提降级/引擎名**（2026-08-14 Duner 定）：
    # 用户不该知道自己这一单被降了级。降级留痕只走 DB 的 degraded_steps → 运营驾驶舱。
    # 标题与 Opus 版一致，两条路的产物对用户无从分辨。
    return f"""# {t['redact_title']}（pp-redact）

- {t['f_in']}：`{src_name}`
- {t['f_out']}：`{dst_name}`
- {t['f_keep']}：{t['provided'] if has_keep else t['not_provided']}
- {t['f_scan']}：{scan_note}

## {t['sec_records']}
{rec or t['empty']}

## {t['sec_result']}

### {t['sub_mech']}

{mech}

### {t['sub_model']}

{qc_txt or t['not_produced']}
{dropped}
{cut}
{t['total'].format(n=n_fix)}
{pp_lang.fix_count_line(n_fix)}
"""


# ---------------- 生产入口 ----------------

def redact(in_path: Path, out_path: Path, keep_path: Path | None = None, *, directive: str = "", ui_lang: str | None = None) -> str:
    """跑完整六步，写出 out_path 与 `<out_path 去 .md>_QC报告.md`。
    返回 "ok" | "failed"。异常一律吞成 failed 并打日志——降级路挂掉不该连累主链路。"""
    t_all = time.time()
    try:
        text = Path(in_path).read_text(encoding="utf-8")
        lines = text.split("\n")
        if not text.strip():
            _log("输入为空 → failed")
            return "failed"
        keep = ""
        if keep_path and Path(keep_path).exists():
            kw = Path(keep_path).read_text(encoding="utf-8").strip()
            if kw:
                keep = KEEP_TPL.format(keep=kw)
        numbered_lines = [f"【{i}】{ln}" for i, ln in enumerate(lines, 1)]
        batches = plan_batches(lines)
        system = build_system() + directive   # 语言指令追加在规则之后，所以它覆盖 skill 的默认设定
        _log(f"起：{Path(in_path).name} {len(text)} 字 / {len(lines)} 行｜{MODEL}/{EFFORT}"
             f"｜{len(batches)} 批（每批约 {BUDGET} 字）｜保留词清单{'有' if keep else '无'}")

        # ── 步 2 分批扫描 × PASSES 遍，取并集 ──
        # 全部 遍×批 一次性丢进池子并发跑：两遍并行才谈得上「墙钟不变」，
        # 串行跑第二遍就是实打实多一倍时间。账号并发 2500，这点路数远不到边。
        def one(t):
            p, k = t
            a, b = batches[k]
            txt, _fin, _acc = call(system, SCAN_TPL.format(
                numbered="\n".join(numbered_lines[a - 1:b])) + keep)
            return p, k, txt

        tasks = [(p, k) for p in range(PASSES) for k in range(len(batches))]
        with ThreadPoolExecutor(max_workers=CONC * PASSES) as ex:
            got = sorted(ex.map(one, tasks))
        # 并集：按 (行号, 原词) 去重，先出现的那一遍说了算。
        # ⚠️ 去重键不含替换词——同一处被两遍报成不同代号时留一个，否则第二个必然
        # 「该行找不到原词」被丢弃，日志里全是噪声。
        subs, seen, added = [], set(), []
        for p in range(PASSES):
            n0 = len(subs)
            for pp_, _k, txt in got:
                if pp_ != p:
                    continue
                for no, old, rep in parse_subs(txt):
                    if (no, old) in seen:
                        continue
                    seen.add((no, old))
                    subs.append((no, old, rep))
            added.append(len(subs) - n0)
        scan_note = (f"{PASSES} 遍取并集，共 {len(subs)} 条"
                     f"（各遍新增 {'/'.join(str(x) for x in added)}）")
        _log(f"扫描 {scan_note}｜{time.time() - t_all:.0f}s")

        # ── 步 2.5 剔除复核：并集管召回，这一步管准确（理由见 REVIEW_TPL 上方）──
        cut_txt = ""
        if REVIEW and subs:
            rv_raw, _fin, _acc = call(system, REVIEW_TPL.format(
                items=group_by_line(subs, lines)) + keep)
            do_txt, cut_txt = split_two(rv_raw, "脱敏", "不脱敏")
            # **只能剔除，不能新增**：结果必须是并集的子集（按 行号+原词 认，替换词允许它改）。
            # 这不是程序替模型判内容，是把它的输出圈在作用域里——万一它又把两段理解反了，
            # 后果就只是「少剔几条」，而不是往正文里塞进清单外的东西
            # （上一版真收到过 `行59 | 北京工信部 | 不脱敏`，解析成功就会把这四个字写进稿子）。
            allow = {(no, old) for no, old, _ in subs}
            kept = [s for s in parse_subs(do_txt) if (s[0], s[1]) in allow]
            # 一条不剩 → 判它是解析/模型失手，不是判断结果，退回并集。
            # **只可能退回并集，不会凭空造条目**：这一步的失败不该把召回也一起赔进去。
            if kept:
                _log(f"剔除复核：{len(subs)} → {len(kept)} 条"
                     f"（判为不脱敏 {len(subs) - len(kept)} 条）")
                subs = kept
            else:
                _log(f"⚠️ 剔除复核返回空 → 退回并集的 {len(subs)} 条（宁可多脱，不可漏脱）")

        # ── 步 3 代号消解 + 落地替换 ──
        subs, notes = dedupe_codes(subs)
        for n in notes:
            _log(f"  代号消解：{n}")
        new, ok, drop = apply_subs(lines, subs)
        _log(f"替换单 {len(subs)} 条 → 落地 {len(ok)} 条"
             f"（实际替换 {sum(c for *_, c in ok)} 处）｜丢弃 {len(drop)} 条")
        for d in drop:
            _log(f"  丢弃：行{d[0]} 「{d[1]}」→「{d[2]}」（{d[3]}）")

        # ── 步 4+5 自检核对 + 质检（合并成一次调用）──
        mech = mech_check(lines, new, ok)
        resid0 = find_residual(new, ok)
        resid_txt = "\n".join(f"- 「{o}」（本处已换成「{r}」）仍明文出现在行{i}：{c}…"
                              for o, r, i, c in resid0[:20]) or "（无）"
        changed = sorted({n for n, *_ in ok})
        diffs = "\n".join(f"行{n}\n  改前：{lines[n - 1]}\n  改后：{new[n - 1]}"
                          for n in changed if 1 <= n <= len(lines)) or "（无）"
        done = "\n".join(f"行{n} 「{o}」→「{r}」×{c}" for n, o, r, c in ok) or "（无）"
        rejected = "\n".join(f"- 行{d[0]} 「{d[1]}」→「{d[2]}」：{d[3]}" for d in drop) or "（无）"
        # 未改动的行只给行号 + 摘要，不给全文：既让自检有机会发现整行漏脱，又不重发一遍全文
        untouched = "\n".join(f"【{i}】{ln[:120]}…" if len(ln) > 120 else f"【{i}】{ln}"
                              for i, ln in enumerate(new, 1)
                              if i not in changed and ln.strip()) or "（无）"
        ck_raw, _fin, _acc = call(system, CHECK_TPL.format(
            mech=mech, resid=resid_txt, diffs=diffs, done=done,
            rejected=rejected, untouched=untouched) + keep)
        fix_txt, qc_txt = split_qc(ck_raw)
        acts = parse_actions(fix_txt)
        # 模型偶尔漏掉「补/撤」前缀直接写三段，一并收下（两个正则互斥，不会重复解析）
        acts += [x for x in parse_subs(fix_txt) if x not in acts]
        n_fix = 0
        if acts:
            new, ok2, drop2 = apply_subs(new, acts)
            n_fix = len(ok2)
            ok += ok2
            drop += drop2
        _log(f"自检+质检：修正 {len(acts)} 条 → 落地 {n_fix} 条")

        resid = find_residual(new, ok)
        if resid:
            _log(f"⚠️ 残留 {len(resid)} 处（同字不同实体属正常，供人工判断）")

        # ── 步 6 写产物 ──
        out = "\n".join(new)
        if not out.strip():
            _log("终稿为空 → failed")
            return "failed"
        Path(out_path).write_text(out, encoding="utf-8")
        qc_path = Path(str(out_path)[:-3] + "_QC报告.md" if str(out_path).endswith(".md")
                       else str(out_path) + "_QC报告.md")
        qc_path.write_text(_qc_md(Path(in_path).name, Path(out_path).name, bool(keep),
                                  ok, drop, mech, qc_txt, n_fix, scan_note, cut_txt, ui_lang),
                            encoding="utf-8")
        _log(f"完成，脱敏 {sum(c for *_, c in ok)} 处，自检质检修正 {n_fix} 处"
             f"｜{len(out)} 字（{(len(out) / max(1, len(text)) - 1) * 100:+.1f}%）"
             f"｜{time.time() - t_all:.0f}s")
        return "ok"
    except Exception as exc:  # noqa: BLE001  降级路挂掉不该连累主链路
        _log(f"异常 → failed：{type(exc).__name__}: {exc}")
        return "failed"

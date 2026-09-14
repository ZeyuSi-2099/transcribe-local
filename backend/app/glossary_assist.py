"""术语库「协助建库」：按访谈大纲起草一本，或给已有的库出体检建议。

设计交付《Transcribe术语库交互优化》的后端侧。两个能力：
  · draft(outline)  → 有序的 [{cat} | {term, def}]，顺序即写入编辑器的顺序
  · check(content)  → {add:[{term,def,cat}], edit:[{term,def,why}], del:[{term,why}]}

**都不写库**：结果只回给前端放进编辑器缓冲区，落库仍走现有 PUT /api/glossaries/{id}。
所以这条链路碰不到 users/ledger/jobs，失败最多是「这次没生成出来」，不会脏数据。

引擎用 DeepSeek（Render 上直连，不惊动 Fly 任务机器）。

**2026-08-05 起 draft 产出三种东西**（提示词 V6，见 _RULES 上方的实测注记）：
  · 完整条目 {term, def}
  · **只有名字的条目 {term, def: ""}** —— 模型看见了但拿不准，留给用户填
  · **缺口 {gap}** —— 「这一类还缺什么」的方向提示，属**待办层**

后两种是这个功能的产品定位决定的：拿真实生产库对照，单份材料能抽的上限只有 14 条，
库里另外 155 条全是公开渠道查不到的机构内部知识。平台交不出成品，只能搭骨架 + 指路。

⚠️ **缺口绝不能进库正文**（由 app/glossary.py 分表存），空释义行注入前要摘掉
   （glossary.strip_unfinished）——库全文是作为 P3 的硬证据原样注入的。
⚠️ 提示词里的收录标准来自两本真实生产库的反推。改这里等于改产品定义，
   先读 docs/design/briefs/BRIEF-glossary-todo-layer.md 与 docs/glossary-draft-acceptance.md。
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from pipeline.ui_lang import ui_lang_name

# 2026-08-05 实测选型（两份真实大纲 × 三个模型，一份 6653 字）：
#
#   模型              首条      总耗时   条目      消歧    外部事实
#   deepseek-chat      —        9s      36        0 条    保守
#   deepseek-v4-flash  151s     156s    24~54     3/24    保守（不确定就写「材料未给出」）
#   deepseek-v4-pro    216s     228s    33~59     4/33    准确（四个合作车企逐个点名，
#                                                          第五个不确定就留了白，没编）
#
# ⚠️ **v4 两个都是推理模型：流式救不了它们。** 实测 flash 前 151 秒一个字不吐（在思考），
#    最后 5 秒把 54 条全喷出来。所以别再指望「换流式就不用等了」——draft_stream 留着是
#    因为它对非推理模型有效、且对推理模型无损，不是因为它能把等待变短。
#
# 选 flash 不选 pro：pro 多花 70 秒，换来的是更准的外部实体（值钱），但它同一份大纲两次
# 跑出 33 条和 59 条，多出来的全是「渠道健康」「健康度」这种从标题里抠的词。产出不稳定
# 比慢更麻烦——用户看到的是「这次怎么这么多废话」。
# （上表是 2026-08-05 的实测，用的还是 V4 Flash；2026-09-11 起改用官方正式名 deepseek-flash = V4.1 Flash。）
MODEL = os.environ.get("GLOSSARY_ASSIST_MODEL", "deepseek-flash")
TIMEOUT_SEC = float(os.environ.get("GLOSSARY_ASSIST_TIMEOUT", "300"))
MAX_OUTLINE_CHARS = 20000          # 大纲入参上限（超出前端就该先拦）
MAX_ENTRIES = 80                   # 会多出「只有名字」的条目，比 V3 时代放宽
GAP_MAX = 60                       # 单条缺口字数，与 glossary.GAP_MAX_CHARS 同口径
MEANING_MAX = 150                  # 与 lib/glossary.ts 同口径
TOTAL_MAX = 8000

_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"


class AssistUnavailable(RuntimeError):
    """引擎没配或调不通——调用方转成 503，别当成「没有可收的词」。"""


# 收录标准：这段是整个功能的产品定义，不是普通提示词。
#
# 2026-08-05 用两份真实访谈大纲 × 三个模型 × 五版提示词实测定稿。三条实测结论钉在这里，
# 改之前先看，都是花了几十次调用换来的：
#
# ① **别在提示词里举具体的分类名**。上一版写了「类与类之间要有业务层次（组织 → 角色 →
#    形态 → 产品 → 指标 → 行话）」，模型直接把这六个词当成固定分类表照抄，然后硬把词
#    往里塞——释义写着「渠道形态」的词被归进「角色」。示例要么用占位符，要么取一个与
#    目标领域完全不重叠的域（这里用远洋航运）。
# ② **加规则会挤掉消歧**。试过再加「机构内部固定说法」和「别收章节标题」两条，条目数
#    从 33 涨到 54，涨的全是大纲的章节标题（「售后服务」「IT系统」「财经政策」），而
#    消歧标记从 4 条掉到 0 条。规则越多，模型越去凑数量、越顾不上写对照。这版是实测的
#    最优点，**要加规则先想清楚拿什么换**。
# ③ **消歧必须限定成「两个真概念的对照」**。只说「容易混的写出来」，模型会去做同音字
#    辨析（「注意"货"非"火"」「"返利"非"凡例"」）——那正是术语库规范明令禁止的
#    「枚举 ASR 误写形」，纯噪音。所以这里给了自检判据：被点名的对照词自己得也是个术语。
# ④ **2026-08-19：第一步那道门禁的判断方向原来是反的。** 原文写「换一家同行还会不会
#    冒出这个词？**不会**，就别收」——正好颠倒：同行也会说才是大路货该挡，同行不会说
#    恰恰证明它是这一家的说法。照字面执行的结果是「手机/平板/耳机」这类品类词被收进来，
#    而客户自己的内部代号反倒落进「别收」那一档。生产实测：一本 108 条的库里
#    混进 6 个品类词，且全是「等你填一句话」的空条目。
#    这同时暴露了 check() 与 draft() 的自相矛盾——check 明写「del：常规词，不会被听错，
#    白占额度」，起草却在往里收。
#    连带修第二处：「拿不准就留空」本是为内部代号这种「不知道它指什么」的词留的出口，
#    「手机」这种「本来就没什么可写」的词也从同一个口子溜了进去，所以给留空加了前提。
#    ⚠️ 只掰正这两处、没有新增规则——按 ② 的教训，加规则要拿消歧去换。
_RULES = f"""你在为一位访谈研究者起草**术语库**。这本库会在转录定稿时作为硬证据，用来给专有名词定字。

**这是一次人机协作，不是让你独自交一份成品。** 你负责把骨架搭起来、把能写的定义写掉、
把缺口指出来；用户负责补上你不可能知道的那部分——机构内部的说法、只有圈内人才懂的代号。
所以**你不确定的东西不要删掉，把它留在那儿告诉用户**，这比你悄悄扔掉有价值得多。

## 判据

一个词该不该进这本库，只问一句：**它被听错时，这句话会不会变成另一个意思？**
会 → 收；不会 → 不收，哪怕它看起来再专业。

注意这一问**与「你知不知道它是什么意思」无关**。一个你完全不懂的词，同样可以（而且往往更该）进这本库。

## 按这四步做

**第一步：把可能是术语的词全列出来。**
先不管你能不能写出它的定义——只要符合下面任一条线索，就先列出来：

1. **专名** —— 人名、机构名、公司简称、产品名、项目代号、平台名
2. **缩写** —— 任何字母/数字组合的代号
3. **行话** —— 字面义与实际义不同的圈内说法。**字面越普通越要收**：字面普通意味着听写时不会引起怀疑，错了也没人发现
4. **音近词** —— 与某个常用词读音相近的词。这类最危险，因为错误结果读起来完全通顺
5. **成组出现的同类实体** —— 材料里以清单、表格、枚举形式并列列出的一组同类专名，要**整组收录**。一组里漏掉几个，剩下的也就不可靠了

这一步宁滥勿缺。唯一要挡在门外的是**日常通用词**和**材料自身的结构性文字**（章节标题、议题名、
提问项）——判断方法：换一家同行做同一件事，他们嘴里**也会**冒出这个词吗？
**会 → 别收**，那是这个行业人人都在说的大路货，听错了也不会让句子变成别的意思。
**不会 → 收**，那正说明它是这一家自己的说法。
品类名（手机、平板、耳机这类）、岗位通称、常见动作词，都属于该挡在门外的那一类。

**第二步：给这些词分类。**
分类名要从**这批材料的实际内容**里长出来，**不要套用任何预设的分类表**——
「组织 / 角色 / 指标 / 行话」这种放之四海皆通的空壳名，说明你没有读材料。
需要时用 `·` 分两级。分类之间应当有阅读上的递进（大到小、外到内、静到动，视材料而定）。

下面这个示例只为示范形式，与你手上的材料无关：一批远洋航运访谈的分类可能长成
「船级与吨位 · 分级代号」「航路与挂靠港」「装卸与理货行话」，而不是「组织」「流程」「指标」。

**第三步：逐个分类回看——这一类还缺什么？**
术语在一个机构里通常**成体系**：有了其中几个，往往就还有同体系的其它几个，只是这份材料里没提到。
逐类问一句「这一类只有这几个吗」，如果判断还该有别的，就在该分类下放一条 gap，
**说清楚缺的是哪一类东西**，让用户自己去补。

- **不要自己猜具体的词填进去。** 机构内部的说法你猜不出来，猜出来的假词比空着更糟。
- gap 要具体到能让人立刻想起自己的东西，不要写「可能还有其他术语」这种废话。

**第四步：写定义。按这个优先级：**

1. **有把握 → 直接写。** 材料里解释过的，按材料的说法写，不要另起炉灶。
2. **没把握 → 把词留下，定义留空。** ← **这是本次最要紧的一条。**
   一个你拿不准的术语，留下名字让用户来填，远远好过把它删掉——删掉之后用户根本不知道
   你看见过它，而这些词往往正是这个机构内部最要紧、外人最不可能猜到的说法。
3. **绝不编。** 一条错释义会被写进每一份终稿，比少一条严重得多。宁可留空。

留空不是偷懒的出口：能写对的别留空。但在「不确定」和「删掉」之间，永远选留空。
**但留空的前提是它已经过了第一步那道门禁。** 分清两种写不出定义的情形：
「这词显然有特定所指，只是我不知道所指为何」→ 留空，交给用户；
「这词本来就没什么特别含义」→ 它根本不该被列出来，那是漏收标准，不是留空的理由。

## 定义怎么写

- 一句话，不超过 {MEANING_MAX} 字；写「是什么」，不写「怎么读」
- 释义相同或高度相近的同类词，用 `/` 并成一行共享一条释义
- **消歧是这本库最值钱的部分，但只做一种**：只在两个都真实存在的概念之间做对照，
  写法「…（非 X）」「…，与 X 区分」。
  **严禁做读音或字形层面的纠错** —— 不要用这个写法去提示某个字容易被写成它的同音字或
  形近字，那不归这本库管，写了只会变成噪音。自检方法：把你点名的那个对照词单独拿出来看，
  **它自己是不是也是一个术语？** 如果它只是个碰巧同音的普通词，就把这半句删掉。

## 联网

你可以联网搜索，用在两处：第三步确认某个体系通常还包含什么；第四步核实你拿不准的专名。
**搜不到就留空**，不要把搜索结果凑合成一条似是而非的定义。
机构内部的说法本来就搜不到，搜一两次没结果就别在它身上耗了。

## 输出

只输出 JSON，不要任何解释性文字：

{{"items": [
  {{"cat": "分类名"}},
  {{"term": "术语", "def": "含义"}},
  {{"term": "术语"}},
  {{"gap": "这一类还缺什么，一句话说清"}}
]}}

items 是有序的：每个分类行后面跟属于它的条目和 gap。
**只给 term、不给 def 的条目就是「我看见了但拿不准」** —— 这正是要交给用户的部分，别省。
最多 {MAX_ENTRIES} 条。"""


# 用哪门语言写（2026-08-30）。⚠️ **这里有两种东西，方向相反**：
#   · **术语本身** = 材料里的原词，一个字都不能动（术语库是拿来给转录定字的，
#     改了写法这本库就废了——它匹配的是稿子里的字符串）；
#   · **分类名 / 释义 / 建议理由 / 缺口提示** = 我们替用户写的话 → 跟界面语言。
# 不加这一句的后果（2026-08-29 生产同款 deepseek-v4-flash 喂西语大纲实测）：
# **分类名 5/5 中文、释义 12/12 中文**——而这本库是用户自己的资产，还会作为硬证据注入 P3。
# 它比「显示不对」严重：中文界面之外的用户，库里被写进了一批他读不懂的字。
def _lang_note(ui_lang: str | None) -> str:
    name = ui_lang_name(ui_lang)
    return f"""

## 本次的语言（覆盖上面任何与语言有关的默认写法）

- **术语（term）**：原样照抄材料里的写法，**绝不翻译、绝不改写、绝不做繁简或字符转换**。
  这本库是拿来在转录稿里逐字比对的，写法一改就再也对不上。
- **你自己写的字** —— 分类名（cat）、释义（def）、修改理由（why）、缺口（gap / gaps） ——
  **一律用 {name} 书写**。
- 释义里引用材料原文的部分保持原样，其余用 {name}。
"""


def _post(messages: list[dict], *, json_mode: bool = True) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise AssistUnavailable("DEEPSEEK_API_KEY 未配置")
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.3,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as c:
            r = c.post(_ENDPOINT, json=body, headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:                       # 网络/鉴权/超时一律归为「暂时不可用」
        raise AssistUnavailable(str(e)[:200]) from e
    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception as e:
        raise AssistUnavailable(f"返回结构异常：{str(data)[:200]}") from e


class _ObjScanner:
    """从流式 JSON 文本里增量抠出 `{"items":[…]}` 数组内的完整对象。

    只做括号配对 + 字符串态跟踪，不调 json.loads —— 解析器要等整段闭合，
    那样就退化回非流式了，而「等它闭合」正是我们要消灭的那 100 秒白屏。
    嵌套深度 1 = 最外层 {，深度 2 = items 里的一条，所以只认第二层。
    """

    def __init__(self) -> None:
        self.depth = 0
        self.cur: list[str] = []
        self.collecting = False
        self.in_str = False
        self.esc = False

    def feed(self, chunk: str):
        for ch in chunk:
            if self.collecting:
                self.cur.append(ch)
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif ch == "\\":
                    self.esc = True
                elif ch == '"':
                    self.in_str = False
                continue
            if ch == '"':
                self.in_str = True
            elif ch == "{":
                self.depth += 1
                if self.depth == 2:
                    self.collecting = True
                    self.cur = ["{"]
            elif ch == "}":
                if self.depth == 2 and self.collecting:
                    yield "".join(self.cur)
                    self.collecting = False
                    self.cur = []
                self.depth -= 1


def _post_stream(messages: list[dict]):
    """流式调用，逐块 yield 文本增量。异常一律归为 AssistUnavailable。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise AssistUnavailable("DEEPSEEK_API_KEY 未配置")
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.3,
        "stream": True,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as c:
            with c.stream("POST", _ENDPOINT, json=body,
                          headers={"Authorization": f"Bearer {key}"}) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        delta = json.loads(payload)["choices"][0].get("delta", {})
                    except Exception:
                        continue          # 心跳/畸形块跳过，别让一行毁掉整条流
                    if delta.get("content"):
                        yield delta["content"]
    except AssistUnavailable:
        raise
    except Exception as e:
        raise AssistUnavailable(str(e)[:200]) from e


def _loads(raw: str) -> dict:
    """模型偶尔会把 JSON 包在 ```json 里——剥掉再解析；解析不出当作空结果，不抛。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _clean_term(t: Any) -> str:
    # 全角竖线是行分隔符，术语与释义里都不能出现，否则解析会串行
    return str(t or "").replace("｜", " ").replace("|", " ").replace("\n", " ").strip()


def _clean_def(d: Any) -> str:
    s = _clean_term(d)
    return s[:MEANING_MAX]


def _draft_messages(outline: str, lang_hint: str, ui_lang: str | None = None) -> list[dict]:
    user = (
        f"下面是一份访谈材料（可能是访谈大纲、公司介绍或往期稿件）。"
        f"请从中挑出值得进术语库的词，最多 {MAX_ENTRIES} 条。\n\n"
        f"输出 JSON：{{\"items\": [{{\"cat\": \"分类名\"}}, {{\"term\": \"术语\", \"def\": \"含义\"}}, ...]}}\n"
        f"items 是有序的：每个分类行后面跟它自己的条目。\n"
        + (f"这批访谈的语言/场景提示：{lang_hint}\n" if lang_hint else "")
        + f"\n材料：\n{outline}"
    )
    return [{"role": "system", "content": _RULES + _lang_note(ui_lang)},
            {"role": "user", "content": user}]


class _DraftAccumulator:
    """把模型吐出的原始对象逐个规整成可写入编辑器的行，并守住上限。

    与 draft() 的批量清洗同一套规则，抽出来是为了让流式与非流式共用——
    两条路径的清洗逻辑一旦分家，就会出现「流式能过、非流式被拦」这种只在生产复现的差异。
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.total = 0
        self.entries = 0

    def take(self, it: Any) -> dict | None:
        """返回该收的条目，返回 None 表示丢弃。撞上限时抛 StopIteration。"""
        if not isinstance(it, dict):
            return None
        if it.get("cat"):
            cat = _clean_term(it["cat"]).lstrip("#").strip()
            if not cat:
                return None
            self.total += len(cat) + 3
            return {"cat": cat}
        if it.get("gap"):
            # 缺口属于待办层：不占正文额度、不进编辑器，由调用方分表落库
            gap = " ".join(_clean_term(it["gap"]).split())[:GAP_MAX]
            return {"gap": gap} if gap else None
        term, meaning = _clean_term(it.get("term")), _clean_def(it.get("def"))
        # ⚠️ **释义为空是合法的**，别加回 `not meaning` 那道判断。
        #    空释义 = 模型看见了这个词但拿不准意思，留下名字交给用户填。
        #    上一版把这种条目直接丢掉，用户根本不知道模型看见过它——那正是这次要改的。
        #    它不会污染转录：注入前由 glossary.strip_unfinished 摘掉整行。
        if not term or term in self.seen:
            return None
        cost = len(term) + len(meaning) + 1
        if self.total + cost > TOTAL_MAX or self.entries >= MAX_ENTRIES:
            raise StopIteration
        self.seen.add(term)
        self.total += cost
        self.entries += 1
        return {"term": term, "def": meaning}


def draft_stream(outline: str, *, lang_hint: str = "", ui_lang: str | None = None):
    """流式起草：每规整好一条就 yield 一条，不等整份跑完。

    为什么非流式不行：pro 出一份 50 条的库要 90-130 秒，这段时间界面上一个字都没有——
    而这个功能的设计意图恰恰是「看着它一条条写进编辑器」。非流式版本是在**全部拿到之后**
    才按 520ms 假装逐条写，等待感全压在前面那 100 秒里。流式之后首条约 3 秒可见，
    「停止」按钮也才真的省得下 token（此前模型早已跑完，停止只是停止播放）。
    """
    outline = (outline or "").strip()[:MAX_OUTLINE_CHARS]
    if not outline:
        return
    scanner = _ObjScanner()
    acc = _DraftAccumulator()
    for chunk in _post_stream(_draft_messages(outline, lang_hint, ui_lang)):
        for raw in scanner.feed(chunk):
            try:
                it = json.loads(raw)
            except Exception:
                continue                  # 半条/畸形对象丢掉，不打断整条流
            try:
                got = acc.take(it)
            except StopIteration:
                return
            if got:
                yield got


def draft(outline: str, *, lang_hint: str = "", ui_lang: str | None = None) -> list[dict]:
    """按访谈大纲起草。返回有序列表：{"cat": 名} 或 {"term": 词, "def": 释义}。

    顺序即前端写入编辑器的顺序（分类行在它的条目之前）。
    材料里确实没有值得收的词时返回 []——调用方据此显示「这份材料里没有需要收的词」。
    """
    outline = (outline or "").strip()[:MAX_OUTLINE_CHARS]
    if not outline:
        return []

    user = (
        f"下面是一份访谈材料（可能是访谈大纲、公司介绍或往期稿件）。"
        f"请从中挑出值得进术语库的词，最多 {MAX_ENTRIES} 条。\n\n"
        f"输出 JSON：{{\"items\": [{{\"cat\": \"分类名\"}}, {{\"term\": \"术语\", \"def\": \"含义\"}}, ...]}}\n"
        f"items 是有序的：每个分类行后面跟它自己的条目。\n"
        + (f"这批访谈的语言/场景提示：{lang_hint}\n" if lang_hint else "")
        + f"\n材料：\n{outline}"
    )
    data = _loads(_post([{"role": "system", "content": _RULES + _lang_note(ui_lang)},
                         {"role": "user", "content": user}]))
    items = data.get("items")
    if not isinstance(items, list):
        return []

    out: list[dict] = []
    seen: set[str] = set()
    total = 0
    entries = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("cat"):
            cat = _clean_term(it["cat"]).lstrip("#").strip()
            if cat:
                out.append({"cat": cat})
                total += len(cat) + 3
            continue
        term, meaning = _clean_term(it.get("term")), _clean_def(it.get("def"))
        if not term or not meaning or term in seen:
            continue
        # 整本 8000 字上限在这里就守住，别让前端拿到一份注定存不下的稿
        cost = len(term) + len(meaning) + 1
        if total + cost > TOTAL_MAX or entries >= MAX_ENTRIES:
            break
        seen.add(term)
        out.append({"term": term, "def": meaning})
        total += cost
        entries += 1

    # 只有分类没有条目 = 等于没起草出来
    return out if entries else []


def existing_terms(content: str) -> dict[str, str]:
    """把库文本解析成 {术语: 释义}。分类行（##）跳过，格式不对的行跳过。"""
    out: dict[str, str] = {}
    for raw in (content or "").splitlines():
        line = raw.strip().lstrip("-").strip()
        if not line or line.startswith("#") or "｜" not in line:
            continue
        term, _, meaning = line.partition("｜")
        term = term.strip()
        if term:
            out[term] = meaning.strip()
    return out


def check(content: str, *, ui_lang: str | None = None) -> dict:
    """给已有的库出体检建议。三类：该补 / 该改 / 该删。

    判据同 _RULES：该删的核心是「常规词，不会被听错，白占额度」。
    返回 {"add": [...], "edit": [...], "del": [...]}，任何一类都可能为空。

    ⚠️ 三类都在服务端按库的实际内容过滤一遍，**不能只靠提示词**。
       2026-08-05 生产实测：模型把库里已有的 8 个词全列进了 add，又把 8 条原样照抄进
       edit（why 写「释义清晰，无需修改」）——18 条建议里只有 1 条真的有用。
       用户翻完整个队列、每条点一次采纳，库一个字都不会变。过滤在这里做才拦得住。
    """
    content = (content or "").strip()
    if not content:
        return {"add": [], "edit": [], "del": [], "gaps": []}
    have = existing_terms(content)

    user = (
        "下面是一本已有的术语库。请按收录标准体检，给出三类**需要动手改**的建议：\n"
        "- add：库里**没有**、但这个场景下明显该收的词（库里已有的词不要列）\n"
        "- edit：释义确实有问题的（太模糊/太长/缺消歧），给出**改写后**的释义\n"
        "- del：常规词，不会被听错，白占额度\n\n"
        "另外给一类 gaps：**这本库还缺哪几类东西**——只说方向，不要猜具体的词。"
        "机构内部的说法你猜不出来，猜出来的假词比空着更糟；写到能让人立刻想起自己的东西为止。\n\n"
        "输出 JSON：{\"add\": [{\"term\":\"\",\"def\":\"\",\"cat\":\"\"}], "
        "\"edit\": [{\"term\":\"\",\"def\":\"\",\"why\":\"\"}], "
        "\"del\": [{\"term\":\"\",\"why\":\"\"}], "
        "\"gaps\": [\"这一类还缺什么，一句话说清\"]}\n\n"
        "**三类都只列需要动手的。** 一条词读着没问题就不要列进 edit——"
        "「无需修改」不是一条建议，是不列出来。三类都可以为空数组，空是好结果。\n"
        "edit 的 def 必须与原释义不同；只是换个说法而意思一样，也不要列。\n"
        "每类最多 8 条，宁缺毋滥。\n"
        "add 的 cat 必须是库里已有的分类名，或一个合适的新分类名。\n"
        "why 一句话说清理由，不超过 40 字。\n\n"
        f"术语库全文：\n{content[:TOTAL_MAX]}"
    )
    data = _loads(_post([{"role": "system", "content": _RULES + _lang_note(ui_lang)},
                         {"role": "user", "content": user}]))

    def _list(key: str, fields: tuple[str, ...]) -> list[dict]:
        raw = data.get(key)
        if not isinstance(raw, list):
            return []
        out = []
        for it in raw:
            if not isinstance(it, dict):
                continue
            row = {f: _clean_def(it.get(f)) for f in fields}
            term = row.get("term")
            if not term:
                continue
            # 三道过滤：add 必须是新词；edit/del 必须命中库里已有的词；
            # edit 还必须真的改了释义，否则采纳等于空操作
            if key == "add" and term in have:
                continue
            if key in ("edit", "del") and term not in have:
                continue
            if key == "edit" and row.get("def", "").strip() == have[term].strip():
                continue
            out.append(row)
            if len(out) >= 8:
                break
        return out

    # gaps 是待办层：**不进正文**，由调用方分表落库（见 api.py 的 check 路由）
    raw_gaps = data.get("gaps")
    gaps = []
    if isinstance(raw_gaps, list):
        for g in raw_gaps[:8]:
            t = " ".join(_clean_term(g).split())[:GAP_MAX]
            if t:
                gaps.append(t)

    return {
        "add": _list("add", ("term", "def", "cat")),
        "edit": _list("edit", ("term", "def", "why")),
        "del": _list("del", ("term", "why")),
        "gaps": gaps,
    }

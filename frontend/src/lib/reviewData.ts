// 复核清单数据模型 + 样例。
// 甲-1a：前端用 SAMPLE_REVIEW 驱动复核交互；
// 甲-1b：后台 P3 按同一 schema 产出真实复核清单替换样例。
// 样例改编自真实渠道回访（已脱敏：人名/公司虚构，渠道术语保留行业写法）。

import type { TranscriptRow } from "./api";

// 与 SAMPLE_REVIEW 匹配的访谈正文（演示"已完成、富实体"的结果页用；约 16 分钟电话面访）。
export const SAMPLE_REVIEW_SEGMENTS: TranscriptRow[] = [
  { t: "00:00:03", sp: "主持人", s: "孙总你好，先给您看一下我的邀请函。我们每年都会有个访谈，三月份就开始了，之前同事应该跟您联系过。" },
  { t: "00:00:35", sp: "被访者", s: "可以的，可以确认。" },
  { t: "00:00:52", sp: "主持人", s: "主要是了解一下华为去年整体的情况，包括今年新品发布，还有渠道下沉这块的东西。" },
  { t: "00:01:27", sp: "被访者", s: "行，快 10 分钟吧，我后面还有个会。" },
  { t: "00:01:38", sp: "主持人", s: "那我直接切入就问新品吧。从四月份新品发布来说，整体的市场感知怎么样？" },
  { t: "00:01:54", sp: "被访者", s: "从竞品的低配置、性价比来讲没有问题。但消费者层面，特别是 Pro 和 Ultra，定价还是偏高，消费降级嘛，还是有一些影响。" },
  { t: "00:02:20", sp: "主持人", s: "对，是的。那 Nova 呢？Nova 现在不是才打出来嘛。" },
  { t: "00:02:31", sp: "被访者", s: "畅享 90 Max 还可以，做工各方面比较受欢迎。但是 Plus 从 ID 也好、屏显也好，竞争力还是偏弱。" },
  { t: "00:02:58", sp: "主持人", s: "明白。那跟 OV 这边比呢？内存也都涨价了，市场对比上有没有什么竞争力？" },
  { t: "00:03:06", sp: "被访者", s: "套路不一样嘛。OV 把我们的弱势发挥得淋漓尽致，内存版本降低呀，ID 搞得更好看啊，屏显搞得更好啊。" },
  { t: "00:03:28", sp: "被访者", s: "他们改的东西，让消费者第一眼就是颜值。这个档位的消费者很难来评估它的价值。" },
  { t: "00:03:46", sp: "被访者", s: "一般就是老人机，或者接打电话用的，没有谁说要研究你的芯片、研究你的系统。" },
  { t: "00:04:10", sp: "被访者", s: "另外定价来讲，它和畅享 90 Max 错的不是特别多。消费降级，大家投差不多的钱，可能也选好的。" },
  { t: "00:04:29", sp: "被访者", s: "更多消费者就是：你已经这样了，我再添点钱，就买个好一点的。明白吧？" },
  { t: "00:04:42", sp: "被访者", s: "上半年我们整体能做到百分之十八的增长，主要还是低端走量。" },
  { t: "00:04:55", sp: "主持人", s: "明白。那 Nova 今年品牌代言人也换了，品牌形象这块市场感知有没有变化？" },
  { t: "00:05:06", sp: "被访者", s: "变化还是很快的。我们下去巡店，不管城市大小，都能感受到粉丝的热情。只能说是效应，还谈不上效益，这帮粉丝都没有钱。" },
  { t: "00:05:39", sp: "被访者", s: "我们的赠品现在有礼盒，有海报，有明星冰箱贴。礼盒反应挺好，就是有点少。" },
  { t: "00:05:52", sp: "主持人", s: "那畅享 90 Max 的备货，总部那边给的支持够吗？" },
  { t: "00:06:02", sp: "主持人", s: "行。然后品牌这块，前段时间不是刚发布了韬定律嘛，您对这个的看法怎么样？" },
  { t: "00:06:08", sp: "被访者", s: "这个定义我觉得提得挺及时的，给渠道吃了颗定心丸。" },
  { t: "00:06:25", sp: "被访者", s: "像 Mate 60 那个时候的热度，其实就是这个逻辑的验证。" },
  { t: "00:06:35", sp: "被访者", s: "Mate 80 那一波我们备货就没跟上，眼睁睁看着别人出货。" },
  { t: "00:06:45", sp: "被访者", s: "鸿蒙 6 出来之后，老用户的换机意愿也明显起来了。" },
  { t: "00:06:57", sp: "被访者", s: "我们现在在地市推那个战略堡垒店，就是要把核心商圈先占住。" },
  { t: "00:07:11", sp: "主持人", s: "嗯。那渠道下沉这块呢？乡镇市场现在做得动吗？" },
  { t: "00:07:18", sp: "被访者", s: "村换那个事嘛，乡镇消费者上过当受过骗，对杂牌不信任，反而认我们这种大牌。" },
  { t: "00:07:30", sp: "被访者", s: "我们 ND 省包 这边一直是跟省代联动的，乡镇的货也从这条线走。" },
  { t: "00:07:50", sp: "主持人", s: "那新款 Mate 80 的预约量现在是什么水平？" },
  { t: "00:08:05", sp: "被访者", s: "畅享 90 Max 现在是亏着钱做的，从当前成本看确实这样。" },
  { t: "00:08:29", sp: "被访者", s: "现在整体是供大于求，渠道里的瑕疵机处理起来也麻烦。" },
  { t: "00:08:50", sp: "主持人", s: "嗯，那畅享 90 Max 在乡镇的动销情况怎么样？" },
  { t: "00:09:01", sp: "被访者", s: "到了 Mate 80 这一代，预约都排到下个月了，是真排不上。" },
  { t: "00:09:22", sp: "被访者", s: "我们一般是按客户曾经的一个最合理的库存去补货，不会一次压太多。" },
  { t: "00:09:45", sp: "被访者", s: "礼盒那个投入是七十五万，啊不对，七十五，反正单台摊下来是这个数。" },
  { t: "00:10:02", sp: "主持人", s: "那县乡的价格秩序呢？现在窜货管得住吗？" },
  { t: "00:10:23", sp: "被访者", s: "还是要保层级，看拼实力谁强。市场秩序乱了，谁都做不好。" },
  { t: "00:10:48", sp: "被访者", s: "县乡这边畅享 90 Max 走得最快，基本到货就出。" },
  { t: "00:11:05", sp: "被访者", s: "我钱我资源多一点，就多压点畅享 90 Max，这个不亏。" },
  { t: "00:11:25", sp: "被访者", s: "五月二十一号那场首销，我们一天出了两百多台。" },
  { t: "00:11:40", sp: "被访者", s: "有的县城就一两家当地的寡头，你绕不开他。" },
  { t: "00:12:19", sp: "被访者", s: "门店现在都要求上封闭柜，新品摆进去，配件挂出来。" },
  { t: "00:12:33", sp: "被访者", s: "总部不是有补贴嘛，装修这块我们出大头。" },
  { t: "00:12:50", sp: "被访者", s: "我说这有意思。" },
  { t: "00:13:17", sp: "主持人", s: "那体验店这边呢？今年的拓店计划是怎么安排的？" },
  { t: "00:13:34", sp: "被访者", s: "太力 在我们这边管 FD 的盘子，授权体验店 的货都从他们那里走。" },
  { t: "00:13:52", sp: "被访者", s: "合作店商 这一层今年也在扩，主要是乡镇的点位。" },
  { t: "00:14:20", sp: "被访者", s: "拓店的话，新店首销我们都是拿畅享 90 Max 打头阵。" },
  { t: "00:14:45", sp: "被访者", s: "中间有顾客进店 [听不清] 处理换机的事，耽误了几分钟。" },
  { t: "00:15:07", sp: "被访者", s: "后来是 [听不清] 跟我们对接的，具体名字我回头查一下给您。" },
  { t: "00:15:30", sp: "主持人", s: "对了，Nova 这次请肖战代言，店里的物料都到位了吗？" },
  { t: "00:15:48", sp: "被访者", s: "到了到了。OPPO Reno16 那边也在搞路演，我们得跟上节奏。" },
  { t: "00:16:21", sp: "主持人", s: "行，今天就先聊到这，耽误您时间了。回头报告出来我发您一份。" },
];

export interface ReviewOccurrence {
  t: string;        // "HH:MM:SS" 起始时间 → 定位音频
  speaker: string;  // 说话人显示名
  lineText: string; // 该行文本（含被复核的词）
  // ↓ 存疑（doubt）专用：一行报告 = 一处存疑，各带各的时间码与原因。挂在卡级的话，同词多处时
  //   你点进第 2 处看到的是第 1 处的原因，而一处疑幻觉会把删除按钮递给同卡所有正常句子。
  reason?: string;          // 该处的存疑原因（缺省回落卡级 reason，老任务无此字段）
  suggestDelete?: boolean;  // 该处疑幻觉 → 「删除这句」升为可见按钮
  anchorT?: string;         // 报告原始时间码（留痕）
  anchorFallback?: boolean; // 锚点收窄失败，本处是全文搜的兜底结果 → 前端提示未精确定位
}

export interface WebSearch {
  query: string;    // 实时联网搜索词
  summary: string;  // 核实结果摘要
  source: string;   // 来源
}

// speaker = P3 把 `[❓]` 打在**说话人标签**上（`M[❓]:`）→「这段是谁说的没定下来」。
// 与 doubt 是两件事：doubt 改的是字，speaker 改的是这一段算谁说的（见 pipeline/review.py）。
export type ReviewType = "entity" | "web" | "doubt" | "speaker";

export interface ReviewItem {
  type: ReviewType;
  term: string;                 // 词 / 短语
  tag: string;                  // 产品名 / 品牌 / 人名 / ""（实体子类）
  occurrences: ReviewOccurrence[];
  reason: string;               // 为什么标它（让用户了解）
  web?: { searches: WebSearch[]; count: number };  // type=web 时有
  confidence: "low" | "mid" | "high";
  mustConfirm?: boolean;        // 实体无法定字（引擎系统性分歧）→ 进必须确认队列
  suggestDelete?: boolean;      // 存疑：疑幻觉 / 整段含糊 → 「删除这句」升为可见按钮
  // 这张卡的 reason 是**我们**写的、不是模型写的（终稿打了 [❓] 而报告没登记 → 就地补的卡）。
  // 前端据此换成界面语言；光看 type 分不出来，它和真存疑卡都是 doubt。
  unreported?: boolean;
  evidence?: {                  // 「已定字」审计区的证据（缺失时前端按 type/confidence 推断依据）
    candidates?: string[];      // 各轨候选写法，如 ["想界","享界","享界"]
    // engine = 引擎证据（兜底）。**别把它并回 glossary**：没挂术语库的任务标「术语库」是误导
    basis: "glossary" | "acoustic+glossary" | "acoustic" | "engine" | "web";
  };
}

export const SAMPLE_REVIEW: ReviewItem[] = [
  // ── 待确认：mid 实体 ──
  {
    type: "entity", term: "孙总", tag: "人名", confidence: "mid",
    reason: "称谓 + 姓氏，建议补全名。",
    occurrences: [
      { t: "00:00:03", speaker: "主持人", lineText: "孙总你好，先给您看一下我的邀请函。" },
    ],
  },
  {
    type: "entity", term: "畅享 90 Max", tag: "产品名", confidence: "mid",
    reason: "四路引擎出现「畅想/畅享」两种写法，按术语库定「畅享」——建议确认。",
    // 依据写实：引擎分歧（声学）+ 术语库定字。别省——省了就只能靠 basisChip 兜底猜
    evidence: { basis: "acoustic+glossary" },
    occurrences: [
      { t: "00:02:31", speaker: "被访者", lineText: "畅享 90 Max 还可以，做工各方面比较受欢迎。" },
      { t: "00:04:10", speaker: "被访者", lineText: "它和畅享 90 Max 错的不是特别多。" },
      { t: "00:05:52", speaker: "主持人", lineText: "那畅享 90 Max 的备货，总部那边给的支持够吗？" },
      { t: "00:08:05", speaker: "被访者", lineText: "畅享 90 Max 现在是亏着钱做的。" },
      { t: "00:08:50", speaker: "主持人", lineText: "那畅享 90 Max 在乡镇的动销情况怎么样？" },
      { t: "00:10:48", speaker: "被访者", lineText: "县乡这边畅享 90 Max 走得最快，基本到货就出。" },
      { t: "00:11:05", speaker: "被访者", lineText: "就多压点畅享 90 Max，这个不亏。" },
      { t: "00:14:20", speaker: "被访者", lineText: "新店首销我们都是拿畅享 90 Max 打头阵。" },
    ],
  },
  {
    type: "entity", term: "韬定律", tag: "术语", confidence: "mid",
    reason: "四路引擎写法打架（滔定率/涛定律/掏定律/韬定律），按术语库定「韬定律」——建议确认。",
    evidence: { basis: "acoustic+glossary" },
    occurrences: [
      { t: "00:06:02", speaker: "主持人", lineText: "前段时间不是刚发布了韬定律嘛，您对这个的看法怎么样？" },
    ],
  },
  {
    type: "entity", term: "太力", tag: "渠道商", confidence: "mid",
    reason: "三种候选（太利/泰利/太力），按术语库「普天太力」定字——建议确认。",
    occurrences: [
      { t: "00:13:34", speaker: "被访者", lineText: "太力 在我们这边管 FD 的盘子。" },
    ],
  },
  {
    type: "entity", term: "Mate 80", tag: "产品名", confidence: "mid", mustConfirm: true,
    reason: "两路引擎系统性分歧：一路全篇作「80」、另一路全篇作「90」——无法判定指 Mate 80 还是 Mate 90，建议逐处听后分别确认。",
    occurrences: [
      { t: "00:06:35", speaker: "被访者", lineText: "Mate 80 那一波我们备货就没跟上。" },
      { t: "00:07:50", speaker: "主持人", lineText: "那新款 Mate 80 的预约量现在是什么水平？" },
      { t: "00:09:01", speaker: "被访者", lineText: "到了 Mate 80 这一代，预约都排到下个月了。" },
    ],
  },
  // ── 待确认：存疑 ──
  {
    type: "doubt", term: "定义", tag: "", confidence: "low",
    reason: "声学多数为「定义」，但语境紧接「韬定律」、「定律」更通——两种都说得通，建议听一下。",
    occurrences: [
      { t: "00:06:08", speaker: "被访者", lineText: "这个定义我觉得提得挺及时的。" },
    ],
  },
  {
    type: "doubt", term: "战略堡垒店", tag: "", confidence: "low",
    reason: "仅一路引擎清晰，其余模糊；「堡垒店」民间有用但无官方背书——建议听一下确认。",
    occurrences: [
      { t: "00:06:57", speaker: "被访者", lineText: "我们现在在地市推那个战略堡垒店。" },
    ],
  },
  {
    type: "doubt", term: "曾经的一个最合理的库存", tag: "", confidence: "low",
    reason: "「曾经」与「层级」近音：声学多数取「曾经」，但「客户层级的合理库存」语义更通——建议听一下。",
    occurrences: [
      { t: "00:09:22", speaker: "被访者", lineText: "我们一般是按客户曾经的一个最合理的库存去补货。" },
    ],
  },
  {
    type: "doubt", term: "封闭柜", tag: "", confidence: "low",
    reason: "各路引擎互歧（冰柜/封面费/封闭柜），联网可查「封闭柜」为展柜产品名，但无声学多数——建议听一下。",
    occurrences: [
      { t: "00:12:19", speaker: "被访者", lineText: "门店现在都要求上封闭柜。" },
    ],
  },
  {
    type: "doubt", term: "百分之十八", tag: "", confidence: "low",
    reason: "数字打架：一路作「百分之十不到」、另一路作「18%」——增长率影响结论，建议听一下定数。",
    occurrences: [
      { t: "00:04:42", speaker: "被访者", lineText: "上半年我们整体能做到百分之十八的增长。" },
    ],
  },
  {
    type: "doubt", term: "七十五万", tag: "", confidence: "low",
    reason: "量级冲突：说话人先说「七十五万」又改口「七十五」，总投入还是单台数未澄清——建议听一下。",
    occurrences: [
      { t: "00:09:45", speaker: "被访者", lineText: "礼盒那个投入是七十五万，啊不对，七十五。" },
    ],
  },
  {
    type: "doubt", term: "五月二十一号", tag: "", confidence: "low",
    reason: "日期打架：一路作「二十一号」、另一路作「三十一号」，且说话人此前有过改口——建议听一下。",
    occurrences: [
      { t: "00:11:25", speaker: "被访者", lineText: "五月二十一号那场首销，我们一天出了两百多台。" },
    ],
  },
  {
    type: "doubt", term: "我说这有意思", tag: "", confidence: "low", suggestDelete: true,
    reason: "疑幻觉：声学三路在此处全部为空，仅主引擎给出这句孤句——可能是无中生有，建议听一下决定去留。",
    occurrences: [
      { t: "00:12:50", speaker: "被访者", lineText: "我说这有意思。" },
    ],
  },
  {
    type: "doubt", term: "「听不清 · 插话段」", tag: "", confidence: "low", suggestDelete: true,
    reason: "整段含糊：顾客进店插话，各路引擎在此段均严重模糊，仅保留大意——建议听一下补全或确认删除。",
    occurrences: [
      { t: "00:14:45", speaker: "被访者", lineText: "中间有顾客进店 [听不清] 处理换机的事。" },
    ],
  },
  {
    type: "doubt", term: "「听不清 · 人名」", tag: "", confidence: "low",
    reason: "音频此处嘈杂，人名听不清——建议听一下补全。",
    occurrences: [
      { t: "00:15:07", speaker: "被访者", lineText: "后来是 [听不清] 跟我们对接的。" },
    ],
  },
  // ── 自动核实：术语库命中（high）──
  {
    type: "entity", term: "华为", tag: "品牌", confidence: "high",
    reason: "术语库命中，写法无误。",
    occurrences: [
      { t: "00:00:52", speaker: "主持人", lineText: "主要是了解一下华为去年整体的情况。" },
    ],
  },
  {
    type: "entity", term: "Pro 和 Ultra", tag: "产品名", confidence: "high",
    reason: "术语库命中（旗舰双型号合称），写法无误。",
    occurrences: [
      { t: "00:01:54", speaker: "被访者", lineText: "特别是 Pro 和 Ultra，定价还是偏高。" },
    ],
  },
  {
    type: "entity", term: "Nova", tag: "产品名", confidence: "high",
    reason: "术语库命中，写法无误。",
    occurrences: [
      { t: "00:02:20", speaker: "主持人", lineText: "那 Nova 呢？Nova 现在不是才打出来嘛。" },
      { t: "00:04:55", speaker: "主持人", lineText: "那 Nova 今年品牌代言人也换了。" },
    ],
  },
  {
    type: "entity", term: "OV", tag: "竞品", confidence: "high",
    reason: "术语库命中：OV = OPPO + vivo 合称。",
    occurrences: [
      { t: "00:02:58", speaker: "主持人", lineText: "那跟 OV 这边比呢？" },
      { t: "00:03:06", speaker: "被访者", lineText: "OV 把我们的弱势发挥得淋漓尽致。" },
    ],
  },
  {
    type: "entity", term: "Mate 60", tag: "产品名", confidence: "high",
    reason: "四路引擎候选（六年/六零Mate六零/60）按术语库 Mate 系列定字。",
    occurrences: [
      { t: "00:06:25", speaker: "被访者", lineText: "像 Mate 60 那个时候的热度。" },
    ],
  },
  {
    type: "entity", term: "鸿蒙 6", tag: "产品名", confidence: "high",
    reason: "候选（红盟六/虹魔6）按术语库「鸿蒙 = HarmonyOS」定字。",
    occurrences: [
      { t: "00:06:45", speaker: "被访者", lineText: "鸿蒙 6 出来之后，老用户的换机意愿也明显起来了。" },
    ],
  },
  {
    type: "entity", term: "ND 省包", tag: "渠道层级", confidence: "high",
    reason: "术语库命中：ND = National Distributor 省级包销。",
    occurrences: [
      { t: "00:07:30", speaker: "被访者", lineText: "我们 ND 省包 这边一直是跟省代联动的。" },
    ],
  },
  {
    type: "entity", term: "FD", tag: "渠道层级", confidence: "high",
    reason: "候选（FB/fb/fd）按术语库「FD = Fulfillment Distributor」定字。",
    occurrences: [
      { t: "00:13:34", speaker: "被访者", lineText: "太力 在我们这边管 FD 的盘子。" },
    ],
  },
  {
    type: "entity", term: "体验店", tag: "渠道业态", confidence: "high",
    reason: "声学同音误识（甜店/田田店）按术语库「HES 体验店」定字。",
    occurrences: [
      { t: "00:13:17", speaker: "主持人", lineText: "那体验店这边呢？今年的拓店计划是怎么安排的？" },
    ],
  },
  // ── 自动核实：联网（web）──
  {
    type: "web", term: "授权体验店", tag: "渠道业态", confidence: "high",
    reason: "术语库未收录，系统自动联网核实。",
    web: {
      count: 2,
      searches: [
        { query: "华为 授权体验店 HES", summary: "华为授权体验店（HES）为官方授权零售业态，确认存在、写法无误。", source: "web" },
        { query: "华为 授权体验店 加盟 层级", summary: "确认该业态命名与渠道层级关系，写法一致。", source: "web" },
      ],
    },
    occurrences: [
      { t: "00:13:34", speaker: "被访者", lineText: "授权体验店 的货都从他们那里走。" },
    ],
  },
  {
    type: "web", term: "合作店商", tag: "渠道层级", confidence: "high",
    reason: "易与「合作电商」混淆，系统自动联网核实。",
    web: {
      count: 1,
      searches: [
        { query: "华为 合作店商 NDKA 渠道层级", summary: "「合作店商」属 NDKA 渠道层级用语（非「电商」），确认写法无误。", source: "web" },
      ],
    },
    occurrences: [
      { t: "00:13:52", speaker: "被访者", lineText: "合作店商 这一层今年也在扩。" },
    ],
  },
  {
    type: "web", term: "肖战", tag: "人名", confidence: "high",
    reason: "公众人物，系统自动联网核实代言关系。",
    web: {
      count: 1,
      searches: [
        { query: "肖战 代言 华为 Nova 2026", summary: "肖战 2026 年 4 月起代言相关产品线，与访谈语境吻合，写法无误。", source: "web" },
      ],
    },
    occurrences: [
      { t: "00:15:30", speaker: "主持人", lineText: "Nova 这次请肖战代言，店里的物料都到位了吗？" },
    ],
  },
  {
    type: "web", term: "OPPO Reno16", tag: "竞品", confidence: "high",
    reason: "竞品型号，系统自动联网核实命名写法。",
    web: {
      count: 1,
      searches: [
        { query: "OPPO Reno16 在售 价格", summary: "OPPO Reno16 为在售真实机型，标准版高配约 4399 元——确认型号写法无误。", source: "web" },
      ],
    },
    occurrences: [
      { t: "00:15:48", speaker: "被访者", lineText: "OPPO Reno16 那边也在搞路演。" },
    ],
  },
];

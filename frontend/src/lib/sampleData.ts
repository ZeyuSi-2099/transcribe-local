export interface BiText { zh: string; en: string; }
export interface SampleRow { t: string; s: string; }
export interface FileMeta { name: string; size: string; duration: string; }

export type JobStatus = "done" | "uploading" | "queued" | "processing" | "failed";
export interface HistoryItem {
  n: string; d: BiText; dur: string; cost: number; lang: string; st: JobStatus; prog?: number;
  expired?: boolean;   // 已完成但超 30 天，R2 已删内容 → 不可查看/导出
  etaMin?: number | null;  // 处理中·估剩余分钟（无录音时长时为 null，文案回退不显时间）
  disconnected?: boolean;  // 实时行：轮询连续失败判定断连，覆盖进度 caption 为「连接中断，正在重连」
  error?: string | null;   // 失败行：后端脱敏话术（error_public，经 listJobs 的 error 字段返回）
  id?: string;             // 后端任务 id（真实行有；样本数据无）——后处理 caption 的爬升观察键
  projectId?: string | null;   // 归属项目（历史页按项目筛选）；未分组/样本数据 = null/缺省
  // 后处理状态（挂在已完成的转录行下，行内 caption 三态显示；无任务 = null/缺省）
  pp?: {
    status: "queued" | "running" | "done" | "failed";
    stepIndex: number; totalSteps: number; currentStep: string | null;
    qcFixCount: number; products: string[];
  } | null;
}
export interface BillingLine { n: string; d: BiText; dur: string; lang: string; cost: number; }
export interface RecentItem { n: string; d: BiText; c: number | null; lang: string; }

// shared.jsx lines 4–12
export const SAMPLE_TRANSCRIPT: SampleRow[] = [
  { t: "00:00:00", s: `大家好，今天我们想聊一个其实挺简单的问题：当我们说“高质量转录”的时候，到底在说什么？` },
  { t: "00:00:14", s: "过去几年，做这件事的产品很多——免费的、付费的、自动的、人工的。但真正能做到接近人工水平的，其实不多。" },
  { t: "00:00:33", s: "我们的目标只有一个：你上传一段音频，得到一份可以直接拿去用的文字稿。" },
  { t: "00:00:46", s: "不需要再校对，不需要再修整，不需要再补全标点。" },
  { t: "00:00:58", s: "为了做到这件事，我们在模型之外，做了一整套针对中文口语的后处理流程：口语词清理、说话人停顿对齐、专有名词词表。" },
  { t: "00:01:21", s: "这些工程上的事情，听起来都很无聊，但它们决定了你打开转录稿的那一刻——是直接可以用，还是又要花半小时整理。" },
  { t: "00:01:42", s: "所以这次的产品，我们没有做太多东西。一个上传按钮，一个下载按钮，仅此而已。" },
];

// shared.jsx lines 14–18
export const SAMPLE_FILE: FileMeta = {
  name: "2026-05-28 · 产品周会.m4a",
  size: "48.2 MB",
  duration: "42 分 11 秒",
};

// b-main.jsx line 11
export const DEMO_MIN = 42.18;

// b-detail.jsx lines 5–17
export const DETAIL_TRANSCRIPT: SampleRow[] = [
  { t: "00:00:00", s: `大家好，今天我们想聊一个其实挺简单的问题：当我们说“高质量转录”的时候，到底在说什么？` },
  { t: "00:00:14", s: "过去几年，做这件事的产品很多——免费的、付费的、自动的、人工的。但真正能做到接近人工水平的，其实不多。" },
  { t: "00:00:33", s: "我们的目标只有一个：你上传一段音频，得到一份可以直接拿去用的文字稿。" },
  { t: "00:00:46", s: "不需要再校对，不需要再修整，不需要再补全标点。" },
  { t: "00:01:05", s: "为了做到这件事，我们在模型之外，做了一整套针对中文口语的后处理流程：口语词清理、停顿对齐、专有名词词表。" },
  { t: "00:01:38", s: "这些工程上的事情，听起来都很无聊，但它们决定了你打开转录稿的那一刻——是直接可以用，还是又要花半小时整理。" },
  { t: "00:03:12", s: "我们先说说定价。这次我们不做套餐，也不做免费额度，就是单一价：每分钟音频零点一五美元。" },
  { t: "00:05:40", s: "之所以这么定，是因为我们相信，按用量付费对绝大多数人是最公平的——你转得多就付得多，转得少几乎不花钱。" },
  { t: "00:09:21", s: "接下来是产品形态。第一版我们只做一件事：上传、转录、下载。没有实时录音，没有协作，没有多语言。" },
  { t: "00:14:08", s: `有人会问，为什么不一上来就做全？因为我们想先把“中文、高精度、好用”这三件事做扎实，再谈别的。` },
  { t: "00:22:47", s: "关于隐私：你的音频在转录完成后会保留七天，方便你回来下载，之后自动删除，你也可以随时手动删。" },
  { t: "00:35:30", s: "最后是路线图。如果这一版验证成功，下一步我们会加英文、加说话人分离、加 API。但那都是后话了。" },
];

// b-detail.jsx line 20
export const DETAIL_DURATION = 42 * 60 + 11;

// b-screens.jsx lines 150–159
export const HISTORY_ITEMS: HistoryItem[] = [
  { n: "会议录音 · 周一.m4a", d: { zh: "刚刚", en: "Just now" },          dur: "31:20",   cost: 47.00,  lang: "zh", st: "processing", prog: 62 },
  { n: "产品周会.m4a",        d: { zh: "今天 14:22", en: "Today 14:22" },  dur: "42:11",   cost: 63.27,  lang: "zh", st: "done" },
  { n: "用户访谈 03.wav",     d: { zh: "昨天 10:08", en: "Yesterday 10:08" }, dur: "1:08:24", cost: 102.60, lang: "en", st: "done" },
  { n: "podcast ep.42.mp3",  d: { zh: "05.24 16:30", en: "05.24 16:30" }, dur: "58:02",   cost: 87.05,  lang: "zh", st: "done" },
  { n: "电话录音.m4a",        d: { zh: "05.20 09:12", en: "05.20 09:12" }, dur: "12:44",   cost: 19.10,  lang: "zh", st: "done" },
  { n: "会议室 A.mp4",        d: { zh: "05.18 15:50", en: "05.18 15:50" }, dur: "26:33",   cost: 0,      lang: "en", st: "failed" },
  { n: "讲座录音.m4a",        d: { zh: "05.15 19:40", en: "05.15 19:40" }, dur: "1:42:08", cost: 153.20, lang: "en", st: "done" },
  { n: "采访稿初版.wav",      d: { zh: "05.12 11:25", en: "05.12 11:25" }, dur: "33:51",   cost: 50.78,  lang: "zh", st: "done" },
];

// b-screens.jsx lines 360–367
export const BILLING_LINES: BillingLine[] = [
  { n: "产品周会.m4a",       d: { zh: "今天 14:22", en: "Today 14:22" },      dur: "42:11",   lang: "zh", cost: 63.27  },
  { n: "用户访谈 03.wav",    d: { zh: "昨天 10:08", en: "Yesterday 10:08" },  dur: "1:08:24", lang: "en", cost: 102.60 },
  { n: "podcast ep.42.mp3", d: { zh: "05.24 16:30", en: "05.24 16:30" },     dur: "58:02",   lang: "zh", cost: 87.05  },
  { n: "电话录音.m4a",       d: { zh: "05.20 09:12", en: "05.20 09:12" },     dur: "12:44",   lang: "zh", cost: 19.10  },
  { n: "讲座录音.m4a",       d: { zh: "05.15 19:40", en: "05.15 19:40" },     dur: "1:42:08", lang: "en", cost: 153.20 },
  { n: "采访稿初版.wav",     d: { zh: "05.12 11:25", en: "05.12 11:25" },     dur: "33:51",   lang: "zh", cost: 50.78  },
];

// 充值记录（发票按"充值"开，不按消费开；每笔最多开一次票）
/** refunded = 这笔充值已被退回多少美元（0 = 没退过）。缺省当 0，老演示数据不用改。 */
/** gift = 推荐礼金那一行：显示在充值页里（它加的是同一个余额），但不开票、不退款 */
export interface TopupRecord { id: string; d: BiText; amount: number; invoiced?: boolean; refunded?: number; gift?: boolean; pending?: boolean; }
export const TOPUP_RECORDS: TopupRecord[] = [
  { id: "tp-0608", d: { zh: "06.08 10:12", en: "06.08 10:12" }, amount: 50 },
  { id: "tp-0520", d: { zh: "05.20 09:30", en: "05.20 09:30" }, amount: 100, invoiced: true },
  { id: "tp-0506", d: { zh: "05.06 14:02", en: "05.06 14:02" }, amount: 50 },
  { id: "tp-0418", d: { zh: "04.18 11:20", en: "04.18 11:20" }, amount: 30, invoiced: true },
];

// b-main.jsx lines 356–361
export const PAID_RECENTS: RecentItem[] = [
  { n: "产品周会.m4a",       d: { zh: "2 小时前", en: "2h ago" },      c: 63.27,  lang: "zh" },
  { n: "用户访谈 03.wav",    d: { zh: "昨天", en: "Yesterday" },       c: 102.60, lang: "en" },
  { n: "podcast ep.42.mp3", d: { zh: "05.24", en: "05.24" },           c: 87.05,  lang: "zh" },
  { n: "电话录音.m4a",       d: { zh: "05.20", en: "05.20" },           c: 19.10,  lang: "zh" },
];

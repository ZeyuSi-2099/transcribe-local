// 定价唯一来源（前端侧）。别处不许硬编码价格。
//
// ⚠️ 这几个数必须与后端 server/app/pricing.py 完全一致——后端测试 test_pricing_parity.py
// 会读本文件逐项比对，改一边忘改另一边会红。**钱的权威始终在后端**：这里算的只是给用户看的
// 预估，真实扣费按上传那一刻钉死在 job 上的费率快照（jobs.rate_cents_per_min）结算。
//
// 🔴 **以后任何价格必须是 $0.60/小时 的整数倍**（2026-08-31 定价 V3 换来的约束）：
// 后端 jobs.rate_cents_per_min 是整数分/分钟，$3.00/小时 = 5 分/分钟正好整除。定一个这张
// 网格上表达不出来的价（$2.50/小时 = 4.1667 分/分钟），就得先给那个字段换更细的单位并迁移
// 全部存量单——而那件事必须与改价同一次上线，改价那天你不会想同时动结算字段。

/** 单价（USD/分钟）：27 门语言同价。$3.00/小时 = $0.05/分钟（2026-08-31 定价 V3）。 */
export const RATE_PER_MIN = 0.05;
/** 每小时单价（USD）。**派生，不写死**：两个数各写各的，改一个忘一个那天不会有任何报错。 */
export const RATE_PER_HOUR = RATE_PER_MIN * 60;

export const TOPUP_TIERS = [10, 30, 60] as const; // USD quick-select amounts.
export const MIN_TOPUP = 10; // USD, lowest allowed top-up.

/** 单文件上限（展示口径；权威在后端 config.MAX_DURATION_SEC / MAX_UPLOAD_BYTES）。
 * 2026-08-15 起**没有**「免费额度只能用于短文件」那道闸了——免费额度是「用多少扣多少」，
 * 一个号最多也就白拿 180/60 分钟，切成一个长文件还是几个短文件成本完全一样，
 * 那道闸唯一的作用是让企业号的额度覆盖不了满 4 小时（240 分钟）的文件。 */
/** 新号免费额度（分钟）：企业 / 学术邮箱 180 · 个人邮箱 60。
 *  ⛔ 红线：**全站只有这一组数**（老账号 300 分钟不回收，也不对外宣传）。
 *  营销文案目录里那几处仍写静态数字（目录自己的约定允许纯数字），但组件里要用这两个常量
 *  ——组件散着写就会出现「这一页 180、那一页 120」而没有任何东西会红。
 *  后端口径在 server/app/freebies.py，两侧改动必须同一次上线。 */
export const FREE_WORK_MIN = 180;
export const FREE_PERSONAL_MIN = 60;

export const MAX_FILE_SEC = 4 * 3600;
export const MAX_FILE_GB = 2;

/** 单价（USD/分钟）。
 *
 * ⚠️ **lang 参数留着不删**（现在被忽略是有意的）：全部调用点的形状因此一个字都不用改，
 * 将来若再按语种分档，只改这一个函数即可。同后端 pricing.rate_cents_per_min。 */
export function rateFor(_lang?: string): number {
  return RATE_PER_MIN;
}

/** 预估费用（USD）：时长 × 单价。真实扣费以后端为准。 */
export function costFor(durationSec: number, lang?: string): number {
  return (durationSec / 60) * rateFor(lang);
}

/** 这笔钱能转多少小时（充值档位下面那行「≈ N 小时」）。 */
export function hoursFor(usdAmount: number): number {
  return usdAmount / RATE_PER_HOUR;
}

// ── 后处理（视角转换/脱敏）：每步加价率，选几步加几步 ──
// 计费基数=整段音频时长，按秒折算（与转录同口径）；免费额度剩余秒结算时可抵（不限文件长短）。
// ⚠️ 与后端 PP_STEP_RATES_CENTS 逐项对账（parity 测试盯着）；真实计费以后端发起时的快照为准。
// 2026-08-31 定价 V3：0.05 → 0.01（= $0.60/小时·每步），与转录同比例下调。
export type PPStep = "narrate" | "redact";
export const PP_STEP_RATES: Record<PPStep, number> = { narrate: 0.01, redact: 0.01 };

/** 所选步骤的合计加价率（USD/分钟）。 */
export function ppRateFor(steps: PPStep[]): number {
  return steps.reduce((s, k) => s + PP_STEP_RATES[k], 0);
}

/** 所选步骤的合计加价率（USD/小时）——对外一律按小时报价。 */
export function ppRatePerHourFor(steps: PPStep[]): number {
  return ppRateFor(steps) * 60;
}

/** 后处理预估费用（USD）：合计加价率 × 音频时长，按秒折算（未计免费额度抵扣）。 */
export function ppCostFor(steps: PPStep[], durationSec: number): number {
  return (Math.max(0, durationSec) / 60) * ppRateFor(steps);
}

export function usd(n: number): string {
  return "$" + n.toFixed(2);
}

export function clock(mins: number): string {
  const m = Math.max(0, Math.floor(mins));
  const s = Math.max(0, Math.round((mins - m) * 60));
  return `${m}:${String(s).padStart(2, "0")}`;
}

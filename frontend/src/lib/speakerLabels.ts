// 说话人标签：**代号 → 界面语言**的唯一来源（前端侧）。
//
// ⚠️ **后端有一份逐条相同的表**（`server/app/speaker_labels.py`），导出 docx/txt 与后处理产物
//    下载都用它。两份靠 `server/tests/test_speaker_label_parity.py` 读本文件逐项比对钉住
//    ——分家的症状是「界面上写 Interviewer、下载下来是主持人」，不报错，只是对不上。
//    （同一套做法见价目表：`src/lib/pricing.ts` ↔ `server/app/pricing.py`。）
//
// ## 为什么代号是中文词
//
// 稿子的 `sp` 字段里存的是**代号**，不是屏幕上那串字。代号沿用流水线本来就在产的那几个中文词
// （`server/pipeline/transcript.py` 的 SPEAKER_MAP），理由有三条：
//   ① 流水线出稿写的就是它，换代号要连流水线一起改；
//   ② 后处理的输入稿由 `app/redact_diff.segments_to_qa` 拼成 `说话人：文本`，
//      而 pp-narrate 的规则按这几个中文词匹配（SKILL.md 明写「本平台问答体说话人是中文显示名」）；
//   ③ 用户一个字都看不到代号——它长什么样不影响任何人。
//
// ⚠️ **绝不能把「屏幕上那串字」写回 `sp`**（2026-08-29 修的 bug）：那样翻译就只剩单向，
//    英文界面点了 Other、中文用户打开还是看到 Other；而且后处理的规则会匹配不上。
//
// ## 为什么不放进 appI18n 对照本
//
// 对照本**以英文原句为索引**，而「其他」的英文原句 `Other` 与脱敏类别清单里的 `Other` 撞车
// ——那边是复数语境，于是说话人按钮在法/西/葡界面上显示成 `Autres / Otros / Outros`
// （「其他们」）。标签有自己的语义域，就该有自己的表。

/** 稿子里允许出现的固定代号。「说话人 N」不在其中，走 UNKNOWN_PREFIX 前缀规则。 */
export const SPEAKER_CODES = ["主持人", "被访者", "其他", "多人混合"] as const;
export type SpeakerCode = (typeof SPEAKER_CODES)[number];

/** 认不出角色时流水线写的前缀，后面跟一个序号（`说话人 2`）。 */
export const UNKNOWN_PREFIX = "说话人";

/** 代号 → 各界面语言。**改这里必须同步改 speaker_labels.py**（对账测试会红）。 */
export const SPEAKER_LABELS: Record<string, Record<string, string>> = {
  zh: { 主持人: "主持人", 被访者: "被访者", 其他: "其他", 多人混合: "多人混合", 说话人: "说话人" },
  en: { 主持人: "Interviewer", 被访者: "Interviewee", 其他: "Other", 多人混合: "Multiple speakers", 说话人: "Speaker" },
  de: { 主持人: "Interviewer", 被访者: "Befragte Person", 其他: "Sonstige", 多人混合: "Mehrere Sprecher", 说话人: "Sprecher" },
  fr: { 主持人: "Intervieweur", 被访者: "Personne interrogée", 其他: "Autre", 多人混合: "Plusieurs locuteurs", 说话人: "Locuteur" },
  es: { 主持人: "Entrevistador", 被访者: "Persona entrevistada", 其他: "Otro", 多人混合: "Varios hablantes", 说话人: "Hablante" },
  it: { 主持人: "Intervistatore", 被访者: "Persona intervistata", 其他: "Altro", 多人混合: "Più interlocutori", 说话人: "Interlocutore" },
  pt: { 主持人: "Entrevistador", 被访者: "Pessoa entrevistada", 其他: "Outro", 多人混合: "Vários locutores", 说话人: "Locutor" },
  ja: { 主持人: "インタビュアー", 被访者: "回答者", 其他: "その他", 多人混合: "複数話者", 说话人: "話者" },
};

/**
 * 说话人代号 → 界面语言的显示名。
 *
 * 认不出的值**原样返回**——用户自己改过的名字、老任务里存进去的显示文字，都不该被吞掉。
 */
export function speakerLabel(sp: string | null | undefined, uiLang: string): string {
  if (!sp) return "";
  const t = SPEAKER_LABELS[(uiLang || "en").split("-")[0].toLowerCase()] ?? SPEAKER_LABELS.en;
  if (t[sp]) return t[sp];
  if (sp.startsWith(UNKNOWN_PREFIX)) {
    return `${t[UNKNOWN_PREFIX]} ${sp.slice(UNKNOWN_PREFIX.length).trim()}`.trim();
  }
  return sp;
}

/** 说话人标签与句子之间的分隔符（导出与后处理输入用）：中文配全角，其余配半角加空格。 */
export function speakerSep(uiLang: string): string {
  return (uiLang || "en").split("-")[0].toLowerCase() === "zh" ? "：" : ": ";
}

// 去 AI 化禁语清单——**全仓唯一允许出现这些字面量的文件**（与 bannedRefundPhrases 同规矩）。
// 为什么禁：Mercury 2026-05-26 拒签，推测主因之一是业务描述里的 "AI transcription" 撞上
// 2025-2026 收紧的 AI startup 策略；此后给 Wise / Relay 的业务描述一律零 AI 词。对外只讲
// 「转录引擎 / 自动核对 / 复核」，不讲实现。MoR 收款方要担法律卖方责任，注册账号进产品里看
// 是常规动作——**应用内界面和公开页一样在审核视线内**，只做公开页就是漏的（2026-07-28 教训：
// 术语库选择器的英文说明里写着 "the AI corrects…"，中文原文并没有，是翻译单方面引入的）。
//
// ⚠️ 扫描范围只能是「用户可见文案」（字符串字面量 + JSX 文本）：\bAI\b 扫全部源码会误伤标识符
// 和依赖名，噪音逼着加一堆豁免，守卫就形同虚设了。注释里讨论 AI（比如本文件）不算违规。

// \bAI\b 单独判、不带 /i：加了 /i 会去匹配小写 ai，误伤面变大。
// 其余语言的「AI」缩写同理必须区分大小写整词判——小写的 ia/ki 在西/法/意/葡/德文里
// 是常见词尾与常见词（copia、merci… / kein…），带 /i 扫会把整份译文淹掉。
export const BANNED_AI_EXACT = /\bAI\b|\bIA\b|\bKI\b|\bIAs\b|\bKIs\b/;
export const BANNED_AI_LOOSE =
  /artificial intelligence|\bGPT\b|\bLLM\b|Whisper|machine learning|neural|人工智能|大模型/i;
// 各语言的实现词（8 门放量后的主要漏点：译者会「好心」把「引擎」还原成「语言模型」）
export const BANNED_AI_LANGS =
  /künstliche[rn]? Intelligenz|maschinelle[srn]? Lernen|Sprachmodell|neuronale|Spracherkennungsmodell|intelligence artificielle|apprentissage automatique|modèle de langue|réseau de neurones|inteligencia artificial|aprendizaje automático|modelo de lenguaje|red(es)? neuronal|intelligenza artificiale|apprendimento automatico|modello linguistico|inteligência artificial|aprendizado de máquina|modelo de linguagem|人工知能|機械学習|言語モデル|ニューラル/i;

export function hitsAiPhrase(text: string): boolean {
  return BANNED_AI_EXACT.test(text) || BANNED_AI_LOOSE.test(text) || BANNED_AI_LANGS.test(text);
}

// 从源码里取出「用户可能读到的文本」：带引号的字符串字面量 + JSX 文本节点。
// 本项目所有 UI 文案都走 useL(zh, en) / L(…) / T(…) / zh·en 字段，全在这两类里；
// 注释（不带引号）与变量名天然被排除——这正是范围限定的关键。
export function userFacingStrings(src: string): string[] {
  const out: string[] = [];
  const quoted = /(["'`])(?:\\.|(?!\1)[\s\S])*?\1/g;          // "…" '…' `…`
  const jsxText = />([^<>{}]*[^\s<>{}][^<>{}]*)</g;           // <span>这里的文本</span>
  for (const m of src.matchAll(quoted)) out.push(m[0]);
  for (const m of src.matchAll(jsxText)) out.push(m[1]);
  return out;
}

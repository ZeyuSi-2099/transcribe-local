export interface LangDef {
  id: string;
  /** [zh display, en display] — 中文界面显示规范中文译名，英文界面显示英文名 */
  name: [string, string];
}

// 台面上的 11 门。**选谁进来**和**摆成什么顺序**是两件事，别混着改：
//
// ① 选取规则＝「全球使用人数前 10（限我们支持的 27 门）」∪「我们自己的 8 门界面语言」。
//    前者给客观基准，后者保底——已经花钱做了本地化、在那些市场做了服务承诺的语言不该藏在下拉里。
//    意大利语（约 0.65 亿，排十五名开外）只凭第二条腿进来；阿拉伯语/印尼语/俄语只凭第一条腿进来。
//    要增删名单，回到这条规则上论证。
//
// ② ⚠️ **摆放顺序是 Duner 定的（2026-08-21），不是按使用人数排的**，下面的分行是有意的：
//      en zh es ／ fr ar pt ／ de ru id ／ ja it 更多
//    上线首日曾按人数降序（en zh es ar fr pt id ru de ja it），当天改成现在这样。
//    **别拿人数去「修正」它**——看着乱不是 bug。真要动，先问人。
//    （唯一硬约束：英语必须在第 0 位，它是上传页默认选中项，也是 langName 的兜底。）
//
// ⚠️ 2026-08-21 之前露出的是 en zh es fr pt de it —— 恰好是 8 门界面语言减去日语，
// 也就是跟着「网站翻译成了哪几门」走，而不是跟着「用户会上传什么语言的录音」走。
// 于是全球第 4 大的阿拉伯语在下拉里，而意大利语在台面上。27 门的引擎配置完全一致
// （主轨 + 3 条参考轨，见 pipeline/vendor/Config/languages/*.yaml），分组从来没有技术理由。
export const COMMON_LANGS: LangDef[] = [
  // 第 1 行 —— 英语排第一＝上传页默认选中项（langName 的兜底也取这里的第 0 个）
  { id: "en", name: ["英语", "English"] },
  { id: "zh", name: ["中文", "Chinese"] },
  { id: "es", name: ["西班牙语", "Spanish"] },
  // 第 2 行
  { id: "fr", name: ["法语", "French"] },
  { id: "ar", name: ["阿拉伯语", "Arabic"] },
  { id: "pt", name: ["葡萄牙语", "Portuguese"] },
  // 第 3 行
  { id: "de", name: ["德语", "German"] },
  { id: "ru", name: ["俄语", "Russian"] },
  { id: "id", name: ["印尼语", "Indonesian"] },
  // 第 4 行 —— 第三格是「更多」，由 LangPicker 补上，凑满三列
  { id: "ja", name: ["日语", "Japanese"] },
  { id: "it", name: ["意大利语", "Italian"] },
];

// 「更多」：剩下 16 门，同样按使用人数降序（全部可选，只是不占台面）
export const MORE_LANGS: LangDef[] = [
  { id: "vi", name: ["越南语", "Vietnamese"] },
  { id: "tr", name: ["土耳其语", "Turkish"] },
  { id: "ko", name: ["韩语", "Korean"] },
  { id: "th", name: ["泰语", "Thai"] },
  { id: "pl", name: ["波兰语", "Polish"] },
  { id: "uk", name: ["乌克兰语", "Ukrainian"] },
  { id: "nl", name: ["荷兰语", "Dutch"] },
  { id: "ro", name: ["罗马尼亚语", "Romanian"] },
  { id: "ms", name: ["马来语", "Malay"] },
  { id: "el", name: ["希腊语", "Greek"] },
  { id: "sv", name: ["瑞典语", "Swedish"] },
  { id: "hu", name: ["匈牙利语", "Hungarian"] },
  { id: "cs", name: ["捷克语", "Czech"] },
  { id: "da", name: ["丹麦语", "Danish"] },
  { id: "fi", name: ["芬兰语", "Finnish"] },
  { id: "no", name: ["挪威语", "Norwegian"] },
];

export const ALL: LangDef[] = [...COMMON_LANGS, ...MORE_LANGS];

type LFn = <T>(zh: T, en: T) => T;

export function langName(id: string, L: LFn): string {
  const o = ALL.find((x) => x.id === id) ?? COMMON_LANGS[0];
  return capFirst(L(o.name[0], o.name[1]));
}

/**
 * 首字母大写——**只为应用内的语种名**（选择器格子、历史表的语言列）。
 *
 * 六门译名派生自营销站的 langNames*（见 lib/appI18n/index.ts），那份是给句子里用的，
 * 所以法/西/意/葡按各自语文规范写成小写（chinois、español…）。但「English」在对照本里
 * 另有一条（它同时是价档名），是大写的 —— 于是选择器里会出现「Anglais｜chinois｜espagnol」
 * 这样一行大小写打架。这里统一抬成首字母大写：独立成项的语种名本就该大写，
 * 且中日文与德语（名词本就大写）都不受影响。
 */
function capFirst(s: string): string {
  return s ? s[0].toLocaleUpperCase() + s.slice(1) : s;
}

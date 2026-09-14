// 应用内（登录后）多语言的「对照本」层（2026-08-02，docs/app-i18n-STATE.md）。
//
// 营销/公开页走文案目录（en.tsx 是键的唯一定义处，其余七门 satisfies MsgCatalog 编译期对齐）。
// 应用内不这么做：那是已经跑了几个月的交互代码，把 431 个 L(zh, en) 调用点逐个改成写编号，
// 等于为了翻译去动上传、复核、计费的逻辑——风险与收益不成比例。
//
// 于是改成外挂：**调用点一行不动**，另建六本以「英文原句」为索引的对照表。
//   L("上传音频", "Upload audio")  +  app.de.ts 里 "Upload audio": "Audio hochladen"
//
// 这套做法的代价与护栏：
//   · 英文原句改了 → 索引失配 → 那一句**回落英文**（不会读到过期译文，这是它比编号方案安全的地方）。
//     `appI18n.guard.test.ts` 会扫源码，对照本里出现源码中不存在的 key 就红，提示「原文改了，六门要跟」。
//   · 同一句英文在两处是两个意思 → 会一处翻错全站错。立项时扫出 4 处（Show more / Request invoice /
//     Confirm close / Narrative draft），已改英文原文消歧；守卫扫不出这类语义问题，**新增文案时自己留意**。
//   · 只有已放量的语言（LIVE_APP_LANGS）要求 100% 覆盖，未放量的可以只翻一半——灰度就是这么做的。
import type { UILang } from "../i18n";
import { ALL } from "../langs";
// 本机版：语种名表从营销站的数据文件抽到本目录（不搬营销站），本地新增文案的译文另放 local.ts
import { langNamesDe, langNamesEs, langNamesFr, langNamesIt, langNamesJa, langNamesPt, type LangNameOverlay } from "./langNames";
import { LOCAL_OVERRIDES } from "./local";
import { appDe } from "./app.de";
import { appFr } from "./app.fr";
import { appEs } from "./app.es";
import { appIt } from "./app.it";
import { appPt } from "./app.pt";
import { appJa } from "./app.ja";

/** 英文原句 → 译文。key 必须与源码里 L() 的第二个实参逐字一致（含标点与空格）。 */
export type AppOverride = Record<string, string>;

const LANG_NAME_OVERLAYS: Partial<Record<UILang, LangNameOverlay>> = {
  de: langNamesDe, fr: langNamesFr, es: langNamesEs, it: langNamesIt, pt: langNamesPt, ja: langNamesJa,
};

/**
 * 27 门语种名（音频语言选择器、历史表的语言列）不在对照本里另抄一份 ——
 * 营销站的语种页早就为八门各备了一套（i18n/data.<lang>.ts 的 langNames*）。
 * 这里按「英文名 → 该语言的名字」转成对照本条目：抄第二份必然漂移，派生不会。
 */
function langNameEntries(lang: UILang): AppOverride {
  const ov = LANG_NAME_OVERLAYS[lang];
  if (!ov) return {};
  return Object.fromEntries(ALL.flatMap((o) => (ov[o.id] ? [[o.name[1], ov[o.id]]] : [])));
}

export const APP_OVERRIDES: Partial<Record<UILang, AppOverride>> = {
  de: { ...langNameEntries("de"), ...appDe, ...LOCAL_OVERRIDES.de },
  fr: { ...langNameEntries("fr"), ...appFr, ...LOCAL_OVERRIDES.fr },
  es: { ...langNameEntries("es"), ...appEs, ...LOCAL_OVERRIDES.es },
  it: { ...langNameEntries("it"), ...appIt, ...LOCAL_OVERRIDES.it },
  pt: { ...langNameEntries("pt"), ...appPt, ...LOCAL_OVERRIDES.pt },
  ja: { ...langNameEntries("ja"), ...appJa, ...LOCAL_OVERRIDES.ja },
};

/**
 * 应用内已放量的界面语言（灰度开关：一门翻完验收一门，才进这个清单）。
 * 侧栏语言菜单只列这里面的语言；守卫也只对这里面的语言要求 100% 覆盖。
 *
 * ⚠️ 与营销页的 LIVE_MARKETING_LANGS 是两个开关：营销页早已八门，应用内单独放量。
 */
export const LIVE_APP_LANGS: UILang[] = ["en", "zh", "de", "fr", "es", "it", "pt", "ja"];

/** 查对照本；zh/en 是原生两门不查表，六门查不到就返回 undefined（调用方回落英文）。 */
export function lookupApp(uiLang: UILang, en: string): string | undefined {
  if (uiLang === "zh" || uiLang === "en") return undefined;
  return APP_OVERRIDES[uiLang]?.[en];
}

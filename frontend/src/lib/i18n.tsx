import { createContext, Fragment, useContext, useEffect, useState, type ReactNode } from "react";
import { lookupApp } from "./appI18n";
import { cjkFor, ensureCjkFont } from "./cjkFonts";

// 界面语言（8 门）：营销/公开页按 LIVE_MARKETING_LANGS 放量（docs/i18n-8-languages-plan.md）；
// 应用内（登录后）按 LIVE_APP_LANGS 放量，文案走 lib/appI18n 的对照本（docs/app-i18n-STATE.md）。
// L 的回落：zh→中文，en→英文，其余六门→查对照本、查不到退英文（绝不退中文）。
export type UILang = "zh" | "en" | "es" | "fr" | "de" | "it" | "pt" | "ja";

export const ALL_UI_LANGS: UILang[] = ["en", "zh", "es", "fr", "de", "it", "pt", "ja"];

/** 已放量的营销界面语言（灰度开关：翻译验收一门放一门；未放量的路径前缀一律回根） */
export const LIVE_MARKETING_LANGS: UILang[] = ["en", "zh", "de", "fr", "es", "it", "pt", "ja"];

/** 语言菜单里的自称（各语言写自己） */
export const LANG_NATIVE: Record<UILang, string> = {
  en: "English", zh: "中文", es: "Español", fr: "Français",
  de: "Deutsch", it: "Italiano", pt: "Português", ja: "日本語",
};

/** <html lang> 值（审查员与爬虫看源码的语言信号）。预渲染写静态 HTML 时也用它，别另写一份 */
export const HTML_LANG: Record<UILang, string> = {
  en: "en", zh: "zh-CN", es: "es", fr: "fr", de: "de", it: "it", pt: "pt", ja: "ja",
};

/** URL 语言前缀：en=根路径（x-default），其余 /{lang}/...（SEO 独立收录的关键） */
export function langHref(path: string, lang: UILang): string {
  const p = path === "" ? "/" : path;
  if (lang === "en") return p;
  return p === "/" ? `/${lang}` : `/${lang}${p}`;
}

/** 路径首段若是已放量语言则返回之（/de/pricing → de）；未放量/非语言段 → null */
export function pathLangOf(pathname: string): UILang | null {
  const seg = pathname.split("/")[1] ?? "";
  return (LIVE_MARKETING_LANGS as string[]).includes(seg) && seg !== "en" ? (seg as UILang) : null;
}

/** 去掉语言前缀后的站内路径（/de/pricing → /pricing；/de → /） */
export function stripLangPrefix(pathname: string): string {
  const lang = pathLangOf(pathname);
  if (!lang) return pathname;
  const rest = pathname.slice(lang.length + 1);
  return rest === "" ? "/" : rest;
}

/** 浏览器语言 → 已放量语言（pt-BR→pt、zh-TW→zh；配不上 → null） */
function matchNav(nav: string): UILang | null {
  const low = nav.toLowerCase();
  for (const l of LIVE_MARKETING_LANGS) {
    if (low === l || low.startsWith(`${l}-`)) return l;
  }
  return null;
}

interface UILangCtx {
  uiLang: UILang;
  setUiLang: (v: UILang) => void;
}

const Ctx = createContext<UILangCtx>({ uiLang: "en", setUiLang: () => {} });

const LANG_KEY = "tx_lang";

// 语言判定：URL 前缀 > 存储 > 浏览器语言 > 英文。
//
// ⚠️ 「一个网址一种语言」不是靠这里保证的，是靠 **applyFirstVisitLangRedirect**：
// 它在本函数之前同步把无前缀网址换成带前缀的同一页，所以轮到这里时 URL 已经带上语言了。
// 别反过来把这里改成「只认 URL」——登录后的应用没有语言 URL 树，只认 tx_lang，
// 改了会让德语用户一登录整个应用掉回英文。
function readLang(): UILang {
  try {
    const fromPath = pathLangOf(window.location.pathname);
    if (fromPath) return fromPath;
    return preferredLang();
  } catch {
    return "en";
  }
}

/** 访客偏好：存储过的选择 → 浏览器语言 → 英文。 */
export function preferredLang(): UILang {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved && (LIVE_MARKETING_LANGS as string[]).includes(saved)) return saved as UILang;
    const nav = (navigator.languages && navigator.languages[0]) || navigator.language || "";
    return matchNav(nav) ?? "en";
  } catch {
    return "en";
  }
}

/** 落地即对齐：无前缀网址 + 访客偏好不是英文 → 就地换到带前缀的同一页。
 *
 * 这是「一个网址一种语言」的执行点，也是本轮 SEO 审计 A1 的正解。
 * 在此之前，`/pricing` 会按访客存储渲染成德语，而它的 canonical、hreflang、
 * 预渲染 head 全写着英文——搜索引擎收到的是「同一个网址，八份内容，都自称是那一个网址」。
 * 「按偏好招待人」与「一个网址一种语言」的唯一兼容做法：**换网址，不换网址上的语言**。
 *
 * 必须在 UILangProvider 读语言**之前**同步跑完——晚一步它就按旧路径判定了。
 * 返回是否改过 URL，仅供测试断言。
 *
 * 三种情况一律不动：已带前缀（URL 说了算）· 偏好就是英文 · **会被应用接管的那几个路径**
 * 带着会话时（去的是应用，应用没有语言 URL 树）。
 *
 * ⚠️ 「带着会话」这一条**只对下面这几个路径成立**，不是对整个站成立。此前它是全站短路，
 * 于是登录用户打开 `/pricing` 时：既不跳到 `/zh/pricing`，又要在**英文的预渲染骨架**上
 * 渲染中文——React 水合失败（#418/#423/#425 各若干条），把整棵印好的正文丢掉重画，
 * 首屏先闪一遍英文再翻成中文（2026-08-22 巡检实见）。公开页登录与否渲染的都是营销页，
 * 该跳就得跳。`/pay` 也留在名单里：一次性结账链接、robots 禁收录，没有跳的理由。
 */
const SESSION_OWNED = new Set(["/", "/signin", "/pay"]);
let langRedirectDone = false;
export function applyFirstVisitLangRedirect(hasSession: boolean): boolean {
  if (langRedirectDone || typeof window === "undefined") return false;
  langRedirectDone = true;
  try {
    if (hasSession && SESSION_OWNED.has(window.location.pathname.replace(/\/$/, "") || "/")) return false;
    if (pathLangOf(window.location.pathname)) return false;
    const want = preferredLang();
    if (want === "en") return false;
    history.replaceState(null, "", hrefForLang(want));
    return true;
  } catch {
    return false;
  }
}

/** 仅测试用：允许一个用例跑完后重置「只跳一次」的闸 */
export function __resetLangRedirect() { langRedirectDone = false; }

/** `initial` 只给构建期预渲染用：Node 里没有 window，readLang 会一路回落英文，
 *  于是八门网址会印出同一份英文正文。传进来才能按网址钉死语言。
 *  浏览器里永远不传——语言得由 URL 前缀现算，传死了「切换语言」就不动了。 */
export function UILangProvider({ children, initial }: { children: ReactNode; initial?: UILang }) {
  const [uiLang, setLang] = useState<UILang>(initial ?? readLang);
  const setUiLang = (v: UILang) => {
    setLang(v);
    try { localStorage.setItem(LANG_KEY, v); } catch { /* localStorage 不可用时忽略 */ }
  };
  // 同步 <html lang>，让网页源码的语言信号与实际界面语言一致（审查员看 source）。
  // 字体栈按 :lang() 分（tokens.css），所以这一行同时决定用哪套字体——CJK 那两套按需拉。
  useEffect(() => {
    try {
      document.documentElement.lang = HTML_LANG[uiLang];
      const k = cjkFor(uiLang);
      if (k) ensureCjkFont(k);
    } catch { /* 无 document 时忽略 */ }
  }, [uiLang]);
  // URL 前缀带来的语言也要记住。德语访客的完整路径是 /de → /de/signin → 登录后应用跳到无前缀的 `/`：
  // 单页内跳转时 uiLang 还在内存里所以当次没事，但**下次直接打开域名**就只剩 localStorage，
  // 而它从没被写过 → 界面掉回英文。用户的感受是「网站忘了我的语言」。（2026-08-02）
  useEffect(() => {
    try {
      if (pathLangOf(window.location.pathname)) localStorage.setItem(LANG_KEY, uiLang);
    } catch { /* localStorage 不可用时忽略 */ }
  }, [uiLang]);
  return <Ctx.Provider value={{ uiLang, setUiLang }}>{children}</Ctx.Provider>;
}

export function useUILang(): UILangCtx {
  return useContext(Ctx);
}

export interface LFn {
  /** L(中文, 英文)：其余六门以英文原句查对照本，查不到回落英文 */
  <T>(zh: T, en: T): T;
  /** 带变量的纯文本：占位符 `{0} {1}…` 按序替换。索引键=英文模板串本身。
   *  别再写 L(`共 ${n} 条`, `${n} entries`) —— 那样的英文原句每次都不同，对照本索引不到。 */
  t(zh: string, en: string, ...args: (string | number)[]): string;
  /** 带变量的富文本：参数可以是 <b>…</b> 这类节点，返回 ReactNode。语序不同的语言靠占位符换位。 */
  x(zh: string, en: string, ...args: ReactNode[]): ReactNode;
}

const PLACEHOLDER = /\{(\d+)\}/g;

function fillText(tpl: string, args: (string | number)[]): string {
  return tpl.replace(PLACEHOLDER, (whole, i) => {
    const v = args[Number(i)];
    return v === undefined ? whole : String(v);
  });
}

function fillNodes(tpl: string, args: ReactNode[]): ReactNode {
  const parts = tpl.split(/(\{\d+\})/);
  return (
    <>
      {parts.map((p, i) => {
        const m = /^\{(\d+)\}$/.exec(p);
        const v = m ? args[Number(m[1])] : undefined;
        return <Fragment key={i}>{m ? (v === undefined ? p : v) : p}</Fragment>;
      })}
    </>
  );
}

/** Returns L(zh, en) bound to the current UI language. Works for strings and ReactNodes.
 *  回落规则：zh → 中文；en → 英文；其余六门 → 查应用内对照本（src/lib/appI18n），查不到回落英文。
 *  营销/公开页不走这里，走 screens/marketing/i18n 的文案目录。 */
export function useL(): LFn {
  const { uiLang } = useUILang();
  const L = function <T>(zh: T, en: T): T {
    if (uiLang === "zh") return zh;
    if (typeof en === "string") {
      const hit = lookupApp(uiLang, en);
      if (hit !== undefined) return hit as unknown as T;
    }
    return en;
  } as LFn;
  L.t = (zh, en, ...args) => fillText(uiLang === "zh" ? zh : lookupApp(uiLang, en) ?? en, args);
  L.x = (zh, en, ...args) => fillNodes(uiLang === "zh" ? zh : lookupApp(uiLang, en) ?? en, args);
  return L;
}

/** 当前网址去掉语言前缀后的站内路径（`/de/pricing` → `/pricing`；`/` → `/`） */
export function currentBarePath(): string {
  if (typeof window === "undefined") return "/";
  return stripLangPrefix(window.location.pathname.replace(/\/$/, "")) || "/";
}

/** 某门语言下的「当前这一页」的完整网址（语言菜单的 href；也是 canonical 的算法） */
export function hrefForLang(l: UILang): string {
  if (typeof window === "undefined") return "/";
  return langHref(currentBarePath(), l) + window.location.search + window.location.hash;
}

/** 公开页切语言：改语言的同时把 URL 前缀换掉。
 *  只写 localStorage 不改 URL 的话，英文界面会挂在 /zh/ 路径上——URL 与内容不自洽，
 *  且刷新时 URL 前缀优先级最高，会把用户刚选的语言顶回去。 */
export function useSetUiLangWithUrl() {
  const { setUiLang } = useUILang();
  return (l: UILang) => {
    setUiLang(l);
    if (typeof window === "undefined") return;
    history.replaceState(null, "", hrefForLang(l));
  };
}

/** 当前语言下的站内链接前缀助手（营销/公开页内链必须用它，SEO 爬 /de/ 树时不许漏回根路径） */
export function useLangHref() {
  const { uiLang } = useUILang();
  return (path: string) => langHref(path, uiLang);
}

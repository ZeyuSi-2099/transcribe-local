// 「一个网址一种语言」的护栏（2026-08-05 SEO 审计 A1）。
//
// 在此之前：`/pricing` 会按访客存储渲染成德语，而 canonical / hreflang / 预渲染 head
// 全写着英文 —— 同一个网址八份内容、都自称是那一个网址。
// 正解不是「在英文网址上不许说德语」，而是**先把人换到德语网址**再渲染。
import { describe, it, expect, beforeEach } from "vitest";
import { applyFirstVisitLangRedirect, __resetLangRedirect, hrefForLang, langHref, preferredLang } from "./i18n";

const go = (url: string) => history.replaceState(null, "", url);

beforeEach(() => {
  localStorage.clear();
  __resetLangRedirect();
  go("/");
});

describe("落地即对齐语言与网址", () => {
  it("存了德语的访客打开英文网址 → 换到德语网址，路径与查询串都留着", () => {
    localStorage.setItem("tx_lang", "de");
    go("/pricing?utm=x");
    expect(applyFirstVisitLangRedirect(false)).toBe(true);
    expect(location.pathname).toBe("/de/pricing");
    expect(location.search).toBe("?utm=x");
  });

  it("网址已经带前缀就不动 —— URL 说了算，存储不许把人从 /fr 拽到 /de", () => {
    localStorage.setItem("tx_lang", "de");
    go("/fr/pricing");
    expect(applyFirstVisitLangRedirect(false)).toBe(false);
    expect(location.pathname).toBe("/fr/pricing");
  });

  it("偏好就是英文时一步都不跳（爬虫与英文访客走的正是这条）", () => {
    localStorage.setItem("tx_lang", "en");
    go("/pricing");
    expect(applyFirstVisitLangRedirect(false)).toBe(false);
    expect(location.pathname).toBe("/pricing");
  });

  it("带着会话打开 `/` 与 `/signin` 不跳：那是去应用，应用没有语言 URL 树", () => {
    localStorage.setItem("tx_lang", "de");
    go("/");
    expect(applyFirstVisitLangRedirect(true)).toBe(false);
    expect(location.pathname).toBe("/");
    __resetLangRedirect();
    go("/signin");
    expect(applyFirstVisitLangRedirect(true)).toBe(false);
    expect(location.pathname).toBe("/signin");
  });

  // 「带着会话」这一条只对会被应用接管的路径成立。此前是全站短路，于是登录用户打开
  // `/pricing` 时既不换网址、又要在英文的预渲染骨架上渲染中文 → React 水合失败、
  // 印好的正文整棵丢掉重画，首屏先闪一遍英文（2026-08-22 巡检实见）。
  it("带着会话打开公开页照样跳：那渲染的是营销页，登录与否都一样", () => {
    localStorage.setItem("tx_lang", "de");
    go("/pricing");
    expect(applyFirstVisitLangRedirect(true)).toBe(true);
    expect(location.pathname).toBe("/de/pricing");
    for (const p of ["/terms", "/privacy", "/case", "/languages/ja"]) {
      __resetLangRedirect();
      go(p);
      expect(applyFirstVisitLangRedirect(true), p).toBe(true);
      expect(location.pathname).toBe(`/de${p}`);
    }
  });

  // 一次性结账链接：robots 禁收录、只靠查询串起作用，没有换网址的理由
  it("带着会话打开 /pay 不跳", () => {
    localStorage.setItem("tx_lang", "de");
    go("/pay?_ptxn=abc");
    expect(applyFirstVisitLangRedirect(true)).toBe(false);
    expect(location.pathname).toBe("/pay");
  });

  it("一次加载只跳一次，不会跟切语言互相拉扯", () => {
    localStorage.setItem("tx_lang", "de");
    expect(applyFirstVisitLangRedirect(false)).toBe(true);
    go("/");
    expect(applyFirstVisitLangRedirect(false)).toBe(false);
  });

  it("存了未放量的语言 → 当没存过，往下走浏览器语言（否则会跳到一个不存在的语言树）", () => {
    localStorage.setItem("tx_lang", "ko");
    expect(preferredLang()).not.toBe("ko");
  });
});

describe("语言菜单的 href", () => {
  it("指向同一页的另一门语言，英语回根路径", () => {
    go("/de/compare/rev");
    expect(hrefForLang("fr")).toBe("/fr/compare/rev");
    expect(hrefForLang("en")).toBe("/compare/rev");
  });

  it("首页在各门语言下就是纯前缀", () => {
    go("/");
    expect(langHref("/", "ja")).toBe("/ja");
    expect(langHref("/", "en")).toBe("/");
  });
});

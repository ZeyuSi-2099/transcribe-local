// 对照本的两道结构护栏（翻译质量另说，这里只守「对得上」和「翻得全」）。
//
// 营销站靠 TypeScript 保证八门键集合一致（en.tsx 是键的唯一定义处，其余 satisfies MsgCatalog）。
// 应用内的键不写在代码里、而是散在 431 个 L() 调用点的英文实参上，编译器管不着——这两条测试
// 就是拿运行时扫描补上编译器的那一课：
//   ① 对照本里出现源码中不存在的英文原句 → 红。多半是英文原文改了而译文没跟（那一句会静默回落英文）。
//   ② 已放量语言（LIVE_APP_LANGS）少翻任何一句 → 红。放量的语言必须翻满，半翻不许上线。
// 占位符那条守的是 L.t()/L.x() 的模板：译文少写或多写 {0}，页面上会漏字或漏出花括号。
import { describe, it, expect } from "vitest";
import { join } from "node:path";
import { extractAppKeys } from "./extractKeys";
import { APP_OVERRIDES, LIVE_APP_LANGS } from ".";
import { appDe } from "./app.de";
import { appFr } from "./app.fr";
import { appEs } from "./app.es";
import { appIt } from "./app.it";
import { appPt } from "./app.pt";
import { appJa } from "./app.ja";
import type { UILang } from "../i18n";

const SRC = join(__dirname, "..", "..");
const KEYS = extractAppKeys(SRC);

/** 手写的六本（不含从营销站派生的语种名——那些不是人在这里维护的） */
const HANDWRITTEN: [UILang, Record<string, string>][] = [
  ["de", appDe], ["fr", appFr], ["es", appEs], ["it", appIt], ["pt", appPt], ["ja", appJa],
];

const placeholders = (s: string) => [...s.matchAll(/\{(\d+)\}/g)].map((m) => m[1]).sort().join(",");

describe("应用内对照本", () => {
  it("抽取器扫到了源码，且认得四种调用形态（守卫本身没瞎跑）", () => {
    expect(KEYS.size).toBeGreaterThan(300);
    expect(KEYS.has("Upload audio — a transcript ready to use")).toBe(true);   // L("中","英")
    expect(KEYS.has("{0} left to confirm")).toBe(true);                         // L.t 模板
    expect(KEYS.has("We sent a 6-digit code to {0}.")).toBe(true);              // L.x 富文本
    expect(KEYS.has("Just now")).toBe(true);                                    // { zh, en } 数据对
    // 管理页是内部后台，不在范围内（Duner 定）
    expect([...KEYS.keys()].some((k) => k.includes("Auto top-up"))).toBe(false);
  });

  for (const [lang, book] of HANDWRITTEN) {
    it(`${lang}：对照本里的每一句都能在源码里找到`, () => {
      const stale = Object.keys(book).filter((k) => !KEYS.has(k));
      expect(
        stale,
        [
          `${lang} 的对照本有 ${stale.length} 句在源码里找不到：`,
          ...stale.slice(0, 8).map((s) => `  ${JSON.stringify(s)}`),
          "多半是英文原文改过而译文没跟——那一句现在会静默回落英文。",
          "先把英文原句抄准（含标点与空格），再改译文；别反过来改源码迁就对照本。",
          "看当前完整清单：npx vite-node scripts/app-i18n-todo.ts",
        ].join("\n"),
      ).toEqual([]);
    });

    it(`${lang}：译文的占位符与英文模板一一对应`, () => {
      const bad = Object.entries(book).filter(([k, v]) => placeholders(k) !== placeholders(v));
      expect(
        bad.map(([k, v]) => `${JSON.stringify(k)} → ${JSON.stringify(v)}`),
        "占位符对不上：译文里少一个 {0} 会漏掉变量，多一个会把花括号原样显示给用户",
      ).toEqual([]);
    });
  }

  for (const lang of LIVE_APP_LANGS.filter((l) => l !== "en" && l !== "zh")) {
    it(`${lang}：已放量语言必须把 ${KEYS.size} 句翻满`, () => {
      const book = APP_OVERRIDES[lang] ?? {};
      const missing = [...KEYS.keys()].filter((k) => book[k] === undefined);
      expect(
        missing.length,
        [
          `${lang} 已在 LIVE_APP_LANGS 里，却还缺 ${missing.length} 句：`,
          ...missing.slice(0, 8).map((s) => `  ${JSON.stringify(s)}`),
          "要么翻完，要么先把它从 LIVE_APP_LANGS 拿掉——半翻的语言不许出现在语言菜单里。",
          `还缺哪些：npx vite-node scripts/app-i18n-todo.ts ${lang}`,
        ].join("\n"),
      ).toBe(0);
    });
  }
});

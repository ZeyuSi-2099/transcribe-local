import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { palette } from "./tokens";

// 第5类 Part B（防漂移 · 测试守卫，替代 codegen）：
// tokens.css 是 tokens.ts 的手抄镜像，易漂移。本测试解析 tokens.css 的颜色变量，
// 逐一断言 === tokens.ts 的 palette 真值——任何一处手抄漂移立即测试红。
// 守备范围 = 颜色变量（漂移风险最高、hex 易比对）；shadow/font 因格式差异不做字面断言。

const css = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf8");
const vars: Record<string, string> = {};
for (const m of css.matchAll(/(--[\w-]+):\s*([^;]+);/g)) vars[m[1]] = m[2].trim();

// tokens.css 颜色变量 → palette 单一事实来源
const EXPECT: Record<string, string> = {
  "--c-bg": palette.sand100,
  "--c-paper": palette.sand50,
  "--c-panel": palette.sand200,
  "--c-line": palette.sand350,
  "--c-lineSoft": palette.sand400,
  "--c-line2": palette.sand300,
  "--c-ink": palette.ink900,
  "--c-inkMuted": palette.ink700,
  "--c-inkFaint": palette.ink500,
  "--c-inkGhost": palette.ink300,
  "--c-accent": palette.terra500,
  "--c-accentFill": palette.terra550,
  "--c-accentDeep": palette.terra700,
  "--c-accentSoft": palette.terra100,
  "--c-accentTint": palette.terra50,
  "--c-ok": palette.green500,
  "--c-warn": palette.gold600,
  "--c-borderStrong": palette.sand500,
  "--surface-raised": palette.sand25,
  "--surface-float": palette.white,
  "--surface-row-active": palette.sand75,
};

describe("tokens.css 与 tokens.ts 同步（防手抄漂移）", () => {
  for (const [name, expected] of Object.entries(EXPECT)) {
    it(`${name} 镜像 palette ${expected}`, () => {
      expect(vars[name]?.toLowerCase()).toBe(expected.toLowerCase());
    });
  }
});

// ── CJK 行高（2026-08-15）─────────────────────────────────────────────────
// 行高跟界面语言走，而 tokens.ts 是编译期常量拿不到语言，所以真值只在 CSS 里。
// 这一条守的不是数值好不好看，是**别只给中文加、把日韩落下**——那种漏法没有报错，
// 只会让某一门语言读起来比别人挤，而没有用户会为此提 bug。
describe("CJK 行高", () => {
  const cjkBlock = css.match(/:root:lang\(zh\)[^{]*\{([^}]*)\}/)?.[0] ?? "";

  it("zh / ja / ko 共用同一条规则，不是各写各的", () => {
    expect(cjkBlock).toContain(":lang(zh)");
    expect(cjkBlock, "日文没跟上").toContain(":lang(ja)");
    expect(cjkBlock, "韩文没跟上（现在还不是界面语言，但放量时没人会记得回来补）").toContain(":lang(ko)");
  });

  it("CJK 的行高必须比拉丁的大", () => {
    for (const k of ["--lh-body", "--lh-read"]) {
      const latin = parseFloat(css.match(new RegExp(`:root\\s*\\{[\\s\\S]*?${k}:\\s*([\\d.]+)`))?.[1] ?? "0");
      const cjk = parseFloat(cjkBlock.match(new RegExp(`${k}:\\s*([\\d.]+)`))?.[1] ?? "0");
      expect(latin, `${k} 拉丁值没定义`).toBeGreaterThan(1);
      expect(cjk, `${k} 的 CJK 值应大于拉丁值 ${latin}`).toBeGreaterThan(latin);
    }
  });
});

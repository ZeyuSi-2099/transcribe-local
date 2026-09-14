import { describe, it, expect } from "vitest";
import { semantic, fonts, FONT_STACKS } from "./tokens";

describe("tokens", () => {
  it("exposes the authoritative palette", () => {
    // Q2-A：断言从旧兼容层平移到语义层 semantic.*（值不变，只换引用对象）
    expect(semantic.accent.brand).toBe("#C8553D");
    expect(semantic.surface.page).toBe("#F2EDE3");
    expect(semantic.text.primary).toBe("#1F1A14");
    // 设计系统 v1 对比度修正后的值
    expect(semantic.text.muted).toBe("#75654F");
    expect(semantic.warning.icon).toBe("#A37A2A");
  });
  it("字体走 CSS 变量：真值按 html[lang] 分栈（tokens.css）", () => {
    expect(fonts.serif).toBe("var(--font-serif)");
    expect(fonts.mono).toBe("var(--font-mono)");
  });

  it("拉丁语言的衬线栈里 CJK 字体绝不打头", () => {
    // 打头会让英文标题用上中文字体的拉丁字形（审计 B1 的 Canvas 实测：493px vs 480px）
    expect(FONT_STACKS.latin.serif.startsWith("'Source Serif 4'")).toBe(true);
    expect(FONT_STACKS.latin.sans.startsWith("'Inter'")).toBe(true);
    // 中日文界面反过来：整句交给同一套 CJK 字体，混排才不会一句两种骨架
    expect(FONT_STACKS.zh.serif.startsWith("'Noto Serif SC'")).toBe(true);
    expect(FONT_STACKS.ja.serif.startsWith("'Noto Serif JP'")).toBe(true);
    // 日文必须有自己的字体——用简体中文字形凑汉字，日语读者一眼看得出
    expect(FONT_STACKS.ja.sans).toContain("Noto Sans JP");
  });
});

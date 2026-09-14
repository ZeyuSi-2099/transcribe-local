import { describe, it, expect } from "vitest";
import {
  parse,
  lineSegments,
  currentLineIndex,
  GLOSSARY_MAX_CHARS,
  MEANING_MAX_CHARS,
  NEAR_THRESHOLD,
} from "./glossary";

describe("glossary parse · 行类型", () => {
  it("空文本：无条目、零字数、无分类", () => {
    const r = parse("");
    expect(r.entryCount).toBe(0);
    expect(r.totalChars).toBe(0);
    expect(r.cats).toEqual([]);
    expect(r.overEntries).toEqual([]);
  });

  it("## 行识别为分类，label 去掉前导 # 与空格", () => {
    const r = parse("## 机构");
    expect(r.lines[0].kind).toBe("cat");
    expect(r.lines[0].label).toBe("机构");
  });

  it("含全角竖线的行识别为 entry，拆出 term 与 meaning", () => {
    const e = parse("慧辰 ｜ HCR 慧辰，市场研究服务商").lines[0];
    expect(e.kind).toBe("entry");
    expect(e.term).toBe("慧辰");
    expect(e.meaning).toBe("HCR 慧辰，市场研究服务商");
  });

  it("mlen = 含义码点数（两端空格被 trim）", () => {
    expect(parse("词 ｜ hello").lines[0].mlen).toBe(5);
    expect(parse("词 ｜  abc  ").lines[0].mlen).toBe(3);
  });

  it("空白行识别为 empty", () => {
    expect(parse("\n").lines.map((l) => l.kind)).toEqual(["empty", "empty"]);
  });

  it("非空、无竖线、非 ## 的行识别为 plain", () => {
    expect(parse("只有术语没有含义").lines[0].kind).toBe("plain");
  });
});

describe("glossary parse · 派生量", () => {
  it("entryCount 只数 entry（不含 plain / cat / empty）", () => {
    const r = parse("## 机构\n慧辰 ｜ 释义\n只有术语\n\n中邮 ｜ 释义");
    expect(r.entryCount).toBe(2);
  });

  it("totalChars = 全文去换行、保留空格（含分类行）", () => {
    expect(parse("ab｜cd").totalChars).toBe(5);
    expect(parse("ab\ncd").totalChars).toBe(4);
    expect(parse("a b\n").totalChars).toBe(3);
  });

  it("cats 列出每个分类的 label、行号与其下条目数", () => {
    const r = parse("## 机构\n慧辰 ｜ 释义\n中邮 ｜ 释义\n## 产品\nPura ｜ 释义");
    expect(r.cats).toHaveLength(2);
    expect(r.cats[0]).toMatchObject({ label: "机构", lineIndex: 0, count: 2 });
    expect(r.cats[1]).toMatchObject({ label: "产品", lineIndex: 3, count: 1 });
  });

  it("charOffset 指向该行在全文中的起始索引（供光标定位）", () => {
    const r = parse("## A\nX ｜ y");
    expect(r.lines[1].charOffset).toBe(5);
  });
});

describe("glossary parse · 校验阈值", () => {
  it("单条含义超 150 字：标 over 并进 overEntries", () => {
    const r = parse(`词 ｜ ${"x".repeat(MEANING_MAX_CHARS + 1)}`);
    expect(r.lines[0].over).toBe(true);
    expect(r.overEntries).toHaveLength(1);
    expect(r.overEntries[0]).toMatchObject({ term: "词", mlen: 151, lineNo: 1 });
  });

  it("含义正好 150 字：不算超", () => {
    const r = parse(`词 ｜ ${"x".repeat(MEANING_MAX_CHARS)}`);
    expect(r.lines[0].over).toBe(false);
    expect(r.overEntries).toEqual([]);
  });

  it("总字数 > 8000：标 overTotal", () => {
    expect(parse("x".repeat(GLOSSARY_MAX_CHARS + 1)).overTotal).toBe(true);
  });

  it("总字数 7200–8000：标 near 但不 overTotal", () => {
    const r = parse("x".repeat(NEAR_THRESHOLD));
    expect(r.near).toBe(true);
    expect(r.overTotal).toBe(false);
  });

  it("总字数 < 7200：既不 near 也不 overTotal", () => {
    const r = parse("x".repeat(100));
    expect(r.near).toBe(false);
    expect(r.overTotal).toBe(false);
  });

  it("常量取值符合方案约定", () => {
    expect(GLOSSARY_MAX_CHARS).toBe(8000);
    expect(MEANING_MAX_CHARS).toBe(150);
    expect(NEAR_THRESHOLD).toBe(7200);
  });
});

describe("glossary · currentLineIndex（光标所在行，0-based）", () => {
  it("光标在第一行 → 0", () => {
    expect(currentLineIndex("abc\ndef", 1)).toBe(0);
  });
  it("光标在第二行 → 1", () => {
    expect(currentLineIndex("abc\ndef", 5)).toBe(1);
  });
  it("光标紧跟换行符后 → 进入下一行", () => {
    expect(currentLineIndex("a\nb", 2)).toBe(1);
  });
  it("空文本 → 0", () => {
    expect(currentLineIndex("", 0)).toBe(0);
  });
});

describe("glossary · lineSegments 着色分段（保留原始空格）", () => {
  it("分类行整行一段 cat", () => {
    expect(lineSegments(parse("## 机构").lines[0])).toEqual([{ text: "## 机构", role: "cat" }]);
  });
  it("术语行拆 term / pipe / meaning，竖线两侧原始空格保留", () => {
    expect(lineSegments(parse("FD ｜ 履约分销").lines[0])).toEqual([
      { text: "FD ", role: "term" },
      { text: "｜", role: "pipe" },
      { text: " 履约分销", role: "meaning" },
    ]);
  });
  it("含义超限的术语行 meaning 段标 meaning-over", () => {
    const seg = lineSegments(parse(`词 ｜ ${"x".repeat(MEANING_MAX_CHARS + 1)}`).lines[0]);
    expect(seg[2].role).toBe("meaning-over");
  });
  it("空行渲染零宽占位以保持行高", () => {
    expect(lineSegments(parse("").lines[0])).toEqual([{ text: "​", role: "empty" }]);
  });
  it("plain 行整行一段 plain", () => {
    expect(lineSegments(parse("只有术语").lines[0])).toEqual([{ text: "只有术语", role: "plain" }]);
  });
});

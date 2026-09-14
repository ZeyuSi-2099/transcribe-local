// 大纲上限的前后端一致性（2026-08-19）。
//
// 两个数字在两边各写了一份：前端要**先拦**（超了后端直接 422，等到点「开始起草」
// 才报错，用户已经等了一步），后端要**兜底**（前端可以绕过）。两份就会漂，漂了不报错——
// 症状是「传进去看着好好的，一点起草就失败」。所以逐个比对。
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MAX_OUTLINE_BYTES, MAX_OUTLINE_CHARS } from "./outlineFile";

const ROOT = process.cwd();
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

/** 从 python 源码里取一个模块级常量的字面量值（只认整数与简单乘法） */
function pyConst(src: string, name: string): number {
  const m = src.match(new RegExp(`^${name}\\s*=\\s*([0-9*\\s]+)`, "m"));
  if (!m) throw new Error(`后端找不到常量 ${name}`);
  return m[1].split("*").reduce((a, b) => a * Number(b.trim()), 1);
}

describe("大纲上限：前端与后端同值", () => {
  it("字数上限 = glossary_assist.MAX_OUTLINE_CHARS", () => {
    const back = pyConst(read("../backend/app/glossary_assist.py"), "MAX_OUTLINE_CHARS");
    expect(MAX_OUTLINE_CHARS).toBe(back);
  });

  it("文件体积上限 = outline_file.MAX_BYTES", () => {
    const back = pyConst(read("../backend/app/outline_file.py"), "MAX_BYTES");
    expect(MAX_OUTLINE_BYTES).toBe(back);
  });
});

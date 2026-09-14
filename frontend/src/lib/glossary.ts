// 术语库解析与校验。纯逻辑、无副作用，前端编辑器与整页共用；后端按同口径校验。
// 格式（沿用现有 Config_Term.md，零迁移）：
//   ## 分类            ← 以 ## 开头 = 分类标题
//   术语 ｜ 含义        ← 全角竖线 U+FF5C 分隔，一行一条
// totalChars 口径 = 全文除换行符外的字符数（保留空格）；前后端必须一致。

export const GLOSSARY_MAX_CHARS = 8000; // 总字数上限
export const MEANING_MAX_CHARS = 150; // 单条含义上限
export const NEAR_THRESHOLD = 7200; // 接近上限阈值（提醒，不禁用；= 上限 90%）

export type LineKind = "cat" | "entry" | "empty" | "plain";

export interface GlossaryLine {
  kind: LineKind;
  raw: string; // 原始行文本（着色渲染用，不可 trim）
  lineIndex: number; // 0-based 行号
  charOffset: number; // 该行首字符在全文中的索引（光标定位用）
  label?: string; // cat：去掉前导 # 与空格后的标题
  term?: string; // entry：竖线前（trim）
  meaning?: string; // entry：竖线后（trim）
  mlen?: number; // entry：含义码点数
  over?: boolean; // entry：mlen > MEANING_MAX_CHARS
}

export interface GlossaryCat {
  label: string;
  lineIndex: number;
  charOffset: number;
  count: number; // 该分类下的条目数
}

export interface OverEntry {
  lineNo: number; // 1-based（展示用）
  term: string;
  mlen: number;
  lineIndex: number; // 0-based
  charOffset: number;
}

/** 只有术语名、释义还空着的条目 —— 待办层的一半（另一半是缺口）。
 *  它是合法的一行，会存进库；但**注入转录引擎前会被摘掉**（后端 strip_unfinished）。 */
export interface BlankEntry {
  lineNo: number; // 1-based（展示用，面板里显示成 L5）
  term: string;
  lineIndex: number; // 0-based
  charOffset: number; // 「跳过去 →」用：光标落到该行竖线之后
}

export interface ParsedGlossary {
  lines: GlossaryLine[];
  entryCount: number;
  totalChars: number;
  cats: GlossaryCat[];
  overEntries: OverEntry[];
  blankEntries: BlankEntry[];
  overTotal: boolean; // totalChars > GLOSSARY_MAX_CHARS（保存禁用）
  near: boolean; // NEAR_THRESHOLD ≤ totalChars ≤ GLOSSARY_MAX_CHARS
}

const PIPE = "｜"; // U+FF5C 全角竖线（非半角 |）

export function parse(text: string): ParsedGlossary {
  const lines: GlossaryLine[] = [];
  const cats: GlossaryCat[] = [];
  const overEntries: OverEntry[] = [];
  const blankEntries: BlankEntry[] = [];
  let entryCount = 0;
  let charOffset = 0;

  text.split("\n").forEach((raw, lineIndex) => {
    const trimmed = raw.trim();
    let line: GlossaryLine;

    if (trimmed === "") {
      line = { kind: "empty", raw, lineIndex, charOffset };
    } else if (trimmed.startsWith("##")) {
      const label = trimmed.replace(/^#+/, "").trim();
      line = { kind: "cat", raw, lineIndex, charOffset, label };
      cats.push({ label, lineIndex, charOffset, count: 0 });
    } else if (raw.includes(PIPE)) {
      const i = raw.indexOf(PIPE);
      const term = raw.slice(0, i).trim();
      const meaning = raw.slice(i + 1).trim();
      const mlen = [...meaning].length;
      const over = mlen > MEANING_MAX_CHARS;
      line = { kind: "entry", raw, lineIndex, charOffset, term, meaning, mlen, over };
      entryCount += 1;
      if (cats.length) cats[cats.length - 1].count += 1;
      if (over) overEntries.push({ lineNo: lineIndex + 1, term, mlen, lineIndex, charOffset });
      // 竖线右侧空着 = 等用户填。光标落点定在竖线之后，用户直接接着打字
      if (!meaning) blankEntries.push({ lineNo: lineIndex + 1, term, lineIndex, charOffset: charOffset + i + PIPE.length });
    } else {
      line = { kind: "plain", raw, lineIndex, charOffset };
    }

    lines.push(line);
    charOffset += raw.length + 1; // +1 为被 split 掉的换行符
  });

  const totalChars = [...text].filter((c) => c !== "\n").length;
  const overTotal = totalChars > GLOSSARY_MAX_CHARS;
  const near = totalChars >= NEAR_THRESHOLD && totalChars <= GLOSSARY_MAX_CHARS;

  return { lines, entryCount, totalChars, cats, overEntries, blankEntries, overTotal, near };
}

// ── 编辑器渲染辅助（纯逻辑，供 GlossaryEditor 的高亮叠层用）──

export type SegRole = "cat" | "term" | "pipe" | "meaning" | "meaning-over" | "plain" | "empty";
export interface Seg {
  text: string;
  role: SegRole;
}

/** 把一行拆成着色段。务必保留原始空格（不 trim），否则与 textarea 错位。 */
export function lineSegments(line: GlossaryLine): Seg[] {
  if (line.kind === "cat") return [{ text: line.raw, role: "cat" }];
  if (line.kind === "plain") return [{ text: line.raw, role: "plain" }];
  if (line.kind === "empty") return [{ text: "​", role: "empty" }]; // 零宽占位保行高
  const i = line.raw.indexOf(PIPE);
  return [
    { text: line.raw.slice(0, i), role: "term" },
    { text: PIPE, role: "pipe" },
    { text: line.raw.slice(i + 1), role: line.over ? "meaning-over" : "meaning" },
  ];
}

/** 光标所在的行号（0-based）。供当前行高亮用。 */
export function currentLineIndex(value: string, caret: number): number {
  return value.slice(0, caret).split("\n").length - 1;
}

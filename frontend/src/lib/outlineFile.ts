// 访谈大纲的文件读取（2026-08-19）。
//
// 两条路径，分工是有意的：
// · `.txt` / `.md` —— **前端直接读，不上传**。大纲里常有还没公开的公司名与受访者姓名，
//   能不过网就不过网；顺带省一次往返。
// · `.docx` —— 必须解析，走后端 `/api/glossaries/outline`（那边用导出 docx 时就装好的
//   python-docx，零新依赖）。
//
// 读出来的文字**一律交回界面显示在可编辑的框里**，不直接送去起草：docx 解析出的东西
// 质量参差（表格被拉平、页眉页脚可能混进来），让人扫一眼比默默塞给模型稳。
import { readOutlineDocx } from "./api";

/** 上限与后端 `glossary_assist.MAX_OUTLINE_CHARS` 同值；
 *  超出后端直接 422，所以前端必须先拦（`outlineFile.test.ts` 比对两边防漂移）。 */
export const MAX_OUTLINE_CHARS = 20000;

/** 与后端 `outline_file.MAX_BYTES` 同值 */
export const MAX_OUTLINE_BYTES = 2 * 1024 * 1024;

/** 文件选择框的 accept；`.markdown` 一并收下，那是 `.md` 的长写法 */
export const OUTLINE_ACCEPT = ".txt,.md,.markdown,.docx";

/** 出错原因用 code 而不是句子：文案要按界面语言出，句子写在这里就没法翻。 */
export type OutlineErrorCode = "ext" | "size" | "empty" | "read";

export class OutlineError extends Error {
  constructor(public code: OutlineErrorCode, message?: string) {
    super(message ?? code);
    this.name = "OutlineError";
  }
}

export interface OutlineResult {
  text: string;
  /** 超过上限被截断（界面要明说，不能默默切掉一半） */
  truncated: boolean;
  /** 字数：截断后的实际值 */
  chars: number;
}

const PLAIN_EXTS = [".txt", ".md", ".markdown"];

function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

/** 读一份大纲文件。失败抛 `OutlineError`，调用方按 code 出文案。 */
export async function readOutlineFile(file: File): Promise<OutlineResult> {
  const ext = extOf(file.name);
  if (ext !== ".docx" && !PLAIN_EXTS.includes(ext)) throw new OutlineError("ext");
  if (file.size > MAX_OUTLINE_BYTES) throw new OutlineError("size");

  let raw: string;
  if (ext === ".docx") {
    raw = (await readOutlineDocx(file)).text;      // 后端已按同一上限截过一次
  } else {
    try {
      raw = await file.text();
    } catch {
      throw new OutlineError("read");
    }
  }

  const text = raw.replace(/\r\n?/g, "\n").trim();
  if (!text) throw new OutlineError("empty");

  // 截断而不是拒绝：大纲前半通常最相关，直接拒了用户还得自己去裁
  const truncated = text.length > MAX_OUTLINE_CHARS;
  const out = truncated ? text.slice(0, MAX_OUTLINE_CHARS) : text;
  return { text: out, truncated, chars: out.length };
}

/** 从拖放事件里取第一个文件（拖一堆进来只认第一个，多选没有意义——起草只用一份材料） */
export function firstFileOf(dt: DataTransfer | null): File | null {
  if (!dt) return null;
  if (dt.files && dt.files.length > 0) return dt.files[0];
  return null;
}

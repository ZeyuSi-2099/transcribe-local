// 从右往左语种（阿拉伯语等）的方向判定。
//
// ⚠️ **后端有一份同口径的实现**（`server/app/bidi.py`），两边都要改。
//
// 说话人标签**不在这里**——它在 `src/lib/speakerLabels.ts`（2026-08-30 搬走）。
// 两件事凑在一个文件里只是因为它们最初一起做，实际毫无关系：方向由**录音语种**决定，
// 标签由**界面语言**决定。放一起会让人以为标签也该跟录音走。

// 27 门里只有 ar 是 RTL；he/fa/ur 先列上，将来加语种不用回头改这里。
const RTL_LANGS = new Set(["ar", "he", "fa", "ur"]);

/** 录音语种是不是从右往左。`ar-EG` 这种带地区码的取前缀。 */
export function isRtlLang(lang?: string | null): boolean {
  return RTL_LANGS.has((lang ?? "").split("-")[0].trim().toLowerCase());
}

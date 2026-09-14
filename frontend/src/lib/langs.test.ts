import { describe, it, expect } from "vitest";
import { COMMON_LANGS, MORE_LANGS, langName } from "./langs";

const Lzh = <T,>(zh: T, _en: T) => zh;
const Len = <T,>(_zh: T, en: T) => en;

describe("langs", () => {
  // 台面上 11 门 + 「更多」= 12 格 = 三列整四行（多一格就多一整行，见 LangPicker 的高度注释）。
  // 顺序按行写死：这是 Duner 2026-08-21 定的摆法，**不是按使用人数排的**，
  // 看着乱不是 bug——所以这里逐行钉住，谁「顺手排整齐」都会红。
  it("台面上 11 门，按定好的四行摆放", () => {
    const rows = [COMMON_LANGS.map((l) => l.id), ["更多"]].flat();
    expect([rows.slice(0, 3), rows.slice(3, 6), rows.slice(6, 9), rows.slice(9, 12)]).toEqual([
      ["en", "zh", "es"],
      ["fr", "ar", "pt"],
      ["de", "ru", "id"],
      ["ja", "it", "更多"],
    ]);
    expect(COMMON_LANGS[0].id).toBe("en");   // 上传页默认选中项 + langName 兜底
  });
  it("more dropdown includes Tier2/3 languages", () => {
    expect(MORE_LANGS.map((l) => l.id)).toContain("vi");
    expect(MORE_LANGS.map((l) => l.id)).toContain("ko");
  });
  it("27 门一门不少，且两组不重叠", () => {
    const ids = [...COMMON_LANGS, ...MORE_LANGS].map((l) => l.id);
    expect(ids.length).toBe(27);
    expect(new Set(ids).size).toBe(27);
  });
  it("langName resolves across all groups in current UI language", () => {
    expect(langName("zh", Lzh)).toBe("中文");
    expect(langName("en", Len)).toBe("English");
    expect(langName("es", Len)).toBe("Spanish");
    expect(langName("unknown", Lzh)).toBe("英语"); // 兜底＝COMMON_LANGS[0]，已随默认语言改为英语
  });
  // 六门译名派生自营销站的 langNames*（给句子用，法/西/意/葡是小写），而「English」在对照本里
  // 另有一条大写的（它同时是价档名）——不统一抬首字母的话，选择器里会出现
  // 「Anglais｜chinois｜espagnol」这样一行大小写打架。
  it("语种名统一首字母大写（法/西/意/葡的小写译名会被抬起来）", () => {
    const Lfr = <T,>(_zh: T, en: T) => (({ Chinese: "chinois", Spanish: "espagnol" } as Record<string, unknown>)[en as string] ?? en) as T;
    expect(langName("zh", Lfr)).toBe("Chinois");
    expect(langName("es", Lfr)).toBe("Espagnol");
    expect(langName("zh", Lzh)).toBe("中文");        // CJK 不受影响
  });
});

// 从源码里抽出「应用内需要翻译的英文原句」。**只在测试与命令行脚本里用**（依赖 node:fs，
// 不会进浏览器产物）。守卫测试与生成清单的脚本共用这一份，避免两处规则漂移。
//
// 抽的是四种形态：
//   L("中", "英")                     · 最常见
//   L.t("中 {0}", "en {0}", x)        · 带变量的纯文本
//   L.x("中 {0}", "en {0}", node)     · 带变量的富文本
//   L.t("中", cond ? "A" : "B", x)    · 英文分单复数（两条都算）
// 外加两种「文案存在数据结构里、由 L 转发」的形态：
//   { zh: "中", en: "英" }            · 档位名、录音类型等
//   "中文话术": "English phrasing"    · 后端话术的英文映射表（HistoryPage 的 ERROR_EN）
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/** 扫哪些文件：应用内屏幕与组件 + 两个供它们取文案的数据文件。 */
export function appSourceFiles(srcRoot: string): string[] {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) {
        // 营销/公开页走自己的文案目录（screens/marketing/i18n、screens/legal），不归对照本管
        if (name === "marketing" || name === "legal") return [];
        // 管理页拆成多文件后，`screens/admin/` 整个目录也是它的一部分（2026-08-14 Tag 重构）
        if (name === "admin") return [];
        return walk(p);
      }
      if (!/\.tsx?$/.test(name) || /\.test\.tsx?$/.test(name)) return [];
      // 管理页是只有站长能进的内部后台，不翻（Duner 定，见 docs/app-i18n-STATE.md）
      if (name === "AdminPage.tsx") return [];
      return [p];
    });
  return [
    ...walk(join(srcRoot, "screens")),
    ...walk(join(srcRoot, "components")),
    // ⚠️ lib/pricing.ts 2026-08-31 从本清单移除：定价 V3 塌成单档后它不再有 TIERS
    // 那张 { zh, en } 档位名表，整个文件一句应用内文案都没有了。留着扫一个空文件，
    // 只会让人以为价目表里还藏着待翻的字。
    // 后端报错的文案（按错误码出，2026-08-30 起）。**必须扫**：它是真·应用内文案，
    // 漏了这一行的症状是「对照本里有这几句、源码里却找不到」——守卫会指着译文喊，
    // 而错的其实是这张扫描清单。
    join(srcRoot, "lib", "userErrors.ts"),
    join(srcRoot, "AppShell.tsx"),        // 实时行的「刚刚 / Just now」
  ];
}

const RE_CALL = /\bL(?:\.[tx])?\(\s*(["'])(?:\\.|(?!\1)[^\\])*\1\s*,\s*/g;
const RE_STR = /^(["'])((?:\\.|(?!\1)[^\\])*)\1/;
const RE_TERNARY = /^[^?"'\n]*\?\s*(["'])((?:\\.|(?!\1)[^\\])*)\1\s*:\s*(["'])((?:\\.|(?!\3)[^\\])*)\3/;
const RE_PAIR = /\bzh:\s*(["'])(?:\\.|(?!\1)[^\\])*\1\s*,\s*en:\s*(["'])((?:\\.|(?!\2)[^\\])*)\2/g;
// 中文键 → 英文值（ERROR_EN 那种映射表）
const RE_ZH_KEY_MAP = /"[^"]*[\u4e00-\u9fa5][^"]*"\s*:\s*"([^"]+)"/g;

const unescape = (s: string) => s.replace(/\\(["'\\])/g, "$1").replace(/\\n/g, "\n");

/** 返回「英文原句 → 出现位置」。位置用于守卫报错时指路。 */
export function extractAppKeys(srcRoot: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  const add = (en: string, at: string) => {
    if (!en.trim()) return;
    const list = out.get(en) ?? [];
    list.push(at);
    out.set(en, list);
  };

  for (const f of appSourceFiles(srcRoot)) {
    const src = readFileSync(f, "utf-8");
    const rel = f.slice(srcRoot.length + 1);
    const lineAt = (i: number) => `${rel}:${src.slice(0, i).split("\n").length}`;

    RE_CALL.lastIndex = 0;
    for (let m = RE_CALL.exec(src); m; m = RE_CALL.exec(src)) {
      const rest = src.slice(RE_CALL.lastIndex);
      const str = RE_STR.exec(rest);
      if (str) { add(unescape(str[2]), lineAt(m.index)); continue; }
      // 英文侧是三元（单复数分支）：两条都要翻
      const tern = RE_TERNARY.exec(rest);
      if (tern) { add(unescape(tern[2]), lineAt(m.index)); add(unescape(tern[4]), lineAt(m.index)); }
      // 其余（L(zh, en) 转发变量、L(item.d.zh, item.d.en)）：文案在数据结构里，由下面两条规则捞
    }

    // 数据里的成对文案按**行**扫，不按整份文件扫：`[^"]*` 跨行会从注释里的一个中文串一路吃到
    // 几十行后的某个冒号，把源码本身当成文案抓进来（2026-08-02 抓出过一整段 mmss 函数体）。
    src.split("\n").forEach((line, i) => {
      const t = line.trim();
      if (t.startsWith("//") || t.startsWith("*")) return;   // 注释里的中文不是文案
      const at = `${rel}:${i + 1}`;
      RE_PAIR.lastIndex = 0;
      for (let m = RE_PAIR.exec(line); m; m = RE_PAIR.exec(line)) add(unescape(m[3]), at);
      RE_ZH_KEY_MAP.lastIndex = 0;
      for (let m = RE_ZH_KEY_MAP.exec(line); m; m = RE_ZH_KEY_MAP.exec(line)) add(unescape(m[1]), at);
    });
  }
  return out;
}

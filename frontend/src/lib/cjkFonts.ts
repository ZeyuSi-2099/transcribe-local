// CJK 字体按需加载。
//
// 为什么不写死在 index.html：那份 <link> 是所有访客都要拉的，而 Noto Sans/Serif SC
// 是站上最大的两份字体资源。英文访客一个汉字都不会看到，没有理由替他们下载
// （审计 B1：「只在需要的语言加载对应 CJK 字体」）。
//
// 反过来，日文原先**一份日文字体都没加载**，汉字是拿简体中文字形凑的——
// 对日语读者来说那是能一眼看出来的错字形。所以 JP 是这里新增的，不是省下来的。
type Cjk = "sc" | "jp";

const HREF: Record<Cjk, string> = {
  sc: "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@400;500;700;900&display=swap",
  jp: "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&family=Noto+Serif+JP:wght@400;500;700;900&display=swap",
};

const done = new Set<Cjk>();

/** 幂等：同一种只会插一次 <link>。SSR / 预渲染（无 document）下静默跳过。 */
export function ensureCjkFont(kind: Cjk): void {
  if (done.has(kind) || typeof document === "undefined") return;
  done.add(kind);
  const el = document.createElement("link");
  el.rel = "stylesheet";
  el.href = HREF[kind];
  el.setAttribute("data-cjk", kind);
  document.head.appendChild(el);
}

/** 语言代码 → 需要哪套 CJK 字体（27 门语种页的示例稿也用它：zh 页要 SC、ja 页要 JP） */
export function cjkFor(lang: string): Cjk | null {
  if (lang === "zh" || lang.startsWith("zh-")) return "sc";
  if (lang === "ja" || lang.startsWith("ja-")) return "jp";
  return null;
}

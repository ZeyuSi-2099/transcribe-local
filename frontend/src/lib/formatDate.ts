// 面向读者的日期一律按语言排版（审计 C6）。
//
// 之前各门语言都直接输出 ISO 串（法语页上写着「relevés au 2026-08」、条款页
// 「Dernière mise à jour · 2026-08-03」）。ISO 是给机器看的格式，不是给读者的。
//
// 只做「年 + 月」和「年 + 月 + 日」两种，因为站上只有这两处用得着：
// 行价调研时点（YYYY-MM）与条款更新日（YYYY-MM-DD）。
import { HTML_LANG, type UILang } from "./i18n";

/** "2026-08" → 各语言的「2026 年 8 月」；解析不出就原样返回（宁可显示 ISO 也别显示 Invalid Date） */
export function formatMonth(iso: string, lang: UILang): string {
  const m = /^(\d{4})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return fmt(new Date(Date.UTC(+m[1], +m[2] - 1, 1)), lang, { year: "numeric", month: "long" }) ?? iso;
}

/** "2026-08-03" → 各语言的长日期 */
export function formatDay(iso: string, lang: UILang): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return fmt(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])), lang, { year: "numeric", month: "long", day: "numeric" }) ?? iso;
}

function fmt(d: Date, lang: UILang, opts: Intl.DateTimeFormatOptions): string | null {
  try {
    // UTC 构造 + UTC 格式化：不加 timeZone 的话，UTC-x 的读者会看到前一天
    return new Intl.DateTimeFormat(HTML_LANG[lang], { ...opts, timeZone: "UTC" }).format(d);
  } catch {
    return null;
  }
}

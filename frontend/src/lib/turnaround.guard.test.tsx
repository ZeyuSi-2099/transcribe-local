// 出稿时长不许写死在文案里（2026-08-31 生产实测后新增）。
//
// 起因见 `turnaround.ts` 文件头：这个数字手打在 63 个地方，上线时写的「1 小时约 10 分钟」
// 在生产 17 个长单里**一单都没达到过**（最快 18 分钟、中位 22 分钟）。流水线这两个月改了
// 很多次，而改的人是工程师——没有任何东西会提醒他去翻八门语言的营销文案。
//
// ⚠️ **判据必须落在「渲染之后的文本」上，不能扫源码**：这些数字有一半是
// `{mono(10)}` 这种包在组件里的写法，源码里 grep `10 分钟` 一条都抓不到，
// 而页面上白纸黑字写着「10 分钟」。清点这次改动时就是这么被骗过去一轮的。

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { CATALOGS, LANDING_DATA } from "../screens/marketing/i18n/catalog";
import { TURNAROUND_MIN_PER_HOUR } from "./turnaround";
import type { UILang } from "./i18n";

const langs = Object.keys(CATALOGS) as UILang[];

/** 哨兵：一个绝不会出现在任何译文里的串。判据是「它出现了没有」——
 *  不需要知道当前承诺的是几分钟，也不需要任何语言的词表，所以改这个数不用动守卫。 */
const SENTINEL = "⑬ETA";

const P = {
  eta: SENTINEL,
  rate: "①R", per: "②P", narrate: "③N", redact: "④D",
  audioDays: "7", transcriptDays: "30", name: "⑤L",
} as Record<string, string>;

const asText = (v: unknown): string => {
  const out = typeof v === "function" ? (v as (p: unknown) => unknown)(P) : v;
  if (out == null) return "";
  if (typeof out === "string" || typeof out === "number") return String(out);
  try { return renderToStaticMarkup(out as ReactNode).replace(/<[^>]*>/g, ""); }
  catch { return ""; }
};

/** 回答「多快出稿」的那三处：首页可见 FAQ、首页结构化 FAQ、语种页 FAQ。
 *  作用域故意窄——只有这几处是**出稿时长的承诺**，别的地方出现分钟数是正当的
 *  （「12 分钟的文件就按 12 分钟算」是计费示例，「音频 7 天后删除」是留存期）。 */
function speedAnswers(lang: UILang): [string, string][] {
  const cat = CATALOGS[lang] as Record<string, unknown>;
  const out: [string, string][] = [
    ["landing faq.a2", asText(cat["faq.a2"])],
    ["languages lang.faqSpeedA", asText(cat["lang.faqSpeedA"])],
    // ⚠️ 定价页不在营销目录里，是另一本 legal 目录——2026-08-31 就是漏在这儿：
    // 营销那 63 处全改对了，定价页那句「约 10 分钟」原样留在生产上，
    // 而守卫全绿。**作用域比判据更容易出错**：判据错了会红，作用域漏了什么都不会发生。
    // ⚠️ 2026-09-01：定价页**整页不再谈出稿时长**（Duner 定）。那一页的「常见问题」区
    // 总共只有这一条、问的还是「多快出稿」，与这一页要讲的价格和计费规则无关，
    // 于是标题＋问＋答整块删掉（八门连键一起删）。所以这里已经没有 `pg.faqA1` 可扫，
    // 改由第④组的**反向判据**盯着：定价页不许再有任何键谈它。
    // 举例小节仍留在扫描里——它是最可能被人顺手把那句塞回去的地方。
    ["pricing pg.exNote", asText(cat["pg.exNote"])],
  ];
  const plain = LANDING_DATA[lang]?.faqPlain(P as never) ?? [];
  plain.forEach((x, i) => {
    if (x.a.includes(SENTINEL) || /\d/.test(x.a)) out.push([`faqPlain[${i}]`, x.a]);
  });
  return out;
}

describe("① 出稿时长必须是注入的", () => {
  for (const lang of langs) {
    it(`${lang}：三处「多快出稿」的答案都用注入值`, () => {
      const cat = CATALOGS[lang] as Record<string, unknown>;
      for (const key of ["faq.a2", "lang.faqSpeedA"]) {
        expect(asText(cat[key]), `${lang} 的 ${key} 里没有注入的出稿时长——写死成常量了？`)
          .toContain(SENTINEL);
      }
      // 红线（2026-09-01 收紧）：定价页整页都不谈出稿时长。这里先钉「举例小节里不许有哨兵」，
      // 免得有人把整句原样复制回去（复制过去仍是注入值，第②条的数字判据抓不到）。
      expect(asText(cat["pg.exNote"]),
        `${lang} 的举例小节又谈起出稿时长了——定价页整页都不该谈`).not.toContain(SENTINEL);
      const plain = LANDING_DATA[lang]?.faqPlain(P as never) ?? [];
      expect(plain.filter((x) => x.a.includes(SENTINEL)),
        `${lang} 的结构化 FAQ 里应恰好一条谈出稿时长且用注入值`).toHaveLength(1);
    });
  }

  it("哨兵确实到得了页面——否则上面每一条都是空转的假绿", () => {
    const hit = langs.filter((l) => speedAnswers(l).some(([, t]) => t.includes(SENTINEL)));
    expect(hit.sort()).toEqual([...langs].sort());
  });
});

describe("② 谈出稿时长的句子里不许再有第二个手打的时间数字", () => {
  // 「注入值在场」不等于「没有写死的」：一句话里同时出现 `{eta}` 和一个手打的
  // 「10 分钟」照样过第①条。这里管的是后半句。
  //
  // ⚠️ **只查分钟单位，不查小时**：这几句话里的小时数是**输入侧**（「1 小时录音…」），
  // 那不是承诺、也不该被注入。出稿时长一律用分钟表达，输入侧一律用小时表达——
  // 单位本身就是判据。若哪天出稿时长改用小时表达，这条守卫要跟着改。
  const TIME_UNIT = /(\d+)\s*(分钟|分(?![钟])|minutes?|minuti|minutos|Minuten|min\b|Min\.)/gi;

  for (const lang of langs) {
    it(`${lang}：没有夹带写死的时长`, () => {
      const bad: string[] = [];
      for (const [where, text] of speedAnswers(lang)) {
        const clean = text.split(SENTINEL).join("〖注入〗");
        for (const m of clean.matchAll(TIME_UNIT)) bad.push(`${where}: 「${m[0]}」`);
      }
      expect(bad, `${lang} 的出稿时长答案里有写死的时间（应改成注入 {p.eta}）：\n  ${bad.join("\n  ")}`)
        .toEqual([]);
    });
  }
});

describe("③ 首页那一行与名片不再把速度当卖点", () => {
  // 2026-08-31 的决定：速度不是我们的卖点，也是我们最守不住的一维——
  // 并发一高尾巴就翻倍。把它从 hero 一行与搜索名片里撤掉，只在 FAQ 里如实答。
  // 谁加回去，这条会红。
  // 同②：只查分钟单位。这四处已经完全不谈出稿时长了，谁把它加回来就会红。
  const TIME_CLAIM = /(\d+)\s*(分钟|分(?![钟])|minutes?|minuti|minutos|Minuten|min\b|Min\.)/i;

  for (const lang of langs) {
    it(`${lang}：hero 一行 / 名片 / 语种页那一行里没有出稿时长`, () => {
      const cat = CATALOGS[lang] as Record<string, unknown>;
      for (const key of ["hero.stats", "meta.desc", "lang.stats", "lang.meta.desc"]) {
        const t = asText(cat[key]);
        expect(TIME_CLAIM.test(t), `${lang} 的 ${key} 又出现了时长宣称：${t}`).toBe(false);
      }
    });
  }
});

describe("④ 承诺的那个数要站得住", () => {
  // 生产 73 单实测（录音 ≥25 分钟 n=17）：出稿÷录音 中位 0.37、最慢 0.62。
  // 这条不是在测代码，是把**当初凭什么定这个数**钉在仓库里——
  // 下次有人想把它调小，得先解释怎么突破这个实测区间。
  it("不低于实测中位（22 分钟），也不高到失去意义", () => {
    expect(TURNAROUND_MIN_PER_HOUR).toBeGreaterThanOrEqual(22);
    expect(TURNAROUND_MIN_PER_HOUR).toBeLessThanOrEqual(60);
  });
});

describe("④ 定价页整页不谈出稿时长", () => {
  // 这一组 2026-09-01 **由「两处逐字相同」翻成了反向判据**，因为那两处只剩一处了。
  //
  // 前一版守的是：首页 `faq.a2` 与定价页 `pg.faqA1` 逐字相同（防「改一处忘另一处」）。
  // 当天 Duner 决定把定价页那条常见问题整块拿掉——那一页只有这一条问答，而它问的是
  // 「多快出稿」，与这一页要讲的价格和计费规则无关。键也连着八门一起删了。
  //
  // ⚠️ 判据必须**翻个方向**，不能只把旧的删掉：删掉之后，谁把那条问答加回定价页
  // 都不会有任何东西报错，而那正是我们刚花一轮讨论决定不要的东西。
  //
  // 两条判据各挡一种加法：
  //  a) 整本 `pg.*` 渲染后不许出现哨兵——出稿时长必须是注入的（第①条已钉），
  //     所以任何谈它的键都会消费 `{eta}`、渲染出哨兵。照抄首页那句回来 → 当场红。
  //  b) `PricingPage.tsx` 不许引入那个常量——出稿时长的唯一来源就是 `lib/turnaround.ts`，
  //     页面拿不到它就不可能正确显示；绕过它手打一个数，等于把 2026-08-31 那次事故
  //     原样再犯（宣称的数在生产 17 个长单里一单都没达到过）。
  // ⚠️ **不能笼统地扫「任何分钟数」**：定价页有大量正当的分钟数（免费额度 180 / 60、
  //     单文件时长上限），那样写会假红、逼人加豁免，守卫就废了。
  for (const lang of langs) {
    it(`${lang}：定价页目录里没有任何键在谈出稿时长`, () => {
      const cat = CATALOGS[lang] as Record<string, unknown>;
      const offenders = Object.keys(cat)
        .filter((k) => k.startsWith("pg."))
        .filter((k) => asText(cat[k]).includes(SENTINEL));
      expect(offenders, `${lang}：定价页又谈起出稿时长了——2026-09-01 已整块删除，它不该回来`)
        .toEqual([]);
    });
  }

  it("定价页拿不到那个常量（出稿时长的唯一来源）", () => {
    const src = readFileSync(join(process.cwd(), "src/screens/legal/PricingPage.tsx"), "utf-8");
    expect(src.includes("TURNAROUND_MIN_PER_HOUR"),
      "PricingPage 又引入了出稿时长常量——这一页 2026-09-01 起整页不谈速度").toBe(false);
  });

  // ⚠️ **语种页 `lang.faqSpeedA` 故意不在这条判据里**（2026-09-01 订正）：
  // 它答的是「多快出稿？怎么计费？」两个问题合在一起的那一条，正文把出稿时长与
  // 单价规则并成一句，本来就不该与另外两处逐字相同。复检时我一度按「三处都该相同」
  // 写判据，那会逼人去改一句本来就正确的文案——**判据错了比没有判据更糟**。
  // 它的数字仍由上面第①条盯着。
  it("语种页那条是另一个问题的答案，本来就不该与首页那句相同", () => {
    const cat = CATALOGS.zh as Record<string, unknown>;
    expect(asText(cat["lang.faqSpeedQ"])).toContain("怎么计费");
    expect(asText(cat["lang.faqSpeedA"])).not.toBe(asText(cat["faq.a2"]));
  });
});

// 版式一致性守卫（2026-08-19）。
//
// 起因是一处「滚动条贴着卡片」，量下去发现底下是四条系统性不一致：页面内边距四套值、
// 滚动条两套、页面标题两套、三栏首行差 11–16px。这类问题的共同点是**测试不会红、
// tsc 不会红，只有真打开界面才看得见**——所以能钉的都钉在这里。
//
// 判据一律是「唯一来源」而不是「某个数字」：数字会有正当理由变，来源分家没有。
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = process.cwd();
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

// 登录后的应用页（营销站是另一套版式，不在此列）
const APP_PAGES = [
  "src/AppShell.tsx",                        // 详情页容器
  "src/screens/HistoryPage.tsx",
  "src/screens/AdminPage.tsx",
  "src/screens/main/MainPage.tsx",           // 上传页
  "src/screens/main/DetailSkeleton.tsx",
  "src/screens/main/GlossaryPage.tsx",
  "src/screens/main/PostprocessPage.tsx",
];

describe("页面内边距只有一个来源", () => {
  for (const f of APP_PAGES) {
    it(`${f.split("/").pop()} 用 layout.pagePad`, () => {
      expect(read(f)).toMatch(/padding: (T\.)?layout\.pagePad/);
    });
  }

  // 曾经并存的三套写法。回潮的话这里立刻红——它们看着都「合理」，正是这样才会回潮。
  it("旧的三套写法一处都不剩", () => {
    for (const f of APP_PAGES) {
      const s = read(f);
      expect(s, f).not.toContain('"28px 34px 30px"');
      expect(s, f).not.toContain('"32px 64px"');
      expect(s, f).not.toContain("${space.s8 + 4}px ${space.s10}px");
    }
  });
});

describe("滚动条只有一套", () => {
  // 不挂 .tx-scroll 就退回系统原生滚动条：macOS 上实测 15px 深灰、紧贴内容，
  // 而 .tx-scroll 是 8px 沙色。同一屏里两种并存，就是用户看到的「太近」。
  //
  // JSX 里 className 与 style 常不在同一行，所以按**文件计数**而不是按行：
  // 一个文件里有几个滚动容器，就得有至少同样多的 .tx-scroll。
  const SCROLLER = /overflow(Y)?: "auto"/g;
  // 唯一豁免：手机劝退屏。它只在触屏设备上出现，那里是覆盖式滚动条，没有这个问题。
  const EXEMPT = ["src/screens/NarrowScreenNotice.tsx"];

  const walk = (dir: string): string[] =>
    readdirSync(join(ROOT, dir)).flatMap((n) => {
      const rel = `${dir}/${n}`;
      if (statSync(join(ROOT, rel)).isDirectory()) return walk(rel);
      return rel.endsWith(".tsx") && !rel.includes(".test.") ? [rel] : [];
    });

  const files = [...walk("src/screens"), ...walk("src/components"), "src/AppShell.tsx"];

  it("每个滚动容器都挂了 tx-scroll", () => {
    const bad: string[] = [];
    for (const f of files) {
      if (EXEMPT.includes(f)) continue;
      const s = read(f);
      const scrollers = (s.match(SCROLLER) ?? []).length;
      const styled = (s.match(/tx-scroll/g) ?? []).length;
      if (scrollers > styled) bad.push(`${f}（${scrollers} 个滚动容器 / ${styled} 个 tx-scroll）`);
    }
    expect(bad).toEqual([]);
  });
});

describe("三栏工作台两页共用一套骨架", () => {
  const G = read("src/screens/main/GlossaryPage.tsx");
  const P = read("src/screens/main/PostprocessPage.tsx");

  it("栅格定义逐字相同", () => {
    const grid = (s: string) => s.match(/gridTemplateColumns: "220px 1fr 300px", gap: [^,]+, minWidth: \d+/)?.[0];
    expect(grid(G)).toBeTruthy();
    expect(grid(G)).toBe(grid(P));
  });

  // 左栏表头与中栏名称框必须同高，否则两栏的**第二行**起点差一截
  // （实测差过 16px：左栏列表 161 起、中栏编辑器 177 起）。
  it("左栏表头与名称框同高，且高度取自同一个令牌", () => {
    for (const [n, s] of [["术语库", G], ["脱敏规则", P]] as const) {
      expect((s.match(/T\.control\.fieldH/g) ?? []).length, n).toBeGreaterThanOrEqual(2);
    }
  });

  it("右栏给滚动条留了间隙", () => {
    for (const [n, s] of [["术语库", G], ["脱敏规则", P]] as const) {
      expect(s, n).toContain('overflowY: "auto", paddingRight: T.space.s2');
    }
  });
});

describe("应用页标题层级一致", () => {
  // type.title 是衬线 30，红线 1 里只留给品牌发声。应用页标题一律 h1（无衬线 22），
  // 且要是真的 <h1>——术语库/脱敏原来用 div，那两页在读屏器里没有一级标题。
  const PAGES_WITH_TITLE = [
    "src/screens/HistoryPage.tsx",
    "src/screens/AdminPage.tsx",
    "src/screens/main/GlossaryPage.tsx",
    "src/screens/main/PostprocessPage.tsx",
  ];
  for (const f of PAGES_WITH_TITLE) {
    it(`${f.split("/").pop()} 用 h1 + type.h1`, () => {
      const s = read(f);
      expect(s).toMatch(/<h1 style={{ \.\.\.(T\.)?type\.h1/);
      expect(s).not.toMatch(/(T\.)?type\.title/);
    });
  }
});

describe("等分栅格在八门语言下都得站得住", () => {
  // 2026-08-19：上传页的语言格写着「4 列等分」，但裸 `1fr` 其实是 `minmax(auto, 1fr)`——
  // 「auto」意味着列永远不会窄过内容，所以译文一长，等分就悄悄不成立了：
  // 拉丁文断不了词 → 那一列被撑宽、其余被挤窄（德语列宽差 22px）；
  // 日文可任意断行 → 就地换成两行（ポルトガル語）。两种症状、同一个原因。
  // 实测：栅格净宽 410px，四列每列 96px，而最长语种名 en 101 / de 114 / it 101 / ja 108
  // ——八门里四门放不下。改三列后每列 131px、可用 101px，八门全够；2026-08-21 露出 11 门后
  // 最紧的一格是日文「インドネシア語」91px，仍余 10px。
  it("上传页语言格：三列 + minmax(0,1fr)", () => {
    const s = read("src/screens/main/LangPicker.tsx");
    expect(s).toContain('gridTemplateColumns: "repeat(3, minmax(0, 1fr))"');
    expect(s).not.toMatch(/gridTemplateColumns: "repeat\(\d+, 1fr\)"/);
  });

  // 「不许悄悄新增」：裸 1fr 的等分栅格全站只剩下面这几处，且都在八门下逐一量过没问题
  // （2026-08-19，量具是临时体检台：一次只渲染一门语言——UILangProvider 会写 <html lang>，
  // 八门同屏时 :lang() 的字体栈会有七门是错的，那样量出来的宽度不作数）。
  // 新增一处就会红：要么改用 minmax(0,1fr)，要么自己在八门下量过再登记进来。
  const BARE_FR_OK: [string, number][] = [
    ["src/screens/TopUpModal.tsx", 2],       // 两栏浮窗 + 三档金额
    ["src/screens/main/Idle.tsx", 1],        // 时长 / 预估费用 两格
  ];
  it("裸 1fr 等分栅格没有新增", () => {
    const walk = (dir: string): string[] =>
      readdirSync(join(ROOT, dir)).flatMap((n) => {
        const rel = `${dir}/${n}`;
        if (statSync(join(ROOT, rel)).isDirectory()) return walk(rel);
        return rel.endsWith(".tsx") && !rel.includes(".test.") ? [rel] : [];
      });
    const found: Record<string, number> = {};
    for (const f of [...walk("src/screens"), ...walk("src/components")]) {
      const n = (read(f).match(/gridTemplateColumns: "(repeat\(\d+, 1fr\)|1fr( 1fr)+)"/g) ?? []).length;
      if (n) found[f] = n;
    }
    expect(found).toEqual(Object.fromEntries(BARE_FR_OK));
  });

  // 产物下载行（详情页右栏，只有约 300px）：文字区必须能被压缩，否则那一行会把下载按钮顶出去。
  // 2026-08-22 在生产页面上量出来的：324px 的行，再挂一句「已按你的 N 处修订」就要 362px。
  // 根因与上一条同源——**flex 子项的默认 min-width 是 auto**，永远不会窄过内容，
  // 于是 textOverflow: ellipsis 一辈子不会生效。tsc 与全部行为测试都照绿，只有真打开界面才看得见。
  it("下载行的文字区可压缩（ellipsis 才会生效）", () => {
    const src = read("src/screens/main/PostprocessCard.tsx");
    const row = src.slice(src.indexOf("function ProductRow"), src.indexOf("function ProductRow") + 3200);
    // 撑开那一栏的是它自己那份 flex:1；没有 minWidth:0 就等于「按内容撑」
    expect(row).toMatch(/flex: 1, minWidth: 0, display: "flex", flexDirection: "column"/);
    // 每一段会变长的文案（清单名 / 修订说明）都要各自带 minWidth: 0
    const ell = row.match(/textOverflow: "ellipsis"[^}]*/g) ?? [];
    expect(ell.length).toBeGreaterThanOrEqual(2);
    for (const e of ell) expect(e).toContain("minWidth: 0");
  });
});

// ── 界面文案里不许出现 markdown 星号（2026-08-26 浏览器走查实见）──────────────
// 运营舱容量卡那段说明里写了 `**全系统真正的吞吐天花板**`，而这些卡片是纯文本渲染、
// 不解析 markdown ⇒ 用户看到的就是一对星号。tsc 与全部测试都是绿的，
// **只有真打开界面才看得见**——同上面那一整类问题，所以钉在这里。
// 判据只看 `L("…")` 里的字符串字面量：注释与代码里的强调本来就该有，不算。
describe("界面文案不带 markdown", () => {
  const walk = (dir: string): string[] => readdirSync(join(ROOT, dir)).flatMap((n) => {
    const rel = `${dir}/${n}`;
    if (statSync(join(ROOT, rel)).isDirectory()) return walk(rel);
    return n.endsWith(".tsx") && !n.includes(".test.") ? [rel] : [];
  });

  it("没有会被原样显示给用户的 ** 强调", () => {
    const bad: string[] = [];
    for (const f of walk("src/screens")) {
      for (const m of read(f).matchAll(/L(?:\.t|\.x)?\(\s*("(?:[^"\\]|\\.)*")/g)) {
        if (m[1].includes("**")) bad.push(`${f}: ${m[1].slice(0, 50)}…`);
      }
    }
    expect(bad, `这些文案会把 ** 原样显示给用户：\n${bad.join("\n")}`).toEqual([]);
  });
});

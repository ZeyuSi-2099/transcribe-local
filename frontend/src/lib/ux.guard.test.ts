// 2026-08-31 全链路穿测（未登录 → 定价 → 登录 → 验证码 → 上传 → 充值 → 付款 → 历史 → 复核 → 导出）
// 改出来的东西，钉在这里。
//
// 本机版（与线上不同）：付款页、登录屏、定价页、免费额度那几组随页面一起去掉；firstRun 只等任务列表（没有账本）。
//
// 这一批的共同点是：**tsc 不会红、既有测试也不会红，只有真打开界面才看得见**——
// 正因如此才要钉。判据一律选「只在错误状态下才成立」的特征，每条都造回过一次 bug 验证会红。
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const read = (p: string) => readFileSync(join(process.cwd(), p), "utf-8");

/** src/ 下所有产品源码（不含测试）。给「全仓扫一遍」那类判据用。 */
function srcFiles(dir = "src"): string[] {
  const out: string[] = [];
  for (const e of readdirSync(join(process.cwd(), dir), { withFileTypes: true })) {
    const p = `${dir}/${e.name}`;
    if (e.isDirectory()) out.push(...srcFiles(p));
    else if (/\.tsx?$/.test(e.name) && !/\.test\.|\.guard\./.test(e.name)) out.push(p);
  }
  return out;
}

describe("焦点环是全站默认，不靠逐个组件挂类", () => {
  const css = read("src/styles/global.css");

  it("有一条不挂类名的 :focus-visible 规则", () => {
    // 判据是「选择器里出现了元素名」而不是「文件里有 focus-visible」——
    // 只有 `.tx-focus:focus-visible` 的话后者照样成立，而那正是修复前的状态。
    expect(css).toMatch(/:where\([^)]*\bbutton\b[^)]*\):where\(:focus-visible\)/);
    expect(css).toMatch(/:where\([^)]*\ba\b[^)]*\):where\(:focus-visible\)/);
  });

  it("`:focus-visible` 也包在 :where() 里——不然组件盖不住它", () => {
    // ⚠️ 2026-09-01：只包元素名的话这条规则是 (0,1,0)，与组件里 `.input { outline: none }`
    // 打平、谁在后面谁赢，于是「组件想自己画焦点态仍盖得住」这句承诺**根本没兑现**——
    // 登录屏那个下划线式邮箱框被套上一个 2px 赤陶方框，看着像报错（Duner 实测报回来的）。
    // 判据钉在**两层 where** 上：写成 `:where(...):focus-visible` 照样能过上一条，却是坏的。
    expect(css).not.toMatch(/:where\([^)]*\):focus-visible\s*[,{]/);
    // 而下划线式输入框要有自己的焦点态——不能只是把全局那圈关掉了事
    const input = read("src/components/Input.module.css");
    expect(input).toMatch(/\.input:focus-visible\s*\{[^}]*outline:\s*none/);
    expect(input).toMatch(/\.input:focus-visible\s*\{[^}]*border-bottom:[^;]*accentDeep/);
  });

  it("用品牌色，不是浏览器默认的蓝", () => {
    const rule = css.slice(css.indexOf(":focus-visible"), css.indexOf(".tx-scroll"));
    expect(rule).toContain("var(--c-accentDeep)");
  });

  it("焦点环不自己写 border-radius（outline 会跟随元素圆角，写死会把胶囊按钮拍方）", () => {
    const from = css.indexOf(":where(");
    const body = css.slice(css.indexOf("{", from), css.indexOf("}", from));
    expect(body).not.toMatch(/border-radius/);
  });
});

describe("小型大写标签的字距按语言切", () => {
  it("type.label 走 CSS 变量，不写死像素", () => {
    // 1.4px 是照拉丁字形调的，套到中日文上会把「另见」撑成「另 见」。
    expect(read("src/styles/tokens.ts")).toMatch(/label:\s*\{[^}]*letterSpacing: "var\(--ls-label\)"/);
  });

  it("CJK 块里把它归零（漏掉的话中日文标签仍被撑散，且不报错）", () => {
    const css = read("src/styles/tokens.css");
    const cjk = css.match(/:root:lang\(zh\)[^{]*\{[^}]*\}/)?.[0] ?? "";
    expect(cjk).toContain(":lang(ja)");
    expect(cjk).toMatch(/--ls-label:\s*0/);
  });

  it("全仓没有第二处写死字距的大写标签", () => {
    // ⚠️ 这一条是 2026-09-01 补的，因为上一轮**漏了一处而且没人发现**：
    // 令牌层改对了、六处同类写法也收编了，唯独 `mk.ts` 的 kicker 没动——而它是营销站
    // 每一屏的章节小标题、19 个调用点，于是中文首页上 11 个 kicker 一直被 1.4px 撑着
    //（痛点 / 解法 / 定价 …），页脚那个「另见」倒是修好了。
    // 判据必须是**全仓扫描**而不是「那几个文件改了没有」：后者只能守住已知的六处，
    // 而这类问题的本质就是「你不知道还有第七处」。
    const bad: string[] = [];
    for (const f of srcFiles()) {
      const t = read(f);
      let i = t.indexOf('textTransform: "uppercase"');
      while (i >= 0) {
        // 窗口取同一个样式对象的量级（前 200 / 后 120 字符），别放大到整段——
        // 放大会把无关的 `letterSpacing: 0` 也圈进来，逼人加豁免，守卫就废了。
        const win = t.slice(Math.max(0, i - 200), i + 120);
        if (/letterSpacing: -?[0-9]/.test(win)) bad.push(f);
        i = t.indexOf('textTransform: "uppercase"', i + 1);
      }
    }
    expect(bad, "这些地方的大写标签字距写死了，中日文界面上会被撑散").toEqual([]);
  });
});

describe("详情页：主次由「还有没有人要拍板的地方」决定", () => {
  const src = read("src/screens/main/Result.tsx");

  it("有待办时，头部出现一颗指向复核的实心键", () => {
    expect(src).toMatch(/pendingCount > 0 && firstPending && \(/);
    expect(src).toContain("{0} left to confirm");
  });

  it("有待办时下载键退成描边——判据是它的底色随 pendingCount 变", () => {
    // 只断言「有这个变量」不够：变量存在而按钮底色写死 accent.fill 时照样绿。
    expect(src).toMatch(/background: pendingCount > 0 \? "transparent" : semantic\.accent\.fill/);
  });

  it("相邻两个文字链的点击盒不重叠（负外边距吃掉的量不能超过 gap）", () => {
    // link() 左右各挂 -8px 负外边距来撑命中区（视觉位置逐像素不变）。但相邻两个链接之间
    // 只隔着容器的 gap，gap < 16 时两个**点击盒会重叠**，重叠部分归后一个——
    // 「跳过」会吃掉「删除这句」右边 4px，而加大命中区正是那一改的目的。
    const SPACE: Record<string, number> = { s1: 4, s2: 8, s3: 12, s4: 16, s5: 20, s6: 24 };
    const mx = Number(src.match(/margin: "-\d+px -(\d+)px", fontSize: 12/)?.[1]);
    expect(mx, "link() 的负外边距没找到——判据失效了，别让它假绿").toBeGreaterThan(0);
    const gapTok = src.match(/删除这句」右边 4px[\s\S]{0,400}?display: "flex", gap: space\.(s\d)/)?.[1] ?? "";
    expect(SPACE[gapTok] ?? 0, `复核卡脚注那一行的 gap=${gapTok}`).toBeGreaterThanOrEqual(mx * 2);
  });

  it("下载始终点得动——这是一道建议的次序，不是一堵墙", () => {
    // disabled 一旦出现在导出主键上，就是把「先复核」从建议变成了强制。
    const seg = src.slice(src.indexOf("primaryExport.run"), src.indexOf("aria-label={L(\"选择格式\""));
    expect(seg).not.toMatch(/disabled/);
  });
});

describe("历史页：状态列三种语义三种颜色", () => {
  const src = read("src/screens/HistoryPage.tsx");

  it("「失败」走警示金，不与「处理中」的赤陶同族", () => {
    const from = src.indexOf('if (st === "failed")');
    const failed = src.slice(from, src.indexOf("</span>;", from));
    expect(failed).toContain("semantic.warning.bg");
    expect(failed).not.toContain("semantic.accent.bgSoft");
  });

  it("「处理中」仍是赤陶脉冲（两者都变中性的话这一列就没有状态色了）", () => {
    const active = src.slice(src.indexOf('if (st === "uploading" || st === "processing")'), src.indexOf('if (st === "failed")'));
    expect(active).toContain("semantic.accent.bgTint");
  });

  it("「重试」在文件名格里，不在行尾列", () => {
    // 行尾列为它常驻 108px，而 99% 的行那一格是空的——长文件名被截断，右边却空着。
    const tail = src.slice(src.indexOf("{/* 行尾："));
    expect(tail).not.toContain("onRetry(item)");
    expect(src).toMatch(/const COLS = "1fr [^"]*44px"/);
  });
});

describe("上传页左栏放「你自己的东西」，不是卖点", () => {
  const src = read("src/screens/main/Idle.tsx");

  it("不再复用登录屏的卖点组件", () => {
    expect(src).not.toMatch(/<HeroPitch\s*\/>/);
  });

  it("栅格恒定两栏——布局不许取决于一个要等半秒才回来的接口", () => {
    // ⚠️ 2026-09-01 第二次修。第一次用本地提示位「照上次的结论猜」，在无痕窗口里那个位子
    // 永远是空的 ⇒ 等于没修（Duner 实测：卡片先居中、半秒后横着跳到右边）。
    // 判据钉在「gridTemplateColumns 不随任何变量变」上：只要它又出现三元表达式就红。
    // 只看最外层那一个（468 是上传卡的固定宽度，卡内还有别的栅格）
    const outer = src.match(/gridTemplateColumns: [^,\n]*468px[^,\n]*/g) ?? [];
    expect(outer, "上传页最外层的栅格列定义").toEqual(['gridTemplateColumns: "1fr 468px"']);
    expect(src, "别把本地提示位加回来——它在无痕窗口里不生效").not.toMatch(/tx_upload_two_col/);
  });

  it("左栏三态，且「还没拉回来」时要占位", () => {
    // 有稿子 → 最近的转录 · 确实没有 → 第一次上传的三步 · 还不知道 → 空占位。
    // ⚠️ 占位不能省：栅格按出现顺序放，不占的话上传卡会掉进 1fr 那一列去。
    expect(src).toMatch(/const loaded = recent !== undefined/);
    expect(src).toMatch(/\{!loaded \? <div aria-hidden \/>/);
    expect(src).toMatch(/<RecentPanel/);
    expect(src).toMatch(/<FirstRunPanel \/>/);
    expect(read("src/AppShell.tsx")).toMatch(/recent=\{jobsLoaded \? historyItems : undefined\}/);
  });

});

describe("语种格是可操作的控件", () => {
  const src = read("src/screens/main/LangPicker.tsx");

  it("是 <button> 不是 <span onClick>（Tab 走不到、回车按不动、没有焦点环）", () => {
    expect(src).not.toMatch(/<span\s+[^>]*onClick=\{\(\) => onChange/);
    expect(src).toMatch(/<button[\s\S]{0,200}onClick=\{\(\) => onChange\(seg\.id\)\}/);
  });

  it("选中态与充值档位同一套（描边 + 浅底），不是唯一一处纯墨填充", () => {
    expect(src).toContain("semantic.accent.bgTint");
    expect(src).not.toMatch(/background: selected \? semantic\.text\.primary/);
  });

  it("两态描边同宽、字重相同——不同就会让那一格撑宽/矮 1px，整排歪掉", () => {
    expect(src).toMatch(/border: selected \? `1\.5px[\s\S]{0,60}: `1\.5px/);
    expect(src).not.toMatch(/fontWeight: selected \?/);
  });
});

describe("卡片面与页面底分得开", () => {
  it("卡片面上提到 sand25，浮层再上提到纯白（两者相等就丢了浮层那一档）", () => {
    const ts = read("src/styles/tokens.ts");
    expect(ts).toMatch(/raised: palette\.sand25/);
    expect(ts).toMatch(/float: palette\.white/);
  });

  it("text.onAccent 没跟着动——它与卡片面语义无关，曾经只是碰巧同值", () => {
    expect(read("src/styles/tokens.ts")).toMatch(/onAccent: palette\.sand50/);
  });
});

describe("页面标题是品牌发声的位置", () => {
  it("type.h1 是衬线（2026-08-31 定；纪律 1 与 11 同日应改而漏了，2026-09-01 补钉）", () => {
    // 没有这一条的话，下一个人照着 CLAUDE.md 纪律 1 的旧文字把它改回 sans，
    // 而 tsc 与全部测试都不会红——正是这次复核查出来的形状。
    expect(read("src/styles/tokens.ts")).toMatch(/h1:\s*\{[^}]*fontFamily: fonts\.serif/);
  });

  it("只到 h1 为止，不许顺手扩到 h2/h3（一屏十几个，衬线铺开就看不清层级）", () => {
    const ts = read("src/styles/tokens.ts");
    for (const k of ["h2", "h3"]) {
      expect(ts.match(new RegExp(`${k}:\\s*\\{[^}]*`))?.[0] ?? "", k).toContain("fonts.sans");
    }
  });
});

// ───────────────────────────────────────────────────────────────────────────
// 2026-09-01 二轮：把 08-31 那两条（焦点环 / 布局不许猜数据）**当成形状**全仓扫了一遍，
// 又各查出一批同族的。两族的共同点仍是「只有真用起来才看得见」：
//   ① 键盘 Tab 一圈，有几个输入框根本不亮；
//   ② 刚登录那半秒，界面先替用户宣布「你什么都没有」，然后自我推翻。
// ───────────────────────────────────────────────────────────────────────────

describe("每一个关掉了焦点环的控件，都要有替代的焦点态", () => {
  const css = read("src/styles/global.css");

  // 内联样式优先级最高——`style={{ outline: "none" }}` 把全站那条规则**彻底**关掉
  // （不是打平，是赢）。所以每一处都得登记：环去哪儿了。
  // ⚠️ 这张表是白名单不是说明：新写一个带 `outline:"none"` 的输入框会让下面那条直接红，
  // 这正是要的效果——08-31 漏掉这一批，就是因为「漏了不报错」。
  const ALLOWED: Record<string, { n: number; via: string }> = {
    // 本机版：充值浮窗不搬，线上登记的「自定义金额（下划线式）」那一处随之去掉
    "src/screens/main/Result.tsx": { n: 1, via: "tx-field" },           // 复核卡「替换成…」胶囊
    "src/screens/main/RedactChanges.tsx": { n: 1, via: "tx-field" },    // 脱敏「改成…」胶囊
    "src/screens/main/PostprocessPage.tsx": { n: 1, via: "tx-field" },  // 保留词清单（铺满卡片）
  };

  it("全仓的内联 outline:none 与登记表逐项相等", () => {
    const found: Record<string, number> = {};
    for (const f of srcFiles()) {
      const n = (read(f).match(/outline:\s*"none"/g) ?? []).length;
      if (n > 0) found[f] = n;
    }
    expect(found).toEqual(
      Object.fromEntries(Object.entries(ALLOWED).map(([f, v]) => [f, v.n])));
  });

  it("登记的每一处都真的把环提到了容器上", () => {
    // 只登记不落实的话，表变成一份「我们打算怎么做」的备忘，而界面上仍然什么都没有。
    for (const [f, { via }] of Object.entries(ALLOWED)) {
      expect(read(f), `${f} 少了 ${via}`).toContain(`className="${via}"`);
    }
  });

  it("两条容器级规则在 global.css 里真的存在", () => {
    // 类名挂了、规则没了＝静默失效：DOM 上看得见 class，Tab 过去还是不亮。
    expect(css).toMatch(/\.tx-field:focus-within\s*\{[^}]*outline:\s*2px/);
    expect(css).toMatch(/\.tx-underline:focus-within\s*\{[^}]*inset 0 -2px/);
  });

  it("下划线式的那条不是方框——方框套在只有一条下边框的控件上像报错", () => {
    // 判据钉在「.tx-underline 规则体里没有 outline」上，而不是「有 box-shadow」：
    // 两条都写上的话后者照样成立，可屏幕上仍然是那个方框。
    const body = css.match(/\.tx-underline:focus-within\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(body).not.toMatch(/outline/);
  });
});

describe("界面不许在数据回来之前替用户宣布「你什么都没有」", () => {
  it("firstRun 等任务列表到齐（初值是空数组）", () => {
    const shell = read("src/AppShell.tsx");
    const line = shell.match(/const firstRun = [^;]*;/)?.[0] ?? "";
    expect(line).toContain("jobsLoaded");
    // jobsLoaded 得真的被置位——只声明不写就是永远 false，整页空态再也出不来了
    expect(shell).toMatch(/setJobsLoaded\(true\)/);
  });

  it("历史页 tab 内那句「暂时没有转录」也要等（它同样是一句断言）", () => {
    // 整页空态在 AppShell 那头已经挡住了，但 tab 内还有第二句——只修一句的话
    // 症状从「整屏说假话」变成「列表区说假话」，一样是先说后改。
    const h = read("src/screens/HistoryPage.tsx");
    expect(h).toMatch(/\{loaded && filtered\.length === 0 &&/);
  });
});

describe("纪律 15 · 文件选择框不许 display:none（2026-09-01）", () => {
  // `display:none` 的元素在部分浏览器上是「不存在」的，程序化 `.click()` 被忽略
  // **而且不报错**——症状是「按钮点不动、拖拽却好使」，只有真去点才发现。
  //
  // 这个坑踩过两次：上传页 2026-09-01 修过并把理由写进了注释，同一天术语库那处
  // 还是老写法（那是术语库里唯一的大纲入口）。第二次之所以还会发生，是因为
  // **注释只对读到它的人有效**，而写新组件的人不会去读别的文件的注释。
  //
  // ⚠️ 判据必须扫**全仓**，不能只钉那两个已知文件：这类问题的本质就是
  // 「你不知道还有第三处」（同 2026-09-01 那次字距漏网的教训）。
  it("全仓没有 display:none 的 <input type=\"file\">", () => {
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(join(process.cwd(), dir), { withFileTypes: true })) {
        const rel = `${dir}/${e.name}`;
        if (e.isDirectory()) walk(rel);
        else if (e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx")) {
          const t = read(rel);
          // ⚠️ **先整段切出 `<input …/>`，再在段内同时判两件事**——不许从 `type="file"`
          // 往后扫：属性顺序是随手写的、没有任何东西约束它，
          // `<input style={{display:"none"}} type="file" />` 这种写法会整条溜过去。
          // 而这条守卫立起来的理由正是「你不知道还有第三处」，那第三处很可能就是它。
          for (const m of t.matchAll(/<input\b[\s\S]{0,600}?\/>/g)) {
            if (/type="file"/.test(m[0]) && /display:\s*"none"/.test(m[0])) offenders.push(rel);
          }
        }
      }
    };
    walk("src");
    expect(offenders, "这些文件里的文件选择框用了 display:none —— 改成视觉隐藏（见 Idle.tsx 的注释）")
      .toEqual([]);
  });
  // ── 全角空格分隔符在 JSX 里只能写成 {"　"}（2026-09-03，生产实见）────────────────
  //
  // JSX 会把**紧挨换行**的空白吃掉——**行首和行尾都算**，而 JS 的 `\s` **包含全角空格 U+3000**，
  // 所以裸写在行首或行尾的 `　` 都会被静默删掉，两句话在页面上糊成一句：
  // 驾驶舱增长页实见「其他 1免费用尽 → 首充中位」「手工 $0.00本周：$0.00」「无不设闸，只看」。
  // 同一个文件里正反例并存——写在行**中**的那个（150 行）好好的。
  //
  // ⚠️ 判据扫**全仓**，不钉那几个已知位置：立这条守卫时它当场多揪出第 4 处
  // （免费发放图例的「个人档　· 峰值 N 分钟/天」），而那处逐屏看截图时并没有发现。
  // ⚠️ **第一版只管行首，是错的**：照它把 `　` 从行首挪到上一行行尾，守卫绿了、
  // 浏览器里照旧糊着（行尾同样紧挨换行）。⇒ 唯一可靠的写法是表达式容器 `{"　"}`，
  // 判据也必须两头都管。写在行**中**的裸 `　` 是好的（两侧都不挨换行），不许误伤。
  it("全仓没有裸写在 JSX 行首或行尾的全角空格分隔符", () => {
    const offenders: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(join(process.cwd(), dir), { withFileTypes: true })) {
        const rel = `${dir}/${e.name}`;
        if (e.isDirectory()) walk(rel);
        else if (e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx")) {
          read(rel).split("\n").forEach((line, i) => {
            // 缩进之后紧跟 / 行尾紧挨着的全角空格 = 它必然是想当分隔符用的，而 JSX 一定会吃掉它。
            // 字符串字面量里的全角空格两侧是引号或反引号，不会命中。
            if (/^[ \t]+\u3000/.test(line) || /\u3000[ \t]*$/.test(line)) offenders.push(`${rel}:${i + 1}`);
          });
        }
      }
    };
    walk("src");
    expect(offenders, "这些行的全角空格会被 JSX 吃掉，两句话会糊在一起 —— 改成 {\"　\"}")
      .toEqual([]);
  });
});

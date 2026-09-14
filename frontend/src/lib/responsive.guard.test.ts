// 小屏适配的守卫（2026-08-19）。
//
// 这一轮修的两个 bug 是同一个模式生出来的：**固定像素栅格 + 祖先 overflow:hidden**。
// 它的可怕之处是不报错、测试也全绿——内容不是溢出（那样还能滑），是被静静裁掉，
// 用户看到的是「几列凭空消失」，只有真在窄窗口里打开才看得见。登录屏 `1fr 468px`
// 与历史页六个固定列都栽在这里。所以把三条硬要求钉住。
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = process.cwd();
const read = (p: string) => readFileSync(join(ROOT, p), "utf-8");

describe("窄窗口不裁内容", () => {
  // 硬要求 1：主区兜底横滑。改回 overflow:hidden 就等于把裁切装回来。
  it("AppShell 内容区横向可滚，不是 hidden", () => {
    const s = read("src/AppShell.tsx");
    const content = s.slice(s.indexOf("右侧内容区"), s.indexOf("右侧内容区") + 1200);
    expect(content).toContain('overflowX: "auto"');
    expect(content).not.toContain('overflow: "hidden"');
  });

  // 硬要求 2：用固定像素栅格的页面必须声明自己需要多宽，否则上面那层横滑不会触发
  // ——容器不被撑开就没有滚动条，固定列照样被裁。数字的算式写在各文件的注释里。
  const MIN_WIDTHS: [string, number][] = [
    ["src/screens/main/Idle.tsx", 808],            // 468 卡 + 间距 + 左列
    ["src/screens/main/GlossaryPage.tsx", 824],    // 220 + 300 + 间距 + 中列
    ["src/screens/main/PostprocessPage.tsx", 824],
    ["src/screens/main/Result.tsx", 800],          // 360 复核栏 + 稿件栏
    // 2026-08-31：行尾列由 108 收回 44（「重试」挪进文件名格），固定六列 682 → 618
    ["src/screens/HistoryPage.tsx", 836],          // 固定六列 618 + 文件名 170 + 行内边距 48
  ];
  for (const [file, min] of MIN_WIDTHS) {
    it(`${file.split("/").pop()} 声明了最小宽度 ${min}`, () => {
      expect(read(file)).toMatch(new RegExp(`minWidth: ${min}|ROW_MIN_WIDTH = ${min}`));
    });
  }

  // 硬要求 2.5：声明了 minWidth 还不够——页面**自己的根容器**不能把溢出裁掉。
  // 术语库 / 后处理页的根写着 overflow:hidden，于是上面那条 minWidth 一直是空的：
  // 内容被撑开了，却在这一层被裁，外层 AppShell 的横滑够不到（2026-08-19 在 900px 下实测）。
  for (const f of ["GlossaryPage", "PostprocessPage"]) {
    it(`${f} 的根容器横向可滚，不是 hidden`, () => {
      const s = read(`src/screens/main/${f}.tsx`);
      expect(s).toContain('overflowX: "auto", overflowY: "hidden"');
      expect(s).not.toContain('padding: "28px 34px 30px", overflow: "hidden"');
    });
  }

  // 硬要求 3：历史页表格自己横滑（表头 sticky、侧栏与筛选 tab 不跟着移）
  it("历史页表格容器允许横向滚动", () => {
    expect(read("src/screens/HistoryPage.tsx")).toContain('overflowX: "auto"');
  });
});

describe("登录屏的窄屏形态", () => {
  // /signin 是构建期预渲染的网址之一：布局若用 JS 量宽度，Node 里没有 window，
  // 手机会先拿到宽屏版再跳变一次。所以布局必须全在 CSS 里。
  it("布局属性不写在内联 style 上（内联会顶掉媒体查询）", () => {
    const s = read("src/screens/LandingLogin.tsx");
    expect(s).not.toContain("gridTemplateColumns");
    expect(s).not.toContain('overflow: "hidden"');
    expect(s).toContain('className="tx-login-grid"');
  });

  it("global.css 里有窄屏单列规则，且卖点文案在窄屏收起", () => {
    const css = read("src/styles/global.css");
    const narrow = css.slice(css.indexOf("@media (max-width: 900px)"));
    expect(narrow).toContain(".tx-login-pitch { display: none; }");
    expect(narrow).toContain(".tx-login-grid");
    // 单列后内容更高，锁死一屏会让按钮够不着
    expect(narrow).toContain(".tx-login-shell { overflow: auto; }");
  });
});

describe("劝退的判据是设备，不是窗口宽度", () => {
  // 桌面用户把窗口拖窄是网页缩放的常态，弹提示反而怪。这条一旦改回按宽度判，
  // 坐在电脑前的人会被告知「请用电脑打开」。
  it("按 pointer:coarse + hover:none 判触屏设备", () => {
    const s = read("src/screens/NarrowScreenNotice.tsx");
    expect(s).toContain("(pointer: coarse) and (hover: none)");
    expect(s).toContain("if (!isTouchOnly()) return null");
  });

  it("手机与平板用屏幕短边分辨——窗口宽度分不开（手机横屏比平板竖屏还宽）", () => {
    const s = read("src/screens/NarrowScreenNotice.tsx");
    expect(s).toContain("Math.min(s.width, s.height)");
  });
});

// 首页前四拍「左文右卡、卡钉住」（2026-09-02 十拍重排，方案 C）。汇流图与它的横竖两版箭头已拆。
// 钉住只在桌面端成立：窄屏上卡占满整屏，钉住等于挡住正文。断点与回落都写在 mk.ts 里，
// Landing 不内联 position——内联优先级最高会顶掉媒体查询（同当年箭头 display 的坑）。
describe("首页前四拍：左文右卡，窄屏不钉", () => {
  it("Landing 用 CSS 类挂栅格，不内联 sticky", () => {
    const s = read("src/screens/marketing/Landing.tsx");
    expect(s).toContain('className="mk-story"');
    expect(s).toContain('className="mk-story-card"');
    for (const line of s.split("\n")) {
      if (line.includes("mk-story")) expect(line).not.toContain("position:");
    }
  });

  it("mk.ts 里桌面 sticky 与窄屏回落齐全", () => {
    const css = read("src/screens/marketing/mk.ts");
    expect(css).toContain(".mk-story-card > div { position: sticky");
    const narrow = css.slice(css.indexOf("@media (max-width: 899px)"));
    expect(narrow).toContain(".mk-story { grid-template-columns: 1fr");
    expect(narrow).toContain(".mk-story-card > div { position: static");
    // 横排小卡（标题左正文右）在 ≤520px 退成上下——140px 的标题列加正文，320px 宽的手机装不下
    const tiny = css.slice(css.indexOf("@media (max-width: 520px)"));
    expect(tiny).toContain(".mk-rowcard { grid-template-columns: 1fr");
  });
});

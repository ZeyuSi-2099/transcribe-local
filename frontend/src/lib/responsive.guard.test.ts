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
    // 本机版：去掉「计费」一列（116），固定五列 502 + 文件名 170 + 行内边距 48
    ["src/screens/HistoryPage.tsx", 720],
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

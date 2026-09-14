import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "./lib/i18n";
import { AppShell } from "./AppShell";
import { TURNAROUND_RATIO } from "./lib/turnaround";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

// 登录态下 AppShell 会拉 /me /jobs /ledger：给个安静的 fetch 桩
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      String(url).includes("/me") ? { email: "a@b.com", balanceCents: 2850 }
      : String(url).includes("/jobs") ? { jobs: [] }
      : { ledger: [] },
  })));
});

describe("AppShell", () => {
  it("new user lands straight on the upload page (welcome screen removed)", () => {
    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 0 }} onLogout={vi.fn()} />);
    expect(screen.getAllByText(/新建转录|New transcription/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/上传，/)).toBeNull(); // 旧欢迎屏标题不再出现
  });

  it("shows real account email and initial in the sidebar", () => {
    wrap(<AppShell me={{ email: "duner@x.com", balanceCents: 3000 }} onLogout={vi.fn()} />);
    expect(screen.getByText("duner@x.com")).toBeInTheDocument();
    expect(screen.getByText("duner")).toBeInTheDocument();      // 显示名 = 邮箱前缀
    expect(screen.getByText("D")).toBeInTheDocument();          // 头像首字母
  });

  it("navigates to billing from the avatar menu", async () => {
    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
    await userEvent.click(screen.getByText("A")); // 打开头像菜单（首字母头像）
    await userEvent.click(screen.getByText(/充值与账单|Billing/));
    // 新账户账本为空 → 账单浮窗显示空态（标题 + 去充值入口）
    expect(screen.getByText(/还没有消费记录|Nothing spent yet/)).toBeInTheDocument();
  });

  // 回归：切换历史任务时，详情页「全文」必须随新任务刷新，不能残留上一个任务的稿。
  // 真凶是 <Result> 被 React 复用实例、内部 segs state 不重置（详见 key={jobId} 修复）。
  it("切换历史任务后全文随新任务刷新，不残留上一个任务的稿", async () => {
    const segsA = [{ t: "00:00:01", s: "甲任务专属句ALPHA标记", sp: "被访者" }];
    const segsC = [{ t: "00:00:01", s: "乙任务专属句GAMMA标记", sp: "被访者" }];
    const jobs = [
      // createdAt 必须是近期动态时间：写死日期会在 30 天后触发「结果已过期」不可点态，测试凭空腐化
      { id: "jobA", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "甲.flac", durationSec: 60, createdAt: new Date(Date.now() - 7200_000).toISOString(), error: null },
      { id: "jobC", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "乙.flac", durationSec: 60, createdAt: new Date(Date.now() - 3600_000).toISOString(), error: null },
    ];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      const body =
        u.includes("/jobs/jobA/result") ? segsA
        : u.includes("/jobs/jobC/result") ? segsC
        : u.includes("/result") ? []
        : u.includes("/review") ? []
        : u.includes("/me") ? { email: "a@b.com", balanceCents: 1000 }
        : u.includes("/ledger") ? { ledger: [] }
        : u.includes("/glossary") ? { text: "" }
        : (u.endsWith("/jobs") || u.includes("/jobs?")) ? { jobs }
        : {};
      return { ok: true, json: async () => body };
    }));

    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 1000 }} onLogout={vi.fn()} />);

    // 进侧栏「我的转录」→ 开甲任务 → 看到甲的全文
    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("甲.flac"));
    expect(await screen.findByText(/ALPHA标记/)).toBeInTheDocument();

    // 经侧栏返回列表 → 开乙任务
    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("乙.flac"));

    // 关键断言：全文是乙的，且甲的全文不再残留
    expect(await screen.findByText(/GAMMA标记/)).toBeInTheDocument();
    expect(screen.queryByText(/ALPHA标记/)).toBeNull();
  });

  // 端到端把「会丢数据」那条走一遍（2026-08-23）：复核决策的 GET 挂掉时，
  // 详情页不许把一本空账本写回服务端——那会盖掉用户早就确认完的全部决策。
  // 双保险：外层不再把「问不出来」谎报成「没有」，页面也拒绝在不知情时落笔。
  it("复核决策取不到时：打开详情不许把空账本写回服务端", async () => {
    const jobs = [
      { id: "jobA", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "甲.flac", durationSec: 60, createdAt: new Date(Date.now() - 7200_000).toISOString(), error: null },
    ];
    const puts: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url), m = init?.method ?? "GET";
      if (u.includes("/review_state")) {
        if (m === "PUT") { puts.push(String(init?.body)); return { ok: true, json: async () => ({}) }; }
        return { ok: false, status: 500, json: async () => ({}) };   // 就是取不到
      }
      const body =
        u.includes("/jobs/jobA/result") ? [{ t: "00:00:01", s: "甲任务专属句ALPHA标记", sp: "被访者" }]
        : u.includes("/result") ? []
        : u.includes("/review") ? []
        : u.includes("/me") ? { email: "a@b.com", balanceCents: 1000 }
        : u.includes("/ledger") ? { ledger: [] }
        : u.includes("/glossar") ? { glossaries: [] }
        : (u.endsWith("/jobs") || u.includes("/jobs?")) ? { jobs }
        : {};
      return { ok: true, json: async () => body };
    }));

    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 1000 }} onLogout={vi.fn()} />);
    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("甲.flac"));
    expect(await screen.findByText(/ALPHA标记/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 1400));   // 回存防抖 800ms，等过它
    expect(puts, `不许回存，却写了：${puts.join(" / ")}`).toEqual([]);
  });

  // 2026-08-22 Duner 实测：先看 A 的详情，回列表再点 B，页面会先原样显示半秒到一秒的
  // **A 的详情页**，然后才切成 B。真凶是取数期间 detailSrc 仍挂着上一单，而渲染分支是
  // 「有数据就渲染详情，否则才看 loading」——于是骨架屏根本没机会出现。
  it("点开另一单：取数期间不许还挂着上一单的详情", async () => {
    const jobs = [
      { id: "jobA", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "甲.flac", durationSec: 60, createdAt: new Date(Date.now() - 7200_000).toISOString(), error: null },
      { id: "jobC", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "乙.flac", durationSec: 60, createdAt: new Date(Date.now() - 3600_000).toISOString(), error: null },
    ];
    let releaseC: (() => void) | null = null;
    const cPending = new Promise<void>((r) => { releaseC = r; });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/jobs/jobC/result")) await cPending;      // 卡住 B 的取数
      const body =
        u.includes("/jobs/jobA/result") ? [{ t: "00:00:01", s: "甲任务专属句ALPHA标记", sp: "被访者" }]
        : u.includes("/jobs/jobC/result") ? [{ t: "00:00:01", s: "乙任务专属句GAMMA标记", sp: "被访者" }]
        : u.includes("/result") ? []
        : u.includes("/review") ? []
        : u.includes("/me") ? { email: "a@b.com", balanceCents: 1000 }
        : u.includes("/ledger") ? { ledger: [] }
        : u.includes("/glossary") ? { text: "" }
        : (u.endsWith("/jobs") || u.includes("/jobs?")) ? { jobs }
        : {};
      return { ok: true, json: async () => body };
    }));

    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 1000 }} onLogout={vi.fn()} />);
    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("甲.flac"));
    expect(await screen.findByText(/ALPHA标记/)).toBeInTheDocument();

    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("乙.flac"));
    // B 还没回来：A 的全文必须已经不在场（在场即回归）
    expect(screen.queryByText(/ALPHA标记/)).toBeNull();

    releaseC!();
    expect(await screen.findByText(/GAMMA标记/)).toBeInTheDocument();
  });

  // 复核决策必须**跟稿子一起**取回来。留在详情页里自己取的话，右栏第一帧一定是
  // 「一条决策都没有」：复核完的稿子先摆「N 项待确认」+ 后处理「还差 N 处确认」，
  // 几百毫秒后才翻成「已全部确认」（2026-08-22 Duner 连拍四张实见）。
  it("点开历史任务：决策与稿子一起取回来，右栏第一帧就是对的", async () => {
    const jobs = [
      { id: "jobA", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "甲.flac", durationSec: 60, createdAt: new Date(Date.now() - 3600_000).toISOString(), error: null },
    ];
    const review = [{
      type: "doubt", term: "尚界", reason: "各轨音糊", mustConfirm: true,
      occurrences: [{ t: "00:00:01", lineText: "甲任务专属句ALPHA标记", reason: "各轨音糊" }],
    }];
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      seen.push(u);
      const body =
        u.includes("/review_state") ? { occRes: { "doubt:尚界": { 0: { kind: "ok" } } }, skipped: {} }
        : u.includes("/result") ? [{ t: "00:00:01", s: "甲任务专属句ALPHA标记", sp: "被访者" }]
        : u.includes("/review") ? review
        : u.includes("/me") ? { email: "a@b.com", balanceCents: 1000 }
        : u.includes("/ledger") ? { ledger: [] }
        : u.includes("/glossar") ? { glossaries: [] }
        : (u.endsWith("/jobs") || u.includes("/jobs?")) ? { jobs }
        : {};
      return { ok: true, json: async () => body };
    }));

    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 1000 }} onLogout={vi.fn()} />);
    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("甲.flac"));

    // 详情一出现就是「已全部确认」——中间不该有「待你确认 1」那一帧
    expect(await screen.findByText(/存疑已全部确认|All cleared/)).toBeInTheDocument();
    // 决策确实是开页面前取的（与稿子同一批）
    expect(seen.some((u) => u.includes("/review_state"))).toBe(true);
  });

  // E1：复核清单取不到（503）不许吞成空清单开详情——那会显示「放心导出」伪装。
  // 正确行为：这次点开失败、弹回列表，用户可重试；绝不能带着 review=[] 进详情页。
  it("历史任务复核清单 503 → 弹回列表，不带空清单伪装进详情", async () => {
    const jobs = [
      { id: "jobA", status: "done", progress: 100, phase: "done", lang: "zh", fileName: "甲.flac", durationSec: 60, createdAt: new Date(Date.now() - 3600_000).toISOString(), error: null },
    ];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/review")) return { ok: false, status: 503, json: async () => ({}) };
      const body =
        u.includes("/result") ? [{ t: "00:00:01", s: "甲任务专属句ALPHA标记", sp: "被访者" }]
        : u.includes("/me") ? { email: "a@b.com", balanceCents: 1000 }
        : u.includes("/ledger") ? { ledger: [] }
        : u.includes("/glossary") ? { text: "" }
        : (u.endsWith("/jobs") || u.includes("/jobs?")) ? { jobs }
        : {};
      return { ok: true, json: async () => body };
    }));

    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 1000 }} onLogout={vi.fn()} />);
    await userEvent.click(screen.getAllByText("我的转录")[0]);
    await userEvent.click(await screen.findByText("甲.flac"));

    // 弹回列表（列表行还在），且详情全文没有渲染出来
    expect(await screen.findByText("甲.flac")).toBeInTheDocument();
    expect(screen.queryByText(/ALPHA标记/)).toBeNull();
  });
});

describe("AppShell — 进度与 ETA 必须同源", () => {
  it("历史行的「约 X 分钟」按显示进度折算，不按后端 Phase 锚点——否则会出现「3% 却说剩 3 分钟」", async () => {
    // 后端锚点已跑到 55%，但本浏览器刚开始观察这个 job（进度条从 0 重新爬）。
    // ETA 若拿 55% 去算会说「剩 3 分钟」，与旁边显示的 0% 自相矛盾。
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        String(url).includes("/me") ? { email: "a@b.com", balanceCents: 100000 }
        : String(url).includes("/jobs") ? { jobs: [{
            id: "j-running", status: "running", progress: 55, phase: "P2",
            lang: "zh", fileName: "长访谈.flac", durationSec: 688,   // 11.5 分钟
            createdAt: new Date().toISOString(), error: null, costCents: 115,
          }] }
        : { ledger: [] },
    })));
    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 100000 }} onLogout={vi.fn()} />);
    await userEvent.click(screen.getAllByText(/我的转录|My transcriptions/)[0]);
    // 首次观察 → 进度条 0%；ETA 必须跟这个 0% 一致（整段录音的预计时长），
    // 不是拿后端锚点 55% 折算出来的那个更小的数。
    // ⚠️ 期望值从 turnaround 现算，不写死分钟数——那个常量是对外承诺，会随实测调整，
    // 写死的话调它就要回头改这条测试，而这条测试要守的是「两个数同源」，不是某个具体分钟数。
    const want = Math.round(688 * TURNAROUND_RATIO / 60);
    const row = await screen.findByText(/0% · 约 \d+ 分钟|0% · ~\d+ min/);
    expect(row.textContent).toMatch(new RegExp(`约 ${want} 分钟|~${want} min`));
  });

  // 小屏劝退（审计 P0-2；2026-08-19 判据从「窗口宽度」改成「设备」）
  //
  // 桌面用户把窗口拖窄是网页缩放的常态，弹提示反而怪——所以桌面**任何宽度都不拦**，
  // 放不下的部分靠横向滚动看。只拦触屏设备：手机换电脑（横过来也不够宽），
  // 平板竖屏只需转横屏，两种话术不能混——对手机说「横过来」是让人白跑一趟。
  describe("小屏劝退", () => {
    const setWidth = (w: number) => {
      Object.defineProperty(window, "innerWidth", { value: w, configurable: true, writable: true });
    };
    /** 模拟触屏无鼠标（手机/平板）或桌面。触屏笔记本有 hover，算桌面。 */
    const setTouch = (touch: boolean) => {
      Object.defineProperty(window, "matchMedia", {
        configurable: true, writable: true,
        value: (q: string) => ({
          matches: touch && q.includes("pointer: coarse"),
          media: q, onchange: null,
          addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
          dispatchEvent: () => false,
        }),
      });
    };
    /** 屏幕物理尺寸：短边决定是手机还是平板（与当前横竖屏无关） */
    const setScreen = (w: number, h: number) => {
      Object.defineProperty(window, "screen", { configurable: true, value: { width: w, height: h } });
    };
    const phone = () => { setTouch(true); setScreen(390, 844); setWidth(390); };
    const tabletPortrait = () => { setTouch(true); setScreen(820, 1180); setWidth(820); };

    it("手机：不渲染应用，让人用电脑", () => {
      phone();
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      expect(screen.getByText(/请用电脑打开|desktop browser/)).toBeInTheDocument();
      expect(screen.queryByText(/请把设备横过来|rotate your device/)).toBeNull();
      expect(screen.queryByText(/新建转录|New transcription/)).toBeNull();
    });

    it("手机横过来仍然拦——横屏 844px 还是不够宽，别让人白转一次", () => {
      setTouch(true); setScreen(390, 844); setWidth(844);
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      expect(screen.getByText(/请用电脑打开|desktop browser/)).toBeInTheDocument();
    });

    it("平板竖屏：让人横过来，不是让人换电脑", () => {
      tabletPortrait();
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      expect(screen.getByText(/请把设备横过来|rotate your device/)).toBeInTheDocument();
      expect(screen.queryByText(/请用电脑打开|desktop browser/)).toBeNull();
    });

    it("平板横屏：放行（1180px 装得下）", () => {
      setTouch(true); setScreen(820, 1180); setWidth(1180);
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      expect(screen.getAllByText(/新建转录|New transcription/).length).toBeGreaterThan(0);
    });

    it("桌面窗口拖到 700px：照常进应用，一个字都不提示", () => {
      setTouch(false); setScreen(1512, 982); setWidth(700);
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      expect(screen.queryByText(/请用电脑打开|desktop browser/)).toBeNull();
      expect(screen.queryByText(/请把设备横过来|rotate your device/)).toBeNull();
      expect(screen.getAllByText(/新建转录|New transcription/).length).toBeGreaterThan(0);
    });

    it("点「仍要继续」后放行——坚持要在手机上看的人不该被锁死", async () => {
      phone();
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      await userEvent.click(screen.getByText(/仍要继续|Continue anyway/));
      expect(screen.getAllByText(/新建转录|New transcription/).length).toBeGreaterThan(0);
      expect(screen.queryByText(/请用电脑打开|desktop browser/)).toBeNull();
    });

    it("宽屏桌面不受影响", () => {
      setTouch(false); setScreen(1512, 982); setWidth(1440);
      wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
      expect(screen.queryByText(/请用电脑打开|desktop browser/)).toBeNull();
      expect(screen.getAllByText(/新建转录|New transcription/).length).toBeGreaterThan(0);
    });
  });
});

describe("上传页的术语库默认值", () => {
  // ⚠️ 这不是偏好问题，是**正确性**问题：挂错一本库不会报错，它会把近音词悄悄拽向那本库
  // 所属行业的写法，界面上还把结果标成【术语库】证据——用户看到的是一条言之凿凿的依据。
  // 所以默认必须是「不使用」，由用户显式挑。判据只在「没人替他选」时才成立。
  const withBooks = () =>
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      return {
        ok: true,
        json: async () =>
          u.includes("/glossaries") ? { glossaries: [
            { id: "g1", name: "光伏采购访谈", language: "zh", content: "", updatedAt: new Date().toISOString() },
            { id: "g2", name: "医疗器械分销", language: "zh", content: "", updatedAt: new Date().toISOString() },
          ] }
          : u.includes("/me") ? { email: "a@b.com", balanceCents: 2850 }
          : u.includes("/jobs") ? { jobs: [] }
          : { ledger: [] },
      };
    }));

  it("有术语库也不替用户挑一本，默认「不使用」", async () => {
    withBooks();
    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
    expect(await screen.findByText(/^不使用$|^Don't use$/)).toBeInTheDocument();
    // 反面：任何一本库的名字都不该出现在那个位置（出现＝我们替他选了）
    expect(screen.queryByText("光伏采购访谈")).toBeNull();
  });

  it("上次显式选过的那本仍然记住（记住选择 ≠ 替他选）", async () => {
    localStorage.setItem("glossaryId", "g2");
    withBooks();
    wrap(<AppShell me={{ email: "a@b.com", balanceCents: 2850 }} onLogout={vi.fn()} />);
    expect(await screen.findByText("医疗器械分销")).toBeInTheDocument();
    localStorage.removeItem("glossaryId");
  });
});

describe("提交后的强制重拉", () => {
  // 源码级判据，例外说明：这一步要跑到才需要「真上传一个文件 + 后端真给出 jobId」，
  // 在 jsdom 里搭这套桩比它守的那行代码还长。而它防的是**重复付费**（提交后列表还是
  // 提交前的快照 → 看着像没交上 → 再交一遍），代价是真金白银，不能没有守卫。
  // 判据钉的是「刷新挂在 jobId 上」——挂在点击或页面切换上都会退回旧行为：
  // 那两个时刻上传还没传完，拉回来的仍是旧快照。
  it("账户数据的刷新挂在 flow.jobId 上，不是挂在点击或页面切换上", async () => {
    const src = await import("fs").then((fs) => fs.readFileSync("src/AppShell.tsx", "utf8"));
    const eff = src.match(/useEffect\(\(\) => \{\s*if \(!flow\.jobId\) return;\s*refreshAccount\(\);[\s\S]*?\}, \[flow\.jobId\]\);/);
    expect(eff, "AppShell 里没有「拿到 jobId 就重拉账户数据」这条 effect").not.toBeNull();
  });
});

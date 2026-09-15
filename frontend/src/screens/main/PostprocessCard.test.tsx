import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PostprocessCard, ppStepClimb } from "./PostprocessCard";
import { UILangProvider } from "../../lib/i18n";
import type { PostprocessStatus } from "../../lib/api";
import { downloadUrl } from "../../lib/download";

vi.mock("../../lib/download", () => ({ downloadUrl: vi.fn(), downloadText: vi.fn() }));

const LISTS = [{ id: "l1", name: "通用保留清单", content: "张三\n李四", updatedAt: "2026-07-18T00:00:00Z" }];

type Call = { url: string; method: string; body?: unknown };
let calls: Call[] = [];
const json = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });

function mockFetch(opts: { status?: PostprocessStatus | null; lists?: typeof LISTS; afterStart?: PostprocessStatus } = {}) {
  calls = [];
  let started = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url === "/api/jobs/j1/postprocess" && method === "GET") {
      if (started && opts.afterStart) return json(opts.afterStart);
      if (opts.status == null) return json({ detail: "no task" }, 404);
      return json(opts.status);
    }
    if (url === "/api/jobs/j1/postprocess" && method === "POST") { started = true; return json({ ok: true }); }
    if (url === "/api/postprocess/redact-lists") return json({ lists: opts.lists ?? LISTS });
    return json({ detail: "not found" }, 404);
  }));
}

const wrap = (p: Partial<React.ComponentProps<typeof PostprocessCard>> = {}) =>
  render(
    <UILangProvider>
      <PostprocessCard jobId="j1" locked={false} remaining={0} {...p} />
    </UILangProvider>,
  );

const RUNNING: PostprocessStatus = {
  status: "running", steps: ["narrate", "redact"], stepIndex: 2, totalSteps: 2,
  currentStep: "redact", products: [], qcFixCount: 0, hasQcReport: false,
  priceCents: 0, listName: "通用保留清单", updatedAt: "2026-07-23T10:41:00Z",
};

beforeEach(() => mockFetch());
afterEach(() => vi.unstubAllGlobals());

describe("PostprocessCard 五态", () => {
  it("锁定态：pill「还差 N 处确认」，无按钮无价格，不打后处理接口", () => {
    wrap({ locked: true, remaining: 3 });
    expect(screen.getByText(/还差 3 处确认|3 left to confirm/)).toBeInTheDocument();
    expect(screen.queryByText(/开始加工|Start processing/)).toBeNull();
    expect(screen.queryByText("免费")).toBeNull();
    expect(calls.filter((c) => c.url.includes("/postprocess"))).toEqual([]);
  });

  it("可发起态：默认只勾视角转换，顺序说明随勾选实时变化，全不选 CTA 禁用", async () => {
    wrap();
    expect(await screen.findByText(/只跑「视角转换」这一步|Runs just "Narrative"/)).toBeInTheDocument();
    // 勾脱敏 → 多选列顺序
    await userEvent.click(screen.getByRole("checkbox", { name: /脱敏|Redact/ }));
    expect(screen.getByText(/按 视角转换 → 脱敏 顺序执行|Narrative → Redact/)).toBeInTheDocument();
    // 全不选 → 提示 + CTA 禁用文案
    await userEvent.click(screen.getByRole("checkbox", { name: /脱敏|Redact/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /视角转换|Narrative/ }));
    expect(screen.getByText(/至少选一个处理|Pick at least one step/)).toBeInTheDocument();
    const cta = screen.getByRole("button", { name: /先选一个处理|Pick a step first/ });
    expect((cta as HTMLButtonElement).disabled).toBe(true);
  });

  it("归类已下架：可发起态不再出现这一步", async () => {
    wrap();
    await screen.findByText(/只跑|Runs just/);
    expect(screen.queryByRole("checkbox", { name: /归类|Categorize/ })).toBeNull();
    expect(screen.queryByText(/先建归类方案|Build a scheme first/)).toBeNull();
  });

  // 本机版（与线上不同）：线上这里测价格行（按步骤×时长预估、失败不计费、按小时的加价率），本机不收费。
  it("本机不显示价格与「失败不计费」", async () => {
    wrap({ durationSec: 3600 });
    await screen.findByText(/只跑|Runs just/);
    expect(document.body.textContent || "").not.toMatch(/\$\d|≈|不计费|No charge/);
  });

  it("脱敏下拉：首项固定「不使用清单 · 智能识别」+ 条数 + 选中 ✓ + 点外关闭", async () => {
    wrap();
    await screen.findByText(/只跑|Runs just/);
    await userEvent.click(screen.getByRole("checkbox", { name: /脱敏|Redact/ }));
    await userEvent.click(screen.getByRole("button", { name: /不使用清单|No list/ }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText(/脱敏保留词清单 · 1|Keep lists · 1/)).toBeInTheDocument();
    const opts = within(menu).getAllByRole("menuitem").map((el) => el.textContent);
    expect(opts[0]).toContain("不使用清单");
    expect(opts[0]).toContain("✓");            // 默认不选清单
    expect(opts[1]).toContain("通用保留清单");
    expect(opts[1]).toContain("2 条");
    // 点外关闭（jsdom 不模拟层叠，点透明遮罩本身）
    fireEvent.click(document.querySelector('div[style*="position: fixed"]') as HTMLElement);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("开始加工：POST 固定顺序 steps，随后进入进行中态", async () => {
    mockFetch({ afterStart: RUNNING });
    wrap();
    await screen.findByText(/只跑|Runs just/);
    await userEvent.click(screen.getByRole("checkbox", { name: /脱敏|Redact/ }));
    await userEvent.click(screen.getByRole("button", { name: /开始加工|Start processing/ }));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/jobs/j1/postprocess");
    // 提交必须按固定顺序 narrate→redact，且不再带 profileId
    // uiLang = 发起这一刻的界面语言：质检报告里我们写的字跟它走（产物正文跟录音语种走）。
    // 判据取**默认渲染的那门语言**，不是「有这个键」——传了个空串也能过「有键」。
    expect(post?.body).toEqual({ steps: ["narrate", "redact"], redactListId: null, uiLang: "zh" });
    expect(await screen.findByText(/加工中 · 第 2 \/ 2 步|Processing · step 2 \/ 2/)).toBeInTheDocument();
  });

  it("进行中：步行三态圆点 + 仅当前步有进度条 + 可离开 + footer", async () => {
    mockFetch({ status: RUNNING });
    wrap();
    expect(await screen.findByText(/加工中 · 第 2 \/ 2 步|Processing · step 2 \/ 2/)).toBeInTheDocument();
    expect(screen.getByText(/可离开|safe to leave/)).toBeInTheDocument();
    expect(screen.getByText(/脱敏 · 通用保留清单|Redact · 通用保留清单/)).toBeInTheDocument();
    expect(screen.getByText(/通常 5–30 分钟|Usually 5–30 min/)).toBeInTheDocument();
    // 进行中不显示价格、无取消按钮
    expect(screen.queryByText(/免费|Free/)).toBeNull();
    expect(screen.queryByText(/取消|Cancel/)).toBeNull();
  });

  it("完成态：产物行 + 行尾拆分导出（.docx ↓ / ▾ 菜单 .md .txt）+ 质检行 + 查看报告", async () => {
    mockFetch({
      status: {
        ...RUNNING, status: "done", stepIndex: 2, currentStep: null,
        products: [{ kind: "narrate", name: "叙述稿" }, { kind: "redact", name: "脱敏稿" }],
        qcFixCount: 8, hasQcReport: true, listName: "通用保留清单",
      },
    });
    wrap();
    expect(await screen.findByText(/加工完成|Processing done/)).toBeInTheDocument();
    expect(screen.getByText(/叙述稿|^Narrative draft$/)).toBeInTheDocument();
    expect(screen.getByText(/脱敏稿|Redacted transcript/)).toBeInTheDocument();
    // 脱敏稿行附清单名
    expect(screen.getByText("通用保留清单")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /\.docx ↓/ })).toHaveLength(2);
    // ▾ 菜单：.md / .txt
    await userEvent.click(screen.getAllByRole("button", { name: /更多格式|More formats/ })[0]);
    expect(screen.getByRole("menuitem", { name: ".md" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: ".txt" })).toBeInTheDocument();
    // 质检行 + 报告链接
    expect(screen.getByText(/质检已修复 8 处|QC fixed 8 spots/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /查看报告|View report/ })).toBeInTheDocument();
  });

  // ══ 「未知」不是「没有」（2026-08-22 Duner 连拍四张实见）══
  // 复核没清零时曾 setSt(null)，而 null 的意思是「问过了，没有加工任务」。解锁那一刻
  // st 还挂着这个假的 null，于是**一份早就加工完的稿子**先摆出可发起态（选处理 / 预估金额 /
  // 开始加工），三秒后才翻成「加工完成」。它不只是闪一下——它在诱导用户再花一次钱。
  it("解锁那一刻不许摆出可发起态：状态没取回来就只摆骨架", async () => {
    let release: ((v: unknown) => void) | null = null;
    const pending = new Promise((r) => { release = r; });
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url === "/api/jobs/j1/postprocess" && method === "GET") {
        await pending;                       // 状态一直不回来
        return json({ ...RUNNING, status: "done", stepIndex: 2, currentStep: null,
                      products: [{ kind: "narrate", name: "叙述稿" }], qcFixCount: 0, hasQcReport: false });
      }
      if (url === "/api/postprocess/redact-lists") return json({ lists: LISTS });
      return json({ detail: "not found" }, 404);
    }));

    // 先锁定（复核没清零）→ 再解锁，正是用户打开一份复核完的稿子时走的那条路
    const { rerender } = render(<UILangProvider><PostprocessCard jobId="j1" locked remaining={17} /></UILangProvider>);
    expect(await screen.findByText(/还差 17 处确认|17 left to confirm/)).toBeInTheDocument();
    rerender(<UILangProvider><PostprocessCard jobId="j1" locked={false} remaining={0} /></UILangProvider>);

    // 状态还没回来：一个字都不许承诺——尤其不许出现「开始加工」
    await screen.findByText(/后处理|Post-processing/);
    expect(screen.queryByRole("button", { name: /开始加工|Start/ })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();

    release!(null);
    expect(await screen.findByText(/加工完成|Processing done/)).toBeInTheDocument();
  });

  // ══ 下载行与改口（2026-08-22）══
  // 只有一份脱敏稿，它永远等于你此刻在清单里看到的样子（后端取稿时应用改口）。
  // 所以这里不新增文件、不改文件名——要做的只是把「下的这份带不带你的修订」说出来。
  const DONE_REDACT = {
    ...RUNNING, status: "done" as const, stepIndex: 2, currentStep: null,
    products: [{ kind: "redact" as const, name: "脱敏稿" }],
    qcFixCount: 0, hasQcReport: true, listName: "通用保留清单",
  };
  const CHANGES = {
    changes: [{
      kind: "geo", from: "山西", to: "本省", count: 1,
      spots: [{ line: 0, idx: 0, seg: 0,
                before: { text: "被访者：我们在山西做了十年", s: 7, e: 9 },
                after: { text: "被访者：我们在本省做了十年", s: 7, e: 9 },
                prev: null, next: null }],
    }],
    overrides: [] as unknown[],
  };
  /** 完成态 + 一条脱敏改动；putFails=true 时保存永远失败。 */
  const mockRedact = (putFails = false) => {
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      if (url === "/api/jobs/j1/postprocess" && method === "GET") return json(DONE_REDACT);
      if (url === "/api/jobs/j1/postprocess/changes") return json(CHANGES);
      if (url.includes("/redact/overrides")) return json(putFails ? { detail: "boom" } : { ok: true }, putFails ? 500 : 200);
      if (url === "/api/postprocess/redact-lists") return json({ lists: LISTS });
      return json({ detail: "not found" }, 404);
    }));
  };
  const keepOriginal = async () => {
    await userEvent.click(await screen.findByText(/脱敏了 1 处|1 spot redacted/));
    await userEvent.click(screen.getByText(/山西/));
    await userEvent.click(screen.getByText(/保留原词|Keep original/));
  };

  // 完成态必须**一次画到位**：脱敏改动清单原来由 RedactChanges 自己取，于是先出
  // 「加工完成 + 两个产物行」，几百毫秒后才冒出「脱敏了 N 处」「已按你的 N 处修订」
  // 和质检那句说明（2026-08-22 Duner 实见，连拍两张）。
  it("一进来就是完成态：清单没到齐之前只摆骨架，不先画一半", async () => {
    let release: ((v: unknown) => void) | null = null;
    const pending = new Promise((r) => { release = r; });
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/jobs/j1/postprocess") return json(DONE_REDACT);
      if (url === "/api/jobs/j1/postprocess/changes") { await pending; return json(CHANGES); }
      return json({ detail: "not found" }, 404);
    }));
    wrap();
    // 状态早就回来了，但清单还没到：不许先把完成态画出来
    await screen.findByText(/后处理|Post-processing/);
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByText(/加工完成|Processing done/)).toBeNull();
    expect(screen.queryByText(/脱敏稿|Redacted transcript/)).toBeNull();

    release!(null);
    // 齐了之后一次全有：产物行与改动清单同时在场
    expect(await screen.findByText(/加工完成|Processing done/)).toBeInTheDocument();
    expect(screen.getByText(/脱敏稿|Redacted transcript/)).toBeInTheDocument();
    expect(screen.getByText(/脱敏了 1 处|1 spot redacted/)).toBeInTheDocument();
  });

  // 反面：跑完的那一刻**不等**——用户正盯着进度条，从「进行中」退回骨架比多冒一行更难看；
  // 那时多出来的是补充，不是纠正。
  it("跑完的那一刻不退回骨架", async () => {
    let done = false;
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/jobs/j1/postprocess") return json(done ? DONE_REDACT : RUNNING);
      if (url === "/api/jobs/j1/postprocess/changes") return new Promise(() => {});   // 永不返回
      return json({ detail: "not found" }, 404);
    }));
    wrap();
    expect(await screen.findByText(/加工中|Processing/)).toBeInTheDocument();
    done = true;
    // 轮询 3s 一次；这里直接等它翻到完成态（清单永远不来也不许卡住）
    expect(await screen.findByText(/加工完成|Processing done/, {}, { timeout: 5000 })).toBeInTheDocument();
  });

  // 外层（AppShell）与稿子并行取加工状态：给了终态就别再打一次同样的接口。
  // 此前这一段是串在稿子后面跑的——「点开一篇转录到右栏安定」5.5 秒里有 1.8 秒是它在排队。
  it("外层已经给了终态：不再自己打接口", async () => {
    mockRedact();
    wrap({ initialStatus: DONE_REDACT });
    expect(await screen.findByText(/加工完成|Processing done/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/api/jobs/j1/postprocess")).toEqual([]);
  });

  // 「还没问过」和「问过了、当时没跑完」不是一回事。少了这道闸，每打开一篇早就加工完的
  // 转录都会被当成「刚跑完」，白跑一次全账户刷新（2026-08-22 巡检实测）。
  it("一进来就是完成态：不当成「刚跑完」去通知外层刷余额", async () => {
    mockRedact();
    const onFinished = vi.fn();
    wrap({ onFinished });
    expect(await screen.findByText(/加工完成|Processing done/)).toBeInTheDocument();
    expect(onFinished).not.toHaveBeenCalled();
  });

  it("真的从「进行中」翻到「完成」时，照常通知外层刷余额", async () => {
    let done = false;
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/jobs/j1/postprocess") return json(done ? DONE_REDACT : RUNNING);
      if (url === "/api/jobs/j1/postprocess/changes") return json({ changes: [], overrides: [] });
      return json({ detail: "not found" }, 404);
    }));
    const onFinished = vi.fn();
    wrap({ onFinished });
    expect(await screen.findByText(/加工中|Processing/)).toBeInTheDocument();
    done = true;
    expect(await screen.findByText(/加工完成|Processing done/, {}, { timeout: 5000 })).toBeInTheDocument();
    expect(onFinished).toHaveBeenCalled();
  });

  it("从这张卡点「开始加工」，跑完照样通知外层刷余额", async () => {
    // 这条守的是 2026-08-26 之前那个真 bug：doStart 里复制了一份 setInterval，
    // 而复制品漏了主轮询器的两件事——不调 onFinished（加工完成即扣款，**用户刚花完钱
    // 看到的还是旧余额**）、到终态也不停（每 3 秒空打后端，永远不停）。
    // 造回 bug 验过：把 setRestart 换回自建定时器，本条即红。
    // 走的是最常见那条路：外层已经问过「这单没有加工任务」(initialStatus=null)，
    // 主轮询器因此早退——正是这条早退让人想在 doStart 里另开一个。
    let done = false;
    calls = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url === "/api/jobs/j1/postprocess" && method === "POST") return json({ ok: true });
      if (url === "/api/jobs/j1/postprocess") return json(done ? DONE_REDACT : RUNNING);
      if (url === "/api/jobs/j1/postprocess/changes") return json({ changes: [], overrides: [] });
      if (url === "/api/postprocess/redact-lists") return json({ lists: LISTS });
      return json({ detail: "not found" }, 404);
    }));
    const onFinished = vi.fn();
    wrap({ initialStatus: null, onFinished });
    await userEvent.click(await screen.findByRole("button", { name: /开始加工|Start processing/ }));
    expect(await screen.findByText(/加工中|Processing/)).toBeInTheDocument();
    done = true;
    expect(await screen.findByText(/加工完成|Processing done/, {}, { timeout: 6000 })).toBeInTheDocument();
    expect(onFinished).toHaveBeenCalled();
  });

  it("终态之后不再空打后端：轮询要停下来", async () => {
    // 复制出来的那份定时器不认终态，只要卡片还挂着就每 3 秒打一次。
    vi.useFakeTimers();
    try {
      mockFetch({ status: DONE_REDACT });
      wrap({ initialStatus: DONE_REDACT });
      await vi.advanceTimersByTimeAsync(100);
      const n = calls.filter((c) => c.url === "/api/jobs/j1/postprocess").length;
      await vi.advanceTimersByTimeAsync(12000);   // 四个轮询周期
      expect(calls.filter((c) => c.url === "/api/jobs/j1/postprocess").length).toBe(n);
    } finally {
      vi.useRealTimers();
    }
  });

  it("改口存上了：下载行说清这一份含你的修订，按钮照常可点", async () => {
    mockRedact();
    wrap();
    await keepOriginal();
    expect(await screen.findByText(/已按你的 1 处修订|includes your 1 change/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /\.docx ↓/ })).toBeEnabled();
    // 文件名与下载路径都不变——只有一份脱敏稿，不给第二个入口
    expect(screen.queryByText(/已修订|revised/i)).not.toBe(screen.getByText(/脱敏稿|Redacted transcript/));
  });

  // 最坏的一种错：屏幕上写着「已保留原词」，服务器上什么都没有，
  // 而你下到一份**自以为改过**的稿。宁可不给下，也不给一份骗人的稿。
  it("改口没存上：下载禁掉 + 点名几处没存上 + 给重试", async () => {
    mockRedact(true);
    wrap();
    await keepOriginal();
    expect(await screen.findByText(/1 处没存上|1 unsaved/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /\.docx ↓/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /更多格式|More formats/ })).toBeDisabled();
    // 重试打的是同一个接口（第一次失败 + 重试 = 两次 PUT）
    await userEvent.click(screen.getByRole("button", { name: /重试|Retry/ }));
    expect(calls.filter((c) => c.url.includes("/redact/overrides")).length).toBe(2);
  });

  it("没有改口时：下载行一个字不多说", async () => {
    mockRedact();
    wrap();
    expect(await screen.findByText(/脱敏稿|Redacted transcript/)).toBeInTheDocument();
    expect(screen.queryByText(/已按你的|includes your/)).toBeNull();
    expect(screen.getByRole("button", { name: /\.docx ↓/ })).toBeEnabled();
  });

  // 报告是**脱敏当时**的留档，故意不跟着改口变（改了就没有原始记录了）。
  // 那样报告与稿子会对不上，所以只在真有改口时说明一句。
  it("质检报告说明只在真有改口时出现", async () => {
    mockRedact();
    wrap();
    await screen.findByText(/脱敏稿|Redacted transcript/);
    expect(screen.queryByText(/不含你之后的修订|not the changes you made/)).toBeNull();
    await keepOriginal();
    expect(await screen.findByText(/不含你之后的修订|not the changes you made/)).toBeInTheDocument();
  });

  it("完成态：无报告时只显示已修复行，不给「查看报告」", async () => {
    mockFetch({ status: { ...RUNNING, status: "done", products: [{ kind: "narrate", name: "叙述稿" }], qcFixCount: 2, hasQcReport: false } });
    wrap();
    expect(await screen.findByText(/质检已修复 2 处|QC fixed 2 spots/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看报告|View report/ })).toBeNull();
  });

  it("失败态：pill「失败」+ 点名失败步；改选处理回可发起并预选上次组合", async () => {
    mockFetch({
      status: { ...RUNNING, status: "failed", failedStep: "redact", steps: ["narrate", "redact"] },
      afterStart: RUNNING,
    });
    wrap();
    expect(await screen.findByText(/后处理没跑完|didn't finish/)).toBeInTheDocument();
    expect(screen.getByText(/^失败$|^Failed$/)).toBeInTheDocument();
    expect(screen.queryByText(/计费|billed/)).toBeNull();   // 本机版不收费，不提计费
    expect(screen.getByText(/「脱敏」这一步中断了|"Redact" step broke off/)).toBeInTheDocument();
    // 改选处理 → 可发起态，narrate + redact 预选
    await userEvent.click(screen.getByRole("button", { name: /改选处理|Change steps/ }));
    expect(await screen.findByText(/开始加工|Start processing/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /视角转换|Narrative/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("checkbox", { name: /脱敏|Redact/ }).getAttribute("aria-checked")).toBe("true");
  });

  it("失败态重试：按上次组合直接重发", async () => {
    mockFetch({
      status: { ...RUNNING, status: "failed", failedStep: "redact", steps: ["narrate", "redact"] },
      afterStart: RUNNING,
    });
    wrap();
    await screen.findByText(/后处理没跑完|didn't finish/);
    await userEvent.click(screen.getByRole("button", { name: /^重试$|^Retry$/ }));
    const post = await vi.waitFor(() => {
      const p = calls.find((c) => c.method === "POST");
      expect(p).toBeTruthy();
      return p;
    });
    expect((post?.body as { steps: string[] }).steps).toEqual(["narrate", "redact"]);
    expect(post?.body).not.toHaveProperty("profileId");
  });
});

// 归类下架（2026-08-17）前发起的任务还带着 categorize。展示得出来、重试不炸，是这一组守的东西。
describe("存量任务（含已下架的归类步）", () => {
  const LEGACY: PostprocessStatus = {
    ...RUNNING, steps: ["narrate", "categorize", "redact"], totalSteps: 3,
    profileName: "高管客户", listName: "通用保留清单",
  };

  it("完成态：归类纪要照样列出来并可下载，附当时的方案名", async () => {
    mockFetch({
      status: {
        ...LEGACY, status: "done", stepIndex: 3, currentStep: null,
        products: [{ kind: "narrate", name: "叙述稿" }, { kind: "categorize", name: "归类纪要" }, { kind: "redact", name: "脱敏稿" }],
        qcFixCount: 0, hasQcReport: false,
      },
    });
    wrap();
    expect(await screen.findByText(/加工完成|Processing done/)).toBeInTheDocument();
    expect(screen.getByText(/归类纪要|Categorized minutes/)).toBeInTheDocument();
    expect(screen.getByText("高管客户")).toBeInTheDocument();       // 不能退成裸英文 kind
    expect(screen.getAllByRole("button", { name: /\.docx ↓/ })).toHaveLength(3);
  });

  it("失败重试：把已下架的 categorize 滤掉再重发（原样发会被后端 422）", async () => {
    mockFetch({
      status: { ...LEGACY, status: "failed", failedStep: "categorize" },
      afterStart: RUNNING,
    });
    wrap();
    await screen.findByText(/后处理没跑完|didn't finish/);
    await userEvent.click(screen.getByRole("button", { name: /^重试$|^Retry$/ }));
    const post = await vi.waitFor(() => {
      const p = calls.find((c) => c.method === "POST");
      expect(p).toBeTruthy();
      return p;
    });
    expect((post?.body as { steps: string[] }).steps).toEqual(["narrate", "redact"]);
  });

  it("改选处理：勾选态里不会凭空多出归类那一项", async () => {
    mockFetch({ status: { ...LEGACY, status: "failed", failedStep: "categorize" } });
    wrap();
    await screen.findByText(/后处理没跑完|didn't finish/);
    await userEvent.click(screen.getByRole("button", { name: /改选处理|Change steps/ }));
    expect(await screen.findByText(/开始加工|Start processing/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /归类|Categorize/ })).toBeNull();
    expect(screen.getByRole("checkbox", { name: /视角转换|Narrative/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("checkbox", { name: /脱敏|Redact/ }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("ppStepClimb（步内匀速模拟）", () => {
  it("按估算时长匀速爬升，封顶 99 不假装完成", () => {
    expect(ppStepClimb(0)).toBe(0);
    expect(ppStepClimb(300)).toBe(49);   // 600s 估算的一半
    expect(ppStepClimb(600)).toBe(99);
    expect(ppStepClimb(6000)).toBe(99);  // 封顶
  });
});


// ── 下载文件名（2026-08-28）────────────────────────────────────────────
// 改动前产物一律叫「脱敏稿.md」「叙述稿.md」：同时加工几单，下下来的文件除了浏览器
// 自己接的 (1)(2) 之外一模一样，分不出哪份属于哪一单。判据是「文件名里有这一单的音频名」。
describe("产物下载的文件名", () => {
  const DONE: PostprocessStatus = {
    ...RUNNING, status: "done", stepIndex: 2, currentStep: null,
    products: [{ kind: "narrate", name: "叙述稿" }, { kind: "redact", name: "脱敏稿" }],
    qcFixCount: 8, hasQcReport: true, listName: "通用保留清单",
  };
  const nameOf = (url: string) => decodeURIComponent(new URL(url, "http://x").searchParams.get("name") ?? "");

  beforeEach(() => { vi.mocked(downloadUrl).mockClear(); mockFetch({ status: DONE }); });

  it("产物名 =「音频名-这一步的名字」", async () => {
    wrap({ stem: "ru_interview" });
    await screen.findByText(/加工完成|Processing done/);
    await userEvent.click(screen.getAllByRole("button", { name: /\.docx ↓/ })[1]);   // 第二个产物 = 脱敏
    const url = vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string;
    expect(nameOf(url)).toBe("ru_interview-脱敏稿");
  });

  it("质检报告同样带音频名", async () => {
    wrap({ stem: "ru_interview" });
    await screen.findByText(/加工完成|Processing done/);
    await userEvent.click(screen.getByRole("button", { name: /查看报告|View report/ }));
    const url = vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string;
    expect(nameOf(url)).toBe("ru_interview-质检报告");
  });

  it("拿不到音频名时不编一个，退回后端缺省名", async () => {
    wrap();   // 不给 stem
    await screen.findByText(/加工完成|Processing done/);
    await userEvent.click(screen.getAllByRole("button", { name: /\.docx ↓/ })[1]);
    const url = vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string;
    expect(url).not.toContain("name=");
  });
});

// ── 下载文件名的后缀只有中/英两种（2026-08-29 Duner 定）──
// ⚠️ 判据必须**取一门第三语言**：只测中英的话，「后缀走 L」和「后缀走 ZhEn」结果完全一样，
//    这条测试就永远是绿的、什么也证明不了（对照本里 Narrative draft 确实译成了德语）。
describe("文件名后缀跟界面语言走（8 门）", () => {
  const DONE2: PostprocessStatus = {
    ...RUNNING, status: "done", stepIndex: 2, currentStep: null,
    products: [{ kind: "narrate", name: "叙述稿" }, { kind: "redact", name: "脱敏稿" }],
    qcFixCount: 8, hasQcReport: true, listName: "通用保留清单",
  };
  const nameOf = (url: string) => decodeURIComponent(new URL(url, "http://x").searchParams.get("name") ?? "");
  beforeEach(() => { vi.mocked(downloadUrl).mockClear(); mockFetch({ status: DONE2 }); });

  // 2026-08-30：后缀原来是「中/英二选一」，理由是「德语界面下到 xx-Erzählfassung.md、
  // 而文件里写的是 Interviewer，同一份稿子两套语言」。说话人标签扩到 8 门之后那个理由
  // 不成立了，规则统一成「我们写的字都跟界面语言走」。判据故意取**德语的译文本身**，
  // 而不是「不等于英文」——后者在回落英文时也能过。
  it("德语界面：文件名后缀是德语，跟界面上的名字一致", async () => {
    render(
      <UILangProvider initial="de">
        <PostprocessCard jobId="j1" locked={false} remaining={0} stem="AR_supply" />
      </UILangProvider>,
    );
    await screen.findByText(/Nachbearbeitung fertig/);
    await userEvent.click(screen.getAllByRole("button", { name: /\.docx ↓/ })[0]);
    expect(nameOf(vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string)).toBe("AR_supply-Erzählfassung");
  });

  it("德语界面发起：POST 带的是 de，不是写死的 zh", async () => {
    mockFetch({ afterStart: RUNNING });
    render(
      <UILangProvider initial="de">
        <PostprocessCard jobId="j1" locked={false} remaining={0} stem="AR_supply" />
      </UILangProvider>,
    );
    await screen.findByText(/Frage-Antwort → Ich-Form/);
    await userEvent.click(screen.getByRole("button", { name: /Nachbearbeitung starten|Start processing/ }));
    const post = calls.find((c) => c.method === "POST");
    expect((post?.body as { uiLang?: string })?.uiLang).toBe("de");
  });

  it("日语界面：质检报告的文件名也跟着翻", async () => {
    render(
      <UILangProvider initial="ja">
        <PostprocessCard jobId="j1" locked={false} remaining={0} stem="AR_supply" />
      </UILangProvider>,
    );
    await screen.findByText(/品質チェック報告|レポートを見る/);
    await userEvent.click(screen.getByRole("button", { name: /レポートを見る|View report/ }));
    expect(nameOf(vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string)).toContain("品質チェック報告");
  });

  it("中文界面仍然是中文后缀", async () => {
    render(
      <UILangProvider initial="zh">
        <PostprocessCard jobId="j1" locked={false} remaining={0} stem="AR_supply" />
      </UILangProvider>,
    );
    await screen.findByText(/加工完成/);
    await userEvent.click(screen.getAllByRole("button", { name: /\.docx ↓/ })[0]);
    expect(nameOf(vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string)).toBe("AR_supply-叙述稿");
  });
});

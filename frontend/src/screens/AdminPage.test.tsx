import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { getAdminOverview, getAdminBalances, getAdminExpiries, getAdminWorkflow, getAdminHealth,
         getAdminAlerts, getAdminUsers, getAdminUserDetail } from "../lib/api";

// setSpy 经 vi.hoisted 提升，工厂与用例都能访问；fixtures 放工厂内（避免 vi.mock 提升后引用未初始化变量）。
const setSpy = vi.hoisted(() => vi.fn());
const refundSpy = vi.hoisted(() => vi.fn());
const p3HealthSpy = vi.hoisted(() => vi.fn());
const p3ProbeSpy = vi.hoisted(() => vi.fn());
const p3CfgSpy = vi.hoisted(() => vi.fn());
const p3CfgSaveSpy = vi.hoisted(() => vi.fn());
const alertHandledSpy = vi.hoisted(() => vi.fn());
const growthReportSpy = vi.hoisted(() => vi.fn());
const growthSpy = vi.hoisted(() => vi.fn());
const adjustSpy = vi.hoisted(() => vi.fn());
const resetSpy = vi.hoisted(() => vi.fn());
const simulateSpy = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", () => {
  // 两类 × 三付费模式 × 三单位（讯飞小时、国际引擎美元）；显示序 = 数组序（2026-07-12 扩语言后 10 卡阵容）
  const fixtures = [
    { vendor: "DeepSeek", label: "DeepSeek", category: "infra", payMode: "prepaid_manual", unit: "cny", sortOrder: 1, amountCny: 8.84, thresholdCny: 20, source: "api", note: null, updatedAt: "06-17 07:00", low: true, noThreshold: false },
    { vendor: "博查", label: "博查 Bocha", category: "infra", payMode: "prepaid_manual", unit: "cny", sortOrder: 2, amountCny: 9.57, thresholdCny: 20, source: "api", note: null, updatedAt: "06-17 07:00", low: true, noThreshold: false },
    { vendor: "G25F", label: "Gemini（主轨备份候选）", category: "infra", payMode: "postpaid", unit: "cny", sortOrder: 3, amountCny: null, thresholdCny: null, source: "manual", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: false },
    { vendor: "ELV", label: "ElevenLabs-Scribe v2（主轨）", category: "asr", payMode: "postpaid", unit: "usd", sortOrder: 10, amountCny: null, thresholdCny: null, source: "manual", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: false },
    { vendor: "豆包", label: "豆包-录音文件识别模型 2.0", category: "asr", payMode: "prepaid_manual", unit: "cny", sortOrder: 11, amountCny: 37.8, thresholdCny: null, source: "api", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: true },
    { vendor: "讯飞", label: "讯飞-录音文件转写大模型", category: "asr", payMode: "prepaid_manual", unit: "hours", sortOrder: 12, amountCny: 40.5597222222222, thresholdCny: null, source: "manual", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: true },
    { vendor: "FunASR", label: "阿里-录音文件识别（Fun-ASR）", category: "asr", payMode: "prepaid_auto", unit: "cny", sortOrder: 13, amountCny: 15.53, thresholdCny: null, source: "api", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: false },
    { vendor: "AAI", label: "AssemblyAI-universal-2", category: "asr", payMode: "prepaid_manual", unit: "usd", sortOrder: 14, amountCny: 12.5, thresholdCny: 5, source: "manual", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: false },
    { vendor: "SPM", label: "Speechmatics-enhanced", category: "asr", payMode: "postpaid", unit: "usd", sortOrder: 15, amountCny: null, thresholdCny: null, source: "manual", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: false },
    { vendor: "SNX", label: "Soniox-stt-async-v5", category: "asr", payMode: "prepaid_manual", unit: "usd", sortOrder: 16, amountCny: null, thresholdCny: null, source: "manual", note: null, updatedAt: "06-17 07:00", low: false, noThreshold: true },
  ];
  return {
    getAdminOverview: vi.fn().mockResolvedValue({ jobs: [], failures: [], recent: [], summary: { running: 0, queued: 0, doneToday: 0, failedToday: 0 } }),
    getAdminBalances: vi.fn().mockResolvedValue(fixtures),
    setAdminBalance: (...a: unknown[]) => setSpy(...a),
    refreshAllBalances: vi.fn().mockResolvedValue(undefined),
    // 充值退款：一笔真金白银（可退 $4）、一笔赠送、一笔超窗口
    getAdminTopups: vi.fn().mockResolvedValue({
      balanceCents: 400,
      topups: [
        { id: 7, amountCents: 1000, refundedCents: 600, source: "stripe", createdAt: "2026-07-27T00:00:00Z", ageDays: 1.0, isBonus: false, refundableCents: 400 },
        { id: 6, amountCents: 3000, refundedCents: 0, source: "bonus", createdAt: "2026-07-20T00:00:00Z", ageDays: 8.0, isBonus: true, refundableCents: 0 },
        { id: 5, amountCents: 2000, refundedCents: 0, source: "stripe", createdAt: "2026-07-01T00:00:00Z", ageDays: 27.0, isBonus: false, refundableCents: 0 },
      ],
    }),
    startAdminRefund: (...a: unknown[]) => refundSpy(...a),
    getAdminP3Health: (...a: unknown[]) => p3HealthSpy(...a),
    probeP3: (...a: unknown[]) => p3ProbeSpy(...a),
    getAdminP3Config: (...a: unknown[]) => p3CfgSpy(...a),
    saveAdminP3Config: (...a: unknown[]) => p3CfgSaveSpy(...a),
    getAdminExpiries: vi.fn().mockResolvedValue({ warnDays: 60, items: [] }),
    getAdminAlerts: vi.fn().mockResolvedValue({ retentionDays: 180, items: [] }),
    getAdminUsers: vi.fn().mockResolvedValue({ adjustMaxCents: 5000, items: [] }),
    getAdminUserDetail: vi.fn(),
    getAdminGrowth: (...a: unknown[]) => growthSpy(...a),
    adjustUserBalance: (...a: unknown[]) => adjustSpy(...a),
    resetUserAccount: (...a: unknown[]) => resetSpy(...a),
    simulateUsage: (...a: unknown[]) => simulateSpy(...a),
    markAlertHandled: (...a: unknown[]) => alertHandledSpy(...a),
    sendGrowthReport: (...a: unknown[]) => growthReportSpy(...a),
    getAdminHealth: vi.fn().mockResolvedValue({
      windowHours: 24, baselineHours: 720, p1: {}, p1Baseline: {},
      p3: { tiers: {}, total: 0, degradedRatio: null, known: 0 },
      pp: {}, glossary: {},
      ops: { loginSends24h: 0, loginSendCapHint: 100, watchdogRequeues: 0,
             gateSlots: { claude: 0 }, gateLimit: 5, machinesRunning: 0,
             recent60: { done: 0, failed: 0 } },
      alerts: { engines: [], degrade: null },
    }),
    getAdminWorkflow: vi.fn().mockResolvedValue({
      promptScope: "dispatcher", imageTag: "task-v23", langPlans: [],
      prompts: { "multi-asr-merge": { path: "x/SKILL.md", lines: 132, sha: "abcd1234" } },
      params: {},
    }),
    getAdminPrompt: vi.fn().mockResolvedValue({ id: "multi-asr-merge", path: "x", sha: "abcd1234", text: "规则正文" }),
  };
});

import { AdminPage } from "./AdminPage";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

// 四个 Tag 之后，各板块不再同屏——用例先切到它所在的 Tag。
// 这不是「为了让测试通过而绕路」：真人也要先点这一下，测试就该照着点。
//
// **必须用 fireEvent 而不是 userEvent**：探活那几条用例开着假定时器，userEvent 的点击要等一个
// 真实 setTimeout，在假定时器下永远不返回 → 用例超时 → `finally` 里的 useRealTimers() 跑不到
// → 假定时器留给后面所有用例，一条坏事变三十条。Tag 就是个普通按钮，不需要模拟指针序列。
function goTag(name: RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}
const TAG_RESOURCE = /^(资源|Resources)$/;
const TAG_USER = /^(用户|Users)$/;
const TAG_WORKFLOW = /^(工作流|Workflow)$/;

describe("AdminPage · 服务商余额", () => {
  beforeEach(() => setSpy.mockClear());

  it("基础设施/转录引擎分组、Gemini 归基建、低水位标红、绑卡不显额、讯飞按小时、美元卡显 $", async () => {
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByText(/基础设施|Infrastructure/)).toBeInTheDocument();
    expect(screen.getByText(/转录引擎|Transcription/)).toBeInTheDocument();
    expect(screen.getByText(/Gemini（主轨备份候选）/)).toBeInTheDocument();     // Gemini 降为备份候选
    expect(screen.getByText(/ElevenLabs-Scribe v2（主轨）/)).toBeInTheDocument(); // 主轨 ELV 卡
    expect(screen.getAllByText(/后付费|Postpaid/).length).toBe(3);              // Gemini/ELV/SPM 后付费徽标
    expect(screen.getAllByText(/绑卡|Card on file/).length).toBe(3);            // 后付费三卡均不显余额
    expect(screen.getAllByText(/偏低|low/).length).toBeGreaterThanOrEqual(1);   // DeepSeek/博查 低
    expect(screen.getByText("$12.5")).toBeInTheDocument();                      // AAI 美元卡显 $
    // 算出来的浮点直显会是「40.5597222222222 小时」（2026-08-07 生产实测），只截过长的那种
    expect(screen.getByText(/40\.56\s*(小时|h)/)).toBeInTheDocument();
    expect(screen.queryByText(/40\.5597/)).not.toBeInTheDocument();
    expect(screen.getByText(/自动充值|Auto/)).toBeInTheDocument();              // FunASR 自动充值徽标
    expect(screen.queryByText(/兜底/)).toBeNull();                              // 自动充值卡现在也显余额，不再"兜底"占位
  });

  it("手动卡点「改」内联编辑并保存（调 setAdminBalance 带 vendor + 金额）", async () => {
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    await screen.findByText(/基础设施|Infrastructure/);
    // 改按钮按显示序（绑卡的 Gemini 无改）：DeepSeek, 博查, 豆包, 讯飞, FunASR
    const editBtns = screen.getAllByRole("button", { name: /^(改|edit)$/ });
    await userEvent.click(editBtns[3]);   // 讯飞（手动卡 → 可改金额+阈值两个输入）
    // 退款区也有个邮箱输入框，按 aria-label 排掉——这里只关心余额卡自己的输入
    const inputs = screen.getAllByRole("textbox")
      .filter((i) => !/用户邮箱|User email/.test(i.getAttribute("aria-label") || ""));
    expect(inputs.length).toBe(2);        // 手动卡两个输入；api 卡只有阈值一个
    await userEvent.clear(inputs[0]);
    await userEvent.type(inputs[0], "50");
    await userEvent.click(screen.getByRole("button", { name: /保存|Save/ }));
    expect(setSpy).toHaveBeenCalledTimes(1);
    expect(setSpy.mock.calls[0][0]).toMatchObject({ vendor: "讯飞", amountCny: 50 });
  });
});

describe("AdminPage · 任务行主轨分片进度（新 primary 块 / 历史 g25f 兼容）", () => {
  it("历史任务（metrics.g25f）：徽标 + 展开明细里 token 遥测文案照旧渲染", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      jobs: [{
        id: "job-old", fileName: "旧任务-历史g25f.mp3", userEmail: "a@x.com",
        status: "running", phase: "P1", progress: 40, attempts: 1, elapsedSec: 120,
        metrics: {
          engines: { G25F: { status: "done", sec: 90 }, DB: { status: "done", sec: 30 } },
          g25f: { done: 6, running: 0, failed: 0, total: 6, onePass: 5, maxAttempts: 2 },
        },
      }],
      failures: [], recent: [], summary: { running: 1, queued: 0, doneToday: 0, failedToday: 0 },
    });
    wrap(<AdminPage />);
    await screen.findByText("旧任务-历史g25f.mp3");
    expect(screen.getByText("G25F 6/6片")).toBeInTheDocument();       // 徽标：done/total 片（来自归一后的 prim）
    await userEvent.click(screen.getByText("旧任务-历史g25f.mp3"));
    expect(screen.getByText(/6片 · (一次过|1-pass) 5\/6 · (最多重试|max) 2(次|×)/)).toBeInTheDocument();
  });

  it("新任务（metrics.primary tag=ELV）：徽标 + 展开明细只显分片进度，不显 token 遥测", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      jobs: [{
        id: "job-new", fileName: "新任务-ELV.mp3", userEmail: "b@x.com",
        status: "running", phase: "P1", progress: 40, attempts: 1, elapsedSec: 60, lang: "ja",
        metrics: {
          engines: { ELV: { status: "running", sec: null }, DB: { status: "done", sec: 20 } },
          primary: { tag: "ELV", done: 3, running: 1, failed: 0, total: 5 },
        },
      }],
      failures: [], recent: [], summary: { running: 1, queued: 0, doneToday: 0, failedToday: 0 },
    });
    wrap(<AdminPage />);
    await screen.findByText("新任务-ELV.mp3");
    expect(screen.getByText("ELV 3/5片")).toBeInTheDocument();        // 徽标：新 primary 块的 done/total
    expect(screen.getByText(/日语|Japanese/)).toBeInTheDocument();    // 27 门后任务卡标语言（lang: ja）
    await userEvent.click(screen.getByText("新任务-ELV.mp3"));
    expect(screen.getByText(/（3\/5片）/)).toBeInTheDocument();       // 展开明细：分片进度行
    expect(screen.queryByText(/一次过|1-pass/)).toBeNull();           // 无 g25f → 不渲染 token 遥测文案
  });
});


describe("AdminPage · 充值退款", () => {
  beforeEach(() => refundSpy.mockReset());

  const lookup = async () => {
    wrap(<AdminPage />);
    goTag(TAG_USER);
    await userEvent.type(await screen.findByLabelText(/用户邮箱|User email/), "u@x.com");
    await userEvent.click(screen.getByRole("button", { name: /查询|Look up/ }));
    await screen.findByText(/当前余额|Balance/);
  };

  it("列出充值记录：可退额、已退额、赠送与超窗口各自给出不可退的理由", async () => {
    await lookup();
    expect(screen.getByText("$4.00")).toBeInTheDocument();            // 可退额（后端算好的，前端不自己推）
    expect(screen.getByText(/已退|refunded/)).toBeInTheDocument();
    expect(screen.getByText(/赠送额度|Bonus credit/)).toBeInTheDocument();
    expect(screen.getByText(/超出 14 天窗口|Past the 14-day window/)).toBeInTheDocument();
    // 三行里只有真金白银那笔给退款按钮
    expect(screen.getAllByRole("button", { name: /^(退款|Refund)$/ }).length).toBe(1);
  });

  it("发起退款：按分传给后端，回显「等回执到账」——发起阶段不动余额", async () => {
    refundSpy.mockResolvedValue(undefined);
    await lookup();
    await userEvent.type(screen.getByLabelText(/退款金额|Refund amount/), "4");
    await userEvent.click(screen.getByRole("button", { name: /^(退款|Refund)$/ }));
    expect(refundSpy).toHaveBeenCalledWith("u@x.com", 7, 400);        // 元 → 分
    expect(await screen.findByText(/待回执到账后扣减|updates on callback/)).toBeInTheDocument();
  });

  it("超过可退额：本地就拦下，不打后端（后端仍会再校验一次）", async () => {
    await lookup();
    await userEvent.type(screen.getByLabelText(/退款金额|Refund amount/), "9");
    await userEvent.click(screen.getByRole("button", { name: /^(退款|Refund)$/ }));
    expect(refundSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(/请输入 0 到 \$4\.00|between 0 and \$4\.00/)).toBeInTheDocument();
  });

});

// ── 融合引擎健康度（P3·无头模式）─────────────────────────────────────────────
// 订阅制没有余额可查，这张卡是成败统计。用例守三件事：状态判定不误报、
// 我方限流不算进成功率分母、探活结果按 probeId 认领（不靠比时刻）。
describe("AdminPage · 融合引擎健康度", () => {
  const health = (over: Record<string, unknown> = {}) => ({
    windowHours: 24,
    counts: { ok: 9, capped: 1 },
    attempted: 10,
    successRate: 0.9,
    capped: null,
    authFailing: false,
    authNote: null,
    lastAttempt: "ok",
    last: { at: "2026-08-07T09:00:00Z", source: "job", outcome: "ok", window: null, note: null, jobId: "j1" },
    lastOkAt: "2026-08-07T09:00:00Z",
    lastCappedAt: null,
    lastCappedWindow: null,
    activeSlots: 2,
    slotLimit: 5,
    ...over,
  });

  beforeEach(() => {
    p3HealthSpy.mockReset();
    p3ProbeSpy.mockReset();
    p3HealthSpy.mockResolvedValue(health());
  });

  it("健康时显示成功率与在飞并发", async () => {
    p3HealthSpy.mockResolvedValue(health({ counts: { ok: 10 }, attempted: 10, successRate: 1 }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/^(正常|Healthy)$/)).toBeInTheDocument();
    expect(screen.getByText(/10\/10 次成功|10\/10 succeeded/)).toBeInTheDocument();
    expect(screen.getByText("2 / 5")).toBeInTheDocument();
  });

  it("撞顶时给出恢复时刻——运营要知道的是「还要多久」而不只是「坏了」", async () => {
    p3HealthSpy.mockResolvedValue(health({
      capped: { reason: "cap_5h", window: "five_hour", until: "2026-08-07T10:30:00Z" },
    }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/额度撞顶|Rate cap hit/)).toBeInTheDocument();
  });

  it("周额度撞顶与 5 小时窗口分开说（恢复时间差一个数量级）", async () => {
    p3HealthSpy.mockResolvedValue(health({
      capped: { reason: "cap_week", window: "seven_day", until: "2026-08-09T10:00:00Z" },
    }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/周额度撞顶|Weekly cap hit/)).toBeInTheDocument();
  });

  it("没有样本时说「无样本」，不是刺眼的 0%", async () => {
    p3HealthSpy.mockResolvedValue(health({ counts: {}, attempted: 0, successRate: null, lastOkAt: null, last: null, lastAttempt: null }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/无调用样本|No calls/)).toBeInTheDocument();
  });

  it("点探活即派机器；后端拒绝时把原因原样显示（501/503 各有含义）", async () => {
    p3ProbeSpy.mockRejectedValue(new Error("机器池已满（转录优先），稍后再探"));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    await userEvent.click(await screen.findByRole("button", { name: /^(探活|Probe)$/ }));
    expect(p3ProbeSpy).toHaveBeenCalled();
    expect(await screen.findByText(/机器池已满/)).toBeInTheDocument();
  });

  it("探活结果按 probeId 认领：别人的事件不算数，自己的才算", async () => {
    vi.useFakeTimers();
    p3ProbeSpy.mockResolvedValue({ probeId: "probe-abc" });
    try {
      wrap(<AdminPage />);
      goTag(TAG_WORKFLOW);
      await vi.advanceTimersByTimeAsync(0);
      fireEvent.click(screen.getByRole("button", { name: /^(探活|Probe)$/ }));
      await vi.advanceTimersByTimeAsync(0);

      // 轮询期间先来一条**别人的**事件（真实转录跑完写的）→ 不能被当成探活结果
      p3HealthSpy.mockResolvedValue(health({
        last: { at: "2026-08-07T09:05:00Z", source: "job", outcome: "ok", window: null, note: null, jobId: "other-job" },
      }));
      await vi.advanceTimersByTimeAsync(4100);
      expect(screen.getByRole("button", { name: /探活中|Probing/ })).toBeInTheDocument();

      // 自己那次回来了 → 结束等待并报结果
      p3HealthSpy.mockResolvedValue(health({
        last: { at: "2026-08-07T09:06:00Z", source: "probe", outcome: "ok", window: null, note: null, jobId: "probe-abc" },
      }));
      await vi.advanceTimersByTimeAsync(4100);
      expect(screen.getByText(/探活通过|Probe passed/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("探活撞上并发满：说清楚「没打到引擎」，别误报成引擎坏了", async () => {
    vi.useFakeTimers();
    p3ProbeSpy.mockResolvedValue({ probeId: "probe-busy" });
    try {
      wrap(<AdminPage />);
      goTag(TAG_WORKFLOW);
      await vi.advanceTimersByTimeAsync(0);
      fireEvent.click(screen.getByRole("button", { name: /^(探活|Probe)$/ }));
      await vi.advanceTimersByTimeAsync(0);
      p3HealthSpy.mockResolvedValue(health({
        last: { at: "2026-08-07T09:06:00Z", source: "probe", outcome: "concurrency", window: null, note: null, jobId: "probe-busy" },
      }));
      await vi.advanceTimersByTimeAsync(4100);
      expect(screen.getByText(/没打到引擎|never reached the engine/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

// 认证失效与撞顶是两件事：撞顶等着就好，这个不动手永远不会好——所以面板给的是动作不是描述。
describe("AdminPage · 融合引擎认证失效", () => {
  const authHealth = {
    windowHours: 24,
    counts: { ok: 3, auth: 2 },
    attempted: 5,
    successRate: 0.6,
    capped: null,
    authFailing: true,
    authNote: "401 Unauthorized",
    lastAttempt: "auth",
    last: { at: "2026-08-07T09:00:00Z", source: "job", outcome: "auth", window: null, note: "401 Unauthorized", jobId: "j9" },
    lastOkAt: "2026-08-07T06:00:00Z",
    lastCappedAt: null,
    lastCappedWindow: null,
    activeSlots: 0,
    slotLimit: 5,
  };

  beforeEach(() => {
    p3ProbeSpy.mockReset();
    p3HealthSpy.mockReset();
    p3HealthSpy.mockResolvedValue(authHealth);
  });

  it("认证失效压过其它状态，并给出可执行的恢复步骤", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/认证失效 · 需要人工处理|Auth failed/)).toBeInTheDocument();
    expect(screen.getByText(/setup-token/)).toBeInTheDocument();          // 明确到命令，不让人猜
    expect(screen.getByText(/转录没有中断|not down/)).toBeInTheDocument(); // 先安抚：生意没停
    expect(screen.getByText(/401 Unauthorized/)).toBeInTheDocument();      // 引擎原话，便于定位
  });

  it("认证失效时不并排显示成功率——两个读数会自相矛盾", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    await screen.findByText(/需要人工处理|needs you/);
    expect(screen.queryByText(/\d+\/\d+ 次成功|\d+\/\d+ succeeded/)).not.toBeInTheDocument();
  });

  it("撞顶同时认证也坏：先说认证（撞顶会自愈，认证不会）", async () => {
    p3HealthSpy.mockResolvedValue({
      ...authHealth,
      capped: { reason: "cap_5h", window: "five_hour", until: "2026-08-07T10:30:00Z" },
    });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/需要人工处理|needs you/)).toBeInTheDocument();
    expect(screen.queryByText(/额度撞顶|Rate cap hit/)).not.toBeInTheDocument();
  });
});

// 滚动窗口的平均值会被已经修好的故障一直拖着——状态得看「最近一次真打到引擎的结果」。
describe("AdminPage · 融合引擎状态取最近一次而非窗口均值", () => {
  const base = {
    windowHours: 24, counts: { ok: 2, auth: 2 }, attempted: 4, successRate: 0.5,
    capped: null, authFailing: false, authNote: null, lastAttempt: "ok",
    last: { at: "2026-08-07T14:03:00Z", source: "job", outcome: "ok", window: null, note: null, jobId: "j1" },
    lastOkAt: "2026-08-07T14:03:00Z", lastCappedAt: null, lastCappedWindow: null,
    activeSlots: 0, slotLimit: 5,
  };

  beforeEach(() => { p3ProbeSpy.mockReset(); p3HealthSpy.mockReset(); });

  it("故障已修好（最近一次成功）→ 说「已恢复」，不再报异常", async () => {
    p3HealthSpy.mockResolvedValue(base);
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/^(已恢复|Recovered)$/)).toBeInTheDocument();
    // 窗口里的失败不隐瞒，降级成副文本继续显示
    expect(screen.getByText(/2\/4 次成功|2\/4 succeeded/)).toBeInTheDocument();
  });

  it("窗口全绿且最近一次成功 → 正常", async () => {
    p3HealthSpy.mockResolvedValue({ ...base, counts: { ok: 4 }, attempted: 4, successRate: 1 });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/^(正常|Healthy)$/)).toBeInTheDocument();
  });

  it("最近一次失败 → 异常，并点明是哪种失败", async () => {
    p3HealthSpy.mockResolvedValue({ ...base, lastAttempt: "timeout", successRate: 0.9, counts: { ok: 9, timeout: 1 }, attempted: 10 });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/异常 · 最近一次超时|Failing · last call Timeouts/)).toBeInTheDocument();
  });

  it("我方限流（冷却跳过/并发让路）不改写状态——它们不是引擎的锅", async () => {
    // lastAttempt 只统计真打到引擎的结果，所以一串 preempt 之后状态仍由上一次真实调用决定
    p3HealthSpy.mockResolvedValue({ ...base, counts: { ok: 2, auth: 2, preempt: 7 }, attempted: 4 });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/^(已恢复|Recovered)$/)).toBeInTheDocument();
    expect(screen.getByText(/冷却跳过 7|Skipped 7/)).toBeInTheDocument();
  });
});


// ── 融合引擎配置（并发 / 机器数 / 强制引擎）─────────────────────────────────────
const CFG = (over: Record<string, unknown> = {}) => ({
  config: {
    fly_max_machines: 50, max_transcribe_jobs: 40, claude_concurrency: 5,
    force_engine: null, forceExpiresAt: null, updated_by: null, updatedAt: null, ...over,
  },
  limits: { claude: 5, pro: 500, flash: 2500, machines: 50 },
  perMachineConc: 40,
  forceMachines: { flash: 50 },
  usage: {},
  effectiveMaxMachines: 50,
});

describe("AdminPage · 融合引擎配置", () => {
  beforeEach(() => {
    p3CfgSpy.mockReset();
    p3CfgSaveSpy.mockReset().mockResolvedValue({ ok: true });
    p3HealthSpy.mockResolvedValue({
      windowHours: 24, counts: {}, attempted: 0, successRate: null, capped: null,
      authFailing: false, authNote: null, lastAttempt: null, last: null,
      lastOkAt: null, lastCappedAt: null, lastCappedWindow: null, activeSlots: 0, slotLimit: 5,
    });
  });

  it("转录任务数旁边显示的是**乘积**占账号上限几成——真正撞墙的是乘积不是台数本身", async () => {
    p3CfgSpy.mockResolvedValue(CFG());
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    // 转录 40 台 × 每机 40 路 = 1600 / 2500 = 64%
    expect(await screen.findByText(/40 台 × 40 路 = 1600 \/ 2500/)).toBeTruthy();
    expect(screen.getByText(/\(64%\)/)).toBeTruthy();
  });

  it("超过账号上限时标红并写明「超出账号上限」，而不是等保存后才报错", async () => {
    p3CfgSpy.mockResolvedValue(CFG());
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    const input = (await screen.findAllByRole("spinbutton"))[1];   // 转录任务数那一行
    fireEvent.change(input, { target: { value: "70" } });          // 70 × 40 = 2800 > 2500
    expect(await screen.findByText(/超出账号上限/)).toBeTruthy();
  });

  it("强制引擎只给 flash 一个选项——claude 是第一档强制无意义，pro 已摘除", async () => {
    p3CfgSpy.mockResolvedValue(CFG());
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByRole("button", { name: "flash" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "pro" })).toBeNull();
    expect(screen.queryByRole("button", { name: /claude/i })).toBeNull();
  });

  it("点强制引擎时带上时长（默认 4 小时）——强制态必须有到期时间，忘了关就是全量受影响", async () => {
    p3CfgSpy.mockResolvedValue(CFG());
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByRole("button", { name: "flash" }));
    expect(p3CfgSaveSpy).toHaveBeenCalledWith({ forceEngine: "flash", forceHours: 4 });
  });

  it("已强制时显示机器数与到期时刻，并警示影响所有真实订单", async () => {
    p3CfgSpy.mockResolvedValue(CFG({
      force_engine: "flash", forceExpiresAt: "2026-08-11T20:00:00Z",
    }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    // 50 台 × 40 = 2000，占 flash 账号上限 2500 的 80%（FORCE_MACHINES 派生值）
    expect(await screen.findByText(/机器数自动压到 50 台/)).toBeTruthy();
    expect(screen.getByText(/影响所有真实订单/)).toBeTruthy();
    expect(screen.getByText(/到期自动恢复/)).toBeTruthy();
  });

  it("已强制时再点同一档 = 取消（回正常阶梯），不需要另找一个「关闭」按钮", async () => {
    p3CfgSpy.mockResolvedValue(CFG({ force_engine: "flash", forceExpiresAt: "2026-08-11T20:00:00Z" }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByRole("button", { name: /✓ flash/ }));
    expect(p3CfgSaveSpy).toHaveBeenCalledWith({ forceEngine: null, forceHours: 4 });
  });

  it("后端校验失败要把原因原样显示出来——它写清了哪一项、为什么", async () => {
    p3CfgSpy.mockResolvedValue(CFG());
    p3CfgSaveSpy.mockRejectedValue(new Error("Pro 并发要在 1–10（20 台 × 50 路 = 1000 > 账号上限 500）"));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByRole("button", { name: "flash" }));
    expect(await screen.findByText(/账号上限 500/)).toBeTruthy();
  });
});

describe("AdminPage · P3 成本按档位显示", () => {
  const withCost = (cost: Record<string, unknown>) => ({
    jobs: [], failures: [],
    recent: [{
      id: "j1", fileName: "某访谈.flac", userEmail: "a@x.com", finishedAt: "08-12 01:35",
      createdAt: "08-12 01:09", recordingType: "onsite", lang: "zh",
      metrics: { engines: { ELV: { status: "done", sec: 190.6 } }, cost },
    }],
    summary: { running: 0, queued: 0, doneToday: 1, failedToday: 0 },
  });
  const openRow = async (cost: Record<string, unknown>) => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(withCost(cost));
    wrap(<AdminPage />);
    await userEvent.click(await screen.findByText("某访谈.flac"));
  };

  it("走 DeepSeek Flash 的单，档位名跟金额一起显示——只看「P3 ¥x」分不清这钱花在哪一档", async () => {
    await openRow({ p1: { ELV: 2.27 }, p3: 1.23, p3_engine: "flash", total: 3.5 });
    expect(screen.getByText(/P3 DS-v4-Flash ¥1\.23/)).toBeInTheDocument();
  });

  it("Opus 是订阅制、没有按量成本，金额 0 也要把档位显出来（0 元不等于没跑）", async () => {
    await openRow({ p1: { ELV: 2.27 }, p3: 0, p3_engine: "opus", total: 2.27 });
    expect(screen.getByText(/P3 Opus ¥0/)).toBeInTheDocument();
  });

  it("Pro 档同理", async () => {
    await openRow({ p1: { ELV: 1 }, p3: 2.35, p3_engine: "pro", total: 3.35 });
    expect(screen.getByText(/P3 DS-v4-Pro ¥2\.35/)).toBeInTheDocument();
  });

  it("解析不出档位的老单显示 ?，**不猜**——成本要能核准就不能有编出来的归属", async () => {
    await openRow({ p1: { ELV: 1 }, p3: 0.5, p3_engine: "unknown", total: 1.5 });
    expect(screen.getByText(/P3 \? ¥0\.5/)).toBeInTheDocument();
  });

  it("连 p3_engine 字段都没有的更老的单，也不能崩、也不能瞎认一个档", async () => {
    await openRow({ p1: { ELV: 1 }, p3: 0.5, total: 1.5 });
    expect(screen.getByText(/P3 \? ¥0\.5/)).toBeInTheDocument();
  });
});


describe("AdminPage · 后处理板块", () => {
  const PP = (over: Record<string, unknown> = {}) => ({
    jobId: "j1", fileName: "某访谈.flac", userEmail: "a@x.com", status: "done",
    steps: ["narrate"], currentStep: null, stepIndex: 1, priceCents: 200,
    qcFixCount: 13, hasQc: true, failedStep: null, errorPublic: null,
    degradedSteps: [], dsCostCny: null, attempts: 1, updatedAt: "08-13 09:30", ...over,
  });
  const wrapWith = async (rows: Record<string, unknown>[]) => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      jobs: [], failures: [], recent: [], postprocess: rows,
      summary: { running: 0, queued: 0, doneToday: 0, failedToday: 0 },
    });
    wrap(<AdminPage />);
    // 圈到后处理板块内断言：页面顶部的汇总卡有「今日完成/今日失败」，
    // 不限定范围的话 /完成/ 会同时命中它们（第一版就是这么挂的）
    const h = await screen.findByText("后处理");
    return within(h.closest("section") ?? h.parentElement!.parentElement!);
  };

  it("后处理是收费功能，运营页要看得到：文件、步骤、状态、价钱", async () => {
    const box = await wrapWith([PP()]);
    expect(box.getByText("某访谈.flac")).toBeInTheDocument();
    expect(box.getByText("视角转换")).toBeInTheDocument();
    expect(box.getByText(/完成/)).toBeInTheDocument();
    expect(box.getByText("$2.00")).toBeInTheDocument();
  });

  it("降级的步要打标——用户说「这份不如上次」时，运营一眼看出来，不用翻日志", async () => {
    await wrapWith([PP({ degradedSteps: ["narrate"] })]);
    expect(screen.getByText("降级")).toBeInTheDocument();
    expect(screen.getByText(/Claude 撞顶，改由 DeepSeek 兜底/)).toBeInTheDocument();  // 图例
  });

  it("没降级就不该出现「降级」字样，也不该出现图例", async () => {
    await wrapWith([PP({ degradedSteps: [] })]);
    expect(screen.queryByText("降级")).toBeNull();
    expect(screen.queryByText(/改由 DeepSeek 兜底/)).toBeNull();
  });

  it("降过级的单显示这一单烧掉的 DeepSeek 钱", async () => {
    await wrapWith([PP({ degradedSteps: ["narrate"], dsCostCny: 0.0954 })]);
    expect(screen.getByText("¥0.10")).toBeTruthy();
  });

  it("没降级过的单成本列显示 —，不显示 ¥0（¥0 会被读成「降级了但没花钱」）", async () => {
    await wrapWith([PP({ dsCostCny: null })]);
    // 只断言「没有 ¥0.00 这一格」——页面别处（服务商余额）本来就有 ¥ 金额，不能全局扫 /¥/
    expect(screen.queryByText("¥0.00")).toBeNull();
  });

  it("老单的 degradedSteps 是 null（那时还没这一列）——不能崩，也不能当成降级", async () => {
    await wrapWith([PP({ degradedSteps: null })]);
    expect(screen.getByText("某访谈.flac")).toBeInTheDocument();
    expect(screen.queryByText("降级")).toBeNull();
  });

  it("多步任务只标降过级的那一步", async () => {
    await wrapWith([PP({ steps: ["narrate", "redact"], degradedSteps: ["narrate"] })]);
    expect(screen.getByText("视角转换")).toBeInTheDocument();
    expect(screen.getByText("脱敏")).toBeInTheDocument();
    expect(screen.getAllByText("降级")).toHaveLength(1);   // 只有一个徽标
  });

  it("失败的那一步标红、状态显示失败", async () => {
    const box = await wrapWith([PP({ status: "failed", failedStep: "narrate", qcFixCount: 0,
                                    errorPublic: "视角转换步执行失败" })]);
    expect(box.getByText(/失败/)).toBeInTheDocument();
  });

  it("处理中显示进度 步/总", async () => {
    await wrapWith([PP({ status: "running", steps: ["narrate", "redact"], stepIndex: 2, qcFixCount: 0 })]);
    expect(screen.getByText(/处理中 2\/2/)).toBeInTheDocument();
  });

  it("没有后处理任务时给一句话，不是空白", async () => {
    await wrapWith([]);
    expect(screen.getByText("暂无后处理任务")).toBeInTheDocument();
  });
});

// ── 待办条 ────────────────────────────────────────────────────────────────────
// 这条的价值全在**克制**：它只报「不动手就不会好」的事。多报一条，人对它的信任就少一分，
// 报到最后又变成没人看。所以用例既守「该报的报了」，也守「不该报的没报」。
describe("AdminPage · 待办条", () => {
  const noBalanceIssue = () => (getAdminBalances as unknown as ReturnType<typeof vi.fn>)
    .mockResolvedValueOnce([{ vendor: "X", label: "X", category: "infra", payMode: "postpaid",
      unit: "cny", sortOrder: 1, amountCny: null, thresholdCny: null, source: "manual",
      note: null, updatedAt: "", low: false }]);
  const quiet = {
    windowHours: 24, counts: { ok: 3 }, attempted: 3, successRate: 1, capped: null,
    authFailing: false, authNote: null, lastAttempt: "ok", last: null,
    lastOkAt: null, lastCappedAt: null, lastCappedWindow: null, activeSlots: 0, slotLimit: 5,
  };

  beforeEach(() => {
    p3HealthSpy.mockReset().mockResolvedValue(quiet);
    (getAdminExpiries as unknown as ReturnType<typeof vi.fn>)
      .mockReset().mockResolvedValue({ warnDays: 60, items: [] });
  });

  it("认证失效要进来——它不动手永远不会好", async () => {
    p3HealthSpy.mockResolvedValue({ ...quiet, authFailing: true });
    wrap(<AdminPage />);
    expect(await screen.findByText(/认证失效/)).toBeInTheDocument();
    expect(screen.getByText(/需要处理 \d+ 件/)).toBeInTheDocument();
  });

  it("撞顶降级不进来——它会自愈；这一条是整条待办条的分寸所在", async () => {
    noBalanceIssue();
    p3HealthSpy.mockResolvedValue({
      ...quiet, capped: { reason: "cap_5h", window: "five_hour", until: "2026-08-14T10:00:00Z" },
    });
    wrap(<AdminPage />);
    expect(await screen.findByText(/没有需要处理的事/)).toBeInTheDocument();
  });

  it("凭证临期进来，并写明到期后会发生什么（只说「快到期」人不会动手）", async () => {
    noBalanceIssue();
    (getAdminExpiries as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      warnDays: 60,
      items: [{ id: "k", vendor: "Paddle", what: "PADDLE_API_KEY", expires: "2026-09-01",
                impact: "充值静默失败", fix: "重建 key", source: "manual", daysLeft: 18, warn: true }],
    });
    wrap(<AdminPage />);
    expect(await screen.findByText(/PADDLE_API_KEY 还有 18 天到期 —— 充值静默失败/)).toBeInTheDocument();
  });

  it("余额低于阈值进来（默认 fixtures 里 DeepSeek/博查 都低）", async () => {
    wrap(<AdminPage />);
    expect(await screen.findByText(/DeepSeek 余额 8\.84 低于阈值 20/)).toBeInTheDocument();
  });

  // 没设阈值的厂商，`low` 恒为 false —— 它在界面上跟「余额充足」一模一样，
  // 但它是**没有保护**：余额见底也不会有任何告警。这正是要单独报一条的理由。
  it("预充值没设阈值要报，且聚成一条（分开四行只会把待办条淹掉）", async () => {
    wrap(<AdminPage />);
    const t = await screen.findByText(/3 家预充值服务商没设余额阈值/);
    expect(t).toBeInTheDocument();
    expect(t.textContent).toMatch(/豆包.*讯飞.*Soniox/s);       // 点名，才知道去补哪几家
    expect(t.textContent).toMatch(/余额见底也不会有任何告警/);   // 说清后果，不然人不会动手
  });

  it("后付费与自动充值不算——按设计它们本就不该盯余额，报了就是永远清不掉的假警报", async () => {
    wrap(<AdminPage />);
    await screen.findByText(/3 家预充值服务商没设余额阈值/);
    const t = screen.getByText(/预充值服务商没设余额阈值/).textContent!;
    expect(t).not.toMatch(/ElevenLabs|Speechmatics/);   // postpaid
    expect(t).not.toMatch(/Fun-ASR/);                   // prepaid_auto（关联了自动充值）
  });

  it("排队超过判死窗口的 1/4 就报——那段窗口是唯一能干预的时间", async () => {
    noBalanceIssue();
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      jobs: [{ id: "q1", fileName: "a.mp3", userEmail: "u@x.com", status: "queued", phase: null,
               progress: 0, attempts: 1, elapsedSec: 7 * 3600, metrics: null, recordingType: null,
               createdAt: "08-14 01:00", lang: "zh" }],
      failures: [], recent: [], summary: { running: 0, queued: 1, doneToday: 0, failedToday: 0 },
      queuedMaxHours: 24,
    });
    wrap(<AdminPage />);
    expect(await screen.findByText(/1 单排队已超 7 小时（满 24 小时判失败/)).toBeInTheDocument();
  });

  it("同因失败达阈值才报——5 单失败是 5 个问题还是同一个炸了 5 次，这个差别决定要不要立刻动手", async () => {
    noBalanceIssue();
    const fail = (id: string, err: string) => ({ id, fileName: `${id}.mp3`, userEmail: "u@x.com", error: err, attempts: 1, failedAt: "08-14 01:00" });
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      jobs: [], recent: [],
      failures: [fail("a", "主轨转录失败（无产物）"), fail("b", "主轨转录失败（无产物）"),
                 fail("c", "主轨转录失败（无产物）"), fail("d", "P3 融合超时")],
      summary: { running: 0, queued: 0, doneToday: 0, failedToday: 4 }, queuedMaxHours: 24,
    });
    wrap(<AdminPage />);
    expect(await screen.findByText(/近 7 天 3 单同因失败：主轨转录失败（无产物）/)).toBeInTheDocument();
    expect(screen.queryByText(/1 单同因失败/)).toBeNull();       // 落单的那条不报
  });
});

// ── 任务流（原「实时任务 / 最近完成 / 最近失败」三块合一）────────────────────
describe("AdminPage · 任务流", () => {
  const overview = () => ({
    jobs: [{ id: "j1", fileName: "跑着的.mp3", userEmail: "u@x.com", status: "running", phase: "P1",
             progress: 30, attempts: 1, elapsedSec: 60, metrics: null, recordingType: null,
             createdAt: "08-14 01:00", lang: "zh" }],
    recent: [{ id: "r1", fileName: "完成的.mp3", userEmail: "u@x.com", metrics: null,
               doneAt: "08-14 02:00", recordingType: null, createdAt: "08-14 01:30", lang: "zh" }],
    failures: [{ id: "f1", fileName: "失败的.mp3", userEmail: "u@x.com", error: "主轨转录失败",
                 attempts: 2, failedAt: "08-14 03:00" },
               { id: "f2", fileName: "也失败的.mp3", userEmail: "u@x.com", error: "主轨转录失败",
                 attempts: 1, failedAt: "08-14 03:10" }],
    summary: { running: 1, queued: 0, doneToday: 1, failedToday: 2 }, queuedMaxHours: 24,
  });

  it("三种状态同屏——合并的意义就在于不用在三张表之间来回找同一批单子", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(overview());
    wrap(<AdminPage />);
    expect(await screen.findByText("跑着的.mp3")).toBeInTheDocument();
    expect(screen.getByText("完成的.mp3")).toBeInTheDocument();
    expect(screen.getByText("失败的.mp3")).toBeInTheDocument();
  });

  it("筛选只留一类", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(overview());
    wrap(<AdminPage />);
    await screen.findByText("跑着的.mp3");
    fireEvent.click(screen.getByRole("button", { name: /^失败 2$/ }));
    expect(screen.queryByText("跑着的.mp3")).toBeNull();
    expect(screen.getByText("失败的.mp3")).toBeInTheDocument();
  });

  it("同因失败可分组细看——这是三张分表做不到的事", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(overview());
    wrap(<AdminPage />);
    await screen.findByText("失败的.mp3");
    fireEvent.click(screen.getByRole("button", { name: /2× 主轨转录失败/ }));
    expect(screen.getByText("失败的.mp3")).toBeInTheDocument();
    expect(screen.getByText("也失败的.mp3")).toBeInTheDocument();
  });

  // 看门狗回收在库里的唯一痕迹。**这一条不进待办条**——这单已经好了，没什么要动手的；
  // 但它得在行上看得见：「谁在悄悄地要跑两遍」是先于失败出现的质量信号。
  // 此前只有资源页那个 24h 汇总数，看得见「回收了 3 次」，却永远查不到是哪三单。
  it("成功但重跑过的单要在行上标出来，一次过的不标", async () => {
    const base = overview();
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...base,
      recent: [{ ...base.recent[0], id: "r2", fileName: "重跑过的.mp3", attempts: 2 },
               { ...base.recent[0], attempts: 1 }],
    });
    wrap(<AdminPage />);
    // 按 textContent 断言：文件名里也带「重跑过」三个字，按元素取会同时命中它
    const row = (await screen.findByText("重跑过的.mp3")).parentElement!;
    expect(row.textContent).toMatch(/重跑过\s*1\s*次/);
    expect((await screen.findByText("完成的.mp3")).parentElement!.textContent).not.toMatch(/重跑过/);
  });

  it("排队久了在任务行上就标出来，并说清满多久判失败", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...overview(),
      jobs: [{ id: "q1", fileName: "排着的.mp3", userEmail: "u@x.com", status: "queued", phase: null,
               progress: 0, attempts: 1, elapsedSec: 8 * 3600, metrics: null, recordingType: null,
               createdAt: "08-14 01:00", lang: "zh" }],
    });
    wrap(<AdminPage />);
    // 这句话在待办条上也有一份（那是有意的：条上告诉你有这回事，行上告诉你是哪一单），
    // 所以按行取，别用全局唯一匹配
    const timeCell = await screen.findByText(/排队中 8:00:00/);
    const card = timeCell.parentElement!.parentElement!;      // 时间格 → 表头行 → 整张任务卡
    expect(within(card).getByText(/满 24 小时判失败并返还预扣/)).toBeInTheDocument();
  });
});

// ── 工作流 Tag ────────────────────────────────────────────────────────────────
describe("AdminPage · 工作流节点", () => {
  const WF = (over: Record<string, unknown> = {}) => ({
    promptScope: "dispatcher", imageTag: "task-v23-redactds", langPlans: [
      // 2026-08-26 起 GEM 挂在全部 27 门上，样本跟着实际阵容走（收起态正好显示 zh + en 两行）
      { lang: "zh", primary: "ELV", refs: ["DB", "FA", "XF", "GEM"], normalize: "zh" },
      { lang: "en", primary: "ELV", refs: ["AAI", "DB", "GEM"], normalize: "" },
    ],
    prompts: {
      "pp-redact": { path: ".claude/skills/pp-redact/SKILL.md", lines: 382, sha: "de891035" },
    },
    params: { ppFlash: { redactBudget: 6000, model: "deepseek-flash" }, pp: { concurrency: 5 } },
    ...over,
  });

  beforeEach(() => {
    (getAdminWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset().mockResolvedValue(WF());
    p3HealthSpy.mockReset().mockResolvedValue({
      windowHours: 24, counts: {}, attempted: 0, successRate: null, capped: null,
      authFailing: false, authNote: null, lastAttempt: null, last: null, lastOkAt: null,
      lastCappedAt: null, lastCappedWindow: null, activeSlots: 0, slotLimit: 5,
    });
    p3CfgSpy.mockReset().mockResolvedValue(CFG());
  });

  it("后处理每一步各占一个节点——它们各有各的闸，合成一块会把差别藏进小字", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText("后处理 · 视角转换")).toBeInTheDocument();
    expect(screen.getByText("后处理 · 脱敏")).toBeInTheDocument();
  });

  it("归类已下架：工作流里不再有这个节点（留着会让运营去查一条不存在的链路）", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    await screen.findByText("后处理 · 视角转换");
    expect(screen.queryByText("后处理 · 归类")).toBeNull();
    expect(screen.queryByText(/无降级路，撞顶只能等/)).toBeNull();
  });

  it("P3 默认展开——它是唯一有实时状态和可执行动作的节点，不该比改造前更难找", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByRole("button", { name: /^(探活|Probe)$/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /融合引擎配置|Merge engine settings/ })).toBeInTheDocument();
  });

  it("参数来自后端给的真实常量，前端不自己写死", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("后处理 · 脱敏"));
    expect(await screen.findByText(/每批 6000 字/)).toBeInTheDocument();
  });

  it("提示词带路径·行数·指纹，并能看全文——只显示内容而不带指纹是假的安心", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("后处理 · 脱敏"));
    expect(await screen.findByText(/382 行 · sha de891035/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /查看全文/ })[0]);
    expect(await screen.findByText("规则正文")).toBeInTheDocument();
  });

  it("顶部写明指纹取自派单前台那一份——双头部署下它不证明线上跑的是这一份", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/task-v23-redactds/)).toBeInTheDocument();
    expect(screen.getByText(/取自派单前台这一份/)).toBeInTheDocument();
  });

  it("27 门语种编排只读可见——排查某语言质量问题时第一眼要看的就是它", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("P1 多路 ASR"));
    // 收起态显示 zh + en 两行，所以主轨 ELV 会出现两次
    expect(await screen.findAllByText("ELV")).toHaveLength(2);
    expect(screen.getByText(/1\. DB.*2\. FA.*3\. XF.*4\. GEM/)).toBeInTheDocument();
    // 非中文也要跑 GEM（2026-08-26 起全 27 门）——这一路少了是静默降质，界面上得看得见
    expect(screen.getByText(/1\. AAI.*2\. DB.*3\. GEM/)).toBeInTheDocument();
  });
});

// ── 资源 · 会到期的凭证 ───────────────────────────────────────────────────────
describe("AdminPage · 会到期的凭证", () => {
  it("倒计时 + 到期后会发生什么 + 怎么换，三样都要有", async () => {
    (getAdminExpiries as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      warnDays: 60,
      items: [{ id: "k", vendor: "Anthropic", what: "CLAUDE_CODE_OAUTH_TOKEN", expires: "2027-08-07",
                impact: "P3 融合全线降级", fix: "claude setup-token 重新生成", source: "manual",
                daysLeft: 358, warn: false }],
    });
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByText("CLAUDE_CODE_OAUTH_TOKEN")).toBeInTheDocument();
    expect(screen.getByText(/还有 358 天 · 2027-08-07/)).toBeInTheDocument();
    expect(screen.getByText(/P3 融合全线降级/)).toBeInTheDocument();
    expect(screen.getByText(/claude setup-token 重新生成/)).toBeInTheDocument();
  });

  it("机器数上限搬到资源了——它管的是全站在飞机器，不是融合那一环的参数", async () => {
    p3CfgSpy.mockReset().mockResolvedValue(CFG());
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByText(/在飞机器容量|Machine capacity/)).toBeInTheDocument();
    expect(screen.getByText(/转录机与后处理机共用/)).toBeInTheDocument();
  });
});

// ── 方案二：节点近况与运行信号 ────────────────────────────────────────────────
// 这一组守的是**口径**，不是「有没有渲染出来」。三条最要紧：耗时按音频分钟归一、
// 成功率跟基线并排、降级率排除判定不了的老单——每一条都是数字会不会骗人的分水岭。
const HEALTH = (over: Record<string, unknown> = {}) => ({
  windowHours: 24, baselineHours: 720,
  p1: {}, p1Baseline: {},
  p3: { tiers: {}, total: 0, degradedRatio: null, known: 0 },
  pp: {}, glossary: {},
  ops: { loginSends24h: 0, loginSendCapHint: 100, watchdogRequeues: 0,
         gateSlots: { claude: 0 }, gateLimit: 5, machinesRunning: 0,
         recent60: { done: 0, failed: 0 } },
  alerts: { engines: [], degrade: null },
  ...over,
});
const setHealth = (over: Record<string, unknown> = {}) =>
  (getAdminHealth as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(HEALTH(over));

describe("AdminPage · 节点近况", () => {
  beforeEach(() => {
    (getAdminHealth as unknown as ReturnType<typeof vi.fn>).mockReset();
    setHealth();
    (getAdminWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      promptScope: "dispatcher", imageTag: "t", langPlans: [], prompts: {}, params: {},
    });
    p3HealthSpy.mockReset().mockResolvedValue({
      windowHours: 24, counts: {}, attempted: 0, successRate: null, capped: null,
      authFailing: false, authNote: null, lastAttempt: null, last: null, lastOkAt: null,
      lastCappedAt: null, lastCappedWindow: null, activeSlots: 0, slotLimit: 5,
    });
    p3CfgSpy.mockReset().mockResolvedValue(CFG());
  });

  it("引擎成功率跟基线并排显示——光说 78% 没用，得说「平时 99%」", async () => {
    setHealth({
      p1: { XF: { total: 40, ok: 31, secPerAudioMin: 2.4 } },
      p1Baseline: { XF: { pct: 99, total: 200 } },
      alerts: { engines: [{ tag: "XF", pct: 78, basePct: 99, ok: 31, total: 40 }], degrade: null },
    });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("P1 多路 ASR"));
    expect(await screen.findByText(/31\/40.*78%/)).toBeInTheDocument();
    expect(screen.getByText(/99%.*n=200/)).toBeInTheDocument();
  });

  it("耗时的单位是「秒/音频分钟」——不归一的话面板会跟着当天文件长度漂", async () => {
    setHealth({ p1: { DB: { total: 10, ok: 10, secPerAudioMin: 0.8 } }, p1Baseline: {} });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("P1 多路 ASR"));
    expect(await screen.findByText(/秒\/音频分钟/)).toBeInTheDocument();
    expect(screen.getByText("0.8s")).toBeInTheDocument();
  });

  it("P3 显示出稿档位与降级率——降级率是判断额度够不够的直接依据", async () => {
    setHealth({ p3: { tiers: { opus: 6, flash: 4 }, total: 10, degradedRatio: 0.4, known: 10 } });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/降级率 40%（10 单可判定）/)).toBeInTheDocument();
  });

  it("后处理按步分开显示降级次数与花的钱，不汇总", async () => {
    setHealth({ pp: { redact: { total: 5, ok: 4, failed: 1, degraded: 2, dsCostCny: 3.4 } } });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("后处理 · 脱敏"));
    // 注意：断言里用 \s* 而不是全角空格——testing-library 会把全角空格归一成普通空格
    expect(await screen.findByText(/4\/5 成功/)).toBeInTheDocument();
    expect(screen.getByText(/降级 2\s*¥3\.40/)).toBeInTheDocument();
  });

  it("术语库助手要看 P95 不只是 P50——它前一百多秒一个字不吐，用户在等", async () => {
    setHealth({ glossary: { glossary_draft: { total: 8, ok: 8, p50Ms: 151000, p95Ms: 228000 } } });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("术语库助手"));
    expect(await screen.findByText(/起草 8\/8\s*P50 151s\s*P95 228s/)).toBeInTheDocument();
  });

  it("没有样本时说「没有样本」，不显示 0（0 会被读成「跑了 0 次」）", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const title = await screen.findByText("后处理 · 视角转换");
    fireEvent.click(title);
    // 按节点取：默认展开的 P3 也会显示同一句，全局唯一匹配会撞车
    const node = title.parentElement!.parentElement!.parentElement!;
    expect(await within(node).findByText(/窗口内没有样本/)).toBeInTheDocument();
  });

  // **空格子有两种，界面上必须能分辨**：一种是「窗口内真的没样本」，另一种是
  // 「埋点写好了但跑在任务机器上，重建 Fly 镜像之前恒为空」。混成同一句话的话，
  // 运营看到 P2 空着只能猜是没人用还是没上线——而这两件事该做的动作完全相反。
  it("P0/P2 没数时说清是「还没上机器」，不是「没有样本」", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const title = await screen.findByText("P2 对齐");
    fireEvent.click(title);
    const node = title.parentElement!.parentElement!.parentElement!;
    expect(await within(node).findByText(/重建镜像之前这里恒为空/)).toBeInTheDocument();
    expect(within(node).queryByText(/窗口内没有样本/)).toBeNull();
  });

  it("P0/P2 有数就显示耗时，且 P95 与 P50 并排（要看的是尾部）", async () => {
    setHealth({ phases: { p2: { total: 12, ok: 11, p50Ms: 96000, p95Ms: 402000 } } });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const title = await screen.findByText("P2 对齐");
    fireEvent.click(title);
    const node = title.parentElement!.parentElement!.parentElement!;
    expect(await within(node).findByText(/P50 96s\s*P95 402s/)).toBeInTheDocument();
    // 失败也要看得见：只显示成功数的话，「这一段经常挂」跟「很健康」长得一样
    expect(within(node).getByText(/失败 1/)).toBeInTheDocument();
    expect(within(node).queryByText(/重建镜像之前/)).toBeNull();
  });
});

describe("AdminPage · 运行信号与新增待办", () => {
  beforeEach(() => {
    (getAdminHealth as unknown as ReturnType<typeof vi.fn>).mockReset();
    setHealth();
    (getAdminBalances as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (getAdminExpiries as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ warnDays: 60, items: [] });
    p3HealthSpy.mockReset().mockResolvedValue({
      windowHours: 24, counts: {}, attempted: 0, successRate: null, capped: null,
      authFailing: false, authNote: null, lastAttempt: null, last: null, lastOkAt: null,
      lastCappedAt: null, lastCappedWindow: null, activeSlots: 0, slotLimit: 5,
    });
    p3CfgSpy.mockReset().mockResolvedValue(CFG());
  });

  it("引擎悄悄坏了进待办条——这是唯一能提前发现它的信号", async () => {
    setHealth({ alerts: { engines: [{ tag: "XF", pct: 78, basePct: 99, ok: 31, total: 40 }], degrade: null } });
    wrap(<AdminPage />);
    expect(await screen.findByText(/XF 近 24h 成功率 78%（平时 99%，31\/40）/)).toBeInTheDocument();
  });

  it("一直在降级进待办条——它跟「单次撞顶」不同，那个自愈，这个要人决定升档还是调闸", async () => {
    setHealth({ alerts: { engines: [], degrade: { ratio: 0.62, known: 21 } } });
    wrap(<AdminPage />);
    expect(await screen.findByText(/62% 的单降级出稿（21 单可判定）/)).toBeInTheDocument();
  });

  it("发码逼近供应商免费额度进待办条——撞上限就是所有人都登不进", async () => {
    setHealth({ ops: { ...HEALTH().ops, loginSends24h: 85 } });
    wrap(<AdminPage />);
    expect(await screen.findByText(/已发出 85\/100 封登录验证码/)).toBeInTheDocument();
  });

  it("资源页显示四把闸、在飞机器、看门狗回收——此前只显示了一把闸和上限", async () => {
    setHealth({ ops: { ...HEALTH().ops, machinesRunning: 3, watchdogRequeues: 2,
                       gateSlots: { claude: 2, pp_narrate: 0, pp_categorize: 1, pp_redact: 0 } } });
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByText(/claude 2\/5.*pp_redact 0\/5/)).toBeInTheDocument();
    // 「在飞机器」与「在飞机器容量」同页，用精确匹配区分标签与那张配置卡的标题
    const label = screen.getByText(/^(在飞机器|Machines in flight)$/);
    expect(within(label.parentElement!).getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/看门狗回收/)).toBeInTheDocument();
  });
});

// ── 告警记录（方案三）────────────────────────────────────────────────────────
// 守的是「不许丢」：漏看一次要能查到、被冷却压掉的次数要看得见、发信失败要看得见。
// 这三件事改造前**一件都没有**——13 处告警全是发一封邮件就没了。
const ALERT = (over: Record<string, unknown> = {}) => ({
  id: 1, tier: "act", subject: "退款回执无法关联到充值（已放行事件）", body: "详情",
  mailed: "sent", mailError: null, suppressed: 0, at: "08-14 09:12",
  handled: false, handledBy: null, ...over,
});
// mock **照后端的规则过滤**：unhandled=true 时只回 act 且未处理的。
// 不这么做的话「待办条只接一档」那条用例根本没验到东西——前端不自己过滤（同一套规则
// 算两遍必然漂），它靠的正是请求时带对了参数，而那就是这里要守的接缝。
const setAlerts = (items: Record<string, unknown>[]) =>
  (getAdminAlerts as unknown as ReturnType<typeof vi.fn>)
    .mockImplementation((_days?: number, unhandled?: boolean) => Promise.resolve({
      retentionDays: 180,
      items: unhandled ? items.filter((a) => a.tier === "act" && !a.handled) : items,
    }));

describe("AdminPage · 告警记录", () => {
  const goResources = () => fireEvent.click(screen.getByRole("button", { name: "资源" }));

  beforeEach(() => {
    (getAdminAlerts as unknown as ReturnType<typeof vi.fn>).mockReset();
    setAlerts([]);
  });

  it("发信失败要看得见——此前只在日志里留一行字，你不会知道有一封没发出去", async () => {
    setAlerts([ALERT({ mailed: "failed", mailError: "provider 502" })]);
    wrap(<AdminPage />);
    goResources();
    // **按区块取**：未处理的 act 告警在待办条上也有一份（有意的——条上告诉你有这回事，
    // 区块里告诉你发信到底成没成），全局取会先命中待办条那一行
    const section = (await screen.findByRole("heading", { name: "告警" })).parentElement!.parentElement!;
    expect(within(section).getByText(/邮件发送失败/)).toBeInTheDocument();
  });

  // 「冷却期内又触发 N 次」是改造前直接丢掉的数字：旧实现冷却命中就 return，
  // 于是「这一小时其实报了 40 次」这个事实永远没了——而「偶发一次」和「一直在响」
  // 该做的处置完全不同。
  it("被冷却压掉的次数要显示出来", async () => {
    setAlerts([ALERT({ tier: "watch", subject: "转录失败率偏高", suppressed: 7 })]);
    wrap(<AdminPage />);
    goResources();
    expect(await screen.findByText(/冷却期内又触发 7 次/)).toBeInTheDocument();
  });

  // 2026-09-03 生产实见：待办条说「需要处理 1 件」，列表默认 7 天、勾上「只看未处理」却显示
  // 「这段时间没有告警」——那条是 08-20 的，挂了两周。未处理的东西没有「过期」这回事，
  // 所以勾上之后时间按钮整个藏掉：留着会让人以为它还在过滤。
  it("勾了「只看未处理」，时间范围按钮消失，请求带 unhandled=true", async () => {
    setAlerts([]);
    wrap(<AdminPage />);
    goResources();
    await screen.findByRole("heading", { name: "告警" });
    expect(screen.getByRole("button", { name: "7 天" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "只看未处理" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "7 天" })).toBeNull());
    expect(screen.queryByRole("button", { name: "180 天" })).toBeNull();
    const calls = (getAdminAlerts as unknown as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls[calls.length - 1][1]).toBe(true);
  });

  it("「发一份增长周报」点一下就调接口，发完刷新列表（周报本身落在这张表里）", async () => {
    growthReportSpy.mockReset().mockResolvedValue({ mailed: true, subject: "增长周报 2026-W36（手动）", week: "2026-W36" });
    setAlerts([]);
    wrap(<AdminPage />);
    goResources();
    await screen.findByRole("heading", { name: "告警" });
    const before = (getAdminAlerts as unknown as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "发一份增长周报" }));
    expect(await screen.findByRole("button", { name: "周报已发到邮箱" })).toBeInTheDocument();
    expect(growthReportSpy).toHaveBeenCalledTimes(1);
    expect((getAdminAlerts as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(before);
  });

  it("「已处理」只给要动手那一档——其余两档自己会消失，多点一次是白费动作", async () => {
    setAlerts([ALERT({ id: 1, tier: "act" }),
               ALERT({ id: 2, tier: "watch", subject: "余额偏低" }),
               ALERT({ id: 3, tier: "fyi", subject: "同企业域名新注册（销售线索）" })]);
    wrap(<AdminPage />);
    goResources();
    await screen.findByText(/余额偏低/);
    expect(screen.getAllByRole("button", { name: "已处理" })).toHaveLength(1);
  });

  it("已经标过的不再给按钮，并显示是谁处理的", async () => {
    setAlerts([ALERT({ handled: true, handledBy: "me@x.com" })]);
    wrap(<AdminPage />);
    goResources();
    expect(await screen.findByText(/已处理 · me@x\.com/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "已处理" })).toBeNull();
  });
});

describe("AdminPage · 待办条只接「要动手」那一档", () => {
  beforeEach(() => {
    (getAdminAlerts as unknown as ReturnType<typeof vi.fn>).mockReset();
    p3HealthSpy.mockReset().mockResolvedValue({
      windowHours: 24, counts: { ok: 3 }, attempted: 3, successRate: 1, capped: null,
      authFailing: false, authNote: null, lastAttempt: "ok", last: null, lastOkAt: null,
      lastCappedAt: null, lastCappedWindow: null, activeSlots: 0, slotLimit: 5,
    });
  });

  it("未处理的「要动手」告警进待办条", async () => {
    setAlerts([ALERT({ subject: "🔴 Paddle 鉴权失败——收款/退款已中断" })]);
    wrap(<AdminPage />);
    expect(await screen.findByText(/Paddle 鉴权失败/)).toBeInTheDocument();
  });

  // 待办条按「活状态」已经报了余额偏低（AdminBalance.low），告警记录里再报一遍
  // 就是同一件事出现两行——而待办条的可信度是它唯一的资产。
  it("「要留意」不进待办条：它要么已有活信号，要么自己会好", async () => {
    setAlerts([ALERT({ tier: "watch", subject: "转录失败率偏高：近 60 分钟 4/9" })]);
    wrap(<AdminPage />);
    // 唯一的信号是一条 watch 告警 → 待办条应当保持「没有需要处理的事」。
    // 这比「数字没变大」更强：它证明 watch 一条都没漏进来。
    expect(await screen.findByText(/没有需要处理的事/)).toBeInTheDocument();
    expect(screen.queryByText(/转录失败率偏高/)).toBeNull();
  });

  it("待办条上带出「还触发了 N 次」——一次和一直在响要分得开", async () => {
    setAlerts([ALERT({ subject: "退款扣减短缺，请人工对账", suppressed: 3 })]);
    wrap(<AdminPage />);
    expect(await screen.findByText(/退款扣减短缺，请人工对账（还触发了 3 次）/)).toBeInTheDocument();
  });
});

// ── 用户 Tag（方案四）──────────────────────────────────────────────────────
// 列表守的是「三种人分得开」，下钻守的是隐私边界与账目口径，调整守的是钱。
const USER = (over: Record<string, unknown> = {}) => ({
  email: "duner@acme.com", balanceCents: 4210, createdAt: "2026-07-02",
  freeMinutesGranted: 300, freeMinutesLeft: 0, freeLimited: false,
  topupCents: 6000, spentCents: 1790, jobs: 37, lastActive: "08-14 12:09", ...over,
});
const setUsers = (items: Record<string, unknown>[]) =>
  (getAdminUsers as unknown as ReturnType<typeof vi.fn>)
    .mockResolvedValue({ adjustMaxCents: 5000, items });
const setDetail = (over: Record<string, unknown> = {}) =>
  (getAdminUserDetail as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    ...USER(), done: 35, failed: 2, postprocess: 3, glossaries: 1, pendingRefunds: 0,
    ledger: [], jobs_: [], ...over,
  });

describe("AdminPage · 用户列表", () => {
  const goUsers = () => fireEvent.click(screen.getByRole("button", { name: "用户" }));

  beforeEach(() => {
    (getAdminUsers as unknown as ReturnType<typeof vi.fn>).mockReset();
    (getAdminUserDetail as unknown as ReturnType<typeof vi.fn>).mockReset();
    setUsers([]);
    setDetail();
  });

  // 列表存在的全部理由：光看余额「$0.00」分不出「花完了」和「从没充过」，
  // 而这两种人一个该跟进、一个该观察，差着一次销售机会。
  it("三种人各有各的说法，不是一样的 $0.00", async () => {
    setUsers([
      USER({ email: "paid@x.com" }),
      USER({ email: "free@x.com", topupCents: 0, spentCents: 0, balanceCents: 0, jobs: 4 }),
      USER({ email: "idle@x.com", topupCents: 0, spentCents: 0, balanceCents: 0, jobs: 0, lastActive: null }),
    ]);
    wrap(<AdminPage />);
    goUsers();
    expect(await screen.findByText(/充值 \$60\.00 · 消费 \$17\.90/)).toBeInTheDocument();
    expect(screen.getByText(/未充值 · 只用过免费额度/)).toBeInTheDocument();
    expect(screen.getByText(/注册后没有动静/)).toBeInTheDocument();
  });

  it("被降额的账号标出来——那是防滥用闸命中的痕迹", async () => {
    setUsers([USER({ freeLimited: true })]);
    wrap(<AdminPage />);
    goUsers();
    expect(await screen.findByText(/免费额度已降额/)).toBeInTheDocument();
  });

  // Q4：列表是扫的、下钻是查的。文件名摆在列表上等于每次打开用户页
  // 都在浏览所有人的访谈主题。
  it("列表里不出现文件名，下钻里才出现", async () => {
    setUsers([USER()]);
    setDetail({ jobs_: [{ id: "j1", fileName: "访谈-03.m4a", status: "done", lang: "zh",
                          durationSec: 2100, attempts: 1, errorPublic: null, at: "08-14 12:09" }] });
    wrap(<AdminPage />);
    goUsers();
    await screen.findByText("duner@acme.com");
    expect(screen.queryByText(/访谈-03\.m4a/)).toBeNull();
    fireEvent.click(screen.getByText("duner@acme.com"));
    expect(await screen.findByText(/访谈-03\.m4a/)).toBeInTheDocument();
  });
});

describe("AdminPage · 用户下钻与余额调整", () => {
  const openUser = async () => {
    fireEvent.click(screen.getByRole("button", { name: "用户" }));
    fireEvent.click(await screen.findByText("duner@acme.com"));
  };

  beforeEach(() => {
    (getAdminUsers as unknown as ReturnType<typeof vi.fn>).mockReset();
    (getAdminUserDetail as unknown as ReturnType<typeof vi.fn>).mockReset();
    adjustSpy.mockReset().mockResolvedValue({ balanceCents: 4710, beforeCents: 4210 });
    setUsers([USER()]);
    setDetail();
  });

  // 赠送额度不是钱：显示成 +$0.00 会让人以为白送了钱，而「这人一共给过我们多少」
  // 是这张表唯一要回答的问题。
  it("注册赠送在账目里明写「不是钱」，不占金额栏", async () => {
    setDetail({ ledger: [{ id: 1, kind: "topup", amountCents: 0, fileName: null, lang: null,
                           durationSec: null, source: "bonus", note: null, operator: null,
                           refundedCents: 0, at: "07-02 11:20" }] });
    wrap(<AdminPage />);
    await openUser();
    expect(await screen.findByText("赠送额度")).toBeInTheDocument();
    expect(screen.getByText(/注册赠送，不计入付费/)).toBeInTheDocument();
  });

  // 「进账本」这件事只做一半的话，三个月后那笔钱照样解释不了
  it("手工调整的理由和操作人跟这笔流水一起显示", async () => {
    setDetail({ ledger: [{ id: 2, kind: "adjust", amountCents: 500, fileName: null, lang: null,
                           durationSec: null, source: "admin", note: "转录质量问题补偿",
                           operator: "ops@x.com", refundedCents: 0, at: "08-14 13:00" }] });
    wrap(<AdminPage />);
    await openUser();
    expect(await screen.findByText("手工调整")).toBeInTheDocument();
    const row = screen.getByText(/转录质量问题补偿/);
    expect(row.textContent).toMatch(/ops@x\.com/);
  });

  // ── 测试号工具（2026-09-03）──
  it("测试域名的号也标「测试号」，只标不过滤", async () => {
    setUsers([USER({ email: "test-a@transcribe.solutions", isTest: true }), USER({ email: "real@acme.com" })]);
    wrap(<AdminPage />);
    fireEvent.click(screen.getByRole("button", { name: "用户" }));
    await screen.findByText("test-a@transcribe.solutions");
    expect(screen.getAllByText("测试号")).toHaveLength(1);
    expect(screen.getByText("real@acme.com")).toBeInTheDocument();
  });

  it("测试号能模拟消耗：分钟数交给后端，回话原样显示", async () => {
    setDetail({ isTest: true });
    simulateSpy.mockResolvedValue({ freeSecondsUsed: 10800, paidSeconds: 1200, chargedCents: 100, balanceCents: 1400 });
    wrap(<AdminPage />);
    await openUser();
    fireEvent.change(await screen.findByLabelText("模拟分钟数"), { target: { value: "200" } });
    fireEvent.click(screen.getByRole("button", { name: "模拟消耗" }));
    await vi.waitFor(() => expect(simulateSpy).toHaveBeenCalledWith("duner@acme.com", 200));
    expect(await screen.findByText(/已记 200 分钟：免费 180 分钟 \+ 付费 \$1\.00，余额 \$14\.00/)).toBeInTheDocument();
  });

  it("真实用户没有「模拟消耗」，但有「重置」；重置要把邮箱原样打一遍才点得动", async () => {
    setDetail({ isTest: false, isAdmin: false });
    resetSpy.mockResolvedValue({ users: 1 });
    wrap(<AdminPage />);
    await openUser();
    const btn = await screen.findByRole("button", { name: "重置为未注册" });
    expect(screen.queryByRole("button", { name: "模拟消耗" })).toBeNull();
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("重置确认邮箱"), { target: { value: "duner@acme.com" } });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    await vi.waitFor(() => expect(resetSpy).toHaveBeenCalledWith("duner@acme.com", "duner@acme.com"));
  });

  it("管理员账号没有「重置」", async () => {
    setDetail({ isAdmin: true });
    wrap(<AdminPage />);
    await openUser();
    await screen.findByRole("button", { name: "模拟消耗" });
    expect(screen.queryByRole("button", { name: "重置为未注册" })).toBeNull();
  });

  it("调整按分提交，加和扣都走同一个口", async () => {
    wrap(<AdminPage />);
    await openUser();
    await screen.findByRole("button", { name: "记一笔" });
    fireEvent.change(screen.getByLabelText("调整金额"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("调整理由"), { target: { value: "补偿" } });
    fireEvent.click(screen.getByRole("button", { name: "记一笔" }));
    await vi.waitFor(() => expect(adjustSpy).toHaveBeenCalledWith("duner@acme.com", 500, "补偿"));

    fireEvent.click(screen.getByRole("button", { name: "扣" }));
    fireEvent.change(screen.getByLabelText("调整金额"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("调整理由"), { target: { value: "对账纠错" } });
    fireEvent.click(screen.getByRole("button", { name: "记一笔" }));
    await vi.waitFor(() => expect(adjustSpy).toHaveBeenCalledWith("duner@acme.com", -300, "对账纠错"));
  });

  // 三道闸都在后端（同一套规则算两遍必然漂）。前端只负责把后端的原话摆出来——
  // 「是不是多打了一个零」这种话，改写成「金额超限」就没用了。
  it("后端拒绝时原样显示它的话，不自己另编一句", async () => {
    adjustSpy.mockRejectedValue(new Error("单次调整不能超过 $50.00（本次 $500.00）——是不是多打了一个零？"));
    wrap(<AdminPage />);
    await openUser();
    await screen.findByRole("button", { name: "记一笔" });
    fireEvent.change(screen.getByLabelText("调整金额"), { target: { value: "500" } });
    fireEvent.change(screen.getByLabelText("调整理由"), { target: { value: "补偿" } });
    fireEvent.click(screen.getByRole("button", { name: "记一笔" }));
    expect(await screen.findByText(/是不是多打了一个零/)).toBeInTheDocument();
  });

  it("在途退款要提示——那部分额度已经被占住了", async () => {
    setDetail({ pendingRefunds: 2 });
    wrap(<AdminPage />);
    await openUser();
    expect(await screen.findByText(/有 2 笔退款在途/)).toBeInTheDocument();
  });
});


// ── 增长 Tag（Growth 需求单批次 B）──────────────────────────────────────────────
const tri = (a: number, b: number, c: number) => ({ thisWeek: a, lastWeek: b, total: c });
const GROWTH = {
  week: "2026-W36", range: { thisWeek: ["2026-08-31", "2026-09-06"], lastWeek: ["2026-08-24", "2026-08-30"] },
  generatedAt: "2026-09-03T12:00+08:00", excluded: { adminAccounts: 1 },
  ladder: { monthNetUsd: 40, tier: 10, steps: [0, 10, 100, 500, 1000, 5000, 10000] },
  funnel: {
    signup: tri(3, 1, 11), signupCorp: tri(1, 0, 3), signupPersonal: tri(2, 1, 6), signupNoQuota: tri(0, 0, 2),
    signupSources: { thisWeek: { test: 1, unrecorded: 2 }, lastWeek: { unrecorded: 1 }, total: { unrecorded: 10, test: 1 } },
    referredSignups: tri(1, 0, 1),
    firstUpload: tri(2, 1, 5), signupToUploadMedianHours: { thisWeek: 2, total: 0 },
    firstDone: tri(2, 1, 5), freeExhausted: tri(0, 0, 0), firstTopup: tri(0, 1, 1),
    firstTopupAmounts: { thisWeek: { "10": 0, "30": 0, "60": 0, other: 0 }, total: { "10": 1, "30": 0, "60": 0, other: 0 } },
    exhaustedToTopupMedianDays: { thisWeek: null, total: null },
    activePaying: { thisMonth: 0, lastMonth: 1, total: 1 },
    secondTopup: tri(0, 0, 0), firstToSecondTopupMedianDays: { thisWeek: null, total: null },
    returnDone: tri(0, 0, 0),
    soldHours: { thisWeek: 0, lastWeek: 0, thisMonth: 0, lastMonth: 0.5, total: 0.5 },
  },
  money: Object.fromEntries(["thisWeek", "lastWeek", "thisMonth", "lastMonth", "total"].map((k) =>
    [k, { topupCents: k === "thisMonth" ? 4000 : 0, refundCents: 0, netTopupCents: k === "thisMonth" ? 4000 : 0, soldCents: 0 }])),
  cost: { thisWeek: { cny: 5.05, usdCents: 70, marginPct: null }, lastWeek: { cny: 0, usdCents: 0, marginPct: null },
          thisMonth: { cny: 5.05, usdCents: 70, marginPct: null }, lastMonth: { cny: 0, usdCents: 0, marginPct: null },
          usdCnyRate: 7.2, note: "每单成本只含 ASR 与 DeepSeek；Claude 订阅费不在内，毛利率偏乐观" },
  freeGrantSeries: [{ date: "2026-09-01", corp: 180, personal: 60 }],
  gifts: { unlocked: tri(1, 0, 1),
           cents: { thisWeek: { referee: 500, referrer: 500, manual: 0 }, lastWeek: { referee: 0, referrer: 0, manual: 0 }, total: { referee: 500, referrer: 500, manual: 2000 } },
           referrerClusters: [{ code: "AB2CD3EF", accounts: 4, paid: 2 }], minAccounts: 2 },
  report: "增长周报 2026-W36 …",
};

describe("AdminPage · 增长 Tag", () => {
  beforeEach(() => { growthSpy.mockReset().mockResolvedValue(GROWTH); });

  it("第五个 Tag「增长」在「用户」前面，顶上是本月净充值与台阶，漏斗十行三列", async () => {
    wrap(<AdminPage />);
    const tabs = screen.getAllByRole("button").map((b) => b.textContent);
    expect(tabs.indexOf("增长")).toBeLessThan(tabs.indexOf("用户"));
    fireEvent.click(screen.getByRole("button", { name: "增长" }));
    expect((await screen.findAllByText("$40.00")).length).toBeGreaterThan(0);   // 本月净充值（钱的表里也有一格）
    expect(screen.getByText("$10")).toBeInTheDocument();                     // 台阶
    expect(screen.getByText("1 注册")).toBeInTheDocument();
    expect(screen.getByText("8 售出小时")).toBeInTheDocument();
    expect(screen.getByText(/来源（本周）：/).textContent).toContain("test 1");
    expect(screen.getByText("经推荐注册")).toBeInTheDocument();
    expect(screen.getByText("毛利率")).toBeInTheDocument();
    // 礼金四行（批次 C）：解锁次数 / 发放总额 / 同一推荐人下的账号数——用码不用邮箱
    expect(screen.getByText("9 礼金解锁（被推荐人首充）")).toBeInTheDocument();
    expect(screen.getByText(/礼金发放（累计）：/).textContent).toContain("手工 $20.00");
    // ⚠️ 门槛（≥N）由后端下发（`gifts.minAccounts`），不许在前端写死——2026-09-03 由 3 降到 2
    expect(screen.getByText(/同一推荐人下 ≥2 个账号：/).textContent).toContain("码 AB2CD3EF 4 个号（2 个充过值）");
    // ⚠️ 两句话之间必须真的隔开（2026-09-03 生产实见「其他 1免费用尽」「手工 $0.00本周」）。
    // JSX 会把**紧挨换行**的空白吃掉——行首行尾都算，而 JS 的 `\s` 包含全角空格 U+3000，
    // 所以分隔符只能写成表达式容器 `{"　"}`。⚠️ 判据是**分隔符在不在**（正向），
    // 不是「有没有糊在一起」（反向）：第一版写成反向正则，而两句之间本来就隔着一个半角空格，
    // 正则永远匹配不上 —— 代码没修好、测试却是绿的，浏览器一开就看见还糊着。
    const body = document.body.textContent ?? "";
    for (const sep of ["　免费用尽 →", "　本周：", "　同一信箱的自荐"]) {
      expect(body, `分隔符没了，两句话会糊在一起：${sep}`).toContain(sep);
    }
    expect(growthSpy).toHaveBeenCalledWith("", false);
  });

  it("「含测试号」开关会带参数重新取数——它是临时看礼金那几行动不动的口子", async () => {
    wrap(<AdminPage />);
    fireEvent.click(screen.getByRole("button", { name: "增长" }));
    await screen.findByText("1 注册");
    fireEvent.click(screen.getByLabelText("含测试号"));
    await vi.waitFor(() => expect(growthSpy).toHaveBeenCalledWith("", true));
  });

  it("这一页没有任何邮箱——它是给增长团队看的口径", async () => {
    wrap(<AdminPage />);
    fireEvent.click(screen.getByRole("button", { name: "增长" }));
    await screen.findByText("1 注册");
    const page = document.body.textContent ?? "";
    // 顶栏/侧栏没有渲染在 AdminPage 里，所以整页正文里出现 @ 就是增长页漏了
    expect(page.includes("@")).toBe(false);
  });

  it("「上一周」按后端给的周号往前拨，不自己算日期", async () => {
    wrap(<AdminPage />);
    fireEvent.click(screen.getByRole("button", { name: "增长" }));
    await screen.findByText("1 注册");
    fireEvent.click(screen.getByRole("button", { name: "← 上一周" }));
    await waitFor(() => expect(growthSpy).toHaveBeenLastCalledWith("2026-W35", false));
  });
});

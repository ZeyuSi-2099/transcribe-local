import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { getAdminOverview, getAdminBalances, getAdminWorkflow, getAdminHealth,
         getAdminAlerts } from "../lib/api";

// 本机版（与线上不同）：充值退款、用户、增长三组与「发增长周报」随功能一起去掉。
// 运行面板本地化（2026-09-15）又去掉了这几组，本机没有这些功能：
//   · 融合引擎健康度 / 认证失效 / 状态取最近一次 / 融合引擎配置 —— Claude 订阅的成败统计、探活、并发闸与强制档位，
//     本机定字模型是「设置」里选的，没有订阅名额、没有降级阶梯、不起云机器探活；
//   · 会到期的凭证、在飞机器容量 —— Paddle 与 Claude 令牌到期、Fly 机器数，本机都没有；
//   · 待办条里的认证失效、撞顶降级、凭证临期、登录验证码用量、降级比例 —— 同上；
//   · 工作流里的派单前台指纹口径、27 门语种编排、P0/P2「还没上机器」—— 本机只有一份代码、中文一套、不单独计时。
// 换成本机的：「这台电脑」一栏、模型后端密钥没设与磁盘快满进待办条、工作流节点照本机流水线画、本机引擎代号照常显示。
const probeSpy = vi.hoisted(() => vi.fn());
const localResSpy = vi.hoisted(() => vi.fn());
// setSpy 经 vi.hoisted 提升，工厂与用例都能访问；fixtures 放工厂内（避免 vi.mock 提升后引用未初始化变量）。
const setSpy = vi.hoisted(() => vi.fn());
const refundSpy = vi.hoisted(() => vi.fn());
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
    getAdminLocalResources: (...a: unknown[]) => localResSpy(...a),
    probeBackend: (...a: unknown[]) => probeSpy(...a),
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
      ops: { watchdogRequeues: 0, recent60: { done: 0, failed: 0 } },
      alerts: { engines: [], degrade: null },
    }),
    getAdminWorkflow: vi.fn().mockResolvedValue({
      prompts: { "merge-saas": { path: "src/transcribe_local/_merge_zh.py · PROMPT_SAAS", lines: 132, sha: "abcd1234" } },
      backend: null,
      params: {},
    }),
    getAdminPrompt: vi.fn().mockResolvedValue({ id: "merge-saas", path: "x", sha: "abcd1234", text: "规则正文" }),
  };
});

import { AdminPage } from "./AdminPage";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

// 「这台电脑」的默认样子：一切正常（模型齐、磁盘够、钥匙设了）——这样别的用例的待办条不会被它带出噪音
const BACKEND = (over: Record<string, unknown> = {}) => ({
  preset: "deepseek", kind: "openai", model: "deepseek-flash", host: "api.deepseek.com",
  local: false, webSearch: true, keyEnv: "DEEPSEEK_API_KEY", keySet: true, ...over,
});
const LOCAL_RES = (over: Record<string, unknown> = {}) => ({
  models: { ready: true, items: [{ id: "firered_asr2", sizeMb: 1200, installed: true }, { id: "silero_vad", sizeMb: 2, installed: true }],
            installedMb: 1202, missingMb: 0, cacheDir: "/Users/x/.cache/transcribe-local/models" },
  disk: { path: "/Users/x/.transcribe-local", freeGb: 116.5, totalGb: 460.4, dataMb: 12.3 },
  memory: { totalMb: 16384, availableMb: 3658, reserveMb: 4096, parallel: 3, parallelWhy: "自动" },
  backend: BACKEND(),
  dataFlow: [{ what: "audio", dest: "local", host: "" }, { what: "transcript", dest: "remote", host: "api.deepseek.com" }],
  ...over,
});
beforeEach(() => {
  localResSpy.mockReset().mockResolvedValue(LOCAL_RES());
  probeSpy.mockReset();
});

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

// 本机版：本机引擎代号不在线上清单里。只按线上清单过滤的话，「各引擎」那一栏在本机永远是空的（修之前就是这样）。
describe("AdminPage · 本机引擎代号", () => {
  it("展开明细列出本机四路的状态与耗时；本机识别不花钱，不出一个空的「P1:」", async () => {
    (getAdminOverview as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      jobs: [], failures: [],
      recent: [{
        id: "j1", fileName: "厨房对话.m4a", userEmail: "local@transcribe.local", doneAt: "09-15 09:48",
        createdAt: "09-15 09:48", recordingType: "all", lang: "zh",
        metrics: { durationSec: 48,
                   engines: { FRED2: { status: "done", sec: 12.3 }, ZIPC: { status: "done", sec: 3.1 },
                              PARA: { status: "done", sec: 2.4 }, QWEN3: { status: "failed", sec: null } },
                   cost: { p1: {}, p3: 0.02, p3_engine: "deepseek-flash", total: 0.02 } },
      }],
      summary: { running: 0, queued: 0, doneToday: 1, failedToday: 0 },
    });
    wrap(<AdminPage />);
    await userEvent.click(await screen.findByText("厨房对话.m4a"));
    expect(screen.getByText(/FRED2 ✓ 12\.3s/)).toBeInTheDocument();
    expect(screen.getByText(/QWEN3 ✗/)).toBeInTheDocument();
    expect(screen.getByText(/P3 deepseek-flash ¥0\.02/)).toBeInTheDocument();
    expect(screen.queryByText(/P1:/)).toBeNull();
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

  it("运营页要看得到后处理：文件、步骤、状态；本机不收费，不显示价钱", async () => {
    const box = await wrapWith([PP()]);
    expect(box.getByText("某访谈.flac")).toBeInTheDocument();
    expect(box.getByText("视角转换")).toBeInTheDocument();
    expect(box.getByText(/完成/)).toBeInTheDocument();
    expect(box.queryByText("$2.00")).toBeNull();
    expect(box.getByText("API 花费")).toBeInTheDocument();
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

  // 本机版：线上排第一的是「Claude 订阅认证失效」；本机对应的是模型后端要的钥匙没设——同样是不动手永远不会好
  it("模型后端要的密钥没设要进来，并写明哪个变量、影响哪几环", async () => {
    noBalanceIssue();
    localResSpy.mockResolvedValue(LOCAL_RES({ backend: BACKEND({ keySet: false }) }));
    wrap(<AdminPage />);
    expect(await screen.findByText(/deepseek-flash 要的环境变量 DEEPSEEK_API_KEY 没设 —— 定字、术语库助手、后处理都调不通/)).toBeInTheDocument();
  });

  it("本机模型不要密钥——变量名空着也不报", async () => {
    noBalanceIssue();
    localResSpy.mockResolvedValue(LOCAL_RES({ backend: BACKEND({ local: true, keyEnv: "", keySet: false, host: "127.0.0.1" }) }));
    wrap(<AdminPage />);
    expect(await screen.findByText(/没有需要处理的事/)).toBeInTheDocument();
  });

  it("磁盘快满要进来——录音和稿子都写在这台电脑上，转到一半写不下就是一单白跑", async () => {
    noBalanceIssue();
    localResSpy.mockResolvedValue(LOCAL_RES({ disk: { path: "/x", freeGb: 3.2, totalGb: 460, dataMb: 1 } }));
    wrap(<AdminPage />);
    expect(await screen.findByText(/磁盘只剩 3\.2 GB/)).toBeInTheDocument();
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
    expect(await screen.findByText(/1 单排队已超 7 小时（满 24 小时判失败）/)).toBeInTheDocument();
    expect(screen.queryByText(/返还预扣/)).toBeNull();   // 本机不收费，没有预扣可返
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
    expect(within(card).getByText(/满 24 小时判失败/)).toBeInTheDocument();
    expect(within(card).queryByText(/返还预扣/)).toBeNull();
  });
});

// ── 工作流 Tag ────────────────────────────────────────────────────────────────
describe("AdminPage · 工作流节点", () => {
  const WF = (over: Record<string, unknown> = {}) => ({
    prompts: {
      "pp-redact": { path: "backend/pipeline/_pp_prompts.py · PP_REDACT", lines: 382, sha: "de891035" },
      "merge-saas": { path: "src/transcribe_local/_merge_zh.py · PROMPT_SAAS", lines: 132, sha: "abcd1234" },
    },
    backend: BACKEND(),
    params: {
      p1: { engines: [{ id: "firered_asr2", tag: "FRED2", route: "AED 自回归" }, { id: "paraformer_2023", tag: "PARA", route: "非自回归" }],
            parallel: 3, numThreads: 2, repeatThreshold: 10, maxRetry: 3 },
      p3: { workflow: "two_stage", roundTokens: 2100, timeoutSec: 1200 },
      pp: { redactBudget: 6000, redactConc: 6, redactPasses: 1, narrateBudget: 6000 },
    },
    ...over,
  });
  const nodeOf = (title: HTMLElement) => title.parentElement!.parentElement!.parentElement!;

  beforeEach(() => {
    (getAdminWorkflow as unknown as ReturnType<typeof vi.fn>).mockReset().mockResolvedValue(WF());
  });

  it("节点照本机流水线画：术语库助手 · 转码与切块 · 多路 ASR · 分歧册 · 融合 · 后处理两步", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    for (const title of ["术语库助手", "P0 转码与切块", "P1 多路 ASR", "分歧册", "P3 融合", "后处理 · 视角转换", "后处理 · 脱敏"]) {
      expect(await screen.findByText(title)).toBeInTheDocument();
    }
    // 云端那几样本机没有，留着会让人去查一条不存在的链路
    expect(screen.queryByText("P2 对齐")).toBeNull();
    expect(screen.queryByText(/派单前台/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^(探活|Probe)$/ })).toBeNull();
  });

  it("P3 默认展开，写明用的是哪个模型后端、钥匙设没设", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const node = nodeOf(await screen.findByText("P3 融合"));
    expect(within(node).getByText(/模型后端 deepseek-flash @ api\.deepseek\.com/)).toBeInTheDocument();
    expect(within(node).getByText(/DEEPSEEK_API_KEY 读取 · 已设置/)).toBeInTheDocument();
    expect(within(node).getByText(/2100 tok/)).toBeInTheDocument();
  });

  it("本机模型不显示钥匙那一行——它根本不要钥匙，写「未设置」会吓人", async () => {
    (getAdminWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      WF({ backend: BACKEND({ model: "qwen3:8b", host: "127.0.0.1", local: true, keyEnv: "", keySet: false }) }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const node = nodeOf(await screen.findByText("P3 融合"));
    expect(within(node).getAllByText(/qwen3:8b · 本机/).length).toBeGreaterThan(0);
    expect(within(node).queryByText(/密钥从环境变量/)).toBeNull();
  });

  it("Claude 订阅：定字写 claude -p，术语库助手与后处理标「用不了」", async () => {
    (getAdminWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(
      WF({ backend: BACKEND({ kind: "claude_p", model: "opus", host: "", keyEnv: "", keySet: false }),
           params: { ...WF().params, p3: { claudeModel: "opus", claudeEffort: "medium", claudeTimeoutSec: 3600 } } }));
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const p3 = nodeOf(await screen.findByText("P3 融合"));
    expect(within(p3).getByText(/claude -p · --model opus · --effort medium/)).toBeInTheDocument();
    const title = screen.getByText("后处理 · 脱敏");
    fireEvent.click(title);
    expect(within(nodeOf(title)).getByText(/用不了：这一环要 API 或本机模型/)).toBeInTheDocument();
  });

  it("P1 列出本机引擎代号与解码路线——排查某一路时第一眼要看的就是它", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/2 台本机引擎 · 同时跑 3 台/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("P1 多路 ASR"));
    expect(await screen.findByText("firered_asr2 · AED 自回归")).toBeInTheDocument();
    expect(screen.getByText("FRED2")).toBeInTheDocument();
  });

  it("参数来自后端给的真实配置，前端不自己写死", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("后处理 · 脱敏"));
    expect(await screen.findByText(/每批 6000 字 · 并发 6 · 扫 1 遍/)).toBeInTheDocument();
  });

  it("提示词带路径·行数·指纹，并能看全文——只显示内容而不带指纹是假的安心", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("后处理 · 脱敏"));
    expect(await screen.findByText(/382 行 · sha de891035/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /查看全文/ })[0]);
    expect(await screen.findByText("规则正文")).toBeInTheDocument();
  });

  it("顶部写明参数口径：这台电脑上实际生效的配置", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    expect(await screen.findByText(/实际生效的配置/)).toBeInTheDocument();
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
  ops: { watchdogRequeues: 0, recent60: { done: 0, failed: 0 } },
  alerts: { engines: [], degrade: null },
  ...over,
});
const setHealth = (over: Record<string, unknown> = {}) =>
  (getAdminHealth as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(HEALTH(over));

describe("AdminPage · 节点近况", () => {
  beforeEach(() => {
    (getAdminHealth as unknown as ReturnType<typeof vi.fn>).mockReset();
    setHealth();
    (getAdminWorkflow as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ prompts: {}, backend: null, params: {} });
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

  // 本机版：模型是「设置」里选的，没有「第一档 / 降级」之分——只列各模型出了几单，不算降级率
  it("P3 列出各模型出了几单，不算降级率", async () => {
    setHealth({ p3: { tiers: { "deepseek-flash": 6, "qwen3:8b": 4 }, total: 10, degradedRatio: 1, known: 10 } });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    // 各档写在各自的 span 里，按整个节点的文字断言
    const node = (await screen.findByText("P3 融合")).parentElement!.parentElement!.parentElement!;
    await waitFor(() => expect(node.textContent).toMatch(/出稿模型\s*deepseek-flash 6\s*·\s*qwen3:8b 4/));
    expect(screen.queryByText(/降级率/)).toBeNull();
  });

  it("后处理按步分开显示成败，不汇总", async () => {
    setHealth({ pp: { redact: { total: 5, ok: 4, failed: 1, degraded: 0, dsCostCny: 0 } } });
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    fireEvent.click(await screen.findByText("后处理 · 脱敏"));
    // 注意：断言里用 \s* 而不是全角空格——testing-library 会把全角空格归一成普通空格
    expect(await screen.findByText(/4\/5 成功/)).toBeInTheDocument();
    expect(screen.getByText(/失败 1/)).toBeInTheDocument();
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

  // **空格子有两种，界面上必须能分辨**：一种是「窗口内真的没样本」，另一种是「本机根本不单独记这一段」。
  // 混成同一句话的话，看到分歧册空着只能猜是没人用还是没记——而这两件事该做的动作完全不同。
  it("转码与切块、分歧册没数时说清是「本机不单独计时」，不是「没有样本」", async () => {
    wrap(<AdminPage />);
    goTag(TAG_WORKFLOW);
    const title = await screen.findByText("分歧册");
    fireEvent.click(title);
    const node = title.parentElement!.parentElement!.parentElement!;
    expect(await within(node).findByText(/本机不单独记这一段的耗时/)).toBeInTheDocument();
    expect(within(node).queryByText(/窗口内没有样本/)).toBeNull();
  });
});

describe("AdminPage · 运行信号与新增待办", () => {
  beforeEach(() => {
    (getAdminHealth as unknown as ReturnType<typeof vi.fn>).mockReset();
    setHealth();
    (getAdminBalances as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  it("引擎悄悄坏了进待办条——这是唯一能提前发现它的信号", async () => {
    setHealth({ alerts: { engines: [{ tag: "QWEN3", pct: 78, basePct: 99, ok: 31, total: 40 }], degrade: null } });
    wrap(<AdminPage />);
    expect(await screen.findByText(/QWEN3 近 24h 成功率 78%（平时 99%，31\/40）/)).toBeInTheDocument();
  });

  it("资源页的运行信号只留本机有的：看门狗回收、近 60 分钟", async () => {
    setHealth({ ops: { watchdogRequeues: 2, recent60: { done: 5, failed: 1 } } });
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    const label = await screen.findByText(/^看门狗回收 · 近 24h$/);
    expect(within(label.parentElement!).getByText("2")).toBeInTheDocument();
    expect(screen.getByText(/5 完成\s*1 失败/)).toBeInTheDocument();
    // 云端才有的几行不再出现
    expect(screen.queryByText(/登录验证码/)).toBeNull();
    expect(screen.queryByText(/^在飞机器$/)).toBeNull();
    expect(screen.queryByText(/并发闸在飞/)).toBeNull();
    expect(screen.queryByText(/在飞机器容量|会到期的凭证/)).toBeNull();
  });
});

// ── 资源 · 这台电脑（本机版独有）──────────────────────────────────────────────
describe("AdminPage · 这台电脑", () => {
  it("模型、存放位置、磁盘、内存、模型后端、数据去哪都在一栏里", async () => {
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByRole("heading", { name: "这台电脑" })).toBeInTheDocument();
    expect(await screen.findByText(/已装齐 2 个 · 1\.2 GB/)).toBeInTheDocument();
    expect(screen.getByText(/转录与结果占 12\.3 MB/)).toBeInTheDocument();
    expect(screen.getByText("/Users/x/.transcribe-local")).toBeInTheDocument();
    expect(screen.getByText("116.5 / 460.4 GB")).toBeInTheDocument();
    expect(screen.getByText(/共 16\.0 GB · 此刻可用 3\.6 GB/)).toBeInTheDocument();
    expect(screen.getByText("deepseek-flash @ api.deepseek.com")).toBeInTheDocument();
    const flow = screen.getByTestId("panel-data-flow");
    expect(within(flow).getByText("不离开这台电脑")).toHaveAttribute("data-dest", "local");
    expect(within(flow).getByText("发给 api.deepseek.com")).toHaveAttribute("data-dest", "remote");
  });

  it("模型不全时写还缺几个、多大", async () => {
    localResSpy.mockResolvedValue(LOCAL_RES({ models: { ready: false, installedMb: 1200, missingMb: 2048, cacheDir: "/c",
      items: [{ id: "a", sizeMb: 1200, installed: true }, { id: "b", sizeMb: 2048, installed: false }] } }));
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByText(/还缺 1 个 · 2\.0 GB/)).toBeInTheDocument();
  });

  it("钥匙没设时写「未设置」——只报设没设，不报值", async () => {
    localResSpy.mockResolvedValue(LOCAL_RES({ backend: BACKEND({ keySet: false }) }));
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    const section = (await screen.findByRole("heading", { name: "这台电脑" })).parentElement!.parentElement!;
    expect(await within(section).findByText(/DEEPSEEK_API_KEY 读取 · 未设置/)).toBeInTheDocument();
  });

  it("测试连接真去问一次后端，连不上把原因原样写出来", async () => {
    probeSpy.mockResolvedValue({ ok: false, why: "HTTP 401" });
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByText("连不上：HTTP 401")).toBeInTheDocument();
    expect(probeSpy).toHaveBeenCalled();
  });

  it("本机资源读不到，其余板块照常——资源页是出事时去看的地方，不能跟着一起塌", async () => {
    localResSpy.mockRejectedValue(new Error("local-resources 500"));
    wrap(<AdminPage />);
    goTag(TAG_RESOURCE);
    expect(await screen.findByText("读不到本机资源")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "运行信号" })).toBeInTheDocument();
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

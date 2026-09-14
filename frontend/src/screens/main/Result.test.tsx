import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";

// jsdom 没实现 <audio>.play()/.pause() 和 Element.scrollTo（真浏览器都有）：mock 掉避免抛错
beforeAll(() => {
  Object.defineProperty(window.HTMLMediaElement.prototype, "play", { configurable: true, value: vi.fn().mockResolvedValue(undefined) });
  Object.defineProperty(window.HTMLMediaElement.prototype, "pause", { configurable: true, value: vi.fn() });
  Object.defineProperty(window.Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
});
import userEvent from "@testing-library/user-event";
import { Result, nearestScrollTop, CACHE_RETRY_MS, CACHE_STALL_MS } from "./Result";
import { UILangProvider } from "../../lib/i18n";
import type { JobMetrics } from "../../lib/api";
import { SAMPLE_REVIEW, SAMPLE_REVIEW_SEGMENTS } from "../../lib/reviewData";
import { downloadUrl } from "../../lib/download";

vi.mock("../../lib/download", async (orig) => ({
  ...(await orig<typeof import("../../lib/download")>()),
  downloadUrl: vi.fn(),
}));

const SEGS = [
  { t: "00:00:01", s: "你好", sp: "主持人" },
  { t: "00:00:05", s: "在的", sp: "被访者" },
];

// v3 两级收口：队列 = 10 存疑 + Mate 80（mustConfirm）= 11 项；已定字审计区 = 17 项
function renderResult(metrics: JobMetrics | null = null, onBack?: () => void) {
  return render(
    <UILangProvider>
      <Result
        onBack={onBack}
        lang="zh"
        job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
        jobId="job1"
        segments={SEGS}
        metrics={metrics}
        review={SAMPLE_REVIEW}
      />
    </UILangProvider>,
  );
}

function renderFull() {
  return render(
    <UILangProvider>
      <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1" segments={SAMPLE_REVIEW_SEGMENTS} metrics={null} review={SAMPLE_REVIEW} />
    </UILangProvider>,
  );
}

describe("Result review-queue detail (v3)", () => {
  it("renders full transcript with speaker labels", () => {
    renderResult();
    expect(screen.getByText("你好")).toBeTruthy();
    expect(screen.getByText("在的")).toBeTruthy();
    expect(screen.getByText(/主持人/)).toBeTruthy();
  });

  it("two-level queue: confirms one occurrence and undoes it", async () => {
    renderResult();
    expect(screen.getByText(/待你确认|Needs your eyes/)).toBeTruthy();
    expect(screen.getByText("0 / 11")).toBeTruthy();
    // 当前项（首个存疑，单处 → 直接逐处形态）点「✓ 这一处无误」→ 进度推进 + 可撤销
    await userEvent.click(screen.getByRole("button", { name: /这一处无误|This one is correct/ }));
    expect(screen.getByText("1 / 11")).toBeTruthy();
    // 项级「撤销」只清 ok/edited 归宿 → 回到 0 / 11
    await userEvent.click(screen.getByText(/^撤销$|^Undo$/));
    expect(screen.getByText("0 / 11")).toBeTruthy();
  });

  it("skips an item and shows the settled audit section", async () => {
    renderResult();
    await userEvent.click(screen.getByText(/跳过（暂不处理）|Skip for now/));
    // 跳过 = 搁置，不计完成；已搁置行给「恢复」
    expect(screen.getByText("0 / 11")).toBeTruthy();
    expect(screen.getByText(/^恢复$|^Restore$/)).toBeTruthy();
    // 审计区：17 项（原 mid 实体也收进来了）。措辞是「已按证据定字」不是「已确认」——
    // 「确认」留给人拍板的动作，系统自己定的用另一个词（2026-08-20）
    expect(screen.getByText(/已按证据定字 17|17 settled by evidence/)).toBeTruthy();
  });

  it("opens edit box from row hover and saves an edited line", async () => {
    renderResult();
    await userEvent.hover(screen.getByText("你好"));
    await userEvent.click(screen.getAllByLabelText(/编辑|Edit/)[0]);
    const box = screen.getByDisplayValue("你好");
    await userEvent.clear(box);
    await userEvent.type(box, "你好啊");
    await userEvent.click(screen.getByText(/^保存$|^Save$/));
    expect(screen.getByText("你好啊")).toBeTruthy();
  });

  it("offers a split download button with docx + txt only (no srt/md)", async () => {
    renderResult();
    expect(screen.getByText(/下载 \.docx|Download \.docx/)).toBeTruthy();
    await userEvent.click(screen.getByLabelText(/选择格式|Choose format/));
    expect(screen.getByText(/Word 文档|Word document/)).toBeTruthy();
    expect(screen.getByText(/纯文本|Plain text/)).toBeTruthy();
    expect(screen.queryByText(".srt")).toBeNull();
    expect(screen.queryByText(".md")).toBeNull();
  });

  it("shows audio duration and elapsed time from metrics", () => {
    renderResult({ durationSec: 40, elapsedSec: 222 });
    expect(screen.getAllByText(/0:40/).length).toBeGreaterThan(0);
    expect(screen.getByText(/用时|took/)).toBeTruthy();
    expect(screen.getByText("3:42")).toBeTruthy();
  });

  it("overview: replaces all occurrences via the merged control", async () => {
    renderFull();
    // 点 Mate 80 排队卡 → 多处项默认总览：处列表 + 项级动作
    await userEvent.click(screen.getByRole("button", { name: /Mate 80 产品名/ }));
    expect(screen.getByRole("button", { name: /3 处全部无误|All 3 correct/ })).toBeTruthy();
    // 合并控件：输入有效新词才浮现「替换 ↵」
    expect(screen.queryByText(/替换 ↵|Replace ↵/)).toBeNull();
    const input = screen.getByPlaceholderText("Mate 80");
    await userEvent.type(input, "Mate 90");
    await userEvent.click(screen.getByText(/替换 ↵|Replace ↵/));
    // 正文三处都换成新写法；整项完成 → 已改 3 处 + 进度 +1
    expect(screen.getAllByText("Mate 90").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/已改 3 处|3 renamed/)).toBeTruthy();
    expect(screen.getByText("1 / 11")).toBeTruthy();
  });

  it("detail view: dots navigation, Enter-replace, mixed decisions, revert", async () => {
    renderFull();
    await userEvent.click(screen.getByRole("button", { name: /Mate 80 产品名/ }));
    // 总览点处行 → 进逐处视图（项级/处级动作永不同屏）
    await userEvent.click(screen.getAllByTitle(/逐处确认这一处|Review this occurrence/)[0]);
    expect(screen.getByRole("button", { name: /这一处无误|This one is correct/ })).toBeTruthy();
    expect(screen.queryByText(/全部无误|All .* correct/)).toBeNull();
    // 合并控件 Enter 同效：第 1 处替换 → 自动前进到下一未决处
    const input = screen.getByPlaceholderText("Mate 80");
    await userEvent.type(input, "Mate 90{Enter}");
    expect(screen.getAllByText("Mate 90").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("0 / 11")).toBeTruthy(); // 项未完成，总进度不动
    // ‹ 总览 → 项级动作带剩余数字
    await userEvent.click(screen.getByText(/‹ 总览|‹ Overview/));
    await userEvent.click(screen.getByRole("button", { name: /其余 2 处无误|Remaining 2 correct/ }));
    expect(screen.getByText("1 / 11")).toBeTruthy();
    expect(screen.getByText(/已改 1 处|1 renamed/)).toBeTruthy();
    // 撤销 = 前向操作：点正文已改词 → 回看态「已改为」→「改回原词」真实回写 + 进度回退
    await userEvent.click(screen.getByRole("button", { name: "Mate 90" }));
    expect(screen.getByText(/这一处已改为「Mate 90」|Renamed to "Mate 90"/)).toBeTruthy();
    await userEvent.click(screen.getByText(/改回原词|Revert/));
    expect(screen.queryAllByText("Mate 90").length).toBe(0);
    expect(screen.getByText("0 / 11")).toBeTruthy();
  });

  it("doubt: rewrites the sentence inside the card", async () => {
    renderFull();
    // 初始当前项 = 首个存疑「百分之十八」（单处 → 直接逐处形态），「改写」卡内展开
    await userEvent.click(screen.getByRole("button", { name: /^改写$|^Rewrite$/ }));
    const box = screen.getByDisplayValue(/上半年我们整体能做到百分之十八/);
    await userEvent.clear(box);
    await userEvent.type(box, "上半年增长百分之十八。");
    await userEvent.click(screen.getByText(/保存改写|Save rewrite/));
    expect(screen.getByText("上半年增长百分之十八。")).toBeTruthy();
    expect(screen.getByText("1 / 11")).toBeTruthy();
  });

  it("重复时间戳：定位/改写精确到含词那行，不连累同时间戳的相邻行", async () => {
    // 相邻两行同一时间戳（两说话人同一秒各一行）——旧代码按 t 定位会命中第一行/两行都命中。
    const segs = [
      { t: "00:23:53", s: "嗯。", sp: "被访者" },
      { t: "00:23:53", s: "啊，社会学研究，我们要摸底。", sp: "主持人" },
    ];
    const review = [{
      type: "doubt" as const, term: "社会学研究", tag: "术语", confidence: "low" as const, reason: "三路互异且糊",
      occurrences: [{ t: "00:23:53", speaker: "主持人", lineText: "啊，社会学研究，我们要摸底。" }],
    }];
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1" segments={segs} metrics={null} review={review} />
      </UILangProvider>,
    );
    // 卡片「改写」载入的是含词的第二行，不是同时间戳第一行「嗯。」
    await userEvent.click(screen.getByRole("button", { name: /^改写$|^Rewrite$/ }));
    const box = screen.getByDisplayValue(/社会学研究/) as HTMLTextAreaElement;
    expect(box.value).not.toBe("嗯。");
    await userEvent.clear(box);
    await userEvent.type(box, "啊，社会科学研究，我们要摸底。");
    await userEvent.click(screen.getByText(/保存改写|Save rewrite/));
    // 第二行改了；第一行「嗯。」原样还在（未被同时间戳连累）
    expect(screen.getByText("啊，社会科学研究，我们要摸底。")).toBeTruthy();
    expect(screen.getByText("嗯。")).toBeTruthy();
  });

  it("suggestDelete doubt: 3s armed delete with toast undo", async () => {
    renderFull();
    // 疑幻觉孤句：删除升为可见按钮
    await userEvent.click(screen.getByRole("button", { name: /我说这有意思 存疑/ }));
    await userEvent.click(screen.getByRole("button", { name: /删除这句|Delete this line/ }));
    // 两段式：按钮先变「确认删除？ 3」再执行
    await userEvent.click(screen.getByRole("button", { name: /确认删除？|Confirm delete\?/ }));
    // 正文删除线行的 chip + 已完成卡上的「已删除」徽标
    expect(screen.getAllByText(/^已删除$|^Deleted$/).length).toBeGreaterThan(0);
    expect(screen.getByText("1 / 11")).toBeTruthy();
    // 墨底 Toast 撤销 → 无损恢复
    expect(screen.getByText(/已删除该句|Deleted line at/)).toBeTruthy();
    await userEvent.click(screen.getByText(/^撤销$|^Undo$/));
    expect(screen.queryAllByText(/^已删除$|^Deleted$/).length).toBe(0);
    expect(screen.getByText("0 / 11")).toBeTruthy();
  });

  it("audit area: revision does not move queue progress", async () => {
    renderFull();
    await userEvent.click(screen.getByText(/已按证据定字 17|17 settled by evidence/));
    // 展开「畅享 90 Max」（mid 实体收进审计区，依据 chip = 声学+术语库）→ ✎ 修订
    await userEvent.click(screen.getByRole("button", { name: /畅享 90 Max 声学\+术语库/ }));
    await userEvent.click(screen.getByText(/✎ 修订|✎ Revise/));
    const input = screen.getByPlaceholderText("畅享 90 Max");
    await userEvent.type(input, "畅享 90 Max Plus");
    await userEvent.click(screen.getByText(/替换 ↵|Replace ↵/));
    // 八处全改；修订不计进度、不挡导出；行尾标「✓ 已修订」
    expect(screen.getAllByText("畅享 90 Max Plus").length).toBeGreaterThanOrEqual(8);
    expect(screen.getByText("0 / 11")).toBeTruthy();
    expect(screen.getByText(/已修订|Revised/)).toBeTruthy();
  });

  it("audit area: go-to-text cycles through occurrences", async () => {
    renderFull();
    await userEvent.click(screen.getByText(/已按证据定字 17|17 settled by evidence/));
    // 点标题 = 展开 + 定位到第 1 处（2026-09-06 起），所以游标一打开就已经指向第 2 处
    await userEvent.click(screen.getByRole("button", { name: /畅享 90 Max 声学\+术语库/ }));
    expect(screen.getByText(/↗ 跳到原文 2\/8|↗ Go to text 2\/8/)).toBeTruthy();
    // 多处项带循环游标：跳一处后游标前进到下一处
    await userEvent.click(screen.getByText(/↗ 跳到原文 2\/8|↗ Go to text 2\/8/));
    expect(screen.getByText(/↗ 跳到原文 3\/8|↗ Go to text 3\/8/)).toBeTruthy();
    // 单处项不带分数
    await userEvent.click(screen.getByRole("button", { name: /韬定律 声学\+术语库/ }));
    expect(screen.getByText(/^↗ 跳到原文$|^↗ Go to text$/)).toBeTruthy();
  });

  it("audit area: settled-by-evidence section stays present (shows 无) when there are none", () => {
    // 「已确认实体」常驻：没有任何已定字实体时也不消失，显示空态「无」
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1" segments={SEGS} metrics={null} review={[]} />
      </UILangProvider>,
    );
    expect(screen.getByText(/已按证据定字 · 无|Nothing settled by evidence/)).toBeTruthy();
  });

  it("guards against review items with zero occurrences (real-data safety)", () => {
    // P0 护栏：后端 report 写法与终稿不一致时会产出 occurrences=[] 的项——不进队列、不崩
    const broken = [
      { type: "doubt" as const, term: "幽灵项", tag: "", confidence: "low" as const, reason: "终稿里搜不到", occurrences: [] },
      ...SAMPLE_REVIEW,
    ];
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1" segments={SEGS} metrics={null} review={broken} />
      </UILangProvider>,
    );
    expect(screen.getByText("0 / 11")).toBeTruthy(); // 还是 11 项，幽灵项被滤掉
    expect(screen.queryByText("幽灵项")).toBeNull();
  });

  it("syncs revisions to backend (debounced PUT, edited line included)", async () => {
    // P1#7：行内编辑保存后 0.8s 防抖，把修订快照 PUT 给后端（导出用修订版）
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    renderResult();
    await userEvent.hover(screen.getByText("你好"));
    await userEvent.click(screen.getAllByLabelText(/编辑|Edit/)[0]);
    const box = screen.getByDisplayValue("你好");
    await userEvent.clear(box);
    await userEvent.type(box, "你好（修订）");
    await userEvent.click(screen.getByText(/^保存$|^Save$/));
    await vi.waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/jobs/job1/transcript"));
      expect(call).toBeTruthy();
      expect(call![1].method).toBe("PUT");
      expect(call![1].body).toContain("你好（修订）");
    }, { timeout: 3000 });
    vi.unstubAllGlobals();
  });

  it("restores review decisions from backend state (re-login keeps progress)", async () => {
    // 决策账本持久化：重登后 GET review_state 读回 → 确认进度不归零
    const fetchMock = vi.fn((url: RequestInfo | URL) => {
      if (String(url).includes("/review_state")) {
        return Promise.resolve({ ok: true, json: async () => ({ occRes: { "doubt:定义": { 0: { kind: "ok" } } }, skipped: {} }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResult();
    await vi.waitFor(() => expect(screen.getByText("1 / 11")).toBeTruthy(), { timeout: 3000 });
    vi.unstubAllGlobals();
  });

  it("persists review decisions to backend (debounced PUT review_state)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    renderResult();
    await userEvent.click(screen.getByRole("button", { name: /这一处无误|This one is correct/ }));
    await vi.waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) => String(url).includes("/review_state") && (init as RequestInit | undefined)?.method === "PUT");
      expect(call).toBeTruthy();
      expect((call![1] as RequestInit).body).toContain('"kind":"ok"');
    }, { timeout: 3000 });
    vi.unstubAllGlobals();
  });

  // ── 复核决策不许被自己写没了（2026-08-23）──
  // 上一轮把「取决策」挪到外层之后，页面里那段自己取的逻辑走了捷径直接跳过，
  // 但**捷径没挂上「不用回存」的牌子**：于是每打开一篇转录都白写一次。
  // 正常时写回去的是同一份、无害；取不到的那一次写回去的是一本**空账本**，
  // 把用户确认过的全部决策盖掉。
  const putsOf = (m: ReturnType<typeof vi.fn>) =>
    m.mock.calls.filter(([u, i]) => String(u).includes("/review_state") && (i as RequestInit | undefined)?.method === "PUT");

  it("外层已经把决策给进来了：挂载后一个字都不回存", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1"
          segments={SEGS} metrics={null} review={SAMPLE_REVIEW} reviewState={{ occRes: {}, skipped: {} }} />
      </UILangProvider>,
    );
    await new Promise((r) => setTimeout(r, 1300));   // 回存防抖 800ms，等过它
    expect(putsOf(fetchMock)).toEqual([]);
    vi.unstubAllGlobals();
  });

  // 这条就是「会丢数据」的那一半：外层取失败时若谎报成 null（＝问过了，没有），
  // 页面就会从空账本开工，并在 800ms 后把这本空的写回服务端——盖掉全部已确认决策。
  it("外层说「没有决策」时也不许回存：真没有就没什么可写的", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1"
          segments={SEGS} metrics={null} review={SAMPLE_REVIEW} reviewState={null} />
      </UILangProvider>,
    );
    await new Promise((r) => setTimeout(r, 1300));
    expect(putsOf(fetchMock)).toEqual([]);
    vi.unstubAllGlobals();
  });

  it("决策取不到时：不写空的盖上去，也不去打 PUT", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/review_state") && (init?.method ?? "GET") === "GET") {
        return { ok: false, status: 500, json: async () => ({}) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResult();
    await new Promise((r) => setTimeout(r, 1300));
    expect(putsOf(fetchMock as never)).toEqual([]);
    vi.unstubAllGlobals();
  });

  // 「不知道服务端有什么就不写」不能变成「这次会话白干」：用户真做了决策时，
  // 先把服务端那份补问回来垫底，再把本次的盖上去存——旧的不丢，新的也在。
  it("决策取不到但用户拍了板：先补问服务端，再把两边并起来存", async () => {
    let getFails = true;
    const server = { occRes: { "entity:老早存在的项": { 0: { kind: "ok" } } }, skipped: {} };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const u = String(url), m = init?.method ?? "GET";
      if (u.includes("/review_state") && m === "GET") {
        if (getFails) { getFails = false; return { ok: false, status: 500, json: async () => ({}) }; }
        return { ok: true, json: async () => server };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    renderResult();
    await userEvent.click(screen.getByRole("button", { name: /这一处无误|This one is correct/ }));
    await vi.waitFor(() => {
      const puts = putsOf(fetchMock as never);
      expect(puts.length).toBeGreaterThan(0);
      const body = String((puts[puts.length - 1][1] as RequestInit).body);
      expect(body).toContain("老早存在的项");   // 服务端原有的没被抹掉
      expect(body).toContain('"kind":"ok"');    // 本次拍的板也存上了
    }, { timeout: 4000 });
    vi.unstubAllGlobals();
  });

  it("shows the 7-day deletion notice, and a read-only state when audio 404s", () => {
    const { container } = renderResult();
    // 平时：播放器下方预告小字在
    expect(screen.getByText(/7 天后自动删除|auto-deleted 7 days/)).toBeTruthy();
    // 录音被删（/audio 404）→ <audio> error → 优雅只读态，预告小字让位
    fireEvent.error(container.querySelector("audio")!);
    expect(screen.getByText(/录音已删除|Audio deleted/)).toBeTruthy();
    expect(screen.queryByText(/7 天后自动删除|auto-deleted 7 days/)).toBeNull();
  });

  // 国内慢网：定位后浏览器要等数据（seeking/waiting），此前界面一声不吭、用户以为没点上而连点。
  // 现在等数据时明示「正在加载」，数据到了（playing/canplay）就收起。
  it("shows a loading hint while the audio is waiting for data after a seek, hides it once playable", () => {
    const { container } = renderResult();
    const audio = container.querySelector("audio")!;
    expect(screen.queryByText(/正在加载这一段的声音|Loading audio for this line/)).toBeNull();
    fireEvent(audio, new Event("seeking"));
    expect(screen.getByText(/正在加载这一段的声音|Loading audio for this line/)).toBeTruthy();
    fireEvent(audio, new Event("playing"));
    expect(screen.queryByText(/正在加载这一段的声音|Loading audio for this line/)).toBeNull();
    fireEvent(audio, new Event("waiting"));
    expect(screen.getByText(/正在加载这一段的声音|Loading audio for this line/)).toBeTruthy();
    fireEvent(audio, new Event("canplay"));
    expect(screen.queryByText(/正在加载这一段的声音|Loading audio for this line/)).toBeNull();
  });

  // 打开详情页就在后台把整段音频拉到本地；拉完把 <audio> 换成本地副本、播放位置带过去，之后定位不再走网络。
  // jsdom 没有 createObjectURL（真浏览器都有）——补上它才走得到这条路径；没有它组件就保持从前的流式行为。
  it("prefetches the whole recording in the background and swaps the audio source to the local copy", async () => {
    const bytes = new Uint8Array(1024);
    // 每次 fetch 给一个新的 Response：React 18 的 effect 在测试里会挂载两次，第一次的 reader 会把同一个流锁住
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      new ReadableStream<Uint8Array>({ start(c) { c.enqueue(bytes.slice(0, 512)); c.enqueue(bytes.slice(512)); c.close(); } }),
      { status: 200, headers: { "content-length": "1024", "content-type": "audio/mp4" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn().mockReturnValue("blob:local-copy");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    try {
      const { container, unmount } = renderResult({ durationSec: 600 } as JobMetrics);
      const audio = container.querySelector("audio") as HTMLMediaElement;
      expect(audio.getAttribute("src")).toBe("/api/jobs/job1/audio");
      // 规则 1：后台下载走单独的网址（不与播放器抢同一个键），播放器在缓存好之前不预取
      expect(audio.getAttribute("preload")).toBe("none");
      expect(fetchMock).toHaveBeenCalledWith("/api/jobs/job1/audio?whole=1", expect.objectContaining({ signal: expect.anything() }));
      await waitFor(() => expect(audio.getAttribute("src")).toBe("blob:local-copy"));
      expect(audio.getAttribute("preload")).toBe("auto");
      expect(screen.getByText(/已缓存到本地|cached locally/)).toBeTruthy();
      // 规则 4：状态常驻，不再 5 秒后消失（从磁盘缓存 0.1 秒完成的第二次打开，闪一下等于没显示）
      await new Promise((r) => setTimeout(r, 30));
      expect(screen.getByText(/已缓存到本地|cached locally/)).toBeTruthy();
      // 卸载时释放本地副本，别让几十 MB 的 blob 挂在内存里
      unmount();
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:local-copy");
    } finally {
      vi.unstubAllGlobals();
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    }
  });

  // 规则 2（2026-09-06 第二轮）：用户一点播放就掐断后台下载把线让给播放器，暂停后从断点续传（Range: bytes=已收到-）。
  // 两个请求分同一条每秒几十 KB 的线的话，出声要等两倍时间——客户 Edge 截图里那 226 kB 就是播放器在下载期间抢走的。
  it("holds the background cache while audio plays and resumes from the byte it stopped at", async () => {
    const head = new Uint8Array(512), tail = new Uint8Array(512);
    let aborted = false;
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.cache === "no-store") return new Response(new Uint8Array(1), { status: 206 });   // 1 字节探针：录音在
      if (!String(url).includes("?whole=1")) return new Response("{}", { status: 200 });
      const range = (init?.headers as Record<string, string> | undefined)?.Range;
      if (!range) {
        // 第一次：给 512 字节后一直挂着，等 abort
        return new Response(new ReadableStream<Uint8Array>({
          start(c) { c.enqueue(head); init?.signal?.addEventListener("abort", () => { aborted = true; c.error(new DOMException("aborted", "AbortError")); }); },
        }), { status: 200, headers: { "content-length": "1024", "content-type": "audio/mp4" } });
      }
      return new Response(new ReadableStream<Uint8Array>({ start(c) { c.enqueue(tail); c.close(); } }),
        { status: 206, headers: { "content-range": "bytes 512-1023/1024", "content-type": "audio/mp4" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:local-copy") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    try {
      const { container, unmount } = renderResult({ durationSec: 600 } as JobMetrics);
      const audio = container.querySelector("audio") as HTMLMediaElement;
      await waitFor(() => expect(screen.getByText(/正在把整段录音缓存到本地 · 50%|Caching the whole recording locally · 50%/)).toBeTruthy());
      fireEvent(audio, new Event("play"));
      expect(aborted).toBe(true);
      expect(screen.getByText(/缓存已暂停 · 50%|Caching paused at 50%/)).toBeTruthy();
      fireEvent(audio, new Event("pause"));
      await waitFor(() => expect(audio.getAttribute("src")).toBe("blob:local-copy"));
      const resumed = fetchMock.mock.calls.find(([url, init]) => String(url).includes("?whole=1") && (init as RequestInit | undefined)?.headers);
      expect((resumed?.[1] as RequestInit).headers).toEqual({ Range: "bytes=512-" });
      unmount();   // 在 revokeObjectURL 还在的时候卸载
    } finally {
      vi.unstubAllGlobals();
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    }
  });

  // 规则 3：断了自动重试 3 次（2 / 5 / 10 秒），仍失败才显示「缓存失败 · 重试」并挂着；此前是安静退回流式路径，谁都不知道有没有缓存好。
  it("retries the background cache three times before showing a failure with a retry link", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("network down"));
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn() });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    try {
      const { unmount } = renderResult({ durationSec: 600 } as JobMetrics);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      // 只数缓存那条网址；React 18 在测试里挂载两次，第一份 effect 立刻被清理（abort），只数活着的那份
      const calls = () => fetchMock.mock.calls.filter(([url, init]) => String(url).includes("?whole=1") && !(init as RequestInit).signal?.aborted).length;
      expect(calls()).toBe(1);
      expect(screen.queryByText(/缓存失败|Couldn't cache/)).toBeNull();
      for (const ms of CACHE_RETRY_MS) {
        await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
      }
      expect(calls()).toBe(1 + CACHE_RETRY_MS.length);
      expect(screen.getByText(/缓存失败|Couldn't cache/)).toBeTruthy();
      // 点「重试」再来一轮
      fireEvent.click(screen.getByRole("button", { name: /重试|Retry/ }));
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(calls()).toBe(2 + CACHE_RETRY_MS.length);
      expect(screen.queryByText(/缓存失败|Couldn't cache/)).toBeNull();
      unmount();
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    }
  });

  // 规则 3 的另一半：网络掉了的时候进行中的下载**不报错、只是一直挂着**（Chrome 离线模拟实测，整段跑完都没触发失败）。
  // 所以「连续 30 秒收不到一个字节」也算断：掐掉这一次、按重试节奏再来，Range 从已收到的字节接着要。
  it("treats 30 s without a single byte as a failure and retries from the bytes it already has", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
      // 第一次：给 512 字节后再也不给，直到被 abort
      return new Response(new ReadableStream<Uint8Array>({
        start(c) { c.enqueue(new Uint8Array(512)); init?.signal?.addEventListener("abort", () => c.error(new DOMException("aborted", "AbortError"))); },
      }), { status: 200, headers: { "content-length": "1024", "content-type": "audio/mp4" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn() });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    try {
      const { unmount } = renderResult({ durationSec: 600 } as JobMetrics);
      await act(async () => { await vi.advanceTimersByTimeAsync(50); });
      const live = () => fetchMock.mock.calls.filter(([url, init]) => String(url).includes("?whole=1") && !(init as RequestInit).signal?.aborted);
      expect(live().length).toBe(1);
      const whole = () => fetchMock.mock.calls.filter(([url]) => String(url).includes("?whole=1")).length;
      const before = whole();
      await act(async () => { await vi.advanceTimersByTimeAsync(CACHE_STALL_MS - 1000); });
      expect(whole()).toBe(before);   // 30 秒内不掐
      await act(async () => { await vi.advanceTimersByTimeAsync(1000 + CACHE_RETRY_MS[0] + 50); });
      const again = fetchMock.mock.calls.filter(([url]) => String(url).includes("?whole=1")).at(-1)!;
      expect((again[1] as RequestInit).headers).toEqual({ Range: "bytes=512-" });   // 已收到的 512 字节不重下
      expect(screen.queryByText(/缓存失败|Couldn't cache/)).toBeNull();            // 还在重试，没到报失败的时候
      unmount();
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    }
  });

  // 录音到期（上传 7 天后删除，后端回 410）：不是断网，不许重试、不许显示「缓存失败」，要切成「录音已删除」态。
  // 2026-09-06 生产实见（Duner 自己账号一单阿语录音正好第 7 天）：只认 404 的话 410 被当成断网、重试三次后显示缓存失败。
  it("treats 410 (recording expired) as deleted audio, not as a network failure", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) =>
      init?.cache === "no-store" ? new Response(new Uint8Array(1), { status: 206 })
      : String(url).includes("?whole=1") ? new Response(null, { status: 410 }) : new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn() });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    try {
      const { container, unmount } = renderResult({ durationSec: 600 } as JobMetrics);
      await waitFor(() => expect(screen.getByText(/录音已删除|Audio deleted/)).toBeTruthy());
      expect(screen.queryByText(/缓存失败|Couldn't cache/)).toBeNull();
      // 不重试：整档请求的次数（React 18 双挂载最多 2 次）在报出「已删除」之后不再涨
      const whole = () => fetchMock.mock.calls.filter(([url]) => String(url).includes("?whole=1")).length;
      const n = whole(); expect(n).toBeLessThanOrEqual(2);
      await new Promise((r) => setTimeout(r, 80));
      expect(whole()).toBe(n);
      // 彻底堵住（Duner 实测：顶部播放器没了，行里的 ▶ 还能从浏览器缓存里放）：每一条播放路径都要没
      expect(screen.queryAllByTitle(/听这一句|Play this line/)).toHaveLength(0);
      expect(screen.queryAllByLabelText(/听这一句|^Listen$/)).toHaveLength(0);
      expect(container.querySelector("audio")!.getAttribute("src")).toBeNull();
      unmount();
    } finally {
      vi.unstubAllGlobals();
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    }
  });

  // 浏览器自己的缓存里还留着整档（我们允许留 24 小时）、服务器上却已删：整档请求被缓存接住会回 200，
  // 页面就会「✓ 已缓存」照常播放。所以另发一条 1 字节、no-store 的探针一定打到服务器，它说没了就以它为准。
  it("a one-byte no-store probe overrides a cached whole-file copy when the server says the audio is gone", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes("?whole=1")) return new Response(new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new Uint8Array(16)); c.close(); } }), { status: 200, headers: { "content-length": "16", "content-type": "audio/mp4" } });
      if (init?.cache === "no-store") return new Response(null, { status: 410 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn().mockReturnValue("blob:local-copy") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
    try {
      const { container, unmount } = renderResult({ durationSec: 600 } as JobMetrics);
      await waitFor(() => expect(screen.getByText(/录音已删除|Audio deleted/)).toBeTruthy());
      const probe = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.cache === "no-store")!;
      expect(String(probe[0])).toBe("/api/jobs/job1/audio");
      expect((probe[1] as RequestInit).headers).toEqual({ Range: "bytes=0-0" });
      expect(container.querySelector("audio")!.getAttribute("src")).toBeNull();   // 本地副本不许留下当来源
      expect(screen.queryByText(/已缓存到本地|cached locally/)).toBeNull();
      unmount();
    } finally {
      vi.unstubAllGlobals();
      delete (URL as unknown as Record<string, unknown>).createObjectURL;
      delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
    }
  });

  // 已定字卡（2026-09-06 Duner）：4 处的卡以前只有一条循环链接，要看第 1 处得先转一圈。
  // 现在卡里每处一行、点哪处正文跳哪处；「跳到原文 N/M」的游标接到点过的那处后面，循环逻辑不变。
  it("settled-entity card lists every occurrence; clicking one jumps there and advances the cycle cursor", async () => {
    renderResult();
    // 打开审计区，展开「畅享 90 Max」（5 处）
    await userEvent.click(screen.getByRole("button", { name: /已按证据定字|settled by evidence/ }));
    await userEvent.click(screen.getByRole("button", { name: /^畅享 90 Max/ }));
    const list = screen.getByRole("list", { name: /每一处|Occurrences/ });
    const rows = within(list).getAllByRole("button");
    const N = rows.length;
    expect(N).toBeGreaterThanOrEqual(5);
    expect(rows[2].textContent).toContain("那畅享 90 Max 的备货");
    // 点标题时已定位到第 1 处 → 游标指向第 2 处
    expect(screen.getByText(new RegExp(`跳到原文 2/${N}|Go to text 2/${N}`))).toBeTruthy();
    // 点第 3 处：那一行标成当前处；「跳到原文」变成 4/N
    await userEvent.click(rows[2]);
    expect(rows[2].getAttribute("aria-current")).toBe("true");
    expect(screen.getByText(new RegExp(`跳到原文 4/${N}|Go to text 4/${N}`))).toBeTruthy();
    // 再点「跳到原文」：去第 4 处，变 5/N——原来的循环一行没改
    await userEvent.click(screen.getByText(new RegExp(`跳到原文 4/${N}|Go to text 4/${N}`)));
    expect(within(list).getAllByRole("button")[3].getAttribute("aria-current")).toBe("true");
    expect(screen.getByText(new RegExp(`跳到原文 5/${N}|Go to text 5/${N}`))).toBeTruthy();
  });

  // 点正文标记词 → 右栏把那张卡亮出来（2026-09-06 Duner：卡展开了但不滚过去）。
  // jsdom 没有 scrollIntoView（真浏览器都有）：mock 成记录「被滚的是哪张卡」。三种卡三种藏法各一条。
  const spyReveal = () => {
    const seen: string[] = [];
    Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: function (this: HTMLElement) { seen.push(this.dataset.reviewCard ?? "?"); } });
    return seen;
  };
  const markedWord = (term: string) => screen.getAllByText(term, { selector: 'span[role="button"]' })[0];

  it("reveal: clicking a settled-entity word opens the audit section and scrolls to that card", async () => {
    const seen = spyReveal();
    renderFull();
    await userEvent.click(markedWord("畅享 90 Max"));
    expect(seen.at(-1)).toBe("entity:畅享 90 Max");
    const card = document.querySelector('[data-review-card="entity:畅享 90 Max"]') as HTMLElement;
    expect(card.dataset.flash).toBe("1");
    expect(card.textContent).toContain("跳到原文");   // 卡是展开的
  });

  it("reveal: with the audit section open, clicking a doubt word re-opens the queue and scrolls to its card", async () => {
    const seen = spyReveal();
    renderFull();
    await userEvent.click(screen.getByText(/已按证据定字 17|17 settled by evidence/));
    // 审计区展开 → 队列折成一行；此前点存疑词右栏一动不动
    expect(document.querySelector('[data-review-card="doubt:战略堡垒店"]')).toBeNull();
    await userEvent.click(markedWord("战略堡垒店"));
    const card = document.querySelector('[data-review-card="doubt:战略堡垒店"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.dataset.flash).toBe("1");
    expect(seen.at(-1)).toBe("doubt:战略堡垒店");
    expect(within(card).getByText(/这一处无误|Looks right/)).toBeTruthy();   // 是展开的当前卡
  });

  it("reveal: after everything is settled, clicking a confirmed word opens「你的决策」and scrolls to that card", async () => {
    const seen = spyReveal();
    renderFull();
    // 把队列全部确认掉
    for (let i = 0; i < 40; i++) {
      const ok = screen.queryAllByRole("button", { name: /这一处无误|全部无误|Looks right|All correct/ })[0];
      if (!ok) break;
      await userEvent.click(ok);
    }
    expect(screen.getByRole("button", { name: /你的决策|decisions by you/ })).toBeTruthy();
    expect(document.querySelector('[data-review-card="doubt:战略堡垒店"]')).toBeNull();   // 折叠着
    await userEvent.click(markedWord("战略堡垒店"));
    const card = document.querySelector('[data-review-card="doubt:战略堡垒店"]') as HTMLElement;
    expect(card).not.toBeNull();
    expect(seen.at(-1)).toBe("doubt:战略堡垒店");
  });

  it("renders the back link and fires onBack", async () => {
    const onBack = vi.fn();
    renderResult(null, onBack);
    await userEvent.click(screen.getByText(/我的转录|My transcripts/));
    expect(onBack).toHaveBeenCalled();
  });

  // 回归：顺序播放到后面 → 暂停 → 点前面某一句，老实现会「点了没反应」。
  // 根因是 currentTime 赋值异步，scope（播到句尾自动停）抢在 seek 落定前装上，
  // timeupdate 拿上一个播放位置去比句尾 → 一 play 就被判停。
  it("play-one-line waits for the seek to land before arming the stop-at-end scope", () => {
    const { container } = renderResult();
    const audio = container.querySelector("audio") as HTMLMediaElement;
    const play = audio.play as ReturnType<typeof vi.fn>;
    play.mockClear();
    Object.defineProperty(audio, "currentTime", { configurable: true, writable: true, value: 3000 });
    Object.defineProperty(audio, "readyState", { configurable: true, value: 1 });   // 已读到时长：设 currentTime 会真的 seek

    fireEvent.click(screen.getAllByTitle(/听这一句|Play this line/)[0]);
    // seek 还没落地 → 一声不吭地等，绝不能此刻就 play（play 了就会被旧位置判停）
    expect(play).not.toHaveBeenCalled();

    fireEvent(audio, new Event("seeked"));
    expect(play).toHaveBeenCalled();
  });

  // 规则 1 的代价：缓存好之前播放器 preload="none"，一个字节都没加载时设 currentTime 只是记下起点、不会发 seeked。
  // 这时还等 seeked 就永远等不到——生产实测（本地构建顶替线上 bundle）点了一句 45 秒没声音，就是它。
  it("play-one-line before any data is loaded: sets the start position and plays without waiting for seeked", () => {
    const { container } = renderResult();
    const audio = container.querySelector("audio") as HTMLMediaElement;
    const play = audio.play as ReturnType<typeof vi.fn>;
    play.mockClear();
    Object.defineProperty(audio, "currentTime", { configurable: true, writable: true, value: 0 });
    Object.defineProperty(audio, "readyState", { configurable: true, value: 0 });   // HAVE_NOTHING

    fireEvent.click(screen.getAllByTitle(/听这一句|Play this line/)[0]);
    expect(audio.currentTime).toBeGreaterThan(0);   // 起点记下了
    expect(play).toHaveBeenCalled();                // 没等 seeked
  });
});

// 回归：时间码只精确到秒，同一秒常有两行。句尾若按「数组里的下一行」取，会拿到同为 52:41
// 的那行 → 句尾 = 句首 → 一按就判停，用户看到的是「这一句点了没声音」。
it("同一秒两行时，句尾取下一个真正更晚的时间码", () => {
  const segs = [
    { t: "00:52:41", s: "哦。", sp: "被访者" },
    { t: "00:52:41", s: "后面还有，还有家居呀，然后餐厅呀。", sp: "主持人" },
    { t: "00:52:51", s: "你是去哪？", sp: "被访者" },
  ];
  const { container } = render(
    <UILangProvider>
      <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
        jobId="job1" segments={segs} metrics={null} review={[]} />
    </UILangProvider>,
  );
  const audio = container.querySelector("audio") as HTMLMediaElement;
  const pause = audio.pause as ReturnType<typeof vi.fn>;
  pause.mockClear();
  Object.defineProperty(audio, "currentTime", { configurable: true, writable: true, value: 0 });

  fireEvent.click(screen.getAllByTitle(/听这一句|Play this line/)[0]);   // 第一行 52:41
  fireEvent(audio, new Event("seeked"));
  audio.currentTime = 3161.5;                       // 才播了半秒
  fireEvent(audio, new Event("timeupdate"));
  expect(pause).not.toHaveBeenCalled();             // 句尾错取成 52:41 的话，这里就停了

  audio.currentTime = 3171.5;                       // 越过下一句(52:51)才该停
  fireEvent(audio, new Event("timeupdate"));
  expect(pause).toHaveBeenCalled();
});

describe("nearestScrollTop", () => {
  const view = { scrollTop: 1000, height: 600 };   // 视口覆盖 1000–1600
  it("目标已完整可见 → 不滚（老实现会硬钉到 1/3 处，看着就是抖一下）", () => {
    expect(nearestScrollTop({ top: 1200, height: 40 }, view)).toBeNull();
  });
  it("目标在视口上方 → 只向上滚到贴顶，不越过", () => {
    const next = nearestScrollTop({ top: 400, height: 40 }, view);
    expect(next).toBe(376);
    expect(next!).toBeLessThan(view.scrollTop);       // 方向：向上
  });
  it("目标在视口下方 → 只向下滚到贴底，不越过", () => {
    const next = nearestScrollTop({ top: 2000, height: 40 }, view);
    expect(next).toBe(2000 + 40 - 600 + 24);
    expect(next!).toBeGreaterThan(view.scrollTop);    // 方向：向下
  });
  it("目标靠近顶部 → 不产生负 scrollTop", () => {
    expect(nearestScrollTop({ top: 5, height: 40 }, { scrollTop: 100, height: 600 })).toBe(0);
  });
});

// ── 复核完之后再打开（2026-08-20 生产实见的 bug）──
// 「当前项」的初始值是按原始复核清单猜的第一项。若那一项早就确认过了，
// 不在决策读回来之后校一次，它会一直挂着完整决策卡（带类别标签、赤陶高亮边框），
// 而顶上写着「存疑已全部确认」——同屏自相矛盾。
describe("Result · 复核完之后重新打开", () => {
  /** 让服务端返回「队列里每一项的每一处都已确认」 */
  async function mockAllConfirmed() {
    const api = await import("../../lib/api");
    const occRes: Record<string, Record<number, { kind: "ok" }>> = {};
    for (const r of SAMPLE_REVIEW) {
      if (r.type === "doubt" || r.mustConfirm === true) {
        occRes[`${r.type}:${r.term}`] = Object.fromEntries(
          r.occurrences.map((_, i) => [i, { kind: "ok" as const }]),
        );
      }
    }
    vi.spyOn(api, "getReviewState").mockResolvedValue({ occRes, skipped: {} } as never);
  }

  it("当前项在决策水合后被校正：不再摆出「等你决定」的样子", async () => {
    await mockAllConfirmed();
    renderResult();
    expect(await screen.findByText(/存疑已全部确认|All cleared/)).toBeTruthy();
    // 清零后决策组默认收起（2026-08-21）——先展开，否则下面测的是「折叠壳藏住了」而非「卡没展开」
    await userEvent.click(screen.getByRole("button", { name: /你的决策|decisions by you/ }));
    // 全部已决 → 谁也不指 → 一张展开的决策卡都不该有。
    // 判据用「重新决定」：它只出现在**展开**的已决卡里；折叠的已确认卡只有「撤销」。
    // （别用「这一处无误」判——已确认的项即使展开也不显示那个按钮，那样测不出 bug。）
    await waitFor(() => {
      expect(screen.queryByText(/^重新决定$|^Redecide$/)).toBeNull();
    });
    expect(screen.getAllByText(/^撤销$|^Undo$/).length).toBeGreaterThan(0);   // 折叠态还在
  });

  it("回头点开一张已确认的卡：标签报的是状态，不是类别", async () => {
    await mockAllConfirmed();
    renderResult();
    await screen.findByText(/存疑已全部确认|All cleared/);
    await userEvent.click(screen.getByRole("button", { name: /你的决策|decisions by you/ }));
    const doubtCount = screen.queryAllByText(/^存疑$|^Uncertain$/).length;   // 正文图例那一个
    // 已确认卡上的词是个按钮，点它回看
    const first = SAMPLE_REVIEW.find((r) => r.type === "doubt")!;
    await userEvent.click(screen.getByRole("button", { name: first.term }));
    // 卡上出现「✓ 已确认」，而类别标签「存疑」没有多出来一个
    expect(screen.getAllByText(/✓ 已确认|✓ Confirmed/).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/^存疑$|^Uncertain$/).length).toBe(doubtCount);
  });
});

// 复核清零之后，17 张决策卡不该继续摊在右栏 —— 那时它们已经不是待办，而是决策留档。
// 判据挑「撤销」：它只出现在折叠的已确认卡里，收起时一个都不该在场；展开后必须回来。
// 造回 bug（清零后仍无条件 queue.map）时第一条断言即红。
it("复核清零后决策组默认收起，展开才看得到每一处", async () => {
  const api = await import("../../lib/api");
  const occRes: Record<string, Record<number, { kind: "ok" }>> = {};
  for (const r of SAMPLE_REVIEW) {
    if (r.type === "doubt" || r.mustConfirm === true) {
      occRes[`${r.type}:${r.term}`] = Object.fromEntries(r.occurrences.map((_, i) => [i, { kind: "ok" as const }]));
    }
  }
  vi.spyOn(api, "getReviewState").mockResolvedValue({ occRes, skipped: {} } as never);
  renderResult();
  await screen.findByText(/存疑已全部确认|All cleared/);
  await waitFor(() => expect(screen.queryAllByText(/^撤销$|^Undo$/).length).toBe(0));
  await userEvent.click(screen.getByRole("button", { name: /你的决策|decisions by you/ }));
  expect(screen.getAllByText(/^撤销$|^Undo$/).length).toBeGreaterThan(0);
});

// 打开稿子**不自动定位**：正文从 0:00 起是对的，复核完的稿子也一样（2026-08-22 Duner 定）。
// 「右栏卡片指着 3:29、正文停在 0:00」靠下面那条解决——卡片本身可点。
it("打开转录：正文不自动滚走", async () => {
  const spy = vi.fn();
  Object.defineProperty(window.Element.prototype, "scrollTo", { configurable: true, value: spy });
  const api = await import("../../lib/api");
  vi.spyOn(api, "getReviewState").mockResolvedValue({ occRes: {}, skipped: {} } as never);
  renderFull();
  await screen.findByText(SAMPLE_REVIEW[0].term);
  await new Promise((r) => setTimeout(r, 60));
  expect(spy).not.toHaveBeenCalled();
});

// 当前那张卡整张都是「回到正文这一句」的靶子——点空白处即可。
// 在此之前只有卡里那句话和时间码可点，而用户最自然的动作正是点一下这张卡，却什么都不发生。
it("点当前存疑卡的空白处 → 正文跳到对应位置", async () => {
  const spy = vi.fn();
  Object.defineProperty(window.Element.prototype, "scrollTo", { configurable: true, value: spy });
  const api = await import("../../lib/api");
  vi.spyOn(api, "getReviewState").mockResolvedValue({ occRes: {}, skipped: {} } as never);
  renderFull();
  const card = await screen.findByTitle(/点卡片空白处|Click anywhere blank/);
  await userEvent.click(card);
  expect(spy).toHaveBeenCalled();
});

// 「就近对齐」的规矩是目标已在视口内就一动不动，于是在一屏能装下二十行的稿子上，
// 点「跳到这一句」经常什么都不发生——用户只能理解成坏了。滚动是手段，指出是哪一行才是目的。
it("跳转一定会指出是哪一行，哪怕它本来就在视野里", async () => {
  const api = await import("../../lib/api");
  vi.spyOn(api, "getReviewState").mockResolvedValue({ occRes: {}, skipped: {} } as never);
  const { container } = renderFull();
  const card = await screen.findByTitle(/点卡片空白处|Click anywhere blank/);
  expect(container.querySelectorAll("[data-flash]").length).toBe(0);
  await userEvent.click(card);
  expect(container.querySelectorAll("[data-flash]").length).toBe(1);
});

// 卡里的按钮各有各的事，点它们不能被「回到正文」抢走
it("点卡里的按钮不触发跳转", async () => {
  const api = await import("../../lib/api");
  vi.spyOn(api, "getReviewState").mockResolvedValue({ occRes: {}, skipped: {} } as never);
  renderFull();
  await screen.findByText(SAMPLE_REVIEW[0].term);
  const spy = vi.fn();
  Object.defineProperty(window.Element.prototype, "scrollTo", { configurable: true, value: spy });
  await userEvent.click(screen.getByRole("button", { name: /跳过（暂不处理）|Skip for now/ }));
  expect(spy).not.toHaveBeenCalled();
});


// ── 从右往左的语言（2026-08-27 阿语生产实测）───────────────────────────
// 没有 dir 时整段按 LTR 排：正文靠左对齐，句尾的 `.` 是中性字符、被推到视觉最右端，
// 于是每句话前面挂一个句号。而以 `؟` 结尾的行反而正常（阿拉伯问号是强 RTL 字符），
// 症状时有时无，更不容易被认出是排版问题。
// 判据取 dir="auto" 而不是 "rtl"：详情页的语种是运行时才知道的，写死要另维护 RTL 清单。
describe("阿拉伯语等 RTL 语种的正文方向", () => {
  const AR_SEGS = [
    { t: "00:00:00", s: "شكرًا لك على وقتك. اليوم أريد أن نتحدث عن سلسلة التوريد.", sp: "主持人" },
    { t: "00:00:10", s: "بكل سرور، تفضل.", sp: "被访者" },
  ];

  function renderAr() {
    return render(
      <UILangProvider>
        <Result
          lang="ar"
          job={{ file: { name: "ar.m4a", size: "", duration: "" }, min: 0 }}
          jobId="job-ar"
          segments={AR_SEGS}
          metrics={null}
          review={[]}
        />
      </UILangProvider>,
    );
  }

  it("正文容器交给浏览器自己判方向", () => {
    const { container } = renderAr();
    const line = screen.getByText(/شكرًا لك على وقتك/);
    // 从文本节点往上找到那个带 dir 的容器（正文列）
    const holder = line.closest("[dir]");
    expect(holder).not.toBeNull();
    expect(holder!.getAttribute("dir")).toBe("auto");
    expect(container).toBeTruthy();
  });

  it("LTR 语种走同一条路径，不需要为它们另开分支", () => {
    // auto 对现有 26 门的判定结果与不写 dir 时一致 —— 所以这一处不必按语种分叉。
    // 这条钉住「别改成 lang==='ar' ? 'rtl' : undefined」那种写法：那会漏掉希伯来语等
    // 将来可能加进来的 RTL 语种，而且每加一门都要记得改这里。
    render(
      <UILangProvider>
        <Result
          lang="zh"
          job={{ file: { name: "zh.m4a", size: "", duration: "" }, min: 0 }}
          jobId="job-zh" segments={SEGS} metrics={null} review={[]}
        />
      </UILangProvider>,
    );
    const zhLine = screen.getByText("你好");
    expect(zhLine.closest("[dir]")!.getAttribute("dir")).toBe("auto");
  });
});


// ── 下载文件名（2026-08-28）────────────────────────────────────────────
// 一单最多下四份东西：转录 / 视角转换 / 脱敏 / 质检报告。转录稿不带后缀的话，
// 它和加工稿在下载目录里只差扩展名，看不出谁是谁。判据是「音频名 + 这一步的名字」。
describe("转录稿的下载文件名", () => {
  it("下载名 =「音频名-产物名」", async () => {
    vi.mocked(downloadUrl).mockClear();
    renderResult();
    await userEvent.click(screen.getByText(/下载 \.docx|Download \.docx/));
    const url = vi.mocked(downloadUrl).mock.calls.at(-1)![0] as string;
    expect(decodeURIComponent(url)).toContain("name=a-转录稿");
  });
});

// ── 说话人归属卡（2026-08-28）：P3 把 [❓] 打在说话人标签上 → 三选一 ──
describe("说话人归属卡", () => {
  const SPK_SEGS = [
    { t: "00:00:01", s: "我们今天想聊聊你们的采购流程。", sp: "主持人" },
    { t: "00:00:05", s: "好的，我们大概是从去年三季度开始调整的。", sp: "主持人" },
  ];
  const SPK_REVIEW = [{
    type: "speaker" as const, term: "好的，我们大概是从去年…", tag: "", confidence: "low" as const,
    reason: "这一段是谁说的没能定下来——请确认归属。",
    occurrences: [{ t: "00:00:05", speaker: "主持人", lineText: "好的，我们大概是从去年三季度开始调整的。" }],
  }];
  const renderSpk = () => render(
    <UILangProvider>
      <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
        jobId="job1" segments={SPK_SEGS} metrics={null} review={SPK_REVIEW} />
    </UILangProvider>,
  );

  it("给出三个归属选项，且不给「无误」这种不表态的出口", () => {
    renderSpk();
    // 模型已经说了它定不下来；再给个「无误」只会让人点它了事，那就等于没问
    expect(screen.queryByRole("button", { name: /这一处无误/ })).toBeNull();
    for (const name of ["主持人", "被访者", "多人混合"]) {
      expect(screen.getAllByRole("button", { name }).length).toBeGreaterThan(0);
    }
  });

  it("选「多人混合」→ 正文那一行的说话人真的改掉（导出即带这个注释）", async () => {
    renderSpk();
    // 改之前：两行都是主持人
    expect(screen.getAllByText("主持人").length).toBeGreaterThan(1);
    await userEvent.click(screen.getAllByRole("button", { name: "多人混合" })[0]);
    await waitFor(() => {
      // 正文里出现「多人混合」这个说话人名 —— 它就是给读者看的注释
      expect(screen.getAllByText("多人混合").length).toBeGreaterThan(0);
    });
  });

  it("正文里不给它画词级下划线（term 只是句首截来的一段，划出来是条没含义的线）", () => {
    // ⚠️ 判据必须让 term **真的是正文的子串**：短到不加省略号时 term 就等于整行。
    // 拿带「…」的 term 试是白测的——它本来就 indexOf 不到，有没有 bug 都不画（第一版就这么假绿）。
    const SHORT = "好的，就是这样。";
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1"
          segments={[{ t: "00:00:05", s: SHORT, sp: "主持人" }]} metrics={null}
          review={[{ ...SPK_REVIEW[0], term: SHORT, occurrences: [{ t: "00:00:05", speaker: "主持人", lineText: SHORT }] }]} />
      </UILangProvider>,
    );
    const marked = screen.queryAllByRole("button").filter((b) => b.textContent === SHORT);
    expect(marked).toHaveLength(0);
  });
});

// ⚠️ 这条守的是**运行时**：SPEAKER_CHOICES 是 const（有暂时性死区），
// 声明挪到「N 位说话人」那行之后 = 打开详情页当场 ReferenceError，而 tsc 一声不吭。
describe("说话人计数", () => {
  it("「多人混合」不计入人数——它是没能拆开的记号，不是一个人", () => {
    render(
      <UILangProvider>
        <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }} jobId="job1"
          segments={[
            { t: "00:00:01", s: "你好", sp: "主持人" },
            { t: "00:00:05", s: "在的", sp: "被访者" },
            { t: "00:00:09", s: "一起说的", sp: "多人混合" },
          ]} metrics={null} review={[]} />
      </UILangProvider>,
    );
    expect(screen.getByText(/2 位说话人/)).toBeTruthy();
  });
});

// ── 阿拉伯语：整行镜像 + 标签按界面语言（2026-08-29） ──
describe("从右往左语种", () => {
  const AR = [
    { t: "00:00:00", s: "شكرًا لك على وقتك.", sp: "主持人" },
    { t: "00:00:10", s: "بكل سرور، تفضل.", sp: "被访者" },
  ];
  const renderAr = (lang = "ar") => render(
    <UILangProvider>
      <Result lang={lang} job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
        jobId="job1" segments={AR} metrics={null} review={[]} />
    </UILangProvider>,
  );

  it("整行镜像：方向设在行上，不是只设在正文那一格", () => {
    // ⚠️ 判据要看**带 dir 的那个元素里装了什么**——只给正文那一格设 dir 时，
    // 说话人还在左边、正文在自己那格里靠右排，中间空一大块（2026-08-29 Duner 实见）。
    // 所以要求那个元素**同时装着说话人标签和正文**，即它是整行而不是正文格。
    // （只查 `display: flex` 是不行的：行容器在方案乙下是块级，判据会跟着版式一起漂。）
    const { container } = renderAr();
    const rtl = Array.from(container.querySelectorAll('[dir="rtl"]'))
      .find((el) => (el.textContent ?? "").includes("شكرًا"));
    expect(rtl).toBeTruthy();
    expect(rtl!.textContent).toContain("主持人");   // 中文界面渲染的，标签与正文在同一个 dir 里
  });

  it("时间码钉死从左往右：数字方向本身有含义（0:10 不能读成 10:0）", () => {
    const { container } = renderAr();
    const t = Array.from(container.querySelectorAll('[dir="ltr"]'))
      .find((el) => /^\d+:\d+$/.test(el.textContent ?? ""));
    expect(t).toBeTruthy();
  });

  it("非 RTL 语种一行都不翻", () => {
    const { container } = renderAr("zh");
    expect(container.querySelector('[dir="rtl"]')).toBeNull();
  });
});

describe("说话人标签按界面语言（8 门）", () => {
  const SEG = [{ t: "00:00:01", s: "hello", sp: "主持人" }];
  const renderUi = (ui: "zh" | "en" | "de" | "ja") => render(
    <UILangProvider initial={ui}>
      <Result lang="es" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
        jobId="job1" segments={SEG} metrics={null} review={[]} />
    </UILangProvider>,
  );
  // 录音是西班牙语、标签跟**界面**走——「我们写的字跟界面语言走，录音内容不翻」。
  // 判据取各门**自己的译文**，不是「不等于中文」：后者在回落英文时也能过。
  it.each([["en", "Interviewer"], ["de", "Interviewer"], ["ja", "インタビュアー"], ["zh", "主持人"]] as const)(
    "%s 界面显示 %s", (ui, want) => {
      renderUi(ui);
      expect(screen.getByText(want)).toBeTruthy();
      if (ui !== "zh") expect(screen.queryByText("主持人")).toBeNull();
    });
});

describe("转录行版式（方案乙）", () => {
  const SEG = [
    { t: "00:00:01", s: "第一句话在这里", sp: "主持人" },
    { t: "00:00:09", s: "第二句话在这里", sp: "被访者" },
    { t: "00:00:17", s: "第三句还是他说的", sp: "被访者" },
  ];
  const renderRows = () => render(
    <UILangProvider initial="zh">
      <Result lang="zh" job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
        jobId="job1" segments={SEG} metrics={null} review={[]} />
    </UILangProvider>,
  );

  it("说话人标签明显小于正文：Duner 定的行高上限靠它守住", () => {
    // ⚠️ 判据是**两者的比值**，不是「等于 11px」——写死数字的话，正文字号改了这条还是绿的，
    // 而那时候标签相对就不小了。实测 12px 的标签会让整篇总高到 1.44 倍，超过 1.4 的上限。
    const { container } = renderRows();
    const sp = Array.from(container.querySelectorAll("span")).find((e) => e.textContent === "主持人");
    const body = Array.from(container.querySelectorAll("div")).find((e) => e.textContent === "第一句话在这里");
    expect(sp && body).toBeTruthy();
    const px = (el: Element) => parseFloat((el as HTMLElement).style.fontSize || "0");
    expect(px(sp!)).toBeGreaterThan(0);
    expect(px(sp!)).toBeLessThanOrEqual(px(body!) * 0.75);
  });

  it("说话人不再是定宽列：标签多长都不影响正文起点", () => {
    // 原来那一列写死 44px（照中文四个字定的），译文长两三倍就把列撑开、正文左缘参差。
    const { container } = renderRows();
    const sp = Array.from(container.querySelectorAll("span")).find((e) => e.textContent === "主持人");
    expect((sp as HTMLElement).style.flex).toBe("");
  });

  it("同一个人连着说只标一次", () => {
    renderRows();
    expect(screen.getAllByText("被访者")).toHaveLength(1);   // 第 2、3 行同一个人
    expect(screen.getAllByText("主持人")).toHaveLength(1);
  });
});

// ── 复核卡里「我们自己写的」那几句原因跟界面语言走（2026-08-30，第三批） ──
//
// 判据只认**德语界面**：中/英两门在源码里就是 `L(zh, en)` 的两个字面量，
// 光测它们等于在测 L() 本身；德语要经对照本查表，才真的验到「这句被翻了」。
// 三张卡的说明是我们写的（说话人 / 联网核实 / 报告漏登记的补卡），
// 其余卡的原因来自 P3 报告、由后端的语言指令管（pipeline/p3_lang.py），不在这里测。
describe("我们自己写的卡片说明跟界面语言走", () => {
  const SEG1 = [{ t: "00:00:01", s: "Bodegas Aranchel 很小", sp: "主持人" }];

  function renderWith(review: any[]) {
    return render(
      <UILangProvider initial="de">
        <Result onBack={() => {}} lang="zh"
          job={{ file: { name: "a.m4a", size: "", duration: "" }, min: 0 }}
          jobId="j" segments={SEG1} metrics={null} review={review} />
      </UILangProvider>,
    );
  }

  it("联网核实卡的说明是德语，不是后端那句中文", () => {
    renderWith([{
      type: "web", term: "Bodegas Aranchel", tag: "", confidence: "high",
      reason: "术语库未收录，系统自动联网核实",
      web: { count: 1, searches: [{ query: "Bodegas Aranchel", summary: "x", source: "web" }] },
      occurrences: [{ t: "00:00:01", speaker: "主持人", lineText: "Bodegas Aranchel 很小" }],
    }]);
    expect(document.body.textContent).not.toContain("术语库未收录");
  });

  it("报告没登记、就地补出来的那张卡也翻（靠 unreported，不能只看 type）", () => {
    renderWith([{
      type: "doubt", term: "Aranchel", tag: "", confidence: "low", unreported: true,
      reason: "模型在终稿这一处标了存疑，但报告里没有写明原因——请自行核对这一句",
      occurrences: [{ t: "00:00:01", speaker: "主持人", lineText: "Bodegas Aranchel 很小" }],
    }]);
    expect(document.body.textContent).toContain("Diese Zeile wurde im Entwurf");
    expect(document.body.textContent).not.toContain("模型在终稿这一处");
  });

  it("模型写的原因原样显示——它由 P3 的语言指令管，前端不许猜着翻", () => {
    renderWith([{
      type: "doubt", term: "Aranchel", tag: "", confidence: "low",
      reason: "Nicht eindeutig zu bestimmen",
      occurrences: [{ t: "00:00:01", speaker: "主持人", lineText: "Bodegas Aranchel 很小" }],
    }]);
    expect(document.body.textContent).toContain("Nicht eindeutig zu bestimmen");
  });
});

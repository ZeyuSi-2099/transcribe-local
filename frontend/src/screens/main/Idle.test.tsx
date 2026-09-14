import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../../lib/i18n";
import { costFor, usd, RATE_PER_HOUR, RATE_PER_MIN } from "../../lib/pricing";
import { Idle } from "./Idle";

function renderIdle(onStart = vi.fn(), balance = 100, onTopUp = vi.fn(), lang = "zh") {
  render(
    <UILangProvider>
      <Idle onStart={onStart} lang={lang} setLang={() => {}} balance={balance} onTopUp={onTopUp} />
    </UILangProvider>,
  );
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  return { onStart, onTopUp, input };
}

const audio = (name: string) => new File(["x"], name, { type: "audio/mp4" });

// jsdom 不解码音频：拦截 createElement("audio")，让 ingest 同步拿到指定时长。
// 预估费用随语种档位变，测试一律用 costFor() 推期望值，不写死金额。
function mockAudioDuration(sec: number) {
  const orig = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
    if (tag !== "audio") return orig(tag);
    const fake: Record<string, unknown> = { preload: "" };
    Object.defineProperty(fake, "src", {
      set() {
        fake.duration = sec;
        (fake.onloadedmetadata as (() => void) | undefined)?.();
      },
    });
    return fake as unknown as HTMLElement;
  }) as typeof document.createElement);
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Idle — explicit start", () => {
  it("does NOT start transcribing immediately when a file is chosen", async () => {
    const { onStart, input } = renderIdle();
    await userEvent.upload(input, audio("interview.m4a"));
    expect(onStart).not.toHaveBeenCalled();
    // 已选文件名应展示出来，让用户确认是否传错
    expect(screen.getByText(/interview\.m4a/)).toBeTruthy();
  });

  it("starts only after the user clicks 开始", async () => {
    const { onStart, input } = renderIdle();
    await userEvent.upload(input, audio("interview.m4a"));
    await userEvent.click(screen.getByText(/开始转录/));
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onStart.mock.calls[0][0].name).toBe("interview.m4a");
    // 第二参数已是时长（number 或 null——取不到时长时为 null），不再是录音类型（2026-08-18 下架）
    expect(["number", "object"]).toContain(typeof onStart.mock.calls[0][1]);
    expect(onStart.mock.calls[0][1]).not.toBe("phonecall");
  });

  it("rejects a non-audio/video file with an inline error and does not ingest it", () => {
    const { onStart, input } = renderIdle();
    fireEvent.change(input, { target: { files: [new File(["x"], "notes.txt", { type: "text/plain" })] } });
    expect(screen.getByText(/不支持的格式|Unsupported format/)).toBeTruthy();
    expect(screen.queryByText(/notes\.txt/)).toBeNull();        // 没收进卡片
    expect(screen.queryByText(/开始转录|^Start$/)).toBeNull();   // 没有「开始转录」按钮
    expect(onStart).not.toHaveBeenCalled();
  });

  it("lets the user replace a wrong file before starting", async () => {
    const { onStart, input } = renderIdle();
    await userEvent.upload(input, audio("wrong.m4a"));
    expect(screen.getByText(/wrong\.m4a/)).toBeTruthy();
    await userEvent.upload(input, audio("right.m4a"));
    expect(screen.getByText(/right\.m4a/)).toBeTruthy();
    expect(screen.queryByText(/wrong\.m4a/)).toBeNull();
    expect(onStart).not.toHaveBeenCalled();
  });
});

describe("Idle — 估费", () => {
  it("预估费用 = 时长 × 单价，且 27 门语言同价（2026-08-31 定价 V3 塌成单档）", async () => {
    mockAudioDuration(3600);
    renderIdle(vi.fn(), 1000, vi.fn(), "ru");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, audio("ru.m4a"));
    // findAll 而不是 find：单档之后 1 小时的估费与左栏 HeroPitch 的每小时单价
    // 恰好是同一个数（$3.00），页面上本来就会出现两处。
    expect((await screen.findAllByText(usd(costFor(3600, "ru")))).length).toBeGreaterThan(0);
    // 换成任何一门语言，同样一小时都是同一个数——「按语种分档」真的没了
    expect(costFor(3600, "en")).toBe(costFor(3600, "ru"));
    expect(costFor(3600, "zh")).toBe(costFor(3600, "ru"));
  });

  // 单价那一行的**单位**（2026-08-31 定价 V3 验收补）：站上其余每一处都按小时报价，
  // 这一行曾是全站唯一按分钟的，于是同一屏左栏写每小时价、右栏写每分钟价。
  // ⚠️ 判据是「每分钟的那个数不许出现在页面上」，不是「有没有『小时』两个字」——
  // 后者在单位没改、只是旁边多了句带「小时」的文案时照样绿。
  it("单价那一行按小时报价：页面上不出现每分钟的数", async () => {
    mockAudioDuration(1800);
    renderIdle(vi.fn(), 1000, vi.fn(), "zh");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, audio("zh.m4a"));
    expect((await screen.findAllByText(new RegExp(`${usd(RATE_PER_HOUR).replace("$", "\\$")}\\s*/`))).length)
      .toBeGreaterThan(0);
    expect(screen.queryByText(new RegExp(`${usd(RATE_PER_MIN).replace("$", "\\$")}\\s*/`))).toBeNull();
  });

  it("免费额度抵扣：预估先抵免费秒、剩余按单价；软墙不把免费覆盖的部分算成缺口", async () => {
    mockAudioDuration(3600);   // 1 小时
    render(
      <UILangProvider>
        <Idle onStart={vi.fn()} lang="zh" setLang={() => {}} balance={30} onTopUp={vi.fn()}
          freeLeftSeconds={1800} />
      </UILangProvider>,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, audio("zh.m4a"));
    // 3600s − 1800s 免费 = 1800s 付费 → 期望值从 costFor 推，不写死金额
    expect((await screen.findAllByText(usd(costFor(1800, "zh")))).length).toBeGreaterThan(0);
    expect(screen.getByText(/已抵免费|free −/)).toBeInTheDocument();
    expect(screen.getByText(/开始转录|^Start/)).toBeInTheDocument();     // 软墙没挡
    expect(screen.getByText(/免费额度剩余|Free quota left/)).toBeInTheDocument();
  });

  it("长文件照样吃免费额度：先抵额度，超出的部分才按单价估", async () => {
    // 2026-08-15 去掉了「免费额度只能用于 ≤90 分钟」那道闸——它与分钟预算重复，
    // 唯一作用是让企业号的额度覆盖不了满 4 小时。这条测试守着别改回去。
    mockAudioDuration(91 * 60);
    render(
      <UILangProvider>
        <Idle onStart={vi.fn()} lang="zh" setLang={() => {}} balance={1000} onTopUp={vi.fn()}
          freeLeftSeconds={3600} />
      </UILangProvider>,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, audio("long.m4a"));
    // 91 分钟 − 60 分钟免费额度 = 31 分钟按现价计（期望值从 costFor 推，不写死金额）
    expect(await screen.findByText(usd(costFor(31 * 60, "zh")))).toBeTruthy();
  });

  it("IP 闸触发（freeLimited）显示「同网络已有试用」提示", () => {
    render(
      <UILangProvider>
        <Idle onStart={vi.fn()} lang="zh" setLang={() => {}} balance={0} onTopUp={vi.fn()}
          freeLeftSeconds={0} freeLimited />
      </UILangProvider>,
    );
    expect(screen.getByText(/同网络已有试用|already has a trial/)).toBeInTheDocument();
  });

  // 2026-08-18「项目」下架（见 CLAUDE.md）：项目是永久锁价承诺而我们没有结项/删除手段，
  // 入口先摘掉。上传页的估费从此只认全局现价。
  it("上传页没有项目选择器；估费走全局现价", async () => {
    mockAudioDuration(3600);
    render(
      <UILangProvider>
        <Idle onStart={vi.fn()} lang="zh" setLang={() => {}} balance={1000} onTopUp={vi.fn()} />
      </UILangProvider>,
    );
    expect(screen.queryByText(/不归入项目|No project/)).toBeNull();
    expect(screen.queryByText(/新建项目|New project/)).toBeNull();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, audio("zh.m4a"));
    expect((await screen.findAllByText(usd(costFor(3600, "zh")))).length).toBeGreaterThan(0);
  });
});

describe("Idle — in-card soft wall", () => {
  it("blocks start when est. cost exceeds balance and pre-fills the shortfall", async () => {
    mockAudioDuration(3600);
    const balance = 1;
    const shortfall = Math.ceil(costFor(3600, "ru") - balance);   // 小语种档 1 小时，$1 余额远不够
    const { onStart, onTopUp, input } = renderIdle(vi.fn(), balance, vi.fn(), "ru");
    await userEvent.upload(input, audio("long.m4a"));
    expect(await screen.findByText(/先充值|Top up first/)).toBeTruthy();
    expect(screen.queryByText(/开始转录/)).toBeNull();
    await userEvent.click(screen.getByText(/先充值|Top up first/));
    expect(onTopUp).toHaveBeenCalledWith(shortfall);
    expect(onStart).not.toHaveBeenCalled();
  });

  it("starts normally when balance covers the cost", async () => {
    mockAudioDuration(600);
    const { onStart, input } = renderIdle(vi.fn(), 100);
    await userEvent.upload(input, audio("ok.m4a"));
    await userEvent.click(await screen.findByText(/开始转录/));
    expect(onStart).toHaveBeenCalled();
  });
});

// ── 上传页左栏：三种内容，但栅格恒定 ────────────────────────────────────────
// 这一屏本地起不来后端（要登录才到得了），所以判据全落在渲染结果上。
// ⚠️ 2026-09-01 改过一次做法：原来是「没有稿子就整卡居中」，于是这一屏的布局取决于一个
// 要等半秒才回来的接口——有历史的老用户每次进来都先看到卡片居中、再看它横着跳到右边。
// 中间试过用本地提示位「照上次的结论猜」，在无痕窗口里那个位子永远是空的，等于没修。
// 现在**不猜了**：栅格恒定两栏，左栏只换内容。
describe("Idle — 左栏与首帧布局", () => {
  const done = (n: string) => ({ n, d: { zh: "9月1日", en: "Sep 1" }, dur: "12:00", cost: 1, lang: "zh", st: "done" as const });

  function renderWith(recent?: Parameters<typeof Idle>[0]["recent"]) {
    return render(
      <UILangProvider>
        <Idle onStart={vi.fn()} lang="zh" setLang={() => {}} balance={100} onTopUp={vi.fn()} recent={recent} />
      </UILangProvider>,
    );
  }
  const grid = (c: HTMLElement) => (c.firstElementChild as HTMLElement).style.gridTemplateColumns;

  it("有已完成的稿子 → 左栏是「最近的转录」", () => {
    const { container } = renderWith([done("a.mp3")]);
    expect(screen.getByText(/最近的转录/)).toBeTruthy();
    expect(screen.getByText("a.mp3")).toBeTruthy();
    expect(grid(container)).toBe("1fr 468px");
  });

  it("确实一份都没有 → 左栏是第一次上传的三步，不是卖点", () => {
    const { container } = renderWith([]);
    expect(screen.getByText(/第一份稿子，从这里开始/)).toBeTruthy();
    expect(screen.getByText(/我们把拿不准的地方逐处标出来/)).toBeTruthy();
    expect(grid(container)).toBe("1fr 468px");
  });

  it("还没拉回来 → 左栏空着占位，但栅格一样宽（上传卡不会横着跳）", () => {
    const { container } = renderWith(undefined);
    expect(grid(container)).toBe("1fr 468px");
    // 三态的栅格逐字相同，才谈得上「不跳」
    expect(screen.queryByText(/最近的转录/)).toBeNull();
    expect(screen.queryByText(/第一份稿子，从这里开始/)).toBeNull();
    // ⚠️ 占位元素不能省：栅格按出现顺序放，不占的话上传卡会掉进 1fr 那一列去
    const cells = (container.firstElementChild as HTMLElement).children;
    expect(cells.length, "左栏占位 + 上传卡（文件框视觉隐藏但仍在树里）").toBeGreaterThanOrEqual(3);
  });

  it("三态的栅格逐字相同——这就是「不跳」的全部含义", () => {
    const a = renderWith([done("a.mp3")]); const g1 = grid(a.container); a.unmount();
    const b = renderWith([]);              const g2 = grid(b.container); b.unmount();
    const c = renderWith(undefined);       const g3 = grid(c.container);
    expect([g1, g2, g3]).toEqual([g1, g1, g1]);
  });
});

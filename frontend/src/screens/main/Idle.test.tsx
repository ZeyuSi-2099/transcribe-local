import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../../lib/i18n";
import { Idle } from "./Idle";

// 本机版（与线上不同）：线上的「估费」「卡内软墙」两组随收费一起去掉，改测「不出现任何金额」。
function renderIdle(onStart = vi.fn(), lang = "zh") {
  render(
    <UILangProvider>
      <Idle onStart={onStart} lang={lang} setLang={() => {}} />
    </UILangProvider>,
  );
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  return { onStart, input };
}

const audio = (name: string) => new File(["x"], name, { type: "audio/mp4" });

// jsdom 不解码音频：拦截 createElement("audio")，让 ingest 同步拿到指定时长。
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

describe("Idle — 本机不收费", () => {
  it("选好文件后只报时长，页面上没有费用、余额、充值与免费额度", async () => {
    mockAudioDuration(3600);
    const { input } = renderIdle();
    await userEvent.upload(input, audio("zh.m4a"));
    expect((await screen.findAllByText("1:00:00")).length).toBeGreaterThan(0);
    const body = document.body.textContent || "";
    expect(body).not.toMatch(/\$\d|预估费用|余额|充值|免费额度|计费/);
    expect(screen.getByText(/开始转录/)).toBeInTheDocument();
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
        <Idle onStart={vi.fn()} lang="zh" setLang={() => {}} recent={recent} />
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

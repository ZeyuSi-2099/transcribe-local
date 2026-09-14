import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { UILangProvider } from "../../lib/i18n";
import { MainPage } from "./MainPage";
import type { Flow } from "../../lib/flow";

const flow: Flow = { state: "idle", progress: 0, displayProgress: 0, phase: null, jobId: null, segments: null, metrics: null, review: null, error: null, disconnected: false, start: vi.fn(), reset: vi.fn() };

describe("MainPage", () => {
  it("shows idle (language picker) on the new page", () => {
    render(<UILangProvider><MainPage flow={flow} onStartJob={vi.fn()} /></UILangProvider>);
    expect(screen.getByText(/音频语言|Audio language/)).toBeInTheDocument();
  });
  // 本机版：默认选中本地能转的中文（线上默认英语），英语那一格是灰的
  it("默认选中中文，本地不支持的语言点不了", () => {
    render(<UILangProvider><MainPage flow={flow} onStartJob={vi.fn()} /></UILangProvider>);
    expect(screen.getByRole("button", { name: /^中文$|^Chinese$/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^英语$|^English$/ })).toBeDisabled();
  });
  // 2026-08-18「录音类型」下架：那个选择框对 27 门里的 26 门不产生任何后果（只有中文配了
  // 3 条参考轨），对中文它的唯一作用是「跑不跑讯飞」——而这个判断不该转嫁给用户。
  it("不再有录音类型选择器（场景分流已取消，一律按最准的方案跑）", () => {
    render(<UILangProvider><MainPage flow={flow} onStartJob={vi.fn()} /></UILangProvider>);
    expect(screen.queryByText(/录音类型|Recording type/)).toBeNull();
    expect(screen.queryByText(/电话访谈|现场面访|Phone interview|In-person/)).toBeNull();
  });
});

// HistoryPage · 后处理行内 caption 三态（README §3：无子行无折叠，占文件名下 caption 位）
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { HistoryPage } from "./HistoryPage";
import type { HistoryItem } from "../lib/sampleData";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

const base = { d: { zh: "今天 10:00", en: "Today 10:00" }, dur: "10:00", cost: 1.0, lang: "zh" };

const ppRunning: HistoryItem = {
  n: "季度回访-王总监.m4a", ...base, st: "done", id: "j1",
  pp: { status: "running", stepIndex: 2, totalSteps: 3, currentStep: "categorize", qcFixCount: 0, products: [] },
};
const ppDone: HistoryItem = {
  n: "渠道商访谈-李经理.m4a", ...base, st: "done", id: "j2",
  pp: { status: "done", stepIndex: 3, totalSteps: 3, currentStep: null, qcFixCount: 8, products: ["narrate", "categorize", "redact"] },
};
const ppFailed: HistoryItem = {
  n: "供应商访谈-张总.m4a", ...base, st: "done", id: "j3",
  pp: { status: "failed", stepIndex: 2, totalSteps: 3, currentStep: "categorize", qcFixCount: 0, products: [] },
};

describe("HistoryPage 后处理 caption 三态", () => {
  it("加工中：bgTint 徽章 + mono「步名 % · 第 N/M 步 · 可离开」，且计入「处理中」tab", () => {
    wrap(<HistoryPage items={[ppRunning]} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/✦ 后处理|✦ Post-processing/)).toBeInTheDocument();
    expect(screen.getByText(/归类 \d+% · 第 2\/3 步 · 可离开|Categorize \d+% · step 2\/3 · can leave/)).toBeInTheDocument();
    // 状态列保持转录语义（已完成）——tab 计数按钮也含「已完成」字样，故取 all
    expect(screen.getAllByText(/^已完成$|^Done$/).length).toBeGreaterThan(0);
    // 「处理中」tab 同时计入加工中的行
    expect(screen.getByRole("button", { name: /处理中 1|Processing 1/ })).toBeInTheDocument();
  });

  it("已加工：sunken 徽章 green 文 + 产物名列举 + 质检修复 N 处", () => {
    wrap(<HistoryPage items={[ppDone]} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/✦ 已加工|✦ Processed/)).toBeInTheDocument();
    expect(screen.getByText(/叙述稿 · 归类纪要 · 脱敏稿|Narrative draft · Categorized minutes · Redacted transcript/)).toBeInTheDocument();
    expect(screen.getByText(/质检修复 8 处|QC fixed 8/)).toBeInTheDocument();
    // 已加工不进「处理中」tab
    expect(screen.getByRole("button", { name: /处理中 0|Processing 0/ })).toBeInTheDocument();
  });

  it("失败：bgSoft 徽章「后处理失败 · 未计费」+「进详情页重试」，状态列不变", () => {
    wrap(<HistoryPage items={[ppFailed]} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/✦ 后处理失败 · 未计费|✦ Post-processing failed · not billed/)).toBeInTheDocument();
    expect(screen.getByText(/进详情页重试|retry from the transcript page/)).toBeInTheDocument();
    expect(screen.getAllByText(/^已完成$|^Done$/).length).toBeGreaterThan(0);
    // 列表不放重试按钮（下载/重试统一收到详情页）
    expect(screen.queryByRole("button", { name: /^重试$|^Retry$/ })).toBeNull();
  });

  it("点整行仍进详情页（列表不放操作）", async () => {
    const onOpen = vi.fn();
    wrap(<HistoryPage items={[ppDone]} onNew={vi.fn()} onOpen={onOpen} />);
    await userEvent.click(screen.getByText("渠道商访谈-李经理.m4a"));
    expect(onOpen).toHaveBeenCalled();
  });

  it("无后处理任务的行：不渲染任何 pp caption", () => {
    wrap(<HistoryPage items={[{ n: "普通.m4a", ...base, st: "done" }]} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.queryByText(/✦/)).toBeNull();
  });
});

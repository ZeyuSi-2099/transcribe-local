import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { SettingsModal } from "./SettingsModal";
import * as api from "../lib/api";

// 本机版（与线上不同）：设置只剩「数据」——存放位置 + 删除转录记录。
// 线上的注销、推荐、余额、音频保留 7 天那几组测试随功能一起去掉；删除转录那组原样保留。
const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

describe("SettingsModal · 本机版", () => {
  it("requires a second confirm to delete all", async () => {
    wrap(<SettingsModal open onClose={vi.fn()} jobCount={3} />);
    expect(screen.queryByRole("button", { name: /确认删除 3 条|Delete 3 transcripts/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /删除全部|Delete all/ }));
    // 确认按钮上写出条数：「确认删除全部」看不出要失去什么
    expect(screen.getByRole("button", { name: /确认删除 3 条|Delete 3 transcripts/ })).toBeInTheDocument();
  });

  it("说的是本机的事实：存在这台电脑上、不自动删；没有账户那一套", () => {
    wrap(<SettingsModal open onClose={vi.fn()} />);
    const body = document.body.textContent || "";
    expect(body).toContain("存在这台电脑上");
    expect(body).not.toContain("7 天");
    expect(body).not.toMatch(/注销账户|余额|推荐同行|邮箱/);
  });
});

describe("SettingsModal · 删除转录记录", () => {
  beforeEach(() => vi.restoreAllMocks());

  const openAndConfirm = async () => {
    await userEvent.click(screen.getByRole("button", { name: /删除全部|Delete all/ }));
    const btn = screen.getByRole("button", { name: /确认删除|Delete \d/ });
    // DangerConfirm 有 3 秒防误点，测试里等它解锁
    await waitFor(() => expect(btn).toBeEnabled(), { timeout: 4000 });
    await userEvent.click(btn);
  };

  it("一条转录都没有时，按钮是禁用的（点了也没东西可删）", () => {
    wrap(<SettingsModal open onClose={vi.fn()} jobCount={0} />);
    expect(screen.getByRole("button", { name: /删除全部|Delete all/ })).toBeDisabled();
  });

  it("确认后真的调接口，并让调用方刷新列表", async () => {
    const spy = vi.spyOn(api, "purgeTranscripts").mockResolvedValue({ jobs: 3, files: 9 });
    const onPurged = vi.fn();
    wrap(<SettingsModal open onClose={vi.fn()} jobCount={3} onPurged={onPurged} />);
    await openAndConfirm();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    await waitFor(() => expect(onPurged).toHaveBeenCalled());
  });

  it("后端拒绝时显示原话，且不通知调用方刷新", async () => {
    vi.spyOn(api, "purgeTranscripts").mockRejectedValue(new Error("还有 1 个任务在进行中"));
    const onPurged = vi.fn();
    wrap(<SettingsModal open onClose={vi.fn()} jobCount={2} onPurged={onPurged} />);
    await openAndConfirm();
    expect(await screen.findByText(/还有 1 个任务在进行中/)).toBeInTheDocument();
    expect(onPurged).not.toHaveBeenCalled();
  });
});

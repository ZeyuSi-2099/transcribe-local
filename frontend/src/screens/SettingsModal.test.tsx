import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { SettingsModal } from "./SettingsModal";
import * as api from "../lib/api";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

const OK: api.DeletePreflight = {
  canDelete: true, blockers: [], balanceCents: 0, jobs: 3, glossaries: 2,
};

describe("SettingsModal", () => {
  it("requires a second confirm to delete all", async () => {
    wrap(<SettingsModal open onClose={vi.fn()} balance={28.5} onTopUp={vi.fn()} jobCount={3} />);
    expect(screen.queryByRole("button", { name: /确认删除 3 条|Delete 3 transcripts/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /删除全部|Delete all/ }));
    // 确认按钮上写出条数：「确认删除全部」看不出要失去什么
    expect(screen.getByRole("button", { name: /确认删除 3 条|Delete 3 transcripts/ })).toBeInTheDocument();
  });

  it("音频保留期：7 天（不出现「长期」/「indefinitely」）", () => {
    wrap(<SettingsModal open onClose={vi.fn()} balance={28.5} onTopUp={vi.fn()} />);
    const body = document.body.textContent || "";
    // 默认中文环境，检查中文文案
    expect(body).toContain("7 天");      // 中文：音频 7 天
    expect(body).toContain("30 天");     // 中文：稿 30 天
    expect(body).toContain("请及时导出");  // 中文：新增导出提示
    expect(body).not.toContain("长期保留");   // 中文：不出现「长期保留」
  });
});

describe("SettingsModal · 注销", () => {
  const EMAIL = "duner@corp.example";

  beforeEach(() => vi.restoreAllMocks());

  const open = async (props: Partial<Parameters<typeof SettingsModal>[0]> = {}) => {
    wrap(<SettingsModal open onClose={vi.fn()} balance={0} onTopUp={vi.fn()} email={EMAIL} {...props} />);
    await userEvent.click(screen.getByRole("button", { name: /注销账户|Close account/ }));
  };

  it("展开才去体检——那几个数只在按下那一刻才有意义", async () => {
    const spy = vi.spyOn(api, "getDeletePreflight").mockResolvedValue(OK);
    wrap(<SettingsModal open onClose={vi.fn()} balance={0} onTopUp={vi.fn()} email={EMAIL} />);
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /注销账户|Close account/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  });

  it("拦截理由原样显示，且不给确认按钮", async () => {
    vi.spyOn(api, "getDeletePreflight").mockResolvedValue({
      ...OK, canDelete: false,
      blockers: ["账户还有 $12.34 余额。按服务条款第 4 条，请先来信办理。"],
    });
    const del = vi.spyOn(api, "deleteAccount");
    await open();
    expect(await screen.findByText(/\$12\.34/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /永久注销|Delete permanently/ })).toBeNull();
    expect(del).not.toHaveBeenCalled();
  });

  it("邮箱没打对之前，确认按钮一直是禁用的（不留反悔期 → 确认动作必须够重）", async () => {
    vi.spyOn(api, "getDeletePreflight").mockResolvedValue(OK);
    const del = vi.spyOn(api, "deleteAccount").mockResolvedValue({ jobs: 3, files: 9 });
    await open();
    const btn = await screen.findByRole("button", { name: /永久注销|Delete permanently/ });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(EMAIL), "duner@corp.exampl");   // 差一个字符
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(EMAIL), "e");
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    await waitFor(() => expect(del).toHaveBeenCalledWith(EMAIL));
  });

  it("大小写不同也算打对了——用户很可能从别处粘贴过来", async () => {
    vi.spyOn(api, "getDeletePreflight").mockResolvedValue(OK);
    vi.spyOn(api, "deleteAccount").mockResolvedValue({ jobs: 0, files: 0 });
    await open();
    const btn = await screen.findByRole("button", { name: /永久注销|Delete permanently/ });
    await userEvent.type(screen.getByPlaceholderText(EMAIL), "DUNER@Corp.Example");
    expect(btn).toBeEnabled();
  });

  it("成功后交给调用方登出（账户已经没了，留在应用里只会到处 401）", async () => {
    vi.spyOn(api, "getDeletePreflight").mockResolvedValue(OK);
    vi.spyOn(api, "deleteAccount").mockResolvedValue({ jobs: 3, files: 9 });
    const onDeleted = vi.fn();
    await open({ onDeleted });
    await userEvent.type(await screen.findByPlaceholderText(EMAIL), EMAIL);
    await userEvent.click(screen.getByRole("button", { name: /永久注销|Delete permanently/ }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
  });

  it("后端拒绝时把原话显示出来，并且不登出", async () => {
    vi.spyOn(api, "getDeletePreflight").mockResolvedValue(OK);
    vi.spyOn(api, "deleteAccount").mockRejectedValue(new Error("还有 1 个任务在进行中"));
    const onDeleted = vi.fn();
    await open({ onDeleted });
    await userEvent.type(await screen.findByPlaceholderText(EMAIL), EMAIL);
    await userEvent.click(screen.getByRole("button", { name: /永久注销|Delete permanently/ }));
    expect(await screen.findByText(/还有 1 个任务在进行中/)).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
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
    wrap(<SettingsModal open onClose={vi.fn()} balance={0} onTopUp={vi.fn()} jobCount={0} />);
    expect(screen.getByRole("button", { name: /删除全部|Delete all/ })).toBeDisabled();
  });

  it("确认后真的调接口，并让调用方刷新列表", async () => {
    const spy = vi.spyOn(api, "purgeTranscripts").mockResolvedValue({ jobs: 3, files: 9 });
    const onPurged = vi.fn();
    wrap(<SettingsModal open onClose={vi.fn()} balance={0} onTopUp={vi.fn()} jobCount={3} onPurged={onPurged} />);
    await openAndConfirm();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    await waitFor(() => expect(onPurged).toHaveBeenCalled());
  });

  it("后端拒绝时显示原话，且不通知调用方刷新", async () => {
    vi.spyOn(api, "purgeTranscripts").mockRejectedValue(new Error("还有 1 个任务在进行中"));
    const onPurged = vi.fn();
    wrap(<SettingsModal open onClose={vi.fn()} balance={0} onTopUp={vi.fn()} jobCount={2} onPurged={onPurged} />);
    await openAndConfirm();
    expect(await screen.findByText(/还有 1 个任务在进行中/)).toBeInTheDocument();
    expect(onPurged).not.toHaveBeenCalled();
  });
});


describe("SettingsModal · 推荐同行（推荐礼金批次 C）", () => {
  const referral = { code: "AB2CD3EF", link: "https://transcribe.solutions/?ref=AB2CD3EF",
                     referredCount: 2, unlockedCents: 500, pending: null, received: false };

  it("后端没给推荐信息时整块不显示（老后端 / 演示态）", () => {
    wrap(<SettingsModal open onClose={vi.fn()} balance={1} onTopUp={vi.fn()} />);
    expect(screen.queryByText("推荐同行")).toBeNull();
  });

  it("有推荐信息：需求单那句文案一字不改、链接、已推荐人数与已解锁金额", () => {
    wrap(<SettingsModal open onClose={vi.fn()} balance={1} onTopUp={vi.fn()} referral={referral} />);
    expect(screen.getByText("推荐同行")).toBeInTheDocument();
    expect(screen.getByText("邀请同行试试，两人各得 $5 —— 对方首次充值时到账。")).toBeInTheDocument();
    expect(screen.getByTestId("referral-link").textContent).toBe(referral.link);
    expect(screen.getByText("已推荐 2 人 · 已解锁 $5.00")).toBeInTheDocument();
    expect(screen.queryByText(/首充即解锁/)).toBeNull();
  });

  it("被推荐人：待解锁那一行带有效期；解锁后变成「已到账」", () => {
    const { unmount } = wrap(<SettingsModal open onClose={vi.fn()} balance={1} onTopUp={vi.fn()}
      referral={{ ...referral, pending: { amountCents: 500, expiresAt: "2026-12-02" } }} />);
    expect(screen.getByText("推荐礼金 $5.00 · 首充即解锁 · 有效期至 2026-12-02")).toBeInTheDocument();
    unmount();
    wrap(<SettingsModal open onClose={vi.fn()} balance={1} onTopUp={vi.fn()} referral={{ ...referral, received: true }} />);
    expect(screen.getByText("推荐礼金 $5.00 已到账")).toBeInTheDocument();
  });

  it("「复制」把链接写进剪贴板并短暂显示已复制", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    wrap(<SettingsModal open onClose={vi.fn()} balance={1} onTopUp={vi.fn()} referral={referral} />);
    await userEvent.click(screen.getByRole("button", { name: "复制" }));
    expect(writeText).toHaveBeenCalledWith(referral.link);
    expect(await screen.findByRole("button", { name: "已复制" })).toBeInTheDocument();
  });
});

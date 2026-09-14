import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { Sidebar } from "./Sidebar";

const noop = () => {};
function renderSidebar(extra: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  return render(
    <UILangProvider>
      <Sidebar active="new" onNav={noop} onOpenBilling={noop} onOpenSettings={noop} onLogout={noop}
        email="a@b.com" balance={0} {...extra} />
    </UILangProvider>,
  );
}

beforeEach(() => { localStorage.clear(); });

describe("Sidebar — 免费额度显示", () => {
  it("有剩余免费额度时，余额卡下方显示「免费额度」+ 剩余时长", () => {
    renderSidebar({ freeLeftSeconds: 3600 });
    expect(screen.getByText(/免费额度|Free quota/)).toBeInTheDocument();
    // ⚠️ 报**分钟**，不是 fmtClock 的 1:00:00（2026-08-31）：额度是按分钟发的，
    // 用户收到的邮件里也是分钟；而上传卡那边同样报分钟——两处分家的症状是
    // 同一屏上出现两个长得不一样的同一个数。判据钉在「分钟」上，改回时钟格式即红。
    expect(screen.getByText(/^60 (分钟|min)$/)).toBeInTheDocument();
  });

  it("免费额度用完（0 秒）就不显示这一行", () => {
    renderSidebar({ freeLeftSeconds: 0 });
    expect(screen.queryByText(/免费额度|Free quota/)).toBeNull();
  });

  it("未传 freeLeftSeconds（旧调用方 / 演示模式）不显示、不报错", () => {
    renderSidebar();
    expect(screen.queryByText(/免费额度|Free quota/)).toBeNull();
    expect(screen.getByText(/余额|Balance/)).toBeInTheDocument();
  });
});

// 收起/展开原来是 <span onClick>：没有 role、没有 tabIndex、没有键盘处理——
// 只用键盘的人根本收不起也展不开。而收起态唯一的入口是那个 T. 标志，
// 它还看不出可以点（2026-08-22 巡检）。
describe("Sidebar — 收起/展开", () => {
  const KEY = "tx-sidebar-collapsed";

  it("展开态与收起态都是真按钮，键盘够得着，且说得出自己是干什么的", async () => {
    const { unmount } = renderSidebar();
    const collapse = screen.getByRole("button", { name: /收起侧边栏|Collapse sidebar/ });
    collapse.focus();
    expect(document.activeElement).toBe(collapse);
    await userEvent.keyboard("{Enter}");
    expect(localStorage.getItem(KEY)).toBe("1");
    // 收起之后必须还有一个看得见、按得到的入口把它展开回来
    const expand = screen.getByRole("button", { name: /展开侧边栏|Expand sidebar/ });
    expand.focus();
    expect(document.activeElement).toBe(expand);
    await userEvent.keyboard("{Enter}");
    expect(localStorage.getItem(KEY)).toBe("0");
    unmount();
  });
});

describe("Sidebar — 充值在路上（2026-09-06）", () => {
  it("有在路上的充值：余额下方显示「入账中」", () => {
    renderSidebar({ pendingTopupCents: 1000 });
    expect(screen.getByRole("status").textContent).toMatch(/\$10\.00 入账中|\$10\.00 on its way/);
  });
  it("到账那几秒显示「已到账」，优先于「入账中」", () => {
    renderSidebar({ pendingTopupCents: 0, justCreditedCents: 1000 });
    expect(screen.getByRole("status").textContent).toMatch(/\$10\.00 已到账|\$10\.00 credited/);
  });
  it("没有在路上的钱：这一行不渲染", () => {
    renderSidebar({ pendingTopupCents: 0 });
    expect(screen.queryByRole("status")).toBeNull();
  });
});


import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { Sidebar } from "./Sidebar";

// 本机版（与线上不同）：线上这里测余额卡、免费额度、在路上的充值——本机都没有了，
// 改测「没有账户那一套」和「设置入口」。收起/展开那条原样保留。
const noop = () => {};
function renderSidebar(extra: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  return render(
    <UILangProvider>
      <Sidebar active="new" onNav={noop} onOpenSettings={noop} {...extra} />
    </UILangProvider>,
  );
}

beforeEach(() => { localStorage.clear(); });

describe("Sidebar — 本机版", () => {
  it("没有余额、免费额度、充值与退出登录", () => {
    renderSidebar({ isAdmin: true });
    expect(screen.queryByText(/余额|Balance/)).toBeNull();
    expect(screen.queryByText(/免费额度|Free quota/)).toBeNull();
    expect(screen.queryByText(/充值与账单|Billing/)).toBeNull();
    expect(screen.queryByText(/退出登录|Sign out/)).toBeNull();
  });

  it("底部是「设置」入口，点了交给调用方开浮窗", async () => {
    let opened = false;
    renderSidebar({ onOpenSettings: () => { opened = true; } });
    await userEvent.click(screen.getByRole("button", { name: /^设置$|^Settings$/ }));
    expect(opened).toBe(true);
  });

  it("运营驾驶舱改名「运行面板」", () => {
    renderSidebar({ isAdmin: true });
    expect(screen.getByText(/^运行面板$|^System status$/)).toBeInTheDocument();
    expect(screen.queryByText(/运营驾驶舱|Operations/)).toBeNull();
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

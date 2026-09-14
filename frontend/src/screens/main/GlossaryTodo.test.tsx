// 待办层面板的护栏。最要紧的一条不是长相，是**它绝不能有采纳按钮**——
// 这块面板里每一条平台都给不出答案，给个「采纳」等于骗用户点一下就好了。
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GlossaryTodo } from "./GlossaryTodo";
import { parse } from "../../lib/glossary";
import type { Gap } from "../../lib/api";

const gapsOf = (...texts: string[]): Gap[] =>
  texts.map((t, i) => ({ id: `g${i}`, text: t, status: "open" as const }));

const blanksOf = (text: string) => parse(text).blankEntries;

describe("还缺这些 · 面板", () => {
  it("整块面板没有任何「采纳」——这里的每一条只能用户自己动手", () => {
    render(
      <GlossaryTodo
        blanks={blanksOf("带教 ｜ \n返点 ｜ 有释义")}
        gaps={gapsOf("缺经销层级的内部叫法")}
        scenario="drafted"
        onJump={() => {}}
        onSetGap={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /Adopt|采纳/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Go there →" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Got it" })).toBeInTheDocument();
  });

  it("待填的词从正文解析即得，跳转落点在竖线之后（用户接着打字就行）", async () => {
    const jump = vi.fn();
    const text = "## 角色\n带教 ｜ ";
    render(
      <GlossaryTodo blanks={blanksOf(text)} gaps={[]} scenario="drafted" onJump={jump} onSetGap={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Go there →" }));
    const at = jump.mock.calls[0][0];
    expect(text.slice(0, at)).toBe("## 角色\n带教 ｜");   // 光标正好落在竖线后
  });

  it("「知道了」只改状态、不删除——用户要能在「已处理」里找回来", async () => {
    const setGap = vi.fn();
    render(
      <GlossaryTodo blanks={[]} gaps={gapsOf("缺系统名")} scenario="checked" onJump={() => {}} onSetGap={setGap} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Got it" }));
    // 落库等收拢动画演完（DISMISS_MS）才发——但计数不等，见下一条
    await waitFor(() => expect(setGap).toHaveBeenCalledWith("g0", "handled"));
  });

  it("「知道了」当场把计数减 1，不等收拢动画演完", async () => {
    // 动画期间卡片还在 DOM 里（要演收拢），计数若跟着它走，用户点完会觉得没反应
    render(
      <GlossaryTodo blanks={[]} gaps={gapsOf("缺系统名", "缺工具名")} scenario="checked"
        onJump={() => {}} onSetGap={() => {}} />,
    );
    expect(screen.getByText("0 terms · 2 gaps")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Got it" })[0]);
    expect(screen.getByText("0 terms · 1 gaps")).toBeInTheDocument();
  });

  it("已处理的缺口不出现在待办列表里，计数也不算它", () => {
    const gaps: Gap[] = [
      { id: "a", text: "还没处理的", status: "open" },
      { id: "b", text: "已经处理的", status: "handled" },
    ];
    render(<GlossaryTodo blanks={[]} gaps={gaps} scenario="leftover" onJump={() => {}} onSetGap={() => {}} />);
    // 场景 C 默认折叠，计数只数 open 的那条
    expect(screen.getByText("0 terms · 1 gaps")).toBeInTheDocument();
  });

  it("场景 C（上次留下的）默认折叠；A/B 默认展开", () => {
    // 折叠靠 CSS 收拢（内容留在 DOM 才能演动画），所以断言「看不见」而不是「不存在」
    const { unmount } = render(
      <GlossaryTodo blanks={[]} gaps={gapsOf("缺系统名")} scenario="leftover" onJump={() => {}} onSetGap={() => {}} />,
    );
    expect(screen.getByRole("button", { name: /Still missing/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("缺系统名")).not.toBeVisible();
    unmount();

    render(<GlossaryTodo blanks={[]} gaps={gapsOf("缺系统名")} scenario="drafted" onJump={() => {}} onSetGap={() => {}} />);
    expect(screen.getByText("缺系统名")).toBeVisible();
  });

  it("三个场景的引导语不同（同一块面板，只有这句和展开态有别）", async () => {
    render(<GlossaryTodo blanks={[]} gaps={gapsOf("x")} scenario="drafted" onJump={() => {}} onSetGap={() => {}} />);
    expect(screen.getByText(/only you know/)).toBeInTheDocument();
  });

  it("没有任何待办时整块不渲染，不留空壳", () => {
    const { container } = render(
      <GlossaryTodo blanks={[]} gaps={[]} scenario="checked" onJump={() => {}} onSetGap={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

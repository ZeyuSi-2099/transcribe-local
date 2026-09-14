import { describe, it, expect, vi } from "vitest";
import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GlossaryEditor, type GlossaryEditorHandle } from "./GlossaryEditor";
import { MEANING_MAX_CHARS } from "../lib/glossary";

describe("GlossaryEditor", () => {
  it("把 value 显示在 textarea 里", () => {
    render(<GlossaryEditor value="FD ｜ 履约分销" onChange={() => {}} />);
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.value).toBe("FD ｜ 履约分销");
  });

  it("输入时以新文本调用 onChange", async () => {
    const fn = vi.fn();
    render(<GlossaryEditor value="" onChange={fn} />);
    await userEvent.type(screen.getByRole("textbox"), "a");
    expect(fn).toHaveBeenCalledWith("a");
  });

  it("高亮叠层渲染分类与术语文本（含全角竖线）", () => {
    const { container } = render(
      <GlossaryEditor value={"## 机构\nFD ｜ 履约分销"} onChange={() => {}} />,
    );
    const backdrop = container.querySelector('[aria-hidden="true"]');
    expect(backdrop?.textContent).toContain("机构");
    expect(backdrop?.textContent).toContain("FD");
    expect(backdrop?.textContent).toContain("｜");
  });

  it("含义超限的行显示 mlen/含义上限 标记", () => {
    const n = MEANING_MAX_CHARS + 1;
    render(<GlossaryEditor value={`词 ｜ ${"x".repeat(n)}`} onChange={() => {}} />);
    expect(screen.getByText(`${n}/${MEANING_MAX_CHARS}`)).toBeTruthy();
  });

  it("默认显示行号；showGutter=false 时隐藏", () => {
    const { container, rerender } = render(<GlossaryEditor value={"a\nb"} onChange={() => {}} />);
    expect(container.querySelectorAll("[data-gutter]").length).toBe(2);
    rerender(<GlossaryEditor value={"a\nb"} onChange={() => {}} showGutter={false} />);
    expect(container.querySelectorAll("[data-gutter]").length).toBe(0);
  });

  it("传入 header 时渲染在编辑器卡内", () => {
    render(<GlossaryEditor value="" onChange={() => {}} header={<span>术语 ｜ 含义</span>} />);
    expect(screen.getByText("术语 ｜ 含义")).toBeTruthy();
  });

  it("jumpTo 把光标定位到指定偏移", () => {
    const ref = createRef<GlossaryEditorHandle>();
    render(<GlossaryEditor ref={ref} value={"A ｜ a\nB ｜ b"} onChange={() => {}} />);
    ref.current!.jumpTo(6);
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).selectionStart).toBe(6);
  });
});

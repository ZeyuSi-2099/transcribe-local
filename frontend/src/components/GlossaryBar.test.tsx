import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GlossaryBar } from "./GlossaryBar";
import type { Glossary } from "../lib/api";
import { BANNED_AI_EXACT } from "../screens/legal/bannedAiPhrases";

const hitsAi = BANNED_AI_EXACT;

const lib = (id: string, name: string, content = ""): Glossary => ({
  id, name, language: null, content, updatedAt: "2026-06-23T00:00:00Z",
});
const LIBS = [lib("g1", "项目A", "A ｜ a"), lib("g2", "项目B", "B ｜ b")];

describe("GlossaryBar 选择器", () => {
  it("空库：显示引导条，点击打开完整管理", async () => {
    const onOpenFull = vi.fn();
    render(<GlossaryBar glossaries={[]} selectedId={null} onSelect={() => {}} onOpenFull={onOpenFull} />);
    await userEvent.click(screen.getByText(/Add a glossary/i));
    expect(onOpenFull).toHaveBeenCalled();
  });

  // 应用内英文界面同样在收款方审核视线内：说明文案讲用途、不讲实现（见 bannedAiPhrases.ts）
  it("英文说明文案不提实现，只说「拿哪本校正这份稿」", () => {
    render(<GlossaryBar glossaries={LIBS} selectedId="g1" onSelect={() => {}} onOpenFull={() => {}} />);
    expect(screen.getByText("Pick which glossary to correct this transcript against (or none)")).toBeTruthy();
    expect(document.body.textContent || "").not.toMatch(hitsAi);
  });

  it("有库且选中某本：显示该库名", () => {
    render(<GlossaryBar glossaries={LIBS} selectedId="g1" onSelect={() => {}} onOpenFull={() => {}} />);
    expect(screen.getByText(/项目A/)).toBeTruthy();
  });

  it("未选（不使用）：触发器显示「不使用」", () => {
    render(<GlossaryBar glossaries={LIBS} selectedId={null} onSelect={() => {}} onOpenFull={() => {}} />);
    expect(screen.getByText(/Don't use/i)).toBeTruthy();
  });

  it("展开下拉选另一本 → onSelect(该 id)", async () => {
    const onSelect = vi.fn();
    render(<GlossaryBar glossaries={LIBS} selectedId="g1" onSelect={onSelect} onOpenFull={() => {}} />);
    await userEvent.click(screen.getByRole("button"));       // 触发器（关闭态唯一 button）
    await userEvent.click(screen.getByText(/项目B/));
    expect(onSelect).toHaveBeenCalledWith("g2");
  });

  it("展开下拉选「不使用」→ onSelect(null)", async () => {
    const onSelect = vi.fn();
    render(<GlossaryBar glossaries={LIBS} selectedId="g1" onSelect={onSelect} onOpenFull={() => {}} />);
    await userEvent.click(screen.getByRole("button"));
    await userEvent.click(screen.getByText(/Don't use/i));
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});

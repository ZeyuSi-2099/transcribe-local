import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { UILangProvider } from "../../lib/i18n";
import { ErrorView } from "./ErrorView";

describe("ErrorView", () => {
  it("renders the declined variant action", () => {
    render(<UILangProvider><ErrorView kind="declined" onReset={vi.fn()} onLink={vi.fn()} /></UILangProvider>);
    expect(screen.getByText(/扣款失败|Payment declined/)).toBeInTheDocument();
  });
  it("falls back to failed for unknown kind", () => {
    render(<UILangProvider><ErrorView kind={"???" as never} onReset={vi.fn()} onLink={vi.fn()} /></UILangProvider>);
    expect(screen.getByText(/转录失败|Transcription failed/)).toBeInTheDocument();
  });
});

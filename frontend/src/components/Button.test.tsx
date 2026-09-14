import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "./Button";

describe("Button", () => {
  it("fires onClick when enabled", async () => {
    const fn = vi.fn();
    render(<Button primary onClick={fn}>Go</Button>);
    await userEvent.click(screen.getByText("Go"));
    expect(fn).toHaveBeenCalledOnce();
  });
  it("does not fire when disabled", async () => {
    const fn = vi.fn();
    render(<Button primary disabled onClick={fn}>Go</Button>);
    await userEvent.click(screen.getByText("Go"));
    expect(fn).not.toHaveBeenCalled();
  });
});

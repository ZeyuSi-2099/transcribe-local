import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Input } from "./Input";

describe("Input", () => {
  it("calls onChange with typed value", async () => {
    const fn = vi.fn();
    render(<Input value="" onChange={(e) => fn(e.target.value)} placeholder="x" />);
    await userEvent.type(screen.getByPlaceholderText("x"), "a");
    expect(fn).toHaveBeenCalledWith("a");
  });
});

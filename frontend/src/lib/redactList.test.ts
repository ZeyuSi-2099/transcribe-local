import { describe, it, expect } from "vitest";
import { countRedactLines } from "./redactList";

describe("countRedactLines", () => {
  it("一行一词，空行不计", () => {
    expect(countRedactLines("")).toBe(0);
    expect(countRedactLines("张三\n\n  \n李四公司\n")).toBe(2);
  });
});

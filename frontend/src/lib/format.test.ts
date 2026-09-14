import { describe, it, expect } from "vitest";
import { fmt, toSec, fmtClock } from "./format";

describe("format", () => {
  it("fmt pads to 2 digits", () => {
    expect(fmt(3)).toBe("03");
    expect(fmt(42)).toBe("42");
  });
  it("toSec parses HH:MM:SS", () => {
    expect(toSec("00:01:05")).toBe(65);
    expect(toSec("01:00:00")).toBe(3600);
  });
  it("fmtClock formats seconds, hours only when needed", () => {
    expect(fmtClock(65)).toBe("1:05");
    expect(fmtClock(3725)).toBe("1:02:05");
    expect(fmtClock(-5)).toBe("0:00");
  });
});

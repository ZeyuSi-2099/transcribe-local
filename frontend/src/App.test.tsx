import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

// 本机版（与线上不同）：没有宣传页、登录与语种页，打开就进应用。
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("App · 本机版", () => {
  it("打开就进应用：不经过登录，侧栏直接出现", async () => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      json: async () => {
        const u = String(url);
        if (u.includes("/me")) return { email: "local@transcribe.local", isAdmin: true };
        if (u.includes("/jobs")) return { jobs: [] };
        return {};
      },
    })));
    render(<App />);
    expect(await screen.findByText(/我的转录|My transcripts/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("you@example.com")).toBeNull();
  });

  it("模型不齐：先进下载页，不进应用", async () => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      json: async () => {
        const u = String(url);
        if (u.includes("/me")) return { email: "local@transcribe.local", isAdmin: true };
        if (u.includes("/local/models")) return { models: [{ id: "paraformer_2023", sizeMb: 234, installed: false }],
          ready: false, missingMb: 234, cacheDir: "/tmp/m",
          download: { running: false, current: null, doneBytes: 0, totalBytes: 0, error: null } };
        return {};
      },
    })));
    render(<App />);
    expect(await screen.findByText(/先下载识别模型|Download the speech models first/)).toBeInTheDocument();
    expect(screen.queryByText(/我的转录|My transcripts/)).toBeNull();
  });

  it("本机服务没起来：说清楚怎么办，不留白屏", async () => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<App />);
    expect(await screen.findByText(/连不上本机服务|Can't reach the local service/)).toBeInTheDocument();
  });
});

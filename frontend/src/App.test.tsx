import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("App", () => {
  it("未登录访问 / 看到宣传页（登录入口收进导航），不再直落登录卡", () => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({}) })));
    render(<App />);
    // 宣传页 hero 主标（slogan 冻结不改）
    expect(screen.getByText(/别再重听一整遍。|Stop re-listening to the whole tape\./)).toBeInTheDocument();
    // 登录入口在导航
    expect(screen.getByText(/^登录$|^Sign in$/)).toBeInTheDocument();
    // 不出现登录卡的邮箱输入框
    expect(screen.queryByPlaceholderText("you@example.com")).toBeNull();
  });

  it("/signin 走登录流程，登录成功后进入应用", async () => {
    localStorage.clear();
    window.history.replaceState(null, "", "/signin");
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      json: async () => {
        const u = String(url);
        if (u.includes("request-code")) return { ok: true, devCode: "123456" };
        if (u.includes("verify")) return { token: "tok-x", email: "a@b.com", balanceCents: 3000 };
        if (u.includes("/me")) return { email: "a@b.com", balanceCents: 3000 };
        if (u.includes("/jobs")) return { jobs: [] };
        return { ledger: [] };
      },
    })));
    render(<App />);
    expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument(); // 登录屏
    await userEvent.type(screen.getByPlaceholderText("you@example.com"), "a@b.com");
    await userEvent.click(screen.getByRole("button", { name: /用邮箱继续|Continue with email/ }));
    await userEvent.type(screen.getByLabelText(/6 位验证码|6-digit code/), "123456");
    await userEvent.click(screen.getByRole("button", { name: /继续|Continue/ }));
    // 登录后直接进上传页：侧栏出现「我的转录」导航即证明已进入 app
    expect(screen.getByText(/我的转录|My transcripts/)).toBeInTheDocument();
    // URL 归一化回 /
    expect(window.location.pathname).toBe("/");
  });

  it("/languages/es 渲染西语语种页（登录与否无关）", () => {
    localStorage.clear();
    window.history.replaceState(null, "", "/languages/es");
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({}) })));
    render(<App />);
    expect(screen.getByText(/西班牙语访谈转录，不用重听一整遍|Spanish interview transcripts you can actually trust/)).toBeInTheDocument();
  });
});

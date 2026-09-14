import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PostprocessPage } from "./PostprocessPage";
import { UILangProvider } from "../../lib/i18n";
import { BANNED_AI_EXACT } from "../legal/bannedAiPhrases";

const LISTS = [
  { id: "l1", name: "通用保留清单", content: "张三\n李四公司", updatedAt: "2026-07-18T00:00:00Z" },
  { id: "l2", name: "医疗项目", content: "协和", updatedAt: "2026-07-17T00:00:00Z" },
];

type Call = { url: string; method: string; body?: unknown };
let calls: Call[] = [];

const json = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });

function mockFetch(lists = LISTS) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url === "/api/postprocess/redact-lists" && method === "GET") return json({ lists });
    if (method === "POST") return json({ id: "new1" });
    if (method === "PUT" || method === "DELETE") return json({ ok: true });
    return json({ detail: "not found" }, 404);
  }));
}

const wrap = () => render(<UILangProvider><PostprocessPage /></UILangProvider>);

beforeEach(() => mockFetch());
afterEach(() => vi.unstubAllGlobals());

describe("PostprocessPage", () => {
  it("加载后：左栏清单列表 + 默认选中第一个 + 编辑器载入内容", async () => {
    wrap();
    expect(await screen.findByText("通用保留清单")).toBeInTheDocument();
    expect(screen.getByText("医疗项目")).toBeInTheDocument();
    expect(screen.getByText("2/20")).toBeInTheDocument();
    // 编辑器载入第一个清单内容；名称 Input 同步（选中收敛 → 缓冲重置是两级 effect，等它落定）
    await waitFor(() => {
      expect((screen.getByLabelText(/清单内容|List content/) as HTMLTextAreaElement).value).toContain("张三");
      expect((screen.getByLabelText(/清单名|List name/) as HTMLInputElement).value).toBe("通用保留清单");
    });
    // 2 行 → 2 条（编辑器头部计数 + 左栏列表项都按行数计）
    expect(screen.getAllByText(/2 条|2 entries/).length).toBeGreaterThan(0);
    // 格式提示只讲怎么写、不讲实现（去 AI 化红线，见 bannedAiPhrases.ts）
    expect(screen.getAllByText(/一行一个保留词|keep-word per line/).length).toBeGreaterThan(0);
    expect(document.body.textContent || "").not.toMatch(BANNED_AI_EXACT);
  });

  it("归类已下架：页面上不再有方案那一套（左栏组/镜像树/能力 chip）", async () => {
    wrap();
    await screen.findByText("通用保留清单");
    expect(screen.queryByText(/归类方案|Schemes/)).toBeNull();
    expect(screen.queryByText(/结构镜像|Structure/)).toBeNull();
    expect(screen.queryByLabelText(/方案内容|Scheme content/)).toBeNull();
    expect(screen.queryByText(/按指标出纪要|Minutes by your scheme/)).toBeNull();
  });

  it("合法编辑：保存可点，PUT 带 name + content", async () => {
    wrap();
    const ta = await screen.findByLabelText(/清单内容|List content/);
    fireEvent.change(ta, { target: { value: "张三\n李四公司\n王五" } });
    const save = screen.getByRole("button", { name: /保存清单|Save list/ });
    expect((save as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(save);
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/postprocess/redact-lists/l1");
    expect(put?.body).toEqual({ name: "通用保留清单", content: "张三\n李四公司\n王五" });
    // 保存后回到已保存态
    expect(await screen.findByText(/✓ 已保存|✓ Saved/)).toBeInTheDocument();
  });

  it("切换清单：编辑器与名称跟着换", async () => {
    wrap();
    await userEvent.click(await screen.findByText("医疗项目"));
    await waitFor(() => {
      expect((screen.getByLabelText(/清单名|List name/) as HTMLInputElement).value).toBe("医疗项目");
      expect((screen.getByLabelText(/清单内容|List content/) as HTMLTextAreaElement).value).toBe("协和");
    });
  });

  it("空态：一个清单都没有 → 居中引导卡 + 新建入口", async () => {
    mockFetch([]);
    wrap();
    expect(await screen.findByText(/笔录还能继续加工|go further/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/新建第一个保留词清单|first keep list/));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/postprocess/redact-lists");
  });

  it("+ 新建：POST 后重载并选中新项", async () => {
    wrap();
    await screen.findByText("通用保留清单");
    await userEvent.click(screen.getByRole("button", { name: /\+ 新建|\+ New/ }));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/postprocess/redact-lists");
    expect((post?.body as { name: string }).name).toMatch(/未命名|Untitled/);
  });

  it("删除是两步确认", async () => {
    wrap();
    await screen.findByText("通用保留清单");
    await userEvent.click(screen.getByRole("button", { name: /^删除$|^Delete$/ }));
    expect(calls.find((c) => c.method === "DELETE")).toBeUndefined(); // 第一步只是武装
    await userEvent.click(screen.getByRole("button", { name: /确认删除|Confirm delete/ }));
    expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/api/postprocess/redact-lists/l1");
  });
});

describe("PostprocessPage · 未保存的改动要登记给 AppShell 拦", () => {
  it("改了内容就登记，保存后撤销；登记的 save 真的会发 PUT", async () => {
    const { pendingUnsaved } = await import("../../lib/unsavedGuard");
    wrap();
    await screen.findByText("通用保留清单");
    expect(pendingUnsaved()).toBeNull();
    const ta = screen.getByLabelText(/清单内容|List content/);
    fireEvent.change(ta, { target: { value: "张三\n李四公司\n王五" } });
    await waitFor(() => expect(pendingUnsaved()).not.toBeNull());
    const ok = await pendingUnsaved()!.save();
    expect(ok).toBe(true);
    expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/l1"))).toBe(true);
    await waitFor(() => expect(pendingUnsaved()).toBeNull());
  });
});

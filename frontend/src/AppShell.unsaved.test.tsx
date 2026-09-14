import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "./lib/i18n";
import { AppShell } from "./AppShell";

// 只测 AppShell 这一层：有没保存的改动时切页要拦、三个出口各自的去向。
// 登记本身由 lib/unsavedGuard 与页面负责（见 PostprocessPage.test）。
let guard: { save: () => Promise<boolean> } | null = null;
vi.mock("./lib/unsavedGuard", () => ({ pendingUnsaved: () => guard, useUnsavedGuard: () => undefined, clearUnsaved: () => undefined }));

beforeEach(() => {
  guard = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true,
    json: async () =>
      String(url).includes("/me") ? { email: "a@b.com", balanceCents: 0 }
      : String(url).includes("/jobs") ? { jobs: [] }
      : { ledger: [] },
  })));
});

const wrap = () => render(<UILangProvider><AppShell me={{ email: "a@b.com", balanceCents: 0 }} /></UILangProvider>);
const nav = (name: RegExp) => userEvent.click(screen.getAllByText(name)[0]);
// 历史页在空态下不渲染 h1（只有空态屏），用文档标题判「到了哪一页」
const onHistory = () => waitFor(() => expect(document.title).toMatch(/我的转录|My transcripts/));
const stillOnUpload = () => expect(document.title).not.toMatch(/我的转录|My transcripts/);

describe("AppShell · 切页前拦「没保存的改动」", () => {
  it("没有未保存的改动时切页不拦", async () => {
    wrap();
    await nav(/^我的转录$|^My transcripts$/);
    expect(screen.queryByRole("dialog")).toBeNull();
    await onHistory();
  });

  it("有未保存的改动：切页弹层；「留下」不动；「直接离开」才切", async () => {
    guard = { save: vi.fn(async () => true) };
    wrap();
    await nav(/^我的转录$|^My transcripts$/);
    const dlg = await screen.findByRole("dialog");
    expect(dlg).toHaveTextContent(/还有没保存的改动|Unsaved changes/);
    await userEvent.click(screen.getByText(/^留下$|^Stay$/));
    expect(screen.queryByRole("dialog")).toBeNull();
    stillOnUpload();   // 还在上传页
    await nav(/^我的转录$|^My transcripts$/);
    await userEvent.click(screen.getByText(/^直接离开$|^Leave without saving$/));
    await onHistory();
    expect(guard.save).not.toHaveBeenCalled();
  });

  it("「保存并离开」：存成了才切；没存成留在原页", async () => {
    const save = vi.fn(async () => false);
    guard = { save };
    wrap();
    await nav(/^我的转录$|^My transcripts$/);
    await screen.findByRole("dialog");
    await userEvent.click(screen.getByText(/^保存并离开$|^Save and leave$/));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    stillOnUpload();   // 没存成，不走
    save.mockResolvedValue(true);
    await userEvent.click(screen.getByText(/^保存并离开$|^Save and leave$/));
    await onHistory();
  });
});

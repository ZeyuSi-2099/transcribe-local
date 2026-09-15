import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { ModelSetup } from "./ModelSetup";
import * as api from "../lib/api";

// 首次启动下载模型（本地独有页面）
const base: api.ModelsStatus = {
  models: [{ id: "paraformer_2023", sizeMb: 234, installed: false }, { id: "silero_vad", sizeMb: 2, installed: true }],
  ready: false, missingMb: 234, cacheDir: "/tmp/models",
  download: { running: false, current: null, doneBytes: 0, totalBytes: 0, error: null },
};
const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

afterEach(() => vi.restoreAllMocks());

describe("ModelSetup", () => {
  it("说清要下多少、下到哪；点开始后显示进度，下完自动进应用", async () => {
    const pull = vi.spyOn(api, "pullModels").mockResolvedValue({
      ...base, download: { running: true, current: "paraformer_2023", doneBytes: 117 << 20, totalBytes: 234 << 20, error: null },
    });
    vi.spyOn(api, "getModels").mockResolvedValue({ ...base, ready: true, missingMb: 0 });
    const onReady = vi.fn();
    wrap(<ModelSetup initial={base} onReady={onReady} />);
    expect(screen.getByText(/约 234 MB/)).toBeInTheDocument();
    expect(screen.getByText(/\/tmp\/models/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /开始下载|Start download/ }));
    expect(pull).toHaveBeenCalled();
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText(/正在下载 paraformer_2023/)).toBeInTheDocument();
    await waitFor(() => expect(onReady).toHaveBeenCalled(), { timeout: 3000 });
  });

  it("下载中断：把原因说出来，按钮变「重试」", () => {
    wrap(<ModelSetup initial={{ ...base, download: { ...base.download, error: "paraformer_2023：HTTP 503" } }} onReady={vi.fn()} />);
    expect(screen.getByRole("alert").textContent).toMatch(/HTTP 503/);
    expect(screen.getByRole("button", { name: /^重试$|^Retry$/ })).toBeInTheDocument();
  });
});

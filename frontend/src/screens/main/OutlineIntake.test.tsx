// 大纲进料区（2026-08-19）。
//
// 这里守的是「进料的三种方式都通，且出错时说人话」：拖、选、直接打字。
// `.docx` 那条路要过后端，测试里把那一跳 mock 掉——这个文件验的是我们自己的行为。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { OutlineIntake } from "./OutlineIntake";
import { MAX_OUTLINE_CHARS } from "../../lib/outlineFile";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof api>("../../lib/api");
  return { ...actual, readOutlineDocx: vi.fn() };
});
const docxMock = vi.mocked(api.readOutlineDocx);

function Harness({ onToastSpy, onBackSpy }: { onToastSpy?: (m: string) => void; onBackSpy?: () => void }) {
  const [v, setV] = useState("");
  return (
    <div>
      <OutlineIntake value={v} onChange={setV} onBack={() => onBackSpy?.()} onToast={(m) => onToastSpy?.(m)} />
    </div>
  );
}

// jsdom 没有 Blob.text()（浏览器里是标准 API，生产不用管）。用 FileReader 补上，
// 别为了测试环境把生产代码退回 FileReader 那套回调写法。
if (!Blob.prototype.text) {
  Blob.prototype.text = function (this: Blob) {
    return new Promise<string>((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result ?? ""));
      r.onerror = () => rej(r.error);
      r.readAsText(this);
    });
  };
}

const box = () => screen.getByLabelText("Interview outline") as HTMLTextAreaElement;
const fileInput = (c: HTMLElement) => c.querySelector('input[type="file"]') as HTMLInputElement;
const drop = () => screen.getByRole("button", { name: /Drop a file here/ });

beforeEach(() => vi.clearAllMocks());

describe("大纲进料", () => {
  it("选一个 .txt，正文进到可编辑的框里", async () => {
    const { container } = render(<Harness />);
    const f = new File(["一、渠道现状\n1. 尚界的分工"], "大纲.txt", { type: "text/plain" });
    fireEvent.change(fileInput(container), { target: { files: [f] } });
    await waitFor(() => expect(box().value).toContain("尚界的分工"));
    // 文件名留着——正文改过之后也得知道这堆字是哪来的
    expect(screen.getByText("大纲.txt")).toBeInTheDocument();
    expect(docxMock).not.toHaveBeenCalled();   // txt 不上传，前端自己读
  });

  it(".docx 走后端解析", async () => {
    docxMock.mockResolvedValue({ text: "从 Word 里读出来的大纲", chars: 11, truncated: false });
    const { container } = render(<Harness />);
    const f = new File([new Uint8Array([80, 75, 3, 4])], "大纲.docx", { type: "application/octet-stream" });
    fireEvent.change(fileInput(container), { target: { files: [f] } });
    await waitFor(() => expect(box().value).toBe("从 Word 里读出来的大纲"));
    expect(docxMock).toHaveBeenCalledOnce();
  });

  it("拖进来一份文件跟点选是同一条路", async () => {
    render(<Harness />);
    const f = new File(["拖进来的大纲"], "d.md", { type: "text/markdown" });
    fireEvent.drop(drop(), { dataTransfer: { files: [f] } });
    await waitFor(() => expect(box().value).toBe("拖进来的大纲"));
  });

  it("格式不对时说清楚认哪几种，而不是默默不动", async () => {
    const toasts: string[] = [];
    const { container } = render(<Harness onToastSpy={(m) => toasts.push(m)} />);
    const f = new File(["%PDF"], "大纲.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput(container), { target: { files: [f] } });
    await waitFor(() => expect(toasts.some((t) => t.includes(".docx"))).toBe(true));
    expect(box().value).toBe("");
  });

  it("超过 2MB 的文件挡在外面", async () => {
    const toasts: string[] = [];
    const { container } = render(<Harness onToastSpy={(m) => toasts.push(m)} />);
    const f = new File(["x"], "大.txt", { type: "text/plain" });
    Object.defineProperty(f, "size", { value: 3 * 1024 * 1024 });
    fireEvent.change(fileInput(container), { target: { files: [f] } });
    await waitFor(() => expect(toasts.some((t) => t.includes("2MB"))).toBe(true));
  });

  it("空文件给出理由", async () => {
    const toasts: string[] = [];
    const { container } = render(<Harness onToastSpy={(m) => toasts.push(m)} />);
    fireEvent.change(fileInput(container), { target: { files: [new File(["   "], "空.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(toasts.some((t) => t.includes("no readable text"))).toBe(true));
  });

  it("大纲过长时截断并明说截了——后端超限直接 422，不能等到点起草才报错", async () => {
    const toasts: string[] = [];
    const { container } = render(<Harness onToastSpy={(m) => toasts.push(m)} />);
    const f = new File(["词".repeat(MAX_OUTLINE_CHARS + 500)], "长.txt", { type: "text/plain" });
    fireEvent.change(fileInput(container), { target: { files: [f] } });
    await waitFor(() => expect(box().value.length).toBe(MAX_OUTLINE_CHARS));
    expect(toasts.some((t) => t.includes(String(MAX_OUTLINE_CHARS)))).toBe(true);
  });

  it("清空按钮把正文和文件名一起收走", async () => {
    const { container } = render(<Harness />);
    fireEvent.change(fileInput(container), { target: { files: [new File(["有内容"], "a.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(box().value).toBe("有内容"));
    await userEvent.click(screen.getByLabelText("Clear the outline"));
    expect(box().value).toBe("");
    expect(screen.queryByText("a.txt")).toBeNull();
  });

  it("回编辑器的路一直在——主区是借来的", async () => {
    const back = vi.fn();
    render(<Harness onBackSpy={back} />);
    await userEvent.click(screen.getByRole("button", { name: /Back to the editor/ }));
    expect(back).toHaveBeenCalledOnce();
  });
});

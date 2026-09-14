import { describe, it, expect, vi, afterEach } from "vitest";
import { downloadUrl } from "./download";

afterEach(() => vi.restoreAllMocks());

describe("downloadUrl", () => {
  it("clicks an in-DOM anchor pointing at the href, with the download filename", () => {
    let cap: { href: string; download: string | null; inDom: boolean } | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      cap = {
        href: this.href,
        download: this.getAttribute("download"),
        inDom: document.body.contains(this),
      };
    });

    downloadUrl("/api/jobs/1/export.docx?name=demo", "demo.docx");

    // 点击瞬间：链接已挂进 DOM，指向后端直链，并带显式文件名
    expect(cap!.inDom).toBe(true);
    expect(cap!.href).toContain("/api/jobs/1/export.docx?name=demo");
    expect(cap!.download).toBe("demo.docx");
  });

  it("does NOT remove the anchor synchronously (could interrupt the download)", () => {
    vi.useFakeTimers();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const remove = vi.spyOn(HTMLAnchorElement.prototype, "remove");

    downloadUrl("/api/x", "x.txt");

    expect(remove).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(remove).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

// 路由的两条底线（2026-08-05 SEO 审计 C3 之后新增）。
//
// 背景：vercel.json 去掉了「所有路径兜回 index.html」的重写，未知路径在边缘就是真 404；
// 客户端这一侧要跟上——**已知路径必须照常渲染，未知路径必须渲染 404 而不是首页**。
// 之前 `/nonexistent-xyz` 静默渲染成首页、还带着首页的 canonical。
//
// 这个文件最要紧的其实是第一条：判断「未知」的那行代码曾经把**首页**也算成未知
// （rawPath 削掉尾斜杠后首页是空串不是 "/"），一上线就是整站 404。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import App, { publicPageFor } from "./App";

const at = (url: string) => {
  localStorage.clear();
  localStorage.setItem("tx_lang", "en");   // 固定语言，免得首访引导把路径改了
  window.history.replaceState(null, "", url);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({}) })));
  return render(<App />);
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("路由", () => {
  for (const url of ["/", "/pricing", "/terms", "/privacy", "/case", "/languages/es", "/de/pricing", "/ja/terms"]) {
    it(`${url} 是真实页面，不许判成 404`, () => {
      const { container } = at(url);
      expect(container.textContent).not.toContain("404");
    });
  }

  // 报价器 2026-08-18 下架（见 CLAUDE.md）。生产上 /quote 由 vercel.json 转到后端回 410；
  // 前端这边它只要**不再是一个页面**即可——publicPageFor 认不出来，才会落到 404.html。
  it("/quote 与 /compare/* 已下架：路由认不出它们（生产上由后端回 410）", () => {
    expect(publicPageFor("/quote")).toBeNull();
    for (const slug of ["rev", "otter", "transcribeme", "happyscribe"]) {
      expect(publicPageFor(`/compare/${slug}`), `/compare/${slug} 不该还是页面`).toBeNull();
    }
  });

  it("拼错的路径渲染 404，而不是静默变成首页", () => {
    at("/nonexistent-xyz");
    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByText(/This page doesn't exist/)).toBeInTheDocument();
  });

  it("404 页必须 noindex —— 它可能挂在任意拼错的网址上", () => {
    at("/nonexistent-xyz");
    expect(document.head.querySelector('meta[name="robots"][content="noindex"]')).not.toBeNull();
  });

  it("404 页不许留下 canonical / hreflang：留着就是自认是某一个真实页面", () => {
    document.head.innerHTML = '<link rel="canonical" href="https://transcribe.solutions/">';
    at("/nonexistent-xyz");
    expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
  });
});

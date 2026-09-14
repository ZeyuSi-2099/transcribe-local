import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { UILangProvider } from "./i18n";
import { apiError, useErrText, type ApiError } from "./userErrors";

// 后端错误码的前端半边（2026-08-30 界面语言一致性第四批）。
// 对账守卫（server/tests/test_user_errors_parity.py）钉的是「码一致、文案齐」的静态一致性；
// 这里钉运行时行为——回归的症状正是界面上蹦出 [object Object] 或一句中文。

const mkRes = (status: number, body: unknown): Response =>
  ({ status, json: async () => body }) as unknown as Response;

describe("apiError：detail 的三种形态都要认", () => {
  it("① {code,msg,params}（现在的后端）→ 带上 code 与 params", async () => {
    const e = await apiError(
      mkRes(402, { detail: { code: "balance_low", msg: "余额不足，请充值", params: {} } }),
      "upload 402",
    );
    expect(e.code).toBe("balance_low");
    expect(e.message).toBe("余额不足，请充值");
    expect(e.status).toBe(402);
  });

  it("② 一句字符串（还没改造的路由）→ 原样透出，不糊成 [object Object]", async () => {
    const e = await apiError(mkRes(409, { detail: "name already exists" }), "glossary 409");
    expect(e.code).toBeUndefined();
    expect(e.message).toBe("name already exists");
    expect(e.message).not.toContain("object");
  });

  it("③ 没有 detail / 响应不是 JSON → 用调用方的 fallback", async () => {
    const noJson = { status: 500, json: async () => { throw new Error("not json"); } } as unknown as Response;
    const e = await apiError(noJson, "upload 500");
    expect(e.message).toBe("upload 500");
  });

  it("带参数的码收得下参数", async () => {
    const e = await apiError(
      mkRes(413, { detail: { code: "upload_too_large", msg: "文件过大", params: { n: 500 } } }),
      "upload 413",
    );
    expect(e.params?.n).toBe(500);
  });
});

// ── useErrText：错误 → 界面语言的一句话 ─────────────────────────────────

function Probe({ e }: { e: unknown }) {
  const errText = useErrText();
  return <span data-testid="t">{errText(e, "兜底句")}</span>;
}

function show(e: unknown, lang: string): string {
  localStorage.setItem("tx_lang", lang);
  render(<UILangProvider><Probe e={e} /></UILangProvider>);
  return screen.getByTestId("t").textContent ?? "";
}

const withCode = (code: string, msg: string, params: Record<string, string | number> = {}): ApiError =>
  Object.assign(new Error(msg), { code, params });

describe("useErrText：认得出的码按界面语言出文案", () => {
  beforeEach(() => localStorage.clear());

  it("中文界面 → 中文", () => {
    expect(show(withCode("glossary_name_taken", "raw"), "zh")).toBe("已经有一本同名的术语库了");
  });

  it("英文界面 → 英文（不是后端那句原文）", () => {
    expect(show(withCode("glossary_name_taken", "已经有一本同名的术语库了"), "en"))
      .toBe("You already have a glossary with that name");
  });

  it("带数字的码把参数插进当前语言的语序里", () => {
    const txt = show(withCode("upload_too_large", "raw", { n: 500 }), "en");
    expect(txt).toContain("500");
    expect(txt).toContain("MB");
  });

  it("认不出的码回落后端给的 msg，不回落成一句笼统的「出错了」", () => {
    // 前后端部署不同步时（Vercel 与 Render 各走各的），说清楚是什么错比语言正确更要紧
    expect(show(withCode("some_future_code", "后端的新话术"), "en")).toBe("后端的新话术");
  });

  it("普通 Error（没有码）→ 透出它的 message", () => {
    expect(show(new Error("verify 400"), "zh")).toBe("verify 400");
  });

  it("空错误 → 用兜底句，绝不渲染成空白或 [object Object]", () => {
    const txt = show(undefined, "zh");
    expect(txt).toBe("兜底句");
  });
});

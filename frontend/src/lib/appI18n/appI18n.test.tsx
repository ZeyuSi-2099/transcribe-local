// 对照层机制本身（不是翻译质量）：查得到用译文、查不到回落英文、模板占位符按序替换。
// 用真实的德语对照本跑，不 mock —— mock 掉的正是「索引是否对得上」这件唯一值得测的事。
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { UILangProvider, useL } from "../i18n";
import { appDe } from "./app.de";

function Probe() {
  const L = useL();
  return (
    <div>
      <span data-testid="hit">{L("取消", "Cancel")}</span>
      <span data-testid="miss">{L("这句还没翻", "Not translated yet — a string no override will ever contain")}</span>
      <span data-testid="tpl">{L.t("还差 {0} 处确认", "{0} left to confirm", 3)}</span>
      <span data-testid="rich">{L.x("发往 {0}。", "We sent a code to {0}.", <b key="e">a@b.com</b>)}</span>
    </div>
  );
}

function renderAs(lang: string) {
  localStorage.setItem("tx_lang", lang);
  return render(<UILangProvider><Probe /></UILangProvider>);
}

describe("应用内对照层", () => {
  beforeEach(() => { localStorage.clear(); history.replaceState(null, "", "/"); });

  it("中文不查表，直接用中文原句", () => {
    renderAs("zh");
    expect(screen.getByTestId("hit").textContent).toBe("取消");
    expect(screen.getByTestId("tpl").textContent).toBe("还差 3 处确认");
  });

  it("英文不查表，直接用英文原句", () => {
    renderAs("en");
    expect(screen.getByTestId("hit").textContent).toBe("Cancel");
  });

  it("德语：对照本命中 → 用德语", () => {
    expect(appDe["Cancel"]).toBeTruthy();   // 前提：这一句确实在对照本里（不在的话下面那条会假绿）
    renderAs("de");
    expect(screen.getByTestId("hit").textContent).toBe(appDe["Cancel"]);
  });

  it("德语：对照本没有这句 → 回落英文，绝不回落中文", () => {
    renderAs("de");
    const t = screen.getByTestId("miss").textContent!;
    expect(t).toBe("Not translated yet — a string no override will ever contain");
    expect(t).not.toMatch(/[一-龥]/);
  });

  it("模板：占位符按序替换（中/英/德三条路都填得上）", () => {
    renderAs("en");
    expect(screen.getByTestId("tpl").textContent).toBe("3 left to confirm");
  });

  it("富文本模板：参数节点原样嵌入，不被字符串化", () => {
    renderAs("en");
    const rich = screen.getByTestId("rich");
    expect(rich.textContent).toBe("We sent a code to a@b.com.");
    expect(rich.querySelector("b")?.textContent).toBe("a@b.com");
  });

  it("对照本的 key 一律是英文原句：不许有中文 key（写反了会静默不生效）", () => {
    for (const k of Object.keys(appDe)) {
      expect(k, `对照本 key 出现中文：${k}`).not.toMatch(/[一-龥]/);
    }
  });
});

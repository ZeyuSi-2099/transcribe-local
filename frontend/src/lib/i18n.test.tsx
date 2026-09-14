import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider, langHref, pathLangOf, stripLangPrefix, useL, useUILang } from "./i18n";

function Probe() {
  const L = useL();
  const { uiLang, setUiLang } = useUILang();
  return (
    <div>
      {/* 探针文案必须是**合成串**：拿 "English" 这类真实文案当样例，哪天它进了对照本（语种名「英语」）测试就假红 */}
      <span data-testid="word">{L("中文文案", "English copy")}</span>
      <span data-testid="lang">{uiLang}</span>
      <button onClick={() => setUiLang("en")}>to-en</button>
      <button onClick={() => setUiLang("zh")}>to-zh</button>
    </div>
  );
}
const wrap = () => render(<UILangProvider><Probe /></UILangProvider>);

// 模拟浏览器语言（覆盖 setup.ts 的全局默认）
function setBrowserLang(lang: string) {
  Object.defineProperty(navigator, "languages", { value: [lang], configurable: true });
  Object.defineProperty(navigator, "language", { value: lang, configurable: true });
}

describe("i18n 默认语言判定（英文优先 + 浏览器侦测）", () => {
  beforeEach(() => { localStorage.clear(); setBrowserLang("en-US"); }); // 基线：英文浏览器、无存储

  it("首次访问·英文浏览器 → 英文", () => {
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("English copy");
  });

  it("首次访问·中文浏览器 → 中文", () => {
    setBrowserLang("zh-CN");
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("中文文案");
  });

  it("回访·localStorage 已选英文 → 尊重选择（无视中文浏览器）", () => {
    setBrowserLang("zh-CN");
    localStorage.setItem("tx_lang", "en");
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("English copy");
  });

  it("回访·localStorage 已选中文 → 尊重选择（无视英文浏览器）", () => {
    setBrowserLang("en-US");
    localStorage.setItem("tx_lang", "zh");
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("中文文案");
  });

  it("非法存储值 → 落到浏览器语言判定（此处中文浏览器 → 中文）", () => {
    setBrowserLang("zh-CN");
    localStorage.setItem("tx_lang", "xx");   // 不在 8 门集合里（别用真语言码当样例：放量后会变成合法值）
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("中文文案");
  });

  it("取不到 navigator 语言 → 兜底英文", () => {
    setBrowserLang(""); // 空串：既不以 zh 开头，也无有效值
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("English copy");
  });

  it("手动切换语言会写入 localStorage 并切换文案", async () => {
    wrap(); // 英文浏览器 → 默认英文
    expect(screen.getByTestId("word").textContent).toBe("English copy");
    await userEvent.click(screen.getByText("to-zh"));
    expect(screen.getByTestId("word").textContent).toBe("中文文案");
    expect(localStorage.getItem("tx_lang")).toBe("zh");
    await userEvent.click(screen.getByText("to-en"));
    expect(localStorage.getItem("tx_lang")).toBe("en");
  });
});

describe("i18n 8 门（2026-08-02）：URL 语言前缀与回落", () => {
  beforeEach(() => {
    localStorage.clear();
    setBrowserLang("en-US");
    history.replaceState(null, "", "/");
  });

  it("langHref：en=根路径，其余加 /{lang} 前缀", () => {
    expect(langHref("/", "en")).toBe("/");
    expect(langHref("/pricing", "en")).toBe("/pricing");
    expect(langHref("/", "zh")).toBe("/zh");
    expect(langHref("/pricing", "de")).toBe("/de/pricing");
  });

  it("pathLangOf / stripLangPrefix：只认已放量语言；/languages/zh 不误判", () => {
    expect(pathLangOf("/zh/pricing")).toBe("zh");
    expect(stripLangPrefix("/zh/pricing")).toBe("/pricing");
    expect(stripLangPrefix("/zh")).toBe("/");
    expect(pathLangOf("/de/pricing")).toBe("de");           // M2 已放量
    expect(pathLangOf("/it/pricing")).toBe("it");           // M3 已放量——8 门全部启用
    expect(pathLangOf("/languages/zh")).toBeNull();        // 首段是 languages，不是语言前缀
    expect(pathLangOf("/pricing")).toBeNull();
  });

  it("URL 带 /zh 前缀 → 界面语言以 URL 为准（无视 localStorage 的 en）", () => {
    localStorage.setItem("tx_lang", "en");
    history.replaceState(null, "", "/zh/pricing");
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("中文文案");
  });

  it("URL 前缀带来的语言会被记住——否则登录后回到无前缀的 `/` 就丢了", () => {
    // 德语访客的路径是 /de → /de/signin → 登录后应用跳到 `/`。当次靠内存里的 uiLang 撑着，
    // 但下次直接打开域名就只剩 localStorage：不写就掉回英文，用户觉得「网站忘了我的语言」。
    localStorage.clear();
    history.replaceState(null, "", "/de/pricing");
    wrap();
    expect(localStorage.getItem("tx_lang")).toBe("de");
  });

  it("无语言前缀的路径不写存储——浏览器语言的探测结果不该冒充用户的选择", () => {
    localStorage.clear();
    setBrowserLang("de-DE");
    history.replaceState(null, "", "/pricing");
    wrap();
    expect(localStorage.getItem("tx_lang")).toBeNull();
  });

  it("L 的回落规则：对照本里没有的句子回落英文，绝不回落中文", () => {
    // 应用内多语言走 appI18n 对照本（以英文原句为索引），探针那句是合成串、必然查不到。
    // 这条守的是回落方向：查不到只能退英文——西语用户绝不该看到中文。
    localStorage.setItem("tx_lang", "es");
    setBrowserLang("es-ES");
    wrap();
    expect(screen.getByTestId("word").textContent).toBe("English copy");
  });
});

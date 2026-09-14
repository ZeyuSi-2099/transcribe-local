import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../../lib/i18n";
import { LangPicker } from "./LangPicker";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

// 界面语言默认中文 → 语言名显示规范中文译名（见 lib/langs.ts name[0]）
describe("LangPicker", () => {
  it("selects a common language", async () => {
    const fn = vi.fn();
    wrap(<LangPicker value="zh" onChange={fn} />);
    await userEvent.click(screen.getByText("英语"));
    expect(fn).toHaveBeenCalledWith("en");
  });
  it("更多下拉里的语言可点选", async () => {
    // 27 门时代：下拉里的语言全部可选，不再是「即将支持」占位
    const fn = vi.fn();
    wrap(<LangPicker value="zh" onChange={fn} />);
    await userEvent.click(screen.getByText(/更多|More/));
    await userEvent.click(await screen.findByText("日语"));
    expect(fn).toHaveBeenCalledWith("ja");
  });
  it("下拉不再出现「即将支持」占位", async () => {
    wrap(<LangPicker value="zh" onChange={() => {}} />);
    await userEvent.click(screen.getByText(/更多|More/));
    expect(screen.queryByText(/即将支持|Coming soon/)).toBeNull();
  });
  it("台面上的 11 门都是可点的格子（不再需要展开「更多」）", async () => {
    const fn = vi.fn();
    wrap(<LangPicker value="zh" onChange={fn} />);
    for (const [name, code] of [
      ["西班牙语", "es"], ["阿拉伯语", "ar"], ["法语", "fr"], ["葡萄牙语", "pt"],
      ["印尼语", "id"], ["俄语", "ru"], ["德语", "de"], ["日语", "ja"], ["意大利语", "it"],
    ] as const) {
      await userEvent.click(screen.getByText(name));
      expect(fn).toHaveBeenCalledWith(code);
    }
  });

  // 说明与标签同一行（格子多一行要 47px，卡片只剩 28px 余量——这句挪上来省的 22px 是唯一来源）。
  // 判据必须两半都在：只查「短句在」的话，旧版那句长的里面也含着这几个字，摘掉修复照样绿。
  it("说明缩短并挪上标签行，格子下面不再另起一行", () => {
    wrap(<LangPicker value="zh" onChange={() => {}} />);
    expect(screen.queryByText(/^选择音频的语言/)).toBeNull();
    // 判据是「标签的父节点里**只有**标签和说明两样」——说明若仍在格子下面另起一行，
    // 标签的父节点就是整个根节点，textContent 会连 11 门语种名一起装进来。
    const row = screen.getByText("音频语言").parentElement!;
    expect(row.textContent).toBe("音频语言我们不自动识别");
    expect(row.children.length).toBe(2);
    // 同一行里说明不许比标签大（写死 12 时它比标签的 11 还大，看着像标题）
    expect(screen.getByText("我们不自动识别").style.fontSize)
      .toBe(screen.getByText("音频语言").style.fontSize);
  });
});

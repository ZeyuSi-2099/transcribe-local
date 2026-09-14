import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LangToggle } from "./LangToggle";
import { LIVE_APP_LANGS } from "../lib/appI18n";

describe("LangToggle（地球语言菜单）", () => {
  it("展开菜单选另一语言：onChange 收到语言码，菜单收起", async () => {
    const fn = vi.fn();
    render(<LangToggle value="zh" onChange={fn} />);
    const trigger = screen.getByRole("button", { name: "Language" });
    expect(trigger.textContent).toContain("中文");            // 按钮显示当前语言名
    await userEvent.click(trigger);
    const items = screen.getAllByRole("menuitemradio");
    expect(items).toHaveLength(LIVE_APP_LANGS.length);        // 缺省=应用内已放量的语言
    expect(items.filter((i) => i.getAttribute("aria-checked") === "true")).toHaveLength(1);
    await userEvent.click(screen.getByRole("menuitemradio", { name: /English/ }));
    expect(fn).toHaveBeenCalledWith("en");
    expect(screen.queryByRole("menuitemradio")).toBeNull();   // 选完自动收起
  });

  // 2026-08-02：之前值不在清单里就按 "English" 显示。德语访客登录后看到「English ✓」，
  // 随手点一下就把存储里的 de 覆写成 en——退回营销页会发现网站「忘了」他选的语言。
  it("当前语言不在可选清单里：如实显示它自己，且不把任何一项标成当前", async () => {
    const fn = vi.fn();
    render(<LangToggle value="de" onChange={fn} langs={["en", "zh"]} />);
    const trigger = screen.getByRole("button", { name: "Language" });
    expect(trigger.textContent).toContain("Deutsch");
    expect(trigger.textContent).not.toContain("English");
    await userEvent.click(trigger);
    const items = screen.getAllByRole("menuitemradio");
    expect(items.map((i) => i.getAttribute("aria-checked"))).toEqual(["false", "false"]);
  });
});

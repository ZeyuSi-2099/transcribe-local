import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GlossaryPage } from "./GlossaryPage";
import { GLOSSARY_MAX_CHARS } from "../../lib/glossary";
import type { Glossary } from "../../lib/api";

const lib = (id: string, name: string, content: string): Glossary => ({
  id, name, language: null, content, updatedAt: "2026-06-23T00:00:00Z",
});

const TWO = [lib("g1", "项目A", "A ｜ a"), lib("g2", "项目B", "B ｜ b")];

function setup(glossaries: Glossary[] | null) {
  const onCreate = vi.fn().mockResolvedValue(lib("gn", "未命名", ""));
  const onUpdate = vi.fn().mockResolvedValue(undefined);
  const onDelete = vi.fn().mockResolvedValue(undefined);
  const r = render(
    <GlossaryPage glossaries={glossaries} onCreate={onCreate} onUpdate={onUpdate} onDelete={onDelete} />,
  );
  return { ...r, onCreate, onUpdate, onDelete };
}

describe("GlossaryPage 多术语库", () => {
  // 「还没取回来」不是「一本都没有」。混用的话，有三本库的人一进这一页会先看到
  // 「还没有术语库 · 新建第一本」——一句错话 + 一个会建出多余空库的按钮（2026-08-22）。
  it("列表没取回来时不摆空态屏，也不给「新建第一本」", () => {
    setup(null);
    expect(screen.queryByText("No glossary yet")).toBeNull();
    expect(screen.queryByRole("button", { name: /Create your first glossary/ })).toBeNull();
  });

  it("空库显示空状态 + 新建第一本入口，点击调用 onCreate", async () => {
    const { onCreate } = setup([]);
    expect(screen.getByText("No glossary yet")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Create your first glossary/ }));
    expect(onCreate).toHaveBeenCalled();
  });

  it("空态屏另有一条起草出口：建库 + 光标直接落到起草框", async () => {
    // 一本库都没有的人最需要帮忙，这一屏原来看不见「可以让我们帮你起草」。
    // 建完必须**接着**把光标送进起草框，否则用户面对的还是「建好了然后呢」。
    // 这里照真实链路搭：AppShell 的 onCreate 会先刷新列表再返回。
    const fresh = lib("gn", "未命名", "");
    function Harness() {
      const [list, setList] = useState<Glossary[]>([]);
      return (
        <GlossaryPage
          glossaries={list}
          onCreate={async () => { setList([fresh]); return fresh; }}
          onUpdate={async () => {}}
          onDelete={async () => {}}
        />
      );
    }
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: /paste an interview outline/i }));
    const outline = await screen.findByLabelText("Interview outline");
    await waitFor(() => expect(document.activeElement).toBe(outline));
  });

  it("有多本时左列列出所有库名", () => {
    setup(TWO);
    expect(screen.getByText("项目A")).toBeTruthy();
    expect(screen.getByText("项目B")).toBeTruthy();
  });

  it("默认选中第一本，编辑器显示其内容", () => {
    const { container } = setup(TWO);
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("A ｜ a");
  });

  it("点第二本 → 编辑器切到第二本内容", async () => {
    const { container } = setup(TWO);
    await userEvent.click(screen.getByText("项目B"));
    expect((container.querySelector("textarea") as HTMLTextAreaElement).value).toBe("B ｜ b");
  });

  it("点「+ 新建」调用 onCreate", async () => {
    const { onCreate } = setup(TWO);
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(onCreate).toHaveBeenCalled();
  });

  it("改内容点保存 → 以选中 id + 库名 + 新内容调用 onUpdate", async () => {
    const { container, onUpdate } = setup(TWO);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "A ｜ a\nC ｜ c" } });
    await userEvent.click(screen.getByRole("button", { name: "Save glossary" }));
    expect(onUpdate).toHaveBeenCalledWith("g1", "项目A", null, "A ｜ a\nC ｜ c");
  });

  it("改库名 → 保存把新名传给 onUpdate", async () => {
    const { onUpdate } = setup(TWO);
    const nameInput = screen.getByLabelText("Glossary name");
    fireEvent.change(nameInput, { target: { value: "项目A2" } });
    await userEvent.click(screen.getByRole("button", { name: "Save glossary" }));
    expect(onUpdate).toHaveBeenCalledWith("g1", "项目A2", null, "A ｜ a");
  });

  it("删除走两步确认 → 确认后以选中 id 调用 onDelete", async () => {
    const { onDelete } = setup(TWO);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(onDelete).toHaveBeenCalledWith("g1");
  });

  it("未修改时保存按钮为「已保存」且禁用", () => {
    setup(TWO);
    const btn = screen.getByRole("button", { name: "Saved" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("超总字数上限时保存禁用、文案变「超出字数上限」", () => {
    const { container } = setup(TWO);
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "x".repeat(GLOSSARY_MAX_CHARS + 1) } });
    const btn = screen.getByRole("button", { name: "Over the limit" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("显示总字数 X / 上限", () => {
    setup(TWO);
    expect(screen.getByText(new RegExp(`/\\s*${GLOSSARY_MAX_CHARS}`))).toBeTruthy();
  });

  it("无重名时新建用基础名 Untitled", async () => {
    const { onCreate } = setup(TWO);
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(onCreate).toHaveBeenCalledWith("Untitled");
  });

  it("已有「Untitled」时新建自动避重名（Untitled → 2 → 3），不再触发 409", async () => {
    const dup = [lib("g1", "Untitled", "x"), lib("g2", "Untitled 2", "y")];
    const { onCreate } = setup(dup);
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(onCreate).toHaveBeenCalledWith("Untitled 3");
  });
});

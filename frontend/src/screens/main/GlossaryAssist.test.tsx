// 协助建库的状态机测试。模型那一跳被 mock 掉——这里验的是我们自己的行为：
// 起草是不是真把条目写进了编辑器、停止后已写的留不留、体检的三框队列会不会
// 因为用户手改而失效、采纳能不能撤销。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { GlossaryAssist } from "./GlossaryAssist";
import { OutlineIntake } from "./OutlineIntake";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof api>("../../lib/api");
  return { ...actual, draftGlossaryStream: vi.fn(), checkGlossaryStream: vi.fn() };
});

const draftMock = vi.mocked(api.draftGlossaryStream);
const checkMock = vi.mocked(api.checkGlossaryStream);

/** 把一组条目做成一条流。gapMs>0 时条目之间留空档，用来测「流还没完」的那些状态 */
function streamOf(items: api.DraftItem[], gapMs = 0) {
  return async (_outline: string, onItem: (it: api.DraftItem) => void, signal?: AbortSignal) => {
    for (const it of items) {
      if (signal?.aborted) return;
      onItem(it);
      if (gapMs) await new Promise((r) => setTimeout(r, gapMs));
    }
  };
}

/** 用一个真的受控容器包起来——setText 的函数式更新必须在真实 state 上验证。
 *  大纲进料区（OutlineIntake）装的是真组件而不是替身：它在生产里就长在主区，
 *  而起草按钮长在右栏，两边靠 GlossaryPage 的一份 outline 串起来——这里照搬那个接线，
 *  否则测的就不是真实结构。 */
function Harness({ initial = "", onToastSpy }: { initial?: string; onToastSpy?: (m: string) => void }) {
  const [text, setText] = useState(initial);
  const [outline, setOutline] = useState("");
  const [intake, setIntake] = useState(true);
  return (
    <div>
      <textarea data-testid="editor" value={text} onChange={(e) => setText(e.target.value)} />
      {intake && (
        <OutlineIntake
          value={outline}
          onChange={setOutline}
          onBack={() => setIntake(false)}
          onToast={(m) => onToastSpy?.(m)}
        />
      )}
      <GlossaryAssist
        text={text}
        setText={setText}
        onToast={(m) => onToastSpy?.(m)}
        writeIntervalMs={5}
        outline={outline}
        intakeOpen={intake}
        onOpenIntake={() => setIntake(true)}
        onCloseIntake={() => setIntake(false)}
      />
    </div>
  );
}

const editor = () => screen.getByTestId("editor") as HTMLTextAreaElement;

beforeEach(() => {
  vi.clearAllMocks();
  // 无 UILangProvider 时 useL 走英文（与 GlossaryPage.test 的既有约定一致）
});
afterEach(() => localStorage.clear());

describe("协助建库 · 起草", () => {
  it("空大纲不发请求，提示先贴一份", async () => {
    const toasts: string[] = [];
    render(<Harness onToastSpy={(m) => toasts.push(m)} />);
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    expect(draftMock).not.toHaveBeenCalled();
    expect(toasts.some((t) => t.includes("Paste an interview outline first"))).toBe(true);
  });

  it("贴大纲 → 条目逐条写进编辑器，分类行带 ## ，条目行用全角竖线", async () => {
    draftMock.mockImplementation(streamOf([
      { cat: "渠道角色" },
      { term: "FD", def: "厂商省级直控的履约分销平台" },
      { term: "KA", def: "重点客户" },
    ]));
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲内容");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));

    // 后端返回后按节奏逐条写入
    await waitFor(() => expect(editor().value).toContain("KA ｜ 重点客户"));
    const v = editor().value;
    expect(v).toContain("## 渠道角色");
    expect(v).toContain("FD ｜ 厂商省级直控的履约分销平台");
    // 三条各占一行，没有互相覆盖（函数式更新的意义就在这）
    expect(v.split("\n").filter(Boolean).length).toBe(3);
  });

  it("材料里没有可收的词 → 给出可操作的说明，不是静默无事发生", async () => {
    draftMock.mockImplementation(streamOf([]));
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "今天天气不错");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    expect(await screen.findByText(/Nothing here needs/)).toBeInTheDocument();
    expect(editor().value).toBe("");
  });

  it("引擎不可用 → 显示后端给的原因，编辑器不动", async () => {
    draftMock.mockRejectedValue(new Error("起草服务暂时不可用：key 没配"));
    render(<Harness initial="原有内容" />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    expect(await screen.findByText(/暂时不可用/)).toBeInTheDocument();
    expect(editor().value).toBe("原有内容");
  });

  it("流式：第一条到了就落进编辑器，不等整份跑完", async () => {
    // 后两条隔很久才来——如果实现是「攒齐再写」，这个断言就会超时
    draftMock.mockImplementation(async (_o, onItem) => {
      onItem({ term: "首条", def: "先到先写" });
      await new Promise((r) => setTimeout(r, 3000));
      onItem({ term: "末条", def: "很久以后" });
    });
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    await waitFor(() => expect(editor().value).toContain("首条 ｜ 先到先写"), { timeout: 1500 });
    // 此时流还开着：进度不该报「共 N 条」（总数还不知道），也不该判定成已结束
    expect(screen.getByRole("button", { name: /Stop/ })).toBeInTheDocument();
  });

  it("停止会掐断请求本身，不只是停止写入", async () => {
    let seen: AbortSignal | undefined;
    draftMock.mockImplementation(async (_o, onItem, signal) => {
      seen = signal;
      onItem({ term: "A", def: "甲" });
      await new Promise((r) => setTimeout(r, 3000));
    });
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    await waitFor(() => expect(editor().value).toContain("A ｜ 甲"));
    await userEvent.click(screen.getByRole("button", { name: /Stop/ }));
    expect(seen?.aborted).toBe(true);
  });

  it("中途停止：已经写进去的留着，不回滚", async () => {
    // 条目之间留 60ms 空档 —— 停止要在「流还开着」的时候按下才算数
    draftMock.mockImplementation(streamOf([
      { term: "A", def: "甲" }, { term: "B", def: "乙" }, { term: "C", def: "丙" },
      { term: "D", def: "丁" }, { term: "E", def: "戊" },
    ], 60));
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    await waitFor(() => expect(editor().value).toContain("A ｜ 甲"));
    await userEvent.click(screen.getByRole("button", { name: /Stop/ }));
    const kept = editor().value;
    expect(kept).not.toContain("E ｜ 戊");
    // 停止后不再继续写（act 包住：流的收尾还会碰一次 state，不包会刷 act 警告）
    await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
    expect(editor().value).toBe(kept);
  });
});

describe("协助建库 · 体检三框", () => {
  const LIB = "## 渠道角色\nFD ｜ 短\n手机 ｜ 通信设备";

  it("三框固定顺序出现；采纳「该补」把条目插进目标分类", async () => {
    checkMock.mockResolvedValue({
      add: [{ term: "窜货", def: "货品流向非授权区域销售", cat: "渠道角色" }],
      edit: [], del: [],
    });
    render(<Harness initial={LIB} />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    expect(await screen.findByText("To add")).toBeInTheDocument();
    expect(screen.getByText("To rewrite")).toBeInTheDocument();
    expect(screen.getByText("To remove")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    const v = editor().value;
    expect(v).toContain("窜货 ｜ 货品流向非授权区域销售");
    // 插在该分类段内，不是甩到文件末尾
    expect(v.indexOf("窜货")).toBeGreaterThan(v.indexOf("## 渠道角色"));
  });

  it("采纳「该删」移除该行，并且能撤销回原样", async () => {
    checkMock.mockResolvedValue({
      add: [], edit: [], del: [{ term: "手机", why: "常规词，不会被听错" }],
    });
    let lastUndo: (() => void) | undefined;
    function Wrap() {
      const [text, setText] = useState(LIB);
      return (
        <div>
          <textarea data-testid="editor" value={text} onChange={(e) => setText(e.target.value)} />
          <GlossaryAssist text={text} setText={setText} onToast={(_m, undo) => { lastUndo = undo; }} writeIntervalMs={5}
            outline="" intakeOpen={false} onOpenIntake={() => {}} onCloseIntake={() => {}} />
        </div>
      );
    }
    render(<Wrap />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    await screen.findByText("To remove");
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(editor().value).not.toContain("手机 ｜");

    await act(async () => { lastUndo?.(); });
    expect(editor().value).toContain("手机 ｜ 通信设备");
  });

  it("建议队列按当前库实时重算：用户自己删掉的词，「该删」里那条自动消失", async () => {
    checkMock.mockResolvedValue({
      add: [], edit: [],
      del: [{ term: "手机", why: "常规词" }, { term: "电脑", why: "常规词" }],
    });
    render(<Harness initial={"手机 ｜ 通信设备\n电脑 ｜ 计算设备"} />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    await screen.findByText("To remove");
    expect(screen.getByText("1 / 2")).toBeInTheDocument();

    // 用户自己在编辑器里删掉「手机」那行 → 队列应当只剩 1 条
    await userEvent.clear(editor());
    await userEvent.type(editor(), "电脑 ｜ 计算设备");
    await waitFor(() => expect(screen.queryByText("1 / 2")).toBeNull());
  });

  it("某类为空时框仍在，显示「都过完了」", async () => {
    checkMock.mockResolvedValue({ add: [], edit: [], del: [{ term: "手机", why: "常规词" }] });
    render(<Harness initial={LIB} />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    await screen.findByText("To add");
    expect(screen.getAllByText(/All done/).length).toBe(2);   // add 与 edit 两框
  });

  it("忽略只把这条移出队列，不改编辑器", async () => {
    checkMock.mockResolvedValue({ add: [], edit: [], del: [{ term: "手机", why: "常规词" }] });
    render(<Harness initial={LIB} />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    await screen.findByText("To remove");
    await userEvent.click(screen.getByRole("button", { name: "Ignore" }));
    expect(editor().value).toBe(LIB);
    await waitFor(() => expect(screen.getAllByText(/All done/).length).toBe(3));
  });

  it("网关类错误翻成人话，不把「502」甩给用户", async () => {
    checkMock.mockRejectedValue(new Error("glossary check 502"));
    render(<Harness initial={LIB} />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    expect(await screen.findByText(/did not go through/)).toBeInTheDocument();
    expect(screen.queryByText(/502/)).toBeNull();
    expect(editor().value).toBe(LIB);          // 失败不动库
  });

  it("检查期间给出活动信号，不是一个卡住的灰按钮", async () => {
    checkMock.mockImplementation(() => new Promise(() => {}));   // 永不 resolve
    render(<Harness initial={LIB} />);
    await userEvent.click(screen.getByRole("button", { name: "Check this glossary" }));
    expect(await screen.findByText("Checking")).toBeInTheDocument();
    expect(screen.getByText(/usually a minute or two/)).toBeInTheDocument();
  });

  it("空库不显示体检入口（没内容可检查）", () => {
    render(<Harness initial="" />);
    expect(screen.queryByRole("button", { name: "Check this glossary" })).toBeNull();
  });
});

// ── 已有库上再起草（2026-08-19）─────────────────────────────────────────
// 写入是**追加**式的（不覆盖，已有内容不会丢），但模型只看大纲、不知道库里已经收过什么。
// 拿同一份大纲再跑一次，它会把「尚界」原样再产出一遍——不去重就是原地堆重复。
describe("协助建库 · 在已有的库上补充", () => {
  it("库里已有的词跳过不写，并说清楚跳了几条", async () => {
    draftMock.mockImplementation(streamOf([
      { term: "尚界", def: "鸿蒙智行旗下的整车品牌" },   // 库里已有
      { term: "倒挂", def: "终端成交价低于进货价" },     // 新的
    ]));
    const toasts: string[] = [];
    render(<Harness initial={"## 渠道\n尚界 ｜ 我自己写过的释义"} onToastSpy={(m) => toasts.push(m)} />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲");
    await userEvent.click(screen.getByRole("button", { name: "Draft the additions" }));

    await waitFor(() => expect(editor().value).toContain("倒挂"));
    // 只出现一次，且留下的是用户自己写的那条释义——不是被模型的版本覆盖
    expect(editor().value.match(/尚界/g)).toHaveLength(1);
    expect(editor().value).toContain("我自己写过的释义");
    await waitFor(() => expect(toasts.some((t) => /1 already in your glossary were skipped/.test(t))).toBe(true));
  });

  it("同一轮里模型自己重复产出的词也只写一条", async () => {
    draftMock.mockImplementation(streamOf([
      { term: "头水货", def: "首批到店的车源" },
      { term: "头水货", def: "首批到店、未经流转的车源" },
    ]));
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Interview outline"), "大纲");
    await userEvent.click(screen.getByRole("button", { name: "Start drafting" }));
    await waitFor(() => expect(editor().value).toContain("头水货"));
    expect(editor().value.match(/头水货/g)).toHaveLength(1);
  });

  it("空库说「帮我建一本」，已有内容说「再补一批」——不能都说成「建」", () => {
    const { unmount } = render(<Harness />);
    expect(screen.getByText("Draft one for me")).toBeInTheDocument();
    unmount();
    render(<Harness initial={"尚界 ｜ 鸿蒙智行旗下的整车品牌"} />);
    expect(screen.getByText("Add another batch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Draft the additions" })).toBeInTheDocument();
  });
});

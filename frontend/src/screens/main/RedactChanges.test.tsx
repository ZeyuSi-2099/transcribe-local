import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../../lib/i18n";
import { RedactChanges } from "./RedactChanges";
import { RedactScope, RedactScopeHelp } from "./RedactScope";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof api>()),
  putPpOverrides: vi.fn(),
}));
const putPpOverrides = vi.mocked(api.putPpOverrides);

/** 造一处：句子由「前缀 + 原词/替换词 + 后缀」拼出，高亮区间跟着算，与后端同构。 */
const spot = (line: number, idx: number, seg: number | null, frm: string, to: string,
              pre = "被访者：我们跟", post = "合作过。"): api.RedactSpot => ({
  line, idx, seg,
  before: { text: pre + frm + post, s: pre.length, e: pre.length + frm.length },
  after: { text: pre + to + post, s: pre.length, e: pre.length + to.length },
  prev: `第 ${line} 行的上一句`,
  next: `第 ${line} 行的下一句`,
});

const CHANGES: api.RedactChange[] = [
  { kind: "person", from: "刘俊峰", to: "XX", count: 3,
    spots: [spot(2, 0, 1, "刘俊峰", "XX"), spot(6, 0, 3, "刘俊峰", "XX"), spot(8, 0, 4, "刘俊峰", "XX")] },
  { kind: "company", from: "明辉电器", to: "XX公司", count: 3,
    spots: [spot(0, 0, 0, "明辉电器", "XX公司"), spot(4, 0, 2, "明辉电器", "XX公司"), spot(10, 0, 5, "明辉电器", "XX公司")] },
  { kind: "geo", from: "江苏", to: "本省", count: 2,
    spots: [spot(2, 1, 1, "江苏", "本省"), spot(12, 0, 6, "江苏", "本省")] },
  { kind: "number", from: "十二", to: "十几", count: 1, spots: [spot(2, 2, 1, "十二", "十几")] },
];
const ok = (changes = CHANGES, overrides: api.RedactOverride[] = []) => ({ changes, overrides });

describe("脱敏改动清单", () => {
  beforeEach(() => { putPpOverrides.mockReset(); });

  // 措辞是「脱敏了 N 处」不是「改了 N 处」：说清是哪一类改动，用户才敢用（2026-08-22 Duner）
  it("默认收起，只报总处数（3+3+2+1=9）", async () => {
    render(<UILangProvider><RedactChanges data={ok()} jobId="j1" /></UILangProvider>);
    await screen.findByText(/脱敏了 9 处|9 spots redacted/);
    expect(screen.queryByText("明辉电器", { exact: false })).toBeNull();
  });

  it("展开后逐条列出「原词 → 脱敏后」并按类分组", async () => {
    render(<UILangProvider><RedactChanges data={ok()} jobId="j1" /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    expect(screen.getByText(/明辉电器/)).toBeInTheDocument();
    for (const g of [/公司名|Company/, /人名|People/, /地点|Places/, /数字与规模|Figures/]) {
      expect(screen.getByText(g)).toBeInTheDocument();
    }
  });

  // 上下文随清单一起给 ⇒ 「改得对不对」不依赖能不能跳回左侧正文。
  // 在此之前，脱敏跑在视角转换之后时那一列全是点不动的「这一处」（2026-08-22 生产实见）。
  it("展开一条 → 每一处都带上一句/这一句/下一句，改动处就地划出前后", async () => {
    render(<UILangProvider><RedactChanges data={ok()} jobId="j1" /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    await userEvent.click(screen.getByText(/明辉电器/));
    expect(screen.getByText("第 0 行的上一句")).toBeInTheDocument();
    expect(screen.getByText("第 0 行的下一句")).toBeInTheDocument();
    // 3 处各来一遍上下文（而不是只给第一处）
    expect(screen.getByText("第 4 行的上一句")).toBeInTheDocument();
    expect(screen.getByText("第 10 行的上一句")).toBeInTheDocument();
    // 划掉的是原词、留下的是脱敏后 —— 一眼看出这一处现在是什么样
    expect(screen.getAllByText("明辉电器").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText(/XX公司/).length).toBeGreaterThanOrEqual(3);
  });

  it("有 seg 时给一个回正文的入口；没有就只是不给，不解释", async () => {
    const noSeg = [{ ...CHANGES[1], spots: CHANGES[1].spots.map((s) => ({ ...s, seg: null })) }];
    const onLocate = vi.fn();
    const { unmount } = render(<UILangProvider><RedactChanges data={ok(noSeg)} jobId="j1" onLocate={onLocate} /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    await userEvent.click(screen.getByText(/明辉电器/));
    expect(screen.queryByText("↗")).toBeNull();
    // 「这一稿是在叙述稿基础上脱敏」这类解释一律不出现——用户不关心前一步跑了什么
    expect(screen.queryByText(/叙述稿|narrative version/)).toBeNull();
    expect(screen.getAllByText(/保留原词|Keep original/).length).toBe(3);   // 改口不受影响
    unmount();

    render(<UILangProvider><RedactChanges data={ok([CHANGES[1]])} jobId="j1" onLocate={onLocate} /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    await userEvent.click(screen.getByText(/明辉电器/));
    await userEvent.click(screen.getAllByText("↗")[1]);
    expect(onLocate).toHaveBeenCalledWith(2);
  });

  // 「不能把多个脱敏项合并到一起审核」（2026-08-22 Duner）：三处「江苏 → 本省」是三处
  it("保留原词只作用于点的那一处，别的处不动", async () => {
    putPpOverrides.mockResolvedValue(undefined);
    render(<UILangProvider><RedactChanges data={ok()} jobId="j1" /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    await userEvent.click(screen.getByText(/明辉电器/));
    await userEvent.click(screen.getAllByText(/保留原词|Keep original/)[1]);   // 第 2 处
    await waitFor(() => expect(putPpOverrides).toHaveBeenCalled());
    expect(putPpOverrides.mock.calls[0][1]).toEqual([
      { line: 4, idx: 0, from: "明辉电器", to: "XX公司", value: "明辉电器" },
    ]);
    expect(screen.getByText(/已保留原词|Original kept/)).toBeInTheDocument();
  });

  it("改成别的写法：先点「改成…」，输入有效新词才浮现执行键", async () => {
    putPpOverrides.mockResolvedValue(undefined);
    render(<UILangProvider><RedactChanges data={ok([CHANGES[2]])} jobId="j1" /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    await userEvent.click(screen.getByText(/江苏/));
    await userEvent.click(screen.getAllByText(/改成…|Change to…/)[0]);
    expect(screen.queryByText(/改 ↵|Set ↵/)).toBeNull();      // 空输入不给执行键
    await userEvent.type(screen.getByPlaceholderText(/改成|Change to/), "华东");
    await userEvent.click(screen.getByText(/改 ↵|Set ↵/));
    await waitFor(() => expect(putPpOverrides).toHaveBeenCalled());
    expect(putPpOverrides.mock.calls[0][1]).toEqual([
      { line: 2, idx: 1, from: "江苏", to: "本省", value: "华东" },
    ]);
  });

  it("改口可撤销，撤销后提交空表", async () => {
    putPpOverrides.mockResolvedValue(undefined);
    const data = ok([CHANGES[2]], [{ line: 2, idx: 1, from: "江苏", to: "本省", value: "江苏" }]);
    render(<UILangProvider><RedactChanges data={data} jobId="j1" /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    // 已保存的改口在重新打开后仍然在场（存的是后端，不是这次会话）
    expect(screen.getByText(/已改口 1|1 overridden/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/江苏/));
    await userEvent.click(screen.getByText(/撤销|Undo/));
    await waitFor(() => expect(putPpOverrides).toHaveBeenCalledWith("j1", []));
  });

  // 老改口（加 idx 之前存的）不许无声消失：摊成逐处形态后照常显示、照常可撤销
  it("没有 idx 的老改口摊到该行的每一处", async () => {
    const data = ok([CHANGES[2]], [{ line: 2, from: "江苏", to: "本省", value: "江苏" }]);
    render(<UILangProvider><RedactChanges data={data} jobId="j1" /></UILangProvider>);
    await userEvent.click(await screen.findByRole("button"));
    expect(screen.getByText(/已改口 1|1 overridden/)).toBeInTheDocument();
  });

  // 后端一切异常都吞成空清单（取数在 PostprocessCard，失败也落成空清单）——
  // 那时这一块必须整个消失，不能留个「脱敏了 0 处」误导用户
  it("空清单就整块不显示", () => {
    const { container } = render(<UILangProvider><RedactChanges data={ok([])} jobId="j1" /></UILangProvider>);
    expect(container.textContent).toBe("");
  });
});

describe("脱敏范围说明", () => {
  it("六类都在，且每类都带一个例子", () => {
    render(<UILangProvider><RedactScope /></UILangProvider>);
    for (const t of [/公司名|company/i, /人名|People's names/, /电话|Phone/, /省市|city or region/,
                     /团队人数|Team size/, /项目名|project names/]) {
      expect(screen.getByText(t)).toBeInTheDocument();
    }
    // 例子一律是「原文 → 脱敏后」的形态，否则说明只讲了规则没给样子
    expect(screen.getAllByText(/→/).length).toBeGreaterThanOrEqual(6);
  });

  // 实测里用户把自己公司写进清单照样被脱敏（身份保护优先）。不写清楚会被当成 bug
  it("讲清保留清单的唯一例外", () => {
    render(<UILangProvider><RedactScope /></UILangProvider>);
    expect(screen.getByText(/唯一的例外|one exception/)).toBeInTheDocument();
  });

  // 2026-08-22 起是 ? 浮窗而不是折叠条：这块是决定要不要按下「开始加工」的依据，
  // 不该缩在一行小字后面。关闭三途（Esc / 遮罩 / ✕）同设计纪律 4。
  it("? 浮窗：默认不在场，点开才有，Esc 可关", async () => {
    render(<UILangProvider><RedactScopeHelp /></UILangProvider>);
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /脱敏会改什么/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/人名|People's names/)).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UILangProvider } from "../lib/i18n";
import { HistoryPage } from "./HistoryPage";
import { HISTORY_ITEMS } from "../lib/sampleData";

const wrap = (ui: React.ReactNode) => render(<UILangProvider>{ui}</UILangProvider>);

describe("HistoryPage", () => {
  it("shows empty state when empty", () => {
    wrap(<HistoryPage empty onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/这里还很安静|It's quiet here/)).toBeInTheDocument();
  });
  // 2026-09-01：这一屏上有两句「你什么都没有」的断言，而 items 的初值与「确实没有」
  // 长得一模一样。加载完之前说这句话，半秒后就要自我推翻——同上传页横跳的成因。
  it("还没拉回来时，两句断言一句都不许说", () => {
    wrap(<HistoryPage loaded={false} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.queryByText(/这里还很安静|It's quiet here/)).toBeNull();
    expect(screen.queryByText(/暂时没有转录|Nothing under/)).toBeNull();
    // 但骨架要在（页头 + tab）——整块消失同样是跳
    expect(screen.getByRole("heading", { name: /我的转录|My transcripts/ })).toBeInTheDocument();
  });
  it("拉回来确实是空的，那就照说不误", () => {
    wrap(<HistoryPage onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/暂时没有转录|Nothing under/)).toBeInTheDocument();
  });
  it("filters to failed rows", async () => {
    wrap(<HistoryPage items={HISTORY_ITEMS} onNew={vi.fn()} onOpen={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /失败 1|Failed 1/ }));
    // 状态胶囊只说状态，「未计费」在计费列——2026-08-19 拆开的，合在一起时德语/意大利语/
    // 葡语的胶囊装不下 132px 的状态列，会裂成两行。两半都得在，缺一半就是信息丢了。
    expect(screen.getByText(/^失败$|^Failed$/, { selector: "span" })).toBeInTheDocument();
    // 本机版：没有计费列，失败行也不说「未计费」
    expect(screen.queryByText(/未计费|No charge/)).toBeNull();
  });
  // 本机版：处理中的行可以取消，点一次变「确认取消」，再点才真取消
  it("处理中的行可以取消（两步确认）", async () => {
    const onCancel = vi.fn();
    wrap(<HistoryPage items={HISTORY_ITEMS} onNew={vi.fn()} onOpen={vi.fn()} onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: /^取消$|^Cancel$/ }));
    expect(onCancel).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /确认取消|Confirm cancel/ }));
    expect(onCancel).toHaveBeenCalledWith(expect.objectContaining({ st: "processing" }));
    // 已完成、失败的行没有取消按钮：样本里只有一条处理中
    expect(screen.queryAllByRole("button", { name: /^取消$|^Cancel$|确认取消/ }).length).toBeLessThanOrEqual(1);
  });
  // 本机版：取消的单库里记成 failed，状态列说「已取消」（中性灰），不说「失败」；「重试」照样在
  it("取消的单：状态列写「已取消」，仍可重试", () => {
    const items = [{ n: "丙.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "1:00", cost: 0, lang: "zh",
                     st: "failed" as const, error: "已取消" }];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getAllByText(/^已取消$|^Canceled$/)).toHaveLength(1);
    expect(screen.queryByText(/^失败$|^Failed$/, { selector: "span" })).toBeNull();
    expect(screen.getByRole("button", { name: /^重试$|^Retry$/ })).toBeInTheDocument();
  });
  it("opens a done row", async () => {
    const onOpen = vi.fn();
    wrap(<HistoryPage items={HISTORY_ITEMS} onNew={vi.fn()} onOpen={onOpen} />);
    await userEvent.click(screen.getByText("产品周会.m4a"));
    expect(onOpen).toHaveBeenCalled();
  });
  it("shows counts on tabs", () => {
    wrap(<HistoryPage items={HISTORY_ITEMS} onNew={vi.fn()} onOpen={vi.fn()} />);
    // 样本数据：8 条 = 1 处理中 + 6 已完成 + 1 失败
    expect(screen.getByRole("button", { name: /全部 8|All 8/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已完成 6|Done 6/ })).toBeInTheDocument();
  });
  // 2026-08-18「项目」下架（见 CLAUDE.md）：项目是永久锁价承诺而我们没有结项/删除手段，
  // 入口先摘掉。这条守着别手滑加回来——表和 jobs.project_id 还在，加回渲染是很容易的事。
  it("不再有项目筛选器（项目入口已下架）", () => {
    const items = [
      { n: "甲.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "1:00", cost: 1, lang: "zh", st: "done" as const },
      { n: "乙.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "1:00", cost: 1, lang: "zh", st: "done" as const },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.queryByRole("combobox", { name: /按项目筛选|Filter by project/ })).toBeNull();
    // 两行都在：没有筛选器就不该有任何行被过滤掉
    expect(screen.getByText("甲.m4a")).toBeInTheDocument();
    expect(screen.getByText("乙.m4a")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部 2|All 2/ })).toBeInTheDocument();
  });

  it("shows a 排队中 badge for queued jobs and counts them under 处理中", () => {
    const items = [
      { n: "排队任务.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "queued" as const },
      { n: "在跑.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 40 },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText("排队中")).toBeInTheDocument();          // 徽章独立成「排队中」（精确，不撞 caption）
    // 「处理中」tab 计数 = 在跑 + 排队 = 2（排队归入在途）
    expect(screen.getByRole("button", { name: /处理中 2|Processing 2/ })).toBeInTheDocument();
  });
  it("shows 上传中 badge (not 处理中) for the live uploading row", () => {
    const items = [
      { n: "上传中.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "uploading" as const, prog: 6 },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText("上传中")).toBeInTheDocument();          // 上传阶段标「上传中」徽章
    expect(screen.getByText(/上传 6%|Uploading 6%/)).toBeInTheDocument(); // 进度行 caption：显示真实上传百分比（不再只是「上传中…」）
    expect(screen.getByRole("button", { name: /处理中 1|Processing 1/ })).toBeInTheDocument(); // 仍归在途计数
  });
  it("百分比只显示整数：进度是连续浮点（供进度条平滑动画），但文字里不许漏出小数", () => {
    const items = [
      { n: "在跑.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 99.47381, etaMin: 6 },
      { n: "在传.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "uploading" as const, prog: 6.3333 },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/^99% · 约 6 分钟 · 可离开$|^99% · ~6 min · can leave$/)).toBeInTheDocument();
    expect(screen.getByText(/^上传 6%$|^Uploading 6%$/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+\.\d+%/);   // 页面上任何地方都不许出现 99.47% 这种
  });
  it("shows eta text when etaMin is present, and omits it when null", () => {
    const items = [
      { n: "有时长.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 65, etaMin: 6 },
      { n: "无时长.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 65, etaMin: null },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/65% · 约 6 分钟 · 可离开|65% · ~6 min · can leave/)).toBeInTheDocument();
    expect(screen.getByText(/^65% · 可离开$|^65% · can leave$/)).toBeInTheDocument();
  });
  it("does not open a processing row (no click-through to empty detail)", async () => {
    const onOpen = vi.fn();
    const items = [
      { n: "处理中.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 40 },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={onOpen} />);
    await userEvent.click(screen.getByText("处理中.mp4"));
    expect(onOpen).not.toHaveBeenCalled();   // 处理中不可点开（没出稿，点进去也没东西可做）
  });
  it("marks a done-but-expired job as 已过期 and not clickable", async () => {
    const onOpen = vi.fn();
    const items = [
      { n: "老任务.m4a", d: { zh: "05.01", en: "05.01" }, dur: "10:00", cost: 15, lang: "zh", st: "done" as const, expired: true },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={onOpen} />);
    expect(screen.getByText("已过期")).toBeInTheDocument();                         // 徽章
    expect(screen.getByText(/内容已过期|Content expired/)).toBeInTheDocument();      // caption
    await userEvent.click(screen.getByText("老任务.m4a"));
    expect(onOpen).not.toHaveBeenCalled();   // 过期不可点开（点开也拿不到结果）
  });
  it("shows 连接中断 caption when disconnected, overriding the normal progress caption", () => {
    const items = [
      { n: "断连中.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 40, disconnected: true },
      { n: "排队断连.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "queued" as const, disconnected: true },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getAllByText(/连接中断，正在重连|Connection lost, reconnecting/).length).toBe(2);
    expect(screen.queryByText(/40% · 可离开|40% · can leave/)).not.toBeInTheDocument();
    expect(screen.queryByText("排队中 · 等待开始")).not.toBeInTheDocument();
  });

  it("does not show 连接中断 caption when disconnected is false/absent", () => {
    const items = [
      { n: "正常处理中.mp4", d: { zh: "刚刚", en: "Just now" }, dur: "—", cost: 0, lang: "zh", st: "processing" as const, prog: 40 },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.queryByText(/连接中断|Connection lost/)).not.toBeInTheDocument();
  });

  it("shows the desensitized failure reason on a failed row when present", () => {
    const items = [
      { n: "失败任务.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "10:00", cost: 0, lang: "zh", st: "failed" as const, error: "转录失败，请重试" },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText("转录失败，请重试")).toBeInTheDocument();
  });

  it("does not crash and shows no reason line when a failed row has no error", () => {
    const items = [
      { n: "失败无原因.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "10:00", cost: 0, lang: "zh", st: "failed" as const },
    ];
    expect(() => wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />)).not.toThrow();
    expect(screen.getByText("失败无原因.m4a")).toBeInTheDocument();
  });

  // 重试的三种拒绝（余额不足 / 录音过期 / 不是失败态）各要用户做的事不同。
  // 吞掉就变成「点了没反应」——这是这个功能最容易退化成的样子，所以钉死。
  it("重试被拒时，理由显示在那一行上，并盖过原来的失败原因", async () => {
    const items = [
      { n: "失败.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "10:00", cost: 0, lang: "zh",
        st: "failed" as const, error: "转录失败，请重试" },
    ];
    const onRetry = vi.fn().mockRejectedValue(new Error("录音已超过保留期，无法重试；请重新上传"));
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /^重试$|^Retry$/ }));
    expect(await screen.findByText("录音已超过保留期，无法重试；请重新上传")).toBeInTheDocument();
    expect(screen.queryByText("转录失败，请重试")).toBeNull();
  });

  it("重试进行中按钮不可重复点（连点两下只发一次）", async () => {
    let release!: () => void;
    const onRetry = vi.fn().mockReturnValue(new Promise<void>((r) => { release = r; }));
    wrap(<HistoryPage items={HISTORY_ITEMS} onNew={vi.fn()} onOpen={vi.fn()} onRetry={onRetry} />);
    const btn = screen.getByRole("button", { name: /^重试$|^Retry$/ });
    await userEvent.click(btn);
    expect(await screen.findByRole("button", { name: /重试中|Retrying/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /重试中|Retrying/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    release();
  });

  it("fires onRetry on a failed row without opening it", async () => {
    const onRetry = vi.fn();
    const onOpen = vi.fn();
    wrap(<HistoryPage items={HISTORY_ITEMS} onNew={vi.fn()} onOpen={onOpen} onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /^重试$|^Retry$/ }));
    expect(onRetry).toHaveBeenCalled();
    expect(onOpen).not.toHaveBeenCalled();
  });

  // 失败原因话术是后端写死的中文（error_public，全站唯一一句）。英文界面不能原样蹦中文——
  // 前端对已知话术做映射（短期方案；错误种类变多了再升级「后端错误码+前端翻译」的长期方案）。
  it("英文界面下已知失败话术显示英文翻译，不蹦中文", () => {
    localStorage.setItem("tx_lang", "en");
    const items = [
      { n: "failed.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "10:00", cost: 0, lang: "zh", st: "failed" as const, error: "转录失败，请重试" },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText(/Transcription failed/)).toBeInTheDocument();
    expect(screen.queryByText("转录失败，请重试")).toBeNull();
    localStorage.removeItem("tx_lang");
  });

  it("英文界面下未知话术原样显示（映射表兜底，不吞不崩）", () => {
    localStorage.setItem("tx_lang", "en");
    const items = [
      { n: "raw.m4a", d: { zh: "刚刚", en: "Just now" }, dur: "10:00", cost: 0, lang: "zh", st: "failed" as const, error: "some raw error" },
    ];
    wrap(<HistoryPage items={items} onNew={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.getByText("some raw error")).toBeInTheDocument();
    localStorage.removeItem("tx_lang");
  });
});

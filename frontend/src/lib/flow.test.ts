import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useFlow, climbProgress, etaMinutes, isDisconnected, observedClimb, CLIMB_COEF } from "./flow";
import { TURNAROUND_MIN_PER_HOUR, TURNAROUND_RATIO } from "./turnaround";
import * as api from "./api";

beforeEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("useFlow (real)", () => {
  it("uploads then polls to done and loads segments", async () => {
    vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    const statuses: api.JobStatus[] = [
      { status: "running", phase: "P1", progress: 15, error: null, metrics: null },
      { status: "done", phase: "done", progress: 100, error: null, metrics: null },
    ];
    vi.spyOn(api, "getJobStatus").mockImplementation(async () => statuses.shift()!);
    vi.spyOn(api, "getResult").mockResolvedValue([{ t: "00:00:01", s: "你好", sp: "主持人" }]);
    vi.spyOn(api, "getReview").mockResolvedValue([]);

    const { result } = renderHook(() => useFlow());
    await act(async () => {
      result.current.start(new File(["x"], "a.m4a"), "zh");
    });

    await waitFor(() => expect(result.current.state).toBe("done"), { timeout: 5000 });
    expect(result.current.segments?.[0].s).toBe("你好");
    expect(result.current.phase).toBe("done");
  });

  it("surfaces queued state when backend reports queued (并发满时实时行应显示排队中)", async () => {
    vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    vi.spyOn(api, "getJobStatus").mockResolvedValue({ status: "queued", phase: null, progress: 0, error: null, metrics: null });
    vi.spyOn(api, "getResult").mockResolvedValue([]);
    vi.spyOn(api, "getReview").mockResolvedValue([]);
    const { result } = renderHook(() => useFlow());
    await act(async () => { result.current.start(new File(["x"], "a.m4a"), "zh"); });
    await waitFor(() => expect(result.current.state).toBe("queued"), { timeout: 5000 });
  });

  it("复核清单取不到（503）不吞成空清单：留在轮询里下个 tick 重试，直到取到才 done（E1 不伪装放心导出）", async () => {
    vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    vi.spyOn(api, "getJobStatus").mockResolvedValue({ status: "done", phase: "done", progress: 100, error: null, metrics: null });
    vi.spyOn(api, "getResult").mockResolvedValue([]);
    const rev = vi.spyOn(api, "getReview")
      .mockRejectedValueOnce(new Error("review 503"))
      .mockResolvedValueOnce([{ id: "r1" } as never]);
    const { result } = renderHook(() => useFlow());
    await act(async () => { result.current.start(new File(["x"], "a.m4a"), "zh"); });
    // 首个 tick getReview 失败：不许带着空清单进 done（那就是伪装）
    expect(result.current.state).not.toBe("done");
    await waitFor(() => expect(result.current.state).toBe("done"), { timeout: 6000 });
    expect(result.current.review?.length).toBe(1);
    expect(rev).toHaveBeenCalledTimes(2);
  });

  it("轮询遇 401（会话过期）→ 停止轮询并给明确指引，不许当网络抖动无限「重连中」", async () => {
    vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    const st = vi.spyOn(api, "getJobStatus")
      .mockRejectedValue(Object.assign(new Error("status 401"), { status: 401 }));
    const { result } = renderHook(() => useFlow());
    await act(async () => { result.current.start(new File(["x"], "a.m4a"), "zh"); });
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.error).toContain("登录已过期");
    expect(result.current.disconnected).toBe(false);   // 不是断连，是会话过期
    const calls = st.mock.calls.length;
    await new Promise((r) => setTimeout(r, 2300));      // 跨过一个轮询周期
    expect(st.mock.calls.length).toBe(calls);           // 轮询已停，不再打后端
  });

  it("goes to error state on upload failure", async () => {
    vi.spyOn(api, "uploadJob").mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useFlow());
    await act(async () => { result.current.start(new File(["x"], "a.m4a"), "zh"); });
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.error).toContain("boom");
  });

  it("不再向 uploadJob 透传录音类型（场景分流已取消）", async () => {
    const up = vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    vi.spyOn(api, "getJobStatus").mockResolvedValue({ status: "done", phase: "done", progress: 100, error: null, metrics: null });
    vi.spyOn(api, "getResult").mockResolvedValue([]);
    vi.spyOn(api, "getReview").mockResolvedValue([]);
    const { result } = renderHook(() => useFlow());
    await act(async () => {
      result.current.start(new File(["x"], "a.m4a"), "zh");
    });
    await waitFor(() => expect(up).toHaveBeenCalled());
    // 第 3 个参数现在是 durationSec，不再是场景；整条链上都不该出现 meeting/phonecall
    expect(up.mock.calls[0][2]).not.toBe("phonecall");
    expect(up.mock.calls[0][2]).not.toBe("meeting");
  });

  it("exposes live metrics from polling", async () => {
    vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    const statuses: api.JobStatus[] = [
      { status: "running", phase: "P2", progress: 70, error: null, metrics: { parts: 2, chars: 100 } },
      { status: "done", phase: "done", progress: 100, error: null, metrics: { parts: 2, chars: 100, speakers: 2, finalSegs: 5 } },
    ];
    vi.spyOn(api, "getJobStatus").mockImplementation(async () => statuses.shift()!);
    vi.spyOn(api, "getResult").mockResolvedValue([]);
    vi.spyOn(api, "getReview").mockResolvedValue([]);
    const { result } = renderHook(() => useFlow());
    await act(async () => { result.current.start(new File(["x"], "a.m4a"), "zh"); });
    await waitFor(() => expect(result.current.state).toBe("done"), { timeout: 5000 });
    expect(result.current.metrics?.speakers).toBe(2);
    expect(result.current.metrics?.finalSegs).toBe(5);
  });

  it("done 后 getResult 瞬时失败 → 下个 tick 重试自愈，不卡在处理中", async () => {
    vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
    vi.spyOn(api, "getJobStatus").mockResolvedValue({ status: "done", phase: "done", progress: 100, error: null, metrics: null });
    let calls = 0;
    vi.spyOn(api, "getResult").mockImplementation(async () => {
      calls += 1;
      if (calls === 1) throw new Error("瞬时 500"); // 第一次失败
      return [{ t: "00:00:01", s: "你好", sp: "主持人" }];
    });
    vi.spyOn(api, "getReview").mockResolvedValue([]);
    const { result } = renderHook(() => useFlow());
    await act(async () => { result.current.start(new File(["x"], "a.m4a"), "zh"); });
    await waitFor(() => expect(result.current.state).toBe("done"), { timeout: 8000 });
    expect(result.current.segments?.[0].s).toBe("你好");
    expect(calls).toBeGreaterThanOrEqual(2); // 第一次失败、第二次重试成功
  });

  it("轮询连续失败达阈值 → disconnected=true；恢复成功后立即归零", async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(api, "uploadJob").mockResolvedValue("job1");
      let calls = 0;
      vi.spyOn(api, "getJobStatus").mockImplementation(async () => {
        calls += 1;
        if (calls <= 3) throw new Error("network down"); // 前 3 次轮询都失败（含首次直探）
        return { status: "running", phase: "P1", progress: 20, error: null, metrics: null };
      });

      const { result } = renderHook(() => useFlow());
      await act(async () => {
        result.current.start(new File(["x"], "a.m4a"), "zh");
        await vi.advanceTimersByTimeAsync(0); // 冲掉 start() 里立即探的首次 tick（第 1 次失败）
      });
      expect(calls).toBe(1);
      expect(result.current.disconnected).toBe(false); // 1 次失败，未达阈值 3

      await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); // 第 2 次失败
      expect(result.current.disconnected).toBe(false);

      await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); // 第 3 次失败 → 达阈值
      expect(result.current.disconnected).toBe(true);
      expect(result.current.state).toBe("queued"); // 全程失败，state 未变化——仍是在途态，disconnected 有意义

      await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); // 第 4 次成功
      expect(result.current.disconnected).toBe(false); // 任一次成功即清零
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("isDisconnected（连续轮询失败次数 → 是否判定断连，默认阈值 3）", () => {
  it("失败次数低于阈值 → false", () => {
    expect(isDisconnected(0)).toBe(false);
    expect(isDisconnected(1)).toBe(false);
    expect(isDisconnected(2)).toBe(false);
  });

  it("失败次数达到/超过阈值 → true", () => {
    expect(isDisconnected(3)).toBe(true);
    expect(isDisconnected(4)).toBe(true);
    expect(isDisconnected(100)).toBe(true);
  });

  it("成功后计数归零 → 回落为 false", () => {
    expect(isDisconnected(0)).toBe(false);
  });

  it("支持自定义阈值", () => {
    expect(isDisconnected(1, 2)).toBe(false);
    expect(isDisconnected(2, 2)).toBe(true);
  });
});

describe("climbProgress（一根匀速条：处理已跑多久 ÷ 录音×CLIMB_COEF，匀速爬到 99% 封顶）", () => {
  // ⚠️ 这一条才是真正的不变量，下面那些具体数字只是它的例子：
  // 爬升系数要比**最快的那一单**还快，进度条才会先爬满、停在 99% 等完成。
  // 2026-08-31 生产 73 单实测：长单处理÷录音 最快 0.30、中位 0.37。
  it("系数必须比实测最快的处理比还小——否则典型的一单会在进度条只走到七成时就完成", () => {
    expect(CLIMB_COEF).toBeLessThan(0.30);
  });

  // 对外承诺要往大了说（留余量），进度条要往小了说（先爬满）。方向相反，所以是两个数。
  it("系数必须小于对外承诺的倍率——两者方向相反，合并成一个数就会有一边是错的", () => {
    expect(CLIMB_COEF).toBeLessThan(TURNAROUND_RATIO);
  });

  it("elapsed=0 → 0%", () => {
    expect(climbProgress(0, 1000)).toBe(0);
  });

  it("elapsed=爬升时长一半 → ≈49.5%（dur=1000 → 爬升 estSec=250，125s=一半）", () => {
    expect(climbProgress(1000 * CLIMB_COEF / 2, 1000)).toBeCloseTo(99 * 0.5, 1);
  });

  it("30min 音频(1800s)：爬升时长 450s，225s 时 ≈49.5%", () => {
    expect(climbProgress(225, 1800)).toBeCloseTo(49.5, 1);
  });

  it("elapsed 远超爬升时长 → 封顶 99%，不假装到 100（情况②停 99 等）", () => {
    expect(climbProgress(100000, 1000)).toBe(99);
  });

  it("durationSec 缺失/为 0 → 固定 10min(600s) 兜底，不除零", () => {
    expect(() => climbProgress(10, null)).not.toThrow();
    expect(() => climbProgress(10, 0)).not.toThrow();
    expect(climbProgress(300, null)).toBeCloseTo(49.5, 1);   // 600s 的一半
  });
});

describe("etaMinutes（估剩余分钟，给“约 X 分钟”文案用）", () => {
  // 🔴 这一条是**跨模块对账**：界面上那个「约 X 分钟」与首页「1 小时录音通常 N 分钟内出稿」
  // 必须是同一个数。2026-08-31 之前首页说 10、界面说 36，用户传一单就能看见我们自相矛盾。
  it("1 小时录音、刚开始 → 报的就是首页承诺的那个数", () => {
    expect(etaMinutes(0, 3600)).toBe(TURNAROUND_MIN_PER_HOUR);
  });

  it("1 小时录音、走到一半 → 承诺值的一半", () => {
    expect(etaMinutes(50, 3600)).toBe(Math.round(TURNAROUND_MIN_PER_HOUR / 2));
  });

  it("dur 缺失 → null（文案回退不显时间）", () => {
    expect(etaMinutes(50, null)).toBeNull();
    expect(etaMinutes(50, undefined)).toBeNull();
    expect(etaMinutes(50, 0)).toBeNull();
  });

  it("最低不低于 1 分钟", () => {
    expect(etaMinutes(99, 3600)).toBeGreaterThanOrEqual(1);
  });
});

describe("observedClimb（历史行：从首次观察到该 job 在跑起算，匀速爬）", () => {
  it("首次观察 → 记起点，返回 0%", () => {
    const seen = new Map<string, { since: number }>();
    expect(observedClimb(seen, "j1", 1000, 10_000)).toBe(0);
    expect(seen.get("j1")).toEqual({ since: 10_000 });
  });

  it("持续观察 → 按起点 elapsed 爬（dur=1000 → estSec=250，125s=一半≈49.5%）", () => {
    const seen = new Map<string, { since: number }>();
    observedClimb(seen, "j1", 1000, 10_000);
    expect(observedClimb(seen, "j1", 1000, 10_000 + 125_000)).toBeCloseTo(49.5, 1);
  });

  it("多个 job 互不串（各自独立起点）", () => {
    const seen = new Map<string, { since: number }>();
    observedClimb(seen, "j1", 1000, 0);
    observedClimb(seen, "j2", 1000, 125_000);
    expect(observedClimb(seen, "j1", 1000, 125_000)).toBeCloseTo(49.5, 1);
    expect(observedClimb(seen, "j2", 1000, 125_000)).toBe(0);   // j2 刚起步
  });

  it("封顶 99%（起算偏晚只会更保守，绝不虚高到 100）", () => {
    const seen = new Map<string, { since: number }>();
    observedClimb(seen, "j1", 1000, 0);
    expect(observedClimb(seen, "j1", 1000, 9_999_000)).toBe(99);
  });
});

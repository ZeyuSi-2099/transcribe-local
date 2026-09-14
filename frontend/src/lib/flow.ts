import { useEffect, useRef, useState } from "react";
import { uploadJob, getJobStatus, getResult, getReview, type TranscriptRow, type JobMetrics } from "./api";
import type { ReviewItem } from "./reviewData";
import { TURNAROUND_RATIO } from "./turnaround";

export type FlowState = "idle" | "uploading" | "queued" | "processing" | "done" | "error";

export interface Flow {
  state: FlowState;
  progress: number;            // uploading=上传%；processing=处理%（后端 Phase 映射原值）
  displayProgress: number;     // 给 UI 用的显示进度：uploading=上传%；processing=climbProgress 爬升后的值（不冻住）
  phase: string | null;        // P0..P3/done（processing 期间）
  jobId: string | null;
  segments: TranscriptRow[] | null;
  metrics: JobMetrics | null;
  review: ReviewItem[] | null;
  error: string | null;
  disconnected: boolean;       // 连续轮询失败达阈值：在途态下提示「连接中断，正在重连」；done/error/idle 恒 false
  start: (file: File, lang: string, durationSec?: number | null, glossaryId?: string | null) => void;
  reset: () => void;
}

// 连续轮询失败达到这个次数才判定「断连」（轮询间隔 2s，3 次 ≈ 6s），避免偶发一次抖动就误报。
const DISCONNECT_THRESHOLD = 3;
export function isDisconnected(failCount: number, threshold: number = DISCONNECT_THRESHOLD): boolean {
  return failCount >= threshold;
}

export function useFlow(): Flow {
  const [state, setState] = useState<FlowState>("idle");
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [segments, setSegments] = useState<TranscriptRow[] | null>(null);
  const [metrics, setMetrics] = useState<JobMetrics | null>(null);
  const [review, setReview] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [disconnectedRaw, setDisconnectedRaw] = useState(false);
  const failCountRef = useRef(0);   // 连续轮询失败计数：tick 成功归零，进 catch +1
  const pollRef = useRef<number | null>(null);
  // 时间驱动爬升的计时状态：处理（running）从何时开始 + 本次录音时长。进度条不再分 Phase，
  // 就一根匀速条按「处理已跑多久 / 录音时长×系数」爬（见 climbProgress）。
  const procStartedAtRef = useRef<number | null>(null);
  const durationSecRef = useRef<number | null>(null);
  // processing 期间后端几十秒不给新数据——单靠轮询重渲会让进度条冻住。开一个 500ms tick 强制重渲，
  // 爬升值仍由 climbProgress 现算（不存 state，避免和后端真实值打架）。
  const [, setDisplayTick] = useState(0);

  const stopPoll = () => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const reset = () => {
    stopPoll();
    setState("idle"); setProgress(0); setPhase(null);
    setJobId(null); setSegments(null); setMetrics(null); setReview(null); setError(null);
    setDisconnectedRaw(false); failCountRef.current = 0;
    procStartedAtRef.current = null; durationSecRef.current = null;
  };

  const fail = (msg: string) => { stopPoll(); setError(msg); setState("error"); };

  const start = async (file: File, lang: string, durationSec?: number | null, glossaryId?: string | null) => {
    stopPoll(); // 连续多次上传时先清掉上一个轮询，否则旧 interval 失引用清不掉、多个轮询抢同一进度状态 → 进度条乱跳
    setError(null); setSegments(null); setMetrics(null); setReview(null);
    setDisconnectedRaw(false); failCountRef.current = 0;
    setState("uploading"); setProgress(0);
    durationSecRef.current = durationSec ?? null;
    procStartedAtRef.current = null;
    let id: string;
    try {
      id = await uploadJob(file, lang, durationSec, (pct) => setProgress(pct), glossaryId);
    } catch (e) {
      fail(String(e));
      return;
    }
    setJobId(id);
    // 刚上传完后端此刻就是 queued，初始态如实置「排队中」；下面立即探一次拿真实状态，
    // 不等 2s 定时器——否则排队任务会先错闪「处理中 10%」再转「排队中」（首轮询前的 2s 空窗）
    setState("queued"); setProgress(0); setPhase(null);
    let finished = false;
    const tick = async () => {
      try {
        const st = await getJobStatus(id);
        failCountRef.current = 0; setDisconnectedRaw(false);   // 轮询成功：任一次成功即清断连提示
        setPhase(st.phase); setProgress(st.progress); setMetrics(st.metrics);
        if (st.status === "done") {
          // 先取结果、成功后再停轮询：getResult 万一瞬时失败（抛出被下方 catch 吞掉），
          // 轮询仍在，下个 tick 重试 done 分支自愈。旧代码先 stopPoll 再 getResult，
          // 一旦 getResult 失败就永久卡在「处理中」（既不 done 也不 error）。
          const segs = await getResult(id);
          // getReview 失败必须抛（进外层 catch、轮询里下个 tick 重试）——不许吞成 []：
          // 复核清单是「放心导出」的判据，空清单伪装正是后端 E1 改 503 要消灭的行为。
          // 复核确实不存在时后端返 200+[]，不会走到抛错这条路。
          const rev = await getReview(id);
          stopPoll(); finished = true;
          setSegments(segs); setReview(rev); setState("done"); setProgress(100); setPhase("done");
        } else if (st.status === "failed") {
          finished = true;
          fail(st.error || "处理失败");
        } else {
          // queued=并发满在排队；running=在跑。单独成 queued 态，供实时行显示「排队中」徽章。
          // 首次进 running（processing）记下处理起点——进度条从这一刻起匀速爬（排队不爬）。
          if (st.status !== "queued" && procStartedAtRef.current === null) {
            procStartedAtRef.current = Date.now();
          }
          setState(st.status === "queued" ? "queued" : "processing");
        }
      } catch (e) {
        // 401 = 会话过期，不是网络抖动：再轮询一万次也不会好，还顶着「正在重连」误导用户。
        // 停轮询、置失败并给明确指引（话术在 HistoryPage 按界面语言映射成英文）。
        if ((e as { status?: number })?.status === 401) {
          finished = true;
          fail("登录已过期，请重新登录");
          return;
        }
        // 瞬时网络错误：保持轮询，但计数——连续失败达阈值才提示用户「断连」，避免偶发抖动误报
        failCountRef.current += 1;
        setDisconnectedRaw(isDisconnected(failCountRef.current));
      }
    };
    await tick();   // 立即探一次：上传完到首轮询的真实状态 ~100ms 内就位，消除「处理中 10%」闪烁
    if (!finished) pollRef.current = window.setInterval(tick, 2000);
  };

  // 组件卸载（如退出登录）时清掉轮询定时器，避免僵尸轮询（带过期 token 一直打 401）
  useEffect(() => () => { if (pollRef.current !== null) clearInterval(pollRef.current); }, []);

  // processing 期间：P2/P3 无子进度，后端可能几十秒都不给新数据——单靠轮询重渲会让进度条冻住。
  // 开一个 500ms tick 强制重渲，displayProgress 每次重算都会用最新 elapsed 时间去爬升。
  useEffect(() => {
    if (state !== "processing") return;
    const t = window.setInterval(() => setDisplayTick((n) => n + 1), 500);
    return () => clearInterval(t);
  }, [state]);

  // 上传态：显示上传自身 %；处理态：一根匀速条按「处理已跑多久」爬到 99%（不分 Phase）；
  // done=100（UI 靠 CSS transition 从当前值平滑过渡到 100）；排队/其余态原样用后端 progress(=0)。
  const displayProgress = state === "processing" && procStartedAtRef.current !== null
    ? climbProgress((Date.now() - procStartedAtRef.current) / 1000, durationSecRef.current)
    : progress;

  // disconnected 只在「排队中/处理中」的在途态有意义；done/error/idle 恒 false（即便断连计数还没来得及清零）
  const disconnected = (state === "processing" || state === "queued") && disconnectedRaw;

  return { state, progress, displayProgress, phase, jobId, segments, metrics, review, error, disconnected, start, reset };
}

// 进度条爬升（2026-07-13 定案，2026-08-31 按新实测重标）：去掉 Phase 分段，一根匀速条按
// 「处理已跑多久」爬到 99%。爬升时长 = 录音时长 × CLIMB_COEF。
//
// ⚠️ **CLIMB_COEF 必须小于「最快的那一单」的处理比，不是小于中位**——它要的是「进度条先爬满、
// 停在 99% 等完成」（最平滑），而不是爬到一半被完成打断从中途跳。2026-08-31 生产 73 单实测：
// 长单处理÷录音 最快 0.30、中位 0.37（2026-07-13 那轮量到的是 0.55/0.66，流水线此后快了不少）。
// 沿用旧的 0.5 会让**典型的一单在进度条只走到七成时就完成**，正是当初想避免的那种跳变。
//   ① 处理先完成 → 调用方在 done 时置 100，UI 用 CSS transition 平滑过渡；
//   ② 爬到 99% 还没完成（正常）→ 停 99% 等，done 才到 100。
// 封顶 99（不到 100 不假装完成）。durationSec 缺失（老任务没记录）→ 固定 10min 兜底爬。
//
// ⚠️ 它**不该**改成从 `turnaround.ts` 派生：那个常量是「对外承诺，留余量往大了说」，
// 这个要「比最快的还快」，两者方向相反。同一个数满足不了两个相反的要求。
export const CLIMB_COEF = 0.25;
const CLIMB_CEIL = 99;
const CLIMB_FALLBACK_SEC = 600;
export function climbProgress(elapsedProcSec: number, durationSec: number | null | undefined): number {
  const estSec = durationSec ? durationSec * CLIMB_COEF : CLIMB_FALLBACK_SEC;
  return Math.min(CLIMB_CEIL, CLIMB_CEIL * Math.min(1, elapsedProcSec / Math.max(estSec, 1)));
}

// 历史列表行的爬升：这些行来自后端轮询（刷新/换设备后 useFlow 的计时状态已丢失），从「本浏览器
// 首次观察到该 job 在跑」起算。起算偏晚只会爬得更保守（更早停 99%），绝不会虚高。
// seen 由调用方持有（按 jobId 记 {since}），本函数负责登记。
export function observedClimb(
  seen: Map<string, { since: number }>,
  jobId: string, durationSec: number | null | undefined, now: number,
): number {
  let cur = seen.get(jobId);
  if (!cur) { cur = { since: now }; seen.set(jobId, cur); }
  return climbProgress((now - cur.since) / 1000, durationSec);
}

// 给「约 X 分钟」文案估剩余时间：预计总处理时长 = 录音时长 × TURNAROUND_RATIO。
//
// ⚠️ **系数从 `turnaround.ts` 取，不许在这里另写一个数**：那个常量同时是首页「1 小时录音通常
// N 分钟内出稿」的来源。分成两份写的后果不是报错，是用户传一单就看见首页说 30 分钟、
// 界面说约 36 分钟——我们自己前后矛盾，而两处都"没错"。2026-08-31 之前正是这个状态。
//
// 与进度条爬升的 CLIMB_COEF 分开是有意的：文案要往大了报（先报久、提前完成），
// 进度条要往小了报（先爬满、停 99% 等）。P3 耗时方差极大，此估算只能给量级、不可能精确。
//
// ⚠️ 入参必须是**用户当下看到的那个进度**（displayProgress / observedClimb 的爬升值，满格 =
// CLIMB_CEIL），不是后端 Phase 锚点 progress。曾经喂后端锚点：进度条按时间从 0 重爬（刷新后）、
// ETA 却按锚点 55% 折算 → 屏幕上并排显示「0% · 约 3 分钟」，自相矛盾。两个数字必须同源。
export function etaMinutes(displayedProgress: number, durationSec: number | null | undefined): number | null {
  if (!durationSec) return null;
  const totalSec = durationSec * TURNAROUND_RATIO;
  const remainSec = totalSec * (1 - Math.min(displayedProgress, CLIMB_CEIL) / CLIMB_CEIL);
  return Math.max(1, Math.round(remainSec / 60));
}

import { useState, useEffect, useCallback, useRef } from "react";
import { useFullHeightScreen } from "./lib/fullHeightScreen";
import { pendingUnsaved } from "./lib/unsavedGuard";
import { Button } from "./components/Button";
import { useL } from "./lib/i18n";
import { semantic, layout, radius, shadow, type as ttype } from "./styles/tokens";
import { useFlow, etaMinutes, observedClimb } from "./lib/flow";
import { rateFor, MIN_TOPUP } from "./lib/pricing";
import { fmtClock, toSec } from "./lib/format";
import { getLedger, getMe, getPendingTopups, getPostprocess, getResult, getReview, getReviewState, listJobs, retryJob, topup as topupApi, markInvoiced, listGlossaries, createGlossary, updateGlossary, deleteGlossary, type Glossary, type JobRow, type LedgerRow, type Me, type PendingTopup, type PostprocessStatus, type ReviewStateBlob, type TranscriptRow } from "./lib/api";
import type { ReviewItem } from "./lib/reviewData";
import type { HistoryItem, BillingLine, TopupRecord } from "./lib/sampleData";
import { Sidebar } from "./components/Sidebar";
import { MainPage } from "./screens/main/MainPage";
import { HistoryPage } from "./screens/HistoryPage";
import { AdminPage } from "./screens/AdminPage";
import { GlossaryPage } from "./screens/main/GlossaryPage";
import { PostprocessPage } from "./screens/main/PostprocessPage";
import { Result } from "./screens/main/Result";
import { DetailSkeleton } from "./screens/main/DetailSkeleton";
import { BillingModal } from "./screens/BillingModal";
import { SettingsModal } from "./screens/SettingsModal";
import { TopUpModal } from "./screens/TopUpModal";
import { NarrowScreenNotice, useNarrowGate } from "./screens/NarrowScreenNotice";

export type Page = "new" | "history" | "detail" | "glossary" | "postprocess" | "admin";

interface AppShellProps {
  me: Me | null;
  onLogout: () => void;
}

// ISO 时间 → 列表显示用短日期
const fmtDate = (iso: string) => {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  const md = `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return { zh: md, en: md };
};

const RESULT_TTL_DAYS = 30;   // R2 result/review 生命周期：30 天后内容被删
const jobToItem = (j: JobRow): HistoryItem & { id: string } => ({
  id: j.id,
  n: j.fileName ?? "未命名音频",
  d: fmtDate(j.createdAt),
  dur: j.durationSec != null ? fmtClock(j.durationSec) : "—",
  // 花费认后端下发的真实值（账本扣费 / 在途冻结额），不在前端拿单价重算——每单费率是上传时的快照
  cost: (j.costCents ?? 0) / 100,
  lang: j.lang ?? "zh",
  st: j.status === "done" ? "done" : j.status === "failed" ? "failed" : j.status === "queued" ? "queued" : "processing",
  prog: j.progress,
  // 完成且超 30 天 → 内容已被 R2 生命周期删除，标过期、不可点开（点开会拿不到结果）
  expired: j.status === "done" && Date.now() - new Date(j.createdAt).getTime() > RESULT_TTL_DAYS * 86400_000,
  error: j.error,   // 失败原因（后端脱敏话术），失败行给用户展示
  pp: j.postprocess ?? null,   // 后处理状态（行内 caption 三态）
});

export function AppShell({
  me,
  onLogout,
}: AppShellProps) {
  useFullHeightScreen();   // 应用是满屏外壳，不跟着文档滚（公开站相反，见 global.css）
  const L = useL();
  // 小屏劝退：只拦触屏设备（手机/平板竖屏），桌面窗口拖窄不拦——判据与理由见 NarrowScreenNotice
  const narrowGate = useNarrowGate();
  const [narrowAck, setNarrowAck] = useState(false);
  const [page, setPage] = useState<Page>("new");
  // 切页前先问「有没保存的改动」（脱敏清单 / 术语库都是手动保存）：有就弹一层，让用户选存不存。
  // 用户主动的切页都走 go()；流程自己的跳转（上传完进历史页、打开详情）仍直接 setPage——那些不会
  // 发生在编辑页上。判据与登记见 lib/unsavedGuard.ts。
  const [leaveTo, setLeaveTo] = useState<Page | null>(null);
  const go = useCallback((next: Page) => {
    if (next !== page && pendingUnsaved()) setLeaveTo(next);
    else setPage(next);
  }, [page]);
  const signedIn = me != null;
  const [balance, setBalance] = useState((me?.balanceCents ?? 0) / 100); // 美元
  const [showTopUp, setShowTopUp] = useState(false);
  const [topUpPrefill, setTopUpPrefill] = useState<number | undefined>(undefined); // 软墙缺口预填
  const [showSettings, setShowSettings] = useState(false); // 设置浮窗（不是页面）
  // 术语库（每用户多本；登录态走后端持久化）。selectedGlossaryId = 下次转录默认用哪本（localStorage 记住上次选择）
  // null = 还没取回来。**不能用空数组表示未知**：术语库页会据此摆出「还没有术语库」
  // 的空态屏（连「新建第一本」的按钮一起），有三本库的人先看到它、再翻成列表。
  const [glossaries, setGlossaries] = useState<Glossary[] | null>(null);
  const [selectedGlossaryId, setSelectedGlossaryId] = useState<string | null>(
    () => localStorage.getItem("glossaryId"));
  // 免费额度（定价 V2 §2）：软墙/预估要算入免费抵扣；limited=IP 闸提示
  const [free, setFree] = useState(me?.free ?? { grantedMinutes: 0, leftSeconds: 0, limited: false });
  const [referral, setReferral] = useState(me?.referral);
  const [showBilling, setShowBilling] = useState(false); // 账单浮窗（不是页面）
  // 账户数据（后端为准）：任务列表 + 账本
  const [jobs, setJobs] = useState<JobRow[]>([]);
  // 「任务列表拉回来了没有」——只服务上传页左栏的首帧布局（见 Idle 里 twoCol 的注释）。
  // 分不清「还不知道」与「确实一份都没有」的话，有历史的老用户每次进上传页都会先看到
  // 单栏居中、再跳成两栏。⚠️ 只在**首次**拉回时置位，之后的轮询不再动它。
  const [jobsLoaded, setJobsLoaded] = useState(false);
  const [ledger, setLedger] = useState<LedgerRow[]>([]);
  const [ledgerLoaded, setLedgerLoaded] = useState(false);
  // 当前这次转录的展示信息（开始时记下，flow 非 idle 时它就是「我的转录」列表里的实时一行）
  const [live, setLive] = useState<{ name: string; lang: string; durationSec: number | null; freeSeconds: number } | null>(null);
  // 详情页看的是哪条："live"=本次转录；历史条目 = 已加载的真实数据
  const [detailSrc, setDetailSrc] = useState<"live" | { item: HistoryItem & { id: string }; segments: TranscriptRow[]; review: ReviewItem[]; rs: { s: ReviewStateBlob } | null; pp: { s: PostprocessStatus | null } | null } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const flow = useFlow();

  // 刷新账户数据（余额/任务/账本）——登录态才有得刷
  const refreshAccount = useCallback(() => {
    if (!signedIn) return;
    getMe().then((m) => {
      setBalance(m.balanceCents / 100);
      if (m.free) setFree(m.free);
      if (m.referral) setReferral(m.referral);
    }).catch(() => {});
    listJobs().then((j) => { setJobs(j); setJobsLoaded(true); }).catch(() => {});
    getLedger().then((l) => { setLedger(l); setLedgerLoaded(true); }).catch(() => {});
    listGlossaries().then((gs) => {
      setGlossaries(gs);
      // 默认选中：localStorage 上次**显式**选过的那本（若还在）→ 否则「不使用」。
      // ⚠️ **绝不替用户挑一本**（2026-08-28 前是回落 `gs[0]`＝最近更新那本）：挂错库不报错，
      // 它会把近音词悄悄拽向那本库所属行业的写法，界面上还显示成【术语库】证据——
      // 与 vendor 那次「用户一本库都没选却吃到别人的行业词表」是同一种错，只是这次是我们自己选的。
      setSelectedGlossaryId((cur) => (cur && gs.some((g) => g.id === cur)) ? cur : null);
    // 取失败也要落地成 []：**未知不能是永久的**，否则术语库页会一直卡在骨架上
    }).catch(() => setGlossaries([]));
  }, [signedIn]);
  useEffect(() => { refreshAccount(); }, [refreshAccount]);

  // ── 在路上的充值（2026-09-06）：微信 / 支付宝付完款 Paddle 要几分钟才确认，此前这几分钟界面上什么都不说，
  // 客户回到应用看到余额 0 以为没付成。现在每 5 秒问一次后端（后端顺手向 Paddle 对账），
  // 有在路上的钱就显示「入账中」，消失那一刻＝到账：刷新余额、显示几秒「已到账」。
  // 轮询只在「有在路上的」或「付款页刚标记过付款成功」时持续，最多 15 分钟，其余时候只在进应用时问一次。
  const [pendingTopups, setPendingTopups] = useState<PendingTopup[]>([]);
  const [justCredited, setJustCredited] = useState<number | null>(null);
  const pendingRef = useRef<PendingTopup[]>([]);
  useEffect(() => {
    if (!signedIn) return;
    let stopped = false;
    let timer = 0;
    const started = Date.now();
    const tick = async () => {
      let items: PendingTopup[] = [];
      try { const r = await getPendingTopups(); items = Array.isArray(r?.items) ? r.items : []; } catch { return; }   // 未登录 / 断网 / 老后端：不再问
      if (stopped) return;
      const gone = pendingRef.current.filter((p) => !items.some((i) => i.txnId === p.txnId));
      pendingRef.current = items;
      setPendingTopups(items);
      if (gone.length) {
        setJustCredited(gone.reduce((s, g) => s + g.amountCents, 0));
        refreshAccount();
        window.setTimeout(() => setJustCredited(null), 6000);
        try { localStorage.removeItem("tx_topup_paid"); } catch { /* 无痕等情况忽略 */ }
      }
      let paidFlag = 0;
      try { paidFlag = Number(localStorage.getItem("tx_topup_paid") || 0); } catch { /* ignore */ }
      const keep = items.length > 0 || (paidFlag > 0 && Date.now() - paidFlag < 15 * 60 * 1000);
      if (keep && Date.now() - started < 15 * 60 * 1000) timer = window.setTimeout(tick, 5000);
    };
    tick();
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [signedIn, refreshAccount]);

  // 术语库 CRUD：登录态写后端持久化后刷新本地列表（别信本地态，看后端读回——历史踩过坑）
  const reloadGlossaries = useCallback(async () => {
    const gs = await listGlossaries();
    setGlossaries(gs);
    return gs;
  }, []);
  const onCreateGlossary = useCallback(async (name: string) => {
    const g = await createGlossary(name, null, "");
    await reloadGlossaries();
    return g;
  }, [reloadGlossaries]);
  const onUpdateGlossary = useCallback(async (id: string, name: string, language: string | null, content: string) => {
    await updateGlossary(id, name, language, content);
    await reloadGlossaries();
  }, [reloadGlossaries]);
  const onDeleteGlossary = useCallback(async (id: string) => {
    await deleteGlossary(id);
    await reloadGlossaries();
    setSelectedGlossaryId((cur) => (cur === id ? null : cur));   // 删掉在用的那本 → 回「不使用」，不顺手换一本
  }, [reloadGlossaries]);
  const onSelectGlossary = useCallback((id: string | null) => {
    setSelectedGlossaryId(id);
    if (id) localStorage.setItem("glossaryId", id); else localStorage.removeItem("glossaryId");
  }, []);


  // 转录完成：后端已按真实时长结账（worker），前端刷新余额与账本即可
  useEffect(() => {
    if (flow.state !== "done" && flow.state !== "error") return;
    refreshAccount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow.state]);

  // 上传落库那一刻**强制重拉一次**账户数据（列表 + 账本 + 余额）。
  // ⚠️ 这条不是锦上添花，它防的是**重复付费**（2026-08-27 巡检里就这么白交了一遍俄语单）：
  // 提交后页面直接跳「我的转录」，而 `page` 那条刷新在**上传还没传完**时就跑了，
  // 拉回来的是提交之前的快照——刚交的那单不在列表里，看上去就像"没提交成功"，于是再交一遍。
  // jobId 是「后端已经收下这一单」的唯一确证，所以刷新挂在它身上，不挂在点击或页面切换上。
  // 账本一并刷：预扣那一笔同样是这一刻才产生的（账单浮窗少两笔是同一个成因）。
  useEffect(() => {
    if (!flow.jobId) return;
    refreshAccount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow.jobId]);

  // 有任务在处理时轮询后端任务列表——单 useFlow 只跟最后一个上传，多个并发任务的实时进度
  // 全靠这里从后端拉，否则连传多个时列表只显示最后一个。无活跃任务则不轮询，避免空转。
  // 后处理在跑的行也要轮询：转录本身已 done、不再有活跃 job，但 caption 的步进/百分比靠这里刷新
  const hasActiveJob = jobs.some((j) =>
    j.status === "queued" || j.status === "running" ||
    j.postprocess?.status === "queued" || j.postprocess?.status === "running");
  const flowActive = flow.state === "uploading" || flow.state === "processing" || flow.state === "queued";
  useEffect(() => {
    if (!signedIn || (!hasActiveJob && !flowActive)) return;
    const t = setInterval(() => { listJobs().then(setJobs).catch(() => {}); }, 2500);
    return () => clearInterval(t);
  }, [signedIn, hasActiveJob, flowActive]);
  // 进历史页刷新一次列表：后处理在详情页发起/完成时列表快照已旧，且旧快照里无在途行
  // 不会触发上面的轮询——不刷会看不到「✦ 已加工」caption（2026-07-24 穿测踩中）
  useEffect(() => {
    if (signedIn && page === "history") listJobs().then(setJobs).catch(() => {});
  }, [signedIn, page]);

  // suggested = 软墙缺口金额（来自上传卡「先充值 →」），预填进充值弹窗。
  // 门①（按单即传即付）：单笔支付最低 $10——缺口不足 $10 的按 $10 预填，差额留在余额
  const openTopUp = (suggested?: number) => {
    setTopUpPrefill(suggested != null ? Math.max(Math.ceil(suggested), MIN_TOPUP) : undefined);
    setShowTopUp(true);
  };

  // 上传 + 开始（已过软墙）：记下本次任务、开跑、直接进「我的转录」
  const startJob = (file: File, lang: string, durationSec: number | null) => {
    // 免费秒在上传那一刻捕获（me 随后刷新会扣减，之后再算就错基了）；与后端预扣同口径
    const freeSeconds = durationSec != null
      ? Math.min(free.leftSeconds, durationSec) : 0;
    setLive({ name: file.name, lang, durationSec, freeSeconds });
    flow.start(file, lang, durationSec, selectedGlossaryId);
    setPage("history");
  };

  // flow 非 idle 时，「我的转录」列表顶部的实时一行
  const liveRow: HistoryItem | null = live && flow.state !== "idle" ? {
    n: live.name,
    d: { zh: "刚刚", en: "Just now" },
    dur: live.durationSec != null ? fmtClock(live.durationSec) : "—",
    // 刚上传这一行后端花费还没轮询回来，本地按当前费率估——同一时刻上传，估价必等于快照价
    // （先抵上传时捕获的免费秒，剩余按单价，与后端预扣同口径。
    //  2026-08-01 生产实测踩坑：不减免费秒会把全免费的单短暂显示成全价，刷新后才归零）
    cost: live.durationSec != null
      ? (Math.max(0, live.durationSec - live.freeSeconds) / 60) * rateFor(live.lang)
      : 0,
    lang: live.lang,
    st: flow.state === "done" ? "done" : flow.state === "error" ? "failed" : flow.state === "uploading" ? "uploading" : flow.state === "queued" ? "queued" : "processing",
    prog: flow.displayProgress,
    // ETA 喂显示进度（不是后端锚点）——否则屏幕上会并排出现「3% · 约 3 分钟」这种自相矛盾
    etaMin: flow.state === "processing" ? etaMinutes(flow.displayProgress, live.durationSec) : null,
    disconnected: flow.disconnected,
    // 刚失败的这单用户最需要知道原因：flow.error 即后端脱敏话术（st.error=error_public）。
    // 不加这行，liveRow 会顶掉后端那条带话术的同 id 历史行，失败原因反而看不见。
    error: flow.error,
  } : null;

  // 历史列表（后端任务，进行中的本次任务由 liveRow 顶替避免重复）。
  // running 行的进度不能裸用后端原值（一根匀速条按时间爬，后端 progress 是 Phase 锚点会冻住）——
  // 「可离开」邀请用户刷新/换设备回来，回来必须还在爬：按 observedClimb 从首次观察起算。
  const climbSeenRef = useRef(new Map<string, { since: number }>());
  const historyItems: (HistoryItem & { id: string })[] = jobs
    .filter((j) => !(liveRow && j.id === flow.jobId))
    .map((j) => {
      const it = jobToItem(j);
      if (j.status === "running") {
        it.prog = Math.round(observedClimb(climbSeenRef.current, j.id, j.durationSec, Date.now()));
        it.etaMin = etaMinutes(it.prog, j.durationSec);   // 与上一行同源，别拿后端锚点算
      } else {
        climbSeenRef.current.delete(j.id);   // 终态/排队不留观察记录，防 Map 无界增长
      }
      return it;
    });

  // 点历史里的已完成任务 → 拉真实文稿与复核清单
  const openHistoryItem = async (item: HistoryItem & { id: string }) => {
    // ⚠️ **先清空上一单**：不清的话 detailProps 仍是上一单的数据，而渲染分支是
    // 「有 detailProps 就渲染 Result，否则才看 loading」——于是点开 B 会先原样显示
    // 半秒到一秒的 A 详情页，再跳成 B（2026-08-22 Duner 实测）。清空后走骨架屏。
    setDetailSrc(null);
    setDetailLoading(true);
    setPage("detail");
    try {
      // getReview 失败不吞成 []（那会带着空清单开详情=「放心导出」伪装）：让它抛进下面的
      // catch，本次点开失败弹回列表，用户重点一次即重试。复核真不存在时后端返 200+[]。
      // ⚠️ **复核决策要跟稿子一起取回来**（2026-08-22 Duner 连拍四张实见）。
      // 决策留在详情页里自己取的话，右栏会先按「一条决策都没有」渲染一遍：
      // 一份早就复核完的稿子先摆「17 项待确认」+ 后处理「还差 17 处确认」，
      // 三秒后才翻成「已全部确认」。**同一个毛病之前在这条路上出过一次**
      // （点开 B 先看到 A 的详情），根因都是「页面先开、数据后到」。
      // ⚠️ **取不到不许当成「没有」**（2026-08-23）：`.catch(() => null)` 曾把
      // 「问不出来」和「问过了、确实没有」写成同一个值，于是详情页从一本空账本开工，
      // 800ms 后照「原样放回去」的老规矩，把这本空的写回服务端——**盖掉用户已经确认过的
      // 全部决策**。所以这里包一层 `{ s }`：外层 null = 没问出来，详情页据此自己再去问一次
      // （另一半保险在 Result 里：不知道服务端有什么就一个字都不写）。
      // 与另外两个仍然不同：稿子和复核清单取不到必须弹回列表，带着空清单进详情等于
      // 「放心导出」伪装；决策取不到不该挡住看稿。
      // 加工状态也一起取：它**不依赖**稿子和复核清单，串在后面跑纯属白等——
      // 实测「点开一篇转录到右栏安定」5.5 秒里有 1.8 秒是它在排队（2026-08-22 巡检）。
      // ⚠️ 包一层 `{ s }`：取到「没有加工任务」是 null，而**没问出来**也得能表示，
      // 两者不能混——混了的话一次 500 就会让一篇加工完的稿子摆出「开始加工」的样子。
      // 外层 null = 没问出来 → 详情页自己去问（＝改动前的行为）。
      const [segments, review, rs, pp] = await Promise.all([
        getResult(item.id),
        getReview(item.id),
        getReviewState(item.id).then((s) => ({ s })).catch(() => null),
        getPostprocess(item.id).then((s) => ({ s })).catch(() => null),
      ]);
      setDetailSrc({ item, segments, review, rs, pp });
    } catch {
      setDetailSrc(null);
      setPage("history");
    } finally {
      setDetailLoading(false);
    }
  };

  // 充值：登录态走后端（开发模式直充 / Stripe 跳支付页）；演示模式本地加
  const doTopup = async (amt: number) => {
    if (!signedIn) {
      setBalance((b) => b + amt);
      setShowTopUp(false); setTopUpPrefill(undefined);
      return;
    }
    try {
      const r = await topupApi(Math.round(amt * 100));
      if (r.checkoutUrl) { window.location.href = r.checkoutUrl; return; }
      if (r.balanceCents != null) setBalance(r.balanceCents / 100);
      getLedger().then(setLedger).catch(() => {});
    } catch { /* 失败保持弹窗关闭前的余额，不乱加 */ }
    setShowTopUp(false); setTopUpPrefill(undefined);
  };

  // 账本 → 账单浮窗的两个列表
  const charges: BillingLine[] = ledger
    .filter((l) => l.kind === "charge" || l.kind === "pp_charge")
    .map((l) => ({
      n: (l.kind === "pp_charge" ? "✦ " : "") + (l.fileName ?? "未命名音频"),
      d: fmtDate(l.createdAt),
      dur: l.durationSec != null ? fmtClock(l.durationSec) : "—",
      lang: l.lang ?? "zh",
      cost: Math.abs(l.amountCents) / 100,
    }));
  // 退款要跟着那笔充值一起露出来（refundedCents）。此前这里只取 topup、退款行整条丢掉，
  // 于是一笔被退回的充值在账单上仍写着「成功」并带着「开票」按钮——用户只看到余额少了、
  // 不知道为什么，而那笔已退的钱还能开票（2026-08-22 巡检实见）。
  // 推荐礼金（kind='gift'）也进这张表：它加的是同一个余额，用户在「充值」页找不到会以为没到账。
  // 但它不是你付的钱：不开票、不退款，行上标成礼金（BillingModal 按 gift 标志分开画）。
  const topups: TopupRecord[] = [
    // 在路上的排最前：它还不是账本里的一笔，只是告诉人「有钱正在到」
    ...pendingTopups.map((p) => ({ id: `pending:${p.txnId}`, d: fmtDate(p.createdAt), amount: p.amountCents / 100, pending: true })),
    ...ledger
      .filter((l) => l.kind === "topup" || l.kind === "gift")
      .map((l) => ({ id: String(l.id), d: fmtDate(l.createdAt), amount: l.amountCents / 100,
                     invoiced: l.invoiced, refunded: (l.refundedCents ?? 0) / 100, gift: l.kind === "gift" })),
  ];

  // 登录后浏览器标签页的标题一直是营销站那句英文（`document.title` 进应用后不再更新），
  // 开着好几个标签页时分不清哪个是应用（2026-08-22 巡检）。
  // 文案直接抄侧边栏那几条，别另起一套——同一个东西两个名字比没名字更糟。
  useEffect(() => {
    const name: Record<Page, string> = {
      new: L("新建转录", "New transcription"),
      history: L("我的转录", "My transcripts"),
      detail: detailSrc && detailSrc !== "live" ? detailSrc.item.n : L("转录详情", "Transcript"),
      glossary: L("术语库", "Glossary"),
      postprocess: L("脱敏规则", "Redaction rules"),
      admin: L("运营驾驶舱", "Operations"),
    };
    document.title = `${name[page]} · Transcribe.`;
  }, [page, detailSrc, L]);

  // ⚠️ **必须先确认拉回来了**（2026-09-01）：`jobs`/`ledger` 的初值都是 `[]`，
  // 而「空数组」在这里被当成断言用——「你还什么都没有」。不加载完就下这个断言的话，
  // 刚登录那半秒里点开「我的转录」看到的是「这里还很安静」+「上传第一个文件 →」，
  // 半秒后整页换成一张有几十行的列表。这是上传页那次横跳的同一个毛病：
  // **界面在猜一个还没回来的事实**，而猜错的代价是我们对用户说了句假话。
  // 同一条思路已经在术语库整页与后处理配置页用过（都是 `loaded &&` 之后才敢摆空态）。
  const firstRun = signedIn ? jobsLoaded && ledgerLoaded && ledger.length === 0 && jobs.length === 0 : true;

  // 详情页要渲染的 Result props
  const detailProps = (() => {
    if (detailSrc === "live" && live) {
      return {
        lang: live.lang,
        job: { file: { name: live.name, size: "", duration: live.durationSec != null ? fmtClock(live.durationSec) : "" }, min: live.durationSec != null ? live.durationSec / 60 : 0 },
        jobId: flow.jobId,
        segments: flow.segments ?? [],
        metrics: flow.metrics,
        review: flow.review ?? [],
      };
    }
    if (detailSrc && detailSrc !== "live") {
      const { item, segments, review, rs, pp } = detailSrc;
      const sec = toSec(item.dur === "—" ? "0" : item.dur);
      return {
        lang: item.lang,
        job: { file: { name: item.n, size: "", duration: item.dur === "—" ? "" : item.dur }, min: sec / 60 },
        jobId: item.id,
        segments,
        metrics: { durationSec: sec || undefined },
        review,
        reviewState: rs ? rs.s : undefined,   // undefined = 没问出来 → 详情页自己去问
        ppInitial: pp ? pp.s : undefined,
      };
    }
    return null;
  })();

  // 放在所有 hook 之后：该劝退且未点「仍要继续」时，整个应用换成劝退屏
  if (narrowGate && !narrowAck)
    return <NarrowScreenNotice gate={narrowGate} onContinue={() => setNarrowAck(true)} />;

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        background: "transparent",
        color: semantic.text.primary,
        display: "flex",
        flexDirection: "row",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <Sidebar
        active={page}
        email={me?.email ?? null}
        onNav={(id) => go(id as Page)}
        onOpenBilling={() => { setShowBilling(true); refreshAccount(); }}   /* 打开即重拉：账本只在挂载/跑完时取过，中间新增的笔数看不见 */
        onOpenSettings={() => setShowSettings(true)}
        onLogout={onLogout}
        balance={balance}
        pendingTopupCents={pendingTopups.reduce((s, p) => s + p.amountCents, 0)}
        justCreditedCents={justCredited}
        freeLeftSeconds={free.leftSeconds}
        glossaryCount={glossaries?.length}
        isAdmin={me?.isAdmin}
      />

      {/* 右侧内容区：各页面在此纵向铺满。
          横向 auto 是**窄窗口的兜底**：各页面用固定像素栅格（上传页 468px 卡、术语库 220+300 两侧栏、
          详情页 360px 复核栏），窗口比它们窄时原本会被这里的 overflow:hidden 裁掉——既看不见也
          滑不出来。改成可横滑后放不下就左右滑，这是通行做法（桌面窗口拖窄不弹任何提示，见
          NarrowScreenNotice 的判据说明）。纵向仍交给各页面自己的滚动容器。
          历史页不靠这层：它的表格自己横滑，表头 sticky、侧栏与筛选 tab 不跟着移。 */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflowX: "auto", overflowY: "hidden", position: "relative" }}>
      {page === "new" && (
        <MainPage
          flow={flow}
          balance={balance}
          onTopUp={openTopUp}
          onStartJob={startJob}
          glossaries={glossaries ?? []}
          selectedGlossaryId={selectedGlossaryId}
          onSelectGlossary={onSelectGlossary}
          onOpenGlossary={() => go("glossary")}
          freeLeftSeconds={free.leftSeconds}
          freeLimited={free.limited}
          recent={jobsLoaded ? historyItems : undefined}
          onOpenRecent={(it) => { void openHistoryItem(it as HistoryItem & { id: string }); }}
          onOpenHistory={() => go("history")}
        />
      )}

      {page === "history" && (
        <HistoryPage
          loaded={jobsLoaded}
          empty={firstRun && !liveRow && historyItems.length === 0}
          items={historyItems}
          liveRow={liveRow}
          onNew={() => go("new")}
          onOpenLive={() => { setDetailSrc("live"); go("detail"); }}
          onOpen={(f) => { void openHistoryItem(f as HistoryItem & { id: string }); }}
          onRetry={async (f) => {
            await retryJob((f as HistoryItem & { id: string }).id);
            // 新单一进列表，上面那个「有在途任务就轮询」的 effect 自己会起来跑进度。
            // 不用 useFlow 接管：它只跟最后一次**上传**，而重试没有上传这一步。
            setJobs(await listJobs());
            refreshAccount();   // 重试要重新预扣，余额得跟着变
          }}
        />
      )}

      {page === "glossary" && (
        <GlossaryPage
          glossaries={glossaries}
          onCreate={onCreateGlossary}
          onUpdate={onUpdateGlossary}
          onDelete={onDeleteGlossary}
        />
      )}

      {page === "postprocess" && <PostprocessPage />}

      {page === "admin" && <AdminPage />}

      {page === "detail" && (detailProps ? (
        <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: layout.pagePad, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {/* key=任务 id：切换任务时强制重挂载 Result，重置其全部内部态（全文 segs、复核确认/删除等），
              否则 React 复用同一实例，旧任务的全文会残留到新任务详情页 */}
          <Result key={detailProps.jobId ?? "live"} onBack={() => go("history")} onPostprocessDone={refreshAccount} {...detailProps} />
        </div>
      ) : detailLoading ? (
        <DetailSkeleton />
      ) : null)}

      </div>

      {leaveTo != null && (
        <UnsavedLeaveDialog
          onStay={() => setLeaveTo(null)}
          onDiscard={() => { const next = leaveTo; setLeaveTo(null); setPage(next); }}
          onSaveAndLeave={async () => {
            const g = pendingUnsaved();
            const ok = g ? await g.save() : true;
            if (ok) { const next = leaveTo; setLeaveTo(null); setPage(next); }
            // 没存成（重名 / 网络）：留在原页，页面自己会显示原因
          }}
        />
      )}

      <BillingModal
        open={showBilling}
        onClose={() => setShowBilling(false)}
        balance={balance}
        onTopUp={() => { setShowBilling(false); openTopUp(); }}
        empty={firstRun && charges.length === 0 && topups.length === 0}
        charges={charges}
        topups={topups}
        onInvoice={signedIn ? (ids) => {
          void markInvoiced(ids.map(Number)).then(() => getLedger().then(setLedger).catch(() => {}));
        } : undefined}
      />

      <SettingsModal
        open={showSettings}
        onClose={() => setShowSettings(false)}
        email={me?.email ?? null}
        balance={balance}
        onTopUp={() => { setShowSettings(false); openTopUp(); }}
        glossaryCount={glossaries?.length ?? 0}
        referral={referral}
        jobCount={jobs.length}
        onManageGlossary={() => { setShowSettings(false); go("glossary"); }}
        onPurged={() => { setJobs([]); setDetailSrc(null); setPage("new"); refreshAccount(); }}
        onDeleted={() => { setShowSettings(false); onLogout(); }}
      />

      {/* 打开时才挂载：prefill 是 useState 初值，重挂才能捡到每次软墙带来的新缺口 */}
      {showTopUp && (
        <TopUpModal
          open
          balance={balance}
          prefill={topUpPrefill}
          onClose={() => { setShowTopUp(false); setTopUpPrefill(undefined); }}
          onDone={(amt) => { void doTopup(amt); }}
        />
      )}
    </div>
  );
}

/** 「还有没保存的改动」三选一：保存并离开 / 直接离开 / 留下。Esc = 留下。 */
function UnsavedLeaveDialog({ onStay, onDiscard, onSaveAndLeave }: {
  onStay: () => void; onDiscard: () => void; onSaveAndLeave: () => Promise<void>;
}) {
  const L = useL();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onStay(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onStay]);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tx-unsaved-title" onClick={onStay}
      style={{ position: "absolute", inset: 0, background: semantic.surface.overlay, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 120, padding: 24 }}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ width: "100%", maxWidth: 440, background: semantic.surface.raised, borderRadius: radius.lg, boxShadow: shadow.lg, padding: "22px 24px 20px" }}>
        <div id="tx-unsaved-title" style={{ ...ttype.h2, marginBottom: 8 }}>{L("还有没保存的改动", "Unsaved changes")}</div>
        <p style={{ margin: "0 0 18px", fontSize: 14, lineHeight: 1.6, color: semantic.text.secondary }}>
          {L("直接离开会丢掉刚才的修改。", "Leaving now discards what you just typed.")}
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <Button ghost onClick={onStay}>{L("留下", "Stay")}</Button>
          <Button ghost onClick={onDiscard}>{L("直接离开", "Leave without saving")}</Button>
          <Button disabled={busy} onClick={() => { setBusy(true); void onSaveAndLeave().finally(() => setBusy(false)); }}>{L("保存并离开", "Save and leave")}</Button>
        </div>
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback, useRef } from "react";
import { useFullHeightScreen } from "./lib/fullHeightScreen";
import { pendingUnsaved } from "./lib/unsavedGuard";
import { Button } from "./components/Button";
import { useL } from "./lib/i18n";
import { semantic, layout, radius, shadow, type as ttype } from "./styles/tokens";
import { useFlow, etaMinutes, observedClimb } from "./lib/flow";
import { fmtClock, toSec } from "./lib/format";
import { getPostprocess, getResult, getReview, getReviewState, listJobs, retryJob, listGlossaries, createGlossary, updateGlossary, deleteGlossary, type Glossary, type JobRow, type Me, type PostprocessStatus, type ReviewStateBlob, type TranscriptRow } from "./lib/api";
import type { ReviewItem } from "./lib/reviewData";
import type { HistoryItem } from "./lib/sampleData";
import { Sidebar } from "./components/Sidebar";
import { MainPage } from "./screens/main/MainPage";
import { HistoryPage } from "./screens/HistoryPage";
import { AdminPage } from "./screens/AdminPage";
import { GlossaryPage } from "./screens/main/GlossaryPage";
import { PostprocessPage } from "./screens/main/PostprocessPage";
import { Result } from "./screens/main/Result";
import { DetailSkeleton } from "./screens/main/DetailSkeleton";
import { SettingsModal } from "./screens/SettingsModal";
import { NarrowScreenNotice, useNarrowGate } from "./screens/NarrowScreenNotice";

// 本机版（与线上不同）：去掉余额、免费额度、充值与账单浮窗、在路上的充值轮询、推荐、退出登录。
// 本机单用户、不收费，音频和稿子不自动删（没有「超 30 天过期」）。其余流程与线上逐行一致。

export type Page = "new" | "history" | "detail" | "glossary" | "postprocess" | "admin";

interface AppShellProps {
  me: Me | null;
}

// ISO 时间 → 列表显示用短日期
const fmtDate = (iso: string) => {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  const md = `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return { zh: md, en: md };
};

const jobToItem = (j: JobRow): HistoryItem & { id: string } => ({
  id: j.id,
  n: j.fileName ?? "未命名音频",
  d: fmtDate(j.createdAt),
  dur: j.durationSec != null ? fmtClock(j.durationSec) : "—",
  cost: 0,   // 本机不收费
  lang: j.lang ?? "zh",
  st: j.status === "done" ? "done" : j.status === "failed" ? "failed" : j.status === "queued" ? "queued" : "processing",
  prog: j.progress,
  expired: false,   // 本机不自动删内容，没有过期
  error: j.error,   // 失败原因（后端脱敏话术），失败行给用户展示
  pp: j.postprocess ?? null,   // 后处理状态（行内 caption 三态）
});

export function AppShell({
  me,
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
  const [showSettings, setShowSettings] = useState(false); // 设置浮窗（不是页面）
  // 术语库（多本，后端持久化）。selectedGlossaryId = 下次转录默认用哪本（localStorage 记住上次选择）
  // null = 还没取回来。**不能用空数组表示未知**：术语库页会据此摆出「还没有术语库」
  // 的空态屏（连「新建第一本」的按钮一起），有三本库的人先看到它、再翻成列表。
  const [glossaries, setGlossaries] = useState<Glossary[] | null>(null);
  const [selectedGlossaryId, setSelectedGlossaryId] = useState<string | null>(
    () => localStorage.getItem("glossaryId"));
  const [jobs, setJobs] = useState<JobRow[]>([]);
  // 「任务列表拉回来了没有」——只服务上传页左栏的首帧布局（见 Idle 里 twoCol 的注释）。
  // 分不清「还不知道」与「确实一份都没有」的话，有历史的老用户每次进上传页都会先看到
  // 单栏居中、再跳成两栏。⚠️ 只在**首次**拉回时置位，之后的轮询不再动它。
  const [jobsLoaded, setJobsLoaded] = useState(false);
  // 当前这次转录的展示信息（开始时记下，flow 非 idle 时它就是「我的转录」列表里的实时一行）
  const [live, setLive] = useState<{ name: string; lang: string; durationSec: number | null } | null>(null);
  // 详情页看的是哪条："live"=本次转录；历史条目 = 已加载的真实数据
  const [detailSrc, setDetailSrc] = useState<"live" | { item: HistoryItem & { id: string }; segments: TranscriptRow[]; review: ReviewItem[]; rs: { s: ReviewStateBlob } | null; pp: { s: PostprocessStatus | null } | null } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const flow = useFlow();

  // 刷新任务列表与术语库
  const refreshAccount = useCallback(() => {
    if (!signedIn) return;
    listJobs().then((j) => { setJobs(j); setJobsLoaded(true); }).catch(() => {});
    listGlossaries().then((gs) => {
      setGlossaries(gs);
      // 默认选中：localStorage 上次**显式**选过的那本（若还在）→ 否则「不使用」。
      // ⚠️ **绝不替用户挑一本**：挂错库不报错，它会把近音词悄悄拽向那本库所属行业的写法。
      setSelectedGlossaryId((cur) => (cur && gs.some((g) => g.id === cur)) ? cur : null);
    // 取失败也要落地成 []：**未知不能是永久的**，否则术语库页会一直卡在骨架上
    }).catch(() => setGlossaries([]));
  }, [signedIn]);
  useEffect(() => { refreshAccount(); }, [refreshAccount]);

  // 术语库 CRUD：写后端后刷新本地列表（别信本地态，看后端读回——历史踩过坑）
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

  // 转录结束：刷新列表
  useEffect(() => {
    if (flow.state !== "done" && flow.state !== "error") return;
    refreshAccount();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow.state]);

  // 上传落库那一刻**强制重拉一次**列表。提交后页面直接跳「我的转录」，而 `page` 那条刷新在
  // **上传还没传完**时就跑了，拉回来的是提交之前的快照——刚交的那单不在列表里，看上去就像
  // 「没提交成功」，于是再交一遍。jobId 是「后端已经收下这一单」的唯一确证，所以刷新挂在它身上。
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
  // 不会触发上面的轮询——不刷会看不到「✦ 已加工」caption
  useEffect(() => {
    if (signedIn && page === "history") listJobs().then(setJobs).catch(() => {});
  }, [signedIn, page]);

  // 上传 + 开始：记下本次任务、开跑、直接进「我的转录」
  const startJob = (file: File, lang: string, durationSec: number | null) => {
    setLive({ name: file.name, lang, durationSec });
    flow.start(file, lang, durationSec, selectedGlossaryId);
    setPage("history");
  };

  // flow 非 idle 时，「我的转录」列表顶部的实时一行
  const liveRow: HistoryItem | null = live && flow.state !== "idle" ? {
    n: live.name,
    d: { zh: "刚刚", en: "Just now" },
    dur: live.durationSec != null ? fmtClock(live.durationSec) : "—",
    cost: 0,
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
  // 「可离开」邀请用户刷新回来，回来必须还在爬：按 observedClimb 从首次观察起算。
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
    // ⚠️ **先清空上一单**：不清的话点开 B 会先原样显示半秒 A 的详情页，再跳成 B。清空后走骨架屏。
    setDetailSrc(null);
    setDetailLoading(true);
    setPage("detail");
    try {
      // 稿子、复核清单、复核决策、加工状态一起取（原委见线上同处注释）：
      // 稿子和清单取不到必须弹回列表；决策与加工状态包一层 `{ s }`，外层 null = 没问出来、详情页自己再问。
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

  // 浏览器标签页标题：文案直接抄侧边栏那几条，别另起一套——同一个东西两个名字比没名字更糟。
  useEffect(() => {
    const name: Record<Page, string> = {
      new: L("新建转录", "New transcription"),
      history: L("我的转录", "My transcripts"),
      detail: detailSrc && detailSrc !== "live" ? detailSrc.item.n : L("转录详情", "Transcript"),
      glossary: L("术语库", "Glossary"),
      postprocess: L("脱敏规则", "Redaction rules"),
      admin: L("运行面板", "System status"),
    };
    document.title = `${name[page]} · Transcribe Local`;
  }, [page, detailSrc, L]);

  // ⚠️ **必须先确认拉回来了**：`jobs` 初值是 `[]`，而「空数组」在这里被当成断言用——「你还什么都没有」。
  // 不加载完就下这个断言的话，刚打开那半秒里点「我的转录」会先看到空态、再换成一张列表。
  const firstRun = signedIn ? jobsLoaded && jobs.length === 0 : true;

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
        onNav={(id) => go(id as Page)}
        onOpenSettings={() => setShowSettings(true)}
        glossaryCount={glossaries?.length}
        isAdmin={me?.isAdmin}
      />

      {/* 右侧内容区：各页面在此纵向铺满。横向 auto 是窄窗口的兜底（原委见线上同处注释）。 */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflowX: "auto", overflowY: "hidden", position: "relative" }}>
      {page === "new" && (
        <MainPage
          flow={flow}
          onStartJob={startJob}
          glossaries={glossaries ?? []}
          selectedGlossaryId={selectedGlossaryId}
          onSelectGlossary={onSelectGlossary}
          onOpenGlossary={() => go("glossary")}
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
            setJobs(await listJobs());
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
          {/* key=任务 id：切换任务时强制重挂载 Result，重置其全部内部态 */}
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

      <SettingsModal
        open={showSettings}
        onClose={() => setShowSettings(false)}
        jobCount={jobs.length}
        onPurged={() => { setJobs([]); setDetailSrc(null); setPage("new"); refreshAccount(); }}
      />
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

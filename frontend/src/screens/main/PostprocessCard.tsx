// PostprocessCard.tsx — 转录详情页 · 后处理接力卡（五态）
// 对照稿：docs/design/postprocessing/README.md §1 + 后处理 细化稿.dc.html A 区
// 挂点：Result.tsx 复核侧栏，绿条幅/进度卡之下、「已确认实体」页脚之上。
// 五态同一卡位原地切换：锁定（队列未清零）/ 可发起 / 进行中 / 完成 / 失败。
// 2026-08-17 下架「归类」：可发起态不再有「无方案变体」，但**展示侧保留 categorize 的名字**——
// 下架前跑完的存量任务，步骤名与产物名还要显示得出来。
// 进度：后端只给步索引；步内 % 由前端匀速模拟（转录进度先例 lib/flow climbProgress 的思路），步间硬切换。

import { useErrText } from "../../lib/userErrors";
import { useEffect, useRef, useState } from "react";
import { semantic, fonts, radius, shadow, motion, space } from "../../styles/tokens";
import { useL, useUILang } from "../../lib/i18n";
import { ppCostFor } from "../../lib/pricing";
import { countRedactLines } from "../../lib/redactList";
import { RedactScopeHelp } from "./RedactScope";
import { RedactChanges, type RedactOverrideState } from "./RedactChanges";
import { downloadUrl } from "../../lib/download";
import {
  getPostprocess, getPpChanges, type PpChanges, startPostprocess, listRedactLists, ppProductUrl, ppQcUrl,
  type PostprocessStatus, type PpStep, type PpStepLegacy, type RedactList,
} from "../../lib/api";

interface PostprocessCardProps {
  jobId: string | null;
  /** 脱敏改动清单「点一处回到正文那一段」：详情页往下透传，其它场景不给就只列不跳 */
  onLocate?: (seg: number) => void;
  locked: boolean;        // 复核队列未清零
  remaining: number;      // 还差 N 处确认（锁定态 pill）
  /** 音频时长（秒）——2026-08-02 改价后按「每步加价率 × 分钟」预估价（缺失时退化为按小时的加价率展示） */
  durationSec?: number | null;
  /** 外层（AppShell）与稿子并行取回来的加工状态。给了就直接开工，不再自己打一次接口。
   *  `undefined` = 外层没问出来（演示态 / 刚跑完的实时任务 / 请求失败）→ 本组件自己去问。
   *  `null` = 问过了，这单没有加工任务。 */
  initialStatus?: PostprocessStatus | null;
  /** 加工完成时回调：完成即扣款，外层据此刷新余额（不接的话侧栏停在扣款前的旧数字） */
  onFinished?: () => void;
  /** 音频文件名（去扩展名）——下载的产物一律叫「音频名-后缀」。
   *  不给就退回后端缺省名（「脱敏稿.md」），那时几单的产物下下来是同一个名字，分不出属于谁。 */
  stem?: string;
}

const STEP_ORDER: PpStep[] = ["narrate", "redact"];
// 步内匀速爬升：每步按 10 分钟估算爬到 99% 等真实完成（封顶不假装完成；与「通常 5–30 分钟」量级一致）
const STEP_EST_SEC = 600;
const STEP_CEIL = 99;
export function ppStepClimb(elapsedSec: number, estSec: number = STEP_EST_SEC): number {
  return Math.min(STEP_CEIL, Math.floor((elapsedSec / Math.max(estSec, 1)) * STEP_CEIL));
}

const fmtTs = (iso: string) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export function PostprocessCard({ jobId, initialStatus, locked, remaining, durationSec, onFinished, onLocate, stem }: PostprocessCardProps) {
  // 脱敏改口状态由 RedactChanges 回报上来，供下载行显示与禁用（见 ProductRow 文件头）
  const [redactOvr, setRedactOvr] = useState<RedactOverrideState | null>(null);
  const L = useL();
  const errText = useErrText();
  const { uiLang } = useUILang();
  // 两张表都保留 categorize：归类下架前跑完的存量任务还带着它，摘掉这一支会让老单显示裸英文
  const stepLabel = (s: string) =>
    s === "narrate" ? L("视角转换", "Narrative") : s === "categorize" ? L("归类", "Categorize") : s === "redact" ? L("脱敏", "Redact") : s;
  const productLabel = (s: PpStepLegacy) =>
    s === "narrate" ? L("叙述稿", "Narrative draft") : s === "categorize" ? L("归类纪要", "Categorized minutes") : L("脱敏稿", "Redacted transcript");
  // 文件名后缀 = 界面上那一步的名字，两者同源（2026-08-30 合并）。
  // 此前后缀单独走「中/英二选一」，理由是「德语界面下到 xx-Narrativer Entwurf.md、
  // 而文件里写的是 Interviewer」——那个理由**已经不成立**：文件里的说话人标签现在也是 8 门。
  // 规则统一成一条：我们写的字都跟界面语言走，录音内容不翻。
  // 下载名统一成「音频名-后缀」，后缀就是界面上那一步的名字（转录/视角转换/脱敏）。
  // 同名文件下第二遍由浏览器自己接「(1)」——我们不能替它编号：文件在不在用户的下载目录里，
  // 只有浏览器知道，我们编出来的 (2) 很可能是个谎。
  // 拿不到音频名就**不传名字**，让后端用它的缺省名——不能只拿后缀当文件名：
  // 「脱敏.md」和原来的「脱敏稿.md」一样分不出单，却让人以为已经带了标识。
  const fileName = (suffix: string) => (stem ? `${stem}-${suffix}` : undefined);

  // ── 任务状态（null = 无任务；undefined = 尚未取到）──
  // 初值取外层并行拿到的那份（Result 由 jobId 做 key，换一单必重挂载，所以初值不会串单）
  const [st, setSt] = useState<PostprocessStatus | null | undefined>(initialStatus);
  const [forceSetup, setForceSetup] = useState(false); // 失败态「改选处理」回可发起
  // 脱敏改动清单：**跟加工状态一起取**，两者齐了才画完成态。
  // 留给 RedactChanges 自己取的话，完成态会分两步出现：先「加工完成 + 两个产物行」，
  // 几百毫秒后才冒出「脱敏了 N 处」「已按你的 N 处修订」和质检那句说明（2026-08-22 Duner 实见）。
  // undefined = 还没取；null = 不需要（这单还没跑完 / 没有脱敏产物）。
  // 这两者的区别正好实现了「只在一进来就是完成态时才等齐」：
  // 加工跑完的那一刻 changes 已经是 null（跑的过程中设的），闸放行——用户正盯着进度条，
  // 从「进行中」退回骨架比多冒一行更难看，那时多出来的是补充，不是纠正。
  const [changes, setChanges] = useState<PpChanges | null | undefined>(undefined);
  const pollRef = useRef<number | null>(null);
  // 「开始加工」用它把主轮询器踢起来（自增即触发下面那个 effect 重跑）。
  // 之所以不直接在 doStart 里 setInterval：那等于复制一份主轮询器，而复制品必然漏东西。
  const [restart, setRestart] = useState(0);
  useEffect(() => {
    // stop 提到最前：下面几条早退路径也要带上清理——「开始加工」是自己另开的定时器
    // （doStart），早退路径不还这一手的话，卸载后它会继续空打后端。
    const stop = () => { if (pollRef.current !== null) { clearInterval(pollRef.current); pollRef.current = null; } };
    if (!jobId) { setSt(null); return stop; }        // 演示态：确实没有任务，null 是事实
    // ⚠️ **复核没清零时不许 setSt(null)**：null 的意思是「问过了，没有加工任务」，
    // 而此刻我们根本没问。解锁的那一刻 st 还挂着这个假的 null，于是可发起态**立刻**渲染出来
    // ——一份早就加工完的稿子，界面却摆出「选要做的处理 / 预估金额 / 开始加工」，
    // 三秒后才翻成「加工完成」。这不只是闪一下：它在诱导用户再花一次钱
    // （2026-08-22 Duner 连拍四张实见）。保持 undefined＝未知，未知就渲染骨架、不作任何断言。
    if (locked) return stop;
    // 外层已经给了终态（无任务 / 已完成 / 已失败）→ 没有可等的了，别再打一次同样的接口。
    // 在途的（queued/running）仍要轮询，否则进度条不会动。
    // ⚠️ `restart === 0` 这一半不能省：`initialStatus` 是外层进详情页时取的那一份，**发起加工后它不会变**。
    // 少了它，「这单本来没有加工任务（null）」会让刚发起的那次轮询在第一拍就被挡回去——
    // 进度条不动、完成了也不通知外层刷余额，正是这次要修的那个毛病换个姿势复现。
    if (restart === 0 && (initialStatus === null || initialStatus?.status === "done" || initialStatus?.status === "failed")) return stop;
    let alive = true;
    const tick = () => {
      getPostprocess(jobId)
        .then((s) => {
          if (!alive) return;
          setSt((prev) => {
            // 跑完的那一刻通知外层刷新余额：加工完成即扣款（pp_charge），不刷的话侧栏
            // 一直显示扣款前的旧数字——用户刚花了钱却看见余额没动（2026-08-07 生产实测）。
            // ⚠️ `prev !== undefined` 这一半不能省：undefined 是「还没问过」，不是「问过了、
            // 当时没跑完」。少了它，**每打开一篇早就加工完的转录都会当成「刚跑完」**，
            // 白跑一次全账户刷新（me/jobs/ledger/glossaries 四个请求，2026-08-22 巡检实测）。
            if (s?.status === "done" && prev !== undefined && prev?.status !== "done") onFinished?.();
            return s;
          });
          if (s == null || s.status === "done" || s.status === "failed") stop(); // 无任务/终态停轮询
        })
        .catch(() => {
          if (!alive) return;
          // 404=尚无任务是正常空态：停轮询（doStart 成功后会重开），别每 3s 空打后端刷 404
          setSt((cur) => cur ?? null);
          stop();
        });
    };
    tick();
    pollRef.current = window.setInterval(tick, 3000);
    return () => { alive = false; stop(); };
  }, [jobId, locked, initialStatus, onFinished, restart]);

  const hasRedact = !!st && st.status === "done" && (st.products ?? []).some((p) => p.kind === "redact");
  useEffect(() => {
    if (!jobId || !st) return;
    if (!hasRedact) { setChanges(null); return; }
    let alive = true;
    // 取不到就当空清单：这一块是知情用的锦上添花，不该把整张卡卡在骨架上
    getPpChanges(jobId)
      .then((r) => { if (alive) setChanges(r); })
      .catch(() => { if (alive) setChanges({ changes: [], overrides: [] }); });
    return () => { alive = false; };
  }, [jobId, hasRedact, st]);

  // ── 可发起态的配置数据与勾选 ──
  const [lists, setLists] = useState<RedactList[]>([]);
  const [checked, setChecked] = useState<Record<PpStep, boolean>>({ narrate: true, redact: false });
  const [redactListId, setRedactListId] = useState<string | null>(null); // null = 不使用清单 · 智能识别
  const [menu, setMenu] = useState<"list" | null>(null);
  const [startErr, setStartErr] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const inSetup = !locked && (st === null || (st?.status === "failed" && forceSetup));
  useEffect(() => {
    if (!inSetup) return;
    let alive = true;
    listRedactLists().then((ls) => { if (alive) setLists(ls); }).catch(() => {});
    return () => { alive = false; };
  }, [inSetup]);

  // 拼音排序（README 下拉规格）
  const cmp = (a: string, b: string) => a.localeCompare(b, "zh-Hans-CN");
  const sortedLists = [...lists].sort((a, b) => cmp(a.name, b.name));

  // 失败态「改选处理」：预选上次组合（清单按名字回配——状态里只有名）。
  // **已下架的步骤要滤掉**：存量失败单的 steps 里可能有 categorize，
  // 不滤的话勾选态凭空多一项、发起时后端 422（normalize_steps 不做兼容放行）。
  const openSetupFrom = (s: PostprocessStatus) => {
    setChecked({
      narrate: s.steps.includes("narrate"),
      redact: s.steps.includes("redact"),
    });
    setForceSetup(true);
  };
  useEffect(() => {
    if (!inSetup || st?.status !== "failed") return;
    if (st.listName) {
      const m = lists.find((l) => l.name === st.listName);
      setRedactListId(m?.id ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inSetup, lists]);

  // ── 步内进度模拟：步索引变化即重新起爬（步间硬切换）──
  const stepAnchor = useRef<{ idx: number; at: number } | null>(null);
  const running = st?.status === "running" || st?.status === "queued";
  if (st && running) {
    if (!stepAnchor.current || stepAnchor.current.idx !== st.stepIndex) {
      stepAnchor.current = { idx: st.stepIndex, at: Date.now() };
    }
  } else {
    stepAnchor.current = null;
  }
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setTick((n) => n + 1), 500);
    return () => clearInterval(t);
  }, [running]);
  const stepPct = st?.status === "running" && stepAnchor.current
    ? ppStepClimb((Date.now() - stepAnchor.current.at) / 1000)
    : 0;

  const doStart = async (steps: PpStep[], lid: string | null) => {
    if (!jobId || steps.length === 0 || starting) return;
    setStarting(true); setStartErr(null);
    try {
      await startPostprocess(jobId, {
        steps,
        ...(steps.includes("redact") ? { redactListId: lid } : {}),
        // 质检报告里我们写的字跟界面语言走（产物正文跟录音语种走）——见 pipeline/pp_lang.py
        uiLang,
      });
      setForceSetup(false);
      const s = await getPostprocess(jobId).catch(() => null);
      setSt(s ?? { status: "queued", steps, stepIndex: 1, totalSteps: steps.length, currentStep: steps[0], products: [], qcFixCount: 0, hasQcReport: false, priceCents: Math.round(ppCostFor(steps, durationSec ?? 0) * 100), updatedAt: new Date().toISOString() });
      // 轮询若已被终态停掉，这里重开——**让主轮询器重跑，别在这里自己另开一个**。
      // 2026-08-26 之前这里复制了一份 setInterval，而那份复制品漏了主轮询器的两件事：
      //   ① 不调 onFinished ⇒ 加工完成即扣款，外层却不刷新，**用户刚花完钱看到的还是旧余额**；
      //   ② 到终态不停 ⇒ 只要卡片还挂着就每 3 秒空打一次后端，永远不停。
      // 主轮询器里正好有一整段注释在讲 ① 为什么必须做，复制的时候没带上。
      setRestart((n) => n + 1);
    } catch (e) {
      setStartErr(errText(e));
    } finally {
      setStarting(false);
    }
  };

  // 失败重试：同一组合从头跑。**先滤掉已下架的步骤**——存量失败单可能带 categorize，
  // 原样重发会被后端 422；滤完若一步不剩，就回可发起态让用户重新选。
  const retry = () => {
    if (!st) return;
    const steps = STEP_ORDER.filter((k) => (st.steps as string[]).includes(k));
    if (steps.length === 0) { openSetupFrom(st); return; }
    void doStart(steps, lists.find((l) => l.name === st.listName)?.id ?? null);
  };

  // ── 通用卡壳 ──
  const shell = (children: React.ReactNode, opt?: { accentBorder?: boolean; dashed?: boolean }) => (
    <div style={{
      background: semantic.surface.raised,
      border: opt?.dashed ? `1px dashed ${semantic.border.strong}` : `1px solid ${opt?.accentBorder ? semantic.accent.brand : semantic.border.default}`,
      borderRadius: radius.md,
      padding: opt?.dashed ? "14px 16px" : 16,
      boxShadow: opt?.accentBorder ? shadow.md : "none",
      flex: "0 0 auto",
    }}>
      {children}
    </div>
  );

  // ══ 锁定态 ══
  if (locked) {
    return shell(
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: semantic.text.muted }}>✦ {L("后处理", "Post-processing")}</span>
          <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.surface.sunken, color: semantic.text.muted, whiteSpace: "nowrap" }}>
            {L.t("还差 {0} 处确认", "{0} left to confirm", remaining)}
          </span>
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.65, color: semantic.text.muted, marginTop: space.s2 }}>
          {L("确认过的稿，才值得加工——清完存疑就能把这份笔录转成叙述稿或脱敏稿。", "A confirmed transcript is worth processing — clear the doubts and turn it into a narrative or a redacted copy.")}
        </div>
      </>,
      { dashed: true },
    );
  }

  // 首次取回前只摆骨架：**未知不是空**。整块消失再冒出来会让右栏跳一下，
  // 而随便摆一个状态则是在撒谎——骨架是唯一诚实又不跳的选项。
  // 完成态还要多等一样东西：脱敏改动清单（见上）。齐了再画，一次到位。
  if (st === undefined || (hasRedact && changes === undefined)) {
    return shell(
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: semantic.text.muted }}>✦ {L("后处理", "Post-processing")}</span>
        </div>
        <div aria-hidden style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: space.s3 }}>
          <span style={{ height: 10, width: "62%", borderRadius: 5, background: semantic.surface.sunken }} />
          <span style={{ height: 10, width: "38%", borderRadius: 5, background: semantic.surface.sunken }} />
        </div>
      </>,
      { dashed: true },
    );
  }

  // ══ 进行中 ══
  if (st && (st.status === "running" || st.status === "queued") && !forceSetup) {
    return shell(
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: semantic.accent.brand, flex: "0 0 auto" }} />
          <span style={{ fontSize: 14, fontWeight: 600 }}>{L.t("加工中 · 第 {0} / {1} 步", "Processing · step {0} / {1}", st.stepIndex, st.totalSteps)}</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted }}>{L("可离开", "safe to leave")}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: space.s2, marginTop: space.s3 }}>
          {st.steps.map((s, i) => {
            const done = i + 1 < st.stepIndex;
            const cur = i + 1 === st.stepIndex;
            return (
              <div key={s}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  {done ? (
                    <span style={{ width: 16, height: 16, borderRadius: "50%", background: semantic.success.graphic, color: semantic.text.onAccent, display: "grid", placeItems: "center", fontSize: 8, flex: "0 0 auto" }}>✓</span>
                  ) : cur ? (
                    <span style={{ width: 16, height: 16, borderRadius: "50%", border: `2px solid ${semantic.accent.brand}`, boxSizing: "border-box", flex: "0 0 auto" }} />
                  ) : (
                    <span style={{ width: 16, height: 16, borderRadius: "50%", border: `1.5px solid ${semantic.border.strong}`, boxSizing: "border-box", flex: "0 0 auto" }} />
                  )}
                  <span style={{ fontSize: 13, fontWeight: cur ? 600 : 400, color: done ? semantic.text.secondary : cur ? semantic.text.primary : semantic.text.ghost }}>
                    {/* 当前步带上它用的那份配置名；归类那支只对存量任务成立 */}
                    {stepLabel(s)}{cur ? (s === "categorize" && st.profileName ? ` · ${st.profileName}` : s === "redact" && st.listName ? ` · ${st.listName}` : "") : ""}
                  </span>
                  <span style={{ flex: 1 }} />
                  {cur && st.status === "running" && (
                    <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.accent.text, fontVariantNumeric: "tabular-nums" }}>{stepPct}%</span>
                  )}
                </div>
                {cur && (
                  <div style={{ height: 4, borderRadius: 2, background: semantic.surface.sunken, marginLeft: 26, marginTop: space.s2, overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${st.status === "running" ? stepPct : 0}%`, background: semantic.accent.brand, borderRadius: 2, transition: `width ${motion.base}` }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: 11, color: semantic.text.muted, marginTop: space.s3 }}>
          {L("通常 5–30 分钟 · 完成后回这里或「我的转录」取产物", "Usually 5–30 min · pick up the results here or in My transcripts")}
        </div>
      </>,
    );
  }

  // ══ 完成态 ══
  if (st && st.status === "done" && !forceSetup) {
    return shell(
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          <span style={{ width: 16, height: 16, borderRadius: "50%", background: semantic.success.graphic, color: semantic.text.onAccent, display: "grid", placeItems: "center", fontSize: 8, flex: "0 0 auto" }}>✓</span>
          <span style={{ fontSize: 14, fontWeight: 600, color: semantic.success.text }}>{L("加工完成", "Processing done")}</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted }}>{fmtTs(st.updatedAt)}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: space.s3 }}>
          {st.products.map((pd) => (
            <ProductRow
              key={pd.kind}
              jobId={jobId}
              kind={pd.kind}
              label={productLabel(pd.kind)}
              sub={pd.kind === "categorize" ? st.profileName ?? null : pd.kind === "redact" ? st.listName ?? null : null}   /* categorize 分支只对存量任务成立 */
              fileName={fileName(productLabel(pd.kind))}   /* 后缀用**产物名**（叙述稿/脱敏稿），不是步骤名——Duner 2026-08-28 定 */
              ovr={pd.kind === "redact" ? redactOvr : null}
            />
          ))}
        </div>
        {st.products.some((p) => p.kind === "redact") && <RedactChanges jobId={jobId} data={changes ?? { changes: [], overrides: [] }} onLocate={onLocate} onState={setRedactOvr} />}
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10, fontSize: 12, color: semantic.success.text }}>
          ✓ {L.t("质检已修复 {0} 处", st.qcFixCount === 1 ? "QC fixed {0} spot" : "QC fixed {0} spots", st.qcFixCount)}
          {st.hasQcReport && jobId && (
            <button className="tx-focus" onClick={() => downloadUrl(ppQcUrl(jobId, fileName(L("质检报告", "QC report"))))}
              style={{ border: "none", background: "transparent", padding: 0, fontSize: 12, color: semantic.accent.text, cursor: "pointer", fontFamily: fonts.sans }}>
              {L("查看报告", "View report")}
            </button>
          )}
        </div>
        {/* 报告是**脱敏当时**的留档，故意不跟着改口一起变（改了就没有原始记录了）。
            但那样报告里写着「山西 → 本省」而稿子里是「山西」——只在真有改口时说明一句。 */}
        {(redactOvr?.count ?? 0) > 0 && st.hasQcReport && (
          <div style={{ fontSize: 11, lineHeight: 1.6, color: semantic.text.muted, marginTop: 4 }}>
            {L("报告记的是脱敏当时的改动，不含你之后的修订。", "The report covers the redaction itself, not the changes you made afterwards.")}
          </div>
        )}
      </>,
    );
  }

  // ══ 失败态 ══
  if (st && st.status === "failed" && !forceSetup) {
    const fl = st.failedStep ? stepLabel(st.failedStep) : null;
    return shell(
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: semantic.accent.text }}>✦ {L("后处理没跑完", "Post-processing didn't finish")}</span>
          <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.accent.bgSoft, color: semantic.danger.fillHover, whiteSpace: "nowrap" }}>
            {L("失败 · 未计费", "Failed · not billed")}
          </span>
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.65, color: semantic.text.secondary, marginTop: space.s2 }}>
          {fl
            ? L.t("「{0}」这一步中断了。稿子原样还在，本次不计费——重试会从头跑所选处理。", "The \"{0}\" step broke off. Your transcript is untouched and this run wasn't billed — retrying runs the selected steps from the start.", fl)
            : L("处理中断了。稿子原样还在，本次不计费——重试会从头跑所选处理。", "The run broke off. Your transcript is untouched and this run wasn't billed — retrying runs the selected steps from the start.")}
        </div>
        {startErr && <div style={{ fontSize: 12, color: semantic.accent.text, marginTop: space.s2 }}>{startErr}</div>}
        <div style={{ display: "flex", gap: space.s2, marginTop: space.s3, alignItems: "center" }}>
          <button className="tx-focus" onClick={retry}
            style={{ display: "inline-flex", alignItems: "center", height: 32, padding: "0 14px", borderRadius: radius.sm, border: `1px solid ${semantic.border.strong}`, background: semantic.surface.raised, fontSize: 13, fontWeight: 500, color: semantic.text.primary, cursor: "pointer", fontFamily: fonts.sans }}>
            {L("重试", "Retry")}
          </button>
          <button className="tx-focus" onClick={() => openSetupFrom(st)}
            style={{ display: "inline-flex", alignItems: "center", height: 32, padding: "0 10px", border: "none", background: "transparent", fontSize: 12, color: semantic.text.muted, cursor: "pointer", fontFamily: fonts.sans }}>
            {L("改选处理", "Change steps")}
          </button>
        </div>
      </>,
    );
  }

  // ══ 可发起态 ══
  const selSteps = STEP_ORDER.filter((s) => checked[s]);
  const n = selSteps.length;
  // 本机版：不显示预估价与「失败不计费」（本机不收费）
  const selList = sortedLists.find((l) => l.id === redactListId) ?? null;

  const toggleStep = (s: PpStep) => setChecked((c) => ({ ...c, [s]: !c[s] }));

  const orderCaption = n === 0
    ? L("至少选一个处理", "Pick at least one step")
    : n === 1
      ? L.t("只跑「{0}」这一步", "Runs just \"{0}\"", stepLabel(selSteps[0]))
      : L.t("按 {0} 顺序执行，前一步输出即下一步输入", "Runs {0} in order — each step feeds the next", selSteps.map(stepLabel).join(" → "));

  // 下拉菜单（自定义浮层）：212px 宽 · max-h 184 内滚 · 拼音排序 · 点外关闭
  const menuPanel = () => {
    const rows: { id: string | null; name: string; count: string; mutedName?: boolean }[] = [
      { id: null, name: L("不使用清单 · 智能识别", "No list · smart detection"), count: "", mutedName: true },
      ...sortedLists.map((l) => ({ id: l.id, name: l.name, count: L.t("{0} 条", "{0} entries", countRedactLines(l.content)) })),
    ];
    const curId = redactListId;
    return (
      <>
        <div style={{ position: "fixed", inset: 0, zIndex: 60 }} onClick={(e) => { e.stopPropagation(); setMenu(null); }} />
        <div role="menu" className="tx-scroll" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 70, width: 212, maxHeight: 184, overflowY: "auto", background: semantic.surface.float, border: `1px solid ${semantic.border.default}`, borderRadius: radius.sm, boxShadow: shadow.md, padding: 4, boxSizing: "border-box" }}>
          <div style={{ fontSize: 11, fontWeight: 500, letterSpacing: "var(--ls-label)", textTransform: "uppercase", color: semantic.text.muted, padding: "5px 10px 3px" }}>
            {L.t("脱敏保留词清单 · {0}", "Keep lists · {0}", sortedLists.length)}
          </div>
          {rows.map((r) => {
            const on = r.id === curId;
            return (
              <button
                key={r.id ?? "__none"}
                role="menuitem"
                className="tx-focus"
                onClick={(e) => { e.stopPropagation(); setRedactListId(r.id); setMenu(null); }}
                onMouseEnter={(e) => { e.currentTarget.style.background = semantic.surface.rowActive; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                style={{ display: "flex", alignItems: "center", gap: space.s2, width: "100%", border: "none", background: "transparent", cursor: "pointer", padding: "7px 10px", borderRadius: 6, textAlign: "left", fontFamily: fonts.sans, transition: `background ${motion.fast}` }}
              >
                <span style={{ fontSize: 13, fontWeight: on ? 600 : 400, color: on ? semantic.accent.text : r.mutedName ? semantic.text.muted : semantic.text.primary, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</span>
                {/* 计数：11px 下限 + muted（红线 11） */}
                {r.count && <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, flex: "0 0 auto" }}>{r.count}</span>}
                {on && <span style={{ fontSize: 11, color: semantic.accent.text, flex: "0 0 auto" }}>✓</span>}
              </button>
            );
          })}
        </div>
      </>
    );
  };

  const chipBtn = (label: string, mutedLabel: boolean) => (
    <span style={{ position: "relative", flex: "0 0 auto" }} onClick={(e) => e.stopPropagation()}>
      <button
        className="tx-focus"
        aria-expanded={menu === "list"}
        onClick={() => setMenu((m) => (m === "list" ? null : "list"))}
        style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: mutedLabel ? semantic.text.muted : semantic.accent.text, border: `1px solid ${semantic.border.strong}`, borderRadius: radius.sm, padding: "3px 9px", background: semantic.surface.float, cursor: "pointer", fontFamily: fonts.sans, maxWidth: 150 }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <span style={{ fontSize: 9, color: semantic.text.muted, flex: "0 0 auto" }}>▾</span>
      </button>
      {menu === "list" && menuPanel()}
    </span>
  );

  // 勾选行：整行可点切换；h32 r8 选中底 bgTint；checkbox 16×16 r4 选中实心 accent.fill 白✓
  const stepRow = (s: PpStep, right?: React.ReactNode, sub?: string) => {
    const on = checked[s];
    const disabled = false;
    return (
      <div
        key={s}
        role="checkbox"
        aria-checked={on}
        aria-disabled={disabled || undefined}
        tabIndex={disabled ? undefined : 0}
        className="tx-focus"
        onClick={() => toggleStep(s)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleStep(s); } }}
        style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 32, padding: "0 10px", borderRadius: radius.sm, background: on ? semantic.accent.bgTint : "transparent", cursor: disabled ? "default" : "pointer", transition: `background ${motion.fast}` }}
      >
        {on ? (
          <span style={{ width: 16, height: 16, borderRadius: 4, background: semantic.accent.fill, color: semantic.text.onAccent, display: "grid", placeItems: "center", fontSize: 10, flex: "0 0 auto" }}>✓</span>
        ) : (
          <span style={{ width: 16, height: 16, borderRadius: 4, border: `1.5px solid ${disabled ? semantic.border.default : semantic.border.strong}`, background: disabled ? semantic.surface.sunken : "transparent", boxSizing: "border-box", flex: "0 0 auto" }} />
        )}
        <span style={{ fontSize: 13, fontWeight: on ? 600 : 500, color: disabled ? semantic.text.ghost : on ? semantic.text.primary : semantic.text.secondary, whiteSpace: "nowrap" }}>{stepLabel(s)}</span>
        {sub && <span style={{ fontSize: 12, color: semantic.text.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</span>}
        <span style={{ flex: 1 }} />
        {right}
      </div>
    );
  };

  return shell(
    <>
      <div style={{ fontSize: 14, fontWeight: 600, color: semantic.accent.text }}>✦ {L("后处理", "Post-processing")}</div>
      <div style={{ fontSize: 12, color: semantic.text.muted, marginTop: 3 }}>{L("选要做的处理，一次跑完。", "Pick the steps — one run does them all.")}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: space.s3 }}>
        {stepRow("narrate", undefined, L("问答 → 第一人称叙述", "Q&A → first-person narrative"))}
        {stepRow(
          "redact",
          checked.redact
            ? chipBtn(selList?.name ?? L("不使用清单", "No list"), selList == null)
            : <span style={{ fontSize: 12, color: semantic.text.muted, whiteSpace: "nowrap" }}>{L("清单可选", "list optional")}</span>,
        )}
      </div>
      {/* 「会改什么」只在勾了脱敏时露出：没勾的时候它是无关信息，而这张卡在 360px 侧栏里寸土寸金 */}
      {checked.redact && (
        <div style={{ marginTop: space.s3, paddingLeft: 10 }}>
          <RedactScopeHelp />
        </div>
      )}
      <div style={{ fontSize: 11, color: semantic.text.muted, marginTop: 10 }}>{orderCaption}</div>
      {startErr && <div style={{ fontSize: 12, color: semantic.accent.text, marginTop: space.s2 }}>{startErr}</div>}
      {/* flexWrap：360px 侧栏 + 英文文案时价格行与 CTA 挤不下一行，CTA 整体折行右对齐，不许溢出卡缘 */}
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: space.s2, rowGap: space.s2, marginTop: space.s3, paddingTop: space.s3, borderTop: `1px solid ${semantic.border.subtle}` }}>
        <span style={{ flex: 1 }} />
        <button
          className="tx-focus"
          disabled={n === 0 || starting}
          onClick={() => { void doStart(selSteps, checked.redact ? redactListId : null); }}
          style={{ display: "inline-flex", alignItems: "center", whiteSpace: "nowrap", marginLeft: "auto", height: 36, padding: "0 16px", borderRadius: radius.sm, border: "none", background: n === 0 ? semantic.surface.sunken : semantic.accent.fill, color: n === 0 ? semantic.text.ghost : semantic.text.onAccent, fontSize: 13, fontWeight: 500, cursor: n === 0 || starting ? "default" : "pointer", fontFamily: fonts.sans, transition: `background ${motion.fast}` }}
        >
          {n === 0 ? L("先选一个处理", "Pick a step first") : L("开始加工 →", "Start processing →")}
        </button>
      </div>
    </>,
    { accentBorder: true },
  );
}

// 产物行：h40 r8 底 rowActive · 扩展名 mono 11 accent 定宽 30 · 行尾拆分导出（.docx ↓ + ▾ 菜单 .md/.txt）
/** 一个产物一行。`ovr` 只有脱敏稿才有——它决定这一行说什么、能不能下。
 *
 * **下载路径不分叉**：永远只有一份脱敏稿，它永远等于你此刻在清单里看到的样子
 * （后端 get_pp_product 取稿时应用改口，R2 上那份原样留档）。所以这里不新增文件、
 * 不改文件名，只把「下的这份带不带你的修订」说出来。
 * 两种情况**禁掉下载**——宁可不给下，也不给一份骗人的稿：
 *   · 存失败：屏幕上写着「已保留原词」而服务器上什么都没有；
 *   · 正在存：刚点完改口就点下载，会抢在保存前面（窗口只有几百毫秒，但它存在，且无声）。 */
function ProductRow({ jobId, kind, label, sub, ovr, fileName }: { jobId: string | null; kind: PpStepLegacy; label: string; sub: string | null; ovr?: RedactOverrideState | null; fileName?: string }) {
  const L = useL();
  const { uiLang } = useUILang();
  const [open, setOpen] = useState(false);
  const blocked = !!ovr && (ovr.failed || ovr.saving);
  const dl = (ext: "md" | "txt" | "docx") => { if (jobId && !blocked) downloadUrl(ppProductUrl(jobId, kind, ext, fileName, uiLang)); };
  // 这一栏只有约 170px：失败那句必须短到能跟「重试」并排一行，否则它自己折成两行、
  // 把重试挤到第三行去（2026-08-22 在生产页面上量的）。完整说法在下面展开区里已经有了。
  const note = ovr?.failed
    ? L.t("⚠ {0} 处没存上", "⚠ {0} unsaved", ovr.count)
    : ovr?.saving
      ? L("正在保存你的修订…", "Saving your changes…")
      : ovr && ovr.count > 0
        ? L.t("已按你的 {0} 处修订", "includes your {0} change(s)", ovr.count)
        : null;
  // 文字区**必须 minWidth:0**：flex 子项的默认 min-width 是 auto，
  // 意味着它永远不会窄过内容——ellipsis 一辈子不会生效，撑爆的是整行（设计纪律 §11 同一个坑）。
  // 说明另起一行，不跟清单名挤在一起：右栏只有约 300px，两段一并排就把下载按钮顶出去
  // （2026-08-22 在生产页面上量出来的：324px 的行，加上说明要 362px）。
  return (
    <div style={{ display: "flex", alignItems: "center", gap: space.s2, minHeight: 40, padding: "6px 10px", borderRadius: radius.sm, background: semantic.surface.rowActive }}>
      <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.accent.text, flex: "0 0 30px" }}>.md</span>
      <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
        <span style={{ display: "flex", alignItems: "baseline", gap: space.s2, minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>{label}</span>
          {sub && <span style={{ fontSize: 11, color: semantic.text.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>{sub}</span>}
        </span>
        {note && (
          <span style={{ display: "flex", alignItems: "baseline", gap: 6, minWidth: 0 }}>
            <span style={{ fontSize: 11, lineHeight: 1.5, color: ovr?.failed ? semantic.danger.text : semantic.text.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
              {note}
            </span>
            {ovr?.failed && (
              <button className="tx-focus" onClick={ovr.retry}
                style={{ border: "none", background: "transparent", padding: 0, cursor: "pointer", fontFamily: fonts.sans, fontSize: 11, color: semantic.accent.text, textDecoration: "underline", flex: "0 0 auto" }}>
                {L("重试", "Retry")}
              </button>
            )}
          </span>
        )}
      </span>
      <span style={{ position: "relative", flex: "0 0 auto" }}>
        <span style={{ display: "inline-flex", alignItems: "stretch", borderRadius: radius.sm, overflow: "hidden", border: `1px solid ${semantic.border.strong}` }}>
          <button className="tx-focus" onClick={() => dl("docx")} disabled={blocked}
            style={{ display: "inline-flex", alignItems: "center", padding: "0 10px", height: 26, border: "none", background: semantic.surface.float, fontSize: 12, fontWeight: 500, color: blocked ? semantic.text.ghost : semantic.text.primary, cursor: blocked ? "default" : "pointer", fontFamily: fonts.sans }}>
            .docx ↓
          </button>
          <button className="tx-focus" aria-label={L("更多格式", "More formats")} aria-expanded={open} disabled={blocked} onClick={() => setOpen((o) => !o)}
            style={{ display: "inline-flex", alignItems: "center", padding: "0 7px", height: 26, border: "none", borderLeft: `1px solid ${semantic.border.subtle}`, background: semantic.surface.float, fontSize: 10, color: blocked ? semantic.text.ghost : semantic.text.muted, cursor: blocked ? "default" : "pointer" }}>
            ▾
          </button>
        </span>
        {open && (
          <>
            <div style={{ position: "fixed", inset: 0, zIndex: 60 }} onClick={() => setOpen(false)} />
            <div role="menu" style={{ position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 70, width: 96, background: semantic.surface.float, border: `1px solid ${semantic.border.default}`, borderRadius: radius.sm, boxShadow: shadow.md, padding: 4, boxSizing: "border-box" }}>
              {(["md", "txt"] as const).map((ext) => (
                <button key={ext} role="menuitem" className="tx-focus"
                  onClick={() => { setOpen(false); dl(ext); }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = semantic.surface.rowActive; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                  style={{ display: "block", width: "100%", border: "none", background: "transparent", cursor: "pointer", padding: "6px 10px", borderRadius: 6, textAlign: "left", fontFamily: fonts.mono, fontSize: 12, color: semantic.text.primary, transition: `background ${motion.fast}` }}>
                  .{ext}
                </button>
              ))}
            </div>
          </>
        )}
      </span>
    </div>
  );
}

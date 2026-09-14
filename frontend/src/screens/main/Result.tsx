// Result.tsx — 转录详情 · 复核队列 v3（两级收口 + 双视图决策卡）
// 对照稿：docs/design/review-queue/HANDOFF-REVIEW-QUEUE.md + reference/复核队列-交互原型.dc.html
//
// v3 结构（叠加在方向 A「复核侧栏式」之上）：
//  1. 两级收口：待确认队列只装 存疑 + mustConfirm 实体；其余实体进「已定字」审计区
//     （展开看证据、可「✎ 修订」——修订不计进度、不挡导出；原「标为有误」流程废除）
//  2. 双视图决策卡：多处项默认总览（处列表 + 项级动作），点行进逐处（点点条 + 处级动作）
//     ——铁律：项级动作与处级动作永不同屏；按钮文案带作用范围与数字
//  3. 合并替换控件：[替换为 |输入| (替换 ↵)]，输入有效新词才浮现执行键，Enter 同效
//  4. 存疑项卡内改写（textarea 就地展开）；「删除这句」一律缩在 footer 文字链
//     （3s 倒计时确认 + Toast 撤销）——疑幻觉那处也不升为醒目按钮，递刀子的界面会诱导误删
//  5. 撤销 = 前向操作：已替换 →「改回原词」真实改回文本；无误/改写 →「重新决定」；删除 → 恢复
//     ——没有任何"只回滚状态不回滚文本"的撤销
//  6. 句级试听：▶ 从句首播到句尾自动停（句尾 = 下一行时间戳，无下一行则 +8s）；
//     播放中 ▶ 变墨底 ❚❚、时间戳变赤陶区间；顶部播放器仍是全局控制（不自动停）
//
// 滚动结构：页面容器锁高不滚；正文与队列各自内部滚动（tx-scroll）。

import { useEffect, useMemo, useRef, useState } from "react";
import { semantic, fonts, type, space, radius, shadow, motion, lh } from "../../styles/tokens";
import { useL, useUILang } from "../../lib/i18n";
import { isRtlLang } from "../../lib/bidi";
import { SPEAKER_CODES, speakerLabel, speakerSep } from "../../lib/speakerLabels";
import { langName } from "../../lib/langs";
import { audioUrl, exportDocxUrl, exportTxtUrl, saveTranscript, getReviewState, saveReviewState, type PostprocessStatus, type ReviewStateBlob, type TranscriptRow, type JobMetrics } from "../../lib/api";
import { downloadText, downloadUrl } from "../../lib/download";
import { fmtClock } from "../../lib/format";
import type { ReviewItem } from "../../lib/reviewData";
import { Button } from "../../components/Button";
import { AudioPlayer, type AudioMark } from "../../components/AudioPlayer";
import { ConvergeWave } from "../../components/Wave";
import { PostprocessCard } from "./PostprocessCard";
import type { FileMeta } from "../../lib/sampleData";

/** 后台缓存的四态（规则 4：常驻显示）。 */
type CacheState = { kind: "caching"; pct: number } | { kind: "paused"; pct: number } | { kind: "failed" } | { kind: "done" };
/** 规则 3：断了自动重试三次的间隔（毫秒）。 */
export const CACHE_RETRY_MS = [2000, 5000, 10000] as const;
/** 规则 3：连续这么久收不到一个字节也算断——网络掉了的时候进行中的下载不报错、只是一直挂着（Chrome 离线模拟实测）。 */
export const CACHE_STALL_MS = 30000;
/** 规则 1：后台下载用单独的网址，与播放器自己的请求不撞同一个键。后端不认这个参数、照常回整档。 */
export const audioCacheUrl = (jobId: string) => `${audioUrl(jobId)}?whole=1`;
const cacheBar = (pct: number) => (
  <span style={{ display: "inline-block", width: 72, height: 4, borderRadius: 2, background: semantic.border.subtle, overflow: "hidden", flex: "0 0 auto" }}>
    <span style={{ display: "block", width: `${pct}%`, height: "100%", background: semantic.accent.brand }} />
  </span>
);

interface ResultProps {
  onBack?: () => void; // 返回「我的转录」
  lang: string;
  job: { file: FileMeta; min: number };
  jobId: string | null;
  segments: TranscriptRow[];
  metrics: JobMetrics | null;
  review: ReviewItem[];
  /** 已存的复核决策。**由外层跟稿子一起取回来**（AppShell），页面一挂载就是对的。
   *  `undefined` = 外层没给（演示态 / 刚跑完的实时任务）→ 本组件自己去取。
   *  `null` = 取过了但没取到 —— 与「一条决策都没有」同义，直接开工。 */
  reviewState?: ReviewStateBlob | null;
  /** 已取到的加工状态（AppShell 与稿子并行取）。`undefined` = 外层没问出来 → 卡片自己去问。 */
  ppInitial?: PostprocessStatus | null;
  onPostprocessDone?: () => void; // 加工完成（已扣款）→ 外层刷新余额
}

const toSec = (t: string) => t.split(":").reduce((a, b) => a * 60 + +b, 0);

/** 卡片→正文的定位滚动：**就近对齐，绝不越过目标**。返回 null = 已经看得见，别动。
 *
 * 老写法是无条件把目标钉到视口 1/3 处，于是目标明明就在眼前偏下，也会先往回滚一段再滚回来
 * （用户观感就是「先向上再向下」，2026-08-08 实测）。改成方向唯一、位移最小：
 * 目标在视口上方就向上滚到贴顶，在下方就向下滚到贴底，已完整可见就一动不动。
 * 播放跟随（activeIdx / reattach）仍用 1/3 定位——那是「跟着读」，需要下文留白，不是这里的语义。 */
export function nearestScrollTop(
  row: { top: number; height: number },
  view: { scrollTop: number; height: number },
  pad = 24,
): number | null {
  const bottom = row.top + row.height;
  const viewBottom = view.scrollTop + view.height;
  if (row.top >= view.scrollTop + pad && bottom <= viewBottom - pad) return null;
  const next = row.top < view.scrollTop + pad ? row.top - pad : bottom - view.height + pad;
  return Math.max(0, next);
}
const mmss = (t: string) => fmtClock(toSec(t));
// 是不是被访者一侧（决定行的字色深浅）。比的是**代号**，所以只需认那一个中文词
// ——`/host/i` 是给演示数据留的（sampleData 里写的是 "Host"）。
const isGuest = (sp?: string | null) => !!sp && sp !== "主持人" && !/host/i.test(sp);
const keyOf = (r: ReviewItem) => `${r.type}:${r.term}`;
// 该处的存疑原因（老任务的 occurrence 无 reason → 回落卡级那一条）
const occReason = (r: ReviewItem, i: number) => r.occurrences[i]?.reason ?? r.reason ?? "";

// v3 两级收口：必须确认 = 存疑 + 无法定字的实体（mustConfirm）；其余定字实体 = 审计区可修订
const needsUser = (r: ReviewItem) => r.type === "doubt" || r.type === "speaker" || r.mustConfirm === true;

type ItemState = "pending" | "confirmed" | "skipped";
type ItemView = "overview" | "detail";
// 一处出现的归宿：确认无误 / 替换成新写法（记旧词，可改回）/ 人工改写整句 / 删除整句
type OccRes =
  | { kind: "ok" }
  | { kind: "renamed"; to: string; prev: string }
  | { kind: "edited" }
  | { kind: "deleted" };
type OccResMap = Record<string, Record<number, OccRes>>;

export function Result({ onBack, lang, job, jobId, segments, metrics, review, reviewState, ppInitial, onPostprocessDone }: ResultProps) {
  const L = useL();
  const { uiLang } = useUILang();
  // 录音语种是不是从右往左（27 门里只有 ar）。整行镜像 / 卡片 / 编辑框都看它。
  const rtl = isRtlLang(lang);
  const spName = (sp?: string | null) => speakerLabel(sp, uiLang);
  // ⚠️ **三张卡的说明是我们自己写的**（review.py 里那几句固定中文），不是模型写的报告——
  // 所以在前端出文案、跟界面语言走，后端那几句只作数据兜底（导出 / 管理页 / 老前端）。
  // 放前端还顺带修掉一个缺陷：报告是**出稿那一刻**写死的，放后端的话用户事后换界面语言，
  // 旧稿这几句就永远停在出稿那天的语言上；放前端则跟着当下的界面走。
  //   · speaker    —— 说话人归属卡（review._speaker_items）
  //   · web        —— 联网核实卡，全站同一句
  //   · unreported —— 终稿打了 [❓] 而报告没登记、就地补出来的那张卡
  // 其余卡（实体 / 真存疑）的 reason 来自 P3 报告，那是模型写的字，由 P3 的语言指令管
  // （pipeline/p3_lang.py），不在这条范围内。
  const reasonOf = (it: ReviewItem, i: number) =>
    it.type === "speaker"
      ? L("这一段是谁说的没能定下来——请确认归属；一段里混了两个人就选「多人混合」。",
          "We could not settle who speaks in this line — please confirm; pick “Multiple speakers” if two people are mixed in.")
      : it.type === "web"
      ? L("术语库未收录，系统自动联网核实",
          "Not in your glossary — checked automatically against the web.")
      : it.unreported
      ? L("模型在终稿这一处标了存疑，但报告里没有写明原因——请自行核对这一句",
          "This line was flagged as uncertain in the draft, but no reason was recorded — please check it yourself.")
      : occReason(it, i);
  const audioRef = useRef<HTMLAudioElement>(null);
  const dur = metrics?.durationSec != null ? fmtClock(metrics.durationSec) : null;
  const elapsed = metrics?.elapsedSec != null ? fmtClock(metrics.elapsedSec) : null;

  // ── 队列 / 审计区 ──
  // P0 护栏：后端可能产出「零出现处」的项（report 写法与终稿不完全一致时搜不到）——
  // 这类项无法定位/试听/替换，不进任何区（信息仍在 report 里，不算丢失）
  const items = useMemo(() => review.filter((r) => r.occurrences.length > 0), [review]);
  const queue = useMemo(
    () => items.filter(needsUser).sort((a, b) => toSec(a.occurrences[0]?.t ?? "0") - toSec(b.occurrences[0]?.t ?? "0")),
    [items]
  );
  const auto = useMemo(() => items.filter((r) => !needsUser(r)), [items]);

  // ── 决策账本 ──
  // 外层给了就直接开工——**这一步决定了右栏第一帧对不对**。自己去取的话，
  // 第一帧必然是「一条决策都没有」：复核完的稿子先摆「17 项待确认」再翻成「已全部确认」。
  const preOcc = (reviewState?.occRes ?? null) as OccResMap | null;
  const preSkip = (reviewState?.skipped ?? null) as Record<string, boolean> | null;
  const [occRes, setOccRes] = useState<OccResMap>(() => preOcc ?? {});
  const occResRef = useRef(occRes); // 镜像：Toast 撤销等延迟回调里也拿得到最新账本
  occResRef.current = occRes;
  const [itemView, setItemView] = useState<Record<string, ItemView | undefined>>({});
  const [focusIdx, setFocusIdx] = useState<Record<string, number>>({});
  const [skipped, setSkipped] = useState<Record<string, boolean>>(() => preSkip ?? {});
  const [draftsAll, setDraftsAll] = useState<Record<string, string>>({}); // 总览「全部替换为」
  const [draftsOne, setDraftsOne] = useState<Record<string, string>>({}); // 逐处「替换为」
  const [editingOcc, setEditingOcc] = useState<{ k: string; i: number } | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [reasonOpen, setReasonOpen] = useState<Record<string, boolean>>({}); // 卡内理由夹 2 行可展开
  const [sentOpen, setSentOpen] = useState<Record<string, boolean>>({});     // 卡内原句夹 4 行可展开
  const [deleteArm, setDeleteArm] = useState<{ k: string; i: number; n: number } | null>(null);
  const [deleted, setDeleted] = useState<Record<number, string>>({}); // 行号 → 删除前原文（按行号，不按会重复的时间戳）
  const [autoOpen, setAutoOpen] = useState(false);
  const [decisionsOpen, setDecisionsOpen] = useState(false); // 清零后「你的决策」是否展开
  const [autoExpanded, setAutoExpanded] = useState<string | null>(null);
  const [jumpIdx, setJumpIdx] = useState<Record<string, number>>({}); // 审计区「跳到原文」的循环游标
  const [revisingKey, setRevisingKey] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; action?: string; onAction?: () => void } | null>(null);
  const [currentKey, setCurrentKey] = useState<string | null>(() => {
    const q = review.filter((r) => r.occurrences.length > 0 && needsUser(r)).sort((a, b) => toSec(a.occurrences[0]?.t ?? "0") - toSec(b.occurrences[0]?.t ?? "0"));
    // 外层已给决策时，第一项要按**未决**算——挑到一个早就确认过的项，
    // 右栏就会顶着「已全部确认」却摊开一张完整决策卡（下面 finally 里那段校正说的正是这件事）
    const undecided = (it: ReviewItem) => !it.occurrences.every((_, i) => preOcc?.[keyOf(it)]?.[i] != null);
    const pick = preOcc ? q.find((r) => !preSkip?.[keyOf(r)] && undecided(r)) : q[0];
    return pick ? keyOf(pick) : null;
  });
  const armTimer = useRef<number | undefined>(undefined);
  const toastTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => { window.clearInterval(armTimer.current); window.clearTimeout(toastTimer.current); }, []);

  const resOf = (k: string, i: number): OccRes | undefined => occRes[k]?.[i];
  const occState = (it: ReviewItem, i: number) => resOf(keyOf(it), i)?.kind ?? "pending";
  const occTerm = (it: ReviewItem, i: number) => { const r = resOf(keyOf(it), i); return r?.kind === "renamed" ? r.to : it.term; };
  // occurrence → 正文行号（行的真正唯一身份）。相邻两行常同一时间戳（两说话人同一秒各一行），
  // 只按 t 会命中第一行/两行都命中——用「t + 原行文本」精确到含该词的那行；行号取自原始 segments、
  // 稳定不随编辑/删除变。定位·高亮·改写·删除全部改用这个行号，杜绝「误伤同时间戳的另一行」。
  const occToRow = useMemo(() => {
    const map = new Map<string, number>();
    for (const it of items) it.occurrences.forEach((o, oi) => {
      let idx = segments.findIndex((s) => s.t === o.t && s.s === o.lineText);
      if (idx < 0) idx = segments.findIndex((s) => s.t === o.t);
      map.set(`${keyOf(it)}#${oi}`, idx);
    });
    return map;
  }, [items, segments]);
  const occRow = (it: ReviewItem, oi: number) => occToRow.get(`${keyOf(it)}#${oi}`) ?? -1;
  const unresolvedIdxs = (it: ReviewItem) => it.occurrences.map((_, i) => i).filter((i) => !resOf(keyOf(it), i));
  const itemStateOf = (map: OccResMap, it: ReviewItem): ItemState => {
    const k = keyOf(it);
    if (it.occurrences.every((_, i) => map[k]?.[i] != null)) return "confirmed";
    if (skipped[k]) return "skipped";
    return "pending";
  };
  const itemState = (it: ReviewItem) => itemStateOf(occRes, it);
  const doneCount = queue.filter((q) => itemState(q) === "confirmed").length;
  const allDone = queue.length > 0 && queue.every((q) => itemState(q) !== "pending");
  // 还有几处等人拍板。头部靠它决定「下载」这颗按钮该不该是整页最醒目的那一个——
  // 商业主张是「确认完才放心导出」，界面主张不能反着说（2026-08-31 穿测）。
  const pendingCount = queue.filter((q) => itemState(q) === "pending").length;
  const firstPending = queue.find((q) => itemState(q) === "pending") ?? null;

  // 记账 + 推进：整项完成 → 跳下一待处理项（重置其视图）；否则 advance 时焦点移到下一未决处
  const setRes = (item: ReviewItem, entries: [number, OccRes | null][], opt?: { advance?: boolean }) => {
    const k = keyOf(item);
    const cur = { ...(occResRef.current[k] ?? {}) };
    entries.forEach(([i, r]) => { if (r) cur[i] = r; else delete cur[i]; });
    const map = { ...occResRef.current, [k]: cur };
    occResRef.current = map;
    setOccRes(map);
    setDeleteArm(null);
    const complete = item.occurrences.every((_, i) => cur[i] != null);
    if (complete) {
      const next = queue.find((q) => keyOf(q) !== k && itemStateOf(map, q) === "pending");
      setCurrentKey(next ? keyOf(next) : null);
      if (next) {
        setItemView((m) => ({ ...m, [keyOf(next)]: undefined }));
        scrollToRow(occRow(next, 0));
      }
    } else if (opt?.advance) {
      const ni = item.occurrences.findIndex((_, i) => cur[i] == null);
      if (ni >= 0) { setFocusIdx((m) => ({ ...m, [k]: ni })); scrollToRow(occRow(item, ni)); }
    }
  };
  // 按行替换文本（只动该出现处所在行，互不影响其它行）
  const replaceInSeg = (t: string, from: string, to: string) => {
    setSegs((prev) => prev.map((s) => (s.t === t && s.s.includes(from) ? { ...s, s: s.s.split(from).join(to) } : s)));
  };

  // ── 处级动作 ──
  const okThis = (it: ReviewItem, i: number) => setRes(it, [[i, { kind: "ok" }]], { advance: true });
  const replaceThis = (it: ReviewItem, i: number) => {
    const k = keyOf(it);
    const d = (draftsOne[k] ?? "").trim();
    if (!d || d === occTerm(it, i)) return;
    replaceInSeg(it.occurrences[i].t, occTerm(it, i), d);
    setRes(it, [[i, { kind: "renamed", to: d, prev: occTerm(it, i) }]], { advance: true });
  };
  // ── 项级动作（只动未决的处） ──
  const okAll = (it: ReviewItem) => setRes(it, unresolvedIdxs(it).map((i) => [i, { kind: "ok" }] as [number, OccRes]));
  const replaceAll = (it: ReviewItem) => {
    const k = keyOf(it);
    const d = (draftsAll[k] ?? "").trim();
    if (!d || d === it.term) return;
    const idxs = unresolvedIdxs(it);
    idxs.forEach((i) => replaceInSeg(it.occurrences[i].t, occTerm(it, i), d));
    setRes(it, idxs.map((i) => [i, { kind: "renamed", to: d, prev: occTerm(it, i) }] as [number, OccRes]));
  };
  // 撤销 = 前向操作：改回原词真实回写文本 + 该处重新置为待定
  const revertOcc = (it: ReviewItem, i: number) => {
    const r = resOf(keyOf(it), i);
    if (r?.kind === "renamed") replaceInSeg(it.occurrences[i].t, r.to, r.prev);
    setRes(it, [[i, null]]);
    setCurrentKey(keyOf(it));
  };
  // 卡内改写：textarea 就地展开，保存 = 句子落稿 + 该处 resolved(edited)
  const saveEditOcc = (it: ReviewItem, i: number) => {
    const rowIdx = occRow(it, i);
    setSegs((prev) => prev.map((s, j) => (j === rowIdx ? { ...s, s: editDraft } : s)));
    setEditingOcc(null);
    setRes(it, [[i, { kind: "edited" }]], { advance: true });
  };
  // 删除：3s 倒计时确认（防误删）→ 执行后 Toast 可撤销
  const armDelete = (it: ReviewItem, i: number) => {
    window.clearInterval(armTimer.current);
    setDeleteArm({ k: keyOf(it), i, n: 3 });
    armTimer.current = window.setInterval(() => {
      setDeleteArm((a) => {
        if (!a || a.n <= 1) { window.clearInterval(armTimer.current); return null; }
        return { ...a, n: a.n - 1 };
      });
    }, 1000);
  };
  const doDelete = (it: ReviewItem, i: number) => {
    window.clearInterval(armTimer.current);
    const rowIdx = occRow(it, i);
    const orig = segs[rowIdx];
    setDeleted((m) => ({ ...m, [rowIdx]: orig?.s ?? "" }));
    setRes(it, [[i, { kind: "deleted" }]], { advance: true });
    showToast(L.t("已删除该句（{0}）", "Deleted line at {0}", mmss(it.occurrences[i].t)), L("撤销", "Undo"), () => {
      setDeleted((m) => { const n = { ...m }; delete n[rowIdx]; return n; });
      setToast(null);
      setCurrentKey(keyOf(it));
      setRes(it, [[i, null]]);
    });
  };
  const restoreLine = (rowIdx: number) => {
    setDeleted((m) => { const n = { ...m }; delete n[rowIdx]; return n; });
    for (const x of items) {
      const oi = x.occurrences.findIndex((_, o) => occRow(x, o) === rowIdx && resOf(keyOf(x), o)?.kind === "deleted");
      if (oi >= 0) { setRes(x, [[oi, null]]); setCurrentKey(keyOf(x)); break; }
    }
  };
  // 跳过 = 搁置整项、不计完成（仅队列项有）
  const skipItem = (it: ReviewItem) => {
    const k = keyOf(it);
    const next = queue.find((q) => keyOf(q) !== k && itemState(q) === "pending");
    setSkipped((m) => ({ ...m, [k]: true }));
    setCurrentKey(next ? keyOf(next) : null);
  };
  // 项级撤销：只清 ok/edited 归宿（renamed 走「改回原词」、deleted 走「恢复」）
  const undoOks = (it: ReviewItem) => {
    const k = keyOf(it);
    const entries = it.occurrences
      .map((_, i) => i)
      .filter((i) => { const r = resOf(k, i); return r?.kind === "ok" || r?.kind === "edited"; })
      .map((i) => [i, null] as [number, OccRes | null]);
    setRes(it, entries);
    setSkipped((m) => ({ ...m, [k]: false }));
    setCurrentKey(k);
  };
  const openItem = (it: ReviewItem, occIdx?: number, view?: ItemView) => {
    const k = keyOf(it);
    const multi = it.occurrences.length > 1;
    const v = view ?? (multi ? "overview" : "detail");
    setCurrentKey(k);
    setSkipped((m) => ({ ...m, [k]: false }));
    setItemView((m) => ({ ...m, [k]: v }));
    const i = occIdx ?? unresolvedIdxs(it)[0] ?? 0;
    setFocusIdx((m) => ({ ...m, [k]: i }));
    setEditingOcc(null);
    setDeleteArm(null);
    if (v === "detail" || !multi) scrollToRow(occRow(it, Math.min(i, it.occurrences.length - 1)));
  };
  // ── 点正文里的标记词 → 右栏把那张卡「亮出来」（2026-09-06 Duner：卡展开了但不滚过去，得手动找）──
  // 三种卡三种藏法：已定字卡在「已按证据定字」那一节的列表里（列表停在顶上）；待确认卡在队列里，
  // 而队列在审计区展开时是折叠成一行的；处理完的卡收在「你的决策」那一行里。此前所有「把某张卡设为当前」
  // 的路径都只改状态，没有一处把右栏滚过去、也不会把折叠的那一节打开。现在统一走这里：
  // 先按卡的种类把所在的那一节打开，渲染完再在它的滚动容器里就近滚到那张卡，并闪 1.4 秒（滚没滚都要指出是哪张）。
  const revealKey = useRef<string | null>(null);
  const [flashCard, setFlashCard] = useState<string | null>(null);
  const flashCardTimer = useRef(0);
  const revealCard = (it: ReviewItem) => {
    const k = keyOf(it);
    if (needsUser(it)) { setAutoOpen(false); if (allDone) setDecisionsOpen(true); }
    else { setAutoOpen(true); setAutoExpanded(k); }
    revealKey.current = k;
  };
  useEffect(() => {
    const k = revealKey.current;
    if (!k) return;
    const el = Array.from(document.querySelectorAll<HTMLElement>("[data-review-card]")).find((e) => e.dataset.reviewCard === k);
    if (!el) return;                       // 那一节还没展开完成：下一次渲染再找
    revealKey.current = null;
    if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setFlashCard(k);
    window.clearTimeout(flashCardTimer.current);
    flashCardTimer.current = window.setTimeout(() => setFlashCard(null), 1400);
  });
  // 已定字卡里定位到第 i 处：正文滚到那一行并高亮，「跳到原文」的循环游标接到它后面
  // （2026-09-06 Duner：4 处的卡要看第 1 处得先转一圈——现在卡里每处一行、点哪处跳哪处，循环逻辑不动）
  const goToAuditOcc = (a: ReviewItem, i: number) => {
    const k = keyOf(a);
    const N = a.occurrences.length;
    setCurrentKey(k);
    setItemView((m) => ({ ...m, [k]: "detail" })); // 处级高亮需要 detail 视角
    setFocusIdx((m) => ({ ...m, [k]: i }));
    scrollToRow(occRow(a, i));
    setJumpIdx((m) => ({ ...m, [k]: (i + 1) % N }));
  };
  // 审计区「跳到原文」：多处时循环跳（1/8 → 2/8 → …→ 回到 1/8），每跳一处正文高亮那一行
  const jumpToOriginal = (a: ReviewItem) => goToAuditOcc(a, (jumpIdx[keyOf(a)] ?? 0) % a.occurrences.length);
  // 已定字项的「修订」：就地展开同一张双视图决策卡——不进队列、不计进度、不挡导出
  const toggleRevise = (a: ReviewItem) => {
    const k = keyOf(a);
    if (revisingKey === k) { setRevisingKey(null); return; }
    setRevisingKey(k);
    setCurrentKey(k);
    setItemView((m) => ({ ...m, [k]: a.occurrences.length > 1 ? "overview" : "detail" }));
    setFocusIdx((m) => ({ ...m, [k]: 0 }));
    setDraftsOne((m) => ({ ...m, [k]: "" }));
    scrollToRow(occRow(a, 0));
  };
  const showToast = (msg: string, action?: string, onAction?: () => void) => {
    window.clearTimeout(toastTimer.current);
    setToast({ msg, action, onAction });
    toastTimer.current = window.setTimeout(() => setToast(null), 6000);
  };

  // ── 正文编辑（行级 ✎ 通用工具，保留） ──
  const [segs, setSegs] = useState<TranscriptRow[]>(segments);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [hovered, setHovered] = useState<number | null>(null);

  // P1#7 修订同步：改名/改写/删除落稿后 0.8s 防抖，把当前文稿快照（剔除已删行）
  // 存到后端——导出/取稿即用修订版。失败静默：下一次修订会再触发。
  const firstSync = useRef(true);
  useEffect(() => {
    if (firstSync.current) { firstSync.current = false; return; }
    if (!jobId) return;
    const t = window.setTimeout(() => {
      saveTranscript(jobId, segs.filter((_sg, i) => deleted[i] == null)).catch(() => {});
    }, 800);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segs, deleted, jobId]);

  // 复核决策账本持久化：修订稿只存「改完的文字」，不含「哪些项已确认」——不另存账本，
  // 重登后确认进度会归零（修订还在、状态全回退）。挂载时读回，变更后 0.8s 防抖回存。
  const [cardHover, setCardHover] = useState(false);   // 当前卡悬停 → 浮出「回到正文」提示
  const stateHydrated = useRef(false);
  const skipStateSave = useRef(false); // 水合 setState 那一轮不回存（存回去的就是刚读来的）
  // ⚠️ **服务端现在有什么，我们知不知道**。这是回存的前提条件，不是优化。
  // 不知道就写的话，写进去的是「本次会话这一小撮」，而服务端那边可能是用户
  // 早就确认完的 17 项——一次网络抖动就能把它们全抹掉（2026-08-23 三条红测试留档）。
  const serverKnown = useRef(false);
  useEffect(() => {
    if (!jobId) { stateHydrated.current = true; return; }   // 演示态（?rev）无持久化
    // 外层已经取回来了（AppShell 与稿子一起取）→ 直接算水合完，别再打一次同样的接口。
    // ⚠️ 这两行缺一不可：
    //   skipStateSave —— 这条捷径原来没挂这块牌子，于是**每打开一篇转录都白写一次**
    //     （生产实测每次都有一条多余的 PUT，谁也没动过决策）。
    //   serverKnown —— 外层把话说到了（给了账本，或给了 null＝确实没有），才算知道。
    if (reviewState !== undefined) {
      stateHydrated.current = true;
      skipStateSave.current = true;
      serverKnown.current = true;
      return;
    }
    let alive = true;
    let hydOcc: OccResMap = {};
    let hydSkip: Record<string, boolean> = {};
    getReviewState(jobId)
      .then((st) => {
        if (!alive) return;
        serverKnown.current = true;   // 问到了（哪怕是空的，那也是「服务端就是空的」）
        if (!st || typeof st !== "object") return;
        const srvOcc = st.occRes && typeof st.occRes === "object" ? (st.occRes as OccResMap) : null;
        const srvSkip = st.skipped && typeof st.skipped === "object" ? (st.skipped as Record<string, boolean>) : null;
        if (!srvOcc && !srvSkip) return;
        // 读回期间用户已拍板的决策优先（merge 时本地覆盖服务端）；有本地决策则水合后仍回存合并结果
        skipStateSave.current = Object.keys(occResRef.current).length === 0;
        if (srvOcc) { hydOcc = srvOcc; setOccRes((prev) => ({ ...srvOcc, ...prev })); }
        if (srvSkip) { hydSkip = srvSkip; setSkipped((prev) => ({ ...srvSkip, ...prev })); }
      })
      .catch(() => {}) // 读不到不挡复核；本次会话的决策仍照常回存
      .finally(() => {
        if (!alive) return;
        stateHydrated.current = true;
        // ⚠️ 当前项必须在决策读回来之后再校一次。它的初始值是按**原始复核清单**猜的第一项，
        // 而那一项很可能早就确认过了——不校的话它会一直挂着完整决策卡（带类别标签、赤陶高亮边框），
        // 顶上却写着「存疑已全部确认」。凡是复核完之后再打开的转录都会撞上（2026-08-20 生产实见）。
        setCurrentKey((cur) => {
          const occ = { ...hydOcc, ...occResRef.current };
          const undecided = (it: ReviewItem) => {
            const k = keyOf(it);
            return !it.occurrences.every((_, i) => occ[k]?.[i] != null);
          };
          const curItem = cur ? queue.find((q) => keyOf(q) === cur) : null;
          if (curItem && undecided(curItem)) return cur;   // 还没决完 → 别打断
          const next = queue.find((q) => !hydSkip[keyOf(q)] && undecided(q));
          return next ? keyOf(next) : null;
        });
      });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);
  useEffect(() => {
    if (!jobId || !stateHydrated.current) return;
    if (skipStateSave.current) { skipStateSave.current = false; return; }
    let alive = true;
    const t = window.setTimeout(() => {
      // ⚠️ **不知道服务端现在有什么，就一个字都不许写。**
      // 写进去的只是本次会话这一小撮，而服务端那边可能是早就确认完的一整本。
      // 但「不写」不能变成「这次白干」：先补问一次，问到了就把服务端那份垫底、
      // 本次的盖在上面（setState 会再触发一轮，那一轮才真的写）。
      // 还是问不到 → 这次不写，等用户下一次改动再试。旧的不丢，是唯一的红线。
      if (!serverKnown.current) {
        getReviewState(jobId)
          .then((st) => {
            if (!alive) return;
            serverKnown.current = true;
            const so = (st?.occRes ?? null) as OccResMap | null;
            const ss = (st?.skipped ?? null) as Record<string, boolean> | null;
            if (so) setOccRes((prev) => ({ ...so, ...prev }));
            if (ss) setSkipped((prev) => ({ ...ss, ...prev }));
            // 服务端本来就是空的 → 上面两个 setState 不会改变引用、effect 不会再跑，
            // 这里补一次写，否则本次会话的决策要等下一次改动才落盘。
            if (!so && !ss) saveReviewState(jobId, { occRes, skipped }).catch(() => {});
          })
          .catch(() => {});
        return;
      }
      saveReviewState(jobId, { occRes, skipped }).catch(() => {});
    }, 800);
    return () => { alive = false; window.clearTimeout(t); };
  }, [occRes, skipped, jobId]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [flashRow, setFlashRow] = useState<number | null>(null); // 「跳到这一句」的落点，闪一下就灭
  const flashTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);
  const [detached, setDetached] = useState(false); // 手动滚动后脱离跟随；点 pill / 跳播重新吸附
  const startEdit = (i: number) => { setEditing(i); setDraft(segs[i].s); };
  const saveEdit = () => {
    if (editing == null) return;
    const next = segs.slice();
    next[editing] = { ...next[editing], s: draft };
    setSegs(next);
    setEditing(null);
  };

  // ── 播放器（真实 <audio> 驱动）+ 句级试听 scope ──
  const [cur, setCur] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [audioDur, setAudioDur] = useState(0);
  const [audioGone, setAudioGone] = useState(false); // 录音 404（上传 7 天后自动删除）→ 优雅只读态
  // ── 国内慢网的两道补丁（2026-09-06，Duner 反馈点卡片后要等 30–60 秒才出声）──
  // 根因不在代码：每次定位都是一次跨洋 Range 请求（浏览器 → Cloudflare → Vercel → Render → R2），
  // 浏览器要缓冲够几秒声音才肯开播，国内带宽常常只有每秒几十 KB。这里不改链路，只做两件事：
  //   ① 等数据时明说「正在加载」——此前 30 秒没任何反馈，用户以为没点上、连点几次，每点一次前面的请求就作废；
  //   ② 打开详情页就在后台把整段音频拉到本地（38 分钟约 9 MB），拉完把 <audio> 的来源换成本地副本，
  //      之后任何定位都不再走网络。拉的过程中点卡片仍走原来的流式路径（慢但能用），换源发生在暂停时、
  //      并把播放位置带过去，用户不会被打断。国内 CDN 与直连 R2 记在 docs/ROADMAP.md 远期。
  const [audioWait, setAudioWait] = useState(false);            // seek 后在等数据
  const [localSrc, setLocalSrc] = useState<string | null>(null); // 整段缓存好之后的 blob 地址
  // 缓存状态常驻在播放器下方（2026-09-06 第二轮，Duner 定的四条规则，见下面那个 effect）：
  //   caching N% / paused（让位播放）/ failed（自动重试 3 次后）/ done。null = 没在缓存（录音已删、或环境不支持）
  const [cache, setCache] = useState<CacheState | null>(null);
  const cacheCtl = useRef<{ hold: () => void; release: () => void; retry: () => void } | null>(null);
  const restoreTime = useRef<number | null>(null);               // 换源时要带过去的播放位置
  const pendingLocal = useRef<string | null>(null);              // 播放中缓存好了 → 等暂停再换
  // 句级试听范围只需 ref（timeupdate 里判断播到句尾自动停）；不再渲染播放中区间，无需 state
  const scopeRef = useRef<{ t: string; end: number } | null>(null);
  const setScope = (s: { t: string; end: number } | null) => { scopeRef.current = s; };
  const total = metrics?.durationSec ?? audioDur;
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    setAudioGone(false);   // 切任务先假定录音在，加载失败（404）再标记
    const onTime = () => {
      // 句级试听：播到句尾自动停
      if (scopeRef.current && a.currentTime >= scopeRef.current.end) { a.pause(); setScope(null); }
      setCur(a.currentTime);
    };
    const onMeta = () => {
      setAudioDur(Number.isFinite(a.duration) ? a.duration : 0); setAudioGone(false);
      // 换成本地副本后浏览器把位置归零——把换源前的位置带回来
      if (restoreTime.current != null) { a.currentTime = restoreTime.current; restoreTime.current = null; }
    };
    const onErr = () => { if (jobId) setAudioGone(true); };   // /audio 404 = 录音已过期删除
    const onPlay = () => { setPlaying(true); cacheCtl.current?.hold(); };   // 规则 2：播放优先，下载让路
    const swapToLocal = () => {
      // 播放中缓存好了的话不当场换源（换源会打断声音），等这次暂停/播完再换
      if (!pendingLocal.current) return;
      restoreTime.current = a.currentTime;
      setLocalSrc(pendingLocal.current);
      pendingLocal.current = null;
    };
    const onPause = () => { setPlaying(false); setAudioWait(false); swapToLocal(); cacheCtl.current?.release(); };
    const onEnded = () => { swapToLocal(); cacheCtl.current?.release(); };
    const onWait = () => setAudioWait(true);      // seeking / waiting：浏览器在等这一段的数据
    const onReady = () => setAudioWait(false);    // playing / canplay：数据到了
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("error", onErr);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onEnded);
    a.addEventListener("seeking", onWait);
    a.addEventListener("waiting", onWait);
    a.addEventListener("playing", onReady);
    a.addEventListener("canplay", onReady);
    a.addEventListener("error", onReady);
    return () => {
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("error", onErr);
      a.removeEventListener("play", onPlay);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("ended", onEnded);
      a.removeEventListener("seeking", onWait);
      a.removeEventListener("waiting", onWait);
      a.removeEventListener("playing", onReady);
      a.removeEventListener("canplay", onReady);
      a.removeEventListener("error", onReady);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);
  // 后台把整段音频缓存到本地（见上面那段注释）。jsdom / 老浏览器没有 createObjectURL → 跳过，行为与从前相同。
  // 2026-09-06 第二轮（客户 Edge 实测后 Duner 定的四条规则）：
  //   规则 1 立刻开始、不排队：下载走**单独的网址**（多一个查询参数），播放器在缓存好之前 preload="none"。
  //           Chrome/Edge 对同一网址的并发请求会排队——此前后台下载要等播放器那段读时长的请求彻底结束才开始，
  //           慢网上那一小段要传很久，客户看到的就是「过了一分钟才开始缓存」（生产实测：同网址首字节 3.3 s、不同网址 1.5 s）。
  //   规则 2 播放优先：用户一点播放就掐断下载把线让给播放器（play 事件 → hold），暂停 / 播完从断点续传
  //           （pause / ended → release，Range: bytes=已收到的字节-）。两个请求分同一条每秒几十 KB 的线，出声要等两倍时间。
  //   规则 3 失败不静默：断了自动重试 3 次（2 / 5 / 10 秒），仍失败显示「缓存失败 · 重试」并挂着。此前是安静退回流式路径。
  //   规则 4 状态常驻：caching / paused / failed / done 四种一直显示在播放器下方，不再 5 秒消失——「已缓存」只闪 5 秒时，
  //           从磁盘缓存 0.1 秒完成的第二次打开根本看不见，客户以为「没缓存」。
  //   不做：显式缓存库（浏览器磁盘缓存已经在起作用，客户截图里两条 0 B disk cache 就是它）；边界不变（4 小时长录音慢网仍要十几分钟）。
  useEffect(() => {
    setLocalSrc(null); setCache(null);
    pendingLocal.current = null; restoreTime.current = null; cacheCtl.current = null;
    if (!jobId || typeof URL.createObjectURL !== "function" || typeof fetch !== "function") return;
    const url = audioCacheUrl(jobId);
    const chunks: Uint8Array[] = [];
    let got = 0, total = 0, attempt = 0;
    let ctrl: AbortController | null = null;
    let timer: number | undefined;
    let stopped = false;    // effect 已清理（切任务 / 卸载）
    let holding = false;    // 让位播放中
    let finished = false;   // 下载完成或彻底失败，hold/release 不再有事可做
    let objectUrl: string | null = null;
    const pct = () => (total ? Math.min(99, Math.floor((got / total) * 100)) : 0);
    const run = async () => {
      ctrl = new AbortController();
      try {
        const r = await fetch(url, { signal: ctrl.signal, headers: got ? { Range: `bytes=${got}-` } : undefined });
        // 录音已删（上传 7 天后 R2 生命周期清掉，后端回 410；404 一并认）：不下载、不重试，直接切成「录音已删除」态。
        // 播放器缓存好之前 preload="none"、自己不会去碰网络，所以这一次整档请求就是它唯一的探针——
        // 2026-09-06 生产实见：只认 404 的话 410 被当成断网重试三次、显示「缓存失败」，而录音其实是到期了。
        if (r.status === 404 || r.status === 410) { markGone(); return; }
        if (!r.ok || !r.body) throw new Error(`audio cache ${r.status}`);
        if (r.status === 206) {
          total = Number((r.headers.get("content-range") || "").split("/")[1]) || total;
        } else {
          chunks.length = 0; got = 0;                                       // 服务器没按断点给：从头收
          total = Number(r.headers.get("content-length")) || 0;
        }
        setCache({ kind: "caching", pct: pct() });
        const reader = r.body.getReader();
        const my = ctrl;
        let stall = window.setTimeout(() => my.abort(), CACHE_STALL_MS);
        try {
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value) {
              chunks.push(value); got += value.length; setCache({ kind: "caching", pct: pct() });
              window.clearTimeout(stall); stall = window.setTimeout(() => my.abort(), CACHE_STALL_MS);
            }
          }
        } finally { window.clearTimeout(stall); }
        if (stopped) return;
        finished = true;
        objectUrl = URL.createObjectURL(new Blob(chunks as BlobPart[], { type: r.headers.get("content-type") || "audio/mp4" }));
        setCache({ kind: "done" });
        const a = audioRef.current;
        if (a && !a.paused) { pendingLocal.current = objectUrl; return; }   // 正在放 → 等暂停再换
        restoreTime.current = a?.currentTime ?? null;
        setLocalSrc(objectUrl);
      } catch {
        if (stopped || holding) return;                                     // 我们自己掐的：等 release 再来
        attempt += 1;
        if (attempt > CACHE_RETRY_MS.length) { finished = true; setCache({ kind: "failed" }); return; }
        timer = window.setTimeout(run, CACHE_RETRY_MS[attempt - 1]);
      }
    };
    // 探针（2026-09-06 生产实见）：整档请求可能被浏览器自己的缓存接住（我们允许它留 24 小时），录音在服务器上
    // 已经删了、页面却照常「✓ 已缓存」还能放。这一条 1 字节、cache: no-store 的请求一定打到服务器，
    // 与整档下载并行发出、不拖慢它；服务器说没了（404/410）就以它为准，整档那份不论从哪来都作废。
    const markGone = () => {
      finished = true; holding = false;
      window.clearTimeout(timer); ctrl?.abort();
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
      pendingLocal.current = null;
      setLocalSrc(null); setCache(null); setAudioGone(true);
    };
    const probeCtrl = new AbortController();
    fetch(audioUrl(jobId), { headers: { Range: "bytes=0-0" }, cache: "no-store", signal: probeCtrl.signal })
      .then((r) => { if (!stopped && (r.status === 404 || r.status === 410)) markGone(); })
      .catch(() => {});   // 探针自己失败（断网）不下结论，整档那条路会按重试规则报
    cacheCtl.current = {
      hold: () => { if (finished || holding) return; holding = true; window.clearTimeout(timer); ctrl?.abort(); setCache({ kind: "paused", pct: pct() }); },
      release: () => { if (!holding) return; holding = false; setCache({ kind: "caching", pct: pct() }); void run(); },
      retry: () => { if (!finished || objectUrl) return; finished = false; attempt = 0; setCache({ kind: "caching", pct: pct() }); void run(); },
    };
    void run();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      ctrl?.abort(); probeCtrl.abort();
      cacheCtl.current = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [jobId]);
  // 顶部播放器 = 全局控制：切换/拖动即解除句级 scope（不自动停）
  const toggle = () => { const a = audioRef.current; if (!a) return; setScope(null); if (a.paused) a.play().catch(() => {}); else a.pause(); };
  const seek = (sec: number) => { const a = audioRef.current; if (a) a.currentTime = sec; setScope(null); setCur(sec); };
  // 句尾 = **下一个真正更晚的时间码**，不是「数组里的下一行」。
  // 时间码只精确到秒，同一秒里常有两行；按 findIndex 取下一行会拿到同为 52:41 的那行，
  // 于是句尾 = 句首，一按就判停——用户看到的是「这一句点了没声音」（2026-08-08 生产实测）。
  const sentenceEnd = (t: string) => {
    const start = toSec(t);
    let end = Infinity;
    for (const s of segs) { const e = toSec(s.t); if (e > start && e < end) end = e; }
    if (end < Infinity) return end;
    // 末句无从定界。别用「句首 +8 秒」估算：转码后的 m4a 时长常比时间码短一点，
    // 估算值落在 duration 之外时浏览器把 seek 夹到末尾 → 一按就 ended，同样是「没声音」。
    const d = audioRef.current?.duration ?? NaN;
    return Number.isFinite(d) && d > 0 ? d : start + 8;
  };
  // 起播前导：时间码只到秒（后端 _seconds_to_hms），且 ASR 标的是「检测到语音」的时刻——
  // 起音（尤其 b/p/t/k/s/sh 这类）在它之前就开始了。从时间码整点起播，听感就是「前几个字没听清」。
  // 0.35s 是气口的长度：够把起音包进来，又不至于把上一句的字带进来。
  // 句尾判停不动（仍是下一个更晚的时间码），所以补前导不会多播。
  const PLAY_LEAD_SEC = 0.35;
  // 句级试听：从句首播到句尾自动停；再点同一句 = 暂停。
  // ⚠️ scope 必须等 seek 真正落定再装：currentTime 赋值是异步的，抢跑装上 scope 的话
  // timeupdate 会拿**上一个播放位置**去比句尾——顺序播放到后面再点前面某句时必然立刻判停，
  // 表现就是「点了播放没反应」（2026-08-08 用户实测：暂停后点某句无效 / 两张卡时第二张无效）。
  const armSeq = useRef(0);
  const playSentence = (t: string) => {
    const a = audioRef.current;
    if (!a || audioGone) return;   // 录音已删：正文点词 / 时间码 / 行尾 ▶ 三条路都从这里走，一处挡住全挡住
    if (playing && scopeRef.current?.t === t) { a.pause(); setScope(null); return; }
    const start = Math.max(0, toSec(t) - PLAY_LEAD_SEC);
    const seq = ++armSeq.current;          // 连点多句时只让最后一次生效
    setScope(null);                        // 先摘掉旧 scope，seek 途中不做任何判停
    const arm = () => {
      if (armSeq.current !== seq) return;
      setScope({ t, end: sentenceEnd(t) });
      a.play().catch(() => {});
    };
    if (Math.abs(a.currentTime - start) < 0.05) arm();
    // 还没加载过任何数据（缓存好之前播放器 preload="none"）：这时设 currentTime 只是记下起点、不会真的 seek，
    // 等 seeked 会永远等不到——直接 play()，浏览器读到时长后自己跳到记下的位置
    else if (a.readyState === 0) { a.currentTime = start; arm(); }
    else { a.addEventListener("seeked", arm, { once: true }); a.currentTime = start; }
    setDetached(false);
  };

  // 播放中的当前句（整行 tint 高亮 + 跟随滚动）
  const activeIdx = useMemo(() => {
    if (!playing) return -1;
    let idx = -1;
    for (let i = 0; i < segs.length; i++) { if (toSec(segs[i].t) <= cur) idx = i; else break; }
    return idx;
  }, [playing, cur, segs]);
  useEffect(() => {
    if (detached || activeIdx < 0 || editing != null) return;
    const c = scrollRef.current, r = rowRefs.current[activeIdx];
    if (c && r) c.scrollTo({ top: Math.max(0, r.offsetTop - c.clientHeight / 3), behavior: "smooth" });
  }, [activeIdx, detached, editing]);
  const reattach = () => {
    setDetached(false);
    const c = scrollRef.current, r = activeIdx >= 0 ? rowRefs.current[activeIdx] : null;
    if (c && r) c.scrollTo({ top: Math.max(0, r.offsetTop - c.clientHeight / 3), behavior: "smooth" });
  };
  // 就近对齐、绝不越过目标（目标已在视口内就一动不动）。
  // ⚠️ **打开稿子时不自动定位**：正文从 0:00 起是对的，复核完的稿子也一样（2026-08-22 Duner 定）。
  // 「右栏卡片指着 1:18、正文停在 0:00」不靠自动滚来解决，靠卡片本身可点（见 queueCard）。
  const scrollToRow = (idx: number) => {
    const c = scrollRef.current, r = idx >= 0 ? rowRefs.current[idx] : null;
    if (!c || !r) return;
    // ⚠️ **不管滚没滚都要闪一下**：就近对齐的规矩是「目标已在视口内就一动不动」，
    // 于是在一屏能装二十行的稿子上，点「跳到这一句」经常什么都不发生——用户只能理解成坏了
    // （2026-08-22 巡检实见，脱敏清单的 ↗ 尤其明显）。滚动是手段，指出是哪一行才是目的。
    setFlashRow(idx);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashRow(null), 1400);
    const next = nearestScrollTop({ top: r.offsetTop, height: r.offsetHeight },
                                  { scrollTop: c.scrollTop, height: c.clientHeight });
    if (next != null) c.scrollTo({ top: next, behavior: "smooth" });
  };
  // 音轨标记点颜色与正文标线同语义：待确认实体=赤陶 / 存疑=金 / 已按证据定字=绿
  const marks: AudioMark[] = items.flatMap((r) => {
    const kind = r.type === "doubt" ? "doubt" as const : needsUser(r) ? "entity" as const : "web" as const;
    const label = r.type === "speaker" ? L("说话人待定", "Speaker unclear")
      : kind === "doubt" ? L("存疑", "Uncertain") : kind === "entity" ? L("待确认实体", "Entity to confirm") : L("已按证据定字", "Settled by evidence");
    return r.occurrences.map((o) => ({ sec: toSec(o.t), kind, label: `${label}：${r.term}` }));
  });

  // ── 说话人归属：三个选项 ──
  // ⚠️ 声明**必须在第一个使用点之前**（下面的「N 位说话人」就用它）：
  //    const 有暂时性死区，声明在后＝打开页面当场 ReferenceError，而 tsc 不报。
  // ⚠️ **写进 `sp` 的是代号**（`SPEAKER_CODES` 里那几个中文词），不是按钮上那串字。
  // 「多人混合」既是代号也是**给读者看的注释**：这一段我们没拆，导出时会显示成
  // 「多人混合：……」（英文界面是 `Multiple speakers: …`），请自行编辑。
  // 三个而不是四个是 Duner 2026-08-28 定的；改措辞只改这一处。
  // 第一行三个都是「这段只有一个人，问是哪一个」；第二行是「这段不止一个人」——
  // **两行回答的是两个不同的问题**，分行是把这层意思做进版面里，不是因为一行放不下。
  // ⚠️ 按钮上的字必须与转录行走**同一个** spName——分两条路的症状是同一屏两种叫法
  //    （日语界面下正文写 Interviewer、按钮写 インタビュアー），而点下去存的是按钮那一个。
  //    也不能走 L()：对照本以英文原句为索引，而「其他」的 `Other` 与脱敏类别清单撞车，
  //    那边是复数语境，于是法/西/葡界面上说话人按钮会显示成「其他们」。
  const SPEAKER_SINGLE = SPEAKER_CODES.slice(0, 3) as readonly string[];   // 主持人 / 被访者 / 其他
  const SPEAKER_MULTI = SPEAKER_CODES[3];                                  // 多人混合
  const chars = segs.reduce((a, s) => a + s.s.length, 0);
  // ⚠️ 「多人混合」不计入人数：它是**我们没能拆开**的记号，不是一个人。
  // 算进去的话，用户每标一段人数就 +1（标十段也只 +1），而那个数字与真实人数无关——
  // 与 `_display_speakers` 里「只有一个说话人时不叫主持人」同一条规矩：不做支撑不了的断言。
  const speakers = new Set(segs.map((s) => s.sp).filter((x) => x && x !== SPEAKER_MULTI)).size;
  const stem = (job.file.name || "transcript").replace(/\.[^.]+$/, "");
  // 下载名统一成「音频名-这一步的名字」：一单最多下四份东西（转录 / 视角转换 / 脱敏 / 质检报告），
  // 不带后缀的话转录稿与加工稿在下载目录里只差扩展名，带后缀才一眼看得出谁是谁。
  // 同名下第二遍的「(1)」交给浏览器——文件在不在用户的下载目录，只有浏览器知道。
  const dlName = `${stem}-${L("转录稿", "Transcript")}`;
  // .txt：有后端任务走导出接口；演示/样本数据前端按同样格式拼（已删除的句子不导出）
  const exportTxt = () => {
    if (jobId) downloadUrl(exportTxtUrl(jobId, dlName, uiLang), `${dlName}.txt`);
    else downloadText(`${dlName}.txt`, segs.filter((_s, i) => deleted[i] == null).map((s) => (s.sp ? `${spName(s.sp)}${speakerSep(uiLang)}${s.s}` : s.s)).join("\n\n"));
  };
  const exportDocx = () => { if (jobId) downloadUrl(exportDocxUrl(jobId, dlName, uiLang), `${dlName}.docx`); };
  const [exportOpen, setExportOpen] = useState(false);
  // Esc 关掉导出菜单：设置/账单/充值三个浮窗都是 Esc 可关的，只有它不是（2026-08-22 巡检）
  useEffect(() => {
    if (!exportOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setExportOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exportOpen]);
  const [exportHover, setExportHover] = useState<string | null>(null);
  const EXPORT_FORMATS = [
    { ext: "docx", name: L("Word 文档", "Word document"), desc: jobId ? L("保留说话人与时间戳 · 推荐", "Speakers & timestamps · recommended") : L("演示数据暂不支持，真实转录可用", "Not available for demo data"), run: exportDocx, disabled: !jobId },
    { ext: "txt", name: L("纯文本", "Plain text"), desc: L("仅正文，无格式", "Text only, no formatting"), run: exportTxt, disabled: false },
  ];
  const primaryExport = jobId
    ? { label: `${L("下载 .docx", "Download .docx")} ↓`, run: exportDocx }
    : { label: `${L("下载 .txt", "Download .txt")} ↓`, run: exportTxt };

  // ── 通用小件 ──
  // ⚠️ padding + 等量负 margin（2026-08-31 穿测）：这些文字链原来 `padding: 0`，
  // 「删除这句」的实际命中区只有 48×18px——比旁边那颗「这一处无误」还小，而它是**不可逆**的。
  // 负 margin 把多出来的 12/16px 抵消掉，所以每一处的视觉位置逐像素不变，只是变得点得着。
  const link = (label: React.ReactNode, onClick: () => void, opt?: { color?: string; u?: boolean }) => (
    <button className="tx-focus" onClick={onClick}
      style={{ border: "none", background: "transparent", padding: "6px 8px", margin: "-6px -8px", fontSize: 12, color: opt?.color ?? semantic.text.muted, cursor: "pointer", textDecoration: opt?.u ? "underline" : "none", whiteSpace: "nowrap", fontFamily: fonts.sans }}>
      {label}
    </button>
  );
  // 展开箭头统一规格（参照 docs/design/early/design-handoff/result.md 的配色逻辑）：15px（▸ 字形偏小，字号补偿）、
  // 收起灰 / 展开变赤陶、旋转过渡
  const caret = (open: boolean) => (
    <span aria-hidden style={{ fontSize: 15, lineHeight: 1, color: open ? semantic.accent.text : semantic.text.muted, transform: open ? "rotate(90deg)" : "none", transition: `transform ${motion.fast}, color ${motion.fast}`, display: "inline-block", flex: "0 0 auto" }}>▸</span>
  );
  const chip = (it: ReviewItem) => {
    // 已确认的项一律只报状态。类别标签（存疑 / 实体 / 联网）回答的是「为什么要你看」，
    // 而顶部绿卡说的是「存疑已全部确认」——同屏两个「存疑」指同一批项、却一个是类别一个像状态，
    // 读起来就是自相矛盾（2026-08-20 Duner 指出）。一个标签只回答一个问题。
    if (itemState(it) === "confirmed") {
      return <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.success.bg, color: semantic.success.text, whiteSpace: "nowrap", flex: "0 0 auto" }}>✓ {L("已确认", "Confirmed")}</span>;
    }
    if (it.type === "doubt") {
      const lab = it.suggestDelete ? L("存疑 · 疑幻觉", "Doubt · hallucination?") : L("存疑", "Uncertain");
      return <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.surface.page, color: semantic.warning.text, whiteSpace: "nowrap", flex: "0 0 auto" }}>{lab}</span>;
    }
    if (it.type === "speaker") {
      return <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.accent.bgTint, color: semantic.accent.text, whiteSpace: "nowrap", flex: "0 0 auto" }}>{L("说话人待定", "Speaker unclear")}</span>;
    }
    if (it.type === "web") return <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.success.bg, color: semantic.success.text, whiteSpace: "nowrap", flex: "0 0 auto" }}>{L("联网 ✓", "Web ✓")}</span>;
    return <span style={{ fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: radius.pill, background: semantic.accent.bgTint, color: semantic.accent.text, whiteSpace: "nowrap", flex: "0 0 auto" }}>{it.tag || L("实体", "Entity")}</span>;
  };
  // 点点条状态色：待定空心 / 无误·改写实心绿 / 已替换实心赤陶 / 已删除实心灰 / 当前实心墨+外环
  const dotStyle = (state: string, small?: boolean): React.CSSProperties => {
    const sz = small ? 7 : 8;
    const base: React.CSSProperties = { width: sz, height: sz, borderRadius: "50%", boxSizing: "border-box", flex: "0 0 auto", display: "inline-block" };
    if (state === "current") return { ...base, background: semantic.text.primary, outline: `1.5px solid ${semantic.text.primary}`, outlineOffset: 2 };
    if (state === "ok" || state === "edited") return { ...base, background: semantic.success.graphic };
    if (state === "renamed") return { ...base, background: semantic.accent.brand };
    if (state === "deleted") return { ...base, background: semantic.text.ghost };
    return { ...base, border: `1.5px solid ${semantic.border.strong}` };
  };
  // 合并替换控件：标签 + 输入 + （输入有效新词后浮现的）执行键，Enter 同效
  const replaceCtl = (label: string, value: string, placeholder: string, onChange: (v: string) => void, onExec: () => void, changed: boolean) => (
    <div className="tx-field" style={{ display: "inline-flex", alignItems: "center", height: 32, border: `1px solid ${changed ? semantic.accent.brand : semantic.border.strong}`, borderRadius: radius.sm, background: semantic.surface.raised, padding: "0 3px 0 12px", gap: space.s2, flex: 1, minWidth: 165, transition: `border-color ${motion.fast}`, boxSizing: "border-box" }}>
      <span style={{ fontSize: 13, fontWeight: 500, color: changed ? semantic.accent.text : semantic.text.secondary, flex: "0 0 auto", whiteSpace: "nowrap" }}>{label}</span>
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && changed) onExec(); }}
        style={{ flex: 1, minWidth: 36, border: "none", background: "transparent", fontSize: 13, fontWeight: 600, color: semantic.text.primary, outline: "none", padding: 0, fontFamily: "inherit" }} />
      {changed && (
        <button className="tx-focus" onClick={onExec}
          style={{ border: "none", background: semantic.accent.fill, color: semantic.text.onAccent, height: 26, padding: "0 10px", borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: "pointer", flex: "0 0 auto", fontFamily: fonts.sans }}>
          {L("替换 ↵", "Replace ↵")}
        </button>
      )}
    </div>
  );
  // ── 双视图决策卡 · 视图 1（总览）：全部出现处列表 + 项级动作（这一屏没有处级动作） ──
  const overviewBody = (it: ReviewItem) => {
    const k = keyOf(it);
    const rest = unresolvedIdxs(it);
    const draftA = draftsAll[k] ?? "";
    const changed = !!draftA.trim() && draftA.trim() !== it.term;
    const someDone = rest.length < it.occurrences.length;
    // 同一个词的多处存疑，各处证据本就可能不同（一处四路分歧、一处语速过快）。原因不一致时
    // 卡顶那一条就不该冒充全卡的解释——改成点名「各不相同」，逐处的原因贴在各自那一行下。
    const mixedReasons = new Set(it.occurrences.map((_, i) => reasonOf(it, i)).filter(Boolean)).size > 1;
    return (
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: -0.2 }}>{it.term}</span>
          {chip(it)}
        </div>
        <div style={{ fontSize: 12, lineHeight: 1.65, color: semantic.text.muted, marginTop: 6 }}>
          {mixedReasons
            ? L.t("{0} 处各自存疑，原因不同——逐处查看。", "{0} occurrences flagged for different reasons — review each.", it.occurrences.length)
            : reasonOf(it, 0)}
        </div>
        <div style={{ background: semantic.surface.page, borderRadius: radius.sm, padding: "5px 4px", marginTop: space.s3 }}>
          {it.occurrences.map((o, i) => {
            const stt = occState(it, i);
            const r = resOf(k, i);
            const term = r?.kind === "renamed" ? r.to : it.term;
            const ti = o.lineText.indexOf(term);
            return (
              <button key={i} className="tx-focus" onClick={() => openItem(it, i, "detail")} title={L("逐处确认这一处", "Review this occurrence")}
                onMouseEnter={(e) => { e.currentTarget.style.background = semantic.surface.raised; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                style={{ display: "flex", alignItems: "center", gap: space.s2, width: "100%", border: "none", background: "transparent", cursor: "pointer", padding: "5px 8px", borderRadius: 6, textAlign: "left", fontFamily: fonts.sans, transition: `background ${motion.fast}` }}>
                <span style={dotStyle(stt, true)} />
                <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, flex: "0 0 36px", fontVariantNumeric: "tabular-nums" }}>{mmss(o.t)}</span>
                <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0, flex: 1 }}>
                  <span style={{ fontSize: 12, color: stt === "pending" ? semantic.text.primary : semantic.text.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {ti >= 0
                      ? <>{o.lineText.slice(0, ti)}<b style={{ borderBottom: `2px solid ${stt === "renamed" ? semantic.success.graphic : semantic.accent.brand}`, fontWeight: 600 }}>{term}</b>{o.lineText.slice(ti + term.length)}</>
                      : o.lineText}
                  </span>
                  {/* 原因各异时贴在各自那一行下：不点进去也能看出这几处为什么被分别标出来 */}
                  {mixedReasons && reasonOf(it, i) && (
                    <span style={{ fontSize: 11, color: semantic.text.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{reasonOf(it, i)}</span>
                  )}
                </span>
                <span style={{ color: semantic.text.ghost, fontSize: 12, flex: "0 0 auto" }}>›</span>
              </button>
            );
          })}
        </div>
        {/* 第一行：全部无误 + 跳过；替换框改独占第二行，输入框才够宽显示完整替换词 */}
        <div style={{ display: "flex", gap: space.s2, marginTop: space.s3, alignItems: "center", flexWrap: "wrap" }}>
          {/* 项级快捷保留，但作用范围写在脸上——一键放行的是 N 个**理由各不相同**的判断 */}
          {rest.length > 0 && (
            <Button primary size="sm" onClick={() => okAll(it)}>
              ✓ {someDone ? L.t("其余 {0} 处无误", "Remaining {0} correct", rest.length) : L.t("{0} 处全部无误", "All {0} correct", rest.length)}
              {mixedReasons ? L("（原因各不相同）", " (differing reasons)") : ""}
            </Button>
          )}
          {rest.length === 0 && <span style={{ fontSize: 12, color: semantic.success.text }}>✓ {L("全部处理完毕", "All handled")}</span>}
          {/* 都处理完了就没什么可「跳过」的——留着只会让人以为还欠着什么 */}
          <span style={{ flex: "1 0 auto", textAlign: "right" }}>{needsUser(it) && itemState(it) !== "confirmed" ? link(L("跳过（暂不处理）", "Skip for now"), () => skipItem(it)) : null}</span>
        </div>
        {rest.length > 0 && (
          <div style={{ display: "flex", marginTop: space.s2 }}>
            {replaceCtl(
              someDone ? L("其余替换为", "Replace rest with") : L("全部替换为", "Replace all with"),
              draftA, it.term,
              (v) => setDraftsAll((m) => ({ ...m, [k]: v })),
              () => replaceAll(it), changed
            )}
          </div>
        )}
      </>
    );
  };

  // 定归属 = 改这一行的 sp + 该处记账。走 setSegs，于是自动被 0.8s 防抖同步到后端（导出即用）
  // ⚠️ **`code` 是代号，不是按钮上那串字**（2026-08-30 修）。存显示文字的话翻译就只剩单向：
  //    英文界面点了 Other，中文用户打开同一份稿子还是看到 Other；而且后处理的输入稿
  //    （`app/redact_diff.segments_to_qa`）按这几个中文代号匹配，存别的语言会匹配不上。
  const setSpeakerAt = (it: ReviewItem, i: number, code: string) => {
    const rowIdx = occRow(it, i);
    if (rowIdx >= 0) setSegs((prev) => prev.map((sg, j) => (j === rowIdx ? { ...sg, sp: code } : sg)));
    setRes(it, [[i, { kind: "renamed", to: code, prev: it.occurrences[i].speaker || "" }]], { advance: true });
  };

  // ── 双视图决策卡 · 视图 2（逐处）：点点条 + 句级播放 + 当前处原句 + 处级动作 ──
  const detailBody = (it: ReviewItem, multi: boolean) => {
    const k = keyOf(it);
    const N = it.occurrences.length;
    const idx = Math.min(focusIdx[k] ?? 0, N - 1);
    const o = it.occurrences[idx];
    const r = resOf(k, idx);
    const isEntity = it.type === "entity" || it.type === "web";
    const draftO = draftsOne[k] ?? "";
    const changed = !!draftO.trim() && draftO.trim() !== occTerm(it, idx);
    const editingThis = editingOcc?.k === k && editingOcc.i === idx;
    const arm = deleteArm?.k === k && deleteArm.i === idx;
    const seg = segs[occRow(it, idx)];   // 按行号取含词那行（不按会重复的时间戳，否则显示错误行的句子）
    const lineNow = seg?.s ?? o.lineText;
    const term = occTerm(it, idx);
    const probe = it.type === "doubt" && it.term.startsWith("「") ? "[听不清]" : term;
    // ⚠️ 说话人卡不划词级高亮：term 是句首截来的一段（见 review._speaker_items），
    // indexOf 必然命中 0，于是卡里的原句会被从头划亮 18 个字——一条没有含义的线。
    const ti = it.type === "speaker" ? -1 : lineNow.indexOf(probe);
    const goToOcc = (i2: number) => {
      setFocusIdx((m) => ({ ...m, [k]: i2 }));
      setEditingOcc(null);
      setDeleteArm(null);
      setDraftsOne((m) => ({ ...m, [k]: "" }));
      scrollToRow(occRow(it, i2));
    };
    return (
      <>
        <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
          {multi && link(L("‹ 总览", "‹ Overview"), () => { setItemView((m) => ({ ...m, [k]: "overview" })); setEditingOcc(null); setDeleteArm(null); }, { color: semantic.text.secondary })}
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: -0.2 }}>{it.term}</span>
          {chip(it)}
        </div>
        {/* 原因跟着**这一处**走（o.reason），不是整张卡一份——同词多处时各处的证据本就不同。
            多处项过去这里整块不显示（只在总览卡顶显示首处那一条），于是你点进第 2 处看到的
            原因其实属于第 1 处（2026-08-12 生产实测）。老任务无 o.reason → 回落卡级。 */}
        {reasonOf(it, idx) && (
          <div style={{ marginTop: 6 }}>
            <div style={{ fontSize: 12, lineHeight: 1.65, color: semantic.text.muted, ...(reasonOpen[`${k}#${idx}`] || reasonOf(it, idx).length <= 46 ? {} : { display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }) }}>{reasonOf(it, idx)}</div>
            {reasonOf(it, idx).length > 46 && link(reasonOpen[`${k}#${idx}`] ? L("收起", "Show less") : L("展开", "Show more"), () => setReasonOpen((m) => ({ ...m, [`${k}#${idx}`]: !reasonOpen[`${k}#${idx}`] })), { color: semantic.accent.text })}
            {o.anchorFallback && o.anchorT && (
              <div style={{ fontSize: 11, color: semantic.text.muted, marginTop: 3 }}>
                {L.t("报告标注在 {0}，未能精确定位到某一句——下方为该词的全部出现处。", "Flagged at {0} in the report but not pinned to one line — all occurrences are listed below.", o.anchorT)}
              </div>
            )}
          </div>
        )}
        <div style={{ background: semantic.surface.page, borderRadius: radius.sm, padding: space.s3, marginTop: space.s3 }}>
          <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
            {/* 点点条 = 处级进度 + 导航三合一；>12 处降级为分数导航 */}
            {multi && N <= 12 && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                {it.occurrences.map((oo, i2) => (
                  <button key={i2} className="tx-focus" onClick={() => goToOcc(i2)} title={`${L.t("第 {0} 处", "#{0}", i2 + 1)} · ${mmss(oo.t)}`}
                    style={{ border: "none", background: "transparent", padding: 5, margin: -5, cursor: "pointer", display: "inline-grid", placeItems: "center" }}>
                    <span style={dotStyle(i2 === idx ? "current" : occState(it, i2))} />
                  </button>
                ))}
              </span>
            )}
            {multi && N > 12 && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: space.s1 }}>
                <button className="tx-focus" aria-label={L("上一处", "Previous")} disabled={idx === 0} onClick={() => goToOcc(idx - 1)}
                  style={{ border: "none", background: "transparent", fontSize: 14, color: idx === 0 ? semantic.text.ghost : semantic.text.secondary, cursor: idx === 0 ? "default" : "pointer", padding: "0 2px" }}>‹</button>
                <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, fontVariantNumeric: "tabular-nums" }}>{idx + 1} / {N}</span>
                <button className="tx-focus" aria-label={L("下一处", "Next")} disabled={idx === N - 1} onClick={() => goToOcc(idx + 1)}
                  style={{ border: "none", background: "transparent", fontSize: 14, color: idx === N - 1 ? semantic.text.ghost : semantic.text.secondary, cursor: idx === N - 1 ? "default" : "pointer", padding: "0 2px" }}>›</button>
              </span>
            )}
            <span style={{ flex: 1 }} />
            {/* 时间戳只做「跳到原文这一句」定位（不再内置播放按钮）——去掉后无重复翻滚、卡片更宽裕，
                用户到左边正文点时间戳自己听 */}
            <button className="tx-focus" onClick={() => scrollToRow(occRow(it, idx))} title={L("跳到原文这一句", "Jump to this line in the transcript")}
              style={{ border: "none", background: "transparent", cursor: "pointer", fontFamily: fonts.mono, fontSize: 11, color: semantic.text.secondary, fontVariantNumeric: "tabular-nums", padding: 0 }}>
              {mmss(o.t)}
            </button>
          </div>
          {/* 当前处原句（取正文实时文本，改过名/改写后即时反映）；改写时隐藏，避免与可编辑副本双倍占高 */}
          {!editingThis && (
            <>
              {/* 整句可点 = 回到正文那一句。此前只有右上角那个 11px 的时间戳能定位，
                  而一旦按了顶部播放器、正文跟着播放滚走，用户手边最显眼的就是这张卡——
                  点它却什么都不发生（2026-08-21 Duner 实测）。button 而非 div onClick：
                  键盘可达，且与卡内其它动作同一交互语言。 */}
              <button className="tx-focus" onClick={() => scrollToRow(occRow(it, idx))}
                title={L("回到正文这一句", "Jump to this line in the transcript")}
                dir={rtl ? "rtl" : undefined}
                style={{ display: "block", width: "100%", textAlign: rtl ? "right" : "left", border: "none", background: "transparent", padding: 0, cursor: "pointer", font: "inherit", fontSize: 13, lineHeight: lh.body, color: semantic.text.primary, marginTop: space.s3, ...(sentOpen[k] || lineNow.length <= 54 ? {} : { display: "-webkit-box", WebkitLineClamp: 4, WebkitBoxOrient: "vertical", overflow: "hidden" }) }}>
                「{ti >= 0
                  ? <>{lineNow.slice(0, ti)}<span style={{ background: it.type === "doubt" ? semantic.surface.page : semantic.accent.bgTint, borderBottom: it.type === "doubt" ? `2px dotted ${semantic.warning.icon}` : `2px solid ${semantic.accent.brand}`, padding: "1px 2px", borderRadius: 3, fontWeight: it.type === "doubt" ? 500 : 400 }}>{probe === "[听不清]" ? `[${it.term.replace(/^「|」$/g, "")}]` : term}</span>{lineNow.slice(ti + probe.length)}</>
                  : lineNow}」
              </button>
              {lineNow.length > 54 && link(sentOpen[k] ? L("收起", "Show less") : L("展开全句", "Show full"), () => setSentOpen((m) => ({ ...m, [k]: !sentOpen[k] })), { color: semantic.accent.text })}
            </>
          )}
          {r ? (
            // 已决回看：撤销 = 前向操作（改回原词 / 重新决定），不撒谎
            <div style={{ display: "flex", alignItems: "center", gap: space.s2, marginTop: space.s3, fontSize: 12 }}>
              <span style={{ color: semantic.success.text }}>
                {r.kind === "renamed" ? L.t("✓ 这一处已改为「{0}」", "✓ Renamed to \"{0}\"", r.to)
                  : r.kind === "edited" ? L("✓ 这一处已人工改写", "✓ Rewritten by you")
                  : r.kind === "deleted" ? L("已删除这句", "Line deleted")
                  : L("✓ 这一处已确认无误", "✓ Confirmed correct")}
              </span>
              <span style={{ flex: 1 }} />
              {r.kind === "renamed" && link(L("改回原词", "Revert"), () => revertOcc(it, idx), { color: semantic.accent.text, u: true })}
              {(r.kind === "ok" || r.kind === "edited") && link(L("重新决定", "Redecide"), () => setRes(it, [[idx, null]]), { color: semantic.accent.text, u: true })}
            </div>
          ) : editingThis ? (
            <>
              <div style={{ fontSize: 11, color: semantic.text.muted, marginTop: space.s3, marginBottom: 4 }}>{L("改写整句 · 原句已载入，直接编辑", "Edit the whole line — original is loaded")}</div>
              {/* dir 设在 textarea 上，光标落点与标点位置才跟着走；不设时阿语基本没法编辑 */}
              <textarea className="tx-scroll" dir={rtl ? "rtl" : undefined} autoFocus value={editDraft} onChange={(e) => setEditDraft(e.target.value)}
                rows={Math.max(2, Math.min(8, Math.ceil(editDraft.length / 20)))}
                style={{ width: "100%", boxSizing: "border-box", border: `1px solid ${semantic.text.primary}`, background: semantic.surface.raised, borderRadius: radius.sm, padding: "10px 12px", fontSize: 13, lineHeight: lh.body, color: semantic.text.primary, fontFamily: "inherit", resize: "vertical", maxHeight: 188, overflowY: "auto" }} />
              <div style={{ display: "flex", gap: space.s2, marginTop: space.s3 }}>
                <Button primary size="sm" onClick={() => saveEditOcc(it, idx)}>{L("保存改写", "Save rewrite")}</Button>
                <Button ghost size="sm" onClick={() => setEditingOcc(null)}>{L("取消", "Cancel")}</Button>
              </div>
            </>
          ) : (
            it.type === "speaker" ? (
            // 说话人卡只回答一件事：这一段算谁说的。**不给「无误」**——模型已经说了它定不下来，
            // 一个不表态的出口只会让人点它了事，那就等于没问。三个键都是明确的归属。
            <div style={{ marginTop: space.s3 }}>
              <div style={{ display: "flex", gap: space.s2, flexWrap: "wrap", alignItems: "center" }}>
                {SPEAKER_SINGLE.map((code) => (
                  <Button key={code} size="sm" primary={code === o.speaker} onClick={() => setSpeakerAt(it, idx, code)}>{spName(code)}</Button>
                ))}
              </div>
              {/* 细线分组：不画的话四个键分两行会被读成「只是放不下才换行」 */}
              <div style={{ display: "flex", gap: space.s2, alignItems: "center", marginTop: space.s3, paddingTop: space.s3, borderTop: `1px solid ${semantic.border.subtle}` }}>
                <Button size="sm" primary={SPEAKER_MULTI === o.speaker} onClick={() => setSpeakerAt(it, idx, SPEAKER_MULTI)}>{spName(SPEAKER_MULTI)}</Button>
                <span style={{ fontSize: 11, color: semantic.text.muted }}>{L("这一段里不止一个人说话", "More than one person in this line")}</span>
              </div>
            </div>
            ) : (
            <div style={{ display: "flex", gap: space.s2, marginTop: space.s3, flexWrap: "wrap", alignItems: "center" }}>
              <Button primary size="sm" onClick={() => okThis(it, idx)}>✓ {L("这一处无误", "This one is correct")}</Button>
              {isEntity && replaceCtl(L("替换为", "Replace with"), draftO, occTerm(it, idx),
                (v) => setDraftsOne((m) => ({ ...m, [k]: v })),
                () => replaceThis(it, idx), changed)}
              {!isEntity && <Button secondary size="sm" onClick={() => { setEditingOcc({ k, i: idx }); setEditDraft(lineNow); }}>{L("改写", "Rewrite")}</Button>}
            </div>
            )
          )}
        </div>
        {/* gap 用 s5(20) 不用 s3(12)：link() 左右各有 -8px 负外边距（撑命中区用的），
            gap 小于 16 时相邻两个链接的**点击盒会重叠**，重叠部分归后一个——
            「跳过」会吃掉「删除这句」右边 4px，而加大命中区正是那一改的目的。
            20 − 16 = 4px 净空。可见间距仍等于 gap（负外边距与内边距互相抵消），
            所以这里从 12 变 20 是视觉上真的宽了 8px，不是补偿。 */}
        <div style={{ display: "flex", gap: space.s5, marginTop: space.s3, paddingTop: space.s3, borderTop: `1px solid ${semantic.border.subtle}`, alignItems: "center", fontSize: 12 }}>
          <span style={{ flex: 1 }} />
          {/* 「删除这句」一律留在这条脚注里：疑幻觉那处也不升为醒目按钮——递刀子的界面会诱导误删，
              标签上的「存疑 · 疑幻觉」已经把该说的说清楚了 */}
          {/* ⚠️ 待确认那一态走**警示金**，不走赤陶（2026-08-31）：赤陶是「我们希望你点的东西」
              ——同一张卡上「✓ 这一处无误」正是这个颜色。把一个不可逆的破坏动作染成同一色系，
              等于让两个方向相反的动作长得一样。金色在本应用里已经是「停一下看清楚」那一档
              （选错格式、余额不够都用它），语义对得上，也不必为此新增一个红。 */}
          {it.type === "doubt" && !r && !editingThis && (arm
            ? link(`${L("确认删除？", "Confirm delete?")} ${deleteArm!.n}`, () => doDelete(it, idx), { color: semantic.warning.text, u: true })
            : link(L("删除这句", "Delete this line"), () => armDelete(it, idx)))}
          {/* 整项都定完了就没什么可「跳过」的——留着只会让人以为还欠着什么（同总览视图） */}
          {needsUser(it) && itemState(it) !== "confirmed" ? link(L("跳过（暂不处理）", "Skip for now"), () => skipItem(it)) : null}
        </div>
      </>
    );
  };

  // ── 队列卡（折叠 / 已完成 / 已搁置 / 当前展开） ──
  const queueCard = (it: ReviewItem) => {
    const k = keyOf(it);
    const st = itemState(it);
    const isCur = currentKey === k;
    const renamed = it.occurrences.filter((_, i) => resOf(k, i)?.kind === "renamed").length;
    const delCount = it.occurrences.filter((_, i) => resOf(k, i)?.kind === "deleted").length;
    if (st === "confirmed" && !isCur) {
      const canUndo = it.occurrences.some((_, i) => { const r = resOf(k, i); return r?.kind === "ok" || r?.kind === "edited"; });
      return (
        <div key={k} data-review-card={k} data-flash={flashCard === k ? "1" : undefined} style={{ background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, padding: `${space.s3 - 1}px ${space.s3}px`, display: "flex", alignItems: "center", gap: space.s2, opacity: 0.72, flex: "0 0 auto" }}>
          <span style={{ width: 16, height: 16, borderRadius: "50%", background: semantic.success.graphic, color: semantic.text.onAccent, display: "grid", placeItems: "center", fontSize: 8, flex: "0 0 auto" }}>✓</span>
          <button className="tx-focus" onClick={() => openItem(it, 0, it.occurrences.length > 1 ? "overview" : "detail")}
            style={{ border: "none", background: "transparent", padding: 0, fontSize: 13, fontWeight: 600, color: semantic.text.primary, cursor: "pointer", fontFamily: fonts.sans, textAlign: "left" }}>
            {it.term}
          </button>
          {renamed > 0 && <span style={{ fontSize: 11, color: semantic.text.muted, whiteSpace: "nowrap" }}>{L.t("已改 {0} 处", "{0} renamed", renamed)}</span>}
          {delCount > 0 && <span style={{ fontSize: 11, color: semantic.text.muted, whiteSpace: "nowrap" }}>{L("已删除", "Deleted")}</span>}
          <span style={{ flex: 1 }} />
          {canUndo && link(L("撤销", "Undo"), () => undoOks(it))}
        </div>
      );
    }
    if (st === "skipped" && !isCur) {
      return (
        <div key={k} data-review-card={k} data-flash={flashCard === k ? "1" : undefined} style={{ background: semantic.surface.raised, border: `1px dashed ${semantic.border.strong}`, borderRadius: radius.md, padding: `${space.s3 - 1}px ${space.s3}px`, display: "flex", alignItems: "center", gap: space.s2, flex: "0 0 auto" }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: semantic.text.secondary }}>{it.term}</span>
          {chip(it)}
          <span style={{ flex: 1 }} />
          {link(L("恢复", "Restore"), () => openItem(it), { color: semantic.accent.text })}
        </div>
      );
    }
    if (!isCur) {
      return (
        <div key={k} data-review-card={k} data-flash={flashCard === k ? "1" : undefined} className="tx-focus" role="button" tabIndex={0} onClick={() => openItem(it)} onKeyDown={(e) => { if (e.key === "Enter") openItem(it); }}
          style={{ background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, padding: `${space.s3 - 1}px ${space.s3}px`, display: "flex", alignItems: "center", gap: space.s2, cursor: "pointer", flex: "0 0 auto", transition: `border-color ${motion.fast}` }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{it.term}</span>
          {chip(it)}
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted }}>{mmss(it.occurrences[0]?.t ?? "0")}</span>
        </div>
      );
    }
    const multi = it.occurrences.length > 1;
    const view = itemView[k] ?? (multi ? "overview" : "detail");
    // 整张当前卡都是「回到正文这一句」的靶子——点空白处即可（2026-08-22 Duner）。
    // 在此之前只有卡里那句话和时间码可点，而正文打开时停在 0:00、卡片指着 3:29，
    // 两边对不上时用户最自然的动作正是「点一下这张卡」，却什么都不发生。
    // 落点跟着当前视图走：总览看的是整项 → 第 1 处；详情看的是某一处 → 那一处。
    const target = occRow(it, Math.min(multi && view === "overview" ? 0 : (focusIdx[k] ?? 0), it.occurrences.length - 1));
    return (
      <div key={k} data-review-card={k} data-flash={flashCard === k ? "1" : undefined}
        onMouseEnter={() => setCardHover(true)} onMouseLeave={() => setCardHover(false)}
        // 卡里的按钮/输入框各有各的事，只有落在空白处才跳
        onClick={(e) => {
          if ((e.target as HTMLElement).closest('button,input,textarea,select,a,[role="button"]')) return;
          scrollToRow(target);
        }}
        title={L("点卡片空白处回到正文这一句", "Click anywhere blank to jump to this line")}
        style={{ position: "relative", cursor: "pointer", background: semantic.surface.raised, border: `1px solid ${semantic.accent.brand}`, borderRadius: radius.md, padding: space.s4, boxShadow: shadow.md, flex: "0 0 auto" }}>
        {/* 可点提示挂在上边框上（fieldset legend 那种做法）：卡内左上是「‹ 总览」、
            右上是词与标签，只有边框上这条带永远不会跟内容打架 */}
        <span aria-hidden="true" style={{ position: "absolute", top: -8, left: 14, padding: "0 6px", background: semantic.surface.raised, fontSize: 11, fontFamily: fonts.sans, color: semantic.accent.text, whiteSpace: "nowrap", pointerEvents: "none", opacity: cardHover ? 1 : 0, transition: `opacity ${motion.fast}` }}>
          ↩ {L("回到正文这一句", "Jump to this line")}
        </span>
        {multi && view === "overview" ? overviewBody(it) : detailBody(it, multi)}
      </div>
    );
  };

  // ── 正文行内标记：队列实体=实线赤陶 / 存疑=点线警告金 / 已定字=实线绿 / 已决=细灰下划线 ──
  const renderMarked = (seg: TranscriptRow) => {
    // ⚠️ 说话人卡不进行内标记：它的 term 是**句首截来的一段**，不是一个真的词——
    // 划下划线等于在句子中间随便划一道，读者会以为那几个字有问题。它的位置由卡片本身指出。
    const lineItems = items.filter((r) => r.type !== "speaker" && r.occurrences.some((o) => o.t === seg.t));
    let nodes: React.ReactNode[] = [seg.s];
    lineItems.forEach((it) => {
      const k = keyOf(it);
      const oi = it.occurrences.findIndex((o) => o.t === seg.t);
      const r = resOf(k, oi);
      if (r && (r.kind === "edited" || r.kind === "deleted")) return; // 改写/删除后整句已变，不再标记
      const term = r?.kind === "renamed" ? r.to : it.term;
      const probe = it.type === "doubt" && it.term.startsWith("「") ? "[听不清]" : term;
      const done = r != null || itemState(it) === "confirmed";
      const isFocus = currentKey === k;
      const audit = !needsUser(it); // 已定字：与「已核实」同语义的绿
      let st: React.CSSProperties;
      if (done) st = { borderBottom: `1px solid ${semantic.border.strong}`, cursor: "pointer" };
      else if (it.type === "doubt") st = { background: semantic.surface.page, borderBottom: `2px dotted ${semantic.warning.icon}`, padding: "1px 2px", borderRadius: 3, fontWeight: 500, cursor: "pointer" };
      else if (it.type === "web" || audit) st = { background: semantic.success.bg, borderBottom: `2px solid ${semantic.success.graphic}`, padding: "1px 2px", borderRadius: 3, cursor: "pointer" };
      else st = { background: semantic.accent.bgTint, borderBottom: `2px solid ${semantic.accent.brand}`, padding: "1px 2px", borderRadius: 3, cursor: "pointer" };
      if (isFocus && !done) st.boxShadow = "0 0 0 2px rgba(200,85,61,.22)";
      nodes = nodes.flatMap((n, ni) => {
        if (typeof n !== "string" || !n.includes(probe)) return [n];
        const parts = n.split(probe);
        const out: React.ReactNode[] = [];
        parts.forEach((p, pi) => {
          out.push(p);
          if (pi < parts.length - 1) {
            out.push(
              <span key={`${k}-${ni}-${pi}`} className="tx-focus" role="button" tabIndex={0} style={st} title={reasonOf(it, oi)}
                onClick={() => {
                  if (audit) { setCurrentKey(k); revealCard(it); }
                  else { openItem(it, oi, "detail"); playSentence(seg.t); revealCard(it); }
                }}
                onKeyDown={(e) => { if (e.key === "Enter") { if (audit) { setCurrentKey(k); revealCard(it); } else { openItem(it, oi, "detail"); playSentence(seg.t); revealCard(it); } } }}>
                {probe === "[听不清]" ? `[${it.term.replace(/^「|」$/g, "")}]` : term}
              </span>
            );
          }
        });
        return out;
      });
    });
    return nodes;
  };

  // 处级高亮：逐处视图（或单处项）才亮当前处所在行；总览不亮
  const curItem = useMemo(() => [...queue, ...auto].find((q) => keyOf(q) === currentKey) ?? null, [queue, auto, currentKey]);
  // 高亮的目标「行号」（不是时间戳）——精确到含词的那行，不会连累同时间戳的相邻行
  const highlightRow = (() => {
    if (!curItem || !currentKey) return -1;
    const view = itemView[currentKey];
    if (!(view === "detail" || curItem.occurrences.length === 1)) return -1;
    return occRow(curItem, Math.min(focusIdx[currentKey] ?? 0, curItem.occurrences.length - 1));
  })();

  // 转录行的版式常量（方案乙，2026-08-30）。
  // 上下留白不对称：上 6 下 8，让「标签 + 它的正文」抱成一组，行与行之间才分得开。
  // 实测（生产真实终稿 9 门语种 × 4 门界面）整篇总高 1.17–1.36 倍，上限 1.4。
  const ROW_PAD_TOP = 6;
  const ROW_PAD_BOT = 8;
  const SP_LH = 1.3;          // 标签行行高。1.35 是 1.40 倍、压线；1.3 是 1.36 倍
  const TS_W = 52;            // 时间码列宽（正文靠它 + 一个间距缩进，与标签左缘对齐）
  // 同一个人连着说时不重复标签。比的是**代号**，所以与界面语言无关。
  const sameSpeakerAsPrev = (i: number) => i > 0 && !!segs[i - 1]?.sp && segs[i - 1].sp === segs[i]?.sp;

  // ── 正文一行 ──
  const row = (i: number, s: TranscriptRow) => {
    const guest = isGuest(s.sp);
    const isDel = deleted[i] != null;
    const isCurrentLine = highlightRow === i;
    if (editing === i) {
      return (
        <div key={i} style={{ display: "flex", gap: space.s4, padding: `${space.s2 - 1}px 0` }}>
          <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, minWidth: 52, paddingTop: 5 }}>{mmss(s.t)}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
              style={{ width: "100%", border: `1px solid ${semantic.text.primary}`, background: semantic.surface.page, borderRadius: radius.sm, padding: `${space.s3 - 1}px ${space.s3}px`, fontFamily: fonts.sans, fontSize: 15, lineHeight: lh.read, color: semantic.text.primary, resize: "vertical", minHeight: 64, boxSizing: "border-box" }} />
            <div style={{ display: "flex", gap: space.s2, marginTop: space.s2 }}>
              <Button primary size="sm" onClick={saveEdit}>{L("保存", "Save")}</Button>
              <Button ghost size="sm" onClick={() => setEditing(null)}>{L("取消", "Cancel")}</Button>
            </div>
          </div>
        </div>
      );
    }
    const showOps = hovered === i && !isDel;
    const isPlayingLine = i === activeIdx;
    const isFlash = i === flashRow;
    const rowBg = isCurrentLine ? semantic.surface.rowActive : (isPlayingLine || isFlash) ? semantic.accent.bgTint : "transparent";
    return (
      <div key={i} ref={(el) => { rowRefs.current[i] = el; }} data-flash={isFlash ? "1" : undefined}
        onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)}
        dir={rtl ? "rtl" : undefined}
        style={{ padding: isCurrentLine || isPlayingLine || isFlash ? `${ROW_PAD_TOP}px ${space.s3}px ${ROW_PAD_BOT}px` : `${ROW_PAD_TOP}px 0 ${ROW_PAD_BOT}px`, margin: isCurrentLine || isPlayingLine || isFlash ? `0 -${space.s3}px` : 0, background: rowBg, borderRadius: radius.sm, boxShadow: isCurrentLine || isFlash ? `inset ${rtl ? -3 : 3}px 0 0 ${semantic.accent.brand}` : "none", transition: `background ${motion.fast}` }}>
        {/* ── 标签行：时间码 + 说话人 ──
            ⚠️ 说话人在正文**上方**、不是左边的一列（2026-08-30 改）。原来那一列宽度写死 44px
            ——照中文四个字定的。译文长两三倍（`Persona entrevistada` 126px），而 44 其实是
            **下限不是宽度**：超了浏览器把列撑开，于是每行被推开的距离不同，**正文左缘参差**，
            而那一列存在的唯一理由就是让正文对齐。上移之后标签多长都不挤正文，这条约束被删掉
            而不是调大——以后加语种、改译法都不用回头量宽度。
            ⚠️ 标签必须**明显小于正文**（11px vs 15px）：整篇总高实测 1.17–1.36 倍，
            12px 就是 1.44 倍，超出可接受范围（Duner 2026-08-30 定的上限是 1.4）。
            时间码与说话人放在同一行，因为它们回答同一个问题：这段是谁、在第几分钟。 */}
        <div style={{ display: "flex", gap: space.s4, alignItems: "baseline", marginBottom: 0 }}>
          {/* ⚠️ 时间码 dir="ltr" 钉死：整行镜像时它会跟着翻，而数字方向本身有含义
              （0:10 不能读成 10:0）。业界共识是「RTL 不是简单地整体照镜子」——
              该翻的是布局（说话人 / 操作按钮），不该翻的是数字与时间。 */}
          {audioGone
            ? <span dir="ltr" style={{ fontFamily: fonts.mono, fontSize: 11, lineHeight: SP_LH, color: semantic.text.muted, minWidth: TS_W, display: "inline-block" }}>{mmss(s.t)}</span>
            : <button className="tx-focus" dir="ltr" onClick={() => playSentence(s.t)} title={L("听这一句（播到句尾自动停）", "Play this line (stops at end)")}
            style={{ fontFamily: fonts.mono, fontSize: 11, lineHeight: SP_LH, color: isPlayingLine && !isCurrentLine ? semantic.accent.text : semantic.text.muted, minWidth: TS_W, border: "none", background: "transparent", cursor: "pointer", textAlign: "left", padding: 0 }}>{mmss(s.t)}</button>}
          {/* 同一个人连着说只标一次。生产终稿里这种情况是 0%（P3 出稿时已把连续同段合并了），
              所以它只在**说话人分离退化成一个人**时才起作用——那时每一行都是同一个标签。 */}
          {!sameSpeakerAsPrev(i) && (
            <span style={{ fontSize: 11, fontWeight: 600, lineHeight: SP_LH, color: guest ? semantic.text.primary : semantic.text.muted, whiteSpace: "nowrap" }}>{spName(s.sp)}</span>
          )}
        </div>
        <div style={{ display: "flex", gap: space.s4, alignItems: "flex-start", paddingInlineStart: TS_W + space.s4 }}>
        {/* dir="auto" 让浏览器按**这一行的首个强方向字符**自己定文字方向（2026-08-27）。
            阿语实测：没有它时整段按 LTR 排——正文靠左对齐，且句尾的 `.` 因为是中性字符
            被推到视觉最右端，每句话前面挂一个句号。以 `؟` 结尾的行反而正常（阿拉伯问号
            是强 RTL 字符），所以症状还时有时无，更难看出是排版问题。
            ⚠️ 用 auto 不用 rtl：详情页的语种是运行时才知道的，写死 rtl 要另维护一份 RTL
            语种清单，而 auto 对现有 26 门 LTR 语言判定结果不变、零影响。
            营销页的示例卡（marketing/ReviewMapCard）早就传了 dir，只是那边语种是写死的。 */}
        <div dir="auto" style={{ flex: 1, minWidth: 0, fontSize: 15, lineHeight: lh.read, color: isDel ? semantic.text.ghost : semantic.text.primary, textDecoration: isDel ? "line-through" : "none" }}>
          {isDel ? deleted[i] : renderMarked(s)}
          {isDel && (
            <span style={{ textDecoration: "none", display: "inline-flex", gap: space.s2, marginLeft: 10, alignItems: "center" }}>
              <span style={{ fontSize: 11, fontWeight: 500, padding: "1px 8px", borderRadius: radius.pill, background: semantic.surface.sunken, color: semantic.text.muted }}>{L("已删除", "Deleted")}</span>
              {link(L("恢复", "Restore"), () => restoreLine(i), { color: semantic.accent.text, u: true })}
            </span>
          )}
        </div>
        <span style={{ display: "inline-flex", gap: 6, alignItems: "flex-start", paddingTop: 2, opacity: showOps ? 1 : 0, transition: `opacity ${motion.fast}`, pointerEvents: showOps ? "auto" : "none" }}>
          {!audioGone && <button className="tx-focus" aria-label={L("听这一句", "Listen")} onClick={() => playSentence(s.t)} style={{ width: 26, height: 26, borderRadius: "50%", background: semantic.surface.raised, border: `1px solid ${semantic.border.strong}`, display: "grid", placeItems: "center", fontSize: 8, cursor: "pointer" }}>▶</button>}
          <button className="tx-focus" aria-label={L("编辑", "Edit")} onClick={() => startEdit(i)} style={{ width: 26, height: 26, borderRadius: "50%", background: semantic.surface.raised, border: `1px solid ${semantic.border.strong}`, display: "grid", placeItems: "center", fontSize: 11, color: semantic.text.secondary, cursor: "pointer" }}>✎</button>
        </span>
        </div>
      </div>
    );
  };

  // 「已定字」依据 chip。**兜底不能说「术语库」**：那会让没挂库的任务也显示成查过术语库
  // （2026-08-08 实测）。缺 basis 时一律退到中性的「引擎证据」，宁可少说也不误导。
  const basisChip = (a: ReviewItem) => {
    const basis = a.evidence?.basis ?? (a.type === "web" ? "web" : "engine");
    const pill = (text: string, color: string, bg?: string) => (
      <span style={{ fontSize: 11, fontWeight: 500, padding: "1px 7px", borderRadius: radius.pill, color,
        ...(bg ? { background: bg } : { border: `1px solid ${semantic.border.default}` }) }}>{text}</span>
    );
    if (basis === "web") return pill(L("联网 ✓", "web ✓"), semantic.success.text, semantic.success.bg);
    if (basis === "acoustic+glossary") return pill(L("声学+术语库", "acoustic+glossary"), semantic.warning.text);
    if (basis === "acoustic") return pill(L("声学", "acoustic"), semantic.warning.text);
    if (basis === "glossary") return pill(L("术语库", "glossary"), semantic.text.muted);
    return pill(L("引擎证据", "engine"), semantic.text.muted);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0, padding: "6px 0 20px", animation: "floatUp .5s ease both",
      // 复核栏定宽 360px + 稿件栏至少 420px：窗口更窄时整页横滑（AppShell 内容区兜底），
      // 而不是让复核栏被裁掉——那是本页最要紧的一栏。
      minWidth: 800 }}>
      {/* 返回 */}
      {onBack && (
        <button className="tx-focus" onClick={onBack} style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 6, border: "none", background: "transparent", fontSize: 13, color: semantic.text.muted, cursor: "pointer", fontFamily: fonts.sans, padding: 0 }}>
          <span style={{ fontSize: 14 }}>←</span> {L("我的转录", "My transcripts")}
        </button>
      )}

      {/* 头部 */}
      {/* ⚠️ 左栏必须 flex:1 且不换行：没有 flex 基准时它的宽度按 max-content 算，而 h1 是
          nowrap 的长文件名 —— 左栏「应得宽度」被撑到远超容器，flexWrap 就把导出按钮挤到下一行
          （2026-08-21 生产实见：文件名短时按钮在右上，文件名长时掉到标题左下）。
          h1 的 ellipsis 只在**父容器有确定宽度**时才起作用，所以 minWidth:0 要跟 flex:1 一起给。
          整页有 minWidth:800 兜底，按钮永不被压扁，故这里不需要 wrap。 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: space.s6, flexWrap: "nowrap", marginTop: space.s3 }}>
        <div style={{ flex: "1 1 auto", minWidth: 0 }}>
          <div style={{ ...type.label, display: "inline-flex", alignItems: "center", gap: space.s2, color: semantic.success.text }}>
            <span style={{ width: 15, height: 15, borderRadius: "50%", background: semantic.success.graphic, color: semantic.text.onAccent, display: "grid", placeItems: "center", flex: "0 0 auto", fontSize: 8 }}>✓</span>
            {L("转录完成", "Done")}{elapsed && <> · {L("用时", "took")} <span style={{ fontFamily: fonts.mono, letterSpacing: 0 }}>{elapsed}</span></>}
          </div>
          <h1 style={{ ...type.h1, margin: `${space.s2}px 0 0`, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%" }}>{job.file.name}</h1>
          <div style={{ ...type.caption, marginTop: 6 }}>
            {langName(lang, L)}{dur && <> · <span style={{ fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" }}>{dur}</span></>} · <span style={{ fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" }}>{chars.toLocaleString()}</span> {L("字", "chars")} · {speakers} {L("位说话人", "speakers")}
          </div>
        </div>
        <div style={{ display: "flex", gap: space.s3, flexShrink: 0, position: "relative", alignItems: "center" }}>
          {/* ⚠️ 主次由「还有没有人要拍板的地方」决定（2026-08-31 穿测）。
              在此之前，整页唯一的实心品牌色按钮永远是「下载 .docx」，而右上角那句
              「待你确认 0 / 11」只是小字加一排细灰杠——**商业主张是「确认完才放心导出」，
              界面主张却在说「现在就下载」**。两者打架时用户信界面。
              还有待办 → 待办键实心、下载键退成描边；清零 → 互换回来。
              ⚠️ 下载**始终点得动**：这是一道建议的次序，不是一堵墙。用户想现在就拿走稿子，
              那是他的权利；我们只负责让他知道还有没看过的地方。 */}
          {pendingCount > 0 && firstPending && (
            <button className="tx-focus"
              onClick={() => { setAutoOpen(false); openItem(firstPending, 0, firstPending.occurrences.length > 1 ? "overview" : "detail"); }}
              style={{ border: "none", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: space.s2, height: 40, padding: "0 18px", borderRadius: radius.sm, background: semantic.accent.fill, color: semantic.text.onAccent, fontFamily: fonts.sans, fontSize: 14, fontWeight: 500, boxShadow: "0 10px 24px -12px rgba(168,68,47,.65)", transition: `background ${motion.fast}` }}>
              {L.t("还差 {0} 处确认", "{0} left to confirm", pendingCount)} →
            </button>
          )}
          {/* 拆分按钮：主键直接下载推荐格式（真实任务=.docx，演示数据=.txt），▾ 开格式菜单 */}
          <span style={{ display: "inline-flex", alignItems: "stretch", borderRadius: radius.sm, overflow: "hidden", border: pendingCount > 0 ? `1px solid ${semantic.border.strong}` : "none", boxShadow: pendingCount > 0 ? "none" : "0 10px 24px -12px rgba(168,68,47,.65)" }}>
            <button className="tx-focus" onClick={primaryExport.run}
              style={{ border: "none", cursor: "pointer", display: "inline-flex", alignItems: "center", height: pendingCount > 0 ? 38 : 40, padding: "0 18px", background: pendingCount > 0 ? "transparent" : semantic.accent.fill, color: pendingCount > 0 ? semantic.text.secondary : semantic.text.onAccent, fontFamily: fonts.sans, fontSize: 14, fontWeight: 500, transition: `background ${motion.fast}` }}>
              {primaryExport.label}
            </button>
            <button className="tx-focus" aria-label={L("选择格式", "Choose format")} aria-expanded={exportOpen} onClick={() => setExportOpen((o) => !o)}
              style={{ border: "none", borderLeft: `1px solid ${pendingCount > 0 ? semantic.border.strong : "rgba(251,247,238,.28)"}`, cursor: "pointer", display: "inline-flex", alignItems: "center", height: pendingCount > 0 ? 38 : 40, padding: "0 11px", background: pendingCount > 0 ? (exportOpen ? semantic.surface.sunken : "transparent") : (exportOpen ? semantic.accent.fillHover : semantic.accent.fill), color: pendingCount > 0 ? semantic.text.secondary : semantic.text.onAccent, fontSize: 15, lineHeight: 1, transition: `background ${motion.fast}` }}>
              ▾
            </button>
          </span>
          {exportOpen && (
            <>
              <div style={{ position: "fixed", inset: 0, zIndex: 60 }} onClick={() => setExportOpen(false)} />
              <div role="menu" style={{ position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 70, width: 264, background: semantic.surface.float, border: `1px solid ${semantic.border.strong}`, borderRadius: radius.md, boxShadow: shadow.lg, overflow: "hidden", padding: "6px 0" }}>
                {EXPORT_FORMATS.map((f) => (
                  <button
                    key={f.ext}
                    className="tx-focus"
                    role="menuitem"
                    disabled={f.disabled}
                    onClick={() => { setExportOpen(false); f.run(); }}
                    onMouseEnter={() => setExportHover(f.ext)}
                    onMouseLeave={() => setExportHover(null)}
                    style={{ display: "flex", alignItems: "center", gap: space.s3, width: "100%", padding: "10px 16px", border: "none", background: !f.disabled && exportHover === f.ext ? semantic.surface.sunken : "transparent", cursor: f.disabled ? "not-allowed" : "pointer", opacity: f.disabled ? 0.55 : 1, textAlign: "left", fontFamily: fonts.sans, transition: `background ${motion.fast}` }}
                  >
                    <span style={{ fontFamily: fonts.mono, fontSize: 12, color: semantic.accent.text, width: 44, flex: "0 0 auto" }}>.{f.ext}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: "block", fontSize: 13, fontWeight: 500, color: semantic.text.primary }}>{f.name}</span>
                      <span style={{ display: "block", fontSize: 12, color: semantic.text.muted, marginTop: 2 }}>{f.desc}</span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* 播放器（带复核标记点）+ 标记点图例；录音上传 7 天后自动删除 → 过期换优雅只读态 */}
      <div style={{ marginTop: space.s4 }}>
        {audioGone ? (
          <div style={{ display: "flex", alignItems: "center", gap: space.s3, padding: "13px 16px", borderRadius: radius.md, background: semantic.surface.sunken, border: `1px solid ${semantic.border.subtle}`, color: semantic.text.muted, fontSize: 13, fontFamily: fonts.sans }}>
            <span aria-hidden style={{ fontSize: 15, flex: "0 0 auto" }}>🔇</span>
            <span>{L("录音已删除 · 为保护隐私，录音在上传 7 天后自动清除。文字稿仍可查看与导出。", "Audio deleted · for privacy, recordings are removed 7 days after upload. Your transcript stays available to view and export.")}</span>
          </div>
        ) : (
          <>
            <AudioPlayer cur={cur} total={total} playing={playing} onToggle={toggle} onSeek={seek} marks={marks} />
            {/* 加载状态：等数据（临时，盖过下面那行）/ 缓存四态（常驻，规则 4）。都没有时这一行不渲染，不占位 */}
            {(audioWait || cache) && (
              <div aria-live="polite" style={{ marginTop: 6, fontSize: 11, fontFamily: fonts.sans, color: audioWait ? semantic.accent.text : cache?.kind === "failed" ? semantic.warning.text : semantic.text.muted, display: "flex", alignItems: "center", gap: 6 }}>
                {audioWait
                  ? <><span aria-hidden>⏳</span>{L("正在加载这一段的声音…（网络慢时要等一会儿）", "Loading audio for this line… (may take a moment on a slow connection)")}</>
                  : cache?.kind === "caching"
                  ? <>{cacheBar(cache.pct)}{L.t("正在把整段录音缓存到本地 · {0}%——缓存好后点任意一处都不用再等", "Caching the whole recording locally · {0}% — once done, every jump plays instantly", cache.pct)}</>
                  : cache?.kind === "paused"
                  ? <>{cacheBar(cache.pct)}{L.t("缓存已暂停 · {0}%——先把网络让给播放，暂停后自动继续", "Caching paused at {0}% — giving the connection to playback, resumes when you pause", cache.pct)}</>
                  : cache?.kind === "failed"
                  ? <><span aria-hidden>⚠</span>{L("整段录音缓存失败（已自动重试 3 次）· 定位仍可用，只是每次要等", "Couldn't cache the recording (retried 3 times) · jumps still work, just slower")}{link(L("重试", "Retry"), () => cacheCtl.current?.retry(), { color: semantic.accent.text, u: true })}</>
                  : <><span aria-hidden>✓</span>{L("整段录音已缓存到本地，定位不用再等", "Recording cached locally — jumps now play instantly")}</>}
              </div>
            )}
            {/* 同一行：左=7 天删除预告，右=标记点图例；同字号(11)同基线对齐 */}
            <div style={{ display: "flex", gap: space.s4, justifyContent: "space-between", alignItems: "baseline", marginTop: 6, fontSize: 11, flexWrap: "wrap" }}>
              {/* 7 天删除预告是必读信息，不能用 ghost（2.01:1）——红线 11 */}
              <span style={{ color: semantic.text.muted, fontFamily: fonts.sans }}>
                {L("录音将在上传 7 天后自动删除（隐私保护）· 请及时复核与导出", "Audio is auto-deleted 7 days after upload (privacy) · review & export in time")}
              </span>
              {marks.length > 0 && (
                <span style={{ display: "inline-flex", gap: space.s4, color: semantic.text.muted, flex: "0 0 auto" }}>
                  {([
                    [semantic.accent.brand, L("待确认实体", "Entities to confirm")],
                    [semantic.warning.icon, L("存疑", "Uncertain")],
                    [semantic.success.graphic, L("已定字", "Settled")],
                  ] as const).map(([c, label]) => (
                    <span key={label} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: c, flex: "0 0 auto" }} />
                      {label}
                    </span>
                  ))}
                </span>
              )}
            </div>
          </>
        )}
      </div>
      {/* 规则 1：缓存好之前播放器不预取（时长从 metrics 拿），别和后台下载抢；没有 metrics 的旧单才让它读一次时长 */}
      {/* 录音已删时连 src 一起拿掉：浏览器自己的缓存里可能还留着一份（我们允许它留 24 小时），留着 src 它就能放 */}
      <audio ref={audioRef} src={audioGone ? undefined : localSrc ?? (jobId ? audioUrl(jobId) : undefined)} style={{ display: "none" }} preload={localSrc ? "auto" : metrics?.durationSec != null ? "none" : "metadata"} />

      {/* 双栏 */}
      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: space.s7, marginTop: space.s5 }}>
        {/* 正文：flex 撑满（与上方播放器通栏对齐，侧边栏收起/展开都自适应） */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", position: "relative" }}>
          <div style={{ ...type.label, paddingBottom: space.s3, borderBottom: `1px solid ${semantic.border.subtle}`, display: "flex", justifyContent: "space-between" }}>
            <span>{L("全文", "Transcript")}</span>
            <span style={{ letterSpacing: 0, textTransform: "none", fontWeight: 400, color: semantic.text.muted }}>{L("点时间戳 ▶ 听这一句 · 点标记词跳到复核卡", "Timestamp plays the line · marked words open review")}</span>
          </div>
          {/* 左右 padding 与高亮行的负 margin 出血等量抵消——行宽不超容器，不出横向滚动条 */}
          <div ref={scrollRef} className="tx-scroll" onWheel={() => { if (playing) setDetached(true); }} onTouchMove={() => { if (playing) setDetached(true); }}
            style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden", padding: `${space.s3}px ${space.s3}px ${space.s6}px`, margin: `0 -${space.s3}px` }}>
            {segs.map((s, i) => row(i, s))}
          </div>
          {/* 脱离跟随时的回归 pill */}
          {playing && detached && activeIdx >= 0 && (
            <button className="tx-focus" onClick={reattach}
              style={{ position: "absolute", right: space.s4, bottom: space.s4, zIndex: 5, display: "inline-flex", alignItems: "center", gap: 7, padding: "6px 14px", borderRadius: radius.pill, border: "none", cursor: "pointer", background: semantic.text.primary, color: semantic.text.onAccent, fontFamily: fonts.sans, fontSize: 12, fontWeight: 500, boxShadow: shadow.md }}>
              {/* 时间码原为野色 #E8A28F（不在调色板里）；bgSoft 在墨底上 12.85:1，更清楚 */}
              ▶ {L("回到当前句", "Back to playing")} <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.accent.bgSoft }}>{mmss(segs[activeIdx].t)}</span>
            </button>
          )}
        </div>

        {/* 复核侧栏：对称手风琴——「待你确认」与「已按证据定字」互为开关，同时只展开一段 */}
        <div style={{ width: 360, flex: "0 0 360px", display: "flex", flexDirection: "column", minHeight: 0 }}>
          {autoOpen && queue.length > 0 ? (
            // 审计区展开时：队列折叠成一行头（保留进度），点击切回
            <button className="tx-focus" onClick={() => setAutoOpen(false)}
              style={{ display: "flex", alignItems: "center", gap: space.s2, width: "100%", border: "none", borderBottom: `1px solid ${semantic.border.subtle}`, background: "transparent", cursor: "pointer", fontFamily: fonts.sans, fontSize: 12, color: semantic.text.muted, padding: `2px 4px ${space.s3 - 2}px`, flex: "0 0 auto" }}>
              {caret(false)}
              <b style={{ color: semantic.text.primary, fontWeight: 600 }}>{L("待你确认", "Needs your eyes")}</b>
              <span style={{ fontFamily: fonts.mono, fontSize: 11, fontVariantNumeric: "tabular-nums" }}>{doneCount} / {queue.length}</span>
              <span style={{ flex: 1 }} />
            </button>
          ) : (
          <div className="tx-scroll" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: space.s3, overflowY: "auto", paddingRight: 2, paddingBottom: space.s3 }}>
          {/* 无复核项：静默卡，不渲染空队列 */}
          {queue.length === 0 && (
            <div style={{ border: `1px dashed ${semantic.border.strong}`, borderRadius: radius.md, padding: `${space.s5}px ${space.s4}px`, textAlign: "center" }}>
              <div style={{ display: "inline-flex", alignItems: "center", gap: space.s2 }}>
                <span style={{ width: 18, height: 18, borderRadius: "50%", background: semantic.success.graphic, color: semantic.text.onAccent, display: "grid", placeItems: "center", fontSize: 9 }}>✓</span>
                <span style={{ ...type.h3 }}>{L("没有要确认的", "Nothing to review")}</span>
              </div>
              <div style={{ fontSize: 12, color: semantic.text.secondary, lineHeight: 1.65, marginTop: 6 }}>{L("全部按证据定字，没有拿不准的地方——直接导出即可。", "Everything checked out — export away.")}</div>
            </div>
          )}
          {queue.length > 0 && (allDone ? (
            // 里程碑 · 绿条幅（设计稿 04 成功三层 + 05 复核清零）：圆形 ✓ + 收束绿波形 + 放心导出
            <div style={{ background: semantic.success.bg, border: `1px solid ${semantic.success.graphic}`, borderRadius: radius.md, padding: `${space.s4 - 2}px ${space.s4}px`, boxShadow: shadow.sm, flex: "0 0 auto" }}>
              <div style={{ display: "flex", alignItems: "center", gap: space.s2 }}>
                <span style={{ width: 22, height: 22, borderRadius: "50%", background: semantic.success.graphic, color: semantic.text.onAccent, display: "grid", placeItems: "center", fontSize: 11, flex: "0 0 auto" }}>✓</span>
                <span style={{ ...type.h3, color: semantic.success.text }}>{L("存疑已全部确认", "All cleared")}</span>
                <span style={{ flex: 1 }} />
                <span style={{ fontFamily: fonts.mono, fontSize: 12, color: semantic.success.text, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{queue.length} / {queue.length} ✓</span>
              </div>
              {/* 收束绿波形：复核全程在跳，清零此刻收成一条线 —— 尘埃落定 */}
              <div style={{ marginTop: space.s3 }}><ConvergeWave /></div>
              <div style={{ fontSize: 12, color: semantic.success.text, marginTop: space.s3 }}>{L("尘埃落定 —— 这份稿子可以放心用了。", "Settled — this transcript is good to go.")}</div>
              {/* 里程碑主按钮走绿（绿=已定稿状态色，设计稿 04/05）；文案「放心导出」= 复核优先产品话术 */}
              <button className="tx-focus" onClick={primaryExport.run}
                style={{ width: "100%", marginTop: space.s3, padding: "9px 14px", border: "none", borderRadius: radius.sm, background: semantic.success.text, color: semantic.text.onAccent, fontFamily: fonts.sans, fontWeight: 500, fontSize: 13, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 8, boxShadow: "0 10px 24px -12px rgba(84,113,63,.7)" }}>
                {L("放心导出", "Export with confidence")} {jobId ? ".docx" : ".txt"} ↓
              </button>
            </div>
          ) : (
            <div style={{ background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, padding: `${space.s4 - 2}px ${space.s4}px`, boxShadow: shadow.sm, flex: "0 0 auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ ...type.h3 }}>{L("待你确认", "Needs your eyes")}</span>
                <span style={{ fontFamily: fonts.mono, fontSize: 12, color: semantic.text.muted, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{doneCount} / {queue.length}</span>
              </div>
              <div style={{ display: "flex", gap: 3, marginTop: space.s3 }}>
                {queue.map((q) => {
                  const st = itemState(q);
                  return <span key={keyOf(q)} title={q.term} style={{ flex: 1, height: 4, borderRadius: 2, background: st === "confirmed" ? semantic.success.graphic : st === "skipped" ? semantic.border.strong : semantic.border.subtle, transition: `background ${motion.fast}` }} />;
                })}
              </div>
              <div style={{ fontSize: 12, color: semantic.text.muted, marginTop: space.s2 }}>{L("确认完即可导出——其余已按证据定字，抽查即可。", "Clear these and the export is solid.")}</div>
            </div>
          ))}

          {/* 全部处理完之后，17 张决策卡不该继续摊在那儿：那时它们已经不是「待办」，
              而是**你做过的决策**——留着只让人以为还有事要做（2026-08-21 Duner 指出）。
              折叠成一行，随时展开回看每一处的归宿（卡内本就带「重新决定 / 改回原词」）。
              措辞用「你的决策」而不是「已确认」：跳过的项也在里面，且要与下方
              「已按证据定字」（系统定的）区分开——谁做的，一眼看得出。 */}
          {allDone ? (
            <div style={{ flex: "0 0 auto" }}>
              <button className="tx-focus" onClick={() => setDecisionsOpen((o) => !o)} aria-expanded={decisionsOpen}
                style={{ display: "flex", alignItems: "center", gap: space.s2, width: "100%", border: "none", background: "transparent", cursor: "pointer", fontFamily: fonts.sans, fontSize: 12, color: semantic.text.muted, padding: "2px 4px" }}>
                {caret(decisionsOpen)}
                <b style={{ color: semantic.text.primary, fontWeight: 600, whiteSpace: "nowrap", flex: "0 0 auto" }}>{L.t("你的决策 {0} 项", "{0} decisions by you", queue.length)}</b>
                <span style={{ minWidth: 0, flex: "1 1 auto" }}>{L("展开可逐处回看与改口", "Open to review or change any of them")}</span>
              </button>
              {decisionsOpen && (
                <div style={{ display: "flex", flexDirection: "column", gap: space.s3, marginTop: space.s3 }}>
                  {queue.map((q) => queueCard(q))}
                </div>
              )}
            </div>
          ) : queue.map((q) => queueCard(q))}

          {/* 后处理接力卡：**复核的全部内容之后**（2026-08-20 移下来）。
              它是下一道工序，不是复核的一部分——解锁条件正是「复核清零」，
              放在汇总与逐项明细中间等于把一句话说到一半插进另一个话题。
              上方那条分隔线是刻意的：告诉人这里换话题了。 */}
          <div style={{ flex: "0 0 auto", marginTop: space.s2, paddingTop: space.s4, borderTop: `1px solid ${semantic.border.default}` }}>
            <PostprocessCard
              jobId={jobId}
              initialStatus={ppInitial}
              locked={queue.length > 0 && !allDone}
              remaining={queue.length - doneCount}
              onFinished={onPostprocessDone}
              durationSec={metrics?.durationSec ?? null}
              // 脱敏清单「点一处回到正文」：段号即正文行号（两者同一份 segments），
              // 复用复核卡那套定位，用户学一次手势处处通用
              onLocate={(seg) => scrollToRow(seg)}
              stem={stem}
            />
          </div>
          </div>
          )}

          {/* 「已按证据定字」审计区：术语库 / 声学多数 / 联网核实的实体，展开看证据、可修订（不计进度）。
              收起 = 底部一行页脚；展开 = 占满剩余高度（队列同时折叠成一行头）。
              ⚠️ 措辞刻意不叫「已确认」：那是**人**拍板的动作（队列里那些），
              这里是系统按证据自己定的，两者同屏，用同一个词会分不清谁做的（2026-08-20 Duner 指出） */}
          {auto.length > 0 && (
            <div style={{ flex: autoOpen ? "1 1 auto" : "0 0 auto", minHeight: 0, display: "flex", flexDirection: "column", borderTop: autoOpen ? "none" : `1px solid ${semantic.border.subtle}`, paddingTop: space.s3 - 2, paddingRight: 2, paddingBottom: space.s2 }}>
              <button className="tx-focus" onClick={() => setAutoOpen((o) => !o)} aria-expanded={autoOpen}
                style={{ display: "flex", alignItems: "center", gap: space.s2, width: "100%", border: "none", background: "transparent", cursor: "pointer", fontFamily: fonts.sans, fontSize: 12, color: semantic.text.muted, padding: "2px 4px" }}>
                <span style={{ color: semantic.success.text }}>✓</span>
                {/* 标题 nowrap：CJK 可在任意字之间断行，360px 侧栏里「已按证据定字 17」会被切成
                    「已按证据定 / 字 17」（2026-08-20 走查实见）。说明句则可换行、可被挤窄。 */}
                <b style={{ color: semantic.text.primary, fontWeight: 600, whiteSpace: "nowrap", flex: "0 0 auto" }}>{L.t("已按证据定字 {0}", "{0} settled by evidence", auto.length)}</b>
                <span style={{ minWidth: 0, flex: "1 1 auto" }}>{L("术语库 / 声学 / 联网 · 抽查即可", "Glossary / acoustics / web · spot-check")}</span>
                {caret(autoOpen)}
              </button>
              {/* 展开列表：占满剩余高度、内部滚动 */}
              {autoOpen && (
              <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", marginTop: 2 }}>
              {auto.map((a) => {
                const k = keyOf(a);
                const ex = autoExpanded === k;
                return (
                  <div key={k} data-review-card={k} data-flash={flashCard === k ? "1" : undefined} style={{ borderBottom: `1px solid ${semantic.border.subtle}`, background: ex ? semantic.surface.float : "transparent", borderRadius: ex ? radius.sm : 0 }}>
                    {/* 点标题：展开的同时定位到第 1 处（与存疑卡「打开就定位」一致）；再点收起 */}
                    <button className="tx-focus" onClick={() => { if (ex) setAutoExpanded(null); else { setAutoExpanded(k); goToAuditOcc(a, 0); } }} aria-expanded={ex}
                      style={{ display: "flex", alignItems: "center", gap: space.s2, width: "100%", border: "none", background: "transparent", cursor: "pointer", fontFamily: fonts.sans, padding: `${space.s2}px ${space.s2}px` }}>
                      <span style={{ fontSize: 13, fontWeight: 500, color: semantic.text.primary }}>{a.term}</span>
                      {basisChip(a)}
                      {itemState(a) === "confirmed" && <span style={{ fontSize: 11, color: semantic.success.text, whiteSpace: "nowrap" }}>✓ {L("已修订", "Revised")}</span>}
                      <span style={{ flex: 1 }} />
                      <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted }}>{a.occurrences.length} {L("处", "×")}</span>
                    </button>
                    {ex && (
                      <div style={{ padding: `0 ${space.s2}px ${space.s3}px` }}>
                        {a.web?.searches.map((s, i) => (
                          <div key={i} style={{ background: semantic.surface.page, borderRadius: radius.sm, padding: `${space.s3}px ${space.s3}px`, marginBottom: 6 }}>
                            <div style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.secondary }}>🔍 {L("搜索", "Search")}「{s.query}」</div>
                            <div style={{ fontSize: 12, lineHeight: 1.6, color: semantic.text.primary, marginTop: 5 }}>{s.summary}</div>
                            <div style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, marginTop: 5 }}>{L("来源", "Source")}：{L("网页检索", "Web search")}</div>
                          </div>
                        ))}
                        {!a.web && <div style={{ fontSize: 12, lineHeight: 1.6, color: semantic.text.secondary, marginBottom: 6 }}>{reasonOf(a, 0)}</div>}
                        {a.evidence?.candidates && (
                          <div style={{ fontSize: 12, color: semantic.text.secondary, marginBottom: 6 }}>{L("各轨候选", "Track candidates")}：{a.evidence.candidates.join(" / ")}</div>
                        )}
                        {/* 每一处一行：时间码 + 那一句，整行可点 → 正文跳到那一处。超过 12 处列表自己滚，不折叠 */}
                        <ul className="tx-scroll" aria-label={L("每一处", "Occurrences")}
                          style={{ listStyle: "none", margin: `${space.s2}px 0 ${space.s2}px`, padding: 0, borderTop: `1px dashed ${semantic.border.subtle}`, maxHeight: 12 * 30, overflowY: "auto" }}>
                          {a.occurrences.map((oo, i2) => {
                            const cur = currentKey === k && (focusIdx[k] ?? -1) === i2;
                            return (
                              <li key={i2}>
                                <button className="tx-focus" onClick={() => goToAuditOcc(a, i2)} title={L.t("跳到第 {0} 处", "Jump to #{0}", i2 + 1)} aria-current={cur ? "true" : undefined}
                                  style={{ display: "grid", gridTemplateColumns: "44px 1fr", gap: space.s2, width: "100%", textAlign: "left", border: "none", borderRadius: radius.sm, padding: "5px 6px", cursor: "pointer", fontFamily: fonts.sans, color: semantic.text.primary,
                                    background: cur ? semantic.accent.bgTint : "transparent", boxShadow: cur ? `inset 3px 0 0 ${semantic.accent.brand}` : "none" }}>
                                  <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, paddingTop: 2, fontVariantNumeric: "tabular-nums" }}>{mmss(oo.t)}</span>
                                  <span style={{ fontSize: 12, lineHeight: 1.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{oo.lineText}</span>
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                        <div style={{ display: "flex", gap: space.s3, fontSize: 12 }}>
                          {link(
                            `↗ ${L("跳到原文", "Go to text")}${a.occurrences.length > 1 ? ` ${((jumpIdx[k] ?? 0) % a.occurrences.length) + 1}/${a.occurrences.length}` : ""}`,
                            () => jumpToOriginal(a),
                            { color: semantic.text.secondary }
                          )}
                          {link(revisingKey === k ? L("收起修订", "Close revision") : `✎ ${L("修订", "Revise")}`, () => toggleRevise(a), { color: semantic.accent.text })}
                        </div>
                        {revisingKey === k && (
                          <div style={{ background: semantic.surface.raised, border: `1px solid ${semantic.accent.brand}`, borderRadius: radius.md, padding: space.s3, marginTop: space.s3, boxShadow: shadow.md }}>
                            {a.occurrences.length > 1 && (itemView[k] ?? "overview") === "overview" ? overviewBody(a) : detailBody(a, a.occurrences.length > 1)}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              </div>
              )}
            </div>
          )}
          {auto.length === 0 && (
            // 审计区常驻：无已定字实体时显示静态空态「· 无」，不可展开（与收起态页脚同视觉）
            <div style={{ flex: "0 0 auto", borderTop: `1px solid ${semantic.border.subtle}`, paddingTop: space.s3 - 2, paddingRight: 2, paddingBottom: space.s2 }}>
              <div style={{ display: "flex", alignItems: "center", gap: space.s2, padding: "2px 4px", fontFamily: fonts.sans, fontSize: 12, color: semantic.text.muted }}>
                <span style={{ color: semantic.success.text }}>✓</span>
                <b style={{ color: semantic.text.primary, fontWeight: 600 }}>{L("已按证据定字 · 无", "Nothing settled by evidence")}</b>
                <span>{L("这段没有需要定字的实体", "Nothing needed disambiguating here")}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 页脚基线：两栏共用的「底」。没有它右下角是悬空的——左侧有用户区收口，右侧没有。
          · 左右不出血，两端正好落在内容宽度上：左端与「全文」那条分隔线同起点，
            右端与复核侧栏同终点——线是给这两栏收底的，横贯到屏幕边缘反而不像它们的底
          · 下负 margin 把线压进内容区的 32px 底部内边距里，落到与侧栏用户区邮箱行同一水平线上
            （实测对齐：两侧都是从页底起算的固定内边距，故视口高度变化时仍保持齐平）
          · 比行分隔线重一档（2px + border.default）：它是结构性的底，不是行与行之间的分隔
          · 上 margin 只决定审计区离线多远，不影响线的位置——右栏是 flex:1，会自己吸收 */}
      <div style={{ flex: "0 0 auto", borderTop: `2px solid ${semantic.border.default}`, margin: `${space.s2}px 0 -28px` }} />

      {/* 墨底 Toast（删除撤销等） */}
      {toast && (
        <div style={{ position: "fixed", bottom: 28, left: "50%", transform: "translateX(-50%)", zIndex: 100, display: "inline-flex", alignItems: "center", gap: space.s3, background: semantic.text.primary, color: semantic.text.onAccent, borderRadius: radius.md, padding: "12px 18px", fontSize: 13, boxShadow: shadow.lg, animation: "toastUp .18s ease both" }}>
          <span>{toast.msg}</span>
          {toast.action && (
            // 成功语境的撤销走绿系（设计稿 04 成功三层：toast 撤销不混赤陶）
            <button className="tx-focus" onClick={toast.onAction} style={{ border: "none", background: "transparent", color: semantic.success.onDark, fontSize: 13, fontWeight: 500, cursor: "pointer", padding: 0, fontFamily: fonts.sans }}>{toast.action}</button>
          )}
        </div>
      )}
    </div>
  );
}

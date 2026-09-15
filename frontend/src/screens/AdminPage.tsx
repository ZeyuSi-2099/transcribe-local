// AdminPage — 运营驾驶舱（仅管理员）：进行中 + 最近完成 + 最近失败 + 当日汇总。
// 本机版（与线上不同）：「资源」不看云供应商容量、证书到期与 Claude 订阅健康度，改看这台电脑（模型、磁盘、内存、
// 模型后端与数据去向）；「工作流」按本机流水线画；各引擎一栏认得本机引擎代号。
// 内部运营台，非客户页：原则「密、清、稳」，复用设计系统（数字 mono 等宽、语义色、衬线仅标题）。
// 任务行可展开下钻：各引擎 {状态+耗时}，主引擎（新任务 ELV/历史任务 G25F）再到每片（一次过/重试几次/耗时，仅历史任务有）+ 聚合 + 成本。
// 自轮询每 5s。访问鉴权在后端（非管理员 404）；侧栏入口由 me.isAdmin 控制。
import { useEffect, useState, useCallback } from "react";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow, layout } from "../styles/tokens";
import { fmtClock } from "../lib/format";
import { langName } from "../lib/langs";
import {
  getAdminOverview, getAdminBalances, setAdminBalance, refreshAllBalances,
  getAdminHealth, getAdminAlerts, markAlertHandled, getAdminLocalResources, probeBackend,
  type AdminOverview, type AdminJob, type AdminFailure, type AdminRecent,
  type AdminMetrics, type EngineState, type AdminBalance, type AdminPostprocess,
  type AdminHealth, type AdminAlert, type AdminLocalResources, type DataFlowItem,
} from "../lib/api";
import { mono, ellipsis, caret, SectionShell, empty, btnStyle, cardInput, linkBtn } from "./admin/ui";
import { WorkflowTab } from "./admin/WorkflowTab";

const ENGINE_ORDER = ["G25F", "ELV", "GEM", "DB", "FA", "XF", "AAI", "SPM", "SNX"];   // G25F=历史任务主轨；ELV=新任务主轨；GEM/AAI/SPM/SNX=新阵容备用/参考轨
// 本机版：本机引擎代号（FRED2 / ZIPC / PARA / QWEN3）不在上面的线上清单里——只按清单过滤的话，
// 「各引擎」那一栏在本机永远是空的。清单里有的照线上排序，其余按 metrics 里的顺序接在后面。
const engineKeys = <T,>(m: Record<string, T>) =>
  [...ENGINE_ORDER.filter((k) => k in m), ...Object.keys(m).filter((k) => !ENGINE_ORDER.includes(k))];
const engineColor = (s: string) =>
  s === "done" ? semantic.success.text : s === "failed" ? semantic.danger.text : s === "running" ? semantic.accent.text : semantic.text.muted;
const fmtSec = (s: number | null | undefined) => (s == null ? "—" : `${s}s`);

// 录音类型：2026-08-18 场景分流已取消，新单一律 "all"。存量的 meeting/phonecall 照实显示，
// 但**不再标路数**——跑几路现在只看语种 profile（中文 4 路、其余 3 路），与场景无关，
// 旧标注（现场 4 路 / 电话 3 路）对新旧单都已经不准。
function useRtype(t?: string | null) {
  const L = useL();
  if (t === "all") return L("全轨（不分场景）", "All tracks");
  if (t === "phonecall") return L("电话访谈（存量）", "Phone (legacy)");
  if (t === "meeting") return L("现场面访（存量）", "On-site (legacy)");
  return "—";
}

// 引擎状态药丸（收起态行内）：done ✓ 绿 / running 赤陶脉冲点 / failed ✗ 赤陶
function EngineChip({ code, status, parts }: { code: string; status: string; parts?: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 8px", borderRadius: radius.pill, background: semantic.surface.sunken, ...mono(11), color: engineColor(status) }}>
      {status === "running"
        ? <span style={{ width: 6, height: 6, borderRadius: "50%", background: semantic.accent.brand, animation: "pulseDot 1.6s infinite" }} />
        : <span aria-hidden>{status === "done" ? "✓" : status === "failed" ? "✗" : "·"}</span>}
      {code}{parts ? ` ${parts}` : ""}
    </span>
  );
}

// P3 档位的展示名。后端存的是产物名里的引擎缀（小写），这里只做「缀 → 人看的名字」的映射：
// opus=订阅制 Claude（无按量成本，金额恒为 0）、flash/pro=DeepSeek 两档、ds=旧的单次版。
// 认不出的缀原样显示、连缀都没有显示 `?`——**不猜**，成本要能核准就不能有编出来的归属
// （与 orchestrator 里「没有引擎缀就记 unknown」同一条原则）。
const P3_ENGINE_LABEL: Record<string, string> = {
  opus: "Opus",
  flash: "DS-v4-Flash",
  pro: "DS-v4-Pro",
  ds: "DS-v4",
  unknown: "?",
};

// 展开下钻：各引擎耗时 + 主引擎明细（分片进度 + 历史任务 token 遥测：聚合 + 每片）
function EngineDetail({ engines, prim, g25f, cost }: {
  engines?: Record<string, EngineState>; prim?: AdminMetrics["primary"] | null; g25f?: AdminMetrics["g25f"]; cost?: AdminMetrics["cost"];
}) {
  const L = useL();
  return (
    <div style={{ marginTop: space.s3, paddingTop: space.s3, borderTop: `1px dashed ${semantic.border.subtle}`, display: "flex", flexDirection: "column", gap: space.s3 }}>
      {engines && (
        <div>
          <div style={{ ...type.label, marginBottom: 5 }}>{L("各引擎 · 状态/耗时", "Engines · status/time")}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
            {engineKeys(engines).map((k) => (
              <span key={k} style={{ ...mono(11), color: engineColor(engines[k].status) }}>
                {k} {engines[k].status === "done" ? "✓" : engines[k].status === "failed" ? "✗" : "⋯"} {fmtSec(engines[k].sec)}
                {k === prim?.tag && prim
                  ? g25f && g25f.onePass != null
                    ? `（${g25f.total}${L("片", "p")} · ${L("一次过", "1-pass")} ${g25f.onePass}/${g25f.total} · ${L("最多重试", "max")} ${g25f.maxAttempts ?? 1}${L("次", "×")}）`
                    : `（${prim.done ?? 0}/${prim.total ?? "?"}${L("片", "p")}）`
                  : ""}
              </span>
            ))}
          </div>
          {g25f?.parts && g25f.parts.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 6 }}>
              {g25f.parts.map((p) => {
                const retried = p.attempts > 1;
                return (
                  <span key={p.index} title={`tokens ${p.inputTokens}/${p.thinkingTokens}/${p.outputTokens}`}
                    style={{ ...mono(11), padding: "2px 7px", borderRadius: radius.sm, background: semantic.surface.sunken, color: !p.success ? semantic.danger.text : retried ? semantic.warning.text : semantic.text.muted }}>
                    #{p.index + 1} {!p.success ? L("失败", "fail") : retried ? L(`重试${p.attempts}次`, `${p.attempts}×`) : L("一次过", "1-pass")} · {p.elapsedSec}s
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}
      {cost && (
        <div>
          <div style={{ ...type.label, marginBottom: 5 }}>{L("成本（¥）", "Cost (¥)")}</div>
          <div style={{ ...mono(11), color: semantic.text.secondary }}>
            {/* 本机识别不花钱，P1 没有分项——那半截不显示，免得出现一个空的「P1:」 */}
            {Object.keys(cost.p1).length > 0 && <>P1: {engineKeys(cost.p1).map((k) => `${k} ¥${cost.p1[k]}`).join(" · ")}{"  |  "}</>}
            P3 {P3_ENGINE_LABEL[cost.p3_engine ?? ""] ?? cost.p3_engine ?? "?"} ¥{cost.p3}
            {"  |  "}{L("总", "total")} <span style={{ color: semantic.text.primary, fontWeight: 600 }}>¥{cost.total}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div style={{ flex: 1, minWidth: 0, padding: `${space.s4}px ${space.s5}px`, background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, boxShadow: shadow.sm }}>
      <div style={{ ...type.label }}>{label}</div>
      <div style={{ ...mono(26), fontWeight: 500, color: tone ?? semantic.text.primary, marginTop: 6 }}>{value}</div>
    </div>
  );
}

function JobCard({ job, last, maxQueuedHours }: { job: AdminJob; last: boolean; maxQueuedHours: number }) {
  const L = useL();
  const [open, setOpen] = useState(false);
  const m = job.metrics ?? {};
  const engines = m.engines ?? null;
  // 归一：新任务用 metrics.primary；历史任务把 metrics.g25f 视作 tag=G25F 的 primary
  const prim = m.primary ?? (m.g25f ? { tag: "G25F", ...m.g25f } : null);
  const queued = job.status === "queued";
  const rtype = useRtype(job.recordingType);
  // 排队时长：满 maxQueuedHours 会被判失败并返还预扣，而在那之前**界面上原本一个字都没有**。
  // 那段窗口是唯一能干预的时间（派单通道坏了、机器起不来），错过就只剩一条失败记录。
  const queueWarn = queued && job.elapsedSec > maxQueuedHours * 3600 * QUEUE_WARN_RATIO;
  return (
    <div
      onClick={() => setOpen((o) => !o)}
      style={{ padding: `${space.s4}px ${space.s5}px`, borderBottom: last ? "none" : `1px solid ${semantic.border.subtle}`, cursor: "pointer" }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: space.s4 }}>
        <div style={{ minWidth: 0, ...ellipsis }}>
          {caret(open)} <span title={job.fileName ?? ""} style={{ fontFamily: fonts.sans, fontSize: 14, fontWeight: 600, color: semantic.text.primary }}>{job.fileName ?? "—"}</span>
          {job.userEmail && <span style={{ marginLeft: 8, ...mono(11), color: semantic.text.muted }}>{job.userEmail}</span>}
        </div>
        <div style={{ ...mono(12), color: queueWarn ? semantic.warning.text : queued ? semantic.text.muted : semantic.accent.text, whiteSpace: "nowrap" }}>
          {queued
            ? L(`排队中 ${fmtClock(job.elapsedSec)}`, `Queued ${fmtClock(job.elapsedSec)}`)
            : `${job.phase ?? "—"} · ${job.progress}%`}
        </div>
      </div>

      {engines && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 9 }}>
          {engineKeys(engines).map((k) => (
            <EngineChip key={k} code={k} status={engines[k].status} parts={k === prim?.tag && prim ? `${prim.done ?? 0}/${prim.total ?? "?"}片` : undefined} />
          ))}
        </div>
      )}

      <div style={{ marginTop: 9, ...mono(11), color: semantic.text.muted }}>
        {[
          job.lang ? langName(job.lang, L) : null,
          m.durationSec != null ? `${L("时长", "Length")} ${fmtClock(m.durationSec)}` : null,
          rtype,
          m.cost ? `${L("总", "total")} ¥${m.cost.total}` : null,
          `${L("已跑", "Elapsed")} ${fmtClock(job.elapsedSec)}`,
          job.createdAt ? `${L("开始", "Start")} ${job.createdAt}` : null,
        ].filter(Boolean).join(" · ")}
        {job.attempts > 1 && <span style={{ color: semantic.warning.text }}> · {L("已重试", "retried")} {job.attempts - 1} {L("次", "×")}</span>}
        {queueWarn && (
          <span style={{ color: semantic.warning.text }}>
            {" · "}{L(`满 ${maxQueuedHours} 小时判失败`, `Fails at ${maxQueuedHours}h`)}
          </span>
        )}
      </div>

      {open && <EngineDetail engines={engines ?? undefined} prim={prim} g25f={m.g25f} cost={m.cost} />}
    </div>
  );
}

function RecentCard({ r, last }: { r: AdminRecent; last: boolean }) {
  const L = useL();
  const [open, setOpen] = useState(false);
  const m = r.metrics ?? {};
  // 归一：新任务用 metrics.primary；历史任务把 metrics.g25f 视作 tag=G25F 的 primary
  const prim = m.primary ?? (m.g25f ? { tag: "G25F", ...m.g25f } : null);
  const rtype = useRtype(r.recordingType);
  return (
    <div
      onClick={() => setOpen((o) => !o)}
      style={{ padding: `${space.s3}px ${space.s5}px`, borderBottom: last ? "none" : `1px solid ${semantic.border.subtle}`, cursor: "pointer" }}
    >
      <div style={{ ...ellipsis }}>
        {caret(open)} <span title={r.fileName ?? ""} style={{ fontSize: 13, color: semantic.text.secondary }}>{r.fileName ?? "—"}</span>
        {r.userEmail && <span style={{ marginLeft: 8, ...mono(11), color: semantic.text.muted }}>{r.userEmail}</span>}
        {/* 成功了但重跑过：看门狗回收的唯一痕迹。它不进待办条（这单已经好了，没什么要动手的），
            但要看得见——「谁在悄悄地要跑两遍」是先于失败出现的质量信号。 */}
        {(r.attempts ?? 1) > 1 && (
          <span style={{ marginLeft: 8, ...mono(11), color: semantic.warning.text }}>
            {L("重跑过", "requeued")} {(r.attempts ?? 1) - 1} {L("次", "×")}
          </span>
        )}
      </div>
      <div style={{ marginTop: 6, ...mono(11), color: semantic.text.muted }}>
        {[
          r.lang ? langName(r.lang, L) : null,
          m.durationSec != null ? `${L("时长", "Length")} ${fmtClock(m.durationSec)}` : null,
          rtype,
          m.cost ? `${L("总", "total")} ¥${m.cost.total}` : null,
          (r.createdAt && r.doneAt) ? `${r.createdAt} – ${r.doneAt}` : r.doneAt,
        ].filter(Boolean).join(" · ")}
      </div>
      {open && <EngineDetail engines={m.engines} prim={prim} g25f={m.g25f} cost={m.cost} />}
    </div>
  );
}

const FAIL_COLS = "1fr 150px 1.4fr 92px 52px";

function FailureRow({ f, last }: { f: AdminFailure; last: boolean }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: FAIL_COLS, gap: space.s4, padding: `${space.s3}px ${space.s5}px`, alignItems: "center", borderBottom: last ? "none" : `1px solid ${semantic.border.subtle}` }}>
      <div title={f.fileName ?? ""} style={{ fontSize: 13, color: semantic.text.secondary, ...ellipsis }}>{f.fileName ?? "—"}</div>
      <div style={{ ...mono(11), color: semantic.text.muted, ...ellipsis }}>{f.userEmail ?? "—"}</div>
      <div title={f.error ?? ""} style={{ fontSize: 12, color: semantic.danger.text, ...ellipsis }}>{f.error ?? "—"}</div>
      <div style={{ ...mono(12), color: semantic.text.muted }}>{f.failedAt}</div>
      <div style={{ ...mono(12), color: semantic.text.muted, textAlign: "right" }}>{f.attempts}</div>
    </div>
  );
}

// 付费模式徽标：只有 prepaid_manual 需盯余额；postpaid/prepaid_auto 只标平静态、不显数字。
const PAY_BADGE: Record<string, { zh: string; en: string; tone: "muted" | "success" }> = {
  postpaid: { zh: "后付费", en: "Postpaid", tone: "muted" },
  prepaid_auto: { zh: "自动充值", en: "Auto top-up", tone: "success" },
  prepaid_manual: { zh: "预充值", en: "Prepaid", tone: "muted" },
};
const toneColor = (t: string) => (t === "success" ? semantic.success.text : semantic.text.muted);
// 类别分组顺序 + 名称（转录引擎 = 把音频转成文字的；基础设施 = LLM 融合 / 联网核实等支撑）
const CAT_GROUPS: { key: string; zh: string; en: string }[] = [
  { key: "infra", zh: "基础设施", en: "Infrastructure" },
  { key: "asr", zh: "转录引擎", en: "Transcription" },
  { key: "other", zh: "其他", en: "Other" },
];

// 单个服务商小卡（统一规范，四行）：① 模型名 + 付费徽标 ② 余额（金额/小时，低水位红标）
// ③ 阈值（可改）④ 更新时间。后付费（绑卡）不显余额；api 自动查的卡只让改阈值（金额下次刷新会覆盖），手动卡可改金额+阈值。
function BalanceCard({ b, onSaved }: { b: AdminBalance; onSaved: () => void }) {
  const L = useL();
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState("");
  const [threshold, setThreshold] = useState("");
  const [busy, setBusy] = useState(false);
  const showsBalance = b.payMode !== "postpaid";   // 后付费绑卡不盯余额；其余都显
  const isApi = b.source === "api";
  const isHours = b.unit === "hours";
  const sym = b.unit === "usd" ? "$" : "¥";   // 金额卡符号：美元厂商 $、人民币 ¥
  const badge = PAY_BADGE[b.payMode ?? "prepaid_manual"] ?? PAY_BADGE.prepaid_manual;
  const u = isHours ? L("小时", "h") : sym;
  // 只截过长小数：讯飞按小时的余额是算出来的浮点，直显会变成「40.5597222222222 小时」
  // （2026-08-07 生产实测）。两位以内原样输出——否则 57.4 会变 57.40、阈值 5 会变 5.00，
  // 平白改掉一堆本来就好好的数字。
  const num = (v: number) => {
    const s = String(v);
    const dot = s.indexOf(".");
    return dot >= 0 && s.length - dot - 1 > 2 ? v.toFixed(2) : s;
  };
  const fmt = (v: number | null) => (v == null ? null : isHours ? `${num(v)} ${L("小时", "h")}` : `${sym}${num(v)}`);

  const startEdit = () => {
    setAmount(b.amountCny != null ? String(b.amountCny) : "");
    setThreshold(b.thresholdCny != null ? String(b.thresholdCny) : "");
    setEditing(true);
  };
  const save = async () => {
    setBusy(true);
    try {
      await setAdminBalance({
        vendor: b.vendor,
        amountCny: amount === "" ? undefined : Number(amount),
        thresholdCny: threshold === "" ? undefined : Number(threshold),
      });
      setEditing(false);
      onSaved();
    } catch { /* 失败保留编辑态 */ } finally { setBusy(false); }
  };

  return (
    <div style={{ width: 196, padding: space.s3, borderRadius: radius.md, background: semantic.surface.sunken,
      border: `1px solid ${b.low ? semantic.danger.text : semantic.border.subtle}`, display: "flex", flexDirection: "column", gap: 7 }}>
      {/* ① 引擎名称（具体模型）+ 付费模式徽标 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 6 }}>
        <span style={{ fontFamily: fonts.sans, fontSize: 11, lineHeight: 1.3, fontWeight: 600, color: semantic.text.primary }}>{b.label || b.vendor}</span>
        <span style={{ ...mono(10), padding: "1px 6px", borderRadius: radius.pill, background: semantic.surface.raised, color: toneColor(badge.tone), whiteSpace: "nowrap", flexShrink: 0 }}>{L(badge.zh, badge.en)}</span>
      </div>

      {!showsBalance ? (
        <div style={{ ...mono(11), color: semantic.text.muted, lineHeight: 1.5 }}>{L("绑卡 · 不会欠费", "Card on file")}</div>
      ) : editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {!isApi && (
            <input style={cardInput} inputMode="decimal" placeholder={L(`余额 ${u}`, `balance ${u}`)} value={amount} onChange={(e) => setAmount(e.target.value)} />
          )}
          <input style={cardInput} inputMode="decimal" placeholder={L(`阈值 ${u}`, `min ${u}`)} value={threshold} onChange={(e) => setThreshold(e.target.value)} />
          <div style={{ display: "flex", gap: 6 }}>
            <button className="tx-focus" disabled={busy} onClick={save} style={{ ...btnStyle(false), padding: "4px 10px", ...mono(11) }}>{L("保存", "Save")}</button>
            <button className="tx-focus" disabled={busy} onClick={() => setEditing(false)} style={{ ...btnStyle(true), padding: "4px 10px", ...mono(11) }}>{L("取消", "Cancel")}</button>
          </div>
        </div>
      ) : (
        <>
          {/* ② 余额（金额或小时） */}
          <div style={{ ...mono(16), fontWeight: b.low ? 600 : 500, color: b.low ? semantic.danger.text : semantic.text.primary }}>
            {fmt(b.amountCny) ?? <span style={{ ...mono(11), color: semantic.text.muted }}>{L("待登记", "not set")}</span>}
            {b.low && <span style={{ ...mono(11), marginLeft: 6 }}>⚠ {L("偏低", "low")}</span>}
          </div>
          {/* ③ 阈值（可改） */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
            {/* 没设阈值不是「—」那么中性：这家的低余额告警永远不会触发，得看得出来 */}
            <span style={{ ...mono(11), color: b.noThreshold ? semantic.warning.text : semantic.text.muted }}>
              {L("阈值", "min")} {fmt(b.thresholdCny) ?? (b.noThreshold ? L("未设 · 不会告警", "unset · never alerts") : "—")}
            </span>
            <button className="tx-focus" onClick={startEdit} style={linkBtn}>{L("改", "edit")}</button>
          </div>
          {/* ④ 更新时间（自动/手动 + 最近一次） */}
          <span style={{ ...mono(11), color: semantic.text.ghost }}>{isApi ? L("自动", "auto") : L("手动", "manual")} · {L("更新", "upd")} {b.updatedAt}</span>
        </>
      )}
    </div>
  );
}

function BalancesSection() {
  const L = useL();
  const [rows, setRows] = useState<AdminBalance[]>([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { getAdminBalances().then(setRows).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);
  const refreshAll = async () => {
    setBusy(true);
    try { await refreshAllBalances(); } catch { /* 忽略 */ } finally { load(); setBusy(false); }
  };

  const groupItems = (key: string) =>
    key === "other"
      ? rows.filter((r) => r.category !== "infra" && r.category !== "asr")
      : rows.filter((r) => r.category === key);

  const refreshBtn = (
    <button className="tx-focus" disabled={busy} onClick={refreshAll}
      style={{ ...mono(11), padding: "4px 10px", borderRadius: radius.sm, cursor: "pointer", border: `1px solid ${semantic.border.strong}`, background: "transparent", color: semantic.text.secondary }}>
      {busy ? L("刷新中…", "Refreshing…") : L("刷新余额", "Refresh")}
    </button>
  );

  return (
    <SectionShell title={L("服务商余额", "Vendor balances")} action={rows.length > 0 ? refreshBtn : undefined}>
      {rows.length === 0
        ? empty(L("还没有服务商数据", "No vendors yet"))
        : (
          <div style={{ padding: space.s4, display: "flex", flexDirection: "column", gap: space.s4 }}>
            {CAT_GROUPS.map((g) => ({ g, items: groupItems(g.key) })).filter(({ items }) => items.length > 0).map(({ g, items }) => (
              <div key={g.key}>
                <div style={{ ...type.label, marginBottom: space.s2 }}>{L(g.zh, g.en)}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: space.s3 }}>
                  {items.map((b) => <BalanceCard key={b.vendor} b={b} onSaved={load} />)}
                </div>
              </div>
            ))}
          </div>
        )}
    </SectionShell>
  );
}

// 后处理板块列宽：文件 / 用户 / 步骤 / 状态 / API 花费 / 时间（本机版：不收费，没有「价」这一列）
const PP_COLS = "minmax(0,2.2fr) minmax(0,1.2fr) minmax(0,1.6fr) minmax(0,1fr) 6em 6em";

function PostprocessSection({ rows }: { rows: AdminPostprocess[] }) {
  const L = useL();
  // categorize 是归类下架（2026-08-17）前的存量任务，只读兼容
  const stepName = (k: string) =>
    k === "narrate" ? L("视角转换", "Narrative")
      : k === "categorize" ? L("归类", "Categorize")
        : k === "redact" ? L("脱敏", "Redact") : k;
  const anyDegraded = rows.some((r) => (r.degradedSteps?.length ?? 0) > 0);
  return (
    <SectionShell title={L("后处理", "Post-processing")}>
      {rows.length === 0 ? empty(L("暂无后处理任务", "No post-processing jobs")) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: PP_COLS, gap: space.s4, padding: `${space.s3}px ${space.s5}px`, borderBottom: `1px solid ${semantic.border.default}`, background: semantic.surface.page, ...type.label }}>
            <div>{L("文件", "File")}</div>
            <div>{L("用户", "User")}</div>
            <div>{L("步骤", "Steps")}</div>
            <div>{L("状态", "Status")}</div>
            {/* 本机版：后处理直接走「设置」里的模型后端——走 API 时这一列是这一单花掉的钱（¥），本机模型不花钱显示 — */}
            <div style={{ textAlign: "right" }}>{L("API 花费", "API cost")}</div>
            <div style={{ textAlign: "right" }}>{L("时间", "When")}</div>
          </div>
          {rows.map((r, i) => {
            const deg = r.degradedSteps ?? [];
            const tone = r.status === "failed" ? semantic.danger.text
              : r.status === "done" ? semantic.success.text : semantic.text.secondary;
            const statusText = r.status === "running"
              ? `${L("处理中", "Running")} ${r.stepIndex}/${r.steps.length}`
              : r.status === "queued" ? L("排队", "Queued")
                : r.status === "failed" ? L("失败", "Failed") : L("完成", "Done");
            return (
              <div key={r.jobId} style={{ display: "grid", gridTemplateColumns: PP_COLS, gap: space.s4, padding: `${space.s3}px ${space.s5}px`, borderBottom: i === rows.length - 1 ? "none" : `1px solid ${semantic.border.subtle}`, alignItems: "baseline", fontSize: 13 }}>
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: semantic.text.primary }} title={r.fileName ?? r.jobId}>{r.fileName ?? r.jobId}</div>
                <div style={{ ...mono(11), color: semantic.text.secondary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.userEmail}</div>
                <div style={{ color: semantic.text.secondary }}>
                  {r.steps.map((k, n) => (
                    <span key={k}>
                      {n > 0 && " · "}
                      <span style={{ color: r.failedStep === k ? semantic.danger.text : undefined }}>{stepName(k)}</span>
                      {/* 降级徽标：这一步是 Claude 撞顶后由 DeepSeek 兜底出的。
                          用户回头说「这份不如上次」时，运营一眼就能看出来，不用翻日志。 */}
                      {deg.includes(k) && (
                        <span style={{ ...mono(10), marginLeft: 4, padding: "1px 4px", color: semantic.warning.text, border: `1px solid ${semantic.warning.text}`, borderRadius: radius.sm }}>
                          {L("降级", "fallback")}
                        </span>
                      )}
                    </span>
                  ))}
                </div>
                <div style={{ color: tone }} title={r.errorPublic ?? undefined}>
                  {statusText}
                  {r.qcFixCount > 0 && <span style={{ ...mono(10), color: semantic.text.muted, marginLeft: 5 }}>{L(`修${r.qcFixCount}`, `${r.qcFixCount} fix`)}</span>}
                </div>
                <div style={{ ...mono(11), textAlign: "right", color: semantic.text.secondary }}>{r.dsCostCny ? `¥${r.dsCostCny.toFixed(2)}` : "—"}</div>
                <div style={{ ...mono(11), textAlign: "right", color: semantic.text.muted }}>{r.updatedAt}</div>
              </div>
            );
          })}
          {anyDegraded && (
            <div style={{ padding: `${space.s3}px ${space.s5}px`, borderTop: `1px solid ${semantic.border.default}`, color: semantic.text.muted, fontSize: 12 }}>
              {L("「降级」= 该步 Claude 撞顶，改由 DeepSeek 兜底产出；内容完整，措辞与分段会粗一些。",
                 "\u201cfallback\u201d = Claude hit its limit on that step, so DeepSeek produced it. Nothing is dropped; wording and paragraphing are coarser.")}
            </div>
          )}
        </>
      )}
    </SectionShell>
  );
}

// ── 告警记录 ──────────────────────────────────────────────────────────────────
// 改造前 13 处告警全是「发完即忘」：漏看一次就永远查不到，而告警恰恰是最需要事后追溯的东西
// ——出事后第一句话往往是「之前有没有征兆」。这个区块就是回答那句话的地方。
const TIER_TONE: Record<string, string> = {
  act: semantic.danger.text, watch: semantic.warning.text, fyi: semantic.text.muted,
};

function AlertsSection() {
  const L = useL();
  const [items, setItems] = useState<AdminAlert[] | null>(null);
  const [err, setErr] = useState(false);
  const [days, setDays] = useState(7);
  const [onlyTodo, setOnlyTodo] = useState(false);
  const [busy, setBusy] = useState(0);

  const load = useCallback(() => {
    getAdminAlerts(days, onlyTodo).then((r) => { setItems(r.items); setErr(false); })
      .catch(() => setErr(true));
  }, [days, onlyTodo]);
  useEffect(() => { load(); }, [load]);

  const handle = async (id: number) => {
    setBusy(id);
    try { await markAlertHandled(id); load(); } catch { /* 失败就保持原样，下次轮询会纠正 */ }
    setBusy(0);
  };
  // 本机版：没有「发一份增长周报」（本机不发邮件、没有增长口径）

  const tierLabel = (t: string) => (
    t === "act" ? L("要动手", "Act") : t === "watch" ? L("要留意", "Watch") : L("知会", "FYI"));

  return (
    <SectionShell
      title={L("告警", "Alerts")}
      action={
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {/* 「只看未处理」不限时间窗（2026-09-03 生产实见：待办条亮着，列表默认 7 天却说没有）——
              勾上之后时间按钮就没有意义，藏掉，别让人以为它还在过滤 */}
          {!onlyTodo && [7, 30, 180].map((d) => (
            <button key={d} className="tx-focus" onClick={() => setDays(d)} style={btnStyle(days !== d)}>
              {L(`${d} 天`, `${d}d`)}
            </button>
          ))}
          <button className="tx-focus" onClick={() => setOnlyTodo((o) => !o)} style={btnStyle(!onlyTodo)}>
            {L("只看未处理", "Unhandled")}
          </button>
        </div>
      }
    >
      {err ? empty(L("读不到告警记录", "Could not load alerts"))
        : !items ? empty(L("读取中…", "Loading…"))
          : items.length === 0 ? empty(onlyTodo ? L("没有未处理的告警", "Nothing unhandled") : L("这段时间没有告警", "No alerts in this window"))
            : items.map((a, i) => (
              <div key={a.id} style={{ display: "flex", gap: space.s4, alignItems: "flex-start", padding: `${space.s3}px ${space.s5}px`, borderBottom: i === items.length - 1 ? "none" : `1px solid ${semantic.border.subtle}` }}>
                <span aria-hidden style={{ color: TIER_TONE[a.tier], fontSize: 11, lineHeight: "20px" }}>●</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, color: semantic.text.secondary }}>{a.subject}</div>
                  <div style={{ marginTop: 4, ...mono(11), color: semantic.text.muted }}>
                    {[
                      a.at,
                      tierLabel(a.tier),
                      // 发信结果要看得见：此前发失败只在日志里留一行字，你不会知道有一封没发出去
                      a.mailed === "sent" ? L("邮件已送达", "mailed")
                        : a.mailed === "failed" ? null : L("未发信", "not mailed"),
                      // 「冷却期内又触发 N 次」以前是丢掉的——它区分「偶发一次」和「一直在响」，
                      // 而这两件事该做的处置完全不同
                      a.suppressed > 0 ? L(`冷却期内又触发 ${a.suppressed} 次`, `+${a.suppressed} more suppressed`) : null,
                      a.handled ? L(`已处理 · ${a.handledBy ?? ""}`, `handled · ${a.handledBy ?? ""}`) : null,
                    ].filter(Boolean).join(" · ")}
                    {a.mailed === "failed" && (
                      <span style={{ color: semantic.danger.text }}> · {L("邮件发送失败", "mail failed")}</span>
                    )}
                  </div>
                </div>
                {/* 「已处理」只给 act：watch/fyi 自己会消失，多点一次是白费动作 */}
                {a.tier === "act" && !a.handled && (
                  <button className="tx-focus" disabled={busy === a.id} onClick={() => handle(a.id)}
                          style={{ ...btnStyle(true), whiteSpace: "nowrap" }}>
                    {L("已处理", "Handled")}
                  </button>
                )}
              </div>
            ))}
    </SectionShell>
  );
}

// ── 运行信号 ──────────────────────────────────────────────────────────────────
// 这一组不属于任何单个节点，但它们说明「整套东西现在健不健康」：看门狗回收数、近 60 分钟成败、月度重跑比例。
// 本机版（与线上不同）：去掉登录验证码用量、在飞机器、并发闸、P3 降级比例与缓存命中率——本机不登录、不派云机器、
// 不走 Claude 名额闸，定字模型是设置里选的（没有降级），本机定字也不记缓存命中明细（那一行会永远「暂无样本」）。
function OpsSection() {
  const L = useL();
  const [d, setD] = useState<AdminHealth | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    const load = () => { getAdminHealth(24).then(setD).catch(() => setErr(true)); };
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  const n = (v: number | null | undefined) => (v == null ? "—" : String(v));
  // 比例一律「百分数（分子/分母）」：光给百分数看不出样本量，而 1/1 = 100% 说明不了任何事
  const pct = (v: number | null | undefined, sub: string) =>
    v == null ? "—" : `${(v * 100).toFixed(1)}%　${sub}`;

  return (
    <SectionShell title={L("运行信号", "Operating signals")}>
      {err ? empty(L("读不到运行信号", "Could not load"))
        : !d ? empty(L("读取中…", "Loading…"))
          : (
            <div style={{ padding: `${space.s3}px ${space.s5}px ${space.s4}px` }}>
              <SignalRow label={L("看门狗回收 · 近 24h", "Watchdog requeues · 24h")} value={n(d.ops.watchdogRequeues)}
                   hint={L("重跑往往成功，所以这不会体现在成功率上", "Requeues usually succeed, so this never shows up in the success rate")} />
              <SignalRow label={L("近 60 分钟", "Last 60 min")}
                   value={`${n(d.ops.recent60.done)} ${L("完成", "done")}　${n(d.ops.recent60.failed)} ${L("失败", "failed")}`} />
              {/* 月度：24h 窗样本太小（一天几单，重跑一单就是一大截），趋势只有月度看得出来 */}
              {d.monthly && (
                <SignalRow label={L(`重跑比例 · 近 ${d.monthly.days} 天`, `Retried · ${d.monthly.days}d`)}
                     value={pct(d.monthly.retry.ratio, `${d.monthly.retry.retried}/${d.monthly.retry.total}`)}
                     hint={L("跑了不止一次的单占比", "Share of jobs that ran more than once")} />
              )}
            </div>
          )}
    </SectionShell>
  );
}

function SignalRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: space.s4, padding: `${space.s3}px 0`, borderTop: `1px solid ${semantic.border.subtle}`, flexWrap: "wrap" }}>
      <div style={{ ...type.label, width: 160, flex: "0 0 auto" }}>{label}</div>
      <div style={{ ...mono(12), color: semantic.text.primary, minWidth: 0, overflowWrap: "anywhere" }}>{value}</div>
      {hint && <div style={{ ...mono(11), color: semantic.text.muted, minWidth: 0, overflowWrap: "anywhere" }}>{hint}</div>}
    </div>
  );
}

// ── 这台电脑 ──────────────────────────────────────────────────────────────────
// 本机版独有（线上「资源」看云供应商、云机器容量、证书到期）：本机要回答的是「这台电脑现在跑不跑得动、
// 东西存在哪、定字发给了谁」。数字全由 /admin/local-resources 给，前端不算；密钥只有设没设，没有值。
const DISK_WARN_GB = 5;   // 剩这么多就提醒：录音、各阶段稿子都写在本机，余量太小转到一半会写不下

function LocalResourcesSection() {
  const L = useL();
  const [d, setD] = useState<AdminLocalResources | null>(null);
  const [err, setErr] = useState(false);
  const [probe, setProbe] = useState<{ ok: boolean; why: string } | "busy" | null>(null);
  useEffect(() => { getAdminLocalResources().then(setD).catch(() => setErr(true)); }, []);

  const gb = (mb: number) => `${(mb / 1024).toFixed(1)} GB`;
  // 与「设置 · 数据去哪」同一套说法（同一份译文），两处说法不一样会让人以为是两件事
  const whatText = (w: DataFlowItem["what"]) => ({
    audio: L("录音", "Audio"),
    transcript: L("定字时的稿子文字", "Transcript text during proofreading"),
    glossary: L("术语库起草与检查", "Glossary drafting and checks"),
    postprocess: L("后处理的稿子文字", "Transcript text in post-processing"),
    search: L("联网核实的搜索词", "Search terms for online checks"),
  })[w];
  const destText = (f: DataFlowItem) =>
    f.dest === "local" ? L("不离开这台电脑", "Stays on this computer")
      : f.dest === "remote" ? L.t("发给 {0}", "Sent to {0}", f.host)
        : f.dest === "claude" ? L("发给 Anthropic（Claude 订阅）", "Sent to Anthropic (Claude subscription)")
          : L("当前后端用不了这一项", "Not available with this backend");

  const b = d?.backend;
  const backendText = !b ? "—"
    : b.kind === "claude_p" ? L.t("Claude 订阅 · {0}", "Claude subscription · {0}", b.model)
      : b.local ? L.t("{0} · 本机", "{0} · on this computer", b.model) : `${b.model} @ ${b.host}`;
  const keyText = b && b.kind === "openai" && !b.local && b.keyEnv
    ? (b.keySet ? L.t("密钥从环境变量 {0} 读取 · 已设置", "Key is read from environment variable {0} · set", b.keyEnv)
      : <span style={{ color: semantic.danger.text }}>{L.t("密钥从环境变量 {0} 读取 · 未设置", "Key is read from environment variable {0} · not set", b.keyEnv)}</span>)
    : undefined;

  const probeBtn = (
    <button className="tx-focus" disabled={probe === "busy" || !b} style={btnStyle(true)}
            onClick={() => { setProbe("busy"); probeBackend().then(setProbe).catch(() => setProbe({ ok: false, why: "" })); }}>
      {probe === "busy" ? L("测试中…", "Testing…") : L("测试连接", "Test connection")}
    </button>
  );

  return (
    <SectionShell title={L("这台电脑", "This computer")} action={probeBtn}>
      {err ? empty(L("读不到本机资源", "Could not load this computer's resources"))
        : !d ? empty(L("读取中…", "Loading…"))
          : (
            <div style={{ padding: `${space.s3}px ${space.s5}px ${space.s4}px` }}>
              <SignalRow label={L("识别模型", "Speech models")}
                value={!d.models ? "—" : d.models.ready
                  ? L.t("已装齐 {0} 个 · {1}", "All {0} installed · {1}", d.models.items.length, gb(d.models.installedMb))
                  : <span style={{ color: semantic.danger.text }}>{L.t("还缺 {0} 个 · {1}", "{0} missing · {1}", d.models.items.filter((m) => !m.installed).length, gb(d.models.missingMb))}</span>}
                hint={d.models?.cacheDir} />
              <SignalRow label={L("存放位置", "Storage")}
                value={d.disk ? L.t("转录与结果占 {0} MB", "Transcripts and results use {0} MB", d.disk.dataMb) : "—"}
                hint={d.disk?.path} />
              <SignalRow label={L("磁盘剩余", "Free disk")}
                value={d.disk ? <span style={{ color: d.disk.freeGb < DISK_WARN_GB ? semantic.danger.text : undefined }}>{`${d.disk.freeGb} / ${d.disk.totalGb} GB`}</span> : "—"} />
              <SignalRow label={L("内存", "Memory")}
                value={d.memory ? L.t("共 {0} · 此刻可用 {1}", "{0} total · {1} free now", gb(d.memory.totalMb), gb(d.memory.availableMb)) : "—"} />
              <SignalRow label={L("同时跑几台引擎", "Engines at once")} value={d.memory ? String(d.memory.parallel) : "—"}
                hint={L("按核数、内存和最重的那台引擎自动算；配置里的 engines.parallel 可以指定",
                        "Worked out from cores, memory and the heaviest engine; set engines.parallel in the config to override")} />
              <SignalRow label={L("模型后端", "Model backend")} value={backendText} hint={keyText} />
              {probe && probe !== "busy" && (
                <div role="status" style={{ ...mono(12), padding: `0 0 ${space.s3}px 176px`, color: probe.ok ? semantic.success.text : semantic.warning.text, overflowWrap: "anywhere" }}>
                  {probe.ok ? L("连接正常", "Connected") : L.t("连不上：{0}", "Not reachable: {0}", probe.why)}
                </div>
              )}
              <SignalRow label={L("数据去哪", "Where your data goes")}
                value={
                  <div data-testid="panel-data-flow" style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                    {d.dataFlow.map((f) => (
                      <div key={f.what}>
                        {whatText(f.what)}{"　→　"}
                        <span data-dest={f.dest} style={{ color: f.dest === "local" ? semantic.success.text : f.dest === "off" ? semantic.text.muted : semantic.warning.text }}>{destText(f)}</span>
                      </div>
                    ))}
                  </div>
                } />
            </div>
          )}
    </SectionShell>
  );
}

// ── 待办条 ────────────────────────────────────────────────────────────────────
// 四个 Tag 全是「看」。没有这条，打开驾驶舱得自己挨个点进去找有没有出事——而绝大多数时候
// 什么事都没有，于是慢慢就不看了，等真出事时也不会正好打开它。
//
// **只报需要人动手的**：「撞顶降级」不进来（它会自愈），「认证失效」要进来（不修永远不会好）。
// 这正是 p3_health 那条「认证失效与额度撞顶必须分开」的纪律在界面上的样子。往这里加条目前，
// 先问一句「看到它之后我要做什么」——答不上来的就不该进来，进来一条就稀释一次这条的可信度。
//
// 自己取数（余额/本机资源/健康度各一个接口）而不是从 AdminPage 传下来：它要在四个 Tag 上都在，
// 而那几个板块各自只在一个 Tag 里。多两个 GET 换组件互不牵扯，在内部运营页上是划算的。
const QUEUE_WARN_RATIO = 0.25;   // 排到判死窗口的 1/4 就提醒——还剩 3/4 的时间够动手
const SAME_CAUSE_MIN = 3;        // 同一原因失败几次算「一个问题」而不是「几次意外」

type Todo = { id: string; tone: "danger" | "warn"; text: string; where: string };

function AlertStrip({ data }: { data: AdminOverview | null }) {
  const L = useL();
  const [bal, setBal] = useState<AdminBalance[] | null>(null);
  const [nh, setNh] = useState<AdminHealth | null>(null);
  const [alerts, setAlerts] = useState<AdminAlert[] | null>(null);
  const [res, setRes] = useState<AdminLocalResources | null>(null);

  useEffect(() => {
    // **每个来源各自 try/catch，且用 async 包住**：`f().catch()` 只挡得住 reject，挡不住
    // f 本身同步抛（比如取数函数还没初始化好）——那一下会打断整个 effect，让后面几路也不取。
    // 待办条的每一路都是独立信号，一路坏了该少报一类，不该整条哑掉。
    const load = async () => {
      const pull = async <T,>(f: () => Promise<T>, set: (v: T) => void) => {
        try { set(await f()); } catch { /* 少报一类 */ }
      };
      await Promise.all([
        pull(getAdminBalances, setBal),
        pull(() => getAdminHealth(24), setNh),
        pull(() => getAdminAlerts(30, true), (r) => setAlerts(r.items)),
        pull(getAdminLocalResources, setRes),
      ]);
    };
    void load();
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, []);

  const todos: Todo[] = [];
  // ① 模型后端要的密钥没设：定字、术语库助手、后处理每一次都调不通，而且不动手永远不会好。
  //   本机版（与线上不同）：线上排第一的是「Claude 订阅认证失效」；本机对应的就是这一条。
  const b = res?.backend;
  if (b && b.kind === "openai" && !b.local && b.keyEnv && !b.keySet) {
    todos.push({ id: "key", tone: "danger", where: L("资源", "Resources"),
      text: L.t("模型后端 {0} 要的环境变量 {1} 没设 —— 定字、术语库助手、后处理都调不通",
                "The model backend {0} needs environment variable {1}, which isn't set — proofreading, the glossary assistant and post-processing can't reach it",
                b.model, b.keyEnv) });
  }
  // ② 磁盘快满：录音和各阶段稿子都写在这台电脑上，转到一半写不下就是一单白跑
  if (res?.disk && res.disk.freeGb < DISK_WARN_GB) {
    todos.push({ id: "disk", tone: "warn", where: L("资源", "Resources"),
      text: L.t("磁盘只剩 {0} GB —— 录音和稿子都存在这台电脑上", "Only {0} GB of disk left — recordings and transcripts are stored on this computer", res.disk.freeGb) });
  }
  for (const b of bal ?? []) {
    if (b.low) {
      todos.push({ id: `bal-${b.vendor}`, tone: "warn", where: L("资源", "Resources"),
        text: L(`${b.label ?? b.vendor} 余额 ${b.amountCny} 低于阈值 ${b.thresholdCny}`,
                `${b.label ?? b.vendor} balance ${b.amountCny} below ${b.thresholdCny}`) });
    }
  }
  // 预充值却没设阈值 = 低余额告警对这几家永远不会触发。**这跟余额充足在界面上长得一样**，
  // 但它是「没有保护」而不是「没有风险」。聚成一条：分开报只会把待办条淹掉。
  const noThr = (bal ?? []).filter((b) => b.noThreshold);
  if (noThr.length) {
    // 点名前三家就够认出是哪一类；全列会把这一行撑到两三行，整条待办条就不再是「一眼扫完」。
    const shown = noThr.slice(0, 3).map((b) => b.label ?? b.vendor);
    const names = shown.join("、") + (noThr.length > shown.length ? L("等", " and more") : "");
    todos.push({ id: "bal-nothreshold", tone: "warn", where: L("资源", "Resources"),
      text: L(`${noThr.length} 家预充值服务商没设余额阈值（${names}）—— 余额见底也不会有任何告警`,
              `${noThr.length} prepaid vendor(s) have no balance threshold (${names}) — they can never alert`) });
  }
  // ③ 排队时长：在它被判失败之前的那段窗口，是唯一能干预的时间
  const maxH = data?.queuedMaxHours ?? 24;
  const stuck = (data?.jobs ?? []).filter((j) => j.status === "queued" && j.elapsedSec > maxH * 3600 * QUEUE_WARN_RATIO);
  if (stuck.length) {
    const worst = Math.max(...stuck.map((j) => j.elapsedSec));
    todos.push({ id: "queue", tone: "warn", where: L("运行", "Live"),
      text: L(`${stuck.length} 单排队已超 ${Math.floor(worst / 3600)} 小时（满 ${maxH} 小时判失败）`,
              `${stuck.length} job(s) queued over ${Math.floor(worst / 3600)}h (failed at ${maxH}h)`) });
  }
  // ④ 某一路引擎悄悄坏了。**这是唯一能提前发现它的信号**——某一路挂掉是「跳过继续」，
  //   整单照样出稿，从任务列表上完全看不出来。判定用「相对基线掉了多少」，且在后端算好。
  for (const e of nh?.alerts.engines ?? []) {
    todos.push({ id: `eng-${e.tag}`, tone: "warn", where: L("工作流 · P1", "Workflow · P1"),
      text: L(`${e.tag} 近 24h 成功率 ${e.pct}%（平时 ${e.basePct}%，${e.ok}/${e.total}）`,
              `${e.tag} at ${e.pct}% in 24h (usually ${e.basePct}%)`) });
  }
  // ⑤ 未处理的「要动手」告警。**只接 act 档**：watch 档要么待办条已经按活状态报了，要么它自己会好。
  for (const a of alerts ?? []) {
    todos.push({ id: `alert-${a.id}`, tone: "danger", where: L("资源 · 告警", "Resources · Alerts"),
      text: a.suppressed > 0
        ? L(`${a.subject}（还触发了 ${a.suppressed} 次）`, `${a.subject} (+${a.suppressed} more)`)
        : a.subject });
  }
  // ⑥ 同因失败聚类：5 单失败是 5 个问题还是同一个炸了 5 次，这个差别决定要不要立刻动手
  const causes = new Map<string, number>();
  for (const f of data?.failures ?? []) {
    const c = failCause(f.error);
    causes.set(c, (causes.get(c) ?? 0) + 1);
  }
  for (const [cause, n] of causes) {
    if (n >= SAME_CAUSE_MIN) {
      todos.push({ id: `cause-${cause}`, tone: "warn", where: L("运行", "Live"),
        text: L(`近 7 天 ${n} 单同因失败：${cause}`, `${n} failures with the same cause in 7d: ${cause}`) });
    }
  }

  const danger = todos.some((t) => t.tone === "danger");
  const tone = danger ? semantic.danger.text : semantic.warning.text;
  return (
    <div style={{
      marginTop: space.s5,
      // 全用 longhand：`border` 简写与 `borderLeftWidth` 混用会被 React 判成冲突并告警
      borderStyle: "solid", borderWidth: "1px 1px 1px 4px",
      borderColor: todos.length ? tone : semantic.border.default,
      borderRadius: radius.md,
      background: todos.length ? semantic.accent.bgTint : semantic.surface.raised,
      padding: `${space.s4}px ${space.s5}px`,
    }}>
      {todos.length === 0 ? (
        <div style={{ ...mono(12), color: semantic.text.muted }}>
          {L("没有需要处理的事。", "Nothing needs you right now.")}
        </div>
      ) : (
        <>
          <div style={{ ...type.label, color: tone, marginBottom: 7 }}>
            {L(`需要处理 ${todos.length} 件`, `${todos.length} need you`)}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {todos.map((t) => (
              <div key={t.id} style={{ ...mono(12), color: t.tone === "danger" ? semantic.danger.text : semantic.text.secondary }}>
                · {t.text}
                <span style={{ color: semantic.text.muted }}>　→ {t.where}</span>
              </div>
            ))}
          </div>
        </>
      )}
      <div style={{ marginTop: 8, ...mono(10), color: semantic.text.muted }}>
        {L("只报不动手就不会好的事。", "Only things that stay broken without you appear here.")}
      </div>
    </div>
  );
}

// 失败原因归并：取首行前 40 字。**不做更聪明的归一**——把不同的错归成一类，比不归还糟
// （会让人以为「就这一个问题」而漏掉另一个）。宁可多分几类。
function failCause(err: string | null): string {
  const first = (err ?? "").split("\n").map((s) => s.trim()).filter(Boolean).pop() ?? "";
  return first.slice(0, 40) || "—";
}

// ── 任务流：原来的「实时任务 / 最近完成 / 最近失败」三块合一 ──────────────────
// 三块结构几乎一样，只是 WHERE 不同。合并之后页面短一半，而且能做跨状态的事——比如按失败
// 原因分组，这在三张分开的表里做不到。
type Flow = "all" | "live" | "done" | "failed";

function TaskFlow({ data }: { data: AdminOverview }) {
  const L = useL();
  const [f, setF] = useState<Flow>("all");
  const [cause, setCause] = useState<string | null>(null);   // 点原因分组后的二次筛选

  const jobs = data.jobs ?? [];
  const recent = data.recent ?? [];
  const failures = data.failures ?? [];
  const shownFailures = cause ? failures.filter((x) => failCause(x.error) === cause) : failures;

  const tabs: { k: Flow; label: string; n: number }[] = [
    { k: "all", label: L("全部", "All"), n: jobs.length + recent.length + failures.length },
    { k: "live", label: L("进行中", "Live"), n: jobs.length },
    { k: "done", label: L("已完成", "Done"), n: recent.length },
    { k: "failed", label: L("失败", "Failed"), n: failures.length },
  ];

  // 失败原因分组：只在有重复原因时才出现——每条各不相同的时候，分组栏纯属噪音
  const causes = [...failures.reduce((m, x) => {
    const c = failCause(x.error);
    return m.set(c, (m.get(c) ?? 0) + 1);
  }, new Map<string, number>())].filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]);

  const showLive = f === "all" || f === "live";
  const showDone = f === "all" || f === "done";
  const showFail = f === "all" || f === "failed";

  return (
    <SectionShell
      title={L("任务流", "Task flow")}
      action={
        <div style={{ display: "flex", gap: 6 }}>
          {tabs.map((t) => (
            <button key={t.k} className="tx-focus" onClick={() => { setF(t.k); setCause(null); }}
                    style={{ ...btnStyle(f !== t.k), padding: "4px 10px" }}>
              {t.label} {t.n}
            </button>
          ))}
        </div>
      }
    >
      {showFail && causes.length > 0 && (
        <div style={{ padding: `${space.s3}px ${space.s5}px`, borderBottom: `1px solid ${semantic.border.default}`, background: semantic.surface.page, display: "flex", gap: space.s3, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ ...type.label }}>{L("同因失败", "Same cause")}</span>
          {causes.map(([c, n]) => (
            <button key={c} className="tx-focus" onClick={() => setCause(cause === c ? null : c)}
                    style={{ ...btnStyle(cause !== c), padding: "3px 9px", maxWidth: 380, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    title={c}>
              {n}× {c}
            </button>
          ))}
        </div>
      )}

      {showLive && (jobs.length > 0
        ? jobs.map((j, i) => <JobCard key={j.id} job={j} last={!showDone && !showFail && i === jobs.length - 1} maxQueuedHours={data.queuedMaxHours ?? 24} />)
        : f === "live" ? empty(L("暂无进行中的任务", "No jobs in flight")) : null)}

      {showDone && (recent.length > 0
        ? recent.map((r, i) => <RecentCard key={r.id} r={r} last={!showFail && i === recent.length - 1} />)
        : f === "done" ? empty(L("暂无已完成任务", "No completed jobs yet")) : null)}

      {showFail && (shownFailures.length > 0 ? (
        <>
          <div style={{ display: "grid", gridTemplateColumns: FAIL_COLS, gap: space.s4, padding: `${space.s3}px ${space.s5}px`, borderBottom: `1px solid ${semantic.border.default}`, background: semantic.surface.page, ...type.label }}>
            <div>{L("文件", "File")}</div>
            <div>{L("用户", "User")}</div>
            <div>{L("原因", "Reason")}</div>
            <div>{L("时间", "When")}</div>
            <div style={{ textAlign: "right" }}>{L("重试", "Tries")}</div>
          </div>
          {shownFailures.map((x, i) => <FailureRow key={x.id} f={x} last={i === shownFailures.length - 1} />)}
        </>
      ) : f === "failed" ? empty(L("近 7 天没有失败", "No failures in the last 7 days")) : null)}

      {f === "all" && jobs.length + recent.length + failures.length === 0 && empty(L("还没有任务", "No jobs yet"))}
    </SectionShell>
  );
}

// 增长放在用户前面（Growth 单 §四 ②）：它是「人的视角」的汇总版——先看数，要看人再点进用户
// 本机版：去掉「增长」「用户」（连同退款）——本机单用户、不收费
const TAGS = ["live", "workflow", "resource"] as const;
type Tag = typeof TAGS[number];

export function AdminPage() {
  const L = useL();
  const [tag, setTag] = useState<Tag>("live");
  const [data, setData] = useState<AdminOverview | null>(null);
  const [err, setErr] = useState(false);

  const load = useCallback(() => {
    getAdminOverview().then((d) => { setData(d); setErr(false); }).catch(() => setErr(true));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);   // 自轮询：进行中任务的 G25F 分片实时跳动
    return () => clearInterval(t);
  }, [load]);

  const s = data?.summary;
  const pp = data?.postprocess ?? [];

  const tagLabel: Record<Tag, string> = {
    live: L("运行", "Live"),
    workflow: L("工作流", "Workflow"),
    resource: L("资源", "Resources"),
  };

  return (
    <div className="tx-scroll" style={{ flex: 1, padding: layout.pagePad, overflowY: "auto", minHeight: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: space.s4 }}>
        <h1 style={{ ...type.h1, margin: 0 }}>{L("运行面板", "System status")}</h1>
        <span style={{ ...mono(11), color: err ? semantic.danger.text : semantic.text.muted }}>
          {err ? L("连接异常 · 重试中", "Connection error · retrying") : L("每 5 秒自动刷新 · 点任务行看明细", "Auto-refresh 5s · click a row for detail")}
        </span>
      </div>

      {/* Tag 条。四个 Tag 是同一条流水线的四种切法：任务视角 / 节点视角 / 供应商视角 / 人的视角。
          正交的好处是不会有东西无处安放、也不会有东西该放两处——同一次故障在四个 Tag 里长相不同。 */}
      <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${semantic.border.default}` }}>
        {TAGS.map((t) => (
          <button key={t} className="tx-focus" onClick={() => setTag(t)}
                  style={{
                    ...mono(13), padding: `8px 16px`, border: "none", cursor: "pointer",
                    background: "transparent",
                    color: tag === t ? semantic.text.primary : semantic.text.muted,
                    borderBottom: `2px solid ${tag === t ? semantic.accent.brand : "transparent"}`,
                    marginBottom: -1, fontWeight: tag === t ? 600 : 400,
                  }}>
            {tagLabel[t]}
          </button>
        ))}
      </div>

      {/* 待办条钉在 Tag 条外面，四个 Tag 上都在。**这是刻意的**：它存在的意义就是让人不必
          挨个 Tag 去找有没有出事，收进某一个 Tag 里等于把那个问题原样装回去。 */}
      <AlertStrip data={data} />

      {tag === "live" && (
        <>
          <div style={{ display: "flex", gap: space.s4, marginTop: space.s5 }}>
            <SummaryCard label={L("处理中", "Running")} value={s?.running ?? 0} tone={(s?.running ?? 0) > 0 ? semantic.accent.text : undefined} />
            <SummaryCard label={L("排队", "Queued")} value={s?.queued ?? 0} />
            <SummaryCard label={L("今日完成", "Done today")} value={s?.doneToday ?? 0} tone={(s?.doneToday ?? 0) > 0 ? semantic.success.text : undefined} />
            <SummaryCard label={L("今日失败", "Failed today")} value={s?.failedToday ?? 0} tone={(s?.failedToday ?? 0) > 0 ? semantic.danger.text : undefined} />
          </div>
          {data ? <TaskFlow data={data} /> : <SectionShell title={L("任务流", "Task flow")}>{empty(L("读取中…", "Loading…"))}</SectionShell>}
          <PostprocessSection rows={pp} />
        </>
      )}

      {tag === "workflow" && <WorkflowTab />}

      {tag === "resource" && (
        <>
          <LocalResourcesSection />
          <OpsSection />
          <AlertsSection />
          <BalancesSection />
        </>
      )}


    </div>
  );
}

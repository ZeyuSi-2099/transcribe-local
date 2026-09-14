// AdminPage — 运营驾驶舱（仅管理员）：进行中 + 最近完成 + 最近失败 + 当日汇总。
// 内部运营台，非客户页：原则「密、清、稳」，复用设计系统（数字 mono 等宽、语义色、衬线仅标题）。
// 任务行可展开下钻：各引擎 {状态+耗时}，主引擎（新任务 ELV/历史任务 G25F）再到每片（一次过/重试几次/耗时，仅历史任务有）+ 聚合 + 成本。
// 自轮询每 5s。访问鉴权在后端（非管理员 404）；侧栏入口由 me.isAdmin 控制。
import { useEffect, useState, useCallback, useRef } from "react";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow, layout } from "../styles/tokens";
import { fmtClock } from "../lib/format";
import { langName } from "../lib/langs";
import {
  getAdminOverview, getAdminBalances, setAdminBalance, refreshAllBalances,
  getAdminTopups, startAdminRefund, getAdminP3Health, probeP3,
  getAdminP3Config, saveAdminP3Config, getAdminExpiries, getAdminHealth,
  getAdminAlerts, markAlertHandled, sendGrowthReport,
  type AdminOverview, type AdminJob, type AdminFailure, type AdminRecent,
  type AdminMetrics, type EngineState, type AdminBalance, type AdminTopup, type AdminPostprocess,
  type AdminP3Health, type AdminP3Config, type AdminExpiry, type AdminHealth,
  type AdminAlert,
} from "../lib/api";
import { usd } from "../lib/pricing";
import { mono, ellipsis, caret, SectionShell, empty, btnStyle, cardInput, linkBtn, fmtAt } from "./admin/ui";
import { WorkflowTab } from "./admin/WorkflowTab";
import { UsersTab } from "./admin/UsersTab";
import { GrowthTab } from "./admin/GrowthTab";

const ENGINE_ORDER = ["G25F", "ELV", "GEM", "DB", "FA", "XF", "AAI", "SPM", "SNX"];   // G25F=历史任务主轨；ELV=新任务主轨；GEM/AAI/SPM/SNX=新阵容备用/参考轨
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
            {ENGINE_ORDER.filter((k) => engines[k]).map((k) => (
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
            P1: {ENGINE_ORDER.filter((k) => k in cost.p1).map((k) => `${k} ¥${cost.p1[k]}`).join(" · ")}
            {"  |  "}P3 {P3_ENGINE_LABEL[cost.p3_engine ?? ""] ?? cost.p3_engine ?? "?"} ¥{cost.p3}
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
          {ENGINE_ORDER.filter((k) => engines[k]).map((k) => (
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
            {" · "}{L(`满 ${maxQueuedHours} 小时判失败并返还预扣`, `Fails at ${maxQueuedHours}h; hold released`)}
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

// ── P3 运行时配置（并发 / 机器数 / 强制引擎）──────────────────────────────────
// 为什么这几个数值得做成界面：它们原本是 env，且**生效在两个不同的地方**——机器数在
// Render 派单时读、并发在 Fly 机器上各自读，改 Render env 对后者无效（很容易改错地方
// 还以为生效了）。挪进 DB 后两侧读同一处，顺带修掉那个坑。
//
// 界面上最要紧的一件事：**真正撞墙的是乘积不是单个数**。每台机器内部还开 perMachineConc
// 路，所以「8 台」的真实含义是 400 路。所以每个输入框旁边都实时显示占账号上限几成——
// 撞墙不会报错，只表现为退避重试导致的莫名变慢，等出了事再查非常难。
//
// **拆成 engine / capacity 两半**（2026-08-14）：机器数上限管的是全站在飞机器（转录机与后处理机
// 共用这一个数），不是融合这一环的参数。它此前和「P3 该用哪个引擎」挤在一张卡里，会让人以为
// 调它只影响融合。所以引擎那半留在工作流的 P3 节点，容量那半搬去资源。
// 两个 part 各自读一次配置、各自维护草稿——它们编辑的字段不相交，不会互相覆盖。
function P3ConfigSection({ part }: { part: "engine" | "capacity" }) {
  const L = useL();
  const [d, setD] = useState<AdminP3Config | null>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [hours, setHours] = useState(4);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setD(await getAdminP3Config()); } catch { /* 读不到就保持上一份，别把卡清空 */ }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const cfg = d?.config;
  const val = (k: string, fallback: number) => draft[k] ?? fallback;
  const forced = cfg?.force_engine ?? null;

  const save = async (body: Parameters<typeof saveAdminP3Config>[0], okMsg: string) => {
    setBusy(true); setMsg(null);
    try {
      await saveAdminP3Config(body);
      setDraft({});
      await load();
      setMsg(okMsg);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : L("保存失败", "Could not save"));
    } finally { setBusy(false); }
  };

  // 并发数输入框 + 「这么设会占账号上限几成」的实时提示
  const numRow = (key: string, apiKey: string, label: string, cur: number, engine?: string) => {
    const v = val(key, cur);
    const per = d?.perMachineConc ?? 50;
    const cap = engine ? d?.limits[engine] : undefined;
    const used = engine === "claude" ? v : v * per;
    const ratio = cap ? used / cap : 0;
    const over = ratio > 1;
    return (
      <div style={{ display: "flex", alignItems: "center", gap: space.s3 }}>
        <div style={{ ...type.label, width: 150 }}>{label}</div>
        <input type="number" min={1} value={v} disabled={busy}
               onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })}
               style={{ ...cardInput, width: 72 }} />
        {cap && (
          // ⚠️ 只有**超出**才着警示色。此前把 >80% 也标橙，结果 Claude 那档 5/5=100% 长期亮着——
          // 可它满额恰恰是正常状态（5 就是订阅墙本身，配成 5 天经地义），常亮的警示等于没有警示。
          <div style={{ ...mono(11), color: over ? semantic.danger.text : semantic.text.muted }}>
            {engine === "claude"
              ? L(`${used} / ${cap} 并发`, `${used} / ${cap} concurrent`)
              : L(`${v} 台 × ${per} 路 = ${used} / ${cap}`, `${v} × ${per} = ${used} / ${cap}`)}
            {" "}({Math.round(ratio * 100)}%)
            {over && L(" · 超出账号上限", " · over account cap")}
          </div>
        )}
        {draft[key] !== undefined && draft[key] !== cur && (
          <button className="tx-focus" disabled={busy} style={btnStyle(false)}
                  onClick={() => void save({ [apiKey]: draft[key] } as never, L("已保存", "Saved"))}>
            {L("保存", "Save")}
          </button>
        )}
      </div>
    );
  };

  if (part === "capacity") {
    return (
      <SectionShell title={L("在飞机器容量", "Machine capacity")}>
        {!d ? empty(L("读取中…", "Loading…")) : (
          <div style={{ padding: space.s5, display: "flex", flexDirection: "column", gap: space.s4 }}>
            {numRow("fm", "flyMaxMachines", L("常态机器数上限", "Machine cap"), cfg!.fly_max_machines, undefined)}
            <div style={{ ...mono(11), color: semantic.text.muted, lineHeight: 1.7 }}>
              {L("这是全站在飞机器的上限，转录机与后处理机共用——不是融合那一环的参数。"
               + "50 是 Fly 每组织的默认配额上限，再高要发邮件申请。机器跑完自毁，闲时零台、按秒计费。",
                "Global cap on in-flight machines, shared by transcription and post-processing. 50 is Fly's default org quota.")}
            </div>
            {/* 挂 flash 档：每台转录机跑 P3 时内部还开 N 路，撞的是 DeepSeek 账号并发——
                显示的是**乘积**，因为真正撞墙的是乘积不是台数本身 */}
            {numRow("tj", "maxTranscribeJobs", L("转录任务数上限", "Transcription cap"), cfg!.max_transcribe_jobs, "flash")}
            <div style={{ ...mono(11), color: semantic.text.muted, lineHeight: 1.7 }}>
              {L("同时在跑的转录单上限，也是全系统真正的吞吐天花板。它与上面那个数的差额，就是留给后处理的位置"
               + "（后处理排在转录后面领名额，设成一样大的话，转录忙起来加工要等很久）。"
               + "排不上的单留在队列里，下一轮自动补派，不失败。",
                "Cap on concurrent transcription jobs — the real throughput ceiling. The gap to the machine cap is what's left for post-processing.")}
            </div>
            <div style={{ ...mono(11), color: semantic.text.muted, display: "flex", gap: space.s4, flexWrap: "wrap" }}>
              <span>{L(`当前生效机器上限 ${d.effectiveMaxMachines} 台`, `Effective cap ${d.effectiveMaxMachines}`)}</span>
              {cfg?.force_engine && <span style={{ color: semantic.warning.text }}>
                {L(`（正在强制 ${cfg.force_engine}，机器数被压到该档的值）`, `(forced ${cfg.force_engine} caps machines)`)}
              </span>}
            </div>
            {msg && <div style={{ ...mono(11), color: msg.includes("已") || msg.includes("Saved") ? semantic.success.text : semantic.danger.text }}>{msg}</div>}
          </div>
        )}
      </SectionShell>
    );
  }

  return (
    <SectionShell title={L("融合引擎配置", "Merge engine settings")}>
      {!d ? empty(L("读取中…", "Loading…")) : (
        <div style={{ padding: space.s5, display: "flex", flexDirection: "column", gap: space.s4 }}>
          {/* 强制引擎：测试态，所以做成警示色而不是普通开关，并且一定带到期时间 */}
          <div style={{
            border: `1px solid ${forced ? semantic.danger.text : semantic.border.default}`,
            background: forced ? semantic.accent.bgTint : "transparent",
            borderRadius: radius.sm, padding: `${space.s3}px ${space.s4}px`,
            display: "flex", flexDirection: "column", gap: space.s3,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: space.s3, flexWrap: "wrap" }}>
              <div style={{ ...type.label, width: 150 }}>{L("强制引擎（测试用）", "Force engine (testing)")}</div>
              {/* 只剩 flash 一档：Pro 2026-08-26 已摘除，claude 本就是第一档、强制它只会让任务排队 */}
              {(["flash"] as const).map((e) => (
                <button key={e} className="tx-focus" disabled={busy}
                        onClick={() => void save({ forceEngine: forced === e ? null : e, forceHours: hours },
                          forced === e ? L("已恢复正常阶梯", "Back to normal ladder")
                                       : L(`已强制 ${e}`, `Forced ${e}`))}
                        style={btnStyle(forced !== e)}>
                  {forced === e ? L(`✓ ${e}（点此取消）`, `✓ ${e} (click to clear)`) : e}
                </button>
              ))}
              {!forced && (
                <label style={{ ...mono(11), color: semantic.text.muted, display: "flex", alignItems: "center", gap: 4 }}>
                  {L("时长", "For")}
                  <select value={hours} onChange={(e) => setHours(Number(e.target.value))}
                          style={{ ...cardInput, width: 72 }}>
                    <option value={1}>1h</option><option value={4}>4h</option><option value={12}>12h</option>
                  </select>
                </label>
              )}
            </div>
            {forced ? (
              <div style={{ ...mono(11), color: semantic.danger.text, lineHeight: 1.7 }}>
                <div>{L(`所有新任务都只走 ${forced}，不降级；机器数自动压到 ${d.forceMachines[forced]} 台`
                       + `（${d.forceMachines[forced]} × ${d.perMachineConc} = ${d.forceMachines[forced] * d.perMachineConc} 路，`
                       + `占该账号上限 ${Math.round(d.forceMachines[forced] * d.perMachineConc / d.limits[forced] * 100)}%）。`,
                  `All new jobs use ${forced} only. Machines capped at ${d.forceMachines[forced]}.`)}</div>
                <div>{L(`到期自动恢复：${cfg?.forceExpiresAt ? new Date(cfg.forceExpiresAt).toLocaleString() : "—"}`
                       + "（已派出的机器不受影响，跑完为止）",
                  `Auto-clears at ${cfg?.forceExpiresAt ? new Date(cfg.forceExpiresAt).toLocaleString() : "—"}`)}</div>
                <div style={{ color: semantic.text.secondary }}>
                  {L("⚠️ 这会影响所有真实订单，不只是你的测试单。", "⚠️ This affects all live jobs, not just your test.")}
                </div>
              </div>
            ) : (
              <div style={{ ...mono(11), color: semantic.text.muted, lineHeight: 1.7 }}>
                {L("空 = 正常阶梯：Claude → Flash。强制某档可以在低负载时测到平时压不到的引擎；"
                 + "到点自动恢复，不必记着关。不提供强制 Claude——它本就是第一档，强制只会让任务挤在订阅墙上排队。",
                  "Empty = normal ladder: Claude → Flash. Forcing a tier lets you exercise engines you can't otherwise reach.")}
              </div>
            )}
          </div>

          {numRow("cc", "claudeConcurrency", L("Claude 并发", "Claude concurrency"), cfg!.claude_concurrency, "claude")}
          {/* ⚠️ 这个数**不是吞吐天花板**：拿不到名额是当场降级 Flash，不是排队等。
              说清楚是因为它长得最像天花板，2026-08-26 盘点时就把它当成过一次。 */}
          <div style={{ ...mono(11), color: semantic.text.muted, lineHeight: 1.7 }}>
            {L("拿不到名额的任务会当场降级 Flash 继续跑，不排队——所以这个数决定的是「多少单能用上 Claude」，"
             + "不是「多少单能跑」。真正的吞吐天花板是「资源 · 在飞机器容量」里的转录任务数。",
              "Jobs that can't get a slot fall back to Flash immediately — this caps quality tier, not throughput.")}
          </div>

          <div style={{ ...mono(11), color: semantic.text.muted, display: "flex", gap: space.s4, flexWrap: "wrap" }}>
            <span>{L("机器数上限已挪到「资源 · 在飞机器容量」——它管的是全站，不只是融合。",
                     "The machine cap now lives under Resources — it's global, not merge-specific.")}</span>
            {cfg?.updated_by && <span>{L(`最后修改 ${cfg.updated_by}`, `By ${cfg.updated_by}`)}
              {cfg.updatedAt ? ` · ${new Date(cfg.updatedAt).toLocaleString()}` : ""}</span>}
          </div>
          {msg && <div style={{ ...mono(11), color: msg.includes("已") || msg.includes("Saved") ? semantic.success.text : semantic.danger.text }}>{msg}</div>}
        </div>
      )}
    </SectionShell>
  );
}

// ── P3 引擎（Claude 无头模式）健康度 ────────────────────────────────────────────
// 订阅制没有「余额」可查：令牌模式下调用不吐额度事件，拿不到还剩百分之几。所以这张卡是
// **成败统计**——近 N 小时真实调用里成功几次、上次撞顶何时、此刻是否还在撞顶冷却中。
// 代价是没任务跑的时段数字不动，那时用「探活」按钮起一台机器真跑一次（几十秒出结果）。

// 降级明细的分组：前三种是引擎本身出状况，后两种是我方限流（不代表订阅不健康）
const OUTCOMES: { key: string; zh: string; en: string; ours: boolean }[] = [
  { key: "capped", zh: "撞顶", en: "Capped", ours: false },
  { key: "auth", zh: "认证失效", en: "Auth failed", ours: false },
  { key: "error", zh: "报错", en: "Errors", ours: false },
  { key: "timeout", zh: "超时", en: "Timeouts", ours: false },
  { key: "preempt", zh: "冷却跳过", en: "Skipped", ours: true },
  { key: "concurrency", zh: "并发让路", en: "Queued out", ours: true },
];
// 「最近一次」那格要显示人话，不是库里的枚举值（ok/preempt 这种只有写代码的人看得懂）
const outcomeLabel = (key: string, L: ReturnType<typeof useL>) => {
  if (key === "ok") return L("成功", "OK");
  const o = OUTCOMES.find((x) => x.key === key);
  return o ? L(o.zh, o.en) : key;
};

const PROBE_WAIT_MS = 150_000;   // 机器 boot + 一次调用；超过这个还没回报就是机器没起来

function P3StatusLine({ h }: { h: AdminP3Health }) {
  const L = useL();
  let rate = h.successRate;
  let tone: string = semantic.text.muted;
  let text = L(`近 ${h.windowHours} 小时无调用样本`, `No calls in the last ${h.windowHours}h`);
  // 认证失效排在撞顶前面：撞顶等着就好，这个不动手永远不会好
  if (h.authFailing) {
    tone = semantic.danger.text;
    text = L("认证失效 · 需要人工处理", "Auth failed · needs you");
  } else if (h.capped) {
    tone = semantic.danger.text;
    text = h.capped.reason === "cap_week"
      ? L(`周额度撞顶 · 约 ${fmtAt(h.capped.until)} 后重试`, `Weekly cap hit · retrying after ${fmtAt(h.capped.until)}`)
      : L(`额度撞顶 · 约 ${fmtAt(h.capped.until)} 后重试`, `Rate cap hit · retrying after ${fmtAt(h.capped.until)}`);
  } else if (h.lastAttempt === "ok") {
    // 最近一次真打到引擎就是成功的 = 此刻是好的。**不拿窗口成功率定状态**：那是滚动平均，
    // 故障修好之后还会把面板按在「异常」上好几个小时，运营以为没修好（2026-08-07 实测）。
    // 窗口里的失败不隐瞒，降级成副文本（下面那句 N/M 次成功）继续显示。
    tone = semantic.success.text;
    text = rate != null && rate < 0.95 ? L("已恢复", "Recovered") : L("正常", "Healthy");
  } else if (h.lastAttempt) {
    tone = semantic.danger.text;
    text = L(`异常 · 最近一次${outcomeLabel(h.lastAttempt, L)}`, `Failing · last call ${outcomeLabel(h.lastAttempt, L)}`);
  }
  if (h.authFailing) rate = null;   // 认证失效时不并排显示成功率：读数会自相矛盾
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: space.s3 }}>
      <span aria-hidden style={{ color: tone, fontSize: 20, lineHeight: 1 }}>●</span>
      <span style={{ ...type.h2, margin: 0, color: tone }}>{text}</span>
      {rate != null && !h.capped && (
        <span style={{ ...mono(12), color: semantic.text.secondary }}>
          {L(`近 ${h.windowHours} 小时 ${h.counts.ok ?? 0}/${h.attempted} 次成功`,
            `${h.counts.ok ?? 0}/${h.attempted} succeeded in ${h.windowHours}h`)}
        </span>
      )}
    </div>
  );
}

function P3HealthSection() {
  const L = useL();
  const [h, setH] = useState<AdminP3Health | null>(null);
  const [err, setErr] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeMsg, setProbeMsg] = useState<string | null>(null);
  const probeId = useRef<string>("");
  const probeStart = useRef(0);

  const load = useCallback(async () => {
    try {
      const d = await getAdminP3Health();
      setH(d);
      setErr(false);
      return d;
    } catch {
      setErr(true);
      return null;
    }
  }, []);

  // 探活结果的话术：把「没打到 Claude」和「打到了但不通」分清楚——前者不是订阅的问题
  const probeResult = useCallback((last: NonNullable<AdminP3Health["last"]>) => {
    if (last.outcome === "ok") return L("探活通过：此刻可用", "Probe passed — engine is up");
    if (last.outcome === "concurrency") return L("并发已满，没打到引擎（转录正忙），稍后再探", "All slots busy — probe never reached the engine; try later");
    if (last.outcome === "capped") return L("探活撞顶：现在一律走备用引擎", "Probe hit the cap — falling back for now");
    if (last.outcome === "timeout") return L("探活超时：调用迟迟不返回", "Probe timed out — no response");
    return L(`探活失败：${last.note || "未知原因"}`, `Probe failed: ${last.note || "unknown"}`);
  }, [L]);

  const tick = useCallback(async () => {
    const d = await load();
    if (!probing) return;
    // 用 probeId 认领自己那次结果：比时刻要跨两个时钟，差几秒就会错认
    if (d?.last && d.last.jobId === probeId.current) {
      setProbing(false);
      setProbeMsg(probeResult(d.last));
      return;
    }
    if (Date.now() - probeStart.current > PROBE_WAIT_MS) {
      setProbing(false);
      setProbeMsg(L("探活机器迟迟没有回报，去 Fly 看看它起没起来", "Probe machine never reported back — check Fly"));
    }
  }, [load, probing, probeResult, L]);

  useEffect(() => { void load(); }, [load]);

  // 平时 30s 刷一次（数字只随真实任务变，刷更勤没有意义）；探活期间 4s 一刷等结果
  useEffect(() => {
    const t = setInterval(() => { void tick(); }, probing ? 4000 : 30000);
    return () => clearInterval(t);
  }, [probing, tick]);

  const startProbe = async () => {
    setProbeMsg(null);
    try {
      const { probeId: id } = await probeP3();
      probeId.current = id;
      probeStart.current = Date.now();
      setProbing(true);
    } catch (e) {
      setProbeMsg(e instanceof Error ? e.message : L("发起探活失败", "Could not start the probe"));
    }
  };

  const probeBtn = (
    <button className="tx-focus" disabled={probing} onClick={() => void startProbe()} style={btnStyle(true)}>
      {probing ? L("探活中…", "Probing…") : L("探活", "Probe")}
    </button>
  );

  const cell = (label: string, value: string, tone?: string) => (
    <div>
      <div style={{ ...type.label, marginBottom: 2 }}>{label}</div>
      <div style={{ ...mono(12), color: tone ?? semantic.text.secondary }}>{value}</div>
    </div>
  );

  // 标题不叫「转录引擎」：那是余额区 ASR 那一组的名字（会撞），且 P3 干的是融合精修不是转录
  return (
    <SectionShell title={L("融合引擎健康度", "Merge engine health")} action={probeBtn}>
      {err && !h ? empty(L("读取失败 · 重试中", "Could not load · retrying")) : !h ? empty(L("读取中…", "Loading…")) : (
        <div style={{ padding: space.s5, display: "flex", flexDirection: "column", gap: space.s4 }}>
          <P3StatusLine h={h} />

          {/* 认证失效是唯一「等下去不会自己好」的状态，所以这里给的是动作不是描述 */}
          {h.authFailing && (
            <div style={{ ...mono(12), color: semantic.danger.text, background: semantic.accent.bgTint, border: `1px solid ${semantic.danger.text}`, borderRadius: radius.sm, padding: `${space.s3}px ${space.s4}px`, lineHeight: 1.7 }}>
              <div>{L("转录没有中断（自动改走备用引擎），但融合质量会下降。恢复步骤：",
                "Transcription is not down (it fell back automatically), but merge quality drops. To fix:")}</div>
              <div>{L("① 确认订阅仍在有效期 → ② 本机跑 claude setup-token 生成新令牌 → ③ fly secrets set CLAUDE_CODE_OAUTH_TOKEN=… -a transcribe-task",
                "1) confirm the subscription is active → 2) run `claude setup-token` locally → 3) `fly secrets set CLAUDE_CODE_OAUTH_TOKEN=… -a transcribe-task`")}</div>
              <div style={{ color: semantic.text.secondary }}>{L("新起的机器自动取新值，不用重新部署镜像；修好后下一单成功，这条会自己消失。",
                "New machines pick it up automatically — no redeploy. This banner clears itself once a call succeeds.")}</div>
              {h.authNote && (
                <div style={{ marginTop: space.s2, color: semantic.text.secondary, wordBreak: "break-all" }}>
                  {L("引擎原话：", "Engine said: ")}{h.authNote}
                </div>
              )}
            </div>
          )}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: space.s4 }}>
            {cell(L("在飞并发", "In flight"),
              h.activeSlots == null ? "—" : `${h.activeSlots} / ${h.slotLimit}`)}
            {cell(L("上次成功", "Last success"), fmtAt(h.lastOkAt))}
            {cell(L("上次撞顶", "Last cap"), fmtAt(h.lastCappedAt),
              h.lastCappedAt ? semantic.text.secondary : semantic.text.muted)}
            {/* 带上来源：探活是我们自己戳的，与真实任务混在一起看会读错 */}
            {cell(L("最近一次", "Latest call"),
              h.last ? `${fmtAt(h.last.at)} · ${outcomeLabel(h.last.outcome, L)}${h.last.source === "probe" ? L("（探活）", " (probe)") : ""}` : "—")}
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: space.s4, paddingTop: space.s3, borderTop: `1px solid ${semantic.border.subtle}` }}>
            {OUTCOMES.map((o) => (
              <span key={o.key} style={{ ...mono(11), color: (h.counts[o.key] ?? 0) === 0 ? semantic.text.muted : o.ours ? semantic.text.secondary : semantic.warning.text }}>
                {L(o.zh, o.en)} {h.counts[o.key] ?? 0}
              </span>
            ))}
          </div>

          {probeMsg && (
            <div style={{ ...mono(12), color: semantic.text.secondary, background: semantic.surface.page, padding: `${space.s3}px ${space.s4}px`, borderRadius: radius.sm }}>
              {probeMsg}
            </div>
          )}

          <p style={{ ...mono(11), color: semantic.text.secondary, margin: 0, lineHeight: 1.5 }}>
            {L("数字来自真实转录调用，没有任务跑的时段不会变化；要看此刻通不通，点右上角「探活」跑一次真实调用（约一分钟出结果）。",
              "These counts come from real transcription calls, so they stay put when nothing is running. To check right now, hit Probe — it runs one real call (about a minute).")}
          </p>
        </div>
      )}
    </SectionShell>
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

// ── 充值退款（条款第 4 条：14 天内、只退未消耗部分）─────────────────────────────
// 可退额一律由后端算：同一套规则前后端各算一遍必然漂移，而这里错一分钱就是真金白银。
// 发起只是「调 provider 退款 API + 占住额度」，余额要等 provider 的退款 webhook 回来才扣，
// 所以这里退成功后不刷新余额数字——刷了也还是旧值，反而让人以为没生效。
const TOPUP_COLS = "1.1fr 1fr 1fr 1fr 1.4fr";

function RefundRow({ t, email, onDone }: { t: AdminTopup; email: string; onDone: () => void }) {
  const L = useL();
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const refundable = t.refundableCents;
  const dollars = (cents: number) => usd(cents / 100);   // usd() 收美元数；账本一律以分记，展示前换算

  const submit = async () => {
    const cents = Math.round(parseFloat(amount || "0") * 100);
    if (!(cents > 0) || cents > refundable) {
      setMsg(L(`请输入 0 到 ${dollars(refundable)} 之间的金额`, `Enter an amount between 0 and ${dollars(refundable)}`));
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await startAdminRefund(email, t.id, cents);
      setMsg(L("已向支付渠道发起，余额待回执到账后扣减", "Sent to the payment provider; balance updates on callback"));
      setAmount("");
      onDone();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : L("发起失败", "Failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: TOPUP_COLS, gap: space.s4, padding: `${space.s3}px ${space.s5}px`, borderTop: `1px solid ${semantic.border.subtle}`, alignItems: "center" }}>
      <div style={mono(12)}>{dollars(t.amountCents)}</div>
      <div style={{ ...mono(11), color: semantic.text.muted }}>
        {t.isBonus ? L("赠送", "Bonus") : (t.source || "—")}
      </div>
      <div style={{ ...mono(11), color: t.ageDays > 14 ? semantic.text.muted : semantic.text.secondary }}>
        {L(`${t.ageDays} 天前`, `${t.ageDays}d ago`)}
      </div>
      <div style={{ ...mono(12), color: refundable > 0 ? semantic.success.text : semantic.text.muted }}>
        {dollars(refundable)}
        {t.refundedCents > 0 && (
          <span style={{ ...mono(10), color: semantic.text.muted }}> · {L("已退", "refunded")} {dollars(t.refundedCents)}</span>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {refundable > 0 ? (
          <div style={{ display: "flex", gap: space.s2, alignItems: "center" }}>
            <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={dollars(refundable).replace("$", "")}
              aria-label={L("退款金额", "Refund amount")} style={{ ...cardInput, width: 72 }} />
            <button className="tx-focus" disabled={busy} onClick={submit} style={btnStyle(false)}>
              {busy ? L("发起中…", "Sending…") : L("退款", "Refund")}
            </button>
          </div>
        ) : (
          <span style={{ ...mono(11), color: semantic.text.muted }}>
            {t.isBonus ? L("赠送额度，不退现金", "Bonus credit — no cash back")
              : t.ageDays > 14 ? L("超出 14 天窗口", "Past the 14-day window")
                : L("无可退余额", "Nothing left to refund")}
          </span>
        )}
        {msg && <span style={{ ...mono(10), color: semantic.text.secondary }}>{msg}</span>}
      </div>
    </div>
  );
}

function RefundsSection() {
  const L = useL();
  const [email, setEmail] = useState("");
  const [data, setData] = useState<{ topups: AdminTopup[]; balanceCents: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const loadedFor = useRef("");     // 当前这份 data 是谁的

  const load = useCallback(async (target: string) => {
    if (!target.trim()) return;
    setBusy(true);
    setErr(null);
    // 换了查的人先清空——不清就会把上一个人的充值记录挂在新邮箱下面显示。
    // **只在换人时清**：退款成功后也会走这里刷新，那时清空会把「已发起」那行提示一并抹掉。
    if (loadedFor.current !== target.trim()) setData(null);
    loadedFor.current = target.trim();
    try {
      setData(await getAdminTopups(target.trim()));
    } catch {
      setData(null);
      setErr(L("查不到该用户的充值记录", "No top-ups found for that user"));
    } finally {
      setBusy(false);
    }
  }, [L]);

  return (
    <SectionShell title={L("充值退款", "Top-up refunds")}>
      <div style={{ padding: space.s4, display: "flex", gap: space.s3, alignItems: "center" }}>
        <input value={email} onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void load(email); }}
          placeholder={L("用户邮箱", "User email")} aria-label={L("用户邮箱", "User email")}
          style={{ ...cardInput, width: 260 }} />
        <button className="tx-focus" disabled={busy} onClick={() => void load(email)} style={btnStyle(true)}>
          {busy ? L("查询中…", "Loading…") : L("查询", "Look up")}
        </button>
        {data && (
          <span style={{ ...mono(11), color: semantic.text.muted }}>
            {L("当前余额", "Balance")} {usd(data.balanceCents / 100)}
          </span>
        )}
      </div>
      {err && <div style={{ ...mono(11), color: semantic.danger.text, padding: `0 ${space.s5}px ${space.s4}px` }}>{err}</div>}
      {data && (data.topups.length === 0
        ? empty(L("该用户没有充值记录", "No top-ups for this user"))
        : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: TOPUP_COLS, gap: space.s4, padding: `${space.s3}px ${space.s5}px`, borderTop: `1px solid ${semantic.border.default}`, background: semantic.surface.page, ...type.label }}>
              <div>{L("充值额", "Amount")}</div>
              <div>{L("来源", "Source")}</div>
              <div>{L("时间", "When")}</div>
              <div>{L("可退", "Refundable")}</div>
              <div>{L("操作", "Action")}</div>
            </div>
            {data.topups.map((t) => (
              <RefundRow key={t.id} t={t} email={email.trim()} onDone={() => void load(email)} />
            ))}
            <div style={{ ...mono(10), color: semantic.text.muted, padding: `${space.s3}px ${space.s5}px` }}>
              {L("各行可退额都受同一份可用余额约束，不能相加；退掉一笔，其余各笔会随之下降。",
                 "Each row's refundable amount is capped by the same available balance — they don't add up; refunding one lowers the rest.")}
            </div>
          </>
        ))}
    </SectionShell>
  );
}


// 后处理板块列宽：文件 / 用户 / 步骤 / 状态 / 价 / 时间
const PP_COLS = "minmax(0,2.2fr) minmax(0,1.2fr) minmax(0,1.6fr) minmax(0,1fr) 5em 5em 6em";

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
            <div style={{ textAlign: "right" }}>{L("价", "Price")}</div>
            {/* 降级路烧掉的 DeepSeek 钱（¥）。只有降过级的单才有值——常态走 Claude 订阅，不按单计费。 */}
            <div style={{ textAlign: "right" }}>{L("降级成本", "DS cost")}</div>
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
                <div style={{ ...mono(11), textAlign: "right", color: semantic.text.secondary }}>{r.priceCents > 0 ? usd(r.priceCents / 100) : "—"}</div>
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

// ── 会到期的凭证 ──────────────────────────────────────────────────────────────
// 这两把钥匙到期都是**静默失败**（充值没反应 / 融合全线降级），不是报错。在此之前它们只写在
// docs/infra-status.md 里，而文档不会在到期前一个月自己翻开。唯一来源见 server/app/expiries.py。
function ExpiriesSection() {
  const L = useL();
  const [d, setD] = useState<{ items: AdminExpiry[]; warnDays: number } | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { getAdminExpiries().then(setD).catch(() => setErr(true)); }, []);
  return (
    <SectionShell title={L("会到期的凭证", "Expiring credentials")}>
      {err ? empty(L("读不到到期清单", "Could not load"))
        : !d ? empty(L("读取中…", "Loading…"))
          : d.items.map((c, i) => (
            <div key={c.id} style={{ padding: `${space.s4}px ${space.s5}px`, borderBottom: i === d.items.length - 1 ? "none" : `1px solid ${semantic.border.subtle}` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: space.s4 }}>
                <span style={{ fontSize: 14, fontWeight: 600, color: semantic.text.primary }}>{c.vendor} · <span style={mono(12) as React.CSSProperties}>{c.what}</span></span>
                <span style={{ ...mono(12), color: c.warn ? semantic.danger.text : semantic.text.secondary, whiteSpace: "nowrap" }}>
                  {c.daysLeft == null ? L("日期有误", "bad date") : L(`还有 ${c.daysLeft} 天 · ${c.expires}`, `${c.daysLeft}d left · ${c.expires}`)}
                </span>
              </div>
              <div style={{ marginTop: 5, ...mono(11), color: semantic.text.muted, lineHeight: 1.7 }}>
                <div>{L("到期后：", "On expiry: ")}{c.impact}</div>
                <div>{L("怎么换：", "Fix: ")}{c.fix}</div>
              </div>
            </div>
          ))}
      {d && (
        <div style={{ padding: `${space.s3}px ${space.s5}px`, borderTop: `1px solid ${semantic.border.default}`, ...mono(11), color: semantic.text.muted, lineHeight: 1.7 }}>
          {L(`两家都查不到到期日（Fly 只给 digest、Paddle 后台不暴露），所以这份清单靠手工维护——`
           + `换完钥匙记得改 server/app/expiries.py，只此一处。还剩 ${d.warnDays} 天时进待办条。`,
            `Neither vendor exposes an expiry date, so this list is maintained by hand in server/app/expiries.py.`)}
        </div>
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
  // 「现在发一份增长周报」：验收不用等周一。发完落在这张列表里（fyi 档），所以发完就刷新
  const [reportState, setReportState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const sendReport = async () => {
    setReportState("sending");
    try { await sendGrowthReport(); setReportState("sent"); load(); } catch { setReportState("failed"); }
  };

  const tierLabel = (t: string) => (
    t === "act" ? L("要动手", "Act") : t === "watch" ? L("要留意", "Watch") : L("知会", "FYI"));

  return (
    <SectionShell
      title={L("告警", "Alerts")}
      action={
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button className="tx-focus" disabled={reportState === "sending"} onClick={sendReport} style={btnStyle(true)}>
            {reportState === "sending" ? L("发送中…", "Sending…")
              : reportState === "sent" ? L("周报已发到邮箱", "Report sent")
                : reportState === "failed" ? L("周报发送失败", "Report failed")
                  : L("发一份增长周报", "Send growth report")}
          </button>
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
// 这一组不属于任何单个节点，但它们说明「整套东西现在健不健康」，而此前一个都没地方看：
// 发码用量（撞上限=登不进，最严重的故障）、四把闸的在飞数（此前只显示一把）、
// 在飞机器实际台数（此前只显示上限）、看门狗回收数、近 60 分钟成败。
function OpsSection() {
  const L = useL();
  const [d, setD] = useState<AdminHealth | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    const load = () => { getAdminHealth(24).then(setD).catch(() => setErr(true)); };
    load();
    const t = setInterval(load, 30_000);   // 机器数那一项后端带 30s 缓存，对齐轮询周期
    return () => clearInterval(t);
  }, []);

  const row = (label: string, value: React.ReactNode, hint?: string) => (
    <div style={{ display: "flex", alignItems: "baseline", gap: space.s4, padding: `${space.s3}px 0`, borderTop: `1px solid ${semantic.border.subtle}` }}>
      <div style={{ ...type.label, width: 160, flex: "0 0 auto" }}>{label}</div>
      <div style={{ ...mono(12), color: semantic.text.primary }}>{value}</div>
      {hint && <div style={{ ...mono(11), color: semantic.text.muted }}>{hint}</div>}
    </div>
  );
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
              {/* 撞上供应商免费额度 = 用户登不进。这是最严重的故障，而它此前零预警。 */}
              {row(L("登录验证码 · 近 24h", "Login codes · 24h"),
                   <span style={{ color: (d.ops.loginSends24h ?? 0) >= d.ops.loginSendCapHint * 0.8 ? semantic.danger.text : undefined }}>
                     {n(d.ops.loginSends24h)} / {d.ops.loginSendCapHint}
                   </span>,
                   L("供应商免费档上限；撞上限后新用户和登出的老用户都进不来", "Vendor free-tier cap"))}
              {row(L("在飞机器", "Machines in flight"), n(d.ops.machinesRunning),
                   L("跑完自毁，闲时零台", "Auto-destroyed when done"))}
              {row(L("并发闸在飞", "Gate slots in use"),
                   Object.entries(d.ops.gateSlots).map(([k, v]) => `${k} ${n(v)}/${d.ops.gateLimit}`).join("　·　"),
                   L("闸满的直接后果是「用户在等」", "A full gate means someone is waiting"))}
              {row(L("看门狗回收 · 近 24h", "Watchdog requeues · 24h"), n(d.ops.watchdogRequeues),
                   L("重跑往往成功，所以这不会体现在成功率上", "Requeues usually succeed, so this never shows up in the success rate"))}
              {row(L("近 60 分钟", "Last 60 min"),
                   `${n(d.ops.recent60.done)} ${L("完成", "done")}　${n(d.ops.recent60.failed)} ${L("失败", "failed")}`)}
              {/* 月度三条：24h 窗样本太小（一天十几单，重跑一单就是 8%），趋势只有月度看得出来 */}
              {d.monthly && <>
                {row(L(`重跑比例 · 近 ${d.monthly.days} 天`, `Retried · ${d.monthly.days}d`),
                     pct(d.monthly.retry.ratio, `${d.monthly.retry.retried}/${d.monthly.retry.total}`),
                     L("跑了不止一次的单占比", "Share of jobs that ran more than once"))}
                {row(L(`P3 降级比例 · 近 ${d.monthly.days} 天`, `P3 fell back · ${d.monthly.days}d`),
                     pct(d.monthly.p3.degradedRatio, `${d.monthly.p3.known} ${L("单", "jobs")}`),
                     L("没用上 Claude、由 DeepSeek 出稿的占比", "Share of drafts produced by DeepSeek instead of Claude"))}
                {row(L(`P3 缓存命中率 · 近 ${d.monthly.days} 天`, `P3 cache hits · ${d.monthly.days}d`),
                     d.monthly.p3Cache.jobs === 0
                       ? <span style={{ color: semantic.text.ghost }}>{L("暂无样本", "No samples")}</span>
                       : pct(d.monthly.p3Cache.hitRatio, `${d.monthly.p3Cache.jobs} ${L("单", "jobs")}`),
                     L("命中与未命中的单价差 20–30 倍，这是 P3 成本的主变量",
                       "Cached vs. uncached input differs 20–30× in price — the main driver of P3 cost"))}
              </>}
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
// 自己取数（余额/到期/健康度各一个接口）而不是从 AdminPage 传下来：它要在四个 Tag 上都在，
// 而那几个板块各自只在一个 Tag 里。多两个 GET 换组件互不牵扯，在内部运营页上是划算的。
const QUEUE_WARN_RATIO = 0.25;   // 排到判死窗口的 1/4 就提醒——还剩 3/4 的时间够动手
const SAME_CAUSE_MIN = 3;        // 同一原因失败几次算「一个问题」而不是「几次意外」

type Todo = { id: string; tone: "danger" | "warn"; text: string; where: string };

function AlertStrip({ data }: { data: AdminOverview | null }) {
  const L = useL();
  const [bal, setBal] = useState<AdminBalance[] | null>(null);
  const [exp, setExp] = useState<AdminExpiry[] | null>(null);
  const [h, setH] = useState<AdminP3Health | null>(null);
  const [nh, setNh] = useState<AdminHealth | null>(null);
  const [alerts, setAlerts] = useState<AdminAlert[] | null>(null);

  useEffect(() => {
    // **每个来源各自 try/catch，且用 async 包住**：`f().catch()` 只挡得住 reject，挡不住
    // f 本身同步抛（比如取数函数还没初始化好）——那一下会打断整个 effect，让后面两路也不取。
    // 待办条的每一路都是独立信号，一路坏了该少报一类，不该整条哑掉。
    const load = async () => {
      const pull = async <T,>(f: () => Promise<T>, set: (v: T) => void) => {
        try { set(await f()); } catch { /* 少报一类 */ }
      };
      await Promise.all([
        pull(getAdminBalances, setBal),
        pull(getAdminExpiries, (r) => setExp(r.items)),
        pull(getAdminP3Health, setH),
        pull(() => getAdminHealth(24), setNh),
        pull(() => getAdminAlerts(30, true), (r) => setAlerts(r.items)),
      ]);
    };
    void load();
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, []);

  const todos: Todo[] = [];
  // ① 认证失效排第一：撞顶等着就好，这个不动手永远不会好
  if (h?.authFailing) {
    todos.push({ id: "auth", tone: "danger", where: L("工作流 · P3", "Workflow · P3"),
      text: L("融合引擎认证失效 —— 续订或重新生成令牌，不然每单都在静默降级", "Merge engine auth failed — renew the token") });
  }
  for (const c of exp ?? []) {
    if (c.warn) {
      todos.push({ id: `exp-${c.id}`, tone: c.daysLeft != null && c.daysLeft <= 14 ? "danger" : "warn",
        where: L("资源", "Resources"),
        text: L(`${c.what} 还有 ${c.daysLeft} 天到期 —— ${c.impact}`, `${c.what} expires in ${c.daysLeft}d — ${c.impact}`) });
    }
  }
  for (const b of bal ?? []) {
    if (b.low) {
      todos.push({ id: `bal-${b.vendor}`, tone: "warn", where: L("资源", "Resources"),
        text: L(`${b.label ?? b.vendor} 余额 ${b.amountCny} 低于阈值 ${b.thresholdCny}`,
                `${b.label ?? b.vendor} balance ${b.amountCny} below ${b.thresholdCny}`) });
    }
  }
  // 预充值却没设阈值 = 低余额告警对这几家永远不会触发。**这跟余额充足在界面上长得一样**，
  // 但它是「没有保护」而不是「没有风险」。聚成一条：分开报四行只会把待办条淹掉，
  // 而这四家本来就该一次补齐。
  const noThr = (bal ?? []).filter((b) => b.noThreshold);
  if (noThr.length) {
    // 点名前三家就够认出是哪一类；全列会把这一行撑到两三行，整条待办条就不再是「一眼扫完」。
    // 要补的时候本来就得去资源页，那里每张卡上都标着「未设 · 不会告警」。
    const shown = noThr.slice(0, 3).map((b) => b.label ?? b.vendor);
    const names = shown.join("、") + (noThr.length > shown.length ? L("等", " and more") : "");
    todos.push({ id: "bal-nothreshold", tone: "warn", where: L("资源", "Resources"),
      text: L(`${noThr.length} 家预充值服务商没设余额阈值（${names}）—— 余额见底也不会有任何告警`,
              `${noThr.length} prepaid vendor(s) have no balance threshold (${names}) — they can never alert`) });
  }
  // ② 排队时长：在它被判失败之前的那段窗口，是唯一能干预的时间，此前界面上完全看不见
  const maxH = data?.queuedMaxHours ?? 24;
  const stuck = (data?.jobs ?? []).filter((j) => j.status === "queued" && j.elapsedSec > maxH * 3600 * QUEUE_WARN_RATIO);
  if (stuck.length) {
    const worst = Math.max(...stuck.map((j) => j.elapsedSec));
    todos.push({ id: "queue", tone: "warn", where: L("运行", "Live"),
      text: L(`${stuck.length} 单排队已超 ${Math.floor(worst / 3600)} 小时（满 ${maxH} 小时判失败并返还预扣）`,
              `${stuck.length} job(s) queued over ${Math.floor(worst / 3600)}h (failed at ${maxH}h)`) });
  }
  // ③ 某一路引擎悄悄坏了。**这是唯一能提前发现它的信号**——参考轨挂掉是「跳过继续」，
  //   整单照样成功出稿，从任务列表上完全看不出来（讯飞失败两周没人发现就是这个盲区）。
  //   判定用「相对基线掉了多少」而非绝对阈值，且在后端算好（同一套规则算两遍必然漂）。
  for (const e of nh?.alerts.engines ?? []) {
    todos.push({ id: `eng-${e.tag}`, tone: "warn", where: L("工作流 · P1", "Workflow · P1"),
      text: L(`${e.tag} 近 24h 成功率 ${e.pct}%（平时 ${e.basePct}%，${e.ok}/${e.total}）`,
              `${e.tag} at ${e.pct}% in 24h (usually ${e.basePct}%)`) });
  }
  // ④ 降级率：判断「Claude 额度到底够不够」最直接的依据，也是将来要不要升档订阅的唯一凭据。
  //   注意这**不是**「撞顶降级」那条被排除的信号——那条说的是单次撞顶（自愈），
  //   这条说的是「一直在降级」，那要人去决定升档还是调闸，不动手不会好。
  if (nh?.alerts.degrade) {
    todos.push({ id: "degrade", tone: "warn", where: L("工作流 · P3", "Workflow · P3"),
      text: L(`近 24h ${Math.round(nh.alerts.degrade.ratio * 100)}% 的单降级出稿（${nh.alerts.degrade.known} 单可判定）—— 考虑升档订阅或调闸`,
              `${Math.round(nh.alerts.degrade.ratio * 100)}% of jobs fell back in 24h`) });
  }
  // ⑤ 发码撞上供应商免费额度 = 用户登不进，是所有故障里最严重的一种。留两成余量就提醒。
  const sends = nh?.ops.loginSends24h;
  const cap = nh?.ops.loginSendCapHint ?? 100;
  if (sends != null && sends >= cap * 0.8) {
    todos.push({ id: "login", tone: sends >= cap ? "danger" : "warn", where: L("资源", "Resources"),
      text: L(`近 24h 已发出 ${sends}/${cap} 封登录验证码 —— 撞上限后新用户和登出的老用户都进不来`,
              `${sends}/${cap} login codes sent in 24h — at the cap nobody can sign in`) });
  }
  // ⑥ 未处理的「要动手」告警。**只接 act 档**：watch 档要么待办条已经按活状态报了
  //   （余额偏低 = 上面那条 b.low），要么它自己会好——两边都报就是同一件事出现两行，
  //   而待办条的可信度是它唯一的资产。act 档不点「已处理」就永远挂着，这正是它该在这儿的理由。
  for (const a of alerts ?? []) {
    todos.push({ id: `alert-${a.id}`, tone: "danger", where: L("资源 · 告警", "Resources · Alerts"),
      text: a.suppressed > 0
        ? L(`${a.subject}（还触发了 ${a.suppressed} 次）`, `${a.subject} (+${a.suppressed} more)`)
        : a.subject });
  }
  // ⑦ 同因失败聚类：5 单失败是 5 个问题还是同一个炸了 5 次，这个差别决定要不要立刻动手
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
        {L("撞顶降级不进这里 —— 它会自己好。只报不动手就不会好的事。",
           "Rate-cap fallbacks are excluded — they self-heal. Only things that stay broken appear here.")}
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
const TAGS = ["live", "workflow", "resource", "growth", "user"] as const;
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
    growth: L("增长", "Growth"),
    user: L("用户", "Users"),
  };

  return (
    <div className="tx-scroll" style={{ flex: 1, padding: layout.pagePad, overflowY: "auto", minHeight: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: space.s4 }}>
        <h1 style={{ ...type.h1, margin: 0 }}>{L("运营驾驶舱", "Operations")}</h1>
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

      {tag === "workflow" && (
        // P3 是唯一已经有埋点的节点，所以它的⑤⑥两块是真内容而不是占位——健康度与配置直接
        // 塞进 P3 节点的展开面板里，而不是另起两张卡（另起就又回到「同一件事分两处」了）。
        <WorkflowTab p3Extra={<><P3HealthSection /><P3ConfigSection part="engine" /></>} />
      )}

      {tag === "resource" && (
        <>
          <OpsSection />
          <AlertsSection />
          <BalancesSection />
          <ExpiriesSection />
          <P3ConfigSection part="capacity" />
        </>
      )}

      {tag === "growth" && <GrowthTab />}

      {/* 用户 Tag：列表/下钻在上，充值退款在下——退款是「对某一笔充值动手」，
          得先找到人才用得上，所以它在下面而不是上面 */}
      {tag === "user" && (
        <>
          <UsersTab />
          <RefundsSection />
        </>
      )}
    </div>
  );
}

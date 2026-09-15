// 运行面板 · 工作流 Tag：各节点自上而下，每个展开成同一套骨架。
//
// **骨架为什么固定成六块**：节点长得很不一样（有的是本机计算、有的调模型后端），
// 但提问的方式是一样的——干什么 / 怎么干的 / 参数 / 挂了怎么办 / 近况。
// 不适用的那块**明写「无」**，不许省略：空着会让人以为是没做完。
//
// **后端只出事实、前端只出文案**：这里的每个数字都来自 /admin/workflow（后端读实际生效的配置与常量），
// 前端一个都不许写死。写死的那一刻，界面就开始和实际跑的东西分家，而且不会报错。
//
// 本机版（与线上不同）：线上这一页描述云端流水线——主轨加参考轨、vendor 脚本与 SKILL、Claude 订阅闸、
// Fly 任务镜像的指纹口径、27 门语种编排。本机一样都没有，原样搬来一打开就是「读不到工作流配置」。
// 节点改成本机这条：术语库助手 · P0 转码与切块 · P1 四路本机引擎 · 分歧册 · P3 定字 · 后处理两步。
import { useCallback, useEffect, useState } from "react";
import { useL } from "../../lib/i18n";
import { semantic, fonts, type, space, radius } from "../../styles/tokens";
import { getAdminWorkflow, getAdminPrompt, getAdminHealth,
         type AdminWorkflow, type AdminHealth, type NodeStat } from "../../lib/api";
import { mono, caret, SectionShell, empty, btnStyle } from "./ui";

type KV = { k: string; v: React.ReactNode };

const dim = { color: semantic.text.muted };

// ── 提示词全文浮层（只读）──────────────────────────────────────────────────────
// 只读是刻意的：改提示词要走代码评审，界面上给个编辑框只会制造「我改了怎么没生效」。
function PromptViewer({ id, onClose }: { id: string; onClose: () => void }) {
  const L = useL();
  const [d, setD] = useState<{ path: string; sha: string; text: string } | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    getAdminPrompt(id).then((r) => { if (alive) setD(r); }).catch(() => { if (alive) setErr(true); });
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { alive = false; window.removeEventListener("keydown", onKey); };
  }, [id, onClose]);

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.42)", zIndex: 60,
      display: "flex", alignItems: "center", justifyContent: "center", padding: space.s5,
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`,
        borderRadius: radius.md, width: "min(900px, 100%)", maxHeight: "86vh",
        display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: space.s4, padding: `${space.s4}px ${space.s5}px`, borderBottom: `1px solid ${semantic.border.default}` }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ ...type.h2, margin: 0 }}>{id}</div>
            {d && <div style={{ ...mono(11), ...dim, marginTop: 3 }}>{d.path} · sha {d.sha}</div>}
          </div>
          <button className="tx-focus" onClick={onClose} style={btnStyle(true)}>{L("关闭", "Close")}</button>
        </div>
        <div className="tx-scroll" style={{ overflow: "auto", padding: space.s5 }}>
          {err ? empty(L("读不到这份提示词", "Could not load this prompt"))
            : !d ? empty(L("读取中…", "Loading…"))
              : <pre style={{ ...mono(12), margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", color: semantic.text.secondary, lineHeight: 1.75 }}>{d.text}</pre>}
        </div>
      </div>
    </div>
  );
}

// ── 一个节点 ────────────────────────────────────────────────────────────────
type NodeDef = {
  id: string;
  title: string;
  summary: string;          // 收起态那行小字
  what: string;             // ① 干什么
  io: string;
  exec: KV[];               // ② 怎么干的
  params: KV[];             // ③ 参数
  onFail: KV[];             // ④ 挂了怎么办
  // ⑤ 这个节点的数据处在什么状态。**空格子有两种，要能分辨**：
  // live = 有记录，空就是「窗口内真的没样本」；untracked = 本机不单独记这一段，空是必然的。
  // 不分辨的话看到的是同一个空白，只能靠猜。
  dataState: "live" | "untracked";
  recent?: React.ReactNode; // ⑤ 近况正文
  // 默认展开：只给 P3。它是整条流水线里唯一决定成稿质量的一环，打开这页最先要看的就是它用了哪个模型。
  defaultOpen?: boolean;
};

function Block({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <div style={{ paddingTop: space.s3, marginTop: space.s3, borderTop: `1px dashed ${semantic.border.subtle}` }}>
      <div style={{ ...type.label, marginBottom: 6 }}>
        <span style={{ color: semantic.accent.text }}>{n}</span>　{title}
      </div>
      {children}
    </div>
  );
}

// ⑤ 近况的两种收尾：有数就渲染，没数就说清楚为什么没有。**不编一个数字放那儿。**
const noData = (L: ReturnType<typeof useL>, state: NodeDef["dataState"]) => (
  <div style={{ ...mono(11), color: semantic.text.muted }}>
    {state === "live"
      ? L("窗口内没有样本。", "No samples in this window.")
      : L("本机不单独记这一段的耗时；整单用时见「运行」页签。",
          "This step isn't timed separately on this computer; see the Live tab for whole-job times.")}
  </div>
);

const pct = (ok: number, total: number) => (total ? Math.round((ok / total) * 100) : null);

// 一行节点统计。**P95 跟 P50 并排**：这类节点要看的往往正是尾部——中位数好看、
// 尾巴很长，是「偶尔卡很久」，而它平均下来就看不见了。
const nodeLine = (label: string, s?: NodeStat) =>
  (s ? `${label} ${s.ok}/${s.total}　P50 ${s.p50Ms != null ? Math.round(s.p50Ms / 1000) : "—"}s`
     + `　P95 ${s.p95Ms != null ? Math.round(s.p95Ms / 1000) : "—"}s` : null);

// 引擎成功率表。**成功率跟基线并排显示**：光说「78%」没用，得说「平时 99%」——
// 各引擎的常态本来就不同。
function EngineStats({ h }: { h: AdminHealth }) {
  const L = useL();
  const tags = Object.keys(h.p1);
  if (tags.length === 0) return noData(L, "live");
  return (
    <div style={{ display: "grid", gridTemplateColumns: "4.5em 5.5em 6em 1fr", gap: `4px ${space.s4}px`, ...mono(11) }}>
      <div style={dim}>{L("引擎", "Engine")}</div>
      <div style={dim}>{L("成功", "Success")}</div>
      <div style={dim}>{L("秒/音频分钟", "s per audio min")}</div>
      <div style={dim}>{L(`基线（近 ${Math.round(h.baselineHours / 24)} 天）`, `Baseline (${Math.round(h.baselineHours / 24)}d)`)}</div>
      {tags.map((t) => {
        const s = h.p1[t];
        const p = pct(s.ok, s.total);
        const b = h.p1Baseline[t];
        const bad = h.alerts.engines.some((a) => a.tag === t);
        return (
          <div key={t} style={{ display: "contents" }}>
            <div style={{ color: semantic.text.primary }}>{t}</div>
            <div style={{ color: bad ? semantic.danger.text : p === 100 ? semantic.success.text : semantic.text.secondary }}>
              {s.ok}/{s.total}{p != null ? `　${p}%` : ""}
            </div>
            {/* 归一后的耗时才是引擎本身的快慢：不除以音频时长的话，今天全是长文件面板就说「变慢了」 */}
            <div style={{ color: semantic.text.secondary }}>{s.secPerAudioMin != null ? `${s.secPerAudioMin}s` : "—"}</div>
            <div style={dim}>{b?.pct != null ? `${b.pct}%　n=${b.total}` : "—"}</div>
          </div>
        );
      })}
    </div>
  );
}

function KvList({ rows }: { rows: KV[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: `4px ${space.s4}px`, ...mono(11) }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "contents" }}>
          <div style={{ ...dim, whiteSpace: "nowrap" }}>{r.k}</div>
          <div style={{ color: semantic.text.secondary, minWidth: 0 }}>{r.v}</div>
        </div>
      ))}
    </div>
  );
}

function NodeRow({ n, last }: { n: NodeDef; last: boolean }) {
  const L = useL();
  const [open, setOpen] = useState(!!n.defaultOpen);
  return (
    <div style={{ borderBottom: last ? "none" : `1px solid ${semantic.border.subtle}` }}>
      <div onClick={() => setOpen((o) => !o)} style={{ padding: `${space.s4}px ${space.s5}px`, cursor: "pointer" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: space.s3 }}>
          {caret(open)}
          <span style={{ fontFamily: fonts.sans, fontSize: 14, fontWeight: 600, color: semantic.text.primary }}>{n.title}</span>
        </div>
        <div style={{ marginTop: 5, marginLeft: 22, ...mono(11), color: semantic.text.muted }}>
          {n.summary}
        </div>
      </div>
      {open && (
        <div style={{ padding: `0 ${space.s5}px ${space.s5}px calc(${space.s5}px + 22px)` }}>
          <Block n="①" title={L("干什么", "What it does")}>
            <div style={{ fontSize: 13, color: semantic.text.secondary, lineHeight: 1.7 }}>{n.what}</div>
            <div style={{ ...mono(11), ...dim, marginTop: 4 }}>{n.io}</div>
          </Block>
          <Block n="②" title={L("怎么干的", "How it runs")}><KvList rows={n.exec} /></Block>
          <Block n="③" title={L("参数", "Parameters")}><KvList rows={n.params} /></Block>
          <Block n="④" title={L("挂了怎么办", "On failure")}><KvList rows={n.onFail} /></Block>
          <Block n="⑤" title={L("近况", "Recent")}>
            {n.recent ?? noData(L, n.dataState)}
          </Block>
        </div>
      )}
    </div>
  );
}

// ── Tag 主体 ────────────────────────────────────────────────────────────────
export function WorkflowTab() {
  const L = useL();
  const [wf, setWf] = useState<AdminWorkflow | null>(null);
  const [h, setH] = useState<AdminHealth | null>(null);
  const [hours, setHours] = useState(24);
  const [err, setErr] = useState(false);
  const [prompt, setPrompt] = useState<string | null>(null);

  const load = useCallback(() => {
    getAdminWorkflow().then((d) => { setWf(d); setErr(false); }).catch(() => setErr(true));
  }, []);
  useEffect(() => { load(); }, [load]);
  // 健康度单独取：它带窗口参数，配置那份不带。取不到就让各节点的 ⑤ 显示「—」，
  // 不该连累「它是怎么配置的」——那一半跟统计没关系。
  useEffect(() => {
    let alive = true;
    getAdminHealth(hours).then((d) => { if (alive) setH(d); }).catch(() => { if (alive) setH(null); });
    return () => { alive = false; };
  }, [hours]);

  if (err) return <SectionShell title={L("工作流", "Workflow")}>{empty(L("读不到工作流配置", "Could not load workflow config"))}</SectionShell>;
  if (!wf) return <SectionShell title={L("工作流", "Workflow")}>{empty(L("读取中…", "Loading…"))}</SectionShell>;

  const p = wf.params;
  const g = <T,>(group: string, key: string, fallback: T): T =>
    ((p?.[group]?.[key] as T | undefined) ?? fallback);
  const num = (group: string, key: string) => {
    const v = p?.[group]?.[key];
    return v == null ? "—" : String(v);
  };
  const onOff = (v: unknown) => (v ? L("开", "On") : L("关", "Off"));

  // 提示词名片：路径 · 行数 · 指纹 + 查看全文。取不到就不显示这一行（不编造）。
  const promptRow = (id: string, label: string): KV | null => {
    const c = wf.prompts[id];
    if (!c) return null;
    return {
      k: label,
      v: (
        <span>
          {c.path} · {c.lines} {L("行", "lines")} · sha {c.sha}{" "}
          <button className="tx-focus" onClick={() => setPrompt(id)}
                  style={{ ...mono(11), padding: 0, border: "none", background: "none", cursor: "pointer", color: semantic.accent.text }}>
            {L("［查看全文］", "[view]")}
          </button>
        </span>
      ),
    };
  };
  const rows = (...xs: (KV | null | false)[]) => xs.filter((x): x is KV => !!x);

  // 模型后端：定字、术语库助手、后处理三处共用「设置」里那一个。名字、主机、钥匙都由后端给。
  const b = wf.backend;
  const backendLabel = !b ? "—"
    : b.kind === "claude_p" ? L.t("Claude 订阅 · {0}", "Claude subscription · {0}", b.model)
      : b.local ? L.t("{0} · 本机", "{0} · on this computer", b.model) : `${b.model} @ ${b.host}`;
  const backendRow: KV = { k: L("执行体", "Runs"), v: L.t("模型后端 {0}", "Model backend {0}", backendLabel) };
  // 钥匙只在「走外部 API」时才有意义：本机模型与 Claude 订阅都不要它。只报设没设，不报值。
  const keyRow: KV | null = b && b.kind === "openai" && !b.local && b.keyEnv ? {
    k: L("密钥", "API key"),
    v: b.keySet ? L.t("密钥从环境变量 {0} 读取 · 已设置", "Key is read from environment variable {0} · set", b.keyEnv)
      : <span style={{ color: semantic.danger.text }}>{L.t("密钥从环境变量 {0} 读取 · 未设置", "Key is read from environment variable {0} · not set", b.keyEnv)}</span>,
  } : null;
  // Claude 订阅只接得了定字；术语库助手与后处理要 OpenAI 协议的后端（设置页的「数据去哪」同样标「用不了」）
  const claudeRow: KV | null = b?.kind === "claude_p"
    ? { k: L("用 Claude 订阅时", "With a Claude subscription"),
        v: <span style={{ color: semantic.warning.text }}>{L("用不了：这一环要 API 或本机模型", "Unavailable: this step needs an API or a local model")}</span> }
    : null;
  const localFallback: KV = { k: L("降级路", "Fallback"), v: L("无（本机计算）。这一步出错整单判失败。", "None (local). An error here fails the job.") };

  // ⑤ 近况的构件。h 为 null（取不到 / 还没回来）时一律回 undefined → 节点显示占位说明，
  // 不显示 0（0 会被读成「跑了 0 次」，而实际是「不知道」）
  const stat = (line: React.ReactNode) => <div style={{ ...mono(11), color: semantic.text.secondary }}>{line}</div>;

  const ppRecent = (step: string) => {
    const s = h?.pp[step];
    if (!s) return undefined;
    const p = pct(s.ok, s.total);
    return stat(
      <>
        {L(`${s.ok}/${s.total} 成功`, `${s.ok}/${s.total} ok`)}{p != null ? `　${p}%` : ""}
        {s.failed > 0 && <span style={{ color: semantic.danger.text }}>　{L(`失败 ${s.failed}`, `${s.failed} failed`)}</span>}
      </>,
    );
  };

  const glossaryRecent = (() => {
    const d = h?.glossary["glossary_draft"];
    const c = h?.glossary["glossary_check"];
    if (!d && !c) return undefined;
    return (
      <div style={{ ...mono(11), color: semantic.text.secondary, lineHeight: 1.8 }}>
        {[nodeLine(L("起草", "Draft"), d), nodeLine(L("体检", "Review"), c)].filter(Boolean).map((s, i) => <div key={i}>{s}</div>)}
        <div style={dim}>
          {L("看的是用户真实等待时长——它前一百多秒一个字不吐（在思考），所以要看 P95 不只是 P50。",
             "This measures how long the user actually waits — watch P95, not just the median.")}
        </div>
      </div>
    );
  })();

  // 出稿用的是哪个模型：本机没有「第一档 / 降级」之分（模型是设置里选的），所以只列各模型出了几单，不算降级率
  const p3Recent = (() => {
    if (!h || h.p3.total === 0) return undefined;
    return stat(
      <>
        {L("出稿模型　", "Model used　")}
        {Object.entries(h.p3.tiers).map(([k, v], i) => (
          <span key={k}>{i > 0 && "　·　"}{k} {v}</span>
        ))}
      </>,
    );
  })();

  const engines = g<{ id: string; tag: string; route: string }[]>("p1", "engines", []);
  const fillers = String(g<string>("p2", "fillers", ""));

  const nodes: NodeDef[] = [
    {
      id: "glossary",
      title: L("术语库助手", "Glossary assistant"),
      summary: L.t("{0} · 提示词写在代码里 · 无降级路", "{0} · prompt lives in code · no fallback", backendLabel),
      what: L("按访谈大纲起草一本术语库，或给已有的库出体检建议。它不在转录流水线上，是用户上传前的准备环节。",
              "Drafts a glossary from an interview outline, or reviews an existing one. Not on the transcription path."),
      io: L("输入：访谈大纲　→　输出：分类 + 术语条目", "In: outline → Out: categories + entries"),
      exec: rows(backendRow, keyRow, promptRow("glossary-assist", L("提示词", "Prompt"))),
      params: [
        { k: L("超时", "Timeout"), v: `${num("glossary", "timeoutSec")}s` },
        { k: L("最多收录", "Max entries"), v: `${num("glossary", "maxEntries")} ${L("条", "entries")}` },
        { k: L("单条释义", "Meaning"), v: `≤ ${num("glossary", "meaningMax")} ${L("字", "chars")}` },
        { k: L("整本上限", "Book cap"), v: `≤ ${num("glossary", "totalMax")} ${L("字", "chars")}` },
        { k: L("大纲入参", "Outline input"), v: `≤ ${num("glossary", "outlineMax")} ${L("字", "chars")}` },
      ],
      onFail: rows(
        { k: L("降级路", "Fallback"), v: L("无。调不通就返回「不可用」让用户重试——不拿一份糟糕的草稿冒充。",
                                          "None. Returns unavailable so the user can retry.") },
        claudeRow,
      ),
      dataState: "live",
      recent: glossaryRecent,
    },
    {
      id: "p0",
      title: L("P0 转码与切块", "P0 Transcode & chunk"),
      summary: L("本地脚本 · 无外部依赖", "Local script · no external calls"),
      what: L("把上传的音频转成统一格式，做声纹分段与人声检测，再切成等长小块；几台引擎吃的是同一份切块。",
              "Converts the upload to a uniform format, finds who speaks when, and cuts equal-length chunks that every engine shares."),
      io: L("输入：上传的音频　→　输出：切块 + 每段是谁在说", "In: uploaded audio → Out: chunks + who speaks in each"),
      exec: [{ k: L("执行体", "Runs"), v: "src/transcribe_local/audio.py · diarize.py" }],
      params: [
        { k: L("采样", "Sampling"), v: `${num("p0", "sampleRate")}Hz · ${num("p0", "channels")}ch` },
        { k: L("声纹分段", "Speaker segmentation"), v: num("p0", "segmentation") },
        { k: L("声纹嵌入", "Speaker embedding"), v: num("p0", "embedding") },
        { k: L("说话人数", "Speakers"), v: L.t("{0}（固定）", "{0} (fixed)", num("p0", "numClusters")) },
        { k: L("人声检测", "Voice detection"), v: g<string | null>("p0", "vad", null) ?? L("关", "Off") },
        { k: L("块长", "Chunk length"), v: `${num("p0", "chopMaxLength")}s · ${num("p0", "chopStrategy")}` },
        { k: L("按换人拆块", "Split at speaker changes"), v: onOff(g("p0", "speakerSplit", false)) },
        { k: L("单文件时长上限", "Max duration"), v: `${Math.round(g<number>("p0", "maxDurationSec", 0) / 3600)}h` },
      ],
      onFail: [localFallback],
      dataState: "untracked",
    },
    {
      id: "p1",
      title: L("P1 多路 ASR", "P1 Multi-engine ASR"),
      summary: L.t("{0} 台本机引擎 · 同时跑 {1} 台", "{0} local engines · {1} at a time", engines.length, num("p1", "parallel")),
      what: L("同一份切块同时交给解码路线互不相同的几台本机引擎，各出一份稿，供分歧册比对。",
              "The same chunks go to local engines with different decoding approaches; each writes its own transcript for the divergence ledger."),
      io: L("输入：切块　→　输出：每台一份稿", "In: chunks → Out: one transcript per engine"),
      exec: [
        { k: L("执行体", "Runs"), v: "sherpa-onnx · src/transcribe_local/engines.py" },
        ...engines.map((e) => ({ k: e.tag, v: `${e.id} · ${e.route}` })),
      ],
      params: [
        { k: L("每台线程数", "Threads per engine"), v: num("p1", "numThreads") },
        { k: L("同时跑几台", "Engines at once"), v: num("p1", "parallel") },
        { k: L("复读熔断", "Loop breaker"), v: L.t("同一短串连续重复 ≥ {0} 次判复读", "≥ {0} consecutive repeats counts as a loop", num("p1", "repeatThreshold")) },
        { k: L("重试梯子", "Retry ladder"), v: L.t("{0} 档", "{0} steps", num("p1", "maxRetry")) },
      ],
      onFail: [
        { k: L("某台某块出错", "One engine, one chunk"),
          v: L("复读或吐空先按梯子重试；走完仍不行，这一台这一块弃用，其余几台照常，分歧册里显示 ∅。",
               "Loops or empty output climb the retry ladder; if that still fails, that engine's chunk is dropped, the others carry on, and the ledger shows ∅.") },
      ],
      dataState: "live",
      recent: h ? <EngineStats h={h} /> : undefined,
    },
    {
      id: "p2",
      title: L("分歧册", "Divergence ledger"),
      summary: L("本地脚本 · 无外部依赖", "Local script · no external calls"),
      what: L("逐块比对各台的稿，穷举找出写法不一致的地方，交给定字逐处定夺；只因语气词不同的差异自动折叠。",
              "Compares the engines chunk by chunk, lists every place they disagree, and hands them to proofreading. Differences caused only by filler words are folded."),
      io: L("输入：各台的稿　→　输出：分歧册（每处分歧各台怎么写）", "In: per-engine transcripts → Out: divergence ledger (how each engine wrote each disagreement)"),
      exec: [{ k: L("执行体", "Runs"), v: "src/transcribe_local/divergence.py" }],
      params: [
        { k: L("折叠语气词", "Fold fillers"), v: onOff(g("p2", "foldFillers", false)) },
        { k: L("语气词表", "Filler list"), v: L.t("{0}（{1} 字）", "{0} ({1} chars)", fillers, [...fillers].length) },
        { k: L("剥掉模型标记", "Strip model tags"), v: onOff(g("p2", "stripTags", false)) },
      ],
      onFail: [localFallback],
      dataState: "untracked",
    },
    {
      id: "p3",
      title: L("P3 融合", "P3 Merge"),
      summary: `${backendLabel} · ${num("p3", "workflow")}`,
      what: L("按分歧点逐处定夺，出一份终稿，并把拿不准的地方列进复核清单。这是整条流水线里唯一决定成稿质量的一环。",
              "Resolves each conflict into a final transcript and lists the uncertain spots for review."),
      io: L("输入：分歧册 + 用户术语库　→　输出：终稿 + 复核报告", "In: divergence ledger + user glossary → Out: final transcript + review report"),
      exec: rows(
        b?.kind === "claude_p"
          ? { k: L("执行体", "Runs"), v: `claude -p · --model ${num("p3", "claudeModel")} · --effort ${num("p3", "claudeEffort")}` }
          : backendRow,
        keyRow,
        promptRow("merge-saas", L("规则书 · 联网版", "Rules · online")),
        promptRow("merge-offline", L("规则书 · 离线版", "Rules · offline")),
        { k: L("联网核实", "Web check"), v: b?.webSearch ? L("开 · 定不下的专名发给博查", "On · unresolved names are searched on Bocha") : L("关", "Off") },
        { k: L("术语库", "Glossary"), v: g("p3", "termsInject", false) ? L("注入定字，作硬证据", "Injected as hard evidence") : L("不注入", "Not injected") },
      ),
      params: b?.kind === "claude_p"
        ? [{ k: L("超时", "Timeout"), v: `${num("p3", "claudeTimeoutSec")}s` }]
        : [
          { k: L("每轮正文", "Per round"), v: `${num("p3", "roundTokens")} tok` },
          { k: L("向前多带", "Overlap"), v: L.t("{0} 块", "{0} chunk(s)", num("p3", "overlap")) },
          { k: L("同时在飞", "Rounds at once"), v: num("p3", "concurrency") },
          { k: L("超时", "Timeout"), v: `${num("p3", "timeoutSec")}s` },
          { k: L("重试", "Retries"), v: num("p3", "maxRetry") },
          { k: L("输出上限", "Output cap"), v: `${num("p3", "maxTokens")} tok` },
        ],
      onFail: [
        { k: L("降级路", "Fallback"), v: L("无。用哪个模型是「设置」里选的，不会自动换一个。", "None. The model is the one chosen in Settings; nothing switches automatically.") },
        { k: L("输出被截断", "Truncated output"), v: L("这一轮对半切开各要一次，切到单块仍截断才报错——不收半截稿。",
                                                     "The round is split in half and retried; it only errors if a single chunk is still cut off. Partial output is never accepted.") },
      ],
      dataState: "live",
      recent: p3Recent,
      defaultOpen: true,
    },
    {
      id: "narrate",
      title: L("后处理 · 视角转换", "Post · Narrative"),
      summary: `${backendLabel} · ${L.t("每批 {0} 字", "{0} chars per batch", num("pp", "narrateBudget"))}`,
      what: L("把问答体的访谈稿改写成叙述体，保留全部事实与措辞证据，不做概括。",
              "Rewrites the Q&A transcript into narrative form without summarizing."),
      io: L("输入：复核后的稿　→　输出：叙述稿 + 问题清单", "In: reviewed transcript → Out: narrative + issue list"),
      exec: rows(backendRow, keyRow, promptRow("pp-narrate", L("规则书", "Skill")), promptRow("shared-qc", L("共用质检", "Shared QC"))),
      params: [
        { k: L("分批", "Batching"), v: L.t("每批 {0} 字 · 并发 {1}", "{0} chars per batch · {1} at a time", num("pp", "narrateBudget"), num("pp", "narrateConc")) },
        { k: L("丢字护栏", "Drop guard"), v: L.t("丢 >{0}% 告警 · >{1}% 判失败", "warn >{0}% · fail >{1}%",
                                                 Math.round(g<number>("pp", "dropWarn", 0) * 100), Math.round(g<number>("pp", "dropFatal", 0) * 100)) },
      ],
      onFail: rows(
        { k: L("降级路", "Fallback"), v: L("无。这一步失败，整个加工判失败，可以重新发起。", "None. If this step fails, the job fails and can be started again.") },
        claudeRow,
      ),
      dataState: "live",
      recent: ppRecent("narrate"),
    },
    {
      id: "redact",
      title: L("后处理 · 脱敏", "Post · Redact"),
      summary: `${backendLabel} · ${L.t("每批 {0} 字", "{0} chars per batch", num("pp", "redactBudget"))}`,
      what: L("把可能暴露受访者身份的信息替换掉（公司 / 人名 / 联系方式 / 地理 / 年限 / 项目），第三方品牌与技术术语原样保留。",
              "Replaces information that could identify the interviewee; third-party brands and technical terms stay."),
      io: L("输入：上一步产物 + 可选保留清单　→　输出：脱敏稿 + 质检报告", "In: previous output + optional keep-list → Out: redacted draft + QC report"),
      exec: rows(
        backendRow, keyRow,
        promptRow("pp-redact", L("规则书", "Skill")),
        promptRow("shared-qc", L("共用质检", "Shared QC")),
        { k: L("怎么替换的", "How"), v: L("模型只出替换单（行号｜原词｜替换词），替换由程序执行——「只替换、不增删」因此是程序事实，不是模型自觉",
                                        "The model only emits a replacement list; the program applies it") },
      ),
      params: [
        { k: L("分批", "Batching"), v: L.t("每批 {0} 字 · 并发 {1} · 扫 {2} 遍", "{0} chars per batch · {1} at a time · {2} pass(es)",
                                           num("pp", "redactBudget"), num("pp", "redactConc"), num("pp", "redactPasses")) },
        { k: L("复核", "Second review"), v: onOff(g("pp", "redactReview", false)) },
        { k: L("清单上限", "Keep-list cap"), v: `≤ ${num("pp", "listMaxChars")} ${L("字", "chars")}` },
      ],
      onFail: rows(
        { k: L("降级路", "Fallback"), v: L("无。这一步失败，整个加工判失败，可以重新发起。", "None. If this step fails, the job fails and can be started again.") },
        claudeRow,
      ),
      dataState: "live",
      recent: ppRecent("redact"),
    },
  ];

  return (
    <>
      <SectionShell
        title={L("工作流", "Workflow")}
        action={
          // 窗口切换只影响 ⑤ 近况；①–④ 是配置，跟窗口无关。7 天那档是用来看「是不是一直
          // 这样」的——24 小时里一次失败可能只是偶发，一周里天天有就是问题。
          <div style={{ display: "flex", alignItems: "center", gap: space.s3 }}>
            <span style={{ ...mono(11), ...dim }}>{L("近况窗口", "Window")}</span>
            {[24, 168].map((x) => (
              <button key={x} className="tx-focus" onClick={() => setHours(x)}
                      style={{ ...btnStyle(hours !== x), padding: "3px 10px" }}>
                {x === 24 ? L("24 小时", "24h") : L("7 天", "7d")}
              </button>
            ))}
          </div>
        }
      >
        {/* 参数口径：写清楚这些数是从哪来的——默认配置叠上用户配置，才是实际在跑的那一份 */}
        <div style={{ padding: `${space.s3}px ${space.s5}px`, borderBottom: `1px solid ${semantic.border.default}`, background: semantic.surface.page, ...mono(11), ...dim, lineHeight: 1.7 }}>
          {L("模型后端", "Model backend")} <span style={{ color: semantic.text.secondary }}>{backendLabel}</span>
          {"　·　"}
          {L("下面的参数取自这台电脑上实际生效的配置（默认配置叠上你的改动），提示词是程序真正送出去的那几份。模型后端在「设置」里改。",
             "Parameters below are the configuration actually in effect on this computer (defaults plus your changes); prompts are the exact text the app sends. Change the model backend in Settings.")}
        </div>
        {nodes.map((n, i) => <NodeRow key={n.id} n={n} last={i === nodes.length - 1} />)}
      </SectionShell>
      {prompt && <PromptViewer id={prompt} onClose={() => setPrompt(null)} />}
    </>
  );
}

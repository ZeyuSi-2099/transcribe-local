// 运营驾驶舱 · 工作流 Tag：各节点自上而下，每个展开成同一套骨架。
//
// **骨架为什么固定成六块**：节点长得很不一样（有的是 skill、有的是纯本地脚本、有的两条路），
// 但提问的方式是一样的——干什么 / 怎么干的 / 参数 / 挂了怎么办 / 近况 / 动作。
// 不适用的那块**明写「无」**，不许省略：空着会让人以为是没做完。
//
// **后端只出事实、前端只出文案**：这里的每个数字都来自 /admin/workflow（后端 import 真实常量），
// 前端一个都不许写死。写死的那一刻，界面就开始和实际跑的东西分家，而且不会报错。
//
// ⚠️ 提示词指纹是**派单前台这一份**的。转录与后处理跑在 Fly 任务镜像上，push 只重部署 Render、
// 不重建 Fly 镜像——两边可能不是同一份，这正是要显示指纹的原因。所以顶部那条说明必须留着，
// 别把它当成「已核对」的对勾（真正的核对要等节点埋点把跑的那一份指纹报回来）。
import { useCallback, useEffect, useState } from "react";
import { useL } from "../../lib/i18n";
import { semantic, fonts, type, space, radius } from "../../styles/tokens";
import { langName } from "../../lib/langs";
import { getAdminWorkflow, getAdminPrompt, getAdminHealth,
         type AdminWorkflow, type AdminHealth, type NodeStat } from "../../lib/api";
import { mono, caret, SectionShell, empty, btnStyle } from "./ui";

type KV = { k: string; v: React.ReactNode };

const dim = { color: semantic.text.muted };

// ── 提示词全文浮层（只读）──────────────────────────────────────────────────────
// 只读是刻意的：改提示词要走代码评审 + 重建镜像，界面上给个编辑框只会制造「我改了怎么没生效」。
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

// ── 27 门语种的引擎编排（只读）────────────────────────────────────────────────
// 之前定的是「不在界面上改」，只读展示不冲突——排查某语言质量问题时，第一件事就是看它
// 用了哪几路参考轨，而这信息此前只在 27 个 yaml 里。
function LangPlans({ plans }: { plans: AdminWorkflow["langPlans"] }) {
  const L = useL();
  const [open, setOpen] = useState(false);
  if (plans.length === 0) return null;
  const shown = open ? plans : plans.filter((p) => p.lang === "zh" || p.lang === "en");
  return (
    <div style={{ marginTop: space.s3 }}>
      <div style={{ display: "grid", gridTemplateColumns: "5.5em 4em 1fr", gap: space.s3, ...mono(11) }}>
        {shown.map((p) => (
          <div key={p.lang} style={{ display: "contents" }}>
            <div style={{ color: semantic.text.secondary }}>{langName(p.lang, L)}</div>
            <div style={{ color: semantic.accent.text }}>{p.primary}</div>
            <div style={dim}>{p.refs.length ? p.refs.map((r, i) => `${i + 1}. ${r}`).join("　") : L("无参考轨", "no reference tracks")}</div>
          </div>
        ))}
      </div>
      <button className="tx-focus" onClick={() => setOpen((o) => !o)} style={{ ...btnStyle(true), marginTop: space.s3 }}>
        {open ? L("收起", "Collapse") : L(`展开全部 ${plans.length} 门`, `Show all ${plans.length}`)}
      </button>
    </div>
  );
}

// ── 一个节点 ────────────────────────────────────────────────────────────────
type NodeDef = {
  id: string;
  title: string;
  summary: string;          // 收起态那行小字
  warn?: boolean;           // 收起态就要看见的要紧事（归类下架后暂时没有节点用它，保留给下一个）
  what: string;             // ① 干什么
  io: string;
  exec: KV[];               // ② 怎么干的
  params: KV[];             // ③ 参数
  onFail: KV[];             // ④ 挂了怎么办
  // ⑤ 这个节点的数据处在什么状态。**空格子有两种，要能分辨**：
  // live = 已在跑，空就是「窗口内真的没样本」；
  // pendingImage = 埋点代码已经写好，但它跑在任务机器上，重建 Fly 镜像之前恒为空。
  // 不分辨的话运营看到的是同一个空白，只能靠猜。
  dataState: "live" | "pendingImage";
  recent?: React.ReactNode; // ⑤ 近况正文
  extra?: React.ReactNode;  // ⑤ 之后追加的实时块（现在只有 P3 的健康度与配置）
  tail?: React.ReactNode;   // 附加区块（现在只有 P1 的语种编排）
  // 默认展开：只给 P3。它是唯一有实时状态和可执行动作的节点，收起来等于把健康度藏到两次点击
  // 之后——那张卡是这个页面上被看得最勤的东西，不该比改造前更难找到。
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
  <div style={{ ...mono(11), color: state === "live" ? semantic.text.muted : semantic.warning.text }}>
    {state === "live"
      ? L("窗口内没有样本。", "No samples in this window.")
      : L("耗时记录已写好，但它跑在任务机器上——重建镜像之前这里恒为空。"
        + "（推代码只重部署派单前台，转录跑的是另一个镜像。）",
          "Timing is instrumented but runs on the task machine — empty until the image is rebuilt.")}
  </div>
);

const pct = (ok: number, total: number) => (total ? Math.round((ok / total) * 100) : null);

// 一行节点统计。**P95 跟 P50 并排**：这类节点要看的往往正是尾部——中位数好看、
// 尾巴很长，是「偶尔卡很久」，而它平均下来就看不见了。
const nodeLine = (label: string, s?: NodeStat) =>
  (s ? `${label} ${s.ok}/${s.total}　P50 ${s.p50Ms != null ? Math.round(s.p50Ms / 1000) : "—"}s`
     + `　P95 ${s.p95Ms != null ? Math.round(s.p95Ms / 1000) : "—"}s` : null);

// 引擎成功率表。**成功率跟基线并排显示**：光说「78%」没用，得说「平时 99%」——
// 各引擎的常态本来就不同（国际轨偶发超时是常态，国产轨几乎不失败）。
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
        <div style={{ marginTop: 5, marginLeft: 22, ...mono(11), color: n.warn ? semantic.warning.text : semantic.text.muted }}>
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
            {n.extra}
          </Block>
          {n.tail}
        </div>
      )}
    </div>
  );
}

// ── Tag 主体 ────────────────────────────────────────────────────────────────
export function WorkflowTab({ p3Extra }: { p3Extra?: React.ReactNode }) {
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
  // 不该连累「它是怎么配置的」——那一半跟埋点没关系。
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
  const rows = (...xs: (KV | null)[]) => xs.filter((x): x is KV => x !== null);

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
        {/* 降级次数是判断「Claude 额度够不够」的直接依据，比成功率更该看见 */}
        {s.degraded > 0 && <span style={{ color: semantic.warning.text }}>　{L(`降级 ${s.degraded}`, `${s.degraded} fell back`)}　¥{s.dsCostCny.toFixed(2)}</span>}
      </>,
    );
  };

  // P0 转码 / P2 对齐：这两段唯一值得看的就是耗时——它们几乎不失败，失败了整单就失败、
  // 已经在失败清单里躺着。没有样本就交回 undefined，让节点自己说明是哪一种空。
  const phaseRecent = (node: string, note: string) => {
    const s = h?.phases?.[node];
    if (!s) return undefined;
    return (
      <div style={{ ...mono(11), color: semantic.text.secondary, lineHeight: 1.8 }}>
        <div>{nodeLine(L("耗时", "Duration"), s)}</div>
        {s.ok < s.total && (
          <div style={{ color: semantic.danger.text }}>{L(`失败 ${s.total - s.ok}`, `${s.total - s.ok} failed`)}</div>
        )}
        <div style={dim}>{note}</div>
      </div>
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

  const p3Recent = (() => {
    if (!h || h.p3.total === 0) return undefined;
    const r = h.p3.degradedRatio;
    return (
      <div style={{ ...mono(11), color: semantic.text.secondary, lineHeight: 1.8 }}>
        <div>
          {L("出稿档位　", "Tiers　")}
          {Object.entries(h.p3.tiers).map(([k, v], i) => (
            <span key={k}>{i > 0 && "　·　"}{k} {v}</span>
          ))}
        </div>
        {r != null && (
          // 降级率是「Claude 额度到底够不够」最直接的依据，也是将来要不要升档订阅的唯一凭据
          <div style={{ color: r >= 0.5 ? semantic.warning.text : semantic.text.secondary }}>
            {L(`降级率 ${Math.round(r * 100)}%（${h.p3.known} 单可判定）`,
               `${Math.round(r * 100)}% fell back (${h.p3.known} classifiable)`)}
          </div>
        )}
      </div>
    );
  })();

  const claudeCmd = `claude -p · --model ${num("p3", "model")} · --effort ${num("p3", "effort")}`;
  const ppConc = num("pp", "concurrency");
  const ppTimeout = num("pp", "timeoutSec");
  const flashHead = `${num("ppFlash", "model")} · effort ${num("ppFlash", "effort")}`;
  const rate = g<Record<string, number>>("p1", "rateCnyPerMin", {});
  const p3Args = g<string[]>("p3", "args", []).join(" ");

  const nodes: NodeDef[] = [
    {
      id: "glossary",
      title: L("术语库助手", "Glossary assistant"),
      summary: L(`${num("glossary", "model")} 直连 · 提示词写在代码里 · 无降级路`,
                 `${num("glossary", "model")} · prompt lives in code · no fallback`),
      what: L("按访谈大纲起草一本术语库，或给已有的库出体检建议。它不在转录流水线上，是用户上传前的准备环节。",
              "Drafts a glossary from an interview outline, or reviews an existing one. Not on the transcription path."),
      io: L("输入：访谈大纲　→　输出：分类 + 术语条目", "In: outline → Out: categories + entries"),
      exec: rows(
        { k: L("执行体", "Runs"), v: L(`DeepSeek ${num("glossary", "model")} 直连 API`, `DeepSeek ${num("glossary", "model")} direct API`) },
        promptRow("glossary-assist", L("提示词", "Prompt")),
      ),
      params: [
        { k: L("超时", "Timeout"), v: `${num("glossary", "timeoutSec")}s` },
        { k: L("最多收录", "Max entries"), v: `${num("glossary", "maxEntries")} ${L("条", "entries")}` },
        { k: L("单条释义", "Meaning"), v: `≤ ${num("glossary", "meaningMax")} ${L("字", "chars")}` },
        { k: L("整本上限", "Book cap"), v: `≤ ${num("glossary", "totalMax")} ${L("字", "chars")}` },
        { k: L("大纲入参", "Outline input"), v: `≤ ${num("glossary", "outlineMax")} ${L("字", "chars")}` },
      ],
      onFail: [{ k: L("降级路", "Fallback"), v: L("无。调不通就返回「不可用」让用户重试——不拿一份糟糕的草稿冒充。",
                                                  "None. Returns unavailable so the user can retry.") }],
      dataState: "live",
      recent: glossaryRecent,
    },
    {
      id: "p0",
      title: L("P0 转码", "P0 Transcode"),
      summary: L("本地脚本 · 无外部依赖", "Local script · no external calls"),
      what: L("把上传的音频转成统一格式，并按静音处切成若干块，供后面各路引擎分片处理。",
              "Converts the upload to a uniform format and splits it at silences for downstream engines."),
      io: L("输入：用户上传的音频　→　输出：FLAC 完整版 + 若干 Part", "In: uploaded audio → Out: full FLAC + parts"),
      exec: [{ k: L("执行体", "Runs"), v: "pipeline/vendor/Workflow/core/Phase0_Audio-Convert.py" }],
      params: [
        { k: L("目标分块", "Target chunk"), v: L(`${num("p0", "TARGET_DURATION_MIN")} 分钟（±${num("p0", "SEARCH_RANGE_MINUTES")} 分钟内找最长静音处切）`,
                                                 `${num("p0", "TARGET_DURATION_MIN")} min (search ±${num("p0", "SEARCH_RANGE_MINUTES")} min)`) },
        { k: L("末块最短", "Min last chunk"), v: `${num("p0", "MIN_LAST_SEGMENT_MIN")} ${L("分钟", "min")}` },
        { k: L("静音判据", "Silence"), v: `${num("p0", "SILENCE_THRESH_DB")}dB · ${num("p0", "SILENCE_MIN_DUR")}s` },
        { k: L("输出格式", "Output"), v: `${num("p0", "AUDIO_FORMAT")} · ${num("p0", "SAMPLE_RATE")}Hz · ${num("p0", "CHANNELS")}ch` },
        { k: L("单文件时长上限", "Max duration"), v: `${Math.round(g<number>("p1", "maxDurationSec", 0) / 3600)}h` },
      ],
      onFail: [{ k: L("降级路", "Fallback"), v: L("无（本地计算）。转不出来整单判失败，不计费。", "None (local). Failure fails the job; not billed.") }],
      dataState: "pendingImage",
      recent: phaseRecent("p0", L("不随音频时长归一：转码是整档一次的开销，长文件本来就该更久。",
                                  "Not normalized by length — transcoding scales with the file.")),
    },
    {
      id: "p1",
      title: L("P1 多路 ASR", "P1 Multi-engine ASR"),
      summary: L("主轨 + 参考轨并行 · 编排按语种", "Primary + reference tracks in parallel · per-language plan"),
      what: L("同一段音频同时交给多路引擎转写：一路主轨出正文，其余作参考轨，供下一步对齐比对。",
              "Runs several ASR engines on the same audio: one primary transcript plus reference tracks."),
      io: L("输入：FLAC　→　输出：每路一份带时间码的稿", "In: FLAC → Out: one timestamped transcript per engine"),
      exec: [
        { k: L("执行体", "Runs"), v: L("8 个引擎适配器（pipeline/vendor/Workflow/engines/）", "8 engine adapters") },
        { k: L("编排来源", "Plan source"), v: L("Config/languages/<语种>.yaml —— 见下方全表", "Config/languages/<lang>.yaml — full table below") },
      ],
      params: [
        // 2026-08-18 取消场景分流：profile 里配几条参考轨就跑几条，逐门条数在下面的语种编排表里
        { k: L("取几路参考轨", "Reference tracks"), v: L("按语种 profile 全跑（不再按场景截断）", "All refs in the language profile (no scene cut)") },
        // 2026-08-25 起主轨整档直传（我们不切片）：切片由 ElevenLabs 内部做，每片占一个账号
        // 并发名额，所以我们能控的旋钮变成了「同时派几单转录」。
        { k: L("主轨切片", "Primary chunking"),
          v: L(`不切片，整档直传（超 ${num("p1", "elvSliceThresholdSec")}s 由供应商内部并行，最多 ${num("p1", "elvMaxSlices")} 片）`,
               `Whole file (vendor parallelises above ${num("p1", "elvSliceThresholdSec")}s, max ${num("p1", "elvMaxSlices")} slices)`) },
        // 供应商并发闸挂在**引擎调用**上（跑完那一路就还名额），不挂在整单上——
        // 所以「同时几单在跑」远大于「同时几单在调 ELV」。
        { k: L("引擎并发闸", "Vendor gates"),
          v: `ELV ${num("p1", "elvConcurrency")} · XF ${num("p1", "xfConcurrency")}` },
        { k: L("同时在飞几单", "Machines in flight"), v: `${num("p1", "maxTranscribeJobs")}` },
        { k: L("进度心跳", "Heartbeat"), v: `${num("p1", "pollSec")}s` },
        { k: L("计价（¥/分钟）", "Rate (¥/min)"), v: Object.entries(rate).map(([k, v]) => `${k} ${v}`).join(" · ") || "—" },
      ],
      onFail: [
        { k: L("参考轨", "Reference"), v: L("挂了跳过，用剩下的继续融合", "Skipped; merge continues with the rest") },
        { k: L("主轨", "Primary"), v: <span style={{ color: semantic.danger.text }}>{L("挂了整单失败。分片有失败也判失败——不缝合带空洞的稿。",
                                                                                       "Failure fails the job. Partial failures too — no transcript with silent holes.")}</span> },
      ],
      dataState: "live",
      recent: h ? <EngineStats h={h} /> : undefined,
      tail: (
        <Block n="⑦" title={L("语种编排（只读）", "Per-language plan (read-only)")}>
          <LangPlans plans={wf.langPlans} />
        </Block>
      ),
    },
    {
      id: "p2",
      title: L("P2 对齐", "P2 Align"),
      summary: L("本地脚本 · 无外部依赖", "Local script · no external calls"),
      what: L("把各路稿按时间与读音对齐，逐行标出主轨与参考轨的分歧点，供融合时判断。",
              "Aligns the tracks and marks where the primary and references disagree."),
      io: L("输入：各路稿　→　输出：对齐稿（分歧点已标注）", "In: per-engine transcripts → Out: aligned file with conflicts marked"),
      exec: [{ k: L("执行体", "Runs"), v: "pipeline/vendor/Workflow/core/Phase2_Align_Match.py" }],
      params: [
        { k: L("候选上限", "Max candidates"), v: L(`${num("p2", "MAX_CANDIDATES")}（长稿动态放宽）`, `${num("p2", "MAX_CANDIDATES")} (widened for long files)`) },
        { k: L("填充词连跳", "Filler skips"), v: `≤ ${num("p2", "MAX_CONSECUTIVE_FILLER_SKIPS")}` },
        { k: L("按无空格规则处理", "No-space languages"), v: (g<string[]>("p2", "NO_SPACE_LANGS", []).join(" · ") || "—") },
      ],
      onFail: [{ k: L("降级路", "Fallback"), v: L("无（本地计算）。对不齐整单判失败，不计费。", "None (local). Failure fails the job; not billed.") }],
      dataState: "pendingImage",
      recent: phaseRecent("p2", L("整条链上最长的一段静默：没有子进度，只靠心跳喂看门狗，此前跑多久无从得知。",
                                  "The longest silent stretch in the pipeline — no sub-progress, only a heartbeat.")),
    },
    {
      id: "p3",
      title: L("P3 融合", "P3 Merge"),
      summary: L(`claude /multi-asr-merge → ${num("ppFlash", "model")}`, `claude /multi-asr-merge → ${num("ppFlash", "model")}`),
      what: L("按分歧点逐处定夺，出一份终稿，并把拿不准的地方列进复核清单。这是整条流水线里唯一决定成稿质量的一环。",
              "Resolves each conflict into a final transcript and lists the uncertain spots for review."),
      io: L("输入：对齐稿 + 用户术语库　→　输出：终稿 + 复核报告", "In: aligned file + user glossary → Out: final transcript + review report"),
      exec: rows(
        { k: L("第一档", "Tier 1"), v: `${claudeCmd} · /multi-asr-merge` },
        promptRow("multi-asr-merge", L("规则书", "Skill")),
        { k: L("第二档", "Tier 2"), v: `${num("p3", "script")} ${p3Args}` },
        { k: L("术语库", "Glossary"), v: L("用户那一本经环境变量注入子进程，不碰共享文件（并发安全）",
                                           "The user's own book is injected via env; no shared file") },
      ),
      params: [
        { k: L("并发闸", "Concurrency gate"), v: L("见下方「融合引擎配置」", "see merge engine settings below") },
        { k: L("超时", "Timeout"), v: L(`${num("p3", "timeoutSec")}s（超时不重试，直接降级）`, `${num("p3", "timeoutSec")}s (no retry; falls back)`) },
        { k: L("放开的工具", "Allowed tools"), v: num("p3", "allowedTools") },
      ],
      onFail: [
        { k: L("降级路", "Fallback"), v: <span style={{ color: semantic.success.text }}>{L(`有 → ${num("ppFlash", "model")}`, `Yes → ${num("ppFlash", "model")}`)}</span> },
        { k: L("什么时候降", "Triggers"), v: L("撞顶（5 小时/周额度）· 并发闸满 · 硬错重试仍败 · 超时 · 冷却期内预判",
                                              "Rate cap · gate full · hard error after retry · timeout · cooldown pre-empt") },
        { k: L("跨机冷却", "Cross-machine cooldown"), v: L("一台撞顶，其余机器读库直接跳过 Claude——每撞一次墙就是一趟白跑的调用",
                                                          "One machine hitting the cap makes the others skip Claude") },
      ],
      dataState: "live",
      recent: p3Recent,
      extra: p3Extra,
      defaultOpen: true,
    },
    {
      id: "narrate",
      title: L("后处理 · 视角转换", "Post · Narrative"),
      summary: L(`claude /pp-narrate → ${num("ppFlash", "model")} · 独立闸 ${ppConc}`,
                 `claude /pp-narrate → ${num("ppFlash", "model")} · own gate ${ppConc}`),
      what: L("把问答体的访谈稿改写成叙述体，保留全部事实与措辞证据，不做概括。",
              "Rewrites the Q&A transcript into narrative form without summarizing."),
      io: L("输入：复核后的稿　→　输出：叙述稿 + 问题清单", "In: reviewed transcript → Out: narrative + issue list"),
      exec: rows(
        { k: L("执行体", "Runs"), v: `${claudeCmd} · /pp-narrate` },
        promptRow("pp-narrate", L("规则书", "Skill")),
        promptRow("shared-qc", L("共用质检", "Shared QC")),
      ),
      params: [
        { k: L("并发闸", "Gate"), v: L(`${ppConc}（engine=pp_narrate，与另两步互不挤占）`, `${ppConc} (engine=pp_narrate, independent)`) },
        { k: L("超时", "Timeout"), v: `${ppTimeout}s` },
      ],
      onFail: [
        { k: L("降级路", "Fallback"), v: <span style={{ color: semantic.success.text }}>{L(`有 → ${flashHead}`, `Yes → ${flashHead}`)}</span> },
        { k: L("什么时候降", "Triggers"), v: L("撞顶或闸满，都立刻降、不排队——用户正等着，等几分钟拿好稿不如立刻拿到稿",
                                              "Rate cap or gate full — both fall back immediately") },
        { k: L("降级参数", "Fallback params"), v: L(`每批 ${num("ppFlash", "narrateBudget")} 字 · 并发 ${num("ppFlash", "narrateConc")}`,
                                                    `${num("ppFlash", "narrateBudget")} chars/batch · conc ${num("ppFlash", "narrateConc")}`) },
        { k: L("丢字护栏", "Drop guard"), v: L(`丢 >${Math.round(g<number>("ppFlash", "dropWarn", 0) * 100)}% 告警 · >${Math.round(g<number>("ppFlash", "dropFatal", 0) * 100)}% 判失败`,
                                               `warn >${Math.round(g<number>("ppFlash", "dropWarn", 0) * 100)}% · fail >${Math.round(g<number>("ppFlash", "dropFatal", 0) * 100)}%`) },
        { k: L("降级也没成", "If fallback fails"), v: L(`退回队列，${num("pp", "retryDelayMin")} 分钟后重试`, `Requeued, retried in ${num("pp", "retryDelayMin")} min`) },
      ],
      dataState: "live",
      recent: ppRecent("narrate"),
    },
    {
      id: "redact",
      title: L("后处理 · 脱敏", "Post · Redact"),
      summary: L(`claude /pp-redact → ${num("ppFlash", "model")} · 独立闸 ${ppConc}`,
                 `claude /pp-redact → ${num("ppFlash", "model")} · own gate ${ppConc}`),
      what: L("把可能暴露受访者身份的信息替换掉（公司 / 人名 / 联系方式 / 地理 / 年限 / 项目），第三方品牌与技术术语原样保留。",
              "Replaces information that could identify the interviewee; third-party brands and technical terms stay."),
      io: L("输入：上一步产物 + 可选保留清单　→　输出：脱敏稿 + 质检报告", "In: previous output + optional keep-list → Out: redacted draft + QC report"),
      exec: rows(
        { k: L("执行体", "Runs"), v: `${claudeCmd} · /pp-redact` },
        promptRow("pp-redact", L("规则书", "Skill")),
        promptRow("shared-qc", L("共用质检", "Shared QC")),
      ),
      params: [
        { k: L("并发闸", "Gate"), v: `${ppConc}（engine=pp_redact）` },
        { k: L("超时", "Timeout"), v: `${ppTimeout}s` },
        { k: L("清单上限", "Keep-list cap"), v: `≤ ${num("pp", "listMaxChars")} ${L("字", "chars")}` },
      ],
      onFail: [
        { k: L("降级路", "Fallback"), v: <span style={{ color: semantic.success.text }}>{L(`有 → ${flashHead}`, `Yes → ${flashHead}`)}</span> },
        { k: L("怎么降的", "How"), v: L("模型只出替换单（行号｜原词｜替换词），替换由程序执行——「只替换、不增删」因此是程序事实，不是模型自觉",
                                        "The model only emits a replacement list; the program applies it") },
        { k: L("降级参数", "Fallback params"), v: L(`每批 ${num("ppFlash", "redactBudget")} 字 · 并发 ${num("ppFlash", "redactConc")} · 扫 ${num("ppFlash", "redactPasses")} 遍 · 复核 ${g<boolean>("ppFlash", "redactReview", false) ? L("开", "on") : L("关", "off")}`,
                                                    `${num("ppFlash", "redactBudget")} chars/batch · conc ${num("ppFlash", "redactConc")} · ${num("ppFlash", "redactPasses")} pass`) },
        { k: L("降级也没成", "If fallback fails"), v: L(`退回队列，${num("pp", "retryDelayMin")} 分钟后重试`, `Requeued, retried in ${num("pp", "retryDelayMin")} min`) },
      ],
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
        {/* 指纹口径：必须写清楚它证明的是什么、不证明什么。写成一个对勾就是在骗自己。 */}
        <div style={{ padding: `${space.s3}px ${space.s5}px`, borderBottom: `1px solid ${semantic.border.default}`, background: semantic.surface.page, ...mono(11), ...dim, lineHeight: 1.7 }}>
          {L("任务镜像", "Task image")} <span style={{ color: semantic.text.secondary }}>{wf.imageTag ?? L("未配置", "not set")}</span>
          {"　·　"}
          {L("下面的提示词指纹取自派单前台这一份。转录与后处理跑在 Fly 任务镜像上，push 只重部署前台、不重建镜像——两边对不对得上，这里判断不了。",
             "Prompt fingerprints below come from the dispatcher's copy. Transcription runs on the Fly task image, which push does not rebuild.")}
        </div>
        {nodes.map((n, i) => <NodeRow key={n.id} n={n} last={i === nodes.length - 1} />)}
      </SectionShell>
      {prompt && <PromptViewer id={prompt} onClose={() => setPrompt(null)} />}
    </>
  );
}

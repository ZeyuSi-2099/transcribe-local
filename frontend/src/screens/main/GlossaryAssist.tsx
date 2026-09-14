// 术语库「协助建库」的右栏部件与状态机（设计交付《Transcribe术语库交互优化》）。
//
// 拆出来的理由：GlossaryPage 已经三百多行且管着一套编辑缓冲，把起草/体检的状态再塞进去
// 会让两套逻辑纠缠。这里只管「产出建议」，落库仍由 GlossaryPage 的保存按钮负责——
// **本文件不调用任何写库接口**。
//
// 状态机（README §Interactions）：
//   empty ──开始起草──▶ drafting ──跑完/停止──▶ done ──检查这本库──▶ checking ──收起──▶ done
//
// ⚠️ 文案红线：面向用户的字里不许出现 AI / 大模型 / 智能 等（bannedAiPhrases.ts 有守卫）。
//    合规写法：「帮我建一本」「开始起草」「检查这本库」「正在核实「X」」。
import { useErrText } from "../../lib/userErrors";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import * as T from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import { Button } from "../../components/Button";
import { draftGlossaryStream, checkGlossaryStream, type DraftItem, type GlossaryChecks } from "../../lib/api";
import { parse, GLOSSARY_MAX_CHARS } from "../../lib/glossary";

/** 起草节奏：队列里有货就按这个间隔往编辑器里落一条。
 *  走流式之后模型自带节奏（一条一两秒），这个值只管「突发几条时别一次刷一屏」，
 *  所以从设计稿的演示值 520ms 收到 200ms——否则缓冲会越积越多，看着像卡住。
 *  做成可注入是为了测试能调小——用 fake timers 配 userEvent 会互相卡死。 */
export const WRITE_INTERVAL_MS = 200;

export type AssistPhase = "idle" | "drafting" | "checking";

/** 网络/网关类错误的原文（「glossary check 502」「[Errno -2] Name or service not known」）
 *  对用户毫无意义，翻成人话并明说库没被动过。
 *
 *  ⚠️ 后端有码的那些（引擎不可用等）先走 `useErrText` 出界面语言的文案（2026-08-30）——
 *  只有真正的网络/网关噪音才落到 fallback。此前后端返回的中文 detail 是**原样贴出去**的，
 *  德语用户会看到一句中文。 */
function humanError(e: unknown, fallback: string, errText: (e: unknown) => string): string {
  if ((e as { code?: string })?.code) return errText(e);
  const raw = String(e instanceof Error ? e.message : e);
  return /\b[45]\d\d\b|Errno|Failed to fetch|NetworkError|Name or service/i.test(raw) ? fallback : raw;
}

/** 把一项渲染成术语库文本的一行。
 *  缺口不进正文（调用方应先分流掉），这里兜底成空串免得漏进编辑器。
 *  释义为空时仍写出 `术语 ｜`——竖线要留着，那是「等你填」的锚点。 */
export function itemToLine(it: DraftItem): string {
  if ("cat" in it) return `## ${it.cat}`;
  if ("gap" in it) return "";
  return it.def ? `${it.term} ｜ ${it.def}` : `${it.term} ｜ `;
}

interface Props {
  /** 当前编辑器全文（受控于 GlossaryPage） */
  text: string;
  /** 用 React setter 类型：流式写入要用函数式更新，否则连写几条会互相覆盖 */
  setText: React.Dispatch<React.SetStateAction<string>>;
  /** 起草/体检期间禁用保存等操作 */
  onPhaseChange?: (p: AssistPhase) => void;
  /** 当前库 id：起草/检查产出的缺口要随这本库落表 */
  glossaryId?: string | null;
  /** 大纲正文。输入区在**主区**（OutlineIntake）、起草按钮在这里，所以它由 GlossaryPage 持有 */
  outline: string;
  /** 主区此刻是不是正借给大纲进料 */
  intakeOpen: boolean;
  onOpenIntake: () => void;
  /** 起草一开始就切回编辑器——术语要落在用户刚贴大纲的那块地方 */
  onCloseIntake: () => void;
  /** 一轮跑完通知外层重新拉缺口，并告诉它这轮是「起草完」还是「检查完」（决定引导语） */
  onTodoRefresh?: (scenario: "drafted" | "checked") => void;
  onToast: (msg: string, undo?: () => void) => void;
  /** 仅测试用：写入间隔（ms） */
  writeIntervalMs?: number;
}

/** 供外部把用户直接送到起草框（空态屏的「贴一份访谈大纲」入口） */
export interface GlossaryAssistHandle {
  focusOutline(): void;
}

export const GlossaryAssist = forwardRef<GlossaryAssistHandle, Props>(function GlossaryAssist(
  { text, setText, onPhaseChange, onToast, glossaryId, outline, intakeOpen, onOpenIntake, onCloseIntake,
    onTodoRefresh, writeIntervalMs = WRITE_INTERVAL_MS }: Props,
  ref,
) {
  const L = useL();
  const errText = useErrText();
  const humanError2 = (e: unknown, fb: string) => humanError(e, fb, errText);
  const [phase, setPhase] = useState<AssistPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  /** 起草完一轮后收起输入框——留一个小入口可再展开（防误点重复追加） */
  const [drafterOpen, setDrafterOpen] = useState(true);

  // 起草进行中的状态
  const [queue, setQueue] = useState<DraftItem[]>([]);
  const [written, setWritten] = useState(0);
  const [clock, setClock] = useState(0);
  /** 流还开着＝模型还在吐；写入指针追平队列不等于结束，得等这个也落下来 */
  const [streaming, setStreaming] = useState(false);
  const stopRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  // 体检结果与三框游标
  const [checks, setChecks] = useState<GlossaryChecks | null>(null);
  const [checking, setChecking] = useState(false);
  const [cursor, setCursor] = useState({ add: 0, edit: 0, del: 0 });
  const [handled, setHandled] = useState<Record<string, true>>({});

  /** 起草期间已经在库里的术语。库里已有 124 条时再拿同一份大纲起草，模型并不知道
   *  哪些收过，会把「尚界」再产出一遍——追加式写入就成了重复堆积。开跑时快照一份，
   *  每写入一条补进去（本轮内部重复也一并挡掉）。 */
  const seenRef = useRef<Set<string>>(new Set());
  const skippedRef = useRef(0);

  useEffect(() => { onPhaseChange?.(phase); }, [phase, onPhaseChange]);

  useImperativeHandle(ref, () => ({
    focusOutline() {
      setDrafterOpen(true);
      onOpenIntake();          // 输入区在主区，把主区切过去就是「送到起草框」
    },
  }));

  // ── 起草：从缓冲队列按节奏逐条写入。队列由流式回调持续追加，所以这个 effect 会反复重跑 ──
  useEffect(() => {
    if (phase !== "drafting" || written >= queue.length) return;
    const t = window.setTimeout(() => {
      if (stopRef.current) return;
      const it = queue[written];
      const term = "term" in it ? it.term.trim() : "";
      // 库里已经有这个词就跳过——追加式写入下，不跳就是原地堆重复
      if (term && seenRef.current.has(term)) {
        skippedRef.current += 1;
        setWritten((n) => n + 1);
        return;
      }
      if (term) seenRef.current.add(term);
      const line = itemToLine(it);
      setText((prev) => (prev.trim() ? `${prev.replace(/\n+$/, "")}\n${line}` : line));
      setWritten((n) => n + 1);
    }, writeIntervalMs);
    return () => window.clearTimeout(t);
    // setText 每次渲染都是新函数，不进依赖；只跟写入指针走
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, written, queue, writeIntervalMs]);

  // 写完自动收尾。⚠️ !streaming 不能省：流式下写入指针会反复追平队列（模型还没吐下一条），
  // 少了这个判断，第一条写完就会被当成「起草结束」。
  useEffect(() => {
    if (phase === "drafting" && !streaming && queue.length > 0 && written >= queue.length) {
      setPhase("idle");
      setDrafterOpen(false);
      onTodoRefresh?.("drafted");
      const skipped = skippedRef.current;
      onToast(skipped > 0
        ? L.t("已写入编辑器，跳过 {0} 条库里已有的 —— 还没保存，删改都随你",
              "Written into the editor; {0} already in your glossary were skipped — not saved yet, edit freely", skipped)
        : L("已写入编辑器，还没保存 —— 删改都随你", "Written into the editor, not saved yet — edit freely"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, streaming, written, queue.length]);

  // 计时器：起草和体检共用（两边都要等一两分钟，都得有个在走的数字）
  useEffect(() => {
    if (phase !== "drafting" && !checking) return;
    const id = window.setInterval(() => setClock((c) => c + 1), 1000);
    return () => window.clearInterval(id);
  }, [phase, checking]);

  const startDraft = async () => {
    if (!outline.trim()) {
      onToast(L("先贴一份访谈大纲", "Paste an interview outline first"));
      return;
    }
    setError(null);
    onCloseIntake();          // 术语要落在刚才贴大纲的那块地方，所以主区先切回编辑器
    // 快照现有术语：这一轮的去重基准
    seenRef.current = new Set(
      parse(text).lines.filter((l) => l.kind === "entry" && l.term).map((l) => l.term as string),
    );
    skippedRef.current = 0;
    stopRef.current = false;
    abortRef.current = new AbortController();
    setClock(0);
    setWritten(0);
    setQueue([]);
    setStreaming(true);
    setPhase("drafting");
    let got = 0;
    try {
      await draftGlossaryStream(outline, (it) => {
        got += 1;
        // 缺口属待办层，**绝不写进编辑器正文**（正文会作为硬证据注入转录引擎）——
        // 后端已随库落表，外层稍后重新拉一次显示在右栏
        if ("gap" in it) return;
        setQueue((q) => [...q, it]);       // 进缓冲，由写入 effect 按节奏落进编辑器
      }, abortRef.current.signal, glossaryId ?? undefined);
      if (stopRef.current) return;
      if (got === 0) {
        setPhase("idle");
        setError(L("这份材料里没有需要收的词——里面基本都是常规词。换一份材料试试。",
                   "Nothing here needs a glossary entry — it is all everyday wording. Try another document."));
      }
    } catch (e) {
      if (stopRef.current) return;         // 自己 abort 的，不是错误
      setPhase("idle");
      setError(humanError2(e, L("这次没起草成，稍后再试一次。你的库没有任何改动。",
                               "The draft did not go through. Try again in a moment — your glossary is untouched.")));
    } finally {
      setStreaming(false);
    }
  };

  const stopDraft = () => {
    stopRef.current = true;
    abortRef.current?.abort();             // 真的掐断请求：模型还没跑完，剩下的 token 不烧了
    setStreaming(false);
    setPhase("idle");
    onToast(L("停下了，已写的留着", "Stopped — what was written stays"));
  };

  const runCheck = async () => {
    setError(null);
    setClock(0);
    setChecking(true);
    try {
      const r = await checkGlossaryStream(text, glossaryId ?? undefined);
      setChecks(r);
      setCursor({ add: 0, edit: 0, del: 0 });
      setHandled({});
      setPhase("checking");
      onTodoRefresh?.("checked");
    } catch (e) {
      setError(humanError2(e, L("这次没检查成，稍后再试一次。你的库没有任何改动。",
                               "The check did not go through. Try again in a moment — your glossary is untouched.")));
    } finally {
      setChecking(false);
    }
  };

  // ── 建议队列的有效性：每次渲染重算，不缓存 ──
  // 该补：目标词还不在库里（用户自己打了就该消失）；该改/该删：目标词仍在库里
  const linesNow = parse(text).lines;
  const termsNow = new Set(linesNow.filter((l) => l.kind === "entry").map((l) => l.term!));
  const liveAdd = (checks?.add ?? []).filter((x) => !termsNow.has(x.term) && !handled["a:" + x.term]);
  const liveEdit = (checks?.edit ?? []).filter((x) => termsNow.has(x.term) && !handled["e:" + x.term]);
  const liveDel = (checks?.del ?? []).filter((x) => termsNow.has(x.term) && !handled["d:" + x.term]);

  const snapshot = () => {
    const before = text;
    const beforeHandled = handled;
    return () => { setText(before); setHandled(beforeHandled); };
  };

  const acceptAdd = (x: { term: string; def: string; cat: string }) => {
    const undo = snapshot();
    const line = `${x.term} ｜ ${x.def}`;
    // 插到目标分类的末尾；没有该分类就新建一段
    const lines = text.split("\n");
    const catIdx = lines.findIndex((l) => l.trim().replace(/^#+\s*/, "") === x.cat && l.trim().startsWith("##"));
    if (catIdx < 0) {
      setText(`${text.replace(/\n+$/, "")}\n\n## ${x.cat}\n${line}`);
    } else {
      let end = catIdx + 1;
      while (end < lines.length && !lines[end].trim().startsWith("##")) end++;
      // 回退到该段最后一个非空行之后
      let at = end;
      while (at > catIdx + 1 && !lines[at - 1].trim()) at--;
      lines.splice(at, 0, line);
      setText(lines.join("\n"));
    }
    setHandled((h) => ({ ...h, ["a:" + x.term]: true }));
    onToast(L.t("加上了「{0}」", "Added 「{0}」", x.term), undo);
  };

  const acceptEdit = (x: { term: string; def: string }) => {
    const undo = snapshot();
    setText(text.split("\n").map((l) => {
      const [t] = l.split("｜");
      return t?.trim() === x.term ? `${x.term} ｜ ${x.def}` : l;
    }).join("\n"));
    setHandled((h) => ({ ...h, ["e:" + x.term]: true }));
    onToast(L.t("改写了「{0}」", "Rewrote 「{0}」", x.term), undo);
  };

  const acceptDel = (x: { term: string }) => {
    const undo = snapshot();
    setText(text.split("\n").filter((l) => l.split("｜")[0]?.trim() !== x.term).join("\n"));
    setHandled((h) => ({ ...h, ["d:" + x.term]: true }));
    onToast(L.t("删掉了「{0}」", "Removed 「{0}」", x.term), undo);
  };

  const ignore = (kind: "a" | "e" | "d", term: string) =>
    setHandled((h) => ({ ...h, [kind + ":" + term]: true }));

  const card: React.CSSProperties = {
    background: T.semantic.surface.raised,
    border: `1px solid ${T.semantic.accent.bgSoft}`,
    borderRadius: T.radius.md,
    padding: `${T.space.s3}px ${T.space.s4}px`,
  };
  const label: React.CSSProperties = { ...T.type.label, color: T.semantic.accent.text };

  // ── 体检三框 ──
  if (phase === "checking" && checks) {
    const boxes = [
      { key: "add" as const, title: L("该补", "To add"), items: liveAdd },
      { key: "edit" as const, title: L("该改", "To rewrite"), items: liveEdit },
      { key: "del" as const, title: L("该删", "To remove"), items: liveDel },
    ];
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: T.space.s3 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ ...T.type.label, color: T.semantic.text.muted }}>{L("检查结果", "Check results")}</span>
          <button onClick={() => setPhase("idle")} style={{ ...T.type.bodySm, color: T.semantic.accent.text, background: "none", border: "none", cursor: "pointer" }}>
            {L("收起", "Close")}
          </button>
        </div>
        {boxes.map((b) => {
          const n = b.items.length;
          const i = n ? cursor[b.key] % n : 0;
          const cur = n ? b.items[i] : null;
          return (
            <div key={b.key} style={card}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: T.space.s2 }}>
                <span style={label}>{b.title}</span>
                {n > 0 && (
                  <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <button aria-label={L("上一条", "Previous")} onClick={() => setCursor((c) => ({ ...c, [b.key]: (c[b.key] - 1 + n) % n }))}
                      style={{ background: "none", border: "none", cursor: "pointer", color: T.semantic.text.muted }}>‹</button>
                    <span style={{ fontFamily: T.fonts.mono, fontSize: 11, fontVariantNumeric: "tabular-nums", color: T.semantic.text.muted }}>{i + 1} / {n}</span>
                    <button aria-label={L("下一条", "Next")} onClick={() => setCursor((c) => ({ ...c, [b.key]: (c[b.key] + 1) % n }))}
                      style={{ background: "none", border: "none", cursor: "pointer", color: T.semantic.accent.text }}>›</button>
                  </span>
                )}
              </div>
              {!cur ? (
                <div style={{ ...T.type.bodySm, color: T.semantic.success.text }}>✓ {L("都过完了", "All done")}</div>
              ) : (
                <>
                  {b.key === "add" && (
                    <>
                      <div style={{ ...T.type.bodySm, color: T.semantic.text.primary }}>
                        <strong>{(cur as any).term}</strong> ｜ {(cur as any).def}
                      </div>
                      <div style={{ ...T.type.caption, color: T.semantic.text.muted, marginTop: 4 }}>
                        {L.t("加到 ## {0}", "into ## {0}", (cur as any).cat)}
                      </div>
                    </>
                  )}
                  {b.key === "edit" && (
                    <>
                      <div style={{ ...T.type.caption, color: T.semantic.text.muted, marginBottom: 4 }}>{(cur as any).why}</div>
                      {/* 不给原文，用户没法判断该不该采纳——原释义划掉、新释义在下 */}
                      {(() => {
                        const oldDef = linesNow.find((l) => l.kind === "entry" && l.term === (cur as any).term)?.meaning;
                        return oldDef ? (
                          <div style={{ ...T.type.bodySm, color: T.semantic.text.ghost, textDecoration: "line-through", marginBottom: 4 }}>
                            {oldDef}
                          </div>
                        ) : null;
                      })()}
                      <div style={{ ...T.type.bodySm, color: T.semantic.text.primary }}>
                        <strong>{(cur as any).term}</strong> ｜ {(cur as any).def}
                      </div>
                    </>
                  )}
                  {b.key === "del" && (
                    <>
                      <div style={{ ...T.type.caption, color: T.semantic.text.muted, marginBottom: 4 }}>{(cur as any).why}</div>
                      <div style={{ ...T.type.bodySm, color: T.semantic.text.primary }}><strong>{(cur as any).term}</strong></div>
                    </>
                  )}
                  <div style={{ display: "flex", gap: T.space.s2, marginTop: T.space.s3 }}>
                    <button
                      onClick={() => b.key === "add" ? acceptAdd(cur as any) : b.key === "edit" ? acceptEdit(cur as any) : acceptDel(cur as any)}
                      style={{ flex: 1, ...T.type.bodySm, fontWeight: 600, color: T.semantic.text.onAccent, background: T.semantic.accent.fill, border: "none", borderRadius: T.radius.sm, padding: `${T.space.s2}px`, cursor: "pointer" }}
                    >{L("采纳", "Accept")}</button>
                    <button
                      onClick={() => ignore(b.key[0] as "a" | "e" | "d", (cur as any).term)}
                      style={{ flex: 1, ...T.type.bodySm, color: T.semantic.text.secondary, background: T.semantic.surface.raised, border: `1px solid ${T.semantic.border.strong}`, borderRadius: T.radius.sm, padding: `${T.space.s2}px`, cursor: "pointer" }}
                    >{L("忽略", "Ignore")}</button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // ── 起草中：进度面板 ──
  if (phase === "drafting") {
    // 流式期间总数还不知道（模型边想边吐），只报已写条数；等流收完再显示 n/N 和进度条。
    // 不做「假进度条」——写一个猜出来的百分比只会让人以为快好了。
    const total = queue.length;
    const mm = String(Math.floor(clock / 60));
    const ss = String(clock % 60).padStart(2, "0");
    return (
      <div style={{ ...card, display: "flex", flexDirection: "column", gap: T.space.s3 }}>
        <div style={{ display: "flex", alignItems: "center", gap: T.space.s2 }}>
          <span style={{ width: 6, height: 6, borderRadius: 999, background: T.semantic.accent.brand, animation: "pulseDot 1.2s ease-in-out infinite" }} aria-hidden />
          <span style={{ ...T.type.label, color: T.semantic.accent.text }}>{L("正在起草", "Drafting")}</span>
          <span style={{ marginLeft: "auto", fontFamily: T.fonts.mono, fontSize: 11, fontVariantNumeric: "tabular-nums", color: T.semantic.text.muted }}>{mm}:{ss}</span>
        </div>
        <div style={{ ...T.type.bodySm, color: T.semantic.text.secondary }}>
          {total === 0
            ? L("正在读访谈大纲 —— 通常要两三分钟，可以先去忙别的",
                "Reading the outline — usually two to three minutes, feel free to do something else")
            : streaming
              ? L.t("已写入 {0} 条，还在继续…", "{0} written so far…", String(written))
              : L.t("已写 {0} 条，共 {1} 条", "{0} of {1} written", String(written), String(total))}
        </div>
        {total > 0 && !streaming && (
          <div style={{ height: 3, background: T.semantic.surface.sunken, borderRadius: 999, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${(written / total) * 100}%`, background: T.semantic.accent.brand, transition: "width 180ms cubic-bezier(.2,0,0,1)" }} />
          </div>
        )}
        <div style={{ ...T.type.caption, color: T.semantic.text.muted }}>
          {L("写完你直接在左边改：删掉不要的行、改释义。按「保存术语库」才会存。",
             "Edit on the left when it finishes — delete lines, reword them. Nothing is stored until you save.")}
        </div>
        <Button secondary full onClick={stopDraft}>{L("停止（已写的留着）", "Stop (keep what is written)")}</Button>
      </div>
    );
  }

  // ── 常规：起草区块 + 检查入口 ──
  const p = parse(text);
  const hasEntries = p.entryCount > 0;
  const nearFull = p.totalChars > GLOSSARY_MAX_CHARS * 0.9;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: T.space.s3 }}>
      {!drafterOpen ? (
        <button
          onClick={() => setDrafterOpen(true)}
          style={{ ...T.type.bodySm, color: T.semantic.accent.text, background: "none", border: "none", cursor: "pointer", textAlign: "left", padding: `${T.space.s1}px 0` }}
        >
          {L("再起草一段 →", "Draft another section →")}
        </button>
      ) : (
      <div style={{ ...card, boxShadow: T.shadow.md }}>
        {/* 空库与已有库说的不是一件事：空库是「建」，已有库是「补」。
            都写成「帮我建一本」的话，手上那本 124 条的库会让人以为要被重来一遍。 */}
        <div style={{ ...T.type.bodySm, fontWeight: 600, color: T.semantic.text.primary }}>
          {hasEntries ? L("再补一批", "Add another batch") : L("帮我建一本", "Draft one for me")}
        </div>
        <div style={{ ...T.type.caption, color: T.semantic.text.secondary, marginTop: 4, lineHeight: 1.55 }}>
          {hasEntries
            ? L.t("新术语接在现有 {0} 条后面；已经有的不会重复添加。",
                  "New terms go after the {0} you already have; anything already in the glossary is skipped.", p.entryCount)
            : L("给一份访谈大纲，我们按分类写成术语 ｜ 含义，直接落到编辑器里。",
                "Give us an interview outline; we write it into the editor as term ｜ meaning, sorted into categories.")}
        </div>

        {/* 输入区在主区（约 780px 宽），不在这张 300px 的卡里——一份三千字的大纲
            在这里只看得见三四行，「读出来给你看一眼」就等于没做到。
            空大纲时按钮**故意保持可点**：点一下 toast「先给一份大纲」，比一个不给理由的
            死按钮清楚（GlossaryAssist.test.tsx 守着这个交互，别改成 disabled）。 */}
        {intakeOpen || outline.trim() ? (
          <>
            <button
              onClick={startDraft}
              style={{
                width: "100%", marginTop: T.space.s3, padding: T.space.s3, borderRadius: T.radius.sm, border: "none",
                cursor: "pointer", ...T.type.bodySm, fontWeight: 600,
                color: outline.trim() ? T.semantic.text.onAccent : T.semantic.text.secondary,
                background: outline.trim() ? T.semantic.accent.fill : T.semantic.surface.sunken,
              }}
            >{hasEntries ? L("补充起草", "Draft the additions") : L("开始起草", "Start drafting")}</button>
            {!intakeOpen && (
              <button
                onClick={onOpenIntake}
                style={{
                  width: "100%", marginTop: T.space.s2, padding: T.space.s2, borderRadius: T.radius.sm,
                  border: `1px solid ${T.semantic.border.strong}`, background: "transparent",
                  cursor: "pointer", ...T.type.bodySm, color: T.semantic.text.primary,
                }}
              >{L("查看大纲", "View the outline")}</button>
            )}
          </>
        ) : (
          <button
            onClick={onOpenIntake}
            style={{
              width: "100%", marginTop: T.space.s3, padding: T.space.s3, borderRadius: T.radius.sm, border: "none",
              cursor: "pointer", ...T.type.bodySm, fontWeight: 600,
              color: T.semantic.text.onAccent, background: T.semantic.accent.fill,
            }}
          >{L("选文件 / 贴大纲", "Choose a file or paste")}</button>
        )}
      </div>
      )}

      {/* 体检也要等一两分钟（同一个引擎），所以给它和起草一样的活动信号：
          脉冲点 + 计时 + 时间预期。此前这里只有一个禁用的灰按钮，看着像卡死了。 */}
      {p.entryCount > 0 && (checking ? (
        <div style={{ ...card, display: "flex", flexDirection: "column", gap: T.space.s2 }}>
          <div style={{ display: "flex", alignItems: "center", gap: T.space.s2 }}>
            <span style={{ width: 6, height: 6, borderRadius: 999, background: T.semantic.accent.brand, animation: "pulseDot 1.2s ease-in-out infinite" }} aria-hidden />
            <span style={{ ...T.type.label, color: T.semantic.accent.text }}>{L("正在检查", "Checking")}</span>
            <span style={{ marginLeft: "auto", fontFamily: T.fonts.mono, fontSize: 11, fontVariantNumeric: "tabular-nums", color: T.semantic.text.muted }}>
              {String(Math.floor(clock / 60))}:{String(clock % 60).padStart(2, "0")}
            </span>
          </div>
          <div style={{ ...T.type.bodySm, color: T.semantic.text.secondary }}>
            {L("正在逐条核对这本库 —— 通常要一两分钟。这一步不会改动你的库。",
               "Going through the glossary entry by entry — usually a minute or two. Nothing is changed by this step.")}
          </div>
        </div>
      ) : (
        <Button secondary full onClick={runCheck}>
          {L("检查这本库", "Check this glossary")}
        </Button>
      ))}

      {nearFull && (
        <div style={{ ...T.type.caption, color: T.semantic.warning.text }}>
          {L("已接近 8000 字上限，起草可能写不全——先精简或另建一本。",
             "Close to the 8,000-character limit — trim it or start a second glossary before drafting.")}
        </div>
      )}

      {error && (
        <div style={{ ...T.type.caption, color: T.semantic.accent.text, lineHeight: 1.6 }}>{error}</div>
      )}
    </div>
  );
});

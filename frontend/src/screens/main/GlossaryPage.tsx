import { useErrText } from "../../lib/userErrors";
import { useUnsavedGuard } from "../../lib/unsavedGuard";
import { useEffect, useRef, useState } from "react";
import { GlossaryEditor, type GlossaryEditorHandle } from "../../components/GlossaryEditor";
import { Button } from "../../components/Button";
import { EmptyState } from "../EmptyState";
import { parse, GLOSSARY_MAX_CHARS, MEANING_MAX_CHARS } from "../../lib/glossary";
import * as T from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import type { Glossary } from "../../lib/api";
import { GlossaryAssist, type AssistPhase, type GlossaryAssistHandle } from "./GlossaryAssist";
import { prefersReducedMotion } from "../../lib/reducedMotion";
import { GlossaryTodo, type TodoScenario } from "./GlossaryTodo";
import { OutlineIntake } from "./OutlineIntake";
import { listGaps, setGapStatus, type Gap } from "../../lib/api";

const MAX_GLOSSARIES = 20;
const MAX_NAME = 40;

interface GlossaryPageProps {
  /** `null` = 还没取回来（**不是「一本都没有」**）。两者混用的话，
   *  有三本库的人一进这一页会先看到「还没有术语库 · 新建第一本」，再翻成列表。 */
  glossaries: Glossary[] | null;
  onCreate: (name: string) => Promise<Glossary | void>;
  onUpdate: (id: string, name: string, language: string | null, content: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

// 新建库的内容模板：教格式，中性示例（非任何真实客户术语）。
const SAMPLE = `## 公司与产品
GMV ｜ 成交总额 Gross Merchandise Volume，平台一定时期的总交易额
SKU ｜ 库存单位 Stock Keeping Unit，商品的最小管理单元

## 人名 / 职务
（在这里补充访谈里出现的人名、公司、职务，帮定稿校正同音字）`;

const BookIcon = ({ size = 24 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
  </svg>
);

export function GlossaryPage({ glossaries: glossariesOrNull, onCreate, onUpdate, onDelete }: GlossaryPageProps) {
  const L = useL();
  const errText = useErrText();
  const loaded = glossariesOrNull != null;
  const glossaries = glossariesOrNull ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 选中收敛：无选中或选中已不在列表（删除/初次加载）→ 落到第一本
  useEffect(() => {
    if (glossaries.length === 0) { setSelectedId(null); return; }
    setSelectedId((cur) => (cur && glossaries.some((g) => g.id === cur)) ? cur : glossaries[0].id);
  }, [glossaries]);

  const selected = glossaries.find((g) => g.id === selectedId) ?? null;

  // 编辑缓冲：选中库变化（切库 / 保存后 server 回写）→ 重置为该库已存值（dirty 清零）
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [savedName, setSavedName] = useState("");
  const [savedText, setSavedText] = useState("");
  useEffect(() => {
    setName(selected?.name ?? "");
    setText(selected?.content ?? "");
    setSavedName(selected?.name ?? "");
    setSavedText(selected?.content ?? "");
    // 仅在选中库的「身份/已存值」变化时重置；编辑过程中（text 本地变）不触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.name, selected?.content]);

  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  // toast：起草前只有「已保存」一种，现在还要承载协助建库的采纳/撤销
  const [toast, setToast] = useState<{ msg: string; undo?: () => void } | null>(null);
  const [assistPhase, setAssistPhase] = useState<AssistPhase>("idle");
  const [confirmDel, setConfirmDel] = useState(false);
  // 待办层的缺口：与库正文分表存，所以要单独拉。切库即重拉。
  const [gaps, setGaps] = useState<Gap[]>([]);
  // 场景只影响引导语与默认展开态：进来就有的算「上次留下的」，跑完一轮才算刚产出的
  const [todoScenario, setTodoScenario] = useState<TodoScenario>("leftover");
  // 轮次：每跑完一轮 +1。只用来重挂载面板（重播入场动画、把展开态按新场景重设）——
  // 连点两次「检查这本库」时 scenario 都是 checked，光看它认不出「又出了一轮结果」。
  const [todoRound, setTodoRound] = useState(0);
  // 切库时协助建库整块重建（见下方 key），它不会再回调 onPhaseChange —— 这里补一刀，
  // 否则起草中途切库会把 phase 永久卡在 drafting，保存按钮再也点不动。
  useEffect(() => { setConfirmDel(false); setSaveErr(null); setAssistPhase("idle"); }, [selectedId]);

  // 切库重拉缺口。用 cancelled 兜住竞态：连点两本库时，先发的请求可能后回来
  useEffect(() => {
    let cancelled = false;
    setGaps([]);
    setTodoScenario("leftover");
    if (!selectedId) return;
    listGaps(selectedId).then((g) => { if (!cancelled) setGaps(g); }).catch(() => {});
    return () => { cancelled = true; };
  }, [selectedId]);

  const refreshGaps = (scenario: TodoScenario) => {
    if (!selectedId) return;
    setTodoScenario(scenario);
    setTodoRound((n) => n + 1);
    listGaps(selectedId).then(setGaps).catch(() => {});
  };

  // 跑完一轮把右栏滚到面板顶部（设计 §6）：这一轮的产出在右栏中段，
  // 库长一点它就在首屏外，不滚的话用户以为「跑完了什么也没出」。
  useEffect(() => {
    if (todoRound === 0) return;
    const el = todoRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    }
  }, [todoRound]);

  const onSetGap = async (gapId: string, status: Gap["status"]) => {
    if (!selectedId) return;
    const before = gaps;
    // 先动本地：这一步没有网络等待的必要，失败再回滚
    setGaps((gs) => gs.map((g) => (g.id === gapId ? { ...g, status } : g)));
    try {
      setGaps(await setGapStatus(selectedId, gapId, status));
      if (status === "handled") {
        showToast(L("知道了 · 收进「已处理」", "Got it · moved to Handled"),
                  () => { setGaps(before); void setGapStatus(selectedId, gapId, "open"); });
      }
    } catch {
      setGaps(before);
    }
  };
  const timerRef = useRef<number | undefined>(undefined);
  const editorRef = useRef<GlossaryEditorHandle>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const todoRef = useRef<HTMLDivElement>(null);
  const assistRef = useRef<GlossaryAssistHandle>(null);
  // 大纲进料：正文提到这一层，因为输入区在**主区**、起草按钮在**右栏**，两边都要读它。
  // intake=true 时主区暂借给大纲；编辑器内容一个字不动，「← 返回编辑器」随时切回。
  const [outline, setOutline] = useState("");
  const [intake, setIntake] = useState(false);
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);
  // 切库时退出进料并丢掉大纲：换了一本库，手上那份材料多半不是给它的，
  // 留着的话会出现「在 B 库点起草、用的却是 A 库的大纲」。
  useEffect(() => { setIntake(false); setOutline(""); }, [selectedId]);


  const p = parse(text);
  const nameOk = name.trim().length > 0 && name.trim().length <= MAX_NAME;
  const dirty = name !== savedName || text !== savedText;
  const canSave = dirty && nameOk && !p.overTotal && !saving && selectedId != null && assistPhase !== "drafting";

  const showToast = (msg: string, undo?: () => void) => {
    setToast({ msg, undo });
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setToast(null), 3600);
  };

  const save = async (): Promise<boolean> => {
    if (!dirty) return true;
    if (!canSave || !selectedId) return false;
    setSaving(true); setSaveErr(null);
    try {
      await onUpdate(selectedId, name.trim(), null, text);
      setSavedName(name.trim()); setSavedText(text);   // 即时反馈；父刷新列表后 effect 再对齐
      // 保存**绝不拦截、绝不二次确认**（业务方硬要求：可以一条不补直接保存）。
      // 但待办还剩多少要顺口说一句，否则用户容易忘了自己还欠着几行。
      const openGaps = gaps.filter((g) => g.status === "open").length;
      showToast(
        p.blankEntries.length + openGaps > 0
          ? L.t("已保存 · 还有 {0} 个词等你填、{1} 条缺口",
                "Saved · {0} terms and {1} gaps still open",
                String(p.blankEntries.length), String(openGaps))
          : L("已保存 · 下次选它转录即生效", "Saved · applies when you pick it for a transcript"));
      return true;
    } catch (e) {
      // 失败（重名 409 / 网络等）：保持 dirty，显示原因让用户改
      setSaveErr(errText(e));
      return false;
    } finally {
      setSaving(false);
    }
  };
  // 切页 / 关窗口前拦一下（与脱敏清单页同一套，见 lib/unsavedGuard.ts）
  useUnsavedGuard(dirty, save);

  /** intent="draft"：从空态屏的「贴一份访谈大纲」进来的——建完直接把光标送进起草框，
   *  而不是照常聚焦库名。少一次「建好了然后呢」的停顿。 */
  const handleNew = async (intent?: "draft") => {
    // 避免与已有库重名（后端重名校验会 409）：「未命名」「未命名 2」… 取第一个不冲突的名
    const base = L("未命名", "Untitled");
    const taken = new Set(glossaries.map((g) => g.name));
    let candidate = base;
    for (let i = 2; taken.has(candidate); i++) candidate = `${base} ${i}`;
    try {
      const g = await onCreate(candidate);
      if (g) {
        setSelectedId(g.id);
        setTimeout(() => {
          if (intent === "draft") { assistRef.current?.focusOutline(); return; }
          // 新建后聚焦库名、全选，便于直接改名
          nameRef.current?.focus(); nameRef.current?.select();
        }, 0);
      }
    } catch (e) {
      // 兜底：万一仍失败（并发抢名等），给可见提示而非静默
      setSaveErr(errText(e));
    }
  };

  const handleDelete = async () => {
    if (!confirmDel) { setConfirmDel(true); return; }
    if (selectedId) await onDelete(selectedId);
    setConfirmDel(false);
  };

  const left = GLOSSARY_MAX_CHARS - p.totalChars;
  const countColor = p.overTotal ? T.semantic.accent.text : p.near ? T.semantic.warning.text : T.semantic.success.text;
  const countHint = p.overTotal
    ? L.t("已超出 {0} 字，请精简后再保存", "{0} over the limit — trim before saving", -left)
    : p.near
    ? L.t("接近上限，还剩 {0} 字", "Approaching the limit — {0} left", left)
    : L.t("还可输入 {0} 字", "{0} characters left", left);

  const header = (
    <div style={{ flex: "0 0 auto", marginBottom: T.space.s6 }}>
      <h1 style={{ ...T.type.h1, color: T.semantic.text.primary, margin: 0 }}>{L("术语库", "Glossary")}</h1>
      <div style={{ ...T.type.body, color: T.semantic.text.secondary, marginTop: T.space.s2 }}>
        {L("每个项目一本——转录时选用哪本，定稿就照它校正人名、公司与专有名词。", "One per project — pick which to apply when transcribing; names, companies and terms are corrected against it.")}
      </div>
    </div>
  );

  // ── 空态：没有任何库 ──
  // ⚠️ 必须先 `loaded`：没取回来时 glossaries 也是空的，而这一屏是在**断言**「你一本都没有」，
  // 还给了「新建第一本」的按钮。未知期间宁可什么都不摆（三栏骨架），也不要说错话。
  if (!loaded) {
    return (
      <div aria-hidden style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: T.layout.pagePad, overflow: "hidden" }}>
        {header}
        <div style={{ display: "grid", gridTemplateColumns: "220px minmax(0,1fr) 300px", gap: T.space.s5, flex: 1, minHeight: 0, marginTop: T.space.s5 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ border: `1px solid ${T.semantic.border.default}`, borderRadius: T.radius.md, background: T.semantic.surface.raised }} />
          ))}
        </div>
      </div>
    );
  }
  if (glossaries.length === 0) {
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: T.layout.pagePad,
      // 横向 auto 不能写成 hidden：三栏里那 520px 是固定的（220 左栏 + 300 右栏），
      // 窗口比 824px 窄时内容真的放不下——hidden 会把右栏整块裁掉，既看不见也滑不出来，
      // 而外层 AppShell 的横滑够不到这一层（2026-08-19 在 900px 下实测到）。
      overflowX: "auto", overflowY: "hidden" }}>
        {header}
        <EmptyState
          icon={<span style={{ color: T.semantic.accent.text, display: "flex" }}><BookIcon size={28} /></span>}
          title={L("还没有术语库", "No glossary yet")}
          body={L("为某个项目建一本行话词典，转录时选用它，定稿照它校正。", "Build a dictionary for a project; pick it when transcribing and the transcript is corrected against it.")}
          actionLabel={L("新建第一本术语库", "Create your first glossary")}
          onAction={() => handleNew()}
          // 一本库都没有的人最需要帮忙，偏偏这一屏原来看不见「可以让我们帮你起草」。
          // 次要出口而非并列按钮：主路径仍是自己建，这条只是把起草框直接摆到面前。
          extra={
            <button
              className="tx-focus"
              onClick={() => handleNew("draft")}
              style={{
                background: "none", border: "none", cursor: "pointer", padding: "4px 6px",
                fontFamily: T.fonts.sans, fontSize: 13, color: T.semantic.accent.text,
              }}
            >
              {L("或者贴一份访谈大纲，我们帮你起草 →",
                 "Or paste an interview outline and we'll draft it for you →")}
            </button>
          }
        />
      </div>
    );
  }

  const cardStyle = {
    background: T.semantic.surface.raised,
    border: `1px solid ${T.semantic.border.default}`,
    borderRadius: T.radius.md,
    padding: T.space.s4,
  } as const;

  const editorHeader = (
    <>
      <span style={{ ...T.type.label, color: T.semantic.text.muted }}>{L("术语 ｜ 含义", "Term ｜ meaning")}</span>
      <span style={{ display: "flex", alignItems: "center", gap: T.space.s3 }}>
        <span style={{ fontFamily: T.fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: T.semantic.text.muted }}>
          {p.entryCount} {L("条", "entries")}
        </span>
        {dirty ? (
          <span style={{ fontSize: 12, color: T.semantic.accent.text }}>● {L("未保存", "Unsaved")}</span>
        ) : (
          <span style={{ fontSize: 12, color: T.semantic.success.text }}>✓ {L("已保存", "Saved")}</span>
        )}
      </span>
    </>
  );

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: T.layout.pagePad,
      // 横向 auto 不能写成 hidden：三栏里那 520px 是固定的（220 左栏 + 300 右栏），
      // 窗口比 824px 窄时内容真的放不下——hidden 会把右栏整块裁掉，既看不见也滑不出来，
      // 而外层 AppShell 的横滑够不到这一层（2026-08-19 在 900px 下实测到）。
      overflowX: "auto", overflowY: "hidden" }}>
      {header}

      {/* 三栏：库列表 ｜ 编辑器 ｜ 统计栏（定高，内部滚动） */}
      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "220px 1fr 300px", gap: T.space.s6, minWidth: 824 }}>
        {/* 左：库列表 */}
        <div style={{ minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", gap: T.space.s3 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: T.control.fieldH }}>
            <span style={{ ...T.type.label, color: T.semantic.text.muted }}>
              {L("术语库", "Glossaries")} <span style={{ fontFamily: T.fonts.mono, color: T.semantic.text.muted }}>{glossaries.length}/{MAX_GLOSSARIES}</span>
            </span>
            <button
              onClick={() => handleNew()}
              disabled={glossaries.length >= MAX_GLOSSARIES}
              style={{
                ...T.type.bodySm, color: glossaries.length >= MAX_GLOSSARIES ? T.semantic.text.muted : T.semantic.accent.text,
                background: "none", border: "none", cursor: glossaries.length >= MAX_GLOSSARIES ? "not-allowed" : "pointer", padding: "2px 4px",
              }}
            >
              + {L("新建", "New")}
            </button>
          </div>
          <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4, paddingRight: T.space.s1 }}>
            {glossaries.map((g) => {
              const active = g.id === selectedId;
              return (
                <button
                  key={g.id}
                  onClick={() => setSelectedId(g.id)}
                  style={{
                    textAlign: "left", border: "none", cursor: "pointer",
                    borderLeft: `3px solid ${active ? T.semantic.accent.text : "transparent"}`,
                    background: active ? T.semantic.accent.bgTint : "transparent",
                    borderRadius: T.radius.sm, padding: `${T.space.s2}px ${T.space.s3}px`,
                    display: "flex", flexDirection: "column", gap: 2, minWidth: 0,
                  }}
                >
                  <span style={{ ...T.type.bodySm, color: active ? T.semantic.text.primary : T.semantic.text.secondary, fontWeight: active ? 600 : 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {g.name}
                  </span>
                  <span style={{ fontFamily: T.fonts.mono, fontSize: 11, color: T.semantic.text.muted }}>
                    {parse(g.content).entryCount} {L("条", "entries")}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* 中：库名 + 编辑器 */}
        <div style={{ minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", gap: T.space.s3 }}>
          <input
            ref={nameRef}
            aria-label={L("库名", "Glossary name")}
            value={name}
            maxLength={MAX_NAME}
            onChange={(e) => setName(e.target.value)}
            placeholder={L("库名", "Glossary name")}
            style={{
              ...T.type.body, fontWeight: 600, color: T.semantic.text.primary,
              background: T.semantic.surface.raised, border: `1px solid ${nameOk ? T.semantic.border.default : T.semantic.accent.bgSoft}`,
              borderRadius: T.radius.sm, padding: `0 ${T.space.s3}px`, height: T.control.fieldH, boxSizing: "border-box",
            }}
          />
          <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {intake ? (
              <OutlineIntake
                value={outline}
                onChange={setOutline}
                onBack={() => setIntake(false)}
                onToast={showToast}
              />
            ) : (
              <GlossaryEditor ref={editorRef} value={text} onChange={setText} header={editorHeader} />
            )}
          </div>
        </div>

        {/* 右：统计 + 删除 + 保存 */}
        <div className="tx-scroll" style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: T.space.s4, overflowY: "auto", paddingRight: T.space.s2 }}>
          <div style={{ ...cardStyle, border: `1px solid ${p.overTotal ? T.semantic.accent.bgSoft : T.semantic.border.default}` }}>
            <div style={{ ...T.type.label, color: T.semantic.text.muted }}>{L("总字数", "Total characters")}</div>
            <div style={{ ...T.type.monoDisplay, fontSize: 28, color: countColor, marginTop: T.space.s2 }}>
              {p.totalChars} / {GLOSSARY_MAX_CHARS}
            </div>
            <div style={{ height: 4, background: T.semantic.surface.sunken, borderRadius: T.radius.pill, marginTop: T.space.s3, overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${Math.min(100, (p.totalChars / GLOSSARY_MAX_CHARS) * 100)}%`, background: countColor }} />
            </div>
            <div style={{ ...T.type.caption, color: countColor, marginTop: T.space.s2 }}>{countHint}</div>
          </div>

          {/* key=selectedId：切库时整块重建。协助建库自己攒着一堆状态（起草队列、体检三框、
              已处理游标），这些全是**针对某一本库**的；不重建的话，A 库的体检建议会留在 B 库的
              右栏，而 B 库若为空，前端那道「已有的词不显示」的过滤还会失效 → 一点采纳就把 A 的
              内容写进 B。用 key 比逐个 reset 可靠：以后再加状态也不会漏。 */}
          {/* 待办层排在检查结果之上（设计 §4.1 的右栏顺序）：
              总字数 → 还缺这些 → 检查结果三框 → 跳转到分类 → 检查/保存 */}
          {/* 外面这层只为「入场动画 + 滚动锚点」。面板没内容时整层不渲染，否则
              右栏的 flex gap 会白留一段。flex:0 0 auto 不能省——右栏会滚，
              可压缩的项会被挤扁后裁掉。 */}
          {(p.blankEntries.length > 0 || gaps.length > 0) && (
            <div
              ref={todoRef}
              style={{
                flex: "0 0 auto",
                ...(todoRound > 0 ? { animation: "riseIn 240ms cubic-bezier(.2,0,0,1)" } : null),
              }}
            >
              {/* key=轮次：重挂载才能重播入场动画，也顺手把展开态按新场景重设 */}
              <GlossaryTodo
                key={todoRound}
                blanks={p.blankEntries}
                gaps={gaps}
                scenario={todoScenario}
                onJump={(off) => editorRef.current?.jumpTo(off)}
                onSetGap={onSetGap}
              />
            </div>
          )}

          <GlossaryAssist
            ref={assistRef}
            key={selectedId ?? "none"}
            text={text}
            setText={setText}
            glossaryId={selectedId}
            outline={outline}
            intakeOpen={intake}
            onOpenIntake={() => setIntake(true)}
            onCloseIntake={() => setIntake(false)}
            onTodoRefresh={refreshGaps}
            onPhaseChange={setAssistPhase}
            onToast={showToast}
          />

          {p.overEntries.length > 0 && (
            <div style={cardStyle}>
              <div style={{ ...T.type.label, color: T.semantic.warning.text, marginBottom: T.space.s2 }}>{L("含义过长", "Meaning too long")}</div>
              {p.overEntries.map((e) => (
                <div key={e.lineIndex} onClick={() => editorRef.current?.jumpTo(e.charOffset)} style={{ ...T.type.bodySm, color: T.semantic.text.secondary, padding: `${T.space.s1}px 0`, cursor: "pointer" }}>
                  <span style={{ fontFamily: T.fonts.mono, color: T.semantic.text.muted }}>L{e.lineNo}</span> · {e.term} ·{" "}
                  <span style={{ fontFamily: T.fonts.mono, color: T.semantic.warning.text }}>{e.mlen}/{MEANING_MAX_CHARS}</span>
                </div>
              ))}
            </div>
          )}

          {p.cats.length > 0 && (
            <div style={cardStyle}>
              <div style={{ ...T.type.label, color: T.semantic.text.muted, marginBottom: T.space.s2 }}>{L("跳转到分类", "Jump to category")}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: T.space.s2 }}>
                {p.cats.map((c) => (
                  <span key={c.lineIndex} onClick={() => editorRef.current?.jumpTo(c.charOffset)} style={{ ...T.type.bodySm, color: T.semantic.text.secondary, background: T.semantic.surface.sunken, borderRadius: T.radius.pill, padding: `2px ${T.space.s3}px`, cursor: "pointer" }}>
                    {c.label} <span style={{ fontFamily: T.fonts.mono, color: T.semantic.text.muted }}>{c.count}</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div style={{ ...cardStyle, background: T.semantic.surface.page }}>
            <div style={{ ...T.type.label, color: T.semantic.text.muted, marginBottom: T.space.s2 }}>{L("格式", "Format")}</div>
            <div style={{ ...T.type.bodySm, color: T.semantic.text.secondary, lineHeight: 1.7 }}>
              {L("· 一行一条：术语 ｜ 含义（全角竖线）", "· One per line: term ｜ meaning")}
              <br />
              {L("· 用 ## 开头另起一个分类", "· Start a category with ##")}
            </div>
          </div>

          <div style={{ flex: 1 }} />

          {!text.trim() && (
            <Button secondary full onClick={() => setText(SAMPLE)}>{L("用示例模板", "Use a template")}</Button>
          )}

          {saveErr && (
            <div style={{ ...T.type.caption, color: T.semantic.accent.text, textAlign: "center" }}>{saveErr}</div>
          )}

          <Button primary full disabled={!canSave} onClick={save}>
            {p.overTotal ? L("超出字数上限", "Over the limit") : !nameOk ? L("先填库名", "Name the glossary") : dirty ? L("保存术语库", "Save glossary") : L("已保存", "Saved")}
          </Button>

          {/* 两步删除：先 Delete → Confirm delete / Cancel（Button 无 danger 变体，确认用原生赤陶按钮）*/}
          {confirmDel ? (
            <div style={{ display: "flex", gap: T.space.s2 }}>
              <button
                onClick={handleDelete}
                style={{
                  flex: 1, ...T.type.body, fontWeight: 600, color: T.semantic.text.onAccent,
                  background: T.semantic.danger.fill, border: "none", borderRadius: T.radius.md,
                  padding: `${T.space.s3}px`, cursor: "pointer",
                }}
              >
                {L("确认删除", "Confirm delete")}
              </button>
              <Button secondary onClick={() => setConfirmDel(false)}>{L("取消", "Cancel")}</Button>
            </div>
          ) : (
            <Button secondary full onClick={handleDelete}>{L("删除", "Delete")}</Button>
          )}
        </div>
      </div>

      {toast && (
        <div style={{ position: "fixed", bottom: 32, left: "50%", transform: "translateX(-50%)", display: "flex", alignItems: "center", gap: T.space.s4, background: T.semantic.text.primary, color: T.semantic.text.onAccent, padding: `${T.space.s3}px ${T.space.s5}px`, borderRadius: T.radius.pill, boxShadow: T.shadow.lg, fontSize: 13, zIndex: 100 }}>
          <span>{toast.msg}</span>
          {toast.undo && (
            <button
              onClick={() => { toast.undo?.(); setToast(null); }}
              style={{ background: "none", border: "none", cursor: "pointer", fontSize: 13, fontWeight: 600, color: T.semantic.accent.bgSoft, padding: 0 }}
            >{L("撤销", "Undo")}</button>
          )}
        </div>
      )}
    </div>
  );
}

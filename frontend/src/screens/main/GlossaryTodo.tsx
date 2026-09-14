// 术语库右栏的「还缺这些」面板 —— 待办层（设计交付《术语库待办层》§4.1、动效 §6）。
//
// 这块和它下面的「检查结果」三框长得像，但**性质相反**，别把两者的交互抄混：
//   检查结果三框 → 平台给得出答案 → 有「采纳」，点了平台直接改库（terracotta）
//   还缺这些     → 平台给不出答案 → **整块面板没有任何采纳按钮**，只有「跳过去」和
//                  「知道了」，因为这里每一条都只能用户自己动手（暖金）
// 颜色也承担这个分工：暖金 = 还没做完（你来），terracotta = 可采纳 / 可提交（我们来）。
//
// 两种待办：
//   · 等你填的词 —— 编辑器里「只有名字、释义空着」的行，从正文解析即得（不单独存）
//   · 还缺这几类 —— 缺口，**与正文分表存**（正文会作为硬证据注入转录引擎，这是给人看的）
//
// ⚠️ 文案红线：不许出现 AI / 大模型 / 智能 / 自动生成 等（bannedAiPhrases.ts 有守卫）。
import { useEffect, useRef, useState } from "react";
import * as T from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import { prefersReducedMotion } from "../../lib/reducedMotion";
import type { Gap } from "../../lib/api";
import type { BlankEntry } from "../../lib/glossary";

/** 场景：只影响引导语与默认展开态，形态是同一个（设计 §4.4） */
export type TodoScenario = "drafted" | "checked" | "leftover";

/** 「知道了」的收拢时长（设计 §6：opacity 120ms 淡出 + 高度 180ms 收拢）。
 *  落库要等收拢演完再发，否则列表当场少一项、卡片还没收完就跳了一下。 */
export const DISMISS_MS = 180;

const caret = (open: boolean) => (
  <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden
    style={{ transform: open ? "rotate(90deg)" : "none", transition: `transform ${T.motion.fast}` }}>
    <path d="M9 6l6 6-6 6" />
  </svg>
);

interface Props {
  blanks: BlankEntry[];
  gaps: Gap[];
  scenario: TodoScenario;
  /** 「跳过去 →」：把光标送到该行竖线之后 */
  onJump: (charOffset: number) => void;
  /** 「知道了」/「恢复」：只改状态，缺口不删——用户要能撤回 */
  onSetGap: (gapId: string, status: Gap["status"]) => void;
}

export function GlossaryTodo({ blanks, gaps, scenario, onJump, onSetGap }: Props) {
  const L = useL();
  // 场景 C（上次留下的）默认折叠成一行；刚起草完 / 刚检查完则展开
  const [open, setOpen] = useState(scenario !== "leftover");
  const [doneOpen, setDoneOpen] = useState(false);
  /** 正在收拢的缺口卡：还在 DOM 里演动画，但计数已经不算它了（设计 §6「计数即时 −1」） */
  const [exiting, setExiting] = useState<Record<string, true>>({});
  const timers = useRef<number[]>([]);
  useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);

  const live = gaps.filter((g) => g.status === "open");
  const done = gaps.filter((g) => g.status === "handled");
  if (blanks.length === 0 && gaps.length === 0) return null;

  const liveCount = live.filter((g) => !exiting[g.id]).length;

  /** 点「知道了」：先演收拢，再落库。减少动效时直接落库（等一段没有动画的 180ms 只是卡顿）。 */
  const dismiss = (id: string) => {
    if (prefersReducedMotion()) { onSetGap(id, "handled"); return; }
    setExiting((e) => ({ ...e, [id]: true }));
    timers.current.push(window.setTimeout(() => onSetGap(id, "handled"), DISMISS_MS));
  };

  const lead = scenario === "drafted"
    ? L("起草完了。下面这几类只有你知道，我们补不出来——在左边自己写上就行。",
         "Draft's done. These are the parts only you know — type them in on the left.")
    : scenario === "checked"
      ? L("照这本库现在的样子看，下面这几类还空着。",
           "Going by the library as it stands, these are still blank.")
      : L("上次留下的，接着处理。", "Left over from last time — pick up where you stopped.");

  const card: React.CSSProperties = {
    flex: "0 0 auto",          // 右栏是 flex column + overflow:auto，不钉住会被压扁后裁掉
    background: T.semantic.surface.raised,
    border: `1px solid ${T.semantic.border.default}`,
    borderRadius: T.radius.md,
  };
  const groupLabel: React.CSSProperties = {
    ...T.type.label, color: T.semantic.text.muted,
    display: "flex", alignItems: "center", gap: 6,
  };
  const countNum: React.CSSProperties = {
    fontFamily: T.fonts.mono, fontSize: 11, fontVariantNumeric: "tabular-nums",
    color: T.semantic.warning.text,
  };

  return (
    <div style={card}>
      <button
        className="tx-focus"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          width: "100%", height: 44, padding: "12px 14px", display: "flex", alignItems: "center",
          gap: 8, background: "none", border: "none", cursor: "pointer", color: T.semantic.text.primary,
        }}
      >
        <span style={{ color: open ? T.semantic.accent.text : T.semantic.text.muted, display: "flex" }}>
          {caret(open)}
        </span>
        <span style={{ ...T.type.label, fontSize: 13.5, fontWeight: 600, letterSpacing: 0 }}>
          {L("还缺这些", "Still missing")}
        </span>
        <span style={{
          marginLeft: "auto", padding: "3px 8px", borderRadius: T.radius.pill,
          background: T.semantic.surface.sunken, fontFamily: T.fonts.mono, fontSize: 11,
          fontVariantNumeric: "tabular-nums",
          color: blanks.length + liveCount > 0 ? T.semantic.warning.text : T.semantic.text.muted,
        }}>
          {L.t("{0} 词 · {1} 条", "{0} terms · {1} gaps", String(blanks.length), String(liveCount))}
        </span>
      </button>

      {/* 展开/折叠走高度过渡（设计 §6：180ms）。用 grid 0fr↔1fr 而不是 maxHeight——
          缺口条数不定，maxHeight 得猜一个上限，猜小了长内容会被裁。
          折叠时 visibility:hidden：内容留在 DOM 才能演收拢，但键盘不能再 Tab 进去。 */}
      <div style={{
        display: "grid", gridTemplateRows: open ? "1fr" : "0fr",
        transition: `grid-template-rows ${T.motion.base}`,
      }}>
        <div style={{
          overflow: "hidden", minHeight: 0,
          visibility: open ? "visible" : "hidden",
          transition: open ? "visibility 0s" : `visibility 0s ${T.motion.base}`,
        }}>
          <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ ...T.type.caption, fontSize: 11.5, lineHeight: 1.6, color: T.semantic.text.muted }}>
            {lead}
          </div>

          {blanks.length > 0 && (
            <div>
              <div style={{ ...groupLabel, marginBottom: 6 }}>
                {L("等你填的词", "Waiting on you")}
                <span style={countNum}>{blanks.length}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {blanks.map((b) => (
                  <div key={b.lineIndex} style={{
                    padding: "7px 10px", borderRadius: T.radius.sm,
                    background: T.semantic.surface.float,
                    border: `1px solid ${T.semantic.border.subtle}`,
                    boxShadow: `inset 3px 0 0 ${T.semantic.warning.icon}`,
                    display: "flex", alignItems: "center", gap: 8,
                  }}>
                    <span style={{ fontSize: 12.5, fontWeight: 500, color: T.semantic.text.primary }}>{b.term}</span>
                    {/* 行号是序号，红线 11 明令不得用 ghost；字号也拉回 11px 下限 */}
                    <span style={{ fontFamily: T.fonts.mono, fontSize: 11, color: T.semantic.text.muted }}>
                      L{b.lineNo}
                    </span>
                    <button className="tx-focus" onClick={() => onJump(b.charOffset)} style={{
                      marginLeft: "auto", background: "none", border: "none", cursor: "pointer",
                      fontSize: 11.5, color: T.semantic.accent.text, padding: 0,
                    }}>
                      {L("跳过去 →", "Go there →")}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {live.length > 0 && (
            <div>
              <div style={{ ...groupLabel, marginBottom: 6 }}>
                {L("还缺这几类", "Kinds still missing")}
                <span style={countNum}>{liveCount}</span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {live.map((g) => {
                  const out = !!exiting[g.id];
                  return (
                  <div key={g.id} style={{
                    padding: "10px 12px", borderRadius: T.radius.sm,
                    background: T.semantic.surface.float,
                    border: `1px solid ${T.semantic.border.subtle}`,
                    // 收拢：先淡出（120ms），高度同时收（180ms）。padding/border 一起归零，
                    // 否则收完还剩一条 20 多 px 的空壳。
                    opacity: out ? 0 : 1,
                    // 收起点是 max-height，所以展开值要贴近真实高度：给个远大于内容的数
                    // （比如 400），前小半段过渡都花在「从没人看得见的 400 收到 90」上，
                    // 看着就是点完先愣一下才动。缺口正文封顶 60 字 ≈ 5 行，200 够用且贴身。
                    maxHeight: out ? 0 : 200,
                    paddingTop: out ? 0 : 10, paddingBottom: out ? 0 : 10,
                    borderWidth: out ? 0 : 1, overflow: "hidden",
                    transition: `opacity ${T.motion.fast}, max-height ${T.motion.base}, padding ${T.motion.base}`,
                  }}>
                    <div style={{ display: "flex", gap: 8 }}>
                      <span style={{ fontFamily: T.fonts.mono, fontSize: 12, color: T.semantic.warning.icon }}>⌇</span>
                      {/* 缺口正文不截断不折叠：截了就等于没说清缺什么 */}
                      <span style={{ fontSize: 12, lineHeight: 1.65, color: T.semantic.text.secondary }}>{g.text}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 6 }}>
                      <button className="tx-focus" onClick={() => dismiss(g.id)} disabled={out} style={{
                        background: "none", border: "none", cursor: "pointer", padding: 0,
                        fontSize: 11.5, color: T.semantic.text.muted,
                      }}>
                        {L("知道了", "Got it")}
                      </button>
                    </div>
                  </div>
                  );
                })}
              </div>
            </div>
          )}

          {blanks.length === 0 && live.length === 0 && (
            <div style={{
              border: `1px dashed ${T.semantic.border.default}`, borderRadius: T.radius.sm,
              padding: "14px 12px", textAlign: "center",
            }}>
              <div style={{ fontSize: 12.5, color: T.semantic.text.secondary }}>
                {L("这一轮没看出明显缺口。", "Nothing obvious missing this round.")}
              </div>
              <div style={{ fontSize: 11.5, lineHeight: 1.6, color: T.semantic.text.muted, marginTop: 4 }}>
                {L("想到自己行里的叫法，直接在左边补一行。",
                   "Remember a term of your own — just add a line on the left.")}
              </div>
            </div>
          )}

          {done.length > 0 && (
            <div style={{ borderTop: `1px solid ${T.semantic.border.subtle}`, paddingTop: 10 }}>
              <button className="tx-focus" onClick={() => setDoneOpen((v) => !v)} aria-expanded={doneOpen} style={{
                display: "flex", alignItems: "center", gap: 6, background: "none", border: "none",
                cursor: "pointer", padding: 0, fontSize: 11.5, color: T.semantic.text.muted,
              }}>
                <span style={{ display: "flex" }}>{caret(doneOpen)}</span>
                {L("已处理", "Handled")}
                <span style={{ fontFamily: T.fonts.mono, fontVariantNumeric: "tabular-nums" }}>{done.length}</span>
              </button>
              {doneOpen && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
                  {done.map((g) => (
                    <div key={g.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{
                        fontSize: 11.5, color: T.semantic.text.ghost, textDecoration: "line-through",
                        flex: 1, minWidth: 0,
                      }}>{g.text}</span>
                      <button className="tx-focus" onClick={() => onSetGap(g.id, "open")} style={{
                        background: "none", border: "none", cursor: "pointer", padding: 0,
                        fontSize: 11.5, color: T.semantic.accent.text,
                      }}>
                        {L("恢复", "Undo")}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          </div>
        </div>
      </div>
    </div>
  );
}

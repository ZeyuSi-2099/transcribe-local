// 运营驾驶舱共用的小部件。原本都写在 AdminPage.tsx 里，Tag 拆分后 WorkflowTab 也要用，
// 抽出来是为了避免 AdminPage ↔ WorkflowTab 互相 import。**只放无状态的壳与样式**——
// 有业务判断的东西留在各自的页面里，别让这里长成第二个逻辑层。
import { semantic, fonts, type, space, radius, shadow } from "../../styles/tokens";

export const mono = (size = 13): React.CSSProperties => ({
  fontFamily: fonts.mono, fontSize: size, fontVariantNumeric: "tabular-nums",
});

export const ellipsis: React.CSSProperties = { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };

export const caret = (open: boolean) => (
  <span aria-hidden style={{ display: "inline-block", transition: "transform .15s", transform: open ? "rotate(90deg)" : "none", color: semantic.text.ghost, fontSize: 15 }}>▸</span>
);

export function SectionShell({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div style={{ marginTop: space.s6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", margin: `0 0 ${space.s3}px` }}>
        <h2 style={{ ...type.h2, margin: 0 }}>{title}</h2>
        {action}
      </div>
      <div style={{ border: `1px solid ${semantic.border.default}`, background: semantic.surface.raised, borderRadius: radius.md, boxShadow: shadow.sm, overflow: "hidden" }}>
        {children}
      </div>
    </div>
  );
}

export const empty = (msg: string) => (
  <div style={{ padding: `${space.s7}px ${space.s5}px`, textAlign: "center", fontSize: 13, color: semantic.text.muted }}>{msg}</div>
);

export const btnStyle = (subtle: boolean): React.CSSProperties => ({
  ...mono(12), padding: "6px 12px", borderRadius: radius.sm, cursor: "pointer",
  border: subtle ? `1px solid ${semantic.border.strong}` : "none",
  background: subtle ? "transparent" : semantic.accent.fill,
  color: subtle ? semantic.text.secondary : semantic.text.onAccent,
});

export const cardInput: React.CSSProperties = {
  ...mono(11), padding: "4px 6px", borderRadius: radius.sm, width: "100%",
  border: `1px solid ${semantic.border.strong}`, background: semantic.surface.page, color: semantic.text.primary,
};

export const linkBtn: React.CSSProperties = {
  ...mono(11), padding: 0, border: "none", background: "none", cursor: "pointer", color: semantic.accent.text,
};

// 「MM-DD HH:MM」。**读不出来一律回 —，不回 Invalid Date**：运营页上一个坏日期
// 比一个「—」难查得多（前者看着像数据、后者一眼知道没有）。
export const fmtAt = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

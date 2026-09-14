import { useState } from "react";
import { parse } from "../lib/glossary";
import { fonts, labelStyle, radius, semantic } from "../styles/tokens";
import { useL } from "../lib/i18n";
import type { Glossary } from "../lib/api";

interface GlossaryBarProps {
  glossaries: Glossary[];
  selectedId: string | null;            // 本次转录用哪本（null = 不使用）
  onSelect: (id: string | null) => void;
  onOpenFull: () => void;               // 跳转术语库整页（管理/编辑）
}

const tick = <span style={{ color: semantic.accent.text, display: "inline-flex", fontSize: 11 }}>✓</span>;

export function GlossaryBar({ glossaries, selectedId, onSelect, onOpenFull }: GlossaryBarProps) {
  const L = useL();
  const [open, setOpen] = useState(false);
  const selected = glossaries.find((g) => g.id === selectedId) ?? null;

  // 空库：引导去建第一本（不打断主流程，没库也能直接转）
  if (glossaries.length === 0) {
    return (
      <div>
        <div style={{ ...labelStyle, marginBottom: 10 }}>{L("术语库", "Glossary")}</div>
        <div
          onClick={onOpenFull}
          style={{
            border: `1.5px dashed ${semantic.border.default}`, borderRadius: radius.md, padding: "12px 16px",
            cursor: "pointer", fontSize: 13, color: semantic.text.secondary, fontFamily: fonts.sans,
            display: "flex", alignItems: "center", gap: 8,
          }}
        >
          <span style={{ color: semantic.accent.text }}>＋</span>
          {L("添加术语库（帮定稿校准人名与术语）", "Add a glossary (helps get names & terms right)")}
        </div>
      </div>
    );
  }

  const triggerLabel = selected ? selected.name : L("不使用", "Don't use");

  // 触发器：全宽贯穿（与语言/录音类型同宽），边框 select 样式，左库名右 ▾
  const triggerStyle: React.CSSProperties = {
    width: "100%", boxSizing: "border-box",
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "11px 14px", fontSize: 14, fontWeight: 500, fontFamily: fonts.sans,
    border: `1px solid ${selected ? semantic.text.primary : semantic.border.default}`, borderRadius: radius.sm,
    background: selected ? semantic.text.primary : "transparent",      // 选中=黑底白字，与语言「中文」选中态一致
    color: selected ? semantic.surface.raised : semantic.text.secondary,
    cursor: "pointer", textAlign: "left",
  };

  const rowStyle = (active: boolean): React.CSSProperties => ({
    padding: "10px 14px", fontSize: 13, cursor: "pointer", display: "flex", alignItems: "center",
    gap: 8, color: active ? semantic.text.primary : semantic.text.secondary, fontWeight: active ? 600 : 400,
    fontFamily: fonts.sans,
  });

  return (
    <div>
      <div style={{ ...labelStyle, marginBottom: 10 }}>{L("术语库", "Glossary")}</div>
      <div style={{ position: "relative" }}>
        <button onClick={() => setOpen((o) => !o)} style={triggerStyle}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{triggerLabel}</span>
          <span style={{ flex: "0 0 auto", marginLeft: 8 }}>▾</span>
        </button>
        {open && (
          <>
            <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => setOpen(false)} />
            <div
              className="tx-scroll"
              style={{
                position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, background: semantic.surface.raised,
                border: `1px solid ${semantic.border.default}`, boxShadow: "0 8px 28px rgba(31,26,20,0.12)",
                borderRadius: radius.sm, maxHeight: 320, overflowY: "auto", zIndex: 50,
              }}
            >
              <div style={rowStyle(selectedId == null)} onClick={() => { onSelect(null); setOpen(false); }}>
                {selectedId == null && tick}
                {L("不使用", "Don't use")}
              </div>
              {glossaries.map((g) => (
                <div key={g.id} style={rowStyle(g.id === selectedId)} onClick={() => { onSelect(g.id); setOpen(false); }}>
                  {g.id === selectedId && tick}
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.name}</span>
                  <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted }}>
                    {parse(g.content).entryCount} {L("条", "entries")}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: semantic.text.muted }}>
        {L("选哪本术语库给本次转录定稿校正（也可不使用）", "Pick which glossary to correct this transcript against (or none)")}
      </div>
    </div>
  );
}

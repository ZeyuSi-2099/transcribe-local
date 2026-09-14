// AudioPlayer.proposed.tsx — 设计系统 v1 版播放器
// 变更：① 播放键 accent.fill（承载白字需 4.5:1）② 时间一律 mono + tabular-nums
//      ③ 新增 marks：把复核标记画在进度条下方，点击跳播——播放器兼任复核导航
//      ④ focus ring（需在 global.css 加 .tx-focus:focus-visible 规则，见文件尾注）
import { semantic, fonts, radius, shadow, motion } from "../styles/tokens";
import { fmtClock } from "../lib/format";
import { useL } from "../lib/i18n";

export interface AudioMark {
  sec: number;                       // 标记在音频中的位置
  kind: "entity" | "doubt" | "web";  // 决定颜色
  label: string;                     // aria/title 文案，如「存疑：ND 省包」
}

interface AudioPlayerProps {
  cur: number;
  total: number;
  playing: boolean;
  onToggle: () => void;
  onSeek: (sec: number) => void;
  marks?: AudioMark[];
}

const MARK_COLOR: Record<AudioMark["kind"], string> = {
  entity: semantic.accent.brand,
  doubt: semantic.warning.icon,
  web: semantic.success.graphic,
};

export function AudioPlayer({ cur, total, playing, onToggle, onSeek, marks = [] }: AudioPlayerProps) {
  const L = useL();
  const pct = total ? Math.min(100, (cur / total) * 100) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, padding: "10px 16px", boxShadow: shadow.sm }}>
      <button
        className="tx-focus"
        onClick={onToggle}
        aria-label={playing ? L("暂停", "Pause") : L("播放", "Play")}
        style={{ width: 32, height: 32, borderRadius: "50%", flex: "0 0 auto", border: "none", background: semantic.accent.fill, color: semantic.text.onAccent, cursor: "pointer", display: "grid", placeItems: "center", transition: `background ${motion.fast}` }}
      >
        {playing ? (
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden>
            <rect x="3" y="2.2" width="2.1" height="7.6" rx="0.6" />
            <rect x="6.9" y="2.2" width="2.1" height="7.6" rx="0.6" />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden style={{ marginLeft: 1 }}>
            <path d="M3.2 2.1v7.8a.5.5 0 0 0 .77.42l6.1-3.9a.5.5 0 0 0 0-.84L3.97 1.69A.5.5 0 0 0 3.2 2.1z" />
          </svg>
        )}
      </button>
      <span style={{ fontFamily: fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: semantic.text.secondary, minWidth: 44 }}>{fmtClock(cur)}</span>
      <div
        onClick={(e) => {
          if (!total) return;
          const r = e.currentTarget.getBoundingClientRect();
          onSeek(((e.clientX - r.left) / r.width) * total);
        }}
        style={{ flex: 1, height: 30, display: "flex", alignItems: "center", cursor: "pointer", position: "relative" }}
      >
        <div style={{ position: "relative", height: 3, width: "100%", background: semantic.border.default, borderRadius: 2 }}>
          <div style={{ position: "absolute", inset: "0 auto 0 0", width: pct + "%", background: semantic.accent.brand, borderRadius: 2 }} />
          <div style={{ position: "absolute", left: pct + "%", top: "50%", width: 11, height: 11, marginLeft: -5, marginTop: -5.5, borderRadius: "50%", background: semantic.accent.brand, boxShadow: `0 0 0 3px ${semantic.surface.raised}` }} />
        </div>
        {/* 标记点：进度条下方 8px，点击跳播 */}
        {total > 0 && marks.map((m, i) => (
          <button
            key={i}
            className="tx-focus"
            title={m.label}
            aria-label={`跳到 ${m.label}（${fmtClock(m.sec)}）`}
            onClick={(e) => { e.stopPropagation(); onSeek(m.sec); }}
            style={{ position: "absolute", left: `${(m.sec / total) * 100}%`, top: "50%", marginTop: 6, width: 12, height: 12, marginLeft: -6, padding: 0, border: "none", background: "transparent", cursor: "pointer", display: "grid", placeItems: "center" }}
          >
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: MARK_COLOR[m.kind], display: "block" }} />
          </button>
        ))}
      </div>
      <span style={{ fontFamily: fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: semantic.text.muted, minWidth: 44, textAlign: "right" }}>{fmtClock(total)}</span>
    </div>
  );
}


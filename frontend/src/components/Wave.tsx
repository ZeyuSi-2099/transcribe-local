// 品牌声波组件 family —— 克制地用在「多引擎并行 → 一份融合稿」最相关的几处（设计稿 05）。
// barPulse keyframe 在 global.css；未到/灰柱一律走 token（不引入表外色）。
import { semantic } from "../styles/tokens";

// 空白页 84×64 图标块里的小声波：7 柱、5 灰 + 3 赤陶点缀，轻脉动（设计稿 02）。
const TILE_H = [40, 64, 46, 86, 56, 72, 50];
const TILE_ACCENT = [false, false, false, true, true, true, false];
export function WaveTile() {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 34 }} aria-hidden>
      {TILE_H.map((h, i) => (
        <span
          key={i}
          style={{
            width: 4,
            height: h + "%",
            borderRadius: 2,
            transformOrigin: "bottom",
            background: TILE_ACCENT[i] ? semantic.accent.brand : semantic.border.strong,
            animation: `barPulse ${(0.9 + (i % 3) * 0.2).toFixed(2)}s ease-in-out ${(i * 0.08).toFixed(2)}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

// 复核清零的收束绿波形：存疑全部确认 → 波柱收平成一条绿线（设计稿 05 复核清零，呈现「收平的最后一帧」）。
// 大多数柱落定到低位、少数略高，整体一条「尘埃落定」的绿线；动效收束由 global.css reduced-motion 守住。
export function ConvergeWave() {
  const n = 28;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 2, height: 30 }} aria-hidden>
      {Array.from({ length: n }).map((_, i) => {
        const settled = i % 5 !== 0;
        return (
          <span
            key={i}
            style={{
              flex: 1,
              height: (settled ? 10 : 26) + "%",
              borderRadius: 1.5,
              background: semantic.success.text,
              opacity: settled ? 0.95 : 0.7,
            }}
          />
        );
      })}
    </div>
  );
}

// 列表行「处理中」声波即进度：已完成柱赤陶、未到柱灰，最前沿脉动 —— 既是品牌母题又看得出进度（设计稿 02/04）。
export function WaveProgress({ progress, width = 200, active = true }: { progress: number; width?: number; active?: boolean }) {
  const n = 24;
  const p = Math.max(0, Math.min(1, progress));
  return (
    <div style={{ width, height: 22, display: "flex", alignItems: "flex-end", gap: 2 }} aria-hidden>
      {Array.from({ length: n }).map((_, i) => {
        const done = i / n < p;
        const onEdge = active && done && i / n > p - 0.2;
        const h = 30 + Math.abs(Math.sin(i * 0.6 + 0.3)) * 62;
        return (
          <span
            key={i}
            style={{
              flex: 1,
              height: h.toFixed(1) + "%",
              borderRadius: 1.5,
              transformOrigin: "bottom",
              background: done ? semantic.accent.brand : semantic.border.strong,
              opacity: done ? 0.95 : 0.85,
              // 进度前进时新点亮的 bar 柔和渐变(灰→赤陶),而非硬切——匀速爬升看着更顺、
              // done 从中途到满(情况①)也是一批 bar 柔和亮起
              transition: "background .4s ease",
              animation: onEdge ? `barPulse ${(0.7 + (i % 3) * 0.18).toFixed(2)}s ease-in-out ${(i * 0.05).toFixed(2)}s infinite` : "none",
            }}
          />
        );
      })}
    </div>
  );
}

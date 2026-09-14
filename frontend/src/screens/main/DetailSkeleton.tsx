// DetailSkeleton — 详情页「数据未到」的纸色骨架占位（设计稿 04 加载）。
// 拉取已完成文稿时先铺骨架、消除进入闪白；纸色 + skel 轻呼吸，不喧宾夺主。
import { semantic, space, radius, layout } from "../../styles/tokens";

// 单根骨架条：纸色块 + skel 呼吸（错相位，避免整屏同步闪）
function Bar({ w, h = 12, mt = 0, delay = 0 }: { w: number | string; h?: number; mt?: number; delay?: number }) {
  return (
    <div
      aria-hidden
      style={{
        width: typeof w === "number" ? `${w}%` : w,
        height: h,
        marginTop: mt,
        borderRadius: 4,
        background: semantic.surface.sunken,
        animation: `skel 1.5s ease-in-out ${delay}s infinite`,
      }}
    />
  );
}

export function DetailSkeleton() {
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "hidden", padding: layout.pagePad, display: "flex", flexDirection: "column" }} aria-busy="true" aria-label="loading">
      {/* 头部：标题 + 副信息 */}
      <Bar w={38} h={26} />
      <Bar w={22} h={12} mt={12} delay={0.1} />
      {/* 播放器条 */}
      <div style={{ marginTop: space.s5, height: 44, borderRadius: radius.md, background: semantic.surface.sunken, animation: "skel 1.5s ease-in-out .15s infinite" }} />
      {/* 文稿若干行：时间戳 + 两段文字 */}
      <div style={{ marginTop: space.s6, display: "flex", flexDirection: "column", gap: space.s5 }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} style={{ display: "flex", gap: space.s4 }}>
            <div style={{ flex: "0 0 44px" }}><Bar w="100%" h={10} delay={i * 0.08} /></div>
            <div style={{ flex: 1 }}>
              <Bar w={i % 2 ? 72 : 90} h={12} delay={i * 0.08 + 0.05} />
              <Bar w={i % 2 ? 48 : 64} h={12} mt={8} delay={i * 0.08 + 0.1} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

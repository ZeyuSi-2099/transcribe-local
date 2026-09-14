// 上传页左栏：**你自己的东西**，不是卖点。
//
// 这个位置 2026-08-31 之前放的是登录屏那个卖点组件（HeroPitch）——两屏共用同一个组件。
// 于是一个已经付过钱、正准备传第 20 份录音的用户，屏幕左边 40% 还在向他推销
// 当时那句主标。那不是产品决策，是登录屏的组件被顺手复用到了登录之后。
//
// 一份稿子都没有时**本组件不渲染**（判断在 Idle 里，整张上传卡居中）：
// 新用户的第一次上传，这一屏只有一件事要做，那本身就是最好的引导。
import { semantic, fonts, type, space, radius, motion } from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import { langName } from "../../lib/langs";
import type { HistoryItem } from "../../lib/sampleData";

interface RecentPanelProps {
  items: HistoryItem[];
  onOpen?: (item: HistoryItem) => void;
  onOpenAll?: () => void;
}

export function RecentPanel({ items, onOpen, onOpenAll }: RecentPanelProps) {
  const L = useL();

  return (
    <div style={{ maxWidth: 540, minWidth: 0, animation: "floatUp .55s ease both" }}>
      {/* 品牌发声只留这一句：它是一屏之主的位置，但说的是「继续做事」而不是「买我们」 */}
      <h2 style={{ fontFamily: fonts.serif, fontWeight: 700, fontSize: "clamp(30px,3.4vw,42px)", lineHeight: 1.12, letterSpacing: "-0.02em", margin: `0 0 ${space.s3}px`, color: semantic.text.primary }}>
        {L("接着上次继续。", "Pick up where you left off.")}
      </h2>
      <p style={{ fontFamily: fonts.sans, fontSize: 14, lineHeight: 1.65, color: semantic.text.secondary, margin: `0 0 ${space.s5}px` }}>
        {L("右边传新的；下面是最近几份，点开即可复核或导出。", "Upload a new one on the right — or reopen one of your recent transcripts.")}
      </p>

      <div style={{ ...type.label, marginBottom: space.s3 }}>{L("最近的转录", "Recent transcripts")}</div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column" }}>
        {items.map((it, i) => (
          <li key={`${it.id ?? it.n}-${i}`} style={{ borderTop: i === 0 ? `1px solid ${semantic.border.default}` : undefined, borderBottom: `1px solid ${semantic.border.default}` }}>
            <button
              onClick={() => onOpen?.(it)}
              style={{ display: "flex", alignItems: "center", gap: space.s4, width: "100%", textAlign: "left", border: "none", background: "transparent", cursor: "pointer", padding: `${space.s3}px ${space.s2}px`, fontFamily: fonts.sans, transition: `background ${motion.fast}` }}
              onMouseEnter={(e) => { e.currentTarget.style.background = semantic.surface.rowActive; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <span style={{ flex: 1, minWidth: 0 }}>
                <span title={it.n} style={{ display: "block", fontSize: 14, fontWeight: 600, color: semantic.text.primary, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{it.n}</span>
                <span style={{ display: "block", fontSize: 12, color: semantic.text.muted, marginTop: 3 }}>
                  {L(it.d.zh, it.d.en)} · {langName(it.lang, L)}
                </span>
              </span>
              {/* 时长走 mono：这一列是纵向对齐的数字，正是等宽字该在的地方 */}
              <span style={{ fontFamily: fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: semantic.text.muted, flex: "0 0 auto" }}>{it.dur}</span>
              <span aria-hidden style={{ color: semantic.text.ghost, flex: "0 0 auto", fontSize: 13 }}>›</span>
            </button>
          </li>
        ))}
      </ul>

      <button
        onClick={onOpenAll}
        style={{ marginTop: space.s4, border: "none", background: "transparent", padding: `${space.s2}px ${space.s2}px`, marginLeft: -space.s2, fontFamily: fonts.sans, fontSize: 13, color: semantic.accent.text, cursor: "pointer", borderRadius: radius.sm }}
      >
        {L("查看全部", "See all")} →
      </button>
    </div>
  );
}

/** 左栏的另一半：**一份稿子都没有时**放这个，而不是让整张上传卡跑到屏幕中间去。
 *
 * ⚠️ 2026-09-01 之前是「没有稿子就整卡居中」。想法没错，代价是**这一屏的布局取决于一个
 * 要等半秒才回来的接口**：任务列表是挂载后才拉的，于是有历史的老用户每次进来都先看到
 * 卡片居中、再眼睁睁看它横着跳到右边去。曾经试过用本地提示位「按上次的结论猜」——
 * 在无痕窗口里那个位子永远是空的，等于没修。
 * ⇒ 现在**不猜了**：栅格恒定两栏，左栏只是换内容。内容淡入不会推动任何东西。
 *
 * 这一栏不是推销（纪律 20）：它说的是「接下来会发生什么」，不是「买我们」。 */
export function FirstRunPanel() {
  const L = useL();
  const steps = [
    L("上传一段录音", "Upload a recording"),
    L("我们把拿不准的地方逐处标出来", "We flag every uncertain spot"),
    L("你只核标记，然后导出", "You check the marks, then export"),
  ];
  return (
    <div style={{ maxWidth: 540, minWidth: 0, animation: "floatUp .55s ease both" }}>
      <h2 style={{ fontFamily: fonts.serif, fontWeight: 700, fontSize: "clamp(30px,3.4vw,42px)", lineHeight: 1.12, letterSpacing: "-0.02em", margin: `0 0 ${space.s3}px`, color: semantic.text.primary }}>
        {L("第一份稿子，从这里开始。", "Your first transcript starts here.")}
      </h2>
      <p style={{ fontFamily: fonts.sans, fontSize: 14, lineHeight: 1.65, color: semantic.text.secondary, margin: `0 0 ${space.s5}px` }}>
        {L("右边选一段录音。转完不用通读——拿不准的地方会逐处标出来，你只核标记。",
           "Pick a recording on the right. You won't have to read the whole thing back — every uncertain spot gets flagged, and you only check the marks.")}
      </p>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column" }}>
        {steps.map((t, i) => (
          <li key={i} style={{ display: "flex", alignItems: "baseline", gap: space.s4, padding: `${space.s3}px ${space.s2}px`, borderTop: `1px solid ${semantic.border.default}`, borderBottom: i === steps.length - 1 ? `1px solid ${semantic.border.default}` : undefined }}>
            <span style={{ fontFamily: fonts.mono, fontSize: 11, fontWeight: 500, color: semantic.accent.text, flex: "0 0 auto" }}>{String(i + 1).padStart(2, "0")}</span>
            <span style={{ fontFamily: fonts.sans, fontSize: 14, lineHeight: 1.6, color: semantic.text.secondary }}>{t}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

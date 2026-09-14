// 小屏劝退（2026-08-06 审计 P0-2；2026-08-19 改判据）
//
// 登录后的应用是桌面布局：侧栏定宽 232px，内容区拿剩下的。装得下历史页的固定六列
// （132+138+84+92+100+44=590，加行内边距 48）还需要主区边距 128 与侧栏 232 —— 合计约
// 998px。低于这个宽度，列会被祖先的 overflow:hidden 裁掉。
//
// **判据是设备，不是窗口宽度。** 桌面用户把窗口拖多窄都不弹提示——那是网页缩放时的常态，
// 弹出来反而怪；放不下的部分靠滚动看（AppShell 的主区允许横向滚动）。只有触屏设备
// （手机 / 平板，靠 pointer:coarse + hover:none 认，触屏笔记本有鼠标故不算）才拦：
// 复核这套动作（读原文 + 听录音 + 逐处拍板）在手上拿着的屏幕上本来就不成立。
//
// 触屏设备里再分两种话术，因为处置不同：
//   · 手机 —— 横过来也不够（Pro Max 横屏 956px < 998），只能换电脑；
//   · 平板竖屏 / 分屏 —— 横过来就够（iPad mini 横屏 1024px），别让人白跑一趟换设备。
// 分辨靠**屏幕短边**（与横竖屏无关）：手机短边最大 430，平板短边最小 744，中间空着
// 300px，怎么切都不会错。用窗口宽度分不开——手机横屏比平板竖屏还宽。
//
// 留「仍要继续」出口：坚持要在手机上看的人不该被锁死。
import { useEffect, useState } from "react";
import { semantic, fonts, space, radius, type as T } from "../styles/tokens";
import { useL } from "../lib/i18n";
import { Brand } from "../components/Brand";

/** 应用装得下所需的最小视口宽度（见文件头算式）。 */
export const APP_MIN_WIDTH = 1000;

/** 手机与平板的分界：屏幕短边（手机 ≤430，平板 ≥744）。 */
export const PHONE_MAX_SHORT_SIDE = 600;

/** 劝退形态：null=放行 · phone=换电脑 · rotate=横过来 */
export type NarrowGate = "phone" | "rotate" | null;

/** 触屏且无悬停 = 手机/平板。触屏笔记本带鼠标（hover:hover），不命中。 */
function isTouchOnly(): boolean {
  return window.matchMedia?.("(pointer: coarse) and (hover: none)")?.matches === true;
}

/** 屏幕短边小于阈值 = 手机。用 screen 而非 window：与当前横竖屏、分屏窗口大小无关。 */
function isPhoneSized(): boolean {
  const s = window.screen;
  if (!s?.width || !s?.height) return true;   // 读不到就按手机处理（更保守的那一边）
  return Math.min(s.width, s.height) < PHONE_MAX_SHORT_SIDE;
}

function evaluateGate(): NarrowGate {
  try {
    if (typeof window === "undefined") return null;
    if (!isTouchOnly()) return null;                       // 桌面：任何宽度都不拦
    if (window.innerWidth >= APP_MIN_WIDTH) return null;   // 平板横屏够宽，放行
    return isPhoneSized() ? "phone" : "rotate";
  } catch {
    return null;   // 判定不了就放行，绝不能因为探测失败把人挡在外面
  }
}

/** 当前该不该劝退、劝哪一种。随窗口/朝向变化实时更新（平板转横屏即自动放行）。 */
export function useNarrowGate(): NarrowGate {
  const [gate, setGate] = useState<NarrowGate>(evaluateGate);
  useEffect(() => {
    const sync = () => setGate(evaluateGate());
    window.addEventListener("resize", sync);
    window.addEventListener("orientationchange", sync);
    sync();
    return () => {
      window.removeEventListener("resize", sync);
      window.removeEventListener("orientationchange", sync);
    };
  }, []);
  return gate;
}

export function NarrowScreenNotice({ gate, onContinue }: { gate: Exclude<NarrowGate, null>; onContinue: () => void }) {
  const L = useL();
  const rotate = gate === "rotate";
  return (
    <div
      style={{
        width: "100%", height: "100%", overflowY: "auto",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        gap: space.s6, padding: space.s6, boxSizing: "border-box", textAlign: "center",
        color: semantic.text.primary,
      }}
    >
      <Brand />
      <div style={{ ...T.title, fontSize: 30, margin: 0, maxWidth: 420 }}>
        {rotate
          ? L("请把设备横过来", "Please rotate your device")
          : L("请用电脑打开", "Please use a desktop browser")}
      </div>
      <p
        style={{
          ...T.body, color: semantic.text.secondary, margin: 0, maxWidth: 420,
          lineHeight: 1.75,
        }}
      >
        {rotate
          ? L(
              "复核要一边看稿、一边听录音、一处一处拍板。竖着拿放不下这些内容，横过来就够了。",
              "Reviewing means reading the transcript, playing the audio and deciding spot by spot. That does not fit in portrait — turning your device sideways is enough.",
            )
          : L(
              "复核要一边看稿、一边听录音、一处一处拍板。这套动作在手机屏幕上放不下，我们没有把它硬塞进来。",
              "Reviewing means reading the transcript, playing the audio and deciding spot by spot. That does not fit on a phone screen, and we would rather not pretend it does.",
            )}
      </p>
      <button
        className="tx-focus"
        onClick={onContinue}
        style={{
          fontFamily: fonts.sans, fontSize: 13, fontWeight: 500,
          color: semantic.accent.text, background: "transparent",
          border: `1px solid ${semantic.border.strong}`, borderRadius: radius.sm,
          padding: `${space.s3}px ${space.s5}px`, minHeight: 44, cursor: "pointer",
        }}
      >
        {L("仍要继续", "Continue anyway")}
      </button>
    </div>
  );
}

import type { ReactNode } from "react";
import { semantic, fonts } from "../styles/tokens";

interface SerifProps {
  children: ReactNode;
  size?: number;
  italic?: boolean;
  color?: string;
  weight?: number; // 大标题用 700/900；默认不设（继承 400）
  lat?: boolean; // 拉丁/数字为主时用 Source Serif 4 优先的字栈
  sans?: boolean; // 功能性标题（文件名/面板名/区块名）：改用 sans，不张扬
}

export function Serif({ children, size = 30, italic, color, weight, lat, sans }: SerifProps) {
  return (
    <span
      style={{
        fontFamily: sans ? fonts.sans : lat ? fonts.serifLat : fonts.serif,
        fontSize: size,
        fontWeight: weight,
        color: color ?? semantic.text.primary,
        lineHeight: 1.1,
        letterSpacing: sans ? -0.2 : -0.6,
        fontStyle: italic ? "italic" : "normal",
      }}
    >
      {children}
    </span>
  );
}

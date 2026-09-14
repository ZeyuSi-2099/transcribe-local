import type { ReactNode } from "react";
import { Button } from "../components/Button";
import { Serif } from "../components/Serif";
import { semantic, labelStyle, shadow, fonts } from "../styles/tokens";

interface EmptyStateProps {
  icon: ReactNode;       // 放进统一图标块（声波 / 词典 / 银行卡，按场景换；设计稿 02/03）
  label?: string;        // 可选 eyebrow（如「充值与账单」）；我的转录 / 术语库不带
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
  extra?: ReactNode;     // 主按钮下的次要出口（ghost 链接等），可选
}

// 整页空白页统一模板（第 1 层）：84×64 图标块 + 衬线一句 + 灰说明 + 一个赤陶按钮，间距 20/10/18。
export function EmptyState({ icon, label, title, body, actionLabel, onAction, extra }: EmptyStateProps) {
  return (
    <div
      style={{
        flex: 1,
        padding: "44px 64px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        background: semantic.surface.page,
      }}
    >
      {/* 统一图标块 84×64（圆角 14 · float 底 · subtle 描边 · sm 阴影）—— 图标按场景换，结构不变 */}
      <div
        style={{
          width: 84,
          height: 64,
          borderRadius: 14,
          background: semantic.surface.float,
          border: `1px solid ${semantic.border.subtle}`,
          boxShadow: shadow.sm,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 20,
        }}
      >
        {icon}
      </div>

      {label && <div style={{ ...labelStyle, marginBottom: 10 }}>{label}</div>}

      <Serif size={26} weight={700}>{title}</Serif>

      <p style={{ marginTop: 10, color: semantic.text.muted, fontSize: 13, lineHeight: 1.6, maxWidth: 320 }}>
        {body}
      </p>

      {actionLabel && (
        <div style={{ marginTop: 18 }}>
          <Button primary onClick={onAction}>
            {actionLabel}
          </Button>
        </div>
      )}

      {extra && <div style={{ marginTop: 12 }}>{extra}</div>}
    </div>
  );
}

// 列表内小空态（第 2 层）：40 图标 + 一行无衬线说明 + 一个文字链，紧凑、不喧宾夺主（设计稿 03）。
export function SmallEmpty({ icon, text, linkLabel, onLink }: { icon: ReactNode; text: string; linkLabel?: string; onLink?: () => void }) {
  return (
    <div style={{ padding: "42px 20px", textAlign: "center" }}>
      <div style={{ width: 40, height: 40, borderRadius: 10, background: semantic.surface.sunken, color: semantic.text.muted, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 12 }}>
        {icon}
      </div>
      <div style={{ fontSize: 14, color: semantic.text.secondary }}>{text}</div>
      {linkLabel && (
        <button className="tx-focus" onClick={onLink} style={{ border: "none", background: "transparent", fontFamily: fonts.sans, fontSize: 13, color: semantic.accent.text, cursor: "pointer", marginTop: 10 }}>
          {linkLabel}
        </button>
      )}
    </div>
  );
}

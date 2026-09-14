// SettingsModal.proposed.tsx — 账户设置 · 设计系统 v1 版
// 对照稿：《屏幕04 · 浮窗三件套 · 精修》②
//
// 本机版（与线上不同，Duner 2026-09-14 定）：只留「删除转录记录」，整块改名「数据」。
// 去掉邮箱、登录方式、余额、计费单价、推荐同行、注销账户——本机单用户、不收费。
// 音频保留那一行改说本机的事实：存在这台电脑上，不自动删。
import { useErrText } from "../lib/userErrors";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "../components/Button";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow } from "../styles/tokens";
import { purgeTranscripts } from "../lib/api";

function SettingRow({ label, labelColor, highlight, children }: { label: string; labelColor?: string; highlight?: boolean; children: ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "130px 1fr auto", gap: space.s5, padding: `${space.s4}px ${space.s5}px`, alignItems: "center", background: highlight ? semantic.surface.rowActive : "transparent" }}>
      <div style={{ ...type.label, color: labelColor ?? semantic.text.muted }}>{label}</div>
      {children}
    </div>
  );
}

const Divider = () => <div style={{ borderTop: `1px solid ${semantic.border.subtle}` }} />;

// 危险确认按钮：出现后 3 秒内禁用，防滑点误触
function DangerConfirm({ label, deep, onConfirm }: { label: string; deep?: boolean; onConfirm: () => void }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { const t = setTimeout(() => setArmed(true), 3000); return () => clearTimeout(t); }, []);
  return (
    <button
      className="tx-focus"
      disabled={!armed}
      onClick={onConfirm}
      style={{ padding: "0 14px", height: 32, fontFamily: fonts.sans, fontSize: 13, fontWeight: 500, background: deep ? semantic.danger.fillHover : semantic.danger.fill, color: semantic.text.onAccent, border: "none", borderRadius: radius.sm, cursor: armed ? "pointer" : "wait", opacity: armed ? 1 : 0.45 }}
    >
      {label}
    </button>
  );
}

/** 「删除转录记录」：删稿与音频，术语库留着。
 *  确认按钮上写出条数——「确认删除全部」看不出要失去什么，「确认删除 12 条」看得出。 */
function PurgeTranscripts({ count, onPurged }: { count: number; onPurged?: () => void }) {
  const L = useL();
  const errText = useErrText();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await purgeTranscripts();
      setArmed(false);
      onPurged?.();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingRow label={L("删除转录记录", "Delete transcripts")} highlight={armed}>
      <div style={{ fontSize: 13, color: semantic.text.secondary, lineHeight: 1.6 }}>
        {L("一次性删除全部文字稿与音频，不可恢复。术语库保留。",
           "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.")}
        {err && <div style={{ marginTop: 6, color: semantic.accent.text }}>{err}</div>}
      </div>
      {armed ? (
        <div style={{ display: "flex", gap: space.s2 }}>
          <Button size="sm" ghost onClick={() => { setArmed(false); setErr(null); }}>{L("取消", "Cancel")}</Button>
          <DangerConfirm
            label={busy
              ? L("正在删除…", "Deleting…")
              : L.t("确认删除 {0} 条", count > 1 ? "Delete {0} transcripts" : "Delete {0} transcript", count)}
            onConfirm={() => { if (!busy) void submit(); }}
          />
        </div>
      ) : (
        <Button size="sm" secondary disabled={count === 0} onClick={() => setArmed(true)}>
          {L("删除全部", "Delete all")}
        </Button>
      )}
    </SettingRow>
  );
}

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  jobCount?: number; // 转录条数（「删除全部」要写出条数，0 时按钮禁用）
  onPurged?: () => void; // 删完转录：调用方刷新列表
}

export function SettingsModal({ open, onClose, jobCount = 0, onPurged }: SettingsModalProps) {
  const L = useL();

  // Esc 关闭（标准浮窗行为，与点遮罩/✕ 等效）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div style={{ position: "absolute", inset: 0, background: semantic.surface.overlay, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100, padding: space.s7 }} onClick={onClose}>
      <div role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 640, maxHeight: "100%", background: semantic.surface.raised, borderRadius: radius.lg, boxShadow: shadow.lg, overflow: "hidden", display: "flex", flexDirection: "column", animation: "floatUp .24s ease both" }}>
        {/* 头部 */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: `${space.s4}px ${space.s6}px`, borderBottom: `1px solid ${semantic.border.default}`, flex: "0 0 auto" }}>
          <span style={{ ...type.h2 }}>{L("设置", "Settings")}</span>
          <button className="tx-focus" onClick={onClose} aria-label={L("关闭", "Close")}
            style={{ width: 32, height: 32, display: "grid", placeItems: "center", borderRadius: radius.sm, border: "none", background: semantic.surface.sunken, color: semantic.text.muted, fontSize: 13, cursor: "pointer" }}>✕</button>
        </div>

        {/* 内容（可滚动） */}
        <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: `${space.s5}px ${space.s6}px ${space.s6 + 4}px` }}>
          <div style={{ ...type.label, marginBottom: space.s3, paddingLeft: space.s5 }}>{L("数据", "Data")}</div>
          <div style={{ border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden" }}>
            <SettingRow label={L("存放位置", "Storage")}>
              <div style={{ fontSize: 13, color: semantic.text.secondary, lineHeight: 1.6 }}>
                {L("音频和文字稿都存在这台电脑上，不会自动删除。",
                   "Audio and transcripts are stored on this computer and are never deleted automatically.")}
              </div>
              <span />
            </SettingRow>
            <Divider />
            <PurgeTranscripts count={jobCount} onPurged={onPurged} />
          </div>
        </div>
      </div>
    </div>
  );
}

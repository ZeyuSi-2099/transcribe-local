// SettingsModal.proposed.tsx — 账户设置 · 设计系统 v1 版
// 对照稿：《屏幕04 · 浮窗三件套 · 精修》②
//
// 本机版（与线上不同，Duner 2026-09-14 定）：账户那一套（邮箱、余额、计费、推荐、注销）全部去掉。
// 留三块：模型后端（定字、术语库起草、后处理共用）· 数据去哪（按当前设置实时算）· 数据（存放位置 + 删除转录记录）。
import { useErrText } from "../lib/userErrors";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "../components/Button";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow } from "../styles/tokens";
import { getBackend, probeBackend, purgeTranscripts, saveBackend, type BackendPresetId, type BackendView, type DataFlowItem } from "../lib/api";

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

/** 模型后端 + 数据去哪。放在一个组件里：换了后端，「数据去哪」要跟着同一次保存的结果变。 */
function BackendAndDataFlow() {
  const L = useL();
  const [v, setV] = useState<BackendView | null>(null);
  const [failed, setFailed] = useState(false);
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [probe, setProbe] = useState<{ ok: boolean; why: string } | "busy" | null>(null);

  useEffect(() => {
    let alive = true;
    getBackend().then((b) => { if (alive) { setV(b); setModel(b.current.model); } }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);

  const save = async (body: { preset: string; model?: string; webSearch?: boolean }) => {
    setBusy(true); setProbe(null);
    try {
      const b = await saveBackend(body);
      setV(b); setModel(b.current.model);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const presetTexts: Record<BackendPresetId, [string, string]> = {
    deepseek: ["DeepSeek API", L("效果稳、不占电脑资源 · 稿子文字会发给 DeepSeek", "Stable results, light on your computer · transcript text is sent to DeepSeek")],
    ollama: [L("本机 Ollama", "Local Ollama"), L("全程不出本机 · 需要先装好并启动 Ollama", "Nothing leaves this computer · install and start Ollama first")],
    lmstudio: [L("本机 LM Studio / mlx", "Local LM Studio / mlx"), L("全程不出本机 · 需要先启动本机模型服务", "Nothing leaves this computer · start the local model server first")],
    claude: [L("Claude 订阅", "Claude subscription"), L("只用于定字 · 术语库起草和后处理需要 API 或本机模型", "Proofreading only · glossary drafting and post-processing need an API or a local model")],
    custom: [L("自定义（配置文件里手改的）", "Custom (set in the config file)"), ""],
  };
  const presetText = (id: BackendPresetId) => presetTexts[id];

  const whatText = (w: DataFlowItem["what"]) => ({
    audio: L("录音", "Audio"),
    transcript: L("定字时的稿子文字", "Transcript text during proofreading"),
    glossary: L("术语库起草与检查", "Glossary drafting and checks"),
    postprocess: L("后处理的稿子文字", "Transcript text in post-processing"),
    search: L("联网核实的搜索词", "Search terms for online checks"),
  })[w];
  const destText = (f: DataFlowItem) =>
    f.dest === "local" ? L("不离开这台电脑", "Stays on this computer")
      : f.dest === "remote" ? L.t("发给 {0}", "Sent to {0}", f.host)
        : f.dest === "claude" ? L("发给 Anthropic（Claude 订阅）", "Sent to Anthropic (Claude subscription)")
          : L("当前后端用不了这一项", "Not available with this backend");

  if (failed && !v) {
    return <div style={{ fontSize: 13, color: semantic.accent.text, marginBottom: space.s6, paddingLeft: space.s5 }}>{L("读不到模型后端设置。", "Couldn't load the model backend settings.")}</div>;
  }
  if (!v) return null;
  const cur = v.current;
  const kind = v.presets.find((p) => p.id === cur.preset)?.kind;

  return (
    <>
      <div style={{ ...type.label, marginBottom: space.s2, paddingLeft: space.s5 }}>{L("模型后端", "Model backend")}</div>
      <div style={{ fontSize: 12, color: semantic.text.muted, marginBottom: space.s3, paddingLeft: space.s5 }}>
        {L("定字、术语库起草、后处理共用这一个设置。", "Proofreading, glossary drafting and post-processing all use this one setting.")}
      </div>
      <div style={{ border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden", marginBottom: space.s6 }}>
        <div role="radiogroup" aria-label={L("模型后端", "Model backend")} style={{ padding: `${space.s2}px ${space.s3}px` }}>
          {v.presets.map((p) => {
            const on = p.id === cur.preset;
            const [name, desc] = presetText(p.id);
            return (
              <button key={p.id} type="button" role="radio" aria-checked={on} disabled={busy}
                className="tx-focus"
                onClick={() => { if (!on) void save({ preset: p.id }); }}
                style={{ display: "grid", gridTemplateColumns: "18px minmax(0, 1fr)", gap: space.s3, width: "100%", textAlign: "left", padding: `${space.s2}px ${space.s3}px`, border: "none", borderRadius: radius.sm, background: on ? semantic.accent.bgTint : "transparent", cursor: on || busy ? "default" : "pointer", fontFamily: fonts.sans }}>
                <span aria-hidden style={{ width: 14, height: 14, marginTop: 3, borderRadius: 7, border: `1.5px solid ${on ? semantic.accent.brand : semantic.border.strong}`, boxShadow: on ? `inset 0 0 0 3px ${semantic.surface.raised}` : "none", background: on ? semantic.accent.brand : "transparent" }} />
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: 14, fontWeight: 500, color: semantic.text.primary }}>{name}</span>
                  <span style={{ display: "block", fontSize: 12, lineHeight: 1.5, color: semantic.text.muted }}>{desc}</span>
                </span>
              </button>
            );
          })}
          {cur.preset === "custom" && (
            <div style={{ padding: `${space.s2}px ${space.s3}px`, fontSize: 13, color: semantic.text.secondary }}>{presetText("custom")[0]} · {cur.base_url}</div>
          )}
        </div>
        <Divider />
        <SettingRow label={L("模型", "Model")}>
          <input id="tx-backend-model" className="tx-focus" value={model} onChange={(e) => setModel(e.target.value)}
            disabled={busy || cur.preset === "custom"}
            style={{ minWidth: 0, fontFamily: fonts.mono, fontSize: 13, padding: "6px 10px", border: `1px solid ${semantic.border.default}`, borderRadius: radius.sm, background: semantic.surface.page, color: semantic.text.primary }} />
          <Button size="sm" secondary disabled={busy || model.trim() === cur.model || !model.trim() || cur.preset === "custom"}
            onClick={() => void save({ preset: cur.preset, model })}>{L("保存", "Save")}</Button>
        </SettingRow>
        {v.key.env && (
          <>
            <Divider />
            <div style={{ padding: `${space.s3}px ${space.s5}px`, fontSize: 12, color: v.key.set ? semantic.text.muted : semantic.warning.text }}>
              {v.key.set
                ? L.t("密钥从环境变量 {0} 读取 · 已设置", "Key is read from environment variable {0} · set", v.key.env)
                : L.t("密钥从环境变量 {0} 读取 · 未设置", "Key is read from environment variable {0} · not set", v.key.env)}
            </div>
          </>
        )}
        {kind === "api" && (
          <>
            <Divider />
            <label style={{ display: "flex", alignItems: "center", gap: space.s3, padding: `${space.s3}px ${space.s5}px`, fontSize: 13, color: semantic.text.secondary, cursor: busy ? "default" : "pointer" }}>
              <input type="checkbox" id="tx-backend-websearch" checked={cur.web_search} disabled={busy}
                onChange={(e) => void save({ preset: cur.preset, webSearch: e.target.checked })} />
              <span>{L("定不下的专名联网核实（博查）", "Verify unclear names online (Bocha)")}</span>
              {!v.bochaKey && <span style={{ fontSize: 12, color: semantic.text.muted }}>{L("需要环境变量 BOCHA_API_KEY", "Needs environment variable BOCHA_API_KEY")}</span>}
            </label>
          </>
        )}
        <Divider />
        <div style={{ display: "flex", alignItems: "center", gap: space.s3, padding: `${space.s3}px ${space.s5}px`, flexWrap: "wrap" }}>
          <Button size="sm" secondary disabled={busy || probe === "busy"}
            onClick={() => { setProbe("busy"); probeBackend().then(setProbe).catch(() => setProbe({ ok: false, why: "" })); }}>
            {probe === "busy" ? L("测试中…", "Testing…") : L("测试连接", "Test connection")}
          </Button>
          {probe && probe !== "busy" && (
            <span role="status" style={{ fontSize: 13, color: probe.ok ? semantic.success.text : semantic.warning.text, overflowWrap: "anywhere" }}>
              {probe.ok ? L("连接正常", "Connected") : L.t("连不上：{0}", "Not reachable: {0}", probe.why)}
            </span>
          )}
        </div>
      </div>

      <div style={{ ...type.label, marginBottom: space.s3, paddingLeft: space.s5 }}>{L("数据去哪", "Where your data goes")}</div>
      <div data-testid="data-flow" style={{ border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden", marginBottom: space.s6 }}>
        {v.dataFlow.map((f, i) => (
          <div key={f.what}>
            {i > 0 && <Divider />}
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: space.s4, padding: `${space.s3}px ${space.s5}px`, fontSize: 13 }}>
              <span style={{ color: semantic.text.secondary }}>{whatText(f.what)}</span>
              <span data-dest={f.dest} style={{ color: f.dest === "local" ? semantic.success.text : f.dest === "off" ? semantic.text.muted : semantic.warning.text, textAlign: "right" }}>{destText(f)}</span>
            </div>
          </div>
        ))}
      </div>
    </>
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
          <BackendAndDataFlow />
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

// 首次启动：下载识别模型（本地独有页面）。
//
// 转录全在这台电脑上跑，没有模型就一单都转不了——所以缺模型时这一屏挡在应用前面，
// 下完自动进应用。要下哪些、多大、下到哪、进度，全由后端算（app/local_models.py），这里只显示。
import { useEffect, useState } from "react";
import { Button } from "../components/Button";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow } from "../styles/tokens";
import { getModels, pullModels, type ModelsStatus } from "../lib/api";
import { useFullHeightScreen } from "../lib/fullHeightScreen";

const fmtMb = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`);
const POLL_MS = 1000;

export function ModelSetup({ initial, onReady }: { initial: ModelsStatus; onReady: () => void }) {
  useFullHeightScreen();   // 与应用外壳一样铺满一屏，卡片才居中（不铺满时卡片贴在页顶）
  const L = useL();
  const [st, setSt] = useState(initial);
  const running = st.download.running;

  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => { getModels().then(setSt).catch(() => {}); }, POLL_MS);
    return () => window.clearInterval(t);
  }, [running]);
  useEffect(() => { if (st.ready) onReady(); }, [st.ready, onReady]);

  const start = () => { pullModels().then(setSt).catch(() => {}); };
  const total = st.download.totalBytes || st.missingMb * 1048576;
  const done = Math.min(st.download.doneBytes, total);
  const pct = total ? Math.round((done / total) * 100) : 0;

  return (
    <div className="tx-scroll" style={{ height: "100%", display: "grid", placeItems: "center", padding: space.s6, overflowY: "auto" }}>
      <div style={{ width: "100%", maxWidth: 520, background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.lg, boxShadow: shadow.md, padding: `${space.s6}px ${space.s6 + 4}px` }}>
        <h1 style={{ ...type.h2, margin: 0 }}>{L("先下载识别模型", "Download the speech models first")}</h1>
        <p style={{ margin: `${space.s3}px 0 0`, fontSize: 14, lineHeight: 1.7, color: semantic.text.secondary }}>
          {L.t("转录全在这台电脑上跑，第一次用要先下载约 {0} 的模型。下完就不用再下。",
               "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.",
               fmtMb(initial.missingMb))}   {/* 用打开时那个数：边下边减的话，这句话会在下载途中自己改口 */}
        </p>
        <div style={{ marginTop: space.s2, fontSize: 12, color: semantic.text.muted, fontFamily: fonts.mono, overflowWrap: "anywhere" }}>
          {L.t("存放位置：{0}", "Saved to: {0}", st.cacheDir)}
        </div>

        {running ? (
          <div style={{ marginTop: space.s5 }}>
            <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
              style={{ height: 8, borderRadius: radius.pill, background: semantic.surface.sunken, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: semantic.accent.brand, transition: "width .4s ease" }} />
            </div>
            <div style={{ marginTop: space.s2, fontSize: 12, color: semantic.text.muted, fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" }}>
              {L.t("正在下载 {0} · {1} / {2}", "Downloading {0} · {1} / {2}",
                   st.download.current ?? "…", fmtMb(done / 1048576), fmtMb(total / 1048576))}
            </div>
          </div>
        ) : (
          <div style={{ marginTop: space.s5, display: "flex", flexDirection: "column", gap: space.s3 }}>
            {st.download.error && (
              <div role="alert" style={{ fontSize: 13, lineHeight: 1.6, color: semantic.warning.text, overflowWrap: "anywhere" }}>
                {L.t("下载中断了：{0}", "Download stopped: {0}", st.download.error)}
              </div>
            )}
            <Button primary full size="lg" onClick={start}>
              {st.download.error ? L("重试", "Retry") : L("开始下载", "Start download")}
            </Button>
          </div>
        )}

        <p style={{ margin: `${space.s5}px 0 0`, fontSize: 12, lineHeight: 1.6, color: semantic.text.muted }}>
          {L("已经有模型？设环境变量 TRANSCRIBE_LOCAL_MODELS 指到模型目录，再刷新这一页。",
             "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.")}
        </p>
      </div>
    </div>
  );
}

// ErrorView.proposed.tsx — 错误态 · 设计系统 v1 版
// 对照稿：《屏幕05 · 登录欢迎错误 · 精修》③
// 变更：四种 kind 分两级语义——lowBalance = 软墙（警告金 ⚠），其余 = 真错误（赤陶 ✕）；
//      标题 48 → 28/600；数字金额 mono；failed 增加「下载已成功部分」真按钮；取消改 ghost。
import { semantic, fonts, type, space } from "../../styles/tokens";
import { useL } from "../../lib/i18n";

import { Button } from "../../components/Button";
import { Serif } from "../../components/Serif";

export type ErrorKind = "lowBalance" | "unsupported" | "declined" | "failed";

interface ErrorViewProps {
  kind: ErrorKind;
  onReset: () => void;
  onLink: () => void;
  onDownloadPartial?: () => void; // failed 时的"下载已成功部分"
}

const monoNum: React.CSSProperties = { fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" };

export function ErrorView({ kind, onReset, onLink, onDownloadPartial }: ErrorViewProps) {
  const L = useL();

  // 软墙（不是事故）用警告金；真错误用赤陶
  const soft = kind === "lowBalance";
  const labelColor = soft ? semantic.warning.text : semantic.accent.text;
  const glyph = soft ? "⚠" : "✕";
  const tileBg = soft ? semantic.warning.bg : semantic.accent.bgTint; // 图标块底：软墙暖金 / 真错赤陶

  const map: Record<ErrorKind, { label: string; title: string; body: React.ReactNode; action: string; onClick: () => void }> = {
    unsupported: {
      label: L("文件不被支持", "Unsupported file"),
      title: L("我们读不懂这种格式。", "We can't read that format."),
      body: L.x("目前只支持 .mp3 .m4a .wav .mp4 .mov。你上传的文件 {0} 暂时无法处理 —— 转个格式我们再试一次？",
        "We support .mp3 .m4a .wav .mp4 .mov for now. {0} can't be processed — convert it and try again?",
        <b key="f" style={{ color: semantic.text.primary }}>demo.aiff</b>),
      action: L("换一个文件", "Choose another"),
      onClick: onReset,
    },
    lowBalance: {
      label: L("余额不足", "Low balance"),
      title: L("余额不够这个文件。", "Not enough balance for this file."),
      body: L.x("你上传的 {0} 约 {1} 分钟，按这门语言的单价计费后超过了当前余额。充值后即可开始转录，文件已就绪。",
        "{0} is about {1} min — at this language's rate that's more than your balance. Top up to start — your file is ready.",
        <b key="f" style={{ color: semantic.text.primary }}>产品周会.m4a</b>, <span key="m" style={monoNum}>42</span>),
      action: L("去充值 →", "Top up →"),
      onClick: onLink,
    },
    declined: {
      label: L("扣款失败", "Payment declined"),
      title: L("这笔充值没走通。", "That top-up didn't go through."),
      body: L.x("充值时银行卡被拒绝，{0}。换一张有效的卡再试一次即可。",
        "Your card was declined during top-up — {0}. Try again with a different card.",
        <b key="u" style={{ color: semantic.success.text }}>{L("余额未变动", "your balance is unchanged")}</b>),
      action: L("重新充值", "Try again"),
      onClick: onLink,
    },
    failed: {
      label: L("转录失败", "Transcription failed"),
      title: L("出了点小问题。", "Something went wrong."),
      body: L.x("音频中段有 {0} 秒无法解析（可能损坏）。{1}你可以下载已成功的部分，或重试一次。",
        "{0} seconds in the middle couldn't be parsed (possibly corrupt). {1} Download the partial result, or retry.",
        <span key="s" style={monoNum}>12</span>,
        <b key="c" style={{ color: semantic.success.text }}>{L("这次不会计费。", "You weren't charged.")}</b>),
      action: L("重新转录", "Try again"),
      onClick: onReset,
    },
  };

  const e = map[kind] ?? map.failed;

  // 整页错误卡（设计稿 04 出错）：沿用空白页「48px 图标块 + 衬线一句 + 主出口」DNA，居中、一套结构
  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, alignItems: "center", justifyContent: "center", textAlign: "center", maxWidth: 520, margin: "0 auto" }}>
      <div style={{ width: 48, height: 48, borderRadius: 12, background: tileBg, color: labelColor, display: "grid", placeItems: "center", fontSize: 22, marginBottom: space.s4 }} aria-hidden>{glyph}</div>
      <div style={{ ...type.label, color: labelColor, marginBottom: space.s3 }}>{e.label}</div>
      <Serif size={26} weight={700}>{e.title}</Serif>
      <p style={{ marginTop: space.s3, color: semantic.text.secondary, fontSize: 14, lineHeight: 1.75, maxWidth: 460 }}>{e.body}</p>
      <div style={{ display: "flex", gap: space.s3, marginTop: space.s6, justifyContent: "center", flexWrap: "wrap" }}>
        <Button primary onClick={e.onClick}>{e.action}</Button>
        {kind === "failed" && onDownloadPartial && (
          <Button secondary onClick={onDownloadPartial}>{L("下载已成功部分", "Download partial")}</Button>
        )}
        <Button ghost onClick={onReset}>{L("取消", "Cancel")}</Button>
      </div>
    </div>
  );
}

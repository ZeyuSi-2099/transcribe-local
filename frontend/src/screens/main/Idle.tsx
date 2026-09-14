// Idle.proposed.tsx — 上传页 · 设计系统 v1 版
// 对照稿：《屏幕03 · 上传页 · 对比》
// 变更：
//  1. 余额软墙提前到卡内：选中文件后若 预估费用 > 余额，就地显示预警条，
//     主按钮变「先充值 →」（onTopUp 带缺口金额预填充值弹窗）；余额够则显示
//     「开始转录」+「转录后约剩 $x」。整页错误态（屏幕9）仍兜底并发场景。
//  2. 预估卡数字 mono + tabular；标签 label 体；卡头 h2 17/600；
//     MULTI-ENGINE 9.5→11px pill；12.5px 半像素字号归位。
//  3. 拖拽区：默认 border.strong 虚线；拖入 = 赤陶虚线 + tint 底。
// 新增 props：balance（当前余额）、onTopUp（打开充值弹窗，参数为建议充值额）。

import { useRef, useState } from "react";
import { semantic, fonts, type, space, radius, shadow, motion } from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import { fmtClock } from "../../lib/format";
import { rateFor, usd, RATE_PER_HOUR } from "../../lib/pricing";
import { Button } from "../../components/Button";
import { RecentPanel, FirstRunPanel } from "./RecentPanel";
import type { HistoryItem } from "../../lib/sampleData";
import { LangPicker } from "./LangPicker";
import { GlossaryBar } from "../../components/GlossaryBar";
import type { Glossary } from "../../lib/api";

interface IdleProps {
  onStart: (file: File, durationSec: number | null) => void;
  lang: string;
  setLang: (id: string) => void;
  balance: number;
  onTopUp?: (suggested: number) => void;
  glossaries?: Glossary[];                 // 全部术语库（选择器用）
  selectedGlossaryId?: string | null;      // 本次转录用哪本（null = 不使用）
  onSelectGlossary?: (id: string | null) => void;
  onOpenGlossary?: () => void;             // 跳术语库整页
  freeLeftSeconds?: number;                // 免费额度剩余秒；预估与软墙都要算入抵扣
  freeLimited?: boolean;                   // IP 闸触发：额度 0 + 「同网络已有试用」提示
  /** 已完成的转录（最近的排在前）。左栏拿它当「你的东西」，没有就整卡居中——见下方注释。
   *  ⚠️ `undefined` 与 `[]` **不是一个意思**：前者＝还没从后端拉回来，后者＝确实一份都没有。
   *  混成一个的话，有历史的老用户每次进这一屏都会先单栏居中、再跳成两栏。 */
  recent?: HistoryItem[];
  onOpenRecent?: (item: HistoryItem) => void;
  onOpenHistory?: () => void;
}

// 与后端 _ALLOWED_UPLOAD_EXT 对齐：音频 + 视频（视频由后端 P0 抽音频）
const ALLOWED_EXT = [".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".amr",
                     ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"];
// 完整清单只在 title（悬停）里给：卡脚原本平铺 14 个扩展名占满两行，
// 而它回答的问题只是「我这个文件能不能传」——答案对 99% 的人是「能」。
// 常见的四个 + 一句「等音视频格式」说得完，真要查的人把鼠标停上去。
const FORMATS = ".mp3 .m4a .wav .flac .aac .ogg .opus .amr · .mp4 .mov .m4v .webm .mkv .avi";
const FORMATS_SHORT = "MP3 · M4A · WAV · MP4";

function fmtSize(bytes: number): string {
  if (bytes >= 1 << 30) return (bytes / (1 << 30)).toFixed(1) + " GB";
  if (bytes >= 1 << 20) return (bytes / (1 << 20)).toFixed(1) + " MB";
  if (bytes >= 1 << 10) return (bytes / (1 << 10)).toFixed(0) + " KB";
  return bytes + " B";
}

const EQ_PROFILE = [0.5, 0.82, 0.6, 1, 0.55, 0.86, 0.46, 0.92, 0.64, 0.74];
function Eq({ count, color, height, active = true }: { count: number; color: string; height: number; active?: boolean }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "flex-end", gap: 3, height }} aria-hidden>
      {Array.from({ length: count }).map((_, i) => (
        <span key={i} style={{ width: 3, borderRadius: 1, background: color, height: `${Math.round(EQ_PROFILE[i % EQ_PROFILE.length] * 100)}%`, transformOrigin: "bottom", animation: active ? `barPulse ${(0.7 + (i % 3) * 0.2).toFixed(2)}s ease-in-out ${(i * 0.09).toFixed(2)}s infinite` : undefined }} />
      ))}
    </span>
  );
}

const FileGlyph = () => (
  <svg width="20" height="20" viewBox="0 0 22 22" fill="none" aria-hidden="true">
    <rect x="4" y="2.5" width="11.5" height="17" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
    <path d="M7.5 9.5h7M7.5 12.5h7M7.5 15.5h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);

const monoNum: React.CSSProperties = { fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" };

export function Idle({ onStart, lang, setLang, balance, onTopUp, glossaries, selectedGlossaryId, onSelectGlossary, onOpenGlossary, freeLeftSeconds, freeLimited, recent, onOpenRecent, onOpenHistory }: IdleProps) {
  const L = useL();
  const [drag, setDrag] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [durSec, setDurSec] = useState<number | null>(null);
  const [fileErr, setFileErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = () => inputRef.current?.click();

  const ingest = (f: File) => {
    // 客户端格式校验：非音视频就地报错、不收进卡片，不让它提交成失败任务进「我的转录」
    const ext = f.name.includes(".") ? "." + f.name.split(".").pop()!.toLowerCase() : "";
    if (!ALLOWED_EXT.includes(ext)) {
      setFileErr(L.t("不支持的格式{0}，请上传音频或视频文件", "Unsupported format{0} — please upload an audio or video file", ext ? ` ${ext}` : ""));
      setFile(null); setDurSec(null);
      return;
    }
    setFileErr(null);
    setFile(f);
    setDurSec(null);
    try {
      if (typeof URL.createObjectURL !== "function") return;
      const url = URL.createObjectURL(f);
      const a = document.createElement("audio");
      a.preload = "metadata";
      a.onloadedmetadata = () => { setDurSec(Number.isFinite(a.duration) && a.duration > 0 ? a.duration : null); URL.revokeObjectURL(url); };
      a.onerror = () => { setDurSec(null); URL.revokeObjectURL(url); };
      a.src = url;
    } catch { setDurSec(null); }
  };
  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) ingest(f);
    e.target.value = "";
  };

  // 单价（分/分钟口径，用于算预估费用）；27 门同价，lang 现在不影响结果。
  // 展示一律按小时（RATE_PER_HOUR），别把这个数直接印到界面上。
  const rate = rateFor(lang);
  // 免费额度抵扣（预估口径，权威在后端事务里）：先抵免费秒，剩余按单价。
  // 不按文件长短设门槛——额度本来就是用多少扣多少，长文件只是把额度一次用完
  const freeUseSec = durSec != null
    ? Math.min(Math.max(0, freeLeftSeconds ?? 0), durSec) : 0;
  const cost = durSec != null ? (Math.max(0, durSec - freeUseSec) / 60) * rate : null;
  const shortfall = cost != null ? Math.max(0, cost - balance) : 0;
  const blocked = cost != null && shortfall > 0; // 软墙：提前画在卡里（免费覆盖的部分不算钱）

  // 顶层布局：grid 两列（hero | 上传卡），垂直居中。
  // margin:auto 在这里是安全的：free space 为负时 auto 外边距按 0 算（2026-08-21 实测），
  // 卡片高过容器就退回顶端对齐、照常滚。**会裁顶且滚不到的是裸 justify-content:center**，
  // 别在外层滚动容器上改用它（要居中就写 `safe center`）。
  // 注：居中下切界面语言会有轻微上下跳动（两列高度随文案变）——已与用户确认，接受此跳动换取居中视觉。
  // ⚠️ 左栏放什么，2026-08-31 换过一次。
  // 在此之前这里挂的是登录屏那个卖点组件（HeroPitch）——**两屏共用同一个组件**，也就是说一个已经付过钱、
  // 正准备传第 20 份录音的用户，屏幕左边 40% 还在向他推销当时的营销主标。
  // 那不是产品决策，是登录屏的组件被顺手复用到了登录之后。
  // 现在放「你自己的东西」：最近几份稿子，点一下就回去。
  // **一份都没有的时候整卡居中**——新用户的第一次上传，这一屏只有一件事要做，
  // 那本身就是最好的引导；在旁边补一栏文字只会把唯一那件事挤到一边。
  const recentDone = (recent ?? []).filter((r) => r.st === "done" && !r.expired).slice(0, 5);
  // ⚠️ **栅格恒定两栏，不随数据变**（2026-09-01 第二次修）。
  // 任务列表是挂载之后才拉回来的，所以任何「按数据决定几栏」的写法都意味着**这一屏的布局
  // 要等半秒**：有历史的老用户每次进来都先看到卡片居中、再看它横着跳到右边去。
  // 第一次修用了个本地提示位「照上次的结论猜」——在无痕窗口里那个位子永远是空的，等于没修
  //（Duner 实测报回来的就是这个）。⇒ 现在不猜了：左栏永远占着位，只是换内容。
  //   有稿子 → 最近的转录 · 确实没有 → 第一次上传的三步 · 还没拉回来 → 空着（内容淡入，不推动任何东西）
  const loaded = recent !== undefined;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 468px", gap: "clamp(40px,6vw,80px)", alignItems: "center", width: "100%", maxWidth: 1180, margin: "auto", minWidth: 808 }}>
      {/* ⚠️ 文件框用**视觉隐藏**而不是 display:none（2026-09-01）：`display:none` 的元素在部分
          浏览器上是「不存在」的，程序化 `.click()` 会被忽略而且不报错——症状正是「按钮点不动、
          拖拽却好使」。视觉隐藏（绝对定位 + 0 尺寸 + 透明）留在渲染树里，`.click()` 一定生效。
          ⚠️ 别给它 `tabIndex={-1}` 之外的可聚焦性：它不该出现在 Tab 序列里（触发口是那颗按钮）。 */}
      <input
        ref={inputRef}
        type="file"
        accept="audio/*,video/*"
        tabIndex={-1}
        aria-hidden
        // ⚠️ 不加 `pointer-events: none`：它对程序化 .click() 毫无作用，却会让
        // testing-library 的 upload() 拒绝交互（「元素不可指向」），把一整组测试打红。
        style={{ position: "absolute", width: 1, height: 1, opacity: 0, overflow: "hidden", clip: "rect(0 0 0 0)", clipPath: "inset(50%)", whiteSpace: "nowrap", border: 0, padding: 0, margin: -1 }}
        onChange={onFile}
      />

      {/* 左栏三态。⚠️ 「还没拉回来」那一态**必须渲染一个占位元素**：栅格按出现顺序放，
          不占的话这张上传卡会掉进 1fr 那一列去。 */}
      {!loaded ? <div aria-hidden />
        : recentDone.length > 0
        ? <RecentPanel items={recentDone} onOpen={onOpenRecent} onOpenAll={onOpenHistory} />
        : <FirstRunPanel />}

      {/* 上传卡 */}
      {/* fill-mode 用 backwards（不是 both）：动画前用 from 帧防闪现，动画后回到无 transform 原位——
          否则 floatUp 结束态会残留 identity transform matrix，把卡内 fixed 下拉遮罩钉在卡内、点卡外关不掉 */}
      <div style={{ background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.lg, padding: `${space.s6}px ${space.s6 + 4}px`, boxShadow: shadow.md, animation: "floatUp .55s .08s ease backwards" }}>
        {/* 卡头：h2 体（不与 hero 抢戏） */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: space.s3, paddingBottom: space.s4, marginBottom: space.s5, borderBottom: `1px solid ${semantic.border.subtle}` }}>
          <div>
            <div style={{ ...type.h2 }}>{L("新建转录", "New transcription")}</div>
            <div style={{ fontSize: 12, color: semantic.text.muted, marginTop: 4 }}>{L("上传音频，得到一份能直接用的文字稿", "Upload audio — a transcript ready to use")}</div>
          </div>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontFamily: fonts.mono, fontSize: 11, letterSpacing: 0.7, color: semantic.accent.text, background: semantic.accent.bgTint, border: `1px solid ${semantic.accent.bgSoft}`, padding: "4px 9px", borderRadius: radius.pill, whiteSpace: "nowrap" }}>
            <Eq count={4} color={semantic.accent.brand} height={13} />
            MULTI-ENGINE
          </span>
        </div>

        <div style={{ marginBottom: space.s5 }}>
          <LangPicker value={lang} onChange={setLang} />
        </div>
        <div style={{ marginBottom: space.s5 }}>
        </div>

        {/* 转录前术语库选择器（选哪本/不使用；不打断主流程，没库也能直接转） */}
        <div style={{ marginBottom: space.s5 }}>
          <GlossaryBar
            glossaries={glossaries ?? []}
            selectedId={selectedGlossaryId ?? null}
            onSelect={onSelectGlossary ?? (() => {})}
            onOpenFull={onOpenGlossary ?? (() => {})}
          />
        </div>

        {/* 文件区 */}
        {!file ? (
          <>
          <div
            onClick={pick}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) ingest(f); }}
            style={{ border: `1.5px dashed ${fileErr ? semantic.warning.icon : drag ? semantic.accent.brand : semantic.border.strong}`, background: drag ? semantic.accent.bgTint : semantic.surface.page, borderRadius: radius.md, padding: `${space.s6}px ${space.s5}px`, display: "flex", flexDirection: "column", alignItems: "center", gap: space.s3, cursor: "pointer", transition: `border-color ${motion.fast}, background ${motion.fast}` }}
          >
            <Eq count={7} color={drag ? semantic.accent.brand : semantic.text.ghost} height={22} active={drag} />
            <Button primary size="md">{L("选择文件", "Choose file")} &nbsp;→</Button>
            <div style={{ fontSize: 12, color: semantic.text.muted }}>{drag ? L("松手即可上传", "Drop to upload") : L("或把文件拖到这里", "or drop your file here")}</div>
          </div>
          {fileErr && (
            // 行内紧凑警告条（设计稿 04 出错）：26px 暖金图标块 + 一句原因 + 出口
            <div role="alert" style={{ display: "flex", alignItems: "center", gap: space.s3, marginTop: space.s3, background: semantic.surface.raised, border: `1px solid ${semantic.border.subtle}`, borderLeft: `3px solid ${semantic.warning.icon}`, borderRadius: radius.sm, padding: `${space.s3 - 2}px ${space.s3}px` }}>
              <span aria-hidden style={{ flex: "0 0 auto", width: 26, height: 26, borderRadius: 7, background: semantic.warning.bg, color: semantic.warning.text, display: "grid", placeItems: "center", fontSize: 14 }}>⚠</span>
              <span style={{ flex: 1, fontSize: 12.5, lineHeight: 1.45, color: semantic.text.secondary }}>{fileErr}</span>
              <button className="tx-focus" onClick={pick} style={{ flex: "0 0 auto", border: "none", background: "transparent", fontSize: 12.5, fontWeight: 500, color: semantic.accent.text, cursor: "pointer", fontFamily: fonts.sans }}>{L("重新选择", "Choose again")}</button>
            </div>
          )}
          </>
        ) : (
          <div style={{ background: semantic.surface.page, border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, padding: `${space.s3}px ${space.s4 - 1}px` }}>
            <div style={{ display: "flex", alignItems: "center", gap: space.s3 }}>
              <span style={{ width: 38, height: 38, borderRadius: radius.sm, background: semantic.accent.bgTint, color: semantic.accent.text, display: "grid", placeItems: "center", flex: "0 0 auto" }}>
                <FileGlyph />
              </span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontFamily: fonts.sans, fontSize: 14, fontWeight: 600, color: semantic.text.primary, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={file.name}>{file.name}</div>
                <div style={{ display: "flex", alignItems: "center", gap: space.s2, fontSize: 12, color: semantic.text.muted, marginTop: 3 }}>
                  <span style={monoNum}>{durSec != null ? fmtClock(durSec) : L("时长解码中…", "reading…")}</span>
                  <i style={{ width: 3, height: 3, borderRadius: "50%", background: semantic.text.ghost }} />
                  <span>{fmtSize(file.size)}</span>
                </div>
              </div>
              <button className="tx-focus" onClick={() => { setFile(null); setDurSec(null); }} aria-label={L("移除", "Remove")} style={{ border: 0, background: semantic.surface.sunken, color: semantic.text.muted, fontSize: 12, cursor: "pointer", width: 28, height: 28, borderRadius: 7, display: "grid", placeItems: "center" }}>✕</button>
            </div>

            {/* 预估卡：数字 mono + tabular */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: space.s2, margin: `${space.s3}px 0` }}>
              <div style={{ background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.sm, padding: `${space.s3}px ${space.s3}px` }}>
                <div style={{ ...type.label }}>{L("音频时长", "Length")}</div>
                <div style={{ ...type.monoStat, color: semantic.text.primary, marginTop: 4 }}>{durSec != null ? fmtClock(durSec) : "—"}</div>
              </div>
              <div style={{ background: semantic.surface.raised, border: `1px solid ${semantic.border.default}`, borderRadius: radius.sm, padding: `${space.s3}px ${space.s3}px` }}>
                <div style={{ ...type.label }}>{L("预估费用", "Est. cost")}</div>
                <div style={{ ...type.monoStat, color: blocked ? semantic.warning.text : semantic.text.primary, marginTop: 4 }}>
                  {cost != null ? usd(cost) : "—"}
                </div>
                {/* 单价独占一行：挤在预估数字后面会断行。
                    ⚠️ 单位是**小时**——站上其余每一处（左栏 / 定价页 / 条款 / 充值浮窗）都按小时报价，
                    这里若改回按分钟，同一屏上会并排出现两个相差六十倍的数，读者要自己换算才知道贵不贵。
                    （注释里不许出现金额字面量：pricing.guard 扫全文，复述被禁的写法会当场自伤。） */}
                <div style={{ ...monoNum, fontSize: 11, color: semantic.text.muted, marginTop: 2, whiteSpace: "nowrap" }}>
                  {usd(RATE_PER_HOUR)}/{L("小时", "hour")}
                  {freeUseSec > 0 && (
                    <span style={{ color: semantic.success.text, marginLeft: 5 }}>
                      {L.t("已抵免费 {0}", "free −{0}", fmtClock(freeUseSec))}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {durSec == null ? (
              <div style={{ fontSize: 12, color: semantic.text.muted, margin: `-${space.s1}px 0 ${space.s3}px` }}>{L("读不出时长——转录开始后按实际时长计费。", "Couldn't read length — billed by actual duration once started.")}</div>
            ) : (
              // 取整规则要在花钱的当下讲，不能只写在定价页。
              // ⚠️ 两句短话、各说一件事（2026-09-01 改）。原来是一句「按秒计费，不足一分钟不按一分钟算；
              // 以服务端实测时长结算。」——「不按一分钟算」是双重否定，读一遍要在脑子里绕一圈；
              // 「服务端实测」是我们内部的说法，用户既不知道那指什么，也不关心是谁测的。
              // 两句都只讲**对他有什么影响**：不会多收；最后按真实时长找齐。
              <div style={{ fontSize: 12, color: semantic.text.muted, margin: `-${space.s1}px 0 ${space.s3}px`, lineHeight: 1.6 }}>
                {L("按秒计费，不向上取整。", "Billed by the second, never rounded up.")}<br />
                {L("转录完成后按实际时长结算，多退少补。", "Settled on the actual length when it's done — any difference comes back.")}
              </div>
            )}

            {/* 软墙预警（提前到卡内；红线：余额不够不能开始） */}
            {blocked && (
              // 行内紧凑警告条（设计稿 04 出错）：26px 暖金图标块 + 一句原因；主出口 = 下方「先充值」整宽键
              <div style={{ display: "flex", alignItems: "center", gap: space.s3, background: semantic.surface.raised, border: `1px solid ${semantic.border.subtle}`, borderLeft: `3px solid ${semantic.warning.icon}`, borderRadius: radius.sm, padding: `${space.s3}px ${space.s3}px`, marginBottom: space.s3 }}>
                <span aria-hidden style={{ flex: "0 0 auto", width: 26, height: 26, borderRadius: 7, background: semantic.warning.bg, color: semantic.warning.text, display: "grid", placeItems: "center", fontSize: 14 }}>⚠</span>
                <span style={{ fontSize: 12, lineHeight: 1.6, color: semantic.text.secondary }}>
                  {L("预估 ", "Est. ")}<span style={monoNum}>{usd(cost!)}</span>{L(" 超出余额 ", " exceeds your balance ")}<span style={monoNum}>{usd(balance)}</span>{L("，还差 ", " — ")}<b style={{ ...monoNum, color: semantic.warning.text }}>{usd(shortfall)}</b>{L("。文件已就绪，充值后一键开始。", " short. Your file is ready — top up and start.")}
                </span>
              </div>
            )}

            {blocked ? (
              <Button primary full size="lg" onClick={() => onTopUp?.(Math.ceil(shortfall))}>{L("先充值", "Top up first")} &nbsp;→</Button>
            ) : (
              <Button primary full size="lg" onClick={() => onStart(file, durSec)}>{L("开始转录", "Start")} &nbsp;→</Button>
            )}
            <div style={{ fontSize: 12, color: semantic.text.muted, textAlign: "center", marginTop: space.s2 }}>
              {blocked
                ? L("充值弹窗会带入缺口金额，完成后回到这里直接开始", "The top-up dialog is pre-filled; you'll come right back here")
                : cost != null
                ? L.x("余额 {0}，转录后约剩 {1}", "Balance {0} → about {1} after",
                    <span key="b" style={monoNum}>{usd(balance)}</span>, <span key="a" style={monoNum}>{usd(balance - cost)}</span>)
                : null}
            </div>
          </div>
        )}

        {/* 卡脚 */}
        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: space.s4, paddingTop: space.s4, borderTop: `1px solid ${semantic.border.subtle}` }}>
          {/* 免费额度状态：有余量报数；IP 闸触发（limited）报「同网络已有试用」——两者互斥 */}
          {/* ⚠️ 免费额度报**分钟**，不报 fmtClock 的 1:27:00（2026-08-31）：
              额度本来就是按分钟发的（企业邮箱 180 分钟 / 个人 60 分钟），
              用户收到的邮件里也是分钟——界面上却要他自己把 1:27:00 换算回 87。
              时钟格式该留给「这段录音多长」那种真正的时间轴（上面的文件时长仍是它）。
              向下取整不四舍五入：这是我们欠用户的额度，宁可少报不可多报。 */}
          {(freeLeftSeconds ?? 0) > 0 && (
            <span style={{ fontSize: 11, color: semantic.success.text }}>
              {/* 剩不到一分钟报「< 1」不报「0」：这一行只在还有额度时才显示，写「0 分钟」
                  等于自己跟自己打架。占位符吃字符串，八门译文一个字都不用改。 */}
              {L.t("免费额度剩余 {0} 分钟", "Free quota left: {0} min",
                   Math.floor(freeLeftSeconds! / 60) >= 1 ? Math.floor(freeLeftSeconds! / 60) : "< 1")}
            </span>
          )}
          {freeLimited && (
            <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted }}>
              {L("同网络已有试用，联系我们开通团队试用", "This network already has a trial — contact us for a team trial")}
            </span>
          )}
          {/* 两行合一行；等宽字也一并去掉——这里没有一列数字要对齐，mono 只贡献「调试输出感」。
              完整扩展名清单挂在 title 上，真要查的人停一下就有。 */}
          <span title={FORMATS} style={{ fontSize: 11, color: semantic.text.muted, lineHeight: 1.7 }}>
            {L("支持格式", "Formats")}：{FORMATS_SHORT} {L("等音视频格式", "and other audio/video")} · {L("单个文件 ≤ 4 小时 · ≤ 2 GB", "up to 4 hours / 2 GB per file")}
          </span>
        </div>
      </div>
    </div>
  );
}

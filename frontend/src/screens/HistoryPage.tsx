// HistoryPage.proposed.tsx — 我的转录 · 设计系统 v1 版
// 对照稿：《屏幕02 · 我的转录 · 对比》
// 变更：状态独立成列（pill 徽章）；数字列 mono + tabular 右对齐；表头 label 体；
//      「查看」「↓」均删除（整行可点 + hover 左缘条 + ›；下载动线统一收到详情页，先复核再导出）；
//      tab 加计数 + 赤陶下划线；处理中行进度合并为一行 caption；失败行给「重试」出口。
// 滚动：页头 + tab 固定，表格内部滚动（.tx-scroll），表头 sticky。
// 窄屏（<768 卡片化）建议在 AppShell 层切换，此文件交付桌面形态。

import { useErrText } from "../lib/userErrors";
import { useRef, useState } from "react";
import { EmptyState, SmallEmpty } from "./EmptyState";
import { WaveTile, WaveProgress } from "../components/Wave";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow, motion, layout } from "../styles/tokens";
import { langName } from "../lib/langs";
import { observedClimb } from "../lib/flow";
import type { HistoryItem } from "../lib/sampleData";

interface HistoryPageProps {
  items?: HistoryItem[];        // 真实任务列表（后端）
  onOpen: (f: HistoryItem) => void;
  onNew: () => void;
  empty?: boolean;
  // 列表拉回来了没有。⚠️ 这一屏上有**两句断言**——整页空态「这里还很安静」与 tab 内的
  // 「『全部』里暂时没有转录」——它们都是在替用户宣布「你没有」。数据没到时 rows 也是空的，
  // 不区分的话刚登录那半秒会先说一句假话再自我推翻（同上传页横跳的成因）。
  // 缺省 true：演示态与测试用例传的是现成数据，不必每处都补这个字段。
  loaded?: boolean;
  liveRow?: HistoryItem | null;
  onOpenLive?: () => void;
  /** 失败行重试。抛错即视为「没重试成」，理由由本组件显示在该行上。 */
  onRetry?: (f: HistoryItem) => void | Promise<unknown>;
  /** 本机版：处理中 / 排队中的行取消。点一次变「确认取消」，再点才真取消；抛错时理由显示在该行上。 */
  onCancel?: (f: HistoryItem) => void | Promise<unknown>;
}

// 列宽：状态/转录于两列留出右侧余量——英文长徽章(Failed · no charge)与长日期(Yesterday 10:08)
// 不再贴住下一列；多出的宽度由 1fr 文件列吸收，中文态只是右侧空一点、不变形。
// 状态列 144 / 计费列 116 —— 两个数都是八门语言逐一量出来的（2026-08-19），不是拍的：
//   状态胶囊最宽的是法语「Traitement en cours」134px（德语「Wird hochgeladen」125、
//   日语「アップロード中」107 次之）；原来 132 差 2px，法语的处理中胶囊一直是两行。
//   计费列最宽的是意大利语「Nessun addebito」104px；原来 100 差 4px。
// 行尾列 44：只放 hover 时的「›」。
// ⚠️ 它 2026-08-19 曾被撑到 108，因为**失败行的「重试」按钮当时也在这一格**，而那个词在
//   多数语言里装不下（德语 121px、葡语 105px、法语 76、西语 75，英语 Retry 也要 45）。
//   代价是：每一行都为它常驻 108px，而 99% 的行这一格是空的——文件名列（1fr）被白白吃掉
//   64px，长文件名当场被截断，右边却空着一大块（2026-08-31 穿测实见）。
//   现在「重试」挪进**文件名格的说明行**，紧挨着失败原因——语义上本来就该在一起
//   （「为什么失败」和「再试一次」），而那一行是流式布局，八门译文再长也只是自然换行，
//   于是那条列宽约束整个消失了。⛔ 别把「重试」挪回行尾列：那会把 108px 的约束一起带回来。
// 其余列宽仍是八门逐一量出来的（2026-08-19），别按中英文改：
//   状态列 144 —— 最宽是法语「Traitement en cours」134px（德语 125、日语 107 次之）；
//   计费列 116 —— 最宽是意大利语「Nessun addebito」104px。各留了 10px 余量。
const COLS = "1fr 144px 138px 84px 92px 44px";   // 本机版：去掉「计费」一列（线上第 6 列 116px）
// 一行装得下所需的最小宽度：固定六列 618 + 文件名列至少 170 + 行内边距 48。
// 窗口比这窄时**横向滚动**（表格的通行做法），而不是让祖先的 overflow:hidden 把列裁掉
// ——被裁的列既看不见也滑不出来，用户看到的是「文件名/语言/时长凭空消失」。
const ROW_MIN_WIDTH = 720;

const mono = (size = 13): React.CSSProperties => ({ fontFamily: fonts.mono, fontSize: size, fontVariantNumeric: "tabular-nums" });

function StatusBadge({ st, expired, canceled }: { st: HistoryItem["st"]; expired?: boolean; canceled?: boolean }) {
  const L = useL();
  if (canceled) {
    // 本机版：用户自己取消的单，库里记成 failed，但它不是「失败」——不用金色喊人动手，给中性灰
    return <span style={{ display: "inline-flex", fontSize: 11, fontWeight: 500, padding: "3px 9px", borderRadius: radius.pill, background: semantic.surface.sunken, color: semantic.text.muted }}>{L("已取消", "Canceled")}</span>;
  }
  if (expired) {
    // 完成但超 30 天，内容已删 → 中性「已过期」徽章（仍是完成态，只是内容没了）
    return <span style={{ display: "inline-flex", fontSize: 11, fontWeight: 500, padding: "3px 9px", borderRadius: radius.pill, background: semantic.surface.sunken, color: semantic.text.muted }}>{L("已过期", "Expired")}</span>;
  }
  if (st === "queued") {
    // 排队中：空心静止点 + 灰底，区别于「处理中」的实心脉冲赤陶点（线上并发=1 时常见）
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 500, padding: "3px 9px", borderRadius: radius.pill, background: semantic.surface.sunken, color: semantic.text.muted }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", border: `1.5px solid ${semantic.text.muted}` }} />
        {L("排队中", "Queued")}
      </span>
    );
  }
  if (st === "uploading" || st === "processing") {
    // 都是在途活动态（实心脉冲赤陶点）；仅文案区分：上传中 → 处理中。引擎真正开工才叫「处理中」
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 500, padding: "3px 9px", borderRadius: radius.pill, background: semantic.accent.bgTint, color: semantic.accent.text }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: semantic.accent.brand, animation: "pulseDot 1.6s infinite" }} />
        {st === "uploading" ? L("上传中", "Uploading") : L("处理中", "Processing")}
      </span>
    );
  }
  if (st === "failed") {
    // 只说状态，不带「· 未计费」——那半句搬到计费列去了（2026-08-19 实测：合在一起时
    // 德语要 176px、意大利语 177px，而状态列只有 132px，三门语言的胶囊会裂成两行）。
    //
    // ⚠️ 2026-08-31 由赤陶换成警示金：原来「失败」是 accentSoft 底 + terra 字，
    // 而「处理中」是 accentTint 底 + terra 字——**同一列里扫下去两者是同一色系**，
    // 得读字才分得出（穿测实见）。换色之后这一列有三种语义、三种颜色：
    //   灰 = 排队 / 已完成 / 已过期（没你的事）
    //   赤陶脉冲点 = 在途（正在跑，等着就行）
    //   金 = 失败（要你动手：这一行右边就是「重试」）
    // 金在本应用里本来就是「停一下看清楚」那一档（选错格式、余额不够都用它），语义对得上，
    // 也不必为这一个胶囊新增一个红。⚠️ 别改回赤陶：赤陶同时还是主按钮与链接色，
    // 一个「要你动手」的状态跟「我们希望你点的东西」同色，等于没有状态色。
    return <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, fontWeight: 500, padding: "3px 9px", borderRadius: radius.pill, background: semantic.warning.bg, color: semantic.warning.text }}>
      <span aria-hidden style={{ fontSize: 10, lineHeight: 1 }}>✕</span>{L("失败", "Failed")}
    </span>;
  }
  return <span style={{ display: "inline-flex", fontSize: 11, fontWeight: 500, padding: "3px 9px", borderRadius: radius.pill, background: semantic.surface.sunken, color: semantic.text.secondary }}>{L("已完成", "Done")}</span>;
}

// **失败话术（error_public）的英文映射。** 这三条与别处不同：它们是**存进数据库那一行的**
// （`jobs.error_public` / `postprocess_jobs.error_public`），历史行里躺的就是这几句中文，
// 所以改不成错误码——改了老任务就一句话都显示不出来。查表翻译、未知话术原样显示。
//
// ⚠️ 重试被拒的那三种理由 2026-08-30 **已经不在这张表里了**：它们是 HTTP 响应、不是库里的行，
// 所以走了错误码（`lib/userErrors.ts`）。别再往这张表里加新的——这里只收「已经写进库的历史话术」。
const ERROR_EN: Record<string, string> = {
  "转录失败，请重试": "Transcription failed — please try again.",   // 本机版：不收费，去掉线上的「本次不计费」
  "登录已过期，请重新登录": "Session expired — please sign in again.",   // 前端 401 停轮询话术（lib/flow）
  "后处理失败，请重试": "Post-processing failed — please try again.",
  "已取消": "Canceled",   // 本机版：取消任务（app/local.py 的 CANCELED_PUBLIC）
};

export function HistoryPage({ items: rows = [], onOpen, onNew, empty, loaded = true, liveRow, onOpenLive, onRetry, onCancel }: HistoryPageProps) {
  const L = useL();
  const [filter, setFilter] = useState("all");
  // 项目筛选："all"=所有 / "none"=未分组 / 项目 id。项目被删后筛选值失效 → 视同 "all"
  const [hovered, setHovered] = useState<number | null>(null);
  // 行号 → 「…」表示进行中，其余表示这次重试被拒的理由（按行号存：HistoryItem 类型里没有 id）
  const errText = useErrText();
  const [retry, setRetry] = useState<Record<number, string>>({});
  // 行号 → "confirm" 等二次确认 / "…" 取消中 / 其余是这次取消被拒的理由
  const [cancel, setCancel] = useState<Record<number, string>>({});
  const items = liveRow ? [liveRow, ...rows] : rows;
  // 后处理加工中行的步内 % 模拟（后端只给步索引）：按「本浏览器首次观察到该步」起匀速爬，
  // 键带步索引 → 步间硬切换重新起爬（与接力卡同思路；observedClimb 无时长时 600s 兜底爬满）
  const ppSeenRef = useRef(new Map<string, { since: number }>());
  const ppStepName = (s: string | null) =>
    // categorize 是归类下架（2026-08-17）前的存量任务，只读兼容；摘掉会让老单显示裸英文
    s === "narrate" ? L("视角转换", "Narrative") : s === "categorize" ? L("归类", "Categorize") : s === "redact" ? L("脱敏", "Redact") : "";
  const ppProductName = (s: string) =>
    s === "narrate" ? L("叙述稿", "Narrative draft") : s === "categorize" ? L("归类纪要", "Categorized minutes") : s === "redact" ? L("脱敏稿", "Redacted transcript") : s;
  const ppActive = (i: HistoryItem) => i.pp?.status === "running" || i.pp?.status === "queued";

  if (empty) {
    return (
      <EmptyState
        icon={<WaveTile />}
        title={L("这里还很安静。", "It's quiet here.")}
        body={L("上传第一段录音 —— 转出的文字稿会留在这里，随时回来预览、编辑、下载。", "Upload your first recording — the transcript will live here, ready to preview, edit and download.")}
        actionLabel={L("上传第一个文件 →", "Upload your first file →")}
        onAction={onNew}
      />
    );
  }

  // 「处理中」tab 收纳排队中（都属在途未完成、用户视角同为「还没好」）+ 后处理加工中的行；其余 tab 精确匹配
  const matchTab = (i: HistoryItem, id: string) =>
    id === "all" ? true : id === "processing" ? (i.st === "processing" || i.st === "queued" || i.st === "uploading" || ppActive(i)) : i.st === id;
  const count = (id: string) => items.filter((i) => matchTab(i, id)).length;
  const tabs = [
    { id: "all", label: L("全部", "All") },
    { id: "processing", label: L("处理中", "Processing") },
    { id: "done", label: L("已完成", "Done") },
    { id: "failed", label: L("失败", "Failed") },
  ];
  const filtered = items.filter((i) => matchTab(i, filter));

  // 底部内边距单独给 24px（不跟顶部的 44px 走）：让列表下沿与侧边栏用户区的邮箱行齐平。
  // 侧栏底部内边距 16px + 邮箱文字到区块底的行高余量 8px = 24px，两侧都从页底起算，
  // 所以视口高度变化时保持齐平。详情页的页脚基线（Result.tsx）落在同一条水平线上。
  return (
    <div style={{ flex: 1, padding: layout.pagePad, overflow: "hidden", display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* 页头（固定）；「新建转录」入口在侧边栏，此处不重复 */}
      <div style={{ marginBottom: space.s5 }}>
        <h1 style={{ ...type.h1, margin: 0 }}>{L("我的转录", "My transcripts")}</h1>
      </div>

      {/* Tab（固定）：选中 = 600 + 2px 赤陶下划线；计数 mono-sm；右侧 = 项目筛选（有项目才显示） */}
      <div style={{ display: "flex", gap: space.s6, alignItems: "flex-end", borderBottom: `1px solid ${semantic.border.subtle}`, marginBottom: space.s4 }}>
        {tabs.map((tab) => {
          const on = filter === tab.id;
          return (
            <button
              key={tab.id}
              className="tx-focus"
              onClick={() => setFilter(tab.id)}
              style={{ border: "none", background: "transparent", cursor: "pointer", fontFamily: fonts.sans, fontSize: 14, fontWeight: on ? 600 : 400, color: on ? semantic.text.primary : semantic.text.muted, padding: "0 0 9px", borderBottom: on ? `2px solid ${semantic.accent.brand}` : "2px solid transparent", marginBottom: -1, transition: `color ${motion.fast}` }}
            >
              {/* 计数一律 muted：ghost 在页底只有 2.01:1，红线 11 明令计数不得用 ghost */}
              {tab.label} <span style={{ ...mono(11), color: semantic.text.muted, fontWeight: on ? 600 : 400 }}>{count(tab.id)}</span>
            </button>
          );
        })}
      </div>

      {/* 表格：内部滚动 + sticky 表头 */}
      <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "auto", border: `1px solid ${semantic.border.default}`, background: semantic.surface.raised, borderRadius: radius.md, boxShadow: shadow.sm }}>
        <div style={{ display: "grid", gridTemplateColumns: COLS, minWidth: ROW_MIN_WIDTH, padding: `${space.s3}px ${space.s6}px`, borderBottom: `1px solid ${semantic.border.default}`, background: semantic.surface.page, position: "sticky", top: 0, zIndex: 2, ...type.label }}>
          <div>{L("文件", "File")}</div>
          <div>{L("状态", "Status")}</div>
          <div>{L("转录于", "When")}</div>
          <div>{L("语言", "Language")}</div>
          <div style={{ textAlign: "right" }}>{L("时长", "Length")}</div>
          <div />
        </div>

        {filtered.map((item, i) => {
          const proc = item.st === "processing";
          const uploading = item.st === "uploading";
          const queued = item.st === "queued";
          const active = proc || uploading;   // 有进度条（上传段显示上传%、转录段显示 Phase0-4 的 0-100%，两段独立显示）
          const pct = Math.round(item.prog ?? 0);   // 文字只报整数；进度条自己吃未取整的浮点
          const pending = active || queued;   // 在途未出结果：时长/计费列留白
          const done = item.st === "done";
          const expired = !!item.expired;     // 完成但超 30 天，内容已删
          // 仅「已完成且未过期」可点开（含完成的实时行，其 st 已变 done）；处理中/排队中/过期都不可点
          const clickable = done && !expired;
          const hov = hovered === i && clickable;
          // 「重试」渲染在文件名格的说明行里（见 COLS 上方注释）。抽成变量而不是内联，
          // 是为了让那一行的 JSX 仍读得出「原因 + 出口」这个结构。
          const retryButton = item.st === "failed" && onRetry ? (
            <button
              className="tx-focus"
              disabled={retry[i] === "…"}
              onClick={(e) => {
                e.stopPropagation();
                setRetry((s) => ({ ...s, [i]: "…" }));
                // 拒绝的理由要落在**这一行**上：三种拒绝（余额不足 / 录音过期 / 不是失败态）
                // 各要用户做的事不同，吞掉就变成「点了没反应」。
                Promise.resolve(onRetry(item))
                  .then(() => setRetry((s) => { const n = { ...s }; delete n[i]; return n; }))
                  // 后端给的是错误码 → 这里就地翻成界面语言（`useErrText`）。
                  // 原先存的是「后端那句中文」，再靠 ERROR_EN 查表——键是散文，后端改一个字就失配。
                  .catch((err) => setRetry((s) => ({ ...s, [i]: errText(err) })));
              }}
              style={{ border: `1px solid ${semantic.border.strong}`, borderRadius: radius.sm, background: "transparent", fontFamily: fonts.sans, fontSize: 12, fontWeight: 500, padding: "3px 10px", color: retry[i] === "…" ? semantic.text.muted : semantic.accent.text, cursor: retry[i] === "…" ? "default" : "pointer", whiteSpace: "nowrap", flex: "0 0 auto" }}
            >{retry[i] === "…" ? L("重试中…", "Retrying…") : L("重试", "Retry")}</button>
          ) : null;
          // 本机版：处理中 / 排队中可以取消（上传中不行——那一段在浏览器里，还没到后台）
          const cancelButton = (proc || queued) && onCancel ? (
            <>
              {cancel[i] && !["confirm", "…"].includes(cancel[i]) && (
                <span style={{ fontSize: 12, lineHeight: 1.5, color: semantic.warning.text, minWidth: 0 }}>{cancel[i]}</span>
              )}
              <button
                className="tx-focus"
                disabled={cancel[i] === "…"}
                onClick={(e) => {
                  e.stopPropagation();
                  if (cancel[i] !== "confirm") { setCancel((s) => ({ ...s, [i]: "confirm" })); return; }
                  setCancel((s) => ({ ...s, [i]: "…" }));
                  Promise.resolve(onCancel(item))
                    .then(() => setCancel((s) => { const n = { ...s }; delete n[i]; return n; }))
                    .catch((err) => setCancel((s) => ({ ...s, [i]: errText(err) })));
                }}
                style={{ border: `1px solid ${cancel[i] === "confirm" ? semantic.warning.icon : semantic.border.strong}`, borderRadius: radius.sm, background: "transparent", fontFamily: fonts.sans, fontSize: 12, fontWeight: 500, padding: "3px 10px", color: cancel[i] === "…" ? semantic.text.muted : cancel[i] === "confirm" ? semantic.warning.text : semantic.text.secondary, cursor: cancel[i] === "…" ? "default" : "pointer", whiteSpace: "nowrap", flex: "0 0 auto" }}
              >{cancel[i] === "…" ? L("取消中…", "Canceling…") : cancel[i] === "confirm" ? L("确认取消", "Confirm cancel") : L("取消", "Cancel")}</button>
            </>
          ) : null;
          return (
            <div
              key={`${item.n}-${i}`}
              className={clickable ? "tx-focus" : undefined}
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              onClick={() => { if (!clickable) return; if (item === liveRow) onOpenLive?.(); else onOpen(item); }}
              onKeyDown={(e) => { if (clickable && e.key === "Enter") { if (item === liveRow) onOpenLive?.(); else onOpen(item); } }}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              style={{
                display: "grid", gridTemplateColumns: COLS, minWidth: ROW_MIN_WIDTH, padding: `${space.s4}px ${space.s6}px`, alignItems: "center",
                borderBottom: i < filtered.length - 1 ? `1px solid ${semantic.border.subtle}` : "none",
                cursor: clickable ? "pointer" : "default",
                background: hov ? semantic.surface.rowActive : "transparent",
                boxShadow: hov ? `inset 3px 0 0 ${semantic.accent.brand}` : "none",
                transition: `background ${motion.fast}`,
              }}
            >
              {/* 文件名（+ 处理中行的进度 caption） */}
              <div style={{ minWidth: 0 }}>
                <div title={item.n} style={{ fontFamily: fonts.sans, fontSize: 14, fontWeight: 600, color: item.st === "failed" || expired ? semantic.text.secondary : semantic.text.primary, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{item.n}</div>
                {/* 轮询连续失败达阈值：覆盖进度 caption，别破坏 uploading/processing/queued 各自的正常文案分支 */}
                {item.disconnected && (active || queued) && (
                  <div style={{ marginTop: 7, ...mono(11), color: semantic.warning.text }}>{L("连接中断，正在重连…", "Connection lost, reconnecting…")}</div>
                )}
                {!item.disconnected && active && (
                  /* flexWrap + minWidth:0（2026-08-31）：文件名列是 1fr，窗口一窄它就只剩 200 出头，
                     而这一行是 150px 波形 + 一句「8% · 约 N 分钟 · 可离开」——原来不换行，
                     于是整句压到状态列上面去了。宁可让文字掉到波形下面一行。 */
                  <div style={{ display: "flex", alignItems: "flex-end", gap: space.s3, marginTop: 7, flexWrap: "wrap", minWidth: 0 }}>
                    {/* 处理中=声波即进度（与处理大屏、登录基准同源）；上传中波形静止只看进度推进。
                        进度条吃连续浮点（爬升要平滑），但文字只报整数——否则会漏出「99.47381%」。 */}
                    <WaveProgress progress={(item.prog ?? 0) / 100} width={150} active={proc} />
                    <span style={{ ...mono(11), color: semantic.text.muted }}>
                      {uploading
                        ? `${L("上传", "Uploading")} ${pct}%`
                        : item.etaMin != null
                          ? `${pct}% · ${L.t("约 {0} 分钟", "~{0} min", item.etaMin)} · ${L("可离开", "can leave")}`
                          : `${pct}% · ${L("可离开", "can leave")}`}
                    </span>
                    {proc && cancelButton}
                  </div>
                )}
                {!item.disconnected && queued && (
                  <div style={{ display: "flex", alignItems: "center", gap: space.s3, marginTop: 7, flexWrap: "wrap", minWidth: 0 }}>
                    <span style={{ ...mono(11), color: semantic.text.muted }}>{L("排队中 · 等待开始", "Queued · waiting to start")}</span>
                    {cancelButton}
                  </div>
                )}
                {expired && (
                  <div style={{ marginTop: 7, ...mono(11), color: semantic.text.muted }}>{L("内容已过期 · 已按隐私策略删除", "Content expired · removed per privacy policy")}</div>
                )}
                {/* 失败原因：后端脱敏话术（error_public），没有就不显示，别崩、别留空行；
                    已知话术按界面语言映射（ERROR_EN），未知的原样显示 */}
                {/* 重试被拒的理由压过原失败原因：用户刚点了按钮，他现在要看的是「为什么没重试成」 */}
                {item.st === "failed" && (
                  <div style={{ display: "flex", alignItems: "center", gap: space.s3, marginTop: 7, flexWrap: "wrap", minWidth: 0 }}>
                    {retry[i] && retry[i] !== "…"
                      ? <span style={{ fontSize: 12, lineHeight: 1.5, color: semantic.warning.text, minWidth: 0 }}>{retry[i]}</span>
                      : item.error && item.error !== "已取消"   // 已取消由状态列说，这里不重复
                        ? <span style={{ fontSize: 12, lineHeight: 1.5, color: semantic.text.muted, minWidth: 0 }}>{L(item.error, ERROR_EN[item.error] ?? item.error)}</span>
                        : null}
                    {retryButton}
                  </div>
                )}
                {/* 后处理状态：占用同一 caption 位（11px / mt7），无子行无折叠；下载/重试统一收到详情页 */}
                {done && !expired && item.pp && (() => {
                  const pp = item.pp;
                  if (pp.status === "running" || pp.status === "queued") {
                    const pct = pp.status === "running"
                      ? Math.round(observedClimb(ppSeenRef.current, `${item.id ?? item.n}#pp${pp.stepIndex}`, null, Date.now()))
                      : 0;
                    return (
                      <div style={{ display: "flex", alignItems: "center", gap: space.s2, marginTop: 7 }}>
                        <span style={{ fontSize: 11, fontWeight: 500, padding: "1px 8px", borderRadius: radius.pill, background: semantic.accent.bgTint, color: semantic.accent.text, flex: "0 0 auto" }}>✦ {L("后处理", "Post-processing")}</span>
                        <span style={{ ...mono(11), color: semantic.accent.text }}>
                          {ppStepName(pp.currentStep)} {pct}% · {L.t("第 {0}/{1} 步", "step {0}/{1}", pp.stepIndex, pp.totalSteps)} · {L("可离开", "can leave")}
                        </span>
                      </div>
                    );
                  }
                  if (pp.status === "done") {
                    return (
                      <div style={{ display: "flex", alignItems: "center", gap: space.s2, marginTop: 7 }}>
                        {/* 底色用 success.bg 而非 sunken：绿字压 sunken 是 4.44:1（差 0.06 不达 AA），
                            压 success.bg 是 4.82:1，语义上也更该是绿底 */}
                        <span style={{ fontSize: 11, fontWeight: 500, padding: "1px 8px", borderRadius: radius.pill, background: semantic.success.bg, color: semantic.success.text, flex: "0 0 auto" }}>✦ {L("已加工", "Processed")}</span>
                        <span style={{ fontSize: 11, color: semantic.text.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {pp.products.map(ppProductName).join(" · ")}
                          {pp.qcFixCount > 0 && <> · <span style={{ color: semantic.success.text }}>{L.t("质检修复 {0} 处", "QC fixed {0}", pp.qcFixCount)}</span></>}
                        </span>
                      </div>
                    );
                  }
                  return (
                    <div style={{ display: "flex", alignItems: "center", gap: space.s2, marginTop: 7 }}>
                      <span style={{ fontSize: 11, fontWeight: 500, padding: "1px 8px", borderRadius: radius.pill, background: semantic.warning.bg, color: semantic.warning.text, flex: "0 0 auto" }}>✦ {L("后处理失败", "Post-processing failed")}</span>
                      <span style={{ fontSize: 11, color: semantic.text.muted }}>{L("进详情页重试", "retry from the transcript page")}</span>
                    </div>
                  );
                })()}
              </div>

              <div><StatusBadge st={item.st} expired={item.expired} canceled={item.st === "failed" && item.error === "已取消"} /></div>
              <div style={{ ...mono(), color: semantic.text.muted }}>{L(item.d.zh, item.d.en)}</div>
              <div style={{ fontSize: 13, color: semantic.text.secondary }}>{langName(item.lang, L)}</div>
              <div style={{ textAlign: "right", ...mono(), color: pending ? semantic.text.muted : semantic.text.secondary }}>{pending ? "—" : item.dur}</div>
              {/* 行尾：只有 hover 时的「›」（整行可点提示）。重试在文件名格里，下载统一走详情页 */}
              <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center" }}>
                {done && hov && <span aria-hidden style={{ color: semantic.text.muted, fontSize: 14 }}>›</span>}
              </div>
            </div>
          );
        })}

        {loaded && filtered.length === 0 && (
          <SmallEmpty
            icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M7 12h10M10 18h4" /></svg>}
            text={L.t("「{0}」里暂时没有转录。", "Nothing under \"{0}\" yet.", tabs.find((t) => t.id === filter)?.label ?? "")}
            linkLabel={L("看全部 →", "View all →")}
            onLink={() => setFilter("all")}
          />
        )}
      </div>
    </div>
  );
}

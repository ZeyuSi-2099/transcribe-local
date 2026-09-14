// RedactChanges.tsx — 脱敏「改了这些」清单（详情页完成态，折叠）
//
// 为什么要有它：脱敏是唯一一个「一次跑完、不给看过程」的步骤，跟「复核优先」是脱节的。
// 说明块（RedactScope）讲的是**规则会改什么**，这里讲的是**这一稿实际改了什么**——
// 抽象规则读完仍不放心，具体改动一看就懂，也是发现「改过头了」的唯一途径。
//
// 2026-08-22 第二版：从「按词聚合、整条一起审」改成**逐处审核**（Duner）。
//   · 每一处自带上下文：上一句 / 这一句 / 下一句，改动处就地划出「原词 → 脱敏后」
//   · 每一处**各自**保留 / 改写。三处「山西 → 本省」是三处，不是一条
//   · 上下文随清单一起给 ⇒ 与「脱敏跑在第几步」无关。此前只能靠跳回左侧正文，
//     而跑在视角转换之后时原话已被重写成叙述（实测 1215 段对话 → 29 段叙述），
//     那一句物理上不存在，界面上就只剩一列点不动的「这一处」，看起来像坏了
//
// 改口**不重跑模型**：重跑要再花一次钱、结果还不保证是用户要的。它只落一份覆盖表，
// 取稿与导出时后端才应用（app/redact_diff.apply_overrides），R2 上那份原始脱敏稿留档不动。
// 于是改口是确定的、即时的、可撤销的——与「已按证据定字 → 修订」同一个心智。
//
// 数据来自后端对比脱敏前后两稿（app/redact_diff.py），不是解析质检报告。
// 拿不到就整块不显示：这是锦上添花，不能让它把详情页拖垮。

import { useEffect, useRef, useState } from "react";
import { semantic, fonts, radius, motion, space } from "../../styles/tokens";
import { useL, type LFn } from "../../lib/i18n";
import { putPpOverrides, type PpChanges, type RedactChange, type RedactOverride, type RedactSpot } from "../../lib/api";

/** 分组标题。顺序即展示顺序——身份信息在前，可回溯线索在后，认不出的垫底。 */
const KIND_ORDER: RedactChange["kind"][] = ["company", "person", "contact", "geo", "number", "project", "other"];

function kindLabel(L: LFn, k: RedactChange["kind"]): string {
  switch (k) {
    case "company": return L("公司名", "Company names");
    case "person": return L("人名", "People's names");
    case "contact": return L("联系方式", "Contact details");
    case "geo": return L("地点", "Places");
    case "number": return L("数字与规模", "Figures and sizes");
    case "project": return L("项目名", "Project names");
    default: return L("其他", "Other");
  }
}

const keyOf = (c: RedactChange) => `${c.from}→${c.to}`;
const spotKeyOf = (sp: RedactSpot) => `${sp.line}#${sp.idx}`;

/** 老改口（加 idx 之前存的）摊成逐处形态，之后全流程只有一种形态。
 *  摊不开的（清单里已找不到那条改动）原样留着，别让用户存过的东西无声消失。 */
function normalize(chs: RedactChange[], ovr: RedactOverride[]): RedactOverride[] {
  return ovr.flatMap((o) => {
    if (o.idx != null) return [o];
    const spread = chs
      .filter((c) => c.from === o.from && c.to === o.to)
      .flatMap((c) => c.spots.filter((s) => s.line === o.line).map((s) => ({ ...o, idx: s.idx })));
    return spread.length ? spread : [o];
  });
}

/** 回报给下载行的状态：改了几处、存没存上。见 PostprocessCard 的 ProductRow。 */
export interface RedactOverrideState {
  count: number;
  saving: boolean;
  failed: boolean;
  retry: () => void;
}

interface Props {
  jobId: string | null;
  /** 改动清单。**由 PostprocessCard 跟加工状态一起取回来**——自己在这儿取的话，
   *  完成态会分两步画：先出「加工完成 + 两个产物行」，几百毫秒后才冒出「脱敏了 N 处」
   *  与「已按你的 N 处修订」（2026-08-22 Duner 实见）。取不到一律是空清单，不是 null。 */
  data: PpChanges;
  /** 点某一处 → 回到正文第 seg 段。只有「脱敏是第一步」时后端才给得出 seg。
   *  拿不到也无妨：上下文就在这一处旁边，本来就不指望跳过去看。 */
  onLocate?: (seg: number) => void;
  /** 把改口状态回报上去，让**下载行**说清「下的这份带不带你的修订」。
   *  没有它的话，改完之后下载行长得跟没改一样；存失败时更糟——屏幕上写着「已保留原词」，
   *  服务器上什么都没有，而你下到的是一份自以为改过的稿（2026-08-22 设计评审）。 */
  onState?: (s: RedactOverrideState) => void;
}

export function RedactChanges({ jobId, data, onLocate, onState }: Props) {
  const L = useL();
  const changes = data.changes;
  const [overrides, setOverrides] = useState<RedactOverride[]>(() => normalize(data.changes, data.overrides));
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);   // 正在「改成…」的那一处
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const saveRef = useRef<(n: RedactOverride[]) => void>(() => {});
  const latestRef = useRef<RedactOverride[]>([]);
  const retry = useRef(() => saveRef.current(latestRef.current)).current;

  // 回报给下载行。依赖全是基本类型 + 恒等的 retry ⇒ 不会来回触发。
  // ⚠️ 必须在下面那句早退**之前**：hooks 不许有条件地调。
  useEffect(() => {
    onState?.({ count: overrides.length, saving, failed, retry });
  }, [overrides.length, saving, failed, onState, retry]);

  if (changes.length === 0) return null;
  const total = changes.reduce((n, c) => n + c.count, 0);
  const groups = KIND_ORDER
    .map((k) => [k, changes.filter((c) => c.kind === k)] as const)
    .filter(([, cs]) => cs.length > 0);

  const isSpot = (o: RedactOverride, c: RedactChange, sp: RedactSpot) =>
    o.line === sp.line && o.idx === sp.idx && o.from === c.from && o.to === c.to;
  /** 这一处当前会导出成什么（未改口则 undefined）。 */
  const overrideOf = (c: RedactChange, sp: RedactSpot): string | undefined =>
    overrides.find((o) => isSpot(o, c, sp))?.value;

  /** 整表覆盖式保存。失败不回滚界面状态——回滚会让用户以为自己没点到，
   *  而真正要紧的是**别让他下到一份自以为改过的稿**：那件事由下载行接管（onState）。 */
  const save = async (next: RedactOverride[]) => {
    setOverrides(next);
    setEditing(null);
    if (!jobId) return;
    setSaving(true);
    setFailed(false);
    try {
      await putPpOverrides(jobId, next);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };
  // 「重试」按钮挂在下载行上，那里拿不到这里的 state——用 ref 兜住最新的一份，
  // 换来一个**恒等**的回调，回报状态的 effect 才不会每渲染一次就重跑。
  saveRef.current = (n) => { void save(n); };
  latestRef.current = overrides;
  const setOverride = (c: RedactChange, sp: RedactSpot, value: string) =>
    save([...overrides.filter((o) => !isSpot(o, c, sp)),
          { line: sp.line, idx: sp.idx, from: c.from, to: c.to, value }]);
  const clearOverride = (c: RedactChange, sp: RedactSpot) =>
    save(overrides.filter((o) => !isSpot(o, c, sp)));

  const ctxLine = (t: string) => (
    <div style={{ fontSize: 11, lineHeight: 1.7, color: semantic.text.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t}</div>
  );

  /** 一处：上一句 / 这一句（就地划出改动）/ 下一句 + 它自己的两个动作。 */
  const spotRow = (c: RedactChange, sp: RedactSpot, n: number) => {
    const sk = `${keyOf(c)}@${spotKeyOf(sp)}`;
    const ov = overrideOf(c, sp);
    // 划掉的＝被舍弃的写法，赤陶的＝导出时真正会出现的字。没改口时舍弃的是原词，
    // 改口后舍弃的是脱敏给的那个词——一眼看得出这一处是保留了还是改了。
    const dropped = ov == null ? c.from : c.to;
    const kept = ov ?? c.to;
    const editingThis = editing === sk;
    const d = draft.trim();
    const ready = !!d && d !== c.to && d !== ov;
    return (
      <div key={sk} style={{ padding: "6px 0", borderTop: `1px solid ${semantic.border.default}` }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, flex: "0 0 auto", fontVariantNumeric: "tabular-nums" }}>{n}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            {sp.prev && ctxLine(sp.prev)}
            <div style={{ fontSize: 12, lineHeight: 1.75, color: semantic.text.primary, overflowWrap: "anywhere" }}>
              {sp.after.text.slice(0, sp.after.s)}
              <span style={{ textDecoration: "line-through", color: semantic.text.muted }}>{dropped}</span>
              <span style={{ color: semantic.accent.text, fontWeight: 600 }}> {kept}</span>
              {sp.after.text.slice(sp.after.e)}
            </div>
            {sp.next && ctxLine(sp.next)}
          </div>
          {sp.seg != null && onLocate && (
            <button className="tx-focus" onClick={() => onLocate(sp.seg!)}
              title={L("回到正文这一句", "Jump to this line in the transcript")}
              style={{ border: "none", background: "transparent", padding: 0, cursor: "pointer", fontSize: 12, color: semantic.accent.text, flex: "0 0 auto" }}>↗</button>
          )}
        </div>
        {/* 逐处动作。**每一处各自决定**——三处「山西 → 本省」是三处，不是一条 */}
        <div style={{ display: "flex", alignItems: "center", gap: space.s2, marginTop: 3, marginLeft: 17, flexWrap: "wrap" }}>
          {ov != null ? (
            <>
              <span style={{ fontSize: 11, color: semantic.text.secondary }}>
                {ov === c.from ? L("已保留原词", "Original kept") : L.t("已改成「{0}」", "Changed to “{0}”", ov)}
              </span>
              {link(L("撤销", "Undo"), () => clearOverride(c, sp))}
            </>
          ) : editingThis ? (
            <span className="tx-field" style={{ display: "inline-flex", alignItems: "center", height: 26, border: `1px solid ${ready ? semantic.accent.brand : semantic.border.strong}`, borderRadius: radius.sm, background: semantic.surface.raised, padding: "0 3px 0 9px", gap: 6, flex: 1, minWidth: 132, transition: `border-color ${motion.fast}`, boxSizing: "border-box" }}>
              <input
                autoFocus
                value={draft}
                placeholder={L("改成…", "Change to…")}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && ready) setOverride(c, sp, d); if (e.key === "Escape") setEditing(null); }}
                style={{ flex: 1, minWidth: 30, border: "none", background: "transparent", fontSize: 12, fontWeight: 600, color: semantic.text.primary, outline: "none", padding: 0, fontFamily: "inherit" }}
              />
              {ready && (
                <button className="tx-focus" onClick={() => setOverride(c, sp, d)}
                  style={{ border: "none", background: semantic.accent.fill, color: semantic.text.onAccent, height: 20, padding: "0 8px", borderRadius: 5, fontSize: 11, fontWeight: 500, cursor: "pointer", flex: "0 0 auto", fontFamily: fonts.sans }}>
                  {L("改 ↵", "Set ↵")}
                </button>
              )}
            </span>
          ) : (
            <>
              {link(L("保留原词", "Keep original"), () => setOverride(c, sp, c.from))}
              {link(L("改成…", "Change to…"), () => { setEditing(sk); setDraft(""); })}
            </>
          )}
        </div>
      </div>
    );
  };

  function link(label: string, onClick: () => void) {
    return (
      <button className="tx-focus" onClick={onClick}
        style={{ border: "none", background: "transparent", padding: 0, cursor: "pointer", fontFamily: fonts.sans, fontSize: 11, color: semantic.accent.text, textDecoration: "underline", flex: "0 0 auto" }}>
        {label}
      </button>
    );
  }

  return (
    <div style={{ marginTop: 10 }}>
      <button
        className="tx-focus"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", border: "none", background: "transparent", padding: "2px 0", cursor: "pointer", textAlign: "left", fontFamily: fonts.sans, fontSize: 12, color: open ? semantic.accent.text : semantic.text.muted }}
      >
        {/* 展开箭头规格同红线 8：15px · 收起灰/展开赤陶 · 旋转 90° */}
        <span aria-hidden style={{ fontSize: 15, lineHeight: 1, color: open ? semantic.accent.text : semantic.text.muted, transform: open ? "rotate(90deg)" : "none", transition: `transform ${motion.fast}, color ${motion.fast}`, display: "inline-block", flex: "0 0 auto" }}>▸</span>
        <span>{L.t("这一稿脱敏了 {0} 处", total === 1 ? "{0} spot redacted in this draft" : "{0} spots redacted in this draft", total)}</span>
      </button>
      {open && (
        <div style={{ marginTop: space.s2, display: "flex", flexDirection: "column", gap: space.s3 }}>
          {groups.map(([k, cs]) => (
            <div key={k}>
              <div style={{ fontSize: 11, fontWeight: 500, letterSpacing: "var(--ls-label)", textTransform: "uppercase", color: semantic.text.muted, marginBottom: 4 }}>
                {kindLabel(L, k)}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {cs.map((c) => {
                  const ck = keyOf(c);
                  const ex = expanded === ck;
                  const decided = c.spots.filter((sp) => overrideOf(c, sp) != null).length;
                  return (
                    <div key={ck} style={{ background: semantic.surface.rowActive, borderRadius: radius.sm, overflow: "hidden" }}>
                      <button
                        className="tx-focus"
                        aria-expanded={ex}
                        onClick={() => { setExpanded(ex ? null : ck); setEditing(null); }}
                        style={{ display: "flex", alignItems: "baseline", gap: space.s2, width: "100%", border: "none", background: "transparent", padding: "5px 8px", cursor: "pointer", textAlign: "left" }}
                      >
                        <span style={{ fontFamily: fonts.mono, fontSize: 11, lineHeight: 1.6, color: semantic.text.secondary, flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>
                          {c.from} <span style={{ color: semantic.text.muted }}>→</span> {c.to}
                        </span>
                        {decided > 0 && (
                          <span style={{ fontSize: 11, color: semantic.accent.text, flex: "0 0 auto", whiteSpace: "nowrap" }}>
                            {L.t("已改口 {0}", "{0} overridden", decided)}
                          </span>
                        )}
                        {c.count > 1 && (
                          <span style={{ fontFamily: fonts.mono, fontSize: 11, color: semantic.text.muted, flex: "0 0 auto", fontVariantNumeric: "tabular-nums" }}>×{c.count}</span>
                        )}
                      </button>
                      {ex && (
                        <div style={{ padding: `0 8px 6px` }}>
                          {c.spots.map((sp, i) => spotRow(c, sp, i + 1))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {failed && (
            <div style={{ fontSize: 11, lineHeight: 1.6, color: semantic.danger.text }}>
              {L("改口没存上。下载已暂停，免得你拿到一份没改的稿——再改一次或在上面那行点「重试」。",
                 "Couldn't save your change. Downloads are paused so you don't get an unrevised draft — change it again, or hit Retry on the row above.")}
            </div>
          )}
          <div style={{ fontSize: 11, lineHeight: 1.6, color: semantic.text.muted }}>
            {saving
              ? L("正在保存…", "Saving…")
              : L("改口只影响导出，不重跑、不额外计费。要长期保住某个词，把它加进保留清单。",
                  "Changes here only affect the export — nothing re-runs and nothing is charged. To keep a word for good, add it to your keep list.")}
          </div>
        </div>
      )}
    </div>
  );
}

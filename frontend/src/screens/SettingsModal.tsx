// SettingsModal.proposed.tsx — 账户设置 · 设计系统 v1 版
// 对照稿：《屏幕04 · 浮窗三件套 · 精修》②
// 变更：分组卡 radius-md（原直角）；行内距 16×20、标签列 130；余额 mono；
//      「充值」ghost→secondary；危险确认按钮按 danger 规范（圆角 + 3 秒防误点）；
//      确认中的行加米深底；✕ 32×32 有底命中区。
import { useErrText } from "../lib/userErrors";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { useL } from "../lib/i18n";
import { semantic, fonts, type, space, radius, shadow } from "../styles/tokens";
import { RATE_PER_HOUR, usd } from "../lib/pricing";
import { getDeletePreflight, deleteAccount, purgeTranscripts, type DeletePreflight, type ReferralInfo } from "../lib/api";

const monoNum: React.CSSProperties = { fontFamily: fonts.mono, fontVariantNumeric: "tabular-nums" };

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

/** 注销区。**不复用 SettingRow**：那是三栏定高的行，而这里要放体检结果 + 输入框 + 报错，
 *  塞进去只会挤成一团。危险动作值得占自己的地方。 */
function CloseAccountSection({ accountEmail, onDeleted }: { accountEmail: string; onDeleted?: () => void }) {
  const L = useL();
  const errText = useErrText();
  const [open, setOpen] = useState(false);
  const [pre, setPre] = useState<DeletePreflight | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  // 体检失败只记一个布尔、文案在渲染时才取——**effect 里不许调 L**：
  // useL() 每次渲染返回新函数，把它写进依赖数组的话，每敲一个字符都会重跑一次体检
  // 并把输入框清空（按钮于是永远点不亮）。而不写进依赖又会读到过期语言。
  const [preFailed, setPreFailed] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 展开才去体检：这几个数（余额、在途任务）只在按下那一刻才有意义，
  // 提前拉会让用户看到打开浮窗那一瞬间的旧状态。
  useEffect(() => {
    if (!open) return;
    setPre(null); setErr(null); setPreFailed(false); setTyped("");
    let alive = true;
    getDeletePreflight()
      .then((p) => { if (alive) setPre(p); })
      .catch(() => { if (alive) setPreFailed(true); });
    return () => { alive = false; };
  }, [open]);

  const matches = typed.trim().toLowerCase() === accountEmail.trim().toLowerCase();

  async function submit() {
    setBusy(true); setErr(null);
    try {
      await deleteAccount(typed.trim());
      onDeleted?.();
    } catch (e) {
      setErr(errText(e));
      setBusy(false);
    }
  }

  const body = { fontSize: 13, color: semantic.text.secondary, lineHeight: 1.6 } as const;

  return (
    <div style={{ padding: `${space.s4}px ${space.s5}px`, background: open ? semantic.surface.rowActive : "transparent" }}>
      <div style={{ display: "flex", gap: space.s5, alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <div style={{ ...type.label, color: semantic.accent.text, marginBottom: 4 }}>{L("注销账户", "Close account")}</div>
          <div style={body}>{L("永久删除账户、所有文字稿和音频。删除立即生效，无法恢复。", "Permanently delete your account, all transcripts and audio. Takes effect immediately and cannot be undone.")}</div>
        </div>
        {!open && <Button size="sm" secondary onClick={() => setOpen(true)}>{L("注销账户", "Close account")}</Button>}
      </div>

      {open && (
        <div style={{ marginTop: space.s4 }}>
          {!pre && !preFailed && <div style={body}>{L("正在检查账户状态…", "Checking your account…")}</div>}
          {preFailed && <div style={{ ...body, color: semantic.accent.text }}>{L("查不到账户状态，稍后再试。", "Couldn't check your account. Try again shortly.")}</div>}

          {/* 拦截：理由已经是成句文案（含金额/任务数与该做什么），原样显示 */}
          {pre && !pre.canDelete && (
            <ul style={{ ...body, margin: 0, paddingLeft: 18, color: semantic.accent.text }}>
              {pre.blockers.map((b) => <li key={b} style={{ marginBottom: 6 }}>{b}</li>)}
            </ul>
          )}

          {pre?.canDelete && (
            <>
              <div style={body}>
                {L.x("将删除 {0}，以及全部音频、复核记录与后处理产物。账单记录按法规要求保留，但不再关联到你。",
                  "This deletes {0}, along with all audio, review data and post-processing output. Billing records are retained as required by law, but no longer linked to you.",
                  <b key="n" style={{ color: semantic.text.primary }}>
                    {L.t("{0} 条转录、{1} 本术语库", "{0} transcripts and {1} glossaries", pre.jobs, pre.glossaries)}
                  </b>)}
              </div>
              {/* 不留反悔期（条款写的是「不可恢复」），所以防误删全靠这一步足够重 */}
              <div style={{ ...body, marginTop: space.s4, marginBottom: space.s2 }}>
                {L.x("请输入 {0} 以确认。", "Type {0} to confirm.",
                  <b key="e" style={{ color: semantic.text.primary, fontFamily: fonts.mono }}>{accountEmail}</b>)}
              </div>
              <Input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={accountEmail} autoFocus />
            </>
          )}

          {err && <div style={{ ...body, marginTop: space.s3, color: semantic.accent.text }}>{err}</div>}

          <div style={{ display: "flex", gap: space.s2, marginTop: space.s4, justifyContent: "flex-end" }}>
            <Button size="sm" ghost onClick={() => setOpen(false)}>{L("取消", "Cancel")}</Button>
            {pre?.canDelete && (
              <button
                className="tx-focus"
                disabled={!matches || busy}
                onClick={() => void submit()}
                style={{ padding: "0 14px", height: 32, fontFamily: fonts.sans, fontSize: 13, fontWeight: 500, background: semantic.danger.fillHover, color: semantic.text.onAccent, border: "none", borderRadius: radius.sm, cursor: matches && !busy ? "pointer" : "not-allowed", opacity: matches && !busy ? 1 : 0.45 }}
              >
                {busy ? L("正在删除…", "Deleting…") : L("永久注销", "Delete permanently")}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** 「删除转录记录」：删稿与音频，**账户留着**。
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
        {L("一次性删除全部文字稿与音频，不可恢复。账户、术语库与余额都保留。",
           "Delete all transcripts and audio at once. Cannot be undone. Your account, glossaries and balance stay.")}
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
  balance: number;
  onTopUp: () => void;
  email?: string | null; // 真实账户邮箱；null = 演示占位
  glossaryCount?: number; // 术语库本数（徽标/概览）
  jobCount?: number; // 转录条数（「删除全部」要写出条数，0 时按钮禁用）
  onManageGlossary?: () => void; // 跳术语库整页（浮窗内不做编辑器）
  onPurged?: () => void; // 删完转录：调用方刷新列表与账本
  onDeleted?: () => void; // 注销成功：账户已经没了，调用方负责登出回宣传页
  referral?: ReferralInfo; // 推荐礼金（2026-09-03）：缺省不显示那一块（老后端 / 演示态）
}

// ── 推荐同行（Growth 需求单《推荐礼金》§三，文案以需求单为准、一字不改）──────────
// 两人各得 $5，对方首次充值时到账。⛔ 不出现速度或准确率。
function ReferralBlock({ r }: { r: ReferralInfo }) {
  const L = useL();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!r.link) return;
    try { await navigator.clipboard.writeText(r.link); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    catch { /* 剪贴板不可用（http / 权限）：链接就在旁边，用户手动选 */ }
  };
  // ⚠️ 金额只有一个来源＝后端（`referrals.GIFT_CENTS`）。此前这里写死 500：
  // 改成 $8 的那天后端改了、界面还写着 $5，而且**不报错**（2026-09-03 复核）
  const giftCents = r.giftCents ?? 500;
  const gift = usd(giftCents / 100);
  // 介绍句用整数写法（需求单原文不带小数位）；有零头时才退回两位小数。
  // ⚠️ 注释里也不许出现金额字面量——`pricing.guard` 扫全文，写了就是假阳性（同 refund.guard 的教训）
  const giftShort = giftCents % 100 === 0 ? `$${giftCents / 100}` : gift;
  return (
    <>
      <div style={{ ...type.label, marginBottom: space.s3, paddingLeft: space.s5 }}>{L("推荐同行", "Invite a colleague")}</div>
      <div style={{ border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden", marginBottom: space.s6 }}>
        <div style={{ padding: `${space.s4}px ${space.s5}px`, fontSize: 13, color: semantic.text.secondary, lineHeight: 1.6 }}>
          {L.t("邀请同行试试，两人各得 {0} —— 对方首次充值时到账。",
               "Invite a fellow researcher: you both get {0} when they make their first top-up.", giftShort)}
        </div>
        <Divider />
        <SettingRow label={L("你的链接", "Your link")}>
          {/* 只允许在 ?ref= 前换行：break-all 会把推荐码从中间掰开（实测「MM6ZGD / 3H」），
              而这串字用户是要抄的 */}
          <div style={{ ...monoNum, fontSize: 13, color: semantic.text.primary, overflowWrap: "normal", minWidth: 0 }} data-testid="referral-link">
            {r.link ? <>{r.link.split("?")[0]}<wbr />{"?" + (r.link.split("?")[1] ?? "")}</> : "—"}
          </div>
          <Button size="sm" secondary onClick={() => void copy()} disabled={!r.link}>{copied ? L("已复制", "Copied") : L("复制", "Copy")}</Button>
        </SettingRow>
        <Divider />
        <SettingRow label={L("已推荐", "Referred")}>
          <div style={{ fontSize: 13, color: semantic.text.secondary }}>
            {L.t("已推荐 {0} 人 · 已解锁 {1}", "{0} referred · {1} unlocked", r.referredCount, usd(r.unlockedCents / 100))}
          </div>
          <span />
        </SettingRow>
        {(r.pending || r.received) && <Divider />}
        {r.pending && (
          <SettingRow label={L("推荐礼金", "Referral credit")}>
            <div style={{ fontSize: 13, color: semantic.text.secondary }}>
              {L.t("推荐礼金 {0} · 首充即解锁 · 有效期至 {1}", "Referral credit {0} · unlocks on your first top-up · valid until {1}", usd(r.pending.amountCents / 100), r.pending.expiresAt)}
            </div>
            <span />
          </SettingRow>
        )}
        {r.received && !r.pending && (
          <SettingRow label={L("推荐礼金", "Referral credit")}>
            <div style={{ fontSize: 13, color: semantic.success.text }}>{L.t("推荐礼金 {0} 已到账", "Referral credit {0} received", gift)}</div>
            <span />
          </SettingRow>
        )}
      </div>
    </>
  );
}

export function SettingsModal({ open, onClose, balance, onTopUp, email, glossaryCount, jobCount = 0, onManageGlossary, onPurged, onDeleted, referral }: SettingsModalProps) {
  const accountEmail = email ?? "liu@gmail.com";
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
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 640, maxHeight: "100%", background: semantic.surface.raised, borderRadius: radius.lg, boxShadow: shadow.lg, overflow: "hidden", display: "flex", flexDirection: "column", animation: "floatUp .24s ease both" }}>
        {/* 头部 */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: `${space.s4}px ${space.s6}px`, borderBottom: `1px solid ${semantic.border.default}`, flex: "0 0 auto" }}>
          <span style={{ ...type.h2 }}>{L("账户设置", "Account settings")}</span>
          <button className="tx-focus" onClick={onClose} aria-label={L("关闭", "Close")}
            style={{ width: 32, height: 32, display: "grid", placeItems: "center", borderRadius: radius.sm, border: "none", background: semantic.surface.sunken, color: semantic.text.muted, fontSize: 13, cursor: "pointer" }}>✕</button>
        </div>

        {/* 内容（可滚动） */}
        <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: `${space.s5}px ${space.s6}px ${space.s6 + 4}px` }}>
          {/* 账户 */}
          <div style={{ border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden", marginBottom: space.s6 }}>
            <SettingRow label={L("邮箱", "Email")}>
              <div style={{ fontSize: 14, color: semantic.text.primary }}>{accountEmail}</div>
              <Button size="sm" ghost>{L("更改", "Change")}</Button>
            </SettingRow>
            <Divider />
            <SettingRow label={L("登录方式", "Sign-in")}>
              <div style={{ fontSize: 13, color: semantic.text.secondary }}>{L("邮箱验证码 · 无密码", "Email code · passwordless")}</div>
              <span />
            </SettingRow>
            <Divider />
            <SettingRow label={L("账户余额", "Balance")}>
              <div style={{ display: "flex", alignItems: "baseline", gap: space.s3 }}>
                <span style={{ ...monoNum, fontSize: 16, fontWeight: 500, color: semantic.text.primary }}>{usd(balance)}</span>
              </div>
              <Button size="sm" secondary onClick={onTopUp}>{L("充值", "Top up")}</Button>
            </SettingRow>
            <Divider />
            <SettingRow label={L("计费单价", "Rate")}>
              <div style={{ fontSize: 13, color: semantic.text.secondary, display: "flex", flexWrap: "wrap", gap: space.s3, justifyContent: "flex-end" }}>
                <span>
                  {L("27 门语言", "All 27 languages")} <span style={{ ...monoNum, color: semantic.text.primary }}>{usd(RATE_PER_HOUR)}</span>
                </span>
                <span style={{ color: semantic.text.muted }}>/{L("小时", "hour")} · {L("美元结算", "USD")}</span>
              </div>
              <span />
            </SettingRow>
            <Divider />
            <SettingRow label={L("术语库", "Glossary")}>
              <div style={{ fontSize: 13, color: semantic.text.secondary }}>
                {glossaryCount && glossaryCount > 0
                  ? L.t("{0} 本术语库", glossaryCount > 1 ? "{0} glossaries" : "{0} glossary", glossaryCount)
                  : L("还没有术语库", "No glossary yet")}
              </div>
              <Button size="sm" secondary onClick={onManageGlossary}>{L("管理", "Manage")} →</Button>
            </SettingRow>
          </div>

          {/* 推荐同行（只在后端给了推荐信息时显示：演示态 / 老后端没有这一块） */}
          {referral && <ReferralBlock r={referral} />}

          {/* 数据与隐私 */}
          <div style={{ ...type.label, marginBottom: space.s3, paddingLeft: space.s5 }}>{L("数据与隐私", "Data & privacy")}</div>
          <div style={{ border: `1px solid ${semantic.border.default}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden", marginBottom: space.s6 }}>
            <SettingRow label={L("音频保留", "Audio retention")}>
              <div style={{ fontSize: 13, color: semantic.text.secondary, lineHeight: 1.6 }}>
                {L.x("转录完成后，原始音频保留 {0} 方便你回来下载，之后自动删除。文字稿保留 30 天，请及时导出以保存。",
                   "After transcription, original audio is kept for {0} so you can re-download, then auto-deleted. Transcripts are kept for 30 days; please export to keep them.",
                   <b key="d" style={{ color: semantic.text.primary }}>{L("7 天", "7 days")}</b>)}
              </div>
              <span />
            </SettingRow>
            <Divider />
            <PurgeTranscripts count={jobCount} onPurged={onPurged} />
          </div>

          {/* 危险区 */}
          <div style={{ ...type.label, color: semantic.accent.text, marginBottom: space.s3, paddingLeft: space.s5 }}>{L("危险区", "Danger zone")}</div>
          <div style={{ border: `1px solid ${semantic.accent.bgSoft}`, borderRadius: radius.md, background: semantic.surface.raised, overflow: "hidden" }}>
            <CloseAccountSection accountEmail={accountEmail} onDeleted={onDeleted} />
          </div>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { semantic, fonts, radius, shadow, labelStyle, space } from "../styles/tokens";
import { useL, useUILang } from "../lib/i18n";
import { usd } from "../lib/pricing";
import { Brand } from "./Brand";
import { LangToggle } from "./LangToggle";
import { LIVE_APP_LANGS } from "../lib/appI18n";

interface SidebarProps {
  active: string;
  onNav: (id: string) => void;
  onOpenBilling: () => void; // 账单改为浮窗，不再是页面跳转
  onOpenSettings: () => void; // 设置改为浮窗，不再是页面跳转
  onLogout: () => void;
  email?: string | null; // 真实账户邮箱；null = 演示模式占位
  balance: number;
  // 在路上的充值（2026-09-06，微信 / 支付宝到账慢）：>0 在余额下方显示「入账中」；到账那几秒显示「已到账」
  pendingTopupCents?: number;
  justCreditedCents?: number | null;
  // 免费额度剩余秒；>0 才在余额下方显示（用完即隐藏）。
  // ⚠️ 显示成**分钟**，不是 fmtClock 的 1:27:00（2026-08-31）：额度本来就是按分钟发的
  // （企业邮箱 180 分钟 / 个人 60 分钟），用户收到的邮件里也是分钟。此前侧栏是时钟格式、
  // 上传卡是时钟格式，同一屏上两处说同一件事都要用户自己换算回分钟。
  // 上传卡（Idle）与这里必须同口径——分家的症状是一屏上出现两个长得不一样的同一个数。
  freeLeftSeconds?: number;
  glossaryCount?: number; // 术语库本数（徽标，0/空不显示）
  isAdmin?: boolean;      // 管理员才显示「运营驾驶舱」入口（后端 me.isAdmin）
}

const EXPANDED = 232;
const COLLAPSED = 64;
const STORE_KEY = "tx-sidebar-collapsed";

// 内联描边图标（项目惯例：stroke=currentColor，颜色由父级 color 决定）
function Svg({ children, size = 19 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: "0 0 auto" }}
    >
      {children}
    </svg>
  );
}
const IconNew = () => <Svg><path d="M12 5v14M5 12h14" /></Svg>;
const IconList = () => <Svg><path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" /></Svg>;
const IconBook = () => <Svg><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></Svg>;
// 划掉的眼睛：脱敏＝让这部分看不见（隐私领域的通用符号，不用学）。
// ⚠️ 这里跟别处的 ✦ 故意不一样，别为了统一改回去：✦ 标的是「这条稿走过后处理」
// （历史页徽标 / 详情页接力卡），而这个是「脱敏规则」的配置入口，两者语义不同。
const IconRedact = () => (
  <Svg>
    <path d="M2.5 12s3.6-6 9.5-6 9.5 6 9.5 6-3.6 6-9.5 6-9.5-6-9.5-6z" />
    <circle cx="12" cy="12" r="2.2" />
    <path d="M4.5 19.5L19.5 4.5" />
  </Svg>
);
const IconBilling = () => <Svg><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M3 10h18" /></Svg>;
const IconSettings = () => (
  <Svg size={17}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </Svg>
);
const IconLogout = () => <Svg><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5M21 12H9" /></Svg>;
const IconGauge = () => <Svg><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></Svg>;  // 活动脉冲：运营监控
const ChevLeft = () => <Svg size={18}><path d="M15 6l-6 6 6 6" /></Svg>;
const ChevRight = () => <Svg size={18}><path d="M9 6l6 6-6 6" /></Svg>;
const ChevDown = () => <Svg size={15}><path d="M6 9l6 6 6-6" /></Svg>;

// 弹出菜单的一行（图标 + 文字，hover 整行高亮）
function MenuRow({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      style={{ display: "flex", alignItems: "center", gap: space.s3, padding: "8px 16px", fontSize: 13, color: semantic.text.primary, cursor: "pointer" }}
      onMouseEnter={(e) => { e.currentTarget.style.background = semantic.surface.sunken; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
    >
      <span style={{ color: semantic.text.secondary, display: "flex" }}>{icon}</span>
      {label}
    </div>
  );
}

export function Sidebar({ active, onNav, onOpenBilling, onOpenSettings, onLogout, balance, pendingTopupCents, justCreditedCents, freeLeftSeconds, email, glossaryCount, isAdmin }: SidebarProps) {
  const accountEmail = email ?? "liu@gmail.com";
  const accountName = email ? email.split("@")[0] : "刘洋";
  const avatarChar = accountName.slice(0, 1).toUpperCase();
  const L = useL();
  // 免费额度：报分钟。⚠️ 剩不到一分钟时报「< 1」不报「0」（2026-09-01）——
  // 向下取整的方向是对的（这是我们欠用户的额度，宁可少报），但这一行**只在还有额度时才显示**，
  // 而屏幕上写着「免费额度 0 分钟」等于自己跟自己打架，用户会读成「已经用完了」。
  // 占位符吃字符串，所以八门译文一个字都不用改（`{0} Min.` / `{0} 分` 各自套上去都成立）。
  const freeMin = Math.floor((freeLeftSeconds ?? 0) / 60);
  const freeMinText = L.t("{0} 分钟", "{0} min", freeMin >= 1 ? freeMin : "< 1");
  const { uiLang, setUiLang } = useUILang();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(STORE_KEY) === "1"; } catch { return false; }
  });
  const [menu, setMenu] = useState(false);

  // Esc 关账号浮层：此前只接了点遮罩，键盘用户无法关闭（2026-08-06 审计 P2-1）
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  const [togHover, setTogHover] = useState(false);
  const toggle = () =>
    setCollapsed((c) => {
      const n = !c;
      try { localStorage.setItem(STORE_KEY, n ? "1" : "0"); } catch { /* 隐私模式忽略 */ }
      return n;
    });

  const links: { id: string; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: "new", label: L("新建转录", "New transcription"), icon: <IconNew /> },
    { id: "history", label: L("我的转录", "My transcripts"), icon: <IconList /> },
    { id: "glossary", label: L("术语库", "Glossary"), icon: <IconBook />, badge: glossaryCount },
    { id: "postprocess", label: L("脱敏规则", "Redaction rules"), icon: <IconRedact /> },
  ];
  if (isAdmin) links.push({ id: "admin", label: L("运营驾驶舱", "Operations"), icon: <IconGauge /> });

  return (
    <div
      style={{
        width: collapsed ? COLLAPSED : EXPANDED,
        flex: `0 0 ${collapsed ? COLLAPSED : EXPANDED}px`,
        height: "100%",
        background: semantic.surface.page,
        borderRight: `1px solid ${semantic.border.default}`,
        display: "flex",
        flexDirection: "column",
        transition: "width .18s ease, flex-basis .18s ease",
      }}
    >
      {/* 顶部：品牌 + 折叠按钮 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "space-between",
          padding: collapsed ? "20px 0 16px" : "22px 18px 16px",
        }}
      >
        {/* ⚠️ 收起/展开必须是真按钮：原来两边都是 <span onClick>，没有 role、没有 tabIndex、
            没有键盘处理——只用键盘的人根本收不起也展不开侧边栏。
            收起态另有一层问题：那时唯一的入口是这个 T. 标志，而它**看不出可以点**
            （没箭头、悬停不变色，只有一个 title），用户想不到去点 logo（2026-08-22 巡检）。
            所以悬停/聚焦时就地换成 ›：位置不变、指向明确，收起态也有了看得见的入口。 */}
        {collapsed ? (
          <button
            className="tx-focus"
            onClick={toggle}
            aria-label={L("展开侧边栏", "Expand sidebar")}
            title={L("展开侧边栏", "Expand sidebar")}
            onMouseEnter={() => setTogHover(true)}
            onMouseLeave={() => setTogHover(false)}
            onFocus={() => setTogHover(true)}
            onBlur={() => setTogHover(false)}
            style={{ border: "none", background: "transparent", padding: 0, width: 32, height: 32, display: "grid", placeItems: "center", borderRadius: radius.sm, cursor: "pointer", color: semantic.text.muted }}
          >
            {togHover ? <ChevRight /> : (
              <span aria-hidden="true" style={{ fontFamily: fonts.serif, fontSize: 22, fontWeight: 500, letterSpacing: -0.4, color: semantic.text.primary }}>
                T<span style={{ color: semantic.accent.brand }}>.</span>
              </span>
            )}
          </button>
        ) : (
          <>
            <Brand size={21} />
            <button
              className="tx-focus"
              onClick={toggle}
              aria-label={L("收起侧边栏", "Collapse sidebar")}
              title={L("收起侧边栏", "Collapse sidebar")}
              onMouseEnter={() => setTogHover(true)}
              onMouseLeave={() => setTogHover(false)}
              onFocus={() => setTogHover(true)}
              onBlur={() => setTogHover(false)}
              style={{ border: "none", background: "transparent", padding: 0, width: 28, height: 28, display: "grid", placeItems: "center", borderRadius: radius.sm, cursor: "pointer", color: togHover ? semantic.text.primary : semantic.text.muted }}
            >
              <ChevLeft />
            </button>
          </>
        )}
      </div>

      {/* 主导航 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 3, padding: "4px 12px" }}>
        {links.map((lk) => {
          const on = active === lk.id;
          return (
            <div
              key={lk.id}
              onClick={() => onNav(lk.id)}
              title={collapsed ? lk.label : undefined}
              style={{
                display: "flex",
                alignItems: "center",
                gap: space.s3,
                justifyContent: collapsed ? "center" : "flex-start",
                padding: collapsed ? "10px 0" : "8px 12px",
                borderRadius: radius.sm,
                cursor: "pointer",
                color: on ? semantic.text.primary : semantic.text.secondary,
                background: on ? semantic.surface.sunken : "transparent",
                fontFamily: fonts.sans,
                fontSize: 14,
                fontWeight: on ? 500 : 400,
                transition: "background .12s, color .12s",
              }}
              onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = semantic.border.subtle; }}
              onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = "transparent"; }}
            >
              {lk.icon}
              {!collapsed && <span style={{ flex: 1 }}>{lk.label}</span>}
              {!collapsed && lk.badge ? (
                <span style={{ display: "inline-flex", alignItems: "center", height: 19, lineHeight: 1, transform: "translateY(1.5px)", fontFamily: fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: semantic.text.muted, flex: "0 0 auto" }}>
                  {lk.badge}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      <div style={{ flex: 1 }} />

      {/* 底部：余额卡（展开态）+ 用户区 */}
      <div style={{ padding: collapsed ? "12px 12px 16px" : "12px 14px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
        {!collapsed && (
          <div
            onClick={onOpenBilling}
            title={L("查看账单", "View billing")}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              padding: "11px 13px",
              borderRadius: radius.sm,
              background: semantic.surface.sunken,
              border: `1px solid ${semantic.border.default}`,
              cursor: "pointer",
              transition: "border-color .12s",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = semantic.text.ghost; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = semantic.border.default; }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span style={{ ...labelStyle }}>{L("余额", "Balance")}</span>
              <span style={{ fontFamily: fonts.mono, fontWeight: 500, fontSize: 15, fontVariantNumeric: "tabular-nums", color: semantic.text.primary }}>{usd(balance)}</span>
            </div>
            {/* 充值在路上：付完款回到应用余额还没变，这一行告诉人「钱在路上、不用重付」；到账后换成「已到账」停几秒 */}
            {justCreditedCents ? (
              <div role="status" style={{ fontSize: 12, color: semantic.success.text, fontFamily: fonts.sans }}>
                ✓ {L.t("{0} 已到账", "{0} credited", usd(justCreditedCents / 100))}
              </div>
            ) : (pendingTopupCents ?? 0) > 0 ? (
              <div role="status" style={{ fontSize: 12, color: semantic.warning.text, fontFamily: fonts.sans }}>
                ⏳ {L.t("{0} 入账中 · 通常 1–3 分钟", "{0} on its way · usually 1–3 min", usd((pendingTopupCents ?? 0) / 100))}
              </div>
            ) : null}
            {(freeLeftSeconds ?? 0) > 0 && (
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ ...labelStyle }}>{L("免费额度", "Free quota")}</span>
                <span style={{ fontFamily: fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: semantic.text.secondary }}>{freeMinText}</span>
              </div>
            )}
          </div>
        )}

        {/* 用户区 + 弹出菜单 */}
        <div style={{ position: "relative" }}>
          <div
            onClick={() => setMenu((m) => !m)}
            title={collapsed ? L("账户", "Account") : undefined}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              justifyContent: collapsed ? "center" : "flex-start",
              padding: collapsed ? "4px 0" : "6px 6px",
              borderRadius: radius.sm,
              cursor: "pointer",
            }}
          >
            <span
              style={{
                width: 32,
                height: 32,
                borderRadius: 16,
                // 承载首字母，所以走 fill 不走 brand（brand 上白字仅 4.07:1）
                background: semantic.accent.fill,
                color: semantic.surface.raised,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 13,
                fontWeight: 500,
                flex: "0 0 auto",
              }}
            >
              {avatarChar}
            </span>
            {!collapsed && (
              <>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontFamily: fonts.sans, fontSize: 13, fontWeight: 500, color: semantic.text.primary, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {accountName}
                  </div>
                  <div style={{ fontSize: 11, color: semantic.text.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {accountEmail}
                  </div>
                </div>
                <span style={{ color: semantic.text.muted, display: "flex" }}><ChevDown /></span>
              </>
            )}
          </div>

          {menu && (
            <>
              {/* 点外面关闭 */}
              <div style={{ position: "fixed", inset: 0, zIndex: 60 }} onClick={() => setMenu(false)} />
              {/* 菜单：展开态弹用户区上方；收起态弹右侧 */}
              <div
                style={{
                  position: "absolute",
                  zIndex: 70,
                  background: semantic.surface.raised,
                  border: `1px solid ${semantic.border.default}`,
                  borderRadius: radius.md,
                  boxShadow: shadow.md,
                  overflow: "hidden",
                  ...(collapsed
                    ? { left: "calc(100% + 10px)", bottom: 0, width: 222 }
                    : { left: 0, right: 0, bottom: "calc(100% + 8px)" }),
                }}
              >
                {/* 头部：姓名 / 邮箱 / 余额 */}
                <div style={{ padding: "13px 15px 12px", borderBottom: `1px solid ${semantic.border.faint}` }}>
                  <div style={{ fontFamily: fonts.sans, fontSize: 14, fontWeight: 600, color: semantic.text.primary }}>{accountName}</div>
                  <div style={{ fontSize: 12, color: semantic.text.muted, marginTop: 2 }}>{accountEmail}</div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 10 }}>
                    <span style={{ ...labelStyle }}>{L("余额", "Balance")}</span>
                    <span style={{ fontFamily: fonts.mono, fontWeight: 500, fontSize: 14, fontVariantNumeric: "tabular-nums", color: semantic.text.primary }}>{usd(balance)}</span>
                  </div>
                  {(freeLeftSeconds ?? 0) > 0 && (
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginTop: 6 }}>
                      <span style={{ ...labelStyle }}>{L("免费额度", "Free quota")}</span>
                      <span style={{ fontFamily: fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: semantic.text.secondary }}>{freeMinText}</span>
                    </div>
                  )}
                </div>

                {/* 账户项 */}
                <div style={{ padding: "6px 0", borderBottom: `1px solid ${semantic.border.faint}` }}>
                  <MenuRow icon={<IconBilling />} label={L("充值与账单", "Billing")} onClick={() => { setMenu(false); onOpenBilling(); }} />
                  <MenuRow icon={<IconSettings />} label={L("账户设置", "Account settings")} onClick={() => { setMenu(false); onOpenSettings(); }} />
                </div>

                {/* 界面语言 */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 15px", borderBottom: `1px solid ${semantic.border.faint}` }}>
                  <span style={{ fontSize: 13, color: semantic.text.secondary }}>{L("界面语言", "Language")}</span>
                  <LangToggle value={uiLang} onChange={setUiLang} langs={LIVE_APP_LANGS} subtle up />
                </div>

                {/* 退出 */}
                <div style={{ padding: "6px 0" }}>
                  <MenuRow icon={<IconLogout />} label={L("退出登录", "Sign out")} onClick={() => { setMenu(false); onLogout(); }} />
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

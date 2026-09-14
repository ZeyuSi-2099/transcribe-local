import { useState } from "react";
import { semantic, fonts, radius, space } from "../styles/tokens";
import { useL, useUILang } from "../lib/i18n";
import { Brand } from "./Brand";
import { LangToggle } from "./LangToggle";
import { LIVE_APP_LANGS } from "../lib/appI18n";

// 本机版（与线上不同）：去掉余额卡、免费额度、在路上的充值、账户菜单与退出登录——本机单用户、不收费。
// 底部换成「设置」入口 + 界面语言；「运营驾驶舱」改名「运行面板」。
interface SidebarProps {
  active: string;
  onNav: (id: string) => void;
  onOpenSettings: () => void; // 设置是浮窗，不是页面跳转
  glossaryCount?: number; // 术语库本数（徽标，0/空不显示）
  isAdmin?: boolean;      // 本机恒为 true；参数留着与线上同形
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
const IconSettings = () => (
  <Svg>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </Svg>
);
const IconGauge = () => <Svg><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></Svg>;  // 活动脉冲：运行面板
const ChevLeft = () => <Svg size={18}><path d="M15 6l-6 6 6 6" /></Svg>;
const ChevRight = () => <Svg size={18}><path d="M9 6l6 6-6 6" /></Svg>;

export function Sidebar({ active, onNav, onOpenSettings, glossaryCount, isAdmin }: SidebarProps) {
  const L = useL();
  const { uiLang, setUiLang } = useUILang();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(STORE_KEY) === "1"; } catch { return false; }
  });

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
  if (isAdmin) links.push({ id: "admin", label: L("运行面板", "System status"), icon: <IconGauge /> });

  const rowStyle = (on: boolean): React.CSSProperties => ({
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
  });

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
      {/* 顶部：品牌 + 折叠按钮（收起/展开必须是真按钮，理由见线上同处注释） */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "space-between",
          padding: collapsed ? "20px 0 16px" : "22px 18px 16px",
        }}
      >
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
              style={rowStyle(on)}
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

      {/* 底部：设置 + 界面语言 */}
      <div style={{ padding: collapsed ? "12px 12px 16px" : "12px 12px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
        <button
          className="tx-focus"
          onClick={onOpenSettings}
          title={collapsed ? L("设置", "Settings") : undefined}
          aria-label={L("设置", "Settings")}
          style={{ ...rowStyle(false), border: "none", width: "100%", textAlign: "left" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = semantic.border.subtle; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
        >
          <IconSettings />
          {!collapsed && <span style={{ flex: 1 }}>{L("设置", "Settings")}</span>}
        </button>
        {!collapsed && (
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 12px" }}>
            <span style={{ fontSize: 13, color: semantic.text.secondary }}>{L("界面语言", "Language")}</span>
            <LangToggle value={uiLang} onChange={setUiLang} langs={LIVE_APP_LANGS} subtle up />
          </div>
        )}
      </div>
    </div>
  );
}

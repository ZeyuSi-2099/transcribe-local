// Transcribe · 设计令牌 v1 提案（可直接替换 src/styles/tokens.ts）
// 变更摘要：
//  - 新增 6 色 / 修正 3 处对比度不达标（详见《Design System 提案》①）
//  - 旧字段全部保留（向后兼容），逐屏迁移时再切到语义令牌
//  - 新增：semantic（语义色）、type（字阶）、space（间距标尺）、motion（动效）、focusRing

// ───────────────────────── 原始色板（颜料，不直接在界面代码引用） ─────────────────────────
export const palette = {
  white:  "#FFFFFF",  // 浮层面（菜单/下拉/浮窗）——2026-08-31 由 sand25 上提
  sand25: "#FFFCF5",  // 卡片面（2026-08-31 由 sand50 上提，见 semantic.surface.raised）
  sand50: "#FBF7EE",  // paper
  sand75: "#F7F1E6",  // 新增：行高亮（编辑器当前行，比 page 浅、比 paper 深半档）
  sand100: "#F2EDE3", // bg
  sand200: "#EDE6D7", // panel
  sand300: "#ECE4D4", // line-2
  sand350: "#E2DACA", // line
  sand400: "#DFD5BE", // lineSoft
  sand500: "#C9BFA9", // 原 Button.module.css 硬编码描边，收编入册
  ink900: "#1F1A14",
  ink700: "#5A4E3F",
  ink500: "#75654F",  // 修正：原 #8A7A66 在纸面仅 3.88:1
  ink300: "#B5A88F",
  terra500: "#C8553D",
  terra550: "#BC4D34", // 新增：承载白字的填充（白字 4.62:1 ✓）
  terra700: "#A8442F",
  terra800: "#9E3F2B", // 新增：danger hover
  terra100: "#F2D9D1",
  terra50: "#FAEDE7",
  green50: "#EEF1E6",  // 新增：成功条幅底（复核清零绿条幅，设计稿 04 里程碑）
  green300: "#9FC08A", // 新增：墨底 toast 上的成功绿（撤销链接，设计稿 04 成功三层）
  green500: "#5C7A4E",
  green700: "#54713F", // 新增：成功文字（5.15:1 ✓）
  gold50: "#FBF3E2",   // 新增：警告图标块暖底（错误卡 / 紧凑警告条，设计稿 04 出错）
  gold500: "#C8993D",  // 仅限装饰（2.43:1，不可承载信息）
  gold600: "#A37A2A",  // 新增：警告图标（≥3:1 ✓）
  gold700: "#85661F",  // 新增：警告文字（5.01:1 ✓）
} as const;

// ───────────────────────── 语义令牌（界面代码一律引用这里） ─────────────────────────
export const semantic = {
  surface: {
    page: palette.sand100,        // 应用底色（global.css 的径向渐变氛围保留）
    // 2026-08-31 卡片面由 sand50 上提到 sand25：原先四档米色两两之间的相对亮度差只有
    // 1.10 倍上下，所有分层全靠 1px 描边扛，界面读起来像「一张纸上画格子」而不是有层次。
    // 上提后 卡片/页面 = 1.15，配 shadow.sm 才分得开。浮层同步上提到纯白（见 float）。
    // ⚠️ 不要改回 sand50：那一档现在的身份是 text.onAccent（深色填充上的反白），两者
    // 曾经共用同一个色值，但语义无关——一起动会让主按钮的白字跟着卡片面走。
    raised: palette.sand25,       // 卡片 / 模态
    float: palette.white,         // 浮层：菜单 / 下拉 / popover——永远比卡片再亮一档，配 border.strong + shadow.lg
    sunken: palette.sand200,      // 选中底、统计块、嵌入面板
    rowActive: palette.sand75,    // 行高亮（编辑器当前行）
    overlay: "rgba(31,26,20,.44)",// 浮窗遮罩
  },
  border: {
    subtle: palette.sand300,      // 行分隔
    default: palette.sand350,     // 卡片描边
    faint: palette.sand400,       // 柔分隔线（弹层内分隔 / LangToggle 描边）— 原 colors.lineSoft，工单 Q1-A 收编
    strong: palette.sand500,      // 可交互控件描边（下拉、secondary 按钮）
  },
  text: {
    primary: palette.ink900,
    secondary: palette.ink700,
    muted: palette.ink500,        // 辅助小字 / 表头 / 时间（AA ✓）
    ghost: palette.ink300,        // 仅占位符 / 禁用，不承载必读信息
    onAccent: palette.sand50,     // 深色填充上的反白
  },
  accent: {
    brand: palette.terra500,      // 图形 / ≥19px 大字 / 描边 / 波形
    fill: palette.terra550,       // 主按钮等承载白字的填充
    fillHover: palette.terra700,
    text: palette.terra700,       // 小号强调文字 / 链接
    bgSoft: palette.terra100,
    bgTint: palette.terra50,
  },
  danger: {                       // 赤陶一色双义：危险沿用 terra（不引入新红）
    fill: palette.terra700,
    fillHover: palette.terra800,
    text: palette.terra700,
  },
  success: { text: palette.green700, graphic: palette.green500, bg: palette.green50, onDark: palette.green300 },
  warning: { text: palette.gold700, icon: palette.gold600, decor: palette.gold500, bg: palette.gold50 },
  focusRing: palette.terra700,    // outline: 2px solid · offset 2px · 仅 :focus-visible
} as const;

// 字体栈**按语言分栈**，所以这里只给变量名，真值在 tokens.css 里按 :lang() 切换
// （审计 B1：原先 serif 栈把 'Noto Serif SC' 排在最前，英文标题实际用的是中文字体的
// 拉丁字形——Canvas 实测 40px「Human-grade transcripts」493px vs Source Serif 4 的 480px）。
// 想知道各门到底是什么栈，看 tokens.css 的 --font-serif / --font-sans。
export const fonts = {
  serif: "var(--font-serif)",
  serifLat: "var(--font-serifLat)",
  sans: "var(--font-sans)",
  mono: "var(--font-mono)",
} as const;

/** 各语言的真值（预渲染与测试要断言时用；浏览器里生效的是 tokens.css 的 CSS 变量） */
export const FONT_STACKS = {
  latin: { serif: "'Source Serif 4', Georgia, serif", sans: "'Inter', system-ui, sans-serif" },
  zh:    { serif: "'Noto Serif SC', 'Source Serif 4', Georgia, serif", sans: "'Inter', 'Noto Sans SC', system-ui, sans-serif" },
  ja:    { serif: "'Noto Serif JP', 'Source Serif 4', Georgia, serif", sans: "'Inter', 'Noto Sans JP', system-ui, sans-serif" },
} as const;

/** 正文行高。真值在 tokens.css：`:root` 是拉丁值，`:lang(zh|ja|ko)` 整体抬一档。
 *  为什么绕 CSS 变量：行高要跟界面语言走，而 tokens.ts 是编译期常量、拿不到当前语言。
 *  用在组件里：`lineHeight: lh.read`。图标那种 `lineHeight: 1` 不要用这个。 */
export const lh = {
  body: "var(--lh-body)",
  read: "var(--lh-read)",
} as const;

// ───────────────────────── 字阶（serif 仅 display/title 两级 = 品牌发声） ─────────────────────────
// 用法：style={{ ...type.h1 }}
export const type = {
  display: { fontFamily: fonts.serif, fontSize: 44, lineHeight: 1.18, fontWeight: 700, letterSpacing: -0.8 },
  title:   { fontFamily: fonts.serif, fontSize: 30, lineHeight: 1.3,  fontWeight: 700, letterSpacing: -0.6 },
  // ⚠️ h1 是**衬线**（2026-08-31）：衬线只给品牌发声，这条纪律没变——变的是认识到
  // 「每一屏的页面标题」正是品牌发声的位置。在此之前登录之后除了左上角 logo 就再也见不到
  // 衬线，公开站有性格、应用内是一套通用 sans 界面，同一个品牌两种气质。
  // 作用域恰好是五个 <h1>（我的转录 / 术语库 / 脱敏规则 / 运营驾驶舱 / 详情页的文件名），
  // 每屏一个、都在最上面，不与正文抢戏。**不要顺手扩到 h2/h3**：那两档是卡头与卡内小标题，
  // 一屏有十几个，衬线铺开就从"有性格"变成"看不清层级"。
  h1:      { fontFamily: fonts.serif, fontSize: 22, lineHeight: 1.35, fontWeight: 700, letterSpacing: -0.4 },
  h2:      { fontFamily: fonts.sans,  fontSize: 17, lineHeight: 1.4,  fontWeight: 600, letterSpacing: -0.2 },
  h3:      { fontFamily: fonts.sans,  fontSize: 14, lineHeight: 1.45, fontWeight: 600, letterSpacing: 0 },
  // 正文三档的行高走 CSS 变量：拉丁值在 tokens.css 的 :root，CJK 在 :lang() 块里整体抬一档。
  // 写死数字的话中日文读长稿会明显发挤——那是按拉丁字形调出来的值（CJK 没有升降部）。
  // 标题与 mono 不跟着变：短行、且数字对齐依赖固定行高。
  bodyLg:  { fontFamily: fonts.sans,  fontSize: 16, lineHeight: lh.read, fontWeight: 400, letterSpacing: 0 },   // 转录正文
  body:    { fontFamily: fonts.sans,  fontSize: 14, lineHeight: lh.body, fontWeight: 400, letterSpacing: 0 },
  bodySm:  { fontFamily: fonts.sans,  fontSize: 13, lineHeight: lh.body, fontWeight: 400, letterSpacing: 0 },
  caption: { fontFamily: fonts.sans,  fontSize: 12, lineHeight: 1.5,  fontWeight: 400, letterSpacing: 0, color: semantic.text.muted },
  // ⚠️ 字距走 CSS 变量、按语言切（2026-08-31，与行高同一套做法）：1.4px 是照拉丁小型大写字
  // 调的，套到中日文上就是把「另见」「文件」这种两字标签**撑散成「另 见」「文 件」**
  // ——中文本来就没有大小写，uppercase 对它是空操作，真正伤到排版的只有字距这一项。
  // 真值在 tokens.css：`:root` 是拉丁值，`:lang(zh|ja|ko)` 归零。
  // ⚠️ 别在组件里读 `type.label.letterSpacing` 去算宽度——它现在是一个字符串。
  label:   { fontFamily: fonts.sans,  fontSize: 11, lineHeight: 1.2,  fontWeight: 500, letterSpacing: "var(--ls-label)", textTransform: "uppercase" as const, color: semantic.text.muted },
  mono:    { fontFamily: fonts.mono,  fontSize: 13, lineHeight: 1.5,  fontWeight: 400, fontVariantNumeric: "tabular-nums" as const },
  monoSm:  { fontFamily: fonts.mono,  fontSize: 11, lineHeight: 1.5,  fontWeight: 400, fontVariantNumeric: "tabular-nums" as const },
  monoDisplay: { fontFamily: fonts.mono, fontSize: 32, lineHeight: 1.1, fontWeight: 500, fontVariantNumeric: "tabular-nums" as const }, // 账单大金额
  monoStat: { fontFamily: fonts.mono, fontSize: 18, fontWeight: 500, lineHeight: 1.2, fontVariantNumeric: "tabular-nums" as const }, // 中号统计数字（预估卡 / 自定义额）— 工单定案①
} as const;

// ───────────────────────── 间距标尺（4px 基准；禁止 9/11/13/15/18/22 等离尺值） ─────────────────────────
export const space = { s1: 4, s2: 8, s3: 12, s4: 16, s5: 20, s6: 24, s7: 32, s8: 40, s9: 48, s10: 64 } as const;

export const radius = { sm: 8, md: 11, lg: 16, pill: 999 } as const;

// ───────────────────────── 版式骨架（登录后的应用页；营销站不走这里） ─────────────────────────
// **应用内每个页面的根容器都必须用 layout.pagePad**，别再各写各的。
// 2026-08-19 之前是四套值并存（32/64、44/64/24、44/64、28/34/30），最窄那套让术语库右栏
// 距页边只剩 34px，滚动条直接贴着卡片。守卫在 src/lib/layout.guard.test.ts。
export const layout = {
  /** 应用页根容器内边距：上下 32 / 左右 64 */
  pagePad: `${space.s7}px ${space.s10}px`,
} as const;

// 单行输入框 / 与之同排的表头行的统一高度。
// 44 而不是「让内容自己撑」：撑出来的高度跟 --lh-body 走，拉丁 1.7 得 42px、中日文 1.8 得 43px
// ——同一个界面换个语言就错位。三栏页靠这个值让左栏表头与中栏库名框等高，第二行才对得齐。
export const control = { fieldH: 44 } as const;

export const shadow = {
  // 2026-08-31 由 `0 1px 2px rgba(33,27,19,.05)` 加重一档：卡片面与页面底之间相对亮度差
  // 只有 1.15 倍，单靠 1px 描边分不开层。这一档的用法是「贴着页面的卡片」，不是浮层。
  sm: "0 1px 2px rgba(60,42,26,.07), 0 4px 12px -8px rgba(60,42,26,.16)",
  md: "0 14px 44px -22px rgba(60,42,26,.42), 0 3px 10px -5px rgba(60,42,26,.14)",
  lg: "0 30px 70px -30px rgba(60,42,26,.5)",
} as const;

// ───────────────────────── 动效（只动 opacity / transform / color；禁 transition:all） ─────────────────────────
export const motion = {
  fast: "120ms ease-out",                      // 颜色 / 描边 / hover
  base: "180ms cubic-bezier(.2,0,0,1)",        // 位移 / 展开
  slow: "240ms cubic-bezier(.2,0,0,1)",        // 浮窗进场（fade + scale .98→1）
} as const;

// 键盘焦点（仅 :focus-visible）
export const focusRing = { outline: `2px solid ${semantic.focusRing}`, outlineOffset: 2 } as const;

// 兼容：旧 labelStyle（= type.label）
export const labelStyle = type.label;

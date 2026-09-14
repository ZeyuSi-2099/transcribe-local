// RedactScope.tsx — 「脱敏会改什么」说明块（发起卡 + 配置页共用）
//
// 为什么要有这一块：脱敏是一次**不可逆的改写**，还按分钟收钱，而在此之前界面上
// 关于它只讲「保留清单绝不动」——全是在说不改什么，一句没说改什么。
// 2026-08-20 生产实测里，连写期望表的人都漏掉了「地理」与「数值年限」两类
// （江苏→本省、十二个人→十几个人），用户不可能猜得更准。
//
// 内容是 pp-redact/SKILL.md「📋 精准脱敏规则」六个小节的用户语言版，一类一句 + 一个例子。
// ⚠️ 规则改了这里要跟着改：它不是文档摘要，是对用户的承诺。
//
// 版式上**故意不做「类别名 | 例子」两列**：定宽列在译文变长时会悄悄失效
// （红线 11），而这一块最窄要塞进 300px 的右栏。改成每类两行堆叠，天然抗长译文。

import { useEffect, useState } from "react";
import { useL, type LFn } from "../../lib/i18n";
import * as T from "../../styles/tokens";

/** 六类脱敏范围：[这一类是什么, 例子]。顺序同 SKILL.md。 */
export function redactScopeRows(L: LFn): [string, string][] {
  return [
    [L("受访者自己的公司名、子公司名", "The interviewee's own company and subsidiaries"),
     L("明辉电器 → XX公司", "Acme Electronics → XX Company")],
    // 公众人物不算「人名」（Duner 2026-09-03 定）：认出赛力斯董事长认不出受访者是谁，脱敏的目的是后者
    [L("人名（新闻里的公众人物除外）", "People's names (public figures stay)"),
     L("张经理 → X经理", "Sarah Chen → XX")],
    [L("电话、邮箱、微信号", "Phone numbers, emails, messaging IDs"),
     L("13800138000 → XXXXXXXXXXX", "13800138000 → XXXXXXXXXXX")],
    [L("受访者所在的省市", "The city or region the interviewee is in"),
     L("江苏 → 本省", "Boston → our city")],
    [L("团队人数、合作年限、金额", "Team size, length of a partnership, amounts"),
     L("十二个人 → 十几个人", "a team of 12 → a team of about a dozen")],
    [L("受访者自己的项目名", "The interviewee's own project names"),
     L("智慧园区一期 → XX项目", "Smart Campus Phase 1 → XX project")],
  ];
}

/** 说明块正文。窄栏（360 侧栏 / 300 右栏）通用。 */
export function RedactScope() {
  const L = useL();
  const rows = redactScopeRows(L);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: T.space.s3 }}>
      {rows.map(([what, eg]) => (
        <div key={eg} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ ...T.type.bodySm, color: T.semantic.text.secondary }}>{what}</span>
          <span style={{ fontFamily: T.fonts.mono, fontSize: 11, lineHeight: 1.5, color: T.semantic.text.muted, overflowWrap: "anywhere" }}>{eg}</span>
        </div>
      ))}
      <div style={{ ...T.type.caption, lineHeight: 1.65, paddingTop: T.space.s2, borderTop: `1px solid ${T.semantic.border.subtle}` }}>
        {L("保留清单里的词不在此列。唯一的例外是受访者自己的公司——它写进清单也照样会被换掉，因为那正是最能认出人的那个词。",
           "Words on your keep list are left alone. The one exception is the interviewee's own company — it gets replaced even when listed, because that name is what identifies them.")}
      </div>
      <div style={{ ...T.type.caption, lineHeight: 1.65 }}>
        {L("第三方品牌、行业术语、成语、业务数据都保留不动。",
           "Third-party brands, industry terms, set phrases, and business figures stay as they are.")}
      </div>
    </div>
  );
}


/** 展开箭头——规格同红线 8（15px · 收起灰/展开赤陶 · 旋转 90°），与 Result.tsx 的 caret() 一致。 */
function caret(open: boolean) {
  return (
    <span aria-hidden style={{ fontSize: 15, lineHeight: 1, color: open ? T.semantic.accent.text : T.semantic.text.muted, transform: open ? "rotate(90deg)" : "none", transition: `transform ${T.motion.fast}, color ${T.motion.fast}`, display: "inline-block", flex: "0 0 auto" }}>▸</span>
  );
}

/** 折叠壳：默认收起，只占一行。发起卡与配置页共用同一份，别各写各的。 */
export function RedactScopeDisclosure({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const L = useL();
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        className="tx-focus"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", border: "none", background: "transparent", padding: "2px 0", cursor: "pointer", textAlign: "left", fontFamily: T.fonts.sans, fontSize: 12, color: open ? T.semantic.accent.text : T.semantic.text.muted }}
      >
        {caret(open)}
        <span>{L("脱敏会改什么？", "What gets redacted?")}</span>
      </button>
      {open && <div style={{ marginTop: T.space.s3 }}><RedactScope /></div>}
    </div>
  );
}

/** 「脱敏会改什么？」帮助浮窗。
 *
 * 为什么不继续用折叠条（2026-08-21 Duner 定）：这块是**对用户的承诺**，是决定
 * 要不要按下「开始加工」的依据，却缩在一行小字后面，读它要先在窄栏里展开六段。
 * 浮窗把它抬成一次完整的阅读——正文宽度够、不挤压发起卡、读完即关。
 *
 * 关闭三途与设计纪律 4 一致：Esc / 点遮罩 / ✕。用 fixed 而非 absolute：
 * 触发点在详情页右栏（360px）里，absolute inset:0 只会盖住那一栏。
 */
export function RedactScopeHelp() {
  const L = useL();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const title = L("脱敏会改什么？", "What gets redacted?");
  return (
    <>
      <button
        className="tx-focus"
        onClick={() => setOpen(true)}
        aria-label={title}
        title={title}
        style={{ display: "inline-flex", alignItems: "center", gap: 5, border: "none", background: "transparent", padding: "2px 0", cursor: "pointer", fontFamily: T.fonts.sans, fontSize: 12, color: T.semantic.text.muted }}
      >
        {/* 圆圈问号：15px 与展开箭头同规格（红线 8），但它不是箭头——不旋转、不换色 */}
        <span aria-hidden style={{ width: 15, height: 15, borderRadius: "50%", border: `1px solid ${T.semantic.border.strong}`, display: "grid", placeItems: "center", fontSize: 10, lineHeight: 1, color: T.semantic.text.secondary, flex: "0 0 auto" }}>?</span>
        <span>{title}</span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={title}
          onClick={() => setOpen(false)}
          style={{ position: "fixed", inset: 0, zIndex: 200, background: T.semantic.surface.overlay, display: "flex", alignItems: "center", justifyContent: "center", padding: T.space.s6 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ width: "100%", maxWidth: 460, maxHeight: "100%", background: T.semantic.surface.raised, borderRadius: T.radius.lg, boxShadow: T.shadow.lg, overflow: "hidden", display: "flex", flexDirection: "column", animation: "floatUp .24s ease both" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: `${T.space.s4}px ${T.space.s5}px`, borderBottom: `1px solid ${T.semantic.border.default}`, flex: "0 0 auto" }}>
              <span style={{ ...T.type.h3 }}>{title}</span>
              <button className="tx-focus" onClick={() => setOpen(false)} aria-label={L("关闭", "Close")}
                style={{ width: 30, height: 30, display: "grid", placeItems: "center", borderRadius: T.radius.sm, border: "none", background: T.semantic.surface.sunken, color: T.semantic.text.muted, fontSize: 12, cursor: "pointer" }}>✕</button>
            </div>
            <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: `${T.space.s5}px ${T.space.s5}px ${T.space.s6}px` }}>
              <RedactScope />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

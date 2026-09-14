// 语言切换：地球图标 + 当前语言名 + 下拉菜单（2026-08-02 全站统一，替换旧「中 / EN」分段钮——
// 与营销导航同一视觉语言；将来扩语言只改 OPTIONS 一处）。
import { useRef, useState } from "react";
import { semantic, fonts } from "../styles/tokens";
import { LANG_NATIVE, type UILang } from "../lib/i18n";
import { LIVE_APP_LANGS } from "../lib/appI18n";

interface LangToggleProps {
  value: UILang;
  onChange: (v: UILang) => void;
  subtle?: boolean;
  /** 菜单向上展开（宿主容器 overflow:hidden 且按钮贴底时用，如侧栏弹出菜单）。
   *  此形态下菜单用 fixed 定位从按钮位置算：容器裁剪的高度只够两三项，
   *  2026-08-02 放到八门后 absolute 定位会把「English / 中文」两项切在卡片外面看不见。 */
  up?: boolean;
  /** 可选语言（缺省=应用内已放量的语言；营销页传 LIVE_MARKETING_LANGS） */
  langs?: UILang[];
}

export function LangToggle({ value, onChange, subtle, up, langs }: LangToggleProps) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  // up 形态：展开时量一次按钮位置，菜单改用 fixed 脱离宿主的 overflow:hidden
  const [fixedPos, setFixedPos] = useState<{ right: number; bottom: number } | null>(null);
  const toggle = () => {
    setOpen((v) => {
      if (!v && up && btnRef.current) {
        const r = btnRef.current.getBoundingClientRect();
        setFixedPos({ right: window.innerWidth - r.right, bottom: window.innerHeight - r.top + 6 });
      }
      return !v;
    });
  };
  const options: UILang[] = langs ?? LIVE_APP_LANGS;
  // value 不在可选列表时**如实显示它自己**，不冒充英文。
  // 2026-08-02 之前是回落显示 "English"：德语访客登录后看到「English ✓」，随手点一下
  // 就把存储里的 de 覆写成 en——退回营销页会发现网站「忘了」他选的语言。
  const current = value;
  const listed = options.includes(value);
  const borderColor = subtle ? semantic.border.default : semantic.border.faint;
  return (
    <div style={{ position: "relative" }} onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
      <button
        className="tx-focus"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Language"
        ref={btnRef}
        onClick={toggle}
        style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "6px 10px", background: "transparent", border: `1px solid ${borderColor}`, borderRadius: 8, cursor: "pointer", fontFamily: fonts.sans, fontSize: 12.5, fontWeight: 500, color: semantic.text.secondary, whiteSpace: "nowrap" }}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3c2.6 2.6 3.9 5.6 3.9 9S14.6 18.4 12 21c-2.6-2.6-3.9-5.6-3.9-9S9.4 5.6 12 3z" />
        </svg>
        {LANG_NATIVE[current]}
      </button>
      {open && (
        <>
          {/* 点外面关闭 */}
          <div style={{ position: "fixed", inset: 0, zIndex: 80 }} onClick={() => setOpen(false)} />
          <div role="menu" className="tx-scroll" style={{
            ...(up && fixedPos
              ? { position: "fixed" as const, right: fixedPos.right, bottom: fixedPos.bottom }
              : { position: "absolute" as const, top: "calc(100% + 6px)", right: 0 }),
            maxHeight: "min(60vh, 420px)", overflowY: "auto", zIndex: 81, minWidth: 126, padding: 5, background: semantic.surface.float, border: `1px solid ${semantic.border.default}`, borderRadius: 9, boxShadow: "0 10px 30px -14px rgba(60,42,26,.35), 0 2px 8px -4px rgba(60,42,26,.14)" }}>
            {options.map((o) => (
              <button
                key={o}
                role="menuitemradio"
                aria-checked={listed && o === current}
                className="tx-focus"
                onClick={() => { onChange(o); setOpen(false); }}
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, width: "100%", padding: "7px 11px", background: "transparent", border: "none", borderRadius: 6, cursor: "pointer", fontFamily: fonts.sans, fontSize: 13, fontWeight: listed && o === current ? 600 : 400, color: semantic.text.primary, textAlign: "left" }}
              >
                {LANG_NATIVE[o]}
                {listed && o === current && <span style={{ color: semantic.accent.text }} aria-hidden>✓</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

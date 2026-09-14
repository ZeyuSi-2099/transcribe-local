import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";
import {
  parse,
  lineSegments,
  currentLineIndex,
  MEANING_MAX_CHARS,
  type SegRole,
} from "../lib/glossary";
import { semantic } from "../styles/tokens";
import { useL } from "../lib/i18n";
import styles from "./GlossaryEditor.module.css";

interface GlossaryEditorProps {
  value: string;
  onChange: (v: string) => void;
  height?: number; // px；不传则填满父容器（父需有高度）
  showGutter?: boolean; // 默认 true
  header?: ReactNode; // 编辑器卡内头条（整页传 术语｜含义 + 条数 + 状态点）
  dense?: boolean; // 紧凑模式（折叠条内联）：字号 -1、行号栏更窄
}

export interface GlossaryEditorHandle {
  jumpTo: (charOffset: number) => void; // 供右栏「单条提醒/分类锚点」点击定位
}

// 着色只改颜色、不改字重——不同字重会让叠层与 textarea 字宽错位。
const SEG_COLOR: Record<SegRole, string> = {
  cat: semantic.accent.text,
  term: semantic.text.primary,
  pipe: semantic.accent.brand,
  meaning: semantic.text.secondary,
  "meaning-over": semantic.warning.text,
  plain: semantic.text.muted,
  empty: "transparent",
};

export const GlossaryEditor = forwardRef<GlossaryEditorHandle, GlossaryEditorProps>(function GlossaryEditor(
  { value, onChange, height, showGutter = true, header, dense },
  ref,
) {
  const L = useL();
  const taRef = useRef<HTMLTextAreaElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const [caret, setCaret] = useState(0);
  /** 「跳过去 →」之后短暂高亮目标行：光标那一竖太细，跳完常找不到自己落在哪 */
  const [flashLine, setFlashLine] = useState<number | null>(null);
  const flashTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => { if (flashTimer.current) clearTimeout(flashTimer.current); }, []);

  const { lines, blankEntries } = parse(value);
  const curLine = currentLineIndex(value, caret);

  // 滚动同步：textarea 滚动 → 平移高亮叠层（backdrop overflow:hidden）
  const syncScroll = () => {
    if (innerRef.current && taRef.current) {
      innerRef.current.style.transform = `translateY(${-taRef.current.scrollTop}px)`;
    }
  };

  /** 把光标送到某个字符位置并滚进视野。右栏「跳过去 →」与 ⌥↑↓ 共用。 */
  const jump = (charOffset: number) => {
    const ta = taRef.current;
    if (!ta) return;
    ta.focus();
    ta.setSelectionRange(charOffset, charOffset);
    setCaret(charOffset);
    const line = currentLineIndex(value, charOffset);
    ta.scrollTop = Math.max(0, line * 24 - ta.clientHeight / 2); // 把目标行滚进视图中段
    syncScroll();
    setFlashLine(line);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashLine(null), 1400);
  };

  useImperativeHandle(ref, () => ({ jumpTo: jump }));

  const rootClass = `${styles.editor}${showGutter ? "" : " " + styles.editorNoGutter}${dense ? " " + styles.dense : ""}`;

  return (
    <div className={rootClass} style={height ? { height } : undefined}>
      {header && <div className={styles.editorHead}>{header}</div>}
      <div className={styles.editorArea}>
        <div className={styles.backdrop} aria-hidden="true">
          <div ref={innerRef} className={styles.inner}>
            {lines.map((line, i) => {
              const over = line.kind === "entry" && !!line.over;
              // 只有术语名、释义还空着 —— 待办层要一眼看得见，否则用户根本不知道这行等着他
              const blank = line.kind === "entry" && !line.meaning;
              const cls = [
                styles.row,
                i === curLine ? styles.rowActive : "",
                over ? styles.rowOver : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <div
                  key={i}
                  className={cls}
                  // 底色始终留着（这行还没填），左缘在光标所在行让位给赤陶（你在这儿）——
                  // 两种信息不能互相顶掉：跳过去之后正是最需要看清「我在填哪一行」的时候
                  style={
                    i === flashLine
                      ? { boxShadow: `inset 0 0 0 2px ${semantic.warning.icon}`, transition: "box-shadow 240ms" }
                      : blank && !over ? {
                          background: `color-mix(in srgb, ${semantic.warning.decor} 13%, transparent)`,
                          boxShadow: `inset 3px 0 0 ${i === curLine ? semantic.accent.brand : semantic.warning.icon}`,
                        } : undefined
                  }
                >
                  {showGutter && (
                    <span
                      data-gutter
                      className={styles.gutter}
                      style={over || blank ? { color: semantic.warning.text } : undefined}
                    >
                      {i + 1}
                    </span>
                  )}
                  <span className={styles.content}>
                    {lineSegments(line).map((s, j) => (
                      <span key={j} style={{ color: SEG_COLOR[s.role] }}>
                        {s.text}
                      </span>
                    ))}
                    {/* ghost 提示追加在行尾：它后面没有真实字符，所以不会把 textarea 的
                        逐字对齐推歪。nowrap 防它自己折行（折了才会带偏后续行）。 */}
                    {blank && (
                      <span style={{ color: semantic.text.ghost, fontSize: 12.5, paddingLeft: 6, whiteSpace: "nowrap" }}>
                        {L("等你填一句话", "waiting on your one line")}
                      </span>
                    )}
                  </span>
                  {over && (
                    <span className={styles.overBadge} style={{ color: semantic.warning.text }}>
                      {line.mlen}/{MEANING_MAX_CHARS}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <textarea
          ref={taRef}
          className={styles.textarea}
          value={value}
          spellCheck={false}
          onChange={(e) => {
            onChange(e.target.value);
            setCaret(e.target.selectionStart);
          }}
          onKeyDown={(e) => {
            // ⌥↓ / ⌥↑ 在「等你填」的行之间跳。填完一个直接跳下一个，不用回右栏点。
            if (!e.altKey || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
            if (blankEntries.length === 0) return;
            e.preventDefault();
            const here = e.currentTarget.selectionStart;
            const down = e.key === "ArrowDown";
            const next = down
              ? blankEntries.find((b) => b.charOffset > here) ?? blankEntries[0]
              : [...blankEntries].reverse().find((b) => b.charOffset < here) ?? blankEntries[blankEntries.length - 1];
            jump(next.charOffset);
          }}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onScroll={syncScroll}
        />
      </div>
    </div>
  );
});

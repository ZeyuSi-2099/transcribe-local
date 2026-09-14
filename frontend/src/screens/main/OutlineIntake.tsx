// 大纲进料区（2026-08-19）——占据术语库页的主区，不是右栏里那个小框。
//
// 为什么占主区：右栏宽度是整页栅格写死的 300px，一份三千字的大纲在里面只看得见三四行，
// 「读出来给你看一眼」就等于没做到。主区约 780px 宽、整屏高，一屏能看三四十行。
// 起草一开始就切回编辑器（GlossaryPage 管），术语落在刚才贴大纲的同一块地方。
//
// ⚠️ 文案红线：用户可见的字里不许出现 AI / 大模型 / 智能等（bannedAiPhrases.ts 有守卫）。
import { useRef, useState } from "react";
import * as T from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import {
  MAX_OUTLINE_CHARS, OUTLINE_ACCEPT, OutlineError, firstFileOf, readOutlineFile,
} from "../../lib/outlineFile";

interface Props {
  value: string;
  onChange: (v: string) => void;
  /** 回到编辑器（主区被借走期间，原内容一个字没动） */
  onBack: () => void;
  onToast: (msg: string) => void;
}

export function OutlineIntake({ value, onChange, onBack, onToast }: Props) {
  const L = useL();
  const [fileName, setFileName] = useState<string | null>(null);
  const [hot, setHot] = useState(false);        // 拖到区域上方
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const errText = (e: unknown): string => {
    const code = e instanceof OutlineError ? e.code : "read";
    if (code === "ext") return L("只认 .txt、.md 和 .docx 这三种文件", "Only .txt, .md and .docx files work here");
    if (code === "size") return L("这个文件超过 2MB，多半是传错了", "That file is over 2MB — it is probably not an outline");
    if (code === "empty") return L("这个文件里没有可读的文字", "There is no readable text in that file");
    return L("这个文件读不出来，换一份试试", "That file could not be read — try another one");
  };

  const take = async (file: File | null) => {
    if (!file || busy) return;
    setBusy(true);
    try {
      const r = await readOutlineFile(file);
      onChange(r.text);
      setFileName(file.name);
      if (r.truncated) {
        // 必须 L.t 不能写模板串：模板串的英文原句每次都不同，对照本永远索引不到，
        // 那一句就会在六门语言里静默回落英文（CLAUDE.md 设计纪律第 6 条）。
        onToast(L.t("大纲很长，只取了前 {0} 字",
                    "That outline is long — only the first {0} characters were taken", MAX_OUTLINE_CHARS));
      }
    } catch (e) {
      onToast(errText(e));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";   // 同一个文件能再选一次
    }
  };

  const chars = [...value].length;
  const over = chars > MAX_OUTLINE_CHARS;

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", gap: T.space.s3, minWidth: 0 }}>
      {/* 顶行：回编辑器 + 字数。主区是借来的，回去的路必须一直看得见 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <button
          className="tx-focus"
          onClick={onBack}
          style={{
            ...T.type.bodySm, color: T.semantic.accent.text, background: "none",
            border: "none", cursor: "pointer", padding: "2px 4px",
          }}
        >
          ← {L("返回编辑器", "Back to the editor")}
        </button>
        <span style={{ fontFamily: T.fonts.mono, fontSize: 12, color: over ? T.semantic.danger.text : T.semantic.text.muted }}>
          {chars.toLocaleString()} / {MAX_OUTLINE_CHARS.toLocaleString()}
        </span>
      </div>

      {/* 拖放区：点一下也能选文件 */}
      <div
        onDragOver={(e) => { e.preventDefault(); setHot(true); }}
        onDragLeave={() => setHot(false)}
        onDrop={(e) => { e.preventDefault(); setHot(false); void take(firstFileOf(e.dataTransfer)); }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        className="tx-focus"
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputRef.current?.click(); } }}
        style={{
          border: `1px dashed ${hot ? T.semantic.accent.brand : T.semantic.border.strong}`,
          background: hot ? T.semantic.accent.bgTint : T.semantic.surface.float,
          borderRadius: T.radius.sm, padding: `${T.space.s4}px ${T.space.s3}px`,
          textAlign: "center", cursor: "pointer", transition: `border-color ${T.motion.fast}, background ${T.motion.fast}`,
        }}
      >
        <div style={{ ...T.type.bodySm, color: T.semantic.text.primary, fontWeight: 500 }}>
          {busy ? L("正在读……", "Reading…") : L("拖文件到这里，或点击选择", "Drop a file here, or click to choose")}
        </div>
        <div style={{ ...T.type.caption, color: T.semantic.text.secondary, marginTop: 4 }}>
          {L("支持 .txt · .md · .docx", "Works with .txt · .md · .docx")}
        </div>
        {/* ⚠️ 文件框用**视觉隐藏**而不是 `display:none`（2026-09-01）：`display:none` 的元素在
            部分浏览器上是「不存在」的，程序化 `.click()` 会被忽略**而且不报错**——症状正是
            「按钮点不动、拖拽却好使」。上传页（`Idle.tsx`）当天按这个理由修过，这一处漏了，
            而它是术语库里唯一的大纲入口。
            ⚠️ 不加 `pointer-events: none`：它对程序化 `.click()` 毫无作用，却会让
            testing-library 的 upload() 拒绝交互（「元素不可指向」），把一整组测试打红。 */}
        <input
          ref={inputRef}
          type="file"
          accept={OUTLINE_ACCEPT}
          onChange={(e) => void take(e.target.files?.[0] ?? null)}
          style={{ position: "absolute", width: 1, height: 1, opacity: 0, overflow: "hidden", clip: "rect(0 0 0 0)", clipPath: "inset(50%)", whiteSpace: "nowrap", border: 0, padding: 0, margin: -1 }}
          aria-hidden
          tabIndex={-1}
        />
      </div>

      {/* 已读入哪份文件。它只是来源提示——正文改过之后也留着，不然就不知道这堆字哪来的 */}
      {fileName && (
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: T.space.s2,
          background: T.semantic.surface.sunken, borderRadius: T.radius.sm,
          padding: `${T.space.s2}px ${T.space.s3}px`, ...T.type.bodySm, color: T.semantic.text.secondary,
        }}>
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {L("已读入", "Loaded")} <b style={{ color: T.semantic.text.primary, fontWeight: 500 }}>{fileName}</b>
          </span>
          <button
            className="tx-focus"
            onClick={() => { setFileName(null); onChange(""); }}
            aria-label={L("清空大纲", "Clear the outline")}
            style={{ background: "none", border: "none", cursor: "pointer", color: T.semantic.text.muted, padding: "0 2px", flex: "0 0 auto" }}
          >
            ✕
          </button>
        </div>
      )}

      {/* 正文：读出来的东西在这里，可以直接改 */}
      <textarea
        // 进料区一打开光标就落在这里：空态屏那条「帮我起草」的出口点完必须能直接开打，
        // 否则用户面对的是「切过来了然后呢」（GlossaryPage.test 守着这个）。
        autoFocus
        className="tx-scroll"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={L("也可以直接把大纲粘贴在这里……", "Or just paste the outline here…")}
        aria-label={L("访谈大纲", "Interview outline")}
        style={{
          flex: 1, minHeight: 0, width: "100%", boxSizing: "border-box",
          border: `1px solid ${over ? T.semantic.danger.text : T.semantic.border.default}`,
          borderRadius: T.radius.sm, background: T.semantic.surface.float,
          padding: T.space.s4, fontFamily: T.fonts.sans, fontSize: 14, lineHeight: 1.75,
          color: T.semantic.text.primary, resize: "none",
        }}
      />
    </div>
  );
}

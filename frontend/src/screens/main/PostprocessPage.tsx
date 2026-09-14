// PostprocessPage.tsx — 后处理配置页（脱敏保留词清单）
// 对照稿：docs/design/postprocessing/README.md §2 + 后处理 细化稿.dc.html B 区
// 骨架复用 GlossaryPage：pad 28/34/30 · 三栏 grid 220/1fr/300 gap22 · 页锁高内滚。
// 数据自管（挂载拉取）——配置只在发起加工时被选用，别处不依赖。
//
// ⚠️ 2026-08-17 下架「归类」后本页只剩一个主体。**整页与侧边栏入口都不能删**：
// 脱敏清单仍要有地方维护。删掉的是左栏的方案组、右栏的结构镜像与错误卡、以及空态的方案 CTA。

import { useErrText } from "../../lib/userErrors";
import { useUnsavedGuard } from "../../lib/unsavedGuard";
import { useEffect, useRef, useState } from "react";
import { Button } from "../../components/Button";
import * as T from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import {
  listRedactLists, createRedactList, updateRedactList, deleteRedactList,
  type RedactList,
} from "../../lib/api";
import { countRedactLines, PP_LIST_MAX_CHARS } from "../../lib/redactList";
import { RedactScopeHelp } from "./RedactScope";

const MAX_LISTS = 20;
const MAX_NAME = 40;

export function PostprocessPage() {
  const L = useL();
  const errText = useErrText();
  const [lists, setLists] = useState<RedactList[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [sel, setSel] = useState<string | null>(null);

  const reload = async () => setLists(await listRedactLists());
  useEffect(() => {
    let alive = true;
    listRedactLists()
      .then((ls) => { if (alive) setLists(ls); })
      .catch(() => {})
      .finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, []);

  // 选中收敛：无选中或选中已不在列表 → 第一个清单 → null
  useEffect(() => {
    setSel((cur) => (cur && lists.some((l) => l.id === cur) ? cur : lists[0]?.id ?? null));
  }, [lists]);

  const selList = sel ? lists.find((l) => l.id === sel) ?? null : null;
  const srvName = selList?.name ?? "";
  const srvText = selList?.content ?? "";

  // 编辑缓冲 + 已存快照（同 GlossaryPage）：选中变化 / 服务端回写 → 重置；保存成功先本地对齐（即时反馈）
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [savedName, setSavedName] = useState("");
  const [savedText, setSavedText] = useState("");
  useEffect(() => {
    setName(srvName); setText(srvText);
    setSavedName(srvName); setSavedText(srvText);
  }, [sel, srvName, srvText]);

  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  useEffect(() => { setConfirmDel(false); setSaveErr(null); }, [sel]);
  const nameRef = useRef<HTMLInputElement>(null);

  const listCount = countRedactLines(text);
  const overTotal = text.length > PP_LIST_MAX_CHARS;
  const nameOk = name.trim().length > 0 && name.trim().length <= MAX_NAME;
  const dirty = name !== savedName || text !== savedText;
  const canSave = dirty && nameOk && !overTotal && !saving && sel != null;

  const save = async (): Promise<boolean> => {
    if (!dirty) return true;
    if (!canSave || !sel) return false;
    setSaving(true); setSaveErr(null);
    try {
      await updateRedactList(sel, name.trim(), text);
      setSavedName(name.trim()); setSavedText(text);   // 即时反馈；reload 后 effect 再对齐
      await reload();
      return true;
    } catch (e) {
      setSaveErr(errText(e));
      return false;
    } finally {
      setSaving(false);
    }
  };
  // 切页 / 关窗口前拦一下（09-03 实测：填了 14 个词没点保存就切走，回来是空的）
  useUnsavedGuard(dirty, save);

  const handleNew = async () => {
    const base = L("未命名", "Untitled");
    const taken = new Set(lists.map((x) => x.name));
    let candidate = base;
    for (let i = 2; taken.has(candidate); i++) candidate = `${base} ${i}`;
    try {
      const { id } = await createRedactList(candidate, "");
      await reload();
      setSel(id);
      setTimeout(() => { nameRef.current?.focus(); nameRef.current?.select(); }, 0);
    } catch (e) {
      setSaveErr(errText(e));
    }
  };

  const handleDelete = async () => {
    if (!confirmDel) { setConfirmDel(true); return; }
    if (sel) {
      try {
        await deleteRedactList(sel);
        await reload();
      } catch (e) {
        setSaveErr(errText(e));
      }
    }
    setConfirmDel(false);
  };

  const cardStyle = {
    background: T.semantic.surface.raised,
    border: `1px solid ${T.semantic.border.default}`,
    borderRadius: T.radius.md,
    padding: `${T.space.s3 + 2}px ${T.space.s4}px`,
  } as const;

  const chip = (name: string, desc: string, accent?: boolean) => (
    <span key={name} style={{ display: "inline-flex", alignItems: "center", gap: 7, border: `1px solid ${accent ? T.semantic.accent.brand : T.semantic.border.default}`, background: accent ? T.semantic.accent.bgTint : T.semantic.surface.raised, borderRadius: T.radius.pill, padding: "5px 14px", fontSize: 12, color: T.semantic.text.secondary, whiteSpace: "nowrap" }}>
      <b style={{ color: accent ? T.semantic.accent.text : T.semantic.text.primary }}>{name}</b> {desc}
    </span>
  );

  // 能力条不许挤垮左边的标题与说明：德语等长语言下 chip 行宽近乎翻倍，
  // 原来的「chip 行 0 0 auto + 文案列 flex:1 minWidth:0」会把文案压成一列一个词、
  // chip 自己还被 overflow 切掉（2026-08-02 德语走查实见）。改成整行可换行 + 文案列给下限。
  const header = (
    <div style={{ flex: "0 0 auto", display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: T.space.s6, marginBottom: T.space.s6 }}>
      <div style={{ flex: "1 1 360px", minWidth: 260 }}>
        <h1 style={{ ...T.type.h1, color: T.semantic.text.primary, margin: 0 }}>{L("脱敏规则", "Redaction rules")}</h1>
        <div style={{ ...T.type.body, color: T.semantic.text.secondary, marginTop: T.space.s2 }}>
          {L("复核确认后的笔录，可以继续加工——在这里备好保留词清单，发起时（转录详情页）直接选用。", "Reviewed transcripts can go further — set up keep lists here, then pick one when you start a run from a transcript.")}
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: T.space.s2, flex: "0 1 auto", maxWidth: "100%", paddingTop: 6 }}>
        {chip(L("视角转换", "Narrative"), L("问答变叙述 · 零配置", "Q&A to narrative · no setup"))}
        {chip(L("脱敏", "Redact"), L("保留清单绝不动 · 清单可选", "Keep-list stays intact · optional"), true)}
      </div>
    </div>
  );

  // ── 空态：一个清单都没有 ──
  if (loaded && lists.length === 0) {
    return (
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: T.layout.pagePad,
      // 横向 auto 不能写成 hidden：三栏里那 520px 是固定的（220 左栏 + 300 右栏），
      // 窗口比 824px 窄时内容真的放不下——hidden 会把右栏整块裁掉，既看不见也滑不出来，
      // 而外层 AppShell 的横滑够不到这一层（2026-08-19 在 900px 下实测到）。
      overflowX: "auto", overflowY: "hidden" }}>
        {header}
        <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ border: `1px dashed ${T.semantic.border.strong}`, borderRadius: T.radius.md, padding: "36px 24px", textAlign: "center", maxWidth: 480, width: "100%" }}>
            <div style={{ color: T.semantic.accent.brand, fontSize: 26 }}>✦</div>
            <div style={{ ...T.type.h2, marginTop: 10 }}>{L("笔录还能继续加工", "Your transcript can go further")}</div>
            <div style={{ ...T.type.bodySm, color: T.semantic.text.secondary, lineHeight: 1.7, marginTop: T.space.s2, maxWidth: 360, marginLeft: "auto", marginRight: "auto" }}>
              {L("视角转换、脱敏——复核确认后的转录稿，在详情页一键发起。想让某些词绝不被改动，就在这里建一个保留词清单。", "Narrative and redaction both start from a reviewed transcript's page. Want certain words left untouched? Build a keep list here.")}
            </div>
            <div style={{ marginTop: T.space.s4, display: "inline-block" }}>
              <Button primary onClick={() => { void handleNew(); }}>{L("新建第一个保留词清单", "Create your first keep list")}</Button>
            </div>
            <div style={{ ...T.type.caption, marginTop: 10 }}>
              {L("不建清单也能跑：脱敏会智能识别敏感信息 · 只用视角转换则无需任何配置", "No list needed — redaction detects sensitive details on its own · narrative alone needs no setup at all")}
            </div>
            {saveErr && <div style={{ ...T.type.caption, color: T.semantic.accent.text, marginTop: T.space.s2 }}>{saveErr}</div>}
          </div>
        </div>
      </div>
    );
  }

  const saveLabel = overTotal ? L("超出字数上限", "Over the limit")
    : !nameOk ? L("先填名称", "Name it first")
    : dirty ? L("保存清单", "Save list")
    : L("已保存", "Saved");

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: T.layout.pagePad,
      // 横向 auto 不能写成 hidden：三栏里那 520px 是固定的（220 左栏 + 300 右栏），
      // 窗口比 824px 窄时内容真的放不下——hidden 会把右栏整块裁掉，既看不见也滑不出来，
      // 而外层 AppShell 的横滑够不到这一层（2026-08-19 在 900px 下实测到）。
      overflowX: "auto", overflowY: "hidden" }}>
      {header}

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "220px 1fr 300px", gap: T.space.s6, minWidth: 824 }}>
        {/* 左：清单列表 */}
        <div style={{ minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", gap: T.space.s3 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", minHeight: T.control.fieldH }}>
            <span style={{ ...T.type.label, color: T.semantic.text.muted }}>
              {L("脱敏保留词清单", "Keep lists")} <span style={{ fontFamily: T.fonts.mono, color: T.semantic.text.muted }}>{lists.length}/{MAX_LISTS}</span>
            </span>
            <button
              onClick={() => { void handleNew(); }}
              disabled={lists.length >= MAX_LISTS}
              style={{ ...T.type.bodySm, color: lists.length >= MAX_LISTS ? T.semantic.text.muted : T.semantic.accent.text, background: "none", border: "none", cursor: lists.length >= MAX_LISTS ? "not-allowed" : "pointer", padding: "2px 4px" }}
            >
              + {L("新建", "New")}
            </button>
          </div>
          <div className="tx-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4, paddingRight: T.space.s1 }}>
            {lists.map((l) => {
              const active = sel === l.id;
              return (
                <button
                  key={l.id}
                  onClick={() => setSel(l.id)}
                  style={{
                    textAlign: "left", border: "none", cursor: "pointer",
                    borderLeft: `3px solid ${active ? T.semantic.accent.brand : "transparent"}`,
                    background: active ? T.semantic.accent.bgTint : "transparent",
                    borderRadius: T.radius.sm, padding: `${T.space.s2}px ${T.space.s3}px`,
                    display: "flex", flexDirection: "column", gap: 2, minWidth: 0,
                  }}
                >
                  <span style={{ ...T.type.bodySm, color: active ? T.semantic.text.primary : T.semantic.text.secondary, fontWeight: active ? 600 : 400, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{l.name}</span>
                  <span style={{ fontFamily: T.fonts.mono, fontSize: 11, color: T.semantic.text.muted }}>{L.t("{0} 条", "{0} entries", countRedactLines(l.content))}</span>
                </button>
              );
            })}
          </div>
          <div style={{ flex: 1 }} />
          <div style={{ ...T.type.caption, lineHeight: 1.65, padding: "0 2px" }}>
            {L("清单在发起加工时选用——和术语库一个道理。", "Pick a list when you start a run — same idea as glossaries.")}
          </div>
        </div>

        {/* 中：名称 + 编辑器 */}
        <div style={{ minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", gap: T.space.s3 }}>
          <input
            ref={nameRef}
            aria-label={L("清单名", "List name")}
            value={name}
            maxLength={MAX_NAME}
            onChange={(e) => setName(e.target.value)}
            placeholder={L("清单名", "List name")}
            style={{
              ...T.type.body, fontWeight: 600, color: T.semantic.text.primary, height: T.control.fieldH, boxSizing: "border-box",
              background: T.semantic.surface.raised, border: `1px solid ${nameOk ? T.semantic.border.default : T.semantic.accent.bgSoft}`,
              borderRadius: T.radius.sm, padding: `0 ${T.space.s3}px`,
            }}
          />
          <div className="tx-field" style={{ flex: 1, minHeight: 0, background: T.semantic.surface.raised, border: `1px solid ${T.semantic.border.default}`, borderRadius: T.radius.md, display: "flex", flexDirection: "column", overflow: "hidden", boxShadow: T.shadow.sm }}>
            <div style={{ flex: "0 0 auto", display: "flex", justifyContent: "space-between", alignItems: "center", padding: `10px ${T.space.s4}px`, borderBottom: `1px solid ${T.semantic.border.subtle}` }}>
              <span style={{ ...T.type.label, color: T.semantic.text.muted }}>{L("一行一个保留词", "One keep-word per line")}</span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: T.space.s3 }}>
                <span style={{ fontFamily: T.fonts.mono, fontSize: 12, fontVariantNumeric: "tabular-nums", color: T.semantic.text.muted }}>
                  {listCount} {L("条", "entries")}
                </span>
                {dirty ? (
                  <span style={{ fontSize: 12, color: T.semantic.accent.text }}>● {L("未保存", "Unsaved")}</span>
                ) : (
                  <span style={{ fontSize: 12, color: T.semantic.success.text }}>✓ {L("已保存", "Saved")}</span>
                )}
              </span>
            </div>
            <textarea
              aria-label={L("清单内容", "List content")}
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              placeholder={L("每行一个保留词（人名、公司名等，脱敏时绝不改动）", "One keep-word per line — never touched when redacting")}
              style={{ flex: 1, minHeight: 0, border: "none", outline: "none", resize: "none", background: "transparent", padding: `${T.space.s3 + 2}px ${T.space.s4}px`, fontFamily: T.fonts.mono, fontSize: 13, lineHeight: 2, color: T.semantic.text.primary }}
            />
          </div>
        </div>

        {/* 右：格式 / 保存 / 删除 */}
        <div className="tx-scroll" style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: T.space.s4, overflowY: "auto", paddingRight: T.space.s2 }}>
          <div style={{ ...cardStyle, background: T.semantic.surface.page }}>
            <div style={{ ...T.type.label, color: T.semantic.text.muted, marginBottom: T.space.s2 }}>{L("格式", "Format")}</div>
            <div style={{ ...T.type.bodySm, color: T.semantic.text.secondary, lineHeight: 1.7 }}>
              {L("· 一行一个保留词", "· one keep-word per line")}
              <br />
              {L("· 清单里的词脱敏时绝不改动", "· listed words are never touched when redacting")}
              <br />
              {L("· 不选清单也能跑：智能识别敏感信息", "· no list needed — smart detection works too")}
            </div>
          </div>

          <div style={{ ...cardStyle, background: T.semantic.surface.page }}>
            <RedactScopeHelp />
          </div>

          <div style={{ flex: 1 }} />

          {saveErr && (
            <div style={{ ...T.type.caption, color: T.semantic.accent.text, textAlign: "center" }}>{saveErr}</div>
          )}

          <Button primary full disabled={!canSave} onClick={save}>{saveLabel}</Button>

          {/* 两步删除（同 GlossaryPage）：先 删除 → 确认删除 / 取消 */}
          {confirmDel ? (
            <div style={{ display: "flex", gap: T.space.s2 }}>
              <button
                onClick={handleDelete}
                style={{
                  flex: 1, ...T.type.body, fontWeight: 600, color: T.semantic.text.onAccent,
                  background: T.semantic.danger.fill, border: "none", borderRadius: T.radius.md,
                  padding: `${T.space.s3}px`, cursor: "pointer",
                }}
              >
                {L("确认删除", "Confirm delete")}
              </button>
              <Button secondary onClick={() => setConfirmDel(false)}>{L("取消", "Cancel")}</Button>
            </div>
          ) : (
            <Button secondary full onClick={handleDelete}>{L("删除", "Delete")}</Button>
          )}
        </div>
      </div>
    </div>
  );
}

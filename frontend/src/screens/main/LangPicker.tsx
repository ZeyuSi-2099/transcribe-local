import { useState } from "react";
import { semantic, fonts, labelStyle, radius, motion, shadow } from "../../styles/tokens";
import { useL } from "../../lib/i18n";
import { COMMON_LANGS, MORE_LANGS, langName } from "../../lib/langs";

interface LangPickerProps {
  value: string;
  onChange: (id: string) => void;
}

export function LangPicker({ value, onChange }: LangPickerProps) {
  const L = useL();
  const [open, setOpen] = useState(false);

  const isMore = MORE_LANGS.some((l) => l.id === value);

  // 等宽方块（chips）：grid 3 列等分；选中=墨黑底白字。不加 ✓ 勾——否则选中项变宽撑大列、其余被挤窄。
  //
  // ⚠️ **列数是量出来的，不是拍的**（2026-08-21 复量，八门逐一实测）。栅格净宽 410px，
  // 三列每列 131px、扣掉左右内距与描边后可用 **101px**。八门里最宽的那一格：
  //   ja インドネシア語 91（余 10，最紧） · de Portugiesisch 85 · it indonesiano 76 ·
  //   en Portuguese 72 · fr indonésien 68 · es/pt portugués 64 · zh 西班牙语 52。
  // **四列装不下**：每列只剩 96px，德语这一格就得 85+28+2=115px。
  // 四列时的症状分两种、看着像两个 bug 其实是同一个：拉丁文断不了词 → 那一列被撑宽、其余被挤窄；
  // 日文可任意断行 → 就地换成两行。想改回四列得先砍语种或加宽整张卡，别只改这个数字。
  //
  // ⚠️ 露出 11 门后**最长的一格换人了**：新进来的印尼语在 fr/it/pt/ja 四门都是最宽的那个。
  // 再加语种前先按这四门量一遍，尤其日文——只剩 10px。量的时候一次只渲染一门语言
  // （UILangProvider 会写 <html lang>，八门同屏时 :lang() 的字体栈有七门是错的）。
  const chipStyle = (selected: boolean): React.CSSProperties => ({
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    width: "100%",
    boxSizing: "border-box",
    padding: "8px 14px",
    cursor: "pointer",
    fontSize: 13,
    fontFamily: fonts.sans,
    // ⚠️ 选中态 2026-08-31 由「纯墨底反白」改成与充值档位同一套（1.5px 赤陶描边 + 极浅赤陶底）。
    // 应用里「我选了这个」只该有一种长相：充值浮窗那三个档位是描边式，这里却是实心墨块，
    // 同一个语义两种画法。墨黑实心在这套暖米色界面里也是唯一一处纯黑填充，很扎眼。
    // 两态描边同宽（都 1.5px）：宽度不同会让选中那一格比别的矮 1px、整排歪掉。
    // ⚠️ **字重两态也必须一样**：选中时加粗会把那一格的文字撑宽，而这三列的余量是量出来的
    // ——日文「インドネシア語」91px、可用 101px，只剩 10px，加粗就顶出去了（见下方列宽注释）。
    border: selected ? `1.5px solid ${semantic.accent.brand}` : `1.5px solid ${semantic.border.default}`,
    borderRadius: radius.sm,
    background: selected ? semantic.accent.bgTint : "transparent",
    color: selected ? semantic.text.primary : semantic.text.secondary,
    fontWeight: 500,
    // 令牌表禁 transition:all —— 只动实际会变的三个属性
    transition: `border-color ${motion.fast}, background ${motion.fast}, color ${motion.fast}`,
    userSelect: "none",
    overflowWrap: "anywhere",
  });
  return (
    <div>
      {/* 说明与标签同一行：格子从 3 行变 4 行是 +47px，而卡片底部只有 28px 余量；
          把这句从格子下面挪上来省掉整整一行（−22px），净增 25px 才放得下。
          文案同时缩短——「选择音频的语言」这半句被紧挨着的标签说完了，留着是重复。
          德语更划算：那句话原本就占两行，并进来后净增只有 6px。 */}
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
        <span style={labelStyle}>{L("音频语言", "Audio language")}</span>
        {/* 字号跟标签取同一个来源：写死 12 的话同一行里它比标签（11）还大，看着像标题 */}
        <span style={{ fontSize: labelStyle.fontSize, color: semantic.text.muted }}>
          {L("我们不自动识别", "we don't auto‑detect")}
        </span>
      </div>
      {/* minmax(0,1fr) 而不是裸 1fr：裸 1fr 等于 minmax(auto,1fr)，「auto」意味着列永远不会窄过
          内容——所以「等分」这句话在译文变长时根本不成立，而且不报错、只是悄悄歪掉。
          配合 chip 上的 overflowWrap：万一将来哪门译文更长，代价是那一行变高，不是整排歪掉。 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
        {/* Common langs */}
        {COMMON_LANGS.map((seg) => (
          // ⚠️ <button> 不是 <span>（2026-08-31）：原来这 10 个语种格是 span+onClick，
          // **Tab 走不到、回车按不动、也没有焦点环**——一个必填项完全不能用键盘操作。
          // 换成 button 之后这些全部由浏览器免费给，包括全局的 :focus-visible 焦点环。
          <button
            key={seg.id}
            type="button"
            aria-pressed={value === seg.id}
            onClick={() => onChange(seg.id)}
            style={chipStyle(value === seg.id)}
          >
            {langName(seg.id, L)}
          </button>
        ))}
        {/* More trigger with dropdown：文字居中（与其他语言对齐），▾ 绝对定位右侧固定 */}
        <div style={{ position: "relative" }}>
          <button type="button" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)} style={{ ...chipStyle(isMore), position: "relative" }}>
            {isMore ? langName(value, L) : L("更多", "More")}
            <span aria-hidden style={{ position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)", fontSize: 12 }}>▾</span>
          </button>
          {open && (
            <>
              <div
                style={{ position: "fixed", inset: 0, zIndex: 40 }}
                onClick={() => setOpen(false)}
              />
              <div
                className="tx-scroll"
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  left: 0,
                  // 浮层面 + 强描边 + shadow.lg：与导出菜单、地球语言菜单同一套（此前是卡片面 + 一个野生阴影值）
                  background: semantic.surface.float,
                  border: `1px solid ${semantic.border.strong}`,
                  boxShadow: shadow.lg,
                  borderRadius: radius.sm,
                  minWidth: 200,
                  maxHeight: 300,
                  overflowY: "auto",
                  zIndex: 50,
                  padding: "6px 0",
                }}
              >
                {/* 27 门时代：下拉全部可选；点选 = onChange + 收起。
                    ⚠️ 这里**不用 role="option"**（2026-09-01 修）：option 必须活在 listbox 里，
                    而这个容器是个裸 div——孤零零的 option 在 ARIA 上是无效组合，读屏念不出
                    「第 N 项／共 27 项」，等于加了个不起作用的角色。一列真 <button> 本来就能用，
                    选中与否用 aria-pressed 说（与上面十格同一套说法）。要做成真 listbox 的话，
                    得连键盘上下键与 aria-activedescendant 一起做，那是另一件事。 */}
                {MORE_LANGS.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    aria-pressed={value === l.id}
                    onClick={() => { onChange(l.id); setOpen(false); }}
                    style={{
                      padding: "9px 14px",
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      cursor: "pointer",
                      width: "100%",
                      textAlign: "left",
                      border: "none",
                      background: value === l.id ? semantic.surface.sunken : "transparent",
                    }}
                  >
                    <span style={{ fontSize: 13, color: value === l.id ? semantic.text.primary : semantic.text.secondary, fontFamily: fonts.sans, fontWeight: value === l.id ? 600 : 400 }}>
                      {langName(l.id, L)}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

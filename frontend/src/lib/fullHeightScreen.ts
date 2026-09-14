// 「这一屏是满屏应用，不是可滚文档」的开关（2026-08-06，审计 C9）。
//
// 公开站改回浏览器原生的文档滚动之后，登录后的应用仍然要保持「一屏钉死、
// 各分栏各自滚」——侧栏、编辑器、右栏是三个独立滚动区，整页跟着滚会乱套。
// 两套模式靠 <html> 上的一个类切换（样式在 global.css 的 html.tx-app 块）。
//
// 用 hook 而不是在组件里手写 classList：卸载必须还原，忘了还原的话
// 从应用退回宣传页会得到一个「钉死一屏、滚不动」的营销页。
import { useEffect } from "react";

export function useFullHeightScreen(): void {
  useEffect(() => {
    const el = document.documentElement;
    el.classList.add("tx-app");
    return () => el.classList.remove("tx-app");
  }, []);
}

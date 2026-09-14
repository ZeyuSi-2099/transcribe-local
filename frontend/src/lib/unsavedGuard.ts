/** 「还有没保存的改动」拦截（2026-09-03）。
 *
 * 脱敏清单页与术语库页都是**手动保存**的（右上角「保存清单 / 保存术语库」），页面上只有一行
 * 小字「● 未保存」。09-03 全流程实测：填了 14 个保留词、没点保存就切去别的页面，回来清单是空的
 * ——一行小字拦不住一次误点。这里把「有没保存的改动」登记到一个全站唯一的位子，AppShell 切页
 * 之前先问一句；关窗口 / 刷新走浏览器自己的 beforeunload。
 *
 * 只登记「当前有没有、怎么存」，不登记内容——内容在页面自己的 state 里，存也由页面自己存。
 * 一次只可能有一个页面在编辑（应用一次只渲染一个页面），所以是单例不是列表。 */
import { useEffect } from "react";

export type UnsavedGuard = { save: () => Promise<boolean> };

let current: UnsavedGuard | null = null;

export function pendingUnsaved(): UnsavedGuard | null {
  return current;
}

/** 测试与页面卸载用：清掉登记。 */
export function clearUnsaved(): void {
  current = null;
}

/** 页面里调：dirty 为真时登记「怎么存」，为假时撤销。save 返回是否真的存成了。 */
export function useUnsavedGuard(dirty: boolean, save: () => Promise<boolean>): void {
  useEffect(() => {
    if (!dirty) {
      current = null;
      return;
    }
    current = { save };
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";   // 浏览器只认这个信号，文案由浏览器自己出
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      current = null;
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [dirty, save]);
}

// 返回上一页时把滚动位置还回去（2026-08-06，审计 C9 的收尾）。
//
// 交回文档滚动之后，浏览器本来就会做这件事——但对客户端渲染的站不管用：
// 浏览器在 HTML 到手那一刻就恢复，那时 React 还没画出内容、文档只有一屏高，
// 于是恢复值被夹成 0。本地开发看不出来（快到内容早就在了），**生产上必现**。
//
// 所以自己接管：离开时记一笔，回来时等内容长够了再滚。
// 只在浏览器判定为「前进/后退」时才恢复——点链接进来的新页面必须落在顶部。
const KEY = "tx_scroll";

type Store = Record<string, number>;

const read = (): Store => {
  try { return JSON.parse(sessionStorage.getItem(KEY) || "{}"); } catch { return {}; }
};

const save = () => {
  try {
    const s = read();
    s[location.pathname + location.search] = Math.round(window.scrollY);
    // 只留最近 30 条，免得会话里逛久了把 sessionStorage 塞满
    const keys = Object.keys(s);
    if (keys.length > 30) delete s[keys[0]];
    sessionStorage.setItem(KEY, JSON.stringify(s));
  } catch { /* 隐私模式下 sessionStorage 可能不可写，放弃恢复即可 */ }
};

const isBackForward = (): boolean => {
  try {
    const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    return nav?.type === "back_forward";
  } catch { return false; }
};

/** 公开页挂一次即可（应用是满屏不滚的，不需要）。 */
export function installScrollRestore(): void {
  if (typeof window === "undefined") return;
  // pagehide 比 beforeunload 可靠：iOS Safari 上后者常不触发
  window.addEventListener("pagehide", save);
  window.addEventListener("beforeunload", save);

  if (!isBackForward()) return;
  const want = read()[location.pathname + location.search];
  if (!want) return;

  // 等内容长到够高再滚。**必须设上限**——页面可能永远长不到那么高
  // （内容改了、或那一版更短），没有上限就是一个每帧空转的死循环。
  let tries = 0;
  const tick = () => {
    if (document.documentElement.scrollHeight - window.innerHeight >= want) {
      window.scrollTo({ top: want, behavior: "auto" });
      return;
    }
    if (++tries > 60) {                       // 约 1 秒；还长不够就滚到能滚的最远处
      window.scrollTo({ top: want, behavior: "auto" });
      return;
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

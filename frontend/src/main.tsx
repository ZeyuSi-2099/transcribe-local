import { StrictMode } from "react";
import { createRoot, hydrateRoot } from "react-dom/client";
import * as Sentry from "@sentry/react";
import App from "./App";
import "./styles/global.css";
import { installScrollRestore } from "./lib/scrollRestore";
import { applyFirstVisitLangRedirect } from "./lib/i18n";
import { getToken } from "./lib/auth";

// 错误监控：仅当配了 VITE_SENTRY_DSN（构建期注入）才启用，本地/未配时静默不动
const dsn = import.meta.env.VITE_SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    environment: import.meta.env.MODE,
    // 只做错误监控，不开性能 tracing（v10 的 tracesSampleRate 还需 browserTracingIntegration，
    // 否则是空配置；错误监控才是目的）
    sendDefaultPii: false, // 隐私：不自动带 IP/cookie（访谈含 PII）
    // 只收本站页面的错误。此前没有这道闸，于是把我们的代码放进别的宿主里跑时
    // （做设计稿用的预览环境，origin 是 *.claudeusercontent.com），那边的报错
    // 也全进了生产项目——2026-08-17 清点，11 个未解决 issue 里 3 个是这么来的，
    // 其中一条还是「replaceState 到 transcribe.solutions 被跨源拒绝」这种只可能
    // 在别人家发生的错。假错误混进来，真错误就淹了。
    // ⚠️ 用 hostname 判、不用 allowUrls：allowUrls 匹配的是堆栈里的脚本 URL，
    // 而这几条的 culprit 只有相对路径（`assets/index-xxx`），匹配不到会被整条丢弃
    // ——那样连生产的真错误都可能误伤。
    beforeSend: (event) =>
      /(^|\.)transcribe\.solutions$/.test(location.hostname) ? event : null,
  });
}

// 返回上一页恢复滚动位置。浏览器自带的那份对客户端渲染的站不管用——它在内容还没画出来时
// 就恢复，位置被夹成 0（本地看不出、生产必现）。
installScrollRestore();

// 生产构建里每个公开网址的正文是构建期印好的（scripts/prerender-meta.ts），
// 这种情况要**接管**已有 DOM 而不是重画：createRoot 会先把容器清空，
// 于是用户看到「有内容 → 空白 → 有内容」，比现在的「空白 → 有内容」还差。
// dev server 与没预印到的路径（/pay）拿到的是空容器，仍走 createRoot——本地行为一字不变。
const rootEl = document.getElementById("root")!;
// 首访语言重定向会当场把 `/` 换成 `/de`，页面语言随之变——预印的那份正文就不是这一门了。
// 所以挂载前先问一次：跳了就丢掉预印内容走客户端渲染（＝今天的行为）。
// 它自带「只跳一次」的闸，App() 里原来那次调用照旧、不会跳第二遍；
// 判断权只有这一处，不在这里另写一份「会不会跳」的逻辑（写两份必然分家）。
if (rootEl.firstChild && applyFirstVisitLangRedirect(!!getToken())) rootEl.textContent = "";
const tree = (
  <StrictMode>
    <App />
  </StrictMode>
);
if (rootEl.firstChild) hydrateRoot(rootEl, tree);
else createRoot(rootEl).render(tree);

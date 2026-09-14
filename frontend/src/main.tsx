import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/global.css";

// 本机版（与线上不同）：不接错误上报——线上的 Sentry 会把报错连同页面地址发到云端；
// 也没有构建期预印的公开页要接管，所以直接 createRoot。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

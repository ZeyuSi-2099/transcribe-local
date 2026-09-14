import { useEffect, useState, type ReactElement } from "react";
import { ALL_UI_LANGS, UILangProvider, applyFirstVisitLangRedirect, pathLangOf, preferredLang, stripLangPrefix } from "./lib/i18n";
import { captureFirstTouch } from "./lib/signupSource";
import { LandingLogin } from "./screens/LandingLogin";
import { LegalPage } from "./screens/legal/LegalPage";
import { PricingPage } from "./screens/legal/PricingPage";
import { PayPage } from "./screens/legal/PayPage";
import { NotFound } from "./screens/legal/NotFound";
import { Landing } from "./screens/marketing/Landing";
import { LanguagePage } from "./screens/marketing/LanguagePage";
import { CasePage } from "./screens/marketing/CasePage";
import { langPageOf } from "./screens/marketing/langPages";
import { AppShell } from "./AppShell";
import { apiLogout, getMe, type Me } from "./lib/api";
import { clearToken, getToken } from "./lib/auth";

/** 路径（已剥语言前缀）→ 该网址**未登录时**渲染的页面；不在公开清单里则 null。
 *
 * 抽出来是为了让构建期预渲染和浏览器走**同一份映射**。分成两份的话，新增一个公开页
 * 只改一边是迟早的事，而症状很隐蔽：网址在浏览器里好好的，抓取器拿到的却是另一回事，
 * 且不报任何错。
 *
 * 不含 `/pay`（robots 禁收录、且要带查询串才有意义）与无效 id/slug 的回落——
 * 那两件是**副作用**（跳转），只发生在浏览器里，留在 Root。
 */
export function publicPageFor(path: string, onSignedIn: (m: Me) => void = () => {}): ReactElement | null {
  if (path === "/terms") return <LegalPage slug="terms" />;
  if (path === "/privacy") return <LegalPage slug="privacy" />;
  if (path === "/pricing") return <PricingPage />;
  if (path === "/case") return <CasePage />;
  if (path.startsWith("/languages/")) {
    const id = path.split("/")[2] ?? "";
    return langPageOf(id) ? <LanguagePage langId={id} /> : null;
  }
  if (path === "/signin") return <LandingLogin onSignedIn={onSignedIn} />;
  if (path === "/") return <Landing />;
  return null;
}

function Root() {
  // 公开页（无需登录、不恢复会话）——访客、SEO 与合规审核直达稳定 URL
  // 语言前缀路由（/zh/... /de/... 等；en=根路径）：已放量语言剥前缀后走同一套路由，
  // 界面语言由 UILangProvider 按 URL 前缀判定；ALL 里但未放量的前缀回剥前缀路径（不留重复内容 URL）
  const rawPath = window.location.pathname.replace(/\/$/, "");
  const seg = rawPath.split("/")[1] ?? "";
  if ((ALL_UI_LANGS as string[]).includes(seg) && seg !== "en" && pathLangOf(rawPath) == null) {
    const rest = rawPath.slice(seg.length + 1) || "/";
    window.location.replace(rest);
    return null;
  }
  // `|| "/"` 不能省：rawPath 削掉了尾斜杠，首页在这里是**空串**不是 "/"。
  // 少了它，下面那条「未知路径 → 404」会把首页判成 404（本地测试当场逮到）。
  const path = (pathLangOf(rawPath) ? stripLangPrefix(rawPath) : rawPath) || "/";
  // 支付落地页：Paddle 的结账链接指向它（`/pay?_ptxn=…`）。**必须留在公开页这一组**——
  // 催付/更新支付方式的邮件也会把人送到这里，那时未必带着会话。
  // 不进 publicPageFor：robots 禁收录、且要带查询串才有意义，预渲染它没有意义。
  if (path === "/pay") return <PayPage />;
  // 语种页 / 对比页的**无效** id、slug（未发布=对手数据未人工核实）一律回主页。
  // 这是副作用，只发生在浏览器里，所以留在这儿而不是 publicPageFor。
  const bad =
    path.startsWith("/languages/") && publicPageFor(path) == null;
  if (bad) {
    window.location.replace("/");
    return null;
  }
  // `/` 与 `/signin` 要先过会话判定（已登录时渲染的是应用），所以不在这里返回
  if (path !== "/" && path !== "/signin") {
    const pub = publicPageFor(path);
    if (pub) return pub;
  }

  const [me, setMe] = useState<Me | null>(null);
  // 有 token 先静默恢复会话（30 天有效期由后端控制），失败清掉回宣传页
  const [booting, setBooting] = useState(() => !!getToken());
  useEffect(() => {
    if (!booting) return;
    getMe()
      .then(setMe)
      .catch(() => clearToken())
      .finally(() => setBooting(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signedIn = me != null;

  if (booting) return null; // 静默恢复中（毫秒级），不闪宣传页

  // 未知路径 = 404，不是首页。原先它静默渲染成首页、还带着首页的 canonical——
  // 对搜索引擎等于「无数个网址内容都是首页」（审计 C3）。
  // 真正的 404 状态码由边缘给（vercel.json 不再兜底重写），这里负责把人接住并 noindex。
  if (path !== "/" && path !== "/signin") return <NotFound />;

  if (!signedIn) {
    // 登录入口收进导航（2026-08 宣传页决议）：/signin = 原登录屏；`/` = 宣传页
    return publicPageFor(path, (m) => {
      try { history.replaceState(null, "", "/"); } catch { /* 无 history 时忽略 */ }
      setMe(m);
    });
  }

  // 已登录：/signin 归一化回 `/`（渲染的都是应用）
  if (path === "/signin") {
    try { history.replaceState(null, "", "/"); } catch { /* 无 history 时忽略 */ }
  }
  return (
    <AppShell
      key={me?.email ?? "app"} // 换账号重置应用内状态
      me={me}
      onLogout={() => {
        void apiLogout();
        clearToken();
        setMe(null);
      }}
    />
  );
}

export default function App() {
  // 必须在 UILangProvider 之前：它按 URL 判定语言，晚一步就读到旧路径。
  // 带着会话就不跳——那是去应用，应用的语言走 tx_lang，与 URL 无关。
  applyFirstVisitLangRedirect(!!getToken());
  // 注册来源（first-touch）：第一次落地就记，放在语言跳转之后——跳转保留查询串，
  // 而 landing_path 要的是剥了语言前缀的路径，先后无所谓；已有记录不覆盖。
  if (typeof window !== "undefined") captureFirstTouch(preferredLang());
  return (
    <UILangProvider>
      <Root />
    </UILangProvider>
  );
}

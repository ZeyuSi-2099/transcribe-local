import { useEffect, useState } from "react";
import { UILangProvider, useL } from "./lib/i18n";
import { AppShell } from "./AppShell";
import { getMe, type Me } from "./lib/api";
import { semantic, type as ttype } from "./styles/tokens";

/** 本机版入口（与线上不同）。
 *
 * 线上这里是一张路由表（宣传页 / 定价 / 条款 / 登录 / 付款落地页）加「有会话才进应用」。
 * 本机只有一个用户、打开就用：问一次本机服务「我是谁」，然后进应用。
 * 服务没起来时不留白屏——说一句怎么办，并且每两秒再问一次，起来了自己进去。 */
function Root() {
  const [me, setMe] = useState<Me | null>(null);
  const [down, setDown] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const ask = () => {
      getMe()
        .then((m) => { if (alive) setMe(m); })
        .catch(() => { if (alive) { setDown(true); timer = window.setTimeout(ask, 2000); } });
    };
    ask();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);
  if (me) return <AppShell me={me} />;
  return down ? <ServiceDown /> : null;
}

function ServiceDown() {
  const L = useL();
  return (
    <div role="alert" style={{ height: "100%", display: "grid", placeItems: "center", padding: 24 }}>
      <div style={{ maxWidth: 440, textAlign: "center" }}>
        <div style={{ ...ttype.h2, marginBottom: 8 }}>{L("连不上本机服务", "Can't reach the local service")}</div>
        <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6, color: semantic.text.secondary }}>
          {L("先在终端里启动服务。启动之后这一页会自己连上，不用刷新。",
             "Start the service in a terminal. This page reconnects on its own once it's running.")}
        </p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <UILangProvider>
      <Root />
    </UILangProvider>
  );
}

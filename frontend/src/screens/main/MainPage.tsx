import { useState } from "react";
import type { Flow } from "../../lib/flow";
import { Idle } from "./Idle";
import { ErrorView } from "./ErrorView";
import type { ErrorKind } from "./ErrorView";
import type { Glossary } from "../../lib/api";
import type { HistoryItem } from "../../lib/sampleData";
import { layout } from "../../styles/tokens";

interface MainPageProps {
  flow: Flow;
  errorKind?: ErrorKind | null;
  balance: number; // 账户余额（美元）
  onTopUp: (suggested?: number) => void; // suggested = 缺口金额，预填充值弹窗
  onStartJob: (file: File, lang: string, durationSec: number | null) => void;
  glossaries?: Glossary[];
  selectedGlossaryId?: string | null;
  onSelectGlossary?: (id: string | null) => void;
  onOpenGlossary?: () => void;
  freeLeftSeconds?: number;   // 免费额度剩余秒（预估/软墙要算入抵扣）
  freeLimited?: boolean;      // IP 闸触发：显示「同网络已有试用」提示
  /** 上传页左栏用：已完成的转录（最近在前）+ 打开动作。空列表 → 上传卡居中，不渲染左栏。 */
  recent?: HistoryItem[];
  onOpenRecent?: (item: HistoryItem) => void;
  onOpenHistory?: () => void;
}

export function MainPage({ flow, errorKind, balance, onTopUp, onStartJob, glossaries, selectedGlossaryId, onSelectGlossary, onOpenGlossary, freeLeftSeconds, freeLimited, recent, onOpenRecent, onOpenHistory }: MainPageProps) {
  const [lang, setLang] = useState("en"); // 默认英语（与 COMMON_LANGS 首项一致）

  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden", background: "transparent" }}>
      <div
        className="tx-scroll"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: layout.pagePad,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
        }}
      >
        {errorKind ? (
          <ErrorView kind={errorKind} onReset={flow.reset} onLink={onTopUp} />
        ) : (
          <Idle
            // 软墙已内置在 Idle 卡内（余额不够 → 主按钮变「先充值 →」并带缺口金额）
            onStart={(file, durationSec) => onStartJob(file, lang, durationSec)}
            lang={lang}
            setLang={setLang}
            balance={balance}
            onTopUp={onTopUp}
            glossaries={glossaries}
            selectedGlossaryId={selectedGlossaryId}
            onSelectGlossary={onSelectGlossary}
            onOpenGlossary={onOpenGlossary}
            freeLeftSeconds={freeLeftSeconds}
            freeLimited={freeLimited}
            recent={recent}
            onOpenRecent={onOpenRecent}
            onOpenHistory={onOpenHistory}
          />
        )}
      </div>
    </div>
  );
}

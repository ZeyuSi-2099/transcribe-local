import { useState } from "react";
import type { Flow } from "../../lib/flow";
import { Idle } from "./Idle";
import { ErrorView } from "./ErrorView";
import type { ErrorKind } from "./ErrorView";
import type { Glossary } from "../../lib/api";
import type { HistoryItem } from "../../lib/sampleData";
import { layout } from "../../styles/tokens";
import { DEFAULT_LOCAL_LANG } from "../../lib/localLangs";

interface MainPageProps {
  flow: Flow;
  errorKind?: ErrorKind | null;
  onStartJob: (file: File, lang: string, durationSec: number | null) => void;
  glossaries?: Glossary[];
  selectedGlossaryId?: string | null;
  onSelectGlossary?: (id: string | null) => void;
  onOpenGlossary?: () => void;
  /** 上传页左栏用：已完成的转录（最近在前）+ 打开动作。空列表 → 上传卡居中，不渲染左栏。 */
  recent?: HistoryItem[];
  onOpenRecent?: (item: HistoryItem) => void;
  onOpenHistory?: () => void;
}

export function MainPage({ flow, errorKind, onStartJob, glossaries, selectedGlossaryId, onSelectGlossary, onOpenGlossary, recent, onOpenRecent, onOpenHistory }: MainPageProps) {
  const [lang, setLang] = useState(DEFAULT_LOCAL_LANG); // 本机版：默认本地能转的那门（线上默认英语）

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
          <ErrorView kind={errorKind} onReset={flow.reset} onLink={flow.reset} />
        ) : (
          <Idle
            onStart={(file, durationSec) => onStartJob(file, lang, durationSec)}
            lang={lang}
            setLang={setLang}
            glossaries={glossaries}
            selectedGlossaryId={selectedGlossaryId}
            onSelectGlossary={onSelectGlossary}
            onOpenGlossary={onOpenGlossary}
            recent={recent}
            onOpenRecent={onOpenRecent}
            onOpenHistory={onOpenHistory}
          />
        )}
      </div>
    </div>
  );
}

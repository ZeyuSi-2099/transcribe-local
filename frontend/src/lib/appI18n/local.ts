// 本机版新增文案的六门译文（本地独有文件）。
//
// 线上的六本对照本（app.<lang>.ts）原样同步、一个字不改；本地加的句子另放这里，
// index.ts 合并时排在最后。键同样是源码里 L() 的英文实参，逐字一致。
import type { UILang } from "../i18n";
import type { AppOverride } from ".";

export const LOCAL_OVERRIDES: Partial<Record<UILang, AppOverride>> = {
  de: {
    "Can't reach the local service": "Lokaler Dienst nicht erreichbar",
    "Start the service in a terminal. This page reconnects on its own once it's running.":
      "Starten Sie den Dienst im Terminal. Diese Seite verbindet sich automatisch, sobald er läuft.",
    "Not available in the local version yet": "In der lokalen Version noch nicht verfügbar",
    "Settings": "Einstellungen",
    "System status": "Systemstatus",
    "Data": "Daten",
    "Storage": "Speicherort",
    "Audio and transcripts are stored on this computer and are never deleted automatically.":
      "Audio und Transkripte liegen auf diesem Computer und werden nie automatisch gelöscht.",
    "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.":
      "Alle Transkripte und Audiodateien auf einmal löschen. Nicht rückgängig zu machen. Ihre Glossare bleiben erhalten.",
    "Post-processing failed": "Nachbearbeitung fehlgeschlagen",
    "Audio deleted · your transcript stays available to view and export.":
      "Audio gelöscht · das Transkript bleibt zum Ansehen und Exportieren verfügbar.",
    "Audio and transcripts stay on this computer and are never deleted automatically":
      "Audio und Transkripte bleiben auf diesem Computer und werden nie automatisch gelöscht",
  },
  fr: {
    "Can't reach the local service": "Service local injoignable",
    "Start the service in a terminal. This page reconnects on its own once it's running.":
      "Lancez le service dans un terminal. Cette page se reconnecte d'elle-même dès qu'il tourne.",
    "Not available in the local version yet": "Pas encore disponible dans la version locale",
    "Settings": "Réglages",
    "System status": "État du système",
    "Data": "Données",
    "Storage": "Stockage",
    "Audio and transcripts are stored on this computer and are never deleted automatically.":
      "L'audio et les transcriptions restent sur cet ordinateur et ne sont jamais supprimés automatiquement.",
    "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.":
      "Supprimer d'un coup toutes les transcriptions et l'audio. Irréversible. Vos glossaires sont conservés.",
    "Post-processing failed": "Échec du post-traitement",
    "Audio deleted · your transcript stays available to view and export.":
      "Audio supprimé · la transcription reste consultable et exportable.",
    "Audio and transcripts stay on this computer and are never deleted automatically":
      "L'audio et les transcriptions restent sur cet ordinateur et ne sont jamais supprimés automatiquement",
  },
  es: {
    "Can't reach the local service": "No se puede conectar con el servicio local",
    "Start the service in a terminal. This page reconnects on its own once it's running.":
      "Inicia el servicio en una terminal. Esta página se reconecta sola en cuanto esté en marcha.",
    "Not available in the local version yet": "Aún no disponible en la versión local",
    "Settings": "Ajustes",
    "System status": "Estado del sistema",
    "Data": "Datos",
    "Storage": "Almacenamiento",
    "Audio and transcripts are stored on this computer and are never deleted automatically.":
      "El audio y las transcripciones se guardan en este ordenador y nunca se borran automáticamente.",
    "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.":
      "Borra todas las transcripciones y el audio de una vez. No se puede deshacer. Tus glosarios se conservan.",
    "Post-processing failed": "Falló el posprocesamiento",
    "Audio deleted · your transcript stays available to view and export.":
      "Audio eliminado · la transcripción sigue disponible para ver y exportar.",
    "Audio and transcripts stay on this computer and are never deleted automatically":
      "El audio y las transcripciones se quedan en este ordenador y nunca se borran automáticamente",
  },
  it: {
    "Can't reach the local service": "Impossibile raggiungere il servizio locale",
    "Start the service in a terminal. This page reconnects on its own once it's running.":
      "Avvia il servizio da un terminale. Questa pagina si ricollega da sola appena è in esecuzione.",
    "Not available in the local version yet": "Non ancora disponibile nella versione locale",
    "Settings": "Impostazioni",
    "System status": "Stato del sistema",
    "Data": "Dati",
    "Storage": "Archiviazione",
    "Audio and transcripts are stored on this computer and are never deleted automatically.":
      "Audio e trascrizioni restano su questo computer e non vengono mai eliminati automaticamente.",
    "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.":
      "Elimina in una volta tutte le trascrizioni e l'audio. Non si può annullare. I glossari restano.",
    "Post-processing failed": "Post-elaborazione non riuscita",
    "Audio deleted · your transcript stays available to view and export.":
      "Audio eliminato · la trascrizione resta disponibile da consultare ed esportare.",
    "Audio and transcripts stay on this computer and are never deleted automatically":
      "Audio e trascrizioni restano su questo computer e non vengono mai eliminati automaticamente",
  },
  pt: {
    "Can't reach the local service": "Não foi possível acessar o serviço local",
    "Start the service in a terminal. This page reconnects on its own once it's running.":
      "Inicie o serviço em um terminal. Esta página se reconecta sozinha assim que ele estiver rodando.",
    "Not available in the local version yet": "Ainda não disponível na versão local",
    "Settings": "Configurações",
    "System status": "Status do sistema",
    "Data": "Dados",
    "Storage": "Armazenamento",
    "Audio and transcripts are stored on this computer and are never deleted automatically.":
      "O áudio e as transcrições ficam neste computador e nunca são apagados automaticamente.",
    "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.":
      "Apagar de uma vez todas as transcrições e o áudio. Não pode ser desfeito. Seus glossários são mantidos.",
    "Post-processing failed": "Falha no pós-processamento",
    "Audio deleted · your transcript stays available to view and export.":
      "Áudio apagado · a transcrição continua disponível para ver e exportar.",
    "Audio and transcripts stay on this computer and are never deleted automatically":
      "O áudio e as transcrições ficam neste computador e nunca são apagados automaticamente",
  },
  ja: {
    "Can't reach the local service": "ローカルサービスに接続できません",
    "Start the service in a terminal. This page reconnects on its own once it's running.":
      "ターミナルでサービスを起動してください。起動するとこのページは自動で接続します。",
    "Not available in the local version yet": "ローカル版ではまだ使えません",
    "Settings": "設定",
    "System status": "システム状態",
    "Data": "データ",
    "Storage": "保存場所",
    "Audio and transcripts are stored on this computer and are never deleted automatically.":
      "音声と文字起こしはこのコンピューターに保存され、自動で削除されることはありません。",
    "Delete all transcripts and audio at once. Cannot be undone. Your glossaries stay.":
      "すべての文字起こしと音声を一括で削除します。元に戻せません。用語集は残ります。",
    "Post-processing failed": "後処理に失敗しました",
    "Audio deleted · your transcript stays available to view and export.":
      "音声は削除されました · 文字起こしは引き続き閲覧・書き出しできます。",
    "Audio and transcripts stay on this computer and are never deleted automatically":
      "音声と文字起こしはこのコンピューターに残り、自動で削除されることはありません",
  },
};

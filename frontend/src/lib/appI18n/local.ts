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
    "Download the speech models first":
      "Laden Sie zuerst die Spracherkennungsmodelle herunter",
    "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.":
      "Die Transkription läuft vollständig auf diesem Computer. Beim ersten Mal werden etwa {0} an Modellen benötigt. Sie laden sie nur einmal herunter.",
    "Saved to: {0}":
      "Speicherort: {0}",
    "Start download":
      "Download starten",
    "Downloading {0} · {1} / {2}":
      "Lade {0} herunter · {1} / {2}",
    "Download stopped: {0}":
      "Download abgebrochen: {0}",
    "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.":
      "Modelle schon vorhanden? Setzen Sie TRANSCRIBE_LOCAL_MODELS auf diesen Ordner und laden Sie die Seite neu.",
    "Stable results, light on your computer · transcript text is sent to DeepSeek":
      "Stabile Ergebnisse, schont Ihren Computer · der Transkripttext wird an DeepSeek gesendet",
    "Local Ollama":
      "Lokales Ollama",
    "Nothing leaves this computer · install and start Ollama first":
      "Nichts verlässt diesen Computer · Ollama zuerst installieren und starten",
    "Local LM Studio / mlx":
      "Lokales LM Studio / mlx",
    "Nothing leaves this computer · start the local model server first":
      "Nichts verlässt diesen Computer · zuerst den lokalen Modellserver starten",
    "Claude subscription":
      "Claude-Abo",
    "Proofreading only · glossary drafting and post-processing need an API or a local model":
      "Nur für die Korrektur · Glossarentwurf und Nachbearbeitung brauchen eine API oder ein lokales Modell",
    "Custom (set in the config file)":
      "Benutzerdefiniert (in der Konfigurationsdatei gesetzt)",
    "Audio":
      "Audio",
    "Transcript text during proofreading":
      "Transkripttext bei der Korrektur",
    "Glossary drafting and checks":
      "Glossarentwurf und -prüfung",
    "Transcript text in post-processing":
      "Transkripttext in der Nachbearbeitung",
    "Search terms for online checks":
      "Suchbegriffe für die Online-Prüfung",
    "Stays on this computer":
      "Bleibt auf diesem Computer",
    "Sent to {0}":
      "Wird an {0} gesendet",
    "Sent to Anthropic (Claude subscription)":
      "Wird an Anthropic gesendet (Claude-Abo)",
    "Not available with this backend":
      "Mit diesem Backend nicht verfügbar",
    "Couldn't load the model backend settings.":
      "Die Einstellungen für das Modell-Backend konnten nicht geladen werden.",
    "Model backend":
      "Modell-Backend",
    "Proofreading, glossary drafting and post-processing all use this one setting.":
      "Korrektur, Glossarentwurf und Nachbearbeitung nutzen alle diese eine Einstellung.",
    "Model":
      "Modell",
    "Key is read from environment variable {0} · set":
      "Schlüssel wird aus der Umgebungsvariable {0} gelesen · gesetzt",
    "Key is read from environment variable {0} · not set":
      "Schlüssel wird aus der Umgebungsvariable {0} gelesen · nicht gesetzt",
    "Verify unclear names online (Bocha)":
      "Unklare Namen online prüfen (Bocha)",
    "Needs environment variable BOCHA_API_KEY":
      "Benötigt die Umgebungsvariable BOCHA_API_KEY",
    "Testing…":
      "Wird getestet…",
    "Test connection":
      "Verbindung testen",
    "Connected":
      "Verbunden",
    "Not reachable: {0}":
      "Nicht erreichbar: {0}",
    "Where your data goes":
      "Wohin Ihre Daten gehen",
    "Canceling…":
      "Wird abgebrochen…",
    "Confirm cancel":
      "Abbrechen bestätigen",
    "Cancel":
      "Abbrechen",
    "Canceled":
      "Abgebrochen",
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
    "Download the speech models first":
      "Téléchargez d'abord les modèles de reconnaissance",
    "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.":
      "La transcription se fait entièrement sur cet ordinateur. La première fois, il faut environ {0} de modèles. Vous ne les téléchargez qu'une fois.",
    "Saved to: {0}":
      "Emplacement : {0}",
    "Start download":
      "Lancer le téléchargement",
    "Downloading {0} · {1} / {2}":
      "Téléchargement de {0} · {1} / {2}",
    "Download stopped: {0}":
      "Téléchargement interrompu : {0}",
    "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.":
      "Vous avez déjà les modèles ? Faites pointer TRANSCRIBE_LOCAL_MODELS vers ce dossier, puis rechargez la page.",
    "Stable results, light on your computer · transcript text is sent to DeepSeek":
      "Résultats stables, léger pour votre ordinateur · le texte de la transcription est envoyé à DeepSeek",
    "Local Ollama":
      "Ollama en local",
    "Nothing leaves this computer · install and start Ollama first":
      "Rien ne quitte cet ordinateur · installez et lancez d'abord Ollama",
    "Local LM Studio / mlx":
      "LM Studio / mlx en local",
    "Nothing leaves this computer · start the local model server first":
      "Rien ne quitte cet ordinateur · lancez d'abord le serveur de modèle local",
    "Claude subscription":
      "Abonnement Claude",
    "Proofreading only · glossary drafting and post-processing need an API or a local model":
      "Correction uniquement · la rédaction du glossaire et le post-traitement demandent une API ou un modèle local",
    "Custom (set in the config file)":
      "Personnalisé (défini dans le fichier de configuration)",
    "Audio":
      "Audio",
    "Transcript text during proofreading":
      "Texte de la transcription pendant la correction",
    "Glossary drafting and checks":
      "Rédaction et vérification du glossaire",
    "Transcript text in post-processing":
      "Texte de la transcription en post-traitement",
    "Search terms for online checks":
      "Termes de recherche pour la vérification en ligne",
    "Stays on this computer":
      "Reste sur cet ordinateur",
    "Sent to {0}":
      "Envoyé à {0}",
    "Sent to Anthropic (Claude subscription)":
      "Envoyé à Anthropic (abonnement Claude)",
    "Not available with this backend":
      "Indisponible avec ce backend",
    "Couldn't load the model backend settings.":
      "Impossible de charger les réglages du backend de modèle.",
    "Model backend":
      "Backend de modèle",
    "Proofreading, glossary drafting and post-processing all use this one setting.":
      "La correction, la rédaction du glossaire et le post-traitement utilisent tous ce réglage.",
    "Model":
      "Modèle",
    "Key is read from environment variable {0} · set":
      "Clé lue depuis la variable d'environnement {0} · définie",
    "Key is read from environment variable {0} · not set":
      "Clé lue depuis la variable d'environnement {0} · non définie",
    "Verify unclear names online (Bocha)":
      "Vérifier en ligne les noms incertains (Bocha)",
    "Needs environment variable BOCHA_API_KEY":
      "Nécessite la variable d'environnement BOCHA_API_KEY",
    "Testing…":
      "Test en cours…",
    "Test connection":
      "Tester la connexion",
    "Connected":
      "Connecté",
    "Not reachable: {0}":
      "Injoignable : {0}",
    "Where your data goes":
      "Où vont vos données",
    "Canceling…":
      "Annulation…",
    "Confirm cancel":
      "Confirmer l'annulation",
    "Cancel":
      "Annuler",
    "Canceled":
      "Annulé",
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
    "Download the speech models first":
      "Primero descarga los modelos de reconocimiento",
    "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.":
      "La transcripción se hace por completo en este ordenador. La primera vez necesita unos {0} de modelos. Solo se descargan una vez.",
    "Saved to: {0}":
      "Se guarda en: {0}",
    "Start download":
      "Empezar la descarga",
    "Downloading {0} · {1} / {2}":
      "Descargando {0} · {1} / {2}",
    "Download stopped: {0}":
      "La descarga se interrumpió: {0}",
    "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.":
      "¿Ya tienes los modelos? Apunta TRANSCRIBE_LOCAL_MODELS a esa carpeta y recarga esta página.",
    "Stable results, light on your computer · transcript text is sent to DeepSeek":
      "Resultados estables, poco peso para tu ordenador · el texto de la transcripción se envía a DeepSeek",
    "Local Ollama":
      "Ollama local",
    "Nothing leaves this computer · install and start Ollama first":
      "Nada sale de este ordenador · instala e inicia Ollama primero",
    "Local LM Studio / mlx":
      "LM Studio / mlx local",
    "Nothing leaves this computer · start the local model server first":
      "Nada sale de este ordenador · inicia primero el servidor de modelos local",
    "Claude subscription":
      "Suscripción de Claude",
    "Proofreading only · glossary drafting and post-processing need an API or a local model":
      "Solo para la corrección · el borrador del glosario y el posprocesamiento necesitan una API o un modelo local",
    "Custom (set in the config file)":
      "Personalizado (definido en el archivo de configuración)",
    "Audio":
      "Audio",
    "Transcript text during proofreading":
      "Texto de la transcripción durante la corrección",
    "Glossary drafting and checks":
      "Borrador y revisión del glosario",
    "Transcript text in post-processing":
      "Texto de la transcripción en el posprocesamiento",
    "Search terms for online checks":
      "Términos de búsqueda para la verificación en línea",
    "Stays on this computer":
      "Se queda en este ordenador",
    "Sent to {0}":
      "Se envía a {0}",
    "Sent to Anthropic (Claude subscription)":
      "Se envía a Anthropic (suscripción de Claude)",
    "Not available with this backend":
      "No disponible con este backend",
    "Couldn't load the model backend settings.":
      "No se pudieron cargar los ajustes del backend de modelos.",
    "Model backend":
      "Backend de modelos",
    "Proofreading, glossary drafting and post-processing all use this one setting.":
      "La corrección, el borrador del glosario y el posprocesamiento usan este mismo ajuste.",
    "Model":
      "Modelo",
    "Key is read from environment variable {0} · set":
      "La clave se lee de la variable de entorno {0} · definida",
    "Key is read from environment variable {0} · not set":
      "La clave se lee de la variable de entorno {0} · sin definir",
    "Verify unclear names online (Bocha)":
      "Verificar en línea los nombres dudosos (Bocha)",
    "Needs environment variable BOCHA_API_KEY":
      "Necesita la variable de entorno BOCHA_API_KEY",
    "Testing…":
      "Probando…",
    "Test connection":
      "Probar la conexión",
    "Connected":
      "Conectado",
    "Not reachable: {0}":
      "No responde: {0}",
    "Where your data goes":
      "Adónde van tus datos",
    "Canceling…":
      "Cancelando…",
    "Confirm cancel":
      "Confirmar cancelación",
    "Cancel":
      "Cancelar",
    "Canceled":
      "Cancelado",
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
    "Download the speech models first":
      "Scarica prima i modelli di riconoscimento",
    "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.":
      "La trascrizione avviene interamente su questo computer. La prima volta servono circa {0} di modelli. Li scarichi una sola volta.",
    "Saved to: {0}":
      "Salvati in: {0}",
    "Start download":
      "Avvia il download",
    "Downloading {0} · {1} / {2}":
      "Download di {0} · {1} / {2}",
    "Download stopped: {0}":
      "Download interrotto: {0}",
    "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.":
      "Hai già i modelli? Imposta TRANSCRIBE_LOCAL_MODELS su quella cartella e ricarica la pagina.",
    "Stable results, light on your computer · transcript text is sent to DeepSeek":
      "Risultati stabili, leggero per il computer · il testo della trascrizione viene inviato a DeepSeek",
    "Local Ollama":
      "Ollama in locale",
    "Nothing leaves this computer · install and start Ollama first":
      "Nulla lascia questo computer · installa e avvia prima Ollama",
    "Local LM Studio / mlx":
      "LM Studio / mlx in locale",
    "Nothing leaves this computer · start the local model server first":
      "Nulla lascia questo computer · avvia prima il server del modello locale",
    "Claude subscription":
      "Abbonamento Claude",
    "Proofreading only · glossary drafting and post-processing need an API or a local model":
      "Solo per la correzione · la bozza del glossario e la post-elaborazione richiedono un'API o un modello locale",
    "Custom (set in the config file)":
      "Personalizzato (impostato nel file di configurazione)",
    "Audio":
      "Audio",
    "Transcript text during proofreading":
      "Testo della trascrizione durante la correzione",
    "Glossary drafting and checks":
      "Bozza e controllo del glossario",
    "Transcript text in post-processing":
      "Testo della trascrizione nella post-elaborazione",
    "Search terms for online checks":
      "Termini di ricerca per la verifica online",
    "Stays on this computer":
      "Resta su questo computer",
    "Sent to {0}":
      "Inviato a {0}",
    "Sent to Anthropic (Claude subscription)":
      "Inviato ad Anthropic (abbonamento Claude)",
    "Not available with this backend":
      "Non disponibile con questo backend",
    "Couldn't load the model backend settings.":
      "Impossibile caricare le impostazioni del backend del modello.",
    "Model backend":
      "Backend del modello",
    "Proofreading, glossary drafting and post-processing all use this one setting.":
      "Correzione, bozza del glossario e post-elaborazione usano tutti questa impostazione.",
    "Model":
      "Modello",
    "Key is read from environment variable {0} · set":
      "La chiave viene letta dalla variabile d'ambiente {0} · impostata",
    "Key is read from environment variable {0} · not set":
      "La chiave viene letta dalla variabile d'ambiente {0} · non impostata",
    "Verify unclear names online (Bocha)":
      "Verifica online i nomi incerti (Bocha)",
    "Needs environment variable BOCHA_API_KEY":
      "Richiede la variabile d'ambiente BOCHA_API_KEY",
    "Testing…":
      "Test in corso…",
    "Test connection":
      "Prova la connessione",
    "Connected":
      "Connesso",
    "Not reachable: {0}":
      "Non raggiungibile: {0}",
    "Where your data goes":
      "Dove vanno i tuoi dati",
    "Canceling…":
      "Annullamento…",
    "Confirm cancel":
      "Conferma annullamento",
    "Cancel":
      "Annulla",
    "Canceled":
      "Annullato",
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
    "Download the speech models first":
      "Baixe primeiro os modelos de reconhecimento",
    "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.":
      "A transcrição roda inteiramente neste computador. Na primeira vez, ela precisa de cerca de {0} de modelos. Você só baixa uma vez.",
    "Saved to: {0}":
      "Salvo em: {0}",
    "Start download":
      "Iniciar download",
    "Downloading {0} · {1} / {2}":
      "Baixando {0} · {1} / {2}",
    "Download stopped: {0}":
      "O download parou: {0}",
    "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.":
      "Já tem os modelos? Aponte TRANSCRIBE_LOCAL_MODELS para essa pasta e recarregue esta página.",
    "Stable results, light on your computer · transcript text is sent to DeepSeek":
      "Resultados estáveis, leve para o computador · o texto da transcrição é enviado à DeepSeek",
    "Local Ollama":
      "Ollama local",
    "Nothing leaves this computer · install and start Ollama first":
      "Nada sai deste computador · instale e inicie o Ollama primeiro",
    "Local LM Studio / mlx":
      "LM Studio / mlx local",
    "Nothing leaves this computer · start the local model server first":
      "Nada sai deste computador · inicie primeiro o servidor de modelo local",
    "Claude subscription":
      "Assinatura do Claude",
    "Proofreading only · glossary drafting and post-processing need an API or a local model":
      "Só para a revisão · o rascunho do glossário e o pós-processamento precisam de uma API ou de um modelo local",
    "Custom (set in the config file)":
      "Personalizado (definido no arquivo de configuração)",
    "Audio":
      "Áudio",
    "Transcript text during proofreading":
      "Texto da transcrição durante a revisão",
    "Glossary drafting and checks":
      "Rascunho e verificação do glossário",
    "Transcript text in post-processing":
      "Texto da transcrição no pós-processamento",
    "Search terms for online checks":
      "Termos de busca para a verificação online",
    "Stays on this computer":
      "Fica neste computador",
    "Sent to {0}":
      "Enviado para {0}",
    "Sent to Anthropic (Claude subscription)":
      "Enviado para a Anthropic (assinatura do Claude)",
    "Not available with this backend":
      "Indisponível com este backend",
    "Couldn't load the model backend settings.":
      "Não foi possível carregar as configurações do backend de modelo.",
    "Model backend":
      "Backend de modelo",
    "Proofreading, glossary drafting and post-processing all use this one setting.":
      "Revisão, rascunho do glossário e pós-processamento usam esta mesma configuração.",
    "Model":
      "Modelo",
    "Key is read from environment variable {0} · set":
      "A chave é lida da variável de ambiente {0} · definida",
    "Key is read from environment variable {0} · not set":
      "A chave é lida da variável de ambiente {0} · não definida",
    "Verify unclear names online (Bocha)":
      "Verificar online os nomes duvidosos (Bocha)",
    "Needs environment variable BOCHA_API_KEY":
      "Precisa da variável de ambiente BOCHA_API_KEY",
    "Testing…":
      "Testando…",
    "Test connection":
      "Testar conexão",
    "Connected":
      "Conectado",
    "Not reachable: {0}":
      "Sem resposta: {0}",
    "Where your data goes":
      "Para onde vão seus dados",
    "Canceling…":
      "Cancelando…",
    "Confirm cancel":
      "Confirmar cancelamento",
    "Cancel":
      "Cancelar",
    "Canceled":
      "Cancelado",
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
    "Download the speech models first":
      "まず音声認識モデルをダウンロードしてください",
    "Transcription runs entirely on this computer. The first time, it needs about {0} of models. You only download them once.":
      "文字起こしはすべてこのコンピューター上で行います。初回は約 {0} のモデルが必要です。ダウンロードは一度だけです。",
    "Saved to: {0}":
      "保存先：{0}",
    "Start download":
      "ダウンロードを開始",
    "Downloading {0} · {1} / {2}":
      "{0} をダウンロード中 · {1} / {2}",
    "Download stopped: {0}":
      "ダウンロードが中断しました：{0}",
    "Already have the models? Point TRANSCRIBE_LOCAL_MODELS at that folder and reload this page.":
      "モデルがすでにある場合は、TRANSCRIBE_LOCAL_MODELS をそのフォルダーに設定してこのページを再読み込みしてください。",
    "Stable results, light on your computer · transcript text is sent to DeepSeek":
      "結果が安定し、PC への負荷も軽い · 文字起こしのテキストは DeepSeek に送信されます",
    "Local Ollama":
      "ローカルの Ollama",
    "Nothing leaves this computer · install and start Ollama first":
      "このコンピューターの外には何も出ません · 先に Ollama をインストールして起動してください",
    "Local LM Studio / mlx":
      "ローカルの LM Studio / mlx",
    "Nothing leaves this computer · start the local model server first":
      "このコンピューターの外には何も出ません · 先にローカルのモデルサーバーを起動してください",
    "Claude subscription":
      "Claude のサブスクリプション",
    "Proofreading only · glossary drafting and post-processing need an API or a local model":
      "校正専用 · 用語集の下書きと後処理には API かローカルモデルが必要です",
    "Custom (set in the config file)":
      "カスタム（設定ファイルで指定）",
    "Audio":
      "音声",
    "Transcript text during proofreading":
      "校正中の文字起こしテキスト",
    "Glossary drafting and checks":
      "用語集の下書きとチェック",
    "Transcript text in post-processing":
      "後処理での文字起こしテキスト",
    "Search terms for online checks":
      "オンライン確認用の検索語",
    "Stays on this computer":
      "このコンピューターから出ません",
    "Sent to {0}":
      "{0} に送信",
    "Sent to Anthropic (Claude subscription)":
      "Anthropic に送信（Claude のサブスクリプション）",
    "Not available with this backend":
      "このバックエンドでは使えません",
    "Couldn't load the model backend settings.":
      "モデルバックエンドの設定を読み込めませんでした。",
    "Model backend":
      "モデルバックエンド",
    "Proofreading, glossary drafting and post-processing all use this one setting.":
      "校正・用語集の下書き・後処理はすべてこの設定を使います。",
    "Model":
      "モデル",
    "Key is read from environment variable {0} · set":
      "キーは環境変数 {0} から読み込みます · 設定済み",
    "Key is read from environment variable {0} · not set":
      "キーは環境変数 {0} から読み込みます · 未設定",
    "Verify unclear names online (Bocha)":
      "判断できない固有名詞をオンラインで確認（Bocha）",
    "Needs environment variable BOCHA_API_KEY":
      "環境変数 BOCHA_API_KEY が必要です",
    "Testing…":
      "テスト中…",
    "Test connection":
      "接続をテスト",
    "Connected":
      "接続できました",
    "Not reachable: {0}":
      "接続できません：{0}",
    "Where your data goes":
      "データの送信先",
    "Canceling…":
      "キャンセル中…",
    "Confirm cancel":
      "キャンセルを確定",
    "Cancel":
      "キャンセル",
    "Canceled":
      "キャンセル済み",
  },
};

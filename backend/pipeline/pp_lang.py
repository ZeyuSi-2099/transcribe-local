"""后处理这一步「用哪门语言写」的唯一指令来源。

规则（Duner 2026-08-30 定）：**我们写的字跟界面语言走，录音里的内容原样不翻。**
落到后处理上是两件事，方向相反，必须一次说清楚：

  · **产物正文**（叙述稿 / 脱敏稿）→ 跟**输入稿**（＝录音语种）
  · **质检报告 / 问题清单里我们写的字**（标题、分类名、说明、理由）→ 跟**界面语言**

举例：界面是西班牙语、录音是法语 ⇒ 产物是法语，报告的说明是西班牙语，
而报告里引用的那几句原文仍然是法语。

⚠️ **同一段指令要送到两条执行路**：Claude 路（`pp_runner._step_prompt` 拼进 prompt）与
   DeepSeek 降级路（`pp_deepseek` / `pp_redact_ds` 拼进 system）。只送一条的症状是
   「有时候是对的」——而降级是撞顶时才走的，平时测不出来。

⚠️ 指令里**不点名录音语种**，只说「与输入稿相同」。点名的风险是我们说的和文件里实际是什么
   不一致（lang 判错、混语访谈），那时模型会听我们的而不是听文件的，等于自己造了一次翻译。
"""
from __future__ import annotations

# 语言名表搬去 `ui_lang.py`（P3 报告那边要用同一份）。这里原样再导出一次，
# 老调用点与守卫不必改；**别在这里再抄一份表**。
from .ui_lang import UI_LANG_NAMES, ui_lang_name  # noqa: F401


def directive(ui_lang: str | None) -> str:
    """拼一段语言指令，追加在 skill 之后（所以它覆盖 skill 里的默认设定）。"""
    name = ui_lang_name(ui_lang)
    return f"""
## 本次任务的语言（覆盖上面任何与语言、字符集相关的默认设定）

1. **产物正文**：与输入稿**同一语言、同一字符集**。本步是改写，不是翻译，也不是字符规范化。
   输入稿里合法的字符（重音字母、西里尔字母、假名、谚文、阿拉伯字母、泰文……）一律原样保留。
   ⛔ **任何情况下都不得把正文里的字符替换成其他语言。** 拿不准就不动。
   **标点也跟着输入稿走**：中文稿用全角（，。「」（）），拉丁语言用半角（, . " " ( )）。
   规则里举的例子如果用了中文标点，那只是例子的写法，照做的是**规则**不是那个字形。

2. **你写给用户看的字**——质检报告 / 问题清单里的标题、章节名、分类名、说明、修改理由——
   **一律用 {name} 书写**。

3. ⚠️ 第 2 条**不包括报告里引用的原文**：改前 → 改后的原句、摘录、举证片段，
   一律保持输入稿原样，**不要翻译**。报告的骨架是我们的话，引文是用户的稿子。
"""

# ── 降级路那两份报告的文案（8 门） ──────────────────────────────────────────
#
# ⚠️ **这两份报告是代码拼的模板，不是模型写的**——所以上面那段语言指令对它们无效
#    （2026-08-30 真跑一遍才发现：产物已经是法语了，问题清单还是中文）。
#    Claude 主路的报告由模型写，走指令；DeepSeek 降级路走这张表。
#    两条路的报告对用户无从分辨，所以两边都得是他的语言。
#
# ⚠️ **末行的「共修复 N 处」原本是机读契约**（`pp_runner.parse_qc_fix_count` 抓它入库）。
#    翻译之后那个正则就抓不到了，而症状是「质检已修复 0 处」——不报错。
#    所以报告里另外埋一行 `<!-- qc-fix-count: N -->`，解析优先认它、认不到再回落旧正则
#    （老单的报告里没有这一行）。**机读的东西不该跟给人看的文案是同一串字。**
#
# 脱敏类别的七个词直接取界面上已在用的译法（`RedactChanges.tsx` + appI18n 对照本），
# 不另写一套——同一个类别在界面上和报告里叫两个名字，用户会以为是两回事。

# ⚠️ 计数类文案一律写成**标签式**（`Total fixes: {n}`），不写「{n} corrections」——
#    拉丁语言有单复数一致，`1 correcciones` 是错的，而给每门加复数形式换来的只是这一行。
#    标签式在八门里都读得通，也不会随数字变形。
FIX_COUNT_MARK = "qc-fix-count"

_R = {
    "zh": {
        "issues_title": "视角转换 · 问题清单", "sec_semantic": "一、语义丢失修复",
        "sec_reference": "二、指代修复", "sec_qc": "三、质检修复",
        "none": "未发现", "changed": "改动 {n} 段（段号：{ids}）",
        "not_applied": "另有 {n} 条未落地：", "appendix": "附 · 未落地明细",
        "total": "共修复 {n} 处",
        "step_narrate": "视角转换", "step_redact": "脱敏",
        "redact_title": "脱敏质检报告", "f_in": "输入", "f_out": "输出",
        "f_keep": "保留词清单", "f_scan": "扫描",
        "provided": "已提供", "not_provided": "未提供（纯智能识别）",
        "sec_records": "一、脱敏修改记录", "empty": "（无）",
        "sec_result": "二、质检结果", "sub_mech": "机械体检（程序）",
        "sub_model": "语义质检（模型）", "not_produced": "（未产出）",
        "th_pos": "位置", "th_orig": "原文", "th_after": "脱敏后", "th_count": "处数",
        "line": "行{n}", "count": "（{n} 条）",
        "sec_dropped": "丢弃的条目（护栏拦下）",
        "sec_kept": "复核判定为「不脱敏」的条目及依据",
        "kinds": ["公司名", "人名", "联系方式", "地点", "数字与规模", "项目名", "其他"],
    },
    "en": {
        "issues_title": "Narrative conversion · Issue list", "sec_semantic": "1. Meaning restored",
        "sec_reference": "2. References clarified", "sec_qc": "3. Quality fixes",
        "none": "None found", "changed": "Paragraphs changed: {n} (#{ids})",
        "not_applied": "Not applied: {n} — ", "appendix": "Appendix · Not applied",
        "total": "Total fixes: {n}",
        "step_narrate": "Narrative draft", "step_redact": "Redacted transcript",
        "redact_title": "Redaction QC report", "f_in": "Input", "f_out": "Output",
        "f_keep": "Keep list", "f_scan": "Scan",
        "provided": "provided", "not_provided": "not provided (automatic detection only)",
        "sec_records": "1. Redaction changes", "empty": "(none)",
        "sec_result": "2. Quality check", "sub_mech": "Mechanical check (program)",
        "sub_model": "Semantic check (model)", "not_produced": "(not produced)",
        "th_pos": "Where", "th_orig": "Original", "th_after": "Redacted", "th_count": "Count",
        "line": "line {n}", "count": " ({n})",
        "sec_dropped": "Dropped by the safety check",
        "sec_kept": "Reviewed and kept as-is, with reasons",
        "kinds": ["Company names", "People's names", "Contact details", "Places",
                  "Figures and sizes", "Project names", "Other"],
    },
    "de": {
        "issues_title": "Perspektivwechsel · Problemliste", "sec_semantic": "1. Wiederhergestellte Aussagen",
        "sec_reference": "2. Geklärte Bezüge", "sec_qc": "3. Qualitätskorrekturen",
        "none": "Nichts gefunden", "changed": "Geänderte Absätze: {n} (#{ids})",
        "not_applied": "Nicht übernommen: {n} — ", "appendix": "Anhang · Nicht übernommen",
        "total": "Korrekturen insgesamt: {n}",
        "step_narrate": "Erzählfassung", "step_redact": "Anonymisiertes Transkript",
        "redact_title": "Anonymisierungs-Prüfbericht", "f_in": "Eingabe", "f_out": "Ausgabe",
        "f_keep": "Behalten-Liste", "f_scan": "Prüflauf",
        "provided": "vorhanden", "not_provided": "nicht vorhanden (nur automatische Erkennung)",
        "sec_records": "1. Anonymisierte Stellen", "empty": "(keine)",
        "sec_result": "2. Prüfergebnis", "sub_mech": "Mechanische Prüfung (Programm)",
        "sub_model": "Inhaltliche Prüfung (Modell)", "not_produced": "(nicht erzeugt)",
        "th_pos": "Stelle", "th_orig": "Original", "th_after": "Anonymisiert", "th_count": "Anzahl",
        "line": "Zeile {n}", "count": " ({n})",
        "sec_dropped": "Von der Schutzprüfung verworfen",
        "sec_kept": "Geprüft und bewusst beibehalten, mit Begründung",
        "kinds": ["Firmennamen", "Personennamen", "Kontaktdaten", "Orte",
                  "Zahlen und Größen", "Projektnamen", "Sonstiges"],
    },
    "fr": {
        "issues_title": "Passage au récit · Liste des points", "sec_semantic": "1. Sens rétabli",
        "sec_reference": "2. Références clarifiées", "sec_qc": "3. Corrections de qualité",
        "none": "Rien trouvé", "changed": "Paragraphes modifiés : {n} (#{ids})",
        "not_applied": "Non appliqués : {n} — ", "appendix": "Annexe · Non appliqué",
        "total": "Total des corrections : {n}",
        "step_narrate": "Version narrative", "step_redact": "Transcription anonymisée",
        "redact_title": "Rapport de contrôle d'anonymisation", "f_in": "Entrée", "f_out": "Sortie",
        "f_keep": "Liste à conserver", "f_scan": "Analyse",
        "provided": "fournie", "not_provided": "non fournie (détection automatique seule)",
        "sec_records": "1. Passages anonymisés", "empty": "(aucun)",
        "sec_result": "2. Résultat du contrôle", "sub_mech": "Contrôle mécanique (programme)",
        "sub_model": "Contrôle sémantique (modèle)", "not_produced": "(non produit)",
        "th_pos": "Endroit", "th_orig": "Original", "th_after": "Anonymisé", "th_count": "Nombre",
        "line": "ligne {n}", "count": " ({n})",
        "sec_dropped": "Écartés par le garde-fou",
        "sec_kept": "Examinés et conservés tels quels, avec justification",
        "kinds": ["Noms d'entreprises", "Les noms de personnes", "Coordonnées", "Lieux",
                  "Chiffres et effectifs", "Noms de projets", "Autres"],
    },
    "es": {
        "issues_title": "Paso a relato · Lista de puntos", "sec_semantic": "1. Sentido recuperado",
        "sec_reference": "2. Referencias aclaradas", "sec_qc": "3. Correcciones de calidad",
        "none": "Nada encontrado", "changed": "Párrafos modificados: {n} (#{ids})",
        "not_applied": "Sin aplicar: {n} — ", "appendix": "Anexo · Sin aplicar",
        "total": "Total de correcciones: {n}",
        "step_narrate": "Versión narrada", "step_redact": "Transcripción anonimizada",
        "redact_title": "Informe de control de anonimización", "f_in": "Entrada", "f_out": "Salida",
        "f_keep": "Lista de conservación", "f_scan": "Análisis",
        "provided": "aportada", "not_provided": "no aportada (solo detección automática)",
        "sec_records": "1. Pasajes anonimizados", "empty": "(ninguno)",
        "sec_result": "2. Resultado del control", "sub_mech": "Control mecánico (programa)",
        "sub_model": "Control semántico (modelo)", "not_produced": "(no generado)",
        "th_pos": "Lugar", "th_orig": "Original", "th_after": "Anonimizado", "th_count": "Veces",
        "line": "línea {n}", "count": " ({n})",
        "sec_dropped": "Descartados por la salvaguarda",
        "sec_kept": "Revisados y conservados tal cual, con motivo",
        "kinds": ["Nombres de empresas", "Los nombres de personas", "Datos de contacto", "Lugares",
                  "Cifras y tamaños", "Nombres de proyectos", "Otros"],
    },
    "it": {
        "issues_title": "Passaggio al racconto · Elenco dei punti", "sec_semantic": "1. Significato recuperato",
        "sec_reference": "2. Riferimenti chiariti", "sec_qc": "3. Correzioni di qualità",
        "none": "Nulla trovato", "changed": "Paragrafi modificati: {n} (#{ids})",
        "not_applied": "Non applicati: {n} — ", "appendix": "Appendice · Non applicati",
        "total": "Totale correzioni: {n}",
        "step_narrate": "Versione narrata", "step_redact": "Trascrizione anonimizzata",
        "redact_title": "Rapporto di controllo dell'anonimizzazione", "f_in": "Ingresso", "f_out": "Uscita",
        "f_keep": "Elenco da conservare", "f_scan": "Scansione",
        "provided": "fornito", "not_provided": "non fornito (solo rilevamento automatico)",
        "sec_records": "1. Passaggi anonimizzati", "empty": "(nessuno)",
        "sec_result": "2. Esito del controllo", "sub_mech": "Controllo meccanico (programma)",
        "sub_model": "Controllo semantico (modello)", "not_produced": "(non prodotto)",
        "th_pos": "Punto", "th_orig": "Originale", "th_after": "Anonimizzato", "th_count": "Volte",
        "line": "riga {n}", "count": " ({n})",
        "sec_dropped": "Scartati dal controllo di sicurezza",
        "sec_kept": "Esaminati e mantenuti invariati, con motivazione",
        "kinds": ["Nomi di aziende", "I nomi di persona", "Contatti", "Luoghi",
                  "Numeri e dimensioni", "Nomi di progetti", "Altro"],
    },
    "pt": {
        "issues_title": "Passagem a relato · Lista de pontos", "sec_semantic": "1. Sentido recuperado",
        "sec_reference": "2. Referências clarificadas", "sec_qc": "3. Correções de qualidade",
        "none": "Nada encontrado", "changed": "Parágrafos alterados: {n} (#{ids})",
        "not_applied": "Não aplicados: {n} — ", "appendix": "Anexo · Não aplicados",
        "total": "Total de correções: {n}",
        "step_narrate": "Versão narrada", "step_redact": "Transcrição anonimizada",
        "redact_title": "Relatório de verificação da anonimização", "f_in": "Entrada", "f_out": "Saída",
        "f_keep": "Lista a manter", "f_scan": "Análise",
        "provided": "fornecida", "not_provided": "não fornecida (apenas deteção automática)",
        "sec_records": "1. Passagens anonimizadas", "empty": "(nenhuma)",
        "sec_result": "2. Resultado da verificação", "sub_mech": "Verificação mecânica (programa)",
        "sub_model": "Verificação semântica (modelo)", "not_produced": "(não produzido)",
        "th_pos": "Local", "th_orig": "Original", "th_after": "Anonimizado", "th_count": "Vezes",
        "line": "linha {n}", "count": " ({n})",
        "sec_dropped": "Descartados pela salvaguarda",
        "sec_kept": "Revistos e mantidos tal como estão, com justificação",
        "kinds": ["Nomes de empresas", "Os nomes de pessoas", "Contactos", "Locais",
                  "Números e dimensões", "Nomes de projetos", "Outros"],
    },
    "ja": {
        "issues_title": "語り変換 · 問題リスト", "sec_semantic": "1. 意味の復元",
        "sec_reference": "2. 指示語の明確化", "sec_qc": "3. 品質の修正",
        "none": "該当なし", "changed": "変更した段落：{n}（#{ids}）",
        "not_applied": "未適用：{n} 件 — ", "appendix": "付録 · 未適用",
        "total": "修正 計 {n} 件",
        "step_narrate": "語り原稿", "step_redact": "匿名化した文字起こし",
        "redact_title": "匿名化チェック報告", "f_in": "入力", "f_out": "出力",
        "f_keep": "保持リスト", "f_scan": "走査",
        "provided": "あり", "not_provided": "なし（自動判定のみ）",
        "sec_records": "1. 匿名化した箇所", "empty": "（なし）",
        "sec_result": "2. チェック結果", "sub_mech": "機械チェック（プログラム）",
        "sub_model": "意味チェック（モデル）", "not_produced": "（未生成）",
        "th_pos": "位置", "th_orig": "原文", "th_after": "匿名化後", "th_count": "件数",
        "line": "{n} 行目", "count": "（{n} 件）",
        "sec_dropped": "安全確認で見送った項目",
        "sec_kept": "確認のうえ匿名化しなかった項目と理由",
        "kinds": ["会社名", "人名", "連絡先", "地名", "数字・規模", "プロジェクト名", "その他"],
    },
}


def report(ui_lang: str | None) -> dict:
    """降级路报告的文案。未放量的界面语言回落英文，不回落中文。"""
    return _R.get((ui_lang or "en").split("-")[0].strip().lower(), _R["en"])


def fix_count_line(n: int) -> str:
    """机读那一行。跟给人看的「共修复 N 处」分开——翻译不该把计数弄丢。"""
    return f"<!-- {FIX_COUNT_MARK}: {n} -->"

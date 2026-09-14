// 本地版现在能转写的录音语言（本地独有文件）。
//
// 上传页照线上列出全部 27 门，不在这里的标灰、点不了（Duner 2026-09-14 定）。
// 以后接上哪门语言的开源引擎，就把它加进来，上传页那一格随之点亮。
export const LOCAL_LANGS: ReadonlySet<string> = new Set(["zh"]);

/** 上传页默认选中的语言。线上默认英语；本地默认必须是能转的那一门，否则一进来就选中一格灰的。 */
export const DEFAULT_LOCAL_LANG = "zh";

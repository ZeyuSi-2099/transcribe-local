import { useL } from "./i18n";

/**
 * 后端报错的文案（8 门）。**后端只给码，文案在这里出。**
 *
 * 为什么不继续用「后端写中文、前端查表翻译」（`HistoryPage` 的 ERROR_EN 那种）：
 * 那张表的**键是中文散文**，后端改一个标点就悄悄失配，德语用户会看到一句中文，
 * 而且不报错、测试也不红。码是稳定标识符，前后端对不上时有守卫会红
 * （`server/tests/test_user_errors_parity.py` 读本文件逐条比对）。
 *
 * ⚠️ 加一条要两边一起加：`server/app/user_errors.py` 的 MESSAGES 与这里的 TEXT。
 * ⚠️ 认不出的码**回落后端给的中文 msg**，不回落成一句「出错了」——
 *    前后端部署不同步时（Vercel 与 Render 各走各的），说清楚是什么错比语言正确更要紧。
 */
export const USER_ERROR_CODES = [
  "email_disposable", "mail_unavailable",
  "confirm_email_mismatch",
  "upload_too_large", "audio_duration_unreadable", "balance_low", "topup_closed",
  "job_not_found", "job_not_failed", "audio_expired",
  "glossary_name_taken", "glossary_limit", "glossary_invalid",
  "outline_too_large", "outline_no_text", "outline_too_long",
  "outline_bad_docx", "outline_unreadable", "outline_not_docx",
  "assist_unavailable", "check_unavailable",
  "pp_job_not_done", "pp_in_flight",
] as const;

export type UserErrorCode = (typeof USER_ERROR_CODES)[number];

export interface ApiError extends Error {
  status?: number;
  code?: string;
  params?: Record<string, string | number>;
}

/** 把一个失败响应做成带 code 的 Error。detail 有三种形态都要认：
 *  ① `{code,msg,params}`（现在的后端）② 一句字符串（还没改造的路由）③ 没有 detail。 */
export async function apiError(r: Response, fallback: string): Promise<ApiError> {
  const d = await r.json().then((j) => j?.detail).catch(() => null);
  if (d && typeof d === "object" && typeof d.code === "string") {
    return Object.assign(new Error(String(d.msg || d.code)), {
      status: r.status, code: d.code as string,
      params: (d.params ?? {}) as Record<string, string | number>,
    });
  }
  return Object.assign(new Error(typeof d === "string" && d ? d : fallback), { status: r.status });
}

/** 错误 → 界面语言的一句话。在组件里用。 */
export function useErrText() {
  const L = useL();
  const TEXT: Record<string, string> = {
    email_disposable: L("一次性邮箱无法注册，请使用工作或常用邮箱",
      "Disposable email addresses aren't accepted — please use a work or regular mailbox"),
    mail_unavailable: L("邮件服务暂时不可用，请稍后再试",
      "Mail delivery is unavailable right now — please try again shortly"),
    confirm_email_mismatch: L("请输入与账户一致的邮箱以确认",
      "Type the email address on this account to confirm"),
    audio_duration_unreadable: L("无法识别音频时长，请检查文件是否损坏",
      "We couldn't read the length of this audio — check whether the file is damaged"),
    balance_low: L("余额不足，请充值", "Not enough balance — please top up."),
    topup_closed: L("充值即将开放，暂不可用", "Top-ups aren't open yet"),
    job_not_found: L("任务不存在", "That job doesn't exist"),
    job_not_failed: L("只有失败的任务可以重试", "Only failed jobs can be retried."),
    audio_expired: L("录音已超过保留期，无法重试；请重新上传",
      "The recording is past its retention window — please upload it again."),
    glossary_name_taken: L("已经有一本同名的术语库了", "You already have a glossary with that name"),
    glossary_limit: L("术语库数量已达上限", "You've reached the limit on glossaries"),
    glossary_invalid: L("这本库有一处不符合格式，请检查后再存",
      "Something in this glossary doesn't fit the format — please check it before saving"),
    outline_no_text: L("这个文件里没有可读的文字", "There's no readable text in this file"),
    outline_too_long: L("大纲内容过长", "That outline is too long"),
    outline_bad_docx: L("这个文件打不开，可能不是有效的 .docx",
      "This file won't open — it may not be a valid .docx"),
    outline_unreadable: L("这个文件的内容读不出来", "We couldn't read the contents of this file"),
    outline_not_docx: L("只支持 .docx 文件", "Only .docx files are supported"),
    assist_unavailable: L("起草服务暂时不可用，请稍后再试",
      "Drafting is unavailable right now — please try again shortly"),
    check_unavailable: L("检查服务暂时不可用，请稍后再试",
      "The check is unavailable right now — please try again shortly"),
    pp_job_not_done: L("转录尚未完成，不能发起后处理",
      "This transcript isn't finished yet, so it can't be processed further"),
    pp_in_flight: L("该转录已有进行中的后处理", "This transcript already has processing under way"),
  };
  return (e: unknown, fallback?: string): string => {
    const err = e as ApiError | undefined;
    const code = err?.code;
    // 带数字的两条走 L.t：拼中文串的话，那个数字就永远卡在中文语序里
    if (code === "upload_too_large")
      return L.t("文件过大（上限 {0} MB）", "File is too large (max {0} MB)", err?.params?.n ?? "");
    if (code === "outline_too_large")
      return L.t("文件过大（大纲上限 {0} MB）", "File is too large (outlines are capped at {0} MB)",
        err?.params?.n ?? "");
    if (code && TEXT[code]) return TEXT[code];
    return (e instanceof Error ? e.message : String(e ?? "")) || fallback || "";
  };
}

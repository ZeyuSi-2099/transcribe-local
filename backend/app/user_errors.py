"""面向**普通用户**的报错：后端只给码，文案由前端按界面语言出。

规则（Duner 2026-08-30 定）：我们写的字跟界面语言走（8 门）。后端此前一律写死中文，
经前端 `ERROR_EN` 这张「中文 → 英文」映射表转一道、再由对照本翻成其余六门——
**键是中文散文**，后端改一个标点，映射就悄悄失配、德语用户看到一句中文，且不报错。

所以这里换成码：
  · `code` 是稳定标识符，前端 `lib/userErrors.ts` 按码出文案（走 `L()` → 对照本 → 8 门）
  · `msg` 是中文原话，只作**兜底与留痕**：日志、管理页、以及前端认不出这个码时的显示
  · `params` 给带数字的那几条（上限多少 MB）——前端拿它填占位符，不拼中文串

⚠️ **只收「普通用户会看到」的那些**。管理页的报错（运营内部页，不翻）、
   参数形状校验（`name 需为字符串` 这类，正确的前端永远触发不到）都不该进来——
   进来只会让这张表长成一本没人维护的字典，而真正该翻的几条淹在里面。

⚠️ 引擎的原始报错（`起草服务暂时不可用：<httpx 的一串英文>`）**不再拼进用户看到的那句**：
   它翻不了、对用户也没意义，留在服务端日志里就够了。
"""
from __future__ import annotations

from fastapi import HTTPException

# code → 中文兜底文案。带 {n} 的由 params 填。
# ⚠️ 加一条就要同步加进 `src/lib/userErrors.ts`，否则那门语言只会看到这里的中文。
#    守卫 `tests/test_user_errors_parity.py` 读前端文件逐条比对（同价目表 / 说话人标签的做法）。
MESSAGES: dict[str, str] = {
    # 登录
    "email_disposable": "一次性邮箱无法注册，请使用工作或常用邮箱",
    "mail_unavailable": "邮件服务暂时不可用，请稍后再试",
    # 账户
    "confirm_email_mismatch": "请输入与账户一致的邮箱以确认",
    # 上传与转录
    "upload_too_large": "文件过大（上限 {n} MB）",
    "audio_duration_unreadable": "无法识别音频时长，请检查文件是否损坏",
    "balance_low": "余额不足，请充值",
    "topup_closed": "充值即将开放，暂不可用",
    "job_not_found": "任务不存在",
    "job_not_failed": "只有失败的任务可以重试",
    "audio_expired": "录音已超过保留期，无法重试；请重新上传",
    # 术语库
    "glossary_name_taken": "已经有一本同名的术语库了",
    "glossary_limit": "术语库数量已达上限",
    "glossary_invalid": "这本库有一处不符合格式，请检查后再存",
    # 大纲上传与助手
    "outline_too_large": "文件过大（大纲上限 {n} MB）",
    "outline_no_text": "这个文件里没有可读的文字",
    "outline_too_long": "大纲内容过长",
    "outline_bad_docx": "这个文件打不开，可能不是有效的 .docx",
    "outline_unreadable": "这个文件的内容读不出来",
    "outline_not_docx": "只支持 .docx 文件",
    "assist_unavailable": "起草服务暂时不可用，请稍后再试",
    "check_unavailable": "检查服务暂时不可用，请稍后再试",
    # 后处理
    "pp_job_not_done": "转录尚未完成，不能发起后处理",
    "pp_in_flight": "该转录已有进行中的后处理",
}


class UserError(HTTPException):
    """detail = {"code", "msg", "params"}。

    ⚠️ **detail 从字符串变成了对象**，所以前端每一处 `throw new Error(detail)` 都要改走
    `lib/apiError.ts`——不改的话界面上会显示 `[object Object]`。改动前那种「后端返一句中文、
    前端原样贴出来」的写法到此为止。
    """

    def __init__(self, status_code: int, code: str, **params):
        assert code in MESSAGES, f"未登记的错误码：{code}"
        msg = MESSAGES[code].format(**params) if params else MESSAGES[code]
        super().__init__(status_code, {"code": code, "msg": msg, "params": params})

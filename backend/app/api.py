"""FastAPI 应用：上传、查询、取结果、放音频、认证桩。

`jobstore`/`blobstore` 为模块级名字，测试可 monkeypatch 成假实现。
"""
import hashlib
import ipaddress
import json
import math
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote

from datetime import datetime, timedelta, timezone

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from .user_errors import UserError
from . import (account_delete, accounts, alerts, balances, bidi, blobstore, claude_gate, config, db, export,
               fly_machines, freebies, glossary, glossary_assist, jobstore, node_events, p3_health,
               outline_file, payments, postprocess, pricing, projects, redact_diff,
               speaker_labels)
from . import admin_users as admin_users_mod   # 别名：路由函数已叫 admin_users，同名会互相盖掉

# 允许上传的扩展名：音频直接转录，视频由 P0(ffmpeg) 抽音频。服务端兜底校验（前端 picker 之外再防一层）。
_ALLOWED_UPLOAD_EXT = {
    ".wav", ".mp3", ".m4a", ".aac", ".opus", ".flac", ".ogg", ".amr",   # 音频
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",                    # 视频
}


def _probe_duration_sec(path: str) -> float | None:
    """ffprobe 服务端实测时长（秒，浮点）——不再只信前端 Form 值。损坏文件/非音视频/
    超时一律返回 None（不编造），调用方转 400。与 pipeline 各引擎 get_audio_duration 同法。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001  损坏/非法输入/超时都不炸，交给上层判 400
        return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Render 免费层只能起一个服务 → 设 RUN_WORKER_IN_API=1 让 api 进程内顺带跑 worker 线程。
    # 本地 docker 有独立 worker 服务，不设此变量。延迟 import 避免本地 api 加载重型 pipeline。
    stop = None
    # 三种形态：① FLY_DISPATCH=1（Render 前台）→ 只起看门狗+派单，转录在 Fly 任务机器上跑；
    # ② RUN_WORKER_IN_API=1（免费层合一）→ 进程内起 worker 处理池；③ 都不设 → 纯 API。
    if config.FLY_DISPATCH:
        try:
            from . import worker
            _, stop = worker.start_in_thread(with_workers=False)
            print("Fly 派单模式：api 进程起看门狗+派单线程（转录在 Fly 机器上跑）", flush=True)
        except Exception as e:  # noqa: BLE001  起不来也保住 API + /healthz
            import traceback as _tb
            try:
                import sentry_sdk
                sentry_sdk.capture_exception()
            except Exception:
                pass
            print(f"派单线程启动失败，降级为纯 API：{e}\n{_tb.format_exc()[-300:]}", flush=True)
    elif os.environ.get("RUN_WORKER_IN_API"):
        try:
            from . import worker
            _, stop = worker.start_in_thread()
            print("api 进程内已启动 worker 线程（RUN_WORKER_IN_API）", flush=True)
        except Exception as e:  # noqa: BLE001  worker 起不来也要保住 API + /healthz，不整体崩
            import traceback as _tb
            try:
                import sentry_sdk
                sentry_sdk.capture_exception()
            except Exception:
                pass
            print(f"worker 线程启动失败，降级为纯 API：{e}\n{_tb.format_exc()[-300:]}", flush=True)
    yield
    if stop is not None:
        stop.set()


def _init_sentry() -> None:
    # 错误监控：仅当配了 SENTRY_DSN 才启用（未配=静默，本地/测试无副作用）。
    # 在 app 创建前 init，便于 sentry-sdk 自动插桩 FastAPI。
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENV", "production"),
            traces_sample_rate=0.1,
            send_default_pii=False,  # 访谈含 PII，不自动带 IP/cookie/body
        )
        print("Sentry 已启用", flush=True)
    except Exception as e:  # noqa: BLE001  监控接不上不该拖垮服务
        print(f"Sentry 初始化失败（跳过）: {e}", flush=True)


_init_sentry()

app = FastAPI(title="Transcribe API", lifespan=lifespan)


@app.middleware("http")
async def _limit_upload_size(request: Request, call_next):
    """上传体积第一道闸（真护盘的那道）：`UploadFile = File(...)` 会让 Starlette 在进
    handler 之前就把整个 multipart spool 到临时盘，handler 里的 Content-Length"预检"
    实为马后炮。只有在 ASGI 层、消费 body 之前看头拒掉，磁盘才真的一字节不写。
    Content-Length 缺失/伪造（chunked 等）仍靠 handler 的流式计数兜底。"""
    if request.method == "POST" and request.url.path == "/api/jobs":
        cl = request.headers.get("content-length", "")
        if cl.isdigit() and int(cl) > config.MAX_UPLOAD_BYTES:
            mb = config.MAX_UPLOAD_BYTES // (1024 * 1024)
            return JSONResponse({"detail": f"文件过大：上传体积上限 {mb}MB"}, status_code=413)
    return await call_next(request)


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    """存活信号（不鉴权、不碰 DB）。收 GET + HEAD——UptimeRobot 默认用 HEAD 探活，
    只声明 GET 会回 405 被误判 down。"""
    return {"ok": True}


# 已下架公开页的墓碑（2026-08-18 起服务 /quote 与 /{lang}/quote，见 vercel.json）。
#
# 为什么这条要绕到后端来：**410 只能由服务端给**，而 Vercel 的 `vercel.json` 里
# 能返回自定义状态码的只有 `routes`，它与我们已经在用的 `rewrites`/`redirects`
# **互斥**（同时出现会构建失败）。前端又是纯静态站、零 serverless function。
# 而 `/api/(.*)` → Render 这条 rewrite 早就在了，借它最省事、也不新增部署面。
#
# 为什么是 410 不是 404：这 8 个网址（报价器八门）已经提交给 GSC 与 Bing，
# 404 会让引擎按"可能是临时故障"反复回来重试几个月；410 是"永久没了"，移除得快得多。
# 也不做 301 到 /pricing：那是"搬家了"，而报价器不是搬家，是**撤掉**——
# 骗引擎说内容在别处，只会让 /pricing 收到一堆不匹配的历史信号。
@app.api_route("/api/gone", methods=["GET", "HEAD"])
def gone():
    body = (
        "<!doctype html><meta charset=utf-8><title>Gone</title>"
        "<p>This page has been removed."
        ' <a href="https://transcribe.solutions/pricing">See pricing</a>.</p>'
    )
    return Response(content=body, status_code=410, media_type="text/html; charset=utf-8")


def _disposition(filename: str) -> dict[str, str]:
    """让浏览器把响应当附件下载并用 filename 命名。

    文件名由响应头下发，前端无需依赖（在部分浏览器对 blob 不生效的）<a download>。
    中文名走 RFC5987 的 filename*，并附 ASCII 回退。
    """
    ascii_name = filename.encode("ascii", "ignore").decode()
    # 去掉会破坏/注入 Content-Disposition 头的字符（控制符如 CR/LF、引号、反斜杠）；
    # name 是用户可控的查询参数，不消毒会让下载文件名乱掉甚至 header 注入。
    ascii_name = re.sub(r'[\x00-\x1f"\\]', "", ascii_name).strip()
    if not ascii_name or ascii_name.startswith("."):   # 全中文名剔完只剩 ".docx" 之类的残名
        ascii_name = f"transcript{ascii_name or ''}"
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"}


def _segments_to_txt(segments: list[dict], lang: str = "", ui: str = "zh") -> str:
    """说话人标签按界面语言（中文界面用中文，其余英文）；RTL 语种每行圈进方向隔离区。

    ⚠️ 分隔符跟着标签走：中文标签配全角「：」，英文标签配半角「: 」——
    英文名后面挂一个全角冒号，在任何语言下都是错的排版。"""
    is_rtl = bidi.is_rtl(lang)
    sep = speaker_labels.speaker_sep(ui)
    lines = []
    for seg in segments:
        sp = speaker_labels.speaker_label(seg.get("sp"), ui)
        line = f"{sp}{sep}{seg.get('s', '')}" if sp else seg.get("s", "")
        lines.append(bidi.isolate(line) if is_rtl else line)
    return "\n\n".join(lines)


SESSION_COOKIE = "tx_session"


def _session_token(request: Request) -> str | None:
    """取会话凭证：优先 Authorization 头（前端 fetch），否则 Cookie。

    Cookie 是给浏览器自发请求用的——<audio> 标签和导出下载链接带不了
    Authorization 头，登录时种的 HttpOnly Cookie 让它们也能过鉴权。
    """
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:]
    return request.cookies.get(SESSION_COOKIE)


def _current_email(request: Request) -> str:
    """会话校验：无效/过期 → 401。"""
    token = _session_token(request)
    email = accounts.session_email(token) if token else None
    if not email:
        raise HTTPException(status_code=401, detail="not signed in")
    return email


def _owned_job(job_id: str, request: Request):
    """登录 + 归属校验：别人的任务返回 404（不暴露存在性）。

    user_email 为空的旧任务（P1 之前的本地遗留）放行给任意登录用户。
    """
    email = _current_email(request)
    job = jobstore.get_job(job_id)
    if job is None or (job.user_email and job.user_email != email):
        raise HTTPException(status_code=404, detail="job not found")
    return job


def _admin_email(request: Request) -> str:
    """管理员校验：登录 + 邮箱在 ADMIN_EMAILS 白名单。未登录或非管理员一律 404
    （不暴露运营后台存在性，与 _owned_job 同口径）。"""
    token = _session_token(request)
    email = accounts.session_email(token) if token else None
    if not email or email.lower() not in config.ADMIN_EMAILS:
        raise HTTPException(status_code=404, detail="not found")
    return email


@app.get("/api/admin/overview")
def admin_overview(request: Request):
    """运营驾驶舱总览（仅管理员）：进行中任务 + 最近失败 + 当日汇总。

    带上 queuedMaxHours 是为了让前端能画「排了多久、离判死还有多久」——那段窗口是唯一
    能干预的时间，此前界面上一个字都没有。阈值只有后端知道，前端自己写死就会跟着漂。"""
    _admin_email(request)
    d = jobstore.admin_overview()
    d["queuedMaxHours"] = config.QUEUED_MAX_HOURS
    return d


@app.get("/api/admin/balances")
def admin_balances(request: Request):
    """各服务商余额（仅管理员）。"""
    _admin_email(request)
    return {"balances": balances.list_balances()}


class SetBalancePayload(BaseModel):
    vendor: str | None = None
    label: str | None = None
    amountCny: float | None = Field(None, ge=0)
    thresholdCny: float | None = Field(None, ge=0)
    note: str | None = None


@app.put("/api/admin/balances")
async def admin_set_balance(request: Request):
    """手动登记/更新某服务商余额与低水位阈值（仅管理员）。None 字段保留原值。
    amountCny/thresholdCny 校验为 >=0 的数字，非法值 422（负数/非数字静默入库
    会致驾驶舱显负余额、低水位判断乱）。鉴权须在 body 校验之前——先 404 后 422，
    避免未登录探测者靠字段报错确认该运营端点存在。body 必须自己读（不用
    `payload: dict = Body(...)`）：FastAPI 的参数校验发生在进 handler 之前，
    非 dict/坏 JSON 的匿名请求会先拿 422，端点存在性照样暴露。"""
    _admin_email(request)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("body 需为对象")
        p = SetBalancePayload(**payload)
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="invalid balance")
    vendor = (p.vendor or "").strip()
    if not vendor:
        raise HTTPException(status_code=422, detail="vendor 必填")
    balances.set_balance(
        vendor, label=p.label, amount_cny=p.amountCny,
        threshold_cny=p.thresholdCny, note=p.note,
    )
    return {"ok": True}


@app.get("/api/admin/p3-health")
def admin_p3_health(request: Request, hours: int = 24):
    """P3 引擎（Claude 无头）健康度（仅管理员）：近 N 小时成败统计 + 当前撞顶冷却 + 在飞并发槽。

    数据来自真实生产调用的埋点（p3_events），所以**没有任务跑的时段数字不会变**——
    这是被动统计的固有代价，要看此刻的状态请用 /p3-probe 探活。
    hours 夹到 1..168：小于 1 分母恒为 0，大于一周对「现在健不健康」已无参考意义。"""
    _admin_email(request)
    data = p3_health.health(hours=max(1, min(hours, 168)))
    try:
        data["activeSlots"] = claude_gate.active_count()
    except Exception:  # noqa: BLE001  查不到在飞数不该让整张健康卡打不开
        data["activeSlots"] = None
    data["slotLimit"] = config.CLAUDE_MAX_CONCURRENCY
    return data


@app.get("/api/admin/workflow")
def admin_workflow(request: Request):
    """工作流 Tag 的静态配置视图（仅管理员）：八个节点的执行体 / 参数 / 降级路 + 语种编排。

    **纯只读、纯事实**——文案在前端。取不到某一块就缺那一块（见 workflow_view）。
    ⚠️ 提示词指纹是**派单前台这一份**的，不是 Fly 上跑的那一份，字段 promptScope 明写了。"""
    _admin_email(request)
    from . import workflow_view
    return workflow_view.snapshot()


@app.get("/api/admin/workflow/prompt")
def admin_workflow_prompt(request: Request, id: str = ""):
    """取一份提示词/规则书全文（只读）。未知 id → 404，不回空字符串
    （空文本会被当成「这份提示词是空的」，那是个比 404 难查得多的假象）。"""
    _admin_email(request)
    from . import workflow_view
    d = workflow_view.prompt_full(id)
    if d is None:
        raise HTTPException(status_code=404, detail="unknown prompt id")
    return d


@app.get("/api/admin/health")
def admin_health(request: Request, hours: int = 24):
    """节点健康度（仅管理员）：各节点近 N 小时的成败/耗时 + 全局运行信号。

    **大头是聚合、不是埋点**——P1 各引擎、P3 档位、后处理每步都从既有数据查出来，所以这张表
    上线当天就有历史。hours 夹到 1..168，与 p3-health 同口径（小于 1 分母恒为 0，
    大于一周对「现在健不健康」没有参考意义）。"""
    _admin_email(request)
    from . import health_view
    return health_view.snapshot(hours=max(1, min(hours, 168)))


@app.get("/api/admin/expiries")
def admin_expiries(request: Request):
    """会到期的凭证清单（仅管理员）。手工维护，唯一来源见 app/expiries.py。"""
    _admin_email(request)
    from . import expiries
    return {"items": expiries.list_expiries(), "warnDays": expiries.WARN_DAYS}


@app.get("/api/admin/alerts")
def admin_alerts(request: Request, days: int = 7, unhandled: int = 0):
    """告警记录（仅管理员）。此前告警只发一封邮件、发完即忘，漏看一次就永远查不到。"""
    _admin_email(request)
    return {"items": alerts.list_alerts(days=max(1, min(days, alerts.RETENTION_DAYS)),
                                        unhandled_only=bool(unhandled)),
            "retentionDays": alerts.RETENTION_DAYS}


@app.get("/api/admin/growth")
def admin_growth(request: Request, week: str = "", tests: str = ""):
    """增长漏斗（仅管理员）：十个数各三列 + 钱的总账 + 免费发放序列。**返回里没有邮箱**。
    week 形如 2026-W36，缺省本周。格式不对回 422，别静默当成本周。"""
    _admin_email(request)
    from . import growth
    if week:
        try:
            growth.parse_week(week)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="week 需形如 2026-W36")
    s = growth.snapshot(week=week or None, include_tests=tests == "1")
    return {**s, "report": growth.render_report(s)}


@app.post("/api/admin/growth/report")
def admin_growth_report(request: Request):
    """驾驶舱「现在发一份增长周报」：立刻把上一周那份发到 ADMIN_EMAILS，验收不用等周一。
    走告警表 fyi 档，所以发完在「告警」列表里也看得到。"""
    _admin_email(request)
    from . import growth
    return growth.send_report(manual=True)


@app.post("/api/admin/alerts/{alert_id}/handled")
def admin_alert_handled(alert_id: int, request: Request):
    """标记一条告警已处理。**只有 act 档能标**——其余两档自己会消失，多点一次是白费动作。
    已标过的返回 changed=false（不翻案、不改人名），前端据此不必重复提示。"""
    email = _admin_email(request)
    return {"changed": alerts.mark_handled(alert_id, email)}


@app.get("/api/admin/users")
def admin_users(request: Request, q: str = "", limit: int = admin_users_mod.LIST_LIMIT):
    """用户列表（仅管理员）。**不含文件名**——那只在下钻里给，见 admin_users.user_detail。"""
    _admin_email(request)
    return {"items": admin_users_mod.list_users(q, limit),
            "adjustMaxCents": config.ADJUST_MAX_CENTS}


@app.get("/api/admin/users/{email}")
def admin_user_detail(email: str, request: Request):
    """单用户下钻：账目 + 任务 + 计数。查无此人 404（与其余 admin 端点同口径）。"""
    _admin_email(request)
    d = admin_users_mod.user_detail(email)
    if d is None:
        raise HTTPException(status_code=404, detail="not found")
    return d


@app.post("/api/admin/users/{email}/adjust")
async def admin_user_adjust(email: str, request: Request):
    """手工调整余额（补偿 / 纠错）。理由必填、单次有上限、不许扣成负数——
    三道闸都在 admin_users.adjust_balance 的同一个事务里，被拒时原样把话给人看。"""
    operator = _admin_email(request)
    body = await request.json()
    try:
        delta = int(body.get("deltaCents") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="调整金额要是整数分")
    try:
        return admin_users_mod.adjust_balance(email, delta, body.get("reason") or "", operator)
    except admin_users_mod.AdjustRejected as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/admin/users/{email}/reset")
async def admin_user_reset(email: str, request: Request):
    """把一个邮箱重置成「从没注册过」（测试用，2026-09-03）。要把邮箱原样打一遍确认——
    与用户侧注销同一套防误删机制。管理员账号不许；任务在跑不许。"""
    operator = _admin_email(request)
    body = await request.json()
    if (body.get("confirm") or "").strip().lower() != email.strip().lower():
        raise HTTPException(status_code=422, detail="请把要重置的邮箱原样打一遍。")
    from . import test_accounts
    try:
        return test_accounts.reset_account(email, operator)
    except test_accounts.TestAccountRejected as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/admin/users/{email}/simulate-usage")
async def admin_user_simulate_usage(email: str, request: Request):
    """给测试号记一笔模拟消耗（先扣免费分钟、再按单价扣余额；只写账本与额度，不造任务）。"""
    operator = _admin_email(request)
    body = await request.json()
    from . import test_accounts
    try:
        return test_accounts.simulate_usage(email, int(body.get("minutes") or 0), operator)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="分钟数要是整数")
    except test_accounts.TestAccountRejected as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/admin/p3-config")
def admin_p3_config(request: Request):
    """P3 运行时配置 + 各档并发占用（供运营舱实时显示「设成这样会占账号上限几成」）。"""
    _admin_email(request)
    from . import p3_config
    cfg = p3_config.get()
    return {
        "config": {**cfg,
                   "forceExpiresAt": cfg["force_expires_at"].isoformat()
                   if cfg.get("force_expires_at") else None,
                   "updatedAt": cfg["updated_at"].isoformat() if cfg.get("updated_at") else None},
        "limits": p3_config.LIMITS,
        "perMachineConc": p3_config.PER_MACHINE_CONC,
        "forceMachines": p3_config.FORCE_MACHINES,
        # 每档「按当前设置会占账号上限几成」——让人在保存前就看见后果，
        # 而不是保存完等着撞 429（撞墙不报错，只表现为退避重试导致的莫名变慢）
        "usage": {e: p3_config.usage_of(e, n) for e, n in
                  (("claude", cfg["claude_concurrency"]),
                   ("flash", p3_config.FORCE_MACHINES["flash"]))},
        "effectiveMaxMachines": p3_config.effective_max_machines(),
    }


@app.put("/api/admin/p3-config")
def admin_p3_config_save(request: Request, body: dict = Body(...)):
    """保存 P3 配置（仅管理员）。校验不过返回 400 并说清哪一项、为什么。

    强制引擎带 forceHours（默认 4）→ 换算成到期时刻存库。**必须有到期时间**：
    强制引擎是测试态，忘了关就是所有真实订单跟着受影响。4 小时够跑 8-16 单又不跨夜。"""
    email = _admin_email(request)
    from . import p3_config
    patch: dict = {}
    for k, col in (("flyMaxMachines", "fly_max_machines"),
                   ("maxTranscribeJobs", "max_transcribe_jobs"),
                   ("claudeConcurrency", "claude_concurrency")):
        if body.get(k) is not None:
            patch[col] = int(body[k])
    if "forceEngine" in body:
        fe = body.get("forceEngine") or None
        patch["force_engine"] = fe
        patch["force_expires_at"] = (
            datetime.now(timezone.utc) + timedelta(hours=float(body.get("forceHours") or 4))
        ) if fe else None
    try:
        cfg = p3_config.save(patch, by=email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "config": {**cfg,
            "forceExpiresAt": cfg["force_expires_at"].isoformat()
            if cfg.get("force_expires_at") else None,
            "updatedAt": cfg["updated_at"].isoformat() if cfg.get("updated_at") else None}}


@app.post("/api/admin/p3-probe")
def admin_p3_probe(request: Request):
    """手动探活（仅管理员）：起一台 Fly 机器真跑一次最小 `claude -p`，验证订阅此刻通不通。

    **异步**：立刻返回 probeId，机器跑完把结果写进 p3_events，前端轮询 /p3-health 拿。
    同步等不现实——机器 boot + 调用要几十秒，HTTP 早超时了。
    探活占一台机器名额，故池满时让位给真实转录（转录是生意，探活是自查）。"""
    _admin_email(request)
    if not config.FLY_DISPATCH or not config.FLY_API_TOKEN:
        # 令牌只发给 Fly 机器，Render 前台既没 claude CLI 也没订阅令牌 → 非派单态无从探活
        raise HTTPException(status_code=501, detail="未开启派单模式，无法起探活机器")
    try:
        busy = fly_machines.count_running_machines() >= config.FLY_MAX_MACHINES
    except Exception:  # noqa: BLE001  数不出在飞机器就别硬起，避免越过成本闸
        raise HTTPException(status_code=502, detail="查询机器池失败，稍后再试")
    if busy:
        raise HTTPException(status_code=503, detail="机器池已满（转录优先），稍后再探")
    from . import p3_probe   # 局部导入：它依赖 pipeline，派单前台平时用不到
    probe_id = p3_probe.new_probe_id()
    try:
        fly_machines.start_task_machine(probe_id, kind="probe")
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="起探活机器失败")
    return {"probeId": probe_id}


@app.post("/api/admin/balances/refresh")
def admin_refresh_balances(request: Request):
    """手动触发全部服务商余额自动拉取（仅管理员）：调各家只读账单接口写库，缺钥匙者跳过。"""
    _admin_email(request)
    return {"refreshed": balances.refresh_all()}


@app.post("/api/admin/balances/refresh-deepseek")
def admin_refresh_deepseek(request: Request):
    """单拉 DeepSeek 余额（保留；全量用 /balances/refresh）。"""
    _admin_email(request)
    r = balances.refresh_deepseek()
    if r is None:
        raise HTTPException(status_code=502, detail="DeepSeek 余额拉取失败或未配置 key")
    return r


@app.get("/api/admin/topups")
def admin_topups(request: Request, email: str = ""):
    """某用户的充值记录 + 每笔当前可退额（仅管理员）——发起退款前先看这张表。"""
    _admin_email(request)
    target = (email or "").strip().lower()
    if not target:
        raise HTTPException(status_code=422, detail="email 必填")
    return {"topups": accounts.list_topups(target), "balanceCents": accounts.balance_cents(target)}


class RefundPayload(BaseModel):
    email: str
    ledgerId: int
    amountCents: int = Field(gt=0)
    reason: str | None = None


@app.post("/api/admin/refunds")
async def admin_refund(request: Request):
    """发起充值退款（仅管理员）：校验额度 → 调 provider 退款 API。

    **只发起，不动余额**——余额扣减一律等 provider 的退款 webhook 回来走 claim_and_debit。
    否则「退款 API 失败但余额已扣」的不一致会留在库里。provider adapter 未接入时返回 501，
    运营看到的是「没退成」，不会误以为钱已经退出去了。
    鉴权在 body 校验之前（先 404 后 422），与 admin_set_balance 同口径。"""
    _admin_email(request)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("body 需为对象")
        p = RefundPayload(**payload)
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail="invalid refund")
    target = p.email.strip().lower()
    provider = _topup_provider(target, p.ledgerId)
    try:
        refund_id = accounts.create_refund(target, p.ledgerId, p.amountCents, provider)
    except accounts.RefundNotAllowed as e:
        raise HTTPException(
            status_code=422,
            detail=f"超出可退额：本笔最多可退 {e.allowed} 分"
                   f"（14 天窗口内、扣除已消耗与在途冻结、并已减去其它待处理退款）",
        )
    # 占额已落库。**只有确定没退成才作废**——超时/断连/5xx 一律保持 pending：
    # 请求可能已经到达 provider 并执行成功，只是响应回不来；作废掉再收到成功回执，
    # 就成了「钱退了、额度却被释放过」。不确定的单交给看门狗 sweep_stale_refunds 暴露给人。
    try:
        provider_refund_id = payments.refund(
            provider, target, p.amountCents, p.reason or "",
            # 原交易号：Paddle 的退款必须带它。取不到（接入前的老充值）→ RefundNotSupported → 501
            provider_txn_id=accounts.topup_provider_txn_id(target, p.ledgerId),
        )
    except payments.RefundNotSupported as e:
        accounts.fail_refund(refund_id, "provider adapter 未接入")   # 请求根本没发出去
        raise HTTPException(
            status_code=501,
            detail=(f"这一笔退不了：{e.reason}。请到 {e.provider} 后台手工退这一笔"
                    if e.reason else f"{e.provider} 退款通道未接入，请到该渠道后台手工退款"))
    except payments.RefundRejected as e:
        accounts.fail_refund(refund_id, f"provider 明确拒绝：{e}")    # 确定性失败
        raise HTTPException(status_code=502, detail=f"provider 拒绝受理，额度已释放：{e}")
    except Exception:
        alerts.send_admin_alert(
            "退款发起结果未知，单据保持挂起",
            f"{target} 退款单 #{refund_id}（{p.amountCents} 分，provider={provider}）"
            f"调用 provider 退款 API 时异常，但**不能判定为失败**（可能已受理）。\n"
            f"单据保持 pending、额度继续占住，等 provider 回执落地。\n"
            f"→ 请到 provider 后台确认实际状态。\n{traceback.format_exc()[-600:]}",
            tier="act",
        )
        raise HTTPException(
            status_code=502,
            detail="退款状态未知（provider 未给回执），单据已挂起等回执——请到该渠道后台确认，不要重复发起",
        )
    accounts.set_refund_provider_id(refund_id, provider_refund_id)
    return {"ok": True, "refundId": refund_id, "providerRefundId": provider_refund_id,
            "note": "余额待退款 webhook 回来后按锁定净额扣减"}


def _topup_provider(email: str, ledger_id: int) -> str:
    """这笔充值当初是从哪个通道进来的（账本 source 即通道名）。查不到按 stripe 兜底。"""
    for t in accounts.list_topups(email):
        if t["id"] == ledger_id:
            return t["source"] or "stripe"
    return "stripe"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=30 * 24 * 3600, path="/api",
        httponly=True, samesite="lax",
        secure=bool(os.environ.get("COOKIE_SECURE")),  # 线上 HTTPS 置 COOKIE_SECURE=1
    )


def _client_ip(request: Request):
    """限流/闸④ 用客户端 IP。

    生产链路是 浏览器 → Vercel rewrite 反代 → (Cloudflare) → Render：CF 看到的连接方
    是 Vercel 出口机（2026-08-01 实测：全体真实用户经反代都记成同一个 AWS 出口 IP
    13.52.103.55 → 闸④/发码限流会集体误伤真客户），所以 CF-Connecting-IP 在这条链上
    是代理 IP 不是用户 IP。取序改为：
      1. X-Vercel-Forwarded-For —— Vercel 反代写入的原始客户端 IP；
      2. X-Forwarded-For 从左到右第一个**公网** IP（Vercel 会覆写 XFF 为真实客户端，
         CF 只在链尾追加连接方）；
      3. CF-Connecting-IP（无反代直连时 = 真实客户端，由 CF 写）；
      4. socket 层地址兜底。
    已知取舍：绕开站点直连 Render API 的脚本可以伪造 1/2 两个头换假 IP——闸④ 对
    这类攻击者退化为软约束（闸①②③ 仍在）。根治要把 API 挂到独立子域名让浏览器直连
    CF（见 docs/ROADMAP.md）。所有候选都过 IP 格式校验（非法串会灌爆 login_ip_throttle
    主键），1/2 还要求是公网地址（内网/保留段是链路噪音不是用户）。"""
    def _valid(cand: str, public_only: bool) -> str | None:
        cand = cand.strip()
        if not cand:
            return None
        try:
            addr = ipaddress.ip_address(cand)
        except ValueError:
            return None
        return cand if (addr.is_global or not public_only) else None

    for h in ("x-vercel-forwarded-for",):
        got = _valid(request.headers.get(h, "").split(",")[0], public_only=True)
        if got:
            return got
    for seg in request.headers.get("x-forwarded-for", "").split(","):
        got = _valid(seg, public_only=True)
        if got:
            return got
    got = _valid(request.headers.get("cf-connecting-ip", ""), public_only=False)
    if got:
        return got
    return request.client.host if request.client else None


@app.post("/api/auth/request-code")
def auth_request_code(request: Request, payload: dict = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    if "@" not in email or len(email) > 254:
        raise HTTPException(status_code=422, detail="invalid email")
    # 闸②一次性邮箱：发码前就拒（既拒注册也省 Resend 额度）。清单见 freebies/data
    if freebies.is_disposable(email):
        raise UserError(422, "email_disposable")
    ip = _client_ip(request)
    # 界面语言决定验证码邮件用哪门语言（前端带上；缺省/不认识一律英文）
    lang = str(payload.get("lang") or "en")
    try:
        dev_code = accounts.request_code(email, ip=ip, lang=lang)
    except accounts.RateLimited as e:
        if e.kind == "cooldown":
            msg = "请求过于频繁，请稍后再试"
        elif e.kind == "ip":
            msg = "该网络请求验证码过于频繁，请稍后再试"
        else:
            msg = "今日验证码请求已达上限，请明天再试"
        raise HTTPException(status_code=429, detail=msg, headers={"Retry-After": str(e.retry_after)}) from e
    except Exception as e:  # noqa: BLE001  邮件服务故障
        # detail 不拼内部异常串（供应商报错/密钥线索，未鉴权可见）；细节只进服务端日志
        print(f"发信失败 email={email}: {e}", flush=True)
        raise UserError(502, "mail_unavailable") from e
    # 开发模式（未配邮件服务）把验证码直接返回，前端自动填入
    return {"ok": True, **({"devCode": dev_code} if dev_code else {})}


@app.post("/api/auth/verify")
def auth_verify(request: Request, response: Response, payload: dict = Body(...)):
    email = (payload.get("email") or "").strip().lower()
    code = (payload.get("code") or "").strip()
    # source = 前端第一次落地时存下的注册来源（只在新建用户那次落库）
    token = accounts.verify_code(email, code, ip=_client_ip(request), source=payload.get("source"))
    if not token:
        raise HTTPException(status_code=401, detail="invalid or expired code")
    _set_session_cookie(response, token)
    return {"token": token, "email": email, "balanceCents": accounts.balance_cents(email),
            "isAdmin": email.lower() in config.ADMIN_EMAILS}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = _session_token(request)
    if token:
        accounts.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/api")
    return {"ok": True}


@app.get("/api/me")
def get_me(request: Request, response: Response):
    email = _current_email(request)
    # 每次打开 app 都续 Cookie（滚动续期，配合 session_email 的 DB 续期，活跃用户永不掉线）；
    # 也顺带补发只有 Bearer 没 Cookie 的老会话（音频/导出是浏览器自发请求，只认 Cookie）。
    token = _session_token(request)
    if token:
        _set_session_cookie(response, token)
    from . import referrals
    return {"email": email, "balanceCents": accounts.balance_cents(email),
            "isAdmin": email.lower() in config.ADMIN_EMAILS,
            # 免费额度（定价 V2 §2）：前端软墙/预估要算入免费抵扣，limited=IP 闸提示
            "free": accounts.free_status(email),
            # 推荐礼金（2026-09-03）：账户页「推荐同行」块 + 被推荐人的待解锁行
            "referral": referrals.summary_for(email)}


@app.get("/api/account/delete-preflight")
def account_delete_preflight(request: Request):
    """注销前体检：能不能删、会删掉什么。前端据此渲染确认框，不必自己猜规则。"""
    try:
        return account_delete.preflight(_current_email(request))
    except account_delete.DeleteRejected as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/transcripts")
def purge_transcripts_api(request: Request):
    """删掉全部转录与音频，账户留着。与注销共用同一套删除机制，只是范围小一圈。"""
    try:
        return {"ok": True, **account_delete.purge_transcripts(_current_email(request))}
    except account_delete.DeleteRejected as e:
        raise UserError(422, e.code) from e


@app.delete("/api/account")
def account_delete_api(request: Request, response: Response, payload: dict = Body(...)):
    """执行注销。**要求把自己的邮箱原样打一遍**——这一步就是防误删的全部机制
    （见 docs：不留反悔期，所以确认动作本身必须够重）。"""
    email = _current_email(request)
    typed = (payload.get("confirmEmail") or "").strip().lower()
    if typed != email.strip().lower():
        raise UserError(422, "confirm_email_mismatch")
    try:
        result = account_delete.delete_account(email)
    except account_delete.DeleteRejected as e:
        # 422 而不是 409：前端拿 detail 直接显示，文案已经写好了「该去做什么」
        raise UserError(422, e.code) from e
    response.delete_cookie(SESSION_COOKIE, path="/api")
    return {"ok": True, **result}


@app.get("/api/glossaries")
def list_glossaries_api(request: Request):
    return {"glossaries": glossary.list_glossaries(_current_email(request))}


def _glossary_or_http(result):
    """create/update 返回值统一转 HTTP：dict 直接返回；str=违规→409(重名/上限) 或 422(校验)。"""
    if isinstance(result, str):
        # ⚠️ 这里原本把 glossary.py 的英文标识符（`name already exists`）**原样贴给用户看**，
        # 于是中文/德文界面上会蹦出一句英文技术串。现在转成码，文案交前端出。
        if "already exists" in result:
            raise UserError(409, "glossary_name_taken")
        if "too many" in result:
            raise UserError(409, "glossary_limit")
        raise UserError(422, "glossary_invalid")
    return result


@app.post("/api/glossaries")
def create_glossary_api(request: Request, payload: dict = Body(...)):
    email = _current_email(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise HTTPException(422, "name 需为字符串")
    content = payload.get("content") or ""
    language = payload.get("language")
    return _glossary_or_http(glossary.create_glossary(email, name, language, content))


@app.put("/api/glossaries/{gid}")
def update_glossary_api(request: Request, gid: str, payload: dict = Body(...)):
    email = _current_email(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise HTTPException(422, "name 需为字符串")
    content = payload.get("content") or ""
    language = payload.get("language")
    out = glossary.update_glossary(email, gid, name, language, content)
    if out is None:
        raise HTTPException(404, "glossary not found")
    return _glossary_or_http(out)


# ── 术语库「协助建库」（起草 / 体检）────────────────────────────────────────
# 两条都**不写库**：只把结果回给前端放进编辑器缓冲区，落库仍走上面的 PUT。
# 所以这条链路碰不到 users/ledger/jobs，最坏情况是「这次没生成出来」。
# 引擎没配或调不通 → 503（前端显示「稍后再试」，不要误报成「材料里没有可收的词」）。


@app.post("/api/glossaries/outline")
async def read_outline_file_api(request: Request, file: UploadFile = File(...)):
    """大纲文件 → 纯文本。**只收 .docx**：txt/md 前端自己读、不上传（大纲里常有
    还没公开的公司名与受访者姓名，能不过网就不过网）。

    读出来的文字回给前端**显示在可编辑的框里**，不直接送去起草——docx 解析出的东西
    质量参差（表格被拉平、页眉页脚可能混进来），让人扫一眼再决定，比默默塞给模型稳。

    体积闸放在 handler 里而不是上面那个 ASGI 中间件：2MB spool 到临时盘无害，
    而中间件那道是为 2GB 音频护盘用的，不必为这条路径把它改复杂。"""
    _current_email(request)                       # 仅鉴权，结果与账号无关
    data = await file.read()
    if len(data) > outline_file.MAX_BYTES:
        raise UserError(413, "outline_too_large", n=outline_file.MAX_BYTES // (1024 * 1024))
    try:
        text = outline_file.read(file.filename or "", data)
    except outline_file.OutlineReadError as e:
        raise UserError(422, e.code) from e
    if not text.strip():
        raise UserError(422, "outline_no_text")
    truncated = len(text) > glossary_assist.MAX_OUTLINE_CHARS
    text = text[:glossary_assist.MAX_OUTLINE_CHARS]
    return {"text": text, "chars": len(text), "truncated": truncated}


def _ui(payload: dict) -> str | None:
    """请求体里的界面语言（前端每次带上当下那门）。术语库助手写的分类名/释义/理由跟它走。
    取不到 → None → `pipeline.ui_lang` 回落英文，**不回落中文**。"""
    return str(payload.get("uiLang") or "").strip()[:8] or None


@app.post("/api/glossaries/draft")
def draft_glossary_api(request: Request, payload: dict = Body(...)):
    _current_email(request)                       # 仅鉴权，结果与账号无关
    outline = payload.get("outline")
    if not isinstance(outline, str):
        raise HTTPException(422, "outline 需为字符串")
    if len(outline) > glossary_assist.MAX_OUTLINE_CHARS:
        raise HTTPException(422, "outline 过长")
    # 埋点：这一节点在库里没留任何痕迹，只能记一笔（它跑在派单前台，不在任务机器上，
    # 所以加埋点不用重建镜像——是「完全没数据」那一类里唯一便宜的一个）。
    # 要看的主要是**耗时**：实测它前一百多秒一个字不吐（在思考），用户就那么等着，
    # 那是产品问题不只是运维问题，而此前没有任何地方看得到。
    t0 = time.monotonic()
    try:
        items = glossary_assist.draft(outline, lang_hint=str(payload.get("hint") or ""),
                                      ui_lang=_ui(payload))
    except glossary_assist.AssistUnavailable as e:
        node_events.record("glossary_draft", "error",
                           ms=round((time.monotonic() - t0) * 1000), note=str(e))
        raise UserError(503, "assist_unavailable") from e   # 引擎原始报错只进日志，不给用户看
    node_events.record("glossary_draft", "ok", ms=round((time.monotonic() - t0) * 1000))
    return {"items": items}


@app.post("/api/glossaries/draft/stream")
def draft_glossary_stream_api(request: Request, payload: dict = Body(...)):
    """起草的流式版：每规整好一条就发一条 SSE，不等整份跑完。

    与上面那条非流式路由并存，**不是替换**：两端部署不同步（Vercel 与 Render 各走各的），
    留着旧路由，新前端配旧后端时不至于整个功能 404。旧路由也仍是后端测试的入口。

    错误分两处：key 没配在流开始前就知道 → 照旧 503；流开到一半断了只能走流内
    `{"error": …}` 事件，因为那时 200 已经发出去了。
    """
    email = _current_email(request)
    outline = payload.get("outline")
    if not isinstance(outline, str):
        raise HTTPException(422, "outline 需为字符串")
    if len(outline) > glossary_assist.MAX_OUTLINE_CHARS:
        raise HTTPException(422, "outline 过长")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise UserError(503, "assist_unavailable")
    hint = str(payload.get("hint") or "")
    ui = _ui(payload)
    # 起草出的缺口要随库留存（用户可以一条不补直接保存，下次进来还在）。
    # 库 id 可选：没传就只把缺口回给前端显示，不落库。
    gid = str(payload.get("glossaryId") or "")
    if gid and not glossary.owns(email, gid):
        raise HTTPException(404, "glossary not found")

    def gen():
        gaps: list[str] = []
        # 流式这条要在**流结束时**才记：耗时的意义是「用户等了多久」，而不是「服务器多快开始吐」。
        # 中途断开则记不到——那没关系，断开的样本本来也不该算进 P95（用户没等完）。
        t0 = time.monotonic()
        outcome = "ok"
        try:
            for it in glossary_assist.draft_stream(outline, lang_hint=hint, ui_lang=ui):
                if "gap" in it:
                    gaps.append(it["gap"])
                yield f"data: {json.dumps(it, ensure_ascii=False)}\n\n"
        except glossary_assist.AssistUnavailable as e:
            outcome = "error"
            yield f"data: {json.dumps({'error': str(e)[:200]}, ensure_ascii=False)}\n\n"
        node_events.record("glossary_draft", outcome, ms=round((time.monotonic() - t0) * 1000))
        # 落库放在流末尾：中途断开就不存，避免留下半份缺口
        if gid and gaps:
            try:
                glossary.add_gaps(gid, gaps)
            except Exception:      # 落库失败不该毁掉这次起草——条目已经写进编辑器了
                pass
        yield "data: [DONE]\n\n"

    # X-Accel-Buffering: Render 前面那层代理默认攒够一批才发，攒了流式就白做了
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/glossaries/check/stream")
def check_glossary_stream_api(request: Request, payload: dict = Body(...)):
    """体检的保活版：结果仍是一次性的，但用 SSE 送，中间靠心跳把连接撑住。

    ⚠️ 这不是为了「早点看到结果」——体检用的是推理模型，思考期一个字都不吐，
    结果只会在最后一次性出来。这么写是因为 **2026-08-05 生产实测直接 502**：
    普通 JSON 响应要等函数返回才发响应头，模型跑一百多秒，Render 网关等不及就掐了。
    起草没踩到这个坑纯属侥幸——它走 StreamingResponse，响应头早就发出去了。

    所以这里两件事缺一不可：① StreamingResponse 让响应头立刻发出；
    ② 每 5 秒一个 SSE 注释行做心跳，免得网关按「空闲」再掐一次。
    """
    email = _current_email(request)
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(422, "content 需为字符串")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise UserError(503, "check_unavailable")
    ui = _ui(payload)

    # 检查产出的缺口也随库留存（场景 B：点检查 → 右栏落下「还缺这些」）
    gid = str(payload.get("glossaryId") or "")
    if gid and not glossary.owns(email, gid):
        raise HTTPException(404, "glossary not found")

    box: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=1)

    def work():
        t0 = time.monotonic()
        try:
            got = glossary_assist.check(content, ui_lang=ui)
            node_events.record("glossary_check", "ok", ms=round((time.monotonic() - t0) * 1000))
            if gid and got.get("gaps"):
                try:
                    glossary.add_gaps(gid, got["gaps"])
                except Exception:   # 落库失败不该毁掉这次检查——三框结果还是有用的
                    pass
            box.put(("ok", got))
        except glossary_assist.AssistUnavailable as e:
            node_events.record("glossary_check", "error",
                               ms=round((time.monotonic() - t0) * 1000), note=str(e))
            box.put(("err", str(e)[:200]))
        except Exception as e:                      # 兜底：别让线程静默死掉、前端干等
            box.put(("err", f"检查失败：{str(e)[:160]}"))

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            try:
                kind, got = box.get(timeout=5)
            except queue.Empty:
                yield ": ping\n\n"                  # SSE 注释行，前端会忽略
                continue
            if kind == "ok":
                yield f"data: {json.dumps(got, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'error': got}, ensure_ascii=False)}\n\n"
            break
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/glossaries/check")
def check_glossary_api(request: Request, payload: dict = Body(...)):
    _current_email(request)
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(422, "content 需为字符串")
    try:
        return glossary_assist.check(content, ui_lang=_ui(payload))
    except glossary_assist.AssistUnavailable as e:
        raise UserError(503, "check_unavailable") from e


@app.get("/api/glossaries/{gid}/gaps")
def list_gaps_api(request: Request, gid: str):
    """待办层：这本库还缺哪几类。与正文分表，**不参与转录注入**。"""
    email = _current_email(request)
    if not glossary.owns(email, gid):
        raise HTTPException(404, "glossary not found")
    return {"gaps": glossary.list_gaps(gid)}


@app.patch("/api/glossaries/{gid}/gaps/{gap_id}")
def set_gap_api(request: Request, gid: str, gap_id: str, payload: dict = Body(...)):
    """标记「知道了」/「恢复」。只动状态，缺口本身不删——用户要能撤回。"""
    email = _current_email(request)
    if not glossary.owns(email, gid):
        raise HTTPException(404, "glossary not found")
    status = payload.get("status")
    if not glossary.set_gap_status(gid, gap_id, str(status or "")):
        raise HTTPException(422, "status 需为 open 或 handled，且 gap 需属于该库")
    return {"gaps": glossary.list_gaps(gid)}


@app.delete("/api/glossaries/{gid}")
def delete_glossary_api(request: Request, gid: str):
    email = _current_email(request)
    if not glossary.delete_glossary(email, gid):
        raise HTTPException(404, "glossary not found")
    return {"ok": True}


# ── 项目（定价 V2 §4.1）：分组 + 锁价。**2026-08-18 入口下架** ──
# 下架的理由不是做不出来，是**建项目等于发一张我们收不回来的永久折扣券**：
# 项目把创建那天的两档费率钉死，之后该项目的上传无论多少单都按那个价（规格里的「锁价不锁量」），
# 而结项/删除/改价全在 §4.2 的 P1 里，一个都没做——`status` 有 closed 这个值，
# 却没有任何代码路径能写进去。于是第一次涨价的那天，存量项目就是永久的旧价通道，且不可逆。
# 今天不疼只是因为项目价恰好等于现价。
#
# 处置照搬「归类」下架的先例：**发起路径关掉，读取路径保留**。
# - POST 关成 410：不再发出新的折扣券（这是唯一真正要止住的动作）
# - GET 保留：projects 表与 jobs.project_id 故意不删，存量数据要查得到；
#   create_job / retry 仍按 project_id 走项目费率——存量单重试不能换价钱
# 要恢复的话：把 POST 改回来 + 前端加回选择器，但**先把结项与锁价期限想清楚**。


@app.get("/api/projects")
def list_projects_api(request: Request):
    return {"projects": projects.list_projects(_current_email(request))}


@app.post("/api/projects")
def create_project_api(request: Request, payload: dict = Body(...)):
    # 410 Gone 而不是 404：这个口存在过，且存量项目还在正常计费——
    # 404 会让人以为是路由写错了，跑去"修"它。
    raise HTTPException(410, "项目功能已下架，不再支持新建")


# ── 后处理：配置 CRUD + 任务（契约 docs/postprocess-implementation-contract.md）──


def _pp_or_http(result):
    """postprocess CRUD 返回值统一转 HTTP（同 _glossary_or_http 口径）：
    dict 直接返回；str 违规 → 409(超上限) / 422(校验)；None → 404。"""
    if result is None:
        raise HTTPException(status_code=404, detail="not found")
    if isinstance(result, str):
        raise HTTPException(status_code=409 if "too many" in result else 422, detail=result)
    return result


@app.get("/api/postprocess/redact-lists")
def list_pp_redact_lists(request: Request):
    return {"lists": postprocess.list_redact_lists(_current_email(request))}


@app.post("/api/postprocess/redact-lists")
def create_pp_redact_list(request: Request, payload: dict = Body(...)):
    email = _current_email(request)
    row = _pp_or_http(postprocess.create_redact_list(
        email, payload.get("name") or "未命名清单", payload.get("content") or ""))
    return {"id": row["id"]}


@app.put("/api/postprocess/redact-lists/{rid}")
def update_pp_redact_list(request: Request, rid: str, payload: dict = Body(...)):
    email = _current_email(request)
    name = payload.get("name")
    if not isinstance(name, str):
        raise HTTPException(422, "name 需为字符串")
    _pp_or_http(postprocess.update_redact_list(email, rid, name, payload.get("content") or ""))
    return {"ok": True}


@app.delete("/api/postprocess/redact-lists/{rid}")
def delete_pp_redact_list(request: Request, rid: str):
    if not postprocess.delete_redact_list(_current_email(request), rid):
        raise HTTPException(404, "list not found")
    return {"ok": True}


# 产物显示名（中文主稿；EN 由前端 L() 按 kind 映射）
# categorize 留在表里是**只读兼容**：归类下架前跑完的存量任务，产物列表里还有 "categorize"，
# 摘掉这一项的话历史页与下载名会退成裸英文 kind。
_PP_PRODUCT_NAMES = {"narrate": "叙述稿", "categorize": "归类纪要", "redact": "脱敏稿"}


@app.post("/api/jobs/{job_id}/postprocess")
def start_postprocess(job_id: str, request: Request, payload: dict = Body(...)):
    """发起后处理。前置：job done；steps 非空且 ⊂ STEPS（固定顺序归一）；
    redactListId 可空=智能识别。幂等：queued/running → 409；
    failed/done 重发 = 同行重置重跑。价格快照发起时钉死（促销期 0）。"""
    job = _owned_job(job_id, request)
    email = _current_email(request)
    if job.status != "done":
        raise UserError(409, "pp_job_not_done")
    steps = postprocess.normalize_steps(payload.get("steps"))
    if steps is None:
        raise HTTPException(status_code=422, detail="steps 需为可用步骤的非空子集")
    rlist = None
    rid = payload.get("redactListId")
    if rid:
        rlist = postprocess.get_redact_list(email, rid)
        if rlist is None:
            raise HTTPException(status_code=422, detail="invalid redact list")
    existing = postprocess.get_pp_job(job_id)
    if existing and existing["status"] in ("queued", "running"):
        raise UserError(409, "pp_in_flight")
    # 2026-08-02 改价：每步加价率 × 音频分钟，按秒折算；发起时快照 rate+时长，结算只认快照。
    # 余额闸按「免费额度抵扣后的应付款」校验（免费额度也能抵后处理，不限文件长短）
    pp_rate = pricing.postprocess_rate_cents_per_min(steps)
    audio_sec = int(job.duration_sec or 0)
    price = pricing.postprocess_price_cents(steps, audio_sec)
    free_left = accounts.free_status(email)["leftSeconds"]
    due_est = round(pp_rate * max(0, audio_sec - free_left) / 60)
    if due_est > 0 and accounts.balance_cents(email) < due_est:
        raise UserError(402, "balance_low")
    # 清单内容在发起这一刻快照进 R2（此后改配置不影响本任务）；先落快照再排队，
    # 排队成功即保证 runner 有输入可取
    blobstore.put_bytes(
        f"postprocess/{job_id}/inputs.json",
        json.dumps({
            "steps": steps,
            "redactList": {"id": rlist["id"], "name": rlist["name"],
                           "content": rlist["content"]} if rlist else None,
        }, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )
    ok = postprocess.create_or_reset(
        job_id, email, steps, price,
        redact_list_id=rlist["id"] if rlist else None,
        list_name=rlist["name"] if rlist else None,
        rate_cents_per_min=pp_rate, audio_seconds=audio_sec,
        # 发起这一刻的界面语言：质检报告里「我们写的字」跟它走（见 pipeline/pp_lang.py）。
        # 前端不传就留空 → 跑的时候回落英文，不回落中文。
        ui_lang=str(payload.get("uiLang") or "")[:8] or None,
    )
    if not ok:   # 并发抢跑：另一请求刚把它排上
        raise UserError(409, "pp_in_flight")
    if config.FLY_DISPATCH:   # 派单失败不挡排队，看门狗会补派（同转录）
        try:
            fly_machines.dispatch_pending()
        except Exception:  # noqa: BLE001
            import traceback
            print(f"后处理派单失败（任务已排队，待看门狗补派）job={job_id}: "
                  f"{traceback.format_exc()[-300:]}", flush=True)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/postprocess")
def get_postprocess(job_id: str, request: Request):
    # 「本人任务但还没跑过后处理」是常态，不是错误：返回 200 + null。
    # 原先返回 404，导致每打开一次这类详情页浏览器控制台就多一条 error（并污染前端错误上报）。
    # 注意别把 _owned_job 的 404 一起改掉——那是「不是你的任务」，仍应 404。
    _owned_job(job_id, request)
    pp = postprocess.get_pp_job(job_id)
    if pp is None:
        return None
    return {
        "status": pp["status"], "steps": pp["steps"],
        "stepIndex": pp["step_index"], "totalSteps": len(pp["steps"]),
        "currentStep": pp["current_step"],
        "products": [{"kind": k, "name": _PP_PRODUCT_NAMES.get(k, k)} for k in pp["products"]],
        "qcFixCount": pp["qc_fix_count"], "hasQcReport": pp["has_qc"],
        "failedStep": pp["failed_step"], "priceCents": pp["price_cents"],
        "profileName": pp["profile_name"], "listName": pp["list_name"],
        "updatedAt": pp["updated_at"],
    }


@app.get("/api/jobs/{job_id}/postprocess/product/{filename}")
def get_pp_product(job_id: str, filename: str, request: Request, name: str = "", ui: str = "zh"):
    """产物下载：filename = {kind}.{md|txt|docx}。md/txt 同内容（不同 MIME），
    docx 用 export 简化段落渲染；文件名走 Content-Disposition（与转录导出同机制）。"""
    job = _owned_job(job_id, request)
    pp = postprocess.get_pp_job(job_id)
    if pp is None:
        raise HTTPException(status_code=404, detail="postprocess not found")
    kind, _, ext = filename.rpartition(".")
    if ext not in ("md", "txt", "docx") or kind not in pp["products"]:
        raise HTTPException(status_code=404, detail="product not found")
    try:
        data = blobstore.get_bytes(f"postprocess/{job_id}/{kind}.md")
    except blobstore.NoSuchKey:   # 30 天生命周期已删
        raise HTTPException(status_code=410, detail="product expired")
    # 用户在改动清单里改口过（保留原词 / 换个写法）→ 取稿时应用，**R2 上那份原样留档**。
    # 应用不了（稿子取不到、行数对不上）就给原样：少改一处用户在清单上看得见，
    # 而下载失败会让人以为整个产物没了。
    if kind == "redact":
        ovr = redact_diff.load_overrides(job_id)
        if ovr:
            try:
                src = redact_diff.redact_input_text(job_id, pp["steps"])
                data = redact_diff.apply_overrides(src, data.decode("utf-8"), ovr).encode("utf-8")
            except Exception:   # noqa: BLE001
                pass
    # 产物里的说话人是**代号**（脱敏稿是问答体，行首就是它）——下载时才按界面语言换。
    # 存的时候不能定死语言：同一份产物要能被任何界面语言的人下载。
    data = speaker_labels.localize_transcript_labels(data.decode("utf-8"), ui).encode("utf-8")
    base = name or _PP_PRODUCT_NAMES.get(kind, kind)
    if ext == "docx":
        return Response(
            content=export.markdown_to_docx(data.decode("utf-8"), job.lang or ""),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers=_disposition(f"{base}.docx"),
        )
    mime = "text/markdown; charset=utf-8" if ext == "md" else "text/plain; charset=utf-8"
    # 后处理产物是模型写的 markdown（阿语稿就是阿语正文）——与转录导出同一套方向处理
    body = bidi.isolate_text(data.decode("utf-8"), job.lang or "").encode("utf-8") \
        if bidi.is_rtl(job.lang or "") else data
    return Response(content=body, media_type=mime, headers=_disposition(f"{base}.{ext}"))


@app.get("/api/jobs/{job_id}/postprocess/qc.md")
def get_pp_qc(job_id: str, request: Request, name: str = "质检报告"):
    _owned_job(job_id, request)
    try:
        data = blobstore.get_bytes(f"postprocess/{job_id}/qc.md")
    except blobstore.NoSuchKey:
        raise HTTPException(status_code=404, detail="qc report not found")
    return Response(content=data, media_type="text/markdown; charset=utf-8",
                    headers=_disposition(f"{name}.md"))


@app.get("/api/jobs/{job_id}/postprocess/changes")
def get_pp_changes(job_id: str, request: Request):
    """脱敏改动清单：逐条「原词 → 脱敏后」，供详情页展开查看。

    **算法是对比前后两稿，不是解析 QC 报告**（理由见 app/redact_diff.py 模块头）。
    一切异常都吞成空清单——这是知情用的锦上添花，不该让它把详情页拖垮；
    前端拿到空就不显示这一块，用户仍可下载 QC 报告看全量记录。

    ⏱ **打了耗时日志**（2026-08-26）：这是全站唯一「一个请求取多个对象」的接口，
    也是详情页那张卡的等待来源。手上的数字来自两个不同的地方且差了 5 倍
    （Render 一次开库约 90ms / Fly sjc 约 490ms），**不足以拿来决定要不要上数据库连接池**——
    所以先让生产自己报。分三段计：鉴权+查库 / 取两份稿 / 逐行对比。"""
    t0 = time.monotonic()
    _owned_job(job_id, request)
    pp = postprocess.get_pp_job(job_id)
    t_db = time.monotonic()
    if pp is None or pp["status"] != "done" or "redact" not in (pp["products"] or []):
        return {"changes": []}
    try:
        src = redact_diff.redact_input_text(job_id, pp["steps"])
        dst = blobstore.get_bytes(f"postprocess/{job_id}/redact.md").decode("utf-8")
    except Exception:   # noqa: BLE001  产物过期/稿子取不到
        return {"changes": []}
    t_r2 = time.monotonic()
    changes = redact_diff.extract_changes(src, dst)
    # 行号 → 正文第几段：脱敏的输入是 segments_to_qa 的产物，段与段之间空一行，
    # 所以偶数行 i 对应第 i//2 段。**只有脱敏是第一步时才成立**——前面跑过视角转换的话，
    # 输入是它的产物、行序与转录稿无关，此时不给锚点（前端只列清单、不给定位），
    # 绝不按一个算不准的行号把用户送到别的句子上。
    first = (pp["steps"] or [None])[0] == "redact"
    for c in changes:
        for sp in c["spots"]:
            i = sp["line"]
            sp["seg"] = i // 2 if first and i % 2 == 0 else None
    out = {"changes": changes, "overrides": redact_diff.load_overrides(job_id)}
    t_end = time.monotonic()
    print(f"[timing] pp/changes job={job_id[:8]} 库{(t_db - t0) * 1000:.0f}ms"
          f" 取稿{(t_r2 - t_db) * 1000:.0f}ms 对比+改口{(t_end - t_r2) * 1000:.0f}ms"
          f" 合计{(t_end - t0) * 1000:.0f}ms", flush=True)
    return out


@app.put("/api/jobs/{job_id}/postprocess/redact/overrides")
def put_pp_overrides(job_id: str, request: Request, body: dict = Body(...)):
    """保存用户对脱敏改动的改口（保留原词 / 换个写法）。

    **不重跑、不计费**：只记「哪一处要写成什么」，取稿与导出时再应用（见 get_pp_product）。
    整表覆盖式保存——前端每次提交当前全量，省掉一套增删语义。
    """
    _owned_job(job_id, request)
    pp = postprocess.get_pp_job(job_id)
    if pp is None or "redact" not in (pp["products"] or []):
        raise HTTPException(status_code=404, detail="redact product not found")
    ovr = redact_diff.valid_overrides(body.get("overrides"))
    if ovr is None:
        raise HTTPException(status_code=422, detail="overrides 需为 [{line,from,to,value}] 列表")
    blobstore.put_bytes(
        redact_diff.overrides_key(job_id),
        json.dumps(ovr, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )
    return {"ok": True, "overrides": len(ovr)}


@app.get("/api/jobs")
def list_jobs(request: Request):
    email = _current_email(request)
    jobs = accounts.list_jobs(email)
    # 历史页行内后处理摘要（契约：每行可选 postprocess 字段；无后处理的行为 None）
    pp = postprocess.job_summaries(email)
    for j in jobs:
        j["postprocess"] = pp.get(j["id"])
    return {"jobs": jobs}


@app.get("/api/ledger")
def get_ledger(request: Request):
    return {"ledger": accounts.list_ledger(_current_email(request))}


@app.post("/api/ledger/invoice")
def post_invoice(request: Request, payload: dict = Body(...)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=422, detail="ids 需为整数列表")
    accounts.mark_invoiced(_current_email(request), ids)
    return {"ok": True}


@app.post("/api/topups")
def post_topup(request: Request, payload: dict = Body(...)):
    """充值。配置 STRIPE_SECRET_KEY 后走 Stripe Checkout（返回支付页 URL）；
    未配置时：仅本地开发（无 RESEND）直充自测，生产环境一律 503 拒绝——杜绝免费到账。"""
    email = _current_email(request)
    amount = payload.get("amountCents")
    if not isinstance(amount, int) or amount < 500 or amount > 1000000:
        raise HTTPException(status_code=422, detail="amountCents 需为 500–1000000 的整数")
    # 测试号 / 管理员（2026-09-03）：不跳 Paddle，直接入账、account 上记 source=test；礼金链路照常触发
    from . import test_accounts
    if test_accounts.is_test(email):
        return {"ok": True, "balanceCents": test_accounts.direct_topup(email, amount)}
    # 收款通道优先级：Paddle（MoR，定价 V2 目标通道）→ Stripe（历史备选）→ dev 直充（仅开发模式）
    if os.environ.get("PADDLE_API_KEY"):
        from . import paddle_pay
        return {"checkoutUrl": paddle_pay.create_checkout(email, amount)}
    if os.environ.get("STRIPE_SECRET_KEY"):
        from . import stripe_pay
        url = stripe_pay.create_checkout(email, amount)
        return {"checkoutUrl": url}
    # 没接收款通道：生产环境失败关闭（杜绝白嫖），只有本地开发模式保留直充便于自测
    if not accounts.email_dev_mode():
        raise UserError(503, "topup_closed")
    new_balance = accounts.credit(email, amount, "dev")
    return {"ok": True, "balanceCents": new_balance}


@app.get("/api/topups/pending")
def get_pending_topups(request: Request):
    """「在路上」的充值（2026-09-06）：界面每 5 秒问一次；后端顺手向 Paddle 对账，付了就入账。
    没接 Paddle 时永远是空列表（测试号直充与 dev 直充不经过这里）。"""
    email = _current_email(request)
    if not os.environ.get("PADDLE_API_KEY"):
        return {"items": [], "balanceCents": accounts.balance_cents(email)}
    from . import paddle_pay
    return {"items": paddle_pay.pending_for(email), "balanceCents": accounts.balance_cents(email)}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Stripe 回调：支付完成 → 入账。未配置 webhook secret 时此端点拒绝。"""
    if not os.environ.get("STRIPE_WEBHOOK_SECRET"):
        raise HTTPException(status_code=503, detail="stripe not configured")
    from . import stripe_pay
    payload = await request.body()
    okk = stripe_pay.handle_webhook(payload, request.headers.get("stripe-signature", ""))
    return {"received": True, "credited": okk}


@app.post("/api/paddle/webhook")
async def paddle_webhook(request: Request):
    """Paddle 回调：transaction.completed → 入账（金额只核对不入账，按发起时锁定的净额记）。
    未配置 webhook secret 时此端点拒绝。"""
    if not os.environ.get("PADDLE_WEBHOOK_SECRET"):
        raise HTTPException(status_code=503, detail="paddle not configured")
    from . import paddle_pay
    payload = await request.body()
    okk = paddle_pay.handle_webhook(payload, request.headers.get("paddle-signature", ""))
    return {"received": True, "credited": okk}


@app.post("/api/jobs")
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("zh"),
    # 2026-08-18 起前端不再传：场景分流已取消（见 orchestrator._resolve_plan）。
    # 默认值从 "meeting" 改成 "all"，**只为让运营舱一眼分得出新旧单**——沿用 "meeting"
    # 会把「用户根本没选过」的新单显示成「他选了现场面访」。参数保留是为老客户端与重试路径。
    recording_type: str = Form("all"),
    duration_sec: int | None = Form(None),   # 前端估算值，仅历史兼容；服务端一律以 ffprobe 实测为准，不再信任它
    glossary_id: str | None = Form(None),
    project_id: str | None = Form(None),
    # 上传那一刻的界面语言。P3 报告里「我们写的说明」（复核卡的原因/依据）按它出。
    # 前端不传 → None → pipeline/ui_lang 回落英文（不回落中文：韩语用户读得懂英文，读不懂中文）。
    ui_lang: str | None = Form(None),
):
    email = _current_email(request)
    if glossary_id and not glossary.owns(email, glossary_id):
        raise HTTPException(422, "invalid glossary")
    project = None
    if project_id:
        project = projects.get_project(email, project_id)   # 归属校验：别人的项目等同不存在
        if project is None:
            raise HTTPException(422, "invalid project")
        if project["status"] != "active":
            raise HTTPException(422, "project closed")
    job_id = str(uuid.uuid4())
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in _ALLOWED_UPLOAD_EXT:
        raise HTTPException(415, f"Unsupported file format: {suffix or 'unknown'}")

    # 体积硬上限第一道闸：Content-Length 预检，能拿到就在落任何临时文件之前拒掉（省磁盘）。
    # 第二道闸（下方流式拷贝时累计计数）兜底 Content-Length 缺失/伪造的情况。
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > config.MAX_UPLOAD_BYTES:
                raise UserError(413, "upload_too_large", n=config.MAX_UPLOAD_BYTES // 1024 // 1024)
        except ValueError:
            pass  # 头部非法数字：交给下面流式兜底判

    # 落临时文件测真实时长（服务端强制上限 + 按实测预扣余额）：从上传对象流式拷贝，不整段进内存。
    file.file.seek(0)
    tmp_path = None
    try:
        written = 0
        sha = hashlib.sha256()   # 闸③文件闸：边拷贝边算内容哈希（免费额度按 sha256 去重）
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > config.MAX_UPLOAD_BYTES:
                    raise UserError(413, "upload_too_large", n=config.MAX_UPLOAD_BYTES // 1024 // 1024)
                sha.update(chunk)
                tmp.write(chunk)
        real_dur = _probe_duration_sec(tmp_path)
        if real_dur is None:
            raise UserError(400, "audio_duration_unreadable")
        if real_dur > config.MAX_DURATION_SEC:
            raise HTTPException(413, f"Audio too long (limit {config.MAX_DURATION_SEC // 60} min)")
        # 费率快照：此刻的价钉死在本单上，结算只认它——日后调价动不了已上传的单。
        # 预扣按快照 ceil 取整，宁可多冻一分，结算时多退。
        # ⚠️ **项目费率通道 2026-08-31 关闭**（定价 V3）：项目 2026-08-18 已下架、存量为 0，
        # 而任何存量项目行钉的都是两档时期的 50/100 分——留着这条分支只可能按 10–20 倍旧价
        # 收钱。project_id 仍照常落库（分组信息不丢），只是不再决定价钱。
        rate = pricing.rate_cents_per_min(lang)
        reserved = math.ceil(real_dur / 60 * rate)
        # 先传 R2 再「预扣+建 job 单事务」：事务里余额够则扣款并建 job、不够整体回滚——
        # 「扣了钱但 job 没建成」的崩溃窗不存在（精确对账）。R2 传完但事务失败只留孤儿
        # 音频对象，无钱责，7 天生命周期自清。
        audio_key = f"audio/{job_id}{suffix}"
        file.file.seek(0)
        # 流式存进对象存储：boto3 从上传文件对象分块读，整段（含大视频）不进内存。
        blobstore.upload_fileobj(audio_key, file.file, file.content_type or "application/octet-stream")
        # 免费额度：哈希去重与余量判定
        # 在 reserve 的事务里做（并发安全）。付费上传不受这两条限制。
        jid = accounts.reserve_and_create_job(email, reserved, audio_key, lang, recording_type,
                                              file_name=file.filename,
                                              duration_sec=int(round(real_dur)),
                                              glossary_id=glossary_id or None,
                                              rate_cents_per_min=rate,
                                              project_id=project_id or None,
                                              audio_sha256=sha.hexdigest(),
                                              ui_lang=(ui_lang or "").strip()[:8] or None)
        if jid is None:
            try:
                blobstore.delete(audio_key)   # 顺手清；失败留给 R2 生命周期
            except Exception:  # noqa: BLE001
                pass
            raise UserError(402, "balance_low")
    finally:
        # tmp_path 可能在分块拷贝抛异常那次就已创建（文件已落盘、只是拷贝没写完，含体积超限中止）——
        # 纳入同一 try 才能在这里兜到；否则每次拷贝失败都在磁盘留一个孤儿临时文件。
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if config.FLY_DISPATCH:   # Fly 派单模式：建 job 后即起机器；失败不挡建 job（看门狗会补派）
        try:
            fly_machines.dispatch_pending()
        except Exception:  # noqa: BLE001
            import traceback
            print(f"create_job 派单失败（job 已建，待看门狗补派）jid={jid}: {traceback.format_exc()[-300:]}", flush=True)
    return {"jobId": jid}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request, ui_lang: str | None = None):
    """把一个失败的单再跑一遍——**用原来那份录音重新建一单**，不是把旧单改回 queued。

    为什么另起一单：失败时预扣已经全额返还、免费秒也退了、audio_hashes 那行也删了
    （见 accounts.refund_job_reservation）。把旧单改回 queued 的话，它身上挂着的
    reserved_cents 是一个**已经退过的数**，结算时会按它扣第二遍——等于重试一次收两次钱。
    重新建单则原样走一遍预扣/免费额度，账目自洽。

    **费率用原单的快照**，不是当前价：重试的是同一次上传，中间调价不该落到用户头上
    （与「每单费率在上传那一刻钉死」同一条口径）。

    录音只留 7 天（R2 生命周期），所以过期的单退不回来——这时给一句明确的话，
    不要让用户点了没反应然后自己猜。"""
    email = _current_email(request)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT audio_key, lang, recording_type, file_name, duration_sec, glossary_id, "
            "rate_cents_per_min, project_id, audio_sha256, status, ui_lang FROM jobs "
            "WHERE id = %s AND user_email = %s",
            (job_id, email),
        ).fetchone()
    if row is None:
        raise UserError(404, "job_not_found")
    (audio_key, lang, rtype, fname, dur, gid, rate, pid, sha256, status, old_ui) = row
    if status != "failed":
        raise UserError(409, "job_not_failed")
    # 录音还在不在：过了保留期就没得重试了。**必须真去问一次对象存储**——
    # 光看 created_at 猜是不是超过 7 天，会在边界上骗人（生命周期不是准点执行的）。
    if not blobstore.exists(audio_key):
        raise UserError(410, "audio_expired")

    rate = int(rate) if rate is not None else pricing.rate_cents_per_min(lang)
    reserved = math.ceil((dur or 0) / 60 * rate)
    jid = accounts.reserve_and_create_job(
        email, reserved, audio_key, lang, rtype,
        file_name=fname, duration_sec=dur, glossary_id=gid,
        rate_cents_per_min=rate, project_id=pid, audio_sha256=sha256,
        # ⚠️ 界面语言**跟当下走，不跟原单走**（与费率相反）。费率沿用是因为它是对用户的
        # 价格承诺；而重试会重新出一份报告，用户此刻读得懂哪门语言才是唯一相关的事。
        ui_lang=(ui_lang or "").strip()[:8] or old_ui)
    if jid is None:
        raise UserError(402, "balance_low")
    if config.FLY_DISPATCH:
        try:
            fly_machines.dispatch_pending()
        except Exception:  # noqa: BLE001
            import traceback
            print(f"retry 派单失败（job 已建，待看门狗补派）jid={jid}: {traceback.format_exc()[-300:]}", flush=True)
    return {"jobId": jid}


# 能下发给用户的 metrics 键（进度页要显示的那几个数字）。**白名单而不是黑名单**：
# 日后 orchestrator 往 metrics 里加字段是常事，黑名单漏一个就是又一次泄漏。
_PUBLIC_METRIC_KEYS = ("parts", "chars", "alignedSegs", "speakers", "finalSegs",
                       "durationSec", "elapsedSec")


def _public_metrics(m):
    """metrics 里混着**只能内部看**的三样东西，整块下发等于摆在浏览器 devtools 里给人抄：
      · engines / primary —— 各 ASR 供应商 tag 与逐路状态（ELV/DB/FA/XF…），供应链全暴露
      · cost —— 每单的人民币成本（p1 分路 + p3 + total）。用户付的是 $0.5~1.0/分钟，
        对着成本一减就知道毛利
      · cost.p3_engine —— opus/flash 档位，既是供应商也撞**去 AI 化红线**
        （设计纪律 7；MoR 审核会注册账号进产品看，devtools 里躺着 "opus" 说不清）
    前端只用得上进度数字（见 src/lib/api.ts 的 JobMetrics），其余一律不出门。"""
    if not isinstance(m, dict):
        return m
    return {k: v for k, v in m.items() if k in _PUBLIC_METRIC_KEYS}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    job = _owned_job(job_id, request)
    # 用户侧只出脱敏话术；内部 traceback/供应商报错（job.error）仅库/admin 查
    return {"status": job.status, "phase": job.phase, "progress": job.progress,
            "error": job.error_public, "metrics": _public_metrics(job.metrics)}


@app.get("/api/jobs/{job_id}/result")
def get_result(job_id: str, request: Request):
    # 取稿与导出同源：有修订版优先返回修订版（见 _result_segments）
    return JSONResponse(content=_result_segments(_owned_job(job_id, request)))


@app.put("/api/jobs/{job_id}/transcript")
def put_transcript(job_id: str, request: Request, body: dict = Body(...)):
    """保存复核修订后的文稿快照（改名/改写/删除后的当前全文）。

    原始转录稿（result_key）保留不动；修订版另存 results_edited/，
    导出与取稿优先用修订版——用户改完导出的就是改完的稿。
    """
    job = _owned_job(job_id, request)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="result not ready")
    segs = body.get("segments")
    if not isinstance(segs, list) or not all(isinstance(s, dict) and "t" in s and "s" in s for s in segs):
        raise HTTPException(status_code=422, detail="segments 需为 [{t, sp, s}] 列表")
    blobstore.put_bytes(
        _edited_key(job_id),
        json.dumps(segs, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )
    return {"ok": True, "segments": len(segs)}


@app.get("/api/jobs/{job_id}/review")
def get_review(job_id: str, request: Request):
    job = _owned_job(job_id, request)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="result not ready")
    try:
        return JSONResponse(content=json.loads(blobstore.get_bytes(f"review/{job_id}.json")))
    except blobstore.NoSuchKey:  # 旧任务确实没有复核清单 → 空清单合理
        return JSONResponse(content=[])
    except Exception:  # R2 抖动/文件损坏等取不到 → 报错，不伪装成“没有要确认的”
        raise HTTPException(status_code=503, detail="review temporarily unavailable")


# 复核决策账本上限（字节）：正常一单几 KB，防滥用兜底
_REVIEW_STATE_MAX = 200_000


def _review_state_key(job_id: str) -> str:
    # 挂 review/ 前缀：复用其 30 天 R2 生命周期规则，与复核清单同寿
    return f"review/{job_id}.state.json"


@app.put("/api/jobs/{job_id}/review_state")
def put_review_state(job_id: str, request: Request, body: dict = Body(...)):
    """保存复核决策账本（每处 确认/替换/改写/删除 的归宿 + 项级跳过标记）。

    修订稿(put_transcript)只存「改完的文字」，不含「哪些项已确认」——没有这份账本，
    重新登录后确认进度会归零（修订还在、状态全回退）。内容对后端不透明，原样存取。
    """
    job = _owned_job(job_id, request)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="result not ready")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(data) > _REVIEW_STATE_MAX:
        raise HTTPException(status_code=413, detail="review state too large")
    blobstore.put_bytes(_review_state_key(job_id), data, "application/json")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/review_state")
def get_review_state(job_id: str, request: Request):
    job = _owned_job(job_id, request)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="result not ready")
    try:
        return JSONResponse(content=json.loads(blobstore.get_bytes(_review_state_key(job_id))))
    except blobstore.NoSuchKey:  # 从未存过 → 空状态，前端从零开始
        return JSONResponse(content={})
    except Exception:  # R2 抖动/损坏 → 报错；别让前端把「取不到」当「没确认过」
        raise HTTPException(status_code=503, detail="review state temporarily unavailable")


# 按扩展名给正确音频 MIME——否则 m4a 被当 audio/mpeg，浏览器读得到时长却放不动
_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".amr": "audio/amr", ".pcm": "audio/L16",
}


def _parse_range(header: str, total: int) -> tuple[int, int] | None:
    """解析 `Range: bytes=START-END` → (start, end) 闭区间；非法/不可满足返回 None。"""
    if not header or "=" not in header:
        return None
    unit, _, rng = header.partition("=")
    if unit.strip().lower() != "bytes" or "-" not in rng:
        return None
    start_s, _, end_s = rng.partition("-")
    try:
        if start_s == "":  # 形如 "-500"：最后 500 字节
            length = int(end_s)
            start, end = max(0, total - length), total - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else total - 1
    except ValueError:
        return None
    end = min(end, total - 1)
    if start > end or start >= total:
        return None
    return start, end


# 试听音频的浏览器缓存时长。音频**一转完就不再变**（7 天后整个删掉），是最适合缓存的东西。
# 设成 1 天而不是 7 天：短于剩余寿命，免得对象删掉了浏览器还拿着缓存显示"能播"。
# `private` 必须有——`/api/*` 是经 Vercel 转发到 Render 的，不声明的话边缘可能缓存别人的录音。
_AUDIO_CACHE = "private, max-age=86400"
_AUDIO_CHUNK = 256 * 1024


def _stream_body(body, chunk: int = _AUDIO_CHUNK):
    """把 R2 的响应流按块转发。**读完/出错都要关**，否则连接漏在池子里。

    浏览器缓冲够了会主动掐断连接，这在音频里是**常态不是异常**——生成器因此被 GC 关闭时
    不该刷一屏报错，所以这里不打日志，只保证 close。"""
    try:
        while True:
            buf = body.read(chunk)
            if not buf:
                return
            yield buf
    finally:
        try:
            body.close()
        except Exception:  # noqa: BLE001  关闭失败无从处置，也不该盖掉真正的错误
            pass


@app.get("/api/jobs/{job_id}/audio")
def get_audio(job_id: str, request: Request):
    """回放音频。**按需取字节、边收边发**，不把整个文件读进内存（见 blobstore.open_range 的理由）。

    浏览器拖进度条 = 一个 Range 请求。此前每来一个就把整份音频（4 小时的约 56 MB）读进
    Render 的 512 MB 内存里再切一段，而复核的用法就是反复点播放、反复定位。
    """
    job = _owned_job(job_id, request)
    mime = _AUDIO_MIME.get(os.path.splitext(job.audio_key)[1].lower(), "application/octet-stream")
    head = request.headers.get("range", "")
    # 先取一个字节问出总长——Range 的合法性要拿总长才判得了（`bytes=-500`、越界都依赖它），
    # 而这一次往返本来也躲不掉（要么问长度、要么直接取）。1 字节的开销可以忽略。
    try:
        if head:
            probe, _, total = blobstore.open_range(job.audio_key, 0, 0)
            probe.close()
            rng = _parse_range(head, total)
        else:
            rng, total = None, None
        start, end = rng if rng is not None else (None, None)
        body, length, total = blobstore.open_range(job.audio_key, start, end)
    except blobstore.NoSuchKey:
        raise HTTPException(status_code=410, detail="audio expired")
    hdr = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": _AUDIO_CACHE}
    if rng is not None:
        hdr["Content-Range"] = f"bytes {start}-{end}/{total}"
    return StreamingResponse(_stream_body(body), status_code=206 if rng is not None else 200,
                             media_type=mime, headers=hdr)


def _edited_key(job_id: str) -> str:
    return f"results_edited/{job_id}.json"


def _result_segments(job) -> list[dict]:
    if job.status != "done" or not job.result_key:
        raise HTTPException(status_code=409, detail="result not ready")
    try:
        return json.loads(blobstore.get_bytes(_edited_key(job.id)))  # 修订版优先
    except Exception:  # noqa: BLE001  无修订版（常态）→ 取原始稿
        pass
    try:
        return json.loads(blobstore.get_bytes(job.result_key))
    except Exception as e:  # noqa: BLE001  原始稿也取不到 = R2 30 天生命周期已删 → 内容过期
        raise HTTPException(status_code=410, detail="result expired") from e


@app.get("/api/jobs/{job_id}/export.docx")
def export_docx(job_id: str, request: Request, name: str = "transcript", ui: str = "zh"):
    job = _owned_job(job_id, request)
    data = export.segments_to_docx(_result_segments(job), job.lang or "", ui)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=_disposition(f"{name}.docx"),
    )


@app.get("/api/jobs/{job_id}/export.txt")
def export_txt(job_id: str, request: Request, name: str = "transcript", ui: str = "zh"):
    job = _owned_job(job_id, request)
    text = _segments_to_txt(_result_segments(job), job.lang or "", ui)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers=_disposition(f"{name}.txt"),
    )

"""FastAPI 应用：上传、查询、取结果、放音频、认证桩。

`jobstore`/`blobstore` 为模块级名字，测试可 monkeypatch 成假实现。
"""
import hashlib
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

from datetime import datetime, timedelta, timezone

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from .user_errors import UserError
from . import (account_delete, alerts, balances, bidi, blobstore, claude_gate, config, db, export,
               glossary, glossary_assist, jobstore, local, node_events, p3_health,
               outline_file, postprocess, redact_diff, speaker_labels)

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
    """本机版：启动时建表、建存储目录，并在接口进程里起后台（一次一单）。
    设 RUN_WORKER_IN_API=0 只起接口、不跑转录（调界面时用）。"""
    db.init_schema()
    blobstore.ensure_bucket()
    stop = None
    if os.environ.get("RUN_WORKER_IN_API", "1") != "0":
        from . import worker
        _, stop = worker.start_in_thread()
    yield
    if stop is not None:
        stop.set()


app = FastAPI(title="Transcribe API", lifespan=lifespan)

# 本机版：只服务本机浏览器。Host 与 Origin 两道检查，挡两类攻击 ——
# ① DNS 重绑定：外网网页把自己的域名解析到 127.0.0.1 后读接口，这时 Host 头是那个域名；
# ② 跨站提交：任意网页都能让浏览器往 127.0.0.1 发表单（上传、删除），这时 Origin 头不是本机。
_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
_HOST_RE = re.compile(r"^(?:\[(?P<v6>[^\]]+)\]|(?P<name>[^:]+))(?::\d+)?$")


def _hostname(host_header: str) -> str:
    m = _HOST_RE.match(host_header or "")
    return (m.group("v6") or m.group("name") or "").lower() if m else ""


@app.middleware("http")
async def _local_only(request: Request, call_next):
    if _hostname(request.headers.get("host", "")) not in _LOCAL_HOSTNAMES:
        return JSONResponse({"detail": "只接受本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        if (urlparse(origin).hostname or "").lower() not in _LOCAL_HOSTNAMES:
            return JSONResponse({"detail": "只接受本机页面发起的请求"}, status_code=403)
    return await call_next(request)


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


def _current_email(request: Request) -> str:
    """本机版：单用户、不登录，所有请求都算本机用户。"""
    return local.EMAIL


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
    """本机版：本机用户就是管理员（运行面板）。"""
    return local.EMAIL


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


@app.get("/api/admin/alerts")
def admin_alerts(request: Request, days: int = 7, unhandled: int = 0):
    """告警记录（仅管理员）。此前告警只发一封邮件、发完即忘，漏看一次就永远查不到。"""
    _admin_email(request)
    return {"items": alerts.list_alerts(days=max(1, min(days, alerts.RETENTION_DAYS)),
                                        unhandled_only=bool(unhandled)),
            "retentionDays": alerts.RETENTION_DAYS}


@app.post("/api/admin/alerts/{alert_id}/handled")
def admin_alert_handled(alert_id: int, request: Request):
    """标记一条告警已处理。**只有 act 档能标**——其余两档自己会消失，多点一次是白费动作。
    已标过的返回 changed=false（不翻案、不改人名），前端据此不必重复提示。"""
    email = _admin_email(request)
    return {"changed": alerts.mark_handled(alert_id, email)}


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
    """手动探活：真跑一次最小 `claude -p`，验证订阅此刻通不通。结果写进 p3_events，前端轮询 /p3-health 拿。

    本机版：不起云机器，在本机后台线程里跑（要本机装了 claude 并登录订阅）。"""
    _admin_email(request)
    from . import p3_probe   # 局部导入：它依赖 pipeline，平时用不到
    probe_id = p3_probe.new_probe_id()
    threading.Thread(target=p3_probe.run_probe, args=(probe_id,), daemon=True, name="p3-probe").start()
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


@app.get("/api/me")
def get_me(request: Request):
    """本机版：单用户，打开就用，自己就是管理员。余额、免费额度、推荐不再下发。"""
    return {"email": _current_email(request), "isAdmin": True}


@app.delete("/api/transcripts")
def purge_transcripts_api(request: Request):
    """删掉全部转录与音频，账户留着。与注销共用同一套删除机制，只是范围小一圈。"""
    try:
        return {"ok": True, **account_delete.purge_transcripts(_current_email(request))}
    except account_delete.DeleteRejected as e:
        # 本机版修正：DeleteRejected 没有 code 属性，线上这里写 `UserError(422, e.code)` 会变成 500、
        # 用户看不到「还有任务在进行中」那句。照同文件注销前检查的写法，直接把原话给出去。
        raise HTTPException(status_code=422, detail=str(e)) from e


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
    # 本机版：不收费 —— 没有加价率、价格与余额闸
    pp_rate = None
    audio_sec = int(job.duration_sec or 0)
    price = 0
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
    jobs = local.list_jobs(email)
    # 历史页行内后处理摘要（契约：每行可选 postprocess 字段；无后处理的行为 None）
    pp = postprocess.job_summaries(email)
    for j in jobs:
        j["postprocess"] = pp.get(j["id"])
    return {"jobs": jobs}


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
        audio_key = f"audio/{job_id}{suffix}"
        file.file.seek(0)
        # 流式存进对象存储：分块读上传文件对象，整段（含大视频）不进内存
        blobstore.upload_fileobj(audio_key, file.file, file.content_type or "application/octet-stream")
        # 本机版：不收费 —— 没有费率快照与预扣，直接建单
        jid = local.create_job(audio_key, lang, recording_type,
                               file_name=file.filename,
                               duration_sec=int(round(real_dur)),
                               glossary_id=glossary_id or None,
                               audio_sha256=sha.hexdigest(),
                               ui_lang=(ui_lang or "").strip()[:8] or None)
    finally:
        # tmp_path 可能在分块拷贝抛异常那次就已创建（文件已落盘、只是拷贝没写完，含体积超限中止）——
        # 纳入同一 try 才能在这里兜到；否则每次拷贝失败都在磁盘留一个孤儿临时文件。
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

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

    # 本机版：不收费，重试就是用原录音再建一单
    jid = local.create_job(
        audio_key, lang, rtype, file_name=fname, duration_sec=dur, glossary_id=gid, audio_sha256=sha256,
        # 界面语言跟当下走，不跟原单走：重试会重新出一份报告，用户此刻读得懂哪门语言才是唯一相关的事
        ui_lang=(ui_lang or "").strip()[:8] or old_ui)
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

"""env 驱动配置。

本机版（与线上不同的地方只在开头这一段）：数据库是本机 SQLite 文件、对象存储是本机文件夹，
都放在 TRANSCRIBE_DATA 下（缺省 ~/.transcribe-local）。线上的 DATABASE_URL / S3_* 不再有。
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("TRANSCRIBE_DATA") or Path.home() / ".transcribe-local")
DATABASE_PATH = os.environ.get("TRANSCRIBE_DB") or str(DATA_DIR / "transcribe.db")
BLOB_DIR = os.environ.get("TRANSCRIBE_BLOBS") or str(DATA_DIR / "blobs")


def _int_env(name: str, default: int) -> int:
    """读环境变量整数；缺省/非法/非正数一律回落 default。"""
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (ValueError, AttributeError):
        return default


# 同时处理的转录任务数（worker 并发池大小）。每个任务内存 ≈ Gemini 并行块数 × ~12MB，
# 总内存 ≈ 基座 + MAX_CONCURRENT_JOBS × 该值。Render 免费层（512MB）建议 1，升内存后调大。
MAX_CONCURRENT_JOBS = _int_env("MAX_CONCURRENT_JOBS", 1)

# 全局 Claude P3 并发上限（跨任务机器，DB 闸）。同时最多这么多任务走 Claude 融合，
# 超出的 P3 自动降级 DeepSeek。与 skill_merge 进程内信号量同 env。
# ⚠️ 它拦的**不是 429**：Claude 订阅没有并发墙，限的是 5 小时窗口与周额度——
# 这把闸控的是「烧额度的速率」，所以拿不到名额时的处置是降级，不是排队（见 claude_gate 模块头）。
CLAUDE_MAX_CONCURRENCY = _int_env("P3_CLAUDE_CONCURRENCY", 5)

# 后处理**每一步各自**的 Claude 并发上限（视角转换/脱敏两把独立的闸，各 5）。
# 与上面的转录闸也独立：后处理一忙不该把转录的 Claude 档挤没了——用户对「转录降质」的
# 感知远强于「加工降质」。各步共用这一个数：谁也不比谁金贵，分开设只是多几个能设错的旋钮。
PP_CLAUDE_MAX_CONCURRENCY = _int_env("PP_CLAUDE_CONCURRENCY", 5)

# 全局 DeepSeek **Pro** 并发上限（第二档，同一张 claude_slots 表按 engine 分闸）。
# 8 台 × 每机 50 路 = 400，占 DeepSeek Pro 账号并发 500 的 80%——**故意留两成余量**：
# 正好压在 500 上每撞一次墙就是一趟白跑的往返（虽有退避重试，代价是延迟）。
# 超出这 8 台的任务自动落到第三档 Flash（账号并发 2500，42 台 × 50 = 2100 仍有余量）。

# 三档阶梯用的两个 DeepSeek 模型名。2026-08-10 人工复核定档：Pro 第一备选、Flash 第二。
# 两者 reasoning_effort 都用官方默认 high（实测 max 更贵不更好、low 质量明显掉）。
# 2026-09-11 起用官方正式名 `deepseek-flash`（= V4.1 Flash）；旧名 `deepseek-v4-flash` 自 09-10
# 起只是临时兼容别名、路由到同一个模型，官方写明「暂时」，没给下线日期。
DS_MODEL_FLASH = os.environ.get("P3_DS_MODEL_FLASH", "deepseek-flash")

# 撞顶冷却窗（分钟）：一台机器撞顶后，多久内其余机器直接跳过 Claude 走 DeepSeek。
# **令牌模式下 claude -p 不吐额度恢复时刻**（T7.1 实测无 rate_limit_event），拿不到 resetsAt 时
# 只能按「撞顶时刻 + 本窗口」自动解除——没有这个兜底，一次撞顶等于永久禁用 Claude。
# 拿得到 resetsAt（本机 claude.ai 登录态）时以 resetsAt 为准，本值不参与。
# 调大 = 撞顶后更久不试 Claude（稳但降质久）；调小 = 恢复快但可能再撞一次墙。
CLAUDE_CAP_COOLDOWN_MIN = _int_env("P3_CAP_COOLDOWN_MIN", 30)          # 5 小时窗
CLAUDE_CAP_WEEK_COOLDOWN_MIN = _int_env("P3_CAP_WEEK_COOLDOWN_MIN", 360)  # 7 天窗撞顶：恢复慢得多

# 单个音频/视频时长上限（秒）。超过则上传被拒（防滥用 + 控内存/处理时长）。缺省 4 小时。
MAX_DURATION_SEC = _int_env("MAX_DURATION_SEC", 14400)

# 原始上传体积硬上限（字节）。保护派单前台（Render）临时盘——create_job 落临时文件测时长，
# 无此闸超大文件/并发会撑爆磁盘。与 MAX_DURATION_SEC（引擎/时长维度）是两回事，两者都要卡。缺省 2GB。
MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024)

# 计费单价见 app/pricing.py（按语种分档 + 限时折扣 + 每单费率快照），不在这里放全局单价——
# 一个全局常量表达不了「这一单当时按哪个价冻的钱」。

# 登录发码限流（防邮箱轰炸 / 烧 Resend 额度）：同邮箱两次发码最小间隔 + 24h 内上限。
LOGIN_CODE_COOLDOWN_SEC = _int_env("LOGIN_CODE_COOLDOWN_SEC", 60)
LOGIN_CODE_DAILY_CAP = _int_env("LOGIN_CODE_DAILY_CAP", 10)
# per-IP 小时窗上限：防换 N 个邮箱各发一次绕开按邮箱的限流，一次性刷爆 Resend 免费额度。
LOGIN_IP_HOURLY_CAP = _int_env("LOGIN_IP_HOURLY_CAP", 10)

# 术语库上限（与前端 glossary.ts 同口径）：总字数去换行计；单条含义 trim 后计。
GLOSSARY_MAX_CHARS = _int_env("GLOSSARY_MAX_CHARS", 8000)
GLOSSARY_MAX_MEANING = _int_env("GLOSSARY_MAX_MEANING", 150)

# 后处理配置上限（与前端同口径）：脱敏清单的内容长度（字符数，去换行计）。
PP_LIST_MAX_CHARS = _int_env("PP_LIST_MAX_CHARS", 8000)
# 后处理撞顶延后重试（分钟）：任务回 queued 且 not_before=now+此值，到点看门狗再补派。
# 现在每步都有 DeepSeek 降级路，走到这里的只剩「降级也失败」——但那正是它必须留着的理由。
PP_RETRY_DELAY_MIN = _int_env("PP_RETRY_DELAY_MIN", 30)

# 看门狗：周期性回收卡死任务（updated_at 超 STALE_MIN 分钟不动的 running 作业 = 卡死）。
# 未到 MAX_ATTEMPTS 次 → 重排重试；到上限 → 判失败，防「毒任务」每次都卡却被无限重排烧钱。
# INTERVAL_SEC 为检查周期。STALE_MIN 需大于任一单次转录最长相位间隔——进度落库后 P1 期间
# 也会周期刷新 updated_at，故可较短；先保守取 30min，第四期归入后台可调参数。
WATCHDOG_INTERVAL_SEC = _int_env("WATCHDOG_INTERVAL_SEC", 120)
WATCHDOG_STALE_MIN = _int_env("WATCHDOG_STALE_MIN", 30)
WATCHDOG_MAX_ATTEMPTS = _int_env("WATCHDOG_MAX_ATTEMPTS", 3)
# 各供应商的**跨机并发闸**（DB 级，闸挂在引擎调用上而不是整单上，见 app/claude_gate.py）。
# ELV：账号 20 并发 ÷ 整档直传每单恒占 4（官方 `min(4, round_up(时长秒/480))`）= 5。
# XF ：讯飞套餐一并发 10（只有中文跑它）。
# ⚠️ 调大之前先升档：ELV 升 Pro 才有 40（20→40 即 5→10），讯飞要另外升套餐——
# 中文单同时受这两把闸约束，只升一家等于白花钱。
ELV_MAX_CONCURRENCY = _int_env("ELV_MAX_CONCURRENCY", 5)
XF_MAX_CONCURRENCY = _int_env("XF_MAX_CONCURRENCY", 10)

# 主轨排不到供应商并发名额时，任务退回队列延后多久再派（分钟）。
# 取 10 的依据：ELV 每次调用约 8 分钟，等一轮就该有名额放出来了。
JOB_RETRY_DELAY_MIN = _int_env("JOB_RETRY_DELAY_MIN", 10)

# 同时在飞的**转录机器**上限。⚠️ 这是**成本闸，不是正确性闸**——供应商并发由上面两把
# DB 闸把关（拿不到名额就在机器上排队等，等的是「那一路引擎」不是整单）。这里存在的唯一
# 理由是：机器排队等名额时也在计费，放 50 台进来大半在干等不划算。
# 取 15 的依据：ELV 是最紧的一路（5 并发），而它只占单子小半段时间，约 3 倍于它就能把它喂满；
# 剩下的机器正好在跑 P2/P3——**那正是这个数要换来的东西**。
MAX_TRANSCRIBE_JOBS = _int_env("MAX_TRANSCRIBE_JOBS", 40)

# 排队超时（小时）：queued 超过此时长（派单通道永久性坏死等）→ 判失败释放预扣款，
# 返还走看门狗清扫。正常排队（并发满等机器）是分钟级，24h 阈值不会误杀。
QUEUED_MAX_HOURS = _int_env("QUEUED_MAX_HOURS", 24)


def _bool_env(name: str, default: bool = False) -> bool:
    """读环境变量布尔；1/true/yes/on → True，0/false/no/off → False，缺省/非法 → default。"""
    v = os.environ.get(name, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# Fly 任务级机器编排（架构封板 2026-06-22）。FLY_DISPATCH=1 时 Render 变「前台」：收上传 →
# create_job → 在 Fly 起一台 performance-1x 机器跑该 job（传 env JOB_ID），机器跑完进程退出即
# auto_destroy 自毁；本进程只跑看门狗（回收+补派），不跑处理池。关（缺省）时行为不变——本地
# docker / Render 免费层仍由进程内 worker 池 claim_next_queued 处理。
FLY_DISPATCH = _bool_env("FLY_DISPATCH", False)
FLY_API_TOKEN = os.environ.get("FLY_API_TOKEN", "")            # fly tokens deploy 生成；Render env 注入
FLY_TASK_APP = os.environ.get("FLY_TASK_APP", "")              # 任务机器所在 Fly app 名
FLY_TASK_IMAGE = os.environ.get("FLY_TASK_IMAGE", "")         # 任务机器镜像 ref（registry.fly.io/<app>:<tag>）
FLY_TASK_REGION = os.environ.get("FLY_TASK_REGION", "sjc")    # 起机器的区域
FLY_MACHINES_API = os.environ.get("FLY_MACHINES_API", "https://api.machines.dev")  # 外部端点
# 全局在飞机器上限（成本闸）：同时最多这么多任务机器在飞，超出的 job 留 queued，靠看门狗在
# 机器自毁腾出名额后补派。与 CLAUDE_MAX_CONCURRENCY（P3 Claude 并发）是两道独立闸。
FLY_MAX_MACHINES = _int_env("FLY_MAX_MACHINES", 50)


def _csv_env(name: str) -> frozenset:
    """逗号分隔的 env → 小写去空白集合；缺省空集。"""
    raw = os.environ.get(name, "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


# 管理员邮箱白名单（逗号分隔）：能进 /admin 运营驾驶舱、看全部用户任务。缺省空 = 无人可进；
# 生产在 Render 设 ADMIN_EMAILS=站主邮箱。比对统一小写。
ADMIN_EMAILS = _csv_env("ADMIN_EMAILS")

# 手工余额调整的单次上限（分，双向都适用）。**它挡的是手滑不是恶意**——能进管理页的人
# 本来就能多点几次，但多打一个零是最常见的一种错。超限时的那一下停顿就是它的全部价值。
ADJUST_MAX_CENTS = _int_env("ADJUST_MAX_CENTS", 5000)   # $50

# 失败率告警：近 WINDOW 分钟内终态作业 ≥ MIN_SAMPLE 且失败率 ≥ PCT% → 邮件告警（带冷却）。
# 样本门槛防偶发单失败误报。
ALERT_FAILRATE_WINDOW_MIN = _int_env("ALERT_FAILRATE_WINDOW_MIN", 60)
ALERT_FAILRATE_MIN_SAMPLE = _int_env("ALERT_FAILRATE_MIN_SAMPLE", 5)
ALERT_FAILRATE_PCT = _int_env("ALERT_FAILRATE_PCT", 40)

# 驾驶舱「增长」里把引擎成本（¥）折成美元用的汇率（Duner 2026-09-03 决策 3）。
# 只用来算毛利率给人看，不进任何账本；页面上会把这个数标出来，别在文案里写死。
USD_CNY_RATE = float(os.environ.get("USD_CNY_RATE", "7.2") or 7.2)

# 低余额是否发邮件：缺省关——驾驶舱面板红标优先（邮件有发送上限，省 Resend 额度）。
# 需邮件兜底时在 Render 设 BALANCE_ALERT_EMAIL=1。关时看门狗完全不查余额（不碰 DB）。
BALANCE_ALERT_EMAIL = _bool_env("BALANCE_ALERT_EMAIL", False)

# 服务商余额自动拉取周期（分钟）：看门狗按此周期调各家只读账单接口刷新余额。余额变化慢，缺省 60min。
BALANCE_REFRESH_MIN = _int_env("BALANCE_REFRESH_MIN", 60)

# 备份多久没新的就算停了（小时）。备份是每小时一次，给 6 小时＝容得下五次连续失败
# 或 GitHub 排期延迟，再不来就是真出事了。
# **这道检查存在的理由是「静默」**：GitHub 免费档额度耗尽、仓库 60 天无活动被自动停用、
# secret 过期——三种都会让备份不声不响地停，而你只会在需要恢复的那天才发现。
BACKUP_STALE_HOURS = _int_env("BACKUP_STALE_HOURS", 6)

# 漏跑多久就补触发一次（小时）。**这是给「GitHub 排期跳过」用的，不是给失败用的**——
# 2026-08-27 复盘 80 次运行：conclusion 全是 success，一次没失败过，但 10/79 个周期被
# GitHub 整段跳过（cron 写 :12，实际触发点在 :05–:58 随机漂），最坏一次空了 10 小时。
# schedule 是尽力而为的，提高 cron 频率治不了它（它是整段窗口不调度，不是随机丢单次）。
# ⇒ 唯一的真修是**换一个我们自己控制的触发源**：看门狗发现漏跑就调一次 workflow_dispatch。
# 取 2 小时＝容得下一次正常的排期抖动（实测中位 58 分、最大正常值 ~90 分）再动手。
BACKUP_SELF_HEAL_HOURS = _int_env("BACKUP_SELF_HEAL_HOURS", 2)
# 补触发之间至少隔多久（分钟），防 token 失效时每轮看门狗都打一次 GitHub。
BACKUP_DISPATCH_COOLDOWN_MIN = _int_env("BACKUP_DISPATCH_COOLDOWN_MIN", 45)
# 备份工作流所在仓库与文件名，以及触发用的令牌（细粒度 PAT，只需该仓库的 Actions: write）。
# **令牌缺省为空＝自愈整个关掉**（只告警，行为与加这段之前一模一样），配上才生效。
BACKUP_REPO = os.environ.get("BACKUP_REPO", "ZeyuSi-2099/Transcribe.solution")
BACKUP_WORKFLOW_FILE = os.environ.get("BACKUP_WORKFLOW_FILE", "db-backup.yml")
BACKUP_DISPATCH_TOKEN = os.environ.get("BACKUP_DISPATCH_TOKEN", "")

-- 本机数据库表结构（SQLite）。由线上 server/app/schema.sql 裁出：只留本机用得到的表，
-- 列与线上同名同义 —— 业务模块能原样同步，靠的就是这一点。
-- 线上的账户、会话、登录、计费账本、退款、支付、项目、防滥用、增长那些表不建。
--
-- 写法约定（db.py 依赖它们）：
--   · 时间列声明为 TIMESTAMPTZ，存 UTC 定宽文本 'YYYY-MM-DD HH:MM:SS.ffffff+00:00'（微秒）。
--     默认值调用 db.py 注册的 now()，与 SQL 里的 now() 同一格式 —— 所以用别的工具直接打开这个库插行，
--     会报 unknown function: now()；读不受影响。
--   · 线上的 UUID 主键 → TEXT + 随机 UUID 表达式；BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT
--   · 线上的 JSONB 列 → 存 JSON 文本
-- ⚠️ 本文件每次启动都整体执行，只许用 IF NOT EXISTS。
--    以后给已有的表加列要另写迁移：SQLite 没有 ADD COLUMN IF NOT EXISTS，直接加在这里对老库不生效。

CREATE TABLE IF NOT EXISTS jobs (
    id                 TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', 1 + abs(random()) % 4, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
    user_email         TEXT,
    status             TEXT NOT NULL DEFAULT 'queued',     -- queued|running|done|failed
    phase              TEXT,                               -- P0|P1|P2|P3|P4|done|NULL
    progress           INTEGER NOT NULL DEFAULT 0,
    lang               TEXT,
    recording_type     TEXT NOT NULL DEFAULT 'meeting',
    audio_key          TEXT NOT NULL,
    result_key         TEXT,
    error              TEXT,
    error_public       TEXT,
    metrics            JSONB,
    attempts           INTEGER NOT NULL DEFAULT 0,
    ui_lang            TEXT,
    file_name          TEXT,
    duration_sec       INTEGER,
    glossary_id        TEXT,
    not_before         TIMESTAMPTZ,
    -- 下面几列是线上计费、项目、防滥用留下的，本机恒为空。留着是为了 jobstore 的查询原样能跑。
    reserved_cents     INTEGER,
    rate_cents_per_min INTEGER,
    free_seconds       INTEGER,
    refunded_cents     INTEGER,
    refunded_at        TIMESTAMPTZ,
    project_id         TEXT,
    audio_sha256       TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT (now()),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT (now())
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs (user_email, created_at DESC);

-- ── 术语库：多本，转录按 jobs.glossary_id 选一本注入定字 ──
CREATE TABLE IF NOT EXISTS glossaries (
    id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', 1 + abs(random()) % 4, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
    email      TEXT NOT NULL,
    name       TEXT NOT NULL,
    language   TEXT,
    content    TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (now())
);
CREATE INDEX IF NOT EXISTS idx_glossaries_email ON glossaries (email, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_glossaries_email_name ON glossaries (email, lower(name));

-- 术语库的待办缺口：与正文分表，免得被注入定字
CREATE TABLE IF NOT EXISTS glossary_gaps (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', 1 + abs(random()) % 4, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
    glossary_id TEXT NOT NULL REFERENCES glossaries(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT (now())
);
CREATE INDEX IF NOT EXISTS idx_glossary_gaps_lib ON glossary_gaps (glossary_id, created_at);

-- ── 服务商余额：本机只盯定字与联网核实会花钱的两家 ──
CREATE TABLE IF NOT EXISTS vendor_balances (
    vendor         TEXT PRIMARY KEY,
    label          TEXT,
    amount_cny     NUMERIC,
    threshold_cny  NUMERIC,
    source         TEXT NOT NULL DEFAULT 'manual',
    note           TEXT,
    low_alerted_at TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT (now()),
    category       TEXT,
    pay_mode       TEXT,
    sort_order     INTEGER,
    unit           TEXT NOT NULL DEFAULT 'cny'
);
INSERT INTO vendor_balances (vendor, label, category, pay_mode, sort_order, unit) VALUES
    ('DeepSeek', 'DeepSeek',   'infra', 'prepaid_manual', 1, 'cny'),
    ('博查',     '博查 Bocha', 'infra', 'prepaid_manual', 2, 'cny')
ON CONFLICT (vendor) DO UPDATE SET
    label      = excluded.label,
    category   = excluded.category,
    pay_mode   = excluded.pay_mode,
    sort_order = excluded.sort_order,
    unit       = excluded.unit;

-- ── 定字名额闸（本机恒放行，表留着让 claude_gate 原样能跑）──
CREATE TABLE IF NOT EXISTS claude_slots (
    engine      TEXT NOT NULL DEFAULT 'claude',
    slot_id     TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT (now()),
    PRIMARY KEY (engine, slot_id)
);

-- ── 定字派单结果埋点（运行面板的定字健康卡）──
CREATE TABLE IF NOT EXISTS p3_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TIMESTAMPTZ NOT NULL DEFAULT (now()),
    source      TEXT NOT NULL,
    job_id      TEXT,
    outcome     TEXT NOT NULL,
    window_kind TEXT,
    resets_at   TIMESTAMPTZ,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_p3_events_at ON p3_events (at DESC);

-- ── 后处理 ──
CREATE TABLE IF NOT EXISTS postprocess_redact_lists (
    id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || substr(hex(randomblob(2)), 2) || '-' || substr('89ab', 1 + abs(random()) % 4, 1) || substr(hex(randomblob(2)), 2) || '-' || hex(randomblob(6)))),
    user_email TEXT NOT NULL,
    name       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (now())
);
CREATE INDEX IF NOT EXISTS idx_pp_lists_email ON postprocess_redact_lists (user_email, updated_at DESC);

CREATE TABLE IF NOT EXISTS postprocess_jobs (
    job_id                TEXT PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    user_email            TEXT NOT NULL,
    steps                 TEXT NOT NULL,                  -- JSON 数组，如 ["narrate","redact"]
    profile_id            TEXT,
    redact_list_id        TEXT,
    profile_name          TEXT,
    list_name             TEXT,
    status                TEXT NOT NULL DEFAULT 'queued', -- queued|running|done|failed
    current_step          TEXT,
    step_index            INTEGER NOT NULL DEFAULT 0,
    price_cents           INTEGER NOT NULL DEFAULT 0,     -- 本机恒为 0
    qc_fix_count          INTEGER NOT NULL DEFAULT 0,
    products              TEXT,
    has_qc                BOOLEAN NOT NULL DEFAULT FALSE,
    failed_step           TEXT,
    error                 TEXT,
    error_public          TEXT,
    attempts              INTEGER NOT NULL DEFAULT 0,
    not_before            TIMESTAMPTZ,
    pp_rate_cents_per_min INTEGER,
    audio_seconds         INTEGER,
    free_seconds          INTEGER,
    degraded_steps        TEXT,
    ds_cost_cny           NUMERIC,
    ui_lang               TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT (now()),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT (now())
);
CREATE INDEX IF NOT EXISTS idx_pp_jobs_status ON postprocess_jobs (status, updated_at);
CREATE INDEX IF NOT EXISTS idx_pp_jobs_email ON postprocess_jobs (user_email);

-- ── 定字运行时配置（单行表）──
CREATE TABLE IF NOT EXISTS p3_config (
    id                  INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    fly_max_machines    INTEGER,
    claude_concurrency  INTEGER,
    pro_concurrency     INTEGER,
    force_engine        TEXT CHECK (force_engine IN ('pro', 'flash')),
    force_expires_at    TIMESTAMPTZ,
    updated_by          TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT (now()),
    max_transcribe_jobs INTEGER
);

-- ── 节点事件：术语库助手等「别处没有痕迹」的调用 ──
CREATE TABLE IF NOT EXISTS node_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TIMESTAMPTZ NOT NULL DEFAULT (now()),
    node    TEXT NOT NULL,
    outcome TEXT NOT NULL,
    ms      INTEGER,
    ref     TEXT,
    note    TEXT
);
CREATE INDEX IF NOT EXISTS idx_node_events_node_at ON node_events (node, at DESC);

-- ── 告警留痕 ──
CREATE TABLE IF NOT EXISTS admin_alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TIMESTAMPTZ NOT NULL DEFAULT (now()),
    last_at    TIMESTAMPTZ NOT NULL DEFAULT (now()),
    tier       TEXT NOT NULL,
    dedup_key  TEXT,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    mailed     TEXT NOT NULL DEFAULT 'pending',
    mail_error TEXT,
    suppressed INTEGER NOT NULL DEFAULT 0,
    handled_at TIMESTAMPTZ,
    handled_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_admin_alerts_at    ON admin_alerts (at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_alerts_dedup ON admin_alerts (dedup_key, last_at DESC);

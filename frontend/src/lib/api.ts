import { preferredLang } from "./i18n";
import { apiError } from "./userErrors";
import type { ReviewItem } from "./reviewData";
import { authHeaders } from "./auth";

export interface TranscriptRow {
  t: string;          // "HH:MM:SS"
  s: string;          // 文本
  sp?: string | null; // 说话人显示名
}

export interface JobMetrics {
  parts?: number;        // P0 切出的语音段数
  chars?: number;        // P1 已识别字数
  alignedSegs?: number;  // P2 对齐段数
  speakers?: number;     // P3 说话人数
  finalSegs?: number;    // P3 终稿段数
  durationSec?: number;  // 音频时长（秒）
  elapsedSec?: number;   // 转录用时（秒）
}

export interface JobStatus {
  status: "queued" | "running" | "done" | "failed";
  phase: string | null;   // P0|P1|P2|P3|done|null
  progress: number;       // 0–100
  error: string | null;
  metrics: JobMetrics | null;
}

const BASE = "/api";

// ── 账户（P1 真实登录 + 余额账本）──
/** 免费额度（定价 V2 §2）：granted 整分钟、left 秒；limited=同网络注册过多（IP 闸），额度为 0 */
export interface FreeQuota { grantedMinutes: number; leftSeconds: number; limited: boolean }
/** 推荐礼金（2026-09-03）：账户页「推荐同行」块 + 被推荐人的待解锁行。老后端没这个字段 → 整块不显示 */
export interface ReferralInfo {
  /** 礼金金额（分）——界面上三处文案都读它，别在前端写死（老后端没有 → 兜底 500） */
  giftCents?: number;
  code: string | null; link: string | null;
  referredCount: number; unlockedCents: number;
  pending: { amountCents: number; expiresAt: string } | null;   // 我是被推荐人、还没首充
  received: boolean;                                            // 我的那份礼金已到账
}
export interface Me { email: string; balanceCents: number; isAdmin?: boolean; free?: FreeQuota; referral?: ReferralInfo }
export interface LedgerRow {
  id: number; kind: "charge" | "pp_charge" | "topup" | "refund" | "gift" | "adjust"; amountCents: number;
  jobId: string | null; fileName: string | null; lang: string | null;
  durationSec: number | null; source: string | null; invoiced: boolean; createdAt: string;
  /** 这笔充值已被退回多少（分）。只有 topup 行会非零；老后端没这个字段 → 当 0。 */
  refundedCents?: number;
}
export interface JobRow {
  id: string; status: "queued" | "running" | "done" | "failed"; progress: number;
  phase: string | null;   // P0..P4/done/null；历史行时间爬升需要（P2/P3 无子进度）
  lang: string | null; fileName: string | null; durationSec: number | null;
  createdAt: string; error: string | null;
  // 本单实际花费（分），后端下发：已结算=账本真实扣费，在途=当前冻结的预扣额。
  // 前端不许拿单价自己乘——每单的费率是上传时钉死的快照，促销到期后重算历史单必然错。
  costCents?: number;
  // 后处理增量（契约 docs/postprocess-implementation-contract.md）：无任务 = null/缺省
  postprocess?: {
    status: "queued" | "running" | "done" | "failed";
    stepIndex: number; totalSteps: number; currentStep: string | null;
    qcFixCount: number; products: string[];
  } | null;
}

async function postJson(path: string, body: unknown, authed = false): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(authed ? authHeaders() : {}) },
    body: JSON.stringify(body),
  });
}

// 开发模式（后端未配邮件服务）会返回 devCode，前端自动填入
/** lang = 当前界面语言：验证码邮件按它选模板（后端不认识就回落英文） */
export async function requestLoginCode(email: string, lang: string): Promise<{ devCode?: string }> {
  const r = await postJson("/auth/request-code", { email, lang });
  if (!r.ok) {
    // 把状态码带给调用方，便于登录卡区分「限流(429)」与「发信失败」给不同提示
    throw await apiError(r, `request-code ${r.status}`);
  }
  return r.json();
}

/** source = 第一次落地时存下的注册来源（lib/signupSource），只在新建用户那次落库 */
export async function verifyLoginCode(email: string, code: string, source?: unknown): Promise<{ token: string } & Me> {
  const r = await postJson("/auth/verify", { email, code, source: source ?? null });
  if (!r.ok) throw new Error(`verify ${r.status}`);
  return r.json();
}

export async function apiLogout(): Promise<void> {
  await postJson("/auth/logout", {}, true).catch(() => {});
}

export async function getMe(): Promise<Me> {
  const r = await fetch(`${BASE}/me`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`me ${r.status}`);
  return r.json();
}

/** 注销前体检：能不能删、会删掉什么。**规则只在后端**——前端照抄一份余额/在途判断，
 *  迟早会跟后端分家，而分家的表现是「按钮能点，点了报错」。 */
export interface DeletePreflight {
  canDelete: boolean;
  blockers: string[];      // 不能删的原因（已是成句文案，直接显示）
  balanceCents: number;
  jobs: number;
  glossaries: number;
}

export async function getDeletePreflight(): Promise<DeletePreflight> {
  const r = await fetch(`${BASE}/account/delete-preflight`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`delete-preflight ${r.status}`);
  return r.json();
}

/** 重跑一个失败的任务：后端用原来那份录音**另建一单**（费率沿用原单快照）。返回新单 id。
 *  后端的三种拒绝各有各的话要说，所以把 detail 原样抛出去给调用方显示：
 *  402 余额不足 / 410 录音过了保留期 / 409 这单不是失败态。 */
export async function retryJob(jobId: string): Promise<{ jobId: string }> {
  const r = await fetch(`${BASE}/jobs/${jobId}/retry?ui_lang=${encodeURIComponent(preferredLang())}`,
                        { method: "POST", headers: authHeaders() });
  if (!r.ok) {
    throw await apiError(r, `retry ${r.status}`);
  }
  return r.json();
}

/** 删掉全部转录与音频，账户留着（术语库、余额、账单不动）。 */
export async function purgeTranscripts(): Promise<{ jobs: number; files: number }> {
  const r = await fetch(`${BASE}/transcripts`, { method: "DELETE", headers: authHeaders() });
  if (!r.ok) {
    throw await apiError(r, `purge-transcripts ${r.status}`);
  }
  return r.json();
}

export async function deleteAccount(confirmEmail: string): Promise<{ jobs: number; files: number }> {
  const r = await fetch(`${BASE}/account`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ confirmEmail }),
  });
  if (!r.ok) {
    throw await apiError(r, `delete-account ${r.status}`);
  }
  return r.json();
}

export async function listJobs(): Promise<JobRow[]> {
  const r = await fetch(`${BASE}/jobs`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`jobs ${r.status}`);
  return (await r.json()).jobs;
}

export async function getLedger(): Promise<LedgerRow[]> {
  const r = await fetch(`${BASE}/ledger`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`ledger ${r.status}`);
  return (await r.json()).ledger;
}

/** 「在路上」的充值（2026-09-06）：后端每次都顺手向 Paddle 对账，付了就入账、这一笔就不再出现。 */
export interface PendingTopup { txnId: string; amountCents: number; createdAt: string; status: string }
export async function getPendingTopups(): Promise<{ items: PendingTopup[]; balanceCents: number }> {
  const r = await fetch(`${BASE}/topups/pending`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`pending ${r.status}`);
  return r.json();
}

// 充值：开发模式直接到账（返回新余额）；配了 Stripe 则返回支付页 URL
export async function topup(amountCents: number): Promise<{ balanceCents?: number; checkoutUrl?: string }> {
  const r = await postJson("/topups", { amountCents }, true);
  if (!r.ok) throw new Error(`topup ${r.status}`);
  return r.json();
}

export async function markInvoiced(ids: number[]): Promise<void> {
  const r = await postJson("/ledger/invoice", { ids }, true);
  if (!r.ok) throw new Error(`invoice ${r.status}`);
}

// ── 术语库（每用户一本；登录态持久化）──
export interface Glossary {
  id: string;
  name: string;
  language: string | null;
  content: string;
  updatedAt: string;
}

function mapGlossary(g: any): Glossary {
  return { id: g.id, name: g.name, language: g.language ?? null, content: g.content ?? "", updatedAt: g.updated_at };
}

export async function listGlossaries(): Promise<Glossary[]> {
  const r = await fetch(`${BASE}/glossaries`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`glossaries ${r.status}`);
  return ((await r.json()).glossaries ?? []).map(mapGlossary);
}

/** create/update 共用：违规时把后端 detail 抛出（前端按文案区分重名/超限/上限）。 */
async function glossaryMutate(method: string, path: string, body: object): Promise<Glossary> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    throw await apiError(r, `glossary ${method} ${r.status}`);
  }
  return mapGlossary(await r.json());
}

export const createGlossary = (name: string, language: string | null, content: string) =>
  glossaryMutate("POST", "/glossaries", { name, language, content });

export const updateGlossary = (id: string, name: string, language: string | null, content: string) =>
  glossaryMutate("PUT", `/glossaries/${id}`, { name, language, content });

// ── 术语库「协助建库」（起草 / 体检）──
// 两条都**不写库**：结果只进编辑器缓冲区，落库仍走 updateGlossary。
// 引擎不可用时后端返 503，这里把 detail 抛出去，让界面区分「服务暂时不可用」与「没有可收的词」。

/** 起草结果的一项，顺序即写入编辑器的顺序。三种形态：
 *  - `{cat}`  分类行
 *  - `{term, def}`  条目行；**def 可以是空串** = 模型看见了但拿不准，留给用户填
 *  - `{gap}`  缺口，属待办层，**不写进编辑器**，由调用方分流到右栏 */
export type DraftItem = { cat: string } | { term: string; def: string } | { gap: string };

/** 缺口：待办层的另一半。**与库正文分表存**——正文会作为硬证据注入转录引擎，
 *  而这是给人看的一句「这一类你还没写」。 */
export interface Gap {
  id: string;
  text: string;
  status: "open" | "handled";
}

export async function listGaps(gid: string): Promise<Gap[]> {
  const r = await fetch(`${BASE}/glossaries/${gid}/gaps`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`listGaps ${r.status}`);
  return (await r.json()).gaps ?? [];
}

export async function setGapStatus(gid: string, gapId: string, status: Gap["status"]): Promise<Gap[]> {
  const r = await fetch(`${BASE}/glossaries/${gid}/gaps/${gapId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ status }),
  });
  if (!r.ok) throw new Error(`setGapStatus ${r.status}`);
  return (await r.json()).gaps ?? [];
}

export interface GlossaryChecks {
  /** 缺口：属待办层，后端已随库落表，这里回给前端立刻显示 */
  gaps?: string[];
  add: { term: string; def: string; cat: string }[];
  edit: { term: string; def: string; why: string }[];
  del: { term: string; why: string }[];
}

async function assistPost<T>(path: string, body: object): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    // 助手写进库里的分类名 / 释义 / 理由跟界面语言走（术语本身是材料里的原词，不动）
    body: JSON.stringify({ ...body, uiLang: preferredLang() }),
  });
  if (!r.ok) {
    throw await apiError(r, `glossary assist ${r.status}`);
  }
  return r.json();
}

/** 大纲 .docx → 纯文本。txt/md 不走这里（前端自己读，不上传，见 lib/outlineFile.ts）。
 *  multipart 不能自己设 Content-Type——boundary 要浏览器生成，手写的话后端解析不出。 */
export async function readOutlineDocx(file: File): Promise<{ text: string; chars: number; truncated: boolean }> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${BASE}/glossaries/outline`, { method: "POST", headers: authHeaders(), body: fd });
  if (!r.ok) {
    throw await apiError(r, `outline ${r.status}`);
  }
  return r.json();
}

export const draftGlossary = (outline: string) =>
  assistPost<{ items: DraftItem[] }>("/glossaries/draft", { outline }).then((d) => d.items ?? []);

/** 流式起草：每收到一条就回调一次，不等整份跑完。
 *
 *  一份 50 条的库要跑一两分钟，非流式那版这段时间界面上一个字都没有。走流式之后
 *  首条几秒内就落进编辑器，`signal` 中断也就真的停在半路（此前「停止」只是停止播放，
 *  模型早跑完了、token 照烧）。
 *
 *  中途出错走流内 `{error}` 事件，因为那时 HTTP 200 早已发出，改不了状态码了。 */
export async function draftGlossaryStream(
  outline: string,
  onItem: (it: DraftItem) => void,
  signal?: AbortSignal,
  glossaryId?: string,
): Promise<void> {
  const r = await fetch(`${BASE}/glossaries/draft/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    // 带上库 id，后端把产出的缺口随库落表（用户可以一条不补直接保存，下次还在）
    body: JSON.stringify({ outline, glossaryId, uiLang: preferredLang() }),
    signal,
  });
  if (!r.ok) {
    throw await apiError(r, `glossary draft ${r.status}`);
  }
  const reader = r.body?.getReader();
  if (!reader) throw new Error("glossary draft: no stream");
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    // SSE 以空行分帧；最后一段可能是半帧，留在 buf 里等下一块
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") return;
      let obj: DraftItem & { error?: string };
      try {
        obj = JSON.parse(payload);
      } catch {
        continue;
      }
      if (obj.error) throw new Error(obj.error);
      onItem(obj);
    }
  }
}

export const checkGlossary = (content: string) =>
  assistPost<GlossaryChecks>("/glossaries/check", { content });

/** 体检：结果仍是一次性的，走 SSE 只为把连接撑住。
 *
 *  2026-08-05 生产实测 502：普通 JSON 请求要等模型跑完（一百多秒）才发响应头，
 *  Render 网关等不及就掐了。后端改成流式 + 心跳，这里跟着改读法。 */
export async function checkGlossaryStream(content: string, glossaryId?: string): Promise<GlossaryChecks> {
  const r = await fetch(`${BASE}/glossaries/check/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ content, glossaryId, uiLang: preferredLang() }),
  });
  if (!r.ok) {
    throw await apiError(r, `glossary check ${r.status}`);
  }
  const reader = r.body?.getReader();
  if (!reader) throw new Error("glossary check: no stream");
  const dec = new TextDecoder();
  let buf = "";
  let out: GlossaryChecks | null = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;      // ": ping" 心跳行落在这里被忽略
      const payload = line.slice(5).trim();
      if (payload === "[DONE]") {
        if (out) return out;
        throw new Error("glossary check: empty result");
      }
      const obj = JSON.parse(payload);
      if (obj.error) throw new Error(obj.error);
      out = obj as GlossaryChecks;
    }
  }
  if (out) return out;
  throw new Error("glossary check: stream ended early");
}

export async function deleteGlossary(id: string): Promise<void> {
  const r = await fetch(`${BASE}/glossaries/${id}`, { method: "DELETE", headers: authHeaders() });
  if (!r.ok) throw new Error(`deleteGlossary ${r.status}`);
}

// ── 后处理（视角转换 narrate / 脱敏 redact）──
// 契约：docs/postprocess-implementation-contract.md。字段 camelCase 按契约原样。
// 归类 categorize 已于 2026-08-17 下架；**存量任务的产物里还有它**，
// 所以展示侧（步骤名/产物名）保留只读映射，可选步骤里不再出现。
export type PpStep = "narrate" | "redact";
export type PpStepLegacy = PpStep | "categorize";
export interface RedactList { id: string; name: string; content: string; updatedAt: string }

export interface PostprocessStatus {
  status: "queued" | "running" | "done" | "failed";
  steps: PpStepLegacy[];    // 存量任务可能含已下架的 categorize
  stepIndex: number;        // 1 起
  totalSteps: number;
  currentStep: PpStepLegacy | null;
  products: { kind: PpStepLegacy; name: string }[];
  qcFixCount: number;
  hasQcReport: boolean;
  failedStep?: string;
  priceCents: number;
  profileName?: string;
  listName?: string;
  updatedAt: string;
}

/** CRUD 共用：违规（重名/超限/422 详错）把后端 detail 抛出给界面。 */
async function ppMutate<T>(method: string, path: string, body?: object): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    throw await apiError(r, `postprocess ${method} ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export async function listRedactLists(): Promise<RedactList[]> {
  const r = await fetch(`${BASE}/postprocess/redact-lists`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`redactLists ${r.status}`);
  // 契约只定了单条字段形状，未定列表包字段名——两种命名都接（后端定稿后可收敛）
  const j = await r.json();
  return j.lists ?? j.redactLists ?? [];
}
export const createRedactList = (name?: string, content?: string) =>
  ppMutate<{ id: string }>("POST", "/postprocess/redact-lists", { name, content });
export const updateRedactList = (id: string, name: string, content: string) =>
  ppMutate<{ ok: true }>("PUT", `/postprocess/redact-lists/${id}`, { name, content });
export const deleteRedactList = (id: string) =>
  ppMutate<{ ok: true }>("DELETE", `/postprocess/redact-lists/${id}`);

/** 发起后处理。
 *  ⚠️ `uiLang` 是**发起这一刻**的界面语言，后端存在这一单上：质检报告里「我们写的字」
 *  跟它走（产物正文跟录音语种走）。不传的话报告会回落英文。 */
export async function startPostprocess(
  jobId: string,
  req: { steps: PpStep[]; profileId?: string; redactListId?: string | null; uiLang?: string },
): Promise<void> {
  await ppMutate<{ ok: true }>("POST", `/jobs/${jobId}/postprocess`, req);
}

/** 无任务 → 后端返回 200 + null（旧版返回 404，两种都当 null 收：
 *  前后端分开部署，中间有一段两版并存的窗口）。其余错误抛出（轮询方决定重试）。 */
export async function getPostprocess(jobId: string): Promise<PostprocessStatus | null> {
  const r = await fetch(`${BASE}/jobs/${jobId}/postprocess`, { headers: authHeaders() });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`getPostprocess ${r.status}`);
  return (await r.json()) ?? null;
}

// kind 收 Legacy：存量任务的归类纪要还要能下载
// name = 下载文件名（不含扩展名），由调用方拼成「音频名-后缀」。不传则用后端缺省（「脱敏稿」等），
// 那正是 2026-08-27 巡检里的毛病：三单的脱敏稿下下来全叫同一个名字，分不出是哪一单的。
/** ⚠️ `ui` 不能省：产物里的说话人存的是代号（脱敏稿是问答体，行首就是它），
 *  后端按这个参数换成界面语言。不传的话德语用户下到的每一行都是「主持人：…」。 */
export function ppProductUrl(jobId: string, kind: PpStepLegacy, ext: "md" | "txt" | "docx", name: string | undefined, ui: string): string {
  const q = new URLSearchParams({ ui });
  if (name) q.set("name", name);
  return `${BASE}/jobs/${jobId}/postprocess/product/${kind}.${ext}?${q}`;
}
export function ppQcUrl(jobId: string, name?: string): string {
  const q = name ? `?name=${encodeURIComponent(name)}` : "";
  return `${BASE}/jobs/${jobId}/postprocess/qc.md${q}`;
}

/** 脱敏改动清单一条：kind 是后端按替换词形态推断的类别，认不出为 "other"。 */
export interface RedactChange {
  kind: "company" | "person" | "contact" | "geo" | "number" | "project" | "other";
  from: string;
  to: string;
  count: number;
  /** 逐处。每一处自带上下文，所以「改得对不对」不依赖能不能跳回左侧正文。 */
  spots: RedactSpot[];
}

/** 一处脱敏：第几行的第几处替换 + 它的上下文。 */
export interface RedactSpot {
  /** 脱敏稿行号（改口时回传给后端）。 */
  line: number;
  /** 这一处在该行里的序号——同一行同一个词改两处时，只有它能把两处分开。 */
  idx: number;
  /** 对应正文第几段；脱敏不是第一步时为 null（原话已被重写，跳不回去）。 */
  seg: number | null;
  /** 脱敏前的这一句（已裁成窗口），`s`/`e` 是原词在 `text` 里的区间。 */
  before: { text: string; s: number; e: number };
  /** 脱敏后的这一句，`s`/`e` 是替换词的区间。 */
  after: { text: string; s: number; e: number };
  /** 上一句 / 下一句（脱敏后，已截断）。没有则 null。 */
  prev: string | null;
  next: string | null;
}

/** 用户对某一处脱敏的改口。`value === from` 即「保留原词」。 */
export interface RedactOverride {
  line: number;
  /** 该行第几处。老数据可能没有——后端此时按整行同名一起作用。 */
  idx?: number;
  from: string;
  to: string;
  value: string;
}

/** 取脱敏改动清单 + 已保存的改口。
 *  后端一切异常都吞成空清单，所以拿到空只表示「没得看」，不是出错。 */
/** 脱敏改动清单 + 已保存的改口。空清单＝没得看（后端把一切异常都吞成空），不是出错。 */
export interface PpChanges { changes: RedactChange[]; overrides: RedactOverride[] }

export async function getPpChanges(jobId: string): Promise<PpChanges> {
  const r = await fetch(`${BASE}/jobs/${jobId}/postprocess/changes`, { headers: authHeaders() });
  if (!r.ok) return { changes: [], overrides: [] };
  const j = await r.json();
  return { changes: j?.changes ?? [], overrides: j?.overrides ?? [] };
}

/** 保存改口（整表覆盖）。**不重跑、不计费**——取稿与导出时后端才应用。 */
export async function putPpOverrides(jobId: string, overrides: RedactOverride[]): Promise<void> {
  const r = await fetch(`${BASE}/jobs/${jobId}/postprocess/redact/overrides`, {
    method: "PUT",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ overrides }),
  });
  if (!r.ok) throw new Error(await r.text());
}

export function uploadJob(
  file: File,
  lang: string,
  durationSec?: number | null,
  onProgress?: (pct: number) => void,
  glossaryId?: string | null,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("lang", lang);
    if (durationSec != null) fd.append("duration_sec", String(Math.round(durationSec)));
    if (glossaryId) fd.append("glossary_id", glossaryId);
    // 上传那一刻的界面语言，钉进这一单（jobs.ui_lang）。P3 报告里「我们写的说明」按它出。
    // 与 lang（录音语种）是两回事：lang 决定跑哪几条轨，这个决定用户读到的字是哪门语言。
    fd.append("ui_lang", preferredLang());
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/jobs`);
    const auth = authHeaders();
    if (auth.Authorization) xhr.setRequestHeader("Authorization", auth.Authorization);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status === 200) return resolve(JSON.parse(xhr.responseText).jobId);
      // ⚠️ 此前这里只抛 `upload 413`，后端说的话被整个丢掉——传了一个超大文件的用户
      // 看到的就是「upload 413」四个字（2026-08-30 顺手修）。现在把 code 带出去，
      // 由 `useErrText` 出界面语言的文案。
      let detail: unknown = null;
      try { detail = JSON.parse(xhr.responseText)?.detail; } catch { /* 不是 JSON 就算了 */ }
      if (detail && typeof detail === "object" && typeof (detail as { code?: string }).code === "string") {
        const d = detail as { code: string; msg?: string; params?: Record<string, string | number> };
        return reject(Object.assign(new Error(String(d.msg || d.code)),
                                    { status: xhr.status, code: d.code, params: d.params ?? {} }));
      }
      reject(Object.assign(new Error(typeof detail === "string" && detail ? detail : `upload ${xhr.status}`),
                           { status: xhr.status }));
    };
    xhr.onerror = () => reject(new Error("upload network error"));
    xhr.send(fd);
  });
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const r = await fetch(`${BASE}/jobs/${jobId}`);
  // 带上状态码：轮询方（lib/flow）要区分「网络抖动（重试）」与「401 会话过期（停轮询）」
  if (!r.ok) throw Object.assign(new Error(`status ${r.status}`), { status: r.status });
  return r.json();
}

export async function getResult(jobId: string): Promise<TranscriptRow[]> {
  const r = await fetch(`${BASE}/jobs/${jobId}/result`);
  if (!r.ok) throw new Error(`result ${r.status}`);
  return r.json();
}

export async function getReview(jobId: string): Promise<ReviewItem[]> {
  const r = await fetch(`${BASE}/jobs/${jobId}/review`);
  if (!r.ok) throw new Error(`review ${r.status}`);
  return r.json();
}

// P1#7 修订同步：把复核修订后的文稿快照存到后端（导出/取稿即用修订版）
export async function saveTranscript(jobId: string, segments: TranscriptRow[]): Promise<void> {
  const r = await fetch(`${BASE}/jobs/${jobId}/transcript`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments }),
  });
  if (!r.ok) throw new Error(`saveTranscript ${r.status}`);
}

// 复核决策账本：修订稿只存「改完的文字」，哪些项已确认得另存——否则重登后确认进度归零。
// 结构由 Result 定义（occRes/skipped），api 层原样透传。
export type ReviewStateBlob = {
  occRes?: Record<string, Record<number, unknown>>;
  skipped?: Record<string, boolean>;
};

export async function getReviewState(jobId: string): Promise<ReviewStateBlob> {
  const r = await fetch(`${BASE}/jobs/${jobId}/review_state`);
  if (!r.ok) throw new Error(`reviewState ${r.status}`);
  return r.json();
}

export async function saveReviewState(jobId: string, state: ReviewStateBlob): Promise<void> {
  const r = await fetch(`${BASE}/jobs/${jobId}/review_state`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  });
  if (!r.ok) throw new Error(`saveReviewState ${r.status}`);
}

export function audioUrl(jobId: string): string {
  return `${BASE}/jobs/${jobId}/audio`;
}

// ⚠️ `ui` 必须带：说话人标签在后端存的是中文，按界面语言翻译发生在导出那一刻。
// 不带的话界面上写 Interviewer、下载下来是「主持人」（见 lib/bidi.ts 与 app/bidi.py）。
export function exportDocxUrl(jobId: string, name: string, ui = "zh"): string {
  return `${BASE}/jobs/${jobId}/export.docx?name=${encodeURIComponent(name)}&ui=${encodeURIComponent(ui)}`;
}

export function exportTxtUrl(jobId: string, name: string, ui = "zh"): string {
  return `${BASE}/jobs/${jobId}/export.txt?name=${encodeURIComponent(name)}&ui=${encodeURIComponent(ui)}`;
}

// ── 运营驾驶舱（仅管理员）──
export interface EngineState { status: string; sec: number | null }   // 各引擎：状态 + 墙钟耗时(秒)
export interface G25fPart {
  index: number; attempts: number; elapsedSec: number;
  inputTokens: number; thinkingTokens: number; outputTokens: number; success: boolean;
}
export interface AdminMetrics extends JobMetrics {
  engines?: Record<string, EngineState>;   // G25F/DB/FA/XF（新任务主轨 tag 可能是 ELV 等）
  primary?: { tag: string; done?: number; running?: number; failed?: number; total?: number };   // 新任务：主引擎分片进度（不含 token 遥测）
  g25f?: {   // 历史任务存量数据（含 token 遥测明细）；新任务改用上面的 primary
    done?: number; running?: number; failed?: number; total?: number;   // 分片进度
    parts?: G25fPart[];                                                  // 每片遥测明细
    onePass?: number; maxAttempts?: number; sumElapsedSec?: number;      // 聚合：一次过数/最多重试/总耗时
    inputTokens?: number; thinkingTokens?: number; outputTokens?: number; costCny?: number;
  };
  // 成本 ¥：P1 各引擎 / P3 / 总。p3Engine 是这单 P3 走的档（后端从产物名解析，见
  // orchestrator 的 cost.p3_engine）——**只在管理页出现**，用户侧 metrics 被 _public_metrics
  // 白名单挡着不下发（供应商名撞去 AI 化红线）。
  cost?: { p1: Record<string, number>; p3: number; p3_engine?: string; total: number };
}
export interface AdminJob {
  id: string; fileName: string | null; userEmail: string | null;
  status: string; phase: string | null; progress: number; attempts: number;
  metrics: AdminMetrics | null; elapsedSec: number;
  recordingType?: string | null; createdAt?: string; lang?: string | null;
}
export interface AdminFailure {
  id: string; fileName: string | null; userEmail: string | null;
  error: string | null; attempts: number; failedAt: string;
}
export interface AdminRecent {
  id: string; fileName: string | null; userEmail: string | null;
  metrics: AdminMetrics | null; doneAt: string;
  recordingType?: string | null; createdAt?: string; lang?: string | null;
  // 成功了但重跑过 = 看门狗回收过一次。这是它在库里留下的唯一痕迹，
  // 不显示的话这类单跟一次过的单完全看不出区别（老数据可能没有这个字段）。
  attempts?: number;
}
export interface AdminPostprocess {
  jobId: string; fileName: string | null; userEmail: string | null;
  status: string;                       // queued | running | done | failed
  steps: string[]; currentStep: string | null; stepIndex: number;
  priceCents: number; qcFixCount: number; hasQc: boolean;
  failedStep: string | null; errorPublic: string | null;
  // 走了降级路的步（Claude 撞顶 → DeepSeek 兜底）。**三态要分清**：
  // null = 老单（那时还没这一列，无从得知）；[] = 跑过、没降级；["narrate"] = 这一步降过。
  degradedSteps: string[] | null;
  /** 降级路的 DeepSeek 花费（人民币）。null = 没降级过 / 老单。仅运营可见。 */
  dsCostCny: number | null;
  attempts: number; updatedAt: string;
}
export interface AdminOverview {
  jobs: AdminJob[];
  failures: AdminFailure[];
  recent: AdminRecent[];
  postprocess?: AdminPostprocess[];     // 后处理任务（收费功能，此前运营页完全看不到）
  summary: { running: number; queued: number; doneToday: number; failedToday: number };
  // 排队多久判失败（后端 env）。前端据此画「排了多久 / 离判死还有多久」——
  // 阈值只有后端知道，前端写死一份必漂。
  queuedMaxHours?: number;
}

export async function getAdminOverview(): Promise<AdminOverview> {
  const r = await fetch(`${BASE}/admin/overview`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`admin ${r.status}`);
  return r.json();
}

// 服务商余额（仅管理员）。category=这家干嘛的，payMode=会不会欠费，unit=余额单位（cny 金额 / hours 时长）。
export interface AdminBalance {
  vendor: string; label: string | null;
  category: "infra" | "asr" | null;
  payMode: "postpaid" | "prepaid_auto" | "prepaid_manual" | null;
  unit: "cny" | "usd" | "hours"; sortOrder: number | null;
  amountCny: number | null; thresholdCny: number | null;
  source: string; note: string | null; updatedAt: string; low: boolean;
  /** 预充值却没设阈值：低余额告警对这家永远不会触发（= 没有保护，不是没有风险）。 */
  noThreshold?: boolean;
}
export async function getAdminBalances(): Promise<AdminBalance[]> {
  const r = await fetch(`${BASE}/admin/balances`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`balances ${r.status}`);
  return (await r.json()).balances;
}
export async function setAdminBalance(b: {
  vendor: string; label?: string; amountCny?: number; thresholdCny?: number; note?: string;
}): Promise<void> {
  const r = await fetch(`${BASE}/admin/balances`, {
    method: "PUT", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify(b),
  });
  if (!r.ok) throw new Error(`setBalance ${r.status}`);
}
export async function refreshAllBalances(): Promise<void> {
  const r = await fetch(`${BASE}/admin/balances/refresh`, { method: "POST", headers: authHeaders() });
  if (!r.ok) throw new Error(`refreshBalances ${r.status}`);
}

// P3 引擎（Claude 无头）健康度（仅管理员）。数字来自真实转录调用的埋点，
// **没任务跑的时段不会变化**；要看此刻通不通用 probeP3（起一台机器真跑一次，异步出结果）。
// 订阅模式拿不到「额度剩余百分比」（令牌模式无额度事件），所以这里是成败统计，不是油表。
export interface AdminP3Health {
  windowHours: number;
  counts: Record<string, number>;          // ok/capped/error/timeout/preempt/concurrency 各几次
  attempted: number;                       // 真打到 Claude 的次数（不含我方限流跳过的）
  successRate: number | null;              // 无样本为 null——显示「无样本」而不是 0%
  capped: { reason: string; window: string | null; until: string } | null;
  // 认证/订阅失效：与撞顶分开的两件事——撞顶会自愈，这个不动手永远不会好（要续订或换令牌）
  authFailing: boolean;
  authNote: string | null;
  // 最后一次真打到引擎的结果（ok/capped/error/timeout/auth）。「此刻好不好」看它，
  // 不看 successRate——那是滚动平均，会被已经修好的故障一直拖着
  lastAttempt: string | null;
  // jobId 用来认领「我刚发起的那次探活」——比对时刻要跨客户端/服务器两个时钟，会误判
  last: { at: string; source: string; outcome: string; window: string | null; note: string | null; jobId: string | null } | null;
  lastOkAt: string | null;
  lastCappedAt: string | null;
  lastCappedWindow: string | null;
  activeSlots: number | null;              // 查不到为 null（不该让整张卡打不开）
  slotLimit: number;
}
export async function getAdminP3Health(hours = 24): Promise<AdminP3Health> {
  const r = await fetch(`${BASE}/admin/p3-health?hours=${hours}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`p3Health ${r.status}`);
  return r.json();
}
// 手动探活：起一台 Fly 机器真跑一次最小调用。返回即「已派出」，结果稍后进埋点，
// 由调用方轮询 getAdminP3Health 拿。失败要把后端 detail 透出来——501/503 各有具体原因。
export async function probeP3(): Promise<{ probeId: string }> {
  const r = await fetch(`${BASE}/admin/p3-probe`, { method: "POST", headers: authHeaders() });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `p3Probe ${r.status}`);
  return r.json();
}

// P3 运行时配置（仅管理员）：并发 / 机器数 / 强制引擎。改完立刻生效，不必重部署——
// 这几个数原本是 env，且**生效在两个不同的地方**（机器数在 Render 派单时读，并发在 Fly
// 机器上读），改 Render env 对后者无效。挪进 DB 后两侧读同一处。
export interface AdminP3Config {
  config: {
    fly_max_machines: number; max_transcribe_jobs: number; claude_concurrency: number;
    force_engine: "pro" | "flash" | null;
    forceExpiresAt: string | null; updated_by: string | null; updatedAt: string | null;
  };
  limits: Record<string, number>;      // 各家账号并发天花板
  perMachineConc: number;              // 每台机器内部开几路（乘数）
  forceMachines: Record<string, number>;  // 强制某档时的机器数（由引擎派生，不可设）
  // 每档「按当前设置会占账号上限几成」——保存前就看得见后果，而不是等着撞 429
  usage: Record<string, { used: number; cap: number; ratio: number; suggestMax: number }>;
  effectiveMaxMachines: number;
}
export async function getAdminP3Config(): Promise<AdminP3Config> {
  const r = await fetch(`${BASE}/admin/p3-config`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`p3Config ${r.status}`);
  return r.json();
}
// 保存。校验不过后端返 400 + 中文原因（哪一项、为什么），务必把 detail 透出去给人看。
export async function saveAdminP3Config(body: {
  flyMaxMachines?: number; maxTranscribeJobs?: number; claudeConcurrency?: number;
  forceEngine?: "pro" | "flash" | null; forceHours?: number;
}): Promise<{ ok: boolean }> {
  const r = await fetch(`${BASE}/admin/p3-config`, {
    method: "PUT", headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `p3Config ${r.status}`);
  return r.json();
}

// 工作流（仅管理员）：八个节点的静态配置视图。**后端只出事实、前端只出文案**——
// 这里的每个数字都是后端 import 真实常量给的，前端一个都不许自己写死（写死就会漂）。
export interface AdminPromptCard { path: string; lines: number; sha: string }
export interface AdminLangPlan { lang: string; primary: string; refs: string[]; normalize: string | null }
export interface AdminWorkflow {
  // dispatcher = 指纹取自派单前台这一份，不是 Fly 任务机器上跑的那一份（见后端模块头）
  promptScope: string;
  imageTag: string | null;
  prompts: Record<string, AdminPromptCard>;
  langPlans: AdminLangPlan[];
  params: Record<string, Record<string, unknown>>;
}
export async function getAdminWorkflow(): Promise<AdminWorkflow> {
  const r = await fetch(`${BASE}/admin/workflow`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`workflow ${r.status}`);
  return r.json();
}
export async function getAdminPrompt(id: string): Promise<{ id: string; path: string; sha: string; text: string }> {
  const r = await fetch(`${BASE}/admin/workflow/prompt?id=${encodeURIComponent(id)}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`prompt ${r.status}`);
  return r.json();
}

// 节点健康度（仅管理员）。**大头是聚合不是埋点**——P1 各引擎、P3 档位、后处理每步都从既有
// 数据查出来，所以上线当天就有历史。前端不重算任何一个数：异常判定也在后端算好（同一套规则
// 算两遍必然漂）。
export interface EngineStat { total: number; ok: number; secPerAudioMin: number | null }
export interface PpStepStat { total: number; ok: number; failed: number; degraded: number; dsCostCny: number }
/** 一个节点近 N 小时的调用统计。P95 与 P50 一起给：要看的往往正是尾部。 */
export interface NodeStat { total: number; ok: number; p50Ms: number | null; p95Ms: number | null }
export interface AdminHealth {
  windowHours: number;
  baselineHours: number;
  p1: Record<string, EngineStat>;
  p1Baseline: Record<string, { pct: number | null; total: number }>;
  p3: { tiers: Record<string, number>; total: number; degradedRatio: number | null; known: number };
  pp: Record<string, PpStepStat>;
  glossary: Record<string, NodeStat>;
  // P0 转码 / P2 对齐。埋点跑在任务机器上，**重建 Fly 镜像之前恒为空**——
  // 空在这里不等于「没跑」，界面上要说清是哪一种。
  phases?: Record<string, NodeStat>;
  ops: {
    loginSends24h: number | null; loginSendCapHint: number;
    watchdogRequeues: number | null;
    gateSlots: Record<string, number | null>; gateLimit: number;
    machinesRunning: number | null;
    recent60: { done: number | null; failed: number | null };
  };
  alerts: {
    engines: { tag: string; pct: number; basePct: number; ok: number; total: number }[];
    degrade: { ratio: number; known: number } | null;
  };
  // 月度趋势。可选：重建 Fly 镜像之前 p3Cache 恒为 0 单（token 明细是新埋的），
  // 而 retry/p3 上线当天就有历史（那两个数一直在库里，只是没人查）。
  monthly?: {
    days: number;
    retry: { total: number; retried: number; ratio: number | null };
    p3: { tiers: Record<string, number>; known: number; degradedRatio: number | null };
    p3Cache: { jobs: number; hit: number; miss: number; out: number; hitRatio: number | null };
  };
}
export async function getAdminHealth(hours = 24): Promise<AdminHealth> {
  const r = await fetch(`${BASE}/admin/health?hours=${hours}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

// 用户视角（2026-08-14）。大头是聚合：余额/免费额度/充值消费/任务/后处理全都已经在库里，
// 只是从没按「人」摆在一起过。
// ⚠️ 列表里**没有文件名**——那只在下钻里给：列表是扫的、下钻是查的，文件名摆在列表上
// 等于每次打开用户页都在浏览所有人的访谈主题。
export interface AdminUser {
  email: string; balanceCents: number; createdAt: string;
  /** 免费额度是另一套账，跟钱并行：单位是分钟，不是金额。 */
  freeMinutesGranted: number; freeMinutesLeft: number; freeLimited: boolean;
  /** 真金白银的充值（**不含注册赠送**——算进来会让「付过钱的」和「只领过赠送的」长得一样）。 */
  topupCents: number;
  spentCents: number;          // 正数（库里是负的出账）
  jobs: number; lastActive: string | null;
  /** 管理员自己的账号：列表里只标不过滤（运维要看全部，增长看板那边才排除） */
  isAdmin: boolean;
  /** 测试域名（@transcribe.solutions）的号：能直充、能模拟消耗、看板里排除；同样只标不过滤 */
  isTest?: boolean;
}
export interface AdminLedgerRow {
  id: number; kind: string; amountCents: number;
  fileName: string | null; lang: string | null; durationSec: number | null;
  source: string | null;
  note: string | null; operator: string | null;   // 只有手工调整那一行有值
  refundedCents: number; at: string;
}
export interface AdminUserJob {
  id: string; fileName: string | null; status: string; lang: string | null;
  durationSec: number | null; attempts: number; errorPublic: string | null; at: string;
}
export interface AdminUserDetail extends AdminUser {
  done: number; failed: number; postprocess: number; glossaries: number; pendingRefunds: number;
  /** 注册来源原文（first-touch，老用户为 null）；推荐码；谁推荐的；他推荐了几个、其中几个充过值 */
  signupSource: Record<string, string> | null;
  referralCode: string | null; referredBy: string | null;
  referrals: { count: number; paid: number };
  ledger: AdminLedgerRow[]; jobs_: AdminUserJob[];
}

// ── 增长（Growth 需求单批次 A/B）：只有计数没有名单 ─────────────────────────────
export type Tri = { thisWeek: number; lastWeek: number; total: number };
export interface AdminGrowth {
  week: string;
  range: { thisWeek: [string, string]; lastWeek: [string, string] };
  generatedAt: string;
  excluded: { adminAccounts: number; testAccounts?: boolean };
  ladder: { monthNetUsd: number; tier: number; steps: number[] };
  funnel: {
    signup: Tri; signupCorp: Tri; signupPersonal: Tri; signupNoQuota: Tri;
    signupSources: { thisWeek: Record<string, number>; lastWeek: Record<string, number>; total: Record<string, number> };
    referredSignups: Tri;
    firstUpload: Tri; signupToUploadMedianHours: { thisWeek: number | null; total: number | null };
    firstDone: Tri; freeExhausted: Tri; firstTopup: Tri;
    firstTopupAmounts: { thisWeek: Record<string, number>; total: Record<string, number> };
    exhaustedToTopupMedianDays: { thisWeek: number | null; total: number | null };
    activePaying: { thisMonth: number; lastMonth: number; total: number };
    secondTopup: Tri; firstToSecondTopupMedianDays: { thisWeek: number | null; total: number | null };
    returnDone: Tri;
    soldHours: { thisWeek: number; lastWeek: number; thisMonth: number; lastMonth: number; total: number };
  };
  money: Record<"thisWeek" | "lastWeek" | "thisMonth" | "lastMonth" | "total",
                { topupCents: number; refundCents: number; netTopupCents: number; soldCents: number }>;
  cost: Record<"thisWeek" | "lastWeek" | "thisMonth" | "lastMonth",
               { cny: number; usdCents: number; marginPct: number | null }> & { usdCnyRate: number; note: string };
  freeGrantSeries: { date: string; corp: number; personal: number }[];
  /** 推荐礼金（批次 C）：解锁次数 / 发放总额（三种来源）/ 同一推荐人下 ≥3 个账号（用码不用邮箱） */
  gifts?: {
    unlocked: Tri;
    cents: Record<"thisWeek" | "lastWeek" | "total", { referee: number; referrer: number; manual: number }>;
    referrerClusters: { code: string; accounts: number; paid: number }[];
    /** 「同一推荐人下 ≥N 个账号」的门槛，由后端定（老后端没有 → 兜底 2） */
    minAccounts?: number;
  };
  report: string;
  error?: string;
}
export async function getAdminGrowth(week = "", includeTests = false): Promise<AdminGrowth> {
  const qs = new URLSearchParams();
  if (week) qs.set("week", week);
  if (includeTests) qs.set("tests", "1");   // 临时把测试号算进来看礼金那几行动不动；周报永远不带
  const q = qs.toString();
  const r = await fetch(`${BASE}/admin/growth${q ? `?${q}` : ""}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`growth ${r.status}`);
  return r.json();
}
/** 把一个邮箱重置成「从没注册过」（测试用）。后端要求把邮箱原样打一遍；管理员账号不许。 */
export async function resetUserAccount(email: string, confirm: string): Promise<Record<string, number>> {
  const r = await fetch(`${BASE}/admin/users/${encodeURIComponent(email)}/reset`, {
    method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ confirm }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `reset ${r.status}`);
  return j;
}
/** 给测试号记一笔模拟消耗（先扣免费分钟、再扣余额）。 */
export async function simulateUsage(email: string, minutes: number): Promise<{ freeSecondsUsed: number; paidSeconds: number; chargedCents: number; balanceCents: number }> {
  const r = await fetch(`${BASE}/admin/users/${encodeURIComponent(email)}/simulate-usage`, {
    method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ minutes }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `simulate ${r.status}`);
  return j;
}
export async function getAdminUsers(q = ""): Promise<{ items: AdminUser[]; adjustMaxCents: number }> {
  const r = await fetch(`${BASE}/admin/users?q=${encodeURIComponent(q)}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`users ${r.status}`);
  return r.json();
}
export async function getAdminUserDetail(email: string): Promise<AdminUserDetail> {
  const r = await fetch(`${BASE}/admin/users/${encodeURIComponent(email)}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`user ${r.status}`);
  return r.json();
}
/** 手工调整余额。三道闸在后端（理由必填 / 单次上限 / 不许扣成负数），被拒时 422 带原话。 */
export async function adjustUserBalance(email: string, deltaCents: number, reason: string) {
  const r = await fetch(`${BASE}/admin/users/${encodeURIComponent(email)}/adjust`, {
    method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ deltaCents, reason }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `adjust ${r.status}`);
  return j as { balanceCents: number; beforeCents: number };
}

// 告警记录（2026-08-14）。此前 13 处告警全是「发完即忘」——没记录、发失败只在日志里留一行、
// 去重记在进程内存里。tier 决定它进不进待办条，见后端 alerts.py 的档位说明。
export interface AdminAlert {
  id: number; tier: "act" | "watch" | "fyi";
  subject: string; body: string;
  /** 邮件那一路的结果：sent 送达 / failed 发失败 / skipped 无管理员或开发模式 / pending 还没写回 */
  mailed: string; mailError: string | null;
  /** 冷却期内又触发了多少次。**这个数以前是丢掉的**——它区分「偶发一次」和「一直在响」。 */
  suppressed: number;
  at: string; handled: boolean; handledBy: string | null;
}
export async function getAdminAlerts(days = 7, unhandled = false): Promise<{ items: AdminAlert[]; retentionDays: number }> {
  const r = await fetch(`${BASE}/admin/alerts?days=${days}&unhandled=${unhandled ? 1 : 0}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`alerts ${r.status}`);
  return r.json();
}
// 驾驶舱「现在发一份增长周报」：立刻把上一周那份发到管理员邮箱（走告警表 fyi 档，列表里也看得到）
export async function sendGrowthReport(): Promise<{ mailed: boolean; subject: string; week: string }> {
  const r = await fetch(`${BASE}/admin/growth/report`, { method: "POST", headers: authHeaders() });
  if (!r.ok) throw new Error(`growth report ${r.status}`);
  return r.json();
}
export async function markAlertHandled(id: number): Promise<{ changed: boolean }> {
  const r = await fetch(`${BASE}/admin/alerts/${id}/handled`, { method: "POST", headers: authHeaders() });
  if (!r.ok) throw new Error(`alert handled ${r.status}`);
  return r.json();
}

// 会到期的凭证。到期是**静默失败**（充值没反应 / 融合全线降级），所以要倒计时不要靠记性。
export interface AdminExpiry {
  id: string; vendor: string; what: string; expires: string;
  impact: string; fix: string; source: string;
  daysLeft: number | null; warn: boolean;
}
export async function getAdminExpiries(): Promise<{ items: AdminExpiry[]; warnDays: number }> {
  const r = await fetch(`${BASE}/admin/expiries`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`expiries ${r.status}`);
  return r.json();
}

// 充值退款（仅管理员）。refundableCents 由后端算（条款第 4 条：14 天内、只退未消耗部分），
// 前端不许自己推——同一套规则算两遍必然漂移。
export interface AdminTopup {
  id: number; amountCents: number; refundedCents: number;
  source: string | null; createdAt: string; ageDays: number;
  isBonus: boolean;          // 注册赠送：用户没付过这笔钱，恒不可退
  refundableCents: number;   // 各行独立算、都受同一份可用额约束，不能相加当总可退额
}
export async function getAdminTopups(email: string): Promise<{ topups: AdminTopup[]; balanceCents: number }> {
  const r = await fetch(`${BASE}/admin/topups?email=${encodeURIComponent(email)}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`topups ${r.status}`);
  return r.json();
}
// 只发起，不动余额——余额等 provider 退款 webhook 回来才扣（见 server/app/payments.py）。
// 失败时把后端的 detail 带出来（501=通道未接入 / 422=超额），运营要看得见「没退成」。
export async function startAdminRefund(email: string, ledgerId: number, amountCents: number): Promise<void> {
  const r = await fetch(`${BASE}/admin/refunds`, {
    method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ email, ledgerId, amountCents }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `refund ${r.status}`);
}

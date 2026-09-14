import { describe, it, expect, vi, beforeEach } from "vitest";
import { requestLoginCode, verifyLoginCode, getJobStatus, getResult, audioUrl, uploadJob, listGlossaries, createGlossary, updateGlossary, deleteGlossary, getAdminTopups, startAdminRefund, retryJob, draftGlossary } from "./api";

beforeEach(() => { vi.restoreAllMocks(); });

describe("api client", () => {
  it("requestLoginCode posts email; verify returns token + me", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true, devCode: "654321" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ token: "tok1", email: "a@b.com", balanceCents: 3000 }) });
    vi.stubGlobal("fetch", fetchMock);
    const r = await requestLoginCode("a@b.com", "de");
    expect(r.devCode).toBe("654321");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/request-code");
    // 界面语言必须发给后端：验证码邮件靠它选模板（漏了就一律英文，德语用户看不出差别在哪）
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ email: "a@b.com", lang: "de" });
    const me = await verifyLoginCode("a@b.com", "654321");
    expect(me.token).toBe("tok1");
    expect(me.balanceCents).toBe(3000);
    // source 是第一次落地时存下的注册来源（lib/signupSource）；没传就发 null，后端存成「未记录」
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ email: "a@b.com", code: "654321", source: null });
  });

  it("getJobStatus fetches status json", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ status: "running", phase: "P1", progress: 15, error: null }),
    }));
    const st = await getJobStatus("job1");
    expect(st.phase).toBe("P1");
  });

  it("getResult returns transcript rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, json: async () => [{ t: "00:00:01", s: "你好", sp: "主持人" }],
    }));
    const rows = await getResult("job1");
    expect(rows[0].s).toBe("你好");
  });

  it("getResult throws on non-ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409 }));
    await expect(getResult("job1")).rejects.toThrow();
  });

  it("audioUrl builds the audio endpoint", () => {
    expect(audioUrl("job1")).toBe("/api/jobs/job1/audio");
  });

  it("uploadJob sends file and lang in form data（recording_type 2026-08-18 起不再发）", async () => {
    const captured: { fd: FormData | null } = { fd: null };
    class FakeXHR {
      upload: Record<string, unknown> = {};
      status = 200;
      responseText = JSON.stringify({ jobId: "job9" });
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      open() {}
      send(fd: FormData) { captured.fd = fd; this.onload?.(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    const id = await uploadJob(new File(["x"], "a.m4a"), "zh", 40, undefined, "gl-7");
    expect(id).toBe("job9");
    expect(captured.fd?.get("lang")).toBe("zh");
    expect(captured.fd?.get("recording_type")).toBeNull();
    expect(captured.fd?.get("glossary_id")).toBe("gl-7");      // 透传选中的术语库 id
    vi.unstubAllGlobals();
  });

  it("uploadJob omits glossary_id when not chosen", async () => {
    const captured: { fd: FormData | null } = { fd: null };
    class FakeXHR {
      upload: Record<string, unknown> = {};
      status = 200;
      responseText = JSON.stringify({ jobId: "job9" });
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      open() {}
      send(fd: FormData) { captured.fd = fd; this.onload?.(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
    await uploadJob(new File(["x"], "a.m4a"), "zh");
    expect(captured.fd?.has("glossary_id")).toBe(false);       // 「不使用」时不带
    vi.unstubAllGlobals();
  });

  it("listGlossaries maps updated_at → updatedAt", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ glossaries: [{ id: "g1", name: "项目A", language: null, content: "FD ｜ 履约", updated_at: "2026-06-23T00:00:00Z" }] }),
    }));
    const gs = await listGlossaries();
    expect(gs[0]).toEqual({ id: "g1", name: "项目A", language: null, content: "FD ｜ 履约", updatedAt: "2026-06-23T00:00:00Z" });
  });

  it("createGlossary POSTs name/language/content and returns mapped row", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id: "g2", name: "项目B", language: "zh", content: "x ｜ y", updated_at: "2026-06-23T01:00:00Z" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const g = await createGlossary("项目B", "zh", "x ｜ y");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/glossaries");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "项目B", language: "zh", content: "x ｜ y" });
    expect(g.id).toBe("g2");
  });

  it("updateGlossary PUTs to /api/glossaries/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ id: "g2", name: "改名", language: null, content: "", updated_at: "2026-06-23T02:00:00Z" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await updateGlossary("g2", "改名", null, "");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/glossaries/g2");
    expect(fetchMock.mock.calls[0][1].method).toBe("PUT");
  });

  it("createGlossary surfaces backend detail on 409 (duplicate name)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "name already exists" }) }));
    await expect(createGlossary("dup", null, "")).rejects.toThrow("name already exists");
  });

  it("deleteGlossary DELETEs /api/glossaries/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    await deleteGlossary("g2");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/glossaries/g2");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
  });
});

describe("admin · 充值退款", () => {
  it("getAdminTopups 按邮箱查，邮箱做 URL 编码（+ 号之类不能生吞）", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ topups: [], balanceCents: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await getAdminTopups("a+b@x.com");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/topups?email=a%2Bb%40x.com");
  });

  it("startAdminRefund 传 email/ledgerId/amountCents（分）", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    await startAdminRefund("u@x.com", 7, 400);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/refunds");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ email: "u@x.com", ledgerId: 7, amountCents: 400 });
  });

  it("失败时把后端 detail 原样抛出——运营要看见「没退成」的真正原因，不能只有状态码", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 501,
      json: async () => ({ detail: "stripe 退款通道未接入，请到该渠道后台手工退款" }),
    }));
    await expect(startAdminRefund("u@x.com", 7, 400)).rejects.toThrow(/未接入/);
  });

  it("后端没给 detail 时兜底带上状态码，不吞成空错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 502, json: async () => { throw new Error("not json"); },
    }));
    await expect(startAdminRefund("u@x.com", 7, 400)).rejects.toThrow(/502/);
  });
});

// ── 上传/重试/术语库助手都要把「当下的界面语言」带给后端（2026-08-30，第三批） ──
//
// 这几条口决定的是**模型写给用户看的字用哪门语言**（复核卡的原因、助手写的分类名与释义）。
// 漏发的症状不是报错，是「德语用户拿到一份中文原因」——跟改动前一模一样，所以只能靠守卫钉。
describe("界面语言随请求发给后端", () => {
  function fakeXhr(captured: { fd: FormData | null }) {
    class FakeXHR {
      upload: Record<string, unknown> = {};
      status = 200;
      responseText = JSON.stringify({ jobId: "j" });
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      open() {}
      setRequestHeader() {}
      send(fd: FormData) { captured.fd = fd; this.onload?.(); }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
  }

  it("上传带 ui_lang，且是当下存的那门", async () => {
    localStorage.setItem("tx_lang", "de");
    const captured: { fd: FormData | null } = { fd: null };
    fakeXhr(captured);
    await uploadJob(new File(["x"], "a.m4a"), "zh");
    expect(captured.fd?.get("ui_lang")).toBe("de");
    vi.unstubAllGlobals();
    localStorage.removeItem("tx_lang");
  });

  it("重试也带，而且跟的是**当下**的界面语言不是原单的", async () => {
    // 费率沿用原单（价格承诺），语言不沿用：重试会重新出一份报告，
    // 用户此刻读得懂哪门语言才是唯一相关的事。
    localStorage.setItem("tx_lang", "ja");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ jobId: "j2" }) });
    vi.stubGlobal("fetch", fetchMock);
    await retryJob("j1");
    expect(String(fetchMock.mock.calls[0][0])).toContain("ui_lang=ja");
    vi.unstubAllGlobals();
    localStorage.removeItem("tx_lang");
  });

  it("术语库助手也带——它写的字是要落进用户自己那本库的", async () => {
    localStorage.setItem("tx_lang", "pt");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await draftGlossary("大纲");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).uiLang).toBe("pt");
    vi.unstubAllGlobals();
    localStorage.removeItem("tx_lang");
  });
});

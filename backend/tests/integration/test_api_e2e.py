"""免费桩端到端：登录态上传（真 WAV、走 ffprobe+预扣）→ 排队 → 本进程 worker(桩 transcribe)
→ 存结果 → API 取结果。不调云、不花钱。

环境要求：compose 起 postgres+minio+**api**（api 须是当前代码镜像，改了 server/ 记得先
`docker compose up -d --build api`），且 **worker 容器要停**（否则容器 worker 会抢先领取
本测试的 job 去跑真实管线 = 真调 ASR 花钱）：
    docker compose up -d --build api && docker compose stop worker
本测试直接驱动一次 worker.process_one()（桩），需本地 env（DATABASE_URL/S3_* 指向本地）。
登录/余额不走邮件（本地 api 可能配了真实 RESEND）：直接 DB 造会话与余额。
"""
import io
import struct
import wave

import pytest
import requests

from app import db, jobstore, worker
from pipeline.transcript import Segment

API = "http://localhost:8000"
EMAIL = "e2e@test.local"
TOKEN = "e2e-test-token"


def _tiny_wav_bytes(seconds: float = 0.5, rate: int = 8000) -> bytes:
    """生成一段真实可被 ffprobe 识别时长的静音 WAV（服务端现在会实测时长并按它预扣）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


def _seed_session_and_balance() -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (email, balance_cents) VALUES (%s, 100000) "
            "ON CONFLICT (email) DO UPDATE SET balance_cents = 100000", (EMAIL,))
        conn.execute(
            "INSERT INTO sessions (token, email, expires_at) VALUES (%s, %s, now() + interval '1 hour') "
            "ON CONFLICT (token) DO UPDATE SET expires_at = now() + interval '1 hour'", (TOKEN, EMAIL))


@pytest.mark.infra
def test_full_http_flow_with_stub_pipeline(monkeypatch):
    _seed_session_and_balance()
    # 桩**收下任何参数**（`*_, **__`），不照抄真函数的签名。
    # ⚠️ 这不是偷懒，是这条测试长红 19 天的直接原因：原来的桩把当时的形参逐个写死，
    # 之后真函数加了 `on_report`（复核卡按时间码锚定那轮）与 `ui_lang`，桩不认，
    # worker 一调就 TypeError → 任务判失败 → 取稿 409。反方向也漂了：桩还留着
    # worker 早就不传的 `recording_type`。**照抄签名的桩，每次改真函数都会漂一次**，
    # 而症状是「端到端测试红了」，看起来像环境问题，于是被一路记成「已知红」。
    # 本测试要验的是 HTTP + 存储 + 取稿这条链路，transcribe 收了什么参数与它无关。
    monkeypatch.setattr(
        worker, "transcribe",
        lambda *_, **__: ([Segment(t="00:00:01", s="你好", sp="主持人")], []),
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    r = requests.post(
        f"{API}/api/jobs",
        files={"file": ("a.wav", io.BytesIO(_tiny_wav_bytes()), "audio/wav")},
        data={"lang": "zh"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    # 领**这一单**来跑，不用 `process_one()`（它领的是「最老的 queued」）。
    # ⚠️ 这不是洁癖：本地库里只要还剩一条别的 queued（上一次跑失败留下的、或手写脚本造的），
    # `process_one()` 就会去跑那一条并照样返回 True，而本测试的断言全部落空——
    # 补上「这一单真的跑成了」那条断言时，当场就撞见了这个情况。
    # 也不用 `run_one_job()`：它会注入全局的 Claude / 引擎闸，那些是模块级状态，
    # 会漏进同一进程里后面跑的测试。
    job = jobstore.claim_specific(jid)
    assert job is not None, "刚建的单没能领到——它不在 queued？"
    worker._process_job(job)

    # ⚠️ 先断言**任务真的跑成了**，并把库里存的异常带进失败信息。
    # 不这么做的话，任何在 worker 内部抛出的错都会被它的兜底 except 转成「任务失败」，
    # 这条测试只会报一个 `409 != 200`——一个与真实原因毫不相干的数字。
    # 这正是它长红 19 天没人查的原因：症状看起来像取稿接口的问题（或干脆像环境问题），
    # 而实际是桩的签名跟真函数分了家。**报错要指向原因，不是指向症状。**
    with db.connect() as conn:
        st, err = conn.execute(
            "SELECT status, error FROM jobs WHERE id = %s", (jid,)).fetchone()
    assert st == "done", f"任务没跑成（status={st}），worker 里存下的异常：\n{err}"

    res = requests.get(f"{API}/api/jobs/{jid}/result", headers=headers)
    assert res.status_code == 200
    # 先断言取回来有东西：直接下标取的话，空稿会报一个 IndexError，
    # 那又是「指向症状不指向原因」。真实路径上空稿由 orchestrator 抛异常拦住
    # （「不出空稿不计费」），这里只是不让报错退化。
    body = res.json()
    assert body, "取回的稿是空的——存稿或取稿这一步丢了内容"
    assert body[0]["s"] == "你好"

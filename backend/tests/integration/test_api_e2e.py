"""免费桩端到端：上传（真 WAV、走 ffprobe）→ 排队 → 本进程 worker(桩 transcribe)
→ 存结果 → API 取结果。不调云、不花钱。

本机版（与线上不同）：线上要 docker 起 postgres+minio+api、用 requests 打 localhost:8000，并直接往库里
造登录会话与余额；本机没有登录与余额，接口用 TestClient 在本进程里调，库与存储是 conftest 给的临时库和临时文件夹。
"""
import io
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from app import api, db, jobstore, worker
from pipeline.transcript import Segment


def _tiny_wav_bytes(seconds: float = 0.5, rate: int = 8000) -> bytes:
    """生成一段真实可被 ffprobe 识别时长的静音 WAV（服务端现在会实测时长并按它预扣）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


@pytest.mark.infra
def test_full_http_flow_with_stub_pipeline(monkeypatch):
    client = TestClient(api.app)
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
    r = client.post(
        "/api/jobs",
        files={"file": ("a.wav", io.BytesIO(_tiny_wav_bytes()), "audio/wav")},
        data={"lang": "zh"},
    )
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    # 领**这一单**来跑，不用 `process_one()`（它领的是「最老的 queued」）。
    # ⚠️ 这不是洁癖：本地库里只要还剩一条别的 queued（上一次跑失败留下的、或手写脚本造的），
    # `process_one()` 就会去跑那一条并照样返回 True，而本测试的断言全部落空——
    # 补上「这一单真的跑成了」那条断言时，当场就撞见了这个情况。
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

    res = client.get(f"/api/jobs/{jid}/result")
    assert res.status_code == 200
    # 先断言取回来有东西：直接下标取的话，空稿会报一个 IndexError，
    # 那又是「指向症状不指向原因」。真实路径上空稿由 orchestrator 抛异常拦住
    # （「不出空稿不计费」），这里只是不让报错退化。
    body = res.json()
    assert body, "取回的稿是空的——存稿或取稿这一步丢了内容"
    assert body[0]["s"] == "你好"

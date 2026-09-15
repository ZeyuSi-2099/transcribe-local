"""上传时服务端测时长 + 强制时长上限 + 按实测预扣余额的集成测试。

真打本地 Postgres + MinIO + 真 ffprobe（不 mock）。只碰本文件专属测试 email/token 的
users/jobs/ledger/sessions 行，自足清理，不做全表清空，不影响其他数据。

本机版（与线上不同）：库与存储是 conftest 给的临时库和临时文件夹；没有登录、余额与预扣——
测时长、存音频、时长上限这几条改成不带登录照跑，费率与余额那三条登记为不适用（conftest.py）。
"""
import io
import math
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import api, blobstore, config, db

pricing = None   # 本机版：计费模块不搬；只有登记为不适用的用例用到它

EMAIL = "create-job-test@example.com"
TOKEN = "test-token-create-job-integration"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _tiny_wav_bytes(duration_sec: float = 2.0) -> bytes:
    """用 ffmpeg lavfi 现生成一段极短的真实 wav，不落中间文件，直接吐到 stdout。"""
    out = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_sec}",
         "-ar", "16000", "-f", "wav", "pipe:1"],
        capture_output=True, check=True,
    )
    return out.stdout


def _wipe():
    # 本机版：每个测试本来就是一个全新的临时库，这里只清 jobs（没有 ledger / sessions / users 表）
    with db.connect() as conn:
        conn.execute("DELETE FROM jobs WHERE user_email = %s", (EMAIL,))


@pytest.fixture(autouse=True)
def _clean():
    db.init_schema()
    blobstore.ensure_bucket()
    _wipe()
    yield
    _wipe()


def _login(balance_cents: int) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO users (email, balance_cents) VALUES (%s, %s)", (EMAIL, balance_cents))
        conn.execute(
            "INSERT INTO sessions (token, email, expires_at) VALUES (%s, %s, now() + interval '1 day')",
            (TOKEN, EMAIL),
        )


@pytest.mark.infra
def test_create_job_probes_duration_and_stores_audio():
    # 本机版：线上这条叫「余额够 → 预扣并建单」；本机不收费，守剩下的「服务端实测时长落库 + 音频真存下」
    client = TestClient(api.app)
    r = client.post(
        "/api/jobs",
        files={"file": ("tiny.wav", io.BytesIO(_tiny_wav_bytes(2.0)), "audio/wav")},
        data={"lang": "zh"},
    )
    assert r.status_code == 200
    jid = r.json()["jobId"]
    with db.connect() as conn:
        row = conn.execute(
            "SELECT duration_sec, audio_key FROM jobs WHERE id = %s", (jid,)
        ).fetchone()
    assert row is not None
    duration_sec, audio_key = row
    assert 1 <= duration_sec <= 3          # 服务端 ffprobe 实测 ~2s 落库，不是编造值
    assert blobstore.get_bytes(audio_key)  # 真存进本机文件夹，不抛 NoSuchKey
    blobstore.delete(audio_key)


@pytest.mark.infra
def test_create_job_snapshots_the_rate_and_reserves_at_it():
    """上传即把当时的单价钉死在 job 上，预扣按这个快照算——之后调价/促销到期都动不了这一单。"""
    _login(10000)
    client = TestClient(api.app)
    r = client.post(
        "/api/jobs",
        files={"file": ("tiny.wav", io.BytesIO(_tiny_wav_bytes(2.0)), "audio/wav")},
        data={"lang": "zh"},
        headers=AUTH,
    )
    assert r.status_code == 200
    with db.connect() as conn:
        dur, rate, reserved = conn.execute(
            "SELECT duration_sec, rate_cents_per_min, reserved_cents FROM jobs WHERE id = %s",
            (r.json()["jobId"],),
        ).fetchone()
    assert rate == pricing.rate_cents_per_min("zh")          # 快照 = 上传时中文档的现价
    assert reserved == math.ceil(dur / 60 * rate)            # 预扣按快照，不按全局常量


@pytest.mark.infra
def test_create_job_snapshots_the_same_rate_for_every_language():
    """单档（2026-08-31 定价 V3）：同一段音频，不论哪门语言，钉进 job 的费率快照都一样。

    ⚠️ 这条测的是**落库的那个数**，不是 pricing 模块的返回值——单测已经覆盖后者了。
    真正会出事的地方是「上传路径上还残留着某条按语种取价的分支」（项目费率通道就是），
    而那只有查 jobs 行才看得出来。"""
    _login(10000)
    client = TestClient(api.app)
    wav = _tiny_wav_bytes(2.0)
    rates = {}
    for lang in ("en", "zh", "ru"):
        r = client.post(
            "/api/jobs",
            files={"file": ("tiny.wav", io.BytesIO(wav), "audio/wav")},
            data={"lang": lang},
            headers=AUTH,
        )
        assert r.status_code == 200
        with db.connect() as conn:
            rates[lang] = conn.execute(
                "SELECT rate_cents_per_min FROM jobs WHERE id = %s", (r.json()["jobId"],)
            ).fetchone()[0]
    assert rates["en"] == rates["ru"] == rates["zh"]
    assert rates["en"] == pricing.RATE_CENTS_PER_MIN


@pytest.mark.infra
def test_create_job_insufficient_balance_402_no_job_balance_untouched():
    # 余额 0：预扣 ceil() 保证任何非空音频至少要 1 分钱，所以这必然不够——不依赖当下的具体单价
    # （曾用 1 分钱构造「不足」，中文降到 $0.10/min 后 2 秒音频正好只要 1 分，前提就塌了）。
    _login(0)
    client = TestClient(api.app)
    r = client.post(
        "/api/jobs",
        files={"file": ("tiny.wav", io.BytesIO(_tiny_wav_bytes(2.0)), "audio/wav")},
        data={"lang": "zh"},
        headers=AUTH,
    )
    assert r.status_code == 402
    with db.connect() as conn:
        n = conn.execute("SELECT count(*) FROM jobs WHERE user_email=%s", (EMAIL,)).fetchone()[0]
        bal = conn.execute("SELECT balance_cents FROM users WHERE email=%s", (EMAIL,)).fetchone()[0]
    assert n == 0          # 没建 job
    assert bal == 0        # reserve 本身原子失败、不动库——余额分文未变


@pytest.mark.infra
def test_create_job_server_probed_duration_over_max_is_413(monkeypatch):
    monkeypatch.setattr(config, "MAX_DURATION_SEC", 1)   # 把上限压到 1 秒，逼近超限分支
    client = TestClient(api.app)
    r = client.post(
        "/api/jobs",
        files={"file": ("tiny.wav", io.BytesIO(_tiny_wav_bytes(2.0)), "audio/wav")},   # 真实 2s > 压低后的 1s
        data={"lang": "zh", "duration_sec": "1"},   # 前端即使谎报很短也不影响服务端实测判定
    )
    assert r.status_code == 413
    with db.connect() as conn:
        n = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
    assert n == 0           # 本机版：没有余额可查，只守「没建单」

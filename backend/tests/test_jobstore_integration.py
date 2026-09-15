import pytest

from app import db, jobstore


@pytest.fixture(autouse=True)
def _clean():
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM jobs")
    yield


@pytest.mark.infra
def test_create_then_get():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    job = jobstore.get_job(jid)
    assert job.status == "queued"
    assert job.audio_key == "audio/x.m4a"
    assert job.lang == "zh"


@pytest.mark.infra
def test_progress_and_done():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()   # set_done 的终态持有守卫要求先处于 running
    jobstore.update_progress(jid, "P1", 15)
    assert jobstore.get_job(jid).phase == "P1"
    jobstore.set_done(jid, "result/x.json")
    job = jobstore.get_job(jid)
    assert job.status == "done" and job.progress == 100 and job.result_key == "result/x.json"


@pytest.mark.infra
def test_claim_is_exclusive():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    first = jobstore.claim_next_queued()
    second = jobstore.claim_next_queued()
    assert first is not None and first.id == jid and first.status == "running"
    assert second is None   # 已无 queued


@pytest.mark.infra
def test_recording_type_persisted():
    jid = jobstore.create_job("audio/p.m4a", "zh", "phonecall", "a@b.com")
    assert jobstore.get_job(jid).recording_type == "phonecall"
    claimed = jobstore.claim_next_queued()
    assert claimed.recording_type == "phonecall"   # claim 也带回该字段


@pytest.mark.infra
def test_metrics_persisted():
    jid = jobstore.create_job("audio/m.m4a", "zh", "meeting", "a@b.com")
    jobstore.update_progress(jid, "P3", 80, {"parts": 2, "chars": 6, "speakers": 2})
    assert jobstore.get_job(jid).metrics == {"parts": 2, "chars": 6, "speakers": 2}


def _age(jid, minutes):
    """把作业 updated_at 拨到过去，模拟卡死（仅测试用）。"""
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET updated_at = now() - %s * interval '1 minute' WHERE id=%s",
            (minutes, jid),
        )


@pytest.mark.infra
def test_claim_increments_attempts():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    assert jobstore.get_job(jid).attempts == 0
    claimed = jobstore.claim_next_queued()
    assert claimed.attempts == 1                 # claim 自增并随 RETURNING 带回
    assert jobstore.get_job(jid).attempts == 1


@pytest.mark.infra
def test_watchdog_requeues_under_cap_then_fails_at_cap():
    # 卡死任务：未到重试上限 → 重排回 queued（不退款，还要重跑）；到上限 → 判失败
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()                 # 第 1 次领取 → attempts=1、running
    _age(jid, 99)                                # 拨老 updated_at = 模拟卡死
    n = jobstore.requeue_stale_running(minutes=30, max_attempts=2)
    assert n == 1
    assert jobstore.get_job(jid).status == "queued"   # attempts(1) < cap(2) → 重排

    jobstore.claim_next_queued()                 # 第 2 次领取 → attempts=2、running
    _age(jid, 99)
    n = jobstore.requeue_stale_running(minutes=30, max_attempts=2)
    assert n == 1
    job = jobstore.get_job(jid)
    assert job.status == "failed"                # attempts(2) >= cap(2) → 判失败
    assert "看门狗" in (job.error or "")


@pytest.mark.infra
def test_watchdog_ignores_fresh_running():
    # 活跃作业（updated_at 新）不被回收——避免把正常长任务误判卡死
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()                 # running，updated_at=now
    n = jobstore.requeue_stale_running(minutes=30, max_attempts=3)
    assert n == 0
    assert jobstore.get_job(jid).status == "running"


@pytest.mark.infra
def test_watchdog_fail_branch_leaves_job_unrefunded_for_sweep():
    # 看门狗硬判失败的任务：本函数只落终态，不动钱——refunded_cents 保持 NULL，
    # 由调用方（worker）随后跑 accounts.sweep_unrefunded_failures 统一补返
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com", reserved_cents=450)
    jobstore.claim_next_queued()
    _age(jid, 99)
    n = jobstore.requeue_stale_running(minutes=30, max_attempts=1)
    assert n == 1
    assert jobstore.get_job(jid).status == "failed"
    with db.connect() as conn:
        row = conn.execute("SELECT refunded_cents FROM jobs WHERE id=%s", (jid,)).fetchone()
    assert row[0] is None   # 待清扫状态



@pytest.mark.infra
def test_set_failed_persists_public_message_separately():
    # error 存内部 traceback（admin 用）；error_public 存用户话术，二者独立、不互相污染
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()   # set_failed 的终态持有守卫要求先处于 running
    jobstore.set_failed(jid, "Traceback (most recent call last):\n  File \"/app/worker.py\"",
                         public="转录失败，请重试")
    job = jobstore.get_job(jid)
    assert job.status == "failed"
    assert "Traceback" in job.error
    assert job.error_public == "转录失败，请重试"
    assert "Traceback" not in job.error_public


@pytest.mark.infra
def test_set_failed_without_public_uses_default():
    # 旧调用点/未传 public 时也要有兜底话术，不能是 None（前端会显示空白）
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()   # set_failed 的终态持有守卫要求先处于 running
    jobstore.set_failed(jid, "boom")
    assert jobstore.get_job(jid).error_public


@pytest.mark.infra
def test_watchdog_fail_branch_sets_public_message():
    # 看门狗判失败分支（requeue_stale_running 的到上限分支）也要写 error_public，不只写内部 error
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()
    _age(jid, 99)
    n = jobstore.requeue_stale_running(minutes=30, max_attempts=1)
    assert n == 1
    job = jobstore.get_job(jid)
    assert job.status == "failed"
    assert job.error_public


@pytest.mark.infra
def test_admin_overview_shape():
    # 总览：排队进 jobs + 失败进 failures + 完成进 recent（带 metrics 供下钻）+ 汇总计数
    jobstore.create_job("audio/x.m4a", "zh", "meeting", "u@x.com", file_name="x.m4a")
    jid2 = jobstore.create_job("audio/y.m4a", "zh", "meeting", "u@x.com", file_name="y.m4a")
    jobstore.claim_specific(jid2)   # set_failed 的终态持有守卫要求先处于 running
    jobstore.set_failed(jid2, "boom")
    jid3 = jobstore.create_job("audio/z.m4a", "zh", "meeting", "u@x.com", file_name="z.m4a")
    jobstore.claim_specific(jid3)   # set_done 同上
    jobstore.set_done(jid3, "result/z.json", {"g25f": {"onePass": 5, "total": 5}})
    ov = jobstore.admin_overview()
    assert ov["summary"]["queued"] >= 1
    assert any(j["fileName"] == "x.m4a" for j in ov["jobs"])
    assert any(f["fileName"] == "y.m4a" for f in ov["failures"])
    z = next(r for r in ov["recent"] if r["fileName"] == "z.m4a")
    assert z["metrics"]["g25f"]["onePass"] == 5 and z["recordingType"] == "meeting" and z["createdAt"]


@pytest.mark.infra
def test_claim_specific_then_not_again():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    job = jobstore.claim_specific(jid)
    assert job is not None and job.status == "running" and job.id == jid
    assert jobstore.claim_specific(jid) is None   # 已 running，再领同一个 → None


@pytest.mark.infra
def test_claim_specific_nonexistent_returns_none():
    import uuid
    assert jobstore.claim_specific(str(uuid.uuid4())) is None


@pytest.mark.infra
def test_list_queued_ids_only_queued():
    j1 = jobstore.create_job("audio/1.m4a", "zh", "meeting", "a@b.com")
    j2 = jobstore.create_job("audio/2.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_specific(j2)                    # j2 → running，应被排除
    assert jobstore.list_queued_ids(10) == [j1]    # 仅剩 queued 的 j1


@pytest.mark.infra
def test_reserved_cents_persisted_and_read_back():
    # A-wire-1 遗留缺口：create_job 存的 reserved_cents，get_job 之前读不回；补上后应同值
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com", reserved_cents=153)
    assert jobstore.get_job(jid).reserved_cents == 153
    claimed = jobstore.claim_next_queued()
    assert claimed.reserved_cents == 153   # claim 也带回该字段


@pytest.mark.infra
def test_reserved_cents_defaults_to_none_when_unset():
    # 老任务未传 reserved_cents（缺省 0，见 create_job 签名）——验证列本身可空读回
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    assert jobstore.get_job(jid).reserved_cents == 0


@pytest.mark.infra
def test_set_done_no_op_on_non_running_job():
    # 终态持有守卫：非 running（仍 queued）的作业调 set_done → no-op，不改状态，防误判双跑互相覆盖
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    assert jobstore.set_done(jid, "result/x.json") is False   # 返回值门控调用方副作用
    job = jobstore.get_job(jid)
    assert job.status == "queued" and job.result_key is None


@pytest.mark.infra
def test_set_failed_no_op_on_non_running_job():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    assert jobstore.set_failed(jid, "boom") is False
    job = jobstore.get_job(jid)
    assert job.status == "queued" and job.error is None


@pytest.mark.infra
def test_set_done_no_op_on_already_done_job():
    # 已终态（done）再收到一次 set_done（如双跑另一份）→ 不覆盖
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()
    assert jobstore.set_done(jid, "result/first.json") is True     # 首次真转移 → True
    assert jobstore.set_done(jid, "result/second.json") is False   # 已是 done，非 running → no-op
    assert jobstore.get_job(jid).result_key == "result/first.json"


@pytest.mark.infra
def test_set_failed_true_on_real_transition_false_on_second():
    # 双跑场景之一：两次 set_failed 打到同一 job——第一次真转移返 True，第二次 no-op 返 False
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()
    assert jobstore.set_failed(jid, "boom1") is True
    assert jobstore.set_failed(jid, "boom2") is False
    assert jobstore.get_job(jid).error == "boom1"   # 第二次没能覆盖


@pytest.mark.infra
def test_list_queued_ids_limit_and_zero():
    for i in range(3):
        jobstore.create_job(f"audio/{i}.m4a", "zh", "meeting", "a@b.com")
    assert len(jobstore.list_queued_ids(2)) == 2   # 受 limit 约束
    assert jobstore.list_queued_ids(0) == []        # limit<=0 → 空，不查库



@pytest.mark.infra
def test_fail_stale_queued_ignores_fresh_queue():
    # 正常排队（并发满等一会）不许误杀
    jid = jobstore.create_job("audio/qf.m4a", "zh", "meeting", "a@b.com")
    assert jobstore.fail_stale_queued(hours=24) == 0
    assert jobstore.get_job(jid).status == "queued"


@pytest.mark.infra
def test_recent_carries_attempts_so_requeues_stay_visible():
    """成功了但重跑过 —— 这是看门狗回收在库里留下的唯一痕迹。

    不带 attempts 的话，这种单在「已完成」列表上跟一次过的单长得一模一样，
    而「谁在悄悄地要跑两遍」是先于失败出现的质量信号（此前只有 24h 汇总数，
    看得见「近一天回收了 3 次」，却永远查不到是哪三单）。"""
    jid = jobstore.create_job("audio/retry.m4a", "zh", "meeting", "u@x.com", file_name="retry.m4a")
    jobstore.claim_next_queued()                                  # attempts=1
    assert jobstore.requeue_stale_running(minutes=0, max_attempts=3) >= 1
    jobstore.claim_specific(jid)                                  # attempts=2，第二次才成
    jobstore.set_done(jid, "result/retry.json", {})
    r = next(x for x in jobstore.admin_overview()["recent"] if x["fileName"] == "retry.m4a")
    assert r["attempts"] == 2


@pytest.mark.infra
def test_requeue_delayed_caps_attempts():
    """重排必须封顶：本函数刷新 updated_at，而「排队 24 小时判失败」那道兜底正是按
    updated_at 算的——不封顶就是每 JOB_RETRY_DELAY_MIN 分钟起一台机器、永远不结束。

    ⚠️ **口径：`WATCHDOG_MAX_ATTEMPTS` 是「这一单总共被认领几次」，不是「能重排几次」**
    （Duner 2026-08-31 定）。认领本身就 `attempts += 1`，而 `requeue_delayed` 要求
    `attempts < 上限`，所以上限为 N 时是「认领 N 次、其中前 N−1 次可以退回队列」，
    第 N 次认领后只能判失败。
    在此之前本测试按「能重排 N 次」写，与实现差一次、长红——两种理解都自洽，
    是当初没写清楚，不是谁写错了。**别把断言改回去凑实现，也别为了让它绿而放宽实现。**"""
    from app import config
    n = config.WATCHDOG_MAX_ATTEMPTS
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    for i in range(n - 1):
        assert jobstore.claim_specific(jid) is not None          # attempts += 1
        assert jobstore.requeue_delayed(jid, delay_min=0) is True, f"第 {i+1} 次重排该成功"
    # 第 n 次认领后 attempts 已达上限 → 不许再退回队列，交由调用方判失败
    assert jobstore.claim_specific(jid) is not None
    assert jobstore.requeue_delayed(jid, delay_min=0) is False
    # 反向：上限确实是「认领次数」——此刻 attempts 正好等于上限，不多不少。
    # 只断言「第 n 次被拒」的话，实现若把闸改成 attempts <= 上限（多给一次）照样绿。
    with db.connect() as conn:
        got = conn.execute("SELECT attempts FROM jobs WHERE id = %s", (jid,)).fetchone()[0]
    assert got == n, f"认领了 {got} 次，口径要求正好 {n} 次"


@pytest.mark.infra
def test_not_before_delays_dispatch_and_claim():
    """延后单在到点前不许被派、也不许被本地 worker 池领走——不然会立刻重派、立刻又排不到。"""
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    assert jobstore.claim_specific(jid) is not None
    assert jobstore.requeue_delayed(jid, delay_min=60) is True
    assert jid not in jobstore.list_queued_ids(10)
    assert jobstore.claim_next_queued() is None
    # 到点后恢复可派
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET not_before = now() - interval '1 minute' WHERE id=%s", (jid,))
    assert jid in jobstore.list_queued_ids(10)

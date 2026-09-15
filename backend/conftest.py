"""全局 pytest 夹具（本机版）。

与线上不同的地方：
① 线上要防「测试连到生产 Supabase / R2」，本机没有远程可连 —— 改为**每个测试各给一个临时的
   数据库文件和存储文件夹**。连库测试（infra）因此不再需要 docker，也不会互相污染、不碰用户数据。
   非 infra 测试拿到的是一个没建表的空库：线上那些「读不到配置就回落默认值」的路径照样走默认分支。
② 线上只拦单元测试调大模型、放行 infra 测试。本机**一律拦**：本地开发不跑任何付费接口的测试
   （2026-09-14 定）。真要打真实接口，显式设 TRANSCRIBE_ALLOW_LLM=1。
③ 线上搬来的测试里，本机根本没有那个功能的，登记在下面的 NOT_APPLICABLE，跑的时候跳过并写明理由。
"""
import os
from pathlib import Path

import pytest

from app import config


@pytest.fixture(autouse=True)
def _isolated_storage(request, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", str(tmp_path / "transcribe.db"))
    monkeypatch.setattr(config, "BLOB_DIR", str(tmp_path / "blobs"))
    # 线上的连库测试默认库已由 docker 建好表；本机每个测试是全新的空库，替它们先建表
    if request.node.get_closest_marker("infra") is not None:
        from app import db
        db.init_schema()


@pytest.fixture(autouse=True)
def _testclient_counts_as_local(monkeypatch):
    """线上的接口测试用 TestClient 默认地址（Host: testserver）。本机访问检查只认本机地址，
    测试里把这个名字当本机 —— 只放宽测试，不放宽产品代码。「外来 Host 被拒」由 test_local_api 用别的域名守着。"""
    from app import api
    monkeypatch.setattr(api, "_LOCAL_HOSTNAMES", api._LOCAL_HOSTNAMES | {"testserver"})


@pytest.fixture(autouse=True)
def _no_llm_calls(monkeypatch):
    """测试一律不许出网调大模型。要测降级路径请显式 monkeypatch 被测的调用点。"""
    if os.environ.get("TRANSCRIBE_ALLOW_LLM") == "1":
        return
    from pipeline import pp_deepseek

    def _blocked(*_a, **_kw):
        raise AssertionError(
            "测试试图真的调用 DeepSeek API。要测降级路径请显式 monkeypatch "
            "pp_deepseek.narrate（被测的是 pp_runner 怎么用它，不是 DeepSeek 本身）。"
        )

    monkeypatch.setattr(pp_deepseek, "call", _blocked)

    # 本地识别层的定字：同样一律拦（导入 local_orchestrator 顺带把 src/ 加进 sys.path）
    from pipeline import local_orchestrator  # noqa: F401
    from transcribe_local import fuse

    def _blocked_fuse(*_a, **_kw):
        raise AssertionError("测试试图真的调用定字接口（DeepSeek / 博查）。请 monkeypatch fuse._call 回放编好的产出。")

    monkeypatch.setattr(fuse, "_call", _blocked_fuse)
    monkeypatch.setattr(fuse, "_bocha", _blocked_fuse)


@pytest.fixture(autouse=True)
def _jobs_in_process(monkeypatch):
    """本机后台默认每单起一个子进程（点取消时好结束它）。测试里的打桩传不进子进程，
    所以测试默认在本进程里跑单；要测子进程的（test_local_cancel）自己打开。"""
    from app import worker
    monkeypatch.setattr(worker, "ISOLATE", False)


# ───────────── 本机不适用的线上测试 ─────────────
# 规则（Duner 2026-09-15 确认）：线上搬来的测试，本地用得上的就改到通过；本地根本没有那个功能，才登记在这里。
# **只看本地有没有这个功能，不看测试过不过、好不好改。**
#
# 键：「文件」= 整份不适用，不收集（这类文件多半一导入就要找本地没搬的模块）；
#     「文件::用例名」= 只跳过这一条（参数化用例按名字整组跳过）。
# 登记了却对不上（线上把用例改名或删了）会让这次测试失败，免得登记表悄悄过期。
NOT_APPLICABLE = {
    # 整份
    "tests/test_config.py": "测云存储与远程数据库的配置项，本机两样都没有",
    "tests/test_db_guard.py": "防止整表删改打到远程数据库，本机只有本地文件库",
    "tests/test_db_integration.py": "Postgres 建表检查与付款回调去重；本机建表由 test_local_sqlite 守，没有付款",
    "tests/test_schema_covers_code.py": "查「注销账户」要删的表都建得出来；本机没有注销账户，只有删除转录",
    "tests/test_rate_limit_retry.py": "云端识别引擎撞限流后的重试，本机识别不走云端",
    "tests/integration/test_e2e.py": "真密钥跑云端四路识别的全流程，本机识别是本地四路",
    # 单条
    "tests/test_balances.py::test_seed_classifies_vendors": "预置 Gemini、FunASR、讯飞、豆包的余额行，本机只看 DeepSeek 和博查",
    "tests/test_balances.py::test_refresh_all_skips_missing_and_swallows_errors":
        "拉火山、阿里云余额，本机只拉 DeepSeek 和博查（本机版由 test_local_balances 守）",
    "tests/test_blobstore_integration.py::test_取件客户端进程内共用一份": "云存储客户端复用省握手，本机存储是文件夹，没有客户端",
    "tests/test_ds_pricing.py::test_价目表与_vendor_脚本逐项一致": "对齐云端定字脚本里的第二份价目表，那个脚本本机不搬",
    "tests/test_ds_pricing.py::test_汇率与生效日与_vendor_一致": "同上",
    "tests/test_merge.py::test_分批版产物名能被生产的查找契约命中": "读云端定字脚本源码，本机定字走 src/transcribe_local/fuse.py",
    "tests/test_merge.py::test_分批版会读用户术语库": "同上",
    "tests/test_merge.py::test_分批版会吐成本标记": "同上",
    "tests/test_merge.py::test_计价按模型分档": "同上",
    "tests/test_merge.py::test_分批版的存疑围栏是必答项": "同上",
    "tests/test_merge.py::test_两个版本都吐出_hit_miss_明细": "同上",
    "tests/test_merge.py::test_orchestrator_在调_p3_之前清零": "读云端编排 orchestrator.py，本机转录入口是 local_orchestrator",
    "tests/test_p3_lang.py::test_vendor脚本真的会把那个环境变量拼进system": "读云端定字脚本源码，本机不搬",
    "tests/test_skill_merge.py::test_both_p3_paths_state_the_timestamp_pair_rule": "读 claude -p 定字用的 SKILL 与云端脚本，本机定字不走这条路",
    "tests/test_skill_merge.py::test_skill_states_the_two_2026_09_04_rules": "同上",
    # 后台处理任务（test_worker / test_worker_postprocess）
    "tests/test_worker.py::test_startup_recover_sweeps_unrefunded_failures": "启动时清扫漏掉的预扣款返还，本机不收费",
    "tests/test_worker.py::test_startup_recover_survives_sweep_failure": "同上",
    "tests/test_worker.py::test_中间产物留档到review前缀下": "把云端识别各阶段的中间稿传上去留档；本机识别层不产出这批文件",
    "tests/test_worker.py::test_失败的单也要留档": "同上",
    "tests/test_worker.py::test_留档挂了不许改变这一单的成败": "同上",
    "tests/test_worker.py::test_开关关掉就不留档": "同上",
    "tests/test_worker.py::test_process_one_no_refund_when_set_failed_loses_race": "失败时返还预扣款，本机不收费",
    "tests/test_worker.py::test_process_one_settles_user_on_done": "转完按时长结算扣费，本机不收费",
    "tests/test_worker.py::test_process_one_settles_with_zero_reserved_when_none": "同上",
    "tests/test_worker.py::test_process_one_no_duration_falls_back_to_job_duration": "同上",
    "tests/test_worker.py::test_process_one_no_duration_anywhere_refunds_reservation": "同上",
    "tests/test_worker.py::test_process_one_consumes_xunfei_hours_on_meeting": "扣讯飞的用量，本机识别不用讯飞",
    "tests/test_worker.py::test_process_one_no_xunfei_consume_without_xf": "同上",
    "tests/test_worker.py::test_run_one_job_injects_gate_claims_and_processes": "云机器一机一单的入口，本机在接口进程里跑后台",
    "tests/test_worker.py::test_run_one_job_skips_when_not_queued": "同上",
    "tests/test_worker.py::test_main_one_job_runs_job_from_env": "同上",
    "tests/test_worker.py::test_main_one_job_missing_env_returns_2": "同上",
    "tests/test_worker.py::test_main_one_job_not_claimed_returns_1": "同上",
    "tests/test_worker.py::test_start_in_thread_dispatch_mode_only_watchdog": "派单到云机器，本机不派单",
    "tests/test_worker.py::test_watchdog_dispatches_when_fly_enabled": "同上",
    "tests/test_worker.py::test_settles_at_the_rate_snapshotted_on_the_job_not_the_current_global_price": "按费率快照与免费额度结算，本机不收费",
    "tests/test_worker.py::test_settles_a_pricier_language_higher_for_the_same_duration": "同上",
    "tests/test_worker.py::test_legacy_job_without_a_snapshot_settles_at_the_old_uniform_price": "同上",
    "tests/test_worker.py::test_settles_only_the_paid_seconds_after_free_quota": "同上",
    "tests/test_worker.py::test_fully_free_job_settles_at_zero": "同上",
    "tests/test_worker.py::test_stale_backup_alerts_as_act": "云端数据库备份停摆的检查与自动补触发，本机没有云端备份",
    "tests/test_worker.py::test_fresh_backup_is_quiet": "同上",
    "tests/test_worker.py::test_unknown_age_does_not_alert": "同上",
    "tests/test_worker.py::test_blobstore_failure_never_breaks_watchdog": "同上",
    "tests/test_worker.py::test_漏跑先补触发_还没到告警阈值就不惊动人": "同上",
    "tests/test_worker.py::test_真出事时补触发与告警都要有": "同上",
    "tests/test_worker.py::test_没配令牌时自愈整个关掉_但告警照发": "同上",
    "tests/test_worker.py::test_补触发有节流_令牌失效时不会每轮都打GitHub": "同上",
    "tests/test_worker.py::test_GitHub_报错不许连累看门狗": "同上",
    "tests/test_worker.py::test_告警正文把最可能的成因排在第一条": "同上",
    "tests/test_worker.py::test_主轨排队超时_默认退回队列不判失败不返还": "云端主轨排不到供应商并发名额时退回队列，本机识别不排供应商的队",
    "tests/test_worker.py::test_主轨排队超时_重排到顶必须判失败并返还": "同上",
    "tests/test_worker_postprocess.py::test_main_one_job_routes_postprocess_by_env": "云机器一机一单的入口，本机在接口进程里跑后台",
    "tests/test_worker_postprocess.py::test_main_one_job_defaults_to_transcription": "同上",
    "tests/test_worker_postprocess.py::test_main_one_job_pp_not_claimed_returns_1": "同上",
    "tests/test_worker_postprocess.py::test_run_one_pp_job_claims_and_runs": "同上",
    "tests/test_worker_postprocess.py::test_run_one_pp_job_skips_when_not_queued": "同上",
    # 后处理执行链（test_pp_runner）
    "tests/test_pp_runner.py::test_run_free_promo_price_zero_no_ledger": "价格为 0 时不走账本，本机不收费",
    "tests/test_pp_runner.py::test_run_capped_requeues_without_charge_or_fail":
        "Claude 订阅那条路（claude -p）的撞顶、并发闸、重试与超时；本机后处理直接走「模型后端」设置那条路",
    "tests/test_pp_runner.py::test_run_capped_warning_with_output_counts_as_success": "同上",
    "tests/test_pp_runner.py::test_gate_full_without_degrade_path_requeues": "同上",
    "tests/test_pp_runner.py::test_run_hard_error_retries_once_then_fails_unpaid": "同上",
    "tests/test_pp_runner.py::test_run_hard_error_then_success_on_retry": "同上",
    "tests/test_pp_runner.py::test_run_timeout_fails_without_retry": "同上",
    "tests/test_pp_runner.py::test_gate_full_also_degrades": "同上",
    # 接口（test_api / test_postprocess_api / test_postprocess / test_create_job_integration）
    "tests/test_api.py::test_auth_flow_request_verify_me": "本机不登录（单用户，打开就用），没有验证码、会话与未登录拦截",
    "tests/test_api.py::test_auth_request_code_passes_client_ip": "同上",
    "tests/test_api.py::test_auth_request_code_prefers_vercel_forwarded_over_cf": "同上",
    "tests/test_api.py::test_auth_request_code_rejects_non_ip_forwarded_value": "同上",
    "tests/test_api.py::test_auth_request_code_falls_back_to_cf_when_no_public_forwarded": "同上",
    "tests/test_api.py::test_auth_request_code_ip_rate_limited_maps_to_429": "同上",
    "tests/test_api.py::test_auth_request_code_send_failure_502_hides_internal_error": "同上",
    "tests/test_api.py::test_gone_tombstone_returns_410": "同上",
    "tests/test_api.py::test_me_backfills_cookie_for_bearer_only_session": "同上",
    "tests/test_api.py::test_business_endpoints_require_auth": "同上",
    "tests/test_api.py::test_glossaries_require_auth": "同上",
    "tests/test_api.py::test_read_endpoints_require_auth": "同上",
    "tests/test_api.py::test_cookie_session_allows_audio_and_export": "同上",
    "tests/test_api.py::test_is_admin_flag_in_me_and_verify": "同上",
    "tests/test_api.py::test_admin_overview_hidden_from_non_admin": "对非管理员隐藏运行面板；本机用户就是管理员",
    "tests/test_api.py::test_admin_balances_hidden_from_non_admin": "同上",
    "tests/test_api.py::test_admin_set_balance_anonymous_illegal_body_hidden": "同上",
    "tests/test_api.py::test_admin_set_balance_anonymous_non_dict_body_hidden": "同上",
    "tests/test_api.py::test_admin_refresh_balances_hidden_from_non_admin": "同上",
    "tests/test_api.py::test_p3_health_hidden_from_non_admin": "同上",
    "tests/test_api.py::test_glossaries_scoped_to_user": "多用户之间的数据隔离；本机只有一个用户",
    "tests/test_api.py::test_topup_dev_mode_credits_and_ledger": "充值、余额、退款，本机不收费",
    "tests/test_api.py::test_topup_failclosed_in_production_without_stripe": "同上",
    "tests/test_api.py::test_pending_topups_endpoint": "同上",
    "tests/test_api.py::test_create_job_insufficient_balance_is_402_no_job_no_upload": "同上",
    "tests/test_api.py::test_create_job_reserved_amount_matches_probed_duration": "同上",
    "tests/test_api.py::test_admin_topups_lists_refundable_per_row": "同上",
    "tests/test_api.py::test_admin_refund_rejects_over_refundable": "同上",
    "tests/test_api.py::test_admin_refund_within_quota_reaches_provider_and_never_touches_balance": "同上",
    "tests/test_api.py::test_admin_refund_releases_hold_when_provider_explicitly_rejects": "同上",
    "tests/test_api.py::test_admin_refund_keeps_hold_when_provider_result_is_unknown": "同上",
    "tests/test_api.py::test_admin_refund_success_path_records_provider_id": "同上",
    "tests/test_api.py::test_admin_refund_rejects_bad_body": "同上",
    "tests/test_api.py::test_create_job_dispatches_to_fly_when_enabled": "上传后派单到云机器，本机不派单",
    "tests/test_api.py::test_create_job_no_dispatch_when_disabled": "同上",
    "tests/test_api.py::test_create_job_dispatch_failure_does_not_block": "同上",
    "tests/test_api.py::test_p3_probe_requires_dispatch_mode": "在云机器上起一台做订阅探活（派单态、机器池）；本机探活在本机后台线程里跑",
    "tests/test_api.py::test_p3_probe_yields_to_transcription_when_pool_full": "同上",
    "tests/test_api.py::test_p3_probe_starts_machine_with_probe_kind": "同上",
    "tests/test_postprocess_api.py::test_pp_config_requires_auth": "本机不登录（单用户，打开就用），没有验证码、会话与未登录拦截",
    "tests/test_postprocess_api.py::test_start_requires_auth": "同上",
    "tests/test_postprocess_api.py::test_product_requires_auth_and_ownership": "同上（归属那半由 test_get_postprocess_404_other_user 守着）",
    "tests/test_postprocess_api.py::test_pp_config_scoped_to_user": "多用户之间的数据隔离；本机只有一个用户",
    "tests/test_postprocess_api.py::test_start_snapshots_price_and_checks_balance": "后处理按分钟计价、查余额与免费额度，本机不收费",
    "tests/test_postprocess_api.py::test_start_free_quota_covers_balance_gate": "同上",
    "tests/test_postprocess_api.py::test_start_dispatches_when_fly_enabled": "发起后派单到云机器，本机不派单",
    "tests/test_postprocess_api.py::test_start_dispatch_failure_does_not_block": "同上",
    "tests/test_postprocess.py::test_postprocess_rate_stacks_per_step": "后处理按步计价，本机不收费",
    "tests/test_postprocess.py::test_postprocess_price_prorates_by_second": "同上",
    "tests/test_create_job_integration.py::test_create_job_snapshots_the_rate_and_reserves_at_it": "上传时钉费率快照并预扣余额，本机不收费",
    "tests/test_create_job_integration.py::test_create_job_snapshots_the_same_rate_for_every_language": "同上",
    "tests/test_create_job_integration.py::test_create_job_insufficient_balance_402_no_job_balance_untouched": "同上",
    # 运行面板（test_health_view_infra / test_workflow_view）
    "tests/test_health_view_infra.py::test_login_sends_read_from_throttle_window": "登录验证码的发送量，本机不登录",
    "tests/test_workflow_view.py::test_prompt_scope_is_declared": "线上指纹取自派单前台、跟任务机器上那份可能不同；本机只有一份，没有这个口径",
    "tests/test_workflow_view.py::test_lang_plans_cover_every_shipped_language": "27 门语种各自的云端引擎编排，本机只有中文一套四路",
    "tests/test_workflow_view.py::test_expiries_countdown_and_warn_flag": "云端凭证（Paddle、Claude 令牌）的到期清单，本机没有",
    "tests/test_workflow_view.py::test_expiries_sorted_by_urgency": "同上",
    "tests/test_workflow_view.py::test_bad_date_does_not_hide_the_row": "同上",
}
_HERE = Path(__file__).resolve().parent
_na_hit: set[str] = set()
_na_files_run: set[str] = set()


def _na_reason(key: str) -> str:
    reason = NOT_APPLICABLE[key]
    while reason == "同上":                      # 「同上」取上一条的理由
        keys = list(NOT_APPLICABLE)
        key = keys[keys.index(key) - 1]
        reason = NOT_APPLICABLE[key]
    return reason


def pytest_ignore_collect(collection_path, config):
    try:
        rel = collection_path.resolve().relative_to(_HERE).as_posix()
    except ValueError:
        return None
    if rel in NOT_APPLICABLE:
        _na_hit.add(rel)
        return True
    return None


def pytest_collection_modifyitems(config, items):
    for item in items:
        _na_files_run.add(item.nodeid.split("::")[0])
        key = item.nodeid.split("[")[0]
        if key in NOT_APPLICABLE:
            _na_hit.add(key)
            item.add_marker(pytest.mark.skip(reason=f"本机不适用：{_na_reason(key)}"))


def _na_stale() -> list[str]:
    """登记了却对不上的：整份的文件已经不存在；单条的文件跑了、却没有这条用例。"""
    stale = []
    for key in NOT_APPLICABLE:
        if "::" not in key:
            if not (_HERE / key).exists():
                stale.append(key)
        elif key.split("::")[0] in _na_files_run and key not in _na_hit:
            stale.append(key)
    return stale


def pytest_sessionfinish(session, exitstatus):
    if _na_stale():
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    files = sum(1 for k in _na_hit if "::" not in k)
    cases = sum(1 for k in _na_hit if "::" in k)
    if files or cases:
        terminalreporter.write_line(f"本机不适用（理由见 backend/conftest.py 的 NOT_APPLICABLE）：整份 {files} 个文件、单条 {cases} 组用例")
    for key in _na_stale():
        terminalreporter.write_line(f"⚠️ 不适用登记对不上（线上改名或删了？）：{key}", red=True)

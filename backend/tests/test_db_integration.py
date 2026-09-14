import pytest

from app import accounts, db


@pytest.mark.infra
def test_init_schema_and_jobs_table_exists():
    db.init_schema()
    with db.connect() as conn:
        row = conn.execute("SELECT to_regclass('public.jobs')").fetchone()
    assert row[0] == "jobs"


@pytest.mark.infra
def test_claim_payment_event_dedupes_within_provider_but_not_across():
    # at-least-once 投递：同一 (provider, event_id) 第二次认领必须失败（真 ON CONFLICT 行为）；
    # 而两家 provider 的 id 字符串可能撞车，撞车时**各自都得能入账**——这正是去重键带 provider 的理由
    db.init_schema()
    event_id = "evt_integration_test_dedupe"
    with db.connect() as conn:
        conn.execute("DELETE FROM payment_events WHERE event_id = %s", (event_id,))
    assert accounts.claim_payment_event("stripe", event_id) is True
    assert accounts.claim_payment_event("stripe", event_id) is False
    assert accounts.claim_payment_event("paddle", event_id) is True    # 同名不同家，互不影响
    assert accounts.claim_payment_event("paddle", event_id) is False

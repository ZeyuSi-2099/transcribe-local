"""本机不收费：转录失败时写进库、给用户看的默认话术不许再提计费。本地独有的测试（线上没有这个文件）。

线上的话术带「本次不计费」，搬过来原样留着的话，本机用户会以为这东西要花钱。
后处理那句由 test_postprocess.py 守（改成了本机的断言）。
"""
import pytest

from app import jobstore


@pytest.mark.infra
def test_transcription_failure_default_message_does_not_mention_billing():
    jid = jobstore.create_job("audio/x.m4a", "zh", "meeting", "a@b.com")
    jobstore.claim_next_queued()
    jobstore.set_failed(jid, "Traceback ...")          # 不传 public：用默认话术
    public = jobstore.get_job(jid).error_public
    assert public == "转录失败，请重试"
    assert "计费" not in public

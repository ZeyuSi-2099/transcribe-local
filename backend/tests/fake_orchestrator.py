"""取消测试用的假转录入口（不是测试文件）：报一次进度，然后一直「算」下去，等着被取消。

worker 通过环境变量 TRANSCRIBE_ORCHESTRATOR=fake_orchestrator 在子进程里导入它。
"""
import time


def transcribe(audio_path, on_phase=None, **_kw):
    if on_phase:
        on_phase("P1", 10, {})
    time.sleep(600)
    raise AssertionError("不该跑到这里：应当在取消时被结束")

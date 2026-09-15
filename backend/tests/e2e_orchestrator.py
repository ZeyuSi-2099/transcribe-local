"""界面走查用的假转录入口（不是测试文件）：不跑识别、不调模型，一秒后给出一份固定的稿子。

走查脚本（frontend/src/test/e2e/）起本机服务时设 TRANSCRIBE_ORCHESTRATOR=e2e_orchestrator，
只把识别换成它；上传、量时长、排队、后台、存结果、取稿都照真的走。
录音长于 FAIL_OVER_SEC 秒时故意失败，用来走「转录失败」那条路。
"""
import time
import wave

from pipeline.transcript import Segment

FAIL_OVER_SEC = 3


def transcribe(audio_path, on_phase=None, **_kw):
    with wave.open(audio_path) as w:
        seconds = w.getnframes() / w.getframerate()
    if on_phase:
        on_phase("P1", 40, {})
    time.sleep(1)
    if seconds > FAIL_OVER_SEC:
        raise RuntimeError("走查：录音长于 3 秒，故意失败")
    return [Segment("00:00:00", "欢迎来到本机转录走查。", "主持人"),
            Segment("00:00:01", "这一句来自假的识别入口。", "嘉宾")], []

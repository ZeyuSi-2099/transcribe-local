"""本机版转录入口：与线上 orchestrator.transcribe 同签名、同返回，里面跑本地这套识别。

    线上：P0 转码 → P1 云端各家引擎 → P2 模糊对齐 → P3 定字 → P4 收尾
    本地：P0 转码 · 声纹 · 切块 → P1 四路本地识别 → P2 按换人拆块 + 分歧册 → P3 定字 → P4 收尾

阶段名与进度百分比照线上（P0 5 · P1 6→50 · P2 50 · P3 61 · P4 99 · done 100），界面不用改。

识别层在 src/transcribe_local（命令行和旧界面也用它），这里只做两件事：
  ① 把它的进度事件翻成线上的 on_phase(phase, progress, metrics)；
  ② 把终稿 + 定字报告交给线上原样的 transcript.py / review.py，出逐句稿与复核卡。
收尾那几步（先记 [❓] 位置再剥、说话人存疑、丢行对账）照抄线上 orchestrator，顺序也照抄。

ui_lang 本机用不上：定字报告只有中文一份。参数留着，是为了和线上同签名。
"""
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from transcribe_local import config as tl_config  # noqa: E402
from transcribe_local import engines as tl_engines  # noqa: E402
from transcribe_local import fuse as tl_fuse  # noqa: E402
from transcribe_local import pipeline as tl_pipeline  # noqa: E402

from .review import _speaker_items, marked_spots, parse_review  # noqa: E402
from .transcript import (count_unparsed_rows, marked_speaker_rows, parse_transcript_md,  # noqa: E402
                         strip_marker, to_json)

LANGS = {"zh"}          # 本地现在只有中文这一套引擎
HEARTBEAT_SEC = 5       # 同线上 P1_POLL_SEC：定时重发当前进度，刷新 updated_at，免得长任务被看门狗判卡死

# 本地融合给「四路都没有内容」的块写的是 `[起点 - ?] X: [❓]`。线上解析器的终点只认数字，
# 不改的话这一行解析不出 → 那张 [❓] 卡跟着没了。终点从来没被读过，去掉问号不改任何别的行为。
_OPEN_END = re.compile(r"^(\s*\[\s*[\d:.]+)\s*-\s*\?\s*\]", re.M)


def config_path() -> Path:
    """用户自己的配置（模型后端、术语库等）。没有这个文件就全用默认值。"""
    if os.environ.get("TRANSCRIBE_CONFIG"):
        return Path(os.environ["TRANSCRIBE_CONFIG"])
    return Path(os.environ.get("TRANSCRIBE_DATA") or Path.home() / ".transcribe-local") / "config.yaml"


def transcribe(audio_path: str, on_phase=None, on_flac=None, glossary_text: str = "", lang: str = "zh",
               on_report=None, ui_lang: str | None = None):
    if lang not in LANGS:
        raise ValueError(f"本地版暂不支持语言 '{lang}'")
    cfg = tl_config.load(config_path())
    work = Path(tempfile.mkdtemp(prefix="local_run_", dir=Path(audio_path).parent))
    if glossary_text and glossary_text.strip():
        gp = work / "术语库.md"
        gp.write_text(glossary_text, encoding="utf-8")
        cfg["terms"]["files"] = [str(gp)]

    metrics: dict = {}
    state = {"phase": "P0", "progress": 5}
    lock = threading.Lock()

    def emit(phase: str, progress: int) -> None:
        with lock:
            state["phase"], state["progress"] = phase, progress
            snap = dict(metrics)
        if on_phase:
            on_phase(phase, progress, snap)

    tags = [tl_engines.TAG.get(e, e) for e in cfg["engines"]["enabled"]]
    engines: dict[str, dict] = {}
    p1_start = [0.0]

    def emit_p1() -> None:
        snap = {k: dict(v) for k, v in engines.items()}
        metrics["engines"] = snap
        settled = sum(1 for v in snap.values() if v["status"] in ("done", "failed"))
        emit("P1", 6 + round(44 * settled / len(snap)) if snap else 6)

    def on_event(kind: str, **kw) -> None:
        if kind == "audio":
            metrics["parts"] = 1
            metrics["durationSec"] = round(kw["minutes"] * 60)
            if on_flac:        # 回放音频：线上交的是 FLAC，本地是 16k 单声道 wav，worker 照样转 m4a
                on_flac(str(work / "run" / f"{Path(audio_path).stem}.16k.wav"))
        elif kind == "chop":
            p1_start[0] = time.time()
            engines.update({t: {"status": "running", "sec": None} for t in tags})
            emit_p1()
        elif kind == "engine_done":
            engines[kw["engine"]] = {"status": "done", "sec": round(time.time() - p1_start[0], 1)}
            if kw["engine"] == tags[0]:
                metrics["chars"] = kw["chars"]
            emit_p1()
        elif kind == "p1qc":           # 四路都跑完了，进分歧册
            emit("P2", 50)
        elif kind == "divergence":
            metrics["divergences"] = kw["substantive"]
        elif kind == "stage" and kw.get("name") == "P3":
            emit("P3", 61)

    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(HEARTBEAT_SEC):
            try:
                emit(state["phase"], state["progress"])
            except Exception:  # noqa: BLE001  心跳失败不该拖垮转录
                pass

    emit("P0", 5)
    beat = threading.Thread(target=_beat, daemon=True, name="local-heartbeat")
    beat.start()
    try:
        r = tl_pipeline.run(Path(audio_path), work / "run", cfg, emit=on_event)
    finally:
        stop.set()
        beat.join(timeout=2)
    metrics["alignedSegs"] = r.blocks

    emit("P4", 99)
    final_md = _OPEN_END.sub(r"\1]", r.merged)
    final_segs = parse_transcript_md(final_md)
    if not final_segs:
        raise RuntimeError("终稿解析为空（定字产物不符合逐行格式）")
    dropped = count_unparsed_rows(final_md)
    if dropped:
        metrics["droppedRows"] = dropped
        print(f"[P4] ⚠️ 终稿有 {dropped} 行像转录行却解析不出（内容已丢，见定字产物）", flush=True)
    # ⚠️ 先记位置、再剥标记，顺序不能反（线上 orchestrator 同处注释）
    marked = marked_spots(to_json(final_segs))
    speaker_marks = marked_speaker_rows(final_md)
    if speaker_marks:
        metrics["speakerDoubts"] = len(speaker_marks)
    for s in final_segs:
        s.s = strip_marker(s.s)
    metrics["speakers"] = len({s.sp for s in final_segs if s.sp})
    metrics["finalSegs"] = len(final_segs)
    try:
        review = parse_review(r.report, to_json(final_segs), bool(glossary_text and glossary_text.strip()),
                              marked, speaker_marks)
    except Exception:  # noqa: BLE001  报告解析炸了，说话人卡不能跟着没（同线上 _parse_review_for）
        review = _speaker_items(speaker_marks)

    if on_report and r.report:
        try:
            on_report(r.report)
        except Exception as e:  # noqa: BLE001  留档是旁路
            print(f"定字报告留档失败（不影响本单）: {e}", flush=True)

    # 本机识别不花钱；定字走 API 时按用量估一个数（同线上：USD×7 折人民币，只作运行面板参考）
    usage = (cfg["p3"].get("_last") or {}).get("usage") or {}
    price = tl_fuse.PRICE
    p3_cny = round((usage.get("miss", 0) * price["miss"] + usage.get("hit", 0) * price["hit"]
                    + usage.get("out", 0) * price["out"]) / 1e6 * 7, 2)
    engine = "claude" if cfg["p3"].get("backend") == "claude_p" else str(cfg["p3"].get("model") or "unknown").lower()
    metrics["cost"] = {"p1": {}, "p3": p3_cny, "p3_engine": engine, "total": p3_cny}
    emit("done", 100)
    return final_segs, review

"""P3 探活：在一台 Fly 机器上真跑一次最小的 `claude -p`，把结果写进 p3_events。

**为什么必须起机器**：派单前台（Render）上既没有 claude CLI 也没有订阅令牌 —— 令牌只发给
Fly 任务机器。所以探活复用现成的派单通道：起一台机器，env 传 JOB_KIND=probe，
机器入口路由到本模块，跑完写库自毁，运营舱轮询 p3_events 拿结果。

**为什么用生产同款 model/effort**（直接复用 skill_merge.build_claude_cmd）：要验的是
「生产这套配置现在通不通」。换个更便宜的模型去探，探到的是另一回事——opus 撞顶而
sonnet 没撞，恰恰是我们最需要知道的那种情况。

探活同样走并发闸：闸满就记一条 concurrency 直接返回，不挤占真实任务的名额
（订阅并发墙是硬的，探活插队会把正在跑的转录挤下去降级）。
"""
import os
import shutil
import subprocess
import tempfile
import uuid

from pipeline.skill_merge import build_claude_cmd, classify_claude_result

from . import claude_gate, p3_health

# 探活提示词：短到不触发任何工具，答案唯一便于核对。
PROBE_PROMPT = "Reply with exactly one word: PONG"
_EXPECT = "PONG"


def new_probe_id() -> str:
    """探活 id（同时用作并发闸的 slot_id）。带前缀便于在库里与真实 job 区分。"""
    return f"probe-{uuid.uuid4().hex[:12]}"


def _timeout_sec() -> int:
    """探活超时独立于 P3_TIMEOUT_SEC（那是给整段融合的 1 小时）——探活几秒该回，
    卡住就是不健康本身，不该让运营对着转圈等一小时。"""
    try:
        return max(30, int(os.environ.get("P3_PROBE_TIMEOUT_SEC", "180")))
    except ValueError:
        return 180


def run_probe(probe_id: str) -> bool:
    """跑一次探活并落库。返回是否真打到了 Claude（闸满/异常返回 False）。

    异常一律落成一条 error 事件再返回：探活机器跑完就自毁，不写库等于运营舱那边
    永远停在「探活中…」——查不出结果比查出坏结果更难排查。"""
    gate = claude_gate.DbClaudeGate(probe_id, source="probe")
    if not gate.acquire():
        p3_health.record("concurrency", source="probe", job_id=probe_id,
                         note="并发闸满，未打到 Claude（生产正忙）")
        return False
    workdir = tempfile.mkdtemp(prefix="p3probe_")
    try:
        cmd = build_claude_cmd(PROBE_PROMPT)
        try:
            proc = subprocess.run(cmd, cwd=workdir, env=dict(os.environ),
                                  capture_output=True, text=True, timeout=_timeout_sec())
        except subprocess.TimeoutExpired:
            p3_health.record("timeout", source="probe", job_id=probe_id,
                             note=f"探活超时（>{_timeout_sec()}s）")
            return True
        stdout = getattr(proc, "stdout", "") or ""
        cls = classify_claude_result(stdout, getattr(proc, "returncode", 1))
        state = cls["state"]
        note = cls.get("message") or ""
        if state == "ok" and _EXPECT not in stdout.upper():
            # 调用成功但答非所问：算不上健康（模型/配置层面有问题），标成 error 让人去看
            state, note = "error", "调用成功但未返回预期内容"
        p3_health.record(state, source="probe", job_id=probe_id, window=cls.get("window"),
                         resets_at=_resets_at(cls), note=note or None)
        return True
    except Exception as e:  # noqa: BLE001  任何意外都要留下痕迹，见 docstring
        p3_health.record("error", source="probe", job_id=probe_id, note=f"探活异常：{e}")
        return True
    finally:
        gate.release()
        shutil.rmtree(workdir, ignore_errors=True)


def _resets_at(cls: dict):
    for rl in cls.get("rate_limits") or []:
        if rl.get("window") == cls.get("window"):
            return rl.get("resets_at")
    return None

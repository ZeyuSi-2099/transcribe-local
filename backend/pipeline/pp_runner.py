"""后处理执行链（契约 docs/postprocess-implementation-contract.md §执行）。

取转录稿（修订版优先）→ 段落转问答体 md（`说话人：文本` 行，无时间戳）→ 逐勾选步
`claude -p "/pp-<step> ..."`（cwd=vendor 根，复用 skill_merge 的 build_claude_cmd 与
classify_claude_result）→ 产物/QC 上 R2 → 结账（price_cents>0 且赢得终态转移才走账本）。

Claude 跑不成时的处置（2026-08-13 定档）：
  - **撞顶（额度耗尽）或闸满（名额被占）→ 一律直接降级 DS-v4-flash**，有降级路的步就走。
    此前闸满是「回队列等几分钟」，撞顶才降级；改成两者都降的理由是
    **不让用户干等**——加工是用户点了按钮在等着的，等几分钟拿到 Opus 稿，
    不如立刻拿到 Flash 稿（降级路的对照物是「没有稿」，不是 Opus 稿）。
    顺带纠正一处理解：Claude 订阅**没有并发墙**，闸拦的是烧额度速率，
    所以闸满与撞顶本就是同一件事的两种程度，没有分开处置的道理（见 claude_gate 模块头）。
  - 有降级路的步：narrate → `pp_deepseek.narrate`；redact → `pp_redact_ds.redact`（＝现有全部步骤）。
  - 降级也没成功 → 退回原路延后重试：兜底挂了不该比没兜底更糟。
    ⚠️ 归类下架（2026-08-17）后，「没有降级路」这个分支不再有步骤会走到，
    但**延后重试这条路留着**——降级本身失败时它是唯一退路，删了就等于脱敏没有兜底。
  - 硬错重试 1 次仍败 → failed（不计费）
  - 超时不重试直接 failed（再试一次又是一整个超时窗，同 P3 的超时不重试理由）
每步跑前 update_step 刷新 updated_at 喂看门狗；真挂死靠看门狗回收（requeue_stale_running）。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path

from app import accounts, blobstore, claude_gate, jobstore, postprocess, speaker_labels
from app.redact_diff import load_transcript_segments as _load_transcript_segments, segments_to_qa
# ↑ 后处理输入的唯一算法；api 侧的改动对比要算出同一份稿子，两处分家就会报出假改动

from . import debug_dump, pp_deepseek, pp_lang, pp_redact_ds, skill_merge

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor"

# 每步的 QC/问题清单文件后缀（skill 按「<输出去 .md> + 后缀」写，见各 SKILL.md 输出要求）
_QC_SUFFIX = {"narrate": "_issues.md", "redact": "_QC报告.md"}
# ⚠️ 只给**日志与失败话术**用（内部/中文口径）。用户下载的 qc.md 标题走 _step_title
#    ——2026-08-30 前这张表直接进了报告，德语用户下载的 Prüfbericht 开头是「# 视角转换」。
_STEP_TITLE = {"narrate": "视角转换", "redact": "脱敏"}


def _step_title(step: str, ui_lang: str | None) -> str:
    """qc.md 里的步骤标题：跟界面语言，与卡片上产物的叫法同名（pp_lang 八门表）。
    表里查不到的步骤（历史 categorize）回落中文旧名——老单只读不重跑，走不到这里。"""
    return pp_lang.report(ui_lang).get(f"step_{step}") or _STEP_TITLE.get(step, step)


def qc_md_for(qc_parts: list[tuple[str, str]], ui_lang: str | None) -> str:
    """把各步的 QC 报告拼成用户可下载的 qc.md。抽出来是为了可测——拼装逻辑埋在
    worker 主循环里的话，「标题是不是用户的语言」只有真跑一单才看得见。"""
    return "\n\n".join(f"# {_step_title(s, ui_lang)}\n\n{txt}" for s, txt in qc_parts)

# 各步的 DS-v4-flash 降级路。签名统一成 (输入, 输出, 保留词清单) —— 视角转换用不上第三个参数，
# 但让签名一致比在调用处分叉少一个出错的地方。
_DEGRADE = {
    "narrate": lambda src, dst, keep, d, ui: pp_deepseek.narrate(src, dst, directive=d, ui_lang=ui),
    "redact": lambda src, dst, keep, d, ui: pp_redact_ds.redact(src, dst, keep, directive=d, ui_lang=ui),
}


_FIX_MARK = re.compile(rf"<!--\s*{pp_lang.FIX_COUNT_MARK}:\s*(\d+)\s*-->")


def parse_qc_fix_count(text: str) -> int:
    """从 QC 报告/问题清单解析修复数。解析不出 → 0（防御式，不编造）。

    三级：
      ① `<!-- qc-fix-count: N -->` —— 降级路自己埋的机读行，**与报告用什么语言无关**；
      ② 「共修复 N 处」总计行 —— Claude 路的报告由模型写，skill 要求末行必写（取末次）；
      ③ 所有「修复 N 处」相加。

    ⚠️ ① 是 2026-08-30 加的：报告文案扩到 8 门之后，②③ 那两个中文正则在德语/日语报告上
    一条都抓不到，症状是卡片显示「质检已修复 0 处」——不报错。**机读的东西不该跟给人看的
    文案是同一串字。** ②③ 留着是给 Claude 路和老单的报告（它们没有 ① 那一行）。
    """
    mark = _FIX_MARK.findall(text or "")
    if mark:
        return int(mark[-1])
    totals = re.findall(r"共修复\s*(\d+)\s*处", text or "")
    if totals:
        return int(totals[-1])
    return sum(int(n) for n in re.findall(r"修复\s*(\d+)\s*处", text or ""))


# 契约行必须独占一行才改写；边上用 [ \t] 不用 \s——\s 含换行，会把上下的空行一并吞掉
_TOTAL_LINE = re.compile(r"^[ \t]*共修复\s*(\d+)\s*处[ \t]*$", re.M)


def localize_qc(text: str, ui_lang: str | None) -> str:
    """Claude 路的报告末行是中文契约「共修复 N 处」（skill 要求必写，机读靠它）——
    报告主体已按指令写成界面语言，唯独这行契约是给程序看的中文，德语用户下载却看得见。
    存档前把给人看的字换成界面语言、机读换成隐形注释：`parse_qc_fix_count` 本来就
    **优先认注释**（降级路 2026-08-30 起就这么埋），等于把已有机制用到底。

    降级路的报告已带注释 → 总计行原样（它本来就是界面语言）。
    ⚠️ 中文界面也走这一遭：人读那行逐字不变、只多一行注释——「只在非中文时才转」
    的路径平时没人跑，这是这一批反复踩实的原则。

    ⚠️ **说话人代号的替换要在两条路都做**（2026-08-30 八门实测拍到）：报告举证时会把
    输入稿行首的内部代号原样抄进引文（`Original: 主持人：…`），en/it 两份都中招。
    所以它排在 `_FIX_MARK` 早退**之前**——降级路的报告同样引用输入稿。"""
    if not text:
        return text
    text = speaker_labels.localize_quoted_labels(text, ui_lang)
    if _FIX_MARK.search(text):
        return text
    total = pp_lang.report(ui_lang)["total"]

    def _sub(m: re.Match) -> str:
        n = m.group(1)
        return total.replace("{n}", n) + "\n" + pp_lang.fix_count_line(int(n))

    return _TOTAL_LINE.sub(_sub, text)


def _step_prompt(step: str, in_rel: str, out_rel: str, list_rel: str | None,
                 directive: str = "") -> str:
    """⚠️ 语言指令必须同时进这里和降级路的 system（`_DEGRADE`）——只送一条的症状是
    「有时候是对的」，而降级是撞顶时才走的，平时测不出来。见 pp_lang 的文件头。"""
    if step == "narrate":
        prompt = f"/pp-narrate {in_rel} {out_rel}"
    else:
        prompt = f"/pp-redact {in_rel} {out_rel}"
        if list_rel:
            prompt += f" {list_rel}"
    return prompt + directive


def _invoke_claude(prompt: str) -> dict:
    """跑一次 claude -p（cwd=vendor 根，同 P3 机制），返回 classify dict
    （state: ok|capped|error|timeout）。超时即杀子进程（P2/P3 同款：看门狗被心跳喂着，
    超时是挂死的唯一回收手段）。"""
    cmd = skill_merge.build_claude_cmd(prompt)
    try:
        proc = subprocess.run(cmd, cwd=str(VENDOR_ROOT), env=dict(os.environ),
                              capture_output=True, text=True,
                              timeout=int(os.environ.get("P3_TIMEOUT_SEC", "3600")))
    except subprocess.TimeoutExpired:
        return {"state": "timeout", "window": None, "rate_limits": [],
                "message": f"Claude 超时（>{os.environ.get('P3_TIMEOUT_SEC', '3600')}s）"}
    stdout = getattr(proc, "stdout", "") or ""
    if stdout:
        print(stdout[-2000:], flush=True)   # 尾部透传到日志
    return skill_merge.classify_claude_result(stdout, getattr(proc, "returncode", 1))


def _run_step(step: str, in_path: Path, out_path: Path,
              list_path: Path | None, gate, directive: str = "") -> str:
    """跑一步。返回 "ok" | "capped" | "gate_full" | "failed"。

    capped 与 gate_full 仍分开返回（日志与运营要分得清是哪一种），但**处置已统一为降级**
    （2026-08-13 改，理由见模块头）。并发闸按步 acquire/release，且**每步一把独立的闸**
    （engine=pp_<step>），闸租约 60min——整任务占着会过期漏计。
    硬错（含产物缺失/空产物）重试 1 次；超时不重试。"""
    if not gate.acquire():
        print(f"[PP] {step} 的 Claude 并发闸满 → 直接降级（不排队）", flush=True)
        return "gate_full"
    try:
        rel = lambda p: str(p.relative_to(VENDOR_ROOT))  # noqa: E731
        prompt = _step_prompt(step, rel(in_path), rel(out_path),
                              rel(list_path) if list_path else None, directive)
        for attempt in range(2):   # 首跑 + 硬错重试 1 次（契约）
            cls = _invoke_claude(prompt)
            # 产物已就绪 → 按成功收下，不管有没有 rate_limit 预警。
            # P3 的「allowed_warning 即 capped」是为**下一次**预判降级设计的；这里手上已经有
            # Opus 产物了，把它判成 capped 只会丢弃一份好稿去换 Flash 稿
            # （2026-07-24 真机 95% 额度实测踩中：当时无降级路，表现是无限重跑）。
            if out_path.exists() and out_path.stat().st_size > 0 and cls["state"] in ("ok", "capped"):
                if cls["state"] == "capped":
                    print(f"[PP] {step} 成功（带 {cls.get('window')} 预警，产物照收）", flush=True)
                return "ok"
            if cls["state"] == "capped":
                print(f"[PP] 撞顶({cls.get('window')}) → {step} 降级", flush=True)
                return "capped"
            if cls["state"] == "timeout":
                print(f"[PP] Claude 超时 → {step} 判失败（不重试）", flush=True)
                return "failed"
            if cls["state"] == "ok" and out_path.exists() and out_path.stat().st_size > 0:
                return "ok"
            print(f"[PP] {step} 第 {attempt + 1} 次失败（state={cls['state']}，产物"
                  f"{'缺失' if not out_path.exists() else '就绪'}）", flush=True)
        return "failed"
    finally:
        gate.release()


def run(pp: dict) -> None:
    """处理一个**已领取**（running）的后处理任务。异常吞掉落 set_failed。
    pp 是 postprocess.claim_* 返回的 dict。"""
    job_id = pp["job_id"]
    steps = pp["steps"]
    step = steps[0] if steps else None
    # 工作目录放 vendor/Output 下（claude cwd=vendor 根，相对路径读写不出沙箱）；跑完整目录删
    out_root = VENDOR_ROOT / "Output"
    out_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"pp_{job_id[:8]}_", dir=out_root))
    try:
        # 输入快照（发起时 API 落 R2）：清单内容只认快照，此后改配置不影响本任务
        inputs = json.loads(blobstore.get_bytes(f"postprocess/{job_id}/inputs.json"))
        qa_md = segments_to_qa(_load_transcript_segments(job_id))
        current = workdir / "transcript.md"
        current.write_text(qa_md, encoding="utf-8")
        list_path = None
        if inputs.get("redactList"):
            list_path = workdir / "redact_list.md"
            list_path.write_text(inputs["redactList"].get("content") or "", encoding="utf-8")

        # 语言：产物正文跟输入稿（＝录音语种），报告里我们写的字跟发起时的界面语言。
        # 老单没有 ui_lang → pp_lang 回落英文，不回落中文。
        ui_lang = pp.get("ui_lang")
        directive = pp_lang.directive(ui_lang)

        qc_parts: list[tuple[str, str]] = []
        for idx, step in enumerate(steps, 1):
            postprocess.update_step(job_id, step, idx)
            out_path = workdir / f"{step}.md"
            # 每步一把独立的 Claude 闸（转录那把也独立），互不挤占
            gate = claude_gate.DbClaudeGate(f"pp-{job_id}", engine=f"pp_{step}")
            result = _run_step(step, current, out_path, list_path, gate, directive)
            if result in ("capped", "gate_full") and step in _DEGRADE:
                # **撞顶与闸满都降级**（2026-08-13）：不让用户干等，理由见模块头。
                # 降级失败不判任务失败，退回原路延后重试——兜底挂了不该比没兜底更糟。
                why = "撞顶" if result == "capped" else "闸满"
                print(f"[PP] Claude {why} → {_STEP_TITLE.get(step, step)}转 DS-v4-flash 降级路",
                      flush=True)
                pp_deepseek.reset_usage()                 # 成本埋点：每步单独计，多步降级各记各的
                degraded_ok = _DEGRADE[step](current, out_path, list_path, directive, ui_lang) == "ok"
                cny = pp_deepseek.usage_cny()
                postprocess.add_ds_cost(job_id, cny)      # 失败也记——钱已经花了
                print(f"[PP] 降级路用量 {pp_deepseek.usage_snapshot()}｜¥{cny:.4f}", flush=True)
                if degraded_ok:
                    postprocess.mark_degraded(job_id, step)   # 留痕：运营页要标出「本单降级产出」
                    print(f"[PP] 降级路完成 {step}（本单为 DeepSeek 产出）", flush=True)
                    result = "ok"
                else:
                    print("[PP] 降级路也未成功 → 退回延后重试", flush=True)
            if result in ("capped", "gate_full"):
                postprocess.requeue_delayed(job_id)   # 回 queued 延后重试，重跑从头开始
                return
            if result == "failed":
                postprocess.set_failed(job_id, step, f"{_STEP_TITLE.get(step, step)} 步执行失败（Claude 硬错/超时/产物缺失）")
                return
            qc_file = workdir / f"{step}{_QC_SUFFIX[step]}"
            if qc_file.exists() and qc_file.stat().st_size > 0:
                qc_parts.append((step, localize_qc(qc_file.read_text(encoding="utf-8"), ui_lang)))
            current = out_path   # 前一步输出即下一步输入

        # 产物/QC 上 R2（30 天生命周期，postprocess/ 前缀）
        for s in steps:
            blobstore.put_bytes(f"postprocess/{job_id}/{s}.md",
                                (workdir / f"{s}.md").read_bytes(),
                                "text/markdown; charset=utf-8")
        qc_fix_count = sum(parse_qc_fix_count(txt) for _, txt in qc_parts)
        has_qc = bool(qc_parts)
        if has_qc:
            qc_md = qc_md_for(qc_parts, ui_lang)
            blobstore.put_bytes(f"postprocess/{job_id}/qc.md", qc_md.encode("utf-8"),
                                "text/markdown; charset=utf-8")

        # won=True 才结账（终态持有守卫，同转录 worker；库层唯一索引再兜一层幂等）
        won = postprocess.set_done(job_id, list(steps), qc_fix_count, has_qc)
        if won and pp.get("price_cents", 0) > 0 and pp.get("user_email"):
            try:
                job = jobstore.get_job(job_id)
                accounts.settle_postprocess(pp["user_email"], job_id, pp["price_cents"],
                                            file_name=job.file_name if job else None)
            except Exception:  # noqa: BLE001  结账失败不拖垮产物交付（产物已上 R2）
                print(f"后处理结账失败 job={job_id}: {traceback.format_exc()[-300:]}", flush=True)
    except Exception:
        postprocess.set_failed(job_id, step, traceback.format_exc()[-2000:])
    finally:
        # 中间产物留档放 finally：**失败与撞顶退回时最需要它**。此前这两条路径直接 return，
        # 工作目录连同「已经跑完的那几步」一起被删——界面上只剩「失败了」三个字，
        # 做到哪一步、做出来什么样，一概查不到。成功路只有产物和 qc 上了 R2，
        # 送进第一步的输入稿（segments_to_qa 的产物）同样没留，而输出不对时它是第一个要看的。
        try:
            _dump_workdir(job_id, workdir)
        finally:
            # 清理必须无条件执行：留档若抛出（哪怕 BaseException），工作目录会漏在
            # vendor/Output 里越堆越多。嵌一层 finally 比信任上面那个 except 更省心。
            shutil.rmtree(workdir, ignore_errors=True)


def _dump_workdir(job_id: str, workdir) -> None:
    """把后处理工作目录里的稿传到 `postprocess/<job_id>/pipeline/`。

    挂 `postprocess/` 前缀 = 复用它 30 天的 R2 生命周期（同转录侧挂 review/ 的做法）。
    工作目录是本单专属的临时目录，里面的东西全是这一单的，所以 `since=0` 全收，
    不需要转录侧那套按修改时间隔离上一单的逻辑。

    开关与转录侧共用 `PIPELINE_DUMP`；整段吞异常——留档坏了不许改变这一单的成败。
    """
    if os.environ.get("PIPELINE_DUMP", "1") == "0":
        return
    try:
        files, dropped = debug_dump.collect(0, workdir)
        got = []
        for name, path in files:
            try:
                blobstore.put_file(f"postprocess/{job_id}/pipeline/{name}", str(path))
                got.append(name)
            except Exception as e:  # noqa: BLE001  单个传不上去不该连累其余的
                dropped.append(f"{name}: 上传失败 {type(e).__name__} {e}")
        if dropped:   # 丢了什么必须说出来：「没收到」和「本来就没有」长得一样
            print(f"[PP] 中间产物留档 {job_id}: {len(got)} 个已传，{len(dropped)} 个未传 → {dropped[:3]}",
                  flush=True)
    except Exception:  # noqa: BLE001
        print(f"[PP] 中间产物留档失败 {job_id}（不影响本单）: {traceback.format_exc()[-300:]}", flush=True)

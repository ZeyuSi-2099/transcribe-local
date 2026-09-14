"""P0–P3 中间产物留档：把这一单跑出来的各阶段稿收起来，交给上层上 R2。

**为什么需要**：Fly 是一机一任务、跑完自毁，workdir 也在 worker 的 finally 里整个删掉。
在此之前只有**终稿 / 复核清单 / P3 报告**上了 R2，而诊断真正要看的是中间那几步：
主轨与各路参考轨各自听成了什么（P1）、四轨并排对齐得对不对（P2）、
程序算出的分歧点清单长什么样（P3 输入）。跑完这些就永远查不到了——2026-08-12 排查
「复核卡片列出全篇同词」时就卡在这一步，报告已灰飞烟灭，只能靠推断。

**只收文本稿，不收音频**：P0 的 FLAC/切片既是大头也最敏感，且回放音频已另存 7 天。

**为什么按 mtime 筛而不是按文件名**：`Output/` 没有任何人清理——Fly 上随机器消失，
本地回滚态（FLY_DISPATCH=0）则会跨任务累积。按「本单开跑之后写的」筛才不会把上一单的稿
混进来；文件名带时间戳但格式是 vendor 的，跟着上游走，不该由这里去解析。

**留档失败绝不许连累转录**：所有出口都吞异常返回空（同 p3_health / conflicts 的处置原则）。
"""
from __future__ import annotations

import os
from pathlib import Path

# 单文件与总量上限。**超了要报出来，不许静默截断**——「没收到」和「本来就没有」
# 长得一样，是最难查的一类假象。实测一单 66 分钟中文访谈全套约 1.5 MB，四小时约 5 MB，
# 这两个数是给异常单留的保险丝，正常单碰不到。
MAX_FILE_MB = 32
MAX_TOTAL_MB = 128

# mtime 比较留 2 秒余量：t0 取在 P0 之前，但文件系统时间戳精度与时钟微调都可能差一点，
# 卡太紧会把本单最早那个产物筛掉。
_SLACK_SEC = 2.0


def collect(since: float, output_dir: str | os.PathLike) -> tuple[list[tuple[str, Path]], list[str]]:
    """→ ([(相对名, 绝对路径)], [被丢掉的说明])。

    相对名保留 `_p3in/` 这层目录——P3 输入副本与 P2 原稿**同名**（产物名由输入 base 决定），
    拍平会互相覆盖，而覆盖后两者都看不出来。
    """
    dropped: list[str] = []
    try:
        root = Path(output_dir)
        if not root.is_dir():
            return [], [f"Output 目录不存在: {root}"]
        cands = [p for p in (*root.glob("*.md"), *root.glob("_p3in/*.md")) if p.is_file()]
        fresh = [p for p in cands if p.stat().st_mtime >= since - _SLACK_SEC]
        fresh.sort(key=lambda p: p.stat().st_mtime)

        picked: list[tuple[str, Path]] = []
        total = 0
        for p in fresh:
            size = p.stat().st_size
            if size > MAX_FILE_MB * 1024 * 1024:
                dropped.append(f"{p.name}: 单文件 {size / 1048576:.1f}MB 超过 {MAX_FILE_MB}MB")
                continue
            if total + size > MAX_TOTAL_MB * 1024 * 1024:
                dropped.append(f"{p.name}: 累计已达 {MAX_TOTAL_MB}MB 上限，此文件及其后未留档")
                break
            picked.append((str(p.relative_to(root)), p))
            total += size
        return picked, dropped
    except Exception as e:  # noqa: BLE001  留档是旁路，坏了不许连累转录
        return [], [f"收集中间产物失败: {type(e).__name__} {e}"]

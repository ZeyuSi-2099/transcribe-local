# -*- coding: utf-8 -*-
"""内存与核数探测 —— 决定几台引擎能同时跑。

只用标准库。整条链除了 sherpa_onnx + numpy 不吃第三方依赖，是这个项目最值钱的一条，
不能为了读一个内存数字把 psutil 拉进来。三个平台三套读法，读不出来就返回 0，
调用方退回最保守的那档（逐台跑）—— 探测失败不该让人跑不动。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

# 给系统、浏览器，以及后面 P3 那步留的余量。宁可少跑一台，也不要把机器逼到换页 ——
# 16 G 机器上换页不是「慢一点」，是一批十几分钟出不来（14B 那次的教训）。
RESERVE_MB = 4096


def _win_status():
    import ctypes

    class S(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    s = S()
    s.dwLength = ctypes.sizeof(S)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s


def total_mb() -> int:
    """这台机器一共多少内存。读不出来返回 0。"""
    try:
        if sys.platform == "win32":
            return int(_win_status().ullTotalPhys // 2 ** 20)
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2 ** 20)
    except Exception:
        return 0


def available_mb() -> int:
    """现在还剩多少可用。读不出来返回 0。

    ⚠️ 这个数**只用来开跑前示警，不用来定默认并行数**：它随手头开着什么应用大幅波动，
    拿它当分母会让同一台机器今天跑三台、明天跑一台，成绩不可比。默认值一律按总内存算。
    """
    try:
        if sys.platform == "win32":
            return int(_win_status().ullAvailPhys // 2 ** 20)
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+)", out).group(1))
            got = {k: int(v) for k, v in re.findall(r"Pages (\w[\w\- ]*?):\s+(\d+)\.", out)}
            free = got.get("free", 0) + got.get("inactive", 0) + got.get("speculative", 0)
            return int(free * page // 2 ** 20)
        with open("/proc/meminfo", encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1]) // 1024
        return 0
    except Exception:
        return 0


def plan(cfg: dict, engine_ids: list[str]) -> tuple[int, str]:
    """同时跑几台，外加一句人话解释为什么是这个数。

    分母用**权重文件的字节数**当单台峰值，这是个上界（onnxruntime 多半 mmap 进来，
    真实常驻更低），所以算出来偏保守 —— 宁可少跑一台。
    """
    from . import engines as eng

    want = cfg["engines"].get("parallel", "auto")
    threads = max(1, int(cfg["engines"].get("num_threads", 2) or 2))
    cores = os.cpu_count() or 2
    heaviest = max((eng.PEAK_MB.get(e, 1200) for e in engine_ids), default=1200)
    total = total_mb()

    by_cores = max(1, (cores - 1) // threads)          # 留一个核给系统，不然界面都卡
    by_mem = max(1, (total - RESERVE_MB) // heaviest) if total else 1
    safe = max(1, min(len(engine_ids), by_cores, by_mem))
    ram = f"{total / 1024:.0f} G" if total else "读不出来"
    basis = (f"{cores} 核 ÷ 每台 {threads} 线程、留 1 核给系统 → 最多 {by_cores} 台；"
             f"内存 {ram} 减去预留 {RESERVE_MB // 1024} G、最重的一台按 {heaviest} M 算 → 最多 {by_mem} 台")

    if want in (None, "auto"):
        return safe, f"自动：{basis}。取小 → {safe} 台"
    n = max(1, int(want))
    if n > safe:
        return n, f"⚠️ 你指定 {n} 台，安全值是 {safe} 台（{basis}）。照跑 —— 但内存不够会换页，反而更慢"
    return n, f"你指定 {n} 台（安全值 {safe} 台）"

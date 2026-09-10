# -*- coding: utf-8 -*-
"""命令行入口。

这个入口不只是给「不想装桌面应用的人」用的 —— 它也是我们自己跑回归的工具，
以及贡献者不装 Rust / Tauri 就能开发流水线的那条路。桌面应用调的是同一套代码。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import __version__

_MINIMAL = ["paraformer_2023"]        # 与 models.MINIMAL_ENGINES 一致，这里只为 --help 文案不触发导入


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="transcribe-local", description="四台本地引擎转一段录音，分歧交给大模型定字")
    ap.add_argument("--version", action="version", version=f"transcribe-local {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑完整条链：切块 → 四路 ASR → 分歧册 → 融合 → 导出")
    p_run.add_argument("audio", type=Path)
    p_run.add_argument("-o", "--out", type=Path, default=Path("out"))
    p_run.add_argument("-c", "--config", type=Path, default=Path("config.yaml"))
    p_run.add_argument("--terms", help="术语库路径，逗号分隔")
    p_run.add_argument("--engines", help="临时指定引擎，逗号分隔")
    p_run.add_argument("--no-fuse", action="store_true", help="只跑到分歧册，不调 Phase 3")

    p_setup = sub.add_parser("setup", help="首次配置：查依赖 → 下模型 → 写一份你自己的 config.yaml")
    p_setup.add_argument("--minimal", action="store_true",
                         help=f"只装一路（{'、'.join(_MINIMAL)}，约 271 M）—— 先出一份稿子再说")
    p_setup.add_argument("-y", "--yes", action="store_true", help="不问，直接装")
    p_setup.add_argument("--dry-run", action="store_true", help="只说要下什么、多大，不真下")

    p_models = sub.add_parser("models", help="模型清单与下载")
    p_models.add_argument("action", choices=["list", "pull"])
    p_models.add_argument("ids", nargs="*", help="模型 id；留空 = 默认要用的全部")
    p_models.add_argument("--minimal", action="store_true", help="只下能出稿的最小一套")

    p_cfg = sub.add_parser("config", help="看配置")
    p_cfg.add_argument("--explain", metavar="键", help="这个默认值从哪来的")
    p_cfg.add_argument("--init", action="store_true", help="在当前目录写一份 config.yaml 模板")

    sub.add_parser("doctor", help="检查依赖与模型是否就绪")

    a = ap.parse_args(argv)
    return {"run": _run, "setup": _setup, "models": _models,
            "config": _config, "doctor": _doctor}[a.cmd](a)


def _load_cfg(a) -> dict:
    from . import config
    cfg = config.load(a.config if getattr(a, "config", None) else None)
    if getattr(a, "terms", None):
        cfg["terms"]["files"] = [s.strip() for s in a.terms.split(",") if s.strip()]
    if getattr(a, "engines", None):
        cfg["engines"]["enabled"] = [s.strip() for s in a.engines.split(",") if s.strip()]
    return cfg


def _run(a) -> int:
    from . import audio, diarize, divergence, engines, export, fuse, models

    cfg = _load_cfg(a)
    ids = cfg["engines"]["enabled"]
    missing = [m for m in _needed(cfg) if not models.installed(m)]
    if missing:
        print(f"缺模型：{', '.join(missing)}\n先跑：transcribe-local models pull", file=sys.stderr)
        return 2

    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    stem = a.audio.stem

    t0 = time.time()
    wav = audio.to_wav(a.audio, out / f"{stem}.16k.wav",
                       cfg["audio"]["sample_rate"], cfg["audio"]["channels"])
    x = audio.load(wav)
    print(f"P0  音频 {len(x) / audio.SR / 60:.1f} 分钟")

    segs = diarize.diarize(x, cfg)
    blocks = diarize.chop(x, segs, cfg)
    diarize.save_blocks(blocks, segs, out / "work")
    print(f"    声纹 {len(segs)} 段 → 切块 {len(blocks)} 块  ({time.time() - t0:.0f}s)")

    rows, qc = {}, []
    for e in ids:
        t = time.time()
        rows[e], bad = engines.transcribe(e, x, blocks, cfg)
        qc += bad
        (out / "work" / f"p1_{engines.TAG.get(e, e)}.md").write_text(
            engines.dump(rows[e]), encoding="utf-8")
        chars = sum(len(r.text) for r in rows[e])
        print(f"P1  {engines.TAG.get(e, e):<6} {engines.ROUTE.get(e, ''):<12} "
              f"{chars:>6} 字  {time.time() - t:.0f}s"
              + (f"  ⚠️ {len(bad)} 块跑飞" if bad else ""))
    if qc:
        import json
        (out / "work" / "qc_warnings.json").write_text(
            json.dumps(qc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"⚠️ 共 {len(qc)} 块重试用尽仍跑飞，清单见 work/qc_warnings.json")

    p3in, ledger, found = divergence.build(rows, cfg)
    (out / "work" / "p3_input.md").write_text(p3in, encoding="utf-8")
    (out / "work" / "divergence.md").write_text(ledger, encoding="utf-8")
    n_sub = sum(len(d.substantive) for d in found)
    n_fill = sum(d.filler_count for d in found)
    print(f"分歧  实质 {n_sub} 处 + 语气词 {n_fill} 处（已折叠）")

    if a.no_fuse:
        print(f"\n停在分歧册（--no-fuse）。产物在 {out / 'work'}")
        return 0

    t = time.time()
    print(f"P3  {cfg['p3']['model']} @ {cfg['p3']['base_url']}")
    merged = fuse.fuse(p3in, found, cfg,
                       on_batch=lambda n, N: print(f"    批次 {n}/{N}", end="\r", flush=True))
    (out / f"{stem}.merged.md").write_text(merged, encoding="utf-8")
    uncertain = merged.count("[❓]")
    print(f"    {len(merged.splitlines())} 行，存疑 {uncertain} 处  ({time.time() - t:.0f}s)")

    written = export.write(merged, out / stem, cfg)
    print("\n导出：" + " · ".join(str(p) for p in written))
    print(f"总耗时 {time.time() - t0:.0f}s")
    return 0


def _setup(a) -> int:
    """首启向导。目标是「一台干净机器从零装到出稿」，所以每一步失败都要说清下一步怎么办。"""
    from . import audio, config, models

    print(f"transcribe-local {__version__} · 首次配置\n")

    print("① 依赖")
    try:
        import sherpa_onnx                                    # noqa: F401
        print("   ✓ sherpa-onnx")
    except ImportError:
        print("   ✗ sherpa-onnx 没装 —— 整条链都靠它，先跑：pip install sherpa-onnx", file=sys.stderr)
        return 2
    if audio.have_ffmpeg():
        print("   ✓ ffmpeg")
    else:
        print("   ! ffmpeg 没装 —— 不装也能跑，但只收 16 kHz 单声道 .wav")

    cfg = config.load(None)
    if a.minimal:
        cfg["engines"]["enabled"] = models.MINIMAL_ENGINES
    miss, mb = models.plan(_needed(cfg))

    print(f"\n② 模型（缓存目录 {models.cache_dir()}）")
    if not miss:
        print("   ✓ 都装好了")
    else:
        for e in miss:
            print(f"   ✗ {e.id:<26}{e.size_mb:>5} M  {e.role}")
        print(f"   合计要下 {mb} M")
        if not a.minimal and mb > 1000:
            print(f"   想先小跑一趟就加 --minimal：只装 {'、'.join(models.MINIMAL_ENGINES)}，"
                  f"能出稿但没有多路融合")
        if a.dry_run:
            print("\n（--dry-run，什么都没下）")
            return 0
        if not a.yes and not _ask(f"   现在下这 {len(miss)} 个？"):
            print("   跳过。想下的时候跑：transcribe-local models pull")
            return 0
        for e in miss:
            print(f"   {e.id:<26} 下载中 …")
            models.download(e.id, on_progress=lambda got, total:
                            print(f"     {got / 1e6:.0f} / {total / 1e6:.0f} M", end="\r", flush=True))
            print(f"   {e.id:<26} 完成            ")

    print("\n③ 配置")
    dst = Path("config.yaml")
    if dst.exists():
        print(f"   {dst} 已存在，没动它")
    elif a.dry_run:
        print(f"   （--dry-run，没写 {dst}）")
    else:
        dst.write_text(config.starter(cfg), encoding="utf-8")
        print(f"   ✓ 写好了 {dst} —— 只放常改的那几个键，其余走默认值")

    print("\n④ Phase 3（融合定字）")
    print(f"   现在指向 {cfg['p3']['model']} @ {cfg['p3']['base_url']}")
    print("   本机模型要先把服务起起来；也可以把 base_url 改到任何说 OpenAI 协议的 API。")
    print("   不想现在管它就先跑 `run --no-fuse`，只出分歧册。")

    print(f"\n就绪。下一步：\n   transcribe-local run 你的录音.m4a")
    return 0


def _ask(q: str) -> bool:
    try:
        return input(f"{q} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:                                          # 非交互环境（管道 / CI）当作否
        print("（非交互环境，当作否）")
        return False


def _needed(cfg: dict) -> list[str]:
    need = list(cfg["engines"]["enabled"])
    need += [cfg["diarize"]["segmentation"], cfg["diarize"]["embedding"]]
    if cfg["vad"]["enabled"]:
        need.append(cfg["vad"]["model"])
    return need


def _models(a) -> int:
    from . import models
    mf = models.manifest()
    if a.action == "list":
        print(f"{'模型':<26}{'角色':<22}{'体积':>8}  {'许可':<10}{'状态'}")
        for e in mf.values():
            print(f"{e.id:<26}{e.role:<22}{e.size_mb:>6} M  {e.license:<10}"
                  f"{'已装' if models.installed(e.id) else '未装'}")
        miss, mb = models.plan([e.id for e in mf.values() if e.enabled_by_default])
        print(f"\n缓存目录：{models.cache_dir()}")
        print(f"还缺 {len(miss)} 个、合计 {mb} M" if miss else "默认要用的都装好了")
        print("⚠️ 许可一栏尚未逐个核实 —— 模型权重的条款与本仓库的许可是两回事。")
        return 0

    if a.minimal and not a.ids:
        from . import config
        cfg = config.load(None)
        cfg["engines"]["enabled"] = models.MINIMAL_ENGINES
        ids = _needed(cfg)
    else:
        ids = a.ids or [e.id for e in mf.values() if e.enabled_by_default]
    for mid in ids:
        if models.installed(mid):
            print(f"{mid:<26} 已装，跳过")
            continue
        e = mf[mid]
        print(f"{mid:<26} 下载 {e.size_mb} M …")
        models.download(mid, on_progress=lambda got, total:
                        print(f"  {got / 1e6:.0f} / {total / 1e6:.0f} M", end="\r", flush=True))
        print(f"{mid:<26} 完成            ")
    return 0


def _config(a) -> int:
    from . import config
    if a.init:
        dst = Path("config.yaml")
        if dst.exists():
            print(f"{dst} 已存在，没覆盖", file=sys.stderr)
            return 1
        dst.write_text(config.starter(config.load(None)), encoding="utf-8")
        print(f"写好了 {dst}")
        return 0
    if not a.explain:
        print(config.DEFAULT_PATH.read_text(encoding="utf-8"))
        return 0
    hits = config.explain(a.explain)
    if not hits:
        print(f"配置里没有 `{a.explain}`", file=sys.stderr)
        return 1
    for line, mark, text in hits:
        note = config.PROVENANCE.get(mark, "无来源标注")
        head, _, inline = line.partition("#")                  # 行内注释归到「理由」里，别跟值挤一行
        print(f"\n{head.strip()}\n  来源：[{mark or '—'}] {note}\n")
        body = [inline] + text.splitlines()[1:]
        for t in body:
            t = re.sub(r"^\s*#\s?", "", t).rstrip()
            t = re.sub(r"^\s*\[(?:%s)\]\s*" % "|".join(config.PROVENANCE), "", t)
            if t.strip():
                print("  " + t)
    return 0


def _doctor(a) -> int:
    from . import audio, config, models
    ok = True
    print(f"transcribe-local {__version__}\n")

    try:
        import sherpa_onnx
        print(f"✓ sherpa-onnx  {getattr(sherpa_onnx, '__version__', '?')}")
    except ImportError:
        print("✗ sherpa-onnx  没装")
        ok = False

    if audio.have_ffmpeg():
        print("✓ ffmpeg")
    else:
        print("✗ ffmpeg       没装（只能喂 16 kHz 单声道 .wav）")

    cfg = config.load(None)
    print(f"\n模型缓存：{models.cache_dir()}")
    for m in _needed(cfg):
        print(f"{'✓' if models.installed(m) else '✗'} {m}")
        ok = ok and models.installed(m)

    p3 = cfg["p3"]
    print(f"\nPhase 3：{p3['model']} @ {p3['base_url']}")
    try:
        import httpx
        httpx.get(p3["base_url"].rstrip("/") + "/models", timeout=3)
        print("✓ 后端可达")
    except Exception:
        print("✗ 后端不可达 —— 本地模型记得先起服务，或把 base_url 改到一个 API")
        ok = False

    print("\n就绪" if ok else "\n还差东西，见上面的 ✗")
    return 0 if ok else 1

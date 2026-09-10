# -*- coding: utf-8 -*-
"""命令行入口。

这个入口不只是给「不想装桌面应用的人」用的 —— 它也是我们自己跑回归的工具，
以及贡献者不装 Rust / Tauri 就能开发流水线的那条路。桌面应用调的是同一套代码。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__


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

    p_models = sub.add_parser("models", help="模型清单与下载")
    p_models.add_argument("action", choices=["list", "pull"])
    p_models.add_argument("ids", nargs="*", help="模型 id；留空 = 默认要用的全部")

    p_cfg = sub.add_parser("config", help="看配置")
    p_cfg.add_argument("--explain", metavar="键", help="这个默认值从哪来的")

    sub.add_parser("doctor", help="检查依赖与模型是否就绪")

    a = ap.parse_args(argv)
    return {"run": _run, "models": _models, "config": _config, "doctor": _doctor}[a.cmd](a)


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

    rows = {}
    for e in ids:
        t = time.time()
        rows[e] = engines.transcribe(e, x, blocks, cfg)
        (out / "work" / f"p1_{engines.TAG.get(e, e)}.md").write_text(
            engines.dump(rows[e]), encoding="utf-8")
        chars = sum(len(r.text) for r in rows[e])
        print(f"P1  {engines.TAG.get(e, e):<6} {engines.ROUTE.get(e, ''):<12} "
              f"{chars:>6} 字  {time.time() - t:.0f}s")

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
        print(f"\n缓存目录：{models.cache_dir()}")
        print("⚠️ 许可一栏尚未逐个核实 —— 模型权重的条款与本仓库的许可是两回事。")
        return 0

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
    if not a.explain:
        print(config.DEFAULT_PATH.read_text(encoding="utf-8"))
        return 0
    hits = config.explain(a.explain)
    if not hits:
        print(f"配置里没有 `{a.explain}`", file=sys.stderr)
        return 1
    for line, mark, text in hits:
        note = config.PROVENANCE.get(mark, "无来源标注")
        print(f"\n{line}\n  来源：[{mark or '—'}] {note}\n")
        print("\n".join("  " + t.strip() for t in text.splitlines()[1:] if t.strip()))
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

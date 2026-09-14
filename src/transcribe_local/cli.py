# -*- coding: utf-8 -*-
"""命令行入口。

这个入口不只是给「不想装桌面应用的人」用的 —— 它也是我们自己跑回归的工具，
以及贡献者不装 Rust / Tauri 就能开发流水线的那条路。桌面应用调的是同一套代码。
"""
from __future__ import annotations

import argparse
import os
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

    p_serve = sub.add_parser("serve", help="起本机服务，界面在浏览器里开")
    p_serve.add_argument("--port", type=int, default=0, help="0 = 自动挑一个空闲端口")
    p_serve.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    p_serve.add_argument("-o", "--out", type=Path, default=Path("out"))
    p_serve.add_argument("-c", "--config", type=Path, default=Path("config.yaml"))

    sub.add_parser("doctor", help="检查依赖与模型是否就绪")

    a = ap.parse_args(argv)
    return {"run": _run, "setup": _setup, "serve": _serve, "models": _models,
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
    from . import models, pipeline

    cfg = _load_cfg(a)
    missing = [m for m in _needed(cfg) if not models.installed(m)]
    if missing:
        print(f"缺模型：{', '.join(missing)}\n先跑：transcribe-local setup", file=sys.stderr)
        return 2

    r = pipeline.run(a.audio, a.out, cfg, no_fuse=a.no_fuse, emit=_printer())
    if r.qc:
        print(f"⚠️ 共 {len(r.qc)} 块重试用尽仍跑飞，清单见 {r.out / 'work' / 'qc_warnings.json'}")
    if not a.no_fuse:
        print("\n导出：" + " · ".join(str(p) for p in r.exports))
    else:
        print(f"\n停在分歧册（--no-fuse）。产物在 {r.out / 'work'}")
    print(f"总耗时 {r.seconds:.0f}s")
    return 0


def _printer():
    """把流水线事件打成人话。界面那边用同样的事件画进度条。"""
    def say(kind, **kw):
        if kind == "audio":
            print(f"P0  音频 {kw['minutes']} 分钟")
        elif kind == "chop":
            print(f"    声纹 {kw['segments']} 段 → 切块 {kw['blocks']} 块  ({kw['seconds']}s)")
        elif kind == "engine_block":
            print(f"P1  {kw['engine']:<6} {kw['done']}/{kw['total']}", end="\r", flush=True)
        elif kind == "engine_done":
            print(f"P1  {kw['engine']:<6} {kw['chars']:>6} 字  {kw['seconds']}s"
                  + (f"  ⚠️ {kw['bad']} 块跑飞" if kw["bad"] else "") + " " * 12)
        elif kind == "p1qc":
            for f in kw["fail"]:
                print(f"P1  ❌ {f}")
            for w in kw["warn"]:
                print(f"P1  ⚠️ {w}")
            if not kw["fail"] and not kw["warn"]:
                print("P1  ✅ 质检通过")
        elif kind == "speaker_split":
            if kw.get("reason"):
                print(f"说话人  {kw['reason']}")
            else:
                print(f"说话人  {kw['split']} 块在换人处拆开 → 共 {kw['sub']} 段（按 Paraformer 逐字时间）")
        elif kind == "divergence":
            print(f"分歧  实质 {kw['substantive']} 处 + 语气词 {kw['fillers']} 处（已折叠）")
        elif kind == "stage" and kw["name"] == "P3":
            print(f"P3  {kw['text']}")
        elif kind == "batch":
            print(f"    批次 {kw['done']}/{kw['total']}", end="\r", flush=True)
        elif kind == "fused":
            print(f"    {kw['lines']} 行，存疑 {kw['uncertain']} 处  ({kw['seconds']}s)" + " " * 12)
    return say


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

    print("\n④ Phase 3（融合定字）—— 两条路，都支持")
    p3 = cfg["p3"]
    print(f"   当前指向：{p3['model']} @ {p3['base_url']}")
    env = p3.get("api_key_env", "")
    if p3["base_url"].startswith(("http://127.0.0.1", "http://localhost")):
        print("   这是本机模型 —— 全程离线，记得先把服务起起来（Ollama / LM Studio / mlx_lm.server）。")
    else:
        ok = bool(os.environ.get(env))
        print(f"   这是云端 API。{'✓ 已读到' if ok else '✗ 还没有'} 环境变量 {env}"
              + ("" if ok else f"  ——  export {env}=... 之后再跑"))
        print("   ⚠️ 走 API 这条路，这一步会把**转录文本**发给你选的那家（音频不会）。")
        print("      要全程离线，把 config.yaml 里的 base_url 改到本机服务即可，同一条代码路径。")
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
        print(f"{'模型':<26}{'角色':<22}{'下载':>7}  {'许可':<12}{'状态'}")
        for e in mf.values():
            print(f"{e.id:<26}{e.role:<22}{e.size_mb:>5} M  {e.license:<12}"
                  f"{'已装' if models.installed(e.id) else '未装'}")
        miss, mb = models.plan([e.id for e in mf.values() if e.enabled_by_default])
        print(f"\n缓存目录：{models.cache_dir()}")
        print(f"还缺 {len(miss)} 个、合计 {mb} M" if miss else "默认要用的都装好了")
        print("⚠️ 模型权重的许可与本仓库的 BSL 是两回事，用户要遵守的是模型方那一份。")
        print("   逐条来源见 models/manifest.toml 的 license_src 字段；上游可能改许可，升版本请重查。")
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


def _serve(a) -> int:
    from . import server
    return server.serve(_load_cfg(a), a.out, port=a.port, open_browser=not a.no_open,
                        cfg_path=a.config)


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

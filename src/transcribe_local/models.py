# -*- coding: utf-8 -*-
"""模型清单、路径解析、下载器。

⛔ 本仓库不重分发权重。下载器只按 models/manifest.toml 的地址从各自官方源拉。

断点续传是必须的，不是加分项：我们自己下 7.9 GB 模型时踩过 ——
`curl --retry` 配 `-C -` 会在重试时截断文件，下到 465 MB 直接归零。
所以这里走显式 HTTP Range 分块，每块下完就落盘。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import paths

CHUNK = 32 * 1024 * 1024          # 每次 Range 请求的大小
MANIFEST = paths.root() / "models" / "manifest.toml"


# 降级路径：只装这一路也能出稿（234 M，全片 54 秒）。
# 让人五分钟内看到第一份结果，再决定要不要下另外 2.5 G —— 没有融合，但有稿子。
MINIMAL_ENGINES = ["paraformer_2023"]


def plan(need: list[str]) -> tuple[list[Entry], int]:
    """要装哪些、一共多大。返回（缺的条目, 合计 MB）。"""
    mf = manifest()
    miss = [mf[m] for m in need if m in mf and not installed(m)]
    return miss, sum(e.size_mb for e in miss)


def cache_dir() -> Path:
    """可用 TRANSCRIBE_LOCAL_MODELS 覆盖。中国网络下换镜像用 TRANSCRIBE_LOCAL_ENDPOINT。"""
    env = os.environ.get("TRANSCRIBE_LOCAL_MODELS")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "transcribe-local" / "models"


@dataclass(frozen=True)
class Entry:
    id: str
    role: str
    url: str
    archive: str
    size_mb: int
    sha256: str
    license: str
    note: str
    enabled_by_default: bool

    @property
    def dirname(self) -> str:
        name = self.url.rsplit("/", 1)[-1]
        for suf in (".tar.bz2", ".tar.gz", ".onnx"):
            if name.endswith(suf):
                return name[: -len(suf)]
        return name

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def manifest() -> dict[str, Entry]:
    raw = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    out = {}
    for m in raw["model"]:
        out[m["id"]] = Entry(
            id=m["id"], role=m["role"], url=m["url"], archive=m.get("archive", "none"),
            size_mb=int(m.get("size_mb", 0)), sha256=m.get("sha256", ""),
            license=m.get("license", "未核实"), note=m.get("note", ""),
            enabled_by_default=bool(m.get("enabled_by_default", True)),
        )
    return out


def model_dir(model_id: str) -> Path:
    """解压出来的目录 —— ASR 引擎要在里面找多个文件。"""
    return cache_dir() / manifest()[model_id].dirname


def model_path(model_id: str) -> Path:
    """单个权重文件 —— 声纹分段 / 声纹嵌入 / VAD 用。"""
    e = manifest()[model_id]
    return cache_dir() / e.filename if e.archive == "none" else model_dir(model_id) / "model.onnx"


def installed(model_id: str) -> bool:
    p = model_path(model_id)
    return p.exists() if manifest()[model_id].archive == "none" else model_dir(model_id).is_dir()


def endpoint_rewrite(url: str) -> str:
    """镜像开关。⚠️ 注意：换域名不一定够 —— 我们实测在全局代理下 hf-mirror 也连不通，
    这个开关得和代理设置一起考虑。"""
    ep = os.environ.get("TRANSCRIBE_LOCAL_ENDPOINT")
    if not ep:
        return url
    for host in ("https://huggingface.co", "https://github.com"):
        if url.startswith(host):
            return ep.rstrip("/") + url[len(host):]
    return url


def download(model_id: str, on_progress=None) -> Path:
    """下到 `<名字>.part`，校验过了才改成正式名字。

    ⚠️ 这个 .part 不是洁癖，是修一个真 bug：以前直接下成正式名字，
    半路断掉留下的半截文件会被 installed() 当成「已装」跳过，用户再怎么重跑都修不回来 ——
    一个坏了的模型比没装更难查。同理，压缩包先解到临时目录、整体改名，
    免得解压中途断电留下一个半拉目录冒充装好了。
    """
    e = manifest()[model_id]
    cache_dir().mkdir(parents=True, exist_ok=True)
    blob = cache_dir() / (e.filename + ".part")
    url = endpoint_rewrite(e.url)

    total = _remote_size(url)
    while True:
        have = blob.stat().st_size if blob.exists() else 0
        if total and have >= total:
            break
        end = have + CHUNK - 1
        with httpx.stream("GET", url, headers={"Range": f"bytes={have}-{end}"},
                          follow_redirects=True, timeout=300) as r:
            if r.status_code not in (200, 206):
                raise RuntimeError(f"{model_id}: HTTP {r.status_code}（源 {url}）")
            with blob.open("ab") as f:
                for part in r.iter_bytes():
                    f.write(part)
        got = blob.stat().st_size
        if got <= have:                               # 一字节没进来，别空转
            raise RuntimeError(f"{model_id}: 下载停滞在 {have} 字节")
        if on_progress:
            on_progress(got, total)
        if not total:                                 # 源不报长度，只能靠 206 耗尽判断
            if got - have < CHUNK:
                break

    _verify(e, blob)
    if not e.archive.startswith("tar"):
        final = cache_dir() / e.filename
        blob.replace(final)
        return final

    stage = cache_dir() / (e.dirname + ".part")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    with tarfile.open(blob) as tf:
        tf.extractall(stage, filter="data")
    inner = stage / e.dirname                       # 归档里一般还包一层同名目录
    src = inner if inner.is_dir() else stage
    dst = model_dir(model_id)
    shutil.rmtree(dst, ignore_errors=True)
    src.rename(dst)
    shutil.rmtree(stage, ignore_errors=True)
    blob.unlink(missing_ok=True)
    return dst


def _remote_size(url: str) -> int:
    try:
        r = httpx.head(url, follow_redirects=True, timeout=30)
        return int(r.headers.get("content-length", 0))
    except Exception:
        return 0


def _verify(e: Entry, blob: Path) -> None:
    h = hashlib.sha256()
    with blob.open("rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    got = h.hexdigest()
    if not e.sha256:
        print(f"⚠️ {e.id} 清单里没有校验和，本次下载未校验。实际值：{got}")
        return
    if got != e.sha256:
        blob.unlink(missing_ok=True)
        raise RuntimeError(f"{e.id} 校验和不符，已删除。期望 {e.sha256}，实际 {got}")

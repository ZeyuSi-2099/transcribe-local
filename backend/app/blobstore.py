"""对象存储的本机实现：函数名与用法和线上（R2 / boto3）一致，文件存在本机文件夹 `config.BLOB_DIR`。

对象键就是相对路径（如 `audio/<job_id>.m4a`）。键里不许出现 `..`、绝对路径或反斜杠 ——
键有一部分来自请求参数，不挡住就能读写到存储目录之外。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath

from . import config


class NoSuchKey(Exception):
    """请求的对象键不存在 —— 供调用方区分「确实没有」与「取不到」。"""


def _root() -> Path:
    return Path(config.BLOB_DIR)


def _path(key: str) -> Path:
    p = PurePosixPath(key)
    if not key or "\\" in key or p.is_absolute() or ".." in p.parts:
        raise ValueError(f"非法的对象键：{key!r}")
    return _root().joinpath(*p.parts)


def _reset_client() -> None:
    """线上用来丢掉缓存的客户端；本机没有客户端，留着让测试原样能调。"""


def ensure_bucket() -> None:
    _root().mkdir(parents=True, exist_ok=True)


def _write_atomic(dst: Path, write) -> None:
    """先写临时文件再改名：写到一半断电，不会留下半截对象冒充完整的。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def put_file(key: str, local_path: str) -> None:
    with open(local_path, "rb") as src:
        _write_atomic(_path(key), lambda f: shutil.copyfileobj(src, f))


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    _write_atomic(_path(key), lambda f: f.write(data))


def upload_fileobj(key: str, fileobj, content_type: str = "application/octet-stream") -> None:
    _write_atomic(_path(key), lambda f: shutil.copyfileobj(fileobj, f))


def delete(key: str) -> None:
    """幂等：不存在也不报错。"""
    _path(key).unlink(missing_ok=True)


def exists(key: str) -> bool:
    try:
        return _path(key).is_file()
    except ValueError:
        return False


def list_keys(prefix: str) -> list[str]:
    root = _root()
    if not root.is_dir():
        return []
    out = []
    for dirpath, _, names in os.walk(root):
        for n in names:
            if n.startswith(".tmp-"):
                continue
            key = Path(dirpath, n).relative_to(root).as_posix()
            if key.startswith(prefix):
                out.append(key)
    return sorted(out)


def delete_many(keys: list[str]) -> int:
    """批量删除，返回受理的个数。与线上一样：一个删不掉不中断，不存在的键也算受理。"""
    done = 0
    for k in keys:
        try:
            delete(k)
            done += 1
        except Exception:  # noqa: BLE001  一个键删不掉不许中断，剩下的照删
            continue
    return done


def get_bytes(key: str) -> bytes:
    try:
        return _path(key).read_bytes()
    except FileNotFoundError as e:
        raise NoSuchKey(key) from e


class _Window:
    """只读文件的一段 [start, start+length)；读完或 close 时关文件。"""

    def __init__(self, f, length: int):
        self._f, self._left = f, length

    def read(self, n: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        n = self._left if n is None or n < 0 else min(n, self._left)
        b = self._f.read(n)
        self._left -= len(b)
        return b

    def close(self) -> None:
        self._f.close()


def open_range(key: str, start: int | None = None, end: int | None = None):
    """按字节范围取对象 → (流, 本次返回的字节数, 对象总大小)。流不读进内存，调用方读完要 close。"""
    path = _path(key)
    try:
        f = open(path, "rb")
    except FileNotFoundError as e:
        raise NoSuchKey(key) from e
    total = os.fstat(f.fileno()).st_size
    if start is None:
        return _Window(f, total), total, total
    last = total - 1 if end is None else min(end, total - 1)
    length = max(0, last - start + 1)
    f.seek(start)
    return _Window(f, length), length, total


def download_to(key: str, local_path: str) -> None:
    try:
        shutil.copyfile(_path(key), local_path)
    except FileNotFoundError as e:
        raise NoSuchKey(key) from e


def newest_key_age_hours(prefix: str) -> float | None:
    """某前缀下最新对象距今多少小时；没有对象 → None（不猜、不当成 0）。"""
    keys = list_keys(prefix)
    if not keys:
        return None
    newest = max(_path(k).stat().st_mtime for k in keys)
    return (time.time() - newest) / 3600

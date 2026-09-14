"""本机对象存储：与线上 R2 版同名同用法。本地独有的测试（线上没有这个文件）。"""
import io

import pytest

from app import blobstore, config


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BLOB_DIR", str(tmp_path / "blobs"))


def test_put_get_exists_delete():
    blobstore.put_bytes("review/j1.json", b'{"a":1}', content_type="application/json")
    assert blobstore.exists("review/j1.json")
    assert blobstore.get_bytes("review/j1.json") == b'{"a":1}'
    blobstore.delete("review/j1.json")
    blobstore.delete("review/j1.json")                      # 幂等
    assert not blobstore.exists("review/j1.json")
    with pytest.raises(blobstore.NoSuchKey):
        blobstore.get_bytes("review/j1.json")


def test_put_file_upload_fileobj_download_to(tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(b"x" * 1000)
    blobstore.put_file("audio/j1.m4a", str(src))
    blobstore.upload_fileobj("audio/j2.m4a", io.BytesIO(b"y" * 10))
    out = tmp_path / "out.bin"
    blobstore.download_to("audio/j1.m4a", str(out))
    assert out.read_bytes() == b"x" * 1000
    with pytest.raises(blobstore.NoSuchKey):
        blobstore.download_to("audio/none.m4a", str(out))


def test_list_keys_and_delete_many():
    for k in ["review/j1/p1.md", "review/j1/p3.md", "review/j2.json", "audio/j1.m4a"]:
        blobstore.put_bytes(k, b"-")
    assert blobstore.list_keys("review/j1/") == ["review/j1/p1.md", "review/j1/p3.md"]
    assert blobstore.list_keys("review/") == ["review/j1/p1.md", "review/j1/p3.md", "review/j2.json"]
    assert blobstore.delete_many(["review/j1/p1.md", "review/没有这个.md"]) == 2   # 不存在也算受理
    assert blobstore.list_keys("review/j1/") == ["review/j1/p3.md"]


def test_open_range_semantics():
    blobstore.put_bytes("audio/a.m4a", bytes(range(100)))
    body, n, total = blobstore.open_range("audio/a.m4a")
    assert (n, total) == (100, 100) and body.read() == bytes(range(100))
    body.close()
    body, n, total = blobstore.open_range("audio/a.m4a", 10, 19)
    assert (n, total) == (10, 100) and body.read(4) + body.read() == bytes(range(10, 20))
    body.close()
    body, n, total = blobstore.open_range("audio/a.m4a", 90)               # 开区间读到尾
    assert (n, total) == (10, 100) and body.read() == bytes(range(90, 100))
    body.close()
    with pytest.raises(blobstore.NoSuchKey):
        blobstore.open_range("audio/none.m4a", 0, 1)


@pytest.mark.parametrize("key", ["../etc/passwd", "/abs/path", "a/../../b", "a\\b", ""])
def test_rejects_keys_escaping_root(key):
    with pytest.raises(ValueError):
        blobstore.put_bytes(key, b"-")
    assert blobstore.exists(key) is False


def test_newest_key_age_hours():
    assert blobstore.newest_key_age_hours("backup/") is None
    blobstore.put_bytes("backup/x", b"-")
    assert 0 <= blobstore.newest_key_age_hours("backup/") < 0.01

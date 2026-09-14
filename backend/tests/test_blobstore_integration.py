import pytest

from app import blobstore


@pytest.mark.infra
def test_put_then_get_roundtrip():
    blobstore.ensure_bucket()
    blobstore.put_bytes("test/hello.txt", b"nihao", "text/plain")
    assert blobstore.get_bytes("test/hello.txt") == b"nihao"


def test_取件客户端进程内共用一份():
    """省的不是构造开销（2ms），是**TLS 握手**——每新建一个 client 就要重新跟 R2 握一次手。

    实测同一批三个对象：每次新建 441ms，共用一个 396ms，后两个各省 40%。
    全站唯一「一个请求取多个对象」的是后处理改动清单接口。
    ⚠️ 这条不需要连真存储，纯对象身份检查，所以不带 infra 标记。
    """
    from app import blobstore

    blobstore._reset_client()
    a = blobstore._client()
    b = blobstore._client()
    assert a is b, "每次都在新建客户端 → 每次都要重握一次手"
    blobstore._reset_client()
    assert blobstore._client() is not a, "_reset_client 没生效，测试就换不了 endpoint"

"""T7.2b 全局 Claude 闸（DB 级）integration 测试。需 postgres（@pytest.mark.infra）。"""
import pytest

from app import claude_gate, db


@pytest.fixture(autouse=True)
def _clean():
    db.init_schema()
    with db.connect() as conn:
        conn.execute("DELETE FROM claude_slots")
    yield


@pytest.mark.infra
def test_acquire_until_limit_then_full():
    for i in range(3):
        assert claude_gate.acquire_slot(f"job{i}", limit=3) is True
    assert claude_gate.acquire_slot("job3", limit=3) is False   # 满，拿不到
    assert claude_gate.active_count() == 3


@pytest.mark.infra
def test_release_frees_a_slot():
    assert claude_gate.acquire_slot("a", limit=1) is True
    assert claude_gate.acquire_slot("b", limit=1) is False      # 满
    claude_gate.release_slot("a")
    assert claude_gate.acquire_slot("b", limit=1) is True       # 释放后可拿


@pytest.mark.infra
def test_same_slot_not_double_counted():
    assert claude_gate.acquire_slot("x", limit=2) is True
    # 同 slot 重复 acquire：ON CONFLICT DO NOTHING → 不新增、不返回行
    assert claude_gate.acquire_slot("x", limit=2) is False
    assert claude_gate.active_count() == 1


@pytest.mark.infra
def test_db_gate_class_acquire_release():
    g = claude_gate.DbClaudeGate("m1")
    assert g.acquire() is True
    assert claude_gate.active_count() == 1
    g.release()
    assert claude_gate.active_count() == 0


@pytest.mark.infra
def test_两档闸互不占用名额():
    """三档阶梯的前提：claude 与 pro 是**两个独立计数**，同一个 job 可以先失手 Claude 再拿 Pro。

    做在 DB 级而不是单测：这条靠的是 (engine, slot_id) 复合主键 + 按 engine 分组计数，
    两者都只有真库才验得出——用假闸怎么写都是绿的。
    """
    from app import claude_gate as g
    job = "t-ladder-1"
    for e in ("claude", "pro"):
        g.release_slot(job, engine=e)
    try:
        # 同一 slot_id 两档各占一个，互不冲突（复合主键）
        assert g.acquire_slot(job, limit=1, engine="claude") is True
        assert g.acquire_slot(job, limit=1, engine="pro") is True
        # 计数分开：各自都只有 1，且各自的 limit 独立生效
        assert g.active_count("claude") >= 1 and g.active_count("pro") >= 1
        assert g.acquire_slot("t-ladder-2", limit=1, engine="pro") is False   # pro 已满
        assert g.acquire_slot("t-ladder-2", limit=2, engine="claude") is True  # claude 还有位
        # 释放 pro 不该动到 claude
        g.release_slot(job, engine="pro")
        assert g.acquire_slot("t-ladder-2", limit=1, engine="pro") is True
    finally:
        for j in (job, "t-ladder-2"):
            for e in ("claude", "pro"):
                g.release_slot(j, engine=e)

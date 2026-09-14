"""多术语库 DB 真逻辑集成测（infra 标记，需本地 Postgres）。
跑法：DATABASE_URL=...localhost... python3 -m pytest -m infra tests/test_glossary_db_integration.py
受 server/conftest.py 守卫：DATABASE_URL host 非本地则硬退出。"""
import pytest

from app import glossary, db

_EMAIL = "glossary_db_test@example.com"


@pytest.fixture(autouse=True)
def _clean():
    db.init_schema()
    with db.connect() as c:
        c.execute("DELETE FROM glossaries WHERE email IN (%s, %s)", (_EMAIL, "other@x.com"))
    yield
    with db.connect() as c:
        c.execute("DELETE FROM glossaries WHERE email IN (%s, %s)", (_EMAIL, "other@x.com"))


@pytest.mark.infra
def test_create_and_list():
    g = glossary.create_glossary(_EMAIL, "项目A", None, "FD ｜ 履约")
    assert isinstance(g, dict) and g["name"] == "项目A"
    rows = glossary.list_glossaries(_EMAIL)
    assert len(rows) == 1 and rows[0]["content"] == "FD ｜ 履约"


@pytest.mark.infra
def test_create_rejects_duplicate_name_case_insensitive():
    glossary.create_glossary(_EMAIL, "项目A", None, "")
    err = glossary.create_glossary(_EMAIL, "  项目A  ", None, "")  # trim 后同名
    assert isinstance(err, str) and "exists" in err
    glossary.create_glossary(_EMAIL, "Project", None, "")
    err2 = glossary.create_glossary(_EMAIL, "PROJECT", None, "")  # 忽略大小写撞名
    assert isinstance(err2, str) and "exists" in err2


@pytest.mark.infra
def test_create_rejects_over_cap():
    for i in range(glossary.MAX_GLOSSARIES):
        out = glossary.create_glossary(_EMAIL, f"lib{i}", None, "")
        assert isinstance(out, dict)
    err = glossary.create_glossary(_EMAIL, "one_more", None, "")
    assert isinstance(err, str) and "too many" in err


@pytest.mark.infra
def test_update_content_and_rename():
    g = glossary.create_glossary(_EMAIL, "A", None, "x")
    out = glossary.update_glossary(_EMAIL, g["id"], "B", "zh", "y ｜ z")
    assert isinstance(out, dict)
    assert out["name"] == "B" and out["content"] == "y ｜ z" and out["language"] == "zh"


@pytest.mark.infra
def test_update_rejects_rename_collision():
    glossary.create_glossary(_EMAIL, "A", None, "")
    g2 = glossary.create_glossary(_EMAIL, "B", None, "")
    err = glossary.update_glossary(_EMAIL, g2["id"], "a", None, "")  # 撞已有 A
    assert isinstance(err, str) and "exists" in err


@pytest.mark.infra
def test_update_allows_same_name_self():
    g = glossary.create_glossary(_EMAIL, "A", None, "x")
    out = glossary.update_glossary(_EMAIL, g["id"], "A", None, "x2")  # 名不变只改内容
    assert isinstance(out, dict) and out["content"] == "x2"


@pytest.mark.infra
def test_update_foreign_returns_none():
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    assert glossary.update_glossary("other@x.com", g["id"], "A2", None, "") is None


@pytest.mark.infra
def test_delete_and_foreign_delete():
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    assert glossary.delete_glossary("other@x.com", g["id"]) is False  # 非本人删不掉
    assert glossary.delete_glossary(_EMAIL, g["id"]) is True
    assert glossary.list_glossaries(_EMAIL) == []


@pytest.mark.infra
def test_get_content_for_worker():
    g = glossary.create_glossary(_EMAIL, "A", None, "FD ｜ 履约")
    assert glossary.get_glossary_content(g["id"]) == "FD ｜ 履约"
    assert glossary.get_glossary_content(None) == ""
    assert glossary.get_glossary_content("00000000-0000-0000-0000-000000000000") == ""


# ── 缺口（待办层）────────────────────────────────────────────────────────────
# 缺口是「这一轮对这本库的判断」，不是永久待办清单。下面四条钉住这个语义。

def _texts(gid, status=None):
    return [g["text"] for g in glossary.list_gaps(gid)
            if status is None or g["status"] == status]


@pytest.mark.infra
def test_gaps_add_dedupes_and_never_revives_handled():
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    glossary.add_gaps(g["id"], ["缺内部系统名", "缺岗位叫法"])
    gid = g["id"]
    handled = glossary.list_gaps(gid)[0]["id"]
    glossary.set_gap_status(gid, handled, "handled")
    # 同样两条再来一轮：不重复插入，已处理的也不因为重跑就复活
    glossary.add_gaps(gid, ["缺内部系统名", "缺岗位叫法"])
    assert len(glossary.list_gaps(gid)) == 2
    assert _texts(gid, "handled") == ["缺内部系统名"]


@pytest.mark.infra
def test_gaps_retire_when_new_round_no_longer_mentions_them():
    """库改过之后重跑，上一轮的旧判断要让位给这一轮——但只是收进「已处理」，能恢复。"""
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    gid = g["id"]
    glossary.add_gaps(gid, ["缺内部系统名", "缺岗位叫法"])
    glossary.add_gaps(gid, ["缺内部系统名", "缺计价口径"])   # 第二轮不再提「岗位叫法」
    assert sorted(_texts(gid, "open")) == sorted(["缺内部系统名", "缺计价口径"])
    assert _texts(gid, "handled") == ["缺岗位叫法"]          # 退役≠删除，仍在库里可恢复


@pytest.mark.infra
def test_gaps_empty_round_leaves_everything_alone():
    """一轮 0 缺口更可能是这次没答好，不能拿它把用户攒着的待办清空。"""
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    gid = g["id"]
    glossary.add_gaps(gid, ["缺内部系统名"])
    glossary.add_gaps(gid, [])
    glossary.add_gaps(gid, ["  ", ""])       # 全是空白，等同于没产出
    assert _texts(gid, "open") == ["缺内部系统名"]


@pytest.mark.infra
def test_gaps_whole_batch_replaced_when_nothing_carries_over():
    """整批换新：旧的全退役，新的必须全进来。

    余量算错（把退役数从余量里再减一次）时，这里会退成 0 条 open ——
    界面上就是「旧的清空了、新的没出现」，看着像缺口功能坏了。
    """
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    gid = g["id"]
    old = [f"上一轮第{i}类" for i in range(5)]
    new = [f"这一轮第{i}类" for i in range(5)]
    glossary.add_gaps(gid, old)
    glossary.add_gaps(gid, new)
    assert sorted(_texts(gid, "open")) == sorted(new)
    assert sorted(_texts(gid, "handled")) == sorted(old)


@pytest.mark.infra
def test_gaps_cap_counts_only_open():
    g = glossary.create_glossary(_EMAIL, "A", None, "")
    gid = g["id"]
    glossary.add_gaps(gid, [f"缺第{i}类" for i in range(glossary.GAP_MAX_COUNT + 4)])
    assert len(_texts(gid, "open")) == glossary.GAP_MAX_COUNT

"""术语库纯校验单测（无 DB）：内容校验 validate + 库名校验 validate_name。
DB 真逻辑（CRUD/上限/重名）见 tests/integration/test_glossary_db.py（infra 标记）。"""
from app import config, glossary


# ── 内容校验（沿用旧口径）──
def test_validate_accepts_normal_glossary():
    assert glossary.validate("## 机构\nFD ｜ 履约分销\nGMV ｜ 成交总额") is None


def test_validate_rejects_over_total_chars():
    assert glossary.validate("x" * (config.GLOSSARY_MAX_CHARS + 1)) is not None


def test_validate_total_excludes_newlines():
    # 换行不计入总字数（与前端 totalChars 口径一致）：8000 字符 + 多个换行仍合法
    assert glossary.validate("x" * config.GLOSSARY_MAX_CHARS + "\n" * 50) is None


def test_validate_rejects_long_meaning():
    assert glossary.validate("词 ｜ " + "x" * (config.GLOSSARY_MAX_MEANING + 1)) is not None


def test_validate_meaning_exactly_max_is_ok():
    assert glossary.validate("词 ｜ " + "x" * config.GLOSSARY_MAX_MEANING) is None


def test_validate_meaning_trimmed_before_counting():
    # 两端空格 trim 后才算长度（与前端 mlen 口径一致）
    assert glossary.validate("词 ｜   " + "x" * config.GLOSSARY_MAX_MEANING + "   ") is None


# ── 库名校验 ──
def test_name_rejects_empty():
    assert glossary.validate_name("   ") is not None


def test_name_rejects_over_40():
    assert glossary.validate_name("x" * 41) is not None


def test_name_accepts_exactly_40():
    assert glossary.validate_name("x" * 40) is None


def test_name_trimmed_before_length_check():
    # 首尾空格不计：40 实字 + 两端空格 → 合法
    assert glossary.validate_name("   " + "x" * 40 + "   ") is None


def test_name_rejects_newline():
    assert glossary.validate_name("a\nb") is not None


def test_name_rejects_control_char():
    assert glossary.validate_name("a\tb") is not None


def test_name_accepts_mixed_chars():
    assert glossary.validate_name("项目 A / 2026-客户 访谈") is None


# ── owns()：术语库归属校验（E4），纯 mock db.connect，不碰真库 ──
class _FakeConn:
    """模拟 psycopg 连接上下文管理器：execute 返回自身以便链式 .fetchone()。"""
    def __init__(self, row=None, boom=False):
        self._row = row
        self._boom = boom

    def execute(self, *a, **k):
        if self._boom:
            raise Exception("invalid input syntax for type uuid")
        return self

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_owns_true_when_row_found(monkeypatch):
    monkeypatch.setattr(glossary.db, "connect", lambda: _FakeConn(row=(1,)))
    assert glossary.owns("a@b.com", "11111111-1111-1111-1111-111111111111") is True


def test_owns_false_when_no_row(monkeypatch):
    monkeypatch.setattr(glossary.db, "connect", lambda: _FakeConn(row=None))
    assert glossary.owns("a@b.com", "11111111-1111-1111-1111-111111111111") is False


def test_owns_false_on_invalid_uuid_instead_of_raising(monkeypatch):
    # 传非 UUID 字符串在 psycopg 里会抛异常（列类型是 UUID）——owns 必须吞掉、归 False，
    # 不能冒泡成 500（调用方 create_job 已建 R2 音频对象，异常会致孤儿）
    monkeypatch.setattr(glossary.db, "connect", lambda: _FakeConn(boom=True))
    assert glossary.owns("a@b.com", "not-a-uuid") is False

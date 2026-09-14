"""连着远程库时，整表 DELETE/UPDATE/TRUNCATE 必须被拦下。

守的是 2026-08-14 事故：一个临时脚本的 `DELETE FROM jobs` 打在了生产上，因为 shell 里
export 着生产连接串、而脚本用 setdefault 设本地（对已存在的值不生效）。
此前的对策只保护 pytest，裸跑 .py 绕得过去——所以判据换成了「远程 + 没有 WHERE」。

这里只测判定函数本身：它不连库、不需要 Postgres，所以属于普通单测而不是 infra 测试。
"""
import pytest

from app import config, db


@pytest.fixture()
def remote(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL",
                        "postgresql://u:p@aws-1-us-east-1.pooler.supabase.com:5432/postgres")
    monkeypatch.delenv("TX_ALLOW_MASS_WRITE", raising=False)


@pytest.fixture()
def local(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL",
                        "postgresql://postgres:postgres@localhost:5432/transcribe")
    monkeypatch.delenv("TX_ALLOW_MASS_WRITE", raising=False)


@pytest.mark.parametrize("sql", [
    "DELETE FROM jobs",
    "delete from postprocess_jobs",
    "  DELETE FROM node_events  ",
    "TRUNCATE jobs",
    "UPDATE jobs SET status='done'",
])
def test_mass_write_blocked_on_remote(remote, sql):
    with pytest.raises(RuntimeError, match="拒绝执行"):
        db._check(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM jobs WHERE id = %s",
    "UPDATE jobs SET status='done' WHERE id = %s",
    "SELECT * FROM jobs",
    "INSERT INTO jobs (id) VALUES (%s)",
    "CREATE TABLE IF NOT EXISTS x (id int)",
])
def test_normal_statements_pass_on_remote(remote, sql):
    """生产代码里没有任何一条不带 WHERE 的 DELETE/UPDATE（写守卫时逐条查过），
    所以这道闸不该拦住任何正常路径——拦错的守卫比没有守卫更糟。"""
    db._check(sql)


def test_local_is_unrestricted(local):
    """本地库随便清——infra 测试的 fixture 就是这么干的，不能把它们一起拦死。"""
    db._check("DELETE FROM jobs")


def test_explicit_unlock(remote, monkeypatch):
    """真要在远程做无条件清理，得专门去设这个变量——让它是个动作，不是默认能力。"""
    monkeypatch.setenv("TX_ALLOW_MASS_WRITE", "1")
    db._check("DELETE FROM jobs")


def test_message_names_the_host_and_statement(remote):
    """报错要说清楚打的是哪个库、哪一句——不然人只会重跑一遍。"""
    with pytest.raises(RuntimeError) as e:
        db._check("DELETE FROM jobs")
    assert "supabase.com" in str(e.value)
    assert "DELETE FROM jobs" in str(e.value)


def test_non_string_query_does_not_break(remote):
    """psycopg 也接受 bytes / Composed；取不到文本时不拦——守卫不该把正常路径搞挂。"""
    db._check(b"SELECT 1")
    db._check(object())

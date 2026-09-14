"""schema.sql 必须建出「注销代码还在点名的每一张表」。

2026-08-31 验收查出的坑：2026-08-17 下架「归类」时，把 `postprocess_profiles` 的
`CREATE TABLE` 从 schema.sql 里删了，只留一句「故意不 DROP」的注释——**一边不建，
一边 `account_delete._PURGE` 还要去删它**。

生产库里那张表早就在，所以一直没出事，症状全落在看不见的地方：
  · 任何**从本文件建起来的新库**（灾备恢复 / 新环境 / 本地测试库）一注销账号就 UndefinedTable；
  · `test_account_delete_infra.py` 的 20 条测试因此在本地永远跑不起来
    ——**整条注销链路本地零覆盖**，而这恰恰是一条不能坏的路。

⚠️ 判据是「**有没有代码还在点它的名**」，不是「这张表还有没有人读」：
`postprocess_profiles` 的业务代码确实早就不读它了，正是这一点让当初那次删除看着无害。
只要还有一句 SQL 会提到这张表，schema 就得建得出来。

⚠️ 本条**不连数据库**（纯文本比对），所以不带 infra 标记——它要能在任何机器上跑，
包括那 20 条连库测试跑不起来的机器。连库的那些才是真正验注销行为的，本条只保证它们跑得起来。
"""

import re
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"


def _created_tables() -> set[str]:
    """schema.sql 里真正会建出来的表名。

    ⚠️ 先去掉注释行：被注释掉的 CREATE 一张表也建不出来，却会让朴素的字符串搜索
    判成「建了」——本次那段删除留下的正是一大段注释。
    """
    lines = (_APP / "schema.sql").read_text(encoding="utf8").splitlines()
    sql = "\n".join(l for l in lines if not l.lstrip().startswith("--"))
    return set(re.findall(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", sql, re.I))


def test_注销要动的每一张表_schema_都建得出来():
    from app import account_delete

    named = [t for t, _ in account_delete._PURGE] + [t for t, _ in account_delete._ANONYMIZE]
    assert named, "注销的表清单读空了——正则或字段名改过？"
    missing = sorted(set(named) - _created_tables())
    assert not missing, (
        f"注销代码会点这些表的名，但 schema.sql 建不出来：{missing}。"
        " 新库上注销账号会 UndefinedTable，且 test_account_delete_infra 整个跑不起来。"
    )

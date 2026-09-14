"""守卫：**用户拿到的任何东西都不许透露这一单走了降级路**（2026-08-14 Duner 定）。

降级是我们的内部处置，用户只该拿到一份稿。让他知道「你这份是备用引擎出的」，
只会凭空制造不信任，而他既没有选择权、也无从判断影响。留痕只走
`postprocess_jobs.degraded_steps` → 运营驾驶舱（AdminPage）。

用户能拿到的东西有两样，都要扫：
  ① 产物正文（narrate.md / redact.md …）—— 由模型生成，靠提示词约束，扫不了
  ② **QC 报告 / 问题清单** —— 由我们的代码拼装，可以扫，本文件扫的就是它

顺带扫一遍面向用户的错误话术，别从那儿漏出去。
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "pipeline"

# 出现在**用户可见字符串**里就算泄露。大小写不敏感。
BANNED = ["降级", "兜底", "备用引擎", "deepseek", "ds-v4", "flash", "claude", "opus"]

# 报告拼装函数所在的文件 → 里面负责产出用户可见文本的函数名
REPORT_FUNCS = [("pp_redact_ds.py", "_qc_md"), ("pp_deepseek.py", "_issues_md")]


def _func_source(path: Path, name: str) -> str:
    """抠出某个函数的源码（到下一个顶格 def/class 为止）。"""
    src = path.read_text(encoding="utf-8")
    i = src.index(f"def {name}(")
    m = re.search(r"\n(?=(?:def |class )\w)", src[i:])
    return src[i:i + m.start()] if m else src[i:]


def _string_literals(code: str) -> list[str]:
    """只取字符串字面量——注释里写「降级」是给我们看的，不进产物。"""
    lits = re.findall(r'"""(.*?)"""', code, re.S)
    lits += re.findall(r'(?<!")"([^"\n]*)"', code)
    lits += re.findall(r"(?<!')'([^'\n]*)'", code)
    return lits


@pytest.mark.parametrize("fname,func", REPORT_FUNCS)
def test_报告拼装里不许出现降级或引擎名(fname, func):
    code = _func_source(SRC / fname, func)
    # docstring 是给维护者看的、不进产物，单独摘掉
    body = code[code.index('"""', code.index('"""') + 3) + 3:] if code.count('"""') >= 2 else code
    hits = [(w, s.strip()) for s in _string_literals(body)
            for w in BANNED if w in s.lower()]
    assert not hits, (
        f"{fname}::{func} 的用户可见文本里出现了降级/引擎字样：{hits}\n"
        "用户不该知道自己这一单被降了级。留痕走 degraded_steps → 运营驾驶舱。")


def test_八门报告文案里也不许出现降级或引擎名():
    """⚠️ 2026-08-30 报告文案搬进了 `pp_lang._R`（要出 8 门），上面那个按函数源码扫的用例
    **就扫不到它们了**——它不会红，只是悄悄变弱。所以这里把整张表也扫一遍。
    加语言、改文案都会经过这里。"""
    from pipeline import pp_lang
    hits = []
    for lang, table in pp_lang._R.items():
        for k, v in table.items():
            for text in (v if isinstance(v, list) else [v]):
                hits += [(lang, k, w) for w in BANNED if w in text.lower()]
    assert not hits, f"报告文案表里出现了降级/引擎字样：{hits}"


def test_每一门的报告标题都还在():
    """标题必须是「就事论事的报告名」，不带任何引擎线索。

    ⚠️ 这里**不能断言与 Opus 版逐字相同**（我初版是这么写的，错了）：Opus 路的标题由模型
    每次自拟，实测两次跑出 `# 脱敏质检报告（pp-redact）` 和 `# 脱敏 QC 报告` 两种。
    既然基准自己就在变，「与基准一致」根本不是可断言的性质。
    真正的要求只有一条——**标题里没有引擎名**，那由上面两个用例保证；这里只钉住
    每一门都真的有一个标题，别翻着翻着漏了一门变成空标题。"""
    from pipeline import pp_lang
    for lang in pp_lang._R:
        t = pp_lang.report(lang)
        assert t["issues_title"].strip(), f"{lang} 的问题清单没有标题"
        assert t["redact_title"].strip(), f"{lang} 的脱敏报告没有标题"


def test_面向用户的失败话术不提引擎():
    """`error_public` 是用户能看到的失败说明，别从这儿漏。

    ⚠️ **只扫 error_public，不扫 error**：`set_failed(job_id, step, error, public=...)` 的
    第三个位置参数是**内部** traceback/诊断（只进库、只给运营看），里面写「Claude 硬错」
    正是它该干的事。初版守卫连它一起扫，把一条合规的诊断信息报成了泄露——
    扫错字段的守卫比没有守卫更糟，它会逼着人去改本来正确的代码。
    """
    pp_py = (SRC.parent / "app" / "postprocess.py").read_text(encoding="utf-8")
    default = re.search(r'ERROR_PUBLIC = "([^"]+)"', pp_py).group(1)
    publics = [default]
    for f in list(SRC.glob("pp*.py")) + [SRC.parent / "app" / "postprocess.py"]:
        publics += re.findall(r'public\s*=\s*"([^"]+)"', f.read_text(encoding="utf-8"))
    for s in publics:
        assert not any(w in s.lower() for w in BANNED), s

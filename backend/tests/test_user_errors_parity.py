"""后端的错误码与前端的文案表必须一一对上。

2026-08-30 起后端只给码、文案在前端（`src/lib/userErrors.ts`）。两边分家的症状分两种，
都不报错：
  · 后端加了码、前端没加 → 那一条永远显示成后端的**中文兜底**（德语用户看到中文）；
  · 前端有、后端没有 → 一条永远走不到的死文案，还会被 i18n 守卫要求八门翻满。

做法照抄 `test_pricing_parity.py`：**后端读前端那个 .ts 文件**逐条比对。
⚠️ 跳过的条件是「整棵前端树都不在」（用 package.json 判），**不是「那个文件不在」**——
   按文件判的话，谁把文件删了/改了名，这条守卫就自己跳过了。
   任务镜像里没有前端树，那时它是构建期守卫、跳过是对的。
"""
import re
from pathlib import Path

import pytest

from app import user_errors

_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = _ROOT / "src" / "lib" / "userErrors.ts"

_skip_in_image = pytest.mark.skipif(
    not (_ROOT / "package.json").exists(),
    reason="任务镜像里没有前端树；这是构建期守卫")


def _frontend_codes() -> set[str]:
    src = FRONTEND.read_text(encoding="utf-8")
    block = re.search(r"USER_ERROR_CODES\s*=\s*\[(.*?)\]\s*as const", src, re.S)
    assert block, "没找到 USER_ERROR_CODES —— 正则该更新了"
    return set(re.findall(r'"([a-z_]+)"', block.group(1)))


@_skip_in_image
def test_两边的错误码完全一致():
    back, front = set(user_errors.MESSAGES), _frontend_codes()
    assert back == front, (
        f"后端有前端没有：{sorted(back - front)}\n"
        f"前端有后端没有：{sorted(front - back)}\n"
        "两边一起加：server/app/user_errors.py 的 MESSAGES + src/lib/userErrors.ts 的 "
        "USER_ERROR_CODES 与 TEXT。")


@_skip_in_image
def test_前端给每一个码都写了文案():
    """光登记在 USER_ERROR_CODES 里不算数——TEXT 里没有就会回落成后端的中文。
    带参数的那两条不走 TEXT（走 L.t），单独放行。"""
    src = FRONTEND.read_text(encoding="utf-8")
    with_params = {"upload_too_large", "outline_too_large"}
    missing = [c for c in user_errors.MESSAGES
               if c not in with_params and not re.search(rf"\n    {c}:", src)]
    assert not missing, f"这些码在 TEXT 里没有文案，会显示成中文：{missing}"
    for c in with_params:
        assert f'code === "{c}"' in src, f"{c} 带数字参数，得走 L.t 单独处理"


def test_带参数的码在后端也真的收得下参数():
    """`{n}` 只在 MESSAGES 里写了没用，等于文案里留一个花括号给用户看。"""
    for code, text in user_errors.MESSAGES.items():
        if "{n}" in text:
            assert user_errors.UserError(413, code, n=7).detail["params"] == {"n": 7}


def test_未登记的码直接炸而不是悄悄发出去():
    """写错码名的话，用户会看到一个原样的标识符。宁可在开发期炸。"""
    with pytest.raises(AssertionError):
        user_errors.UserError(422, "not_a_real_code")


@_skip_in_image
def test_没有中文散文从后端漏给用户():
    """这条守的是「改造有没有做完」：`api.py` 里面向用户那几条路由不该再出现
    `HTTPException(..., "中文…")`。管理页与参数形状校验不在此列，所以只扫**码表里
    那几句中文**——它们要是还留在 api.py 里，就说明有一处没改到。"""
    api = (Path(__file__).resolve().parent.parent / "app" / "api.py").read_text(encoding="utf-8")
    leaked = [m for m in user_errors.MESSAGES.values()
              if "{" not in m and f'"{m}"' in api]
    assert not leaked, f"这几句还在 api.py 里直接抛给用户：{leaked}"

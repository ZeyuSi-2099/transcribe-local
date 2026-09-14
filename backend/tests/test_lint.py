"""静态守卫：未定义的名字（NameError 那一类）在导入时不报错、只在那条路真跑到时炸。

2026-09-03 生产实见：`api.retry_job` 用了 `db.connect()` 却没 import `db`，08-15 上线起 18 天
「重试」按钮对所有用户都是 500——909 条测试没有一条跑到那个函数，而 ruff 一秒就能指出来。
只选 F821/F811/F823（未定义 / 重复定义 / 赋值前引用），不选风格类规则——守卫只守「会炸」的。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_no_undefined_names_in_app_and_pipeline():
    if shutil.which("ruff") is None and subprocess.run(
            [sys.executable, "-m", "ruff", "--version"], capture_output=True).returncode != 0:
        pytest.skip("ruff 未安装（requirements.txt 里有，装上再跑）")
    r = subprocess.run([sys.executable, "-m", "ruff", "check", "app", "pipeline",
                        "--select", "F821,F811,F823", "--exclude", "pipeline/vendor"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

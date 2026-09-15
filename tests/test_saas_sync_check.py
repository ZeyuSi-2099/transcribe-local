"""saas_sync_check：线上改了什么、本地跟没跟上。用两个临时 git 仓模拟线上与本地，内容一律编的。"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import saas_sync_check as C  # noqa: E402

RULES = [
    {"glob": ["docs/*"], "as": "skip", "why": "线上自己的笔记"},
    {"glob": ["server/app/menu.py"], "as": "modify", "why": "本地菜单换了一道菜"},
    {"glob": ["server/app/*", "src/*"], "as": "same", "why": "原样"},
]
SAAS_FILES = {"server/app/soup.py": "熬汤\n", "server/app/menu.py": "菜单\n",
              "server/app/old.py": "旧灶台\n", "src/Oven.tsx": "烤箱\n", "docs/notes.md": "笔记\n"}
LOCAL_FILES = {"backend/app/soup.py": "熬汤\n", "backend/app/menu.py": "本地菜单\n",
               "backend/app/old.py": "旧灶台\n", "frontend/src/Oven.tsx": "烤箱\n"}


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, files: dict):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def repos(tmp_path):
    saas, local = tmp_path / "saas", tmp_path / "local"
    write(saas, SAAS_FILES)
    write(local, LOCAL_FILES)
    git(saas, "init", "-q")
    git(saas, "add", "-A")
    git(saas, "commit", "-qm", "base")
    return saas, local, git(saas, "rev-parse", "HEAD")


def kinds(rows):
    return {r.saas: r.kind for r in rows}


def test_in_sync_modify_is_registered_and_nothing_alerts(repos):
    saas, local, base = repos
    rows = C.check(saas, RULES, base, root=local)
    assert kinds(rows) == {"server/app/soup.py": "一致", "server/app/menu.py": "已登记",
                           "server/app/old.py": "一致", "src/Oven.tsx": "一致"}
    assert not [r for r in rows if r.kind in C.ALERTS]


def test_saas_edits_same_file_reports_new_diff(repos):
    saas, local, base = repos
    (saas / "server/app/soup.py").write_text("熬汤，多放姜\n", encoding="utf-8")   # 没提交也算
    row = next(r for r in C.check(saas, RULES, base, root=local) if r.saas == "server/app/soup.py")
    assert row.kind == "未登记差异" and "saas_pull" in row.note


def test_local_edits_same_file_reports_new_diff_blaming_local(repos):
    saas, local, base = repos
    (local / "frontend/src/Oven.tsx").write_text("烤箱，调高十度\n", encoding="utf-8")
    row = next(r for r in C.check(saas, RULES, base, root=local) if r.saas == "src/Oven.tsx")
    assert row.kind == "未登记差异" and "本地改过" in row.note


def test_saas_edits_modify_file_is_registered_but_needs_merge(repos):
    saas, local, base = repos
    (saas / "server/app/menu.py").write_text("菜单，加一道甜点\n", encoding="utf-8")
    git(saas, "commit", "-qam", "改菜单")
    row = next(r for r in C.check(saas, RULES, base, root=local) if r.saas == "server/app/menu.py")
    assert row.kind == "要合并" and row.note == "本地菜单换了一道菜"


def test_new_unmatched_and_deleted_files(repos):
    saas, local, base = repos
    write(saas, {"server/tools/grill.py": "烤架\n", "server/app/pan.py": "平底锅\n"})
    git(saas, "add", "-A")
    git(saas, "rm", "-q", "server/app/old.py")
    git(saas, "commit", "-qm", "新旧")
    k = kinds(C.check(saas, RULES, base, root=local))
    assert k["server/tools/grill.py"] == "未登记文件"
    assert k["server/app/pan.py"] == "缺文件"
    assert k["server/app/old.py"] == "线上删了"
    assert "docs/notes.md" not in k                     # skip 的不比


def test_base_line_round_trip_keeps_rest_of_manifest(tmp_path):
    m = tmp_path / "saas.yaml"
    m.write_text("# 说明\nbase: aaaa\n\nrules:\n  - glob: [\"x\"]\n    as: skip\n", encoding="utf-8")
    assert C.read_base(m) == "aaaa"
    C.write_base("bbbb", m)
    assert C.read_base(m) == "bbbb"
    assert m.read_text(encoding="utf-8").endswith("rules:\n  - glob: [\"x\"]\n    as: skip\n")

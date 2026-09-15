"""本机版：构建好的界面挂在接口同一个端口上。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import local_web


def _client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不该被读到", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": []}

    local_web.mount(app, dist)
    return TestClient(app)


def test_api_routes_win_and_unknown_api_is_404(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/jobs").json() == {"jobs": []}
    assert c.get("/api/nope").status_code == 404


def test_static_files_and_app_routes_fall_back_to_index(tmp_path):
    c = _client(tmp_path)
    assert c.get("/assets/index-abc.js").text == "console.log(1)"
    assert c.get("/favicon.svg").text == "<svg/>"
    for path in ("/", "/history", "/zh/history"):
        r = c.get(path)
        assert r.text == "<div id=root></div>" and r.headers["cache-control"] == "no-cache"


def test_cannot_escape_dist(tmp_path):
    c = _client(tmp_path)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "不该被读到" not in c.get(path).text

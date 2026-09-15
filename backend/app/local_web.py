"""本机版独有：把构建好的界面（frontend/dist）挂在接口的同一个端口上。

线上界面放在 Vercel、接口在 Render，本机只有一个进程：`transcribe-local serve` 起接口时顺手把界面挂上，
用户不用另起 Node。开发时仍然用 vite（frontend/ 下 `npm run dev`，/api 代理到接口）。

⚠️ 要在 app.api 的接口全部注册完之后再挂：兜底路由按注册顺序排在最后，接口先匹配。
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def mount(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def _web(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "没有这个接口")
        f = (dist / path).resolve()
        if path and f.is_file() and dist in f.parents:           # 只认 dist 里面的文件，挡 ../ 越界
            return FileResponse(f)
        # 界面路由（/history、/zh/… 这类）都回 index.html，由界面自己认路径。
        # index.html 不许缓存：重新构建后文件名带新哈希，浏览器得拿到新的 index 才找得到。
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

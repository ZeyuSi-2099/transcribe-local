"""打安装包时，把构建好的界面（frontend/dist）一起放进去。

界面要用 Node 构建，构建产物不进 git（Duner 2026-09-15 定 g8：发布时由 CI 构建，见 .github/workflows/release.yml）。
所以这里按「有就带上」处理：本机没构建也照样能打包、能 `pip install -e .` 开发，只是包里没有界面。
发版时 CI 设 TRANSCRIBE_REQUIRE_UI=1，界面不在就直接失败 —— 发出去一个打不开界面的包，比打包失败糟得多。
"""
import os
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":          # 从源码跑时 serve 直接读仓库里的 frontend/dist，不用往包里放
            return
        dist = Path(self.root) / "frontend" / "dist"
        if (dist / "index.html").is_file():
            build_data["force_include"][str(dist)] = "transcribe_local/frontend/dist"
        elif os.environ.get("TRANSCRIBE_REQUIRE_UI"):
            raise RuntimeError(f"界面还没构建（{dist}）：先 cd frontend && npm ci && npm run build")

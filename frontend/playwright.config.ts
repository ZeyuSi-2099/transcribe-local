// 界面走查（端到端）：真起一次本机服务（后端 + 构建好的界面），只把识别换成假的。
// 怎么跑、哪里是假的，见 src/test/e2e/README.md。
import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const PORT = Number(process.env.E2E_PORT ?? 8790);
// 数据放临时目录，不碰 ~/.transcribe-local。写回环境变量：配置文件在每个测试进程里还会再读一遍，得是同一个目录
const DATA = (process.env.E2E_DATA ??= mkdtempSync(join(tmpdir(), "transcribe-e2e-")));

export default defineConfig({
  testDir: "src/test/e2e",
  workers: 1,               // 共用一个本机服务，后台一次一单
  retries: 0,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, locale: "zh-CN" },
  webServer: {
    command: `${process.env.E2E_PYTHON ?? "python3"} -m transcribe_local serve --no-open --port ${PORT}`,
    cwd: DATA,              // 不在仓库根起：serve 会先认当前目录的 config.yaml
    env: {
      PYTHONPATH: [join(ROOT, "src"), join(ROOT, "backend", "tests")].join(":"),
      TRANSCRIBE_DATA: DATA,
      TRANSCRIBE_CONFIG: join(DATA, "config.yaml"),
      TRANSCRIBE_ORCHESTRATOR: "e2e_orchestrator",
    },
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});

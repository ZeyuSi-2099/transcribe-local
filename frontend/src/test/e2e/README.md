# 前端走查（桩 API，不依赖后端、不花钱）
1. `npm run dev`（起前端 5173）。
2. `npx playwright test src/test/e2e/happy-path.spec.ts`。
   该测试拦截 /api，桩出整条 upload→poll→result，验证前端主链路渲染真实结构。
   首次运行需先装 Playwright：`npm i -D @playwright/test && npx playwright install chromium`。

# 真全栈联调（需后端 + 会花钱）
1. 后端：`docker compose up -d`（含 worker → 真四路 ASR）。
2. 前端：`npm run dev`（/api 已代理到 :8000）。
3. 浏览器选一段真音频，走完 → 看到真实带说话人转录 + 可播放。

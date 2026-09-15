// 界面走查：一个人真正会走的路。本机服务是真起的，识别是假的（backend/tests/e2e_orchestrator.py）。
import { test, expect, type Page } from "@playwright/test";

/** 一段静音 WAV（16 kHz 单声道 16 位）。上传时后端要用 ffprobe 量真实时长，所以得是真音频。 */
function wav(seconds: number): Buffer {
  const rate = 16000;
  const data = rate * seconds * 2;
  const b = Buffer.alloc(44 + data);
  b.write("RIFF", 0); b.writeUInt32LE(36 + data, 4); b.write("WAVE", 8);
  b.write("fmt ", 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write("data", 36); b.writeUInt32LE(data, 40);
  return b;
}

// 首次下载模型是另一页、另一件事：这里说「模型齐了」，免得走查先下 2 G 模型
async function modelsReady(page: Page) {
  await page.route("**/api/local/models", (route) => route.fulfill({
    json: { models: [], ready: true, missingMb: 0, cacheDir: "",
            download: { running: false, current: null, doneBytes: 0, totalBytes: 0, error: null } },
  }));
}

async function transcribe(page: Page, name: string, seconds: number) {
  await modelsReady(page);
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({ name, mimeType: "audio/wav", buffer: wav(seconds) });
  await page.getByRole("button", { name: /开始转录/ }).click();
}

test("选文件 → 开始转录 → 看到带说话人的稿子", async ({ page }) => {
  await transcribe(page, "走查.wav", 2);
  // 点了开始就回到「我的转录」（转录在后台跑，人可以离开）；这一行变成已完成，点开看稿
  const row = page.getByRole("button", { name: /走查\.wav.*已完成/ });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.click();
  await expect(page.getByText("欢迎来到本机转录走查。")).toBeVisible();
  await expect(page.getByText("这一句来自假的识别入口。")).toBeVisible();
  await expect(page.getByText("主持人").first()).toBeVisible();
});

test("转录失败：说清楚失败了、让人重试，不提计费", async ({ page }) => {
  await transcribe(page, "走查-失败.wav", 5);   // 假识别对长于 3 秒的录音故意失败
  await expect(page.getByText("转录失败，请重试").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/计费/)).toHaveCount(0);
});

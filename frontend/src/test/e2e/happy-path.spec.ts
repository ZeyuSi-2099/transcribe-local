import { test, expect } from "@playwright/test";

test("happy path: 选文件 → 处理 → 看到带说话人的转录", async ({ page }) => {
  let polls = 0;
  await page.route("**/api/jobs", (route) =>
    route.fulfill({ json: { jobId: "job1" } }),
  );
  await page.route("**/api/jobs/job1", (route) => {
    polls += 1;
    const done = polls >= 2;
    route.fulfill({
      json: done
        ? { status: "done", phase: "done", progress: 100, error: null }
        : { status: "running", phase: "P2", progress: 60, error: null },
    });
  });
  await page.route("**/api/jobs/job1/result", (route) =>
    route.fulfill({ json: [{ t: "00:00:01", s: "你好世界", sp: "主持人" }] }),
  );

  await page.goto("http://localhost:5173/");
  // 视登录态：若有登录页，先走桩登录或直接进入（按现有 App 行为）。
  // 选文件（隐藏 input）：
  await page.setInputFiles('input[type="file"]', {
    name: "a.m4a", mimeType: "audio/mp4", buffer: Buffer.from("AUDIO"),
  });
  await expect(page.getByText("你好世界")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText(/主持人/)).toBeVisible();
});

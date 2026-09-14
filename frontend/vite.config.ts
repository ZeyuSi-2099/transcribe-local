/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 本机版：后端默认 127.0.0.1:8765（与后端测试同一个端口），换端口设 TRANSCRIBE_API
      "/api": process.env.TRANSCRIBE_API ?? "http://127.0.0.1:8765",
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["**/node_modules/**", "**/dist/**", "src/test/e2e/**"],
  },
});

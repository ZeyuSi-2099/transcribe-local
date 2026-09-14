import "@testing-library/jest-dom";
import { beforeEach } from "vitest";

// i18n 默认语言改为"按浏览器语言判定"后，测试环境默认把浏览器语言固定为中文，
// 以保留组件测试历史上的中文默认假设；i18n.test.tsx 会按用例自行覆盖 navigator。
beforeEach(() => {
  Object.defineProperty(navigator, "languages", { value: ["zh-CN"], configurable: true });
  Object.defineProperty(navigator, "language", { value: "zh-CN", configurable: true });
});

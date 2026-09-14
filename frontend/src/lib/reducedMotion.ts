/** 用户是否要求「减少动效」。
 *
 * 全局 CSS 已经把 animation / transition 压平（global.css 的 prefers-reduced-motion 块），
 * 但**带延时的 JS 动画收尾**压不平——比如「先淡出 180ms 再落库」，CSS 把动画变成 0ms 了，
 * setTimeout 还是照等 180ms，结果就是一次莫名其妙的卡顿。所以 JS 这边也要能问一句。
 *
 * jsdom 没有 matchMedia，用可选调用兜住（测试环境视为不减少动效，走完整路径）。
 */
export const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;

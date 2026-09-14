// 纯直链下载：浏览器导航到后端 URL，由服务器的 Content-Disposition 命名。
//
// 不 fetch、不创建 blob——blob 在部分 Chrome 里会忽略 <a download> 属性、落盘成
// 无后缀 UUID。直链没有 blob，UUID 物理上不可能出现：浏览器要么用响应头里的
// 文件名，要么退用 URL 末段（仍带后缀、能打开）。download 属性是同源直链的额外保险。
//
// 注意：若浏览器装了"接管下载"的扩展（如「猫抓」，带 downloads 权限），它会在下载层
// 统一改名，这与前端用什么方式无关——需在该站点停用此类扩展。

// 前端文本导出（演示/样本数据没有后端任务时的兜底）。
// 用 data: URL 而非 blob：同样零请求，且不踩 blob+download 属性的浏览器怪癖。
export function downloadText(filename: string, text: string): void {
  downloadUrl(`data:text/plain;charset=utf-8,${encodeURIComponent(text)}`, filename);
}

export function downloadUrl(href: string, filename?: string): void {
  const a = document.createElement("a");
  a.href = href;
  if (filename) a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  // 延迟移除：个别浏览器点击后还要短暂读取该元素
  setTimeout(() => a.remove(), 1500);
}

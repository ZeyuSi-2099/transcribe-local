// 脱敏保留词清单（后处理配置）——配置页与详情页接力卡共用的计数与上限。
//
// 前身是 ppProfile.ts：那里还有一整套「归类方案」#/## 解析器（结构镜像、错误卡、指标计数）。
// 2026-08-17 归类下架后解析器无人使用，整个删掉并改名——留着一个名叫 ppProfile
// 却只导出脱敏两项的文件，下次读代码的人得先花时间确认它跟归类没关系。

export const PP_LIST_MAX_CHARS = 8000;     // 同后端 PP_LIST_MAX_CHARS 缺省

/** 脱敏清单条数：一行一词，空行不计。 */
export function countRedactLines(content: string): number {
  return content.split("\n").filter((l) => l.trim().length > 0).length;
}

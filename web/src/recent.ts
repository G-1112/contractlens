/**
 * 最近分析记录（第十五轮功能升级 · R-UX-01）。
 *
 * 评委刷新页面或误关标签页后，报告此前无法找回（后端结果虽在，
 * 但前端没有入口）。这里把最近完成的分析存 localStorage（仅 file_id、
 * 合同类型、文件名、时间戳——不含合同内容），供上传页「最近的分析」
 * 一键重开。最多保留 5 条，失败的记录自动剔除。
 */

export interface RecentReport {
  fileId: string;
  contractType: string;
  filename: string;
  ts: number;
}

const KEY = "contractlens.recentReports";
const MAX = 5;

export function loadRecent(): RecentReport[] {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? (JSON.parse(raw) as RecentReport[]) : [];
    return Array.isArray(list) ? list.filter((x) => x && typeof x.fileId === "string") : [];
  } catch {
    return [];
  }
}

export function saveRecent(item: Omit<RecentReport, "ts">): void {
  try {
    const list = loadRecent().filter((x) => x.fileId !== item.fileId);
    list.unshift({ ...item, ts: Date.now() });
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    /* localStorage 不可用（隐私模式等）时静默降级 */
  }
}

export function removeRecent(fileId: string): RecentReport[] {
  const list = loadRecent().filter((x) => x.fileId !== fileId);
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* 忽略 */
  }
  return list;
}

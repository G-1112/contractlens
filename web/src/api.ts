import type {
  AnalysisReport,
  ContractTypeInput,
  DocumentResponse,
  StreamEvent,
  UploadResult,
} from "./types";

const BASE = "/api";

/**
 * 从错误响应里取可读文案。
 * 后端错误统一是 `{"detail": "..."}`，直接 res.text() 会把 JSON 原样抛给用户
 * （2026-11-17 评审：上传失败弹窗里出现过 `{"detail":"..."}` 字样）。
 */
async function readError(res: Response): Promise<string> {
  const raw = await res.text().catch(() => "");
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // 非 JSON 响应（如网关错误页），原样返回
  }
  return raw || `请求失败（HTTP ${res.status}）`;
}

export async function uploadFile(
  file: File,
  contractType: ContractTypeInput,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("contract_type", contractType);
  const res = await fetch(`${BASE}/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function getDocument(fileId: string): Promise<DocumentResponse> {
  const res = await fetch(`${BASE}/document/${fileId}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function getResult(fileId: string): Promise<AnalysisReport> {
  const res = await fetch(`${BASE}/result/${fileId}`);
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

/**
 * 通过 fetch 流式读取 SSE。
 * onEvent 逐条回调；返回后可通过 getResult 拉取最终报告。
 */
export async function analyze(
  fileId: string,
  contractType: ContractTypeInput,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, contract_type: contractType }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(await readError(res));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        onEvent(JSON.parse(payload) as StreamEvent);
      } catch {
        // 忽略非法事件
      }
    }
  }
}

export type ReportFormat = "md" | "revised" | "checklist" | "pdf" | "docx";

export function reportDownloadUrl(fileId: string, format: ReportFormat): string {
  return `${BASE}/report/${fileId}?format=${format}`;
}

/**
 * 通过 fetch 触发导出并拿到 Blob。
 * 用 fetch 而不是 `<a href>` 的原因：PDF/DOCX 需要后端渲染，失败时要能读到
 * 后端给的 detail（如"缺 GTK，请改用 Markdown"）并提示用户，锚点跳转只能看到
 * 浏览器错误页。
 */
export async function downloadReport(fileId: string, format: ReportFormat): Promise<Blob> {
  const res = await fetch(reportDownloadUrl(fileId, format));
  if (!res.ok) throw new Error(await readError(res));
  return res.blob();
}

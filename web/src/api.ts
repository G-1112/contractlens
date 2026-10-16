import type {
  AnalysisReport,
  ContractTypeInput,
  DocumentResponse,
  StreamEvent,
  UploadResult,
} from "./types";

const BASE = "/api";

export async function uploadFile(
  file: File,
  contractType: ContractTypeInput,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("contract_type", contractType);
  const res = await fetch(`${BASE}/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDocument(fileId: string): Promise<DocumentResponse> {
  const res = await fetch(`${BASE}/document/${fileId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getResult(fileId: string): Promise<AnalysisReport> {
  const res = await fetch(`${BASE}/result/${fileId}`);
  if (!res.ok) throw new Error(await res.text());
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
  if (!res.ok || !res.body) throw new Error(await res.text());

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

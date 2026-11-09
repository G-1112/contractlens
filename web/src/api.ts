import type {
  AnalysisReport,
  ChatResponse,
  ContractType,
  DocumentResponse,
  StreamEvent,
  UploadResult,
} from "./types";

const BASE = "/api";

/* ------------------------------------------------------------------ *
 * 评审访问口令（R-AUTH-01 前端侧）
 *
 * 背景：公网部署开启 REVIEW_ACCESS_CODE 后，所有 /api/* 需带
 * X-Review-Code 头；导出走 <a href> 无法自定义请求头，故改用 ?code=。
 * 口令存 localStorage，仅在用户设备上保留。
 * ------------------------------------------------------------------ */

const CODE_KEY = "contractlens.reviewCode";

export function getReviewCode(): string {
  try {
    return localStorage.getItem(CODE_KEY) ?? "";
  } catch {
    return ""; // 隐私模式 / 禁用存储时退化为无口令（页面会提示重新输入）
  }
}

export function setReviewCode(code: string): void {
  try {
    if (code) localStorage.setItem(CODE_KEY, code);
    else localStorage.removeItem(CODE_KEY);
  } catch {
    /* 忽略存储失败 */
  }
}

/** 口令无效（401）专用错误，页面可据此提示重新输入。 */
export class ReviewCodeError extends Error {
  constructor(message = "访问口令无效，请重新输入") {
    super(message);
    this.name = "ReviewCodeError";
  }
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const code = getReviewCode();
  return code ? { ...extra, "X-Review-Code": code } : { ...extra };
}

async function ensureOk(res: Response): Promise<void> {
  if (res.ok) return;
  if (res.status === 401) throw new ReviewCodeError();
  throw new Error(await res.text());
}

/** 健康检查（门禁放行，用于判断是否需要口令页）。 */
/**
 * 下载预置示例合同（评审零门槛体验）。
 * key 白名单见后端 api/routers/sample.py：loan / loan_injection / rent / labor / intern / guarantee。
 */
export async function fetchSample(key: string): Promise<Blob> {
  const res = await fetch(`${BASE}/sample/${key}`, { headers: authHeaders() });
  await ensureOk(res);
  return res.blob();
}

export async function getHealth(): Promise<{ status: string; access_gate: boolean }> {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * 探测当前口令是否有效。
 * 用一个必然不存在的 file_id 打 /api/result/：401=口令不对，
 * 任何其它状态（404）都说明已通过门禁。
 */
export async function probeReviewCode(): Promise<boolean> {
  const res = await fetch(`${BASE}/result/__probe__`, { headers: authHeaders() });
  return res.status !== 401;
}

export async function uploadFile(
  file: File,
  contractType: ContractType,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("contract_type", contractType);
  const res = await fetch(`${BASE}/upload`, {
    method: "POST",
    body: form,
    headers: authHeaders(),
  });
  await ensureOk(res);
  return res.json();
}

export async function getDocument(fileId: string): Promise<DocumentResponse> {
  const res = await fetch(`${BASE}/document/${fileId}`, { headers: authHeaders() });
  await ensureOk(res);
  return res.json();
}

export async function getResult(fileId: string): Promise<AnalysisReport> {
  const res = await fetch(`${BASE}/result/${fileId}`, { headers: authHeaders() });
  await ensureOk(res);
  return res.json();
}

/**
 * 通过 fetch 流式读取 SSE。
 * onEvent 逐条回调；返回后可通过 getResult 拉取最终报告。
 */
export async function analyze(
  fileId: string,
  contractType: ContractType,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/analyze`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ file_id: fileId, contract_type: contractType }),
    signal,
  });
  if (!res.ok || !res.body) {
    await ensureOk(res);
    throw new Error("响应为空");
  }

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

/**
 * 导出链接（<a href> 无自定义请求头，口令走 ?code= 查询参数）。
 */
export function reportDownloadUrl(fileId: string, format: ReportFormat): string {
  const code = getReviewCode();
  const suffix = code ? `&code=${encodeURIComponent(code)}` : "";
  return `${BASE}/report/${fileId}?format=${format}${suffix}`;
}

/**
 * 按需生成谈判话术。
 *
 * 分析阶段默认不再内联生成话术（真实 LLM 下那是第二个 LLM 波次，实测 3~5s），
 * 报告页拿到结果后按需调用本接口补齐，用户感知到的首屏时间更短。
 */
export async function generateScript(
  fileId: string,
  clauseIds: string[],
  tone = "礼貌",
): Promise<string[]> {
  const res = await fetch(`${BASE}/script`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ file_id: fileId, clause_ids: clauseIds, tone }),
  });
  await ensureOk(res);
  const data = (await res.json()) as { scripts: string[] };
  return data.scripts;
}

/** 追问对话：基于已生成的审查报告回答问题，返回带 sources/trace 的结构化响应。 */
export async function chat(fileId: string, question: string): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ file_id: fileId, question }),
  });
  await ensureOk(res);
  return res.json();
}

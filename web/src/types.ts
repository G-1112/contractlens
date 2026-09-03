// 与后端 api/schemas/__init__.py 一一对应的前端类型

export type RiskLevel = "高" | "中" | "低" | "无";
export type ContractType = "rent" | "labor" | "intern";
export type Source = "both" | "rule_override" | "llm_only" | "rule_only";

export interface Clause {
  id: string;
  text: string;
  page: number;
  bbox: number[];
  level: number;
  parent: string | null;
}

export interface RiskItem {
  clause_id: string;
  clause_text: string;
  page: number;
  final_level: RiskLevel;
  source: Source;
  needs_review: boolean;
  risk_type: string;
  explain: string;
  suggest: string;
  rewrite: string;
  regulation_refs: string[];
  rule_ids: string[];
}

export interface AnalysisReport {
  file_id: string;
  contract_type: ContractType;
  total_score: number;
  overall_level: RiskLevel;
  radar: Record<string, number>;
  radar_dimensions: string[];
  items: RiskItem[];
  talk_scripts: string[];
  clause_count: number;
  risk_counts: Record<string, number>;
  disclaimer: string;
}

export interface UploadResult {
  file_id: string;
  has_text_layer: boolean;
  page_count: number;
  contract_type: ContractType;
  filename: string;
}

export interface DocumentResponse {
  file_id: string;
  contract_type: ContractType;
  page_count: number;
  clauses: Clause[];
}

// SSE 事件
export interface ProgressEvent {
  type: "progress";
  stage: "parse" | "rule" | "judge" | "score";
  current: number;
  total: number;
}
export interface RiskEvent {
  type: "risk";
  data: RiskItem;
}
export interface DoneEvent {
  type: "done";
  report: AnalysisReport;
}
export interface ErrorEvent {
  type: "error";
  code: string;
  fallback?: string;
  message?: string;
}
export type StreamEvent = ProgressEvent | RiskEvent | DoneEvent | ErrorEvent;

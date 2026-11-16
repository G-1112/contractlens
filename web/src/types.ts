// 与后端 api/schemas/__init__.py 一一对应的前端类型

export type RiskLevel = "高" | "中" | "低" | "无";
export type ContractType = "rent" | "labor" | "intern";
/** 用户输入层类型：auto = 合同类型自动分类（后端解析正文后定型） */
export type ContractTypeInput = ContractType | "auto";
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
  /** 条款文本疑似含 Prompt 注入（后端 R-INJ-01 检测），已忽略该指令 */
  injection_suspected?: boolean;
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
  /** 条款超限只分析前 N 条（ANALYZE_MAX_CLAUSES） */
  truncated?: boolean;
  /** 任一条款疑似 Prompt 注入（后端安全创新点 R-INJ-01） */
  injection_flagged?: boolean;
  risk_counts: Record<string, number>;
  disclaimer: string;
}

export interface UploadResult {
  file_id: string;
  has_text_layer: boolean;
  page_count: number;
  /** 回显用户选择：可能是 auto（精确判定在 /api/analyze 解析后进行） */
  contract_type: ContractTypeInput;
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
  /** 附加提示（如「条款超限，已截断为前 N 条」） */
  message?: string;
}
/** 第十三轮自动分类：仅当请求 contract_type=auto 时推送，告知前端定型结果 */
export interface ContractTypeDetectedEvent {
  type: "contract_type_detected";
  data: ContractType;
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
export type StreamEvent =
  | ProgressEvent
  | ContractTypeDetectedEvent
  | RiskEvent
  | DoneEvent
  | ErrorEvent;

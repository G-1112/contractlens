import { create } from "zustand";
import type {
  AnalysisReport,
  ContractType,
  ContractTypeInput,
  DocumentResponse,
  RiskItem,
} from "../types";

type Stage = "upload" | "analyzing" | "report";

interface AppState {
  stage: Stage;
  fileId: string | null;
  contractType: ContractTypeInput;
  filename: string;
  uploadInfo: { page_count: number; has_text_layer: boolean } | null;

  // 分析过程
  progress: { stage: string; current: number; total: number } | null;
  streamedItems: RiskItem[];
  error: string | null;

  // 结果
  report: AnalysisReport | null;
  document: DocumentResponse | null;
  selectedClauseId: string | null;

  // actions
  setStage: (s: Stage) => void;
  setFile: (fileId: string, contractType: ContractTypeInput, filename: string) => void;
  /** 第十三轮自动分类：SSE 收到 contract_type_detected 后把 auto 回写为确定类型 */
  setContractType: (t: ContractType) => void;
  setUploadInfo: (info: { page_count: number; has_text_layer: boolean }) => void;
  setProgress: (p: { stage: string; current: number; total: number } | null) => void;
  appendRisk: (item: RiskItem) => void;
  setError: (e: string | null) => void;
  setReport: (r: AnalysisReport) => void;
  setDocument: (d: DocumentResponse) => void;
  selectClause: (id: string | null) => void;
  reset: () => void;
}

const initial = {
  stage: "upload" as Stage,
  fileId: null,
  contractType: "rent" as ContractType,
  filename: "",
  uploadInfo: null,
  progress: null,
  streamedItems: [],
  error: null,
  report: null,
  document: null,
  selectedClauseId: null,
};

export const useStore = create<AppState>((set) => ({
  ...initial,

  setStage: (stage) => set({ stage }),
  setFile: (fileId, contractType, filename) =>
    set({ fileId, contractType, filename, error: null }),
  setContractType: (contractType) => set({ contractType }),
  setUploadInfo: (uploadInfo) => set({ uploadInfo }),
  setProgress: (progress) => set({ progress }),
  appendRisk: (item) =>
    set((s) => ({ streamedItems: [...s.streamedItems, item] })),
  setError: (error) => set({ error }),
  setReport: (report) => set({ report }),
  setDocument: (document) => set({ document }),
  selectClause: (selectedClauseId) => set({ selectedClauseId }),
  reset: () => set({ ...initial }),
}));

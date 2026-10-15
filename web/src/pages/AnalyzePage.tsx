import { useEffect, useRef } from "react";
import { analyze, getDocument, getResult } from "../api";
import { useStore } from "../store/useStore";
import type { RiskItem, StreamEvent } from "../types";
import RiskCard from "../components/RiskCard";

const STAGE_LABEL: Record<string, string> = {
  parse: "正在解析文档…",
  rule: "正在运行规则引擎…",
  judge: "正在 AI 逐条判定…",
  score: "正在生成评分与报告…",
};

export default function AnalyzePage() {
  const {
    fileId,
    contractType,
    filename,
    uploadInfo,
    progress,
    streamedItems,
    error,
    appendRisk,
    setProgress,
    setError,
    setReport,
    setDocument,
    setStage,
  } = useStore();
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !fileId) return;
    started.current = true;

    const ctrl = new AbortController();
    (async () => {
      try {
        await analyze(fileId, contractType, async (e: StreamEvent) => {
          if (e.type === "progress") setProgress({ ...e });
          else if (e.type === "risk") appendRisk(e.data);
          else if (e.type === "error") setError(e.message ?? e.code);
          else if (e.type === "done") {
            // 拉取最终报告与文档
            const report = await getResult(fileId);
            const doc = await getDocument(fileId);
            setReport(report);
            setDocument(doc);
            setStage("report");
          }
        }, ctrl.signal);
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setError(err instanceof Error ? err.message : "分析失败");
        }
      }
    })();

    return () => ctrl.abort();
  }, [fileId, contractType, appendRisk, setProgress, setError, setReport, setDocument, setStage]);

  const total = progress?.total ?? 0;
  const current = progress?.current ?? 0;
  const pct = total > 0 ? Math.round((current / total) * 100) : 8;

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6 text-center">
        <h2 className="text-xl font-bold">正在审查「{filename}」</h2>
        <p className="mt-1 text-sm text-slate-500">
          {uploadInfo ? `${uploadInfo.page_count} 页 · 规则 + AI 双引擎判定` : ""}
        </p>
      </div>

      {/* 进度条 */}
      <div className="mb-6 rounded-xl bg-white p-5 shadow-sm">
        <div className="mb-2 flex items-center justify-between text-sm">
          <span className="text-slate-600">
            {STAGE_LABEL[progress?.stage ?? "parse"] ?? "处理中…"}
          </span>
          <span className="font-medium text-brand-600">
            {progress?.stage === "judge" ? `${current}/${total} 条` : `${pct}%`}
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
          <div
            className="h-full rounded-full bg-brand-600 transition-all duration-300"
            style={{ width: `${progress?.stage === "judge" ? Math.max(pct, 4) : pct}%` }}
          />
        </div>
        {progress?.stage === "judge" && (
          <p className="mt-2 text-xs text-slate-400">
            正在判定第 {current} 条 / 共 {total} 条，风险卡片会逐条出现
          </p>
        )}
      </div>

      {/* 流式风险卡片 */}
      <div className="space-y-3">
        {streamedItems.map((item: RiskItem) => (
          <RiskCard key={item.clause_id} item={item} compact />
        ))}
        {streamedItems.length === 0 && !error && (
          <div className="py-8 text-center text-slate-400">解析中…</div>
        )}
      </div>

      {error && (
        <div className="mt-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">
          <p className="font-semibold">分析遇到问题</p>
          <p className="mt-1">{error}</p>
          <p className="mt-1 text-xs">
            若为 LLM 不可用，系统已自动降级为纯规则判定，结果仍可查看。
          </p>
        </div>
      )}
    </div>
  );
}

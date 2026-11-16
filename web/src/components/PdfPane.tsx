import { useEffect, useMemo, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
// Vite 的 ?url 导入：构建期把 worker 产出为独立资源（FR-08 PDF 原文视图）
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { Clause, RiskItem, RiskLevel } from "../types";
import { useStore } from "../store/useStore";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

const SCALE = 1.5;

// 与全局配色一致：高=红 / 中=琥珀 / 低=蓝（同 ClauseViewer / RiskCard）
const LEVEL_FILL: Record<RiskLevel, string> = {
  高: "rgba(239, 68, 68, 0.22)",
  中: "rgba(245, 158, 11, 0.28)",
  低: "rgba(59, 130, 246, 0.22)",
  无: "transparent",
};
const LEVEL_BORDER: Record<RiskLevel, string> = {
  高: "rgba(239, 68, 68, 0.85)",
  中: "rgba(245, 158, 11, 0.85)",
  低: "rgba(59, 130, 246, 0.85)",
  无: "transparent",
};

interface Props {
  clauses: Clause[];
  items: RiskItem[];
  /** 上传时的 PDF 原始字节（服务端分析完即删，前端回放本地副本） */
  data: ArrayBuffer;
}

interface Rect {
  clauseId: string;
  level: RiskLevel;
  left: number;
  top: number;
  width: number;
  height: number;
}

export default function PdfPane({ clauses, items, data }: Props) {
  const { selectedClauseId, selectClause } = useStore();
  const [pageNumber, setPageNumber] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [rects, setRects] = useState<Rect[]>([]);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const scrollTargetRef = useRef<string | null>(null);

  // clause_id → 风险等级（与 ClauseViewer 同口径：取该条款第一条风险条目的等级）
  const levelByClause = useMemo(() => {
    const map: Record<string, RiskLevel> = {};
    for (const it of items) {
      if (!(it.clause_id in map) && it.final_level !== "无") {
        map[it.clause_id] = it.final_level;
      }
    }
    return map;
  }, [items]);

  // 载入 PDF（slice 拷贝一份，避免 getDocument 转移 store 里的 ArrayBuffer）
  useEffect(() => {
    let cancelled = false;
    const task = pdfjsLib.getDocument({ data: data.slice(0) });
    task.promise
      .then((doc) => {
        if (cancelled) {
          doc.destroy();
          return;
        }
        docRef.current = doc;
        setNumPages(doc.numPages);
        setPageNumber(1);
      })
      .catch(() => setError("PDF 加载失败，请改用文本视图查看"));
    return () => {
      cancelled = true;
      docRef.current?.destroy();
      docRef.current = null;
    };
  }, [data]);

  // 渲染当前页 canvas + 按风险等级着色的高亮层（bbox 为归一化比例坐标）
  useEffect(() => {
    const doc = docRef.current;
    const canvas = canvasRef.current;
    if (!doc || !canvas) return;
    let cancelled = false;
    (async () => {
      try {
        const page = await doc.getPage(pageNumber);
        const viewport = page.getViewport({ scale: SCALE });
        const context = canvas.getContext("2d");
        if (!context || cancelled) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        await page.render({ canvasContext: context, viewport }).promise;
        if (cancelled) return;

        const next: Rect[] = [];
        for (const c of clauses) {
          const level = levelByClause[c.id];
          if (c.page !== pageNumber || !level || c.bbox.length < 4) continue;
          const [x0, y0, x1, y1] = c.bbox;
          next.push({
            clauseId: c.id,
            level,
            left: Math.min(x0, x1) * 100,
            top: Math.min(y0, y1) * 100,
            width: Math.abs(x1 - x0) * 100,
            height: Math.abs(y1 - y0) * 100,
          });
        }
        setRects(next);

        // 点击风险卡 → 滚动定位到对应 bbox
        const target = scrollTargetRef.current;
        if (target) {
          scrollTargetRef.current = null;
          requestAnimationFrame(() => {
            document
              .getElementById(`pdf-hl-${target}`)
              ?.scrollIntoView({ behavior: "smooth", block: "center" });
          });
        }
      } catch {
        if (!cancelled) setError("PDF 页面渲染失败，请改用文本视图查看");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pageNumber, numPages, clauses, levelByClause]);

  // 卡片 → 画布联动：跳到条款所在页，渲染完成后由上面的 effect 完成滚动
  useEffect(() => {
    if (!selectedClauseId) return;
    const clause = clauses.find((c) => c.id === selectedClauseId);
    if (!clause) return;
    scrollTargetRef.current = selectedClauseId;
    if (clause.page !== pageNumber) {
      setPageNumber(clause.page);
    } else {
      requestAnimationFrame(() => {
        document
          .getElementById(`pdf-hl-${selectedClauseId}`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
        scrollTargetRef.current = null;
      });
    }
  }, [selectedClauseId, clauses, pageNumber]);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center gap-3 text-xs text-slate-500">
        <span className="font-semibold text-slate-700">合同原文（PDF）</span>
        <span className="flex items-center gap-1">
          <i className="inline-block h-2 w-2 rounded-full bg-red-500" /> 高风险
        </span>
        <span className="flex items-center gap-1">
          <i className="inline-block h-2 w-2 rounded-full bg-amber-500" /> 中风险
        </span>
        <span className="flex items-center gap-1">
          <i className="inline-block h-2 w-2 rounded-full bg-blue-500" /> 低风险
        </span>
      </div>

      {error && (
        <div className="mb-2 rounded-lg bg-amber-50 p-3 text-xs text-amber-700">{error}</div>
      )}

      <div className="max-h-[70vh] overflow-auto rounded-lg bg-slate-100 p-2">
        <div className="relative mx-auto bg-white shadow" style={{ width: "fit-content" }}>
          <canvas ref={canvasRef} className="block h-auto max-w-full" />
          {rects.map((r) => (
            <div
              key={r.clauseId}
              id={`pdf-hl-${r.clauseId}`}
              title={`${r.level}风险 · ${r.clauseId}（点击定位风险卡）`}
              onClick={() => selectClause(r.clauseId)}
              className="absolute cursor-pointer rounded-sm"
              style={{
                left: `${r.left}%`,
                top: `${r.top}%`,
                width: `${r.width}%`,
                height: `${r.height}%`,
                background: LEVEL_FILL[r.level],
                border: `1px solid ${LEVEL_BORDER[r.level]}`,
              }}
            />
          ))}
        </div>
      </div>

      <div className="mt-3 flex items-center justify-center gap-3 text-sm">
        <button
          onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
          disabled={pageNumber <= 1}
          className="rounded-lg border border-slate-300 px-3 py-1 font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          上一页
        </button>
        <span className="text-slate-500">
          {pageNumber} / {numPages || "…"}
        </span>
        <button
          onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))}
          disabled={pageNumber >= numPages}
          className="rounded-lg border border-slate-300 px-3 py-1 font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          下一页
        </button>
      </div>
    </div>
  );
}

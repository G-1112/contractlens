import { useEffect, useRef } from "react";
import type { Clause, RiskItem, RiskLevel } from "../types";
import { useStore } from "../store/useStore";

const LEVEL_CLASS: Record<RiskLevel, string> = {
  高: "clause-high",
  中: "clause-mid",
  低: "clause-low",
  无: "",
};

interface Props {
  clauses: Clause[];
  items: RiskItem[];
}

export default function ClauseViewer({ clauses, items }: Props) {
  const { selectedClauseId, selectClause } = useStore();
  const levelByClause: Record<string, RiskLevel> = {};
  for (const it of items) {
    if (!(it.clause_id in levelByClause)) levelByClause[it.clause_id] = it.final_level;
  }

  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  const containerRef = useRef<HTMLDivElement>(null);

  // 选中项变化时滚动到对应条款（卡片 → 原文联动）
  useEffect(() => {
    if (!selectedClauseId) return;
    const el = refs.current[selectedClauseId];
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedClauseId]);

  return (
    <div
      ref={containerRef}
      className="max-h-[70vh] overflow-y-auto rounded-xl border border-slate-200 bg-white p-4"
    >
      <div className="mb-3 flex items-center gap-3 text-xs text-slate-500">
        <span className="font-semibold text-slate-700">合同原文</span>
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

      <div className="space-y-2">
        {clauses.map((c) => {
          const level = levelByClause[c.id] ?? "无";
          const isSelected = selectedClauseId === c.id;
          return (
            <div
              key={c.id}
              ref={(el) => {
                refs.current[c.id] = el;
              }}
              onClick={() => selectClause(isSelected ? null : c.id)}
              className={`cursor-pointer rounded-lg border border-transparent px-3 py-2 text-sm leading-relaxed transition hover:bg-slate-50 ${
                LEVEL_CLASS[level]
              } ${isSelected ? "clause-selected" : ""}`}
              style={{ marginLeft: `${(c.level - 1) * 12}px` }}
            >
              <span className="mr-2 select-none text-xs text-slate-400">{c.id}</span>
              {c.text}
            </div>
          );
        })}
      </div>
    </div>
  );
}

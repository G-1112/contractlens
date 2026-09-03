import { useState } from "react";
import type { RiskItem, RiskLevel, Source } from "../types";
import { useStore } from "../store/useStore";

const LEVEL_STYLE: Record<RiskLevel, string> = {
  高: "bg-red-100 text-red-700",
  中: "bg-amber-100 text-amber-700",
  低: "bg-blue-100 text-blue-700",
  无: "bg-slate-100 text-slate-600",
};

const SOURCE_LABEL: Record<Source, string> = {
  both: "规则 + AI 一致",
  rule_override: "规则命中 · 需复核",
  llm_only: "AI 判断",
  rule_only: "规则命中",
};

const SOURCE_STYLE: Record<Source, string> = {
  both: "bg-slate-100 text-slate-600",
  rule_override: "bg-purple-100 text-purple-700",
  llm_only: "bg-emerald-100 text-emerald-700",
  rule_only: "bg-orange-100 text-orange-700",
};

interface Props {
  item: RiskItem;
  compact?: boolean;
}

export default function RiskCard({ item, compact }: Props) {
  const [open, setOpen] = useState(!compact);
  const selectClause = useStore((s) => s.selectClause);
  const selectedClauseId = useStore((s) => s.selectedClauseId);
  const isSelected = selectedClauseId === item.clause_id;

  return (
    <div
      className={`cursor-pointer rounded-xl border bg-white p-4 shadow-sm transition hover:shadow ${
        isSelected ? "border-brand-600 ring-2 ring-brand-500" : "border-slate-200"
      }`}
      onClick={() => selectClause(isSelected ? null : item.clause_id)}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${LEVEL_STYLE[item.final_level]}`}>
          {item.final_level}风险
        </span>
        {item.risk_type && (
          <span className="text-xs text-slate-500">{item.risk_type}</span>
        )}
        <span className={`rounded-full px-2 py-0.5 text-xs ${SOURCE_STYLE[item.source]}`}>
          {SOURCE_LABEL[item.source]}
        </span>
        {item.needs_review && (
          <span className="text-xs font-medium text-purple-600">建议人工复核</span>
        )}
      </div>

      <p className="mt-2 line-clamp-3 text-sm text-slate-700">{item.clause_text}</p>

      {open && (
        <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 text-sm">
          {item.explain && (
            <div>
              <span className="font-medium text-slate-500">说明：</span>
              <span className="text-slate-700">{item.explain}</span>
            </div>
          )}
          {item.suggest && (
            <div>
              <span className="font-medium text-slate-500">建议：</span>
              <span className="text-slate-700">{item.suggest}</span>
            </div>
          )}
          {item.rewrite && (
            <div className="rounded-lg bg-slate-50 p-2">
              <span className="font-medium text-slate-500">改写：</span>
              <span className="text-slate-700">{item.rewrite}</span>
            </div>
          )}
          {item.regulation_refs.length > 0 && (
            <div className="text-xs text-slate-400">
              参考法规：{item.regulation_refs.join("；")}
            </div>
          )}
        </div>
      )}

      <button
        className="mt-2 text-xs font-medium text-brand-600 hover:text-brand-700"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {open ? "收起" : "展开详情"}
      </button>
    </div>
  );
}

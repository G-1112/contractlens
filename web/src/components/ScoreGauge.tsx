import type { RiskLevel } from "../types";

const LEVEL_META: Record<RiskLevel, { color: string; label: string; ring: string }> = {
  高: { color: "text-red-600", label: "高风险", ring: "#dc2626" },
  中: { color: "text-amber-600", label: "中风险", ring: "#d97706" },
  低: { color: "text-emerald-600", label: "低风险", ring: "#059669" },
  无: { color: "text-slate-600", label: "无风险", ring: "#64748b" },
};

interface Props {
  score: number;
  level: RiskLevel;
}

export default function ScoreGauge({ score, level }: Props) {
  const meta = LEVEL_META[level];
  const r = 52;
  const c = 2 * Math.PI * r;
  const dash = (score / 100) * c;

  return (
    <div className="flex flex-col items-center">
      <div className="relative h-36 w-36">
        <svg viewBox="0 0 120 120" className="h-full w-full -rotate-90">
          <circle cx="60" cy="60" r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
          <circle
            cx="60"
            cy="60"
            r={r}
            fill="none"
            stroke={meta.ring}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={`${dash} ${c - dash}`}
            className="transition-all duration-700"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`text-4xl font-bold ${meta.color}`}>{score}</span>
          <span className="text-xs text-slate-400">/ 100</span>
        </div>
      </div>
      <span className={`mt-2 font-semibold ${meta.color}`}>{meta.label}</span>
    </div>
  );
}

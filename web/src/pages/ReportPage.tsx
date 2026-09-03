import { useStore } from "../store/useStore";
import ScoreGauge from "../components/ScoreGauge";
import RadarChart from "../components/RadarChart";
import ClauseViewer from "../components/ClauseViewer";
import RiskCard from "../components/RiskCard";
import TalkScript from "../components/TalkScript";
import { reportDownloadUrl } from "../api";

export default function ReportPage() {
  const { report, document, fileId, reset } = useStore();
  if (!report || !fileId) return null;

  const items = report.items;
  const dims = report.radar_dimensions;
  const values = dims.map((d) => report.radar[d] ?? 100);

  return (
    <div className="space-y-6">
      {/* 顶部操作栏 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">审查报告</h2>
          <p className="text-sm text-slate-500">
            共 {report.clause_count} 条条款 · 命中风险 {items.length} 条
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={reset}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50"
          >
            ← 重新上传
          </button>
          <a
            href={reportDownloadUrl(fileId, "md")}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50"
          >
            导出 Markdown
          </a>
          <a
            href={reportDownloadUrl(fileId, "pdf")}
            className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            导出 PDF 报告
          </a>
        </div>
      </div>

      {/* 仪表盘 */}
      <div className="grid gap-4 md:grid-cols-3">
        <div className="flex items-center justify-center rounded-xl border border-slate-200 bg-white p-4">
          <ScoreGauge score={report.total_score} level={report.overall_level} />
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-700">五维风险雷达</h3>
          <RadarChart dimensions={dims} values={values} />
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-700">风险分布</h3>
          <div className="space-y-2">
            {[
              ["高", "bg-red-500", report.risk_counts["高"] ?? 0],
              ["中", "bg-amber-500", report.risk_counts["中"] ?? 0],
              ["低", "bg-blue-500", report.risk_counts["低"] ?? 0],
            ].map(([label, color, count]) => (
              <div key={label as string} className="flex items-center gap-2">
                <i className={`inline-block h-3 w-3 rounded-full ${color}`} />
                <span className="w-8 text-sm text-slate-600">{label}风险</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full ${color}`}
                    style={{
                      width: `${items.length ? ((count as number) / items.length) * 100 : 0}%`,
                    }}
                  />
                </div>
                <span className="w-6 text-right text-sm font-medium">
                  {count as number}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-4 rounded-lg bg-slate-50 p-2 text-xs text-slate-400">
            {report.disclaimer}
          </p>
        </div>
      </div>

      {/* 原文 + 风险卡片 双向联动 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            原文高亮（点击定位）
          </h3>
          {document && <ClauseViewer clauses={document.clauses} items={items} />}
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            风险清单（点击跳转原文）
          </h3>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            {items.map((it) => (
              <RiskCard key={it.clause_id} item={it} />
            ))}
            {items.length === 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-slate-400">
                未发现风险条款 🎉
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 谈判话术 */}
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">
          谈判话术（可直接发送）
        </h3>
        <TalkScript scripts={report.talk_scripts} />
      </div>
    </div>
  );
}

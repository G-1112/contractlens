import { useMemo, useState } from "react";
import { useStore } from "../store/useStore";
import ScoreGauge from "../components/ScoreGauge";
import RadarChart from "../components/RadarChart";
import ClauseViewer from "../components/ClauseViewer";
import PdfPane from "../components/PdfPane";
import RiskCard from "../components/RiskCard";
import TalkScript from "../components/TalkScript";
import CopyButton from "../components/CopyButton";
import { reportDownloadUrl, downloadReport, type ReportFormat } from "../api";
import { buildRewriteDigest } from "../utils/clipboard";

type LevelFilter = "全部" | "高" | "中" | "低";

const LEVELS: LevelFilter[] = ["全部", "高", "中", "低"];

export default function ReportPage() {
  const { report, document, fileId, filename, pdfData, reset } = useStore();
  // 交互增强：风险等级筛选 + 关键词搜索（条款多时快速定位）
  const [level, setLevel] = useState<LevelFilter>("全部");
  const [keyword, setKeyword] = useState("");
  // 导出状态：PDF/DOCX 走后端渲染，失败时把后端 detail 显示在报告页顶部
  const [exportErr, setExportErr] = useState<string | null>(null);
  const [exporting, setExporting] = useState<ReportFormat | null>(null);
  // FR-08：PDF 用画布渲染 + bbox 高亮；DOCX / 无本地 PDF 副本时回退文本视图
  const isPdf = filename.toLowerCase().endsWith(".pdf");

  const filtered = useMemo(() => {
    const list = report?.items ?? [];
    const kw = keyword.trim().toLowerCase();
    return list.filter((it) => {
      if (level !== "全部" && it.final_level !== level) return false;
      if (kw) {
        const hay = `${it.clause_text}${it.explain}${it.suggest}${it.risk_type}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }, [report, level, keyword]);

  if (!report || !fileId) return null;

  const items = report.items;
  const fid = fileId;

  /** PDF / DOCX 走后端渲染：用 fetch 拿 Blob，失败时展示后端 detail（如缺 GTK 的指引）。 */
  async function handleExport(format: ReportFormat, name: string) {
    setExportErr(null);
    setExporting(format);
    try {
      const blob = await downloadReport(fid, format);
      const url = URL.createObjectURL(blob);
      const a = window.document.createElement("a");
      a.href = url;
      a.download = name;
      window.document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : "导出失败，请重试");
    } finally {
      setExporting(null);
    }
  }
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
            href={reportDownloadUrl(fid, "md")}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50"
          >
            导出 Markdown
          </a>
          <a
            href={reportDownloadUrl(fid, "revised")}
            title="仅含建议修改的条款：原文 ↔ 改写后对照，可直接发给对方谈判"
            className="rounded-lg border border-brand-600 bg-white px-3 py-2 text-sm font-medium text-brand-700 hover:bg-slate-50"
          >
            下载修订稿
          </a>
          <a
            href={reportDownloadUrl(fid, "checklist")}
            title="可勾选的整改清单：逐条列出待处理项，适合打印后与对方逐条核对"
            className="rounded-lg border border-brand-600 bg-white px-3 py-2 text-sm font-medium text-brand-700 hover:bg-slate-50"
          >
            整改清单
          </a>
          <button
            onClick={() => handleExport("docx", "合同审查报告.docx")}
            disabled={exporting !== null}
            title="Word 报告（只依赖 python-docx，不依赖 GTK，任何环境都能导出）"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-60"
          >
            {exporting === "docx" ? "生成中…" : "导出 Word"}
          </button>
          <button
            onClick={() => handleExport("pdf", "合同审查报告.pdf")}
            disabled={exporting !== null}
            title="PDF 需要 WeasyPrint + GTK 运行库；环境缺失时会提示改用 Word / Markdown"
            className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {exporting === "pdf" ? "生成中…" : "导出 PDF 报告"}
          </button>
        </div>
      </div>

      {/* 导出失败提示：PDF 依赖 WeasyPrint+GTK，缺失时展示后端给出的替代方案 */}
      {exportErr && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          <b>导出失败：</b>
          {exportErr}
        </div>
      )}

      {/* 条款超限提示（ANALYZE_MAX_CLAUSES 截断） */}
      {report.truncated && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700">
          条款较多，已截断为前 {report.clause_count} 条逐条分析，其余条款未纳入本次判定。
        </div>
      )}

      {/* 注入检测横幅（安全创新点 R-INJ-01） */}
      {report.injection_flagged && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          ⚠ 检测到部分条款文本疑似包含<b>提示注入</b>（试图指示 AI 忽略审查规则）。
          系统已按数据而非指令处理这些条款，对应风险卡片带「疑似提示注入」标记，建议人工复核原文。
        </div>
      )}

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
          {document && isPdf && pdfData ? (
            <PdfPane clauses={document.clauses} items={items} data={pdfData} />
          ) : (
            document && <ClauseViewer clauses={document.clauses} items={items} />
          )}
        </div>
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-700">
              风险清单（点击跳转原文）
            </h3>
            <div className="ml-auto flex items-center gap-1">
              {/* 一键复制全部改写建议：谈判时可直接粘贴发送，无需下载整份文档 */}
              <CopyButton
                text={buildRewriteDigest(filtered)}
                label="复制全部改写"
                doneLabel="已复制全部"
                className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                  filtered.some((it) => it.rewrite || it.suggest)
                    ? "bg-brand-600 text-white hover:bg-brand-700"
                    : "cursor-not-allowed bg-slate-100 text-slate-400"
                }`}
              />
              {LEVELS.map((lv) => (
                <button
                  key={lv}
                  onClick={() => setLevel(lv)}
                  className={`rounded-md px-2 py-1 text-xs font-medium transition ${
                    level === lv
                      ? "bg-brand-600 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                  }`}
                >
                  {lv}
                </button>
              ))}
            </div>
          </div>
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索条款关键词（如 押金、违约金）"
            className="mb-2 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-brand-500"
          />
          <p className="mb-2 text-xs text-slate-400">
            显示 {filtered.length} / {items.length} 条
          </p>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            {filtered.map((it) => (
              <RiskCard key={it.clause_id} item={it} />
            ))}
            {items.length === 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-slate-400">
                未发现风险条款 🎉
              </div>
            )}
            {items.length > 0 && filtered.length === 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-slate-400">
                没有符合当前筛选条件的风险条款
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

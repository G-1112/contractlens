import { useStore } from "./store/useStore";
import UploadPage from "./pages/UploadPage";
import AnalyzePage from "./pages/AnalyzePage";
import ReportPage from "./pages/ReportPage";

export default function App() {
  const stage = useStore((s) => s.stage);

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
              <EyeIcon />
            </div>
            <div>
              <h1 className="text-base font-semibold leading-tight">契约眼</h1>
              <p className="text-xs text-slate-500">合同条款风险审查</p>
            </div>
          </div>
          <p className="hidden text-xs text-slate-400 sm:block">
            不构成法律意见，仅供参考
          </p>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        {stage === "upload" && <UploadPage />}
        {stage === "analyzing" && <AnalyzePage />}
        {stage === "report" && <ReportPage />}
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <p className="mx-auto max-w-7xl px-4 py-3 text-center text-xs text-slate-400">
          契约眼 · AI 辅助合同风险审查工具 · 本结果由 AI 生成，不构成法律意见，仅供参考
        </p>
      </footer>
    </div>
  );
}

function EyeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

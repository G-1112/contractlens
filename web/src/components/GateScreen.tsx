import { useState } from "react";

interface Props {
  error: string;
  onSubmit: (code: string) => Promise<void>;
}

/**
 * 评审访问口令页（R-AUTH-01 前端侧）。
 *
 * 仅在 /api/health 返回 access_gate=true 且本地无有效口令时出现。
 * 口令只存 localStorage，不上送任何第三方；后端用常量时间比较校验。
 */
export default function GateScreen({ error, onSubmit }: Props) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const value = code.trim();
    if (!value || busy) return;
    setBusy(true);
    try {
      await onSubmit(value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600 text-white">
            <LockIcon />
          </div>
          <div>
            <h1 className="text-base font-semibold leading-tight">契约眼</h1>
            <p className="text-xs text-slate-500">合同条款风险审查</p>
          </div>
        </div>

        <p className="mb-5 text-sm leading-6 text-slate-600">
          这是赛事评审专用实例，请输入评审访问口令后使用。
        </p>

        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs font-medium text-slate-500" htmlFor="review-code">
            访问口令
          </label>
          <input
            id="review-code"
            type="text"
            autoComplete="off"
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="例如 CL-xxxx-xxxx"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
          {error ? (
            <p className="text-xs text-red-600" role="alert" data-testid="gate-error">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={busy || !code.trim()}
            className="w-full rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "校验中…" : "进入"}
          </button>
        </form>

        <p className="mt-6 text-[11px] leading-5 text-slate-400">
          口令仅保存在本机浏览器，不会上传。若没有口令，请向作品提交者索取。
        </p>
      </div>
    </div>
  );
}

function LockIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

import { useState } from "react";

interface Props {
  error: string;
  onSubmit: (username: string, password: string) => Promise<void>;
}

/**
 * 评审登录页（R-AUTH-02 前端侧，第十四轮区域赛升级）。
 *
 * 由单口令门禁升级为「账号 + 密码」登录，对齐大赛平台「账号/密码」提交栏位。
 * 校验走 POST /api/login（后端常量时间比较 + 连续失败锁定），通过后
 * token 只存 localStorage，不上送任何第三方；后续请求照旧带 X-Review-Code。
 */
export default function GateScreen({ error, onSubmit }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const u = username.trim();
    const p = password.trim();
    if (!u || !p || busy) return;
    setBusy(true);
    try {
      await onSubmit(u, p);
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
          这是赛事评审专用实例，请输入评审账号与密码登录。
        </p>

        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs font-medium text-slate-500" htmlFor="review-username">
            账号
          </label>
          <input
            id="review-username"
            type="text"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="评审账号"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
          <label className="block text-xs font-medium text-slate-500" htmlFor="review-password">
            密码
          </label>
          <input
            id="review-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="评审密码"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          />
          {error ? (
            <p className="text-xs text-red-600" role="alert" data-testid="gate-error">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={busy || !username.trim() || !password.trim()}
            className="w-full rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "校验中…" : "登录"}
          </button>
        </form>

        <p className="mt-6 text-[11px] leading-5 text-slate-400">
          账号与密码由作品提交材料提供，仅保存在本机浏览器，不会上传。若未获得，请向作品提交者索取。
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

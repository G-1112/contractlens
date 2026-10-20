import { useState } from "react";

interface Props {
  scripts: string[];
  /** 按需生成中（分析阶段不再内联生成，进入报告页后才发起） */
  loading?: boolean;
  /** 生成失败时的可重试提示 */
  error?: string | null;
  onRetry?: () => void;
}

export default function TalkScript({ scripts, loading, error, onRetry }: Props) {
  const [copied, setCopied] = useState<number | null>(null);

  const copy = async (text: string, i: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(i);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* 忽略剪贴板失败 */
    }
  };

  if (loading) {
    return (
      <div
        className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-400"
        data-testid="talk-script-loading"
      >
        正在生成谈判话术…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700">
        {error}
        {onRetry && (
          <button
            onClick={onRetry}
            className="ml-2 rounded-md bg-amber-100 px-2 py-1 text-xs font-medium hover:bg-amber-200"
          >
            重试
          </button>
        )}
      </div>
    );
  }

  if (scripts.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-400">
        暂无谈判话术
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {scripts.map((s, i) => (
        <div
          key={i}
          className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-slate-400">话术 {i + 1}</span>
            <button
              onClick={() => copy(s, i)}
              className="rounded-md bg-brand-50 px-2 py-1 text-xs font-medium text-brand-600 hover:bg-brand-100"
            >
              {copied === i ? "已复制 ✓" : "复制"}
            </button>
          </div>
          <p className="text-sm leading-relaxed text-slate-700">{s}</p>
        </div>
      ))}
    </div>
  );
}

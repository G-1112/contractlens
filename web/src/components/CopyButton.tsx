import { useState } from "react";
import { copyText } from "../utils/clipboard";

interface Props {
  /** 要复制的内容 */
  text: string;
  /** 按钮文案 */
  label?: string;
  /** 复制成功后的提示文案 */
  doneLabel?: string;
  className?: string;
}

/**
 * 复制按钮（第四轮交互增强）。
 * 点击后短暂显示「已复制」，给用户明确反馈。
 */
export default function CopyButton({
  text,
  label = "复制",
  doneLabel = "已复制",
  className,
}: Props) {
  const [done, setDone] = useState(false);

  async function handleCopy(e: React.MouseEvent) {
    // 卡片本身有点击跳转逻辑，复制不应触发它
    e.stopPropagation();
    const ok = await copyText(text);
    if (!ok) return;
    setDone(true);
    window.setTimeout(() => setDone(false), 1200);
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={done ? "已复制到剪贴板" : "复制到剪贴板"}
      className={
        className ??
        `rounded-md px-2 py-0.5 text-xs font-medium transition ${
          done
            ? "bg-emerald-100 text-emerald-700"
            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
        }`
      }
    >
      {done ? `✓ ${doneLabel}` : label}
    </button>
  );
}

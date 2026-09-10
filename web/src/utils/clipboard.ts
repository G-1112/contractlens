/**
 * 剪贴板工具（第四轮交互增强）。
 *
 * 谈判场景下用户最需要的是「把某条改写文本复制走」，
 * 此前只能手动框选或下载整份修订稿。
 *
 * 兼容性：优先用 navigator.clipboard（需要安全上下文），
 * 失败时回退到 textarea + execCommand（离线/非 https 环境仍可用）。
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的兜底实现
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** 把多条风险条款的改写建议拼成可直接发送的纯文本 */
export function buildRewriteDigest(
  items: { final_level: string; clause_id: string; clause_text: string; rewrite?: string; suggest?: string; risk_type?: string }[],
): string {
  const order: Record<string, number> = { 高: 0, 中: 1, 低: 2, 无: 3 };
  const picked = items
    .filter((it) => it.rewrite || it.suggest)
    .sort((a, b) => (order[a.final_level] ?? 9) - (order[b.final_level] ?? 9));

  if (!picked.length) return "";

  const lines: string[] = ["以下条款建议修改：", ""];
  picked.forEach((it, i) => {
    lines.push(`${i + 1}. [${it.final_level}风险] ${it.risk_type || it.clause_id}`);
    lines.push(`   原文：${it.clause_text.replace(/\s+/g, " ").slice(0, 120)}`);
    lines.push(`   建议改为：${(it.rewrite || it.suggest || "").replace(/\s+/g, " ")}`);
    lines.push("");
  });
  return lines.join("\n").trim();
}

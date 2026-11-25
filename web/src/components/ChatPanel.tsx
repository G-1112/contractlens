import { useEffect, useRef, useState } from "react";
import { chat } from "../api";
import type { ChatResponse, ChatSource } from "../types";

interface Message {
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
}

/** 来源类型徽标的配色：条款原文 / 法条出处 / 已判定风险 */
const KIND_STYLE: Record<string, { label: string; cls: string }> = {
  clause: { label: "条款原文", cls: "bg-sky-100 text-sky-700" },
  regulation: { label: "法条出处", cls: "bg-emerald-100 text-emerald-700" },
  risk: { label: "已判定风险", cls: "bg-rose-100 text-rose-700" },
};

function SourceChip({ s }: { s: ChatSource }) {
  const st = KIND_STYLE[s.kind] ?? { label: s.kind, cls: "bg-slate-100 text-slate-600" };
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs">
        <span className={`rounded px-1 py-0.5 font-medium ${st.cls}`}>{st.label}</span>
        <span className="font-medium text-slate-700">{s.ref}</span>
      </summary>
      <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-600">{s.text}</p>
    </details>
  );
}

function AssistantBubble({ m }: { m: Message }) {
  const r = m.response;
  return (
    <div className="mr-auto max-w-[85%] rounded-2xl rounded-tl-sm border border-slate-200 bg-white p-3 shadow-sm">
      {r?.injection_suspected && (
        <div className="mb-2 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700">
          ⚠ 本次回答涉及的条款文本疑似含提示注入，已按数据而非指令处理。
        </div>
      )}
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">{m.text}</p>
      {r && r.sources.length > 0 && (
        <div className="mt-2 space-y-1.5">
          <p className="text-xs font-medium text-slate-500">引用来源（可追溯）</p>
          {r.sources.map((s, i) => (
            <SourceChip key={i} s={s} />
          ))}
        </div>
      )}
      {r && r.trace.length > 0 && (
        <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer select-none">
            工具调用轨迹（{r.trace.length} 步）
          </summary>
          <ol className="mt-1 list-decimal space-y-0.5 pl-4 font-mono text-[11px] text-slate-500">
            {r.trace.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

const EXAMPLES = [
  "第4条约定的年利率是否合法？请引用具体法条说明",
  "本合同有哪些高风险条款？",
  "借款人提前还款有什么限制？",
];

export default function ChatPanel({ fileId }: { fileId: string }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // 生成方式徽标跟随最后一次真实回答，初始为 mock，不冒充模型能力
  const [mode, setMode] = useState("mock");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function ask(q: string) {
    const question = q.trim();
    if (!question || loading) return;
    setError("");
    setInput("");
    setMessages((ms) => [...ms, { role: "user", text: question }]);
    setLoading(true);
    try {
      const r = await chat(
          fileId,
          question,
          // 第十九轮多轮追问：携带最近 3 轮问答（服务器再截断清洗）
          (() => {
            const pairs: { q: string; a: string }[] = [];
            for (let i = 0; i < messages.length - 1; i++) {
              if (messages[i].role === "user" && messages[i + 1].role === "assistant") {
                pairs.push({ q: messages[i].text, a: messages[i + 1].text });
              }
            }
            return pairs.slice(-3);
          })(),
        );
      setMode(r.mode);
      setMessages((ms) => [...ms, { role: "assistant", text: r.answer, response: r }]);
    } catch (e) {
      setError(`追问失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="chat-panel"
      className="flex flex-col rounded-xl border border-slate-200 bg-white"
    >
      {/* 标题栏 */}
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <h3 className="text-sm font-semibold text-slate-700">
          智能追问 · 合同审查 Agent
        </h3>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">
          {mode === "llm" ? "模型生成" : "确定性模板模式"}
        </span>
      </div>

      {/* 消息区 */}
      <div className="flex max-h-[55vh] min-h-[220px] flex-col gap-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              基于上面的审查报告继续追问，回答会引用具体条款与法条出处：
            </p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((q) => (
                <button
                  key={q}
                  onClick={() => ask(q)}
                  className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs text-brand-700 hover:bg-brand-100"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div
              key={i}
              className="ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-brand-600 px-3 py-2 text-sm text-white"
            >
              {m.text}
            </div>
          ) : (
            <AssistantBubble key={i} m={m} />
          ),
        )}
        {loading && (
          <div className="mr-auto rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 text-sm text-slate-400 shadow-sm">
            检索条款与法规中…
          </div>
        )}
        {error && <p className="text-xs text-rose-600">{error}</p>}
        <div ref={bottomRef} />
      </div>

      {/* 输入区 */}
      <div className="border-t border-slate-100 p-3">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) ask(input);
            }}
            placeholder="针对这份合同继续提问，如：第4条年利率是否合法？"
            maxLength={500}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500"
          />
          <button
            onClick={() => ask(input)}
            disabled={loading || !input.trim()}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition ${
              loading || !input.trim()
                ? "cursor-not-allowed bg-slate-300"
                : "bg-brand-600 hover:bg-brand-700"
            }`}
          >
            {loading ? "…" : "发送"}
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-400">
          回答基于合同原文与法规库检索生成，不构成法律意见，仅供参考。
        </p>
      </div>
    </div>
  );
}

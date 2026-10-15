import { useCallback, useRef, useState } from "react";
import { uploadFile } from "../api";
import { useStore } from "../store/useStore";
import type { ContractType } from "../types";

const CONTRACT_TYPES: { value: ContractType; label: string; desc: string }[] = [
  { value: "rent", label: "房屋租赁", desc: "租房合同" },
  { value: "labor", label: "劳动合同", desc: "入职合同" },
  { value: "intern", label: "实习协议", desc: "实习协议" },
  { value: "loan", label: "借款合同", desc: "民间借贷/消费贷" },
  { value: "guarantee", label: "保证担保", desc: "担保/保证合同" },
];

export default function UploadPage() {
  const { contractType, setFile, setUploadInfo, setStage, setError, reset } =
    useStore();
  const [file, setFileState] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 扫描件提示（第五轮修复）：后端 upload 会返回 has_text_layer，
  // 此前前端只把它存进 store 就立刻进入分析，导致用户要等到分析阶段
  // 才收到 NO_TEXT_LAYER 报错。这里改为上传后立即拦下并给出可操作指引。
  const [noTextLayer, setNoTextLayer] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const doUpload = useCallback(async () => {
    if (!file) return;
    setUploading(true);
    setErr(null);
    setNoTextLayer(false);
    try {
      const res = await uploadFile(file, contractType);
      setFile(res.file_id, res.contract_type, res.filename);
      setUploadInfo({ page_count: res.page_count, has_text_layer: res.has_text_layer });

      // 无文本层（通常是扫描件/图片版 PDF）：不要进入分析，否则必然失败。
      if (!res.has_text_layer) {
        setNoTextLayer(true);
        return;
      }
      setStage("analyzing");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "上传失败");
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }, [file, contractType, setFile, setUploadInfo, setStage, setError]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFileState(f);
  }, []);

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-8 text-center">
        <h2 className="text-2xl font-bold">上传一份合同，逐条看清风险</h2>
        <p className="mt-2 text-slate-500">
          支持 PDF / DOCX · 单文件 ≤ 20MB · 分析完成后立即删除，不落盘存储
        </p>
      </div>

      {/* 合同类型 */}
      <div className="mb-5 grid grid-cols-3 gap-3">
        {CONTRACT_TYPES.map((t) => (
          <button
            key={t.value}
            onClick={() => {
              reset();
              useStore.setState({ contractType: t.value });
            }}
            className={`rounded-xl border-2 p-3 text-left transition ${
              contractType === t.value
                ? "border-brand-600 bg-brand-50"
                : "border-slate-200 bg-white hover:border-slate-300"
            }`}
          >
            <div className="font-semibold">{t.label}</div>
            <div className="text-xs text-slate-500">{t.desc}</div>
          </button>
        ))}
      </div>

      {/* 文件拖拽区 */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed p-10 transition ${
          dragOver ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.doc"
          className="hidden"
          onChange={(e) => setFileState(e.target.files?.[0] ?? null)}
        />
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-100 text-brand-600">
          <UploadIcon />
        </div>
        {file ? (
          <div className="text-center">
            <p className="font-medium">{file.name}</p>
            <p className="text-xs text-slate-500">
              {(file.size / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
        ) : (
          <>
            <p className="font-medium">拖拽文件到此处，或点击选择</p>
            <p className="mt-1 text-xs text-slate-400">PDF 需为文本型（扫描件需走 OCR）</p>
          </>
        )}
      </div>

      {err && (
        <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>
      )}

      {noTextLayer && (
        <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm">
          <div className="mb-1 font-semibold text-amber-800">
            这份 PDF 没有文字层，无法直接审查
          </div>
          <p className="text-amber-800">
            文件看起来是扫描件或图片版（页面内容是一张图，而不是可选中的文字）。
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-amber-800">
            <li>用 Word / WPS 打开原件，另存为 <code>.docx</code> 后重新上传；</li>
            <li>或导出为<strong>文字版 PDF</strong>（打印 → 另存为 PDF，而非扫描）；</li>
            <li>文档 30 页以内、20MB 以内可以直接上传。</li>
          </ul>
          <p className="mt-2 text-amber-700">
            提示：本项目当前版本不做 OCR，扫描件需先在本地转成可选中文字的版本。
          </p>
        </div>
      )}

      <button
        onClick={doUpload}
        disabled={!file || uploading}
        className="mt-5 w-full rounded-xl bg-brand-600 py-3 font-semibold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {uploading ? "上传中…" : "开始审查"}
      </button>

      {/* 功能亮点 */}
      <div className="mt-8 grid grid-cols-3 gap-3 text-center text-sm">
        {[
          ["规则引擎", "80 条规则库确定性判定"],
          ["AI 语义判定", "大模型逐条解读大白话"],
          ["谈判话术", "一键生成可直接发送的话术"],
        ].map(([t, d]) => (
          <div key={t} className="rounded-xl bg-white p-4 shadow-sm">
            <div className="font-semibold">{t}</div>
            <div className="mt-1 text-xs text-slate-500">{d}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 3v12m0-12 4 4m-4-4-4 4" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
  );
}

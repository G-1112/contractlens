# 契约眼 ContractLens

> 上传一份合同，拿到逐条风险标注、通俗解释、修改建议，以及一段可以直接发给对方的谈判话术。

针对**房屋租赁合同 / 劳动合同 / 实习协议**三类合同，做一条**文档处理流水线**（而非聊天框）：

```
解析 → 结构化切分 → 规则 + LLM 双引擎判定 → 可视化呈现 → 可交付的修订稿
```

- 后端：Python 3.11+ / FastAPI / pdfplumber / python-docx / Pydantic / PyYAML
- 前端：React 18 / TypeScript / Vite / TailwindCSS / Zustand / ECharts
- 判定：规则引擎（确定性） + LLM（语义）双引擎融合，LLM 失败自动降级为纯规则
- 检索：法规片段 RAG（默认纯 Python 字符级 TF-IDF，可选 ChromaDB + bge-small-zh）

---

## 目录结构

```
contractlens/
├── api/                 # FastAPI 后端
│   ├── main.py          # 入口
│   ├── pipeline.py      # 编排：解析→检索→规则/LLM→融合→评分
│   ├── routers/         # upload / analyze(SSE) / script / report
│   ├── parser/          # pdf/docx 解析 + 条款切分（零 LLM 依赖）
│   ├── engine/          # rule_engine / llm_engine / fusion / retriever / scoring
│   ├── rules/           # rent.yaml / labor.yaml / intern.yaml（80 条规则）
│   ├── report/          # 谈判话术 + 报告导出（md/docx/pdf）
│   └── schemas/         # Pydantic 数据模型
├── web/                 # React 前端
├── data/
│   ├── regulations/     # 法规片段（rent/labor/intern.json）
│   └── samples/         # 3 份含风险条款的样本合同
├── tests/               # 单元测试（30 项，覆盖切分/规则/融合/评分/流水线）
├── scripts/             # make_sample_docx.py（生成可上传的样本）
├── docs/                # prompt-log / iteration / RETRO / 技术文档
└── pyproject.toml
```

---

## ⚡ 10 分钟极速复现（评委必读）

### 环境要求

- Python **3.11+**（推荐 3.11，部分依赖对 3.14 尚未发布 wheel）
- Node.js **18+**
- 包管理：`uv`（推荐）或 `pip`

### 第 1 步：安装后端依赖

```bash
cd contractlens

# 方式 A：uv（推荐，锁文件清晰）
uv venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"       # 基础 + 测试依赖

# 方式 B：pip
pip install -e ".[dev]"
```

> 报告 PDF 导出依赖 `weasyprint`（需系统 cairo/pango）。若安装失败，可跳过，
> 使用 `md` 格式导出；`pdfplumber` 用于 PDF 解析，纯文本/文档类流程不依赖它也能跑。

### 第 2 步：配置环境变量（可选）

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（不填则自动进入 mock 离线模式）
```

**不填任何 Key 也能完整跑通**：系统进入 mock 模式，规则引擎照常工作，LLM 判定降级为
确定性启发式，端到端演示不受影响（这也是 NFR-03 可复现性的设计保证）。

### 第 3 步：启动后端

```bash
uvicorn api.main:app --reload --port 8000
# 打开 http://127.0.0.1:8000/docs 查看接口
```

### 第 4 步：启动前端

```bash
cd web
npm install
npm run dev
# 打开 http://localhost:5173
```

### 第 5 步：上传合同试跑

1. 生成一份样本（可选）：`python scripts/make_sample_docx.py`
2. 浏览器打开前端 → 选合同类型 → 上传 `data/samples/房屋租赁合同样本.docx`
3. 观察流式进度 → 逐条风险卡片 → 仪表盘（评分/雷达）→ 原文↔风险卡联动 → 谈判话术 → 导出报告

### 第 6 步：跑测试（验证）

```bash
pytest -q
# 预期：30 passed
```

---

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/upload` | 上传 PDF/DOCX → `{file_id, has_text_layer, page_count}` |
| POST | `/api/analyze` | SSE 流式分析，逐条推送 `progress / risk / done` 事件 |
| GET | `/api/document/{file_id}` | 获取条款树（前端原文高亮） |
| GET | `/api/result/{file_id}` | 获取完整分析结果（含雷达） |
| POST | `/api/script` | 生成谈判话术 |
| POST | `/api/report/{file_id}?format=md|pdf|docx` | 导出报告 |
| GET | `/api/health` | 健康检查（显示 LLM 是否启用） |

---

## 核心设计（对应评分维度）

| 官方维度 | 本项目落点 |
|---|---|
| 过程可复现性（25%） | mock 模式零 Key 跑通 + 10 分钟复现 + `docs/prompt-log/` + 细粒度提交 |
| 解题有效性（25%） | 真实痛点（毕业生租房/签约）+ 完整产品闭环 |
| 方法创新（20%） | 规则 + LLM 双引擎融合，对 AI 黑盒的工程约束（见 `docs/技术文档.md`） |
| 工程质量（15%） | 分层解耦，解析层/规则层零 LLM 依赖，可独立测试 |
| 产品精细度（15%） | 原文↔风险卡双向联动、流式输出、思考可视化、免责声明 |

### 双引擎融合策略（FR-07）

```
规则命中 && LLM 等级 ≥ 规则等级 → 取 LLM，来源 "both"
规则命中 && LLM 等级 < 规则等级 → 取规则，来源 "rule_override"（标记"需复核"）
规则命中 && LLM 判定缺失       → 取规则，来源 "rule_only"
无规则命中                     → 取 LLM，来源 "llm_only"
```

规则命中即抬高等级下限：即便 AI 判低分，命中高风险规则也不能低于该规则等级。
前端用不同标签区分「规则命中」与「AI 判断」，不冒充权威。

---

## 隐私与合规

- 文件**分析完成后立即删除**，不落盘持久化（`api/routers/analyze.py` 的 finally 块）。
- 全局显著标注「不构成法律意见，仅供参考」，不做法律效力判定。
- 不收集用户数据、无账号系统、无云端存储。

## 许可

本项目为参赛作品，仅用于展示与学习，不构成法律意见。

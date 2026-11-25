# 契约眼 ContractLens

> 上传一份合同，拿到逐条风险标注、通俗解释、修改建议，以及一段可以直接发给对方的谈判话术。

针对**房屋租赁合同 / 劳动合同 / 实习协议 / 借款合同 / 保证合同**五类合同，做一条**文档处理流水线**（而非聊天框）：

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
│   ├── rules/           # rent/labor/intern/loan/guarantee.yaml（111 条规则）
│   ├── report/          # 谈判话术 + 报告导出（md/docx/pdf）
│   └── schemas/         # Pydantic 数据模型
├── web/                 # React 前端
├── data/
│   ├── regulations/     # 法规片段（5 类 × 25~30 条，合计 140 条）
│   ├── samples/         # 6 份含风险条款的样本合同（含 1 份提示注入攻击样本）
│   └── evalset/          # 合成标注集 50 份 + labels.json
├── tests/               # 单元 + 集成测试（收集 222 项，覆盖解析/规则/融合/评分/安全/路由/解析复杂度全链路）
├── scripts/             # 样本生成 / 评测 / 验收脚本（见下）
├── docs/                # prompt-log / iteration / RETRO / 技术文档 / 安全设计
└── pyproject.toml
```

---

## 📊 脚本一览（全部可复跑）

| 脚本 | 作用 | 典型用法 |
|---|---|---|
| `make_sample_docx.py` / `make_finance_samples.py` | 生成可上传的样本合同（含注入攻击样本） | `python scripts/make_finance_samples.py` |
| `build_evalset.py` / `evaluate_accuracy.py` | 合成标注集 + 条款级 P/R/F1 | `python scripts/evaluate_accuracy.py` |
| `evaluate_finance.py` | 金融方向端到端 + 性能基线，**报告头自动自证 mock/真实 LLM 口径** | `python scripts/evaluate_finance.py` |
| `e2e_smoke.py` | 端到端 HTTP 冒烟（健康/审查/法条/注入/对话） | `python scripts/e2e_smoke.py <base_url>` |
| `verify_export_online.py` | 线上五格式导出复验（PDF 魔数 + 404 路径） | `python scripts/verify_export_online.py <base_url>` |
| `scan_anonymity.py` | **匿名评审脱敏闸门**：命中学校/姓名/学号/导师/邮箱即退出码 1 | `python scripts/scan_anonymity.py` |

## 🆕 第 14–17 轮升级摘要（2026-11-24/25，区域赛）

| 轮次 | 内容 | 关键数字 |
|---|---|---|
| 13 | 检索器对照实验（BM25 盲评胜 76% 但端到端 F1 降，回滚 TF-IDF） | F1 0.7240 · 检索 65.2ms |
| 19 | Prompt v14 分级标尺 + 追问多轮化（R-CHAT-02） | 全量 F1 0.7885 · 盲测 0.7626 |
| 14 | 登录界面（账号+密码双字段，平台表单要求） | reviewer / 口令 |
| 15 | 前端分包（echarts/core 按需 + 动态 import） | 主 JS 1.32MB → 187KB |
| 16 | HMAC 会话令牌（2h 过期/防篡改/审计日志）+ 最近分析一键重开 | 门禁 15 项巡检全绿 |
| 17 | 静态资源 gzip + immutable 缓存（避开 SSE） | 主 JS 传输 189KB → 60KB |

新环境变量：`REVIEW_USERNAME`（登录账号，默认 reviewer）、`REVIEW_SESSION_TTL`（会话秒数，默认 7200）。
证据链：`docs/iteration/regional_v2/收敛迭代报告.md`、`docs/检索器对照实验.md`、
`docs/通用大模型对照实验.md`、`docs/可用性验证记录.md`、`docs/prompt-log/`。

## ⚖️ 两种运行口径的实测差异（务必按口径引用数字）

同一份 `借款合同样本.docx`，本机实测：

| 口径 | 条款 | 高 | 中 | 低 | 总分 | 分析耗时 |
|---|---|---|---|---|---|---|
| mock 离线（无 Key） | 12 | 5 | 3 | 0 | 33 | **34.9 ms** |
| 真实 LLM（deepseek-chat，并发 8） | 12 | 7 | 2 | 3~4 | 27 | **7.0 ~ 11.0 s** |

差异来源：LLM 覆盖规则未枚举的长尾，额外产出「低」风险；耗时升两个数量级。
且 `temperature=0` 下真实 LLM 仍有跨次波动（保证合同实测出现「高5中6」与「高6中5」），
因此**对外引用请写明口径与区间，不要写单点值**。

---

## ⚡ 10 分钟极速复现（评委必读）

### 环境要求

- Python **3.11+**（推荐 3.11，部分依赖对 3.14 尚未发布 wheel）
- Node.js **18+**
- 包管理：`uv`（推荐）或 `pip`

### 第 1 步：安装后端依赖

```bash
cd contractlens

# 方式 A：uv（推荐）—— uv.lock 已随仓库提交，可精确复现
uv sync --extra dev          # 严格按 uv.lock 安装，版本与作者环境一致
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 方式 B：pip（不读 uv.lock，版本可能漂移）
pip install -e ".[dev]"
```

> **关于 `uv.lock`**：仓库内提交了锁文件（156 个包，含精确版本与哈希），
> 用 `uv sync` 可保证「10 分钟复现」得到与作者完全一致的依赖版本。
> 用 pip 安装则只会按 `pyproject.toml` 的 `>=` 约束解析，**结果可能不同**。
> 若只需最小运行集，用 `uv sync`（不带 `--extra dev`）即可。
>
> 报告 PDF 导出**优先**走 `weasyprint`（需系统 cairo/pango）。装不上时**自动降级**到
> `reportlab` + 内置 CID 中文字体（纯 Python wheel、零系统依赖），PDF 照常可导出。
> `pdfplumber` 用于 PDF 解析，纯文本/文档类流程不依赖它也能跑。

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
npm install       # 已带 package-lock.json，会按锁文件装
npm run dev
# 打开 http://localhost:5173
```

#### 依赖安全说明（npm audit 结果）

前端依赖曾报 3 条公告（echarts 5.5.1 XSS / vite 5.4.10 dev-server 绕过 /
esbuild 0.21.5 开发态任意写），已通过升级全部消除：

| 包 | 旧版本 | 升级后 | 结果 |
|---|---|---|---|
| `echarts` | 5.5.1 | **6.1.0** | XSS 公告消除；图表配置兼容（label 仍来自后端常量） |
| `vite` | 5.4.10 | **6.4.3** | 三条 dev-server 公告消除（fs.deny 绕过 / path traversal / launch-editor） |
| `esbuild` | 0.21.5 | **0.25.12**（随 vite 6） | 开发态任意写公告消除 |

```bash
# 复现审计（注意：npmmirror 镜像未实现 audit 接口，需显式官方源）
cd web && npm audit --registry=https://registry.npmjs.org
# → found 0 vulnerabilities
```

### 第 5 步：上传合同试跑

1. 生成一份样本（可选）：`python scripts/make_sample_docx.py`
2. 浏览器打开前端 → 选合同类型 → 上传 `data/samples/房屋租赁合同样本.docx`
3. 观察流式进度 → 逐条风险卡片 → 仪表盘（评分/雷达）→ 原文↔风险卡联动 → 谈判话术 → 导出报告

### 第 6 步：跑测试（验证）

```bash
pytest -q
# 预期：222 passed（含安全/并发/路由集成回归测试）
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
| GET\|POST | `/api/report/{file_id}?format=md\|revised\|checklist\|pdf\|docx` | 导出报告（前端 5 个按钮是 `<a href>`，必须支持 GET） |
| POST | `/api/chat` | 追问对话 Agent（返回 sources 可追溯引用 + trace 工具轨迹） |
| GET | `/api/health` | 健康检查（显示 LLM 是否启用） |

---

## 核心设计（对应评分维度）

| 官方维度 | 本项目落点 |
|---|---|
| 过程可复现性（25%） | mock 模式零 Key 跑通 + 10 分钟复现 + `docs/prompt-log/`（4 条完整链）+ **30+ 次细粒度提交** |
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
- 内存态存储内置 **TTL 过期清理**，长时间运行内存不无限增长。
- 全局显著标注「不构成法律意见，仅供参考」，不做法律效力判定。
- 不收集用户数据、无账号系统、无云端存储。
- 完整的 Prompt 注入防护、输出枚举校验、上传流式限长等安全设计见 [`docs/安全设计.md`](docs/安全设计.md)。

## 许可

本项目为参赛作品，仅用于展示与学习，不构成法律意见。

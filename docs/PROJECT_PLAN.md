# Hermes Project — 投研自动化系统

## 一、需求概述

构建面向半导体/科技硬件行业的**自动化投研 Pipeline**，从报告获取到投资决策支持的完整闭环。

### 核心需求

1. **报告自动获取**：每天从知识星球定时下载投行 PDF 报告，按行业/公司智能过滤
2. **深度分析**：PDF → OCR/文本提取 → LLM 结构化分析（公司、评级、目标价、营收预测、风险/机会信号）
3. **因果推理**：从"TP 调高到 3000"这种表层结果，下沉到"为什么调？什么业务驱动？数据支撑是什么？产业链传导到谁？"的因果链
4. **跨报告聚合**：多投行对同一公司的观点聚合，识别共识/分歧/孤点，构建证据矩阵
5. **产业链传导**：从单公司 impact 自动推导跨公司传导链（A↑ → B 紧缺 → C 成本上升）
6. **信号追踪**：随时间推移追踪 driver 的生命周期（emerging → consensus → fading），支撑回测
7. **可视化管理**：Web Dashboard 统一查看报告、逻辑链、产业传导图、行业数据库

### 行业覆盖

- AI 芯片（GPU/TPU/ASIC）、HBM/存储、CoWoS/先进封装、Foundry/产能
- 光互连、数据中心/算力、功率半导体/能源

### 公司覆盖

TSMC, MediaTek, NVIDIA, Broadcom, AMD, Intel, Qualcomm, Marvell, Micron, SK Hynix, Samsung, SMIC, GlobalFoundries, Lumentum, Coherent, Fabrinet, Apple, Google, Microsoft, Amazon, Meta, Palantir, CoreWeave 等 30+ 家

---

## 二、系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    Data Sources                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ 知识星球  │  │ Finnhub  │  │ News RSS │               │
│  │ (PDF报告) │  │ (股价)   │  │ (新闻)   │               │
│  └─────┬────┘  └────┬─────┘  └────┬─────┘               │
└────────┼────────────┼─────────────┼──────────────────────┘
         │            │             │
         ▼            ▼             ▼
┌─────────────────────────────────────────────────────────┐
│                   Phase 1: Download                      │
│  zsxq_downloader.py — 每日23:50自动下载                   │
│  · 关键词智能过滤 (强/弱信号分级)                          │
│  · SQLite去重 · 反爬延迟 · 邮件通知                       │
└──────────────────────┬──────────────────────────────────┘
                       │ PDF/PPTX/XLSX
                       ▼
┌─────────────────────────────────────────────────────────┐
│                Phase 2: Deep Analysis                    │
│  pdf_vision_analyzer.py                                  │
│  · PyMuPDF文本提取 → OCR fallback (Tesseract)             │
│  · LLM Scanner (0-20评分) → Deep Analysis (结构化Markdown)│
│  · Anthropic SDK → DeepSeek 兼容端点                      │
│  输出: *_analysis.md + *_analysis.json                   │
└──────────────────────┬──────────────────────────────────┘
                       │ markdown
                       ▼
┌─────────────────────────────────────────────────────────┐
│            Phase 2.5: Logic Extraction (NEW)             │
│  logic_extractor.py                                      │
│  · LLM从markdown提取结构化因果逻辑链                       │
│  · driver + evidence[] + impacts[] + change_from_prior   │
│  · 自校验: 数值一致性、逻辑跳跃检测                        │
│  输出: *_logic.json                                      │
└──────────────────────┬──────────────────────────────────┘
                       │ logic chains
                       ▼
┌─────────────────────────────────────────────────────────┐
│            Phase 3: Group & Parse                        │
│  vision_parser.py + run_pipeline.py                      │
│  · Regex提取: company/ticker/rating/TP/revenue/risks     │
│  · 行业三层标签体系 (赛道/价值链/技术主题)                 │
│  · normalize_company() 公司名归一化                       │
└──────────────────────┬──────────────────────────────────┘
                       │ grouped by company
                       ▼
┌─────────────────────────────────────────────────────────┐
│         Phase 3.5: Logic Aggregation (NEW)               │
│  logic_aggregator.py                                     │
│  · LLM聚类: 同义driver归一化 (中英对照)                    │
│  · 证据矩阵: metric × bank → value                       │
│  · Impact Graph去重合并                                   │
│  · 共识强度: full(≥4) / strong(3) / partial(2) / isolated│
│  · 分歧识别: bull-bear split                             │
│  输出: AGGREGATED_*.md + aggregated_drivers DB            │
└──────────────────────┬──────────────────────────────────┘
                       │ aggregated logic
                       ▼
┌─────────────────────────────────────────────────────────┐
│               Phase 4: Consensus                         │
│  run_pipeline.py (consensus 部分)                        │
│  · 溯源式报告: driver → 证据矩阵 → 传导链 → 分歧 → 风险   │
│  · 输入从原始markdown改为聚合逻辑链 (更高质量)             │
│  输出: CONSENSUS_*.md                                    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│               Cross-Cutting Modules                      │
│                                                          │
│  supply_chain_graph.py — 全局产业传导图                   │
│  · 实体归一化 → 有向图构建 → BFS多跳路径发现              │
│  · 289实体 / 373传导边 / 27家公司                          │
│                                                          │
│  panel.py — 信号生命周期追踪 + 回测骨架                    │
│  · emerging → strengthening → stable → diverging         │
│  · signal_snapshots DB (138条记录/24公司)                 │
│                                                          │
│  industry_report.py — 行业跨公司综合报告                   │
│  industry_data.py — 结构化行业数据库 (HBM/CoWoS/Memory)   │
│  alert_system.py — 分级Alert (TP变化/评级变化/共识)       │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Presentation Layer                          │
│  server.py (Flask) + report_renderer.py                  │
│                                                          │
│  Dashboard Routes:                                       │
│  /                — 统一首页 (统计+导航+公司列表)          │
│  /logic/<company> — 逻辑链溯源报告 (证据矩阵+传导图)       │
│  /company/<name>  — 周报摘要 (要点+报告卡片+PDF链接)       │
│  /supply-chain    — 全局产业传导图                         │
│  /industry        — 行业数据库 (HBM/CoWoS/Memory)         │
│  /settings        — 设置CRUD (公司/行业管理)              │
│  /alerts          — Alert面板                             │
│                                                          │
│  API Routes:                                             │
│  /api/overview, /api/companies, /api/industries          │
│  /api/alerts, /api/logic/<co>, /api/panel/<co>           │
│  /api/supply-chain-graph, /api/supply-chain/<driver>     │
│  /api/settings/*, /api/generate-report, /api/actions/*   │
└─────────────────────────────────────────────────────────┘
```

---

## 三、技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 语言 | Python 3.14 | 全栈 |
| Web 框架 | Flask | Dashboard + REST API |
| LLM | Anthropic SDK → DeepSeek端点 | 分析/提取/聚合 |
| PDF 处理 | PyMuPDF (fitz) | 文本提取 + 渲染 |
| OCR | Tesseract (pytesseract) | 扫描版PDF fallback |
| PPTX/XLSX | python-pptx, openpyxl | 非PDF格式支持 |
| 数据库 | SQLite | 下载记录/逻辑链/行业数据/信号快照 |
| 定时任务 | macOS LaunchAgent (23:50) + Cron (周一08:03) | 自动下载 + Earnings watch |
| 邮件 | smtplib (QQ邮箱SMTP) | 下载结果 + Alert通知 |
| 前端 | 纯HTML/CSS (dark theme) | Dashboard渲染 |
| 数据源 | 知识星球API, Finnhub, RSS | 报告/股价/新闻 |

---

## 四、数据模型

### 核心实体

```
LogicChain (逻辑链)
├── driver: str              # 驱动因素 (e.g. "AI ASIC订单超预期")
├── direction: str           # bullish / bearish / neutral
├── confidence: str          # high / medium / low
├── evidence: [EvidencePoint]  # 数据支撑
│   ├── metric: str          # 指标名
│   ├── value: str           # 数值
│   └── source: str          # 来源
├── impacts: [Impact]        # 产业链传导
│   ├── entity: str          # 受影响实体
│   ├── role: str            # direct/upstream/downstream/competitor
│   └── effect: str          # 影响描述
├── change_from_prior: str   # 与前期变化
└── bank/date/company/ticker # 元数据

AggregatedDriver (聚合驱动)
├── canonical: str           # 归一化driver名
├── consensus_level: str     # full/strong/partial/isolated
├── evidence_matrix: [...]   # metric × bank
├── impact_graph: [...]      # 去重合并后的传导图
├── change_consensus: str    # 综合变化
└── disputes: [...]          # 分歧点
```

### 数据库表

```
zsxq_reports.db (下载记录)
  downloaded_files: file_id, file_name, date, status, industry_match, company_match

logic_chains.db (逻辑链)
  logic_chains: id, report_path, company, bank, date, driver_slug, driver_raw, ...
  evidence_points: id, chain_id, metric, value, source
  impacts: id, chain_id, entity, role, effect
  aggregated_drivers: id, company, driver_slug, consensus_level, aggregated_json

industry_metrics.db (行业数据)
  hbm_capacity, hbm_supply_demand, cowos_capacity, memory_pricing
  signal_snapshots (回测快照)
```

---

## 五、文件清单

```
hermes/
├── run_pipeline.py           # Pipeline 编排器 (Download→Analyze→Extract→Group→Aggregate→Consensus)
├── server.py                 # Flask Dashboard + REST API (~1800行)
├── pdf_vision_analyzer.py    # Phase 2: PDF→LLM 深度分析
├── vision_parser.py          # Regex: Markdown→结构化JSON
├── logic_schema.py           # 数据模型 (dataclass)
├── logic_store.py            # 逻辑链 SQLite CRUD
├── logic_extractor.py        # Phase 2.5: LLM 提取逻辑链
├── logic_aggregator.py       # Phase 3.5: 跨报告聚合
├── supply_chain_graph.py     # 全局产业传导图 (BFS)
├── panel.py                  # 信号面板 + 回测骨架
├── llm_client.py             # [refactor] 共享LLM client
├── utils.py                  # [refactor] 共享工具函数
├── zsxq_downloader.py        # Phase 1: 知识星球下载器
├── config.py                 # 配置 CLI 管理
├── config.json               # 配置 (gitignored)
├── config.example.json       # 配置示例
├── industry_report.py        # 行业跨公司报告
├── industry_data.py          # HBM/CoWoS/Memory结构化数据库
├── industry_db.py            # 通用行业指标提取
├── alert_system.py           # Alert引擎 (email通知)
├── dashboard.py              # 终端Dashboard
├── report_renderer.py        # Markdown→HTML 渲染
├── extract_charts.py         # PDF图表页提取
├── backtest.py               # 批量回填工具
├── maintain.py               # 报告生命周期管理
├── earnings_watcher.py       # Earnings日历
├── data_sources/             # 外部数据源
│   ├── stock_price.py
│   ├── news_feed.py
│   └── earnings.py
├── templates/
│   └── dashboard.html        # (legacy)
└── docs/
    ├── PROJECT_PLAN.md       # 本文档
    └── superpowers/
        ├── specs/            # 设计文档
        └── plans/            # 实现计划
```

---

## 六、部署配置

### 环境变量

```bash
export ANTHROPIC_AUTH_TOKEN=<deepseek-api-key>
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_MODEL=deepseek-v4-pro[1m]
```

### 定时任务

| 任务 | 时间 | 方式 |
|------|------|------|
| 报告自动下载 | 每天 23:50 | macOS LaunchAgent |
| Earnings watch | 每周一 08:03 | Cron |
| 回测快照 | 手动 `python3 panel.py --snapshot` | - |

### 启动

```bash
cd ~/ClaudeCode/hermes
python3 server.py              # Dashboard → http://localhost:8899
python3 run_pipeline.py        # 全量Pipeline
python3 run_pipeline.py --logic-only  # 仅逻辑链提取+聚合+共识
```

---

## 七、成本估算

| 阶段 | Tokens/报告 | 成本/报告 (DeepSeek) |
|------|------------|---------------------|
| Phase 2: Deep Analysis | ~4000 output | ~$0.0011 |
| Phase 2: Scanner | ~1000 output | ~$0.0003 |
| Phase 2.5: Logic Extraction | ~1500 output | ~$0.0004 |
| Phase 3.5: Aggregation (per company) | ~2000 output | ~$0.0006 |
| Phase 4: Consensus (per company) | ~4000 output | ~$0.0011 |
| **总计** | | **~$0.0035/报告** |

按每天 10 份新报告计算：~$0.04/天，~$15/年。

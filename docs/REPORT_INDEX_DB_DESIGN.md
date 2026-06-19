# Report Index Database 设计文档

> `report_index.db` — Hermes 文档索引与搜索系统
> 版本：1.0 | 日期：2026-05-17

---

## 一、定位与架构

```
                          ┌──────────────────────────┐
                          │     report_index.db       │
                          │                          │
   _analysis.json ──────→ │  documents               │ ←── 报告元数据
   _analysis.md ────────→ │  doc_companies (N:M)     │ ←── 报告↔公司
   independent .pages ──→ │  doc_industries (N:M)    │ ←── 报告↔行业
                          │  doc_fts (FTS5)          │ ←── 全文搜索
   config.json ─────────→ │  entity_registry         │ ←── 实体注册表
                          │  entity_aliases          │ ←── 别名/关键词
                          └──────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              /search 页面   /api/search    CLI search.py
```

**与现有 DB 的关系**：

| 数据库 | 用途 | 数据粒度 |
|--------|------|---------|
| `zsxq_reports.db` | 下载记录 | 文件级 |
| `logic_chains.db` | 因果逻辑链 | driver/evidence/impact |
| `industry_metrics.db` | 量化行业数据 | 指标数据点 |
| **`report_index.db`** | **文档索引+搜索** | **报告级元数据+N:M映射** |

`report_index.db` 是 **文档层**，串联起下载、分析、逻辑链各层的数据。搜索命中后可以通过 `doc_id` 关联到 `logic_chains.db` 的详细逻辑链和 `_analysis.md` 的完整分析。

---

## 二、表结构设计

### 2.1 `documents` — 报告主表

```sql
CREATE TABLE documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_name      TEXT NOT NULL,              -- 原始文件名
    pdf_path      TEXT,                       -- 相对 REPORT_BASE 的路径
    source_type   TEXT NOT NULL DEFAULT 'investment_banking',
                  -- 'investment_banking' | 'independent_research' | 'industry_report'
    bank          TEXT,                       -- 投行名 (独立研究时 = 'Independent')
    report_date   TEXT,                       -- YYYY-MM-DD (从文件名提取)
    title         TEXT,                       -- 报告标题
    summary       TEXT,                       -- _analysis.md 前 500 字
    raw_json_path TEXT,                       -- 对应 _analysis.json 路径
    md_path       TEXT,                       -- 对应 _analysis.md 路径
    overview_path TEXT,                       -- 持久化公司报告的路径 (非空表示已生成)
    is_expired    INTEGER DEFAULT 0,          -- 过期标记 (1=已过期)
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**设计要点**：
- `source_type` 区分投行报告、独立研究、AI 生成的行业报告
- `is_expired` 软删除，不过期数据永久保留在 DB 中
- `overview_path` 实现文档→公司报告的 1:1 链接
- 无 UNIQUE 约束，允许同一文件被重新索引（INSERT OR REPLACE 由应用层控制）

### 2.2 `doc_companies` — 报告↔公司 N:M

```sql
CREATE TABLE doc_companies (
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    company_name TEXT NOT NULL,             -- 归一化后的 canonical 名
    ticker       TEXT,                      -- 股票代码
    PRIMARY KEY (doc_id, company_name)
);
CREATE INDEX idx_dc_company ON doc_companies(company_name);
```

**设计要点**：
- N:M 关系：一份报告可涉及多家公司（如 Samsung 报告同时影响 SK Hynix）
- `company_name` 存储 canonical 名，与 `entity_registry.canonical_name` 对应
- CASCADE 删除：文档删除时自动清理关联

### 2.3 `doc_industries` — 报告↔行业 N:M

```sql
CREATE TABLE doc_industries (
    doc_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    industry_slug TEXT NOT NULL,            -- 行业 slug
    layer         TEXT NOT NULL DEFAULT '', -- 'sector' | 'value_chain' | 'tech_theme'
    match_count   INTEGER DEFAULT 1,       -- 关键词匹配次数
    PRIMARY KEY (doc_id, industry_slug, layer)
);
CREATE INDEX idx_di_industry ON doc_industries(industry_slug);
```

**设计要点**：
- `layer` 来自 `_analysis.json` 的三层行业标签体系（赛道/价值链/技术主题）
- 同一份报告可能在同一 layer 下挂多个行业标签
- 搜索过滤时可以按 `industry_slug` 精准定位

### 2.4 `doc_fts` — FTS5 全文搜索

```sql
CREATE VIRTUAL TABLE doc_fts USING fts5(
    doc_id UNINDEXED,       -- 关联键，不参与分词
    title,                  -- 报告标题
    bank,                   -- 投行名
    company,                -- 逗号分隔的公司名 (来自 doc_companies)
    industry_tags,          -- 逗号分隔的行业标签
    content_text,           -- 全文内容 (_analysis.md 前 5000 字)
    tokenize='unicode61'    -- CJK 分词器
);
```

**FTS5 设计决策**：

| 决策 | 选择 | 原因 |
|------|------|------|
| 模式 | Standalone (非 content=) | 文档文本在文件系统中，不在 SQLite 表里 |
| Tokenizer | `unicode61` | SQLite 内置，支持 CJK 单字切分 |
| 内容列 | 前 5000 字 | 平衡搜索覆盖度与 DB 体积 |
| 更新策略 | INSERT OR REPLACE | 每次重新索引时覆盖旧条目 |

**FTS5 搜索语法**：
- `SMIC` → 自动加前缀 `SMIC*`（前缀匹配）
- `"AI ASIC"` → 短语精确匹配
- `SMIC AND 7nm` → 布尔与
- `SMIC OR TSMC` → 布尔或

**CJK 分词说明**：`unicode61` 对中文按单字切分。"中芯国际" 切分为 "中/芯/国/际"。对于精确公司名匹配，这是足够的；对于长文本语义搜索，未来可考虑 jieba 分词 + 自定义 tokenizer。

### 2.5 `entity_registry` — 统一实体注册表

```sql
CREATE TABLE entity_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name  TEXT NOT NULL UNIQUE,   -- 规范化名
    entity_type     TEXT NOT NULL,          -- 'company' | 'industry'
    ticker          TEXT,                   -- 股票代码 (仅 company)
    config_industry TEXT,                   -- 所属行业 (来自 config.json)
    report_path     TEXT,                   -- 持久化报告路径 (公司报告时填写)
    is_active       INTEGER DEFAULT 1,     -- 是否活跃
    report_count    INTEGER DEFAULT 0,     -- 关联报告数 (可重新统计)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**设计要点**：
- **统一管理公司和行业**：所有实体在一个表中，通过 `entity_type` 区分
- **`report_path`** 是实现报告路由的核心字段：Pipeline 通过此字段找到公司持久化报告的路径并更新
- **`report_count`** 定期从 `doc_companies` 重新统计，反映活跃度
- 与 `config.json tracking.companies[]` 和 `tracking.industries[]` 保持同步

### 2.6 `entity_aliases` — 别名表

```sql
CREATE TABLE entity_aliases (
    alias      TEXT NOT NULL,
    entity_id  INTEGER NOT NULL REFERENCES entity_registry(id) ON DELETE CASCADE,
    alias_type TEXT DEFAULT 'search',       -- 'search' | 'keyword' | 'ticker'
    PRIMARY KEY (alias, entity_id)
);
CREATE INDEX idx_ea_alias ON entity_aliases(alias);
```

**别名来源**：

| alias_type | 来源 | 示例 |
|-----------|------|------|
| `keyword` | config.json `keywords[]` | `"smic"`, `"中芯国际"`, `"台积电"` |
| `ticker` | config.json `ticker` | `"2330.TW"`, `"NVDA.US"` |

**用途**：
- 搜索扩展：输入 "台积电" 也能匹配到 TSMC 的文档
- 文件名匹配：`index_independent_research` 通过别名匹配确定报告归属公司

---

## 三、索引流程

### 3.1 投行报告索引 (`index_analysis`)

```
_input: _analysis.json 路径
    │
    ├── 1. 读取 JSON → 提取 parsed.company, parsed.ticker, parsed.industry_tags
    ├── 2. 从文件名提取 bank (utils.extract_bank_from_filename)
    ├── 3. 从文件名提取 report_date (regex: (\d{6}))
    ├── 4. 公司归一化 (run_pipeline.normalize_company)
    ├── 5. 读取 _analysis.md 前 5000 字
    │
    ├── 6. INSERT/UPDATE documents 表
    ├── 7. INSERT OR IGNORE doc_companies
    ├── 8. 遍历 industry_tags (3层) → INSERT OR IGNORE doc_industries
    ├── 9. ensure_company / ensure_industry (entity_registry 惰性创建)
    └── 10. INSERT OR REPLACE doc_fts
```

### 3.2 独立研究索引 (`index_independent_research`)

```
_input: .pages / .md / .txt 文件路径
    │
    ├── .pages → textutil 转 txt (纯图片型降级为元数据)
    ├── .md/.txt → 直接读取
    │
    ├── 关键词匹配公司 (config.json keywords 在文本中匹配)
    ├── 关键词匹配行业 (config.json industry keywords)
    ├── 从 company→industry 映射补全行业标签
    │
    └── 写入 documents + doc_companies + doc_industries + doc_fts
```

---

## 四、搜索流程

```
用户输入 "SMIC 7nm capacity"
    │
    ├── Parse: ["SMIC*", "7nm*", "capacity*"]  (每个词加前缀通配)
    ├── FTS query: "SMIC* AND 7nm* AND capacity*"
    │
    ├── doc_fts MATCH → ranked doc_ids
    │       │
    │       └── JOIN documents → 获取元数据
    │       └── JOIN doc_companies → 获取公司名
    │       └── JOIN doc_industries → 获取行业标签
    │       └── snippet() → 高亮片段
    │
    ├── 聚合计数 (aggs): banks, companies (并发小 JOIN)
    └── 返回 {query, total, results[], aggs{}}
```

**排序**：FTS5 内置 BM25 算法，rank 越小越相关。

**过滤**：支持 `company`, `industry`, `bank`, `source_type` 四个维度的 WHERE 过滤。

---

## 五、过期机制

```
过期判断:
  公司报告: report_date < NOW() - company_report_expire_quarters × 90 天
            (默认 180 天 ≈ 2 个季度)
  行业报告: report_date < NOW() - industry_report_expire_days 天
            (默认 365 天 ≈ 1 年)

过期操作:
  1. mark_expired(): 将 documents.is_expired 设为 1 (软删除)
  2. remove_expired_overviews(): 删除 ai_semiconductor_research/ 中的
     对应 Company_Overview.md (当该公司所有文档均过期时)

调度: Pipeline 每次运行末尾自动执行
CLI:  python3 report_index.py expired --cleanup
```

**设计理念**：
- 文档数据不过期：DB 中的索引记录永久保留，`is_expired=1` 仅影响搜索默认过滤
- 生成文件可过期：`Company_Overview.md` 和 `INDUSTRY_*.md` 是生成内容，过期后删除
- Pipeline 重新运行时自动重新生成最新版本

---

## 六、与其他模块的交互

```
run_pipeline.py:
  Phase 2 后 → index_analysis()           # 自动索引新分析
  Phase 4 后 → 写入 Company_Overview.md    # 更新公司持久化报告
  Phase 6    → mark_expired() + cleanup   # 过期清理

server.py:
  /search          → HTML 搜索页面 + 结果渲染
  /api/search      → JSON 搜索 API
  / (首页)          → 搜索框 + 快捷入口

backfill.py:
  批量回填历史数据

search.py:
  CLI 交互式搜索

industry_report.py:
  行业报告生成后可通过 ReportIndex 索引
```

---

## 七、性能考量

| 场景 | 数据量 | 策略 |
|------|--------|------|
| FTS5 搜索 | 145 文档, ~2.5MB 文本 | 毫秒级，无需优化 |
| 回填索引 | 148 JSON 文件 | 逐份处理，30 秒完成 |
| 聚合计数 | JOIN doc_companies | 有索引，毫秒级 |
| 过期扫描 | 全表扫描 145 行 | 可忽略 |

**扩展预估**：
- 1000 份报告：FTS5 仍可毫秒级响应
- 10000 份报告：需为 `report_date` 和 `bank` 加索引
- 100000+ 份：考虑 FTS5 外部 content 模式 + 定期 vacuum

---

## 八、数据完整性约束

| 约束 | 实现方式 |
|------|---------|
| 文档-公司唯一 | PRIMARY KEY (doc_id, company_name) |
| 文档-行业唯一 | PRIMARY KEY (doc_id, industry_slug, layer) |
| 实体名唯一 | UNIQUE (canonical_name) |
| 别名-实体唯一 | PRIMARY KEY (alias, entity_id) |
| 外键级联删除 | REFERENCES ... ON DELETE CASCADE |
| 公司名归一化 | 统一通过 normalize_company() |

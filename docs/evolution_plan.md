# Hermes 系统进化作战计划

> 基于 700+ 份 IB Report、2960 条逻辑链、1437 个聚合 Driver 的数据基础。
> 三个短期进化方向：反共识信号检测 / 逻辑链回测 / L4 警报闭环。

---

## 0. 数据现状

| 数据资产 | 规模 | 说明 |
|---------|------|------|
| logic_chains | 2,960 条 | 每条含 driver + evidence + impacts |
| evidence_points | 8,274 条 | 每条含 metric + value + source |
| impacts | 6,568 条 | 每条含 entity + role + effect |
| aggregated_drivers | 1,437 个 | 跨投行聚合，含 consensus_level |
| valuations | 53 家公司 | 含 tp_new/tp_old/rating/report_date |
| 报告 | 700 份 analysis.json | 32 个日期目录，643 PDF |

---

## 1. 反共识信号检测

### 现状
`aggregated_drivers` 表已有 `consensus_level` 字段（full/strong/partial/isolated）。
**POC 扫描发现**：1,125 条 isolated 观点，其中 229 条方向与市场主流相反。

### 问题
- AMD 的 "opex overruns" 被 UBS 报告了 7 次，每次 driver 名字略有不同——这是 driver 聚类/去重的问题
- isolated 不一定有价值——有些只是某投行写了篇冷门报告

### 信号增强方案
对每条 isolated 观点计算 **Contrarian Score**：

```
Contrarian Score = Evidence权重 × 方向背离度 × 时效性衰减
```

- **Evidence权重**：该 driver 的 evidence_points 数量 / 平均值（POC 中最高 20 条 evidence）
- **方向背离度**：若 majority=bullish 而 isolated=bearish → ×2.0；若 majority 有 5+ banks 支持 → ×1.5
- **时效性衰减**：7 天内 ×1.0，14 天内 ×0.7，30 天内 ×0.3

### 实现路径
- 新增 `contrarian_signals` 表（或直接往 `aggregated_drivers` 加 `contrarian_score` 字段）
- 在 `logic_aggregator.py` 的 `aggregate()` 完成后计算
- Dashboard 新增 "反共识" tab，按 score 排序展示
- **改动量**：约 200 行代码 + 1 个新 tab

### POC 产出示例
```
AMD: UBS 坚持 bearish "opex overruns" (主流 16 banks 一致 bullish) → Score: 8.2
NVIDIA: Goldman Sachs bearish "DDR5 demand headwind" (主流 14 banks bullish) → Score: 6.7
```

---

## 2. 逻辑链回测

### 现状
`valuations` 表有 tp_new/tp_old，`aggregated_drivers` 有方向/共识。
**POC 发现**：13 家公司同时有两份数据，当前市场下全部 ALIGNED（多头市场+多头观点一致）。

### 核心思路
不是回测"投行说涨它涨没涨"——这是 noisy 的。而是回测：**当 consensus 达到 full/strong 级别时，后续 TP 变动的方向是否一致**。

### 回测指标

| 指标 | 计算方式 |
|------|---------|
| **Consensus 命中率** | full/strong consensus 方向与 30天后 TP 变动方向一致的比例 |
| **Bank 准确率** | 每家投行的 tp_new 在 90 天内是否被市场验证（需股价数据） |
| **Driver 预测力** | 特定 driver 出现后，30天 TP 变动的平均幅度和方向 |
| **分歧价值** | partial/isolated 观点在后续被验证为正确的比例 |

### 短期可做（无需股价数据）
1. **TP 变动 vs Driver 方向一致性**：POC 已验证可行，随数据积累自动改善
2. **Consensus 升级/降级追踪**：某个 driver 从 partial→strong→full 的时间序列，对比同期 TP 变化
3. **Bank 间分歧量化**：同一公司同一 driver 下不同 bank 的 TP 差异（std dev）

### 中期需要
- **真实股价数据**：yfinance 或 Bloomberg API 拉取历史股价
- **回测表**：存储 {driver_slug, date, consensus_level, direction, tp_at_time, price_30d_later, hit}

### 实现路径
- **Phase 1（本周可做）**：新增 `backtest_results` 表，用现有 TP 数据跑第一轮回测
- **Phase 2（需要股价）**：接 yfinance，跑真正的 30/60/90 天回测
- **改动量**：Phase 1 约 150 行，Phase 2 约 300 行 + yfinance 依赖

### POC 产出示例
```
MediaTek: 96 bullish / 23 bearish drivers, Avg TP +50.1%, ALIGNED
  → full consensus "AI ASIC demand surge" 出现后 30 天，TP 中位数 +12%
Lumentum: 仅 1 bullish driver, Avg TP +111%
  → 数据不足，无法评估共识可靠性
```

---

## 3. L4 警报闭环

### 现状
`chokepoint_alert.py` 已能扫描 L4 公司提及。**POC 发现**：6,568 条 impacts 中有 725 条提及 L4 公司（72 家不同 L4 公司被提到）。

### 问题
警报发了，但不知道准不准。比如：
- "NAMICS Underfill 短缺 → SK Hynix HBM 良率下降" 这个链，实际发生了吗？
- 同一条 L4 警报在不同日期重复触发，是噪音还是信号加强？

### 闭环设计

**Step 1：警报持久化**
新增 `chokepoint_alerts` 表：
```sql
CREATE TABLE chokepoint_alerts (
    id INTEGER PRIMARY KEY,
    l4_company TEXT NOT NULL,
    alert_date TEXT NOT NULL,
    source_report TEXT NOT NULL,
    downstream_entities TEXT,  -- JSON array of affected entities
    impact_chain TEXT,          -- JSON: full path L4→L3→L2→L1
    verified INTEGER DEFAULT 0, -- 0=unverified, 1=verified, -1=noise
    verified_date TEXT,
    verified_evidence TEXT
);
```

**Step 2：自动验证**
每次新报告入库时，扫描 impacts 表，检查是否有实体匹配之前警报的 `downstream_entities`。
如果 30 天内 downstream entity 出现了与警报方向一致的 impact → 标记 `verified=1`。

**Step 3：警报质量评分**
```
Alert Quality = verified_count / total_alerts_for_company
```
低质量警报源（总是触发但从不验证）降权或静默。

### 实现路径
- 新增 `chokepoint_alerts` 表 + `chokepoint_alert.py` 更新
- 在 `run_pipeline.py` 阶段 2.5 之后加钩子：新 logic chain → 检查是否验证了历史警报
- Dashboard 新增 "L4 警报追踪" 卡片
- **改动量**：约 250 行 + 1 个新 DB 表

### POC 产出示例
```
2026-05-09: "TSMC CoWoS" 出现在 BofA MediaTek 报告中
  → 影响链: TSMC(CoWoS) → MediaTek(ASIC) → Google(TPU)
  → 30 天内验证: ✅ (5/15 JPM: "MediaTek CoWoS allocation increased")
  → Alert Quality: 3/5 verified (60%)
```

---

## 4. 实施优先级

| 优先级 | 方向 | 改动量 | 价值 | 依赖 |
|--------|------|--------|------|------|
| **P0** | 反共识信号检测 | ~200行 | 高：直接从现有数据提取可交易的信号 | 无 |
| **P1** | L4 警报闭环 | ~250行 + 1表 | 高：让 chokepoint 矩阵从静态变动态 | chokepoint_index.json |
| **P2** | 逻辑链回测 Phase 1 | ~150行 + 1表 | 中：验证系统预测力，但需更多数据 | valuations 表 |

### 建议节奏

**本周**：P0（反共识信号）→ Dashboard 新增 tab，用户可立即看到价值
**下周**：P1（L4 警报闭环）→ 让 chokepoint_alert 不再是一次性扫描
**两周后**：P2（回测 Phase 1）→ 随数据积累逐步完善

---

## 5. 其他发现（POC 过程中暴露的问题）

### 5.1 Driver 去重问题
AMD "opex overruns" 出现 7 个不同 driver_slug（`opex-overruns`, `opex-overrun-persistent`, `persistent-opex-overruns`...），说明 `logic_aggregator.py` 的 LLM 聚类不够激进。建议增加聚类 prompt 中的合并粒度，或加 post-processing 的 Levenshtein 距离去重。

### 5.2 Bank 名称归一化
`valuations` 表中同一投行有多个名称变体（"BofA Securities" vs "BofA Secur", "Morgan Stanley" vs "Morgan Sta"）。建议在 `valuation_store.py` 的 `upsert_from_analysis()` 中统一 normalize。

### 5.3 数据密度不均
Lumentum 只有 1 条 driver，但 TP 变动 +111%——数据太稀疏无法评估。13 家重叠公司中，MediaTek(119 drivers)、NVIDIA(80+)、Broadcom(32) 数据最丰富，应优先对这些公司做深度回测。

---

## 6. 长期方向展望

短期三个方向跑通后，自然的下一步：

1. **时序传导模型**：给 LogicChain 加 `time_horizon` 字段，让 supply_chain_graph 的 BFS 能估算"影响到达时间"
2. **知识图谱自动补全**：用 LLM 从 700 份报告中提取隐式供应商关系，自动扩展 L4 矩阵
3. **Agentic Research**：LLM 主动提出假设 → 从已有数据中找证据 → 生成研究备忘录

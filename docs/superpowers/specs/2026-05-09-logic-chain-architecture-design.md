# Hermes Logic Chain Architecture — Design Spec

**Goal:** Replace shallow "TP change" extraction with structured causal logic chains that trace from
rating changes → business drivers → evidence → supply chain impacts, enabling systematic
cross-report reasoning.

**Architecture:** Four new modules (logic_schema, logic_extractor, logic_aggregator, logic_store)
insert between the existing Analyze and Consensus phases. Prompt enhancements to Analyze and
Consensus. New API routes and report rendering mode.

**Tech Stack:** Python 3, dataclasses, SQLite, Anthropic SDK (DeepSeek), Flask

---

## Pipeline Change

```
Phase 1:  Download              (unchanged)
Phase 2:  Analyze               (enhanced prompt — add logic derivation section)
Phase 2.5: Logic Extraction      (NEW — LLM extracts structured logic chains from markdown)
Phase 3:  Group by company       (unchanged)
Phase 3.5: Logic Aggregation     (NEW — cluster drivers, build evidence matrix, map impacts)
Phase 4:  Consensus              (rewritten — takes aggregated logic as input)
```

## New Files

| File | Responsibility |
|------|---------------|
| `logic_schema.py` | Dataclass definitions for LogicChain, EvidencePoint, Impact, AggregatedDriver |
| `logic_extractor.py` | Phase 2.5: LLM extracts logic chains from `*_analysis.md` → `*_logic.json` |
| `logic_aggregator.py` | Phase 3.5: Cluster drivers across reports, build evidence matrix + impact graph |
| `logic_store.py` | SQLite tables (logic_chains, evidence_points, impacts, aggregated_drivers) + CRUD |

## Modified Files

| File | Change |
|------|--------|
| `pdf_vision_analyzer.py` | `SYSTEM_PROMPT_DEEP` gains "## 逻辑推导链" section requirement |
| `run_pipeline.py` | Add Phase 2.5, Phase 3.5; rewrite Phase 4 to use aggregated logic |
| `server.py` | New routes: `/api/logic/<company>`, `/api/supply-chain/<driver>`, `/logic/<company>` |
| `report_renderer.py` | New render mode for logic-chain trace reports |

## Data Model

```python
@dataclass
class EvidencePoint:
    metric: str     # "Q1 ASIC出货量 YoY"
    value: str      # "+45%"
    source: str     # "MediaTek Q1法说会"

@dataclass
class Impact:
    entity: str     # "TSMC"
    role: str       # upstream | downstream | competitor | direct
    effect: str     # "CoWoS产能紧缺加剧"

@dataclass
class LogicChain:
    driver: str              # "AI ASIC 订单超预期"
    direction: str           # bullish | bearish | neutral
    confidence: str          # high | medium | low
    evidence: list[EvidencePoint]
    impacts: list[Impact]
    change_from_prior: str   # relative to previous report
    bank: str
    date: str
    company: str
    ticker: str

@dataclass
class AggregatedDriver:
    canonical: str           # normalized driver name
    consensus_level: str     # full(≥4) | strong(3) | partial(2) | isolated(1)
    banks: list[str]
    evidence_matrix: dict    # metric → {bank: value}
    impact_graph: list[Impact]
    change_consensus: str
    disputes: list[dict]
```

## LLM Prompts

### Logic Extraction (Phase 2.5)
System prompt instructs LLM to extract logic chains as JSON arrays.
Each chain must cite specific evidence from the report text.
Self-validation pass checks numerical consistency.

### Consensus (Phase 4 rewrite)
Takes `aggregated_logic.json` as input instead of raw markdown.
Outputs trace-style report: drivers ordered by consensus strength →
evidence matrix table → supply chain impact diagram → disputes → risk of weak evidence.

## Presentation Layer

- `/api/logic/<company>` — JSON endpoint for aggregated logic chains
- `/api/supply-chain/<driver>` — supply chain impact trace by driver
- `/logic/<company>` — interactive HTML: driver list | evidence matrix | impact graph
- `report_renderer.py --type logic-chain` — full trace report rendering

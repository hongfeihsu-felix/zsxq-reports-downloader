#!/usr/bin/env python3
"""
Logic chain data models for the Hermes causal reasoning system.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class EvidencePoint:
    """A single data point supporting a logic chain."""
    metric: str             # e.g. "Q1 ASIC出货量 YoY"
    value: str              # e.g. "+45%"
    source: str             # e.g. "MediaTek Q1法说会"

    def to_dict(self):
        return asdict(self)


@dataclass
class Impact:
    """Supply chain impact from a logic chain."""
    entity: str             # e.g. "TSMC"
    role: str               # direct | upstream | downstream | competitor
    effect: str             # e.g. "CoWoS产能紧缺加剧"

    def to_dict(self):
        return asdict(self)


@dataclass
class LogicChain:
    """A single causal logic chain extracted from one analyst report."""
    driver: str                         # e.g. "AI ASIC 订单超预期"
    direction: str                      # bullish | bearish | neutral
    confidence: str                     # high | medium | low
    evidence: list[dict] = field(default_factory=list)    # list of EvidencePoint dicts
    impacts: list[dict] = field(default_factory=list)     # list of Impact dicts
    change_from_prior: str = ""         # relative to previous report
    prior_reference: str = ""           # referenced prior report date/content
    bank: str = ""
    date: str = ""
    company: str = ""
    ticker: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class AggregatedDriver:
    """Cross-report aggregation of logic chains around one canonical driver."""
    canonical: str                      # normalized driver name
    slug: str                           # URL-safe slug
    consensus_level: str                # full(≥4) | strong(3) | partial(2) | isolated(1)
    banks: list[str] = field(default_factory=list)
    company: str = ""
    report_count: int = 0
    direction: str = ""                 # dominant direction
    evidence_matrix: list[dict] = field(default_factory=list)
    # evidence_matrix: [{"metric": "...", "GS": "+45%", "MS": "+42%", ...}, ...]
    impact_graph: list[dict] = field(default_factory=list)
    # impact_graph: [{"entity": "...", "role": "...", "effect": "...", "banks": [...]}, ...]
    change_consensus: str = ""
    disputes: list[dict] = field(default_factory=list)
    # disputes: [{"topic": "...", "bull": "...", "bear": "..."}, ...]

    def to_dict(self):
        return asdict(self)

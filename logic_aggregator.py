#!/usr/bin/env python3
"""
Phase 3.5: Logic Chain Aggregator

跨报告聚合逻辑链：
  - 同义 driver 识别 + 归一化 (LLM fuzzy match)
  - 构建 evidence_matrix (metric × bank)
  - 构建 impact_graph  (去重合并所有 impacts)
  - 计算共识强度 (full/strong/partial/isolated)
  - 识别分歧和孤点信号

用法：
  python3 logic_aggregator.py --company MediaTek
  python3 logic_aggregator.py --all
"""

import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional

from llm_client import call_llm
from logic_schema import LogicChain, AggregatedDriver
from logic_store import (save_aggregated_driver, load_aggregated_drivers,
                          getAllLogicChainsForCompany, query_by_company)

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"


# Prompt for clustering similar drivers across reports
CLUSTER_PROMPT = """你是一位投研数据分析师。下方是多家投行对同一家公司提出的驱动因素列表。
请将语义相同的驱动归一化为 canonical name，然后按 canonical 分组。

规则（按优先级）：
1. 激进合并：同一主题的细微变体必须合并。例如 "TPU竞争风险"/"TPU竞争与执行风险"/"TPU竞争与份额风险"/"TPU竞争格局与份额风险" 这四个本质相同，归入 "TPU设计竞争与份额风险"
2. 中英文同义的归一化到中文
3. 同类但不同颗粒度的合并到更宽泛的 canonical
4. 确实不同主题的驱动才分开
5. 每个 canonical 应该是简短、可读的中文短语 (≤20字)
6. 输出 JSON：{"clusters": [{"canonical": "...", "items": ["driver1", "driver2"], "slug": "..."}]}

输入驱动列表：
{drivers_text}

只输出 JSON，不要解释。"""


def load_logic_chains_for_company(company: str) -> list[dict]:
    """Load all logic chains for a company from JSON files + database.
    Uses config.json aliases for fuzzy matching (handles AMD→Advanced Micro Devices etc)."""
    chains = []

    # Build candidate db names: original + aliases from config.json
    candidate_names = {company}
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        for c in cfg.get("tracking", {}).get("companies", []):
            canonical = c["name"]
            if canonical.lower() == company.lower():
                # Add all keywords + ticker as candidate db names
                for kw in c.get("keywords", []):
                    candidate_names.add(kw)
                ticker = c.get("ticker", "").split(".")[0]
                if ticker:
                    candidate_names.add(ticker)

    # Try database
    try:
        from logic_store import get_conn
        conn = get_conn()
        all_db_names = [r[0] for r in
                        conn.execute("SELECT DISTINCT company FROM logic_chains").fetchall()]
        conn.close()

        for db_name in all_db_names:
            db_lower = db_name.lower()
            # Match if any candidate appears as substring in db_name
            for cand in candidate_names:
                if cand.lower() in db_lower:
                    db_chains = getAllLogicChainsForCompany(db_name)
                    if db_chains:
                        return db_chains
    except Exception:
        pass

    # Fall back to JSON files
    for f in sorted(REPORT_BASE.rglob("*_logic.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for item in (data if isinstance(data, list) else [data]):
                item_company = item.get("company", "")
                for cand in candidate_names:
                    if cand.lower() in item_company.lower():
                        chains.append(item)
                        break
        except (json.JSONDecodeError, KeyError):
            continue
    return chains


def _cluster_drivers(chains: list[dict]) -> list[dict]:
    """Use LLM to cluster semantically similar drivers."""
    if not chains:
        return []

    # Build driver list
    drivers = []
    seen = set()
    for c in chains:
        driver = c.get("driver", "")
        if driver and driver not in seen:
            seen.add(driver)
            drivers.append(driver)

    if len(drivers) <= 1:
        # Single driver, no clustering needed
        slug = re.sub(r'[^\w]+', '_', drivers[0].lower())[:60] if drivers else "unknown"
        return [{"canonical": drivers[0], "items": drivers, "slug": slug}]

    drivers_text = "\n".join(f"- {d}" for d in drivers)
    prompt = CLUSTER_PROMPT.replace("{drivers_text}", drivers_text)

    try:
        raw_json, _ = call_llm(prompt, "Cluster these drivers.", max_tokens=2048)
        # Parse JSON
        json_match = re.search(r'\{[\s\S]*\}', raw_json)
        if json_match:
            result = json.loads(json_match.group(0))
            return result.get("clusters", [])
    except Exception as e:
        print(f"  ⚠️  Clustering failed: {e}")

    # Fallback: each driver is its own cluster
    return [{"canonical": d, "items": [d],
             "slug": re.sub(r'[^\w]+', '_', d.lower())[:60]} for d in drivers]


def aggregate(company: str, chains: list[dict] = None,
              max_chains: int = 50) -> list[AggregatedDriver]:
    """Aggregate logic chains for a company across all reports."""
    if chains is None:
        chains = load_logic_chains_for_company(company)

    if not chains:
        print(f"  No logic chains found for {company}")
        return []

    # Limit for token efficiency
    chains = chains[:max_chains]

    # Step 1: Cluster drivers
    clusters = _cluster_drivers(chains)
    if not clusters:
        return []

    # Step 2: For each cluster, build evidence matrix + impact graph
    results = []
    for cluster in clusters:
        canonical = cluster["canonical"]
        slug = cluster.get("slug", re.sub(r'[^\w]+', '_', canonical.lower())[:60])
        driver_items = set(cluster.get("items", []))

        # Collect all chains matching this cluster
        cluster_chains = [c for c in chains if c.get("driver", "") in driver_items]

        if not cluster_chains:
            continue

        banks = sorted(set(c.get("bank", "?") for c in cluster_chains if c.get("bank")))
        directions = [c.get("direction", "neutral") for c in cluster_chains]
        dominant_dir = max(set(directions), key=directions.count) if directions else "neutral"

        # Consensus level
        n_banks = len(banks)
        if n_banks >= 4:
            consensus_level = "full"
        elif n_banks >= 3:
            consensus_level = "strong"
        elif n_banks >= 2:
            consensus_level = "partial"
        else:
            consensus_level = "isolated"

        # Build evidence matrix: metric → {bank: value}
        evidence_by_metric = defaultdict(dict)
        for c in cluster_chains:
            bank = c.get("bank", "?")
            for ev in c.get("evidence", []):
                metric = ev.get("metric", "")
                value = ev.get("value", "")
                if metric:
                    evidence_by_metric[metric][bank] = value

        evidence_matrix = []
        for metric, bank_values in evidence_by_metric.items():
            row = {"metric": metric}
            row.update(bank_values)
            evidence_matrix.append(row)

        # Build impact graph: entity → merged by role
        impact_by_entity = defaultdict(lambda: {"roles": set(), "effects": set(),
                                                 "banks": set()})
        for c in cluster_chains:
            bank = c.get("bank", "?")
            for imp in c.get("impacts", []):
                entity = imp.get("entity", "")
                if not entity:
                    continue
                entry = impact_by_entity[entity]
                entry["roles"].add(imp.get("role", "direct"))
                entry["effects"].add(imp.get("effect", ""))
                entry["banks"].add(bank)

        impact_graph = []
        for entity, data in impact_by_entity.items():
            impact_graph.append({
                "entity": entity,
                "role": "/".join(sorted(data["roles"])),
                "effect": "; ".join(data["effects"]),
                "banks": sorted(data["banks"])
            })

        # Change consensus
        changes = [c.get("change_from_prior", "") for c in cluster_chains
                   if c.get("change_from_prior")]
        change_consensus = "; ".join(sorted(set(changes)))[:300] if changes else ""

        # Detect disputes (opposite directions in same cluster)
        disputes = []
        bullish_banks = [c.get("bank", "?") for c in cluster_chains
                         if c.get("direction") == "bullish"]
        bearish_banks = [c.get("bank", "?") for c in cluster_chains
                         if c.get("direction") == "bearish"]
        if bullish_banks and bearish_banks:
            disputes.append({
                "topic": f"{canonical} — 方向分歧",
                "bull": f"{', '.join(bullish_banks)}: 看多",
                "bear": f"{', '.join(bearish_banks)}: 看空"
            })

        ad = AggregatedDriver(
            canonical=canonical,
            slug=slug,
            consensus_level=consensus_level,
            banks=banks,
            company=company,
            report_count=len(cluster_chains),
            direction=dominant_dir,
            evidence_matrix=evidence_matrix,
            impact_graph=impact_graph,
            change_consensus=change_consensus,
            disputes=disputes
        )
        results.append(ad)

        # Persist to database
        try:
            save_aggregated_driver(ad)
        except Exception:
            pass

    # Sort by consensus strength
    level_order = {"full": 0, "strong": 1, "partial": 2, "isolated": 3}
    results.sort(key=lambda x: (level_order.get(x.consensus_level, 99), -x.report_count))

    return results


def aggregate_all(report_dir: str = None) -> dict[str, list[AggregatedDriver]]:
    """Aggregate for all companies with logic chains."""
    from collections import defaultdict

    # Find all companies from logic JSON files
    companies = set()
    search_dir = Path(report_dir) if report_dir else REPORT_BASE
    for f in sorted(search_dir.rglob("*_logic.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for item in (data if isinstance(data, list) else [data]):
                company = item.get("company", "")
                if company:
                    companies.add(company)
        except Exception:
            continue

    if not companies:
        print("No logic chains found. Run Phase 2.5 first.")
        return {}

    print(f"\n🔗 Phase 3.5: Logic Aggregation ({len(companies)} companies)\n")

    results = {}
    for company in sorted(companies):
        print(f"  📊 {company}...")
        try:
            drivers = aggregate(company)
            results[company] = drivers
            print(f"     {len(drivers)} aggregated drivers ("
                  f"{sum(1 for d in drivers if d.consensus_level in ('full', 'strong'))} strong consensus)")
        except Exception as e:
            print(f"     ❌ {e}")

    return results


def format_aggregated_markdown(company: str, drivers: list[AggregatedDriver]) -> str:
    """Render aggregated logic as markdown for consensus input."""
    if not drivers:
        return f"# {company} — No logic chains available"

    sections = [f"## {company} — 聚合逻辑链 ({len(drivers)} 驱动因素)\n"]

    for i, ad in enumerate(drivers, 1):
        consensus_emoji = {"full": "✅✅", "strong": "✅", "partial": "⚡", "isolated": "🔍"}
        emoji = consensus_emoji.get(ad.consensus_level, "❓")
        direction_arrow = {"bullish": "🟢↑", "bearish": "🔴↓", "neutral": "🟡→"}

        sections.append(f"### {i}. {ad.canonical} {direction_arrow.get(ad.direction, '')} {emoji}")
        sections.append(f"**共识强度:** {ad.consensus_level} | **投行:** {', '.join(ad.banks)}")
        sections.append(f"**报告数:** {ad.report_count}")

        if ad.evidence_matrix:
            sections.append("\n**证据矩阵:**\n")
            banks = sorted(set(b for row in ad.evidence_matrix for b in row if b != "metric"))
            header = "| Metric | " + " | ".join(banks) + " |"
            sep = "|" + "|".join(["------"] * (len(banks) + 1)) + "|"
            sections.append(header)
            sections.append(sep)
            for row in ad.evidence_matrix:
                cells = [row.get("metric", "")]
                for b in banks:
                    cells.append(row.get(b, "—"))
                sections.append("| " + " | ".join(cells) + " |")

        if ad.impact_graph:
            sections.append("\n**产业链传导:**\n")
            for imp in ad.impact_graph:
                banks_str = f" ({', '.join(imp['banks'])})" if imp.get('banks') else ""
                sections.append(f"- **{imp['entity']}** [{imp['role']}]: {imp['effect']}{banks_str}")

        if ad.change_consensus:
            sections.append(f"\n**与前期变化:** {ad.change_consensus}")

        if ad.disputes:
            sections.append("\n**分歧:**\n")
            for d in ad.disputes:
                sections.append(f"- **{d['topic']}**")
                sections.append(f"  - 🐂 {d['bull']}")
                sections.append(f"  - 🐻 {d['bear']}")

        sections.append("")

    return "\n".join(sections)


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Logic Chain Aggregator")
    parser.add_argument("--company", help="Company name to aggregate")
    parser.add_argument("--all", action="store_true", help="Aggregate all companies")
    parser.add_argument("--output", help="Output markdown file for consensus input")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.all:
        results = aggregate_all()
        for company, drivers in results.items():
            md = format_aggregated_markdown(company, drivers)
            out_path = REPORT_BASE / f"AGGREGATED_{company}_{datetime.now().strftime('%Y%m%d')}.md"
            out_path.write_text(md, encoding="utf-8")
            print(f"     📄 {out_path}")
    elif args.company:
        drivers = aggregate(args.company)
        if args.json:
            print(json.dumps([d.to_dict() for d in drivers], ensure_ascii=False, indent=2))
        else:
            md = format_aggregated_markdown(args.company, drivers)
            print(md[:2000])
            if args.output:
                Path(args.output).write_text(md, encoding="utf-8")
                print(f"\n📄 Saved to {args.output}")
    else:
        parser.print_help()

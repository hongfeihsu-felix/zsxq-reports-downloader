#!/usr/bin/env python3
"""
Supply Chain Propagation Graph — 跨公司全局产业传导图

从所有 *_logic.json 中收集 impact 关系，归一化实体，构建有向图，
自动发现链式传导路径。

用法：
  python3 supply_chain_graph.py                    # 全局传导图
  python3 supply_chain_graph.py --entity TSMC       # TSMC 相关传导链
  python3 supply_chain_graph.py --depth 4            # 传导深度
  python3 supply_chain_graph.py --json               # JSON 输出
"""

import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional

REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"


# ============ Entity Normalizer ============

# Manual normalization map (报告中的变体 → canonical entity)
ENTITY_NORMALIZE = {
    # 公司名中英对照
    "联发科": "MediaTek",
    "博通": "Broadcom",
    "台积电": "TSMC",
    "谷歌": "Google",
    "苹果": "Apple",
    "中国安卓oem": "Android OEMs",
    # Entity variants
    "mediatek inc.": "MediaTek",
    "mediatek inc": "MediaTek",
    "google (tpu)": "Google",
    "google tpu business": "Google",
    "google tpu v9": "Google",
    "google tpu v8t": "Google",
    "google gemini": "Google",
    "google pixel": "Google",
    "tsmc/cowos supply chain": "TSMC",
    "other asic competitors (broadcom, marvell)": "Broadcom/Marvell",
    "ai asic competitors": "AI ASIC Competitors",
    "ai asic design service competitors": "AI ASIC Competitors",
    "smartphone soc competitors": "Smartphone SoC Competitors",
    "chinese smartphone soc vendors": "Android OEMs",
    "hyperscale csps": "Hyperscalers",
    "hyperscalers": "Hyperscalers",
    "smartphone oems": "Smartphone OEMs",
    "smartphone market": "Smartphone Market",
    "memory suppliers": "Memory Suppliers",
    "nvidia gpus": "NVIDIA",
    "ai asic segment": "AI ASIC",
    "ai asic end market": "AI ASIC",
    "ai asic customers": "AI ASIC Customers",
    "edge devices (mobile, auto, iot, pc)": "Edge Devices",
    "data center optical interconnect market": "Optical Interconnect Market",
    "intel (emib-t packaging)": "Intel",
    "samsung": "Samsung",
    "qualcomm": "Qualcomm",
}

_company_aliases = None


def _load_company_aliases() -> dict[str, str]:
    """Build alias→canonical from config.json."""
    global _company_aliases
    if _company_aliases is not None:
        return _company_aliases

    mapping = {}
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        for c in cfg.get("tracking", {}).get("companies", []):
            canonical = c["name"]
            for kw in c.get("keywords", []):
                mapping[kw.lower()] = canonical
            mapping[c["name"].lower()] = canonical
            ticker = c.get("ticker", "").split(".")[0].strip()
            if ticker and len(ticker) > 2:
                mapping[ticker.lower()] = canonical
    _company_aliases = mapping
    return mapping


def normalize_entity(name: str) -> str:
    """Normalize entity name to canonical form."""
    if not name:
        return "Unknown"
    name = name.strip()

    # 1. Direct manual mapping
    key = name.lower().rstrip(".")
    if key in ENTITY_NORMALIZE:
        return ENTITY_NORMALIZE[key]

    # 2. Company alias matching (config.json)
    aliases = _load_company_aliases()
    if key in aliases:
        return aliases[key]

    # 3. Substring company match (for names containing company aliases)
    for alias, canonical in aliases.items():
        if len(alias) > 3 and alias in key:
            return canonical

    # 4. Return cleaned original
    # Remove corporate suffixes, normalize spacing
    cleaned = re.sub(r'\b(inc\.?|corp\.?|ltd\.?|co\.?|plc)\b', '', name, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else name.strip()


# ============ Impact Graph ============

class SupplyChainGraph:
    """全局产业链有向图"""

    def __init__(self):
        self.edges: dict[str, list[dict]] = defaultdict(list)
        # edge: {target, role, effect, driver, bank, date, company}
        self.companies_loaded: set[str] = set()

    GARBAGE_ENTITIES = {"unknown", "in this report", "this report", "median multiples",
                         "none", "n/a", "na", "?", ""}

    def load_all(self):
        """Load all impact relations from all *_logic.json files."""
        for logic_file in sorted(REPORT_BASE.rglob("*_logic.json")):
            try:
                data = json.loads(logic_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                continue

            chains = data if isinstance(data, list) else [data]
            for chain in chains:
                company = chain.get("company", "")
                if company:
                    self.companies_loaded.add(company)

                source_entity = normalize_entity(company) if company else "Unknown"
                if source_entity.lower().strip() in self.GARBAGE_ENTITIES:
                    continue
                driver = chain.get("driver", "")[:80]
                bank = chain.get("bank", "")
                date = chain.get("date", "")

                for imp in chain.get("impacts", []):
                    target = normalize_entity(imp.get("entity", ""))
                    if not target or target == source_entity:
                        continue
                    if target.lower().strip() in self.GARBAGE_ENTITIES:
                        continue
                        continue
                    role = imp.get("role", "direct")
                    effect = imp.get("effect", "")

                    # Add edge: source → target
                    self.edges[source_entity].append({
                        "target": target,
                        "role": role,
                        "effect": effect,
                        "driver": driver,
                        "bank": bank,
                        "date": date,
                        "company": company
                    })

        # Deduplicate edges (same source→target with same role)
        for src in self.edges:
            seen = set()
            unique = []
            for e in self.edges[src]:
                key = (e["target"], e["role"])
                if key not in seen:
                    seen.add(key)
                    unique.append(e)
            self.edges[src] = unique

        return self

    def get_edges_for(self, entity: str) -> list[dict]:
        """Get all outgoing edges from an entity."""
        entity = normalize_entity(entity)
        return self.edges.get(entity, [])

    def get_incoming_to(self, entity: str) -> list[tuple[str, dict]]:
        """Get all incoming edges to an entity. Returns [(source, edge), ...]"""
        entity = normalize_entity(entity)
        results = []
        for src, edges in self.edges.items():
            for e in edges:
                if e["target"] == entity:
                    results.append((src, e))
        return results

    def find_paths(self, start_entity: str, max_depth: int = 4) -> list[dict]:
        """BFS to find all propagation paths from start entity up to max_depth."""
        start = normalize_entity(start_entity)
        paths = []
        visited_edges = set()

        # BFS queue: (current_path of edges, current_entity)
        queue = [([], start)]

        for depth in range(max_depth):
            if not queue:
                break
            next_queue = []
            for path, entity in queue:
                for edge in self.edges.get(entity, []):
                    edge_key = (entity, edge["target"], edge["role"])
                    if edge_key in visited_edges:
                        continue
                    visited_edges.add(edge_key)
                    new_path = path + [{"from": entity, **edge}]
                    paths.append(new_path)
                    next_queue.append((new_path, edge["target"]))
            queue = next_queue

        # Sort by path length then number of unique companies
        paths.sort(key=lambda p: (len(p), -len(set(e.get("bank", "") for e in p))))
        return paths

    def find_connected_to(self, entity: str, max_depth: int = 3) -> dict:
        """Find all entities connected to the given entity (upstream + downstream)."""
        entity = normalize_entity(entity)
        connected = {"upstream": [], "downstream": [], "same_layer": []}

        # Downstream: entity → X
        for edge in self.edges.get(entity, []):
            connected["downstream"].append(edge)

        # Upstream: X → entity
        for src, edge in self.get_incoming_to(entity):
            connected["upstream"].append({"from": src, **edge})

        return connected

    def summary(self) -> dict:
        """Graph summary statistics."""
        all_entities = set(self.edges.keys())
        for edges in self.edges.values():
            for e in edges:
                all_entities.add(e["target"])

        # Count roles
        role_counts = defaultdict(int)
        for edges in self.edges.values():
            for e in edges:
                role_counts[e["role"]] += 1

        # Most connected entities
        entity_degree = {}
        for entity in all_entities:
            out_degree = len(self.edges.get(entity, []))
            in_degree = len(self.get_incoming_to(entity))
            entity_degree[entity] = out_degree + in_degree

        top_entities = sorted(entity_degree.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_entities": len(all_entities),
            "total_edges": sum(len(v) for v in self.edges.values()),
            "companies_loaded": sorted(self.companies_loaded),
            "role_distribution": dict(role_counts),
            "most_connected": top_entities[:15]
        }


# ============ Renderers ============

def render_graph_markdown(graph: SupplyChainGraph, focus_entity: str = "",
                           max_depth: int = 3) -> str:
    """Render supply chain graph as markdown."""
    summary = graph.summary()

    lines = [
        f"# 全局产业传导图",
        f"**{summary['total_entities']}** 个实体 · **{summary['total_edges']}** 条传导边",
        f"覆盖公司: {', '.join(summary['companies_loaded'][:12])}",
        "",
        "---",
        "",
    ]

    if focus_entity:
        entity = normalize_entity(focus_entity)
        lines.append(f"## 聚焦: {entity}")
        lines.append("")

        paths = graph.find_paths(entity, max_depth=max_depth)
        if not paths:
            lines.append(f"*No propagation paths found for {entity}*")
        else:
            lines.append(f"### 传导路径 ({len(paths)} paths found)")
            lines.append("")

            for i, path in enumerate(paths[:20], 1):
                # Path as chain: A →[role]→ B →[role]→ C
                chain_parts = []
                banks = set()
                for step in path:
                    chain_parts.append(
                        f"**{step['from']}** →[{step['role']}]→ **{step['target']}**"
                    )
                    if step.get("bank"):
                        banks.add(step["bank"])

                chain_str = " → ".join(chain_parts)
                lines.append(f"{i}. {chain_str}")
                lines.append(f"   Banks: {', '.join(sorted(banks)[:5])}")

                # Show key effects
                for step in path[:2]:
                    effect = step.get("effect", "")[:100]
                    if effect:
                        lines.append(f"   - {effect}")
                lines.append("")

    # Top connected entities
    lines.append("## 核心传导节点")
    lines.append("")
    lines.append("| Entity | Degree | Upstream | Downstream |")
    lines.append("|--------|--------|----------|------------|")
    for entity, degree in summary["most_connected"][:20]:
        outgoing = graph.get_edges_for(entity)
        incoming = graph.get_incoming_to(entity)
        upstream_str = ", ".join(sorted(set(e[0] for e in incoming))[:3])
        downstream_str = ", ".join(sorted(set(e["target"] for e in outgoing))[:3])
        lines.append(f"| {entity} | {degree} | {upstream_str} | {downstream_str} |")

    # Role distribution
    lines.append("")
    lines.append("## 传导角色分布")
    lines.append("")
    for role, count in sorted(summary["role_distribution"].items(),
                              key=lambda x: x[1], reverse=True):
        lines.append(f"- **{role}**: {count} edges")

    return "\n".join(lines)


def render_graph_json(graph: SupplyChainGraph) -> dict:
    """Export graph as JSON for API/web consumption."""
    summary = graph.summary()
    nodes = []
    links = []

    entity_ids = {}
    for entity_name in set(list(graph.edges.keys()) +
                           [e["target"] for edges in graph.edges.values() for e in edges]):
        if entity_name not in entity_ids:
            eid = len(entity_ids)
            entity_ids[entity_name] = eid
            degree = (len(graph.edges.get(entity_name, [])) +
                      len(graph.get_incoming_to(entity_name)))
            nodes.append({
                "id": eid,
                "name": entity_name,
                "degree": degree
            })

    for src, edges in graph.edges.items():
        for e in edges:
            links.append({
                "source": entity_ids[src],
                "target": entity_ids[e["target"]],
                "role": e["role"],
                "effect": e.get("effect", "")[:100],
                "banks": list(set(edge.get("bank", "") for edge in [e]))
            })

    return {"summary": summary, "nodes": nodes, "links": links}


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supply Chain Propagation Graph")
    parser.add_argument("--entity", help="Focus on a specific entity")
    parser.add_argument("--depth", type=int, default=3, help="Propagation depth (default: 3)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--output", help="Save markdown to file")

    args = parser.parse_args()

    print("Building supply chain graph...")
    graph = SupplyChainGraph().load_all()
    summary = graph.summary()
    print(f"  {summary['total_entities']} entities, {summary['total_edges']} edges "
          f"from {len(summary['companies_loaded'])} companies\n")

    if args.json:
        data = render_graph_json(graph)
        if args.entity:
            entity = normalize_entity(args.entity)
            data["paths"] = graph.find_paths(entity, max_depth=args.depth)
            data["connected"] = graph.find_connected_to(entity)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        md = render_graph_markdown(graph, focus_entity=args.entity or "",
                                    max_depth=args.depth)
        print(md[:3000])
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"\n📄 Saved to {args.output}")

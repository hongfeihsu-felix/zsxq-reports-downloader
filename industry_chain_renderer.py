#!/usr/bin/env python3
"""
AI Industry Chain Matrix Renderer — 产业链矩阵可视化

从 industry_chain_matrix.json 生成:
  - Mermaid 架构图 (可在 Dashboard 用 mermaid.js 渲染)
  - NVDA vs Google TPU 供应商对比表
  - 各层详细 Markdown
  - JSON API 输出

用法:
  python3 industry_chain_renderer.py              # 输出 Mermaid 图
  python3 industry_chain_renderer.py --nvda-vs-tpu  # NVDA vs TPU 对比
  python3 industry_chain_renderer.py --layer 4     # 单层详情
  python3 industry_chain_renderer.py --json        # JSON 输出
"""

import json
import argparse
from pathlib import Path
from typing import Any

MATRIX_PATH = Path(__file__).parent / "industry_chain_matrix.json"


def load_matrix() -> dict[str, Any]:
    with open(MATRIX_PATH, encoding="utf-8") as f:
        return json.load(f)


# ============ Mermaid Diagram ============

def render_mermaid_diagram(matrix: dict[str, Any], focus: str = "all") -> str:
    """生成 Mermaid 架构图。

    focus="all" — 完整 11 层产业链
    focus="nvda" — 仅 NVDA 供应链层
    focus="tpu" — 仅 Google TPU 供应链层
    """
    layers = matrix["layers"]
    lines = ["graph TB"]
    lines.append("    %% AI 产业链架构图 — " +
                 { "all": "全产业链 11 层",
                   "nvda": "NVDA GPU 供应链",
                   "tpu": "Google TPU 供应链"
                 }.get(focus, "全产业链"))

    # Style classes
    lines.append("    classDef app fill:#e3f2fd,stroke:#1565c0,stroke-width:2px")
    lines.append("    classDef chip fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px")
    lines.append("    classDef ip fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1px")
    lines.append("    classDef foundry fill:#fff3e0,stroke:#e65100,stroke-width:2px")
    lines.append("    classDef cowos fill:#fce4ec,stroke:#c62828,stroke-width:2px")
    lines.append("    classDef hbm fill:#e8eaf6,stroke:#283593,stroke-width:2px")
    lines.append("    classDef abf fill:#efebe9,stroke:#4e342e,stroke-width:1px")
    lines.append("    classDef optical fill:#e0f7fa,stroke:#00695c,stroke-width:2px")
    lines.append("    classDef pcb fill:#fff8e1,stroke:#f9a825,stroke-width:1px")
    lines.append("    classDef power fill:#fbe9e7,stroke:#bf360c,stroke-width:1px")
    lines.append("    classDef server fill:#eceff1,stroke:#37474f,stroke-width:2px")

    style_classes = ["app", "chip", "ip", "foundry", "cowos", "hbm",
                     "abf", "optical", "pcb", "power", "server"]

    # Define nodes
    for layer in layers:
        lid = layer["id"]
        name = layer["name"]
        cls = style_classes[int(lid[1:])] if lid.startswith("P") and int(lid[1:]) < len(style_classes) else ""
        # Truncated name for diagram
        short_name = name.replace(" ", "<br/>")
        lines.append(f"    {lid}(\"{short_name}\")")
        if cls:
            lines.append(f"    class {lid} {cls}")

    # Edges (upstream/downstream relations)
    for layer in layers:
        for down_id in layer.get("downstream", []):
            lines.append(f"    {layer['id']} --> {down_id}")

    # Subgraph for NVDA vs TPU annotation
    lines.append("")
    lines.append("    subgraph legend [图例]")
    lines.append("        direction LR")
    lines.append("        N1[关键供给瓶颈]")
    lines.append("        N2[TSMC单点]")
    lines.append("        N3[NVDA+TPU共用]")
    lines.append("    end")

    # Annotate key bottlenecks
    lines.append("    %% 关键供给瓶颈标注")
    lines.append("    L3 -.-> |90%+垄断| N2")
    lines.append("    L4 -.-> |98%垄断| N2")
    lines.append("    L5 -.-> |供需缺口 2026-2027| N1")

    return "\n".join(lines)


def render_simple_architecture(matrix: dict[str, Any]) -> str:
    """精简版 ASCII 架构图 (CLI 输出用)"""
    layers = matrix["layers"]
    lines = ["╔══════════════════════════════════════════════════════════════╗"]
    lines.append("║         AI 产业链架构 — NVDA vs Google TPU                   ║")
    lines.append("╠══════════════════════════════════════════════════════════════╣")

    for layer in layers:
        lid = layer["id"]
        name = layer["name"]
        nv_suppliers = layer.get("nvidia_suppliers", [])
        tp_suppliers = layer.get("google_tpu_suppliers", [])
        mkt = layer.get("market_share", [])

        # Layer header
        lines.append("║                                                              ║")
        lines.append(f"║  {lid}: {name:<52s} ║")
        lines.append(f"║  {'─'*58} ║")

        # Key competitor
        top2 = mkt[:2]
        top_str = " > ".join(f"{t['entity']} {t['share_pct']}%" for t in top2) if top2 else "N/A"
        lines.append(f"║  市占: {top_str:<53s} ║")

        # NVDA suppliers
        if nv_suppliers:
            nv_names = [s["name"] for s in nv_suppliers[:3]]
            lines.append(f"║  NVDA: {', '.join(nv_names):<53s} ║")

        # TPU suppliers
        if tp_suppliers:
            tp_names = [s["name"] for s in tp_suppliers[:3]]
            lines.append(f"║  TPU:  {', '.join(tp_names):<53s} ║")

        # Downstream arrow
        if layer.get("downstream"):
            down_names = [str(d) for d in layer["downstream"]]
            lines.append(f"║  ↓ → {', '.join(down_names):<53s} ║")

    lines.append("╚══════════════════════════════════════════════════════════════╝")
    return "\n".join(lines)


# ============ NVDA vs TPU Comparison ============

def render_nvda_vs_tpu_markdown(matrix: dict[str, Any]) -> str:
    """NVDA GPU vs Google TPU 全产业链对比表 (Markdown)"""
    summary = matrix["nvidia_vs_tpu_summary"]
    lines = [
        f"# {summary['title']}",
        "",
        summary["description"],
        "",
        "| 维度 | NVDA GPU | Google TPU |",
        "|------|----------|------------|",
    ]

    for comp in summary["comparison"]:
        lines.append(f"| {comp['dimension']} | {comp['nvidia']} | {comp['google_tpu']} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-layer supplier detail
    lines.append("## 各层核心供应商对比")
    lines.append("")
    lines.append("| 层级 | NVDA 核心供应商 | Google TPU 核心供应商 | 关键差异 |")
    lines.append("|------|-----------------|---------------------|---------|")

    for layer in matrix["layers"]:
        nv = layer.get("nvidia_suppliers", [])
        tp = layer.get("google_tpu_suppliers", [])
        if not nv and not tp:
            continue

        nv_str = "<br>".join(f"**{s['name']}**: {s['note']}" for s in nv[:2]) if nv else "—"
        tp_str = "<br>".join(f"**{s['name']}**: {s['note']}" for s in tp[:2]) if tp else "—"

        # Key difference
        diff_parts = []
        nv_set = {s["name"] for s in nv}
        tp_set = {s["name"] for s in tp}
        shared = nv_set & tp_set
        nv_only = nv_set - tp_set
        tp_only = tp_set - nv_set
        if shared:
            diff_parts.append(f"共享: {', '.join(sorted(shared))}")
        if nv_only:
            diff_parts.append(f"NVDA独有: {', '.join(sorted(nv_only))}")
        if tp_only:
            diff_parts.append(f"TPU独有: {', '.join(sorted(tp_only))}")
        diff_str = "; ".join(diff_parts)

        lines.append(f"| **{layer['id']}: {layer['name']}** | {nv_str} | {tp_str} | {diff_str} |")

    return "\n".join(lines)


# ============ Layer Detail ============

def render_layer_detail_markdown(matrix: dict[str, Any], layer_id: int) -> str:
    """单层详细分析 Markdown"""
    layers = matrix["layers"]
    layer = next((ly for ly in layers if ly["id"] == layer_id), None)
    if not layer:
        return f"Layer {layer_id} not found"

    def _fmt_mkt(mkt_list: list[dict]) -> str:
        rows = ["| 公司/实体 | 市占率 | 备注 |", "|----------|--------|------|"]
        for m in mkt_list:
            rows.append(f"| {m['entity']} | {m['share_pct']}% | {m.get('note', '')} |")
        return "\n".join(rows)

    def _fmt_suppliers(suppliers: list[dict]) -> str:
        if not suppliers:
            return "*此层无直接供应商*"
        rows = ["| 供应商 | 角色 | 说明 |", "|--------|------|------|"]
        for s in suppliers:
            rows.append(f"| {s['name']} | {s['role']} | {s.get('note', '')} |")
        return "\n".join(rows)

    up = layer.get("upstream", [])
    down = layer.get("downstream", [])
    up_names = ", ".join(f"{u} {layers[u]['name']}" for u in up) if up else "无 (顶层)"
    down_names = ", ".join(f"{d} {layers[d]['name']}" for d in down) if down else "无 (底层)"

    return f"""# {layer['id']}: {layer['name']} ({layer['name_en']})

**上下游关系:** {up_names} → **{layer['id']}** → {down_names}

{layer['description']}

## 关键竞争要素

{chr(10).join(f'- {k}' for k in layer['key_competitiveness'])}

## 市占率分布

{_fmt_mkt(layer['market_share'])}

## NVDA 线供应商

{_fmt_suppliers(layer['nvidia_suppliers'])}

## Google TPU 线供应商

{_fmt_suppliers(layer['google_tpu_suppliers'])}
"""


# ============ JSON API ============

def render_matrix_json(matrix: dict[str, Any]) -> dict[str, Any]:
    """完整矩阵 JSON (API 输出)"""
    return {
        "meta": matrix["meta"],
        "layers": matrix["layers"],
        "nvidia_vs_tpu_summary": matrix["nvidia_vs_tpu_summary"],
    }


def render_summary_json(matrix: dict[str, Any]) -> dict[str, Any]:
    """精简摘要 JSON (Dashboard 用)"""
    summary_layers = []
    for layer in matrix["layers"]:
        top2_mkt = layer["market_share"][:2]
        nv = layer.get("nvidia_suppliers", [])
        tp = layer.get("google_tpu_suppliers", [])
        summary_layers.append({
            "id": layer["id"],
            "slug": layer["slug"],
            "name": layer["name"],
            "name_en": layer["name_en"],
            "top_market_share": [{"entity": m["entity"], "share_pct": m["share_pct"]}
                                 for m in top2_mkt],
            "nvidia_key_supplier": nv[0]["name"] if nv else None,
            "google_tpu_key_supplier": tp[0]["name"] if tp else None,
            "downstream": layer.get("downstream", []),
            "upstream": layer.get("upstream", []),
        })

    return {
        "description": matrix["meta"]["description"],
        "last_updated": matrix["meta"]["last_updated"],
        "layer_count": len(matrix["layers"]),
        "nvidia_vs_tpu_comparison": matrix["nvidia_vs_tpu_summary"]["comparison"],
        "layers": summary_layers,
    }


# ============ CSV Export ============

CSV_EXPORT_PATH = Path(__file__).parent / "industry_chain_matrix.csv"


def export_matrix_csv(matrix: dict[str, Any]) -> str:
    """将完整四层矩阵导出为 CSV。包含 NVDA/TPU/昇腾 三条线 + 国产化状态。"""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Layer", "层级名称", "品类(Category)", "供应链线", "系统供应商(Supplier)",
        "核心元件(Component)", "元件供应商(Component Supplier)",
        "元件供应商份额", "元件瓶颈", "国产化状态", "卡脖子材料(Material)",
        "材料类型", "材料供应商(Material Supplier)", "国家",
        "垄断级别", "材料说明"
    ])

    def _base(lid, lname, sl_label, supplier_label, loc):
        return [lid, lname, lname, sl_label, supplier_label]

    def _emit_supplier(lid, lname, sl_label, supplier_label, note, loc):
        writer.writerow(_base(lid, lname, sl_label, supplier_label, loc) +
                        [note, "", "", "", loc, "", "", "", "", "", ""])

    def _emit_material(lid, lname, sl_label, supplier_label, comp_label, cs_name, cs_share,
                       bottleneck, loc, mat_label, mat_type, ms_name, ms_country, ms_mono, ms_note):
        writer.writerow(_base(lid, lname, sl_label, supplier_label, loc) +
                        [comp_label, cs_name, cs_share, bottleneck, loc,
                         mat_label, mat_type, ms_name, ms_country, ms_mono, ms_note])

    for layer in matrix["layers"]:
        lid = f"{layer['id']}"
        lname = layer["name"]

        for sl_key, sl_label in [("nvidia_suppliers", "NVDA线"), ("google_tpu_suppliers", "TPU线"), ("ascend_suppliers", "昇腾线")]:
            for supplier in layer.get(sl_key, []):
                sname = supplier["name"]
                supplier_label = f"[{sl_label}] {sname}"
                loc = supplier.get("localization", "")

                if "deep_chain" not in supplier or not supplier["deep_chain"]:
                    _emit_supplier(lid, lname, sl_label, supplier_label, supplier.get("note", ""), loc)
                    continue

                for comp in supplier["deep_chain"]:
                    comp_label = comp["component"]
                    bottleneck = "Y" if comp.get("bottleneck") else ""
                    comp_note = comp.get("bottleneck_note", "") or ""
                    comp_suppliers = comp.get("suppliers", [])
                    # Component localization inherits from supplier if not set
                    comp_loc = comp.get("localization", loc)

                    if not comp_suppliers:
                        if "materials" in comp and comp["materials"]:
                            for mat in comp["materials"]:
                                for ms in (mat.get("suppliers") or []):
                                    _emit_material(lid, lname, sl_label, supplier_label, comp_label,
                                                   "", "", bottleneck, comp_loc,
                                                   mat["material"], mat["type"],
                                                   ms.get("name_cn", ms["name"]),
                                                   ms.get("country", ""),
                                                   ms.get("monopoly_level", ""),
                                                   ms.get("note", ""))
                                if not mat.get("suppliers"):
                                    _emit_material(lid, lname, sl_label, supplier_label, comp_label,
                                                   "", "", bottleneck, comp_loc,
                                                   mat["material"], mat["type"], "", "", "", mat.get("description", ""))
                        else:
                            _emit_material(lid, lname, sl_label, supplier_label, comp_label,
                                           "", "", bottleneck, comp_loc,
                                           "", "", "", "", "", comp_note)
                        continue

                    for cs in comp_suppliers:
                        cs_name = cs["name"]
                        cs_share = f"{cs['share_pct']}%"
                        cs_note = cs.get("note", "")

                        if "materials" not in comp or not comp["materials"]:
                            _emit_material(lid, lname, sl_label, supplier_label, comp_label,
                                           cs_name, cs_share, bottleneck, comp_loc,
                                           "", "", "", "", "", cs_note)
                            continue

                        for mat in comp["materials"]:
                            if not mat.get("suppliers"):
                                _emit_material(lid, lname, sl_label, supplier_label, comp_label,
                                               cs_name, cs_share, bottleneck, comp_loc,
                                               mat["material"], mat["type"], "", "", "", mat.get("description", ""))
                                continue
                            for ms in mat["suppliers"]:
                                _emit_material(lid, lname, sl_label, supplier_label, comp_label,
                                               cs_name, cs_share, bottleneck, comp_loc,
                                               mat["material"], mat["type"],
                                               ms.get("name_cn", ms["name"]),
                                               ms.get("country", ""),
                                               ms.get("monopoly_level", ""),
                                               ms.get("note", ""))

    return output.getvalue()


# ============ Chokepoint Index ============
# ============ Chokepoint Index ============

CHOKEPOINT_INDEX_PATH = Path(__file__).parent / "chokepoint_index.json"


def build_chokepoint_index(matrix: dict[str, Any]) -> dict[str, Any]:
    """从产业链矩阵自动生成 L4 卡脖子技术反向索引。

    遍历 layer → supplier → deep_chain → materials，
    按 L4 材料供应商名称聚合，生成反向查找表。
    """
    entries: dict[str, dict] = {}

    for layer in matrix["layers"]:
        lid = layer["id"]
        layer_name = layer["name"]
        # 遍历所有 supplier 列表
        for supplier_list_key in ["nvidia_suppliers", "google_tpu_suppliers"]:
            for supplier in layer.get(supplier_list_key, []):
                supplier_name = supplier["name"]
                for comp in supplier.get("deep_chain", []):
                    component_name = comp["component"]
                    for mat in comp.get("materials", []):
                        material_name = mat["material"]
                        material_type = mat["type"]
                        for ms in mat["suppliers"]:
                            key = ms["name"]
                            if key not in entries:
                                entries[key] = {
                                    "name_cn": ms.get("name_cn", key),
                                    "country": ms.get("country", ""),
                                    "materials": [],
                                    "affects": [],
                                    "downstream_tickers": [],
                                    "report_tags": [key],
                                }
                                if ms.get("name_cn") and ms["name_cn"] != key:
                                    entries[key]["report_tags"].append(ms["name_cn"])
                                if ms.get("note"):
                                    for tag in [key, ms.get("name_cn", ""), material_name]:
                                        if tag:
                                            entries[key]["report_tags"].append(tag)

                            entry = entries[key]

                            # 去重添加 material
                            mat_key = f"{material_name}|{material_type}"
                            if not any(m["material"] == material_name for m in entry["materials"]):
                                entry["materials"].append({
                                    "material": material_name,
                                    "type": material_type,
                                })

                            # 添加影响链
                            affects_key = f"{lid}|{supplier_name}|{component_name}"
                            if not any(a["component"] == component_name and a["supplier"] == supplier_name
                                       for a in entry["affects"]):
                                impact_level = "critical" if ms.get("monopoly_level") in ("absolute", "dominant") else "high"
                                entry["affects"].append({
                                    "layer_id": lid,
                                    "layer_name": layer_name,
                                    "supplier": supplier_name,
                                    "component": component_name,
                                    "impact_level": impact_level,
                                })

                            # 收集下游公司（L2 supplier 层面的公司）
                            # 从 supplier name 中提取公司名
                            ticker_names = set()
                            for s in supplier.get("suppliers", []):
                                ticker_names.add(s["name"].split("/")[-1].strip())
                            if not supplier.get("suppliers"):
                                ticker_names.add(supplier_name)

                            for t in ticker_names:
                                if t not in entry["downstream_tickers"]:
                                    entry["downstream_tickers"].append(t)

                # 清理 report_tags 去重
                for entry in entries.values():
                    entry["report_tags"] = list(dict.fromkeys(
                        t for t in entry["report_tags"] if t and len(t) > 1
                    ))

    # ---- L3 component supplier indexing ----
    # Also index deep_chain component suppliers with high share or bottleneck status
    for layer in matrix["layers"]:
        lid = layer["id"]
        layer_name = layer["name"]
        for supplier_list_key in ["nvidia_suppliers", "google_tpu_suppliers"]:
            for supplier in layer.get(supplier_list_key, []):
                supplier_name = supplier["name"]
                for comp in supplier.get("deep_chain", []):
                    component_name = comp["component"]
                    is_bottleneck = comp.get("bottleneck", False)
                    for cs in comp.get("suppliers", []):
                        cs_name = cs["name"]
                        cs_share = cs.get("share_pct", 0)
                        # Only index if >=30% share or on a bottleneck component with >=15% share
                        if cs_share < 15:
                            continue
                        if cs_share < 30 and not is_bottleneck:
                            continue

                        key = cs_name
                        if key not in entries:
                            entries[key] = {
                                "name_cn": cs_name,
                                "country": "",
                                "materials": [],
                                "affects": [],
                                "downstream_tickers": [],
                                "report_tags": [cs_name],
                            }

                        entry = entries[key]
                        # Mark as L3 component supplier
                        comp_mat_label = f"[L3元件] {component_name}"
                        if not any(m["material"] == comp_mat_label for m in entry["materials"]):
                            entry["materials"].append({
                                "material": comp_mat_label,
                                "type": "L3核心元件",
                            })

                        # Determine impact level
                        if cs_share >= 40 and is_bottleneck:
                            impact = "critical"
                        elif cs_share >= 30:
                            impact = "high"
                        else:
                            impact = "medium"

                        if not any(a["component"] == component_name and a["supplier"] == supplier_name
                                   for a in entry["affects"]):
                            entry["affects"].append({
                                "layer_id": lid,
                                "layer_name": layer_name,
                                "supplier": supplier_name,
                                "component": component_name,
                                "impact_level": impact,
                            })

                        # Add downstream tickers
                        ticker_names = set()
                        for s in comp.get("suppliers", []):
                            ticker_names.add(s["name"].split("/")[-1].strip())
                        for t in ticker_names:
                            if t not in entry["downstream_tickers"]:
                                entry["downstream_tickers"].append(t)

    # Clean report_tags
    for entry in entries.values():
        entry["report_tags"] = list(dict.fromkeys(
            t for t in entry["report_tags"] if t and len(t) > 1
        ))

    # ---- Stats ----
    country_dist = {}
    type_dist = {}
    monopoly_dist = {}
    for e in entries.values():
        c = e.get("country", "") or "N/A"
        country_dist[c] = country_dist.get(c, 0) + 1
        for m in e["materials"]:
            t = m["type"]
            type_dist[t] = type_dist.get(t, 0) + 1
    # Rebuild monopoly_dist properly
    monopoly_dist = {}
    for layer in matrix["layers"]:
        for sl in ["nvidia_suppliers", "google_tpu_suppliers"]:
            for s in layer.get(sl, []):
                for c in s.get("deep_chain", []):
                    for m in c.get("materials", []):
                        for ms in m["suppliers"]:
                            ml = ms.get("monopoly_level", "oligopoly")
                            monopoly_dist[ml] = monopoly_dist.get(ml, 0) + 1

    return {
        "last_updated": matrix["meta"]["last_updated"],
        "stats": {
            "total_companies": len(entries),
            "by_country": country_dist,
            "by_type": type_dist,
            "by_monopoly_level": monopoly_dist,
        },
        "entries": entries,
    }


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Industry Chain Matrix Renderer")
    parser.add_argument("--mermaid", action="store_true", help="Output Mermaid diagram")
    parser.add_argument("--mermaid-focus", choices=["all", "nvda", "tpu"], default="all",
                        help="Mermaid focus (default: all)")
    parser.add_argument("--ascii", action="store_true", help="Output ASCII architecture diagram")
    parser.add_argument("--nvda-vs-tpu", action="store_true", help="NVDA vs TPU comparison table")
    parser.add_argument("--layer", type=int, help="Layer detail (0-10)")
    parser.add_argument("--json", action="store_true", help="Full JSON output")
    parser.add_argument("--summary-json", action="store_true", help="Summary JSON output")
    parser.add_argument("--build-chokepoint-index", action="store_true",
                        help="Generate chokepoint_index.json (L4 reverse index)")
    parser.add_argument("--export-csv", action="store_true",
                        help="Export full 4-level matrix to CSV (industry_chain_matrix.csv)")

    args = parser.parse_args()
    matrix = load_matrix()

    if args.export_csv:
        csv_content = export_matrix_csv(matrix)
        CSV_EXPORT_PATH.write_text(csv_content, encoding="utf-8")
        rows = csv_content.strip().count("\n")
        print(f"Matrix CSV written to {CSV_EXPORT_PATH}")
        print(f"  {rows} data rows (1 header)")
    elif args.build_chokepoint_index:
        idx = build_chokepoint_index(matrix)
        with open(CHOKEPOINT_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
        print(f"Chokepoint index written to {CHOKEPOINT_INDEX_PATH}")
        print(f"  {idx['stats']['total_companies']} L4 companies indexed")
        print(f"  Countries: {idx['stats']['by_country']}")
        print(f"  Types: {idx['stats']['by_type']}")
        print(f"  Monopoly levels: {idx['stats']['by_monopoly_level']}")
    elif args.mermaid:
        print(render_mermaid_diagram(matrix, args.mermaid_focus))
    elif args.ascii:
        print(render_simple_architecture(matrix))
    elif args.nvda_vs_tpu:
        print(render_nvda_vs_tpu_markdown(matrix))
    elif args.layer is not None:
        print(render_layer_detail_markdown(matrix, args.layer))
    elif args.json:
        print(json.dumps(render_matrix_json(matrix), ensure_ascii=False, indent=2))
    elif args.summary_json:
        print(json.dumps(render_summary_json(matrix), ensure_ascii=False, indent=2))
    else:
        # Default: ASCII diagram
        print(render_simple_architecture(matrix))
        print()
        print("Options: --mermaid | --ascii | --nvda-vs-tpu | --layer N | --json | --summary-json | --build-chokepoint-index | --export-csv")

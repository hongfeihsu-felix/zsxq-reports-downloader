#!/usr/bin/env python3
"""Split L7 互连与光通信 into L7 光模块与激光器 + L8 网络互连与交换.
Renumbers old L8→L9, L9→L10, L10→L11."""

import json
from pathlib import Path

MATRIX_PATH = Path(__file__).parent.parent / "industry_chain_matrix.json"
data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

old_layers = data["layers"]

# Find old L7
l7 = next(l for l in old_layers if l["id"] == 7)

# ---- Optical suppliers (L7) ----
optical_nvda = []
optical_tpu = []
networking_nvda = []
networking_tpu = []

optical_nvda_names = {
    "中际旭创/Innolight", "新易盛/Eoptolink", "Coherent", "Fabrinet"
}
optical_tpu_names = {
    "中际旭创/Innolight", "Lumentum"
}
networking_nvda_names = {
    "NVIDIA Mellanox", "Credo Technology", "Astera Labs", "Amphenol"
}
networking_tpu_names = {
    "Google Jupiter", "Credo Technology", "Broadcom"
}

for s in l7.get("nvidia_suppliers", []):
    if s["name"] in optical_nvda_names:
        optical_nvda.append(s)
    elif s["name"] in networking_nvda_names:
        networking_nvda.append(s)
    else:
        # Ambiguous — put in optical by default
        optical_nvda.append(s)

for s in l7.get("google_tpu_suppliers", []):
    if s["name"] in optical_tpu_names:
        optical_tpu.append(s)
    elif s["name"] in networking_tpu_names:
        networking_tpu.append(s)
    else:
        optical_tpu.append(s)

# ---- Market share split ----
optical_mkt = [
    {"entity": "中际旭创/Innolight", "share_pct": 30, "note": "800G/1.6T 光模块出货量全球第一；NVDA+Google 双客户"},
    {"entity": "Coherent", "share_pct": 15, "note": "VCSEL+EML 激光器 + 800G 光模块"},
    {"entity": "新易盛/Eoptolink", "share_pct": 10, "note": "800G LPO 方案领先；NVDA 供应商"},
    {"entity": "Fabrinet", "share_pct": 8, "note": "NVDA 光模块 L1 代工 (800G 硅光子)"},
    {"entity": "Hisense/海信宽带", "share_pct": 8, "note": "800G 批量出货"},
    {"entity": "Lumentum", "share_pct": 7, "note": "EML/DML 激光器 + 光开关；组件级为主"},
    {"entity": "Others (天孚/联特/源杰)", "share_pct": 22, "note": "光引擎 FAU + 光芯片 + 器件"}
]

networking_mkt = [
    {"entity": "NVIDIA (Mellanox)", "share_pct": 60, "note": "InfiniBand Quantum-3 + Spectrum-X Ethernet；NVDA 生态锁定"},
    {"entity": "Broadcom (Tomahawk)", "share_pct": 15, "note": "Tomahawk 5/6 交换芯片；Google Jupiter 外部网络"},
    {"entity": "Arista", "share_pct": 10, "note": "AI 数据中心交换机；SONiC 开源"},
    {"entity": "Cisco", "share_pct": 8, "note": "Silicon One 交换芯片；企业级 AI 网络"},
    {"entity": "Astera Labs", "share_pct": 4, "note": "PCIe 6.0/CXL 3.0 Retimer；每GPU配4-8颗"},
    {"entity": "Credo Technology", "share_pct": 3, "note": "224G SerDes + AEC 有源铜缆 DSP"}
]

# ---- Create new layers ----
new_l7 = {
    "id": 7,
    "slug": "optical-transceivers",
    "name": "光模块与激光器",
    "name_en": "Optical Transceivers & Lasers",
    "description": "800G→1.6T→3.2T 光模块是 GPU 集群 Scale-out 的带宽基础。EML/CWDM/VCSEL 激光器 + DSP 芯片 + 硅光子集成三条技术路线共同决定光互联带宽天花板。",
    "upstream": l7["upstream"],
    "downstream": [8],
    "key_competitiveness": [
        "光模块速率迭代 (800G→1.6T→3.2T PAM4)",
        "EML/CWDM/VCSEL 激光器产能与良率",
        "硅光子集成度 (CPO 2027-2028 商用)",
        "DSP 芯片供应 (224G→448G PAM4)",
        "NVDA/Google 认证壁垒 (6-12 月资格周期)"
    ],
    "market_share": optical_mkt,
    "nvidia_suppliers": optical_nvda,
    "google_tpu_suppliers": optical_tpu,
    "linked_industry_slugs": ["interconnect"]
}

new_l8 = {
    "id": 8,
    "slug": "network-interconnect",
    "name": "网络互连与交换",
    "name_en": "Network Interconnect & Switching",
    "description": "GPU-to-GPU 互联的物理层：InfiniBand/Ethernet 交换芯片、SerDes/Retimer、高速连接器。NVDA 以 Mellanox(Spectrum-X/Quantum-3) 自研闭环，Google 以 Jupiter OCS 光交换突围。",
    "upstream": [7],
    "downstream": [9],
    "key_competitiveness": [
        "交换芯片带宽 (51.2T→102.4T)",
        "InfiniBand vs Ethernet 路线之争",
        "SerDes 速率竞赛 (224G→448G PAM4)",
        "PCIe/CXL Retimer (PCIe 6.0/7.0)",
        "高速连接器 (224G 背板/Overpass)"
    ],
    "market_share": networking_mkt,
    "nvidia_suppliers": networking_nvda,
    "google_tpu_suppliers": networking_tpu,
    "linked_industry_slugs": ["interconnect"]
}

# ---- Rebuild layer list ----
new_layers = []
for layer in old_layers:
    lid = layer["id"]
    if lid <= 6:
        # Update downstream refs that pointed at old L7
        layer["downstream"] = [7 if d == 7 else d for d in layer.get("downstream", [])]
        new_layers.append(layer)
    elif lid == 7:
        # Replace old L7 with two new layers
        new_layers.append(new_l7)
        new_layers.append(new_l8)
    elif lid >= 8:
        # Renumber old L8→L9, L9→L10, L10→L11
        new_lid = lid + 1
        layer = dict(layer)  # shallow copy
        layer["id"] = new_lid
        # Update upstream/downstream refs
        layer["upstream"] = [8 if u == 7 else (new_lid - 1 if u == lid - 1 else u) for u in layer.get("upstream", [])]
        # Re-map: old downstream 10→11, keep others
        new_down = []
        for d in layer.get("downstream", []):
            if d == 10:
                new_down.append(11)
            elif d > 7:
                new_down.append(d + 1)
            else:
                new_down.append(d)
        layer["downstream"] = new_down
        new_layers.append(layer)

# Fix L6 downstream (should point to 7=optical, not 8=networking)
for l in new_layers:
    if l["id"] == 6:
        l["downstream"] = [7, 8]  # ABF → optical + networking

# Fix L9(new PCB) upstream (was pointing to old L7=7, now points to networking L8=8)
for l in new_layers:
    if l["id"] == 9:
        l["upstream"] = [8]  # PCB ← networking

data["layers"] = new_layers
data["meta"]["version"] = "3.0"

MATRIX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

print("Split complete. Layer inventory:")
for l in new_layers:
    dc = sum(1 for s in l.get("nvidia_suppliers", []) + l.get("google_tpu_suppliers", [])
             if "deep_chain" in s)
    print(f"  L{l['id']} {l['name']} — NVDA:{len(l.get('nvidia_suppliers',[]))} TPU:{len(l.get('google_tpu_suppliers',[]))} deep_chain:{dc} upstream:{l['upstream']} downstream:{l['downstream']}")

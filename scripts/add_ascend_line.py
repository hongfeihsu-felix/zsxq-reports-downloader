#!/usr/bin/env python3
"""Add Huawei Ascend (昇腾) supply chain line to industry_chain_matrix.json.

Adds ascend_suppliers array to each layer, with localization status.
Target shipments (internal): 2026 1M, 2027 3M, 2028 8M units.
"""

import json
from pathlib import Path

MATRIX_PATH = Path(__file__).parent.parent / "industry_chain_matrix.json"
d = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

# ---- Localization legend ----
# ✓ 国产可替代  △ 受限/部分替代  ✗ 卡脖子（无国产替代）

# ===== L0 AI应用与模型 =====
l0 = next(l for l in d["layers"] if l["id"] == 0)
l0["ascend_suppliers"] = [
    {"name": "华为云", "role": "盘古大模型 + ModelArts平台", "note": "自研盘古大模型5.0，NLP/CV/多模态全栈。昇腾原生优化", "localization": "✓"},
    {"name": "科大讯飞", "role": "星火大模型 + 教育/医疗场景", "note": "昇腾910B集群训练，国产模型+国产算力闭环", "localization": "✓"},
]

# ===== L1 AI芯片设计 =====
l1 = next(l for l in d["layers"] if l["id"] == 1)
l1["ascend_suppliers"] = [
    {"name": "华为海思", "role": "昇腾910B/910C/950芯片设计 (in-house)", "note": "Da Vinci Core自研架构。910C: BF16≈280-320 TFLOPS(约H100的80%)，功耗仅310W。2026目标出货1M颗，2027 3M，2028 8M", "localization": "✓"},
    {"name": "华为", "role": "MindSpore框架 + CANN算子库", "note": "全栈自研AI框架，昇腾原生优化。CANN提供2000+算子", "localization": "✓"},
    {"name": "ARM", "role": "AArch64 CPU IP授权", "note": "Neoverse V2架构，但美国出口管制下未来授权存在政策风险。华为已在探索RISC-V替代", "localization": "△"},
]
# Update market_share
l1["market_share"].append({"entity": "Huawei Ascend", "share_pct": 8, "note": "中国AI芯片市占率~39%，2026年预计突破50%；910C出货目标1M/3M/8M(26-28)"})

# ===== L2 IP/EDA =====
l2 = next(l for l in d["layers"] if l["id"] == 2)
l2["ascend_suppliers"] = [
    {"name": "华为海思 (in-house)", "role": "自研IP：Da Vinci NPU + 昆仑 VPU + 高速SerDes", "note": "昇腾芯片内部IP大部分自研，减少对外授权依赖", "localization": "✓"},
    {"name": "华大九天", "role": "国产EDA — 模拟/数字全流程", "note": "国产EDA龙头，模拟约60%覆盖率，数字前端约35%，物理实现约25%。华为部分流程已切换", "localization": "△"},
    {"name": "概伦电子", "role": "国产EDA — SPICE仿真 + 良率分析", "note": "SPICE仿真工具国产替代主力。高端签核(Signoff)仍依赖Synopsys PrimeTime", "localization": "△"},
    {"name": "Synopsys/Cadence", "role": "EDA工具 — 前端综合/布局布线", "note": "高端节点(7nm以下)仍依赖。美国出口管制下存在断供风险", "localization": "✗"},
]

# ===== L3 晶圆代工 (MAX BOTTLENECK) =====
l3 = next(l for l in d["layers"] if l["id"] == 3)
l3["ascend_suppliers"] = [
    {"name": "SMIC/中芯国际", "role": "昇腾910B/C 核心代工厂", "note": "N+2/N+3工艺(等效7nm)。2026E月产能1-1.5万片晶圆，良率40-50%。Q3两座新晶圆厂投产。受限于DUV多重曝光无EUV，无法进入3nm", "localization": "△"},
    {"name": "—", "role": "3nm及以下：无国产方案", "note": "EUV光刻机被管制，3nm节点在2028年前无解。这是昇腾产业链最硬的结构性瓶颈", "localization": "✗"},
]
l3["market_share"].append({"entity": "SMIC (昇腾线)", "share_pct": 5, "note": "仅计算昇腾相关先进制程(≤14nm)产能。全球先进制程份额约5%"})

# ===== L4 先进封装 (CRITICAL) =====
l4 = next(l for l in d["layers"] if l["id"] == 4)
l4["ascend_suppliers"] = [
    {"name": "通富微电/TFME", "role": "昇腾2.5D封装主力 (>60%份额)", "note": "提供基板2.5D封装(非CoWoS，无硅中介层)。产能已被华为预订至2027年。回避硅中介层但代价是无法集成HBM", "localization": "△"},
    {"name": "长电科技/JCET", "role": "XDFOI Chiplet + Fanout-WLP", "note": "全球第三大封测厂。XDFOI三维堆叠用于HBM配套封装。Fanout-WLP良率约75% vs TSMC CoWoS ~85-95%", "localization": "△"},
    {"name": "盛合晶微", "role": "2.5D先进封装 (华为哈勃持股)", "note": "国内唯一大规模量产2.5D封装企业，昇腾950核心封测。华为哈勃投资绑定", "localization": "△"},
    {"name": "—", "role": "Si Interposer 硅中介层量产：2026-27预期", "note": "国内无大尺寸硅中介层量产能力，这是昇腾无法使用CoWoS级封装的物理制约", "localization": "✗"},
]

# ===== L5 HBM (WORST BOTTLENECK) =====
l5 = next(l for l in d["layers"] if l["id"] == 5)
l5["ascend_suppliers"] = [
    {"name": "CXMT/长鑫存储", "role": "国产DRAM龙头 — LPDDR5/DDR5", "note": "仅能量产LPDDR5/DDR5，无HBM产品线。HBM2e/HBM3均无国产方案。2026Q3若良率突破80%则为产业链'胜负手'", "localization": "✗"},
    {"name": "—", "role": "HBM2e/HBM3：无国产", "note": "HBM需要TSV深孔刻蚀+热压键合设备，均被美国/日本管制。昇腾910B用LPDDR5替代HBM，内存带宽约2TB/s vs H100的3.35TB/s(HBM3)——这是昇腾与H100最核心的性能差距来源", "localization": "✗"},
    {"name": "雅克科技", "role": "HBM前驱体材料", "note": "HBM TSV填充所需的高K介质前驱体，国产替代先锋。若Q1业绩同比+150%则预示产业链打通", "localization": "△"},
    {"name": "赛腾股份", "role": "HBM检测设备", "note": "半导体检测设备国产龙头，HBM晶圆级/封装级检测设备研发中", "localization": "△"},
]
l5["market_share"].append({"entity": "CXMT (昇腾线)", "share_pct": 0, "note": "仅LPDDR5/DDR5，无HBM。预计2027年前无国产HBM量产"})

# ===== L6 ABF载板 =====
l6 = next(l for l in d["layers"] if l["id"] == 6)
l6["ascend_suppliers"] = [
    {"name": "深南电路", "role": "ABF载板 — 14层+ FC-BGA", "note": "国内唯一量产14层以上FC-BGA基板。昇腾910C基板份额约60%", "localization": "✓"},
    {"name": "兴森科技", "role": "ABF载板 — 20层+ ABF，约70%昇腾份额", "note": "珠海基地72亿元产能专为昇腾定制，成本较海外低30%", "localization": "✓"},
    {"name": "华正新材", "role": "CBF膜 (ABF替代方案)", "note": "CBF膜已通过昇腾910C验证，6亿产能产线投用。ABF膜国产替代关键标的", "localization": "△"},
    {"name": "味之素 (Ajinomoto)", "role": "ABF薄膜材料 — 100%进口依赖", "note": "载板加工已国产化，但ABF膜本身仍100%依赖味之素。'壳国产、芯进口'", "localization": "✗"},
]

# ===== L7 光模块与激光器 =====
l7 = next(l for l in d["layers"] if l["id"] == 7)
l7["ascend_suppliers"] = [
    {"name": "华为 (in-house)", "role": "OptiXtrans光交换 + 400G/800G光模块", "note": "华为自研光通信全栈方案，DCI/光传送网完整生态", "localization": "✓"},
    {"name": "中际旭创/Innolight", "role": "800G/1.6T光模块", "note": "同时供应NVDA/TPU/昇腾三条线，全球光模块出货第一", "localization": "✓"},
    {"name": "海信宽带", "role": "400G/800G光模块", "note": "中国光模块第二梯队龙头，昇腾集群配套", "localization": "✓"},
    {"name": "光迅科技/Accelink", "role": "光芯片 + 光模块", "note": "国产光芯片龙头，CWDM/EML替代。100G EML量产，200G送样", "localization": "△"},
]
l7["market_share"].append({"entity": "华为 (昇腾线光模块)", "share_pct": 0, "note": "自研光交换+光模块，不对外销售。生态内闭环"})

# ===== L8 网络互连与交换 =====
l8 = next(l for l in d["layers"] if l["id"] == 8)
l8["ascend_suppliers"] = [
    {"name": "华为 (in-house)", "role": "CloudEngine数据中心交换机 + 自研交换芯片", "note": "华为自研交换芯片(Solar系列)+CloudEngine交换机，全栈自研", "localization": "✓"},
    {"name": "盛科通信", "role": "国产交换芯片", "note": "中国唯一商用交换芯片公司，但性能与Broadcom Tomahawk差距较大", "localization": "△"},
    {"name": "华丰科技", "role": "高速背板连接器 (华为哈勃持股2.95%)", "note": "国产高速连接器龙头，市占率超70%。224G PAM4连接器送样中", "localization": "✓"},
]

# ===== L9 PCB/CCL =====
l9 = next(l for l in d["layers"] if l["id"] == 9)
l9["ascend_suppliers"] = [
    {"name": "深南电路", "role": "AI服务器高速PCB", "note": "昇腾平台认证PCB供应商，与NVDA线共享产线", "localization": "✓"},
    {"name": "沪电股份/WUS", "role": "AI服务器PCB", "note": "昇腾+NVDA双平台认证", "localization": "✓"},
    {"name": "胜宏科技/Victory Giant", "role": "AI PCB (NVDA线为主)", "note": "昇腾平台PCB认证中，产线复用", "localization": "✓"},
]

# ===== L10 电力与散热 =====
l10 = next(l for l in d["layers"] if l["id"] == 10)
l10["ascend_suppliers"] = [
    {"name": "华为 (in-house)", "role": "iCooling液冷方案", "note": "华为自研AI数据中心液冷，昇腾384超节点标配", "localization": "✓"},
    {"name": "英维克/Envicool", "role": "Coolinside全链液冷", "note": "同时供应NVDA+昇腾液冷方案，国产液冷龙头", "localization": "✓"},
    {"name": "高澜股份", "role": "液冷冷板 + CDU", "note": "昇腾384超节点液冷配套，华为核心供应商", "localization": "✓"},
    {"name": "科华数据", "role": "UPS + 数据中心电力", "note": "国产UPS龙头，华为数据中心电力配套", "localization": "✓"},
]

# ===== L11 服务器集成 =====
l11 = next(l for l in d["layers"] if l["id"] == 11)
l11["ascend_suppliers"] = [
    {"name": "华为 (in-house)", "role": "Atlas 900 AI集群 (8/16/64卡)", "note": "全球最大AI训练集群之一。昇腾910B/C原厂集成，自研机柜设计", "localization": "✓"},
    {"name": "华鲲振宇 (四川长虹控股)", "role": "昇腾服务器ODM — >40%份额", "note": "昇腾服务器出货量最大合作伙伴。2026Q3若营收突破60亿则确认需求爆发", "localization": "✓"},
    {"name": "超聚变", "role": "FusionServer Pro + 昇腾卡", "note": "2022年从华为独立，专注服务器ODM。昇腾生态核心集成商", "localization": "✓"},
    {"name": "中科曙光", "role": "昇腾AI服务器代工", "note": "国产HPC龙头，昇腾服务器第二梯队", "localization": "✓"},
    {"name": "浪潮信息", "role": "昇腾AI服务器", "note": "中国最大服务器厂商，昇腾平台快速放量", "localization": "✓"},
]

# Update meta
d["meta"]["version"] = "4.0"
d["meta"]["description"] = "AI 产业链十二层矩阵 — NVDA vs Google TPU vs 华为昇腾 三条线供应商、市占率、关键竞争力、国产化状态"
d["meta"]["data_sources"].append("华为昇腾产业链调研 2026 (内部)")
d["meta"]["last_updated"] = "2026-06-07"

MATRIX_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# Summary
for l in d["layers"]:
    asc = l.get("ascend_suppliers", [])
    if asc:
        loc = {"✓": 0, "△": 0, "✗": 0}
        for s in asc:
            loc[s.get("localization", "?")] = loc.get(s.get("localization", "?"), 0) + 1
        print(f"L{l['id']:2d} {l['name']:<28s} Ascend:{len(asc)} suppliers  ✓{loc.get('✓',0)} △{loc.get('△',0)} ✗{loc.get('✗',0)}")
    else:
        print(f"L{l['id']:2d} {l['name']:<28s} Ascend:0")

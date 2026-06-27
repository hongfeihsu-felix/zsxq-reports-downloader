#!/usr/bin/env python3
"""
Config Manager - 统一管理公司、行业、关键词配置

用法：
  python3 config.py show                          # 查看完整配置
  python3 config.py list companies                # 列出所有跟踪公司
  python3 config.py list industries               # 列出所有跟踪行业
  python3 config.py add-company TSMC --ticker 2330.TW --keywords "tsmc,台積電" --industry Foundry
  python3 config.py remove-company TSMC
  python3 config.py toggle-company TSMC           # 切换 active/inactive
  python3 config.py add-industry "HBM" --keywords "hbm,hbm3,dram,高带宽内存"
  python3 config.py remove-industry "HBM"
  python3 config.py set threshold 10              # 设置 scanner 阈值
  python3 config.py set expire 90                 # 设置报告过期天数
"""

import json
import re
import sys
import argparse
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "api": {
        "cookie": "",
        "group_id": "",
        "proxy": {
            "http": "socks5://127.0.0.1:7897",
            "https": "socks5://127.0.0.1:7897"
        }
    },
    "api_keys": {
        "finnhub": ""
    },
    "email": {
        "smtp_server": "smtp.qq.com",
        "smtp_port": 587,
        "sender_email": "",
        "sender_password": "",
        "recipient_email": ""
    },
    "tracking": {
        "companies": [
            {
                "name": "TSMC",
                "ticker": "2330.TW",
                "keywords": ["tsmc", "taiwan semiconductor", "台積電", "台积电"],
                "industry": "Foundry",
                "active": True
            },
            {
                "name": "MediaTek",
                "ticker": "2454.TW",
                "keywords": ["mediatek", "聯發科", "联发科"],
                "industry": "Fabless",
                "active": True
            },
            {
                "name": "NVIDIA",
                "ticker": "NVDA.US",
                "keywords": ["nvidia", "nvda"],
                "industry": "AI Chip",
                "active": True
            },
            {
                "name": "AMD",
                "ticker": "AMD.US",
                "keywords": ["amd", "advanced micro devices"],
                "industry": "AI Chip",
                "active": True
            },
            {
                "name": "Broadcom",
                "ticker": "AVGO.US",
                "keywords": ["broadcom", "avgo"],
                "industry": "AI Chip",
                "active": True
            },
            {
                "name": "Intel",
                "ticker": "INTC.US",
                "keywords": ["intel", "intc", "intel foundry"],
                "industry": "Foundry",
                "active": True
            },
            {
                "name": "Qualcomm",
                "ticker": "QCOM.US",
                "keywords": ["qualcomm", "qcom"],
                "industry": "Fabless",
                "active": True
            },
            {
                "name": "Marvell",
                "ticker": "MRVL.US",
                "keywords": ["marvell", "mrvl"],
                "industry": "AI Chip",
                "active": True
            },
            {
                "name": "Micron",
                "ticker": "MU.US",
                "keywords": ["micron", "micron technology", "美光"],
                "industry": "Memory",
                "active": True
            },
            {
                "name": "SK Hynix",
                "ticker": "000660.KS",
                "keywords": ["sk hynix", "hynix", "海力士"],
                "industry": "Memory",
                "active": True
            },
            {
                "name": "Samsung",
                "ticker": "005930.KS",
                "keywords": ["samsung", "samsung electronics", "samsung foundry"],
                "industry": "Memory",
                "active": True
            },
            {
                "name": "SMIC",
                "ticker": "0981.HK",
                "keywords": ["smic", "中芯国际", "中芯"],
                "industry": "Foundry",
                "active": True
            },
            {
                "name": "GlobalFoundries",
                "ticker": "GFS.US",
                "keywords": ["globalfoundries", "global foundries", "gf", "格芯"],
                "industry": "Foundry",
                "active": True
            }
        ],
        "industries": [
            {
                "name": "CoWoS / Advanced Packaging",
                "slug": "cowos",
                "keywords": [
                    "cowos", "chip on wafer", "advanced packaging", "3D packaging",
                    "先进封装", "chiplet", "hybrid bonding", "silicon interposer",
                    "fan-out", "info", "CoWoS-S", "CoWoS-L", "CoWoS-R"
                ],
                "active": True
            },
            {
                "name": "HBM / Memory",
                "slug": "memory",
                "keywords": [
                    "hbm", "hbm3", "hbm3e", "hbm4", "dram", "nand", "flash",
                    "memory", "storage", "ssd", "ddr", "lpddr", "高带宽内存",
                    "存储", "内存", "memory chip"
                ],
                "active": True
            },
            {
                "name": "AI Chip (GPU/TPU/ASIC)",
                "slug": "ai-chip",
                "keywords": [
                    "gpu", "tpu", "asic", "ai chip", "ai accelerator",
                    "ai semiconductor", "ai芯片", "training chip", "inference chip",
                    "h100", "h200", "gb200", "gb300", "b100", "b200", "b300",
                    "nvl72", "nvl36", "nvl144", "vr200", "rubin", "vera rubin",
                    "kyber", "oberon", "dgx", "hgx",
                    "mi300", "mi400",
                    "trainium", "inferentia", "maia", "mtia", "gaudi"
                ],
                "active": True
            },
            {
                "name": "Foundry / Capacity",
                "slug": "foundry",
                "keywords": [
                    "foundry", "wafer", "capacity", "utilization", "capex",
                    "晶圆代工", "产能", "资本支出", "3nm", "2nm", "n3", "n2",
                    "fab", "node", "process technology"
                ],
                "active": True
            },
            {
                "name": "Computing Power / Datacenter",
                "slug": "compute",
                "keywords": [
                    "datacenter", "data center", "server", "computing power",
                    "算力", "hyperscaler", "hyperscale", "超大规模",
                    "CSP", "cloud provider", "cloud", "inference", "training",
                    "supercomputer", "compute", "rack", "机柜", "整机柜", "整机架",
                    "AI服务器", "GPU服务器", "nvl72", "nvl36",
                    "liquid cooling", "cooling"
                ],
                "active": True
            },
            {
                "name": "Power / Energy",
                "slug": "power",
                "keywords": [
                    "power semiconductor", "power management", "pmic",
                    "电源管理", "功率半导体", "sic", "gan", "mosfet", "igbt",
                    "power supply", "pdu", "rack pdu", "busbar", "母线",
                    "bbu", "backup battery", "power shelf", "电源架",
                    "供电", "功耗", "energy", "renewable"
                ],
                "active": True
            },
            {
                "name": "Interconnect / Optical",
                "slug": "interconnect",
                "keywords": [
                    "optical module", "光模块", "光通信", "interconnect",
                    "connecting", "transceiver", "cpo", "co-packaged optics",
                    "serdes", "switch", "networking", "ethernet", "infiniband",
                    "铜缆", "copper cable", "背板", "backplane",
                    "dac", "acc", "aec", "nvlink", "nvswitch", "pcie"
                ],
                "active": True
            }
        ]
    },
    "scanner": {
        "score_threshold": 10
    },
    "maintenance": {
        "report_expire_days": 90,
        "consensus_expire_days": 7
    }
}


class ConfigManager:
    """统一配置管理器"""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, 'r', encoding='utf-8') as f:
                existing = json.load(f)

            # 自动迁移：将旧字段合并进新结构，只补齐缺失的 top-level key
            merged = dict(DEFAULT_CONFIG)
            # 保留旧 config 中的 api/email 字段
            for section in ["api", "email"]:
                if section in existing:
                    merged[section].update(existing[section])
            # 保留旧的直接字段
            for key in ["cookie", "group_id", "proxy"]:
                if key in existing and "api" not in existing:
                    if key == "cookie":
                        merged["api"]["cookie"] = existing[key]
                    elif key == "group_id":
                        merged["api"]["group_id"] = existing[key]
                    elif key == "proxy":
                        merged["api"]["proxy"].update(existing[key])
            # tracking 使用默认（用户通过 CLI 管理）
            if "tracking" in existing:
                for section in ["companies", "industries"]:
                    if section in existing["tracking"]:
                        merged["tracking"][section] = existing["tracking"][section]
            return merged
        return dict(DEFAULT_CONFIG)

    def save(self):
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    # ---- Companies ----

    def get_companies(self, active_only: bool = True) -> list[dict]:
        companies = self.data["tracking"]["companies"]
        if active_only:
            return [c for c in companies if c.get("active", True)]
        return companies

    def find_company(self, name: str) -> Optional[dict]:
        name_lower = name.lower()
        for c in self.data["tracking"]["companies"]:
            if c["name"].lower() == name_lower:
                return c
        return None

    def add_company(self, name: str, ticker: str = "",
                    keywords: str = "", industry: str = ""):
        if not name or not name.strip():
            print("❌ Company name is required")
            return
        name = name.strip()
        if self.find_company(name):
            print(f"⚠️  Company '{name}' already exists. Use update or remove first.")
            return
        if ticker and not re.match(r'^[A-Z0-9.]+$', ticker, re.IGNORECASE):
            print(f"⚠️  Ticker '{ticker}' looks unusual (expected: letters, digits, dots)")

        kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else [name.lower()]
        self.data["tracking"]["companies"].append({
            "name": name,
            "ticker": ticker,
            "keywords": kw_list,
            "industry": industry,
            "active": True
        })
        self.save()
        print(f"✅ Added company: {name}")

    def remove_company(self, name: str):
        companies = self.data["tracking"]["companies"]
        self.data["tracking"]["companies"] = [
            c for c in companies if c["name"].lower() != name.lower()
        ]
        self.save()
        print(f"✅ Removed company: {name}")

    def toggle_company(self, name: str):
        c = self.find_company(name)
        if c:
            c["active"] = not c.get("active", True)
            status = "active" if c["active"] else "inactive"
            self.save()
            print(f"✅ {name} → {status}")

    # ---- Industries ----

    def get_industries(self, active_only: bool = True) -> list[dict]:
        industries = self.data["tracking"]["industries"]
        if active_only:
            return [i for i in industries if i.get("active", True)]
        return industries

    def find_industry(self, slug: str) -> Optional[dict]:
        slug_lower = slug.lower()
        for i in self.data["tracking"]["industries"]:
            if i["slug"].lower() == slug_lower or i["name"].lower() == slug_lower:
                return i
        return None

    def add_industry(self, name: str, slug: str = "", keywords: str = ""):
        slug = slug or name.lower().replace(" ", "-").replace("/", "-")
        if self.find_industry(slug):
            print(f"⚠️  Industry '{name}' already exists.")
            return

        kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
        self.data["tracking"]["industries"].append({
            "name": name,
            "slug": slug,
            "keywords": kw_list,
            "active": True
        })
        self.save()
        print(f"✅ Added industry: {name}")

    def remove_industry(self, slug: str):
        self.data["tracking"]["industries"] = [
            i for i in self.data["tracking"]["industries"]
            if i["slug"].lower() != slug.lower()
        ]
        self.save()
        print(f"✅ Removed industry: {slug}")

    # ---- Settings ----

    def set(self, key: str, value: str):
        if key == "threshold":
            self.data["scanner"]["score_threshold"] = int(value)
        elif key == "expire":
            self.data["maintenance"]["report_expire_days"] = int(value)
        else:
            print(f"❌ Unknown setting: {key}")
            return
        self.save()
        print(f"✅ {key} = {value}")

    # ---- Display ----

    def show(self):
        """打印完整配置概览"""
        print(f"\n{'=' * 60}")
        print(f"  ⚙️  Config Overview")
        print(f"{'=' * 60}")

        companies = self.get_companies(active_only=False)
        active_cos = [c for c in companies if c.get("active", True)]
        print(f"\n  Companies: {len(active_cos)} active / {len(companies)} total")
        for c in sorted(companies, key=lambda x: (not x.get("active", True), x["name"])):
            status = "✅" if c.get("active", True) else "⏸️"
            print(f"    {status} {c['name']:<20} {c.get('ticker', ''):<12} [{c.get('industry', '')}]")

        industries = self.get_industries(active_only=False)
        active_inds = [i for i in industries if i.get("active", True)]
        print(f"\n  Industries: {len(active_inds)} active / {len(industries)} total")
        for ind in sorted(industries, key=lambda x: (not x.get("active", True), x["name"])):
            status = "✅" if ind.get("active", True) else "⏸️"
            kws = ", ".join(ind.get("keywords", [])[:5])
            print(f"    {status} {ind['name']:<30} [{kws}...]")

        scanner = self.data.get("scanner", {})
        maint = self.data.get("maintenance", {})
        print(f"\n  Scanner threshold: {scanner.get('score_threshold', 10)}")
        print(f"  Report expire: {maint.get('report_expire_days', 90)} days")
        print(f"\n{'=' * 60}\n")

    def show_list(self, item_type: str):
        """列出公司或行业（简洁模式）"""
        if item_type == "companies":
            items = self.get_companies(active_only=False)
            print(f"\n{'Company':<20} {'Ticker':<12} {'Industry':<20} {'Status'}")
            print(f"{'─'*20} {'─'*12} {'─'*20} {'─'*8}")
            for c in sorted(items, key=lambda x: x["name"]):
                status = "active" if c.get("active", True) else "paused"
                print(f"{c['name']:<20} {c.get('ticker', ''):<12} {c.get('industry', ''):<20} {status}")
        elif item_type == "industries":
            items = self.get_industries(active_only=False)
            print(f"\n{'Industry':<35} {'Keywords':<50} {'Status'}")
            print(f"{'─'*35} {'─'*50} {'─'*8}")
            for i in sorted(items, key=lambda x: x["name"]):
                status = "active" if i.get("active", True) else "paused"
                kws = ", ".join(i.get("keywords", [])[:5])
                print(f"{i['name']:<35} {kws:<50} {status}")
        print()


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Config Manager")
    sub = parser.add_subparsers(dest="command", help="Commands")

    # show
    sub.add_parser("show", help="Show full config")

    # list
    list_cmd = sub.add_parser("list", help="List companies or industries")
    list_cmd.add_argument("type", choices=["companies", "industries"], help="What to list")

    # add-company
    ac = sub.add_parser("add-company", help="Add a company")
    ac.add_argument("name", help="Company name")
    ac.add_argument("--ticker", default="", help="Stock ticker")
    ac.add_argument("--keywords", default="", help="Comma-separated keywords")
    ac.add_argument("--industry", default="", help="Industry name")

    # remove-company
    rc = sub.add_parser("remove-company", help="Remove a company")
    rc.add_argument("name", help="Company name")

    # toggle-company
    tc = sub.add_parser("toggle-company", help="Toggle company active/inactive")
    tc.add_argument("name", help="Company name")

    # add-industry
    ai = sub.add_parser("add-industry", help="Add an industry")
    ai.add_argument("name", help="Industry name")
    ai.add_argument("--slug", default="", help="Short slug")
    ai.add_argument("--keywords", default="", help="Comma-separated keywords")

    # remove-industry
    ri = sub.add_parser("remove-industry", help="Remove an industry")
    ri.add_argument("slug", help="Industry slug or name")

    # set
    set_cmd = sub.add_parser("set", help="Set a config value")
    set_cmd.add_argument("key", help="Setting key (threshold, expire)")
    set_cmd.add_argument("value", help="Setting value")

    args = parser.parse_args()
    mgr = ConfigManager()

    if args.command == "show":
        mgr.show()
    elif args.command == "list":
        mgr.show_list(args.type)
    elif args.command == "add-company":
        mgr.add_company(args.name, args.ticker, args.keywords, args.industry)
    elif args.command == "remove-company":
        mgr.remove_company(args.name)
    elif args.command == "toggle-company":
        mgr.toggle_company(args.name)
    elif args.command == "add-industry":
        mgr.add_industry(args.name, args.slug, args.keywords)
    elif args.command == "remove-industry":
        mgr.remove_industry(args.slug)
    elif args.command == "set":
        mgr.set(args.key, args.value)
    else:
        parser.print_help()

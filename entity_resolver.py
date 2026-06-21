#!/usr/bin/env python3
"""Shared entity, ticker, market, and currency resolution helpers."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent
CONFIG_PATH = PROJECT_DIR / "config.json"


FALLBACK_ALIASES: dict[str, list[str]] = {
    "MediaTek": ["mediatek", "mediatek inc", "mediatek inc.", "聯發科", "联发科", "2454"],
    "TSMC": ["tsmc", "taiwan semiconductor", "台積電", "台积电", "2330"],
    "NVIDIA": ["nvidia", "nvda", "nvidia corporation", "nvidia corp"],
    "Broadcom": ["broadcom", "avgo", "broadcom inc", "broadcom inc."],
    "Qualcomm": ["qualcomm", "qcom", "qualcomm inc", "qualcomm inc."],
    "AMD": ["amd", "advanced micro devices"],
    "Intel": ["intel", "intc", "intel corporation", "intel corp"],
    "Marvell": ["marvell", "mrvl", "marvell technology"],
    "Samsung": ["samsung", "samsung electronics", "samsung elec"],
    "SK Hynix": ["sk hynix", "sk hynix inc", "hynix", "海力士"],
    "Micron": ["micron", "micron technology", "mu", "美光"],
    "SMIC": ["smic", "semiconductor manufacturing international", "中芯国际", "中芯"],
    "UMC": ["umc", "united microelectronics", "联电", "聯電"],
    "Hua Hong": ["hua hong", "hua hong semiconductor", "华虹半导体", "华虹半导体有限公司", "華虹半導體"],
    "GlobalFoundries": ["globalfoundries", "global foundries", "gf", "格芯"],
    "Lumentum": ["lumentum", "lite"],
    "Coherent": ["coherent", "cohr"],
    "Fabrinet": ["fabrinet", "fn"],
    "Palantir": ["palantir", "pltr", "palantir technologies"],
    "CoreWeave": ["coreweave", "core weave"],
    "X-Energy": ["x-energy", "x energy"],
    "Amazon": ["amazon", "amzn", "amazon.com"],
    "Microsoft": ["microsoft", "msft", "microsoft corp"],
    "Meta": ["meta", "meta platforms", "facebook"],
    "Google": ["google", "alphabet", "googl", "goog"],
    "Apple": ["apple", "aapl", "apple inc"],
    "Tesla": ["tesla", "tsla"],
    "Oracle": ["oracle", "orcl"],
}

TICKER_CURRENCY = {
    "US": "USD",
    "HK": "HKD",
    "TW": "TWD",
    "TT": "TWD",
    "KS": "KRW",
    "KQ": "KRW",
    "T": "JPY",
    "JP": "JPY",
    "SS": "CNY",
    "SZ": "CNY",
    "SH": "CNY",
}


@dataclass(frozen=True)
class EntityMatch:
    name: str
    ticker: str = ""
    industry: str = ""
    ah_premium: float = 1.0


_company_map: dict[str, EntityMatch] | None = None


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _ticker_base(ticker: str) -> str:
    return (ticker or "").split(".")[0].lower().strip()


def _add_alias(mapping: dict[str, EntityMatch], alias: str, match: EntityMatch) -> None:
    alias = (alias or "").lower().strip().rstrip(".")
    if alias and alias not in mapping:
        mapping[alias] = match


def build_company_map() -> dict[str, EntityMatch]:
    mapping: dict[str, EntityMatch] = {}

    cfg = _load_config()
    for c in cfg.get("tracking", {}).get("companies", []):
        canonical = c.get("name", "")
        if not canonical:
            continue
        ticker = c.get("ticker", "")
        ah = c.get("ah_cross", {})
        match = EntityMatch(
            name=canonical,
            ticker=ticker,
            industry=c.get("industry", ""),
            ah_premium=float(ah.get("premium_ratio", 1.0) or 1.0),
        )
        _add_alias(mapping, canonical, match)
        _add_alias(mapping, ticker, match)
        _add_alias(mapping, _ticker_base(ticker), match)
        for kw in c.get("keywords", []):
            _add_alias(mapping, kw, match)

    for canonical, aliases in FALLBACK_ALIASES.items():
        fallback = EntityMatch(name=canonical)
        _add_alias(mapping, canonical, fallback)
        for alias in aliases:
            _add_alias(mapping, alias, fallback)

    return mapping


def get_company_map() -> dict[str, EntityMatch]:
    global _company_map
    if _company_map is None:
        _company_map = build_company_map()
    return _company_map


def resolve_company(raw: str) -> Optional[EntityMatch]:
    text = (raw or "").lower().strip().rstrip(".")
    if not text:
        return None

    mapping = get_company_map()
    if text in mapping:
        return mapping[text]

    base = _ticker_base(text)
    if base in mapping:
        return mapping[base]

    # Prefer longer aliases so "global foundries" wins before "gf".
    for alias in sorted(mapping, key=len, reverse=True):
        if alias and alias in text:
            return mapping[alias]
    return None


def normalize_company(raw: str) -> str:
    match = resolve_company(raw)
    if match:
        return match.name
    return raw.strip().title() if raw else "Unknown"


def ticker_suffix(ticker: str) -> str:
    parts = (ticker or "").upper().strip().split(".")
    return parts[-1] if len(parts) > 1 else ""


def default_currency_for_ticker(ticker: str, default: str = "") -> str:
    return TICKER_CURRENCY.get(ticker_suffix(ticker), default)


def detect_currency(text: str, ticker: str = "", default: str = "USD") -> str:
    """Detect target-price currency from explicit text, then ticker suffix."""
    content = text or ""
    if "NT$" in content or "TWD" in content or "NTD" in content or "新台币" in content or "新臺幣" in content:
        return "TWD"
    if "HKD" in content or "HK$" in content or "港元" in content:
        return "HKD"
    if "US$" in content or "USD" in content or "美元" in content or "$" in content:
        return "USD"
    if "₩" in content or "KRW" in content or "韩元" in content or "韓元" in content or "韩圜" in content:
        return "KRW"
    if re.search(r"\bW\s*\d", content):
        return "KRW"
    if "JPY" in content or "日元" in content or "日圓" in content or "円" in content or "¥" in content:
        return "JPY"
    if "RMB" in content or "CNY" in content or "人民币" in content or "人民幣" in content:
        return "CNY"
    if default_currency_for_ticker(ticker):
        return default_currency_for_ticker(ticker)
    if "元" in content:
        return "CNY"
    return default

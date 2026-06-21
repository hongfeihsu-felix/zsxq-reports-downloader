"""Unit tests for shared entity/ticker/currency resolution."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from entity_resolver import (
    default_currency_for_ticker,
    detect_currency,
    normalize_company,
    resolve_company,
)


def test_normalize_company_from_ticker_alias():
    assert normalize_company("NVDA") == "NVIDIA"
    assert normalize_company("AVGO.US") == "Broadcom"
    assert normalize_company("MRVL") == "Marvell"


def test_resolve_company_includes_ah_premium():
    match = resolve_company("688981")
    assert match is not None
    assert match.name == "SMIC"
    assert match.ah_premium == 1.3


def test_default_currency_for_ticker_suffix():
    assert default_currency_for_ticker("6976.T") == "JPY"
    assert default_currency_for_ticker("005930.KS") == "KRW"
    assert default_currency_for_ticker("2454.TW") == "TWD"
    assert default_currency_for_ticker("0700.HK") == "HKD"
    assert default_currency_for_ticker("NVDA.US") == "USD"


def test_detect_currency_prefers_explicit_text():
    assert detect_currency("目标价：7,100 日元", ticker="6976.T") == "JPY"
    assert detect_currency("目标价：500 美元", ticker="0700.HK") == "USD"
    assert detect_currency("Target Price: W320,000", ticker="005930.KS") == "KRW"


def test_detect_currency_falls_back_to_ticker():
    assert detect_currency("目标价：7100", ticker="6976.T") == "JPY"
    assert detect_currency("Target Price: 500", ticker="NVDA.US") == "USD"

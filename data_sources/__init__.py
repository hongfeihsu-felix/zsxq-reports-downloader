"""
Data Sources - 统一行情 & 财报数据接口

用法：
  from data_sources import get_price, get_earnings, get_earnings_calendar

  price = get_price("2330.TW")
  earnings = get_earnings("TSMC", "2330.TW")
  calendar = get_earnings_calendar(["2330.TW", "NVDA"])
"""

from .stock_price import StockPriceFetcher, get_price
from .earnings import EarningsFetcher, get_earnings, get_earnings_calendar
from .eastmoney import EastmoneyFetcher

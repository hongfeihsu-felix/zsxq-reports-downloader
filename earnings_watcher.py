#!/usr/bin/env python3
"""
Earnings Watcher — 财报日历自动监控

每周一运行：
  - 拉取所有跟踪公司的财报日历
  - 标记本周/下周即将发布财报的公司
  - 自动拉取最近财报数据
  - 生成预警摘要

用法：
  python3 earnings_watcher.py                # 检查本周财报
  python3 earnings_watcher.py --next-week    # 检查下周财报
  python3 earnings_watcher.py --pull-all     # 拉取所有跟踪公司最新财报
  python3 earnings_watcher.py --send-email   # 发送邮件通知

Cron (每周一 08:00):
  0 8 * * 1 cd /path/to/hermes && python3 earnings_watcher.py --send-email
"""

import json
import smtplib
import argparse
import time as _time
from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from data_sources.earnings import EarningsFetcher, get_earnings_calendar
from data_sources.stock_price import get_price

PROJECT_DIR = Path(__file__).parent

# 邮件配置
import os as _os
EMAIL_CONFIG = {
    "smtp_server": "smtp.qq.com",
    "smtp_port": 587,
    "sender_email": _os.environ.get("SENDER_EMAIL", ""),
    "sender_password": _os.environ.get("SENDER_PASSWORD", ""),
    "recipient_email": _os.environ.get("RECIPIENT_EMAIL", ""),
}


def load_tracked_tickers() -> list[dict]:
    """从 config.json 加载所有跟踪的公司及其 ticker"""
    config_path = PROJECT_DIR / "config.json"
    if not config_path.exists():
        return []

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    companies = cfg.get("tracking", {}).get("companies", [])

    result = []
    for c in companies:
        if c.get("active", True) and c.get("ticker"):
            result.append({
                "name": c["name"],
                "ticker": c["ticker"],
                "industry": c.get("industry", "")
            })
    return result


def pull_earnings_batch(companies: list[dict], delay: float = 3.0) -> list[dict]:
    """批量拉取财报数据（带延迟避免限流）"""
    ef = EarningsFetcher()
    results = []

    for i, co in enumerate(companies):
        if i > 0:
            _time.sleep(delay)

        ticker = co["ticker"]
        print(f"  [{i+1}/{len(companies)}] {co['name']} ({ticker})...")
        try:
            data = ef.get(co["name"], ticker, force_refresh=True)
            # Attach stock price
            try:
                price = get_price(ticker, force_refresh=(i == 0))
                _time.sleep(delay)
            except Exception:
                price = {"error": "unavailable"}

            results.append({
                "company": co["name"],
                "ticker": ticker,
                "earnings": data,
                "price": price
            })
        except Exception as e:
            print(f"    ❌ {e}")
            results.append({
                "company": co["name"], "ticker": ticker,
                "earnings": {"error": str(e)}, "price": {"error": str(e)}
            })

    return results


def check_calendar(tickers: list[str], days_ahead: int = 7) -> dict:
    """检查财报日历，返回即将发布的公司"""
    ef = EarningsFetcher()
    calendar = ef.get_calendar(tickers)
    this_week = ef.this_week_earnings(tickers)

    # Also check next N days
    upcoming = []
    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)

    for item in calendar:
        if item.get("next_date"):
            try:
                edate = datetime.fromisoformat(item["next_date"]).date()
                if today <= edate <= cutoff:
                    upcoming.append(item)
            except (ValueError, TypeError):
                pass

    return {
        "calendar": calendar,
        "this_week": this_week,
        "upcoming": upcoming,
        "checked_at": datetime.now().isoformat()
    }


def format_summary(calendar_data: dict, earnings_data: list[dict] = None) -> str:
    """生成人类可读的摘要"""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"Earnings Watcher Report — {today}",
        "=" * 50,
        ""
    ]

    # Calendar
    this_week = calendar_data.get("this_week", [])
    upcoming = calendar_data.get("upcoming", [])

    if this_week:
        lines.append(f"📅 THIS WEEK ({len(this_week)} companies):")
        for item in this_week:
            ticker = item.get("ticker", "?")
            est_eps = item.get("estimate_eps", 0)
            lines.append(f"  • {ticker} — {item.get('next_date', '?')} — Est EPS: {est_eps}")
    else:
        lines.append("📅 THIS WEEK: No earnings scheduled")
        # Show next
        if upcoming:
            lines.append(f"\n📆 Upcoming (next 7 days, {len(upcoming)} companies):")
            for item in upcoming[:10]:
                ticker = item.get("ticker", "?")
                lines.append(f"  • {ticker} — {item.get('next_date', '?')}")

    # Pulled earnings data
    if earnings_data:
        lines.append(f"\n📊 Latest Earnings Pulled ({len(earnings_data)} companies):")
        for item in earnings_data:
            co = item["company"]
            ticker = item["ticker"]
            earn = item.get("earnings", {})
            price = item.get("price", {})

            price_str = ""
            if price.get("price"):
                chg = price.get("change_pct", 0) or 0
                price_str = f" | ${price['price']} ({chg:+.1f}%)"

            quarters = earn.get("quarters", [])
            if quarters and not quarters[0].get("error"):
                q = quarters[0]
                rev = q.get("revenue", 0)
                eps = q.get("eps", 0)
                period = q.get("period", "?")
                if rev > 1e8:
                    rev_str = f"{rev/1e8:.1f}B"
                elif rev > 1e4:
                    rev_str = f"{rev/1e4:.1f}M"
                else:
                    rev_str = f"{rev:.0f}"
                lines.append(f"  • {co} ({ticker}): {period} Rev={rev_str} EPS={eps:.2f}{price_str}")
            else:
                lines.append(f"  • {co} ({ticker}): data unavailable{price_str}")

    return "\n".join(lines)


def send_email_report(body: str, recipient: str = None):
    """发送邮件报告"""
    recipient = recipient or EMAIL_CONFIG["recipient_email"]
    today = datetime.now().strftime("%Y-%m-%d")

    msg = MIMEMultipart()
    msg['From'] = EMAIL_CONFIG["sender_email"]
    msg['To'] = recipient
    msg['Subject'] = f"📅 Earnings Watcher — {today}"

    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"])
        server.starttls()
        server.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
        server.send_message(msg)
        server.quit()
        print(f"📧 Report sent to {recipient}")
        return True
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Earnings Watcher")
    parser.add_argument("--next-week", action="store_true", help="Check next week (14 days)")
    parser.add_argument("--pull-all", action="store_true", help="Pull latest earnings for all tracked companies")
    parser.add_argument("--send-email", action="store_true", help="Send email report")
    parser.add_argument("--recipient", help="Email recipient override")

    args = parser.parse_args()

    companies = load_tracked_tickers()
    if not companies:
        print("No tracked companies found in config.json")
        exit(1)

    print(f"\n📡 Earnings Watcher")
    print(f"   Tracked: {len(companies)} companies")
    print()

    tickers = [c["ticker"] for c in companies]

    # Phase 1: Check calendar
    days = 14 if args.next_week else 7
    print(f"📅 Checking earnings calendar...")
    calendar_data = check_calendar(tickers, days_ahead=days)

    this_week = calendar_data.get("this_week", [])
    if this_week:
        print(f"   ⚠️  {len(this_week)} companies reporting this week:")
        for item in this_week:
            print(f"      {item['ticker']} — {item.get('next_date', '?')}")

    upcoming = calendar_data.get("upcoming", [])
    if upcoming:
        print(f"   📆 {len(upcoming)} companies in next {days} days")

    # Phase 2: Pull earnings data (for this week or all)
    if args.pull_all:
        targets = companies
    elif this_week:
        # Only pull for companies reporting this week
        tw_tickers = {item["ticker"] for item in this_week}
        targets = [c for c in companies if c["ticker"] in tw_tickers]
    else:
        targets = []

    earnings_data = []
    if targets:
        print(f"\n📊 Pulling earnings data for {len(targets)} companies...")
        earnings_data = pull_earnings_batch(targets)

    # Phase 3: Summary
    summary = format_summary(calendar_data, earnings_data)
    print(f"\n{summary}")

    if args.send_email:
        send_email_report(summary, recipient=args.recipient)

#!/usr/bin/env python3
"""Pipeline daily report — collects consensus, aggregation, and industry results
into a structured email report sent after the daily pipeline run.

Usage:
    python3 pipeline_report.py --date 20260515
    python3 pipeline_report.py --date 20260515 --send-email
"""
import json
import smtplib
import argparse
from pathlib import Path
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


PROJECT_DIR = Path(__file__).parent
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
CONFIG_PATH = PROJECT_DIR / "config.json"


def load_email_config() -> dict:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg.get("email", {})
    return {}


def collect_report_data(date_str: str) -> dict:
    """Collect all report data for a given date."""
    report_dir = REPORT_BASE / date_str
    if not report_dir.exists():
        return {"error": f"Report directory not found: {report_dir}"}

    data = {
        "date": date_str,
        "consensus": [],
        "aggregated": [],
        "industries": [],
        "stats": {"total_reports": 0, "companies_covered": 0, "consensus_count": 0},
    }

    # Collect consensus reports
    for f in sorted(report_dir.glob("CONSENSUS_*.md")):
        company = f.stem.replace("CONSENSUS_", "").replace(f"_{date_str}", "").replace("_", " ")
        text = f.read_text(encoding="utf-8")
        data["consensus"].append({"company": company, "content": text, "path": str(f)})

    # Collect aggregated logic
    for f in sorted(report_dir.glob("AGGREGATED_*.md")):
        company = f.stem.replace("AGGREGATED_", "").replace(f"_{date_str}", "").replace("_", " ")
        text = f.read_text(encoding="utf-8")
        data["aggregated"].append({"company": company, "content": text, "path": str(f)})

    # Collect industry reports
    industry_dir = PROJECT_DIR / "data_sources" / "industries"
    if industry_dir.exists():
        for f in sorted(industry_dir.glob("*.md")):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime.strftime("%Y%m%d") == date_str:
                data["industries"].append({
                    "name": f.stem.replace("_", " ").title(),
                    "content": f.read_text(encoding="utf-8")[:2000],
                    "path": str(f),
                })

    # Stats
    analysis_files = list(report_dir.rglob("*_analysis.md"))
    data["stats"]["total_reports"] = len(analysis_files)
    data["stats"]["consensus_count"] = len(data["consensus"])
    data["stats"]["companies_covered"] = len(
        set(c["company"] for c in data["consensus"])
    )

    return data


def format_html_report(data: dict) -> str:
    """Format collected data into an HTML email report."""
    date_str = data.get("date", datetime.now().strftime("%Y%m%d"))
    stats = data.get("stats", {})

    html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; max-width: 720px;
         margin: 0 auto; padding: 20px; color: #1a1a2e; line-height: 1.6; }}
  h1 {{ color: #e94560; border-bottom: 2px solid #e94560; padding-bottom: 8px; }}
  h2 {{ color: #0f3460; margin-top: 28px; }}
  h3 {{ color: #16213e; }}
  .stat {{ display: inline-block; background: #0f3460; color: #fff;
           padding: 6px 14px; border-radius: 4px; margin: 4px; font-size: 0.9em; }}
  .company-section {{ background: #f8f9fa; border-left: 3px solid #e94560;
                      padding: 12px 16px; margin: 16px 0; border-radius: 0 6px 6px 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th {{ background: #0f3460; color: #fff; padding: 8px 12px; text-align: left; }}
  td {{ padding: 6px 12px; border-bottom: 1px solid #ddd; }}
  .footer {{ margin-top: 30px; font-size: 0.8em; color: #888; border-top: 1px solid #ddd; padding-top: 10px; }}
  .no-data {{ color: #999; font-style: italic; }}
</style></head><body>
<h1>Hermes 投研日报</h1>
<h2>{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}</h2>

<div style="margin:12px 0">
  <span class="stat">报告 {stats.get('total_reports', 0)} 份</span>
  <span class="stat">覆盖 {stats.get('companies_covered', 0)} 家公司</span>
  <span class="stat">共识 {stats.get('consensus_count', 0)} 篇</span>
</div>
"""

    # Consensus section
    consensus = data.get("consensus", [])
    if consensus:
        html += "<h2>共识摘要</h2>"
        for c in consensus:
            # Strip markdown headings to avoid duplicate titles in email
            content = c["content"]
            # Keep first 3000 chars for email readability
            if len(content) > 3000:
                content = content[:3000] + "\n\n... (完整内容见 Dashboard)"
            # Convert basic markdown to HTML-like text
            content = content.replace("##", "<h3>").replace("###", "<h4>")
            content = content.replace("\n\n", "<br><br>").replace("\n", "<br>")
            html += f"""
<div class="company-section">
  <h3>{c['company']}</h3>
  {content}
</div>"""
    else:
        html += '<p class="no-data">今日无共识报告生成（可能报告数不足2份）</p>'

    # Aggregated logic summary
    aggregated = data.get("aggregated", [])
    if aggregated:
        html += "<h2>逻辑链聚合</h2>"
        for a in aggregated:
            n_drivers = a["content"].count("### Driver")
            html += f"<p><strong>{a['company']}</strong>: {n_drivers} 个驱动因素已聚合</p>"

    # Industry updates
    industries = data.get("industries", [])
    if industries:
        html += "<h2>行业主题更新</h2>"
        for ind in industries:
            html += f"<p><strong>{ind['name']}</strong>: 报告已更新</p>"

    html += f"""
<div class="footer">
  <p>Hermes 投研系统 · 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
  <p>完整报告: <a href="http://localhost:8899">Dashboard</a></p>
</div>
</body></html>"""
    return html


def send_email(html_body: str, date_str: str, recipient: str = None) -> bool:
    """Send the pipeline report via email."""
    config = load_email_config()

    sender = config.get("sender_email", "")
    password = config.get("sender_password", "")
    recipient = recipient or config.get("recipient_email", "")

    if not sender or not password:
        print("❌ Email config incomplete. Set sender_email and sender_password in config.json")
        return False

    if not recipient:
        print("❌ No recipient email configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = (
        f"Hermes 投研日报 - {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    )
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP(config["smtp_server"], config["smtp_port"])
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print(f"📧 Pipeline report sent to {recipient}")
        return True
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False


# ============ CLI ============

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Daily Report")
    parser.add_argument("--date", help="Target date (YYYYMMDD), default: today")
    parser.add_argument("--send-email", action="store_true", help="Send email report")
    parser.add_argument("--recipient", help="Email recipient override")
    parser.add_argument("--output", help="Save HTML report to file")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    data = collect_report_data(date_str)

    if "error" in data:
        print(f"❌ {data['error']}")
        exit(1)

    html = format_html_report(data)

    if args.output:
        Path(args.output).write_text(html, encoding="utf-8")
        print(f"📄 Report saved: {args.output}")

    if args.send_email:
        send_email(html, date_str, recipient=args.recipient)
    elif not args.output:
        # Print summary to console
        stats = data["stats"]
        print(f"\nPipeline Report — {date_str}")
        print(f"  Reports: {stats['total_reports']}")
        print(f"  Companies: {stats['companies_covered']}")
        print(f"  Consensus: {stats['consensus_count']}")
        print(f"  HTML size: {len(html)} chars")
        print(f"\n  Use --send-email to send, --output to save")

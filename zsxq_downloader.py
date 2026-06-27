#!/usr/bin/env python3
"""
知识星球 投行报告下载器 v5
通过 MCP HTTP 接口调用 API，支持行业/公司过滤、每日定时、邮件通知
"""

import os
import re
import json
import time
import random
import fcntl
import smtplib
import sqlite3
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============ 配置区 ============
GROUPS = [
    {"id": "51111812185184", "name": "Economic国际投行研报", "filter": True},
    {"id": "88888812815442", "name": "半导体大佬的会议室", "filter": False},
]
SAVE_BASE_DIR = Path.home() / "hermes_reports" / "Investment_Banking_Report"

# ZSXQ API key — set via env var ZSXQ_API_KEY, or edit config.json
_MCP_KEY = os.environ.get("ZSXQ_API_KEY", "")
MCP_URL = f"https://mcp.zsxq.com/topic/mcp?api_key={_MCP_KEY}" if _MCP_KEY else ""

# 邮件配置 — set via env vars, or edit config.json
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 587
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")

# 延迟设置
DOWNLOAD_INTERVAL = (10, 30)  # 秒

# ============ 行业/公司过滤列表（从 config.json 加载）============

# 弱信号关键词：这些词太通用，单独命中不足以判定相关。
WEAK_SIGNAL_INDUSTRY_KW = {
    "server", "cloud", "compute", "computing", "inference", "training",
    "capacity", "utilization", "capex", "node",
    "memory", "storage", "flash", "ssd", "ddr", "lpddr", "nand",
    "switch", "networking", "ethernet", "infiniband", "serdes",
    "energy", "renewable",
    "rack", "liquid cooling",
    "thermal", "散热", "冷板", "浸没式", "cold plate", "cdu",
    "机柜", "ai服务器", "gpu服务器",
    "pdu", "busbar", "bbu", "供电", "功耗",
    "背板", "铜缆",
    "hyperscale", "超大规模",
    "connecting", "interconnect",
    "3D packaging", "fan-out", "info",
    "foundry", "wafer", "fab",
    "power semiconductor", "power management", "power supply", "pmic",
    "电源管理", "功率半导体", "产能", "资本支出", "算力",
    "光通信", "存储", "内存", "晶圆代工", "先进封装",
}

# 弱信号公司关键词：太短或太通用的公司别名
WEAK_SIGNAL_COMPANY_KW = {
    "gf", "meta", "lite", "fn", "mu", "tsm",
}

# 短关键词（≤3字符）必须做单词边界匹配
SHORT_KW_MIN_LEN = 4

import re as _re

def _kw_match(kw: str, text: str) -> bool:
    """关键词匹配：短词做单词边界匹配，长词做子串匹配"""
    kw_lower = kw.lower()
    text_lower = text.lower()
    if len(kw) <= SHORT_KW_MIN_LEN:
        pattern = r'(?<![a-zA-Z])' + _re.escape(kw_lower) + r'(?![a-zA-Z])'
        return bool(_re.search(pattern, text_lower))
    return kw_lower in text_lower


# ============ MCP API 调用 ============

def _call_api(method: str, path: str, query: dict = None) -> dict:
    """通过 MCP HTTP 接口调用知识星球 API，返回 resp_data 内容"""
    arguments = {"method": method, "path": path}
    if query:
        arguments["query"] = query

    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": "tools/call",
        "params": {
            "name": "call_zsxq_api",
            "arguments": arguments
        }
    }

    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            for line in body.split("\n"):
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "result" in data:
                        result = json.loads(data["result"]["content"][0]["text"])
                        if result.get("success"):
                            return result.get("body", {}).get("resp_data", result)
                        else:
                            raise RuntimeError(f"API error: status={result.get('status_code')}")
                    elif "error" in data:
                        raise RuntimeError(f"MCP error: {data['error']}")
            raise RuntimeError("No valid response from MCP")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")


def _download_file_content(url: str, save_path: Path) -> bool:
    """下载文件内容（CDN URL）"""
    result = subprocess.run(
        ["curl", "-sS", "-o", str(save_path), "--max-time", "180", url],
        capture_output=True, text=True, timeout=200
    )
    return result.returncode == 0 and save_path.exists() and save_path.stat().st_size > 0


# ============ 配置加载 ============

def _load_config():
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def get_industry_keywords():
    cfg = _load_config()
    if cfg and "tracking" in cfg:
        result = {}
        for ind in cfg["tracking"].get("industries", []):
            if ind.get("active", True):
                result[ind["slug"]] = ind.get("keywords", [])
        if result:
            return result
    return {
        "ai-chip": ["GPU", "TPU", "ASIC", "AI chip", "AI semiconductor"],
        "memory": ["memory", "hbm", "dram", "nand"],
        "foundry": ["foundry", "TSMC", "SMIC", "GlobalFoundries"],
    }


def get_company_keywords():
    cfg = _load_config()
    if cfg and "tracking" in cfg:
        keywords = []
        for c in cfg["tracking"].get("companies", []):
            if c.get("active", True):
                keywords.extend(c.get("keywords", []))
                keywords.append(c["name"])
                ticker = c.get("ticker", "")
                if ticker:
                    ticker_clean = ticker.split(".")[0].strip()
                    if ticker_clean and len(ticker_clean) > 2 and ticker_clean not in keywords:
                        keywords.append(ticker_clean)
        if keywords:
            return list(set(keywords))
    return ["TSMC", "NVIDIA", "AMD", "MediaTek", "Broadcom", "Qualcomm", "Intel",
            "Micron", "SK Hynix", "Samsung", "Marvell", "SMIC", "GlobalFoundries"]


def _classify_matches(keywords, text):
    strong = []
    weak = []
    for kw in keywords:
        if _kw_match(kw, text):
            if kw.lower() in WEAK_SIGNAL_INDUSTRY_KW or kw.lower() in WEAK_SIGNAL_COMPANY_KW:
                weak.append(kw)
            else:
                strong.append(kw)
    return strong, weak


def match_industry(text):
    strong_matched = set()
    weak_matched = set()
    for category, keywords in get_industry_keywords().items():
        strong, weak = _classify_matches(keywords, text)
        for kw in strong:
            strong_matched.add(category)
        for kw in weak:
            weak_matched.add(category)
    return list(strong_matched), list(weak_matched)


def match_company(text):
    company_kws = get_company_keywords()
    strong, weak = _classify_matches(company_kws, text)
    return strong, weak


def should_download(file_name, text=""):
    """判断是否应该下载此文件。必须至少命中一个强信号关键词。"""
    combined = file_name + " " + text
    ind_strong, ind_weak = match_industry(combined)
    co_strong, co_weak = match_company(combined)
    has_strong = bool(ind_strong or co_strong)
    if has_strong:
        return True, list(set(ind_strong + ind_weak)), list(set(co_strong + co_weak))
    return False, [], []


# ============ 数据库 ============

def init_db():
    db_path = Path(__file__).parent / "zsxq_reports.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloaded_files (
            file_id INTEGER PRIMARY KEY,
            file_name TEXT,
            download_date TEXT,
            download_time TEXT,
            status TEXT,
            industry_match TEXT,
            company_match TEXT
        )
    ''')
    conn.commit()
    return conn


def is_already_downloaded(conn, file_id):
    cursor = conn.cursor()
    cursor.execute("SELECT file_id FROM downloaded_files WHERE file_id = ?", (file_id,))
    return cursor.fetchone() is not None


def record_download(conn, file_id, file_name, status, industry_match, company_match,
                    date_str: str = None):
    cursor = conn.cursor()
    if date_str:
        today = date_str
    else:
        today = datetime.now().strftime("%Y%m%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT OR REPLACE INTO downloaded_files
        (file_id, file_name, download_date, download_time, status, industry_match, company_match)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (file_id, file_name, today, now, status,
          ",".join(industry_match), ",".join(company_match)))
    conn.commit()


def get_today_dir(date_str: str = None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    save_dir = SAVE_BASE_DIR / date_str
    save_dir.mkdir(parents=True, exist_ok=True)
    return save_dir


# ============ 核心下载流程 ============

def fetch_all_files(group_id: str, max_pages: int = 15, errors: list = None) -> list[dict]:
    """获取指定星球所有文件列表（用于首次回填，不限制今日）"""
    all_files = []
    query = {"count": 30, "sort": "by_create_time"}
    page = 0

    while page < max_pages:
        page += 1
        time.sleep(random.uniform(2, 5))

        try:
            data = _call_api("GET", f"/v2/groups/{group_id}/files", query)
        except Exception as e:
            msg = f"API 调用失败: {e}"
            print(f"   ❌ {msg}")
            if errors is not None:
                errors.append(msg)
            break

        files = data.get("files", [])
        if not files:
            break

        for f in files:
            finfo = f.get("file", {})
            topic = f.get("topic", {})
            talk = topic.get("talk", {})
            finfo["_topic_text"] = talk.get("text", "")
            all_files.append(finfo)

        next_index = data.get("index")
        if not next_index:
            break
        query["index"] = next_index

    return all_files


def fetch_today_files(group_id: str, errors: list = None, date_str: str = None) -> list[dict]:
    """获取指定星球今日文件列表（通过 MCP）"""
    all_files = []
    if date_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    else:
        today_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    query = {"count": 30, "sort": "by_create_time"}
    page = 0

    while page < 10:
        page += 1
        time.sleep(random.uniform(2, 5))

        try:
            data = _call_api("GET", f"/v2/groups/{group_id}/files", query)
        except Exception as e:
            msg = f"API 调用失败: {e}"
            print(f"   ❌ {msg}")
            if errors is not None:
                errors.append(msg)
            break

        files = data.get("files", [])
        if not files:
            break

        for f in files:
            finfo = f.get("file", {})
            create_time = finfo.get("create_time", "")
            if today_str in create_time:
                # Include topic text for better keyword matching
                topic = f.get("topic", {})
                talk = topic.get("talk", {})
                finfo["_topic_text"] = talk.get("text", "")
                all_files.append(finfo)
            elif all_files and today_str not in create_time:
                return all_files

        next_index = data.get("index")
        if not next_index:
            break
        query["index"] = next_index

    return all_files


def download_file(file_id, file_name, save_dir, conn, date_str: str = None) -> str:
    """下载单个文件"""
    if is_already_downloaded(conn, file_id):
        print(f"       ⏭️ 已下载过，跳过: {file_name[:40]}")
        return "skipped"

    # 获取下载链接
    for attempt in range(3):
        if attempt > 0:
            wait = random.uniform(15, 30)
            print(f"       🔄 重试 {attempt+1}/3，等待 {wait:.0f}秒...")
            time.sleep(wait)
        try:
            data = _call_api("GET", f"/v2/files/{file_id}/download_url")
            download_url = data.get("download_url", "")
            if download_url:
                break
        except Exception as e:
            print(f"       ❌ 获取下载链接异常: {e}")
            if attempt == 2:
                return "failed"

    if not download_url:
        print(f"       🚫 无下载链接")
        return "failed"

    # 清理文件名
    safe_name = "".join(c for c in file_name if c.isalnum() or c in '._- ()[]{}「」').strip()
    if not safe_name.endswith('.pdf'):
        safe_name += '.pdf'
    save_path = save_dir / safe_name

    # 下载文件
    print(f"       📥 下载中...")
    if _download_file_content(download_url, save_path):
        size = os.path.getsize(save_path)
        print(f"       ✅ {size/1024/1024:.2f}MB -> {safe_name[:45]}")
        _, industries, companies = should_download(file_name)
        record_download(conn, file_id, file_name, "success", industries, companies,
                        date_str=date_str)
        return "success"
    else:
        print(f"       ❌ 下载失败")
        return "failed"


# ============ 邮件与摘要 ============

def extract_summary_from_analysis(pdf_path: str) -> str:
    pdf_path = Path(pdf_path)
    md_path = pdf_path.parent / f"{pdf_path.stem}_analysis.md"

    if not md_path.exists():
        print(f"     🔬 Analyzing: {pdf_path.name[:50]}...")
        try:
            from pdf_vision_analyzer import PDFReportAnalyzer
            pipeline = PDFReportAnalyzer()
            pipeline.analyze_and_save(str(pdf_path), output_dir=str(pdf_path.parent))
        except Exception as e:
            print(f"     ❌ Analysis failed: {e}")
            return "    [分析失败]"

    if md_path.exists():
        md_text = md_path.read_text(encoding="utf-8")
        lines = md_text.split("\n")
        summary_lines = []
        in_summary = False
        for line in lines:
            if "## 报告摘要" in line or "## 核心发现" in line:
                in_summary = True
                continue
            if in_summary:
                if line.startswith("## ") and "报告摘要" not in line and "核心发现" not in line:
                    break
                stripped = line.strip()
                if stripped:
                    summary_lines.append(stripped)
        if not summary_lines:
            for line in lines:
                if line.startswith("- **"):
                    stripped = line.strip()
                    if stripped:
                        summary_lines.append(stripped)
        if summary_lines:
            return "\n".join(f"    {s}" for s in summary_lines[:15])
    return "    [摘要未生成]"


def _send_notification(body: str, today_str: str, stats: str):
    """Send a lightweight notification email (no analysis summaries)."""
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = f"每日投行报告总结 - {today_str} ({stats})"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 邮件已发送至 {RECIPIENT_EMAIL}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


def send_email(success_list, failed_list, total_processed,
               summaries: dict = None, error_messages=None):
    if error_messages is None:
        error_messages = []
    if summaries is None:
        summaries = {}

    today_str = datetime.now().strftime("%Y-%m-%d")

    if not success_list and not failed_list and not error_messages:
        # All files were already downloaded — send a brief notification
        body = f"每日投行报告总结 - {today_str}\n\n"
        body += f"处理文件数: {total_processed}\n"
        body += "状态: 全部已下载过，无新文件\n"
        _send_notification(body, today_str, "0/0")
        return

    body = f"每日投行报告总结 - {today_str}\n\n"
    body += f"处理文件数: {total_processed}\n"
    body += f"成功: {len(success_list)}\n"
    body += f"失败: {len(failed_list)}\n"

    if error_messages:
        body += f"\n⚠️ 注意/错误 ({len(error_messages)})：\n"
        for err in error_messages:
            body += f"  - {err}\n"

    if success_list:
        body += f"\n{'─' * 50}\n"
        from utils import extract_bank_from_filename
        by_bank: dict[str, list[str]] = {}
        for f in success_list:
            bank = extract_bank_from_filename(f)
            by_bank.setdefault(bank, []).append(f)
        for bank in sorted(by_bank.keys()):
            for f in by_bank[bank]:
                body += f"\n  📄 {bank} — {f}\n"
                summary = summaries.get(f, "")
                if summary:
                    body += f"{summary}\n"
                else:
                    body += f"    [等待分析]\n"

    if failed_list:
        body += f"\n{'─' * 50}\n"
        body += f"=== 下载失败 ({len(failed_list)}) ===\n"
        for f in failed_list:
            body += f"  - {f}\n"

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = f"每日投行报告总结 - {today_str} ({len(success_list)}/{total_processed})"
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 邮件已发送至 {RECIPIENT_EMAIL}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")


# ============ Lock ============

DOWNLOADER_LOCK = Path(__file__).parent / ".zsxq_downloader.lock"


def _acquire_lock():
    fd = os.open(str(DOWNLOADER_LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        os.close(fd)
        return None


# ============ 主入口 ============

def run_daily(date_str: str = None):
    lock_fd = _acquire_lock()
    if lock_fd is None:
        print("❌ Downloader lock held by another instance, exiting.")
        return

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 投行报告下载器 v5 启动")
    print(f"📁 保存目录: {SAVE_BASE_DIR}")
    print(f"🔐 认证方式: MCP HTTP")
    print(f"📡 覆盖星球: {len(GROUPS)} 个")
    if date_str:
        print(f"📅 指定日期: {date_str}")

    conn = init_db()
    today_dir = get_today_dir(date_str)
    print(f"📂 今日目录: {today_dir}")

    error_messages = []

    # 收集所有星球今日文件
    all_files = []
    for g in GROUPS:
        print(f"\n📡 [{g['name']}] 获取文件列表...")
        try:
            if g["filter"]:
                # 投行频道：只取今日文件 + 关键词过滤
                files = fetch_today_files(g["id"], errors=error_messages, date_str=date_str)
            else:
                # 半导体频道：拉全部历史文件，is_already_downloaded 去重
                files = fetch_all_files(g["id"], max_pages=5, errors=error_messages)

            print(f"   📋 {g['name']}: {len(files)} 个文件")
            for finfo in files:
                finfo["_group_name"] = g["name"]
                finfo["_filter"] = g["filter"]
            all_files.extend(files)
        except Exception as e:
            msg = f"获取文件列表失败 [{g['name']}]: {e}"
            print(f"   ❌ {msg}")
            error_messages.append(msg)

    print(f"\n📋 今日总计: {len(all_files)} 个文件")

    # 过滤 + 匹配
    matched_files = []
    skipped_filter = 0
    skipped_nomatch = 0

    for finfo in all_files:
        file_name = finfo.get("name", "")
        topic_text = finfo.get("_topic_text", "")
        do_filter = finfo.get("_filter", True)

        if do_filter:
            should_down, industries, companies = should_download(file_name, topic_text)
            if not should_down:
                skipped_nomatch += 1
                continue
        else:
            # 半导体频道：全部下载，简单匹配行业标签
            industries, _ = match_industry(file_name + " " + topic_text)
            _, companies = match_company(file_name + " " + topic_text)
            if not companies:
                companies = [finfo.get("_group_name", "")]

        matched_files.append({
            "file_id": finfo.get("file_id"),
            "name": file_name,
            "size": finfo.get("size", 0),
            "industries": list(set(industries)) if isinstance(industries, list) else [],
            "companies": list(set(companies)) if isinstance(companies, list) else [],
            "group": finfo.get("_group_name", ""),
        })
        print(f"   ✅ [{finfo.get('_group_name', '')}] {file_name[:50]}")

    if skipped_nomatch > 0:
        print(f"   ⏭️ 关键词不匹配跳过: {skipped_nomatch} 个")

    print(f"\n🎯 匹配文件: {len(matched_files)} 个")

    # 下载
    success_list = []
    failed_list = []

    for i, f in enumerate(matched_files, 1):
        file_id = f["file_id"]
        file_name = f["name"]
        size_kb = f["size"] / 1024

        print(f"\n[{i}/{len(matched_files)}] [{f['group']}] {file_name[:50]}... ({size_kb:.0f}KB)")

        result = download_file(file_id, file_name, today_dir, conn, date_str=date_str)

        if result == "success":
            success_list.append(file_name)
        elif result == "failed":
            failed_list.append(file_name)

        if i < len(matched_files):
            wait = random.uniform(*DOWNLOAD_INTERVAL)
            print(f"   ⏱️ 等待 {wait:.0f}秒...")
            time.sleep(wait)

    # 邮件通知（不含分析摘要，分析由 pipeline 在 5:00 执行）
    print("\n📧 发送邮件通知...")
    send_email(success_list, failed_list, len(matched_files),
               summaries={}, error_messages=error_messages)

    # 总结
    print(f"\n{'='*60}")
    print(f"📊 下载完成!")
    print(f"   处理: {len(matched_files)} 个")
    print(f"   成功: {len(success_list)} 个")
    print(f"   失败: {len(failed_list)} 个")
    print(f"   保存: {today_dir}")
    print(f"{'='*60}\n")

    conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="知识星球投行报告下载器")
    parser.add_argument("--date", help="Target date (YYYYMMDD), default: today")
    args = parser.parse_args()
    run_daily(date_str=args.date)

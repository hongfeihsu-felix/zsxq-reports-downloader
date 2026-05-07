#!/usr/bin/env python3
"""
知识星球 投行报告下载器 v3
支持行业/公司过滤、每日定时、邮件通知
"""

import os
import re
import json
import time
import random
import smtplib
import sqlite3
import requests
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse, parse_qsl, unquote

# 代理配置
PROXY = {
    "http": "socks5://127.0.0.1:7897",
    "https": "socks5://127.0.0.1:7897"
}

# 全局 session
session = requests.Session()
session.proxies.update(PROXY)


# ============ 配置区 ============
COOKIE = "abtest_env=product; zsxq_access_token=51BF16B1-43E2-4E4B-B875-4A346A01BB2C_BF80320C8CF13163"
GROUP_ID = "51111812185184"
SAVE_BASE_DIR = Path.home() / "hermes_reports" / "Investment_Banking_Report"

# 邮件配置
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 587
SENDER_EMAIL = "hongfeihsu@foxmail.com"
SENDER_PASSWORD = "hfedvenxbtsyebff"
RECIPIENT_EMAIL = "hongfeihsu@foxmail.com"

# 延迟设置
MIN_DELAY = 2.0
MAX_DELAY = 5.0
DOWNLOAD_INTERVAL = (30, 90)  # 秒

# ============ 行业/公司过滤列表 ============
# 格式：行业关键词 -> 匹配关键词列表（匹配任一即下载）
INDUSTRY_KEYWORDS = {
    "AI_Core": [
        "GPU", "TPU", "ASIC", "AI chip", "AI semiconductor", "AI芯片",
        "H100", "H200", "GB200", "B100", "TPU","MTIA","Maia","MI300", "Gaudi", "Trainium"
    ],
    "Memory": [
        "HBM", "DRAM", "SRAM", "NAND", "flash memory", "memory semiconductor",
        "SK Hynix", "Samsung memory", "Micron memory", "SDNK", "WDC", "STX",
        "兆易创新", "Gigadevice", "佰维存储", "江波龙", "长江存储"
    ],
    "Interconnect": [
        "optical module", "光模块", "光通信", "interconnect", "connecting",
        "Lumentum", "Coherent", "Fabrinet", "新易盛", "中际旭创", "联特科技",
        "光迅科技", "剑桥科技"
    ],
    "Power": [
        "power electronics", "power semiconductor", "电源管理", "功率半导体",
        "X-Energy", "BE Inc", "Bloom Energy", "清洁能源", "氢能"
    ],
    "Fab": [
        "foundry", "晶圆代工", "TSMC", "SMIC", "UMC", "Intel Foundry",
        "Samsung Foundry", "GlobalFoundries", "TowerSemi", "PSMC", "Huahong"
    ],
    "Fabless": [
        "Broadcom", "Qualcomm", "MediaTek", "Marvell", "fabless",
        "NVIDIA", "AMD", "Xilinx", "Marvell"
    ]
}

# 公司关键词（精确匹配）
COMPANY_KEYWORDS = [
    # Foundries
    "TSMC", "SMIC", "UMC", "Intel Foundry", "Samsung Foundry", "Global Foundries",
    "Tower Semiconductor", "PSMC", "Huahong Group", "华虹",

    # Fabless
    "Broadcom", "Qualcomm", "MediaTek", "Marvell",

    # Interconnect/Connecting
    "Lumentum", "Coherent", "Fabrinet", "新易盛", "中际旭创", "联特科技",

    # Memory
    "SK Hynix", "Samsung", "Micron", "SDNK", "WDC", "STX",
    "兆易创新", "Gigadevice", "佰维存储", "江波龙",

    # US Giant Tech
    "NVDA", "AMZN", "MSFT", "META", "TSLA", "GOOGLE", "AAPL",
    "AMD", "Intel", "MRVL", "ORCL", "AVGO", "NVIDIA",

    # Power
    "X-Energy", "BE Inc", "Bloom Energy",

    # AI Application
    "Palantir", "CoreWeave", "Tempus AI",

    # MISC
    "AMKR", "MPWR", "BABA", "阿里巴巴"
]

# 排除关键词（明确不要的）
EXCLUDE_KEYWORDS = []  # 可添加排除词


def match_industry(text):
    """检查文本是否匹配行业关键词"""
    text_lower = text.lower()
    matched = []
    for category, keywords in INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.append(category)
                break
    return list(set(matched))


def match_company(text):
    """检查文本是否匹配公司关键词"""
    text_lower = text.lower()
    matched = []
    for kw in COMPANY_KEYWORDS:
        if kw.lower() in text_lower:
            matched.append(kw)
    return list(set(matched))


def should_download(file_name, text=""):
    """判断是否应该下载此文件"""
    # 如果有text，同时检查行业和公司
    # 如果只有file_name，只检查公司（更宽松）

    combined = file_name + " " + text
    matched_industries = match_industry(combined)
    matched_companies = match_company(combined)

    # 有行业匹配或有公司匹配则下载
    if matched_industries or matched_companies:
        return True, matched_industries, matched_companies

    return False, [], []


def get_stealth_headers():
    """生成隐蔽请求头"""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/132.0",
    ]
    ua = random.choice(user_agents)

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": COOKIE,
        "Origin": "https://wx.zsxq.com",
        "Referer": "https://wx.zsxq.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": ua,
        "X-Version": "2.91.0"
    }


def smart_delay():
    """智能延迟"""
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)


def init_db():
    """初始化SQLite数据库"""
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
    """检查文件是否已下载"""
    cursor = conn.cursor()
    cursor.execute("SELECT file_id FROM downloaded_files WHERE file_id = ?", (file_id,))
    return cursor.fetchone() is not None


def record_download(conn, file_id, file_name, status, industry_match, company_match):
    """记录下载结果"""
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y%m%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute('''
        INSERT OR REPLACE INTO downloaded_files
        (file_id, file_name, download_date, download_time, status, industry_match, company_match)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (file_id, file_name, today, now, status,
          ",".join(industry_match), ",".join(company_match)))

    conn.commit()


def get_today_dir():
    """获取今天的保存目录"""
    today_str = datetime.now().strftime("%Y%m%d")
    save_dir = SAVE_BASE_DIR / today_str
    save_dir.mkdir(parents=True, exist_ok=True)
    return save_dir


def get_download_url(file_id):
    """获取文件下载链接"""
    url = f"https://api.zsxq.com/v2/files/{file_id}/download_url"

    for attempt in range(5):
        if attempt > 0:
            wait = random.uniform(15, 30)
            print(f"       🔄 重试 {attempt+1}/5，等待 {wait:.0f}秒...")
            time.sleep(wait)

        try:
            headers = get_stealth_headers()
            resp = session.get(url, headers=headers, timeout=30)
            data = resp.json()

            if data.get("succeeded"):
                return data.get("resp_data", {}).get("download_url")

            error_code = data.get("code")
            if error_code == 1030:
                print(f"       🚫 权限不足 (1030)")
                return None

        except Exception as e:
            print(f"       ❌ 请求异常: {e}")

    return None


def download_file(file_id, file_name, save_dir, conn):
    """下载单个文件"""
    # 检查是否已下载
    if is_already_downloaded(conn, file_id):
        print(f"       ⏭️ 已下载过，跳过: {file_name[:40]}")
        return "skipped"

    # 获取下载链接
    download_url = get_download_url(file_id)
    if not download_url:
        return "failed"

    try:
        resp = session.get(download_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=180, stream=True)

        if resp.status_code != 200:
            print(f"       ❌ HTTP {resp.status_code}")
            return "failed"

        # 清理文件名
        safe_name = "".join(c for c in file_name if c.isalnum() or c in '._- ()[]{}「」').strip()
        if not safe_name.endswith('.pdf'):
            safe_name += '.pdf'

        save_path = save_dir / safe_name

        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        size = os.path.getsize(save_path)
        print(f"       ✅ {size/1024/1024:.2f}MB -> {safe_name[:45]}")

        # 匹配行业和公司
        _, industries, companies = should_download(file_name)
        record_download(conn, file_id, file_name, "success", industries, companies)

        return "success"

    except Exception as e:
        print(f"       ❌ 下载异常: {e}")
        return "failed"


def send_email(success_list, failed_list, total_processed, error_messages=None):
    """发送邮件通知"""
    if error_messages is None:
        error_messages = []

    # 即使没有成功/失败也要发送（如果有错误信息）
    if not success_list and not failed_list and not error_messages:
        return

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 构建邮件内容
    body = f"""投行报告下载完成 - {today_str}

处理文件数: {total_processed}
成功: {len(success_list)}
失败: {len(failed_list)}
"""

    # 错误/警告信息
    if error_messages:
        body += f"""
⚠️ 注意/错误 ({len(error_messages)})：
"""
        for err in error_messages:
            body += f"  - {err}\n"

    if success_list:
        body += f"""
=== 成功下载 ({len(success_list)}) ===
"""
        for f in success_list:
            body += f"  - {f}\n"

    if failed_list:
        body += f"""
=== 下载失败 ({len(failed_list)}) ===
"""
        for f in failed_list:
            body += f"  - {f}\n"

    # 创建邮件
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL

    # 如果有错误，邮件主题加前缀
    if error_messages:
        subject_prefix = "⚠️"
    else:
        subject_prefix = "📥"

    msg['Subject'] = f"{subject_prefix} 投行报告下载完成 - {today_str} ({len(success_list)}/{total_processed})"

    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # 发送
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 邮件已发送至 {RECIPIENT_EMAIL}")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")


def fetch_files_by_date(target_date):
    """获取指定日期的文件列表"""
    headers = get_stealth_headers()

    url = f"https://api.zsxq.com/v2/groups/{GROUP_ID}/files"
    params = {"count": 30, "sort": "by_create_time"}

    all_files = []
    page = 0

    while page < 5:  # 最多5页
        page += 1
        smart_delay()

        try:
            resp = session.get(url, headers=headers, params=params, timeout=30)
            data = resp.json()

            if not data.get("succeeded"):
                break

            files = data.get("resp_data", {}).get("files", [])
            if not files:
                break

            for f in files:
                finfo = f.get("file", {})
                create_time = finfo.get("create_time", "")

                # 检查是否为目标日期
                if target_date in create_time:
                    all_files.append(finfo)
                elif len(all_files) > 0:
                    # 假设文件按时间排序，遇到更早的日期就停止
                    break

            # 获取下一页
            index = data.get("resp_data", {}).get("index")
            if index:
                params["index"] = index
            else:
                break

        except Exception as e:
            print(f"   ❌ 获取文件列表异常: {e}")
            break

    return all_files


def run_daily():
    """每日运行主函数"""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 投行报告下载器启动")
    print(f"📁 保存目录: {SAVE_BASE_DIR}")
    print(f"🏠 星球ID: {GROUP_ID}")

    # 初始化数据库
    conn = init_db()

    # 获取今天的保存目录
    today_dir = get_today_dir()
    print(f"📂 今日目录: {today_dir}")

    # 获取今天的日期字符串
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 获取文件列表（翻页直到不是今天的文件）
    print("\n📡 获取文件列表...")
    headers = get_stealth_headers()

    url = f"https://api.zsxq.com/v2/groups/{GROUP_ID}/files"
    params = {"count": 30, "sort": "by_create_time"}

    all_files = []
    page = 0
    error_messages = []  # 收集错误信息

    while page < 10:  # 最多10页
        page += 1
        smart_delay()

        try:
            resp = session.get(url, headers=headers, params=params, timeout=30)
            data = resp.json()

            # 检查 API 是否成功
            if not data.get("succeeded"):
                error_code = data.get("code")
                error_info = data.get("error", data.get("info", "未知错误"))

                # 检查是否是 Cookie/Token 过期问题
                if error_code in [14001, 401, 403]:
                    cookie_error = f"⚠️ Cookie/Token 可能已过期 (code={error_code})，请更新 cookie"
                    print(f"   ❌ {cookie_error}")
                    error_messages.append(cookie_error)
                elif error_code == 1059:
                    # 反爬/限流
                    warning = f"⚠️ 触发反爬机制 (code=1059)，请求被限制"
                    print(f"   ⚠️ {warning}")
                    error_messages.append(warning)
                else:
                    general_error = f"⚠️ API 请求失败 (code={error_code}): {error_info}"
                    print(f"   ❌ {general_error}")
                    error_messages.append(general_error)
                break

            files = data.get("resp_data", {}).get("files", [])
            if not files:
                print(f"   📭 没有更多文件")
                break

            # 筛选今天的文件
            for f in files:
                finfo = f.get("file", {})
                create_time = finfo.get("create_time", "")

                if today_str in create_time:
                    all_files.append(finfo)
                elif all_files and today_str not in create_time:
                    # 遇到更早的日期，停止
                    break

            # 检查是否需要继续获取
            if len(all_files) == 0:
                pass  # 继续获取

            # 获取下一页
            index = data.get("resp_data", {}).get("index")
            if index:
                params["index"] = index
            else:
                break

        except Exception as e:
            error_msg = f"⚠️ 网络请求异常: {str(e)}"
            print(f"   ❌ {error_msg}")
            error_messages.append(error_msg)
            continue

    print(f"📋 今日文件: {len(all_files)} 个")

    # 过滤匹配的文件
    matched_files = []
    for finfo in all_files:
        file_name = finfo.get("name", "")
        should_down, industries, companies = should_download(file_name)

        if should_down:
            matched_files.append({
                "file_id": finfo.get("file_id"),
                "name": file_name,
                "size": finfo.get("size", 0),
                "industries": industries,
                "companies": companies
            })
            print(f"   ✅ 匹配: {file_name[:50]}")
            print(f"      行业: {industries}, 公司: {companies}")
        else:
            print(f"   ⏭️ 跳过: {file_name[:50]}")

    print(f"\n🎯 匹配文件: {len(matched_files)} 个")

    # 下载匹配的文件
    success_list = []
    failed_list = []

    for i, f in enumerate(matched_files, 1):
        file_id = f["file_id"]
        file_name = f["name"]
        size_kb = f["size"] / 1024

        print(f"\n[{i}/{len(matched_files)}] {file_name[:50]}... ({size_kb:.0f}KB)")

        result = download_file(file_id, file_name, today_dir, conn)

        if result == "success":
            success_list.append(file_name)
        elif result == "failed":
            failed_list.append(file_name)

        # 下载间隔
        if i < len(matched_files):
            wait = random.uniform(*DOWNLOAD_INTERVAL)
            print(f"   ⏱️ 等待 {wait:.0f}秒...")
            time.sleep(wait)

    # 发送邮件通知
    print("\n📧 发送邮件通知...")
    send_email(success_list, failed_list, len(matched_files), error_messages)

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
    run_daily()
#!/usr/bin/env python3
"""
WeChat Official Account — Critical Alert 草稿推送

从最新行业报告中提取 CRITICAL 级别的关键变化，生成公众号草稿文章。
聚焦四类关键变化：
  - 宏观：AI capex、地缘政策、利率/汇率
  - 上游需求：HBM/CoWoS/Substrate 供需突变
  - 企业本质：产品良率、产能、制程节点、客户获得/丢失
  - 投行指引突变：单次 TP 变动 ≥50%、评级跃迁(多级跳)、共识骤变

用法：
  python3 wechat_push.py                          # 扫描并推送
  python3 wechat_push.py --dry-run                 # 仅生成预览，不推送
  python3 wechat_push.py --send                    # 推送草稿 + 发送预览到 openid
"""

import os
import re
import sys
import json
import time
import hashlib
import argparse
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict

PROJECT_DIR = Path(__file__).parent
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"

# ============ WeChat API ============

WECHAT_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECHAT_DRAFT_URL = "https://api.weixin.qq.com/cgi-bin/draft/add"
WECHAT_UPLOAD_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material"
WECHAT_PREVIEW_URL = "https://api.weixin.qq.com/cgi-bin/message/mass/preview"
WECHAT_DRAFT_LIST_URL = "https://api.weixin.qq.com/cgi-bin/draft/batchget"
WECHAT_DRAFT_DELETE_URL = "https://api.weixin.qq.com/cgi-bin/draft/delete"

_token_cache = {"token": "", "expires_at": 0}
_thumb_cache = {"media_id": "", "uploaded_at": 0}


def _load_wechat_config():
    cfg_path = PROJECT_DIR / "config.json"
    if not cfg_path.exists():
        return {}
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return cfg.get("wechat", {})


def get_access_token(force_refresh: bool = False) -> str:
    """获取/刷新微信 access_token (缓存2小时)"""
    global _token_cache
    now = time.time()
    if not force_refresh and _token_cache["token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    wc = _load_wechat_config()
    appid = wc.get("appid", "")
    secret = wc.get("secret", "")
    if not appid or not secret:
        raise ValueError("WeChat appid/secret not configured in config.json")

    resp = requests.get(WECHAT_TOKEN_URL, params={
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret
    }, timeout=15)
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Failed to get access_token: {data}")

    _token_cache = {"token": token, "expires_at": now + data.get("expires_in", 7200)}
    print(f"  🔑 Access token obtained (expires in {data.get('expires_in', 7200)}s)")
    return token


def _get_thumb_media_id() -> str:
    """获取/上传封面图 thumb (缓存永久素材)"""
    global _thumb_cache
    if _thumb_cache["media_id"]:
        return _thumb_cache["media_id"]

    def _upload(token: str) -> str:
        logo = PROJECT_DIR / "static" / "logo.png"
        if not logo.exists():
            return ""
        with open(logo, "rb") as f:
            resp = requests.post(
                f"{WECHAT_UPLOAD_URL}?access_token={token}&type=image",
                files={"media": ("logo.png", f, "image/png")},
                timeout=30
            )
        return resp.json().get("media_id", "")

    token = get_access_token()
    mid = _upload(token)
    # Retry once with fresh token on auth failure
    if not mid:
        token = get_access_token(force_refresh=True)
        mid = _upload(token)

    if mid:
        _thumb_cache = {"media_id": mid, "uploaded_at": time.time()}
    return mid


def push_draft(title: str, content_html: str, digest: str = "",
               author: str = "Hermes") -> str:
    """推送草稿到公众号草稿箱。返回 media_id。"""
    def _do_push(token: str) -> dict:
        thumb = _get_thumb_media_id()
        body = {
            "articles": [{
                "title": title,
                "author": author,
                "digest": digest or title[:50],
                "content": content_html,
                "content_source_url": "",
                "thumb_media_id": thumb,
                "need_open_comment": 0,
                "only_fans_can_comment": 0,
            }]
        }
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        resp = requests.post(
            f"{WECHAT_DRAFT_URL}?access_token={token}",
            data=raw,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30
        )
        return resp.json()

    token = get_access_token()
    data = _do_push(token)
    media_id = data.get("media_id", "")

    # Retry once with fresh token on auth failure
    if not media_id and data.get("errcode") in (40001, 42001):
        print(f"  ⚠️  Token invalid, refreshing and retrying...")
        token = get_access_token(force_refresh=True)
        data = _do_push(token)
        media_id = data.get("media_id", "")

    if not media_id:
        raise RuntimeError(f"Draft push failed: {data}")
    print(f"  ✅ Draft pushed — media_id: {media_id}")
    return media_id


# ============ Draft Management (list / delete / batch delete) ============

def list_drafts(count_per_page: int = 20) -> list[dict]:
    """获取公众号草稿箱的所有草稿。返回 [{media_id, title, update_time, ...}, ...]"""
    all_items = []
    offset = 0
    token = get_access_token()

    while True:
        body = {"offset": offset, "count": count_per_page, "no_content": 1}
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        resp = requests.post(
            f"{WECHAT_DRAFT_LIST_URL}?access_token={token}",
            data=raw,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30
        )
        data = resp.json()

        # Retry once with fresh token on auth failure
        if data.get("errcode") in (40001, 42001):
            token = get_access_token(force_refresh=True)
            resp = requests.post(
                f"{WECHAT_DRAFT_LIST_URL}?access_token={token}",
                data=raw,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=30
            )
            data = resp.json()

        items = data.get("item", [])
        if not items:
            break
        all_items.extend(items)
        offset += len(items)

        # Stop when fewer items returned than requested (last page)
        if len(items) < count_per_page:
            break
        time.sleep(0.2)  # Rate limit: 5 req/s max

    return all_items


def delete_draft(media_id: str) -> bool:
    """删除单个草稿。成功返回 True，失败返回 False。"""
    token = get_access_token()
    body = {"media_id": media_id}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    resp = requests.post(
        f"{WECHAT_DRAFT_DELETE_URL}?access_token={token}",
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30
    )
    data = resp.json()

    # Retry once with fresh token on auth failure
    if data.get("errcode") in (40001, 42001):
        token = get_access_token(force_refresh=True)
        resp = requests.post(
            f"{WECHAT_DRAFT_DELETE_URL}?access_token={token}",
            data=raw,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30
        )
        data = resp.json()

    if data.get("errcode") == 0:
        return True
    print(f"  ❌ Delete failed for {media_id}: {data}")
    return False


def batch_delete_drafts(
    keyword: str = "",
    keep_recent: int = 0,
    older_than_days: int = 0,
    list_only: bool = False,
    confirm: bool = True,
    delay: float = 0.3
) -> dict:
    """批量管理草稿箱。支持按关键词过滤、保留最近 N 条、按天数筛选、仅列出、全删。

    Returns:
        {"total": int, "matched": int, "deleted": int, "skipped": int, "errors": list}
    """
    print("📋 正在获取草稿列表...")
    drafts = list_drafts()

    if not drafts:
        print("  ✅ 草稿箱为空")
        return {"total": 0, "matched": 0, "deleted": 0, "skipped": 0, "errors": []}

    total = len(drafts)
    print(f"  📦 共 {total} 条草稿\n")

    # Print header
    print(f"{'#':>4}  {'media_id':<40}  {'更新时间':<22}  标题")
    print("-" * 120)

    # Sort by update_time descending
    drafts.sort(key=lambda d: d.get("update_time", 0), reverse=True)

    for i, d in enumerate(drafts, 1):
        ts = d.get("update_time", 0)
        if ts:
            time_str = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            time_str = "N/A"
        title = d.get("content", {}).get("news_item", [{}])[0].get("title", "(无标题)")
        mid = d.get("media_id", "")
        print(f"{i:>4}  {mid:<40}  {time_str:<22}  {title[:50]}")

    print("-" * 120)

    # Filter
    to_delete = drafts
    skipped = 0

    if keep_recent > 0 and len(to_delete) > keep_recent:
        skipped = len(to_delete) - keep_recent
        to_delete = to_delete[:keep_recent]

    if older_than_days > 0:
        cutoff = time.time() - older_than_days * 86400
        kept = len(to_delete)
        to_delete = [d for d in to_delete if int(d.get("update_time", 0)) < cutoff]
        filtered = kept - len(to_delete)
        if filtered:
            print(f"📅 {older_than_days} 天前的草稿: {len(to_delete)} 条（跳过 {filtered} 条较新的）")

    if keyword:
        to_delete = [d for d in to_delete if keyword.lower() in json.dumps(d, ensure_ascii=False).lower()]
        matched = len(to_delete)
        print(f'\n🔍 关键词 "{keyword}" 匹配 {matched} 条')
    else:
        matched = len(to_delete)

    if skipped:
        print(f"📌 保留最近 {keep_recent} 条，跳过 {skipped} 条")

    if list_only:
        print(f"\n📝 仅列出模式，不执行删除")
        return {"total": total, "matched": matched, "deleted": 0, "skipped": skipped, "errors": []}

    if not to_delete:
        print("\n✅ 没有匹配的草稿需要删除")
        return {"total": total, "matched": 0, "deleted": 0, "skipped": skipped, "errors": []}

    print(f"\n⚠️  即将删除 {len(to_delete)} 条草稿，该操作不可撤销。")

    if confirm:
        answer = input("确认删除？输入 yes 继续: ")
        if answer.strip().lower() != "yes":
            print("❌ 已取消")
            return {"total": total, "matched": matched, "deleted": 0, "skipped": skipped, "errors": []}

    # Execute deletion
    deleted = 0
    errors = []
    for i, d in enumerate(to_delete, 1):
        mid = d["media_id"]
        title = d.get("content", {}).get("news_item", [{}])[0].get("title", "(无标题)")
        status = f"[{i}/{len(to_delete)}]"
        if delete_draft(mid):
            print(f"  {status} 🗑  {title[:40]}")
            deleted += 1
        else:
            errors.append(mid)
        if i < len(to_delete):
            time.sleep(delay)

    print(f"\n{'='*60}")
    print(f"✅ 删除完成: {deleted}/{len(to_delete)} 成功" + (f", {len(errors)} 失败" if errors else ""))
    if errors:
        print(f"❌ 失败的 media_id: {errors}")

    return {"total": total, "matched": matched, "deleted": deleted, "skipped": skipped, "errors": errors}


def send_preview(media_id: str):
    """发送预览到配置的 openid"""
    wc = _load_wechat_config()
    openid = wc.get("openid", "")
    if not openid:
        print("  ⚠️  No openid configured, skipping preview")
        return

    token = get_access_token()
    body = {
        "touser": openid,
        "mpnews": {"media_id": media_id},
        "msgtype": "mpnews"
    }
    resp = requests.post(
        f"{WECHAT_PREVIEW_URL}?access_token={token}",
        json=body, timeout=15
    )
    data = resp.json()
    if data.get("errcode") == 0:
        print(f"  📤 Preview sent to {openid}")
    else:
        print(f"  ⚠️  Preview failed: {data}")


# ============ Critical Alert Scanner ============

def _extract_date_from_name(pdf_name: str) -> str:
    m = re.search(r'(\d{6})', pdf_name)
    if m:
        ds = m.group(1)
        return f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"
    return ""


GARBAGE_COMPANIES = {
    "unknown", "in this report", "this report", "median multiples",
    "none", "n/a", "regis resources limited", "enlight renewable energy ltd",
}

def scan_critical_alerts() -> list[dict]:
    """扫描所有 analysis，筛选 CRITICAL 级别的关键变化"""
    alerts = []
    for f in sorted(REPORT_BASE.rglob("*_analysis.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        parsed = data.get("parsed", {})
        tp = parsed.get("target_price") or {}
        new_tp = tp.get("new")
        old_tp = tp.get("old")
        if not (new_tp and old_tp and old_tp > 0):
            continue

        change = (new_tp - old_tp) / old_tp
        if abs(change) < 0.25:  # Below WATCH threshold
            continue
        # Sanity: skip obviously wrong (e.g., 80→3 parser error)
        if old_tp > 1000 and new_tp < 10:
            continue
        if change < -0.95:  # >95% drop almost certainly a parser error
            continue

        company = (parsed.get("company", "") or "Unknown").strip()
        if company.lower() in GARBAGE_COMPANIES:
            continue
        pdf_name = data.get("pdf_name", "")
        from utils import extract_bank_from_filename
        bank = extract_bank_from_filename(pdf_name)
        date = _extract_date_from_name(pdf_name)
        rating = parsed.get("rating", "") or "N/A"

        # Read analysis markdown for driver context
        md_path = f.with_suffix(".md")
        md_text = ""
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")[:4000]

        # Extract key drivers from markdown
        drivers = re.findall(
            r'(?:驱动因素|核心发现|key\s+driver|driver)[：:\s]*[-•]?\s*(.+?)(?:\n|$)',
            md_text, re.IGNORECASE
        )
        risk_signals = parsed.get("risk_signals", [])[:3]
        opp_signals = parsed.get("opportunity_signals", [])[:3]

        # Classify the change category
        category = _classify_change(md_text, change, company)

        if abs(change) >= 0.50:
            severity = "CRITICAL"
        elif abs(change) >= 0.30:
            severity = "HIGH"
        else:
            severity = "WATCH"
        alerts.append({
            "company": company,
            "bank": bank,
            "date": date,
            "rating": rating,
            "new_tp": new_tp,
            "old_tp": old_tp,
            "change_pct": change,
            "severity": severity,
            "category": category,
            "drivers": drivers[:3],
            "risks": risk_signals,
            "opportunities": opp_signals,
            "currency": tp.get("currency", ""),
        })

    # Deduplicate by bank+company+date, keep newest
    seen = {}
    for a in sorted(alerts, key=lambda x: x["date"], reverse=True):
        key = f"{a['bank']}_{a['company']}_{a['date']}"
        if key not in seen:
            seen[key] = a
    alerts = sorted(seen.values(), key=lambda x: abs(x["change_pct"]), reverse=True)

    return alerts


def _classify_change(md_text: str, change: float, company: str) -> str:
    """分类关键变化类型"""
    text_lower = md_text.lower()

    # 宏观
    macro_kw = ["capex", "capital expenditure", "geopolitical", "export control",
                "sanction", "interest rate", "fed", "tariff", "trade war"]
    if any(kw in text_lower for kw in macro_kw) and abs(change) >= 0.5:
        return "🌍 Macro / Policy"

    # 上游需求
    demand_kw = ["supply chain", "shortage", "glut", "sufficiency", "utilization",
                 "hbm", "cowos", "substrate", "capacity", "lead time"]
    if any(kw in text_lower for kw in demand_kw) and abs(change) >= 0.5:
        return "📦 Supply/Demand Shock"

    # 企业本质
    fundamental_kw = ["yield", "良率", "ramp", "爬坡", "qualification", "qual",
                      "design win", "share gain", "share loss", "customer loss",
                      "product cycle", "node", "nm", "process technology"]
    if any(kw in text_lower for kw in fundamental_kw):
        return "🏭 Fundamental Change"

    # 投行指引突变
    guidance_kw = ["guidance", "指引", "revised up", "revised down",
                   "raised target", "cut target", "above consensus", "below consensus"]
    if any(kw in text_lower for kw in guidance_kw) or abs(change) >= 0.5:
        return "📈 Guidance Shock"

    return "⚡ General Alert"


# ============ Push History & Dedup ============

PUSH_HISTORY_FILE = PROJECT_DIR / "push_history.json"
DEDUP_WINDOW_HOURS = 24
DEDUP_ESCALATION_PCT = 15  # re-push if change magnitude increased by ≥15pp


class PushHistory:
    """Track what was pushed to avoid duplicate alerts within 24h."""

    def __init__(self):
        self.records: list[dict] = []
        self._load()

    def _load(self):
        if PUSH_HISTORY_FILE.exists():
            try:
                self.records = json.loads(PUSH_HISTORY_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.records = []

    def _save(self):
        PUSH_HISTORY_FILE.write_text(json.dumps(self.records, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def should_push(self, company: str, change_pct: float) -> tuple[bool, str]:
        """Check if this company+direction should be pushed now.

        Returns (should_push, reason).
        Dedup rule: same company + same direction within 24h → skip.
        Exception: change magnitude increased ≥15pp → re-push.
        """
        direction = "up" if change_pct >= 0 else "down"
        cutoff = time.time() - DEDUP_WINDOW_HOURS * 3600

        for r in self.records:
            if (r["company"] == company
                    and r["direction"] == direction
                    and r["pushed_at"] > cutoff):
                prev_pct = abs(r["change_pct"])
                curr_pct = abs(change_pct)
                if curr_pct - prev_pct >= DEDUP_ESCALATION_PCT / 100:
                    continue  # escalated, push again
                return False, f"dedup: {company} {direction} already pushed at {datetime.fromtimestamp(r['pushed_at']).strftime('%H:%M')}"
        return True, ""

    def record(self, company: str, change_pct: float, media_id: str):
        self.records.append({
            "company": company,
            "direction": "up" if change_pct >= 0 else "down",
            "change_pct": change_pct,
            "media_id": media_id,
            "pushed_at": time.time(),
        })
        # Keep last 7 days only
        cutoff = time.time() - 7 * 86400
        self.records = [r for r in self.records if r["pushed_at"] > cutoff]
        self._save()


# ============ Auto Push ============

def auto_push_critical_alerts(verbose: bool = True) -> list[dict]:
    """Pipeline完成后的自动推送：扫描 ≥25% TP变动，去重后生成并推送草稿。

    Returns list of pushed alert summaries.
    """
    from llm_client import call_llm

    alerts = scan_critical_alerts()
    if not alerts:
        if verbose:
            print("  ✅ No alerts ≥25% TP change")
        return []

    history = PushHistory()
    pushed = []

    for a in alerts:
        company = a["company"]
        change_pct = a["change_pct"]
        direction = "↑" if change_pct > 0 else "↓"

        ok, reason = history.should_push(company, change_pct)
        if not ok:
            if verbose:
                print(f"  ⏭️  {company} {direction}{abs(change_pct):.0%} — {reason}")
            continue

        # Build context for this company
        company_alerts = [al for al in alerts if al["company"] == company]
        alert_lines = []
        for al in company_alerts[:5]:
            d = "上调" if al["change_pct"] > 0 else "下调"
            alert_lines.append(
                f"{al['bank']}: TP {d} {abs(al['change_pct']):.0%} "
                f"({al['old_tp']:,.0f}→{al['new_tp']:,.0f} {al['currency']}), "
                f"评级{al['rating']}, 日期{al['date']}, "
                f"驱动:{'; '.join(al.get('drivers', [])[:2])}"
            )
        context = f"公司:{company}\n告警数:{len(company_alerts)}\n" + "\n".join(alert_lines)

        # Generate via LLM
        try:
            summary = generate_push_summary(context, content_type="company")
        except Exception as e:
            if verbose:
                print(f"  ❌ {company} LLM failed: {e}")
            continue

        banks = sorted(set(al["bank"] for al in company_alerts))
        title = f"{company} 关键变化 — {', '.join(banks[:3])}"
        html = render_push_html(title, summary, source_info=", ".join(banks))

        try:
            media_id = push_draft(title, html,
                                  digest=summary[:80].replace("\n", " ").replace("#", "").strip())
            history.record(company, change_pct, media_id)
            pushed.append({
                "company": company,
                "change_pct": change_pct,
                "media_id": media_id,
                "title": title,
            })
            if verbose:
                print(f"  ✅ Pushed: {company} {direction}{abs(change_pct):.0%} — {media_id}")
        except Exception as e:
            if verbose:
                print(f"  ❌ {company} push failed: {e}")

    return pushed


# ============ Push Content Generator (LLM-powered concise format) ============

def generate_push_summary(context: str, content_type: str = "company") -> str:
    """Use LLM to generate a concise push summary (≤300 chars body text).

    Structure follows GS/JPM/MS research note style:
      1. 核心洞察 — key insight, one sharp sentence
      2. 逻辑支撑 — reasoning chain + supporting data
      3. 操作建议 — actionable recommendation
    """
    from llm_client import call_llm

    type_guide = {
        "company": "这是关于一家公司的投行目标价变动告警，需要提炼核心变化和驱动因素",
        "industry": "这是关于一个行业的投行研究报告摘要，需要提炼最关键的供需或政策信号",
    }

    system = f"""You write ultra-concise Chinese research push notifications for institutional investors.
{type_guide.get(content_type, type_guide['company'])}

CRITICAL RULES:
- Every bank mention MUST include its old→new TP with currency: e.g. "GS: W285,000→320,000 (+12%)", not just "%". Never omit the actual price numbers.
- Total body text (excluding title) MUST be ≤300 Chinese characters. Be ruthlessly concise.
- Lead with the most important insight — name the key price move(s) with numbers.
- Use specific numbers (prices, %, bp, multiples) whenever available — no vague language.
- Tone: sharp, professional, GS/JPM/MS house style. No marketing fluff.
- Output format (use these exact section headers):

🔍 核心洞察
<1-2 sharp sentences, must quote specific old→new TP with currency>

📊 逻辑支撑
<brief reasoning chain with supporting data, each bank cite must show its TP numbers>

💡 操作建议
<1 sentence actionable recommendation>
"""

    user = f"Context data:\n{context[:3000]}\n\nGenerate the push notification following the format above. Body ≤300 chars."

    text, _ = call_llm(system, user, max_tokens=600)
    return text.strip()


def render_push_html(title: str, body: str, source_info: str = "") -> str:
    """Render push content into WeChat-optimized HTML with unified template.

    Template structure (consistent across all companies):
      ┌─ 标题 (红色底线)
      ├─ 🔍 核心洞察 (红左边框 + 浅灰卡片)
      ├─ 📊 逻辑支撑 (橙左边框 + 浅灰卡片)
      ├─ 💡 操作建议 (绿左边框 + 浅灰卡片)
      └─ Sources + 免责声明
    """

    # --- Style constants (unified across all pushes) ---
    SECTION_STYLE = (
        'background:#f8f9fa;padding:12px 16px;margin:10px 0;'
        'border-radius:0 8px 8px 0;font-size:13px;line-height:1.8;'
    )
    HEADER_STYLE = 'font-size:14px;font-weight:700;margin:0 0 8px;'

    # --- Convert markdown tables to images before parsing ---
    body = convert_markdown_tables_to_images(body)

    # --- Parse LLM output into sections ---
    # Normalize: ensure consistent section marker format
    body = body.strip()
    for marker in ["🔍 核心洞察", "📊 逻辑支撑", "💡 操作建议"]:
        body = body.replace(f"\n{marker}\n", f"\n{marker}\n")
        body = body.replace(f"\n{marker}", f"\n{marker}\n")
        # Handle the first marker
        if body.startswith(marker):
            body = marker + "\n" + body[len(marker):].lstrip()

    sections = {"insight": "", "reasoning": "", "action": ""}
    current = None
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("🔍 核心洞察") or line == "🔍 核心洞察":
            current = "insight"
            continue
        elif line.startswith("📊 逻辑支撑") or line == "📊 逻辑支撑":
            current = "reasoning"
            continue
        elif line.startswith("💡 操作建议") or line == "💡 操作建议":
            current = "action"
            continue
        if current:
            sections[current] += line + "\n"

    # --- Render each section as a styled card ---
    def _render_card(emoji_title: str, border_color: str, header_color: str,
                     content: str) -> str:
        # Clean content and convert bullet markers
        content = content.strip()
        if not content:
            return ""
        # Normalize bullet formats: - / • / · / 1. → unified bullet
        bullets = []
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Remove leading bullet markers then re-add unified style
            line = re.sub(r'^[-•·]\s*', '', line)
            line = re.sub(r'^\d+[\.\)]\s*', '', line)
            bullets.append(
                f'<div style="margin:2px 0;padding-left:8px;border-left:2px solid {border_color}20">'
                f'{line}</div>'
            )
        body_html = "\n".join(bullets)

        return f'''
<div style="{SECTION_STYLE} border-left:4px solid {border_color}">
  <div style="{HEADER_STYLE} color:{header_color}">{emoji_title}</div>
  {body_html}
</div>'''

    insight_html = _render_card("🔍 核心洞察", "#dc3545", "#dc3545", sections["insight"])
    reasoning_html = _render_card("📊 逻辑支撑", "#d2991d", "#d2991d", sections["reasoning"])
    action_html = _render_card("💡 操作建议", "#3fb950", "#3fb950", sections["action"])

    source_note = ""
    if source_info:
        source_note = (
            f'<p style="font-size:10px;color:#888;margin:8px 0 0;'
            f'padding:8px 12px;background:#f5f5f5;border-radius:4px">'
            f'📄 Sources: {source_info}</p>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:0 auto;padding:16px;color:#333;background:#fff">

<h1 style="font-size:18px;border-bottom:2px solid #dc3545;padding-bottom:10px;margin:0 0 16px;line-height:1.4">{title}</h1>

{insight_html}
{reasoning_html}
{action_html}

{source_note}

<div style="margin:16px 0 0;padding:10px 14px;background:#f0f0f0;border-radius:6px;font-size:10px;color:#999;line-height:1.6">
<b>免责声明：</b>本文由Hermes AI自动生成，基于公开投行研究报告的结构化数据。内容仅供参考，不构成投资建议。投资有风险，决策须谨慎。<br>
<b>生成时间：</b>{datetime.now().strftime('%Y-%m-%d %H:%M')} · Hermes AI Research
</div>

</body></html>"""


# ============ Draft Article Generator (legacy long-form) ============

def generate_draft_html(alerts: list[dict]) -> str:
    """生成公众号草稿文章 HTML"""
    today = datetime.now().strftime("%Y-%m-%d")
    criticals = [a for a in alerts if a["severity"] == "CRITICAL"]
    highs = [a for a in alerts if a["severity"] == "HIGH"]

    # Group by category
    by_cat = defaultdict(list)
    for a in alerts:
        by_cat[a["category"]].append(a)

    # Summary stats
    total = len(alerts)
    companies = len(set(a["company"] for a in alerts))
    banks = len(set(a["bank"] for a in alerts))

    # Build sections
    sections = ""
    cat_order = ["🌍 Macro / Policy", "📦 Supply/Demand Shock",
                  "🏭 Fundamental Change", "📈 Guidance Shock", "⚡ General Alert"]
    for cat in cat_order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        sections += f'<h2>{cat} ({len(items)})</h2>\n'
        for a in items:
            direction = "📈" if a["change_pct"] > 0 else "📉"
            sections += f'''
<div style="background:#f8f9fa;border-left:4px solid {'#dc3545' if a['severity']=='CRITICAL' else '#fd7e14'};padding:12px 16px;margin:10px 0;border-radius:0 8px 8px 0">
  <p style="margin:0 0 4px;font-size:15px"><b>{direction} {a['bank']}: {a['company']}</b></p>
  <p style="margin:0;color:#666;font-size:13px">
    TP: {a['old_tp']:,.0f} → <b>{a['new_tp']:,.0f}</b> ({a['change_pct']:+.0%}) {a['currency']} | Rating: {a['rating']} | {a['date']}
  </p>
'''
            if a.get("drivers"):
                sections += '<ul style="margin:8px 0;font-size:13px">'
                for d in a["drivers"][:2]:
                    sections += f"<li>{d[:120]}</li>"
                sections += "</ul>"
            sections += "</div>\n"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Hermes Critical Alert — {today}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:600px;margin:0 auto;padding:16px;color:#333;background:#fff">

<h1 style="font-size:18px;border-bottom:2px solid #dc3545;padding-bottom:8px">🚨 Hermes Critical Alert — {today}</h1>
<p style="color:#888;font-size:12px;margin:0 0 16px">
  {total} alerts ({len(criticals)} CRITICAL, {len(highs)} HIGH) · {companies} companies · {banks} banks
</p>

<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:12px;margin:0 0 20px;font-size:13px">
  <b>📌 本期关注：</b>
  {', '.join(sorted(set(a['company'] for a in criticals[:5]))) or '无CRITICAL级变化'}
  {f" — 等{len(criticals)}家公司发生重大变化" if criticals else ""}
</div>

{sections}

<div style="margin:24px 0;padding:12px;background:#f0f0f0;border-radius:6px;font-size:11px;color:#999">
  <b>关注范围：</b>
  宏观政策 · 上游需求突变 · 企业本质变化(良率/产能/客户) · 投行指引突变<br>
  <b>生成时间：</b>{datetime.now().strftime('%Y-%m-%d %H:%M')} · Hermes AI Research · 自动生成，仅供参考
</div>

</body></html>"""


# ============ CLI ============

# ============ Table → Image rendering (via playwright) ============

def _parse_markdown_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """Parse a markdown table block into (headers, rows).
    Input is a list of lines like:
      | H1 | H2 | H3 |
      |-----|-----|-----|
      | V1  | V2  | V3  |
    """
    if len(lines) < 2:
        return [], []
    headers = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    data_start = 1
    if data_start < len(lines) and re.match(r'^[\|\s\-:]+$', lines[data_start]):
        data_start = 2
    rows = []
    for line in lines[data_start:]:
        line = line.strip()
        if not line or not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(cells)
    return headers, rows


def _md_table_to_image(md_lines: list[str]) -> str:
    """Convert a markdown table block to an <img> tag (via WeChat upload).
    Returns empty string on failure.
    """
    headers, rows = _parse_markdown_table(md_lines)
    if not headers or not rows:
        return ""

    col_html = "".join(f"<th>{h}</th>" for h in headers)
    row_html = ""
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        row_html += f"<tr>{cells}</tr>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body{{background:#fff;margin:8px;padding:0;font-family:-apple-system,sans-serif}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th{{background:#f0f4f8;color:#333;padding:8px 12px;text-align:left;font-weight:600;border-bottom:2px solid #ddd}}
td{{padding:7px 12px;border-bottom:1px solid #eee;color:#333}}
tr:nth-child(even) td{{background:#fafbfc}}
</style></head>
<body><table><tr>{col_html}</tr>{row_html}</table></body></html>"""

    try:
        png_bytes = html_table_to_image_bytes(html, width=640)
        url = upload_image_to_wechat(png_bytes, f"table_{hashlib.md5(''.join(md_lines).encode()).hexdigest()[:8]}.png")
        if url:
            return f'<img src="{url}" style="max-width:100%;border:1px solid #eee;border-radius:4px;margin:8px 0">'
    except Exception as e:
        print(f"  ⚠️  Table→image conversion failed: {e}")
    return ""


def convert_markdown_tables_to_images(text: str) -> str:
    """Scan text for markdown table blocks and replace them with <img> tags.
    A table block is 2+ consecutive lines starting with |.
    """
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            is_separator = bool(re.match(r'^[\|\s\-:]+$', next_line))
            if is_separator or next_line.startswith("|"):
                table_lines = [line]
                j = i + 1
                while j < len(lines) and lines[j].strip().startswith("|"):
                    table_lines.append(lines[j].strip())
                    j += 1
                img_tag = _md_table_to_image(table_lines)
                if img_tag:
                    result.append(img_tag)
                    i = j
                    continue
        result.append(lines[i])
        i += 1
    return "\n".join(result)


def _render_table_html(rows: list[dict], columns: list[str],
                       title: str = "") -> str:
    """Build a standalone HTML page containing just a styled table."""
    col_html = "".join(f"<th>{c}</th>" for c in columns)
    row_html = ""
    for row in rows:
        cells = "".join(f"<td>{row.get(c, '')}</td>" for c in columns)
        row_html += f"<tr>{cells}</tr>"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body{{background:#fff;margin:0;padding:12px;font-family:-apple-system,sans-serif}}
h3{{font-size:14px;color:#333;margin:0 0 8px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th{{background:#0f3460;color:#fff;padding:6px 10px;text-align:left;font-weight:600}}
td{{padding:5px 10px;border-bottom:1px solid #ddd}}
</style></head>
<body>{'<h3>' + title + '</h3>' if title else ''}
<table><tr>{col_html}</tr>{row_html}</table>
</body></html>"""


def html_table_to_image_bytes(html: str, width: int = 600) -> bytes:
    """Render HTML table to a PNG image using playwright. Returns PNG bytes."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 400})
        page.set_content(html)
        # Wait for render
        page.wait_for_timeout(500)
        # Get the table element dimensions
        table = page.query_selector("table")
        if table:
            screenshot = table.screenshot(type="png")
        else:
            screenshot = page.screenshot(type="png", full_page=True)
        browser.close()
        return screenshot


def upload_image_to_wechat(image_bytes: bytes, filename: str = "table.png") -> str:
    """Upload PNG image to WeChat permanent material. Returns the image URL."""
    token = get_access_token()
    resp = requests.post(
        f"{WECHAT_UPLOAD_URL}?access_token={token}&type=image",
        files={"media": (filename, image_bytes, "image/png")},
        timeout=30
    )
    data = resp.json()
    url = data.get("url", "")
    if not url:
        print(f"  ⚠️  Image upload failed: {data}")
    return url


def build_logic_push_html(company: str, drivers_data: list[dict]) -> str:
    """Build WeChat push HTML for a logic dashboard: text intro + table images."""
    import textwrap

    total_reports = sum(d.get("report_count", 0) for d in drivers_data)
    banks = sorted(set(
        b for d in drivers_data for b in d.get("banks", [])
    ))

    html_parts = [
        '<div style="max-width:100%;font-size:15px;color:#333;line-height:1.8">',
        f'<h2 style="color:#e94560;border-bottom:2px solid #e94560;padding-bottom:6px">'
        f'{company} — 逻辑链溯源</h2>',
        f'<p style="color:#888;font-size:13px">{len(drivers_data)} 个驱动因素 | '
        f'{total_reports} 份报告 | {", ".join(banks[:5])}</p>',
    ]

    for i, d in enumerate(drivers_data):
        consensus_label = {"full": "Full", "strong": "Strong",
                           "partial": "Partial", "isolated": "Isolated"}
        level = d.get("consensus_level", "")
        badge = consensus_label.get(level, level)

        html_parts.append(
            f'<div style="background:#f8f9fa;border-left:3px solid #e94560;'
            f'padding:12px 16px;margin:16px 0;border-radius:0 6px 6px 0">'
            f'<h3 style="margin:0 0 4px;color:#0f3460">{i+1}. {d.get("canonical", "")} '
            f'<span style="font-size:11px;background:#0f3460;color:#fff;padding:2px 8px;border-radius:4px">{badge}</span></h3>'
            f'<p style="color:#888;font-size:12px;margin:4px 0">'
            f'{d.get("report_count", 0)} reports | {", ".join(d.get("banks", [])[:5])} | {d.get("direction", "")}</p>'
        )

        # Evidence matrix → table image
        evidence = d.get("evidence_matrix", [])
        if evidence:
            columns = list(evidence[0].keys()) if evidence else ["metric"]
            table_html = _render_table_html(evidence, columns,
                                            title=f"{d.get('canonical', '')} — 证据矩阵")
            try:
                png_bytes = html_table_to_image_bytes(table_html)
                url = upload_image_to_wechat(png_bytes, f"evidence_{i}.png")
                if url:
                    html_parts.append(
                        f'<p style="font-weight:600;color:#0f3460;margin:8px 0 4px">证据矩阵</p>'
                        f'<img src="{url}" style="max-width:100%;border:1px solid #ddd;border-radius:4px">'
                    )
            except Exception as e:
                html_parts.append(f'<p style="color:#999">[表格渲染失败: {e}]</p>')

        # Impact graph → bullet list (no table, already text)
        impacts = d.get("impact_graph", [])[:8]
        if impacts:
            html_parts.append('<p style="font-weight:600;color:#0f3460;margin:8px 0 4px">产业链传导</p>')
            for imp in impacts:
                html_parts.append(
                    f'<p style="margin:2px 0;font-size:13px">'
                    f'<b>{imp.get("entity", "")}</b> [{imp.get("role", "")}] → '
                    f'{imp.get("effect", "")}</p>'
                )

        # Change consensus
        change = d.get("change_consensus", "")
        if change:
            html_parts.append(
                f'<p style="font-size:12px;color:#666;margin-top:8px">'
                f'<b>与前期变化:</b> {change}</p>'
            )

        html_parts.append('</div>')  # close driver card

    html_parts.append(
        '<p style="color:#aaa;font-size:11px;text-align:center;margin-top:24px">'
        'Hermes 投研系统 · 自动生成</p></div>'
    )

    return "\n".join(html_parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WeChat Official Account — Draft Push & Management")
    sub = parser.add_subparsers(dest="command", help="子命令")

    # ---- push (original behavior, kept backward compatible) ----
    p_push = sub.add_parser("push", help="扫描关键Alert并推送草稿")
    p_push.add_argument("--dry-run", action="store_true", help="Preview only, no push")
    p_push.add_argument("--send", action="store_true", help="Push draft + send preview")
    p_push.add_argument("--output", help="Save draft HTML to file")

    # ---- draft:list ----
    p_list = sub.add_parser("list", help="列出草稿箱所有草稿")
    p_list.add_argument("--keyword", "-k", default="", help="按标题关键词过滤")

    # ---- draft:delete ----
    p_del = sub.add_parser("delete", help="批量删除草稿")
    p_del.add_argument("--keyword", "-k", default="", help="仅删除标题匹配关键词的草稿")
    p_del.add_argument("--keep-recent", "-n", type=int, default=0, help="保留最近 N 条，删除其余")
    p_del.add_argument("--older-than", "-d", type=int, default=0, help="仅删除 N 天前的草稿")
    p_del.add_argument("--all", action="store_true", help="删除所有草稿（需确认）")
    p_del.add_argument("--yes", "-y", action="store_true", help="跳过确认，直接执行")
    p_del.add_argument("--delay", type=float, default=0.3, help="每条删除间隔秒数（默认0.3）")

    # ---- Keep backward compat: no subcommand = push mode ----
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] not in {"push", "list", "delete"}):
        # Fallback: treat as push mode with optional flags
        sys.argv.insert(1, "push")

    args = parser.parse_args()

    # ====== DRAFT LIST ======
    if args.command == "list":
        result = batch_delete_drafts(
            keyword=args.keyword,
            list_only=True,
            confirm=False
        )
        print(f"\n📦 总计 {result['total']} 条草稿" + (f", 关键词匹配 {result['matched']} 条" if args.keyword else ""))

    # ====== DRAFT DELETE ======
    elif args.command == "delete":
        if not args.all and not args.keyword and not args.keep_recent and not args.older_than:
            print("❌ 请指定删除范围: --all / -k 关键词 / -n 保留最近N条 / -d N天前")
            print("   示例: python wechat_push.py delete --all           # 删除所有")
            print("   示例: python wechat_push.py delete -d 3             # 删除3天前的草稿")
            print("   示例: python wechat_push.py delete -d 3 -k 'Test'   # 删除3天前且含Test的")
            print("   示例: python wechat_push.py delete -n 10 -d 7       # 保留10条+7天内的")
            sys.exit(1)

        batch_delete_drafts(
            keyword=args.keyword,
            keep_recent=args.keep_recent if not args.all else 0,
            older_than_days=args.older_than,
            confirm=not args.yes,
            delay=args.delay
        )

    # ====== PUSH (original flow) ======
    elif args.command == "push":
        print("🔍 Scanning for critical alerts...")
        alerts = scan_critical_alerts()
        criticals = [a for a in alerts if a["severity"] == "CRITICAL"]
        highs = [a for a in alerts if a["severity"] == "HIGH"]

        if not alerts:
            print("  ✅ No critical/high alerts found")
            sys.exit(0)

        print(f"  🚨 {len(criticals)} CRITICAL, {len(highs)} HIGH")
        for a in alerts[:8]:
            direction = "↑" if a["change_pct"] > 0 else "↓"
            print(f"  {a['severity']:<8} {a['bank']:<18} {a['company']:<25} "
                  f"TP {direction}{abs(a['change_pct']):.0%}  [{a['category']}]")

        html = generate_draft_html(alerts)

        if args.output:
            Path(args.output).write_text(html, encoding="utf-8")
            print(f"\n📄 Draft saved to {args.output}")

        if args.dry_run:
            print("\n📝 DRAFT PREVIEW (dry-run):")
            preview = re.sub(r'<[^>]+>', '', html)
            print(preview[:1500])
            sys.exit(0)

        if args.send:
            wc = _load_wechat_config()
            if not wc.get("appid") or not wc.get("secret"):
                print("\n❌ WeChat appid/secret not configured.")
                sys.exit(1)

            try:
                top_co = sorted(set(a["company"] for a in alerts))[:2]
                title = f"🚨 {', '.join(top_co)} TP变动 {datetime.now().strftime('%m/%d')}"
                if len(title) > 64:
                    title = f"🚨 Hermes Alert {datetime.now().strftime('%m/%d')}"
                digest = f"{len(criticals)} CRITICAL, {len(highs)} HIGH"
                media_id = push_draft(title, html, digest=digest)
                send_preview(media_id)
                print(f"\n✅ Draft pushed: {title}")
            except Exception as e:
                print(f"\n❌ Push failed: {e}")
                sys.exit(1)
        else:
            print("\n💡 Use --send to push draft, --dry-run to preview, --output to save HTML")

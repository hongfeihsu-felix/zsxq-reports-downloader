"""
News Feed — 媒体/大V 信息流聚合
Primary: Google News RSS (free) + NewsAPI (100 req/day free tier)

用法：
  from data_sources.news_feed import NewsFetcher
  nf = NewsFetcher()
  articles = nf.search("TSMC AI chip", max_results=10)
  # → [{"title": "...", "source": "Bloomberg", "url": "...", ...}]

  # 批量跟踪
  briefing = nf.daily_briefing()
  # → 按公司/行业/投资者的分组新闻摘要
"""

import os
import json
import time
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import requests
import feedparser

DB_PATH = Path(__file__).parent.parent / "industry_metrics.db"

# ============ 监控关键词模板 ============

TRACKING_TOPICS = {
    "companies": {
        # 从 config.json 加载，这里作为 fallback
        "TSMC": "TSMC OR 台積電 OR 台积电",
        "NVIDIA": "NVIDIA OR NVDA",
        "MediaTek": "MediaTek OR 聯發科 OR 联发科",
        "AMD": "AMD OR 'Advanced Micro Devices'",
        "Broadcom": "Broadcom OR AVGO",
    },
    "industries": {
        "AI Chip": "AI chip OR AI accelerator OR GPU OR TPU OR ASIC",
        "HBM Memory": "HBM OR HBM3 OR HBM4 OR 'high bandwidth memory'",
        "CoWoS": "CoWoS OR 'advanced packaging' OR chiplet",
        "Foundry": "foundry OR wafer fab OR 'process node'",
        "Datacenter": "datacenter OR hyperscaler OR 'cloud capex'",
    },
    "investors": {
        "ARK": '"Cathie Wood" OR "ARK Invest" OR ARKK',
        "Bridgewater": '"Ray Dalio" OR Bridgewater Associates',
        "Buffett": "Berkshire Hathaway OR 'Warren Buffett' 13F",
    },
    "media": {
        "Bloomberg": "",   # will use NewsAPI source filter
        "Reuters": "",     # will use NewsAPI source filter
    }
}

# 知名媒体源（NewsAPI 支持）
MAJOR_SOURCES = [
    "bloomberg", "reuters", "financial-times", "the-wall-street-journal",
    "cnbc", "business-insider", "fortune", "the-economist",
    "techcrunch", "the-verge", "wired", "ars-technica"
]


class NewsFetcher:
    """新闻获取器 — Google News RSS + NewsAPI"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_cache()
        self._newsapi_key = self._load_newsapi_key()

    def _load_newsapi_key(self) -> str:
        key = os.environ.get("NEWSAPI_KEY", "")
        if key:
            return key
        config_path = DB_PATH.parent / "config.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                return (cfg.get("api_keys", {}).get("newsapi_ai", "")
                        or cfg.get("api_keys", {}).get("newsapi", ""))
            except (json.JSONDecodeError, KeyError):
                pass
        return ""

    def _init_cache(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_articles (
                url_hash TEXT PRIMARY KEY,
                title TEXT,
                source TEXT,
                url TEXT,
                published TEXT,
                summary TEXT,
                topic TEXT,
                fetched_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _cache_article(self, article: dict, topic: str):
        url_hash = hashlib.md5(article["url"].encode()).hexdigest()
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """INSERT OR IGNORE INTO news_articles
               (url_hash, title, source, url, published, summary, topic, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (url_hash, article["title"], article["source"], article["url"],
             article.get("published", ""), article.get("summary", ""),
             topic, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    # ---- Google News RSS (free, no key needed) ----

    def _search_rss(self, query: str, max_results: int = 10) -> list[dict]:
        """通过 Google News RSS 搜索"""
        try:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en&ceid=US:en"
            feed = feedparser.parse(url)
            articles = []
            for entry in feed.entries[:max_results]:
                # Parse source from title ("Title - Source")
                title_parts = entry.title.rsplit(" - ", 1)
                title = title_parts[0].strip()
                source = title_parts[1].strip() if len(title_parts) > 1 else "Google News"

                articles.append({
                    "title": title,
                    "source": source,
                    "url": entry.link,
                    "published": entry.get("published", ""),
                    "summary": entry.get("summary", ""),
                    "_source": "rss"
                })
            return articles
        except Exception as e:
            return [{"title": "RSS Error", "source": "", "url": "",
                     "summary": str(e), "_source": "rss"}]

    # ---- NewsAPI.ai (2,000 searches free tier) ----

    def _search_newsapi(self, query: str, max_results: int = 10,
                        sources: list[str] = None) -> list[dict]:
        """通过 NewsAPI.ai 搜索 (150,000+ publishers, 2k free searches)"""
        if not self._newsapi_key:
            return []

        try:
            params = {
                "keyword": query,
                "apiKey": self._newsapi_key,
                "articlesCount": max_results,
                "lang": "eng",
                "sortBy": "date"
            }
            if sources:
                params["sources"] = ",".join(sources)

            resp = requests.get(
                "https://newsapi.ai/api/v1/article/getArticles",
                params=params,
                timeout=15
            )

            if resp.status_code != 200:
                return []

            data = resp.json()
            # Response format: {"articles": {"results": [...]}}
            results = data.get("articles", {}).get("results", [])

            articles = []
            for item in results[:max_results]:
                source_info = item.get("source", {}) or {}
                articles.append({
                    "title": item.get("title", ""),
                    "source": source_info.get("title", source_info.get("name", "")),
                    "url": item.get("url", ""),
                    "published": item.get("dateTime", item.get("date", "")),
                    "summary": (item.get("body", "") or "")[:300],
                    "_source": "newsapi.ai"
                })
            return articles
        except Exception:
            return []

    # ---- Unified Search ----

    def search(self, query: str, max_results: int = 10,
               use_newsapi: bool = True) -> list[dict]:
        """统一搜索：RSS + NewsAPI 合并去重"""
        all_articles = []

        # NewsAPI.ai first (higher quality, more sources)
        if use_newsapi and self._newsapi_key:
            newsapi_results = self._search_newsapi(query, max_results)
            all_articles.extend(newsapi_results)

        # RSS as supplement (free, always available)
        rss_count = max(max_results - len(all_articles), 3)
        if rss_count > 0:
            time.sleep(0.3)
            rss_results = self._search_rss(query, rss_count)
            # Merge, dedup by URL
            existing_urls = {a["url"] for a in all_articles if a["url"]}
            for art in rss_results:
                if art["url"] not in existing_urls:
                    all_articles.append(art)

        # Sort by published date (newest first), put empty dates last
        all_articles.sort(key=lambda a: a.get("published", ""), reverse=True)
        return all_articles[:max_results]

    # ---- Daily Briefing ----

    def daily_briefing(self, topics: dict = None) -> dict:
        """每日简报：按 topic 分组搜索"""
        if topics is None:
            topics = TRACKING_TOPICS

        briefing = {}
        for category, queries in topics.items():
            if not queries:
                continue
            briefing[category] = {}
            for name, query in queries.items():
                if not query:
                    continue
                articles = self.search(query, max_results=5)
                # Filter: last 24 hours
                recent = []
                cutoff = datetime.now() - timedelta(days=1)
                for a in articles:
                    pub = a.get("published", "")
                    if pub:
                        try:
                            from email.utils import parsedate_to_datetime
                            pub_dt = parsedate_to_datetime(pub)
                            if pub_dt < cutoff:
                                continue
                        except Exception:
                            pass
                    recent.append(a)

                briefing[category][name] = recent
                # Cache
                for a in recent:
                    self._cache_article(a, f"{category}/{name}")

        return briefing

    def format_briefing(self, briefing: dict) -> str:
        """将简报格式化为可读文本"""
        lines = [f"📰 Daily Briefing — {datetime.now().strftime('%Y-%m-%d')}",
                 "=" * 60, ""]

        total = 0
        for category, topics in briefing.items():
            cat_articles = sum(len(v) for v in topics.values())
            if cat_articles == 0:
                continue
            total += cat_articles
            lines.append(f"## {category.upper()} ({cat_articles} articles)")
            lines.append("")

            for name, articles in topics.items():
                if not articles:
                    continue
                lines.append(f"  {name}:")
                for a in articles[:3]:
                    src = a.get("source", "?")
                    pub = a.get("published", "")[:16]
                    lines.append(f"    • [{src}] {a['title'][:80]}  {pub}")
                    if a.get("summary"):
                        lines.append(f"      {a['summary'][:100]}")
                lines.append("")

        if total == 0:
            lines.append("  No new articles in the past 24 hours.")

        return "\n".join(lines)

    # ---- Topic Helpers ----

    def company_news(self, company_name: str, ticker: str = "",
                     max_results: int = 5) -> list[dict]:
        """获取特定公司新闻"""
        query_parts = [company_name]
        if ticker:
            query_parts.append(ticker)
        return self.search(" OR ".join(query_parts), max_results=max_results)

    def industry_news(self, industry_name: str, max_results: int = 5) -> list[dict]:
        """获取行业新闻"""
        # Look up keyword from TRACKING_TOPICS
        for cat in TRACKING_TOPICS.values():
            if industry_name in cat:
                return self.search(cat[industry_name], max_results=max_results)
        return self.search(industry_name, max_results=max_results)

    @staticmethod
    def format_for_report(articles: list[dict]) -> str:
        """格式化新闻为 LLM context"""
        if not articles:
            return "Recent News: No recent articles found."

        lines = ["Recent News Headlines:"]
        for a in articles[:5]:
            src = a.get("source", "?")
            pub = a.get("published", "")[:10]
            lines.append(f"  [{src}] {a['title']} ({pub})")
        return "\n".join(lines)


# ============ CLI ============

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="News Feed")
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--briefing", action="store_true", help="Generate daily briefing")
    parser.add_argument("--company", help="Company name for targeted news")
    parser.add_argument("--max", type=int, default=10, help="Max results")
    parser.add_argument("--no-newsapi", action="store_true", help="Skip NewsAPI")

    args = parser.parse_args()
    nf = NewsFetcher()

    if args.briefing:
        briefing = nf.daily_briefing()
        print(nf.format_briefing(briefing))
    elif args.company:
        articles = nf.company_news(args.company, max_results=args.max)
        for a in articles:
            print(f"[{a['source']}] {a['title']}")
            print(f"  {a['url'][:80]}")
            print()
    elif args.query:
        articles = nf.search(args.query, max_results=args.max,
                             use_newsapi=not args.no_newsapi)
        for a in articles:
            src_tag = f"[{a['_source']}]" if a.get("_source") else ""
            print(f"{src_tag} [{a['source']}] {a['title']}")
            if a.get("summary"):
                print(f"  {a['summary'][:120]}")
            print()
    else:
        parser.print_help()

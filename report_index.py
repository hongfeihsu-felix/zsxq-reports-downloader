#!/usr/bin/env python3
"""
Report Index Database — 文档索引与全文搜索

表:
  documents        — 每份报告元数据
  doc_companies    — N:M 报告↔公司
  doc_industries   — N:M 报告↔行业
  doc_fts          — FTS5 全文搜索
  entity_registry  — 统一实体注册表 (公司/行业)
  entity_aliases   — 实体别名

用法:
  python3 report_index.py backfill              # 回填所有已有分析
  python3 report_index.py stats                 # 显示统计
  python3 report_index.py search <query>        # CLI 搜索
  python3 report_index.py ingest <path>         # 索引独立研究
  python3 report_index.py expired --cleanup     # 清理过期文件
"""

import json
import re
import sys
import sqlite3
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from utils import extract_bank_from_filename

DB_PATH = Path(__file__).parent / "report_index.db"
REPORT_BASE = Path.home() / "hermes_reports" / "Investment_Banking_Report"
AI_SEMI_DIR = Path.home() / "hermes_reports" / "ai_semiconductor_research"
PROJECT_DIR = Path(__file__).parent


DOCUMENT_COLUMNS = {
    "pdf_path": "TEXT",
    "source_type": "TEXT NOT NULL DEFAULT 'investment_banking'",
    "bank": "TEXT",
    "report_date": "TEXT",
    "title": "TEXT",
    "summary": "TEXT",
    "raw_json_path": "TEXT",
    "md_path": "TEXT",
    "overview_path": "TEXT",
    "alert_severity": "TEXT",
    "is_expired": "INTEGER DEFAULT 0",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


def _load_config() -> dict:
    config_path = PROJECT_DIR / "config.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {}


def _get_expire_config() -> tuple[int, int]:
    cfg = _load_config()
    maint = cfg.get("maintenance", {})
    co_q = maint.get("company_report_expire_quarters", 2)
    ind_d = maint.get("industry_report_expire_days", 365)
    return co_q * 90, ind_d


def init_db(db_path: Path = None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            pdf_name      TEXT NOT NULL,
            pdf_path      TEXT,
            source_type   TEXT NOT NULL DEFAULT 'investment_banking',
            bank          TEXT,
            report_date   TEXT,
            title         TEXT,
            summary       TEXT,
            raw_json_path TEXT,
            md_path       TEXT,
            overview_path TEXT,
            alert_severity TEXT,
            is_expired    INTEGER DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS doc_companies (
            doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            company_name TEXT NOT NULL,
            ticker       TEXT,
            PRIMARY KEY (doc_id, company_name)
        );
        CREATE INDEX IF NOT EXISTS idx_dc_company ON doc_companies(company_name);

        CREATE TABLE IF NOT EXISTS doc_industries (
            doc_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            industry_slug TEXT NOT NULL,
            layer         TEXT NOT NULL DEFAULT '',
            match_count   INTEGER DEFAULT 1,
            PRIMARY KEY (doc_id, industry_slug, layer)
        );
        CREATE INDEX IF NOT EXISTS idx_di_industry ON doc_industries(industry_slug);

        CREATE TABLE IF NOT EXISTS entity_registry (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name  TEXT NOT NULL UNIQUE,
            entity_type     TEXT NOT NULL,
            ticker          TEXT,
            config_industry TEXT,
            report_path     TEXT,
            is_active       INTEGER DEFAULT 1,
            report_count    INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS entity_aliases (
            alias      TEXT NOT NULL,
            entity_id  INTEGER NOT NULL REFERENCES entity_registry(id) ON DELETE CASCADE,
            alias_type TEXT DEFAULT 'search',
            PRIMARY KEY (alias, entity_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ea_alias ON entity_aliases(alias);

        CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
            doc_id UNINDEXED,
            title,
            bank,
            company,
            industry_tags,
            content_text,
            tokenize='unicode61'
        );
    """)
    _migrate_db(conn)
    conn.commit()
    conn.close()


def _migrate_db(conn: sqlite3.Connection):
    """Apply additive migrations for existing local report indexes."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    for name, ddl in DOCUMENT_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {ddl}")
    _dedupe_documents(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_report_date ON documents(report_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_alert ON documents(alert_severity)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_raw_json_unique ON documents(raw_json_path)")


def _dedupe_documents(conn: sqlite3.Connection):
    """Collapse duplicate document rows created before raw_json_path was unique."""
    rows = conn.execute("""
        SELECT raw_json_path, MIN(id) AS keep_id, GROUP_CONCAT(id) AS ids
        FROM documents
        WHERE raw_json_path IS NOT NULL AND raw_json_path != ''
        GROUP BY raw_json_path
        HAVING COUNT(*) > 1
    """).fetchall()
    for row in rows:
        keep_id = row["keep_id"]
        ids = [int(v) for v in row["ids"].split(",")]
        dup_ids = [doc_id for doc_id in ids if doc_id != keep_id]
        for dup_id in dup_ids:
            conn.execute("""
                INSERT OR IGNORE INTO doc_companies(doc_id, company_name, ticker)
                SELECT ?, company_name, ticker FROM doc_companies WHERE doc_id=?
            """, (keep_id, dup_id))
            conn.execute("""
                INSERT OR IGNORE INTO doc_industries(doc_id, industry_slug, layer, match_count)
                SELECT ?, industry_slug, layer, match_count FROM doc_industries WHERE doc_id=?
            """, (keep_id, dup_id))
            conn.execute("DELETE FROM doc_fts WHERE doc_id=?", (dup_id,))
            conn.execute("DELETE FROM doc_companies WHERE doc_id=?", (dup_id,))
            conn.execute("DELETE FROM doc_industries WHERE doc_id=?", (dup_id,))
            conn.execute("DELETE FROM documents WHERE id=?", (dup_id,))


class ReportIndex:
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        if not self.db_path.exists():
            init_db(self.db_path)
        self.conn = sqlite3.connect(str(self.db_path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        _migrate_db(self.conn)
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ========== 实体注册 ==========

    def sync_entity_registry(self) -> dict:
        """从 config.json 同步 entity_registry + entity_aliases"""
        cfg = _load_config()
        companies = cfg.get("tracking", {}).get("companies", [])
        industries = cfg.get("tracking", {}).get("industries", [])

        co_count, ind_count = 0, 0

        for c in companies:
            name = c.get("name", "")
            ticker = c.get("ticker", "")
            industry = c.get("industry", "")
            active = 1 if c.get("active", True) else 0
            if not name:
                continue

            self.conn.execute("""
                INSERT INTO entity_registry
                    (canonical_name, entity_type, ticker, config_industry, is_active)
                VALUES (?, 'company', ?, ?, ?)
                ON CONFLICT(canonical_name) DO UPDATE SET
                    ticker=excluded.ticker,
                    config_industry=excluded.config_industry,
                    is_active=excluded.is_active,
                    updated_at=datetime('now')
            """, (name, ticker, industry, active))
            co_count += 1

            row = self.conn.execute(
                "SELECT id FROM entity_registry WHERE canonical_name=? AND entity_type='company'",
                (name,)
            ).fetchone()
            if not row:
                continue
            entity_id = row[0]

            keywords = c.get("keywords", [])
            for kw in keywords:
                kw_lower = kw.lower().strip()
                if kw_lower:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entity_aliases(alias, entity_id, alias_type) VALUES (?,?,?)",
                        (kw_lower, entity_id, "keyword")
                    )
            if ticker:
                self.conn.execute(
                    "INSERT OR IGNORE INTO entity_aliases(alias, entity_id, alias_type) VALUES (?,?,?)",
                    (ticker.lower(), entity_id, "ticker")
                )

        for ind in industries:
            slug = ind.get("slug", "")
            name = ind.get("name", "")
            active = 1 if ind.get("active", True) else 0
            if not slug:
                continue

            self.conn.execute("""
                INSERT INTO entity_registry
                    (canonical_name, entity_type, ticker, config_industry, is_active)
                VALUES (?, 'industry', NULL, ?, ?)
                ON CONFLICT(canonical_name) DO UPDATE SET
                    is_active=excluded.is_active,
                    updated_at=datetime('now')
            """, (slug, name, active))
            ind_count += 1

            row = self.conn.execute(
                "SELECT id FROM entity_registry WHERE canonical_name=? AND entity_type='industry'",
                (slug,)
            ).fetchone()
            if not row:
                continue
            entity_id = row[0]

            keywords = ind.get("keywords", [])
            for kw in keywords:
                kw_lower = kw.lower().strip()
                if kw_lower:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO entity_aliases(alias, entity_id, alias_type) VALUES (?,?,?)",
                        (kw_lower, entity_id, "keyword")
                    )

        self.conn.commit()
        return {"companies": co_count, "industries": ind_count}

    def ensure_company(self, name: str, ticker: str = "") -> int:
        row = self.conn.execute(
            "SELECT id FROM entity_registry WHERE canonical_name=? AND entity_type='company'",
            (name,)
        ).fetchone()
        if row:
            return row[0]
        self.conn.execute(
            "INSERT INTO entity_registry(canonical_name, entity_type, ticker) VALUES (?,?,?)",
            (name, "company", ticker)
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def ensure_industry(self, slug: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM entity_registry WHERE canonical_name=? AND entity_type='industry'",
            (slug,)
        ).fetchone()
        if row:
            return row[0]
        self.conn.execute(
            "INSERT INTO entity_registry(canonical_name, entity_type) VALUES (?,?)",
            (slug, "industry")
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # ========== 索引 ==========

    def index_analysis(self, json_path: str) -> Optional[int]:
        """索引一份 _analysis.json"""
        jp = Path(json_path)
        if not jp.exists():
            return None

        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None

        pdf_name = data.get("pdf_name", jp.stem.replace("_analysis", ""))
        parsed = data.get("parsed", {}) or {}
        bank = extract_bank_from_filename(pdf_name)
        if bank == "?":
            bank = ""

        # 提取日期
        report_date = ""
        date_m = re.search(r'(\d{6})(?:\.pdf|\.pptx|\.xlsx|\.pages|$)', pdf_name)
        if date_m:
            ds = date_m.group(1)
            report_date = f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"

        # 标题、摘要
        md_path = jp.with_suffix("").with_suffix(".md")
        md_rel = ""
        summary = ""
        md_text = ""
        if md_path.exists():
            md_rel = str(md_path)
            md_text = md_path.read_text(encoding="utf-8", errors="replace")
            summary = md_text[:500]

        # 公司名 (归一化)
        from run_pipeline import normalize_company
        raw_company = parsed.get("company", "") or ""
        canonical = normalize_company(raw_company)

        # 若 parsed 里没有公司，从文件名提取
        if canonical == "Unknown" or not canonical:
            name_match = re.match(r'^[A-Za-z\s&.]+?-([A-Za-z\s&.]+?)[（(]', pdf_name)
            if name_match:
                canonical = normalize_company(name_match.group(1).strip())

        ticker = parsed.get("ticker", "")
        rating = parsed.get("rating", "")
        tp = parsed.get("target_price") or {}
        alert_severity = parsed.get("alert_severity", "") or ""

        # Upsert documents
        self.conn.execute("""
            INSERT INTO documents
                (pdf_name, pdf_path, source_type, bank, report_date, title,
                 summary, raw_json_path, md_path, alert_severity)
            VALUES (?, ?, 'investment_banking', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_json_path) DO UPDATE SET
                bank=excluded.bank, report_date=excluded.report_date,
                summary=excluded.summary, md_path=excluded.md_path,
                alert_severity=excluded.alert_severity,
                updated_at=datetime('now')
        """, (pdf_name, str(jp), bank, report_date, "", summary, str(jp), md_rel, alert_severity))

        doc_id = self.conn.execute("SELECT id FROM documents WHERE raw_json_path=?", (str(jp),)).fetchone()
        if not doc_id:
            return None
        doc_id = doc_id[0]

        # doc_companies
        if canonical and canonical != "Unknown":
            self.conn.execute(
                "INSERT OR IGNORE INTO doc_companies(doc_id, company_name, ticker) VALUES (?,?,?)",
                (doc_id, canonical, ticker)
            )
            self.ensure_company(canonical, ticker)

        # doc_industries (从 industry_tags 三层)
        industry_tags = parsed.get("industry_tags", {})
        if isinstance(industry_tags, dict):
            for layer, tags in industry_tags.items():
                if isinstance(tags, list):
                    for t in tags:
                        slug = t.get("slug", "") if isinstance(t, dict) else ""
                        mc = t.get("match_count", 1) if isinstance(t, dict) else 1
                        if slug:
                            self.conn.execute(
                                "INSERT OR IGNORE INTO doc_industries(doc_id, industry_slug, layer, match_count) VALUES (?,?,?,?)",
                                (doc_id, slug, layer, mc)
                            )
                            self.ensure_industry(slug)

        # FTS
        company_str = canonical if canonical and canonical != "Unknown" else ""
        ind_slugs = ""
        if isinstance(industry_tags, dict):
            all_slugs = set()
            for layer, tags in industry_tags.items():
                if isinstance(tags, list):
                    for t in tags:
                        s = t.get("slug", "") if isinstance(t, dict) else ""
                        if s:
                            all_slugs.add(s)
            ind_slugs = ", ".join(sorted(all_slugs))

        content_text = md_text[:5000] if md_text else summary
        self.conn.execute(
            "INSERT OR REPLACE INTO doc_fts(doc_id, title, bank, company, industry_tags, content_text) VALUES (?,?,?,?,?,?)",
            (doc_id, pdf_name, bank, company_str, ind_slugs, content_text)
        )

        self.conn.commit()
        return doc_id

    def index_independent_research(self, file_path: str, source_type: str = "independent_research",
                                   title: str = "", bank: str = "Independent") -> Optional[int]:
        """索引独立研究报告 (.md / .pages)"""
        fp = Path(file_path)
        if not fp.exists():
            print(f"  ❌ File not found: {file_path}")
            return None

        # 提取日期 (在读取内容之前，因为 .pages 降级路径也需要)
        report_date = ""
        date_m = re.search(r'(\d{6})', fp.stem)
        if date_m:
            ds = date_m.group(1)
            report_date = f"20{ds[:2]}-{ds[2:4]}-{ds[4:6]}"
        if not report_date:
            report_date = datetime.now().strftime("%Y-%m-%d")

        # .pages → 转 txt (尝试 textutil, 但纯图片型 .pages 会失败)
        if fp.suffix == ".pages":
            import tempfile
            tmp_txt = Path(tempfile.mktemp(suffix=".txt"))
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-output", str(tmp_txt), str(fp)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and tmp_txt.exists():
                md_text = tmp_txt.read_text(encoding="utf-8")
                tmp_txt.unlink(missing_ok=True)
            else:
                md_text = f"Title: {title or fp.stem}\nSource: {bank}\nFile: {fp.name}\nDate: {report_date}\n(Image-based slides - see original file for full content)"
                tmp_txt.unlink(missing_ok=True)
        elif fp.suffix in (".md", ".txt"):
            md_text = fp.read_text(encoding="utf-8")
        else:
            print(f"  ❌ Unsupported file type: {fp.suffix}")
            return None

        summary = md_text[:500]
        content_text = md_text[:5000]

        # 通过 config.json keywords 匹配公司
        cfg = _load_config()
        company_map = {}
        for c in cfg.get("tracking", {}).get("companies", []):
            name = c.get("name", "")
            if not name:
                continue
            for kw in c.get("keywords", []):
                company_map[kw.lower()] = name

        matched_companies = set()
        text_lower = md_text.lower()
        for kw, name in company_map.items():
            if kw in text_lower:
                matched_companies.add(name)

        # 匹配行业
        matched_industries = set()
        for ind in cfg.get("tracking", {}).get("industries", []):
            slug = ind.get("slug", "")
            if not slug:
                continue
            for kw in ind.get("keywords", []):
                if kw.lower() in text_lower:
                    matched_industries.add(slug)
                    break

        # 检测公司所在行业（通过 config.json company→industry 映射）
        co_to_ind = {}
        for c in cfg.get("tracking", {}).get("companies", []):
            if c.get("name") and c.get("industry"):
                co_to_ind[c["name"]] = c["industry"]

        # 对于匹配到的公司，同时添加其行业
        industry_slug_map = {}
        for ind in cfg.get("tracking", {}).get("industries", []):
            if ind.get("slug") and ind.get("name"):
                industry_slug_map[ind["name"]] = ind["slug"]

        for co in matched_companies:
            ind_name = co_to_ind.get(co, "")
            if ind_name:
                slug = industry_slug_map.get(ind_name, ind_name.lower().replace(" ", "-").replace("/", "-"))
                matched_industries.add(slug)

        # Insert document
        self.conn.execute("""
            INSERT INTO documents
                (pdf_name, pdf_path, source_type, bank, report_date, title, summary, raw_json_path, md_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fp.name, str(fp), source_type, bank, report_date, title, summary, "", str(fp)))
        self.conn.commit()

        doc_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # doc_companies
        for co in matched_companies:
            self.conn.execute(
                "INSERT OR IGNORE INTO doc_companies(doc_id, company_name) VALUES (?,?)",
                (doc_id, co)
            )
            self.ensure_company(co)

        # doc_industries
        for slug in matched_industries:
            self.conn.execute(
                "INSERT OR IGNORE INTO doc_industries(doc_id, industry_slug, layer) VALUES (?,?,?)",
                (doc_id, slug, "independent")
            )
            self.ensure_industry(slug)

        # FTS
        co_str = ", ".join(sorted(matched_companies))
        ind_str = ", ".join(sorted(matched_industries))
        self.conn.execute(
            "INSERT INTO doc_fts(doc_id, title, bank, company, industry_tags, content_text) VALUES (?,?,?,?,?,?)",
            (doc_id, fp.name, bank, co_str, ind_str, content_text)
        )

        self.conn.commit()
        return doc_id

    # ========== 搜索 ==========

    def search(self, query: str, limit: int = 20, offset: int = 0,
               company: str = None, industry: str = None, bank: str = None,
               source_type: str = None) -> dict:
        """FTS5 全文搜索"""
        # 清洗特殊字符 (FTS5 语法保留字符: @ # $ % ^ & * ( ) - + = { } [ ] | \ : ; " ' < > , . ? / ~ ` !)
        clean = re.sub(r'[@#$%^&*()+\-={}\[\]|\\:;"\'<>,.?/~`!]', ' ', query)
        clean = re.sub(r'\s+', ' ', clean).strip()

        if not clean:
            return {"query": query, "total": 0, "results": [], "aggs": {}}

        # 构造 MATCH 查询：对每个词加前缀通配
        terms = [f'"{t}"' if " " in t else f"{t}*" for t in clean.split() if t]

        fts_query = " AND ".join(terms)

        where_parts = ["doc_fts MATCH ?"]
        params: list = [fts_query]

        if company:
            where_parts.append(
                "d.id IN (SELECT doc_id FROM doc_companies WHERE company_name LIKE ?)"
            )
            params.append(f"%{company}%")
        if industry:
            where_parts.append(
                "d.id IN (SELECT doc_id FROM doc_industries WHERE industry_slug=?)"
            )
            params.append(industry)
        if bank:
            where_parts.append("d.bank LIKE ?")
            params.append(f"%{bank}%")
        if source_type:
            where_parts.append("d.source_type=?")
            params.append(source_type)

        where_clause = " AND ".join(where_parts)

        # 统计总数
        count_sql = f"SELECT COUNT(*) FROM doc_fts JOIN documents d ON d.id = doc_fts.doc_id WHERE {where_clause}"
        total = self.conn.execute(count_sql, params).fetchone()[0]

        # 搜索结果
        sql = f"""
            SELECT d.id, d.pdf_name, d.bank, d.report_date, d.source_type,
                   d.title, d.summary, d.raw_json_path, d.md_path,
                   snippet(doc_fts, 3, '<mark>', '</mark>', '...', 32) AS snippet,
                   rank
            FROM doc_fts
            JOIN documents d ON d.id = doc_fts.doc_id
            WHERE {where_clause}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = self.conn.execute(sql, params).fetchall()

        results = []
        for r in rows:
            companies = [
                row[0] for row in self.conn.execute(
                    "SELECT company_name FROM doc_companies WHERE doc_id=?", (r["id"],)
                ).fetchall()
            ]
            industries = [
                row[0] for row in self.conn.execute(
                    "SELECT industry_slug FROM doc_industries WHERE doc_id=?", (r["id"],)
                ).fetchall()
            ]
            results.append({
                "doc_id": r["id"],
                "pdf_name": r["pdf_name"],
                "bank": r["bank"],
                "report_date": r["report_date"],
                "source_type": r["source_type"],
                "summary": (r["summary"] or "")[:200],
                "snippet": r["snippet"],
                "companies": companies,
                "industries": industries,
                "md_path": r["md_path"],
            })

        # 聚合
        agg = self._search_aggregations(query, company, industry, bank, source_type)

        return {"query": query, "total": total, "results": results, "aggs": agg}

    def _search_aggregations(self, base_query: str, company: str, industry: str,
                              bank: str, source_type: str) -> dict:
        """聚合计数 (banks/companies/industries)"""
        terms = [f'"{t}"' if " " in t else f"{t}*" for t in base_query.strip().split() if t]
        fts_q = " AND ".join(terms) if terms else ""

        where_parts = ["doc_fts MATCH ?"] if fts_q else ["1=1"]
        params = [fts_q] if fts_q else []

        def _run_doc_agg(select_col: str):
            w = " AND ".join(where_parts + [f"d.{select_col} IS NOT NULL AND d.{select_col} != ''"])
            rows = self.conn.execute(
                f"SELECT d.{select_col}, COUNT(*) as cnt FROM doc_fts JOIN documents d ON d.id = doc_fts.doc_id WHERE {w} GROUP BY d.{select_col} ORDER BY cnt DESC LIMIT 15",
                params
            ).fetchall()
            return {r[0]: r[1] for r in rows}

        # Companies come from doc_companies (JOIN via subquery)
        comp_where = " AND ".join(where_parts)
        comp_rows = self.conn.execute(f"""
            SELECT c.company_name, COUNT(DISTINCT d.id) as cnt
            FROM doc_fts
            JOIN documents d ON d.id = doc_fts.doc_id
            JOIN doc_companies c ON c.doc_id = d.id
            WHERE {comp_where}
            GROUP BY c.company_name ORDER BY cnt DESC LIMIT 15
        """, params).fetchall()

        return {
            "banks": _run_doc_agg("bank"),
            "companies": {r[0]: r[1] for r in comp_rows},
            "source_types": _run_doc_agg("source_type"),
        }

    # ========== 查询 ==========

    def get_document(self, doc_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["companies"] = [
            r[0] for r in self.conn.execute(
                "SELECT company_name FROM doc_companies WHERE doc_id=?", (doc_id,)
            ).fetchall()
        ]
        d["industries"] = [
            r[0] for r in self.conn.execute(
                "SELECT industry_slug FROM doc_industries WHERE doc_id=?", (doc_id,)
            ).fetchall()
        ]
        return d

    def get_company_documents(self, company: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute("""
            SELECT d.* FROM documents d
            JOIN doc_companies c ON c.doc_id = d.id
            WHERE c.company_name LIKE ? AND d.is_expired = 0
            ORDER BY d.report_date DESC LIMIT ?
        """, (f"%{company}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def get_industry_documents(self, slug: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute("""
            SELECT d.* FROM documents d
            JOIN doc_industries i ON i.doc_id = d.id
            WHERE i.industry_slug = ? AND d.is_expired = 0
            ORDER BY d.report_date DESC LIMIT ?
        """, (slug, limit)).fetchall()
        return [dict(r) for r in rows]

    def list_entities(self, entity_type: str = "company", active_only: bool = True) -> list[dict]:
        cond = "WHERE entity_type=? "
        params = [entity_type]
        if active_only:
            cond += "AND is_active=1 "
        rows = self.conn.execute(
            f"SELECT * FROM entity_registry {cond}ORDER BY report_count DESC",
            params
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        banks = self.conn.execute(
            "SELECT COUNT(DISTINCT bank) FROM documents WHERE bank != '' AND bank IS NOT NULL"
        ).fetchone()[0]
        companies = self.conn.execute(
            "SELECT COUNT(DISTINCT company_name) FROM doc_companies"
        ).fetchone()[0]
        industries = self.conn.execute(
            "SELECT COUNT(DISTINCT industry_slug) FROM doc_industries"
        ).fetchone()[0]
        source_dist = {
            r[0]: r[1] for r in self.conn.execute(
                "SELECT source_type, COUNT(*) FROM documents GROUP BY source_type"
            ).fetchall()
        }
        return {
            "total_documents": total,
            "banks": banks,
            "companies": companies,
            "industries": industries,
            "source_distribution": source_dist,
        }

    def get_dashboard_summary(self) -> dict:
        """Return dashboard counters without scanning the report filesystem."""
        row = self.conn.execute("""
            SELECT
                COUNT(*) AS analyzed,
                SUM(CASE WHEN alert_severity IN ('high', 'medium') THEN 1 ELSE 0 END) AS active_alerts
            FROM documents
            WHERE source_type='investment_banking' AND is_expired=0
        """).fetchone()
        companies = [
            r["company_name"] for r in self.conn.execute("""
                SELECT company_name
                FROM doc_companies
                GROUP BY company_name
                ORDER BY company_name
            """).fetchall()
        ]
        return {
            "analyzed": row["analyzed"] or 0,
            "active_alerts": row["active_alerts"] or 0,
            "companies": companies,
        }

    def update_company_overview_path(self, company: str, overview_path: str):
        self.conn.execute("""
            UPDATE entity_registry SET report_path=?, updated_at=datetime('now')
            WHERE canonical_name=? AND entity_type='company'
        """, (overview_path, company))
        self.conn.commit()

    def update_report_counts(self):
        rows = self.conn.execute("""
            SELECT company_name, COUNT(*) as cnt FROM doc_companies
            GROUP BY company_name
        """).fetchall()
        for r in rows:
            self.conn.execute(
                "UPDATE entity_registry SET report_count=? WHERE canonical_name=? AND entity_type='company'",
                (r[1], r[0])
            )
        self.conn.commit()

    def rebuild_fts(self):
        self.conn.execute("DELETE FROM doc_fts")
        docs = self.conn.execute("SELECT * FROM documents").fetchall()
        count = 0
        for d in docs:
            company_str = ", ".join(
                r[0] for r in self.conn.execute(
                    "SELECT company_name FROM doc_companies WHERE doc_id=?", (d["id"],)
                ).fetchall()
            )
            ind_str = ", ".join(
                r[0] for r in self.conn.execute(
                    "SELECT industry_slug FROM doc_industries WHERE doc_id=?", (d["id"],)
                ).fetchall()
            )
            content = (d["summary"] or "")[:5000]
            if d["md_path"] and Path(d["md_path"]).exists():
                try:
                    content = Path(d["md_path"]).read_text(encoding="utf-8")[:5000]
                except UnicodeDecodeError:
                    content = Path(d["md_path"]).read_text(encoding="utf-8", errors="replace")[:5000]
            self.conn.execute(
                "INSERT INTO doc_fts(doc_id, title, bank, company, industry_tags, content_text) VALUES (?,?,?,?,?,?)",
                (d["id"], d["pdf_name"] or "", d["bank"] or "", company_str, ind_str, content)
            )
            count += 1
        self.conn.commit()
        return count

    # ========== 过期管理 ==========

    def mark_expired(self, dry_run: bool = True) -> dict:
        co_days, ind_days = _get_expire_config()
        now = datetime.now()

        co_cutoff = now - timedelta(days=co_days)
        ind_cutoff = now - timedelta(days=ind_days)

        co_expired = self.conn.execute("""
            SELECT d.id, d.pdf_name, d.report_date FROM documents d
            JOIN doc_companies c ON c.doc_id = d.id
            WHERE d.is_expired = 0 AND d.report_date < ?
        """, (co_cutoff.strftime("%Y-%m-%d"),)).fetchall()

        ind_expired = self.conn.execute("""
            SELECT d.id, d.pdf_name, d.report_date FROM documents d
            JOIN doc_industries i ON i.doc_id = d.id
            WHERE d.is_expired = 0 AND source_type='industry_report' AND d.report_date < ?
        """, (ind_cutoff.strftime("%Y-%m-%d"),)).fetchall()

        all_expired = {r[0]: r for r in co_expired + ind_expired}

        if not dry_run and all_expired:
            for doc_id in all_expired:
                self.conn.execute(
                    "UPDATE documents SET is_expired=1, updated_at=datetime('now') WHERE id=?",
                    (doc_id,)
                )
            self.conn.commit()

        return {
            "company_expire_days": co_days,
            "industry_expire_days": ind_days,
            "expired_count": len(all_expired),
            "company_expired": len(co_expired),
            "industry_expired": len(ind_expired),
        }

    def remove_expired_overviews(self, dry_run: bool = True) -> list[str]:
        co_days, _ = _get_expire_config()
        cutoff = (datetime.now() - timedelta(days=co_days)).strftime("%Y-%m-%d")

        removed = []
        for d in sorted(AI_SEMI_DIR.iterdir()):
            if not d.is_dir():
                continue
            overview = d / f"{d.name}_Overview.md"
            if not overview.exists():
                continue
            # 检查是否有活跃文档（非过期）
            company_name = d.name.replace("_Overview", "").replace("_", " ")
            active = self.conn.execute("""
                SELECT COUNT(*) FROM documents d2
                JOIN doc_companies c ON c.doc_id = d2.id
                WHERE c.company_name LIKE ? AND d2.is_expired = 0 AND d2.report_date >= ?
            """, (f"%{company_name}%", cutoff)).fetchone()[0]
            if active == 0:
                removed.append(str(overview))
                if not dry_run:
                    overview.unlink(missing_ok=True)

        return removed


# ========== CLI ==========

def main():
    parser = argparse.ArgumentParser(description="Report Index Database")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("backfill", help="Index all existing _analysis.json files")
    sub.add_parser("stats", help="Show index statistics")
    sub.add_parser("sync-entities", help="Sync entity registry from config.json")
    sub.add_parser("rebuild-fts", help="Rebuild FTS index")

    search_cmd = sub.add_parser("search", help="Search reports")
    search_cmd.add_argument("query", help="Search query")
    search_cmd.add_argument("--limit", type=int, default=20)
    search_cmd.add_argument("--company", help="Filter by company")
    search_cmd.add_argument("--industry", help="Filter by industry")
    search_cmd.add_argument("--json", action="store_true", help="JSON output")

    company_cmd = sub.add_parser("company", help="List documents for a company")
    company_cmd.add_argument("name", help="Company name")

    industry_cmd = sub.add_parser("industry", help="List documents for an industry")
    industry_cmd.add_argument("slug", help="Industry slug")

    ingest_cmd = sub.add_parser("ingest", help="Index independent research")
    ingest_cmd.add_argument("path", help="File path (.md / .pages)")
    ingest_cmd.add_argument("--title", default="", help="Report title")
    ingest_cmd.add_argument("--bank", default="Independent", help="Source name")

    expire_cmd = sub.add_parser("expired", help="Show/manage expired documents")
    expire_cmd.add_argument("--cleanup", action="store_true", help="Execute cleanup")

    args = parser.parse_args()

    if args.cmd == "backfill":
        idx = ReportIndex()
        idx.sync_entity_registry()
        all_jsons = sorted(REPORT_BASE.rglob("*_analysis.json"))
        print(f"Found {len(all_jsons)} analysis JSON files")
        ok = skip = err = 0
        for i, jp in enumerate(all_jsons, 1):
            doc_id = idx.index_analysis(str(jp))
            if doc_id:
                ok += 1
            elif doc_id is None:
                err += 1
            else:
                skip += 1
            if i % 50 == 0:
                print(f"  [{i}/{len(all_jsons)}] indexed={ok} skipped={skip} errors={err}")
        print(f"Done: indexed={ok} skipped={skip} errors={err}")
        idx.rebuild_fts()
        idx.update_report_counts()
        idx.close()

    elif args.cmd == "stats":
        idx = ReportIndex()
        s = idx.get_stats()
        print(f"Total documents: {s['total_documents']}")
        print(f"Banks: {s['banks']}  Companies: {s['companies']}  Industries: {s['industries']}")
        print(f"Source distribution: {s['source_distribution']}")
        idx.close()

    elif args.cmd == "sync-entities":
        idx = ReportIndex()
        r = idx.sync_entity_registry()
        print(f"Synced: {r['companies']} companies, {r['industries']} industries")
        idx.close()

    elif args.cmd == "rebuild-fts":
        idx = ReportIndex()
        n = idx.rebuild_fts()
        print(f"Rebuilt FTS: {n} documents indexed")
        idx.close()

    elif args.cmd == "search":
        idx = ReportIndex()
        r = idx.search(args.query, limit=args.limit,
                       company=args.company, industry=args.industry)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"🔍 \"{r['query']}\" — {r['total']} results\n")
            for item in r["results"]:
                companies = ", ".join(item["companies"])
                bank = item["bank"] or "?"
                print(f"  [{bank:12s}] {item['pdf_name'][:60]}")
                print(f"    Companies: {companies}")
                print(f"    Snippet: {item['snippet']}")
                print()
        idx.close()

    elif args.cmd == "company":
        idx = ReportIndex()
        docs = idx.get_company_documents(args.name)
        print(f"📊 {args.name}: {len(docs)} documents\n")
        for d in docs:
            print(f"  [{d['bank']:12s}] {d['pdf_name']} ({d['report_date']})")
        idx.close()

    elif args.cmd == "industry":
        idx = ReportIndex()
        docs = idx.get_industry_documents(args.slug)
        print(f"📊 {args.slug}: {len(docs)} documents\n")
        for d in docs:
            print(f"  [{d['bank']:12s}] {d['pdf_name']} ({d['report_date']})")
        idx.close()

    elif args.cmd == "ingest":
        idx = ReportIndex()
        path = args.path
        if path.startswith("~"):
            path = str(Path(path).expanduser())
        doc_id = idx.index_independent_research(path, title=args.title, bank=args.bank)
        if doc_id:
            print(f"  ✅ Indexed as doc_id={doc_id}")
        else:
            print("  ❌ Failed to index")
        idx.close()

    elif args.cmd == "expired":
        idx = ReportIndex()
        r = idx.mark_expired(dry_run=not args.cleanup)
        print(f"Company expire: {r['company_expire_days']}d, Industry expire: {r['industry_expire_days']}d")
        print(f"Expired: {r['expired_count']} ({r['company_expired']} company + {r['industry_expired']} industry)")
        if args.cleanup:
            removed = idx.remove_expired_overviews(dry_run=False)
            if removed:
                print(f"Removed overview files: {len(removed)}")
                for f in removed:
                    print(f"  🗑️ {f}")
        else:
            removed = idx.remove_expired_overviews(dry_run=True)
            if removed:
                print(f"Would remove {len(removed)} overview files:")
                for f in removed:
                    print(f"  📄 {f}")
        idx.close()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

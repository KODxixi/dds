#!/usr/bin/env python3
"""Archive the public, robots-allowed surface of dcbbs.com.

The crawler deliberately stays inside these boundaries:

* public resource detail pages: https://m.dcbbs.com/p-<id>.html
* public news detail pages: https://www.dcbbs.com/i-<id>.html
* preview images whose exact /fileroot1/ URLs are present in a resource page
* public poster images plus metadata-only video preview URLs explicitly present in a resource page

It never calls download, login, search, FlexPaper, View.aspx, or UserManage
endpoints.  Paid originals are not downloaded.  Public preview text and page
images are labelled as previews rather than complete documents.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError


BASE_WWW = "https://www.dcbbs.com"
BASE_MOBILE = "https://m.dcbbs.com"
DEFAULT_START = date(2024, 7, 13)
DEFAULT_END = date(2026, 7, 13)
DEFAULT_RESOURCE_MIN = 211_506
DEFAULT_RESOURCE_MAX = 229_746
DEFAULT_NEWS_MIN = 46_177
DEFAULT_NEWS_MAX = 165_599
USER_AGENT = (
    "Mozilla/5.0 (compatible; DDS-Research-Archive/1.0; "
    "+https://www.dcbbs.com/robots.txt)"
)
USAGE_SCOPE = "restricted/internal-research"
RIGHTS_NOTICE = (
    "DCBBS states that uploaded resources are for learning/exchange only and "
    "must not be used commercially or redistributed without authorization."
)

RESOURCE_URL_RE = re.compile(r"^https://m\.dcbbs\.com/p-(\d+)\.html$")
NEWS_URL_RE = re.compile(r"^https://www\.dcbbs\.com/i-(\d+)\.html$")
PREVIEW_URL_RE = re.compile(
    r"^https://(?:www|m)\.dcbbs\.com/fileroot1/[^?#]+\.(?:gif|png|jpe?g|webp)$",
    re.IGNORECASE,
)
RESOURCE_MEDIA_URL_RE = re.compile(
    r"^https://(?:www|m)\.dcbbs\.com/(?:fileroot1|fileroot_temp1)/[^?#]+\.(?:gif|png|jpe?g|webp)$",
    re.IGNORECASE,
)
RESOURCE_VIDEO_URL_RE = re.compile(
    r"^https://(?:www|m)\.dcbbs\.com/fileroot_temp1/[^?#]+\.(?:mp4|webm)$",
    re.IGNORECASE,
)
ID_IN_URL_RE = re.compile(r"/([pi])-(\d+)\.html", re.IGNORECASE)
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
DATETIME_RE = re.compile(r"(20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\xa0", " ").split())


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"[\d,]+", value)
    return int(match.group(0).replace(",", "")) if match else None


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    value = re.sub(r"\.(?:pdf|pptx?|docx?|xlsx?)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[-_]?佰策地产文库(?:dcbbs\.com)?$", "", value, flags=re.IGNORECASE)
    value = value.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", value).strip("-_—")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.part")
    tmp.write_bytes(content)
    tmp.replace(path)


class RateLimiter:
    """A process-wide fixed-interval limiter shared by worker threads."""

    def __init__(self, requests_per_second: float) -> None:
        self.interval = 0.0 if requests_per_second <= 0 else 1.0 / requests_per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)


class RobotsGuard:
    def __init__(self, timeout: float = 30.0) -> None:
        self.parsers: dict[str, RobotFileParser] = {}
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"})
        for base in (BASE_WWW, BASE_MOBILE):
            robots_url = f"{base}/robots.txt"
            response = session.get(robots_url, timeout=timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            self.parsers[urlparse(base).netloc] = parser

    def ensure_allowed(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in self.parsers:
            raise ValueError(f"URL outside approved hosts: {url}")

        explicitly_allowed = bool(
            RESOURCE_URL_RE.fullmatch(url)
            or NEWS_URL_RE.fullmatch(url)
            or PREVIEW_URL_RE.fullmatch(url)
            or RESOURCE_MEDIA_URL_RE.fullmatch(url)
            or url in {f"{BASE_WWW}/robots.txt", f"{BASE_MOBILE}/robots.txt"}
        )
        if not explicitly_allowed:
            raise ValueError(f"URL outside hard allowlist: {url}")
        if not self.parsers[parsed.netloc].can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows URL: {url}")


class Fetcher:
    def __init__(
        self,
        guard: RobotsGuard,
        rate: float,
        timeout: float,
        retries: int,
    ) -> None:
        self.guard = guard
        self.limiter = RateLimiter(rate)
        self.timeout = timeout
        self.retries = retries
        self.local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.4",
                }
            )
            self.local.session = session
        return session

    def get(self, url: str) -> requests.Response:
        self.guard.ensure_allowed(url)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                # Never let requests follow a redirect before the destination has
                # passed the same hard allowlist and robots checks.  These archive
                # endpoints are expected to be stable, so a redirect is recorded as
                # an error instead of being followed at all.
                response = self._session().get(url, timeout=self.timeout, allow_redirects=False)
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = urljoin(url, response.headers.get("Location", ""))
                    raise RuntimeError(f"redirect blocked: {url} -> {target or '(missing Location)'}")
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(f"retryable HTTP {response.status_code}")
                return response
            except (requests.RequestException, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(30.0, (2**attempt) + random.uniform(0.2, 0.9)))
        raise RuntimeError(f"GET failed after {self.retries + 1} attempts: {url}: {last_error}")


SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    doc_id INTEGER PRIMARY KEY,
    source_url TEXT NOT NULL,
    mobile_url TEXT NOT NULL,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    upload_date TEXT NOT NULL,
    document_format TEXT,
    page_count INTEGER,
    file_size TEXT,
    cost TEXT,
    uploader TEXT,
    uploader_url TEXT,
    categories_json TEXT NOT NULL,
    keywords_json TEXT NOT NULL,
    description TEXT,
    preview_text TEXT,
    preview_html TEXT,
    preview_image_urls_json TEXT NOT NULL,
    public_media_urls_json TEXT NOT NULL,
    public_video_urls_json TEXT NOT NULL,
    preview_public INTEGER NOT NULL,
    thumbnail_url TEXT,
    rights_notice TEXT NOT NULL,
    usage_scope TEXT NOT NULL,
    raw_path TEXT,
    etag TEXT,
    last_modified TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news (
    article_id INTEGER PRIMARY KEY,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    published_date TEXT NOT NULL,
    category TEXT,
    author TEXT,
    author_url TEXT,
    views INTEGER,
    summary TEXT,
    keywords_json TEXT NOT NULL,
    body_text TEXT,
    body_html TEXT,
    images_json TEXT NOT NULL,
    links_json TEXT NOT NULL,
    previous_id INTEGER,
    next_id INTEGER,
    rights_notice TEXT NOT NULL,
    usage_scope TEXT NOT NULL,
    raw_path TEXT,
    etag TEXT,
    last_modified TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_items (
    kind TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (kind, item_id)
);

CREATE TABLE IF NOT EXISTS preview_files (
    doc_id INTEGER NOT NULL,
    preview_index INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT,
    content_type TEXT,
    byte_count INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL,
    error TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, preview_index)
);

CREATE TABLE IF NOT EXISTS resource_media_files (
    doc_id INTEGER NOT NULL,
    media_index INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT,
    content_type TEXT,
    byte_count INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL,
    error TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, media_index)
);

CREATE TABLE IF NOT EXISTS resource_media_scan (
    doc_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    error TEXT,
    scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_media_files (
    article_id INTEGER NOT NULL,
    media_index INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT,
    content_type TEXT,
    byte_count INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL,
    error TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (article_id, media_index)
);

CREATE TABLE IF NOT EXISTS news_chain_edges (
    run_id TEXT NOT NULL,
    min_id INTEGER NOT NULL,
    max_id INTEGER NOT NULL,
    current_id INTEGER NOT NULL,
    previous_id INTEGER NOT NULL,
    reverse_next_id INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (run_id, current_id)
);

CREATE TABLE IF NOT EXISTS open_source_matches (
    doc_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    publisher TEXT NOT NULL,
    match_type TEXT NOT NULL,
    evidence TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, source_url)
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resources_date ON resources(upload_date);
CREATE INDEX IF NOT EXISTS idx_news_date ON news(published_date);
CREATE INDEX IF NOT EXISTS idx_crawl_kind_status ON crawl_items(kind, status);
CREATE INDEX IF NOT EXISTS idx_news_chain_status ON news_chain_edges(status);
"""


RESOURCE_COLUMNS = [
    "doc_id",
    "source_url",
    "mobile_url",
    "title",
    "normalized_title",
    "upload_date",
    "document_format",
    "page_count",
    "file_size",
    "cost",
    "uploader",
    "uploader_url",
    "categories_json",
    "keywords_json",
    "description",
    "preview_text",
    "preview_html",
    "preview_image_urls_json",
    "public_media_urls_json",
    "public_video_urls_json",
    "preview_public",
    "thumbnail_url",
    "rights_notice",
    "usage_scope",
    "raw_path",
    "etag",
    "last_modified",
    "fetched_at",
]

NEWS_COLUMNS = [
    "article_id",
    "source_url",
    "title",
    "published_at",
    "published_date",
    "category",
    "author",
    "author_url",
    "views",
    "summary",
    "keywords_json",
    "body_text",
    "body_html",
    "images_json",
    "links_json",
    "previous_id",
    "next_id",
    "rights_notice",
    "usage_scope",
    "raw_path",
    "etag",
    "last_modified",
    "fetched_at",
]


class Store:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.db_path = output / "normalized" / "dcbbs.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate_schema()
        self._seed_metadata()
        self._seed_known_gaps()
        self._seed_open_source_matches()
        self.conn.commit()

    def _migrate_schema(self) -> None:
        resource_columns = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(resources)")
        }
        if "public_media_urls_json" not in resource_columns:
            self.conn.execute(
                "ALTER TABLE resources ADD COLUMN public_media_urls_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "public_video_urls_json" not in resource_columns:
            self.conn.execute(
                "ALTER TABLE resources ADD COLUMN public_video_urls_json TEXT NOT NULL DEFAULT '[]'"
            )
        chain_columns = {
            str(row[1]) for row in self.conn.execute("PRAGMA table_info(news_chain_edges)")
        }
        if chain_columns and not {"run_id", "min_id", "max_id"}.issubset(chain_columns):
            # This table contains derived navigation-audit evidence only.  The
            # pre-run-id development schema cannot be scoped safely, so rebuild
            # just this audit table without touching archived content.
            self.conn.execute("DROP TABLE news_chain_edges")
            self.conn.executescript(
                """
                CREATE TABLE news_chain_edges (
                    run_id TEXT NOT NULL,
                    min_id INTEGER NOT NULL,
                    max_id INTEGER NOT NULL,
                    current_id INTEGER NOT NULL,
                    previous_id INTEGER NOT NULL,
                    reverse_next_id INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, current_id)
                );
                CREATE INDEX IF NOT EXISTS idx_news_chain_status
                    ON news_chain_edges(status);
                """
            )
            self.conn.execute("DELETE FROM metadata WHERE key LIKE 'news_chain_%'")
            self.conn.execute(
                """UPDATE crawl_items
                   SET status='gap_hint',
                       error='legacy navigation evidence removed during migration; requires verification',
                       fetched_at=?
                   WHERE kind='news' AND status='link_gap'""",
                (utc_now(),),
            )

    def _seed_metadata(self) -> None:
        values = {
            "source": "https://www.dcbbs.com/",
            "window_start": DEFAULT_START.isoformat(),
            "window_end": DEFAULT_END.isoformat(),
            "usage_scope": USAGE_SCOPE,
            "rights_notice": RIGHTS_NOTICE,
            "crawler_version": "1.3",
        }
        self.conn.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", values.items()
        )

    def _seed_known_gaps(self) -> None:
        now = utc_now()
        resource_gap = (("resource", item_id, "gap_hint", "candidate gap; requires direct GET", now) for item_id in range(214_234, 215_128))
        news_gap = (("news", item_id, "gap_hint", "candidate gap; requires direct GET", now) for item_id in range(129_162, 130_158))
        self.conn.executemany(
            "INSERT OR IGNORE INTO crawl_items(kind,item_id,status,error,fetched_at) VALUES (?,?,?,?,?)",
            resource_gap,
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO crawl_items(kind,item_id,status,error,fetched_at) VALUES (?,?,?,?,?)",
            news_gap,
        )
        # v1.1 migration: an earlier build treated these ranges as confirmed
        # missing.  They are only navigation hints, so force a direct GET on
        # the next crawl before they can count as resolved.
        self.conn.execute(
            """UPDATE crawl_items SET status='gap_hint', error='candidate gap; requires direct GET'
               WHERE status='known_missing'
                 AND ((kind='resource' AND item_id BETWEEN 214234 AND 215127)
                   OR (kind='news' AND item_id BETWEEN 129162 AND 130157))"""
        )

    def _seed_open_source_matches(self) -> None:
        now = utc_now()
        rows = [
            (
                229745,
                "https://www.cih-index.com/report/detail/123120.html",
                "中指云",
                "exact",
                "title, period and 23-page count match; multiple preview metrics match",
                "public metadata/summary",
                now,
            ),
            (
                229744,
                "https://www.cih-index.com/report/detail/123261.html",
                "中指云",
                "exact",
                "title, period and 21-page count match; multiple preview metrics match",
                "public metadata/summary",
                now,
            ),
            (
                229732,
                "https://www.cih-index.com/report/detail/123250.html",
                "中指云",
                "exact",
                "title, week range and 17-page count match; numeric fingerprint matches",
                "public metadata/summary",
                now,
            ),
            (
                229746,
                "https://mreport.cih-index.com/cih/detail/122605.html",
                "中指云",
                "high_confidence",
                "official title adds a weak '-快报' suffix; 17 pages and numeric fingerprint match",
                "public metadata/summary",
                now,
            ),
        ]
        verified_matches = [
            (224510, "https://www.cih-index.com/report/detail/89023.html", "exact", "same title, 65 pages and 2024-11 report period"),
            (226150, "https://www.cih-index.com/report/detail/93670.html", "exact", "normalized title, 26 pages and 2025-01 period match"),
            (226171, "https://www.cih-index.com/report/detail/93924.html", "high_confidence", "same Changshu week, date range and 18 pages; official title omits Suzhou prefix"),
            (226179, "https://www.cih-index.com/report/detail/94455.html", "exact", "same Deyang week 09 and 14 pages"),
            (226181, "https://www.cih-index.com/report/detail/94441.html", "high_confidence", "same Haikou title and period; 20 versus 18 pages indicates a version difference"),
            (226187, "https://www.cih-index.com/report/detail/94299.html", "exact", "same Jinan week 09 and 22 pages"),
            (226188, "https://www.cih-index.com/report/detail/92758.html", "high_confidence", "same Nanchong 2025-01 report; 65 versus 63 pages indicates a version difference"),
            (226191, "https://www.cih-index.com/report/detail/98457.html", "exact", "same 300-city land report, 2025-04 period and 24 pages"),
            (226208, "https://www.cih-index.com/report/detail/94300.html", "exact", "same Chengdu week 09 and 23 pages"),
            (226213, "https://www.cih-index.com/report/detail/94366.html", "exact", "same Guiyang week 09 and 22 pages"),
            (226214, "https://www.cih-index.com/report/detail/94460.html", "exact", "same Huaibei week 09 and 14 pages"),
            (226219, "https://www.cih-index.com/report/detail/94323.html", "exact", "same Quanzhou week 09 and 22 pages"),
            (226222, "https://www.cih-index.com/report/detail/93699.html", "exact", "normalized title, 26 pages and 2025-01 period match"),
            (226229, "https://www.cih-index.com/report/detail/93672.html", "high_confidence", "same Chongqing 2025-01 report; 26 versus 27 pages indicates a version difference"),
            (226245, "https://www.cih-index.com/report/detail/93591.html", "exact", "normalized title, 26 pages and 2025-01 period match"),
            (226254, "https://www.cih-index.com/report/detail/94242.html", "exact", "same Foshan week 09 and 22 pages"),
            (226257, "https://www.cih-index.com/report/detail/94339.html", "high_confidence", "same Linan week, date range and 15 pages; official title omits Hangzhou prefix"),
            (226258, "https://www.cih-index.com/report/detail/94408.html", "exact", "same Dalian week 09 and 22 pages"),
            (226287, "https://www.cih-index.com/report/detail/90983.html", "exact", "same title, 70 pages and 2024-12 report period"),
        ]
        rows.extend(
            (
                doc_id,
                source_url,
                "中指云",
                match_type,
                evidence,
                "public metadata/summary/first three preview pages",
                now,
            )
            for doc_id, source_url, match_type, evidence in verified_matches
        )
        rows.append(
            (
                212953,
                "https://www.most.gov.cn/zxgz/jgdj/jcdt/202107/t20210707_175732.html",
                "中华人民共和国科学技术部",
                "context_only",
                "same exhibition center and its four public theme areas; not the same design document",
                "public background context only; no substitute document found",
                now,
            )
        )
        self.conn.executemany(
            """INSERT OR IGNORE INTO open_source_matches
               (doc_id,source_url,publisher,match_type,evidence,source_scope,checked_at)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )

    def existing_ids(
        self,
        kind: str,
        ids: list[int],
        accept_link_gaps: bool = True,
    ) -> set[int]:
        if not ids:
            return set()
        low, high = min(ids), max(ids)
        statuses = ("ok", "missing", "known_missing", "link_gap")
        if not accept_link_gaps:
            statuses = ("ok", "missing", "known_missing")
        placeholders = ",".join("?" for _ in statuses)
        rows = self.conn.execute(
            f"""SELECT item_id FROM crawl_items
                WHERE kind=? AND item_id BETWEEN ? AND ?
                  AND status IN ({placeholders})""",
            (kind, low, high, *statuses),
        )
        return {int(row[0]) for row in rows}

    def mark_item(self, kind: str, item_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO crawl_items(kind,item_id,status,error,fetched_at)
               VALUES (?,?,?,?,?)""",
            (kind, item_id, status, error, utc_now()),
        )

    def mark_news_gap(
        self,
        current_id: int,
        previous_id: int | None,
        min_id: int,
        max_id: int,
    ) -> int:
        if previous_id is None or previous_id >= current_id - 1:
            return 0
        if previous_id < min_id or current_id > max_id + 1:
            return 0
        rows = [
            (
                "news",
                item_id,
                "link_gap",
                (
                    f"skipped by bidirectionally validated navigation: "
                    f"i-{current_id} previous=i-{previous_id}; "
                    f"i-{previous_id} next=i-{current_id}"
                ),
                utc_now(),
            )
            for item_id in range(previous_id + 1, current_id)
        ]
        changes_before = self.conn.total_changes
        self.conn.executemany(
            """INSERT INTO crawl_items(kind,item_id,status,error,fetched_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(kind,item_id) DO UPDATE SET
                 status=excluded.status,
                 error=excluded.error,
                 fetched_at=excluded.fetched_at
               WHERE crawl_items.status='gap_hint'""",
            rows,
        )
        return self.conn.total_changes - changes_before

    def mark_news_chain_edge(
        self,
        run_id: str,
        min_id: int,
        max_id: int,
        current_id: int,
        previous_id: int,
        reverse_next_id: int | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO news_chain_edges
               (run_id,min_id,max_id,current_id,previous_id,reverse_next_id,status,error,checked_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                min_id,
                max_id,
                current_id,
                previous_id,
                reverse_next_id,
                status,
                error,
                utc_now(),
            ),
        )

    def upsert_resource(self, record: dict[str, Any]) -> None:
        placeholders = ",".join("?" for _ in RESOURCE_COLUMNS)
        update = ",".join(f"{column}=excluded.{column}" for column in RESOURCE_COLUMNS[1:])
        self.conn.execute(
            f"INSERT INTO resources({','.join(RESOURCE_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(doc_id) DO UPDATE SET {update}",
            [record.get(column) for column in RESOURCE_COLUMNS],
        )
        self.conn.execute(
            """INSERT OR REPLACE INTO resource_media_scan(doc_id,status,error,scanned_at)
               VALUES (?,'ok',NULL,?)""",
            (record["doc_id"], utc_now()),
        )

    def upsert_news(self, record: dict[str, Any]) -> None:
        placeholders = ",".join("?" for _ in NEWS_COLUMNS)
        update = ",".join(f"{column}=excluded.{column}" for column in NEWS_COLUMNS[1:])
        self.conn.execute(
            f"INSERT INTO news({','.join(NEWS_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(article_id) DO UPDATE SET {update}",
            [record.get(column) for column in NEWS_COLUMNS],
        )

    def checkpoint(self, phase: str, cursor: int | None = None) -> None:
        counts = {
            "resources": self.conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
            "news": self.conn.execute("SELECT COUNT(*) FROM news").fetchone()[0],
            "preview_files": self.conn.execute(
                "SELECT COUNT(*) FROM preview_files WHERE status='ok'"
            ).fetchone()[0],
            "resource_media_files": self.conn.execute(
                "SELECT COUNT(*) FROM resource_media_files WHERE status='ok'"
            ).fetchone()[0],
            "resource_media_scanned": self.conn.execute(
                "SELECT COUNT(*) FROM resource_media_scan WHERE status='ok'"
            ).fetchone()[0],
            "news_media_files": self.conn.execute(
                "SELECT COUNT(*) FROM news_media_files WHERE status='ok'"
            ).fetchone()[0],
            "errors": self.conn.execute(
                "SELECT COUNT(*) FROM crawl_items WHERE status IN ('error','invalid')"
            ).fetchone()[0],
        }
        payload = {
            "phase": phase,
            "cursor": cursor,
            "updated_at": utc_now(),
            "counts": counts,
            "database": str(self.db_path),
        }
        atomic_write_text(self.output / "checkpoint.json", json.dumps(payload, ensure_ascii=False, indent=2))

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def meta_content(soup: BeautifulSoup, key: str) -> str:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return clean_text(tag.get("content")) if tag else ""


def canonical_url(soup: BeautifulSoup, fallback: str) -> str:
    tag = soup.find("link", rel=lambda value: value and "canonical" in value)
    return urljoin(fallback, tag.get("href")) if tag and tag.get("href") else fallback


def extract_resource_public_media(
    soup: BeautifulSoup,
    preview_urls: list[str],
    thumbnail_url: str,
) -> tuple[list[str], list[str]]:
    image_candidates: list[str] = [thumbnail_url] if thumbnail_url else []
    video_candidates: list[str] = []
    for node in soup.find_all("video"):
        if node.get("poster"):
            image_candidates.append(urljoin(BASE_MOBILE, node.get("poster")))
        if node.get("src"):
            video_candidates.append(urljoin(BASE_MOBILE, node.get("src")))
    for node in soup.find_all("source"):
        if node.get("src"):
            video_candidates.append(urljoin(BASE_MOBILE, node.get("src")))

    public_media_urls: list[str] = []
    for candidate in image_candidates:
        candidate = urljoin(BASE_MOBILE, candidate)
        if (
            RESOURCE_MEDIA_URL_RE.fullmatch(candidate)
            and candidate not in preview_urls
            and candidate not in public_media_urls
        ):
            public_media_urls.append(candidate)

    public_video_urls: list[str] = []
    for candidate in video_candidates:
        candidate = urljoin(BASE_MOBILE, candidate)
        if RESOURCE_VIDEO_URL_RE.fullmatch(candidate) and candidate not in public_video_urls:
            public_video_urls.append(candidate)
    return public_media_urls, public_video_urls


def parse_resource_html(doc_id: int, html: bytes, headers: requests.structures.CaseInsensitiveDict) -> dict[str, Any]:
    soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
    if meta_content(soup, "og:type").lower() != "document":
        raise ValueError("page is not an og:type=document resource")

    page_text = clean_text(soup.get_text(" ", strip=True))
    id_match = re.search(r"文档编号[：:]\s*(\d+)", page_text)
    structured_ids: set[int] = set()
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            match = RESOURCE_URL_RE.fullmatch(clean_text(str(candidate.get("@id", ""))))
            if match:
                structured_ids.add(int(match.group(1)))
    if not structured_ids:
        for script in soup.find_all("script"):
            for value in re.findall(r"\bvar\s+id\s*=\s*['\"](\d+)['\"]", script.string or script.get_text()):
                structured_ids.add(int(value))
    marker_id = int(id_match.group(1)) if id_match else None
    if marker_id is not None and marker_id != doc_id:
        raise ValueError("visible document id marker is mismatched")
    if marker_id is None and structured_ids != {doc_id}:
        raise ValueError("document id marker is absent or mismatched")

    title = meta_content(soup, "og:title")
    upload_date = meta_content(soup, "og:release_date")
    if not title or not DATE_RE.fullmatch(upload_date):
        raise ValueError("required title/upload_date metadata is missing")

    info: dict[str, str] = {}
    for span in soup.find_all("span"):
        text = clean_text(span.get_text(" ", strip=True))
        for label, key in (
            ("上传时间", "upload_date"),
            ("格式", "format"),
            ("页数", "pages"),
            ("大小", "size"),
        ):
            if text.startswith(label):
                info[key] = clean_text(re.sub(rf"^{label}[：:]", "", text))

    uploader = ""
    uploader_url = ""
    for link in soup.find_all("a", href=True):
        if re.search(r"/u-\d+\.html", link["href"]):
            uploader = clean_text(link.get_text(" ", strip=True))
            uploader_url = urljoin(BASE_WWW, link["href"])
            break

    categories: list[dict[str, str]] = []
    for link in soup.find_all("a", href=True):
        if re.search(r"/c-[\d]+\.html", link["href"]):
            value = {"name": clean_text(link.get_text(" ", strip=True)), "url": urljoin(BASE_WWW, link["href"])}
            if value["name"] and value not in categories:
                categories.append(value)

    keywords: list[str] = []
    keyword_meta = meta_content(soup, "keywords")
    if keyword_meta:
        keywords.extend(clean_text(item) for item in re.split(r"[,，]", keyword_meta) if clean_text(item))
    for link in soup.find_all("a", href=True):
        if "/tag/" in link["href"]:
            value = clean_text(link.get_text(" ", strip=True))
            if value and value not in keywords:
                keywords.append(value)

    preview = soup.select_one(".detail-article.prolistshowimg")
    preview_text = "\n".join(
        clean_text(node.get_text(" ", strip=True)) for node in preview.select("p") if clean_text(node.get_text(" ", strip=True))
    ) if preview else ""
    preview_html = preview.decode_contents() if preview else ""

    preview_urls: list[str] = []
    image_nodes = soup.select("#page .page img")
    if not image_nodes:
        image_nodes = [
            node for node in soup.find_all("img") if re.search(r"_第\d+页", clean_text(node.get("alt")))
        ]
    for node in image_nodes:
        candidate = node.get("data-original") or node.get("src")
        if not candidate:
            continue
        candidate = urljoin(BASE_MOBILE, candidate)
        if PREVIEW_URL_RE.fullmatch(candidate) and candidate not in preview_urls:
            preview_urls.append(candidate)

    thumbnail_url = meta_content(soup, "og:image")
    public_media_urls, public_video_urls = extract_resource_public_media(
        soup, preview_urls, thumbnail_url
    )

    source_url = f"{BASE_WWW}/p-{doc_id}.html"
    document_format = (meta_content(soup, "og:document:type") or info.get("format", "")).upper()
    page_count = parse_int(meta_content(soup, "og:document:page") or info.get("pages"))
    if re.search(r"[（(]\s*0\s*页\s*[）)]", title) and (page_count is None or page_count > 1_000_000):
        page_count = 0
    return {
        "doc_id": doc_id,
        "source_url": source_url,
        "mobile_url": f"{BASE_MOBILE}/p-{doc_id}.html",
        "title": title,
        "normalized_title": normalize_title(title),
        "upload_date": upload_date,
        "document_format": document_format,
        "page_count": page_count,
        "file_size": info.get("size", ""),
        "cost": meta_content(soup, "og:document:cost"),
        "uploader": uploader,
        "uploader_url": uploader_url,
        "categories_json": json_dumps(categories),
        "keywords_json": json_dumps(keywords),
        "description": meta_content(soup, "description") or meta_content(soup, "og:description"),
        "preview_text": preview_text,
        "preview_html": preview_html,
        "preview_image_urls_json": json_dumps(preview_urls),
        "public_media_urls_json": json_dumps(public_media_urls),
        "public_video_urls_json": json_dumps(public_video_urls),
        "preview_public": int(bool(preview_text or preview_urls or public_media_urls or public_video_urls)),
        "thumbnail_url": thumbnail_url,
        "rights_notice": RIGHTS_NOTICE,
        "usage_scope": USAGE_SCOPE,
        "raw_path": None,
        "etag": clean_text(headers.get("ETag")),
        "last_modified": clean_text(headers.get("Last-Modified")),
        "fetched_at": utc_now(),
    }


def clean_news_body(body: Any, base_url: str) -> tuple[str, str, list[str], list[dict[str, str]]]:
    fragment = BeautifulSoup(str(body), "html.parser")
    root = fragment.select_one(".P_conten") or fragment
    for node in root.select("script,style,noscript,[id*='Pager'],.paginator"):
        node.decompose()
    for node in list(root.find_all("div")):
        if "AspNetPager" in clean_text(node.get_text(" ", strip=True)):
            node.decompose()
    for nested_body in root.find_all("body"):
        nested_body.unwrap()

    images: list[str] = []
    for node in root.find_all("img"):
        candidate = node.get("data-original") or node.get("data-src") or node.get("src")
        if candidate:
            candidate = urljoin(base_url, candidate)
            if candidate not in images:
                images.append(candidate)

    links: list[dict[str, str]] = []
    for node in root.find_all("a", href=True):
        item = {"text": clean_text(node.get_text(" ", strip=True)), "url": urljoin(base_url, node["href"])}
        if item not in links:
            links.append(item)

    text = root.get_text("\n", strip=True).replace("\xa0", " ")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text, root.decode_contents(), images, links


def parse_news_html(article_id: int, html: bytes, headers: requests.structures.CaseInsensitiveDict) -> dict[str, Any]:
    soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
    article = soup.select_one("article.article-wrapper")
    title_node = soup.select_one(".article-header h1.article-title")
    if not article or not title_node:
        raise ValueError("article wrapper/title is missing")
    expected_url = f"{BASE_WWW}/i-{article_id}.html"
    source_url = canonical_url(soup, expected_url)
    canonical_match = NEWS_URL_RE.fullmatch(source_url)
    if not canonical_match or int(canonical_match.group(1)) != article_id:
        raise ValueError(f"canonical article id mismatch: {source_url}")
    title = clean_text(title_node.get_text(" ", strip=True))

    clock = soup.select_one(".post-meta-items .fa-clock-o")
    date_container = clock.find_parent(class_="post-meta-item") if clock else None
    date_text = clean_text(date_container.get_text(" ", strip=True)) if date_container else ""
    date_match = DATETIME_RE.search(date_text)
    if not date_match:
        raise ValueError("published date is missing")
    published_at = date_match.group(1)
    published_date = published_at[:10]

    eye = soup.select_one(".post-meta-items .fa-eye")
    view_container = eye.find_parent(class_="post-meta-item") if eye else None
    views = parse_int(clean_text(view_container.get_text(" ", strip=True))) if view_container else None

    category_links = soup.select(".bread-crumb a")
    category = clean_text(category_links[-1].get_text(" ", strip=True)) if category_links else ""

    author = ""
    author_url = ""
    for link in soup.select(".postMetaLockup--authorWithBio .u-flex1 a[href*='/u-']"):
        value = clean_text(link.get_text(" ", strip=True))
        if value:
            author = value
            author_url = urljoin(BASE_WWW, link.get("href"))
            break

    body = soup.select_one(".article-wrapper .grap .P_conten")
    if body:
        body_text, body_html, images, links = clean_news_body(body, f"{BASE_WWW}/i-{article_id}.html")
    else:
        body_text, body_html, images, links = "", "", [], []

    previous_id: int | None = None
    next_id: int | None = None
    nav_candidates: list[tuple[int, Any]] = []
    # Well-formed pages keep this block inside <article>.  Some imported news
    # bodies contain malformed markup that makes html.parser reparent the
    # source navigation div under <html>; fall back to the whole document only
    # when the scoped lookup found nothing.  The strict labels and i-ID hrefs
    # below prevent unrelated recommendation links from being accepted.
    for scope in (article, soup):
        boundary = article if scope is article else soup
        for label in scope.find_all(string=re.compile(r"^\s*上一篇\s*[：:]")):
            node = label.parent
            for _depth in range(4):
                if node is None or node is boundary:
                    break
                text = clean_text(node.get_text(" ", strip=True))
                if "下一篇" in text:
                    nav_candidates.append((len(text), node))
                    break
                node = node.parent
        if nav_candidates:
            break
    if nav_candidates:
        nav = min(nav_candidates, key=lambda item: item[0])[1]
        nav_html = nav.decode_contents()
        nav_base = r"(?:https://(?:www|m)\.dcbbs\.com)?/i-(\d+)\.html"
        previous_match = re.search(
            rf"上一篇\s*[：:]\s*<a\b[^>]*\bhref=[\"']{nav_base}",
            nav_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        next_match = re.search(
            rf"下一篇\s*[：:]\s*<a\b[^>]*\bhref=[\"']{nav_base}",
            nav_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if previous_match:
            previous_id = int(previous_match.group(1))
        if next_match:
            next_id = int(next_match.group(1))

    keywords = [clean_text(item) for item in re.split(r"[,，]", meta_content(soup, "keywords")) if clean_text(item)]
    return {
        "article_id": article_id,
        "source_url": source_url,
        "title": title,
        "published_at": published_at,
        "published_date": published_date,
        "category": category,
        "author": author,
        "author_url": author_url,
        "views": views,
        "summary": meta_content(soup, "description"),
        "keywords_json": json_dumps(keywords),
        "body_text": body_text,
        "body_html": body_html,
        "images_json": json_dumps(images),
        "links_json": json_dumps(links),
        "previous_id": previous_id,
        "next_id": next_id,
        "rights_notice": RIGHTS_NOTICE,
        "usage_scope": USAGE_SCOPE,
        "raw_path": None,
        "etag": clean_text(headers.get("ETag")),
        "last_modified": clean_text(headers.get("Last-Modified")),
        "fetched_at": utc_now(),
    }


@dataclass
class FetchResult:
    kind: str
    item_id: int
    status: str
    record: dict[str, Any] | None = None
    content: bytes | None = None
    error: str | None = None


def fetch_resource(fetcher: Fetcher, doc_id: int) -> FetchResult:
    url = f"{BASE_MOBILE}/p-{doc_id}.html"
    try:
        response = fetcher.get(url)
        if response.status_code == 404:
            return FetchResult("resource", doc_id, "missing")
        if response.status_code != 200:
            return FetchResult("resource", doc_id, "error", error=f"HTTP {response.status_code}")
        record = parse_resource_html(doc_id, response.content, response.headers)
        return FetchResult("resource", doc_id, "ok", record=record, content=response.content)
    except ValueError as exc:
        return FetchResult("resource", doc_id, "invalid", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - persisted for retry/reporting
        return FetchResult("resource", doc_id, "error", error=str(exc))


def fetch_news(fetcher: Fetcher, article_id: int) -> FetchResult:
    url = f"{BASE_WWW}/i-{article_id}.html"
    try:
        response = fetcher.get(url)
        if response.status_code == 404:
            return FetchResult("news", article_id, "missing")
        if response.status_code != 200:
            return FetchResult("news", article_id, "error", error=f"HTTP {response.status_code}")
        record = parse_news_html(article_id, response.content, response.headers)
        return FetchResult("news", article_id, "ok", record=record, content=response.content)
    except ValueError as exc:
        return FetchResult("news", article_id, "invalid", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        return FetchResult("news", article_id, "error", error=str(exc))


def raw_path_for(output: Path, kind: str, item_id: int, item_date: str) -> Path:
    year, month, day = item_date[:10].split("-")
    prefix = "p" if kind == "resource" else "i"
    directory = "resources" if kind == "resource" else "news"
    return output / "raw" / directory / year / month / day / f"{prefix}_{item_id}.html.gz"


def save_raw(output: Path, kind: str, item_id: int, item_date: str, content: bytes) -> str:
    path = raw_path_for(output, kind, item_id, item_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with gzip.open(tmp, "wb", compresslevel=6) as stream:
        stream.write(content)
    tmp.replace(path)
    return path.relative_to(output).as_posix()


def iter_descending(max_id: int, min_id: int, limit: int | None = None) -> Iterator[int]:
    count = 0
    for item_id in range(max_id, min_id - 1, -1):
        if limit is not None and count >= limit:
            break
        yield item_id
        count += 1


def chunks(values: Iterable[int], size: int) -> Iterator[list[int]]:
    batch: list[int] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def run_range(
    kind: str,
    store: Store,
    fetcher: Fetcher,
    min_id: int,
    max_id: int,
    start_date: date,
    end_date: date,
    workers: int,
    keep_raw: bool,
    max_items: int | None,
    verify_link_gaps: bool = False,
    report_empty_batches: bool = True,
) -> None:
    fetch_function = fetch_resource if kind == "resource" else fetch_news
    batch_size = max(32, workers * 8)
    total_candidates = max_id - min_id + 1
    if max_items is not None:
        total_candidates = min(total_candidates, max_items)
    started = time.monotonic()
    processed = 0
    inserted = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch in chunks(iter_descending(max_id, min_id, max_items), batch_size):
            existing = store.existing_ids(
                kind, batch, accept_link_gaps=not verify_link_gaps
            )
            pending = [item_id for item_id in batch if item_id not in existing]
            futures = {pool.submit(fetch_function, fetcher, item_id): item_id for item_id in pending}
            results = [future.result() for future in as_completed(futures)]
            results.sort(key=lambda result: result.item_id, reverse=True)

            for result in results:
                if result.status == "ok" and result.record and result.content is not None:
                    item_date_text = (
                        result.record["upload_date"] if kind == "resource" else result.record["published_date"]
                    )
                    item_date = date.fromisoformat(item_date_text)
                    if not (start_date <= item_date <= end_date):
                        store.mark_item(kind, result.item_id, "out_of_range", item_date_text)
                        continue
                    if keep_raw:
                        result.record["raw_path"] = save_raw(
                            store.output, kind, result.item_id, item_date_text, result.content
                        )
                    if kind == "resource":
                        store.upsert_resource(result.record)
                    else:
                        store.upsert_news(result.record)
                    store.mark_item(kind, result.item_id, "ok")
                    inserted += 1
                else:
                    store.mark_item(kind, result.item_id, result.status, result.error)
                    if result.status in {"error", "invalid"}:
                        errors += 1

            processed += len(batch)
            store.commit()
            store.checkpoint(f"crawl_{kind}", min(batch) - 1)
            elapsed = max(0.001, time.monotonic() - started)
            if report_empty_batches or pending:
                print(
                    f"[{kind}] {processed:,}/{total_candidates:,} candidates; "
                    f"new={inserted:,}; errors={errors:,}; {processed / elapsed:.2f} ids/s",
                    flush=True,
                )


def load_or_fetch_news_for_chain(
    store: Store,
    fetcher: Fetcher,
    article_id: int,
    start_date: date,
    end_date: date,
    keep_raw: bool,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Return a normalized news row, fetching it only when it is not archived."""

    existing = store.conn.execute(
        "SELECT * FROM news WHERE article_id=?", (article_id,)
    ).fetchone()
    if existing is not None and not force_refresh:
        record = dict(existing)
        item_date = date.fromisoformat(record["published_date"])
        if not (start_date <= item_date <= end_date):
            raise RuntimeError(
                f"navigation reached out-of-window i-{article_id}: {record['published_date']}"
            )
        return record, False

    result = fetch_news(fetcher, article_id)
    if result.status != "ok" or result.record is None or result.content is None:
        error = (
            f"navigation target fetch failed ({result.status}): "
            f"{result.error or 'no additional detail'}"
        )
        # A page linked by the global navigation graph is not safe to classify
        # as a terminal 404/invalid result.  Keep it retryable for direct-ID
        # fallback instead of silently punching a hole in the archive.
        store.mark_item("news", article_id, "error", error)
        store.commit()
        raise RuntimeError(
            f"navigation target i-{article_id} was not fetchable: "
            f"{result.status}: {result.error or ''}"
        )

    record = result.record
    item_date_text = record["published_date"]
    item_date = date.fromisoformat(item_date_text)
    if not (start_date <= item_date <= end_date):
        store.mark_item("news", article_id, "out_of_range", item_date_text)
        store.commit()
        raise RuntimeError(
            f"navigation reached out-of-window i-{article_id}: {item_date_text}"
        )
    if keep_raw:
        record["raw_path"] = save_raw(
            store.output, "news", article_id, item_date_text, result.content
        )
    elif force_refresh and existing is not None:
        record["raw_path"] = existing["raw_path"]
    store.upsert_news(record)
    store.mark_item("news", article_id, "ok")
    store.commit()
    return record, True


class NewsChainError(RuntimeError):
    """A validated navigation crawl failed and may require direct-ID fallback."""

    def __init__(self, message: str, run_id: str) -> None:
        super().__init__(message)
        self.run_id = run_id


def _run_news_chain_impl(
    store: Store,
    fetcher: Fetcher,
    min_id: int,
    max_id: int,
    start_date: date,
    end_date: date,
    keep_raw: bool,
    max_items: int | None,
    run_id: str,
) -> None:
    """Follow the public global previous/next chain with bidirectional checks.

    Every accessible article is archived once.  Numeric IDs skipped by a
    *bidirectionally* verified edge are recorded as ``link_gap`` rather than
    being mislabelled as HTTP 404s.  Any broken, cyclic, contradictory or
    one-way edge aborts the chain so the direct-ID crawler can be used as the
    explicit fallback for that interval.
    """

    if min_id > max_id:
        raise ValueError("news chain min_id must not exceed max_id")

    current_id = max_id
    expected_next_id: int | None = None
    visited: set[int] = set()
    nodes = 0
    fetched = 0
    reused = 0
    link_gaps = 0
    validated_edges = 0
    started = time.monotonic()
    completed = False

    while True:
        if max_items is not None and nodes >= max_items:
            break
        if current_id in visited:
            raise RuntimeError(f"news navigation cycle detected at i-{current_id}")
        if not (min_id <= current_id <= max_id):
            raise RuntimeError(f"news navigation escaped configured IDs at i-{current_id}")
        visited.add(current_id)

        record, was_fetched = load_or_fetch_news_for_chain(
            store, fetcher, current_id, start_date, end_date, keep_raw
        )
        nodes += 1
        fetched += int(was_fetched)
        reused += int(not was_fetched)

        if expected_next_id is not None:
            reverse_next_id = record.get("next_id")
            if reverse_next_id != expected_next_id and not was_fetched:
                # Stored navigation can become stale when a formerly-latest
                # article later gains a next page.  Refresh once before
                # declaring the public chain inconsistent.
                record, refreshed = load_or_fetch_news_for_chain(
                    store,
                    fetcher,
                    current_id,
                    start_date,
                    end_date,
                    keep_raw,
                    force_refresh=True,
                )
                fetched += int(refreshed)
                reverse_next_id = record.get("next_id")
            if reverse_next_id != expected_next_id:
                error = (
                    f"one-way navigation: i-{expected_next_id} previous=i-{current_id}, "
                    f"but i-{current_id} next={reverse_next_id!r}"
                )
                store.mark_news_chain_edge(
                    run_id,
                    min_id,
                    max_id,
                    expected_next_id,
                    current_id,
                    reverse_next_id,
                    "invalid",
                    error,
                )
                store.commit()
                raise RuntimeError(error)

            contradiction = store.conn.execute(
                """SELECT article_id FROM news
                   WHERE article_id>? AND article_id<?
                   ORDER BY article_id DESC LIMIT 1""",
                (current_id, expected_next_id),
            ).fetchone()
            if contradiction is not None:
                error = (
                    f"navigation edge i-{expected_next_id}->i-{current_id} skips "
                    f"archived i-{int(contradiction[0])}"
                )
                store.mark_news_chain_edge(
                    run_id,
                    min_id,
                    max_id,
                    expected_next_id,
                    current_id,
                    reverse_next_id,
                    "invalid",
                    error,
                )
                store.commit()
                raise RuntimeError(error)

            link_gaps += store.mark_news_gap(
                expected_next_id, current_id, min_id, max_id
            )
            store.mark_news_chain_edge(
                run_id,
                min_id,
                max_id,
                expected_next_id,
                current_id,
                reverse_next_id,
                "bidirectional_ok",
            )
            validated_edges += 1

        previous_id = record.get("previous_id")
        if current_id == min_id:
            if previous_id is None or previous_id >= current_id:
                raise RuntimeError(
                    f"invalid lower boundary navigation on i-{current_id}: {previous_id!r}"
                )
            completed = True
            store.conn.executemany(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                (
                    ("news_chain_status", "complete"),
                    ("news_chain_run_id", run_id),
                    ("news_chain_top_id", str(max_id)),
                    ("news_chain_bottom_id", str(min_id)),
                    ("news_chain_completed_at", utc_now()),
                ),
            )
            store.commit()
            break
        if previous_id is None:
            raise RuntimeError(f"missing previous link before lower boundary on i-{current_id}")
        previous_id = int(previous_id)
        if previous_id >= current_id:
            raise RuntimeError(
                f"non-descending previous link on i-{current_id}: i-{previous_id}"
            )
        if previous_id < min_id:
            raise RuntimeError(
                f"navigation ended at i-{current_id} before configured i-{min_id}; "
                f"previous=i-{previous_id}"
            )

        expected_next_id = current_id
        current_id = previous_id
        if nodes % 100 == 0:
            elapsed = max(0.001, time.monotonic() - started)
            print(
                f"[news-chain] nodes={nodes:,}; current=i-{current_id}; "
                f"new={fetched:,}; reused={reused:,}; link-gaps={link_gaps:,}; "
                f"edges={validated_edges:,}; {nodes / elapsed:.2f} articles/s",
                flush=True,
            )
        store.commit()
        store.checkpoint("crawl_news_chain", current_id)

    if not completed:
        store.conn.executemany(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
            (
                ("news_chain_status", "partial"),
                ("news_chain_run_id", run_id),
                ("news_chain_last_id", str(current_id)),
                ("news_chain_checked_at", utc_now()),
            ),
        )
        store.commit()

    elapsed = max(0.001, time.monotonic() - started)
    print(
        f"[news-chain] {'complete' if completed else 'partial'}; nodes={nodes:,}; "
        f"new={fetched:,}; reused={reused:,}; link-gaps={link_gaps:,}; "
        f"edges={validated_edges:,}; {nodes / elapsed:.2f} articles/s",
        flush=True,
    )


def run_news_chain(
    store: Store,
    fetcher: Fetcher,
    min_id: int,
    max_id: int,
    start_date: date,
    end_date: date,
    keep_raw: bool,
    max_items: int | None,
) -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{os.getpid()}"
    store.conn.execute(
        """DELETE FROM metadata WHERE key IN
           ('news_chain_last_id','news_chain_completed_at','news_chain_error')"""
    )
    store.conn.executemany(
        "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
        (
            ("news_chain_status", "running"),
            ("news_chain_run_id", run_id),
            ("news_chain_top_id", str(max_id)),
            ("news_chain_bottom_id", str(min_id)),
            ("news_chain_started_at", utc_now()),
        ),
    )
    store.commit()
    try:
        _run_news_chain_impl(
            store,
            fetcher,
            min_id,
            max_id,
            start_date,
            end_date,
            keep_raw,
            max_items,
            run_id,
        )
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
        store.conn.executemany(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
            (
                ("news_chain_status", status),
                ("news_chain_run_id", run_id),
                ("news_chain_error", error[:4000]),
                ("news_chain_checked_at", utc_now()),
            ),
        )
        store.commit()
        if isinstance(exc, Exception):
            raise NewsChainError(error, run_id) from exc
        raise


@dataclass
class PreviewTask:
    doc_id: int
    upload_date: str
    preview_index: int
    source_url: str


@dataclass
class PreviewResult:
    task: PreviewTask
    status: str
    content: bytes | None = None
    content_type: str | None = None
    error: str | None = None


def detect_image_format(content: bytes) -> str | None:
    """Return the decoded image format, rejecting header-only/truncated payloads."""

    if content.startswith((b"GIF87a", b"GIF89a")):
        magic_format = "gif"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        magic_format = "png"
    elif content.startswith(b"\xff\xd8\xff"):
        magic_format = "jpeg"
    elif len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        magic_format = "webp"
    else:
        return None

    try:
        with Image.open(io.BytesIO(content)) as image:
            decoded_format = (image.format or "").lower()
            image.verify()
        # verify() checks container structure but intentionally does not decode
        # pixels.  Reopen and load so a file with a valid header/table but a
        # truncated scan is rejected as well.
        with Image.open(io.BytesIO(content)) as image:
            if (image.format or "").lower() != decoded_format:
                return None
            for frame_index in range(getattr(image, "n_frames", 1)):
                image.seek(frame_index)
                image.load()
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError, EOFError, IndexError):
        return None
    return decoded_format if decoded_format == magic_format else None


def image_magic_matches(content: bytes) -> bool:
    return detect_image_format(content) is not None


def stored_image_record_matches(output: Path, row: Any, expected_url: str) -> bool:
    """Verify a resumable media row against its current URL and exact local bytes."""

    try:
        if row["status"] != "ok" or row["source_url"] != expected_url:
            return False
        local_path = row["local_path"]
        expected_size = int(row["byte_count"])
        expected_sha256 = str(row["sha256"])
    except (KeyError, TypeError, ValueError):
        return False
    if not local_path or expected_size < 0 or not expected_sha256:
        return False

    relative_path = Path(str(local_path))
    if relative_path.is_absolute():
        return False
    root = output.resolve()
    path = (output / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    try:
        content = path.read_bytes()
    except OSError:
        return False
    return bool(
        len(content) == expected_size
        and hashlib.sha256(content).hexdigest() == expected_sha256
        and image_magic_matches(content)
    )


def audit_news_media_records(
    store: "Store",
    news_scope: str,
    news_params: tuple[Any, ...],
    news_scope_n: str,
    news_params_n: tuple[Any, ...],
) -> dict[str, Any]:
    """Count only current-URL rows whose exact local bytes still validate."""

    expected: dict[tuple[int, int], str] = {}
    for row in store.conn.execute(
        f"SELECT article_id,images_json FROM news WHERE {news_scope}", news_params
    ):
        for index, url in enumerate(json_loads(row["images_json"], []), start=1):
            if PREVIEW_URL_RE.fullmatch(url):
                expected[(int(row["article_id"]), index)] = url

    attempted = 0
    saved = 0
    failed = 0
    integrity_errors: list[dict[str, Any]] = []
    for row in store.conn.execute(
        f"""SELECT m.article_id,m.media_index,m.source_url,m.local_path,m.content_type,
                   m.byte_count,m.sha256,m.status,m.error,m.fetched_at
            FROM news_media_files m JOIN news n ON n.article_id=m.article_id
            WHERE {news_scope_n}""",
        news_params_n,
    ):
        key = (int(row["article_id"]), int(row["media_index"]))
        expected_url = expected.get(key)
        if expected_url is None or row["source_url"] != expected_url:
            continue
        attempted += 1
        if row["status"] != "ok":
            failed += 1
        elif stored_image_record_matches(store.output, row, expected_url):
            saved += 1
        else:
            failed += 1
            integrity_errors.append(
                {
                    "source": "news_media_integrity",
                    "article_id": key[0],
                    "media_index": key[1],
                    "source_url": expected_url,
                    "local_path": row["local_path"],
                    "error": "status is ok but current URL/file size/SHA-256/decode validation failed",
                }
            )
    return {
        "expected": len(expected),
        "attempted": attempted,
        "saved": saved,
        "failed": failed,
        "integrity_errors": integrity_errors,
    }


def fetch_preview(fetcher: Fetcher, task: PreviewTask) -> PreviewResult:
    try:
        response = fetcher.get(task.source_url)
        content_type = clean_text(response.headers.get("Content-Type")).lower()
        if response.status_code != 200:
            return PreviewResult(task, "error", content_type=content_type, error=f"HTTP {response.status_code}")
        content = response.content
        if not content_type.startswith("image/") or not image_magic_matches(content):
            return PreviewResult(
                task,
                "invalid",
                content=content,
                content_type=content_type,
                error=f"not a usable image: {content_type}, {len(content)} bytes",
            )
        return PreviewResult(task, "ok", content=content, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        return PreviewResult(task, "error", error=str(exc))


def fetch_resource_media(fetcher: Fetcher, task: PreviewTask) -> PreviewResult:
    try:
        response = fetcher.get(task.source_url)
        content_type = clean_text(response.headers.get("Content-Type")).lower()
        if response.status_code != 200:
            return PreviewResult(task, "error", content_type=content_type, error=f"HTTP {response.status_code}")
        content = response.content
        valid = content_type.startswith("image/") and image_magic_matches(content)
        if not valid:
            return PreviewResult(
                task,
                "invalid",
                content=content,
                content_type=content_type,
                error=f"not usable public media: {content_type}, {len(content)} bytes",
            )
        return PreviewResult(task, "ok", content=content, content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        return PreviewResult(task, "error", error=str(exc))


def extension_for_preview(url: str, content_type: str, content: bytes | None = None) -> str:
    detected = detect_image_format(content) if content is not None else None
    if detected:
        return {"gif": ".gif", "png": ".png", "jpeg": ".jpg", "webp": ".webp"}[detected]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".gif", ".png", ".jpg", ".jpeg", ".webp"}:
        return suffix
    return {
        "image/gif": ".gif",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }.get(content_type.split(";")[0], ".img")


def run_previews(store: Store, fetcher: Fetcher, workers: int, max_items: int | None) -> None:
    query = "SELECT doc_id,upload_date,preview_image_urls_json FROM resources ORDER BY doc_id DESC"
    params: tuple[Any, ...] = ()
    if max_items is not None:
        query += " LIMIT ?"
        params = (max_items,)
    rows = store.conn.execute(query, params).fetchall()

    tasks: list[PreviewTask] = []
    for row in rows:
        urls = json_loads(row["preview_image_urls_json"], [])
        existing = {
            int(item[0])
            for item in store.conn.execute(
                "SELECT preview_index FROM preview_files WHERE doc_id=? AND status='ok'", (row["doc_id"],)
            )
        }
        for index, url in enumerate(urls, start=1):
            if index not in existing and PREVIEW_URL_RE.fullmatch(url):
                tasks.append(PreviewTask(int(row["doc_id"]), row["upload_date"], index, url))

    started = time.monotonic()
    done = 0
    ok = 0
    batch_size = max(32, workers * 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch in chunks(tasks, batch_size):
            futures = {pool.submit(fetch_preview, fetcher, task): task for task in batch}
            for future in as_completed(futures):
                result = future.result()
                task = result.task
                local_path: str | None = None
                byte_count: int | None = len(result.content) if result.content is not None else None
                digest: str | None = None
                if result.status == "ok" and result.content is not None and result.content_type:
                    year, month, _day = task.upload_date.split("-")
                    extension = extension_for_preview(task.source_url, result.content_type, result.content)
                    path = (
                        store.output
                        / "previews"
                        / year
                        / month
                        / f"p_{task.doc_id}"
                        / f"page_{task.preview_index:03d}{extension}"
                    )
                    atomic_write_bytes(path, result.content)
                    local_path = path.relative_to(store.output).as_posix()
                    digest = hashlib.sha256(result.content).hexdigest()
                    ok += 1
                    store.conn.execute(
                        """INSERT OR REPLACE INTO preview_files
                           (doc_id,preview_index,source_url,local_path,content_type,byte_count,sha256,status,error,fetched_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            task.doc_id,
                            task.preview_index,
                            task.source_url,
                            local_path,
                            result.content_type,
                            byte_count,
                            digest,
                            result.status,
                            result.error,
                            utc_now(),
                        ),
                    )
                done += 1
            store.commit()
            store.checkpoint("download_previews")
            elapsed = max(0.001, time.monotonic() - started)
            print(
                f"[previews] {done:,}/{len(tasks):,}; ok={ok:,}; {done / elapsed:.2f} images/s",
                flush=True,
            )


def run_news_media(
    store: Store,
    fetcher: Fetcher,
    workers: int,
    max_items: int | None,
    start_date: date,
    end_date: date,
    min_id: int,
    max_id: int,
) -> None:
    scope, params = record_scope("news", start_date, end_date, min_id, max_id)
    query = f"""SELECT article_id,published_date,images_json FROM news
                WHERE {scope} ORDER BY article_id DESC"""
    if max_items is not None:
        query += " LIMIT ?"
        params = (*params, max_items)
    rows = store.conn.execute(query, params).fetchall()

    tasks: list[PreviewTask] = []
    for row in rows:
        urls = json_loads(row["images_json"], [])
        existing = {
            int(item["media_index"]): item
            for item in store.conn.execute(
                """SELECT media_index,source_url,local_path,byte_count,sha256,status
                   FROM news_media_files WHERE article_id=?""",
                (row["article_id"],),
            )
        }
        for index, url in enumerate(urls, start=1):
            # Only archive public DCBBS /fileroot1/ URLs explicitly embedded in
            # the article. External images remain attributed URLs in news.jsonl.
            if (
                PREVIEW_URL_RE.fullmatch(url)
                and not stored_image_record_matches(store.output, existing.get(index), url)
            ):
                tasks.append(PreviewTask(int(row["article_id"]), row["published_date"], index, url))

    started = time.monotonic()
    done = 0
    ok = 0
    batch_size = max(32, workers * 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch in chunks(tasks, batch_size):
            futures = {pool.submit(fetch_preview, fetcher, task): task for task in batch}
            for future in as_completed(futures):
                result = future.result()
                task = result.task
                local_path: str | None = None
                byte_count: int | None = len(result.content) if result.content is not None else None
                digest: str | None = None
                if result.status == "ok" and result.content is not None and result.content_type:
                    year, month, _day = task.upload_date.split("-")
                    extension = extension_for_preview(task.source_url, result.content_type, result.content)
                    path = (
                        store.output
                        / "news_media"
                        / year
                        / month
                        / f"i_{task.doc_id}"
                        / f"image_{task.preview_index:03d}{extension}"
                    )
                    atomic_write_bytes(path, result.content)
                    local_path = path.relative_to(store.output).as_posix()
                    digest = hashlib.sha256(result.content).hexdigest()
                    ok += 1
                    store.conn.execute(
                        """INSERT OR REPLACE INTO news_media_files
                           (article_id,media_index,source_url,local_path,content_type,byte_count,sha256,status,error,fetched_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            task.doc_id,
                            task.preview_index,
                            task.source_url,
                            local_path,
                            result.content_type,
                            byte_count,
                            digest,
                            result.status,
                            result.error,
                            utc_now(),
                        ),
                    )
                done += 1
            store.commit()
            store.checkpoint("download_news_media")
            elapsed = max(0.001, time.monotonic() - started)
            print(
                f"[news-media] {done:,}/{len(tasks):,}; ok={ok:,}; {done / elapsed:.2f} images/s",
                flush=True,
            )


def run_resource_media_backfill(store: Store, max_items: int | None) -> None:
    query = """SELECT r.doc_id,r.raw_path,r.thumbnail_url,r.preview_image_urls_json
               FROM resources r
               LEFT JOIN resource_media_scan s ON s.doc_id=r.doc_id
               WHERE s.doc_id IS NULL OR s.status!='ok'
               ORDER BY r.doc_id DESC"""
    params: tuple[Any, ...] = ()
    if max_items is not None:
        query += " LIMIT ?"
        params = (max_items,)
    rows = store.conn.execute(query, params).fetchall()
    done = 0
    errors = 0
    for row in rows:
        try:
            if not row["raw_path"]:
                raise ValueError("raw HTML path is missing")
            raw_path = store.output / row["raw_path"]
            with gzip.open(raw_path, "rb") as stream:
                html = stream.read()
            soup = BeautifulSoup(html.decode("utf-8", errors="replace"), "html.parser")
            preview_urls = json_loads(row["preview_image_urls_json"], [])
            media_urls, video_urls = extract_resource_public_media(
                soup, preview_urls, clean_text(row["thumbnail_url"])
            )
            store.conn.execute(
                """UPDATE resources
                   SET public_media_urls_json=?,public_video_urls_json=?
                   WHERE doc_id=?""",
                (json_dumps(media_urls), json_dumps(video_urls), row["doc_id"]),
            )
            store.conn.execute(
                """INSERT OR REPLACE INTO resource_media_scan(doc_id,status,error,scanned_at)
                   VALUES (?,'ok',NULL,?)""",
                (row["doc_id"], utc_now()),
            )
        except Exception as exc:  # noqa: BLE001 - recorded for retry and manifest
            errors += 1
            store.conn.execute(
                """INSERT OR REPLACE INTO resource_media_scan(doc_id,status,error,scanned_at)
                   VALUES (?,'error',?,?)""",
                (row["doc_id"], str(exc), utc_now()),
            )
        done += 1
        if done % 100 == 0:
            store.commit()
            print(f"[resource-media-scan] {done:,}/{len(rows):,}; errors={errors:,}", flush=True)
    store.commit()
    store.checkpoint("scan_resource_media")
    print(f"[resource-media-scan] {done:,}/{len(rows):,}; errors={errors:,}", flush=True)


def run_resource_media(store: Store, fetcher: Fetcher, workers: int, max_items: int | None) -> None:
    query = "SELECT doc_id,upload_date,public_media_urls_json FROM resources ORDER BY doc_id DESC"
    params: tuple[Any, ...] = ()
    if max_items is not None:
        query += " LIMIT ?"
        params = (max_items,)
    rows = store.conn.execute(query, params).fetchall()

    tasks: list[PreviewTask] = []
    for row in rows:
        urls = json_loads(row["public_media_urls_json"], [])
        existing = {
            int(item[0])
            for item in store.conn.execute(
                "SELECT media_index FROM resource_media_files WHERE doc_id=? AND status='ok'",
                (row["doc_id"],),
            )
        }
        for index, url in enumerate(urls, start=1):
            if index not in existing and RESOURCE_MEDIA_URL_RE.fullmatch(url):
                tasks.append(PreviewTask(int(row["doc_id"]), row["upload_date"], index, url))

    started = time.monotonic()
    done = 0
    ok = 0
    batch_size = max(8, workers * 2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch in chunks(tasks, batch_size):
            futures = {pool.submit(fetch_resource_media, fetcher, task): task for task in batch}
            for future in as_completed(futures):
                result = future.result()
                task = result.task
                local_path: str | None = None
                byte_count: int | None = len(result.content) if result.content is not None else None
                digest: str | None = None
                if result.status == "ok" and result.content is not None and result.content_type:
                    year, month, _day = task.upload_date.split("-")
                    extension = extension_for_preview(task.source_url, result.content_type, result.content)
                    path = (
                        store.output
                        / "resource_media"
                        / year
                        / month
                        / f"p_{task.doc_id}"
                        / f"media_{task.preview_index:03d}{extension}"
                    )
                    atomic_write_bytes(path, result.content)
                    local_path = path.relative_to(store.output).as_posix()
                    digest = hashlib.sha256(result.content).hexdigest()
                    ok += 1
                    store.conn.execute(
                        """INSERT OR REPLACE INTO resource_media_files
                           (doc_id,media_index,source_url,local_path,content_type,byte_count,sha256,status,error,fetched_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            task.doc_id,
                            task.preview_index,
                            task.source_url,
                            local_path,
                            result.content_type,
                            byte_count,
                            digest,
                            result.status,
                            result.error,
                            utc_now(),
                        ),
                    )
                done += 1
            store.commit()
            store.checkpoint("download_resource_media")
            elapsed = max(0.001, time.monotonic() - started)
            print(
                f"[resource-media] {done:,}/{len(tasks):,}; ok={ok:,}; {done / elapsed:.2f} files/s",
                flush=True,
            )


JSON_FIELDS = {
    "resources": {
        "categories_json",
        "keywords_json",
        "preview_image_urls_json",
        "public_media_urls_json",
        "public_video_urls_json",
    },
    "news": {"keywords_json", "images_json", "links_json"},
}


def row_to_export(row: sqlite3.Row, table: str) -> dict[str, Any]:
    result = dict(row)
    for field in JSON_FIELDS.get(table, set()):
        result[field.removesuffix("_json")] = json_loads(result.pop(field, None), [])
    if table == "resources":
        result["preview_public"] = bool(result["preview_public"])
    return result


def record_scope(
    table: str,
    start_date: date,
    end_date: date,
    min_id: int,
    max_id: int,
    alias: str = "",
) -> tuple[str, tuple[Any, ...]]:
    prefix = f"{alias}." if alias else ""
    if table == "resources":
        id_column, date_column = "doc_id", "upload_date"
    elif table == "news":
        id_column, date_column = "article_id", "published_date"
    else:
        raise ValueError(f"unsupported scoped table: {table}")
    return (
        f"{prefix}{id_column} BETWEEN ? AND ? AND {prefix}{date_column} BETWEEN ? AND ?",
        (min_id, max_id, start_date.isoformat(), end_date.isoformat()),
    )


def export_jsonl(
    store: Store,
    table: str,
    order_column: str,
    scope_clause: str,
    scope_params: tuple[Any, ...],
) -> Path:
    path = store.output / "exports" / f"{table}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in store.conn.execute(
            f"SELECT * FROM {table} WHERE {scope_clause} ORDER BY {order_column} DESC",
            scope_params,
        ):
            stream.write(json_dumps(row_to_export(row, table)) + "\n")
    tmp.replace(path)
    return path


def export_csv_index(
    store: Store,
    table: str,
    scope_clause: str,
    scope_params: tuple[Any, ...],
) -> Path:
    path = store.output / "exports" / f"{table}_index.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    if table == "resources":
        columns = [
            "doc_id",
            "upload_date",
            "title",
            "document_format",
            "page_count",
            "file_size",
            "cost",
            "preview_public",
            "source_url",
            "raw_path",
        ]
        order = "doc_id"
    else:
        columns = [
            "article_id",
            "published_date",
            "title",
            "category",
            "author",
            "views",
            "source_url",
            "raw_path",
        ]
        order = "article_id"
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in store.conn.execute(
            f"SELECT {','.join(columns)} FROM {table} WHERE {scope_clause} ORDER BY {order} DESC",
            scope_params,
        ):
            writer.writerow(dict(row))
    tmp.replace(path)
    return path


def run_export(
    store: Store,
    start_date: date,
    end_date: date,
    resource_min: int,
    resource_max: int,
    news_min: int,
    news_max: int,
) -> dict[str, Any]:
    metadata_values = {
        str(row["key"]): str(row["value"])
        for row in store.conn.execute(
            """SELECT key,value FROM metadata WHERE key IN
               ('news_chain_status','news_chain_run_id','news_chain_top_id',
                'news_chain_bottom_id','news_chain_error')"""
        )
    }
    news_chain_status = metadata_values.get("news_chain_status", "not_run")
    news_chain_run_id = metadata_values.get("news_chain_run_id", "")
    news_chain_bounds_match = (
        metadata_values.get("news_chain_top_id") == str(news_max)
        and metadata_values.get("news_chain_bottom_id") == str(news_min)
    )
    resource_scope, resource_params = record_scope(
        "resources", start_date, end_date, resource_min, resource_max
    )
    news_scope, news_params = record_scope("news", start_date, end_date, news_min, news_max)
    resources_jsonl = export_jsonl(
        store, "resources", "doc_id", resource_scope, resource_params
    )
    news_jsonl = export_jsonl(store, "news", "article_id", news_scope, news_params)
    resources_csv = export_csv_index(store, "resources", resource_scope, resource_params)
    news_csv = export_csv_index(store, "news", news_scope, news_params)
    resource_scope_r, resource_params_r = record_scope(
        "resources", start_date, end_date, resource_min, resource_max, "r"
    )
    news_scope_n, news_params_n = record_scope(
        "news", start_date, end_date, news_min, news_max, "n"
    )
    news_media_audit = audit_news_media_records(
        store, news_scope, news_params, news_scope_n, news_params_n
    )

    matches_path = store.output / "exports" / "open_source_matches.jsonl"
    matches_tmp = matches_path.with_name(matches_path.name + f".{os.getpid()}.tmp")
    with matches_tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in store.conn.execute(
            f"""SELECT m.* FROM open_source_matches m
                JOIN resources r ON r.doc_id=m.doc_id
                WHERE {resource_scope_r} ORDER BY m.doc_id DESC""",
            resource_params_r,
        ):
            stream.write(json_dumps(dict(row)) + "\n")
    matches_tmp.replace(matches_path)

    candidates_path = store.output / "exports" / "open_source_candidates.csv"
    candidates_tmp = candidates_path.with_name(candidates_path.name + f".{os.getpid()}.tmp")
    with candidates_tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        columns = ["doc_id", "upload_date", "title", "normalized_title", "page_count", "source_url", "reason"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        rows = store.conn.execute(
            f"""SELECT r.doc_id,r.upload_date,r.title,r.normalized_title,r.page_count,r.source_url,
                      CASE
                        WHEN r.preview_public=0 THEN 'no_public_preview'
                        WHEN r.page_count IS NULL THEN 'unknown_total_pages'
                        ELSE 'partial_preview_only'
                      END AS reason
               FROM resources r
               WHERE ({resource_scope_r})
                 AND (r.preview_public=0
                   OR r.page_count IS NULL
                   OR json_array_length(r.preview_image_urls_json) < r.page_count)
               ORDER BY r.doc_id DESC""",
            resource_params_r,
        )
        for row in rows:
            writer.writerow(dict(row))
    candidates_tmp.replace(candidates_path)

    errors_path = store.output / "exports" / "errors.jsonl"
    errors_tmp = errors_path.with_name(errors_path.name + f".{os.getpid()}.tmp")
    with errors_tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in store.conn.execute(
            """SELECT kind,item_id,status,error,fetched_at FROM crawl_items
               WHERE status IN ('error','invalid','gap_hint')
                 AND ((kind='resource' AND item_id BETWEEN ? AND ?)
                   OR (kind='news' AND item_id BETWEEN ? AND ?))
               ORDER BY kind,item_id DESC""",
            (resource_min, resource_max, news_min, news_max),
        ):
            stream.write(json_dumps({"source": "crawl", **dict(row)}) + "\n")
        if news_chain_run_id:
            for row in store.conn.execute(
                """SELECT run_id,min_id,max_id,current_id,previous_id,reverse_next_id,
                          status,error,checked_at
                   FROM news_chain_edges
                   WHERE run_id=? AND min_id=? AND max_id=? AND status='invalid'
                   ORDER BY current_id DESC""",
                (news_chain_run_id, news_min, news_max),
            ):
                stream.write(json_dumps({"source": "news_chain", **dict(row)}) + "\n")
        if (
            news_chain_bounds_match
            and news_chain_status in {"failed", "interrupted", "fallback_failed"}
        ):
            stream.write(
                json_dumps(
                    {
                        "source": "news_chain_run",
                        "run_id": news_chain_run_id,
                        "status": news_chain_status,
                        "error": metadata_values.get("news_chain_error", ""),
                        "min_id": news_min,
                        "max_id": news_max,
                    }
                )
                + "\n"
            )
        for row in store.conn.execute(
            f"""SELECT s.doc_id,s.status,s.error,s.scanned_at
               FROM resource_media_scan s JOIN resources r ON r.doc_id=s.doc_id
               WHERE s.status!='ok' AND ({resource_scope_r})
               ORDER BY s.doc_id DESC""",
            resource_params_r,
        ):
            stream.write(json_dumps({"source": "resource_media_scan", **dict(row)}) + "\n")
        for row in store.conn.execute(
            f"""SELECT p.doc_id,p.preview_index,p.status,p.error,p.source_url,p.fetched_at
               FROM preview_files p JOIN resources r ON r.doc_id=p.doc_id
               WHERE p.status!='ok' AND ({resource_scope_r})
               ORDER BY p.doc_id DESC,p.preview_index""",
            resource_params_r,
        ):
            stream.write(json_dumps({"source": "preview", **dict(row)}) + "\n")
        for row in store.conn.execute(
            f"""SELECT m.doc_id,m.media_index,m.status,m.error,m.source_url,m.fetched_at
               FROM resource_media_files m JOIN resources r ON r.doc_id=m.doc_id
               WHERE m.status!='ok' AND ({resource_scope_r})
               ORDER BY m.doc_id DESC,m.media_index""",
            resource_params_r,
        ):
            stream.write(json_dumps({"source": "resource_media", **dict(row)}) + "\n")
        for row in store.conn.execute(
            f"""SELECT m.article_id,m.media_index,m.status,m.error,m.source_url,m.fetched_at
               FROM news_media_files m JOIN news n ON n.article_id=m.article_id
               WHERE m.status!='ok' AND ({news_scope_n})
               ORDER BY m.article_id DESC,m.media_index""",
            news_params_n,
        ):
            stream.write(json_dumps({"source": "news_media", **dict(row)}) + "\n")
        for row in news_media_audit["integrity_errors"]:
            stream.write(json_dumps(row) + "\n")
    errors_tmp.replace(errors_path)

    gaps_path = store.output / "exports" / "id_gaps.csv"
    gaps_tmp = gaps_path.with_name(gaps_path.name + f".{os.getpid()}.tmp")
    with gaps_tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        columns = ["kind", "item_id", "status", "error", "fetched_at"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in store.conn.execute(
            """SELECT kind,item_id,status,error,fetched_at FROM crawl_items
               WHERE status IN ('missing','known_missing','link_gap')
                 AND ((kind='resource' AND item_id BETWEEN ? AND ?)
                   OR (kind='news' AND item_id BETWEEN ? AND ?))
               ORDER BY kind,item_id DESC""",
            (resource_min, resource_max, news_min, news_max),
        ):
            writer.writerow(dict(row))
    gaps_tmp.replace(gaps_path)

    resource_count = store.conn.execute(
        f"SELECT COUNT(*) FROM resources WHERE {resource_scope}", resource_params
    ).fetchone()[0]
    news_count = store.conn.execute(
        f"SELECT COUNT(*) FROM news WHERE {news_scope}", news_params
    ).fetchone()[0]
    preview_count = store.conn.execute(
        f"""SELECT COUNT(*) FROM preview_files p JOIN resources r ON r.doc_id=p.doc_id
            WHERE p.status='ok' AND ({resource_scope_r})""",
        resource_params_r,
    ).fetchone()[0]
    error_count = store.conn.execute(
        """SELECT COUNT(*) FROM crawl_items
           WHERE status IN ('error','invalid')
             AND ((kind='resource' AND item_id BETWEEN ? AND ?)
               OR (kind='news' AND item_id BETWEEN ? AND ?))""",
        (resource_min, resource_max, news_min, news_max),
    ).fetchone()[0]
    preview_expected = store.conn.execute(
        f"""SELECT COALESCE(SUM(json_array_length(preview_image_urls_json)),0)
            FROM resources WHERE {resource_scope}""",
        resource_params,
    ).fetchone()[0]
    preview_attempted = store.conn.execute(
        f"""SELECT COUNT(*) FROM preview_files p JOIN resources r ON r.doc_id=p.doc_id
            WHERE {resource_scope_r}""",
        resource_params_r,
    ).fetchone()[0]
    preview_failed = store.conn.execute(
        f"""SELECT COUNT(*) FROM preview_files p JOIN resources r ON r.doc_id=p.doc_id
            WHERE p.status!='ok' AND ({resource_scope_r})""",
        resource_params_r,
    ).fetchone()[0]
    resource_media_expected = store.conn.execute(
        f"""SELECT COALESCE(SUM(json_array_length(public_media_urls_json)),0)
            FROM resources WHERE {resource_scope}""",
        resource_params,
    ).fetchone()[0]
    resource_video_expected = store.conn.execute(
        f"""SELECT COALESCE(SUM(json_array_length(public_video_urls_json)),0)
            FROM resources WHERE {resource_scope}""",
        resource_params,
    ).fetchone()[0]
    resource_media_count = store.conn.execute(
        f"""SELECT COUNT(*) FROM resource_media_files m JOIN resources r ON r.doc_id=m.doc_id
            WHERE m.status='ok' AND ({resource_scope_r})""",
        resource_params_r,
    ).fetchone()[0]
    resource_media_attempted = store.conn.execute(
        f"""SELECT COUNT(*) FROM resource_media_files m JOIN resources r ON r.doc_id=m.doc_id
            WHERE {resource_scope_r}""",
        resource_params_r,
    ).fetchone()[0]
    resource_media_failed = store.conn.execute(
        f"""SELECT COUNT(*) FROM resource_media_files m JOIN resources r ON r.doc_id=m.doc_id
            WHERE m.status!='ok' AND ({resource_scope_r})""",
        resource_params_r,
    ).fetchone()[0]
    resource_scan_ok = store.conn.execute(
        f"""SELECT COUNT(*) FROM resource_media_scan s JOIN resources r ON r.doc_id=s.doc_id
            WHERE s.status='ok' AND ({resource_scope_r})""",
        resource_params_r,
    ).fetchone()[0]
    resource_scan_errors = store.conn.execute(
        f"""SELECT COUNT(*) FROM resource_media_scan s JOIN resources r ON r.doc_id=s.doc_id
            WHERE s.status!='ok' AND ({resource_scope_r})""",
        resource_params_r,
    ).fetchone()[0]
    resource_scan_pending = max(0, resource_count - resource_scan_ok - resource_scan_errors)
    news_media_expected = int(news_media_audit["expected"])
    news_media_count = int(news_media_audit["saved"])
    news_media_attempted = int(news_media_audit["attempted"])
    news_media_failed = int(news_media_audit["failed"])
    news_media_integrity_failed = len(news_media_audit["integrity_errors"])
    missing_counts = {
        "resource": store.conn.execute(
            """SELECT COUNT(*) FROM crawl_items WHERE kind='resource'
               AND item_id BETWEEN ? AND ? AND status IN ('missing','known_missing','link_gap')""",
            (resource_min, resource_max),
        ).fetchone()[0],
        "news": store.conn.execute(
            """SELECT COUNT(*) FROM crawl_items WHERE kind='news'
               AND item_id BETWEEN ? AND ? AND status IN ('missing','known_missing','link_gap')""",
            (news_min, news_max),
        ).fetchone()[0],
    }
    direct_missing_counts = {
        kind: store.conn.execute(
            """SELECT COUNT(*) FROM crawl_items WHERE kind=?
               AND item_id BETWEEN ? AND ? AND status IN ('missing','known_missing')""",
            (
                kind,
                resource_min if kind == "resource" else news_min,
                resource_max if kind == "resource" else news_max,
            ),
        ).fetchone()[0]
        for kind in ("resource", "news")
    }
    navigation_gap_counts = {
        kind: store.conn.execute(
            """SELECT COUNT(*) FROM crawl_items WHERE kind=?
               AND item_id BETWEEN ? AND ? AND status='link_gap'""",
            (
                kind,
                resource_min if kind == "resource" else news_min,
                resource_max if kind == "resource" else news_max,
            ),
        ).fetchone()[0]
        for kind in ("resource", "news")
    }
    news_chain_edge_counts = {
        str(row["status"]): int(row["count"])
        for row in store.conn.execute(
            """SELECT status,COUNT(*) AS count FROM news_chain_edges
               WHERE run_id=? AND min_id=? AND max_id=? GROUP BY status""",
            (news_chain_run_id, news_min, news_max),
        )
    }
    news_chain_edge_errors = news_chain_edge_counts.get("invalid", 0)
    date_ranges = {
        "resources": dict(
            store.conn.execute(
                f"""SELECT MIN(upload_date) AS min_date, MAX(upload_date) AS max_date
                    FROM resources WHERE {resource_scope}""",
                resource_params,
            ).fetchone()
        ),
        "news": dict(
            store.conn.execute(
                f"""SELECT MIN(published_date) AS min_date, MAX(published_date) AS max_date
                    FROM news WHERE {news_scope}""",
                news_params,
            ).fetchone()
        ),
    }
    resource_total = resource_max - resource_min + 1
    news_total = news_max - news_min + 1
    resource_terminal = store.conn.execute(
        """SELECT COUNT(*) FROM crawl_items
           WHERE kind='resource' AND item_id BETWEEN ? AND ?
             AND status IN ('ok','missing','known_missing','link_gap','out_of_range')""",
        (resource_min, resource_max),
    ).fetchone()[0]
    news_terminal = store.conn.execute(
        """SELECT COUNT(*) FROM crawl_items
           WHERE kind='news' AND item_id BETWEEN ? AND ?
             AND status IN ('ok','missing','known_missing','link_gap','out_of_range')""",
        (news_min, news_max),
    ).fetchone()[0]
    resource_errors = store.conn.execute(
        """SELECT COUNT(*) FROM crawl_items WHERE kind='resource'
           AND item_id BETWEEN ? AND ? AND status IN ('error','invalid')""",
        (resource_min, resource_max),
    ).fetchone()[0]
    news_errors = store.conn.execute(
        """SELECT COUNT(*) FROM crawl_items WHERE kind='news'
           AND item_id BETWEEN ? AND ? AND status IN ('error','invalid')""",
        (news_min, news_max),
    ).fetchone()[0]
    completeness = {
        "resources": {
            "candidate_ids": resource_total,
            "terminal_ids": resource_terminal,
            "request_errors": resource_errors,
            "unprocessed_ids": max(0, resource_total - resource_terminal - resource_errors),
        },
        "news": {
            "candidate_ids": news_total,
            "terminal_ids": news_terminal,
            "request_errors": news_errors,
            "unprocessed_ids": max(0, news_total - news_terminal - news_errors),
        },
        "previews": {
            "expected_public_urls": preview_expected,
            "attempted": preview_attempted,
            "saved": preview_count,
            "failed": preview_failed,
            "unprocessed": max(0, preview_expected - preview_attempted),
        },
        "resource_media": {
            "expected_public_urls": resource_media_expected,
            "attempted": resource_media_attempted,
            "saved": resource_media_count,
            "failed": resource_media_failed,
            "unprocessed": max(0, resource_media_expected - resource_media_attempted),
        },
        "resource_media_scan": {
            "resources": resource_count,
            "scanned_ok": resource_scan_ok,
            "errors": resource_scan_errors,
            "pending": resource_scan_pending,
        },
        "news_media": {
            "expected_public_urls": news_media_expected,
            "attempted": news_media_attempted,
            "saved": news_media_count,
            "failed": news_media_failed,
            "integrity_failed": news_media_integrity_failed,
            "unprocessed": max(0, news_media_expected - news_media_attempted),
        },
    }
    news_chain_required = navigation_gap_counts["news"] > 0
    news_chain_resolution_ok = bool(
        not news_chain_required
        or (
            bool(news_chain_run_id)
            and news_chain_bounds_match
            and news_chain_status in {"complete", "fallback_complete"}
            and news_chain_edge_errors == 0
            and news_chain_edge_counts.get("bidirectional_ok", 0) > 0
        )
    )
    screenshot_files = [
        str(path.relative_to(store.output).as_posix())
        for path in sorted((store.output / "screenshots").glob("*.png"))
    ] if (store.output / "screenshots").exists() else []
    archive_complete = bool(
        resource_terminal == resource_total
        and news_terminal == news_total
        and error_count == 0
        and preview_attempted == preview_expected
        and preview_failed == 0
        and preview_count == preview_expected
        and resource_media_attempted == resource_media_expected
        and resource_media_failed == 0
        and resource_media_count == resource_media_expected
        and resource_scan_ok == resource_count
        and resource_scan_errors == 0
        and resource_scan_pending == 0
        and news_media_attempted == news_media_expected
        and news_media_failed == 0
        and news_media_count == news_media_expected
        and news_chain_resolution_ok
    )
    manifest = {
        "source": "https://www.dcbbs.com/",
        "generated_at": utc_now(),
        "archive_status": "complete" if archive_complete else "partial",
        "requested_window": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "id_boundaries": {
            "resources": {"min": resource_min, "max": resource_max},
            "news": {"min": news_min, "max": news_max},
        },
        "completeness": completeness,
        "news_navigation_chain": {
            "status": news_chain_status,
            "run_id": news_chain_run_id or None,
            "bounds_match_requested_window": news_chain_bounds_match,
            "edge_validation": news_chain_edge_counts,
            "method": "global previous/next links with reverse-link validation",
            "error": metadata_values.get("news_chain_error") or None,
        },
        "counts": {
            "resources": resource_count,
            "news": news_count,
            "public_preview_images": preview_count,
            "public_resource_media": resource_media_count,
            "public_video_preview_urls_metadata_only": resource_video_expected,
            "public_news_images": news_media_count,
            "request_errors": error_count,
            "news_chain_errors": news_chain_edge_errors,
            "preview_errors": preview_failed,
            "resource_media_errors": resource_media_failed,
            "resource_media_scan_errors": resource_scan_errors,
            "news_media_errors": news_media_failed,
            "news_media_integrity_errors": news_media_integrity_failed,
            "total_errors": (
                error_count
                + news_chain_edge_errors
                + preview_failed
                + resource_media_failed
                + resource_scan_errors
                + news_media_failed
            ),
            "confirmed_missing_ids": missing_counts,
            "direct_404_ids": direct_missing_counts,
            "navigation_skipped_ids": navigation_gap_counts,
        },
        "date_ranges": date_ranges,
        "artifacts": {
            "database": str(store.db_path.relative_to(store.output)),
            "resources_jsonl": str(resources_jsonl.relative_to(store.output)),
            "news_jsonl": str(news_jsonl.relative_to(store.output)),
            "resources_csv": str(resources_csv.relative_to(store.output)),
            "news_csv": str(news_csv.relative_to(store.output)),
            "resource_media_directory": "resource_media",
            "news_media_directory": "news_media",
            "open_source_matches": str(matches_path.relative_to(store.output)),
            "open_source_candidates": str(candidates_path.relative_to(store.output)),
            "errors": str(errors_path.relative_to(store.output)),
            "id_gaps": str(gaps_path.relative_to(store.output)),
            "screenshots": screenshot_files,
        },
        "scope": {
            "included": [
                "public resource metadata",
                "public preview text",
                "only preview image URLs explicitly present in public HTML",
                "public resource thumbnail/poster images explicitly present in public HTML",
                "public video-preview URLs retained as metadata only; video files are not downloaded",
                "public news article text and embedded URLs",
                "public DCBBS news images explicitly embedded in article HTML",
                "verified public-source matches",
            ],
            "excluded": [
                "paid original downloads",
                "guessed preview pages beyond the public list",
                "login/VIP/UserManage/FlexPaper/View/search endpoints",
            ],
        },
        "rights_notice": RIGHTS_NOTICE,
        "usage_scope": USAGE_SCOPE,
        "data_quality_notes": [
            (
                "DCBBS frequently serves JPEG bytes under .gif URLs and image/gif headers; "
                "files are preserved as served and validated by image magic, so consumers "
                "must not choose a decoder from the filename extension alone."
            )
        ],
    }
    manifest_dir = store.output / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = manifest_dir / f"manifest_{stamp}.json"
    atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    atomic_write_text(store.output / "manifest_latest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    markdown = f"""# DCBBS public archive manifest

- Status: **{'complete' if archive_complete else 'partial'}**
- Window: `{start_date.isoformat()}` through `{end_date.isoformat()}`
- Resources: **{resource_count:,}**
- News articles: **{news_count:,}**
- News navigation chain: **{news_chain_status}** ({news_chain_edge_counts.get('bidirectional_ok', 0):,} validated edges)
- News IDs skipped by validated navigation: **{navigation_gap_counts['news']:,}**
- Public preview images saved: **{preview_count:,}**
- Public resource thumbnail/poster images saved: **{resource_media_count:,}**
- Public video-preview URLs (metadata only): **{resource_video_expected:,}**
- Public news images saved: **{news_media_count:,}**
- Remaining request errors: **{error_count:,}**
- Preview errors: **{preview_failed:,}**
- Resource-media errors: **{resource_media_failed:,}**
- Resource-media scan errors: **{resource_scan_errors:,}**
- News-media errors: **{news_media_failed:,}**
- Usage: `{USAGE_SCOPE}`
- Format note: many source `.gif` URLs contain valid JPEG bytes; decode by file signature.

Only publicly rendered metadata, preview text/images and news content are archived.
Paid originals and restricted endpoints are excluded.  Preview images are copied only
when their exact URL appears in the public page source; hidden page filenames are never
guessed.  See `manifest_latest.json` for machine-readable details.
"""
    atomic_write_text(store.output / "_manifest.md", markdown)
    store.checkpoint("export_complete" if archive_complete else "export_partial")
    return manifest


def run_probe(fetcher: Fetcher) -> None:
    checks: list[dict[str, Any]] = []
    for doc_id in (229746, 211506, 214234):
        result = fetch_resource(fetcher, doc_id)
        checks.append(
            {
                "kind": "resource",
                "id": doc_id,
                "status": result.status,
                "title": result.record.get("title") if result.record else None,
                "date": result.record.get("upload_date") if result.record else None,
                "preview_images": len(json_loads(result.record.get("preview_image_urls_json"), []))
                if result.record
                else 0,
                "error": result.error,
            }
        )
    for article_id in (165599, 128739, 46177, 46176):
        result = fetch_news(fetcher, article_id)
        checks.append(
            {
                "kind": "news",
                "id": article_id,
                "status": result.status,
                "title": result.record.get("title") if result.record else None,
                "date": result.record.get("published_date") if result.record else None,
                "body_chars": len(result.record.get("body_text", "")) if result.record else 0,
                "previous_id": result.record.get("previous_id") if result.record else None,
                "error": result.error,
            }
        )
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    expected = {
        ("resource", 229746): ("ok", "2026-07-04"),
        ("resource", 211506): ("ok", "2024-07-14"),
        ("resource", 214234): ("missing", None),
        ("news", 165599): ("ok", "2026-07-12"),
        ("news", 128739): ("ok", "2025-11-09"),
        ("news", 46177): ("ok", "2024-07-13"),
        ("news", 46176): ("ok", "2024-07-12"),
    }
    for check in checks:
        status, item_date = expected[(check["kind"], check["id"])]
        if check["status"] != status or check["date"] != item_date:
            raise SystemExit(f"probe failed: {check}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "probe",
            "resources",
            "news",
            "news-chain",
            "previews",
            "resource-media-scan",
            "resource-media",
            "news-media",
            "export",
            "all",
        ),
        help="pipeline phase to run",
    )
    parser.add_argument("--output", type=Path, default=Path("Vault/DCBBS"))
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--resource-min", type=int, default=DEFAULT_RESOURCE_MIN)
    parser.add_argument("--resource-max", type=int, default=DEFAULT_RESOURCE_MAX)
    parser.add_argument("--news-min", type=int, default=DEFAULT_NEWS_MIN)
    parser.add_argument("--news-max", type=int, default=DEFAULT_NEWS_MAX)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="global requests/second across all workers (default: 1.0)",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--no-raw", action="store_true", help="do not save gzip HTML responses")
    parser.add_argument(
        "--max-items",
        type=int,
        help=(
            "limit candidate IDs/docs in a phase for a smoke run; "
            "for news-chain, limit accessible chain nodes"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.start > args.end:
        raise SystemExit("--start must be on or before --end")
    if args.workers < 1 or args.rate <= 0:
        raise SystemExit("--workers and --rate must be positive")

    guard = RobotsGuard(timeout=args.timeout)
    fetcher = Fetcher(guard, args.rate, args.timeout, args.retries)
    if args.command == "probe":
        run_probe(fetcher)
        return 0

    store = Store(args.output.resolve())
    try:
        store.conn.executemany(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
            (
                ("window_start", args.start.isoformat()),
                ("window_end", args.end.isoformat()),
                ("resource_min", str(args.resource_min)),
                ("resource_max", str(args.resource_max)),
                ("news_min", str(args.news_min)),
                ("news_max", str(args.news_max)),
            ),
        )
        store.commit()
        if args.command in {"resources", "all"}:
            run_range(
                "resource",
                store,
                fetcher,
                args.resource_min,
                args.resource_max,
                args.start,
                args.end,
                args.workers,
                not args.no_raw,
                args.max_items,
            )
        if args.command in {"resource-media-scan", "all"}:
            run_resource_media_backfill(store, args.max_items)
        if args.command in {"previews", "all"}:
            run_previews(store, fetcher, args.workers, args.max_items)
        if args.command in {"resource-media", "all"}:
            run_resource_media(store, fetcher, args.workers, args.max_items)
        if args.command == "news":
            run_range(
                "news",
                store,
                fetcher,
                args.news_min,
                args.news_max,
                args.start,
                args.end,
                args.workers,
                not args.no_raw,
                args.max_items,
            )
        if args.command == "news-chain":
            run_news_chain(
                store,
                fetcher,
                args.news_min,
                args.news_max,
                args.start,
                args.end,
                not args.no_raw,
                args.max_items,
            )
        if args.command == "all":
            try:
                run_news_chain(
                    store,
                    fetcher,
                    args.news_min,
                    args.news_max,
                    args.start,
                    args.end,
                    not args.no_raw,
                    args.max_items,
                )
            except NewsChainError as exc:
                print(
                    f"[news-chain] {exc}; falling back to direct ID verification",
                    flush=True,
                )
                # Revisit even previously validated link gaps.  This makes the
                # recovery path independent of navigation evidence and leaves
                # a pure per-ID terminal result for every configured number.
                run_range(
                    "news",
                    store,
                    fetcher,
                    args.news_min,
                    args.news_max,
                    args.start,
                    args.end,
                    args.workers,
                    not args.no_raw,
                    args.max_items,
                    verify_link_gaps=True,
                )
                total = args.news_max - args.news_min + 1
                terminal = store.conn.execute(
                    """SELECT COUNT(*) FROM crawl_items
                       WHERE kind='news' AND item_id BETWEEN ? AND ?
                         AND status IN ('ok','missing','known_missing','out_of_range')""",
                    (args.news_min, args.news_max),
                ).fetchone()[0]
                remaining_errors = store.conn.execute(
                    """SELECT COUNT(*) FROM crawl_items
                       WHERE kind='news' AND item_id BETWEEN ? AND ?
                         AND status IN ('error','invalid','gap_hint','link_gap')""",
                    (args.news_min, args.news_max),
                ).fetchone()[0]
                fallback_status = (
                    "fallback_complete"
                    if terminal == total and remaining_errors == 0
                    else "fallback_failed"
                )
                if fallback_status == "fallback_complete":
                    store.conn.execute(
                        """UPDATE news_chain_edges
                           SET status='fallback_resolved', checked_at=?
                           WHERE run_id=? AND status='invalid'""",
                        (utc_now(), exc.run_id),
                    )
                store.conn.executemany(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                    (
                        ("news_chain_status", fallback_status),
                        ("news_chain_run_id", exc.run_id),
                        ("news_chain_checked_at", utc_now()),
                    ),
                )
                store.commit()
            chain_status_row = store.conn.execute(
                "SELECT value FROM metadata WHERE key='news_chain_status'"
            ).fetchone()
            if chain_status_row is not None and chain_status_row[0] == "complete":
                residual = store.conn.execute(
                    """SELECT COUNT(*) FROM crawl_items
                       WHERE kind='news' AND item_id BETWEEN ? AND ?
                         AND status IN ('error','invalid','gap_hint')""",
                    (args.news_min, args.news_max),
                ).fetchone()[0]
                if residual:
                    print(
                        f"[news] retrying {residual:,} residual direct-ID states",
                        flush=True,
                    )
                    run_range(
                        "news",
                        store,
                        fetcher,
                        args.news_min,
                        args.news_max,
                        args.start,
                        args.end,
                        args.workers,
                        not args.no_raw,
                        args.max_items,
                        report_empty_batches=False,
                    )
        if args.command in {"news-media", "all"}:
            run_news_media(
                store,
                fetcher,
                args.workers,
                args.max_items,
                args.start,
                args.end,
                args.news_min,
                args.news_max,
            )
        if args.command in {"export", "all"}:
            manifest = run_export(
                store,
                args.start,
                args.end,
                args.resource_min,
                args.resource_max,
                args.news_min,
                args.news_max,
            )
            print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2), flush=True)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

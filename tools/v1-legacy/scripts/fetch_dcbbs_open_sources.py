#!/usr/bin/env python3
"""Archive a fixed, audited set of public official sources for DCBBS records.

This program is deliberately independent from ``scrape_dcbbs.py`` and its
SQLite database.  It has a fixed 18-item catalogue, performs no search, does
not authenticate, and does not follow an URL unless that exact URL has passed
the catalogue allowlist (or the one narrowly defined MOHURD attachment rule).

Typical use::

    python scripts/fetch_dcbbs_open_sources.py list
    python scripts/fetch_dcbbs_open_sources.py fetch --only 225086,227474
    python scripts/fetch_dcbbs_open_sources.py verify
    python scripts/fetch_dcbbs_open_sources.py self-test

Output defaults to ``data_out/dcbbs/open_sources`` and is intentionally kept
outside the main scraper database.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import gzip
import hashlib
import html as html_module
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.robotparser
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests


APP_NAME = "DDSOpenSourceArchiver"
USER_AGENT = f"{APP_NAME}/1.0 (+local research archive; no authentication)"
DEFAULT_OUTPUT = Path("data_out/dcbbs/open_sources")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
ACCESS_BOUNDARY_STATUSES = {401, 402, 403, 407}
MAX_REDIRECT_HOPS = 5
MAX_ROBOTS_REDIRECTS = 3
MAX_HTML_BYTES = 64 * 1024 * 1024
MAX_PDF_BYTES = 2 * 1024 * 1024 * 1024
MOHURD_DOWNLOAD_PATH = "/api-gateway/jpaas-web-server/front/document/download"
MOHURD_ATTACHMENT_NAME = "城市更新典型案例集（第二批）.pdf"
MOHURD_FINAL_PATH = (
    "/cms_files/filemanager/1150240553/attach/20253/"
    "DE66492DE4A8843D4DF5CFA539A1F676.pdf"
)
MOHURD_FINAL_FILENAME = "城市更新典型案例集（第二批）"


class ArchiveError(RuntimeError):
    """Base class for a controlled archival failure."""


class URLPolicyError(ArchiveError):
    """An URL is malformed or is outside the exact allowlist."""


class RedirectPolicyError(URLPolicyError):
    """A redirect target is not explicitly allowlisted."""


class RobotsDenied(ArchiveError):
    """robots.txt disallows the requested public URL."""


class RobotsUnavailable(ArchiveError):
    """robots.txt could not be checked safely; fail closed."""


class AccessBoundary(ArchiveError):
    """The source requires authentication, payment, or proxy credentials."""


class ContentValidationError(ArchiveError):
    """Downloaded bytes do not pass the required format validation."""


class AttachmentDiscoveryError(ArchiveError):
    """The one approved official attachment could not be identified safely."""


@dataclass(frozen=True)
class SourceSpec:
    doc_id: int
    title: str
    expected_page_count: int
    source_url: str
    publisher: str
    copyright_notice: str
    match_quality: Literal["exact", "high"]
    access_scope: Literal["full_document_public", "landing", "summary", "summary_fallback"]
    source_format: Literal["pdf", "html", "html_to_pdf"]
    allowed_redirect_urls: tuple[str, ...] = ()
    discovery_rule: str | None = None
    notes: str = ""
    evidence_url: str | None = None
    excluded_urls: tuple[str, ...] = ()

    def allowed_urls(self) -> set[str]:
        return {canonical_url(self.source_url), *(canonical_url(u) for u in self.allowed_redirect_urls)}

    def as_manifest_base(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "expected_page_count": self.expected_page_count,
            "publisher": self.publisher,
            "copyright_notice": self.copyright_notice,
            "source_url": self.source_url,
            "match_quality": self.match_quality,
            "access_scope": self.access_scope,
            "source_format": self.source_format,
            "notes": self.notes,
            "evidence_url": self.evidence_url,
            "excluded_urls": list(self.excluded_urls),
        }

    def legacy_state_base(self) -> dict[str, Any]:
        """Fields written by v1 states, before per-item fingerprints existed."""

        return {
            key: value
            for key, value in self.as_manifest_base().items()
            if key not in {"evidence_url", "excluded_urls"}
        }


RIGHTS_DEFAULT = "版权及使用条件以来源网站和报告内声明为准；本地研究归档不改变任何权利归属。"


CATALOG: tuple[SourceSpec, ...] = (
    SourceSpec(
        225086,
        "2023年东西湖区国民经济和社会发展统计公报.pdf",
        6,
        "https://www.dxh.gov.cn/ZWGK/QZFXXGKML/TJXX_16680/TJGB/202411/P020241212567259416230.pdf",
        "武汉市东西湖区人民政府",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        227474,
        "德勤-2025年商业地产行业展望：迎接发展拐点把握时代机遇.pdf",
        32,
        "https://www.deloitte.com/content/dam/assets-zone1/cn/zh/docs/industries/financial-services/2025/deloitte-cn-re-2025-commercial-real-estate-outlook-zh-250106.pdf",
        "德勤中国",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        224774,
        "【普华永道】2024年上半年中国物流行业并购活动回顾及趋势展望.pdf",
        19,
        "https://www.pwccn.com/zh/transportation/2024-china-ma-half-year-review-outlook-logistics.pdf",
        "普华永道中国",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        226971,
        "中国内地和香港IPO市场：2025年第一季度回顾-毕马威-2025.4.1-19页.pdf",
        19,
        "https://assets.kpmg.com/content/dam/kpmg/tw/pdf/2025/03/china-hk-ipo-2025-q1-review.pdf",
        "毕马威",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        227395,
        "【世邦魏理仕】2025年3月疾风知劲草：北京办公资产价值的分化与重构.pdf",
        19,
        "https://www.cbre.com.cn/insights/reports/%E7%96%BE%E9%A3%8E%E7%9F%A5%E5%8A%B2%E8%8D%89-%E5%8C%97%E4%BA%AC%E5%8A%9E%E5%85%AC%E8%B5%84%E4%BA%A7%E4%BB%B7%E5%80%BC%E7%9A%84%E5%88%86%E5%8C%96%E4%B8%8E%E9%87%8D%E6%9E%84",
        "世邦魏理仕（CBRE）",
        RIGHTS_DEFAULT,
        "exact",
        "summary_fallback",
        "html",
        notes="原 mktgdocs PDF 被 robots.txt 明确禁止；仅归档 CBRE 官方公开摘要页。",
        excluded_urls=(
            "https://mktgdocs.cbre.com/2299/ecd7b6a8-c326-4c65-b6a8-c63eccff76ac-292324814/____________________.pdf",
        ),
    ),
    SourceSpec(
        224862,
        "中国中铁2024年半年度报告.pdf",
        317,
        "https://static.cninfo.com.cn/finalpage/2024-08-30/1221093186.pdf",
        "中国中铁股份有限公司／巨潮资讯网",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        224709,
        "中国铁建2024年半年度报告.pdf",
        278,
        "https://static.cninfo.com.cn/finalpage/2024-08-31/1221082058.PDF",
        "中国铁建股份有限公司／巨潮资讯网",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        224696,
        "金地集团：2024年半年度报告.PDF",
        215,
        "https://static.sse.com.cn/disclosure/bond/announcement/company/c/new/2024-08-30/175235_20240830_AZQ5.pdf",
        "金地（集团）股份有限公司／上海证券交易所",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
        evidence_url="https://www.shclearing.com.cn/xxpl/cwbg/bnb/202408/t20240830_1471991.html",
        excluded_urls=(
            "https://www.shclearing.com.cn/wcm/shch/pages/client/download/download.jsp?FileName=P020240830398850313960.pdf",
        ),
        notes="上海清算所同名附件实测仅 11 页，属于债务融资工具版本，与 DCBBS 215 页条目不符，明确排除。",
    ),
    SourceSpec(
        224680,
        "武商集团：2024年半年度报告.PDF",
        165,
        "https://static.cninfo.com.cn/finalpage/2024-08-30/1221055238.PDF",
        "武商集团股份有限公司／巨潮资讯网",
        RIGHTS_DEFAULT,
        "exact",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        227434,
        "2025年北京市人民政府海淀区城市更新实施指引.pdf",
        115,
        "https://www.beijing.gov.cn/hudong/gfxwjzj/qjzjxx/202506/P020250702359873352733.pdf",
        "北京市人民政府",
        RIGHTS_DEFAULT,
        "high",
        "full_document_public",
        "pdf",
        notes="官方公开版本；与 DCBBS 条目为高置信匹配。",
    ),
    SourceSpec(
        227196,
        "【住建部】2025年城市更新典型案例集（第二批）.pptx",
        92,
        "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2025/art_370c9fd153dd4c00a10e45a1badf6cc3.html",
        "中华人民共和国住房和城乡建设部",
        RIGHTS_DEFAULT,
        "high",
        "full_document_public",
        "html_to_pdf",
        discovery_rule="mohurd_city_renewal_second_batch_pdf",
        notes="DCBBS 标为 PPTX；官方通知页公开附件为 PDF，保留格式差异。",
    ),
    SourceSpec(
        227105,
        "【中国标准化研究院】2025年城市综合发展指数报告.pdf",
        24,
        "https://www.cnis.ac.cn/bydt/kydt/202510/P020251014612237886428.pdf",
        "中国标准化研究院",
        RIGHTS_DEFAULT,
        "high",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        227594,
        "【仲量联行】2025年丨广州办公楼市场白皮书 聚焦数字新势力重塑楼宇生态圈（2025年12月发布）.pdf",
        40,
        "https://www.joneslanglasalle.com.cn/content/dam/jllcom/en/cn/documents/reports/research-reports/25-insights-2025-guangzhou-office-market-whitepaper.pdf",
        "仲量联行（JLL）",
        RIGHTS_DEFAULT,
        "high",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        227170,
        "【世邦魏理仕】2025年一季度中国房地产市场报告.pdf",
        16,
        "https://www.cbre.com.cn/insights/figures/%E4%B8%AD%E5%9B%BD%E6%88%BF%E5%9C%B0%E4%BA%A7%E5%B8%82%E5%9C%BA%E6%8A%A5%E5%91%8A-2025%E5%B9%B4%E7%AC%AC%E4%B8%80%E5%AD%A3%E5%BA%A6",
        "世邦魏理仕（CBRE）",
        RIGHTS_DEFAULT,
        "high",
        "summary_fallback",
        "html",
        notes="原 mktgdocs PDF 被 robots.txt 明确禁止；仅归档 CBRE 官方公开摘要页。",
        excluded_urls=(
            "https://mktgdocs.cbre.com/2299/f61319a1-79b6-4138-926d-a692f5942c49-1062478567/China_Figures_Q1_2025_Chinese.pdf",
        ),
    ),
    SourceSpec(
        227410,
        "【世联】2024年可持续发展报告（暨ESG报告）.pdf",
        59,
        "https://www.worldunion.com.cn/uploadfiles/2025/06/2025062310164316432023.pdf",
        "深圳世联行集团股份有限公司",
        RIGHTS_DEFAULT,
        "high",
        "full_document_public",
        "pdf",
    ),
    SourceSpec(
        227307,
        "【艾瑞咨询】2025年中国城市可信数据空间行业研究报告.pdf",
        28,
        "https://www.idigital.com.cn/report/4756?type=0",
        "艾瑞咨询",
        RIGHTS_DEFAULT,
        "high",
        "summary",
        "html",
        notes="仅归档无需登录即可浏览的官方摘要页，不声称取得完整报告。",
    ),
    SourceSpec(
        227806,
        "【仲量联行】2025年中国办公楼租赁指南（2025年8月发布）.pdf",
        37,
        "https://www.joneslanglasalle.com.cn/en-cn/insights/market-dynamics/china-office-leasing-guide",
        "仲量联行（JLL）",
        RIGHTS_DEFAULT,
        "high",
        "landing",
        "html",
        notes="仅归档无需登录即可浏览的官方落地页，不声称取得完整报告。",
    ),
    SourceSpec(
        227294,
        "世邦魏理仕-2025年上半年上海房地产市场回顾.pdf",
        21,
        "https://www.cbre.com.cn/press-releases/2025h1-shanghai-mv",
        "世邦魏理仕（CBRE）",
        RIGHTS_DEFAULT,
        "high",
        "summary",
        "html",
        notes="仅归档无需登录即可浏览的官方新闻稿，不声称取得完整报告。",
    ),
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_url(url: str) -> str:
    """Return a conservative canonical HTTPS URL or reject it.

    Paths and query strings are intentionally *not* normalized: allowlisting
    is exact, so semantically similar spellings do not inherit permission.
    """

    if not isinstance(url, str) or not url:
        raise URLPolicyError("URL must be a non-empty string")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https":
        raise URLPolicyError(f"only HTTPS is allowed: {url!r}")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise URLPolicyError(f"URL must have a plain hostname and no userinfo: {url!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise URLPolicyError(f"invalid URL port: {url!r}") from exc
    if port not in (None, 443):
        raise URLPolicyError(f"non-default port is forbidden: {url!r}")
    if parts.fragment:
        raise URLPolicyError(f"URL fragments are forbidden: {url!r}")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    netloc = host if port is None else f"{host}:443"
    path = parts.path or "/"
    if any(ord(ch) < 0x20 for ch in path + parts.query):
        raise URLPolicyError(f"control character in URL: {url!r}")
    return urllib.parse.urlunsplit(("https", netloc, path, parts.query, ""))


def origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(canonical_url(url))
    return f"{parts.scheme}://{parts.netloc}"


def same_https_origin(first_url: str, second_url: str) -> bool:
    first = urllib.parse.urlsplit(canonical_url(first_url))
    second = urllib.parse.urlsplit(canonical_url(second_url))
    return (
        first.scheme == second.scheme == "https"
        and first.hostname == second.hostname
        and (first.port or 443) == (second.port or 443)
    )


def safe_component(value: str, limit: int = 96) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return (value or "item")[:limit]


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload)


def deterministic_gzip(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def catalogue_fingerprint(catalogue: Sequence[SourceSpec]) -> str:
    payload = json.dumps(
        [dataclasses.asdict(item) for item in catalogue],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def item_fingerprint(spec: SourceSpec) -> str:
    payload = json.dumps(
        dataclasses.asdict(spec),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def state_matches_spec(state: Mapping[str, Any], spec: SourceSpec) -> bool:
    """Accept current per-item state or a compatible pre-migration v1 state."""

    expected = item_fingerprint(spec)
    recorded = state.get("item_sha256")
    if recorded is not None:
        return recorded == expected
    # Version 1 keyed state validity to the whole catalogue.  Migrate only if
    # every then-existing immutable field still matches this one spec.  Thus a
    # change to another catalogue row cannot invalidate this row, while source
    # changes such as 224696/227170/227395 correctly force a refetch.
    return all(state.get(key) == value for key, value in spec.legacy_state_base().items())


class OriginRateLimiter:
    """Sequential per-origin minimum interval limiter."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second < 0:
            raise ValueError("requests_per_second must be non-negative")
        self.interval = 0.0 if requests_per_second == 0 else 1.0 / requests_per_second
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        if self.interval == 0:
            return
        origin = origin_of(url)
        with self._lock:
            now = time.monotonic()
            delay = self.interval - (now - self._last.get(origin, float("-inf")))
            if delay > 0:
                time.sleep(delay)
            self._last[origin] = time.monotonic()


@dataclass
class FetchConfig:
    output: Path = DEFAULT_OUTPUT
    rate: float = 0.5
    retries: int = 3
    timeout: float = 30.0
    max_html_bytes: int = MAX_HTML_BYTES
    max_pdf_bytes: int = MAX_PDF_BYTES


@dataclass
class FetchResult:
    response: requests.Response
    final_url: str
    redirect_history: list[dict[str, Any]]
    runtime_allowlist_extensions: list[dict[str, Any]]


class RobotsPolicy:
    """Fetch and enforce robots.txt per origin, failing closed on ambiguity."""

    def __init__(
        self,
        session: requests.Session,
        config: FetchConfig,
        limiter: OriginRateLimiter,
    ) -> None:
        self.session = session
        self.config = config
        self.limiter = limiter
        self._cache: dict[str, tuple[str, urllib.robotparser.RobotFileParser | None]] = {}

    def _request_robots(self, url: str) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            self.limiter.wait(url)
            try:
                response = self.session.get(
                    url,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"},
                    timeout=self.config.timeout,
                    allow_redirects=False,
                    stream=False,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.retries:
                    break
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.config.retries:
                response.close()
                time.sleep(_retry_delay(response, attempt))
                continue
            return response
        raise RobotsUnavailable(f"robots.txt request failed for {url}: {last_error}")

    def _load(self, target_url: str) -> tuple[str, urllib.robotparser.RobotFileParser | None]:
        origin = origin_of(target_url)
        if origin in self._cache:
            return self._cache[origin]
        robots_url = canonical_url(f"{origin}/robots.txt")
        current = robots_url
        seen: set[str] = set()
        redirect_history: list[dict[str, Any]] = []
        request_chain: list[dict[str, Any]] = []

        while True:
            if current in seen:
                error = f"robots.txt redirect cycle reached {current}"
                self._archive_robots_chain(
                    origin, robots_url, request_chain, redirect_history, "fail_closed", error
                )
                raise RobotsUnavailable(error)
            seen.add(current)
            response = self._request_robots(current)
            status = response.status_code
            headers = dict(response.headers)
            body = response.content
            response.close()
            record: dict[str, Any] = {
                "hop": len(request_chain),
                "url": current,
                "http_status": status,
                "content_sha256": sha256_bytes(body),
                "byte_size": len(body),
                "content_type": str(headers.get("Content-Type", "")),
                "location": headers.get("Location"),
                "_body": body,
            }
            request_chain.append(record)
            if len(body) > 4 * 1024 * 1024:
                error = f"robots.txt response exceeds 4 MiB at {current}"
                self._archive_robots_chain(
                    origin, robots_url, request_chain, redirect_history, "fail_closed", error
                )
                raise RobotsUnavailable(error)

            if status in REDIRECT_STATUSES:
                location = headers.get("Location")
                if not location:
                    error = f"robots.txt HTTP {status} has no Location at {current}"
                    self._archive_robots_chain(
                        origin, robots_url, request_chain, redirect_history, "fail_closed", error
                    )
                    raise RobotsUnavailable(error)
                if len(redirect_history) >= MAX_ROBOTS_REDIRECTS:
                    error = f"robots.txt exceeds {MAX_ROBOTS_REDIRECTS} redirects at {current}"
                    self._archive_robots_chain(
                        origin, robots_url, request_chain, redirect_history, "fail_closed", error
                    )
                    raise RobotsUnavailable(error)
                try:
                    next_url = canonical_url(urllib.parse.urljoin(current, location))
                except URLPolicyError as exc:
                    error = f"unsafe robots.txt redirect from {current}: {exc}"
                    record["redirect_error"] = str(exc)
                    self._archive_robots_chain(
                        origin, robots_url, request_chain, redirect_history, "fail_closed", error
                    )
                    raise RobotsUnavailable(error) from exc
                record["resolved_location"] = next_url
                if not same_https_origin(robots_url, next_url):
                    error = f"cross-origin robots.txt redirect refused: {current} -> {next_url}"
                    self._archive_robots_chain(
                        origin, robots_url, request_chain, redirect_history, "fail_closed", error
                    )
                    raise RobotsUnavailable(error)
                redirect_entry = {
                    "status": status,
                    "from": current,
                    "location": location,
                    "to": next_url,
                }
                redirect_history.append(redirect_entry)
                if next_url in seen:
                    error = f"robots.txt redirect cycle refused: {current} -> {next_url}"
                    self._archive_robots_chain(
                        origin, robots_url, request_chain, redirect_history, "fail_closed", error
                    )
                    raise RobotsUnavailable(error)
                current = next_url
                continue

            if status in (404, 410):
                result: tuple[str, urllib.robotparser.RobotFileParser | None] = ("missing_allow", None)
                decision = "missing_allow"
            elif status in ACCESS_BOUNDARY_STATUSES:
                result = ("access_denied", None)
                decision = "access_denied"
            elif 200 <= status < 300:
                text = body.decode("utf-8", "replace")
                parser = urllib.robotparser.RobotFileParser(current)
                parser.parse(text.splitlines())
                result = ("parsed", parser)
                decision = "parsed"
            else:
                error = f"robots.txt returned HTTP {status} for {origin}"
                self._archive_robots_chain(
                    origin, robots_url, request_chain, redirect_history, "fail_closed", error
                )
                raise RobotsUnavailable(error)
            self._archive_robots_chain(
                origin, robots_url, request_chain, redirect_history, decision, None
            )
            break
        self._cache[origin] = result
        return result

    def _archive_robots_chain(
        self,
        origin: str,
        robots_url: str,
        request_chain: Sequence[Mapping[str, Any]],
        redirect_history: Sequence[Mapping[str, Any]],
        decision: str,
        error: str | None,
    ) -> None:
        host = urllib.parse.urlsplit(origin).hostname or "unknown"
        base = self.config.output / "raw" / "robots" / safe_component(host)
        archived_chain: list[dict[str, Any]] = []
        for index, raw_record in enumerate(request_chain):
            record = dict(raw_record)
            body = bytes(record.pop("_body", b""))
            relative = Path("hops") / f"hop_{index:02d}_{record['http_status']}.body.gz"
            atomic_write_bytes(base / relative, deterministic_gzip(body))
            record["archived_body_path"] = relative.as_posix()
            archived_chain.append(record)
        final_archive = base / "robots.txt.gz"
        if request_chain and request_chain[-1].get("http_status") not in REDIRECT_STATUSES:
            final_body = bytes(request_chain[-1].get("_body", b""))
            atomic_write_bytes(final_archive, deterministic_gzip(final_body))
        else:
            final_archive.unlink(missing_ok=True)
        atomic_write_json(
            base / "metadata.json",
            {
                "robots_url": robots_url,
                "origin": origin,
                "final_url": archived_chain[-1]["url"] if archived_chain else None,
                "http_status": archived_chain[-1]["http_status"] if archived_chain else None,
                "redirect_count": len(redirect_history),
                "redirect_history": list(redirect_history),
                "request_chain": archived_chain,
                "decision": decision,
                "error": error,
                "checked_at": utc_now(),
                "redirect_policy": (
                    f"automatic redirects disabled; at most {MAX_ROBOTS_REDIRECTS} manual redirects; "
                    "every hop must remain on the same HTTPS origin; cycles fail closed"
                ),
            },
        )

    def assert_allowed(self, target_url: str) -> None:
        status, parser = self._load(target_url)
        if status == "access_denied":
            raise RobotsDenied(f"robots.txt access status denies crawling {target_url}")
        if status == "missing_allow":
            return
        assert parser is not None
        if not parser.can_fetch(APP_NAME, canonical_url(target_url)):
            raise RobotsDenied(f"robots.txt disallows {target_url}")


def _retry_delay(response: requests.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return min(max(float(raw), 0.0), 30.0)
    except (TypeError, ValueError):
        return min(2**attempt, 8)


def validate_pdf(path: Path) -> dict[str, Any]:
    """Validate PDF magic, EOF, strict object parsing, and every page stream."""

    size = path.stat().st_size
    if size < 64:
        raise ContentValidationError(f"PDF is implausibly small: {size} bytes")
    with path.open("rb") as handle:
        head = handle.read(8)
        if not head.startswith(b"%PDF-"):
            raise ContentValidationError("PDF magic is missing at byte zero")
        handle.seek(max(0, size - 8192))
        tail = handle.read()
    if b"%%EOF" not in tail:
        raise ContentValidationError("PDF EOF marker is missing from the final 8 KiB")

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - environment diagnostic
        raise ContentValidationError("pypdf is required for complete PDF validation") from exc

    try:
        reader = PdfReader(str(path), strict=True)
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            result = reader.decrypt("")
            if not result:
                raise ContentValidationError("PDF requires a non-empty password; refusing to bypass it")
        page_count = len(reader.pages)
        if page_count < 1:
            raise ContentValidationError("PDF contains no pages")
        decoded_stream_bytes = 0
        for index, page in enumerate(reader.pages):
            # Resolve the page dictionary, resources, media box, and all content
            # streams.  This is deliberately stronger than merely counting the
            # page-tree nodes and detects many truncated/corrupt downloads.
            if page.indirect_reference is not None:
                page.indirect_reference.get_object()
            _ = tuple(float(v) for v in page.mediabox)
            resources = page.get("/Resources")
            if hasattr(resources, "get_object"):
                resources.get_object()
            contents = page.get_contents()
            if contents is not None:
                decoded_stream_bytes += len(contents.get_data())
        metadata = reader.metadata
        trailer = reader.trailer
        if "/Root" not in trailer:
            raise ContentValidationError("PDF trailer has no /Root")
    except ContentValidationError:
        raise
    except Exception as exc:
        raise ContentValidationError(f"strict PDF parse failed: {type(exc).__name__}: {exc}") from exc

    return {
        "pdf_version": head[:8].decode("ascii", "replace"),
        "page_count": page_count,
        "encrypted": encrypted,
        "decoded_page_stream_bytes": decoded_stream_bytes,
        "metadata_present": bool(metadata),
        "byte_size": size,
        "sha256": sha256_file(path),
        "validation": "magic+eof+pypdf_strict+all_page_streams",
    }


def discover_mohurd_attachment(page_url: str, raw_html: bytes) -> str:
    """Return only the precisely approved MOHURD PDF attachment URL.

    The opaque ``fileUrl`` token is supplied by the official page, but every
    other URL component is fixed: HTTPS, same origin, exact path, exactly two
    query parameters, and an exact decoded filename.  The returned complete
    URL is added to the single item's runtime allowlist before any request.
    """

    page_url = canonical_url(page_url)
    page_parts = urllib.parse.urlsplit(page_url)
    text = raw_html.decode("utf-8", "replace")
    hrefs = re.findall(r"\bhref\s*=\s*([\"'])(.*?)\1", text, flags=re.IGNORECASE | re.DOTALL)
    matches: list[str] = []
    for _, raw_href in hrefs:
        candidate_text = html_module.unescape(raw_href.strip())
        try:
            candidate = canonical_url(urllib.parse.urljoin(page_url, candidate_text))
        except URLPolicyError:
            continue
        parts = urllib.parse.urlsplit(candidate)
        if parts.scheme != "https" or parts.netloc != page_parts.netloc:
            continue
        if parts.path != MOHURD_DOWNLOAD_PATH:
            continue
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != 2 or {key for key, _ in pairs} != {"fileUrl", "fileName"}:
            continue
        params = dict(pairs)
        if params["fileName"] != MOHURD_ATTACHMENT_NAME or not params["fileUrl"]:
            continue
        if len(params["fileUrl"]) > 4096:
            continue
        matches.append(candidate)
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise AttachmentDiscoveryError(
            f"expected exactly one approved MOHURD attachment, found {len(unique)}"
        )
    return unique[0]


def validate_mohurd_attachment_redirect(source_url: str, target_url: str) -> dict[str, Any]:
    """Validate the known official gateway-to-PDF redirect before allowlisting."""

    source = urllib.parse.urlsplit(canonical_url(source_url))
    target = urllib.parse.urlsplit(canonical_url(target_url))
    if source.netloc != "www.mohurd.gov.cn" or source.path != MOHURD_DOWNLOAD_PATH:
        raise RedirectPolicyError("MOHURD redirect source is not the approved download gateway")
    if target.scheme != "https" or target.netloc != source.netloc:
        raise RedirectPolicyError("MOHURD attachment redirect must remain on the exact HTTPS origin")
    path_shape = r"/cms_files/filemanager/[1-9]\d{5,12}/attach/\d{5,8}/[A-F0-9]{32}\.pdf"
    if not re.fullmatch(path_shape, target.path) or target.path != MOHURD_FINAL_PATH:
        raise RedirectPolicyError("MOHURD attachment redirect has an unapproved path")
    try:
        pairs = urllib.parse.parse_qsl(target.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise RedirectPolicyError("MOHURD attachment redirect has a malformed query") from exc
    if len(pairs) != 1 or pairs[0] != ("fileName", MOHURD_FINAL_FILENAME):
        raise RedirectPolicyError("MOHURD attachment redirect must have one exact fileName parameter")
    return {
        "url": canonical_url(target_url),
        "rule": "mohurd_same_origin_exact_path_and_filename_redirect",
        "reason": "same HTTPS origin, approved path shape, exact fixed path, and one exact fileName",
    }


class OpenSourceArchiver:
    def __init__(
        self,
        config: FetchConfig,
        catalogue: Sequence[SourceSpec] = CATALOG,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.catalogue = tuple(catalogue)
        self.spec_by_id = {item.doc_id: item for item in self.catalogue}
        self.session = session or requests.Session()
        self.session.auth = None
        self.session.headers.clear()
        self.limiter = OriginRateLimiter(config.rate)
        self.robots = RobotsPolicy(self.session, config, self.limiter)
        self.fingerprint = catalogue_fingerprint(self.catalogue)
        self._validate_catalogue()
        self._init_output()

    def _validate_catalogue(self) -> None:
        ids = [item.doc_id for item in self.catalogue]
        if len(ids) != len(set(ids)):
            raise ValueError("catalogue doc_id values must be unique")
        for item in self.catalogue:
            allowed = item.allowed_urls()
            for url in item.excluded_urls:
                if canonical_url(url) in allowed:
                    raise ValueError(f"item {item.doc_id} excluded URL is accidentally allowlisted")
            if item.evidence_url:
                canonical_url(item.evidence_url)
            if item.source_format == "html_to_pdf" and not item.discovery_rule:
                raise ValueError(f"item {item.doc_id} needs a discovery rule")
            if item.access_scope != "full_document_public" and item.source_format != "html":
                raise ValueError(f"non-full item {item.doc_id} must archive HTML only")

    def _init_output(self) -> None:
        for relative in (
            "documents/exact",
            "documents/high",
            "raw/html",
            "raw/robots",
            "state/items",
            "state/partials",
            "quarantine",
        ):
            (self.config.output / relative).mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.config.output / "catalog.json",
            {
                "schema_version": 2,
                "catalogue_sha256": self.fingerprint,
                "item_count": len(self.catalogue),
                "items": [
                    {**dataclasses.asdict(item), "item_sha256": item_fingerprint(item)}
                    for item in self.catalogue
                ],
                "policy": {
                    "authentication": "none",
                    "payment_bypass": "none",
                    "redirects": "manual; every target requires exact allowlist and robots check",
                    "robots": (
                        f"per HTTPS origin; at most {MAX_ROBOTS_REDIRECTS} manual same-origin HTTPS "
                        "redirects; final 404/410 means missing_allow; unsafe chains fail closed"
                    ),
                },
            },
        )
        self.rebuild_manifest()

    def _state_path(self, doc_id: int) -> Path:
        return self.config.output / "state" / "items" / f"{doc_id}.json"

    def _partial_path(self, doc_id: int) -> Path:
        return self.config.output / "state" / "partials" / f"{doc_id}.part"

    def _partial_meta_path(self, doc_id: int) -> Path:
        return self.config.output / "state" / "partials" / f"{doc_id}.json"

    def _read_state(self, doc_id: int) -> dict[str, Any]:
        path = self._state_path(doc_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        spec = self.spec_by_id.get(doc_id)
        if spec is None or not state_matches_spec(data, spec):
            return {}
        return data

    def _save_state(self, spec: SourceSpec, **updates: Any) -> dict[str, Any]:
        state = {
            **updates,
            **spec.as_manifest_base(),
            "item_sha256": item_fingerprint(spec),
            "catalogue_sha256": self.fingerprint,
            "updated_at": utc_now(),
        }
        state.setdefault("actual_access_scope", spec.access_scope)
        atomic_write_json(self._state_path(spec.doc_id), state)
        self.rebuild_manifest()
        return state

    def rebuild_manifest(self) -> Path:
        records: list[dict[str, Any]] = []
        for spec in sorted(self.catalogue, key=lambda item: item.doc_id):
            state_path = self._state_path(spec.doc_id)
            state: dict[str, Any] = {}
            if state_path.exists():
                try:
                    candidate = json.loads(state_path.read_text("utf-8"))
                    if state_matches_spec(candidate, spec):
                        state = candidate
                except (OSError, json.JSONDecodeError):
                    state = {}
            records.append(
                {
                    "status": "pending",
                    **state,
                    **spec.as_manifest_base(),
                    "actual_access_scope": state.get("actual_access_scope", spec.access_scope),
                    "item_sha256": item_fingerprint(spec),
                    "catalogue_sha256": self.fingerprint,
                }
            )
        payload = b"".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            for record in records
        )
        path = self.config.output / "manifest.jsonl"
        atomic_write_bytes(path, payload)
        return path

    def _request_with_retries(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        stream: bool,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            self.limiter.wait(url)
            try:
                response = self.session.get(
                    url,
                    headers={"User-Agent": USER_AGENT, **dict(headers)},
                    timeout=self.config.timeout,
                    allow_redirects=False,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.retries:
                    break
                time.sleep(min(2**attempt, 8))
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.config.retries:
                response.close()
                time.sleep(_retry_delay(response, attempt))
                continue
            return response
        raise ArchiveError(f"request failed for {url}: {last_error}")

    def _fetch_allowlisted(
        self,
        start_url: str,
        allowed_urls: set[str],
        *,
        headers: Mapping[str, str] | None = None,
        stream: bool,
        accepted_statuses: set[int] | None = None,
        redirect_allowlist_extender: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> FetchResult:
        current = canonical_url(start_url)
        allowed = {canonical_url(url) for url in allowed_urls}
        history: list[dict[str, Any]] = []
        runtime_extensions: list[dict[str, Any]] = []
        for hop in range(MAX_REDIRECT_HOPS + 1):
            if current not in allowed:
                raise URLPolicyError(f"URL is not in the exact allowlist: {current}")
            self.robots.assert_allowed(current)
            response = self._request_with_retries(current, headers=headers or {}, stream=stream)
            status = response.status_code
            if status in ACCESS_BOUNDARY_STATUSES:
                response.close()
                raise AccessBoundary(f"HTTP {status} at {current}; no authentication/payment bypass attempted")
            if status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise RedirectPolicyError(f"HTTP {status} has no Location at {current}")
                target = canonical_url(urllib.parse.urljoin(current, location))
                if target not in allowed:
                    if redirect_allowlist_extender is None:
                        raise RedirectPolicyError(
                            f"redirect target is not explicitly allowlisted: {current} -> {target}"
                        )
                    extension = redirect_allowlist_extender(current, target)
                    if canonical_url(str(extension.get("url", ""))) != target:
                        raise RedirectPolicyError("runtime redirect validator returned a different URL")
                    allowed.add(target)
                    runtime_extensions.append(extension)
                history.append(
                    {
                        "status": status,
                        "from": current,
                        "to": target,
                        "runtime_allowlist_extended": any(entry["url"] == target for entry in runtime_extensions),
                    }
                )
                current = target
                continue
            if not 200 <= status < 300 and status not in (accepted_statuses or set()):
                response.close()
                raise ArchiveError(f"HTTP {status} at {current}")
            return FetchResult(response, current, history, runtime_extensions)
        raise RedirectPolicyError(f"more than {MAX_REDIRECT_HOPS} redirects from {start_url}")

    def _read_bounded(self, response: requests.Response, limit: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=128 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > limit:
                raise ContentValidationError(f"response exceeds configured limit of {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def _archive_html_bytes(
        self,
        spec: SourceSpec,
        body: bytes,
        *,
        final_url: str,
        response_headers: Mapping[str, Any],
        redirect_history: list[dict[str, Any]],
        layer: str,
    ) -> dict[str, Any]:
        content_type = str(response_headers.get("Content-Type", ""))
        lower_head = body[:1024].lower()
        if body.startswith(b"%PDF-"):
            raise ContentValidationError("expected HTML but received a PDF")
        if b"<html" not in lower_head and b"<!doctype html" not in lower_head:
            # Some pages prepend a long BOM/XML declaration; inspect a wider
            # window while still refusing arbitrary binary responses.
            if b"<html" not in body[:16_384].lower():
                raise ContentValidationError(f"HTML magic not found (Content-Type={content_type!r})")
        digest = sha256_bytes(body)
        relative = Path("raw") / "html" / layer / str(spec.doc_id) / f"{digest}.html.gz"
        absolute = self.config.output / relative
        atomic_write_bytes(absolute, deterministic_gzip(body))
        return {
            "artifact_path": relative.as_posix(),
            "artifact_kind": "html_gzip_raw",
            "byte_size": len(body),
            "sha256": digest,
            "content_type": content_type,
            "final_url": final_url,
            "redirect_history": redirect_history,
            "gzip_mtime": 0,
        }

    def _fetch_html(self, spec: SourceSpec, allowed_urls: set[str], layer: str) -> tuple[bytes, dict[str, Any]]:
        result = self._fetch_allowlisted(
            spec.source_url,
            allowed_urls,
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"},
            stream=True,
        )
        try:
            body = self._read_bounded(result.response, self.config.max_html_bytes)
            record = self._archive_html_bytes(
                spec,
                body,
                final_url=result.final_url,
                response_headers=result.response.headers,
                redirect_history=result.redirect_history,
                layer=layer,
            )
        finally:
            result.response.close()
        return body, record

    def _load_partial_meta(self, spec: SourceSpec, source_url: str) -> dict[str, Any]:
        path = self._partial_meta_path(spec.doc_id)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        recorded_item = value.get("item_sha256")
        if recorded_item is not None and recorded_item != item_fingerprint(spec):
            return {}
        if value.get("doc_id") != spec.doc_id or value.get("source_url") != source_url:
            return {}
        return value

    def _download_pdf(
        self,
        spec: SourceSpec,
        source_url: str,
        allowed_urls: set[str],
        redirect_allowlist_extender: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        source_url = canonical_url(source_url)
        part = self._partial_path(spec.doc_id)
        meta_path = self._partial_meta_path(spec.doc_id)
        partial_meta = self._load_partial_meta(spec, source_url)
        if not partial_meta and part.exists():
            part.unlink()
        current_size = part.stat().st_size if part.exists() else 0
        request_headers: dict[str, str] = {"Accept": "application/pdf,*/*;q=0.1"}
        if current_size:
            request_headers["Range"] = f"bytes={current_size}-"
            validator = partial_meta.get("etag") or partial_meta.get("last_modified")
            if validator:
                request_headers["If-Range"] = str(validator)

        result = self._fetch_allowlisted(
            source_url,
            allowed_urls,
            headers=request_headers,
            stream=True,
            accepted_statuses={416},
            redirect_allowlist_extender=redirect_allowlist_extender,
        )
        response = result.response
        try:
            append = False
            already_complete = False
            if current_size and response.status_code == 416:
                content_range = response.headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes\s+\*/(\d+)", content_range)
                if not match or int(match.group(1)) != current_size:
                    raise ContentValidationError(
                        f"invalid completed-resume Content-Range {content_range!r}; "
                        f"local size is {current_size}"
                    )
                already_complete = True
            elif current_size and response.status_code == 206:
                content_range = response.headers.get("Content-Range", "")
                match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range)
                if not match or int(match.group(1)) != current_size:
                    raise ContentValidationError(
                        f"invalid resume Content-Range {content_range!r}; expected start {current_size}"
                    )
                append = True
            elif response.status_code == 206:
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith("bytes 0-"):
                    raise ContentValidationError(f"unexpected initial Content-Range {content_range!r}")
            elif response.status_code == 200:
                append = False  # server ignored Range: safely restart from zero
            else:
                raise ContentValidationError(f"unexpected PDF response status {response.status_code}")

            if not already_complete:
                atomic_write_json(
                    meta_path,
                    {
                        "catalogue_sha256": self.fingerprint,
                        "item_sha256": item_fingerprint(spec),
                        "doc_id": spec.doc_id,
                        "source_url": source_url,
                        "final_url": result.final_url,
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "updated_at": utc_now(),
                    },
                )
                mode = "ab" if append else "wb"
                part.parent.mkdir(parents=True, exist_ok=True)
                with part.open(mode) as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        if handle.tell() > self.config.max_pdf_bytes:
                            raise ContentValidationError(
                                f"PDF exceeds configured limit of {self.config.max_pdf_bytes} bytes"
                            )
                    handle.flush()
                    os.fsync(handle.fileno())
        finally:
            response.close()

        try:
            validation = validate_pdf(part)
        except Exception:
            if part.exists():
                digest = sha256_file(part)
                quarantine = self.config.output / "quarantine" / str(spec.doc_id) / f"{digest}.invalid"
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                os.replace(part, quarantine)
            meta_path.unlink(missing_ok=True)
            raise

        filename = f"{spec.doc_id}_{safe_component(Path(urllib.parse.urlsplit(source_url).path).name, 120)}"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        relative = Path("documents") / spec.match_quality / str(spec.doc_id) / filename
        destination = self.config.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, destination)
        meta_path.unlink(missing_ok=True)
        return {
            "artifact_path": relative.as_posix(),
            "artifact_kind": "pdf",
            **validation,
            "expected_page_count": spec.expected_page_count,
            "page_count_matches_expected": validation["page_count"] == spec.expected_page_count,
            "final_url": result.final_url,
            "redirect_history": result.redirect_history,
            "runtime_redirect_allowlist_extensions": result.runtime_allowlist_extensions,
            "content_type": response.headers.get("Content-Type", ""),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
        }

    def _verify_existing(self, spec: SourceSpec, state: Mapping[str, Any]) -> dict[str, Any] | None:
        artifact_path = state.get("artifact_path")
        if not isinstance(artifact_path, str):
            return None
        absolute = self.config.output / Path(artifact_path)
        try:
            absolute.resolve().relative_to(self.config.output.resolve())
        except (OSError, ValueError):
            return None
        if not absolute.is_file():
            return None
        if state.get("artifact_kind") == "pdf":
            validation = validate_pdf(absolute)
            if validation["sha256"] != state.get("sha256"):
                return None
            return {**dict(state), **validation, "verified_at": utc_now()}
        if state.get("artifact_kind") == "html_gzip_raw":
            try:
                body = gzip.decompress(absolute.read_bytes())
            except (OSError, EOFError, gzip.BadGzipFile):
                return None
            if sha256_bytes(body) != state.get("sha256"):
                return None
            return {**dict(state), "byte_size": len(body), "verified_at": utc_now()}
        return None

    def fetch_one(self, spec: SourceSpec) -> dict[str, Any]:
        previous = self._read_state(spec.doc_id)
        if previous.get("status") in {"archived", "archived_needs_review", "summary_fallback"}:
            verified = self._verify_existing(spec, previous)
            if verified is not None:
                return self._save_state(spec, **verified)

        allowed_urls = spec.allowed_urls()
        try:
            if spec.source_format == "html":
                _, artifact = self._fetch_html(spec, allowed_urls, spec.access_scope)
                status = "summary_fallback" if spec.access_scope == "summary_fallback" else "archived"
                return self._save_state(
                    spec,
                    status=status,
                    actual_access_scope=spec.access_scope,
                    archived_at=utc_now(),
                    claim=(
                        "official public summary fallback only; excluded PDF was not requested"
                        if spec.access_scope == "summary_fallback"
                        else "public landing/summary HTML only; no full document claimed"
                    ),
                    **artifact,
                )

            discovery_artifact: dict[str, Any] | None = None
            discovery_extension: dict[str, Any] | None = None
            redirect_extender: Callable[[str, str], dict[str, Any]] | None = None
            pdf_url = spec.source_url
            if spec.source_format == "html_to_pdf":
                raw_html, discovery_artifact = self._fetch_html(spec, allowed_urls, "discovery")
                if spec.discovery_rule != "mohurd_city_renewal_second_batch_pdf":
                    raise AttachmentDiscoveryError(f"unknown discovery rule {spec.discovery_rule!r}")
                pdf_url = discover_mohurd_attachment(spec.source_url, raw_html)
                # This is the sole runtime allowlist extension.  The complete
                # canonical URL is added before requesting it, and it still
                # receives an independent robots check.
                allowed_urls.add(pdf_url)
                discovery_extension = {
                    "url": pdf_url,
                    "rule": spec.discovery_rule,
                    "reason": f"same-origin exact path and exact filename {MOHURD_ATTACHMENT_NAME}",
                }
                redirect_extender = validate_mohurd_attachment_redirect

            artifact = self._download_pdf(
                spec,
                pdf_url,
                allowed_urls,
                redirect_allowlist_extender=redirect_extender,
            )
            redirect_extensions = artifact.pop("runtime_redirect_allowlist_extensions")
            status = "archived"
            if not artifact["page_count_matches_expected"]:
                status = "archived_needs_review"
            return self._save_state(
                spec,
                status=status,
                actual_access_scope="full_document_public",
                archived_at=utc_now(),
                runtime_allowlist_extensions=(
                    ([discovery_extension] if discovery_extension else []) + redirect_extensions
                ),
                discovery_artifact=discovery_artifact,
                **artifact,
            )
        except Exception as exc:
            return self._save_state(
                spec,
                status="blocked" if isinstance(exc, (RobotsDenied, RobotsUnavailable, AccessBoundary)) else "error",
                error_type=type(exc).__name__,
                error=str(exc),
                actual_access_scope=spec.access_scope,
                failed_at=utc_now(),
                boundary_note="no login, payment, credential, or redirect bypass was attempted",
            )

    def fetch(self, only: set[int] | None = None) -> list[dict[str, Any]]:
        selected = [spec for spec in self.catalogue if only is None or spec.doc_id in only]
        if only is not None:
            missing = sorted(only - {spec.doc_id for spec in selected})
            if missing:
                raise ValueError(f"unknown doc_id values: {missing}")
        results: list[dict[str, Any]] = []
        for index, spec in enumerate(selected, 1):
            print(f"[{index}/{len(selected)}] {spec.doc_id} {spec.access_scope} {spec.source_url}", flush=True)
            state = self.fetch_one(spec)
            print(f"  -> {state['status']}", flush=True)
            results.append(state)
        return results

    def verify(self, only: set[int] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for spec in self.catalogue:
            if only is not None and spec.doc_id not in only:
                continue
            state = self._read_state(spec.doc_id)
            verified = self._verify_existing(spec, state) if state else None
            results.append(
                {
                    "doc_id": spec.doc_id,
                    "status": "verified" if verified else ("pending" if not state else "invalid"),
                    "artifact_path": state.get("artifact_path"),
                }
            )
        return results


# ---------------------------------------------------------------------------
# Embedded unittest suite.  It uses only temporary directories and mocked
# network responses so the "only one new file" constraint remains true.
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = body
        self.headers = dict(headers or {})
        self.closed = False

    def iter_content(self, chunk_size: int = 1) -> Iterator[bytes]:
        for offset in range(0, len(self.content), max(1, chunk_size)):
            yield self.content[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, routes: Mapping[str, Sequence[FakeResponse]]) -> None:
        self.routes = {url: list(responses) for url, responses in routes.items()}
        self.calls: list[dict[str, Any]] = []
        self.auth: Any = None
        self.headers: dict[str, str] = {}

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        queue = self.routes.get(url)
        if not queue:
            raise AssertionError(f"unexpected network request: {url}")
        return queue.pop(0)


def _test_spec(
    url: str,
    *,
    source_format: Literal["pdf", "html", "html_to_pdf"] = "html",
    access_scope: Literal["full_document_public", "landing", "summary", "summary_fallback"] = "summary",
    redirects: tuple[str, ...] = (),
) -> SourceSpec:
    return SourceSpec(
        999001,
        "test.pdf",
        1,
        url,
        "Test Publisher",
        RIGHTS_DEFAULT,
        "exact",
        access_scope,
        source_format,
        redirects,
    )


class URLPolicyTests(unittest.TestCase):
    def test_rejects_non_https_userinfo_port_and_fragment(self) -> None:
        for url in (
            "http://example.com/a",
            "https://user@example.com/a",
            "https://example.com:444/a",
            "https://example.com/a#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(URLPolicyError):
                canonical_url(url)

    def test_unknown_redirect_is_not_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = "https://example.test/report"
            robots = "https://example.test/robots.txt"
            session = FakeSession(
                {
                    robots: [FakeResponse(404)],
                    source: [FakeResponse(302, headers={"Location": "https://evil.test/file.pdf"})],
                }
            )
            archiver = OpenSourceArchiver(
                FetchConfig(Path(tmp), rate=0, retries=0), [_test_spec(source)], session=session  # type: ignore[arg-type]
            )
            with self.assertRaises(RedirectPolicyError):
                archiver._fetch_allowlisted(source, {source}, stream=False)
            self.assertEqual([call["url"] for call in session.calls], [robots, source])


class CatalogueTests(unittest.TestCase):
    def test_fixed_catalogue_reflects_two_robots_summary_fallbacks(self) -> None:
        self.assertEqual(len(CATALOG), 18)
        self.assertEqual(len({item.doc_id for item in CATALOG}), 18)
        self.assertEqual(
            sum(item.match_quality == "exact" and item.access_scope == "full_document_public" for item in CATALOG),
            8,
        )
        self.assertEqual(
            sum(item.match_quality == "high" and item.access_scope == "full_document_public" for item in CATALOG),
            5,
        )
        self.assertEqual(sum(item.access_scope in {"landing", "summary"} for item in CATALOG), 3)
        self.assertEqual(sum(item.access_scope == "summary_fallback" for item in CATALOG), 2)
        for item in CATALOG:
            self.assertTrue(item.publisher)
            self.assertTrue(item.copyright_notice)
            self.assertEqual(canonical_url(item.source_url), item.source_url)

        by_id = {item.doc_id: item for item in CATALOG}
        sse = by_id[224696]
        self.assertEqual(urllib.parse.urlsplit(sse.source_url).hostname, "static.sse.com.cn")
        self.assertEqual(sse.expected_page_count, 215)
        self.assertTrue(any("shclearing.com.cn" in url for url in sse.excluded_urls))
        self.assertNotIn(canonical_url(str(sse.evidence_url)), sse.allowed_urls())
        self.assertTrue(sse.allowed_urls().isdisjoint(sse.excluded_urls))
        self.assertIn("11 页", sse.notes)
        for doc_id in (227395, 227170):
            self.assertEqual(by_id[doc_id].access_scope, "summary_fallback")
            self.assertEqual(by_id[doc_id].source_format, "html")
            self.assertTrue(all("mktgdocs.cbre.com" in url for url in by_id[doc_id].excluded_urls))
            self.assertTrue(by_id[doc_id].allowed_urls().isdisjoint(by_id[doc_id].excluded_urls))


class RobotsTests(unittest.TestCase):
    def test_disallow_stops_before_content_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = "https://example.test/private/report"
            robots = "https://example.test/robots.txt"
            session = FakeSession(
                {robots: [FakeResponse(200, b"User-agent: *\nDisallow: /private/\n")]}
            )
            archiver = OpenSourceArchiver(
                FetchConfig(Path(tmp), rate=0, retries=0), [_test_spec(source)], session=session  # type: ignore[arg-type]
            )
            with self.assertRaises(RobotsDenied):
                archiver._fetch_allowlisted(source, {source}, stream=False)
            self.assertEqual([call["url"] for call in session.calls], [robots])

    def test_same_origin_robots_redirect_to_404_is_missing_allow_and_fully_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = "https://example.test/report"
            robots = "https://example.test/robots.txt"
            missing = "https://example.test/404"
            session = FakeSession(
                {
                    robots: [FakeResponse(302, b"redirect", {"Location": "/404"})],
                    missing: [FakeResponse(404, b"not found")],
                    source: [FakeResponse(200, b"public")],
                }
            )
            archiver = OpenSourceArchiver(
                FetchConfig(Path(tmp), rate=0, retries=0), [_test_spec(source)], session=session  # type: ignore[arg-type]
            )
            result = archiver._fetch_allowlisted(source, {source}, stream=False)
            self.assertEqual([call["url"] for call in session.calls], [robots, missing, source])
            metadata_path = Path(tmp) / "raw" / "robots" / "example.test" / "metadata.json"
            metadata = json.loads(metadata_path.read_text("utf-8"))
            self.assertEqual(metadata["decision"], "missing_allow")
            self.assertEqual(metadata["redirect_count"], 1)
            self.assertEqual(metadata["redirect_history"][0]["to"], missing)
            self.assertEqual(len(metadata["request_chain"]), 2)
            for hop in metadata["request_chain"]:
                self.assertTrue((metadata_path.parent / hop["archived_body_path"]).is_file())
            result.response.close()

    def test_cross_origin_and_https_downgrade_robots_redirects_fail_closed(self) -> None:
        for location in (
            "https://other.test/robots.txt",
            "http://example.test/robots.txt",
            "https://user@example.test/robots.txt",
            "https://example.test:444/robots.txt",
        ):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as tmp:
                source = "https://example.test/report"
                robots = "https://example.test/robots.txt"
                session = FakeSession(
                    {robots: [FakeResponse(302, b"redirect", {"Location": location})]}
                )
                archiver = OpenSourceArchiver(
                    FetchConfig(Path(tmp), rate=0, retries=0),
                    [_test_spec(source)],
                    session=session,  # type: ignore[arg-type]
                )
                with self.assertRaises(RobotsUnavailable):
                    archiver._fetch_allowlisted(source, {source}, stream=False)
                self.assertEqual([call["url"] for call in session.calls], [robots])
                metadata = json.loads(
                    (Path(tmp) / "raw" / "robots" / "example.test" / "metadata.json").read_text("utf-8")
                )
                self.assertEqual(metadata["decision"], "fail_closed")

    def test_robots_redirect_cycle_and_more_than_three_hops_fail_closed(self) -> None:
        scenarios = (
            {
                "https://example.test/robots.txt": [FakeResponse(302, headers={"Location": "/r1"})],
                "https://example.test/r1": [FakeResponse(302, headers={"Location": "/robots.txt"})],
            },
            {
                "https://example.test/robots.txt": [FakeResponse(302, headers={"Location": "/r1"})],
                "https://example.test/r1": [FakeResponse(302, headers={"Location": "/r2"})],
                "https://example.test/r2": [FakeResponse(302, headers={"Location": "/r3"})],
                "https://example.test/r3": [FakeResponse(302, headers={"Location": "/r4"})],
            },
        )
        for routes in scenarios:
            with self.subTest(routes=list(routes)), tempfile.TemporaryDirectory() as tmp:
                source = "https://example.test/report"
                archiver = OpenSourceArchiver(
                    FetchConfig(Path(tmp), rate=0, retries=0),
                    [_test_spec(source)],
                    session=FakeSession(routes),  # type: ignore[arg-type]
                )
                with self.assertRaises(RobotsUnavailable):
                    archiver._fetch_allowlisted(source, {source}, stream=False)


class AttachmentDiscoveryTests(unittest.TestCase):
    def test_only_exact_same_origin_mohurd_attachment_is_accepted(self) -> None:
        page = "https://www.mohurd.gov.cn/gongkai/example.html"
        href = (
            "/api-gateway/jpaas-web-server/front/document/download?"
            "fileUrl=opaque-token&amp;fileName="
            "%E5%9F%8E%E5%B8%82%E6%9B%B4%E6%96%B0%E5%85%B8%E5%9E%8B%E6%A1%88%E4%BE%8B%E9%9B%86"
            "%EF%BC%88%E7%AC%AC%E4%BA%8C%E6%89%B9%EF%BC%89.pdf"
        )
        result = discover_mohurd_attachment(page, f'<a href="{href}">附件</a>'.encode())
        self.assertEqual(urllib.parse.urlsplit(result).hostname, "www.mohurd.gov.cn")
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result).query))["fileName"], MOHURD_ATTACHMENT_NAME)

    def test_wrong_host_or_filename_is_rejected(self) -> None:
        page = "https://www.mohurd.gov.cn/gongkai/example.html"
        bad = b'<a href="https://evil.test/api-gateway/jpaas-web-server/front/document/download?fileUrl=x&fileName=other.pdf">x</a>'
        with self.assertRaises(AttachmentDiscoveryError):
            discover_mohurd_attachment(page, bad)

    def test_mohurd_gateway_redirect_is_shape_checked_allowlisted_and_robots_checked(self) -> None:
        gateway = (
            "https://www.mohurd.gov.cn/api-gateway/jpaas-web-server/front/document/download?"
            "fileUrl=opaque&fileName="
            "%E5%9F%8E%E5%B8%82%E6%9B%B4%E6%96%B0%E5%85%B8%E5%9E%8B%E6%A1%88%E4%BE%8B%E9%9B%86"
            "%EF%BC%88%E7%AC%AC%E4%BA%8C%E6%89%B9%EF%BC%89.pdf"
        )
        final = urllib.parse.urlunsplit(
            (
                "https",
                "www.mohurd.gov.cn",
                MOHURD_FINAL_PATH,
                urllib.parse.urlencode({"fileName": MOHURD_FINAL_FILENAME}),
                "",
            )
        )
        robots = "https://www.mohurd.gov.cn/robots.txt"
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(
                {
                    robots: [FakeResponse(404)],
                    gateway: [FakeResponse(302, headers={"Location": final})],
                    final: [FakeResponse(200, b"%PDF-test")],
                }
            )
            archiver = OpenSourceArchiver(
                FetchConfig(Path(tmp), rate=0, retries=0),
                [_test_spec(gateway, source_format="pdf", access_scope="full_document_public")],
                session=session,  # type: ignore[arg-type]
            )
            checked: list[str] = []
            real_assert = archiver.robots.assert_allowed

            def record_and_assert(url: str) -> None:
                checked.append(url)
                real_assert(url)

            archiver.robots.assert_allowed = record_and_assert  # type: ignore[method-assign]
            result = archiver._fetch_allowlisted(
                gateway,
                {gateway},
                stream=False,
                redirect_allowlist_extender=validate_mohurd_attachment_redirect,
            )
            self.assertEqual(result.final_url, final)
            self.assertEqual(checked, [gateway, final])
            self.assertEqual([call["url"] for call in session.calls], [robots, gateway, final])
            self.assertEqual(result.runtime_allowlist_extensions[0]["url"], final)
            result.response.close()

    def test_mohurd_redirect_rejects_duplicate_or_wrong_filename(self) -> None:
        gateway = "https://www.mohurd.gov.cn/api-gateway/jpaas-web-server/front/document/download?fileUrl=x&fileName=y"
        for query in (
            "fileName=wrong",
            urllib.parse.urlencode(
                [("fileName", MOHURD_FINAL_FILENAME), ("fileName", MOHURD_FINAL_FILENAME)]
            ),
        ):
            target = urllib.parse.urlunsplit(
                ("https", "www.mohurd.gov.cn", MOHURD_FINAL_PATH, query, "")
            )
            with self.subTest(query=query), self.assertRaises(RedirectPolicyError):
                validate_mohurd_attachment_redirect(gateway, target)


class PDFValidationAndResumeTests(unittest.TestCase):
    @staticmethod
    def make_pdf() -> bytes:
        from pypdf import PdfWriter

        output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(output)
        return output.getvalue()

    def test_strict_pdf_validation_and_truncation_detection(self) -> None:
        pdf = self.make_pdf()
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.pdf"
            bad = Path(tmp) / "bad.pdf"
            good.write_bytes(pdf)
            bad.write_bytes(pdf[: len(pdf) // 2])
            info = validate_pdf(good)
            self.assertEqual(info["page_count"], 1)
            self.assertEqual(info["sha256"], sha256_bytes(pdf))
            with self.assertRaises(ContentValidationError):
                validate_pdf(bad)

    def test_range_resume_is_validated_then_archived(self) -> None:
        pdf = self.make_pdf()
        source = "https://example.test/report.pdf"
        robots = "https://example.test/robots.txt"
        split = len(pdf) // 2
        response = FakeResponse(
            206,
            pdf[split:],
            {
                "Content-Range": f"bytes {split}-{len(pdf)-1}/{len(pdf)}",
                "Content-Type": "application/pdf",
                "ETag": '"v1"',
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            spec = _test_spec(source, source_format="pdf", access_scope="full_document_public")
            session = FakeSession({robots: [FakeResponse(404)], source: [response]})
            archiver = OpenSourceArchiver(
                FetchConfig(output, rate=0, retries=0), [spec], session=session  # type: ignore[arg-type]
            )
            archiver._partial_path(spec.doc_id).write_bytes(pdf[:split])
            atomic_write_json(
                archiver._partial_meta_path(spec.doc_id),
                {
                    "catalogue_sha256": archiver.fingerprint,
                    "doc_id": spec.doc_id,
                    "source_url": source,
                    "etag": '"v1"',
                },
            )
            result = archiver._download_pdf(spec, source, {source})
            self.assertEqual(result["sha256"], sha256_bytes(pdf))
            self.assertTrue((output / result["artifact_path"]).is_file())
            content_call = session.calls[-1]
            self.assertEqual(content_call["headers"]["Range"], f"bytes={split}-")

    def test_416_for_complete_partial_is_validated_without_redownload(self) -> None:
        pdf = self.make_pdf()
        source = "https://example.test/report.pdf"
        robots = "https://example.test/robots.txt"
        response = FakeResponse(
            416,
            b"",
            {
                "Content-Range": f"bytes */{len(pdf)}",
                "Content-Type": "application/pdf",
                "ETag": '"v1"',
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            spec = _test_spec(source, source_format="pdf", access_scope="full_document_public")
            session = FakeSession({robots: [FakeResponse(404)], source: [response]})
            archiver = OpenSourceArchiver(
                FetchConfig(output, rate=0, retries=0), [spec], session=session  # type: ignore[arg-type]
            )
            archiver._partial_path(spec.doc_id).write_bytes(pdf)
            atomic_write_json(
                archiver._partial_meta_path(spec.doc_id),
                {
                    "catalogue_sha256": archiver.fingerprint,
                    "doc_id": spec.doc_id,
                    "source_url": source,
                    "etag": '"v1"',
                },
            )
            result = archiver._download_pdf(spec, source, {source})
            self.assertEqual(result["sha256"], sha256_bytes(pdf))
            self.assertEqual(session.calls[-1]["headers"]["Range"], f"bytes={len(pdf)}-")


class HTMLArchiveTests(unittest.TestCase):
    def test_html_is_saved_as_reproducible_gzip_and_manifest_keeps_scope(self) -> None:
        source = "https://example.test/summary"
        robots = "https://example.test/robots.txt"
        body = b"<!doctype html><html><title>Public summary</title></html>"
        with tempfile.TemporaryDirectory() as tmp:
            spec = _test_spec(source)
            session = FakeSession(
                {
                    robots: [FakeResponse(404)],
                    source: [FakeResponse(200, body, {"Content-Type": "text/html"})],
                }
            )
            archiver = OpenSourceArchiver(
                FetchConfig(Path(tmp), rate=0, retries=0), [spec], session=session  # type: ignore[arg-type]
            )
            state = archiver.fetch_one(spec)
            artifact = Path(tmp) / state["artifact_path"]
            self.assertEqual(gzip.decompress(artifact.read_bytes()), body)
            self.assertEqual(state["access_scope"], "summary")
            self.assertEqual(state["claim"], "public landing/summary HTML only; no full document claimed")
            manifest = json.loads((Path(tmp) / "manifest.jsonl").read_text("utf-8"))
            self.assertEqual(manifest["publisher"], "Test Publisher")
            self.assertIn("copyright_notice", manifest)

    def test_cbre_excluded_pdfs_are_never_requested_and_status_is_summary_fallback(self) -> None:
        specs = [item for item in CATALOG if item.doc_id in {227395, 227170}]
        robots = "https://www.cbre.com.cn/robots.txt"
        body = b"<!doctype html><html><title>Official CBRE summary</title></html>"
        routes: dict[str, list[FakeResponse]] = {robots: [FakeResponse(404)]}
        for spec in specs:
            routes[spec.source_url] = [FakeResponse(200, body, {"Content-Type": "text/html"})]
        with tempfile.TemporaryDirectory() as tmp:
            session = FakeSession(routes)
            archiver = OpenSourceArchiver(
                FetchConfig(Path(tmp), rate=0, retries=0), specs, session=session  # type: ignore[arg-type]
            )
            states = [archiver.fetch_one(spec) for spec in specs]
            requested = {call["url"] for call in session.calls}
            for spec, state in zip(specs, states, strict=True):
                self.assertEqual(state["status"], "summary_fallback")
                self.assertEqual(state["actual_access_scope"], "summary_fallback")
                self.assertIn("summary_fallback", state["artifact_path"])
                self.assertTrue(requested.isdisjoint(spec.excluded_urls))
                self.assertIn("excluded PDF was not requested", state["claim"])


class PerItemFingerprintTests(unittest.TestCase):
    def test_shclearing_correction_invalidates_only_224696_item_fingerprint(self) -> None:
        current_by_id = {item.doc_id: item for item in CATALOG}
        previous_224696 = dataclasses.replace(
            current_by_id[224696],
            source_url=(
                "https://www.shclearing.com.cn/wcm/shch/pages/client/download/download.jsp?"
                "FileName=P020240830398850313960.pdf"
            ),
            publisher="金地（集团）股份有限公司／上海清算所",
            notes="",
            excluded_urls=(),
        )
        previous_catalogue = tuple(
            previous_224696 if item.doc_id == 224696 else item for item in CATALOG
        )
        previous_states = {
            item.doc_id: {"item_sha256": item_fingerprint(item)} for item in previous_catalogue
        }
        invalidated = [
            item.doc_id
            for item in CATALOG
            if not state_matches_spec(previous_states[item.doc_id], item)
        ]
        self.assertEqual(invalidated, [224696])

    def test_catalogue_change_preserves_unchanged_legacy_archive_only(self) -> None:
        first = _test_spec("https://one.test/summary")
        second = dataclasses.replace(
            first,
            doc_id=999002,
            source_url="https://two.test/summary",
        )
        body = b"<!doctype html><html><title>kept</title></html>"
        digest = sha256_bytes(body)
        relative = Path("raw") / "html" / "summary" / str(first.doc_id) / f"{digest}.html.gz"

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            old = OpenSourceArchiver(
                FetchConfig(output, rate=0, retries=0),
                [first, second],
                session=FakeSession({}),  # type: ignore[arg-type]
            )
            atomic_write_bytes(output / relative, deterministic_gzip(body))
            atomic_write_json(
                old._state_path(first.doc_id),
                {
                    **first.legacy_state_base(),
                    "catalogue_sha256": old.fingerprint,
                    "status": "archived",
                    "artifact_kind": "html_gzip_raw",
                    "artifact_path": relative.as_posix(),
                    "sha256": digest,
                    "byte_size": len(body),
                },
            )
            atomic_write_json(
                old._state_path(second.doc_id),
                {
                    **second.legacy_state_base(),
                    "catalogue_sha256": old.fingerprint,
                    "status": "blocked",
                },
            )

            changed_second = dataclasses.replace(second, source_url="https://two.test/new-summary")
            current = OpenSourceArchiver(
                FetchConfig(output, rate=0, retries=0),
                [first, changed_second],
                session=FakeSession({}),  # type: ignore[arg-type]
            )
            self.assertNotEqual(old.fingerprint, current.fingerprint)
            self.assertEqual(current._read_state(first.doc_id)["status"], "archived")
            self.assertEqual(current._read_state(changed_second.doc_id), {})

            # fetch_one performs only local verification, then migrates the
            # retained v1 state to a per-item fingerprint without any request.
            migrated = current.fetch_one(first)
            self.assertEqual(migrated["item_sha256"], item_fingerprint(first))
            self.assertEqual(migrated["catalogue_sha256"], current.fingerprint)
            self.assertEqual(migrated["status"], "archived")


def parse_only(value: str | None) -> set[int] | None:
    if value is None or not value.strip():
        return None
    try:
        return {int(part.strip()) for part in value.split(",") if part.strip()}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--only must be comma-separated integer doc_id values") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="show the fixed audited catalogue; no network")
    list_parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    for name, help_text in (
        ("fetch", "archive selected public sources"),
        ("verify", "verify already archived local artifacts; no network"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
        command.add_argument("--only", help="comma-separated DCBBS doc_id values")
        if name == "fetch":
            command.add_argument("--rate", type=float, default=0.5, help="maximum requests/second/origin")
            command.add_argument("--retries", type=int, default=3)
            command.add_argument("--timeout", type=float, default=30.0)

    subparsers.add_parser("self-test", help="run embedded unittest suite with mocked network")
    return parser


def run_self_tests() -> int:
    names = [
        CatalogueTests,
        URLPolicyTests,
        RobotsTests,
        AttachmentDiscoveryTests,
        PDFValidationAndResumeTests,
        HTMLArchiveTests,
        PerItemFingerprintTests,
    ]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "self-test":
        return run_self_tests()
    if args.command == "list":
        if args.json:
            print(json.dumps([dataclasses.asdict(item) for item in CATALOG], ensure_ascii=False, indent=2))
        else:
            for item in CATALOG:
                print(
                    f"{item.doc_id}\t{item.match_quality}\t{item.access_scope}\t"
                    f"{item.publisher}\t{item.source_url}"
                )
        return 0

    only = parse_only(args.only)
    config = FetchConfig(
        output=args.output,
        rate=getattr(args, "rate", 0.0),
        retries=getattr(args, "retries", 0),
        timeout=getattr(args, "timeout", 30.0),
    )
    archiver = OpenSourceArchiver(config)
    if args.command == "fetch":
        results = archiver.fetch(only)
        counts: dict[str, int] = {}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        print(json.dumps({"manifest": str(archiver.rebuild_manifest()), "counts": counts}, ensure_ascii=False))
        return 1 if counts.get("error") or counts.get("blocked") else 0
    results = archiver.verify(only)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any(item["status"] == "invalid" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

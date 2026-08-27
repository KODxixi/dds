#!/usr/bin/env python3
"""Archive allowlisted external images embedded in DCBBS news bodies.

This is deliberately separate from ``scrape_dcbbs.py``.  It opens the main
SQLite archive in read-only mode, never writes to its schema, and treats every
external image as third-party material restricted to internal research use.
Only exact URLs already present in ``news.images_json`` are candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from PIL import Image, UnidentifiedImageError


SCHEMA_VERSION = 1
MAX_MEDIA_BYTES = 10 * 1024 * 1024
# RFC 9309 requires crawlers to parse at least 500 KiB of robots content.
MAX_ROBOTS_BYTES = 512 * 1024
PRODUCT_TOKEN = "DDS-Research-Archive"
USER_AGENT = (
    "Mozilla/5.0 (compatible; DDS-Research-Archive/1.0; "
    "+https://www.dcbbs.com/robots.txt)"
)
USAGE_SCOPE = "restricted/internal-research"
RIGHTS_BOUNDARY = (
    "Third-party embedded images remain subject to their respective rightsholders; "
    "archive only for restricted internal research, with no public redistribution "
    "and no commercial reuse without permission."
)
ROBOTS_STANDARD = "https://www.rfc-editor.org/rfc/rfc9309.html"

QPIC_HOSTS = frozenset({"mmecoa.qpic.cn", "mmbiz.qpic.cn"})
WECHAT_ASSET_HOST = "res.wx.qq.com"
ALLOWLISTED_HOSTS = frozenset((*QPIC_HOSTS, WECHAT_ASSET_HOST))
EXCLUDED_HOSTS = {"wx.qlogo.cn": "profile/avatar and personal-data minimization"}

QPIC_PATH_RE = re.compile(
    r"^/(?:"
    r"(?:sz_)?(?:mmecoa|mmbiz)_(?:jpg|png|gif|svg)/[^/]+/640"
    r"|(?:sz_)?(?:mmecoa|mmbiz)_(?:jpg|png|gif|svg)/[^/]+\.(?:jpe?g|png|gif|webp)"
    r"|(?:mmecoa|mmbiz)/[^/]+/640"
    r")$",
    re.IGNORECASE,
)
WECHAT_EMOJI_PATH_RE = re.compile(
    r"^/t/wx_fed/we-emoji/res/(?:v\d+(?:\.\d+)*/)?assets/"
    r"(?:Expression|newemoji)/[^/]+\.png$",
    re.IGNORECASE,
)
ROBOTS_RULE_RE = re.compile(r"^\s*(?:user-agent|allow|disallow)\s*:", re.IGNORECASE | re.MULTILINE)


class UrlNotAllowedError(ValueError):
    """Raised when a URL is outside the exact external-media allowlist."""


class MediaValidationError(ValueError):
    """Raised when fetched bytes are not a completely decodable safe image."""


class DownloadError(RuntimeError):
    """Raised when a response violates an HTTP or size boundary."""


@dataclass(frozen=True)
class MediaReference:
    article_id: int
    image_index: int
    original_url: str


@dataclass
class MediaTask:
    canonical_url: str
    host: str
    references: list[MediaReference]


@dataclass(frozen=True)
class MediaInfo:
    format: str
    extension: str
    frame_count: int
    width: int | None
    height: int | None


@dataclass(frozen=True)
class DownloadPayload:
    content: bytes
    content_type: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonicalize_url(raw_url: str) -> str:
    """Validate an embedded URL, remove only its fragment, and retain its query."""

    if not isinstance(raw_url, str) or not raw_url.strip():
        raise UrlNotAllowedError("URL must be a non-empty string")
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme.lower() != "https":
        raise UrlNotAllowedError("only HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UrlNotAllowedError("userinfo is not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlNotAllowedError("invalid port") from exc
    if port is not None:
        raise UrlNotAllowedError("explicit ports are not allowed")

    host = (parsed.hostname or "").lower()
    if parsed.netloc.lower() != host or host not in ALLOWLISTED_HOSTS:
        raise UrlNotAllowedError(f"host is not allowlisted: {parsed.netloc}")

    decoded_path = unquote(parsed.path)
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        raise UrlNotAllowedError("dot path segments are not allowed")
    if host in QPIC_HOSTS:
        allowed_path = bool(QPIC_PATH_RE.fullmatch(decoded_path))
    else:
        allowed_path = bool(WECHAT_EMOJI_PATH_RE.fullmatch(decoded_path))
    if not allowed_path:
        raise UrlNotAllowedError(f"path is not allowlisted for {host}: {parsed.path}")

    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=30)
    connection.execute("PRAGMA query_only=ON")
    return connection


def collect_media_tasks(
    db_path: Path | str,
) -> tuple[list[MediaTask], dict[str, int], dict[str, Any]]:
    """Read ``news.images_json`` without mutating the source database."""

    path = Path(db_path)
    references: dict[str, list[MediaReference]] = {}
    exclusions = {
        "dcbbs_internal": 0,
        "explicitly_excluded_host": 0,
        "not_allowlisted": 0,
        "invalid_images_json": 0,
        "non_string_url": 0,
    }
    articles_scanned = 0
    image_references = 0
    allowed_references = 0
    min_article_id: int | None = None
    max_article_id: int | None = None

    connection = _read_only_connection(path)
    try:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(news)")}
        if not {"article_id", "images_json"}.issubset(columns):
            raise RuntimeError("source database is missing news.article_id/images_json")
        for article_id, images_json in connection.execute(
            "SELECT article_id,images_json FROM news ORDER BY article_id"
        ):
            article_id = int(article_id)
            articles_scanned += 1
            min_article_id = article_id if min_article_id is None else min(min_article_id, article_id)
            max_article_id = article_id if max_article_id is None else max(max_article_id, article_id)
            try:
                urls = json.loads(images_json or "[]")
            except (TypeError, json.JSONDecodeError):
                exclusions["invalid_images_json"] += 1
                continue
            if not isinstance(urls, list):
                exclusions["invalid_images_json"] += 1
                continue
            for image_index, original_url in enumerate(urls, start=1):
                image_references += 1
                if not isinstance(original_url, str):
                    exclusions["non_string_url"] += 1
                    continue
                source_host = (urlsplit(original_url).hostname or "").lower()
                if source_host in {"www.dcbbs.com", "m.dcbbs.com"}:
                    exclusions["dcbbs_internal"] += 1
                    continue
                if source_host in EXCLUDED_HOSTS:
                    exclusions["explicitly_excluded_host"] += 1
                    continue
                try:
                    canonical_url = canonicalize_url(original_url)
                except UrlNotAllowedError:
                    exclusions["not_allowlisted"] += 1
                    continue
                allowed_references += 1
                references.setdefault(canonical_url, []).append(
                    MediaReference(article_id, image_index, original_url)
                )
    finally:
        connection.close()

    tasks = [
        MediaTask(url, urlsplit(url).hostname or "", sorted(items, key=lambda item: (item.article_id, item.image_index)))
        for url, items in sorted(references.items())
    ]
    snapshot = {
        "database": str(path.resolve()),
        "open_mode": "read-only",
        "articles_scanned": articles_scanned,
        "article_id_min": min_article_id,
        "article_id_max": max_article_id,
        "image_references_scanned": image_references,
        "allowlisted_reference_positions": allowed_references,
        "canonical_tasks": len(tasks),
    }
    return tasks, exclusions, snapshot


def _safe_svg_info(content: bytes) -> MediaInfo:
    lowered = content.lower()
    forbidden_tokens = (b"<!doctype", b"<!entity", b"javascript:", b"@import")
    if any(token in lowered for token in forbidden_tokens):
        raise MediaValidationError("SVG contains a forbidden declaration or active reference")
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise MediaValidationError(f"invalid SVG XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise MediaValidationError("XML root is not SVG")

    for node in root.iter():
        local_name = node.tag.rsplit("}", 1)[-1].lower() if isinstance(node.tag, str) else ""
        if local_name in {"script", "foreignobject", "iframe", "object", "embed", "style"}:
            raise MediaValidationError(f"unsafe SVG element: {local_name}")
        for raw_name, raw_value in node.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            value = str(raw_value).strip()
            value_lower = value.lower()
            if name in {"href", "src"} and value and not value.startswith("#"):
                raise MediaValidationError(f"external SVG reference in {name}")
            if any(token in value_lower for token in ("javascript:", "data:", "@import")):
                raise MediaValidationError("unsafe SVG attribute value")
            for target in re.findall(r"url\(\s*([^)]*?)\s*\)", value, flags=re.IGNORECASE):
                cleaned = target.strip().strip("\"'")
                if cleaned and not cleaned.startswith("#"):
                    raise MediaValidationError("external SVG url() reference")

    width = _svg_dimension(root.attrib.get("width"))
    height = _svg_dimension(root.attrib.get("height"))
    return MediaInfo("svg", ".svg", 1, width, height)


def _svg_dimension(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d+)(?:px)?\s*", value, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _magic_format(content: bytes) -> str | None:
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return None


def validate_media_bytes(
    content: bytes,
    content_type: str,
    *,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> MediaInfo:
    """Validate raster magic/full decoding or a deliberately restricted SVG."""

    if not content:
        raise MediaValidationError("empty image response")
    if len(content) > max_bytes:
        raise MediaValidationError(f"image exceeds {max_bytes}-byte size limit")
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized_type.startswith("image/"):
        raise MediaValidationError(f"non-image Content-Type: {content_type}")
    if normalized_type == "image/svg+xml" or content.lstrip().startswith(b"<svg"):
        return _safe_svg_info(content)

    magic = _magic_format(content)
    if magic is None:
        raise MediaValidationError("unrecognized raster image magic")
    try:
        with Image.open(io.BytesIO(content)) as image:
            decoded_format = (image.format or "").lower()
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            decoded_format = (image.format or "").lower()
            width, height = image.size
            frame_count = int(getattr(image, "n_frames", 1))
            for frame_index in range(frame_count):
                image.seek(frame_index)
                image.load()
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError, EOFError, IndexError) as exc:
        raise MediaValidationError(f"raster decode failed: {exc}") from exc
    if decoded_format == "jpg":
        decoded_format = "jpeg"
    if decoded_format != magic:
        raise MediaValidationError(f"image magic/decoder mismatch: {magic} versus {decoded_format}")
    extension = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}[magic]
    return MediaInfo(magic, extension, frame_count, width, height)


def classify_robots_response(
    host: str,
    status_code: int,
    content_type: str,
    content: bytes,
    candidate_urls: Sequence[str],
) -> dict[str, Any]:
    """Classify one robots response using RFC 9309 access-result semantics."""

    base = {
        "host": host,
        "robots_url": f"https://{host}/robots.txt",
        "status_code": status_code,
        "content_type": content_type,
        "checked_at": utc_now(),
        "standard": ROBOTS_STANDARD,
        "note": "robots rules govern crawler access, not copyright or redistribution rights",
    }
    if 400 <= status_code <= 499:
        return {**base, "classification": "unavailable_4xx", "allowed": True, "rules_detected": False}
    if 500 <= status_code <= 599 or status_code <= 0:
        return {**base, "classification": "unreachable_5xx", "allowed": False, "rules_detected": False}
    if 300 <= status_code <= 399:
        return {**base, "classification": "redirect_blocked", "allowed": False, "rules_detected": False}
    if status_code != 200:
        return {**base, "classification": "unexpected_status", "allowed": False, "rules_detected": False}

    text = content.decode("utf-8", errors="replace")
    if not ROBOTS_RULE_RE.search(text):
        return {**base, "classification": "success_no_rules", "allowed": True, "rules_detected": False}
    parser = RobotFileParser()
    parser.set_url(base["robots_url"])
    parser.parse(text.splitlines())
    allowed = all(parser.can_fetch(PRODUCT_TOKEN, url) for url in candidate_urls)
    return {
        **base,
        "classification": "success_allowed" if allowed else "success_disallowed",
        "allowed": allowed,
        "rules_detected": True,
    }


def fetch_robots_status(
    host: str,
    candidate_urls: Sequence[str],
    *,
    session: requests.Session | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch a small robots response without cookies or redirect following."""

    if host not in ALLOWLISTED_HOSTS:
        raise UrlNotAllowedError(f"robots host is not allowlisted: {host}")
    owned_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"})
    response: requests.Response | None = None
    try:
        response = session.get(
            f"https://{host}/robots.txt",
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        body = bytearray()
        for chunk in response.iter_content(8192):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > MAX_ROBOTS_BYTES:
                return {
                    "host": host,
                    "robots_url": f"https://{host}/robots.txt",
                    "status_code": response.status_code,
                    "content_type": response.headers.get("Content-Type", ""),
                    "classification": "response_too_large",
                    "allowed": False,
                    "rules_detected": False,
                    "checked_at": utc_now(),
                    "standard": ROBOTS_STANDARD,
                    "note": "robots response exceeded the conservative read cap",
                }
        return classify_robots_response(
            host,
            int(response.status_code),
            response.headers.get("Content-Type", ""),
            bytes(body),
            candidate_urls,
        )
    except requests.RequestException as exc:
        return {
            "host": host,
            "robots_url": f"https://{host}/robots.txt",
            "status_code": 0,
            "content_type": "",
            "classification": "network_error",
            "allowed": False,
            "rules_detected": False,
            "checked_at": utc_now(),
            "standard": ROBOTS_STANDARD,
            "note": f"robots fetch failed conservatively: {type(exc).__name__}: {exc}",
        }
    finally:
        if response is not None:
            response.close()
        if owned_session:
            session.close()


def download_url(
    session: Any,
    url: str,
    *,
    max_bytes: int = MAX_MEDIA_BYTES,
    timeout: float = 30.0,
) -> DownloadPayload:
    """GET one allowlisted URL with redirect and streaming-size boundaries."""

    canonical_url = canonicalize_url(url)
    response = session.get(
        canonical_url,
        timeout=timeout,
        allow_redirects=False,
        stream=True,
    )
    try:
        status = int(response.status_code)
        if 300 <= status <= 399:
            raise DownloadError(
                f"redirect blocked: HTTP {status} -> {response.headers.get('Location', '(missing Location)')}"
            )
        if status != 200:
            raise DownloadError(f"strict HTTP 200 required; received {status}")
        content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise DownloadError(f"non-image Content-Type: {content_type or '(missing)'}")
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise DownloadError(f"declared body exceeds {max_bytes}-byte size limit")
            except ValueError as exc:
                raise DownloadError(f"invalid Content-Length: {declared}") from exc
        body = bytearray()
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > max_bytes:
                raise DownloadError(f"streamed body exceeds {max_bytes}-byte size limit")
        payload = DownloadPayload(bytes(body), content_type)
        validate_media_bytes(payload.content, payload.content_type, max_bytes=max_bytes)
        return payload
    finally:
        response.close()


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("rate must be positive")
        self.interval = 1.0 / requests_per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)


class HttpMediaFetcher:
    def __init__(self, rate: float, timeout: float, retries: int = 3) -> None:
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
                    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.4",
                }
            )
            self.local.session = session
        return session

    def __call__(self, task: MediaTask) -> DownloadPayload:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                return download_url(self._session(), task.canonical_url, timeout=self.timeout)
            except (requests.RequestException, DownloadError, MediaValidationError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(30.0, (2**attempt) + random.uniform(0.2, 0.9)))
        raise DownloadError(f"download failed after {self.retries + 1} attempts: {last_error}")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.part")
    temporary.write_bytes(content)
    temporary.replace(path)


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def load_jsonl(path: Path | str) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    records: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL at {source}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise RuntimeError(f"JSONL record at {source}:{line_number} is not an object")
            records.append(record)
    return records


def write_jsonl(path: Path | str, records: Iterable[Mapping[str, Any]]) -> None:
    ordered = sorted(records, key=lambda item: str(item.get("canonical_url", "")))
    text = "".join(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n" for record in ordered)
    _atomic_write_text(Path(path), text)


def _safe_local_path(output_root: Path, local_path: Any) -> Path | None:
    if not isinstance(local_path, str) or not local_path:
        return None
    relative = Path(local_path)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        return None
    root = output_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def stored_record_matches(
    output_root: Path | str,
    record: Mapping[str, Any],
    expected_url: str,
) -> bool:
    """Verify URL, path, size, SHA-256, media type, and complete decoding."""

    try:
        canonical_url = canonicalize_url(expected_url)
        if record.get("status") != "ok" or record.get("canonical_url") != canonical_url:
            return False
        byte_count = int(record.get("byte_count"))
        digest = str(record.get("sha256"))
        content_type = str(record.get("content_type"))
        detected_format = str(record.get("detected_format"))
    except (TypeError, ValueError, UrlNotAllowedError):
        return False
    path = _safe_local_path(Path(output_root), record.get("local_path"))
    if path is None or not path.is_file() or path.is_symlink() or byte_count > MAX_MEDIA_BYTES:
        return False
    try:
        if path.stat().st_size != byte_count:
            return False
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            return False
        info = validate_media_bytes(content, content_type)
    except (OSError, MediaValidationError):
        return False
    return info.format == detected_format


def _record_for_success(
    output_root: Path,
    task: MediaTask,
    payload: DownloadPayload,
) -> dict[str, Any]:
    info = validate_media_bytes(payload.content, payload.content_type)
    url_digest = hashlib.sha256(task.canonical_url.encode("utf-8")).hexdigest()
    path = (
        output_root
        / "external_news_media"
        / "files"
        / task.host
        / url_digest[:2]
        / f"{url_digest}{info.extension}"
    )
    _atomic_write_bytes(path, payload.content)
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_url": task.canonical_url,
        "url_sha256": url_digest,
        "host": task.host,
        "status": "ok",
        "local_path": path.relative_to(output_root).as_posix(),
        "content_type": payload.content_type,
        "detected_format": info.format,
        "frame_count": info.frame_count,
        "width": info.width,
        "height": info.height,
        "byte_count": len(payload.content),
        "sha256": hashlib.sha256(payload.content).hexdigest(),
        "fetched_at": utc_now(),
        "error": None,
        "references": [asdict(reference) for reference in task.references],
    }


def _record_for_failure(task: MediaTask, status: str, error: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_url": task.canonical_url,
        "url_sha256": hashlib.sha256(task.canonical_url.encode("utf-8")).hexdigest(),
        "host": task.host,
        "status": status,
        "local_path": None,
        "content_type": None,
        "detected_format": None,
        "frame_count": None,
        "width": None,
        "height": None,
        "byte_count": None,
        "sha256": None,
        "fetched_at": utc_now(),
        "error": error,
        "references": [asdict(reference) for reference in task.references],
    }


def _build_manifest(
    output_root: Path,
    snapshot: Mapping[str, Any],
    exclusions: Mapping[str, int],
    robots_statuses: Mapping[str, Mapping[str, Any]],
    records: Mapping[str, Mapping[str, Any]],
    *,
    saved: int,
    resumed: int,
    interrupted: bool,
) -> dict[str, Any]:
    statuses = {str(record.get("status")): 0 for record in records.values()}
    for record in records.values():
        status = str(record.get("status"))
        statuses[status] = statuses.get(status, 0) + 1
    failed = sum(count for status, count in statuses.items() if status not in {"ok", "robots_blocked"})
    blocked = statuses.get("robots_blocked", 0)
    ok_total = statuses.get("ok", 0)
    archive_status = (
        "interrupted"
        if interrupted
        else "complete_for_allowlisted_scope"
        if failed == 0 and blocked == 0 and ok_total == int(snapshot.get("canonical_tasks", 0))
        else "partial"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "archive_status": archive_status,
        "source": "DCBBS news.images_json external embedded URLs",
        "source_database": dict(snapshot),
        "usage_scope": USAGE_SCOPE,
        "rights_boundary": RIGHTS_BOUNDARY,
        "robots_standard": ROBOTS_STANDARD,
        "robots": {host: dict(value) for host, value in sorted(robots_statuses.items())},
        "allowlist": {
            "exact_hosts": sorted(ALLOWLISTED_HOSTS),
            "qpic_path": QPIC_PATH_RE.pattern,
            "wechat_emoji_path": WECHAT_EMOJI_PATH_RE.pattern,
            "canonicalization": "strip URL fragment only; preserve query exactly",
            "redirects": "blocked",
            "required_status": 200,
            "max_media_bytes": MAX_MEDIA_BYTES,
        },
        "excluded_hosts": EXCLUDED_HOSTS,
        "exclusions": dict(exclusions),
        "counts": {
            "tasks": int(snapshot.get("canonical_tasks", 0)),
            "reference_positions": int(snapshot.get("allowlisted_reference_positions", 0)),
            "ok_total": ok_total,
            "saved": saved,
            "resumed": resumed,
            "failed": failed,
            "robots_blocked": blocked,
            "status_distribution": statuses,
        },
        "artifacts": {
            "media_directory": "external_news_media/files",
            "jsonl": "external_news_media/external_news_media.jsonl",
            "manifest": "external_news_media/manifest.json",
        },
        "validation": {
            "resume_identity": "canonical URL + byte size + SHA-256 + complete decode",
            "raster": "magic signature + Pillow verify + every frame decoded",
            "svg": "XML parse; DOCTYPE, ENTITY, script, active elements, and external references rejected",
        },
        "output_root": str(output_root.resolve()),
    }


def archive_external_media(
    db_path: Path | str,
    output_root: Path | str,
    fetch: Callable[[MediaTask], DownloadPayload],
    robots_statuses: Mapping[str, Mapping[str, Any]],
    *,
    workers: int = 6,
    checkpoint_every: int = 25,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Archive tasks while keeping the source SQLite database read-only."""

    if workers < 1 or checkpoint_every < 1:
        raise ValueError("workers and checkpoint_every must be positive")
    tasks, exclusions, snapshot = collect_media_tasks(Path(db_path))
    if max_items is not None:
        if max_items < 0:
            raise ValueError("max_items must be non-negative")
        tasks = tasks[:max_items]
        snapshot = dict(snapshot)
        snapshot["canonical_tasks_discovered"] = snapshot["canonical_tasks"]
        snapshot["canonical_tasks"] = len(tasks)
        snapshot["allowlisted_reference_positions"] = sum(len(task.references) for task in tasks)

    root = Path(output_root).resolve()
    archive_dir = root / "external_news_media"
    jsonl_path = archive_dir / "external_news_media.jsonl"
    manifest_path = archive_dir / "manifest.json"
    archive_dir.mkdir(parents=True, exist_ok=True)
    existing = {
        str(record.get("canonical_url")): record
        for record in load_jsonl(jsonl_path)
        if record.get("canonical_url")
    }
    records: dict[str, dict[str, Any]] = {}
    queued: list[MediaTask] = []
    resumed = 0
    saved = 0

    for task in tasks:
        previous = existing.get(task.canonical_url)
        if previous and stored_record_matches(root, previous, task.canonical_url):
            record = dict(previous)
            record["references"] = [asdict(reference) for reference in task.references]
            records[task.canonical_url] = record
            resumed += 1
            continue
        robots = robots_statuses.get(task.host)
        if not robots or not bool(robots.get("allowed")):
            records[task.canonical_url] = _record_for_failure(
                task,
                "robots_blocked",
                "host has no current robots status allowing these exact embedded URLs",
            )
            continue
        queued.append(task)

    completed = 0
    interrupted = False
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch, task): task for task in queued}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    payload = future.result()
                    if not isinstance(payload, DownloadPayload):
                        raise TypeError("fetch callable must return DownloadPayload")
                    records[task.canonical_url] = _record_for_success(root, task, payload)
                    saved += 1
                except Exception as exc:  # noqa: BLE001 - each URL must be recorded and resumable
                    records[task.canonical_url] = _record_for_failure(
                        task,
                        "error",
                        f"{type(exc).__name__}: {exc}",
                    )
                completed += 1
                if completed % checkpoint_every == 0:
                    write_jsonl(jsonl_path, records.values())
    except (KeyboardInterrupt, SystemExit):
        interrupted = True
        raise
    finally:
        write_jsonl(jsonl_path, records.values())
        manifest = _build_manifest(
            root,
            snapshot,
            exclusions,
            robots_statuses,
            records,
            saved=saved,
            resumed=resumed,
            interrupted=interrupted,
        )
        _atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive allowlisted external images explicitly embedded in DCBBS news"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data_out/dcbbs/normalized/dcbbs.sqlite3"),
        help="source DCBBS SQLite database; always opened read-only",
    )
    parser.add_argument("--output", type=Path, default=Path("data_out/dcbbs"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--rate", type=float, default=3.0, help="global media requests per second")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--max-items", type=int, help="limit canonical URLs for a smoke run")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="print source/allowlist counts without fetching robots or media",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.workers < 1 or args.rate <= 0 or args.retries < 0 or args.checkpoint_every < 1:
        raise SystemExit("workers/rate/checkpoint must be positive and retries non-negative")
    tasks, exclusions, snapshot = collect_media_tasks(args.db)
    if args.max_items is not None:
        tasks = tasks[: args.max_items]
    if args.plan_only:
        print(
            json.dumps(
                {
                    "source": snapshot,
                    "exclusions": exclusions,
                    "selected_tasks": len(tasks),
                    "selected_references": sum(len(task.references) for task in tasks),
                    "usage_scope": USAGE_SCOPE,
                    "rights_boundary": RIGHTS_BOUNDARY,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    by_host: dict[str, list[str]] = {}
    for task in tasks:
        by_host.setdefault(task.host, []).append(task.canonical_url)
    robots_statuses = {
        host: fetch_robots_status(host, urls, timeout=args.timeout)
        for host, urls in sorted(by_host.items())
    }
    fetcher = HttpMediaFetcher(args.rate, args.timeout, args.retries)
    manifest = archive_external_media(
        args.db,
        args.output,
        fetcher,
        robots_statuses,
        workers=args.workers,
        checkpoint_every=args.checkpoint_every,
        max_items=args.max_items,
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    return 0 if manifest["archive_status"] == "complete_for_allowlisted_scope" else 2


if __name__ == "__main__":
    raise SystemExit(main())

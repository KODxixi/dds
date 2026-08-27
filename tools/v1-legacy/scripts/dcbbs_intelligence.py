"""Read-only DCBBS adapter for DDS professional intelligence.

DCBBS is a discovery corpus, not a transaction or consumer-opinion source.  This
module therefore exposes a deliberately narrow contract:

    SQLite snapshot (read-only)
        -> title-level candidate retrieval
        -> optional original-source verification
        -> professional_intelligence

Unverified DCBBS records always remain L3 leads.  A verified original source may
upgrade only the corresponding claim scope; it never upgrades the aggregator
record itself and never feeds price, absorption, WTP, ROI, IRR, or land-bid
mathematics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "Vault" / "DCBBS" / "normalized" / "dcbbs.sqlite3"
DEFAULT_MANIFEST_PATH = ROOT / "Vault" / "DCBBS" / "manifest_latest.json"
DEFAULT_CATALOG_PATH = ROOT / "Vault" / "DCBBS" / "open_sources" / "catalog.json"

MAX_SELECTED_PER_KIND = 12
MAX_QUERY_TERMS = 10

_RESOURCE_REQUIRED = {
    "doc_id",
    "title",
    "source_url",
    "upload_date",
    "document_format",
    "page_count",
    "description",
    "preview_text",
    "usage_scope",
    "raw_path",
    "fetched_at",
}
_NEWS_REQUIRED = {
    "article_id",
    "title",
    "source_url",
    "published_date",
    "category",
    "author",
    "summary",
    "usage_scope",
    "raw_path",
    "fetched_at",
}

_OFFICIAL_PUBLISHER_MARKERS = (
    "人民政府",
    "住房和城乡建设部",
    "自然资源部",
    "统计局",
    "发展和改革委员会",
    "规划和自然资源",
    "公共资源交易",
)
_STREET_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·]{2,18}?(?:大道|大街|街|路|巷|片区|板块|新城|新区)"
)
_COORDINATE_RE = re.compile(r"^-?\d+(?:\.\d+)?\s*[,，]\s*-?\d+(?:\.\d+)?$")
_SPACE_RE = re.compile(r"\s+")

# Used only to avoid labelling an explicit other-city title as local.  Unknown
# geography remains ``unknown`` rather than being guessed from the corpus.
_COMMON_CITY_NAMES = (
    "北京", "上海", "天津", "重庆", "广州", "深圳", "武汉", "济南", "青岛",
    "杭州", "南京", "苏州", "成都", "西安", "郑州", "长沙", "合肥", "福州",
    "厦门", "宁波", "无锡", "佛山", "东莞", "珠海", "南昌", "南宁", "昆明",
    "贵阳", "海口", "三亚", "沈阳", "大连", "长春", "哈尔滨", "石家庄",
    "太原", "呼和浩特", "兰州", "银川", "西宁", "乌鲁木齐", "徐州", "绍兴",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _clean_text(value: Any, limit: int = 240) -> str:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clean_geo_name(value: Any) -> str:
    text = _clean_text(value, 40)
    return re.sub(r"(?:省|市|区|县)$", "", text).strip()


def _like_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:14]
    return f"{prefix}:{digest}"


def _clamp_limit(value: Any, default: int = 8) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(MAX_SELECTED_PER_KIND, parsed))


def _configured_path(context: Mapping[str, Any], key: str, env_key: str, default: Path) -> Path:
    supplied = context.get(key) or os.getenv(env_key)
    return Path(str(supplied)).expanduser() if supplied else default


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """Open an SQLite file without permitting writes or journal creation."""
    resolved = path.expanduser().resolve()
    uri_path = quote(resolved.as_posix(), safe="/:")
    immutable = str(os.getenv("DDS_DCBBS_IMMUTABLE", "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    uri = f"file:{uri_path}?mode=ro" + ("&immutable=1" if immutable else "")
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _schema_state(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing: dict[str, list[str]] = {}
    for table, required in (
        ("resources", _RESOURCE_REQUIRED),
        ("news", _NEWS_REQUIRED),
    ):
        columns = _table_columns(connection, table) if table in tables else set()
        absent = sorted(required - columns)
        if absent:
            missing[table] = absent
    return {
        "tables": sorted(tables),
        "missing_columns": missing,
        "resources_usable": "resources" in tables and "resources" not in missing,
        "news_usable": "news" in tables and "news" not in missing,
    }


def _query_terms(report: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    parcel = _mapping(report.get("parcel"))
    project = _mapping(report.get("project"))
    weighted: list[tuple[str, int, str]] = []

    def add(value: Any, weight: int, kind: str) -> None:
        text = _clean_text(value, 80)
        if not text or _COORDINATE_RE.match(text):
            return
        if kind in {"city", "district"}:
            normalized = _clean_geo_name(text)
            if normalized:
                weighted.append((normalized, weight, kind))
        elif len(text) >= 2:
            weighted.append((text, weight, kind))

    add(context.get("city") or parcel.get("city") or project.get("city"), 6, "city")
    add(context.get("district") or parcel.get("district") or project.get("district"), 8, "district")
    for key in ("project_name", "developer", "product_type"):
        add(context.get(key) or project.get(key) or parcel.get(key), 7, key)
    for raw in (
        context.get("address"),
        context.get("vision"),
        parcel.get("address"),
        parcel.get("vision"),
    ):
        for token in _STREET_RE.findall(_clean_text(raw, 240)):
            add(token, 10, "place")
    for value in _sequence(context.get("dcbbs_query_terms")):
        add(value, 9, "explicit")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value, weight, kind in weighted:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append({"value": value, "weight": weight, "kind": kind})
        if len(result) >= MAX_QUERY_TERMS:
            break
    return result


def _select_title_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    terms: Sequence[Mapping[str, Any]],
    candidate_limit: int = 100,
) -> list[sqlite3.Row]:
    if not terms:
        return []
    clauses = ["title LIKE ? ESCAPE '\\'" for _ in terms]
    patterns = [f"%{_like_literal(str(term['value']))}%" for term in terms]
    weighted = [
        "CASE WHEN title LIKE ? ESCAPE '\\' THEN "
        f"{max(0, int(term.get('weight') or 0))} ELSE 0 END"
        for term in terms
    ]
    sql = (
        f"SELECT {', '.join(columns)}, ({' + '.join(weighted)}) AS _sql_score "
        f"FROM {table} WHERE "
        + " OR ".join(clauses)
        + " ORDER BY _sql_score DESC, 1 DESC LIMIT ?"
    )
    params = [*patterns, *patterns, max(1, min(200, int(candidate_limit)))]
    return list(connection.execute(sql, params))


def _locality(title: str, city: str, district: str, matched_terms: Sequence[str]) -> str:
    city_core = _clean_geo_name(city)
    district_core = _clean_geo_name(district)
    if district and district in title:
        return "same_district"
    if district_core and city_core and district_core in title and city_core in title:
        return "same_district"
    if city and city in title:
        return "same_city"
    if city_core and city_core in title:
        return "same_city"
    explicit = [name for name in _COMMON_CITY_NAMES if name in title]
    if explicit and city_core and city_core not in explicit:
        return "cross_city"
    if any(term and term in title for term in matched_terms):
        return "place_match"
    return "unknown"


def _rank_row(
    row: Mapping[str, Any],
    terms: Sequence[Mapping[str, Any]],
    city: str,
    district: str,
) -> tuple[int, list[str], str]:
    title = str(row.get("title") or "")
    matched = [str(term["value"]) for term in terms if str(term["value"]) in title]
    score = sum(
        int(term.get("weight") or 0)
        for term in terms
        if str(term.get("value") or "") in title
    )
    normalized_title = re.sub(r"[\s._-]+", "", title).casefold()
    for term in terms:
        normalized_term = re.sub(r"[\s._-]+", "", str(term.get("value") or "")).casefold()
        if normalized_term and normalized_title == normalized_term:
            score += 20
    locality = _locality(title, city, district, matched)
    score += {"same_district": 18, "same_city": 12, "place_match": 8}.get(locality, 0)
    return score, matched, locality


def _load_catalog(path: Path) -> dict[int, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for item in _sequence(_mapping(payload).get("items")):
        if not isinstance(item, Mapping):
            continue
        try:
            doc_id = int(item.get("doc_id"))
        except (TypeError, ValueError):
            continue
        result[doc_id] = dict(item)
    return result


def _db_origin_matches(
    connection: sqlite3.Connection, doc_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    if not doc_ids or "open_source_matches" not in {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        return {}
    columns = _table_columns(connection, "open_source_matches")
    required = {"doc_id", "source_url", "publisher", "match_type", "source_scope"}
    if not required.issubset(columns):
        return {}
    placeholders = ",".join("?" for _ in doc_ids)
    rows = connection.execute(
        "SELECT doc_id,source_url,publisher,match_type,source_scope "
        f"FROM open_source_matches WHERE doc_id IN ({placeholders})",
        list(doc_ids),
    )
    result: dict[int, dict[str, Any]] = {}
    priority = {"exact": 3, "high": 2, "high_confidence": 2, "context_only": 1}
    for row in rows:
        item = dict(row)
        doc_id = int(item["doc_id"])
        current = result.get(doc_id)
        if not current or priority.get(str(item.get("match_type")), 0) > priority.get(
            str(current.get("match_type")), 0
        ):
            result[doc_id] = item
    return result


def _origin_tier(origin: Mapping[str, Any]) -> tuple[str, float, str]:
    match_type = str(origin.get("match_quality") or origin.get("match_type") or "").lower()
    if match_type == "context_only":
        return "L3", 0.25, "context_only"
    if match_type not in {"exact", "high", "high_confidence"}:
        return "L3", 0.35, "discovery_only"
    publisher = str(origin.get("publisher") or "")
    host = urlparse(str(origin.get("source_url") or "")).hostname or ""
    if host.endswith(".gov.cn") or any(marker in publisher for marker in _OFFICIAL_PUBLISHER_MARKERS):
        return "L1", 0.85, "claim_scope_only"
    return "L2", 0.70, "claim_scope_only"


def _resource_item(
    row: Mapping[str, Any],
    score: int,
    matched_terms: Sequence[str],
    locality: str,
    origin: Mapping[str, Any] | None,
) -> dict[str, Any]:
    doc_id = int(row["doc_id"])
    source_id = f"dcbbs:resource:{doc_id}"
    item: dict[str, Any] = {
        "source_id": source_id,
        "record_kind": "resource",
        "title": _clean_text(row.get("title"), 220),
        "summary": _clean_text(row.get("description") or row.get("preview_text"), 220),
        "repository_upload_date": row.get("upload_date"),
        "document_format": row.get("document_format"),
        "page_count": row.get("page_count"),
        "dcbbs_url": row.get("source_url"),
        "captured_at": row.get("fetched_at"),
        "matched_terms": list(matched_terms),
        "locality": locality,
        "geography_method": "title_inferred" if locality != "unknown" else "unknown",
        "retrieval_score": score,
        "provenance_state": "aggregator_only",
        "trust_tier": "L3",
        "decision_eligibility": "mechanism_only",
        "rights_status": row.get("usage_scope") or "restricted/internal-research",
        "export_policy": "citation_only",
        "limitations": [
            "库内上传日期不等于报告发布日。",
            "不得用本记录直接证明售价、去化、成本、收益或拿地边界。",
        ],
    }
    if origin:
        tier, confidence, eligibility = _origin_tier(origin)
        origin_id = f"dcbbs:origin:{doc_id}"
        item["provenance_state"] = str(
            origin.get("match_quality") or origin.get("match_type") or "matched"
        )
        item["original_source"] = {
            "source_id": origin_id,
            "source_url": origin.get("source_url"),
            "publisher": origin.get("publisher"),
            "trust_tier": tier,
            "confidence": {"score": confidence},
            "decision_eligibility": eligibility,
            "source_scope": origin.get("source_scope") or origin.get("access_scope"),
            "note": origin.get("notes") or "",
        }
    return item


def _news_item(
    row: Mapping[str, Any],
    score: int,
    matched_terms: Sequence[str],
    locality: str,
) -> dict[str, Any]:
    article_id = int(row["article_id"])
    return {
        "source_id": f"dcbbs:news:{article_id}",
        "record_kind": "news",
        "title": _clean_text(row.get("title"), 220),
        "summary": _clean_text(row.get("summary"), 220),
        "published_date": row.get("published_date"),
        "category": row.get("category"),
        "author_label": row.get("author"),
        "dcbbs_url": row.get("source_url"),
        "captured_at": row.get("fetched_at"),
        "matched_terms": list(matched_terms),
        "locality": locality,
        "geography_method": "title_inferred" if locality != "unknown" else "unknown",
        "retrieval_score": score,
        "provenance_state": "aggregator_only",
        "trust_tier": "L3",
        "author_type": "media_aggregator",
        "eligible_for_social_intelligence": False,
        "decision_eligibility": "context_only",
        "rights_status": row.get("usage_scope") or "restricted/internal-research",
        "export_policy": "citation_only",
        "limitations": [
            "聚合媒体内容不代表真实购房者意见或跨平台共识。",
            "具体事实须回到政府、开发商或原始媒体页面复核。",
        ],
    }


def _source_registry(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = [
        {
            "source_id": "dcbbs:archive",
            "name": "DCBBS 地产专业情报档案",
            "source_type": "professional_intelligence_archive",
            "source_url": "https://www.dcbbs.com/",
            "trust_tier": "L3",
            "used_for": "专业资料发现、设计机制候选、市场事件线索与原始来源追索",
            "rights_status": "restricted/internal-research",
            "limitations": "不直接证明售价、去化、库存、成本、ROI、IRR、拿地价或C端共识。",
        }
    ]
    for item in items:
        registry.append(
            {
                "source_id": item.get("source_id"),
                "name": item.get("title"),
                "source_type": f"dcbbs_{item.get('record_kind')}",
                "source_url": item.get("dcbbs_url"),
                "trust_tier": "L3",
                "used_for": item.get("decision_eligibility"),
                "rights_status": item.get("rights_status"),
                "limitations": "citation_only; aggregator record remains L3",
            }
        )
        origin = _mapping(item.get("original_source"))
        if origin.get("source_id"):
            registry.append(
                {
                    "source_id": origin.get("source_id"),
                    "name": f"原始来源 · {origin.get('publisher') or item.get('title')}",
                    "source_type": "verified_original_source",
                    "source_url": origin.get("source_url"),
                    "trust_tier": origin.get("trust_tier"),
                    "used_for": origin.get("source_scope") or "对应字段或主张的条件性核验",
                    "limitations": "仅原始来源已核验的claim scope可升档，DCBBS记录本身仍为L3。",
                }
            )
    return registry


def _evidence_nodes(
    items: Sequence[Mapping[str, Any]], query_terms: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    key = "|".join(str(item.get("value") or "") for item in query_terms)
    nodes: list[dict[str, Any]] = [
        {
            "evidence_id": _stable_id("dcbbs:retrieval", key or "empty"),
            "claim": f"DCBBS 标题检索返回 {len(items)} 条专业资料或媒体候选；仅用于发现与机制参考。",
            "evidence_type": "analysis_inference",
            "source_refs": ["dcbbs:archive"],
            "confidence": {"score": 0.35, "level": "low", "actionable": False},
            "decision_eligibility": "discovery_only",
            "needs_human_review": True,
            "supports_roles": ["case_benchmark_agent", "market_value_agent", "risk_trust_agent"],
        }
    ]
    for item in items:
        origin = _mapping(item.get("original_source"))
        if not origin or origin.get("trust_tier") not in {"L1", "L2"}:
            continue
        nodes.append(
            {
                "evidence_id": f"{origin['source_id']}:verification",
                "claim": f"已为《{item.get('title')}》定位到可核验原始来源；升档仅限已声明的来源范围。",
                "evidence_type": "observed_fact",
                "source_refs": [origin["source_id"], item.get("source_id")],
                "confidence": origin.get("confidence") or {"score": 0.55},
                "decision_eligibility": "claim_scope_only",
                "needs_human_review": origin.get("trust_tier") != "L1",
                "supports_roles": ["case_benchmark_agent", "market_value_agent", "risk_trust_agent"],
            }
        )
    return nodes


def _manifest_health(path: Path, counts: Mapping[str, int]) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"status": "missing", "count_mismatch": True}
    declared = _mapping(manifest.get("counts"))
    resources = declared.get("resources")
    news = declared.get("news")
    mismatch = resources != counts.get("resources") or news != counts.get("news")
    return {
        "status": "stale" if mismatch else "current",
        "generated_at": manifest.get("generated_at"),
        "declared_counts": {"resources": resources, "news": news},
        "count_mismatch": mismatch,
    }


def build_dcbbs_professional_intelligence(
    report: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fail-soft, publication-safe professional-intelligence packet."""
    context = dict(context or {})
    terms = _query_terms(report, context)
    query_view = [{"value": item["value"], "kind": item["kind"]} for item in terms]
    if str(context.get("dcbbs_mode") or "auto").lower() == "off":
        return {
            "status": "disabled",
            "providers": ["dcbbs"],
            "query": {"terms": query_view, "mode": "title_only"},
            "items": [],
            "evidence_nodes": [],
            "source_registry": [],
            "evidence_gaps": ["dcbbs_disabled_by_request"],
        }

    db_path = _configured_path(context, "dcbbs_db_path", "DDS_DCBBS_DB_PATH", DEFAULT_DB_PATH)
    manifest_path = _configured_path(
        context, "dcbbs_manifest_path", "DDS_DCBBS_MANIFEST_PATH", DEFAULT_MANIFEST_PATH
    )
    catalog_path = _configured_path(
        context, "dcbbs_catalog_path", "DDS_DCBBS_CATALOG_PATH", DEFAULT_CATALOG_PATH
    )
    base = {
        "providers": ["dcbbs"],
        "query": {"terms": query_view, "mode": "title_only"},
        "items": [],
        "evidence_nodes": [],
        "source_registry": [],
        "evidence_gaps": [],
        "decision_boundaries": [
            "DCBBS records never enter social_intelligence or market_sentiment.",
            "Unverified DCBBS numbers never enter price, absorption, WTP, ROI, IRR, or land-bid models.",
            "Restricted previews and media are citation-only and are not embedded in sendable reports.",
        ],
    }
    if not db_path.is_file():
        return {
            **base,
            "status": "missing",
            "reason_code": "dcbbs_database_missing",
            "evidence_gaps": ["dcbbs_database_missing"],
        }

    try:
        connection = _connect_read_only(db_path)
    except (sqlite3.Error, OSError):
        return {
            **base,
            "status": "blocked",
            "reason_code": "dcbbs_database_unavailable",
            "evidence_gaps": ["dcbbs_database_unavailable"],
        }

    try:
        schema = _schema_state(connection)
        if not schema["resources_usable"] and not schema["news_usable"]:
            return {
                **base,
                "status": "blocked",
                "reason_code": "dcbbs_schema_incompatible",
                "health": {"schema": schema},
                "evidence_gaps": ["dcbbs_schema_incompatible"],
            }

        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if usable
            else 0
            for table, usable in (
                ("resources", schema["resources_usable"]),
                ("news", schema["news_usable"]),
            )
        }
        city = _clean_text(
            context.get("city") or _mapping(report.get("parcel")).get("city"), 40
        )
        district = _clean_text(
            context.get("district") or _mapping(report.get("parcel")).get("district"), 40
        )
        resource_rows: list[sqlite3.Row] = []
        news_rows: list[sqlite3.Row] = []
        if schema["resources_usable"]:
            resource_rows = _select_title_rows(
                connection,
                "resources",
                (
                    "doc_id", "title", "source_url", "upload_date", "document_format",
                    "page_count", "description", "preview_text", "usage_scope", "fetched_at",
                ),
                terms,
            )
        if schema["news_usable"]:
            news_rows = _select_title_rows(
                connection,
                "news",
                (
                    "article_id", "title", "source_url", "published_date", "category",
                    "author", "summary", "usage_scope", "fetched_at",
                ),
                terms,
            )

        resource_ranked = []
        for row in resource_rows:
            row_map = dict(row)
            score, matched, locality = _rank_row(row_map, terms, city, district)
            resource_ranked.append((score, int(row_map["doc_id"]), row_map, matched, locality))
        resource_ranked.sort(key=lambda item: (-item[0], -item[1]))

        news_ranked = []
        for row in news_rows:
            row_map = dict(row)
            score, matched, locality = _rank_row(row_map, terms, city, district)
            news_ranked.append((score, int(row_map["article_id"]), row_map, matched, locality))
        news_ranked.sort(key=lambda item: (-item[0], -item[1]))

        resource_limit = _clamp_limit(context.get("dcbbs_resource_limit"), 8)
        news_limit = _clamp_limit(context.get("dcbbs_news_limit"), 6)
        selected_resource_rows = resource_ranked[:resource_limit]
        selected_news_rows = news_ranked[:news_limit]
        selected_doc_ids = [item[1] for item in selected_resource_rows]
        origin_map = _db_origin_matches(connection, selected_doc_ids)
        origin_map.update(
            {
                doc_id: value
                for doc_id, value in _load_catalog(catalog_path).items()
                if doc_id in selected_doc_ids
            }
        )

        items: list[dict[str, Any]] = []
        for score, doc_id, row, matched, locality in selected_resource_rows:
            items.append(
                _resource_item(row, score, matched, locality, origin_map.get(doc_id))
            )
        for score, _article_id, row, matched, locality in selected_news_rows:
            items.append(_news_item(row, score, matched, locality))

        health = {
            "database_counts": counts,
            "schema": schema,
            "manifest": _manifest_health(manifest_path, counts),
            "database_file": db_path.name,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        gaps: list[str] = []
        if health["manifest"].get("count_mismatch"):
            gaps.append("dcbbs_manifest_stale")
        if schema["missing_columns"]:
            gaps.append("dcbbs_schema_partial")
        if not terms:
            gaps.append("dcbbs_query_terms_missing")
        if not items:
            gaps.append("dcbbs_no_title_match")
        registry = _source_registry(items)
        evidence_nodes = _evidence_nodes(items, terms)
        return {
            **base,
            "status": "partial" if gaps else "ready",
            "query": {"terms": query_view, "mode": "title_only"},
            "counts": {
                **counts,
                "candidates": len(resource_rows) + len(news_rows),
                "selected": len(items),
                "promoted_claims": sum(
                    1
                    for item in items
                    if _mapping(item.get("original_source")).get("trust_tier") in {"L1", "L2"}
                ),
            },
            "items": items,
            "evidence_nodes": evidence_nodes,
            "source_registry": registry,
            "evidence_gaps": gaps,
            "health": health,
        }
    except sqlite3.DatabaseError:
        return {
            **base,
            "status": "blocked",
            "reason_code": "dcbbs_database_corrupt_or_busy",
            "evidence_gaps": ["dcbbs_database_corrupt_or_busy"],
        }
    finally:
        connection.close()


def attach_dcbbs_professional_intelligence(
    report: dict[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach DCBBS as a professional-intelligence provider in-place."""
    packet = build_dcbbs_professional_intelligence(report, context)
    existing = _mapping(report.get("professional_intelligence"))
    other_items = [
        item
        for item in _sequence(existing.get("items"))
        if isinstance(item, Mapping) and not str(item.get("source_id") or "").startswith("dcbbs:")
    ]
    other_nodes = [
        item
        for item in _sequence(existing.get("evidence_nodes"))
        if isinstance(item, Mapping) and not str(item.get("evidence_id") or "").startswith("dcbbs:")
    ]
    other_sources = [
        item
        for item in _sequence(existing.get("source_registry"))
        if isinstance(item, Mapping) and not str(item.get("source_id") or "").startswith("dcbbs:")
    ]
    providers = [str(item) for item in _sequence(existing.get("providers")) if item]
    if "dcbbs" not in providers:
        providers.append("dcbbs")
    report["professional_intelligence"] = {
        **existing,
        **packet,
        "providers": providers,
        "items": [*other_items, *packet.get("items", [])],
        "evidence_nodes": [*other_nodes, *packet.get("evidence_nodes", [])],
        "source_registry": [*other_sources, *packet.get("source_registry", [])],
        "provider_results": {
            **_mapping(existing.get("provider_results")),
            "dcbbs": packet,
        },
    }
    return report


__all__ = [
    "attach_dcbbs_professional_intelligence",
    "build_dcbbs_professional_intelligence",
]

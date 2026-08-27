"""Privacy-safe social observation and evidence-profile aggregation.

Only records supplied by the caller are processed.  This module deliberately
contains no HTTP client or crawler dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:  # Support both ``scripts.social_intelligence`` and app.py's script path.
    from .evidence_contract import compute_evidence_confidence, confidence_level
except ImportError:  # pragma: no cover - exercised by the existing app import style
    from evidence_contract import compute_evidence_confidence, confidence_level


PLATFORMS = ("xiaohongshu", "douyin", "wechat_official", "58_reviews", "fang_reviews")
AUTHOR_TYPES = (
    "consumer",
    "owner",
    "prospective_buyer",
    "media",
    "institution",
    "kol",
    "developer",
    "broker",
    "expert",
    "unknown",
)
CONTENT_TYPES = ("post", "video", "article", "comment", "review", "unknown")

_PLATFORM_ALIASES = {
    "xiaohongshu": "xiaohongshu",
    "xhs": "xiaohongshu",
    "redbook": "xiaohongshu",
    "red": "xiaohongshu",
    "小红书": "xiaohongshu",
    "小紅書": "xiaohongshu",
    "douyin": "douyin",
    "抖音": "douyin",
    "tiktokcn": "douyin",
    "wechat_official": "wechat_official",
    "wechatofficial": "wechat_official",
    "weixinofficial": "wechat_official",
    "微信公众号": "wechat_official",
    "微信公众平台": "wechat_official",
    "公众号": "wechat_official",
    "公眾號": "wechat_official",
    "58同城": "58_reviews",
    "58_reviews": "58_reviews",
    "房天下": "fang_reviews",
    "fang_reviews": "fang_reviews",
}

_AUTHOR_ALIASES = {
    "consumer": "consumer",
    "customer": "consumer",
    "buyer": "consumer",
    "user": "consumer",
    "c端": "consumer",
    "c端用户": "consumer",
    "消费者": "consumer",
    "购房者": "consumer",
    "业主": "owner",
    "owner": "owner",
    "真实业主": "owner",
    "prospectivebuyer": "prospective_buyer",
    "intendedbuyer": "prospective_buyer",
    "意向客户": "prospective_buyer",
    "意向购房者": "prospective_buyer",
    "media": "media",
    "媒体": "media",
    "自媒体": "media",
    "institution": "institution",
    "机构": "institution",
    "官方机构": "institution",
    "kol": "kol",
    "博主": "kol",
    "达人": "kol",
    "developer": "developer",
    "开发商": "developer",
    "房企": "developer",
    "broker": "broker",
    "agent": "broker",
    "中介": "broker",
    "经纪人": "broker",
    "expert": "expert",
    "专家": "expert",
    "学者": "expert",
    "unknown": "unknown",
    "未知": "unknown",
}

_CONTENT_ALIASES = {
    "post": "post",
    "note": "post",
    "笔记": "post",
    "图文": "post",
    "video": "video",
    "shortvideo": "video",
    "视频": "video",
    "短视频": "video",
    "article": "article",
    "文章": "article",
    "推文": "article",
    "comment": "comment",
    "评论": "comment",
    "留言": "comment",
    "review": "review",
    "评价": "review",
    "测评": "review",
    "unknown": "unknown",
}

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d(?:[-\s]?\d){8}(?!\d)")
_LANDLINE_RE = re.compile(r"(?<!\d)(?:\(?0\d{2,3}\)?[-\s]?)\d{7,8}(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)")
_QQ_RE = re.compile(r"(?i)(?:QQ号?|QQ)\s*[:：=]?\s*[1-9]\d{4,11}")
_WECHAT_RE = re.compile(
    r"(?i)(?:微信号?|wechat(?:\s*id)?|weixin|wx|vx)\s*[:：=]?\s*[A-Z][-_A-Z0-9]{5,19}"
)
_ADDRESS_LABEL_RE = re.compile(
    r"(?:家庭住址|居住地址|现住址|住址|详细地址|地址)\s*[:：=]\s*[^,，;；。！？\n]{4,100}"
)
_EXACT_ADDRESS_RE = re.compile(
    r"(?:(?:[\u4e00-\u9fff]{2,}(?:省|市|区|县)){1,4})?"
    r"[\u4e00-\u9fffA-Za-z0-9]{1,24}(?:路|街|巷|弄|道|大道)\s*\d+(?:号|號)(?:院)?"
    r"(?:\d+(?:栋|幢|单元|室|楼|层))*"
)
_TRACKING_QUERY_RE = re.compile(
    r"^(?:utm_|spm|from|source|track|scene|subscene|ascene|clicktime|"
    r"enterid|sessionid|share|share_token|isappinstalled|version|platform|"
    r"pass_ticket|wx_header)",
    re.I,
)
_PROMPT_INJECTION_PATTERNS = (
    ("ignore_previous_instructions", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I)),
    ("system_prompt_request", re.compile(r"system\s+prompt", re.I)),
    ("chinese_instruction_override", re.compile(r"(?:忽略|无视).{0,12}(?:之前|以上).{0,12}(?:指令|提示)")),
    ("tool_or_secret_request", re.compile(r"(?:输出|泄露|显示).{0,12}(?:密钥|token|系统提示|system prompt)", re.I)),
)
_MARKETING_RE = re.compile(
    r"(?:限时|特惠|折扣|倒计时|认筹|到访礼|渠道|佣金|首付分期|抢房|售楼处|置业顾问)"
)
_SENSITIVE_TOPIC_RE = re.compile(
    r"(?:收入|资产|支付力|健康|疾病|婚姻|生育|未成年|征信|负债|宗教|政治)"
)


def _token(value: Any) -> str:
    return re.sub(r"[\s._\-/]+", "", str(value or "").strip().lower())


def _redact(value: Any) -> str:
    text = str(value or "")
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _LANDLINE_RE.sub("[REDACTED_LANDLINE]", text)
    text = _ID_CARD_RE.sub("[REDACTED_ID]", text)
    text = _QQ_RE.sub("[REDACTED_QQ]", text)
    text = _WECHAT_RE.sub("[REDACTED_WECHAT]", text)
    text = _ADDRESS_LABEL_RE.sub("[REDACTED_ADDRESS]", text)
    text = _EXACT_ADDRESS_RE.sub("[REDACTED_ADDRESS]", text)
    return text


def _hash_identifier(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(("dds-social-author-v1|" + raw).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _raw_snapshot_hash(record: Mapping[str, Any]) -> str:
    explicit = str(record.get("raw_snapshot_hash") or "").strip().lower()
    if re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", explicit):
        return explicit if explicit.startswith("sha256:") else f"sha256:{explicit}"
    encoded = json.dumps(
        dict(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _duplicate_cluster(title: str, content: str, canonical_url: str) -> str:
    text_basis = re.sub(r"[\W_]+", "", f"{title}{content}".casefold(), flags=re.UNICODE)
    basis = text_basis or canonical_url.casefold()
    return "social-family:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _prompt_injection_flags(text: str) -> list[str]:
    return [name for name, pattern in _PROMPT_INJECTION_PATTERNS if pattern.search(text)]


def _engagement(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int | float] = {}
    for key in ("likes", "comments", "shares", "favorites", "views"):
        raw = value.get(key)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number) or number < 0:
            continue
        result[key] = int(number) if number.is_integer() else round(number, 3)
    return result


def _marketing_probability(record: Mapping[str, Any], author_type: str, text: str) -> float:
    if record.get("marketing_probability") is not None:
        return _number(record.get("marketing_probability"), 0.0)
    if author_type in {"developer", "broker"}:
        return 0.85
    if _MARKETING_RE.search(text):
        return 0.70
    if author_type in {"media", "institution", "kol"}:
        return 0.40
    return 0.10


def _consensus_eligible(record: Mapping[str, Any]) -> bool:
    return (
        record.get("author_type") in {"consumer", "owner", "prospective_buyer"}
        and float(record.get("marketing_probability") or 0.0) < 0.60
        and float(record.get("bot_probability") or 0.0) < 0.60
    )


def _independent_records(
    records: Sequence[Mapping[str, Any]], *, consensus_only: bool = False
) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if consensus_only and not _consensus_eligible(record):
            continue
        family = str(record.get("duplicate_cluster") or record.get("record_id") or "")
        if family and family in seen:
            continue
        if family:
            seen.add(family)
        result.append(record)
    return result


def _normalize_platform(value: Any, url: Any = "") -> str:
    compact = _token(value)
    if compact in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[compact]
    raw = str(value or "").strip().lower()
    if raw in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[raw]
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    if "xiaohongshu" in host or "xhslink" in host:
        return "xiaohongshu"
    if "douyin" in host:
        return "douyin"
    if host == "mp.weixin.qq.com" or "weixin.qq.com" in host:
        return "wechat_official"
    return ""


def _normalize_author_type(value: Any, platform: str) -> str:
    compact = _token(value)
    normalized = _AUTHOR_ALIASES.get(compact)
    if normalized:
        return normalized
    # Official accounts are publishers by default, never assumed C-end users.
    return "media" if platform == "wechat_official" else "unknown"


def _normalize_content_type(value: Any, platform: str) -> str:
    compact = _token(value)
    normalized = _CONTENT_ALIASES.get(compact)
    if normalized:
        return normalized
    return {
        "xiaohongshu": "post",
        "douyin": "video",
        "wechat_official": "article",
    }.get(platform, "unknown")


def _canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        return ""
    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port and not (
        (parts.scheme.lower() == "http" and port == 80)
        or (parts.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    identity_query: list[tuple[str, str]] = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if _TRACKING_QUERY_RE.search(key):
            continue
        safe_key = _redact(key)
        safe_value = _redact(item)
        identity_query.append((safe_key, safe_value))
    identity_query.sort()
    query = urlencode(identity_query, doseq=True)
    return _redact(
        urlunsplit(
            (parts.scheme.lower(), host, path.rstrip("/") or "/", query, "")
        )
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1_000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value or "").strip()
        if not text:
            return None
        text = text.replace("/", "-")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(1.0, max(0.0, number))


def _normalize_geo(value: Any) -> str:
    return re.sub(r"(?:省|市|区|县|自治州|自治区)$", "", str(value or "").strip())


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: Iterable[Any] = re.split(r"[,，;；|\n]+", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("text") or item.get("label") or item.get("value") or ""
        text = _redact(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _stance(value: Any) -> str:
    token = _token(value)
    if token in {"support", "supportive", "positive", "支持", "赞成", "正向", "认同"}:
        return "support"
    if token in {"oppose", "opposition", "negative", "反对", "反方", "负向", "质疑"}:
        return "opposition"
    return "neutral"


def _allowed_record(record: Mapping[str, Any]) -> bool:
    if record.get("is_public") is False and record.get("authorized") is not True:
        return False
    scope = _token(record.get("access_scope") or record.get("access"))
    if scope in {"private", "restricted", "unauthorized", "私密", "未授权"}:
        return record.get("authorized") is True
    return True


def _behavior_anchor_type(record: Mapping[str, Any]) -> str | None:
    """Return a strict, evidence-bearing anchor classification.

    The base flag must be the literal boolean ``True``.  Truthy strings such
    as ``"false"`` or ``"1"`` never unlock a confidence ceiling.
    """
    if record.get("real_behavior_anchor") is not True:
        return None
    explicit = _token(
        record.get("behavior_anchor_type")
        or record.get("real_behavior_anchor_type")
        or record.get("anchor_type")
    )
    if explicit in {"structuredbehavior", "structured_behavior", "结构化行为"}:
        return "structured_behavior"
    if explicit in {"transaction", "transactionanchor", "成交", "交易"}:
        return "transaction"
    if record.get("structured_behavior_anchor") is True:
        return "structured_behavior"
    if record.get("transaction_anchor") is True:
        return "transaction"
    return "real_behavior"


def _simulation_n(record: Mapping[str, Any]) -> int:
    if "simulation_n" in record:
        value = record.get("simulation_n")
    elif "simulation_sample_n" in record:
        value = record.get("simulation_sample_n")
    else:
        nested = record.get("simulation")
        value = nested.get("n") if isinstance(nested, Mapping) else 0
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _record_freshness(published_at: datetime | None, now: datetime, window_days: int) -> float:
    if published_at is None:
        return 0.35
    age_days = max(0.0, (now - published_at).total_seconds() / 86_400)
    return max(0.0, 1.0 - age_days / max(1, window_days))


def _normalize_record(
    record: Mapping[str, Any],
    *,
    city: str,
    district: str | None,
    now: datetime,
    window_days: int,
) -> tuple[dict[str, Any] | None, str | None]:
    if not _allowed_record(record):
        return None, "unauthorized"
    raw_url = record.get("canonical_url") or record.get("url") or record.get("source_url")
    platform = _normalize_platform(record.get("platform"), raw_url)
    if not platform:
        return None, "unsupported_platform"

    record_city = str(record.get("city") or record.get("location_city") or "").strip()
    if record_city and city and _normalize_geo(record_city) != _normalize_geo(city):
        return None, "outside_city"

    published = _parse_datetime(
        record.get("published_at")
        or record.get("created_at")
        or record.get("date")
        or record.get("observed_at")
    )
    if published and published < now - timedelta(days=window_days):
        return None, "outside_window"

    canonical_url = _canonical_url(raw_url)
    if str(raw_url or "").strip() and not canonical_url:
        return None, "invalid_url"
    title = _redact(record.get("title") or "").strip()
    content = _redact(
        record.get("content")
        or record.get("text")
        or record.get("summary")
        or record.get("claim")
        or ""
    ).strip()
    if not canonical_url and not title and not content:
        return None, "empty_content"
    explicit_hash = str(record.get("content_hash") or "").strip().lower()
    if explicit_hash:
        if not re.fullmatch(r"[a-f0-9]{16,128}", explicit_hash):
            explicit_hash = hashlib.sha256(explicit_hash.encode("utf-8")).hexdigest()
        content_hash = explicit_hash
    else:
        hash_basis = "|".join((title, content, canonical_url, platform))
        content_hash = hashlib.sha256(hash_basis.encode("utf-8")).hexdigest()

    raw_author = (
        record.get("author_id")
        or record.get("author")
        or record.get("author_name")
        or record.get("account_id")
    )
    record_district = str(record.get("district") or "").strip()
    geo_default = 1.0 if record_city and _normalize_geo(record_city) == _normalize_geo(city) else 0.55
    if district:
        if record_district and _normalize_geo(record_district) == _normalize_geo(district):
            geo_default = 1.0
        elif record_district:
            geo_default = 0.65

    freshness_default = _record_freshness(published, now, window_days)
    factors = {
        "source": _number(record.get("source"), 0.55),
        "coverage": _number(record.get("coverage"), 0.0),
        "freshness": _number(record.get("freshness"), freshness_default),
        "independent_cross": _number(record.get("independent_cross"), 0.0),
        "geographic_relevance": _number(
            record.get("geographic_relevance"), geo_default
        ),
        "method_fit": _number(record.get("method_fit"), 0.70),
        "stability": _number(record.get("stability"), 0.50),
    }
    explicit_dimensions = {
        key for key in factors if record.get(key) is not None
    }

    anchor_type = _behavior_anchor_type(record)
    author_type = _normalize_author_type(record.get("author_type"), platform)
    captured = _parse_datetime(record.get("captured_at")) or now
    combined_text = " ".join(part for part in (title, content) if part)
    marketing_probability = _marketing_probability(record, author_type, combined_text)
    bot_probability = _number(record.get("bot_probability"), 0.0)
    duplicate_cluster = _duplicate_cluster(title, content, canonical_url)
    rights_status = _token(record.get("rights_status") or "public") or "public"
    normalized: dict[str, Any] = {
        "record_id": f"social:{content_hash[:20]}",
        "evidence_type": "social_observation",
        "platform": platform,
        "author_type": author_type,
        "content_type": _normalize_content_type(record.get("content_type"), platform),
        "author_hash": _hash_identifier(raw_author),
        "canonical_url": canonical_url,
        "content_hash": content_hash,
        "raw_snapshot_hash": _raw_snapshot_hash(record),
        "duplicate_cluster": duplicate_cluster,
        "title": title,
        "published_at": published.isoformat() if published else None,
        "captured_at": captured.isoformat(),
        "city": _redact(record_city or city),
        "district": _redact(record_district),
        "geography": {
            "city": _redact(record_city or city),
            "district": _redact(record_district) or None,
        },
        "segment": _redact(record.get("segment") or "").strip(),
        "topic": _redact(record.get("topic") or "").strip(),
        "stance": _stance(record.get("stance") or record.get("sentiment")),
        "emotion": _redact(record.get("emotion") or "").strip() or None,
        "purchase_stage": _redact(record.get("purchase_stage") or "").strip() or None,
        "content_excerpt": content[:500],
        "redacted_text": content[:500],
        "engagement": _engagement(record.get("engagement")),
        "pains": _list(record.get("pains") or record.get("pain_points")),
        "preferences": _list(record.get("preferences")),
        "objections": _list(record.get("objections")),
        "triggers": _list(record.get("triggers")),
        "counter_evidence_refs": _list(record.get("counter_evidence_refs")),
        "marketing_probability": marketing_probability,
        "bot_probability": bot_probability,
        "rights_status": rights_status,
        "pii_redacted": True,
        "content_trust": "untrusted_external_text",
        "prompt_injection_flags": _prompt_injection_flags(combined_text),
        "real_behavior_anchor": anchor_type is not None,
        "behavior_anchor_type": anchor_type,
        "_factors": factors,
        "_explicit_dimensions": explicit_dimensions,
        "_simulation_n": _simulation_n(record),
        "_explicit_simulation_stability": record.get("simulation_stability"),
    }
    return normalized, None


def _average(values: Iterable[float], default: float = 0.0) -> float:
    collected = list(values)
    return sum(collected) / len(collected) if collected else default


def _confidence_cap(records: Sequence[Mapping[str, Any]]) -> float:
    records = _independent_records(records)
    if len(records) <= 1:
        return 0.35
    platforms = {record["platform"] for record in records}
    if len(platforms) <= 1:
        return 0.55
    authors = {record.get("author_hash") for record in records if record.get("author_hash")}
    has_anchor = any(record.get("real_behavior_anchor") is True for record in records)
    has_structured_anchor = any(
        record.get("behavior_anchor_type") in {"structured_behavior", "transaction"}
        for record in records
    )
    if has_structured_anchor and len(authors) >= 2 and len(records) >= 3:
        return 0.90
    if has_anchor and len(authors) >= 2 and len(records) >= 3:
        return 0.80
    return 0.65


def _evidence_confidence(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    records = _independent_records(records)
    if not records:
        return compute_evidence_confidence({}, cap=0.0)
    platforms = {record["platform"] for record in records}
    authors = {record.get("author_hash") for record in records if record.get("author_hash")}

    derived = {
        "source": 0.55,
        "coverage": min(1.0, len(records) / 20),
        "freshness": 0.35,
        "independent_cross": min(
            1.0,
            max(0, len(platforms) - 1) * 0.35 + min(0.30, len(authors) / 20),
        ),
        "geographic_relevance": 0.55,
        "method_fit": 0.70,
        "stability": 0.50,
    }
    dimensions: dict[str, float] = {}
    for key in derived:
        explicit_values = [
            float(record["_factors"][key])
            for record in records
            if key in record["_explicit_dimensions"]
        ]
        if explicit_values:
            dimensions[key] = _average(explicit_values)
        elif key in {"freshness", "geographic_relevance", "source", "method_fit", "stability"}:
            dimensions[key] = _average(
                float(record["_factors"][key]) for record in records
            )
        else:
            dimensions[key] = derived[key]
    return compute_evidence_confidence(dimensions, cap=_confidence_cap(records))


def _simulation_stability(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sample_n = max((int(record.get("_simulation_n") or 0) for record in records), default=0)
    explicit = [
        _number(record.get("_explicit_simulation_stability"), 0.0)
        for record in records
        if record.get("_explicit_simulation_stability") is not None
    ]
    if explicit:
        score = _average(explicit)
        basis = "provided_stability"
    elif sample_n:
        score = min(1.0, math.log10(max(1, sample_n)) / 5.0)
        basis = "simulation_sample_size"
    else:
        score = 0.0
        basis = "no_simulation"
    score = round(score, 6)
    return {
        "score": score,
        "level": confidence_level(score),
        "simulation_n": sample_n,
        "basis": basis,
        "affects_evidence_confidence": False,
    }


def _unique(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _representativeness_limits(records: Sequence[Mapping[str, Any]]) -> list[str]:
    if not records:
        return ["没有可用的公开或授权社媒记录，不能构造客群证据画像。"]
    limits = ["社媒内容是自选择样本，不能代表目标城市全部购房人群。"]
    platforms = {record["platform"] for record in records}
    authors = {record.get("author_hash") for record in records if record.get("author_hash")}
    if len(platforms) == 1:
        limits.append("仅覆盖单一平台，平台机制与内容生态可能造成偏差。")
    if len(authors) < 30:
        limits.append(f"唯一作者仅 {len(authors)} 个，样本规模不足以推断总体比例。")
    if not any(record.get("published_at") for record in records):
        limits.append("记录缺少可验证发布时间，时间覆盖无法完整判断。")
    if not any(record.get("real_behavior_anchor") is True for record in records):
        limits.append("尚未与到访、调研、认购或成交等真实行为锚点交叉验证。")
    if not any(record.get("author_type") == "consumer" for record in records):
        limits.append("当前没有明确的 C 端作者，媒体或机构内容不能替代消费者证据。")
    return limits


def _profile(records: Sequence[Mapping[str, Any]], segment: str, topic: str) -> dict[str, Any]:
    independent = _independent_records(records)
    consensus_records = _independent_records(records, consensus_only=True)
    dates = sorted(
        record["published_at"] for record in records if record.get("published_at")
    )
    platforms = sorted({record["platform"] for record in records})
    authors = {record.get("author_hash") for record in records if record.get("author_hash")}
    consensus_authors = {
        record.get("author_hash")
        for record in consensus_records
        if record.get("author_hash")
    }
    support = [record for record in consensus_records if record.get("stance") == "support"]
    opposition = [record for record in consensus_records if record.get("stance") == "opposition"]
    neutral = [record for record in consensus_records if record.get("stance") == "neutral"]
    minimum_authors = 20 if _SENSITIVE_TOPIC_RE.search(f"{segment} {topic}") else 10
    eligible = len(consensus_authors) >= minimum_authors

    return {
        "profile_id": "persona-evidence:"
        + hashlib.sha256(f"{segment}|{topic}".encode("utf-8")).hexdigest()[:16],
        "evidence_type": "social_observation",
        "segment": segment or None,
        "topic": topic or None,
        "unique_authors": len(authors),
        "consensus_unique_authors": len(consensus_authors),
        "record_count": len(records),
        "evidence_family_count": len(independent),
        "minimum_authors_required": minimum_authors,
        "aggregation_status": "eligible" if eligible else "insufficient_sample",
        "decision_eligible": eligible,
        "platform_coverage": {"platforms": platforms, "count": len(platforms)},
        "consensus_platform_coverage": {
            "platforms": sorted({record["platform"] for record in consensus_records}),
            "count": len({record["platform"] for record in consensus_records}),
        },
        "time_coverage": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
            "dated_records": len(dates),
        },
        "support": {
            "count": len(support),
            "evidence": _unique(
                record.get("content_excerpt", "") for record in support
            ),
        },
        "opposition": {
            "count": len(opposition),
            "evidence": _unique(
                record.get("content_excerpt", "") for record in opposition
            ),
        },
        "neutral_count": len(neutral),
        "pains": _unique(item for record in consensus_records for item in record.get("pains", [])),
        "preferences": _unique(
            item for record in consensus_records for item in record.get("preferences", [])
        ),
        "objections": _unique(
            item for record in consensus_records for item in record.get("objections", [])
        ),
        "triggers": _unique(
            item for record in consensus_records for item in record.get("triggers", [])
        ),
        "evidence_confidence": _evidence_confidence(consensus_records),
        "simulation_stability": _simulation_stability(records),
        "representativeness_limits": _representativeness_limits(records),
    }


def _claims(
    records: Sequence[Mapping[str, Any]], *, window_days: int
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for record in _independent_records(records):
        span = record.get("content_excerpt") or record.get("title") or ""
        if not span:
            continue
        claims.append(
            {
                "claim_id": f"social-claim:{str(record.get('duplicate_cluster') or '').split(':')[-1]}",
                "evidence_type": "social_observation",
                "evidence_span": span,
                "topic": record.get("topic") or None,
                "stance": record.get("stance") or "neutral",
                "emotion": record.get("emotion"),
                "purchase_stage": record.get("purchase_stage"),
                "pain": list(record.get("pains") or []),
                "preference": list(record.get("preferences") or []),
                "objection": list(record.get("objections") or []),
                "trigger": list(record.get("triggers") or []),
                "geography": deepcopy(record.get("geography") or {}),
                "time_window": {
                    "published_at": record.get("published_at"),
                    "captured_at": record.get("captured_at"),
                    "window_days": int(window_days),
                },
                "counter_evidence_refs": list(
                    record.get("counter_evidence_refs") or []
                ),
                "source_refs": [record.get("record_id")],
                "confidence": _evidence_confidence([record]),
                "content_trust": "untrusted_external_text",
                "prompt_injection_flags": list(
                    record.get("prompt_injection_flags") or []
                ),
            }
        )
    return claims


def _source_registry(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": record.get("record_id"),
            "platform": record.get("platform"),
            "canonical_url": record.get("canonical_url"),
            "published_at": record.get("published_at"),
            "captured_at": record.get("captured_at"),
            "rights_status": record.get("rights_status"),
            "raw_snapshot_hash": record.get("raw_snapshot_hash"),
            "duplicate_cluster": record.get("duplicate_cluster"),
        }
        for record in records
    ]


def _public_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def build_social_intelligence(
    records: Sequence[Mapping[str, Any]] | None,
    city: str,
    district: str | None = None,
    window_days: int = 180,
) -> dict[str, Any]:
    """Normalize supplied public/authorized records and build evidence profiles.

    No network access occurs.  Increasing a downstream simulation sample can
    change ``simulation_stability`` but is intentionally excluded from every
    empirical evidence-confidence dimension.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    now = datetime.now(timezone.utc)
    input_records = list(records or [])
    normalized: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    rejection_counts: Counter[str] = Counter()
    duplicate_count = 0

    for raw in input_records:
        if not isinstance(raw, Mapping):
            rejection_counts["invalid_record"] += 1
            continue
        record, rejection = _normalize_record(
            raw,
            city=city,
            district=district,
            now=now,
            window_days=int(window_days),
        )
        if record is None:
            rejection_counts[rejection or "invalid_record"] += 1
            continue
        url = str(record.get("canonical_url") or "")
        content_hash = str(record.get("content_hash") or "")
        if (url and url in seen_urls) or (content_hash and content_hash in seen_hashes):
            duplicate_count += 1
            continue
        if url:
            seen_urls.add(url)
        if content_hash:
            seen_hashes.add(content_hash)
        normalized.append(record)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in normalized:
        segment = str(record.get("segment") or "")
        topic = str(record.get("topic") or "")
        # Evidence profiles are only named by explicit input labels.  An empty
        # pair is never converted into a synthetic persona.
        if segment or topic:
            grouped[(segment, topic)].append(record)
    profiles = [
        _profile(group, segment, topic)
        for (segment, topic), group in sorted(grouped.items())
    ]

    evidence_confidence = _evidence_confidence(normalized)
    simulation_stability = _simulation_stability(normalized)
    limits = _representativeness_limits(normalized)
    independent = _independent_records(normalized)
    consensus_records = _independent_records(normalized, consensus_only=True)
    consensus_authors = {
        record.get("author_hash")
        for record in consensus_records
        if record.get("author_hash")
    }
    evidence_gaps = []
    if not normalized:
        evidence_gaps.append("缺少目标窗口内可用的公开或授权社媒记录。")
    if normalized and not profiles:
        evidence_gaps.append("记录没有显式 segment/topic，未生成客群证据画像。")
    if normalized and not any(
        record.get("real_behavior_anchor") is True for record in normalized
    ):
        evidence_gaps.append("缺少真实行为锚点，社媒证据置信度上限为 0.65。")
    if normalized and len(consensus_authors) < 10:
        evidence_gaps.append(
            f"可进入普通聚合的独立 C 端作者仅 {len(consensus_authors)} 个，低于 10 个门槛。"
        )

    return {
        "status": "ready" if normalized else "missing",
        "city": _redact(city),
        "district": _redact(district) if district else None,
        "window_days": int(window_days),
        "data_scope": {
            "mode": "provided_records_only",
            "network_access": False,
            "public_or_authorized_only": True,
        },
        "input_record_count": len(input_records),
        "record_count": len(normalized),
        "evidence_family_count": len(independent),
        "consensus_eligible_record_count": len(consensus_records),
        "excluded_from_consensus_count": len(normalized) - len(consensus_records),
        "aggregation_status": (
            "eligible" if len(consensus_authors) >= 10 else "insufficient_sample"
        ) if normalized else "missing",
        "duplicate_count": duplicate_count,
        "rejected_count": sum(rejection_counts.values()),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "platforms": sorted({record["platform"] for record in normalized}),
        "author_type_counts": dict(
            sorted(Counter(record["author_type"] for record in normalized).items())
        ),
        "content_type_counts": dict(
            sorted(Counter(record["content_type"] for record in normalized).items())
        ),
        "records": [_public_record(record) for record in normalized],
        "claims": _claims(normalized, window_days=int(window_days)),
        "source_registry": _source_registry(normalized),
        "evidence_confidence": evidence_confidence,
        "simulation_stability": simulation_stability,
        "persona_evidence_profiles": profiles,
        "representativeness_limits": limits,
        "evidence_gaps": evidence_gaps,
    }


__all__ = [
    "AUTHOR_TYPES",
    "CONTENT_TYPES",
    "PLATFORMS",
    "build_social_intelligence",
]

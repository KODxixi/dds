"""Deterministic bridge from truth-domain ReportRun to the V4 compiler."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping, Sequence

from dds.contracts import UNIT_BY_ID, VALID_SECTION_IDS
from dds.analysis_profile import (
    optional_units_from_metadata,
    required_units_from_metadata,
)
from dds.domain import ReportRun, ResolvedField, ResolvedStatus, SectionResult
from dds.reporting import AssetResolver, build_frozen_package


def _portable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return _portable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _portable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_portable(item) for item in value]
    return value


def _portable_source_id(raw_evidence_id: str) -> tuple[str, str]:
    evidence_id_hash = sha256(raw_evidence_id.encode("utf-8")).hexdigest()
    return f"evidence-{evidence_id_hash[:24]}", evidence_id_hash


def _source_id_map(run: ReportRun) -> dict[str, str]:
    raw_ids = [str(item.evidence_id or "") for item in run.evidence_records]
    if any(not item for item in raw_ids):
        raise ValueError("every EvidenceRecord requires a non-empty evidence_id")
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("EvidenceRecord.evidence_id values must be unique")
    mapping = {
        raw_id: _portable_source_id(raw_id)[0]
        for raw_id in raw_ids
    }
    if len(mapping) != len(set(mapping.values())):
        raise ValueError("portable evidence source_id collision")
    return mapping


def _mapped_source_refs(
    source_refs: Sequence[str],
    source_id_map: Mapping[str, str],
) -> list[str]:
    mapped: list[str] = []
    for value in source_refs:
        raw_id = str(value)
        if raw_id not in source_id_map:
            raise ValueError(
                f"section source_ref has no EvidenceRecord: {raw_id!r}"
            )
        portable_id = source_id_map[raw_id]
        if portable_id not in mapped:
            mapped.append(portable_id)
    return mapped


def _section_status(section: SectionResult) -> str:
    if section.status is ResolvedStatus.RESOLVED and not section.gaps:
        return "ready"
    if section.status is ResolvedStatus.UNKNOWN:
        return "missing"
    if any(
        isinstance(item, ResolvedField) and item.status is ResolvedStatus.HUMAN_INPUT
        for item in section.data.values()
    ):
        return "blocked"
    return "partial"


def _confidence_score(section: SectionResult) -> float:
    confidence = section.confidence
    if isinstance(confidence, Mapping):
        raw = confidence.get("score", 0.0)
    elif isinstance(confidence, (int, float)):
        raw = confidence
    else:
        raw = 0.0
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _resolved_value(section: SectionResult, field: str) -> Any:
    value = section.data.get(field)
    return value.value if isinstance(value, ResolvedField) else value


def _section_blocks(
    section: SectionResult,
    source_id_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    portable_refs = _mapped_source_refs(section.evidence_refs, source_id_map)
    if section.section_id == "SC2":
        competitors = _resolved_value(section, "competitors")
        if isinstance(competitors, Sequence) and not isinstance(competitors, (str, bytes)):
            rows = []
            for item in competitors:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    {
                        "project": item.get("project_name"),
                        "district": item.get("district"),
                        "listing_price": item.get("price"),
                        "unit": "CNY/m2",
                        "distance_km": item.get("distance_km"),
                    }
                )
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "title": "结构化有效竞品",
                        "columns": ["project", "district", "listing_price", "unit", "distance_km"],
                        "rows": rows[:12],
                        "source_refs": list(portable_refs),
                    }
                )
    elif section.section_id == "AD3":
        product_mix = _resolved_value(section, "product_mix")
        if isinstance(product_mix, Sequence) and not isinstance(product_mix, (str, bytes)):
            rows = []
            for item in product_mix:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    {
                        "direction": item.get("direction_id"),
                        "positioning": item.get("positioning"),
                        "customer": item.get("target_customer"),
                        "value": item.get("value_proposition"),
                        "trigger": item.get("switch_trigger"),
                    }
                )
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "title": "产品定位三方向比较",
                        "columns": ["direction", "positioning", "customer", "value", "trigger"],
                        "rows": rows,
                        "source_refs": list(portable_refs),
                    }
                )
    elif section.section_id == "VA1":
        scenarios = _resolved_value(section, "premium_factors")
        if isinstance(scenarios, Sequence) and not isinstance(scenarios, (str, bytes)):
            rows = []
            for item in scenarios:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    {
                        "scenario": item.get("name"),
                        "combined_rate": item.get("combined_rate"),
                        "unit_price": item.get("resulting_unit_price_cny_m2"),
                        "gross_value": item.get("gross_incremental_value_cny"),
                        "cost": item.get("incremental_cost_cny"),
                        "net_value": item.get("net_incremental_value_cny"),
                    }
                )
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "title": "Premium sensitivity scenarios",
                        "columns": [
                            "scenario",
                            "combined_rate",
                            "unit_price",
                            "gross_value",
                            "cost",
                            "net_value",
                        ],
                        "rows": rows,
                        "source_refs": list(portable_refs),
                    }
                )
    elif section.section_id == "VA2":
        forecast = _resolved_value(section, "sales_forecast")
        if isinstance(forecast, Mapping):
            curves = forecast.get("curves")
            if isinstance(curves, Sequence) and not isinstance(
                curves, (str, bytes)
            ):
                rows = [
                    {
                        "strategy": item.get("strategy_id"),
                        "scenario": item.get("scenario_id"),
                        "absorption_12m": item.get("absorption_12m"),
                        "clearance_month": item.get("clearance_month"),
                        "value_index": item.get("realized_value_index"),
                        "npv_index": item.get("risk_adjusted_npv_index"),
                    }
                    for item in curves
                    if isinstance(item, Mapping)
                ]
                if rows:
                    blocks.append(
                        {
                            "type": "table",
                            "title": "Standard 100-unit operating scenarios",
                            "columns": [
                                "strategy",
                                "scenario",
                                "absorption_12m",
                                "clearance_month",
                                "value_index",
                                "npv_index",
                            ],
                            "rows": rows,
                            "source_refs": list(portable_refs),
                        }
                    )
            monthly = forecast.get("monthly")
            if isinstance(monthly, Sequence) and not isinstance(
                monthly, (str, bytes)
            ):
                rows = [
                    {
                        "month": item.get("month"),
                        "subscriptions": item.get("subscriptions"),
                        "cancellations": item.get("cancellations"),
                        "contracts": item.get("contracts"),
                        "contracted_value_cny": item.get(
                            "contracted_sales_value_cny"
                        ),
                    }
                    for item in monthly
                    if isinstance(item, Mapping)
                ]
                if rows:
                    blocks.append(
                        {
                            "type": "table",
                            "title": "Project monthly subscriptions and contracts",
                            "columns": [
                                "month",
                                "subscriptions",
                                "cancellations",
                                "contracts",
                                "contracted_value_cny",
                            ],
                            "rows": rows,
                            "source_refs": list(portable_refs),
                        }
                    )
            schemes = forecast.get("schemes")
            if isinstance(schemes, Sequence) and not isinstance(
                schemes, (str, bytes)
            ):
                rows = [
                    {
                        "scheme": item.get("scheme_id"),
                        "feasible": item.get("feasible"),
                        "absorption_12m": item.get("absorption_12m"),
                        "realized_value_cny": item.get(
                            "realized_value_cny"
                        ),
                        "risk_adjusted_npv_cny": item.get(
                            "risk_adjusted_npv_cny"
                        ),
                        "peak_funding_cny": item.get(
                            "peak_funding_cny"
                        ),
                        "tail_inventory_rate": item.get(
                            "tail_inventory_rate"
                        ),
                    }
                    for item in schemes
                    if isinstance(item, Mapping)
                ]
                if rows:
                    blocks.append(
                        {
                            "type": "table",
                            "title": "Risk-adjusted Pareto scheme comparison",
                            "columns": [
                                "scheme",
                                "feasible",
                                "absorption_12m",
                                "realized_value_cny",
                                "risk_adjusted_npv_cny",
                                "peak_funding_cny",
                                "tail_inventory_rate",
                            ],
                            "rows": rows,
                            "source_refs": list(portable_refs),
                        }
                    )
    for conclusion in section.conclusions:
        text = str(conclusion).strip()
        if text:
            blocks.append(
                {
                    "type": "narrative",
                    "text": text,
                    "source_refs": list(portable_refs),
                }
            )
    if not blocks and section.gaps:
        blocks.append(
            {
                "type": "gap",
                "text": "；".join(str(item) for item in section.gaps),
                "source_refs": [],
            }
        )
    return _portable(blocks)


class ReportCompilerAdapter:
    """Create compiler input using only an already assembled ReportRun."""

    def build_report_seed(
        self,
        run: ReportRun,
        *,
        required_units: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        required = tuple(dict.fromkeys(str(item) for item in (
            required_units or required_units_from_metadata(run.metadata)
        )))
        if not required:
            raise ValueError("required_units must not be empty")
        unknown = [item for item in required if item not in VALID_SECTION_IDS]
        if unknown:
            raise ValueError(f"unknown required units: {unknown}")
        source_id_map = _source_id_map(run)
        pages: list[dict[str, Any]] = []
        for section_id in required:
            section = run.sections.get(section_id)
            if not isinstance(section, SectionResult):
                section = SectionResult(
                    section_id=section_id,
                    data={},
                    gaps=[UNIT_BY_ID[section_id]["gap"]],
                    status=ResolvedStatus.UNKNOWN,
                )
            status = _section_status(section)
            title = UNIT_BY_ID[section_id]["title"]
            takeaway = (
                str(section.conclusions[0])
                if section.conclusions
                else str(section.gaps[0])
                if section.gaps
                else "本单元尚未形成有证据支持的结论。"
            )
            evidence_type = (
                "observed_fact"
                if section_id == "SC2"
                else "analysis_inference"
            )
            pages.append(
                {
                    "page_id": f"{section_id.lower()}-primary",
                    "chapter_id": section_id.lower(),
                    "section_id": section_id,
                    "unit_id": section_id,
                    "unit_status": status,
                    "layout": "gap" if status in {"missing", "blocked"} else "summary",
                    "title": title,
                    "takeaway": takeaway,
                    "blocks": _section_blocks(section, source_id_map),
                    "source_refs": _mapped_source_refs(
                        section.evidence_refs,
                        source_id_map,
                    ),
                    "confidence": {"score": _confidence_score(section)},
                    "evidence_type": evidence_type,
                }
            )
        source_registry = [
            {
                "source_id": source_id_map[item.evidence_id],
                "name": item.metric_id or source_id_map[item.evidence_id],
                "source_type": item.evidence_type.value,
                "trust_tier": "dds_recalculated",
                "status": "frozen",
                "canonical_ref": (
                    f"dds:evidence/{source_id_map[item.evidence_id]}"
                ),
                "evidence_id_sha256": _portable_source_id(item.evidence_id)[1],
                "used_for": item.metric_id,
                "limitations": list(item.limitations),
                "sha256": item.source_hash,
                "captured_at": _portable(item.effective_at),
                "rights_status": "source_policy_required",
                "source_scope": item.geography,
            }
            for item in sorted(run.evidence_records, key=lambda value: value.evidence_id)
        ]
        context = run.project_context
        profile = run.metadata.get("analysis_profile")
        included = list(required)
        for section_id in optional_units_from_metadata(run.metadata):
            section = run.sections.get(str(section_id))
            if isinstance(section, SectionResult) and _section_status(section) not in {"missing", "blocked"}:
                included.append(str(section_id))
        included = list(dict.fromkeys(included))
        if included != list(required):
            pages_by_unit = {str(page["section_id"]): page for page in pages}
            for section_id in included:
                if section_id in pages_by_unit:
                    continue
                section = run.sections[section_id]
                pages.append({
                    "page_id": f"{section_id.lower()}-primary",
                    "chapter_id": section_id.lower(),
                    "section_id": section_id,
                    "unit_id": section_id,
                    "unit_status": _section_status(section),
                    "layout": "summary",
                    "title": UNIT_BY_ID[section_id]["title"],
                    "takeaway": str(section.conclusions[0]) if section.conclusions else "条件单元已有实质内容。",
                    "blocks": _section_blocks(section, source_id_map),
                    "source_refs": _mapped_source_refs(section.evidence_refs, source_id_map),
                    "confidence": {"score": _confidence_score(section)},
                    "evidence_type": "analysis_inference",
                })
        return {
            "meta": {
                "as_of": _portable(context.base_date),
                "compiled_at": f"{_portable(context.base_date)}T00:00:00Z",
                "run_id": run.run_id,
                "analysis_profile": profile,
                "decision_scope": run.metadata.get("decision_scope"),
            },
            "project": {
                "project_id": context.project_id,
                "name": context.project_name or context.project_id,
                "city": context.city,
                "district": context.district,
                "project_type": context.project_type,
            },
            "project_panorama": {
                "required_units": list(required),
                "included_units": included,
                "unit_policy": profile.get("unit_policy", {}) if isinstance(profile, Mapping) else {},
            },
            "page_manifest_authoritative": True,
            "page_manifest": pages,
            "source_registry": source_registry,
            "evidence_gaps": [
                {
                    "section_id": section_id,
                    "gap": str(gap),
                }
                for section_id in required
                for gap in getattr(run.sections.get(section_id), "gaps", [])
            ],
        }

    def build_frozen_compiler_package(
        self,
        run: ReportRun,
        *,
        required_units: Sequence[str] | None = None,
        as_of: str | date | None = None,
        asset_resolver: AssetResolver | None = None,
    ) -> dict[str, Any]:
        seed = self.build_report_seed(run, required_units=required_units)
        frozen_as_of = as_of or run.project_context.base_date
        if frozen_as_of is None:
            raise ValueError("as_of or project_context.base_date is required")
        return build_frozen_package(
            seed,
            project_id=run.project_context.project_id,
            request_id=run.run_id,
            as_of=frozen_as_of,
            asset_resolver=asset_resolver,
        )


__all__ = ["ReportCompilerAdapter"]

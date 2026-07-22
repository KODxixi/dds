"""Evidence-aware data orchestration for DDS decision units.

The orchestrator makes every missing field visible.  It may fetch real data,
but it never converts absence into a synthetic benchmark or awards confidence
for fixed assumptions such as a configured city name.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

from dds.contracts import (
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    EvidenceRecord,
    FieldOrigin,
    ProjectContext,
    ResolvedField,
    ResolvedStatus,
    SectionData,
    SectionResult,
    compute_confidence_from_evidence,
    confidence_level,
)
from dds.data.fallback import FallbackEngine
from dds.data.fetcher import DataFetcher

logger = logging.getLogger(__name__)


class DataOrchestrator:
    """Resolve section fields against an auditable evidence ledger."""

    def __init__(
        self,
        city: str = "",
        project_type: str = "",
        data_dir: str = "",
        *,
        data_fetcher: DataFetcher | None = None,
        fallback_engine: FallbackEngine | None = None,
    ):
        self.city = city
        self.project_type = project_type
        self.data_fetcher = data_fetcher or DataFetcher(
            city=city,
            project_type=project_type,
            data_dir=data_dir,
        )
        self.fallback_engine = fallback_engine or FallbackEngine(
            city=city,
            project_type=project_type,
        )

    async def ensure_section_data(
        self,
        section_id: str,
        project_context: ProjectContext | Mapping[str, Any],
        existing_data: Optional[dict[str, Any] | SectionData | SectionResult] = None,
    ) -> SectionData:
        """Resolve one section without hiding unavailable information."""

        if section_id not in VALID_SECTION_IDS:
            raise ValueError(f"无效的 section ID: {section_id}")

        context = ProjectContext.from_mapping(project_context)
        context_payload = context.to_dict()
        evidence_records = self._extract_evidence_records(context_payload)
        evidence_by_id = {
            item.evidence_id: item for item in evidence_records if item.evidence_id
        }
        source_data = self._coerce_existing_data(existing_data)
        if section_id == "CS":
            self._seed_cs_from_evidence(source_data, evidence_records, context)

        final_data: dict[str, Any] = {}
        data_origin: dict[str, FieldOrigin] = {}
        resolved_fields: dict[str, ResolvedField] = {}
        warnings: list[str] = []

        for field_name in SECTION_REQUIREMENTS.get(section_id, []):
            if field_name in source_data and not self._is_empty_value(
                source_data[field_name], field_name=field_name
            ):
                resolved, origin = self._resolve_existing_value(
                    section_id,
                    field_name,
                    source_data[field_name],
                    evidence_records,
                    evidence_by_id,
                )
                self._store_resolution(
                    field_name,
                    resolved,
                    origin,
                    final_data,
                    data_origin,
                    resolved_fields,
                )
                if resolved.status != ResolvedStatus.RESOLVED:
                    warnings.append(
                        f"字段 [{field_name}] 已提供但缺少可核验证据，状态={resolved.status.value}"
                    )
                continue

            # ``data_gaps=[]`` is a valid, explicit empty ledger.  It is not a
            # missing value and must remain distinguishable from an absent key.
            if section_id == "CS" and field_name == "data_gaps" and field_name in source_data:
                resolved = ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=list(source_data[field_name] or []),
                    confidence={"score": 0.0, "derived_from_evidence": True},
                    reason="系统汇总的数据缺口清单；空列表表示当前没有登记缺口",
                )
                origin = FieldOrigin(
                    source="system_derived",
                    label="系统汇总",
                    evidence_refs=[],
                    resolution_status=ResolvedStatus.RESOLVED,
                )
                self._store_resolution(
                    field_name,
                    resolved,
                    origin,
                    final_data,
                    data_origin,
                    resolved_fields,
                )
                continue

            fetched_value, fetched_origin = await self._fetch(
                section_id,
                field_name,
                context_payload,
            )
            if fetched_value is not None and fetched_origin is not None:
                resolved = self._resolution_from_fetched(
                    fetched_value,
                    fetched_origin,
                    evidence_by_id,
                )
                self._store_resolution(
                    field_name,
                    resolved,
                    fetched_origin,
                    final_data,
                    data_origin,
                    resolved_fields,
                )
                if resolved.status != ResolvedStatus.RESOLVED:
                    warnings.append(
                        f"字段 [{field_name}] 的抓取结果无可核验证据，按 partial 处理"
                    )
                continue

            fallback_value, fallback_origin = self.fallback_engine.get_fallback(
                section_id=section_id,
                field=field_name,
            )
            self._store_resolution(
                field_name,
                fallback_value,
                fallback_origin,
                final_data,
                data_origin,
                resolved_fields,
            )
            warnings.append(
                f"字段 [{field_name}] 无可核验证据: {fallback_origin.label}"
            )

        confidence = self._calculate_section_confidence(
            section_id,
            final_data,
            data_origin,
            evidence_records=evidence_records,
            project_context=context,
            resolved_fields=resolved_fields,
        )
        missing_fields = [
            field_name
            for field_name, resolved in resolved_fields.items()
            if resolved.status
            not in {ResolvedStatus.RESOLVED, ResolvedStatus.NOT_APPLICABLE}
        ]
        result = SectionData(
            section_id=section_id,
            data=final_data,
            confidence=confidence,
            missing_fields=missing_fields,
            data_origin=data_origin,
            warnings=warnings,
            resolved_fields=resolved_fields,
        )
        logger.info(
            "Section %s 数据准备完成: 证据派生置信度=%.2f, unresolved=%d",
            section_id,
            confidence["score"],
            len(missing_fields),
        )
        return result

    async def ensure_all_sections(
        self,
        project_context: ProjectContext | Mapping[str, Any],
        existing_sections: Optional[
            dict[str, dict[str, Any] | SectionData | SectionResult]
        ] = None,
    ) -> dict[str, SectionData]:
        """Resolve all 12 sections and populate CS with the explicit gap ledger."""

        existing_sections = existing_sections or {}
        results: dict[str, SectionData] = {}
        for section_id in VALID_SECTION_IDS:
            results[section_id] = await self.ensure_section_data(
                section_id=section_id,
                project_context=project_context,
                existing_data=existing_sections.get(section_id),
            )

        all_gaps = []
        for current_id, section in results.items():
            if current_id == "CS":
                continue
            for field_name in section.missing_fields:
                resolved = section.field_resolution(field_name)
                all_gaps.append(
                    {
                        "section_id": current_id,
                        # Legacy consumers used ``section``; keep both values
                        # identical so there is no key/internal-ID ambiguity.
                        "section": current_id,
                        "field": field_name,
                        "status": resolved.status.value,
                        "reason": resolved.reason,
                        "action": (
                            "human_input"
                            if resolved.status == ResolvedStatus.HUMAN_INPUT
                            else "collect_evidence"
                        ),
                    }
                )

        cs = results.get("CS")
        if cs is not None:
            gap_resolution = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=all_gaps,
                confidence={"score": 0.0, "derived_from_evidence": True},
                reason="系统根据所有章节的 unresolved 字段汇总",
            )
            cs.data["data_gaps"] = all_gaps
            cs.resolved_fields["data_gaps"] = gap_resolution
            cs.data_origin["data_gaps"] = FieldOrigin(
                source="system_derived",
                label="系统汇总",
                evidence_refs=[],
                resolution_status=ResolvedStatus.RESOLVED,
            )
            cs.missing_fields = [
                item for item in cs.missing_fields if item != "data_gaps"
            ]

        return results

    def _coerce_existing_data(
        self,
        existing_data: dict[str, Any] | SectionData | SectionResult | None,
    ) -> dict[str, Any]:
        if existing_data is None:
            return {}
        if isinstance(existing_data, (SectionData, SectionResult)):
            return dict(existing_data.data)
        return dict(existing_data)

    def _extract_evidence_records(self, context: Mapping[str, Any]) -> list[EvidenceRecord]:
        raw_records = context.get("evidence_records") or context.get("evidence") or []
        if isinstance(raw_records, Mapping):
            raw_records = list(raw_records.values())
        records = []
        for item in raw_records:
            try:
                records.append(EvidenceRecord.from_mapping(item))
            except (TypeError, ValueError) as exc:
                logger.warning("忽略无效 EvidenceRecord: %s", exc)
        return records

    def _seed_cs_from_evidence(
        self,
        data: dict[str, Any],
        evidence_records: list[EvidenceRecord],
        context: ProjectContext,
    ) -> None:
        evidence_ids = [item.evidence_id for item in evidence_records if item.evidence_id]
        if evidence_records and "sources" not in data:
            data["sources"] = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=[
                    {
                        "evidence_id": item.evidence_id,
                        "source_id": item.source_id,
                        "source_ref": item.source_ref,
                        "source_hash": item.source_hash,
                    }
                    for item in evidence_records
                ],
                evidence_refs=evidence_ids,
                reason="由 EvidenceRecord 账本生成",
            )
        methods = sorted({item.method for item in evidence_records if item.method})
        if methods and "methods" not in data:
            data["methods"] = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=methods,
                evidence_refs=evidence_ids,
                reason="由 EvidenceRecord.method 汇总",
            )
        if "assumptions" not in data and evidence_records:
            data["assumptions"] = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=list(context.assumptions),
                evidence_refs=[],
                reason="项目上下文中显式登记的假设；空列表表示未登记假设",
            )
        if "confidence_gaps" not in data and evidence_records:
            gaps = []
            if any(not item.effective_at for item in evidence_records):
                gaps.append("部分证据缺少 observed_at/as_of，freshness 记为 0")
            if any(not item.method for item in evidence_records):
                gaps.append("部分证据缺少 method，method_fit 记为 0")
            data["confidence_gaps"] = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=gaps,
                evidence_refs=evidence_ids,
                reason="由 EvidenceRecord 元数据缺口汇总；空列表表示未发现该类缺口",
            )
        data.setdefault("data_gaps", [])

    def _matching_evidence_refs(
        self,
        records: list[EvidenceRecord],
        section_id: str,
        field_name: str,
    ) -> list[str]:
        exact_ids = {
            field_name.casefold(),
            f"{section_id}.{field_name}".casefold(),
            f"{section_id}:{field_name}".casefold(),
        }
        refs = []
        for item in records:
            metric = item.metric_id.casefold()
            if metric in exact_ids or metric.rsplit(".", 1)[-1] == field_name.casefold():
                if item.evidence_id:
                    refs.append(item.evidence_id)
        return list(dict.fromkeys(refs))

    def _resolve_existing_value(
        self,
        section_id: str,
        field_name: str,
        raw_value: Any,
        evidence_records: list[EvidenceRecord],
        evidence_by_id: dict[str, EvidenceRecord],
    ) -> tuple[ResolvedField, FieldOrigin]:
        if section_id == "CS" and field_name == "data_gaps" and isinstance(raw_value, list):
            return (
                ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=list(raw_value),
                    confidence={"score": 0.0, "derived_from_evidence": True},
                    reason="显式数据缺口账本；空列表是合法状态",
                ),
                FieldOrigin(
                    source="system_derived",
                    label="显式缺口账本",
                    evidence_refs=[],
                    resolution_status=ResolvedStatus.RESOLVED,
                ),
            )
        if isinstance(raw_value, ResolvedField):
            resolved = raw_value
        elif isinstance(raw_value, Mapping) and "status" in raw_value:
            resolved = ResolvedField.from_mapping(raw_value)
        elif isinstance(raw_value, Mapping) and "value" in raw_value:
            refs = list(raw_value.get("evidence_refs") or [])
            resolved = ResolvedField(
                status=(ResolvedStatus.RESOLVED if refs else ResolvedStatus.PARTIAL),
                value=raw_value.get("value"),
                evidence_refs=refs,
                assumptions=list(raw_value.get("assumptions") or []),
                confidence=raw_value.get("confidence"),
                reason=str(raw_value.get("reason") or ""),
            )
        else:
            refs = self._matching_evidence_refs(
                evidence_records,
                section_id,
                field_name,
            )
            resolved = ResolvedField(
                status=(ResolvedStatus.RESOLVED if refs else ResolvedStatus.PARTIAL),
                value=raw_value,
                evidence_refs=refs,
                reason=(
                    ""
                    if refs
                    else "值已提供，但没有 EvidenceRecord，不能视为已核验"
                ),
            )

        referenced = [
            evidence_by_id[ref]
            for ref in resolved.evidence_refs
            if ref in evidence_by_id
        ]
        traceable = [item for item in referenced if item.has_traceable_source]
        evidence_free_cs_fields = {"assumptions", "confidence_gaps", "data_gaps"}
        if (
            resolved.status == ResolvedStatus.RESOLVED
            and not traceable
            and not (section_id == "CS" and field_name in evidence_free_cs_fields)
        ):
            resolved.status = ResolvedStatus.PARTIAL
            resolved.reason = resolved.reason or "引用证据不存在或缺少可追溯来源"
        source = traceable[0].source_id if traceable else (
            resolved.status.value
            if resolved.status in {ResolvedStatus.UNKNOWN, ResolvedStatus.HUMAN_INPUT}
            else "provided_unverified"
        )
        origin = FieldOrigin(
            source=source,
            label=("可追溯证据" if traceable else "已提供但未核验"),
            confidence_penalty=(0.0 if traceable else 1.0),
            evidence_refs=list(resolved.evidence_refs),
            resolution_status=resolved.status,
            source_ref=traceable[0].source_ref if traceable else "",
            source_hash=traceable[0].source_hash if traceable else "",
            evidence_type=traceable[0].evidence_type if traceable else None,
        )
        return resolved, origin

    async def _fetch(
        self,
        section_id: str,
        field_name: str,
        context: dict[str, Any],
    ) -> tuple[Any | None, FieldOrigin | None]:
        fetched = await self.data_fetcher.fetch(
            section_id=section_id,
            field=field_name,
            context=context,
        )
        if isinstance(fetched, EvidenceRecord):
            return fetched.value, FieldOrigin(
                source=fetched.source_id,
                label="抓取证据",
                evidence_refs=[fetched.evidence_id] if fetched.evidence_id else [],
                resolution_status=ResolvedStatus.RESOLVED,
                source_ref=fetched.source_ref,
                source_hash=fetched.source_hash,
                evidence_type=fetched.evidence_type,
            )
        if isinstance(fetched, tuple) and len(fetched) == 2:
            value, origin = fetched
            if origin is not None and not isinstance(origin, FieldOrigin):
                origin = FieldOrigin(**dict(origin))
            return value, origin
        if isinstance(fetched, Mapping) and fetched.get("found"):
            value = fetched.get("data", fetched.get("value"))
            refs = list(fetched.get("evidence_refs") or [])
            return value, FieldOrigin(
                source=str(fetched.get("source") or "external_unverified"),
                label=str(fetched.get("label") or "外部抓取"),
                confidence_penalty=0.0 if refs else 1.0,
                evidence_refs=refs,
                resolution_status=(
                    ResolvedStatus.RESOLVED if refs else ResolvedStatus.PARTIAL
                ),
                source_ref=str(fetched.get("source_ref") or ""),
                source_hash=str(fetched.get("source_hash") or ""),
                evidence_type=fetched.get("evidence_type"),
            )
        return None, None

    def _resolution_from_fetched(
        self,
        value: Any,
        origin: FieldOrigin,
        evidence_by_id: dict[str, EvidenceRecord],
    ) -> ResolvedField:
        if isinstance(value, ResolvedField):
            return value
        traceable = [
            evidence_by_id[ref]
            for ref in origin.evidence_refs
            if ref in evidence_by_id and evidence_by_id[ref].has_traceable_source
        ]
        # A URL/hash on FieldOrigin is useful provenance metadata, but the
        # report ledger still needs a matching EvidenceRecord before resolution.
        status = (
            ResolvedStatus.RESOLVED
            if traceable
            else ResolvedStatus.PARTIAL
        )
        origin.resolution_status = status
        return ResolvedField(
            status=status,
            value=value,
            evidence_refs=list(origin.evidence_refs),
            reason=("" if status == ResolvedStatus.RESOLVED else "抓取结果缺少可追溯来源"),
        )

    def _store_resolution(
        self,
        field_name: str,
        resolved: ResolvedField,
        origin: FieldOrigin,
        final_data: dict[str, Any],
        data_origin: dict[str, FieldOrigin],
        resolved_fields: dict[str, ResolvedField],
    ) -> None:
        # Preserve legacy access to plain resolved values; unresolved fields
        # remain explicit ResolvedField objects instead of deceptive strings.
        final_data[field_name] = (
            resolved.value
            if resolved.value is not None
            else resolved
        )
        data_origin[field_name] = origin
        resolved_fields[field_name] = resolved

    def _is_empty_value(self, value: Any, *, field_name: str = "") -> bool:
        if isinstance(value, ResolvedField):
            return False
        if isinstance(value, Mapping) and "status" in value:
            return False
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        if isinstance(value, (list, dict)) and len(value) == 0:
            return field_name != "data_gaps"
        return False

    def _calculate_section_confidence(
        self,
        section_id: str,
        data: dict[str, Any],
        data_origin: dict[str, FieldOrigin],
        *,
        evidence_records: list[EvidenceRecord] | None = None,
        project_context: ProjectContext | Mapping[str, Any] | None = None,
        resolved_fields: dict[str, ResolvedField] | None = None,
    ) -> dict[str, Any]:
        """Derive confidence from evidence metadata; never from fixed defaults."""

        del data  # presence alone is not evidence quality
        records = evidence_records or []
        by_id = {item.evidence_id: item for item in records if item.evidence_id}
        refs = {
            ref
            for origin in data_origin.values()
            for ref in origin.evidence_refs
        }
        if resolved_fields:
            refs.update(
                ref
                for resolved in resolved_fields.values()
                for ref in resolved.evidence_refs
            )
        referenced = [by_id[ref] for ref in refs if ref in by_id]

        required_metric_ids = []
        for field_name in SECTION_REQUIREMENTS.get(section_id, []):
            field_refs = (
                resolved_fields[field_name].evidence_refs
                if resolved_fields and field_name in resolved_fields
                else data_origin.get(field_name, FieldOrigin("", "")).evidence_refs
            )
            metric_ids = [
                by_id[ref].metric_id
                for ref in field_refs
                if ref in by_id and by_id[ref].metric_id
            ]
            required_metric_ids.append(
                metric_ids[0] if metric_ids else f"{section_id}.{field_name}"
            )

        result = compute_confidence_from_evidence(
            referenced,
            required_metric_ids=required_metric_ids,
            project_context=project_context,
        )
        result["section_id"] = section_id
        result["unresolved_field_count"] = sum(
            1
            for origin in data_origin.values()
            if origin.resolution_status
            not in {ResolvedStatus.RESOLVED, ResolvedStatus.NOT_APPLICABLE}
        )
        return result

    def calculate_overall_confidence(
        self,
        sections: dict[str, SectionData | SectionResult],
    ) -> dict[str, Any]:
        if not sections:
            score = 0.0
            section_breakdown: dict[str, float] = {}
        else:
            section_breakdown = {
                section_id: self._confidence_score(section.confidence)
                for section_id, section in sections.items()
            }
            score = sum(section_breakdown.values()) / len(section_breakdown)
        level = confidence_level(score)
        return {
            "score": score,
            "level": level,
            "level_label": {
                "high": "高",
                "medium": "中",
                "low": "低",
                "undecidable": "不可判定",
            }[level],
            "section_breakdown": section_breakdown,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "derived_from_evidence": True,
        }

    def _confidence_score(self, confidence: dict[str, Any] | float | None) -> float:
        if isinstance(confidence, Mapping):
            value = confidence.get("score", 0.0)
        else:
            value = confidence or 0.0
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def get_data_quality_report(
        self,
        sections: dict[str, SectionData],
    ) -> dict[str, Any]:
        quality_by_section = {}
        total_fields = 0
        evidence_backed_fields = 0
        unresolved_by_status: dict[str, int] = {}

        for section_id, section in sections.items():
            section_fields = len(section.data)
            backed = sum(
                1
                for origin in section.data_origin.values()
                if origin.is_evidence_backed
                and origin.resolution_status == ResolvedStatus.RESOLVED
            )
            total_fields += section_fields
            evidence_backed_fields += backed
            for origin in section.data_origin.values():
                if origin.resolution_status != ResolvedStatus.RESOLVED:
                    key = origin.resolution_status.value
                    unresolved_by_status[key] = unresolved_by_status.get(key, 0) + 1
            quality_by_section[section_id] = {
                "total_fields": section_fields,
                "real_fields": backed,
                "evidence_backed_fields": backed,
                "real_ratio": backed / section_fields if section_fields else 0.0,
                "confidence_score": self._confidence_score(section.confidence),
                "warnings_count": len(section.warnings),
            }

        overall_confidence = self.calculate_overall_confidence(sections)
        return {
            "overall_confidence": overall_confidence,
            "quality_by_section": quality_by_section,
            "summary": {
                "total_fields": total_fields,
                # Retain old keys, but define "real" as evidence-backed.
                "real_fields": evidence_backed_fields,
                "real_ratio": (
                    evidence_backed_fields / total_fields if total_fields else 0.0
                ),
                "fallback_fields": total_fields - evidence_backed_fields,
                "fallback_by_strategy": unresolved_by_status,
                "evidence_backed_fields": evidence_backed_fields,
                "unresolved_by_status": unresolved_by_status,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


__all__ = ["DataOrchestrator"]





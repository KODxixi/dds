"""Strict four-gate truth-contract enforcement for DDS reports.

The gates are ordered and cumulative:

``structure -> evidence -> decision -> delivery``

A report may be structurally complete while explicitly containing unknowns.
It cannot become decision- or delivery-ready until its resolved claims are
linked to traceable evidence and their evidence-derived confidence is adequate.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from dds.analysis_profile import (
    analysis_profile_contract_errors,
    required_units_from_metadata,
)
from dds.contracts import (
    EVIDENCE_TYPES,
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    EvidenceRecord,
    FieldOrigin,
    GateResult,
    ProjectContext,
    ReportRun,
    ResolvedField,
    ResolvedStatus,
    SectionData,
    SectionResult,
    ValidationResult,
    compute_confidence_from_evidence,
    confidence_level,
)

logger = logging.getLogger(__name__)

GATE_ORDER = ("structure", "evidence", "decision", "delivery")
_BENCHMARK_ORIGINS = {
    "city_benchmark",
    "project_type_average",
    "national_benchmark",
}


class ContractEnforcer:
    """Validate and enforce all four report truth gates."""

    def __init__(self, *, decision_confidence_threshold: float = 0.55):
        self.decision_confidence_threshold = max(
            0.0,
            min(1.0, float(decision_confidence_threshold)),
        )

    def validate_report(
        self,
        sections: (
            dict[str, SectionData | SectionResult | Mapping[str, Any]]
            | ReportRun
            | Mapping[str, Any]
        ),
        report_metadata: Mapping[str, Any] | None = None,
        evidence_records: (
            Iterable[EvidenceRecord | Mapping[str, Any]]
            | Mapping[str, EvidenceRecord | Mapping[str, Any]]
            | None
        ) = None,
    ) -> ValidationResult:
        """Return gate-by-gate validation without mutating report content."""

        (
            section_map,
            metadata,
            context,
            raw_evidence,
            report_status,
        ) = self._unpack_report(sections, report_metadata, evidence_records)
        evidence, evidence_input_errors = self._coerce_evidence(raw_evidence)
        evidence_by_id = {
            item.evidence_id: item for item in evidence if item.evidence_id
        }
        required_units = required_units_from_metadata(metadata)

        structure_errors, structure_warnings = self._validate_structure(section_map, required_units)
        structure_errors.extend(analysis_profile_contract_errors(metadata))
        structure_gate = GateResult(
            name="structure",
            status="pass" if not structure_errors else "fail",
            errors=structure_errors,
            warnings=structure_warnings,
        )

        evidence_errors, evidence_warnings, section_confidences = self._validate_evidence(
            section_map,
            evidence,
            evidence_by_id,
            context,
            evidence_input_errors,
            required_units,
        )
        if not structure_gate.passed:
            evidence_errors.insert(0, "evidence gate 依赖 structure gate 通过")
        evidence_gate = GateResult(
            name="evidence",
            status=(
                "pass"
                if structure_gate.passed and not evidence_errors
                else "fail"
            ),
            errors=evidence_errors,
            warnings=evidence_warnings,
            details={"section_confidences": dict(section_confidences)},
        )

        decision_errors, decision_warnings = self._validate_decision(
            section_map,
            section_confidences,
            required_units,
        )
        if not evidence_gate.passed:
            decision_errors.insert(0, "decision gate 依赖 evidence gate 通过")
        decision_gate = GateResult(
            name="decision",
            status=(
                "pass"
                if evidence_gate.passed and not decision_errors
                else "fail"
            ),
            errors=decision_errors,
            warnings=decision_warnings,
        )

        delivery_errors, delivery_warnings = self._validate_delivery(
            section_map,
            metadata,
            report_status,
            required_units,
        )
        if not decision_gate.passed:
            delivery_errors.insert(0, "delivery gate 依赖 decision gate 通过")
        delivery_gate = GateResult(
            name="delivery",
            status=(
                "pass"
                if decision_gate.passed and not delivery_errors
                else "fail"
            ),
            errors=delivery_errors,
            warnings=delivery_warnings,
        )

        gates = {
            "structure": structure_gate,
            "evidence": evidence_gate,
            "decision": decision_gate,
            "delivery": delivery_gate,
        }
        errors = self._deduplicate(
            error for gate in gates.values() for error in gate.errors
        )
        warnings = self._deduplicate(
            warning for gate in gates.values() for warning in gate.warnings
        )
        overall_confidence = self._calculate_overall_confidence_from_scores(
            section_confidences
        )
        result = ValidationResult(
            valid=all(gate.passed for gate in gates.values()),
            errors=errors,
            warnings=warnings,
            overall_confidence=overall_confidence,
            section_confidences=section_confidences,
            gates=gates,
        )
        if result.valid:
            logger.info(
                "报告四级契约校验通过，证据派生整体置信度: %.2f",
                overall_confidence,
            )
        else:
            logger.error(
                "报告四级契约校验失败: %s",
                result.gate_status,
            )
        return result

    def validate_run(self, report_run: ReportRun | Mapping[str, Any]) -> ValidationResult:
        """Explicit ReportRun entry point."""

        return self.validate_report(report_run)

    def enforce_contract(
        self,
        sections: (
            dict[str, SectionData | SectionResult | Mapping[str, Any]]
            | ReportRun
            | Mapping[str, Any]
        ),
        report_metadata: Mapping[str, Any] | None = None,
        evidence_records: (
            Iterable[EvidenceRecord | Mapping[str, Any]]
            | Mapping[str, EvidenceRecord | Mapping[str, Any]]
            | None
        ) = None,
    ) -> ValidationResult:
        result = self.validate_report(
            sections,
            report_metadata,
            evidence_records,
        )
        result.raise_if_invalid()
        return result

    def _unpack_report(
        self,
        report_or_sections: Any,
        report_metadata: Mapping[str, Any] | None,
        explicit_evidence: Any,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        ProjectContext,
        Any,
        ResolvedStatus | str | None,
    ]:
        metadata = dict(report_metadata or {})
        report_status: ResolvedStatus | str | None = None
        if isinstance(report_or_sections, ReportRun):
            section_map = dict(report_or_sections.sections)
            metadata = {**report_or_sections.metadata, **metadata}
            context = report_or_sections.project_context
            report_evidence = report_or_sections.evidence_records
            report_status = report_or_sections.status
        elif isinstance(report_or_sections, Mapping) and "sections" in report_or_sections:
            payload = dict(report_or_sections)
            section_map = dict(payload.get("sections") or {})
            context = ProjectContext.from_mapping(
                payload.get("project_context")
                or payload.get("context")
                or metadata.get("project_context")
                or metadata.get("context")
            )
            report_evidence = payload.get("evidence_records") or payload.get("evidence")
            metadata = {**dict(payload.get("metadata") or {}), **metadata}
            report_status = payload.get("status")
        else:
            section_map = (
                dict(report_or_sections)
                if isinstance(report_or_sections, Mapping)
                else {}
            )
            context = ProjectContext.from_mapping(
                metadata.get("project_context") or metadata.get("context")
            )
            report_evidence = metadata.get("evidence_records") or metadata.get("evidence")
            report_status = metadata.get("status")
        return (
            section_map,
            metadata,
            context,
            explicit_evidence if explicit_evidence is not None else report_evidence,
            report_status,
        )

    def _coerce_evidence(self, raw_evidence: Any) -> tuple[list[EvidenceRecord], list[str]]:
        errors: list[str] = []
        if raw_evidence is None:
            return [], []
        if isinstance(raw_evidence, Mapping):
            if "evidence_id" in raw_evidence or "metric_id" in raw_evidence:
                items = [raw_evidence]
            else:
                items = []
                for key, value in raw_evidence.items():
                    if isinstance(value, Mapping) and not value.get("evidence_id"):
                        value = {**dict(value), "evidence_id": str(key)}
                    items.append(value)
        else:
            items = list(raw_evidence)
        records: list[EvidenceRecord] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(items):
            if isinstance(item, Mapping):
                raw_type = str(item.get("evidence_type") or "observed_fact").strip().lower()
                if raw_type not in EVIDENCE_TYPES:
                    errors.append(
                        f"EvidenceRecord[{index}] evidence_type 非法: {raw_type}"
                    )
            try:
                record = EvidenceRecord.from_mapping(item)
            except (TypeError, ValueError) as exc:
                errors.append(f"EvidenceRecord[{index}] 无法解析: {exc}")
                continue
            if record.evidence_id and record.evidence_id in seen_ids:
                errors.append(f"EvidenceRecord ID 重复: {record.evidence_id}")
            seen_ids.add(record.evidence_id)
            records.append(record)
        return records, errors

    def _validate_structure(
        self,
        sections: dict[str, Any],
        required_units: tuple[str, ...] = tuple(VALID_SECTION_IDS),
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if not sections:
            errors.append("报告 sections 为空")
        for section_id in required_units:
            if section_id not in sections:
                errors.append(f"缺少必填章节: {section_id}")
                continue
            section = sections[section_id]
            internal_id = self._internal_section_id(section)
            if not internal_id:
                errors.append(f"章节键 {section_id} 缺少内部 section_id")
            elif internal_id != section_id:
                errors.append(
                    f"章节键 {section_id} 与内部 section_id {internal_id} 不一致"
                )
            data = self._section_data(section)
            if data is None:
                errors.append(f"章节 {section_id} 的 data 必须是 mapping")
                continue
            errors.extend(self._validate_section_fields(section_id, data))

        unexpected = sorted(set(sections).difference(VALID_SECTION_IDS))
        if unexpected:
            warnings.append(f"报告包含未登记章节: {unexpected}")

        if "CS" in sections:
            cs_data = self._section_data(sections["CS"])
            if cs_data is not None and "data_gaps" in cs_data:
                gaps = self._plain_value(cs_data["data_gaps"])
                if not isinstance(gaps, list):
                    errors.append("CS.data_gaps 必须是 list；空列表 [] 是合法值")
        return errors, warnings

    def _validate_section_fields(
        self,
        section_or_id: SectionData | SectionResult | str,
        data: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Validate key presence; explicit unresolved states are structural data."""

        if isinstance(section_or_id, str):
            section_id = section_or_id
            section_data = data
        else:
            section_id = section_or_id.section_id
            section_data = self._section_data(section_or_id)
        if section_data is None:
            return [f"章节 {section_id} 的 data 必须是 mapping"]
        errors = []
        for field_name in SECTION_REQUIREMENTS.get(section_id, []):
            if field_name not in section_data:
                errors.append(f"章节 {section_id} 缺少必填字段: {field_name}")
                continue
            if self._is_empty_value(section_data[field_name], field_name=field_name):
                errors.append(f"章节 {section_id} 字段 [{field_name}] 为空且未显式标记 unknown")
        return errors

    def validate_evidence_record(self, record: EvidenceRecord) -> list[str]:
        """Public single-record provenance validation helper."""

        errors = []
        prefix = f"证据 {record.evidence_id or '<missing-id>'}"
        if not record.evidence_id:
            errors.append(f"{prefix} 缺少 evidence_id")
        if not record.metric_id:
            errors.append(f"{prefix} 缺少 metric_id")
        if record.value is None:
            errors.append(f"{prefix} value 为空")
        if not record.source_id:
            errors.append(f"{prefix} 缺少 source_id")
        if not record.source_ref and not record.source_hash:
            if record.is_benchmark:
                errors.append(f"{prefix} 是基准数据但缺少 source_ref/source_hash，不能通过")
            else:
                errors.append(f"{prefix} 缺少 source_ref/source_hash")
        return errors

    def _validate_evidence(
        self,
        sections: dict[str, Any],
        evidence: list[EvidenceRecord],
        evidence_by_id: dict[str, EvidenceRecord],
        context: ProjectContext,
        input_errors: list[str],
        required_units: tuple[str, ...] = tuple(VALID_SECTION_IDS),
    ) -> tuple[list[str], list[str], dict[str, float]]:
        errors = list(input_errors)
        warnings: list[str] = []
        if not evidence:
            errors.append("报告没有 EvidenceRecord；占位值或来源标签不能替代证据")
        for record in evidence:
            errors.extend(self.validate_evidence_record(record))
            if not record.effective_at:
                warnings.append(
                    f"证据 {record.evidence_id or '<missing-id>'} 缺少 observed_at/as_of，freshness=0"
                )
            if not record.method:
                warnings.append(
                    f"证据 {record.evidence_id or '<missing-id>'} 缺少 method，method_fit=0"
                )

        section_confidences: dict[str, float] = {}
        for section_id in required_units:
            section = sections.get(section_id)
            if section is None:
                section_confidences[section_id] = 0.0
                continue
            data = self._section_data(section)
            if data is None:
                section_confidences[section_id] = 0.0
                continue

            referenced_ids: set[str] = set(self._section_evidence_refs(section))
            required_metric_ids = []
            for field_name in SECTION_REQUIREMENTS.get(section_id, []):
                if field_name not in data:
                    continue
                resolved = self._field_resolution(section, field_name, data[field_name])
                field_refs = list(resolved.evidence_refs)
                referenced_ids.update(field_refs)
                if section_id != "CS":
                    for evidence_id in field_refs:
                        if evidence_id not in evidence_by_id:
                            errors.append(
                                f"章节 {section_id} 字段 [{field_name}] 引用不存在的证据: {evidence_id}"
                            )
                if section_id != "CS" and resolved.status == ResolvedStatus.RESOLVED:
                    if not field_refs:
                        errors.append(
                            f"章节 {section_id} 字段 [{field_name}] 标记 resolved 但没有 evidence_refs"
                        )
                elif section_id != "CS" and resolved.status in {
                    ResolvedStatus.UNKNOWN,
                    ResolvedStatus.HUMAN_INPUT,
                    ResolvedStatus.PARTIAL,
                }:
                    warnings.append(
                        f"章节 {section_id} 字段 [{field_name}] 状态={resolved.status.value}"
                    )

                if resolved.status == ResolvedStatus.NOT_APPLICABLE:
                    if not resolved.reason:
                        errors.append(
                            f"章节 {section_id} 字段 [{field_name}] not_applicable 必须说明 reason"
                        )
                    continue
                metric_ids = [
                    evidence_by_id[ref].metric_id
                    for ref in field_refs
                    if ref in evidence_by_id and evidence_by_id[ref].metric_id
                ]
                required_metric_ids.append(
                    metric_ids[0] if metric_ids else f"{section_id}.{field_name}"
                )

            for field_name, origin in self._section_origins(section).items():
                if origin.source in _BENCHMARK_ORIGINS:
                    traceable_refs = [
                        evidence_by_id[ref]
                        for ref in origin.evidence_refs
                        if ref in evidence_by_id and evidence_by_id[ref].has_traceable_source
                    ]
                    if not traceable_refs:
                        errors.append(
                            f"章节 {section_id} 字段 [{field_name}] 使用无来源 {origin.source}，不能通过"
                        )

            referenced_records = [
                evidence_by_id[item]
                for item in referenced_ids
                if item in evidence_by_id
            ]
            confidence = compute_confidence_from_evidence(
                referenced_records,
                required_metric_ids=required_metric_ids,
                project_context=context,
            )
            score = float(confidence["score"])
            section_confidences[section_id] = score
            declared = self._declared_confidence_score(section)
            if (
                section_id != "CS"
                and declared is not None
                and declared > score + 0.05
            ):
                errors.append(
                    f"章节 {section_id} 声明置信度 {declared:.3f} 高于证据派生值 {score:.3f}"
                )

        return errors, warnings, section_confidences

    def _validate_decision(
        self,
        sections: dict[str, Any],
        section_confidences: dict[str, float],
        required_units: tuple[str, ...] = tuple(VALID_SECTION_IDS),
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        for section_id in required_units:
            if section_id == "CS" or section_id not in sections:
                continue
            section = sections[section_id]
            data = self._section_data(section)
            if data is None:
                continue
            unresolved = []
            for field_name in SECTION_REQUIREMENTS.get(section_id, []):
                if field_name not in data:
                    continue
                resolved = self._field_resolution(section, field_name, data[field_name])
                if resolved.status not in {
                    ResolvedStatus.RESOLVED,
                    ResolvedStatus.NOT_APPLICABLE,
                }:
                    unresolved.append(f"{field_name}:{resolved.status.value}")
            if unresolved:
                errors.append(
                    f"章节 {section_id} 仍有未解决决策字段: {', '.join(unresolved)}"
                )

            section_status = self._section_status(section)
            if section_status in {
                ResolvedStatus.UNKNOWN,
                ResolvedStatus.HUMAN_INPUT,
            }:
                errors.append(
                    f"章节 {section_id} status={section_status.value}，不可进入决策 gate"
                )
            elif section_status == ResolvedStatus.PARTIAL:
                errors.append(f"章节 {section_id} status=partial，必须显式 resolved 后才能决策")

            score = section_confidences.get(section_id, 0.0)
            if score < self.decision_confidence_threshold:
                errors.append(
                    f"章节 {section_id} 证据派生置信度 {score:.3f} 低于决策阈值 "
                    f"{self.decision_confidence_threshold:.3f}"
                )
        return errors, warnings

    def _validate_delivery(
        self,
        sections: dict[str, Any],
        metadata: Mapping[str, Any],
        report_status: ResolvedStatus | str | None,
        required_units: tuple[str, ...] = tuple(VALID_SECTION_IDS),
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        if metadata.get("delivery_ready") is False:
            errors.append("report_metadata.delivery_ready=false")
        delivery_status = str(metadata.get("delivery_status") or "").strip().lower()
        if delivery_status in {"fail", "failed", "blocked", "rejected"}:
            errors.append(f"report_metadata.delivery_status={delivery_status}")
        if report_status is not None:
            normalized = self._normalise_status(report_status)
            if normalized in {
                ResolvedStatus.PARTIAL,
                ResolvedStatus.UNKNOWN,
                ResolvedStatus.HUMAN_INPUT,
            }:
                errors.append(f"ReportRun.status={normalized.value}，不可交付")

        unresolved = self._unresolved_fields(sections, required_units)
        cs = sections.get("CS")
        if cs is not None:
            cs_data = self._section_data(cs) or {}
            for field_name in SECTION_REQUIREMENTS["CS"]:
                if field_name not in cs_data:
                    continue
                status = self._field_resolution(cs, field_name, cs_data[field_name]).status
                if status not in {
                    ResolvedStatus.RESOLVED,
                    ResolvedStatus.NOT_APPLICABLE,
                }:
                    errors.append(
                        f"CS 字段 [{field_name}] status={status.value}，不可交付"
                    )
            if "data_gaps" in cs_data:
                gaps = self._plain_value(cs_data["data_gaps"])
                if isinstance(gaps, list):
                    gap_keys = {
                        (
                            str(item.get("section_id") or item.get("section") or ""),
                            str(item.get("field") or ""),
                        )
                        for item in gaps
                        if isinstance(item, Mapping)
                    }
                    unreported = sorted(set(unresolved).difference(gap_keys))
                    if unreported:
                        errors.append(f"CS.data_gaps 未登记 unresolved 字段: {unreported}")
                    if not unresolved and gaps == []:
                        # This is an intentional valid state, not an empty-field error.
                        pass
        return errors, warnings

    def _unresolved_fields(
        self,
        sections: dict[str, Any],
        required_units: tuple[str, ...] = tuple(VALID_SECTION_IDS),
    ) -> list[tuple[str, str]]:
        result = []
        for section_id, section in sections.items():
            if section_id == "CS" or section_id not in required_units:
                continue
            data = self._section_data(section)
            if data is None:
                continue
            for field_name in SECTION_REQUIREMENTS.get(section_id, []):
                if field_name not in data:
                    continue
                status = self._field_resolution(section, field_name, data[field_name]).status
                if status not in {ResolvedStatus.RESOLVED, ResolvedStatus.NOT_APPLICABLE}:
                    result.append((section_id, field_name))
        return result

    def _section_data(self, section: Any) -> dict[str, Any] | None:
        if isinstance(section, (SectionData, SectionResult)):
            return dict(section.data)
        if isinstance(section, Mapping):
            data = section.get("data")
            return dict(data) if isinstance(data, Mapping) else None
        return None

    def _internal_section_id(self, section: Any) -> str:
        if isinstance(section, (SectionData, SectionResult)):
            return str(section.section_id or "")
        if isinstance(section, Mapping):
            return str(section.get("section_id") or "")
        return ""

    def _section_evidence_refs(self, section: Any) -> list[str]:
        if isinstance(section, SectionResult):
            return list(section.evidence_refs)
        if isinstance(section, SectionData):
            return [
                ref
                for origin in section.data_origin.values()
                for ref in origin.evidence_refs
            ]
        if isinstance(section, Mapping):
            return [str(item) for item in (section.get("evidence_refs") or [])]
        return []

    def _section_origins(self, section: Any) -> dict[str, FieldOrigin]:
        if isinstance(section, SectionData):
            return dict(section.data_origin)
        if isinstance(section, Mapping):
            origins = section.get("data_origin") or {}
            result = {}
            for key, value in dict(origins).items():
                try:
                    result[str(key)] = (
                        value if isinstance(value, FieldOrigin) else FieldOrigin(**dict(value))
                    )
                except (TypeError, ValueError):
                    continue
            return result
        return {}

    def _field_resolution(
        self,
        section: Any,
        field_name: str,
        value: Any,
    ) -> ResolvedField:
        if isinstance(section, (SectionData, SectionResult)):
            return section.field_resolution(field_name)
        if isinstance(value, ResolvedField):
            return value
        if isinstance(value, Mapping) and "status" in value:
            return ResolvedField.from_mapping(value)
        refs = []
        assumptions = []
        confidence = None
        if isinstance(section, Mapping):
            refs = [str(item) for item in (section.get("evidence_refs") or [])]
            assumptions = list(section.get("assumptions") or [])
            confidence = section.get("confidence")
            resolved_fields = section.get("resolved_fields") or {}
            if field_name in resolved_fields:
                return ResolvedField.from_mapping(resolved_fields[field_name])
        return ResolvedField(
            status=ResolvedStatus.RESOLVED,
            value=value,
            evidence_refs=refs,
            assumptions=assumptions,
            confidence=confidence,
        )

    def _section_status(self, section: Any) -> ResolvedStatus:
        if isinstance(section, SectionResult):
            return section.status
        if isinstance(section, Mapping) and "status" in section:
            return self._normalise_status(section.get("status"))
        return ResolvedStatus.RESOLVED

    def _normalise_status(self, status: Any) -> ResolvedStatus:
        if isinstance(status, ResolvedStatus):
            return status
        value = str(status or "").strip().lower()
        aliases = {
            "ready": ResolvedStatus.RESOLVED,
            "complete": ResolvedStatus.RESOLVED,
            "human_input_required": ResolvedStatus.HUMAN_INPUT,
            "blocked": ResolvedStatus.UNKNOWN,
        }
        if value in aliases:
            return aliases[value]
        try:
            return ResolvedStatus(value)
        except ValueError:
            return ResolvedStatus.UNKNOWN

    def _declared_confidence_score(self, section: Any) -> float | None:
        confidence = getattr(section, "confidence", None)
        if confidence is None and isinstance(section, Mapping):
            confidence = section.get("confidence")
        if isinstance(confidence, Mapping):
            if "score" not in confidence:
                return None
            confidence = confidence.get("score")
        if confidence is None:
            return None
        try:
            return max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            return None

    def _plain_value(self, value: Any) -> Any:
        if isinstance(value, ResolvedField):
            return value.value
        if isinstance(value, Mapping) and "status" in value and "value" in value:
            return value.get("value")
        return value

    def _is_empty_value(self, value: Any, *, field_name: str = "") -> bool:
        if isinstance(value, ResolvedField):
            return False
        if isinstance(value, Mapping) and "status" in value:
            return False
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if isinstance(value, (list, dict)) and len(value) == 0:
            return field_name != "data_gaps"
        return False

    def _calculate_overall_confidence_from_scores(
        self,
        section_confidences: Mapping[str, float],
    ) -> float:
        group_weights = {"SC": 0.25, "AD": 0.40, "VA": 0.30, "CS": 0.05}
        section_to_group = {
            "SC1": "SC",
            "SC2": "SC",
            "SC3": "SC",
            "AD1": "AD",
            "AD2": "AD",
            "AD3": "AD",
            "AD4": "AD",
            "AD5": "AD",
            "VA1": "VA",
            "VA2": "VA",
            "VA3": "VA",
            "CS": "CS",
        }
        grouped: dict[str, list[float]] = {}
        for section_id in VALID_SECTION_IDS:
            group = section_to_group[section_id]
            grouped.setdefault(group, []).append(
                max(0.0, min(1.0, float(section_confidences.get(section_id, 0.0))))
            )
        weighted = sum(
            (sum(scores) / len(scores)) * group_weights[group]
            for group, scores in grouped.items()
            if scores
        )
        total_weight = sum(group_weights[group] for group in grouped if grouped[group])
        return weighted / total_weight if total_weight else 0.0

    def _calculate_overall_confidence(
        self,
        sections: dict[str, SectionData | SectionResult],
    ) -> float:
        """Legacy helper: aggregate declared values without relabelling them."""

        scores = {
            section_id: self._declared_confidence_score(section) or 0.0
            for section_id, section in sections.items()
        }
        return self._calculate_overall_confidence_from_scores(scores)

    def get_validation_summary(self, result: ValidationResult) -> dict[str, Any]:
        level = confidence_level(result.overall_confidence)
        labels = {
            "high": "高",
            "medium": "中",
            "low": "低",
            "undecidable": "不可判定",
        }
        return {
            "status": "PASS" if result.valid else "FAIL",
            "gate_status": result.gate_status,
            "gates": {
                name: gate.to_dict() for name, gate in result.gates.items()
            },
            "overall_confidence": {
                "score": result.overall_confidence,
                "level": level,
                "label": labels[level],
                "derived_from_evidence": True,
            },
            "section_confidences": {
                section_id: {
                    "score": score,
                    "level": confidence_level(score),
                    "label": labels[confidence_level(score)],
                }
                for section_id, score in result.section_confidences.items()
            },
            "errors_count": len(result.errors),
            "warnings_count": len(result.warnings),
            "errors": list(result.errors),
            "warnings": list(result.warnings),
        }

    def _deduplicate(self, values: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(str(item) for item in values))


__all__ = ["GATE_ORDER", "ContractEnforcer"]


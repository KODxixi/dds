"""Truth-preserving writer for already assembled SectionResult objects."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Iterable, Mapping

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.domain import EvidenceRecord, ResolvedField, SectionResult


TASK_CONTENT_WRITING = "content_writing"
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:%|\uFF05)?(?![A-Za-z0-9_])"
)


class UnsupportedNumericClaimError(ValueError):
    """Raised when prose introduces a number absent from evidence or formulas."""

    def __init__(self, unsupported_numbers: Iterable[str]):
        self.unsupported_numbers = list(dict.fromkeys(unsupported_numbers))
        super().__init__(
            "unsupported numeric claims: " + ", ".join(self.unsupported_numbers)
        )


def _canonical_number(token: str) -> str:
    value = token.strip().replace(",", "")
    is_percent = value.endswith(("%", "\uFF05"))
    if is_percent:
        value = value[:-1]
    try:
        number = Decimal(value)
    except InvalidOperation:
        return token
    canonical = format(number.normalize(), "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical in {"-0", "+0", ""}:
        canonical = "0"
    return canonical + ("%" if is_percent else "")


def _numbers(value: Any) -> list[str]:
    if value is None:
        return []
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return [
            number
            for item in value.values()
            for number in _numbers(item)
        ]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [number for item in value for number in _numbers(item)]
    return [_canonical_number(match.group(0)) for match in _NUMBER_PATTERN.finditer(str(value))]


def _embedded_formulas(value: Any) -> list[Any]:
    if value is None:
        return []
    if is_dataclass(value):
        value = asdict(value)
    formulas: list[Any] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if "formula" in str(key).casefold():
                formulas.append(item)
            formulas.extend(_embedded_formulas(item))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            formulas.extend(_embedded_formulas(item))
    return formulas


def _display(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


class ContentWriterAgent(BaseAgent):
    """Render existing section content and fail closed on unsupported numbers."""

    @property
    def agent_id(self) -> str:
        return "content_writer"

    @property
    def agent_name(self) -> str:
        return "Content Writer Agent"

    def can_handle(self, task_type: str) -> bool:
        return task_type == TASK_CONTENT_WRITING

    async def execute(self, task: AgentTask) -> AgentResult:
        parameters = task.parameters
        raw_section = parameters.get("section_result") or parameters.get("section")
        if raw_section is None:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=["content_writing requires section_result"],
            )

        try:
            section = SectionResult.from_mapping(raw_section)
            if not section.section_id and task.section_id:
                section.section_id = task.section_id
            formulas = (
                parameters.get("formulas")
                or parameters.get("formulae")
                or parameters.get("formula")
                or {}
            )
            content = self.write(
                section,
                evidence_records=(
                    parameters.get("evidence_records")
                    or parameters.get("evidence")
                    or []
                ),
                formulas=formulas,
                draft=parameters.get("draft"),
            )
        except (TypeError, ValueError) as exc:
            unsupported = (
                exc.unsupported_numbers
                if isinstance(exc, UnsupportedNumericClaimError)
                else []
            )
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                data={
                    "section_id": getattr(raw_section, "section_id", task.section_id),
                    "content": "",
                    "unsupported_numbers": unsupported,
                },
                errors=[str(exc)],
            )

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "section_id": section.section_id,
                "content": content,
                "unsupported_numbers": [],
                "numeric_claims": list(dict.fromkeys(_numbers(content))),
            },
        )

    def write(
        self,
        section_result: SectionResult | Mapping[str, Any],
        *,
        evidence_records: (
            Iterable[EvidenceRecord | Mapping[str, Any]]
            | Mapping[str, EvidenceRecord | Mapping[str, Any]]
            | None
        ) = None,
        formulas: Any = None,
        draft: str | None = None,
    ) -> str:
        """Return prose only when every numeric token is evidence/formula backed."""
        section = SectionResult.from_mapping(section_result)
        content = self._render(section) if draft is None else str(draft).strip()
        unsupported = self.unsupported_numbers(
            content,
            section,
            evidence_records=evidence_records,
            formulas=formulas,
        )
        if unsupported:
            raise UnsupportedNumericClaimError(unsupported)
        return content

    def unsupported_numbers(
        self,
        content: str,
        section_result: SectionResult | Mapping[str, Any],
        *,
        evidence_records: (
            Iterable[EvidenceRecord | Mapping[str, Any]]
            | Mapping[str, EvidenceRecord | Mapping[str, Any]]
            | None
        ) = None,
        formulas: Any = None,
    ) -> list[str]:
        section = SectionResult.from_mapping(section_result)
        refs = set(section.evidence_refs)
        for field_name in section.data:
            refs.update(section.field_resolution(field_name).evidence_refs)

        raw_records: Iterable[EvidenceRecord | Mapping[str, Any]]
        if isinstance(evidence_records, Mapping):
            raw_records = evidence_records.values()
        else:
            raw_records = evidence_records or []
        records = [EvidenceRecord.from_mapping(item) for item in raw_records]
        allowed_values = [
            record.value
            for record in records
            if record.evidence_id in refs and record.has_traceable_source
        ]
        allowed_values.extend(_embedded_formulas(section.data))
        allowed_values.append(formulas)
        allowed_numbers = set(_numbers(allowed_values))

        unsupported = []
        for token in _numbers(content):
            if token not in allowed_numbers and token not in unsupported:
                unsupported.append(token)
        return unsupported

    def _render(self, section: SectionResult) -> str:
        lines = [f"### {section.section_id or 'Section'}"]
        lines.extend(str(item) for item in section.conclusions if str(item).strip())

        for field_name in sorted(section.data):
            resolved: ResolvedField = section.field_resolution(field_name)
            if resolved.value is not None:
                lines.append(f"- {field_name}: {_display(resolved.value)}")
            elif resolved.reason:
                lines.append(f"- {field_name} [{resolved.status.value}]: {resolved.reason}")

        for label, items in (
            ("Assumptions", section.assumptions),
            ("Counter-evidence", section.counter_evidence),
            ("Gaps", section.gaps),
            ("Actions", section.actions),
        ):
            values = [str(item) for item in items if str(item).strip()]
            if values:
                lines.append(f"{label}: " + "; ".join(values))
        return "\n".join(lines)


__all__ = [
    "TASK_CONTENT_WRITING",
    "UnsupportedNumericClaimError",
    "ContentWriterAgent",
]

"""DDS-owned qualification for a binding *conditional entry* decision.

This module deliberately does not qualify a land bid, land-price ceiling, IRR,
ROI, pricing commitment or sales commitment.  It layers a hash-bound human
review over an already-valid report-delivery attestation and an exact draft
ReportDocument.  Research agents cannot mint either the review or the final
decision validation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any


DECISION_REVIEW_SCHEMA = "dds.decision-review/1.0"
DECISION_VALIDATION_SCHEMA = "dds.decision-validation/1.0"
DECISION_SCOPE = "conditional_entry"
AUTHORITY = "DDS"
VALID_STATUS = "validated_for_decision"
VALIDATOR = "dds.conditional-entry-validator/1.0"

ALLOWED_USES = (
    "pre_acquisition_screening",
    "due_diligence_authorization",
    "concept_validation_authorization",
)
PROHIBITED_USES = (
    "land_bid",
    "land_price_ceiling",
    "pricing_commitment",
    "sales_commitment",
    "irr_or_roi_decision",
)
ALLOWED_OUTCOMES = (
    "approve_conditional_entry",
    "hold",
    "no_go",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PROHIBITED_OUTPUT_KEYS = {
    "irr",
    "roi",
    "land_bid_boundaries",
    "land_price_ceiling",
    "pricing_commitment",
    "sales_commitment",
}


class DecisionQualificationError(RuntimeError):
    """Raised when a conditional-entry decision cannot be qualified."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def compute_report_document_hash(document: Mapping[str, Any]) -> str:
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    return _hash(document)


def compute_review_hash(review: Mapping[str, Any]) -> str:
    if not isinstance(review, Mapping):
        raise TypeError("review must be a mapping")
    payload = {key: deepcopy(value) for key, value in review.items() if key != "review_hash"}
    return _hash(payload)


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def _find_prohibited_outputs(value: Any, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key) in _PROHIBITED_OUTPUT_KEYS and _nonempty(item):
                findings.append(child)
            findings.extend(_find_prohibited_outputs(item, path=child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            findings.extend(_find_prohibited_outputs(item, path=f"{path}[{index}]"))
    return findings


def _decision_contract(document: Mapping[str, Any]) -> dict[str, Any]:
    contract = document.get("decision_scope_contract")
    if not isinstance(contract, Mapping):
        raise DecisionQualificationError("decision_scope_contract_missing")
    if contract.get("scope") != DECISION_SCOPE:
        raise DecisionQualificationError("decision_scope_contract_mismatch")
    claim_ids = _text_list(contract.get("decision_claim_ids"))
    condition_ids = _text_list(contract.get("condition_ids"))
    if not claim_ids:
        raise DecisionQualificationError("decision_claim_ids_missing")
    if not condition_ids:
        raise DecisionQualificationError("conditional_entry_conditions_missing")
    if tuple(_text_list(contract.get("allowed_uses"))) != tuple(sorted(ALLOWED_USES)):
        raise DecisionQualificationError("decision_allowed_uses_mismatch")
    if tuple(_text_list(contract.get("prohibited_uses"))) != tuple(sorted(PROHIBITED_USES)):
        raise DecisionQualificationError("decision_prohibited_uses_mismatch")
    return {
        "claim_ids": claim_ids,
        "condition_ids": condition_ids,
    }


def _verify_report_delivery_minimum(
    validation: Mapping[str, Any], evidence: Mapping[str, Any]
) -> None:
    if validation.get("schema_version") != "dds.report-delivery-validation/1.0":
        raise DecisionQualificationError("report_delivery_validation_schema_mismatch")
    if validation.get("authority") != AUTHORITY:
        raise DecisionQualificationError("report_delivery_validation_authority_mismatch")
    if validation.get("status") != "validated_for_report_delivery":
        raise DecisionQualificationError("report_delivery_validation_not_ready")
    expected_hash = str(validation.get("validation_hash") or "")
    payload = {key: deepcopy(value) for key, value in validation.items() if key != "validation_hash"}
    if not _SHA256_RE.fullmatch(expected_hash) or expected_hash != _hash(payload):
        raise DecisionQualificationError("report_delivery_validation_hash_mismatch")
    package_hash = str(evidence.get("package_hash") or "")
    if not _SHA256_RE.fullmatch(package_hash):
        raise DecisionQualificationError("evidence_package_hash_missing")


def _verify_review(
    review: Mapping[str, Any],
    *,
    evidence_package_hash: str,
    report_document_hash: str,
    required_claim_ids: Sequence[str],
    required_condition_ids: Sequence[str],
) -> None:
    if review.get("schema_version") != DECISION_REVIEW_SCHEMA:
        raise DecisionQualificationError("decision_review_schema_mismatch")
    if review.get("decision_scope") != DECISION_SCOPE:
        raise DecisionQualificationError("decision_review_scope_mismatch")
    if review.get("outcome") not in ALLOWED_OUTCOMES:
        raise DecisionQualificationError("decision_review_outcome_invalid")
    if review.get("acknowledged") is not True:
        raise DecisionQualificationError("decision_review_not_acknowledged")
    reviewer_hash = str(review.get("reviewer_id_hash") or "")
    if not _SHA256_RE.fullmatch(reviewer_hash):
        raise DecisionQualificationError("decision_review_reviewer_hash_invalid")
    if not str(review.get("reviewer_role") or "").strip():
        raise DecisionQualificationError("decision_review_reviewer_role_missing")
    if not str(review.get("reviewed_at") or "").strip():
        raise DecisionQualificationError("decision_review_timestamp_missing")
    if review.get("evidence_package_hash") != evidence_package_hash:
        raise DecisionQualificationError("decision_review_evidence_hash_mismatch")
    if review.get("report_document_hash") != report_document_hash:
        raise DecisionQualificationError("decision_review_document_hash_mismatch")
    if not set(required_claim_ids).issubset(_text_list(review.get("approved_claim_ids"))):
        raise DecisionQualificationError("decision_review_claim_approval_incomplete")
    if not set(required_condition_ids).issubset(_text_list(review.get("condition_ids"))):
        raise DecisionQualificationError("decision_review_conditions_incomplete")
    expected_review_hash = str(review.get("review_hash") or "")
    if not _SHA256_RE.fullmatch(expected_review_hash):
        raise DecisionQualificationError("decision_review_hash_invalid")
    if expected_review_hash != compute_review_hash(review):
        raise DecisionQualificationError("decision_review_hash_mismatch")


def build_decision_validation(
    evidence: Mapping[str, Any],
    report_delivery_validation: Mapping[str, Any],
    document: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    report_delivery_verifier: Callable[[Mapping[str, Any], Mapping[str, Any]], Any]
    | None = None,
) -> dict[str, Any]:
    """Create a DDS attestation for a hash-bound conditional-entry decision."""

    if report_delivery_verifier is not None:
        report_delivery_verifier(report_delivery_validation, evidence)
    else:
        _verify_report_delivery_minimum(report_delivery_validation, evidence)

    prohibited = _find_prohibited_outputs(document)
    if prohibited:
        raise DecisionQualificationError(
            "conditional_entry_contains_prohibited_outputs:" + ",".join(prohibited)
        )
    contract = _decision_contract(document)
    document_hash = compute_report_document_hash(document)
    package_hash = str(evidence.get("package_hash") or "")
    _verify_review(
        review,
        evidence_package_hash=package_hash,
        report_document_hash=document_hash,
        required_claim_ids=contract["claim_ids"],
        required_condition_ids=contract["condition_ids"],
    )

    result = {
        "schema_version": DECISION_VALIDATION_SCHEMA,
        "authority": AUTHORITY,
        "validator": VALIDATOR,
        "status": VALID_STATUS,
        "decision_scope": DECISION_SCOPE,
        "decision_eligibility": True,
        "validated_for_decision": True,
        "allowed_uses": list(ALLOWED_USES),
        "prohibited_uses": list(PROHIBITED_USES),
        "evidence_package_hash": package_hash,
        "report_delivery_validation_hash": report_delivery_validation.get(
            "validation_hash"
        ),
        "report_document_hash": document_hash,
        "decision_review_hash": review.get("review_hash"),
        "review_outcome": review.get("outcome"),
        "approved_claim_ids": contract["claim_ids"],
        "condition_ids": contract["condition_ids"],
    }
    result["validation_hash"] = _hash(result)
    return result


def verify_decision_validation(
    validation: Mapping[str, Any],
    evidence: Mapping[str, Any],
    document: Mapping[str, Any],
    review: Mapping[str, Any],
) -> bool:
    if not isinstance(validation, Mapping):
        raise DecisionQualificationError("decision_validation_not_object")
    expected_hash = str(validation.get("validation_hash") or "")
    payload = {key: deepcopy(value) for key, value in validation.items() if key != "validation_hash"}
    if not _SHA256_RE.fullmatch(expected_hash) or expected_hash != _hash(payload):
        raise DecisionQualificationError("decision_validation_hash_mismatch")
    if validation.get("schema_version") != DECISION_VALIDATION_SCHEMA:
        raise DecisionQualificationError("decision_validation_schema_mismatch")
    if validation.get("authority") != AUTHORITY or validation.get("validator") != VALIDATOR:
        raise DecisionQualificationError("decision_validation_authority_mismatch")
    if validation.get("status") != VALID_STATUS:
        raise DecisionQualificationError("decision_validation_status_mismatch")
    if validation.get("decision_scope") != DECISION_SCOPE:
        raise DecisionQualificationError("decision_validation_scope_mismatch")
    if validation.get("decision_eligibility") is not True:
        raise DecisionQualificationError("decision_validation_eligibility_missing")
    if validation.get("validated_for_decision") is not True:
        raise DecisionQualificationError("decision_validation_flag_missing")
    if validation.get("evidence_package_hash") != evidence.get("package_hash"):
        raise DecisionQualificationError("decision_validation_evidence_hash_mismatch")
    if validation.get("report_document_hash") != compute_report_document_hash(document):
        raise DecisionQualificationError("decision_validation_document_hash_mismatch")
    if validation.get("decision_review_hash") != review.get("review_hash"):
        raise DecisionQualificationError("decision_validation_review_hash_mismatch")
    contract = _decision_contract(document)
    _verify_review(
        review,
        evidence_package_hash=str(evidence.get("package_hash") or ""),
        report_document_hash=compute_report_document_hash(document),
        required_claim_ids=contract["claim_ids"],
        required_condition_ids=contract["condition_ids"],
    )
    if _find_prohibited_outputs(document):
        raise DecisionQualificationError("decision_validation_prohibited_outputs")
    return True


__all__ = [
    "ALLOWED_OUTCOMES",
    "ALLOWED_USES",
    "AUTHORITY",
    "DECISION_REVIEW_SCHEMA",
    "DECISION_SCOPE",
    "DECISION_VALIDATION_SCHEMA",
    "DecisionQualificationError",
    "PROHIBITED_USES",
    "VALID_STATUS",
    "build_decision_validation",
    "compute_report_document_hash",
    "compute_review_hash",
    "verify_decision_validation",
]

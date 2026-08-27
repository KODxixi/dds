"""Evidence-bound DecisionScenario generation with an explicit rule fallback."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, Mapping

try:
    from ark_runtime import ArkRuntime, ArkRuntimeConfig, get_ark_runtime
    from garchos_openapi_contract import OpenAPIContract
except ModuleNotFoundError:  # Package import: ``import scripts.decision_field_scenarios``.
    from .ark_runtime import ArkRuntime, ArkRuntimeConfig, get_ark_runtime
    from .garchos_openapi_contract import OpenAPIContract


RULE_BASELINE_NAME = "规则基线（非经营结论）"
RULE_BASELINE_SUMMARY = (
    "Ark 未配置、不可用或返回内容未通过证据校验；本基线只保留已确认约束，"
    "不包含售价、货值、去化或收益事实，所有经营判断均须人工补充证据并复核。"
)
_APPROVED_SCENARIO_NAME = "证据约束方案"
_APPROVED_SCENARIO_SUMMARY = "仅依据已核验规划条件形成的方案。"
_APPROVED_ASSUMPTIONS = {
    ("manual_review", "经营指标仍需人工复核"),
}
_APPROVED_RISKS = {
    "evidence_gap": {
        "severity": "medium",
        "statement": "缺少经营结果证据。",
    }
}
_UNSUPPORTED_BUSINESS_FACT_TERMS = (
    "售价",
    "货值",
    "去化",
    "收益",
    "溢价",
    "成交",
    "利润",
    "现金流",
    "IRR",
    "ROI",
)


class ArkScenarioProvider:
    """Small Ark Responses API adapter; credentials remain constructor-private."""

    def __init__(
        self,
        *,
        api_key: str,
        model_alias: str,
        base_url: str | None = None,
        timeout_seconds: float = 90.0,
        max_output_tokens: int = 6000,
        client: Any | None = None,
        runtime: ArkRuntime | None = None,
    ) -> None:
        self.max_output_tokens = max(256, int(max_output_tokens))
        if runtime is None:
            runtime = ArkRuntime(
                ArkRuntimeConfig(
                    api_key=str(api_key or "").strip(),
                    model_aliases=(str(model_alias or "").strip(),),
                    base_url=str(base_url or "").strip() or None,
                    timeout_seconds=float(timeout_seconds),
                    max_output_tokens=self.max_output_tokens,
                    max_attempts=3,
                ),
                client=client,
            )
        self._runtime = runtime

    def generate(
        self,
        *,
        project_id: str,
        workflow_id: str,
        project_context: dict[str, Any],
        case_matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        public_input = {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "project_context": deepcopy(project_context),
            "case_matches": deepcopy(case_matches),
        }
        instructions = (
            "你是 DDS 审慎研判引擎。只输出 JSON 对象，顶层字段必须是 decision_scenarios。"
            "输出 1 至 3 个方案。不得补写售价、货值、去化、收益等经营事实。"
            "任何数值 metric 只能逐字复用 ProjectContext 中 required constraint 的 code/value，"
            "并携带输入中的完整 EvidenceRef；每条 risk 和每个方案也必须引用固定 EvidenceRef，"
            "不得创建新的 EvidenceRef。"
            "非基线方案 name 必须逐字为“证据约束方案”，summary 必须逐字为"
            "“仅依据已核验规划条件形成的方案。”；assumptions 只允许 manual_review/"
            "“经营指标仍需人工复核”，risks 只允许 evidence_gap/“缺少经营结果证据。”。"
            "project_id 与 workflow_id 必须与输入一致。证据不足时 metrics 留空，并使用 evidence_gap。"
        )
        text = self._runtime.complete(
            json.dumps(public_input, ensure_ascii=False, sort_keys=True),
            instructions=instructions,
            max_output_tokens=self.max_output_tokens,
            temperature=0.2,
            json_object=True,
        )
        parsed = json.loads(text)
        scenarios = parsed.get("decision_scenarios") if isinstance(parsed, dict) else None
        if not isinstance(scenarios, list):
            raise ValueError("Ark response is missing decision_scenarios")
        return scenarios


def create_ark_scenario_provider(
    environ: Mapping[str, str] | None = None,
) -> ArkScenarioProvider | None:
    try:
        if environ is None:
            runtime = get_ark_runtime()
            values = os.environ
        else:
            values = environ
            runtime = ArkRuntime(ArkRuntimeConfig.from_environment(values))
        return ArkScenarioProvider(
            api_key="configured-inside-runtime",
            model_alias="configured-inside-runtime",
            max_output_tokens=int(values.get("DDS_ARK_MAX_OUTPUT_TOKENS") or 6000),
            runtime=runtime,
        )
    except Exception:
        return None


def build_decision_scenarios(
    project: dict[str, Any],
    *,
    provider: Any | None = None,
    contract: OpenAPIContract | None = None,
    now: Callable[[], str] | None = None,
) -> list[dict[str, Any]]:
    active_contract = contract or OpenAPIContract.from_environment()
    analysis_context = project.get("analysis_context") or {}
    project_context = analysis_context.get("project_context") or {}
    case_matches = analysis_context.get("case_matches") or []
    project_id = str(project.get("id") or "").strip()
    workflow_id = str(analysis_context.get("workflow_id") or "").strip()
    active_contract.validate_schema("StableId", project_id)
    active_contract.validate_schema("StableId", workflow_id)
    active_contract.validate_schema("ProjectContext", project_context)
    for match in case_matches:
        active_contract.validate_schema("CaseMatch", match)

    baseline = _rule_baseline(
        project_id=project_id,
        workflow_id=workflow_id,
        project_context=project_context,
        case_matches=case_matches,
        created_at=(now or _now)(),
    )
    active_contract.validate_schema("DecisionScenario", baseline)
    if provider is None:
        return [baseline]
    try:
        scenarios = provider.generate(
            project_id=project_id,
            workflow_id=workflow_id,
            project_context=deepcopy(project_context),
            case_matches=deepcopy(case_matches),
        )
        return _validated_provider_scenarios(
            scenarios,
            project_id=project_id,
            workflow_id=workflow_id,
            project_context=project_context,
            case_matches=case_matches,
            contract=active_contract,
        )
    except Exception:
        return [baseline]


def _rule_baseline(
    *,
    project_id: str,
    workflow_id: str,
    project_context: dict[str, Any],
    case_matches: list[dict[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    context_revision = max(1, int(project_context.get("context_revision") or 1))
    seed = f"{project_id}:{workflow_id}:{context_revision}".encode("utf-8")
    evidence_refs = _unique_evidence(case_matches)
    return {
        "scenario_id": f"scenario_rule_{sha256(seed).hexdigest()[:20]}",
        "project_id": project_id,
        "workflow_id": workflow_id,
        "revision": context_revision,
        "name": RULE_BASELINE_NAME,
        "summary": RULE_BASELINE_SUMMARY,
        "baseline": True,
        "assumptions": [
            {
                "code": "rule_baseline",
                "statement": "当前结果只复述已确认约束，不构成投资、设计或经营承诺。",
            }
        ],
        "metrics": [],
        "risks": [
            {
                "code": "evidence_gap",
                "severity": "high",
                "statement": "缺少可核验经营证据，相关事实与计算必须由项目用户人工补充并复核。",
                "evidence_refs": [],
            }
        ],
        "evidence_refs": evidence_refs,
        "reason_codes": ["evidence_gap", "rule_out_of_scope"],
        "confidence": 0,
        "created_at": str(created_at),
    }


def _validated_provider_scenarios(
    scenarios: Any,
    *,
    project_id: str,
    workflow_id: str,
    project_context: dict[str, Any],
    case_matches: list[dict[str, Any]],
    contract: OpenAPIContract,
) -> list[dict[str, Any]]:
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 3:
        raise ValueError("Ark must return one to three DecisionScenario objects")
    allowed = {
        item["evidence_id"]: item
        for item in _unique_evidence(case_matches)
    }
    accepted: list[dict[str, Any]] = []
    confirmed_metrics = _confirmed_constraint_metrics(project_context)
    for raw in scenarios:
        scenario = deepcopy(raw)
        contract.validate_schema("DecisionScenario", scenario)
        if scenario.get("project_id") != project_id or scenario.get("workflow_id") != workflow_id:
            raise ValueError("Ark scenario identity does not match request")
        if not scenario.get("evidence_refs"):
            raise ValueError("Ark non-baseline scenarios require fixed evidence")
        _validate_approved_non_factual_copy(scenario)
        narrative = "\n".join(
            [
                str(scenario.get("name") or ""),
                str(scenario.get("summary") or ""),
                *(str(item.get("statement") or "") for item in scenario.get("assumptions") or []),
                *(str(item.get("statement") or "") for item in scenario.get("risks") or []),
            ]
        )
        if any(term.lower() in narrative.lower() for term in _UNSUPPORTED_BUSINESS_FACT_TERMS):
            raise ValueError("Ark scenario contains unsupported business facts")
        for metric in scenario.get("metrics") or []:
            if not metric.get("evidence_refs"):
                raise ValueError("Ark numeric metrics require verified evidence")
            metric_key = (str(metric.get("code") or ""), _normalized_number(metric.get("value")))
            if metric_key not in confirmed_metrics:
                raise ValueError("Ark metric is not a confirmed project constraint")
            constraint = confirmed_metrics[metric_key]
            metric["code"] = str(constraint.get("code") or "")
            metric["label"] = str(constraint.get("label") or "")
            metric["value"] = constraint.get("value")
            metric["unit"] = str(constraint.get("unit") or "")
            metric.pop("method", None)
        for risk in scenario.get("risks") or []:
            if not risk.get("evidence_refs"):
                raise ValueError("Ark risks require fixed evidence")
            template = _APPROVED_RISKS[str(risk.get("code") or "")]
            risk["severity"] = template["severity"]
            risk["statement"] = template["statement"]
        references = list(scenario.get("evidence_refs") or [])
        for metric in scenario.get("metrics") or []:
            references.extend(metric.get("evidence_refs") or [])
        for risk in scenario.get("risks") or []:
            references.extend(risk.get("evidence_refs") or [])
        for evidence in references:
            evidence_id = str(evidence.get("evidence_id") or "")
            if evidence_id not in allowed or evidence != allowed[evidence_id]:
                raise ValueError("Ark scenario references unverified evidence")
        contract.validate_schema("DecisionScenario", scenario)
        accepted.append(scenario)
    return accepted


def _validate_approved_non_factual_copy(scenario: dict[str, Any]) -> None:
    """Fail closed: narrative fields cannot assert facts that EvidenceRef cannot bind."""
    if scenario.get("name") != _APPROVED_SCENARIO_NAME:
        raise ValueError("Ark scenario name is outside the approved non-factual template")
    if scenario.get("summary") != _APPROVED_SCENARIO_SUMMARY:
        raise ValueError("Ark scenario summary is outside the approved non-factual template")
    assumptions = {
        (str(item.get("code") or ""), str(item.get("statement") or ""))
        for item in scenario.get("assumptions") or []
    }
    if not assumptions.issubset(_APPROVED_ASSUMPTIONS):
        raise ValueError("Ark scenario assumptions are outside the approved non-factual template")
    for item in scenario.get("risks") or []:
        code = str(item.get("code") or "")
        template = _APPROVED_RISKS.get(code)
        if template is None or str(item.get("statement") or "") != template["statement"]:
            raise ValueError("Ark scenario risks are outside the approved non-factual template")


def _confirmed_constraint_metrics(
    project_context: dict[str, Any],
) -> dict[tuple[str, float], dict[str, Any]]:
    confirmed: dict[tuple[str, float], dict[str, Any]] = {}
    for constraint in project_context.get("constraints") or []:
        if not constraint.get("required"):
            continue
        normalized = _normalized_number(constraint.get("value"))
        if normalized is not None:
            confirmed[(str(constraint.get("code") or ""), normalized)] = deepcopy(
                constraint
            )
    return confirmed


def _normalized_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return round(float(value), 9)
    except (TypeError, ValueError):
        return None


def _unique_evidence(case_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for match in case_matches:
        for evidence in match.get("evidence_refs") or []:
            evidence_id = str(evidence.get("evidence_id") or "")
            existing = by_id.get(evidence_id)
            if existing is not None and existing != evidence:
                raise ValueError("evidence_id maps to conflicting evidence")
            if existing is None:
                copied = deepcopy(evidence)
                by_id[evidence_id] = copied
                found.append(copied)
    return found


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

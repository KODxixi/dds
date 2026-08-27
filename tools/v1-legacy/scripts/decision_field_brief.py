"""Deterministic, auditable project brief compiler."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from decision_field_models import now_iso, transition


QUESTIONS = (
    {
        "id": "primary_goal",
        "prompt": "这次联席研判首先要回答什么？",
        "why": "系统会据此决定证据优先级和报告主线。",
        "type": "choice",
        "required": True,
        "choices": [
            {"value": "entry_decision", "label": "值不值得进入"},
            {"value": "land_boundary", "label": "拿地边界"},
            {"value": "product_direction", "label": "产品方向"},
            {"value": "design_brief", "label": "设计任务书"},
            {"value": "investment_committee", "label": "投委会汇报"},
        ],
    },
    {
        "id": "project_stage",
        "prompt": "项目现在处于哪个阶段？",
        "why": "同一证据在机会筛选和投委会阶段需要不同严谨度。",
        "type": "choice",
        "required": True,
        "choices": [
            {"value": "screening", "label": "机会筛选"},
            {"value": "initial_review", "label": "前期初判"},
            {"value": "underwriting", "label": "拿地测算"},
            {"value": "committee", "label": "投委会"},
            {"value": "post_acquisition", "label": "已拿地深化"},
        ],
    },
    {
        "id": "hard_constraints",
        "prompt": "有哪些不可接受的错误或硬约束？",
        "why": "这些条件会进入 hard stop，而不是被综合评分稀释。",
        "type": "text",
        "required": False,
        "placeholder": "例如：地价上限、限高、交付时间或必须保留的资源",
    },
    {
        "id": "financial_assumptions",
        "prompt": "已知的容积率或目标售价是多少？",
        "why": "缺少这些数字时仍可研判，但拿地和产品结论置信度会降低。",
        "type": "numbers",
        "required": False,
        "fields": [
            {"id": "far", "label": "容积率", "min": 0.1, "max": 20},
            {"id": "expected_price", "label": "目标售价（元/㎡）", "min": 5000, "max": 200000},
        ],
    },
    {
        "id": "vision",
        "prompt": "对产品、客群或风险还有什么假设？",
        "why": "自由假设会被标为用户输入，不会伪装成外部事实。",
        "type": "text",
        "required": False,
        "placeholder": "例如：低密改善，优先判断总价与去化风险",
    },
)


class BriefCompiler:
    def __init__(self) -> None:
        self.questions = QUESTIONS
        self._by_id = {question["id"]: question for question in QUESTIONS}

    def next_question(self, project: dict[str, Any]) -> dict[str, Any] | None:
        answered = set(project.get("brief_answered") or [])
        for question in self.questions:
            if question["id"] not in answered:
                return deepcopy(question)
        return None

    def answer(
        self,
        project: dict[str, Any],
        question_id: str,
        value: Any,
        *,
        skip: bool = False,
    ) -> dict[str, Any]:
        if question_id not in self._by_id:
            raise ValueError(f"unknown brief question: {question_id}")
        question = self._by_id[question_id]
        if skip and question["required"]:
            raise ValueError(f"{question_id} is required")

        updated = deepcopy(project)
        normalized = None if skip else self._normalize(question, value)
        brief = updated["brief"]
        if question_id == "financial_assumptions":
            assumptions = normalized or {}
            brief["far"] = assumptions.get("far")
            brief["expected_price"] = assumptions.get("expected_price")
        else:
            brief[question_id] = normalized

        answered = list(updated.get("brief_answered") or [])
        if question_id not in answered:
            answered.append(question_id)
        updated["brief_answered"] = answered
        skipped = list(updated.get("brief_skipped") or [])
        if skip and question_id not in skipped:
            skipped.append(question_id)
        elif not skip and question_id in skipped:
            skipped.remove(question_id)
        updated["brief_skipped"] = skipped
        updated["completeness"] = round(len(answered) / len(self.questions), 2)
        updated["updated_at"] = now_iso()
        return updated

    def can_analyze(self, project: dict[str, Any]) -> bool:
        answered = set(project.get("brief_answered") or [])
        required = {question["id"] for question in self.questions if question["required"]}
        return required.issubset(answered)

    def finalize(self, project: dict[str, Any], *, direct: bool = False) -> dict[str, Any]:
        if not self.can_analyze(project):
            raise ValueError("primary_goal and project_stage are required before analysis")
        updated = deepcopy(project)
        answered = set(updated.get("brief_answered") or [])
        skipped = set(updated.get("brief_skipped") or [])
        if direct:
            existing_gaps = {gap.get("field") for gap in updated.get("evidence_gaps") or []}
            for question in self.questions:
                missing = question["id"] not in answered or question["id"] in skipped
                if not question["required"] and missing and question["id"] not in existing_gaps:
                    updated.setdefault("evidence_gaps", []).append(
                        {
                            "field": question["id"],
                            "reason": "用户选择直接研判，报告需降低相关结论置信度",
                            "severity": "medium",
                        }
                    )
        return transition(updated, "ready_for_analysis")

    def _normalize(self, question: dict[str, Any], value: Any) -> Any:
        question_id = question["id"]
        if question["type"] == "choice":
            allowed = {choice["value"] for choice in question["choices"]}
            if value not in allowed:
                raise ValueError(f"invalid value for {question_id}")
            return value
        if question_id == "hard_constraints":
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            text = str(value or "").strip()
            return [item.strip() for item in re.split(r"[\n;；]+", text) if item.strip()]
        if question_id == "financial_assumptions":
            if value in (None, ""):
                return {}
            if not isinstance(value, dict):
                raise ValueError("financial_assumptions must be an object")
            result: dict[str, float] = {}
            for field in question["fields"]:
                raw = value.get(field["id"])
                if raw in (None, ""):
                    continue
                number = float(raw)
                if not (field["min"] <= number <= field["max"]):
                    raise ValueError(f"{field['id']} is outside allowed range")
                result[field["id"]] = number
            return result
        text = str(value or "").strip()
        if len(text) > 500:
            raise ValueError(f"{question_id} is too long")
        return text or None

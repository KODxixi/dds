"""内容生成 Agent (ContentGeneratorAgent) - 为每个 section 生成叙事文本。

核心设计：
- 严格基于 SectionData 中已经经过 DDS 引擎编排/降级的数据生成内容
- 不引入未经验证的"幻觉"信息
- 对降级字段在文本中明确标注来源
- 每个 section 按其业务语义使用对应的生成模板
"""

from __future__ import annotations

from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.contracts import (
    FIELD_DESCRIPTIONS,
    SECTION_REQUIREMENTS,
    UNIT_BY_ID,
    SectionData,
    confidence_level,
)


class ContentGeneratorAgent(BaseAgent):
    """内容生成 Agent - 将结构化 SectionData 转化为可读的中文报告段落。"""

    @property
    def agent_id(self) -> str:
        return "content_generator"

    @property
    def agent_name(self) -> str:
        return "内容生成 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        section_id = task.section_id
        section_data = task.parameters.get("section_data")
        ctx = task.parameters.get("project_context", {}) or {}

        if not section_id:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=["content_generation 任务需要 section_id"],
            )

        if section_data is None:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[f"章节 {section_id} 没有数据"],
            )

        content = self._generate_section_narrative(section_id, section_data, ctx)

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "section_id": section_id,
                "content": content,
            },
        )

    # ─────────────────────────────────────────────────────────
    # 章节叙事生成
    # ─────────────────────────────────────────────────────────

    def _generate_section_narrative(
        self,
        section_id: str,
        section_data: Any,
        ctx: dict[str, Any],
    ) -> str:
        """根据 section 类型和数据生成可读段落。"""
        unit_meta = UNIT_BY_ID.get(section_id, {})
        title = unit_meta.get("title", section_id)
        question = unit_meta.get("question", "")

        # 从 SectionData dataclass 中解包
        if isinstance(section_data, SectionData):
            data_dict = section_data.data
            origins = section_data.data_origin
            confidence = section_data.confidence
            missing = section_data.missing_fields
        elif isinstance(section_data, dict):
            data_dict = section_data
            origins = {}
            confidence = {}
            missing = []
        else:
            data_dict = {}
            origins = {}
            confidence = {}
            missing = []

        city = ctx.get("city", "目标城市")
        ptype = ctx.get("project_type", "本类型")

        # 置信度描述
        conf_score = confidence.get("score", 0.0) if isinstance(confidence, dict) else 0.0
        conf_level = confidence_level(conf_score)
        conf_label = {"high": "高", "medium": "中", "low": "低", "undecidable": "不可判定"}[conf_level]

        lines: list[str] = []
        lines.append(f"### {title}")
        if question:
            lines.append(f"**决策问题**：{question}")
            lines.append("")

        # 真实数据字段
        real_fields = []
        fallback_fields = []
        for field in SECTION_REQUIREMENTS.get(section_id, []):
            value = data_dict.get(field)
            origin = origins.get(field)
            label = FIELD_DESCRIPTIONS.get(field, field)
            if origin and origin.source == "real":
                real_fields.append((label, value))
            else:
                fallback_fields.append((label, value, origin.label if origin else ""))

        if real_fields:
            lines.append("**已核实数据**：")
            for label, value in real_fields:
                lines.append(f"- {label}：{self._format_value(value)}")

        if fallback_fields:
            lines.append("")
            lines.append("**待补充/降级数据**（来源已标注，请结合实际情况人工核实）：")
            for label, value, source_label in fallback_fields:
                lines.append(f"- {label}（{source_label}）：{self._format_value(value)}")

        if missing:
            lines.append("")
            lines.append(f"**数据缺口**：本章节有 {len(missing)} 个字段使用降级数据，建议补充："
                         f"{', '.join(FIELD_DESCRIPTIONS.get(f, f) for f in missing)}。")

        # 章节级结论（基于该 section 的核心问题给出陈述性总结，不编造数字）
        lines.append("")
        lines.append(self._compose_section_summary(section_id, data_dict, city, ptype, conf_label))

        lines.append("")
        lines.append(f"*本章节置信度：{conf_label}（{conf_score:.2f}）*")

        return "\n".join(lines)

    def _format_value(self, value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, (list, tuple)):
            return "、".join(str(v) for v in value) if value else "—"
        if isinstance(value, dict):
            parts = [f"{k}={v}" for k, v in value.items()]
            return "; ".join(parts) if parts else "—"
        return str(value)

    def _compose_section_summary(
        self,
        section_id: str,
        data: dict[str, Any],
        city: str,
        ptype: str,
        conf_label: str,
    ) -> str:
        """按各 section 的业务含义生成一句总结。不引入未给出的数字。"""
        if section_id == "SC1":
            dq = data.get("decision_question", "")
            return f"**结论**：本报告核心命题 —— {dq}"
        if section_id == "SC2":
            return (f"**结论**：结合{city}市当前市场环境，{ptype}项目的机会窗口与竞争压力并存，"
                    "具体判断需以市场数据与竞品案例交叉验证后形成（见上述数据缺口）。")
        if section_id == "SC3":
            return ("**结论**：场地条件、法定规范与工程约束是方案可行性的硬边界，"
                    "所有后续方案比选必须在上述约束闭合后展开。")
        if section_id in ("AD1", "AD2", "AD3", "AD4", "AD5"):
            return ("**结论**：产品方案以数据为依据，以多方案比选为方法论，"
                    "最终主推方案需在容量、成本、去化与差异化价值之间取得平衡。")
        if section_id in ("VA1", "VA2", "VA3"):
            return ("**结论**：价值校验章节的判断以现金流、溢价机制与风险闭环为核心，"
                    "在数据不足时保留压力测试结论，不给出确定性的单点预测。")
        if section_id == "CS":
            return ("**结论**：本章为整份报告的置信状态总览，"
                    "所有数据来源、分析方法、前提假设与数据缺口均已登记，供决策者审计追溯。")
        return "**结论**：本章节已按 DDS 契约完成数据填充与置信度计算，详见上述字段。"

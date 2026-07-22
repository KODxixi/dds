"""DDS v2 核心架构测试。

验证：
1. 数据主动管理 - 检测缺失并显式标记，永不静默缺失
2. 契约强制校验 - 12 个 section 完整性校验
3. 置信度计算 - 每个字段标记来源，每个 section 有置信度
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 目录到路径
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path.absolute()))

from dds.contracts import (  # noqa: E402
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    ContractViolationError,
    ResolvedStatus,
)
from dds.data import DataOrchestrator  # noqa: E402
from dds.engine import ContractEnforcer  # noqa: E402


async def test_data_orchestrator_never_empty():
    """测试：数据永远不为空，缺失时自动降级。"""
    print("\n=== 测试 1: DataOrchestrator 永不空值 ===")

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")

    # 传入空数据，模拟完全没有数据的情况
    project_context = {"city": "北京", "project_type": "住宅"}
    empty_data = {}

    section = await orchestrator.ensure_section_data("SC1", project_context, empty_data)

    # 验证：所有必填字段都被填充了
    required_fields = SECTION_REQUIREMENTS["SC1"]
    print(f"SC1 必填字段数: {len(required_fields)}")

    for field in required_fields:
        assert field in section.data, f"字段 [{field}] 应该已被填充，但没有"
        value = section.data[field]
        origin = section.data_origin[field]
        print(f"  ✓ {field}: {str(value)[:50]}... (来源: {origin.label})")

    print(f"  置信度分数: {section.confidence['score']:.2f}")
    print(f"  置信度等级: {section.confidence['level_label']}")
    print("  ✓ 所有字段都已填充，没有静默空值")


async def test_all_sections_complete():
    """测试：所有 12 个 section 都能完整生成。"""
    print("\n=== 测试 2: 12 个 Decision-Unit 完整性 ===")

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    project_context = {"city": "北京", "project_type": "住宅"}

    all_sections = await orchestrator.ensure_all_sections(project_context, {})

    # 验证：12 个 section 全部存在
    assert len(all_sections) == 12, f"应该有 12 个 section，实际有 {len(all_sections)}"
    print("✓ 成功生成全部 12 个 section")

    for section_id in VALID_SECTION_IDS:
        section = all_sections[section_id]
        confidence = section.confidence["score"]
        real_ratio = section.real_data_ratio()
        print(f"  {section_id}: 置信度={confidence:.2f}, 真实数据比例={real_ratio:.0%}")

    # 验证 CS 章节包含数据缺口汇总
    cs_section = all_sections["CS"]
    assert "data_gaps" in cs_section.data, "CS 章节应该包含数据缺口汇总"
    print(f"  ✓ CS 章节数据缺口数: {len(cs_section.data['data_gaps'])}")

    # 生成数据质量报告
    quality_report = orchestrator.get_data_quality_report(all_sections)
    print(f"  ✓ 整体置信度: {quality_report['overall_confidence']['score']:.2f}")
    print(f"  ✓ 真实数据占比: {quality_report['summary']['real_ratio']:.0%}")


async def test_contract_enforcer():
    """测试：契约强制校验。"""
    print("\n=== 测试 3: ContractEnforcer 契约校验 ===")

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    enforcer = ContractEnforcer()

    project_context = {"city": "北京", "project_type": "住宅"}

    # 无 EvidenceRecord 时仅结构 gate 可通过，证据/决策/交付必须失败
    all_sections = await orchestrator.ensure_all_sections(project_context, {})
    result = enforcer.validate_report(all_sections, {})

    print(f"  完整数据校验结果: {'PASS' if result.valid else 'FAIL'}")
    print(f"  错误数: {len(result.errors)}, 警告数: {len(result.warnings)}")
    print(f"  整体置信度: {result.overall_confidence:.2f}")
    assert result.gate_status["structure"] == "pass"
    assert result.gate_status["evidence"] == "fail"
    assert not result.valid
    assert result.overall_confidence == 0.0

    # 测试 2: 缺少一个 section 应该失败
    print("\n  故意删除 SC1 测试校验失败...")
    incomplete_sections = {k: v for k, v in all_sections.items() if k != "SC1"}

    result2 = enforcer.validate_report(incomplete_sections, {})
    assert not result2.valid, "缺少 section 应该校验失败"
    print(f"  ✓ 缺少 SC1 时校验失败，错误: {result2.errors[0]}")

    # 测试 3: enforce_contract 应该抛出异常
    try:
        enforcer.enforce_contract(incomplete_sections, {})
        assert False, "应该抛出 ContractViolationError"
    except ContractViolationError as e:
        print(f"  ✓ 强制执行契约正确抛出异常: {str(e)[:50]}...")


async def test_mixed_real_and_fallback_data():
    """测试：混合真实数据和降级数据的场景。"""
    print("\n=== 测试 4: 混合真实数据与降级数据 ===")

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    project_context = {"city": "北京", "project_type": "住宅"}

    # 传入部分真实数据，部分缺失
    existing_data = {
        "SC1": {
            "project_id": "TEST-001",  # 真实数据
            "decision_question": "这是一个测试决策问题",  # 真实数据
            # evidence_boundary 缺失，应该自动降级
            # base_date 缺失，应该自动降级
        }
    }

    section = await orchestrator.ensure_section_data(
        "SC1", project_context, existing_data["SC1"]
    )

    # 原始值保留，但无 EvidenceRecord 时只能是 provided_unverified/partial。
    assert section.data["project_id"] == "TEST-001"
    assert section.data_origin["project_id"].source == "provided_unverified"
    assert section.field_resolution("project_id").status == ResolvedStatus.PARTIAL

    # 缺失字段必须是显式 human_input，而不是伪造基准。
    assert "evidence_boundary" in section.data
    assert section.data_origin["evidence_boundary"].source == "human_input"
    assert (
        section.field_resolution("evidence_boundary").status
        == ResolvedStatus.HUMAN_INPUT
    )
    assert section.confidence["score"] == 0.0

    print("  ✓ 原始值保留且明确标为未核验，缺失数据显式登记")
    print("  ✓ 未核验字段: project_id, decision_question")
    print(
        f"  ✓ 自动填充: evidence_boundary={section.data_origin['evidence_boundary'].label}"
    )
    print(f"  ✓ 自动填充: base_date={section.data_origin['base_date'].label}")
    print(f"  ✓ 置信度: {section.confidence['score']:.2f}")


async def main():
    print("DDS v2 核心架构测试")
    print("=" * 60)

    await test_data_orchestrator_never_empty()
    await test_all_sections_complete()
    await test_contract_enforcer()
    await test_mixed_real_and_fallback_data()

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("\n核心特性验证：")
    print("  ✓ 数据主动管理 - 检测缺失、显式 unknown/human_input、永不静默缺失")
    print("  ✓ 契约强制校验 - 12 个 section 完整性校验")
    print("  ✓ 置信度计算 - 每个字段标记来源，每个 section 有置信度")
    print("  ✓ 数据透明化 - CS 章节自动汇总所有数据缺口")


if __name__ == "__main__":
    asyncio.run(main())

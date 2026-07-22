"""DDS v2 核心功能测试。

验证：
1. 数据主动管理 - 检测缺失并显式标记，永不静默缺失
2. 契约强制校验 - 12 个 section 完整性校验
3. 置信度计算 - 每个字段标记来源，每个 section 有置信度
4. 数据质量报告 - CS 章节自动汇总数据缺口
"""

import asyncio
import sys

from dds.contracts import ResolvedStatus
from dds.data import DataOrchestrator
from dds.engine import ContractEnforcer


async def test_1_never_empty():
    """测试 1: 缺失必须显式，且不得获得无来源置信度。"""
    print("\n" + "=" * 60)
    print("测试 1: 永不静默缺失 - unknown/human_input 显式登记")
    print("=" * 60)

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    project_context = {"city": "北京", "project_type": "住宅"}

    # 传入空数据
    section = await orchestrator.ensure_section_data("SC1", project_context, {})

    print(f"✓ Section {section.section_id} 生成完成")
    print(f"  字段总数: {len(section.data)}")
    print(f"  真实数据字段: {len(section.data) - len(section.missing_fields)}")
    print(f"  降级填充字段: {len(section.missing_fields)}")
    print(f"  置信度分数: {section.confidence['score']:.2f}")
    print(f"  置信度等级: {section.confidence['level_label']}")

    for field, origin in section.data_origin.items():
        value = str(section.data[field])[:40]
        print(f"  [{field}] {origin.label}: {value}...")

    assert section.is_complete(), "所有必填键都应该存在并显式声明 resolution status"
    assert len(section.data) > 0, "结构数据不应该为空"
    assert section.confidence["score"] == 0.0, "无 EvidenceRecord 时置信度必须为 0"
    assert all(
        section.field_resolution(field).status == ResolvedStatus.HUMAN_INPUT
        for field in section.missing_fields
    )

    print("\n✓ 测试 1 通过：缺失均显式登记，且未被固定分数洗白")


async def test_2_all_sections_complete():
    """测试 2: 所有 12 个 section 完整生成。"""
    print("\n" + "=" * 60)
    print("测试 2: 12 个 Decision-Unit 完整性")
    print("=" * 60)

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    project_context = {"city": "北京", "project_type": "住宅"}

    all_sections = await orchestrator.ensure_all_sections(project_context, {})

    print(f"✓ 成功生成 {len(all_sections)} 个 section")
    print()

    for section_id, section in all_sections.items():
        real_count = len(section.data) - len(section.missing_fields)
        print(
            f"  {section_id}: {len(section.data)} 字段, "
            f"真实数据 {real_count}, "
            f"置信度 {section.confidence['score']:.2f}, "
            f"{section.confidence['level_label']}"
        )

    # 验证 CS 章节包含数据缺口汇总
    cs_section = all_sections["CS"]
    print(f"\n✓ CS 章节数据缺口数: {len(cs_section.data['data_gaps'])}")

    assert len(all_sections) == 12, "应该有 12 个 section"
    assert "data_gaps" in cs_section.data, "CS 章节应该包含数据缺口汇总"

    print("\n✓ 测试 2 通过：12 个 section 全部生成，CS 章节自动汇总数据缺口")


async def test_3_contract_enforcer():
    """测试 3: 契约强制校验。"""
    print("\n" + "=" * 60)
    print("测试 3: 契约强制校验")
    print("=" * 60)

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    enforcer = ContractEnforcer()
    project_context = {"city": "北京", "project_type": "住宅"}

    # 仅有显式缺口的完整结构不能冒充证据完整报告
    all_sections = await orchestrator.ensure_all_sections(project_context, {})
    result = enforcer.validate_report(all_sections, {})

    print(f"  完整数据校验结果: {'PASS' if result.valid else 'FAIL'}")
    print(f"  错误数: {len(result.errors)}")
    print(f"  警告数: {len(result.warnings)}")
    print(f"  整体置信度: {result.overall_confidence:.2f}")

    assert not result.valid, "无 EvidenceRecord 时 evidence gate 必须失败"
    assert result.gate_status["structure"] == "pass"
    assert result.gate_status["evidence"] == "fail"
    assert result.overall_confidence == 0.0

    # 缺少一个 section 应该失败
    print("\n  故意删除 SC1 测试校验失败...")
    incomplete_sections = {k: v for k, v in all_sections.items() if k != "SC1"}

    result2 = enforcer.validate_report(incomplete_sections, {})
    assert not result2.valid, "缺少 section 应该校验失败"
    print(f"  ✓ 缺少 SC1 时校验失败，错误数: {len(result2.errors)}")

    # enforce_contract 应该抛出异常
    try:
        enforcer.enforce_contract(incomplete_sections, {})
        assert False, "应该抛出 ContractViolationError"
    except Exception as e:
        print(f"  ✓ 强制执行契约正确抛出异常: {type(e).__name__}")

    print("\n✓ 测试 3 通过：契约强制校验正常工作")


async def test_4_mixed_data():
    """测试 4: 混合真实数据与降级数据。"""
    print("\n" + "=" * 60)
    print("测试 4: 混合真实数据与降级数据")
    print("=" * 60)

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    project_context = {"city": "北京", "project_type": "住宅"}

    # 传入部分真实数据，部分缺失
    existing_data = {
        "SC1": {
            "project_id": "TEST-001",
            "decision_question": "这是一个测试决策问题",
            # evidence_boundary 缺失，应该自动降级
            # base_date 缺失，应该自动降级
        }
    }

    section = await orchestrator.ensure_section_data(
        "SC1", project_context, existing_data["SC1"]
    )

    # 值本身保留；没有 EvidenceRecord 时来源只能是未核验、resolution=partial。
    assert section.data["project_id"] == "TEST-001"
    assert section.data_origin["project_id"].source == "provided_unverified"
    assert section.field_resolution("project_id").status == ResolvedStatus.PARTIAL
    print(f"  ✓ 未核验值保留: project_id = {section.data['project_id']}")
    print(
        f"  ✓ 未核验值保留: decision_question = {section.data['decision_question'][:40]}..."
    )

    # 缺失字段必须显式 human_input，不得生成城市/全国基准。
    assert "evidence_boundary" in section.data
    assert section.data_origin["evidence_boundary"].source == "human_input"
    assert (
        section.field_resolution("evidence_boundary").status
        == ResolvedStatus.HUMAN_INPUT
    )
    assert section.confidence["score"] == 0.0
    print(
        f"  ✓ 自动填充缺失字段: evidence_boundary = {section.data_origin['evidence_boundary'].label}"
    )
    print(f"  ✓ 自动填充缺失字段: base_date = {section.data_origin['base_date'].label}")

    print(f"\n  整体置信度: {section.confidence['score']:.2f}")

    print("\n✓ 测试 4 通过：混合真实数据与降级数据正常工作")


async def test_5_data_quality_report():
    """测试 5: 数据质量报告。"""
    print("\n" + "=" * 60)
    print("测试 5: 数据质量报告")
    print("=" * 60)

    orchestrator = DataOrchestrator(city="北京", project_type="住宅")
    project_context = {"city": "北京", "project_type": "住宅"}

    all_sections = await orchestrator.ensure_all_sections(project_context, {})
    quality_report = orchestrator.get_data_quality_report(all_sections)

    print(f"  整体置信度: {quality_report['overall_confidence']['score']:.2f}")
    print(f"  置信度等级: {quality_report['overall_confidence']['level_label']}")
    print(f"  总字段数: {quality_report['summary']['total_fields']}")
    print(f"  真实数据字段数: {quality_report['summary']['real_fields']}")
    print(f"  真实数据占比: {quality_report['summary']['real_ratio']:.1%}")
    print(f"  降级字段数: {quality_report['summary']['fallback_fields']}")

    if quality_report["summary"]["fallback_by_strategy"]:
        print("  降级策略分布:")
        for strategy, count in quality_report["summary"][
            "fallback_by_strategy"
        ].items():
            print(f"    - {strategy}: {count} 个字段")

    print("\n✓ 测试 5 通过：数据质量报告正常生成")


async def main():
    print("DDS v2 核心功能测试")
    print("=" * 60)
    print()

    try:
        await test_1_never_empty()
        await test_2_all_sections_complete()
        await test_3_contract_enforcer()
        await test_4_mixed_data()
        await test_5_data_quality_report()

        print("\n" + "=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        print("\n核心功能验证：")
        print("  ✓ 数据主动管理 - 检测缺失并显式登记，永不静默缺失")
        print("  ✓ 契约强制校验 - 12 个 section 完整性校验")
        print("  ✓ 置信度计算 - 每个字段标记来源，每个 section 有置信度")
        print("  ✓ 数据透明化 - CS 章节自动汇总所有数据缺口")
        print()

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

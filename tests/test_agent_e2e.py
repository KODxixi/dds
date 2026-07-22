"""端到端测试：验证 DDS 多Agent系统可以生成完整报告。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dds.agents.pipeline import create_agent_system, generate_report


def test_agent_system_assembly():
    """验证 5 个专业 Agent 能被正确注册到协调 Agent。"""
    orch = create_agent_system()
    # 通过内部映射验证 5 个 task type 都有对应 Agent
    expected_types = {
        "requirement_analysis",
        "data_orchestration",
        "content_generation",
        "quality_audit",
        "report_export",
    }
    registered = set(orch._agents.keys())
    missing = expected_types - registered
    assert not missing, f"以下任务类型未注册 Agent: {missing}"
    print(f"[OK] 5 个专业 Agent 注册完整: {sorted(registered)}")


async def _run_full_pipeline():
    """执行一次完整的报告生成流程，返回结果与输出文件路径。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await generate_report(
            project_context={
                "city": "北京",
                "project_type": "住宅",
                "project_name": "朝阳核心区测试地块",
            },
            existing_data={
                # 用户提供的部分真实数据
                "SC1": {
                    "decision_question": "北京朝阳核心区高端住宅开发决策分析：是否拿地、做何产品？",
                },
            },
            report_id="TEST-E2E-001",
            output_dir=tmpdir,
        )
        return result, Path(tmpdir)


def test_end_to_end_report_generation():
    """端到端测试：从输入到 HTML 文件输出的全链路。"""
    result, tmpdir = asyncio.run(_run_full_pipeline())

    print(f"\n=== 端到端测试结果 ===")
    print(f"success: {result['success']}")
    print(f"report_id: {result['report_id']}")
    print(f"overall_confidence: {result.get('overall_confidence', 'N/A')}")
    print(f"duration_seconds: {result.get('duration_seconds', 'N/A'):.2f}s")
    print(f"output_path: {result.get('output_path', '')}")
    print(f"errors: {result.get('errors', [])}")
    print(f"warnings count: {len(result.get('warnings', []))}")
    print(f"section confidences: {result.get('section_confidences', {})}")

    assert result["success"], f"报告生成失败: {result['errors']}"

    output_path = Path(result["output_path"])
    assert output_path.exists(), f"HTML 输出文件不存在: {output_path}"

    html_content = output_path.read_text(encoding="utf-8")
    # 验证关键内容存在
    assert "DDS v2" in html_content, "HTML 中缺少 DDS 标识"
    assert "北京" in html_content, "HTML 中缺少城市名"
    assert "住宅" in html_content, "HTML 中缺少项目类型"
    assert "SC1" in html_content, "HTML 中缺少章节 SC1"
    assert "CS" in html_content, "HTML 中缺少 CS 置信状态章节"
    assert "置信度" in html_content, "HTML 中缺少置信度展示"
    assert "数据缺口清单" in html_content, "HTML 中缺少数据缺口表"

    file_size = output_path.stat().st_size
    print(f"[OK] HTML 文件大小: {file_size} bytes")
    assert file_size > 5000, f"HTML 文件过小 ({file_size} bytes)，可能内容不完整"


def test_sections_all_present():
    """验证所有 12 个章节都被处理并有置信度。"""
    result, _ = asyncio.run(_run_full_pipeline())
    assert result["success"]
    section_confs = result.get("section_confidences", {})
    expected_sids = {"SC1", "SC2", "SC3", "AD1", "AD2", "AD3", "AD4", "AD5", "VA1", "VA2", "VA3", "CS"}
    missing = expected_sids - set(section_confs.keys())
    assert not missing, f"缺失章节置信度: {missing}"
    print(f"[OK] 全部 12 个章节都有置信度分数")


if __name__ == "__main__":
    print("=" * 60)
    print("DDS Agent 集成 - 端到端测试")
    print("=" * 60)

    test_agent_system_assembly()
    test_end_to_end_report_generation()
    test_sections_all_present()

    print("\n" + "=" * 60)
    print("全部测试通过 ✓")
    print("=" * 60)

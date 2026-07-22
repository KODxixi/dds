"""调试脚本：逐步运行 Agent 流程，定位内容生成失败点。"""
import logging
import asyncio

from dds.agents.base import AgentTask
from dds.agents.pipeline import create_agent_system
from dds.agents.orchestrator import (
    TASK_REQUIREMENT_ANALYSIS,
    TASK_DATA_ORCHESTRATION,
    TASK_CONTENT_GENERATION,
    TASK_QUALITY_AUDIT,
)



logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")

async def main():
    orch = create_agent_system()

    ctx = {"city": "北京", "project_type": "住宅", "project_name": "朝阳测试地块"}
    existing = {"SC1": {"decision_question": "北京朝阳高端住宅决策分析？"}}

    # Step 1: requirement
    print("\n=== Step 1: Requirement ===")
    t1 = AgentTask(task_type=TASK_REQUIREMENT_ANALYSIS,
                   parameters={"project_context": ctx, "existing_data": existing})
    r1 = await orch._agents[TASK_REQUIREMENT_ANALYSIS].run(t1)
    print(f"success={r1.success}, errors={r1.errors}")
    ctx2 = r1.data.get("project_context", ctx)
    sc1_fields = r1.data.get("sc1_fields")

    # Step 2: data orchestration
    print("\n=== Step 2: Data Orchestration ===")
    t2 = AgentTask(task_type=TASK_DATA_ORCHESTRATION,
                   parameters={"project_context": ctx2, "existing_data": existing, "sc1_fields": sc1_fields})
    r2 = await orch._agents[TASK_DATA_ORCHESTRATION].run(t2)
    print(f"success={r2.success}, errors={r2.errors}")
    sections = r2.data.get("sections", {})
    print(f"sections keys: {list(sections.keys())}")
    for sid, sd in sections.items():
        print(f"  {sid}: type={type(sd).__name__}, data_keys={list(sd.data.keys()) if hasattr(sd,'data') else 'N/A'}")

    # Step 3: content generation for each section
    print("\n=== Step 3: Content Generation per section ===")
    for sid, sd in sections.items():
        t3 = AgentTask(task_type=TASK_CONTENT_GENERATION, section_id=sid,
                       parameters={"section_id": sid, "section_data": sd, "project_context": ctx2})
        r3 = await orch._agents[TASK_CONTENT_GENERATION].run(t3)
        status = "OK" if r3.success else "FAIL"
        print(f"  [{status}] {sid}: errors={r3.errors}")
        if not r3.success:
            print(f"     full result: {r3}")

    # Step 4: quality audit (if all content ok)
    print("\n=== Step 4: Quality Audit ===")
    # 先把 narrative 注入
    for sid, sd in sections.items():
        t3 = AgentTask(task_type=TASK_CONTENT_GENERATION, section_id=sid,
                       parameters={"section_id": sid, "section_data": sd, "project_context": ctx2})
        r3 = await orch._agents[TASK_CONTENT_GENERATION].run(t3)
        if r3.success and hasattr(sd, "data"):
            sd.data["narrative"] = r3.data.get("content", "")

    t4 = AgentTask(task_type=TASK_QUALITY_AUDIT,
                   parameters={"sections": sections, "project_context": ctx2})
    r4 = await orch._agents[TASK_QUALITY_AUDIT].run(t4)
    print(f"success={r4.success}, valid={r4.data.get('valid')}, errors={r4.errors}")
    print(f"audit errors: {r4.data.get('errors', [])}")

if __name__ == "__main__":
    asyncio.run(main())

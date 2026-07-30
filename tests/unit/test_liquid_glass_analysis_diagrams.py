from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (
    ROOT / "src/dds/reporting/templates/dds_report_liquid_glass_v4.html"
).read_text(encoding="utf-8")
BUILD_SCRIPT = (ROOT / "tools/dev/build_shenzhen_sc_report.py").read_text(
    encoding="utf-8"
)


def test_analysis_diagram_keeps_content_structured_instead_of_embedding_svg() -> None:
    assert "def _analysis_diagram(" in BUILD_SCRIPT
    assert '"program_blocks": [' in BUILD_SCRIPT
    assert '"relations": [' in BUILD_SCRIPT
    assert "def _cards_svg(" not in BUILD_SCRIPT
    assert "def _flow_svg(" not in BUILD_SCRIPT
    assert "data:image/svg+xml" not in BUILD_SCRIPT


def test_concept_cards_use_sibling_glass_surfaces_not_solid_or_nested_glass() -> None:
    assert '<div class="diagram-card glass">' in TEMPLATE
    assert "diagram-node solid-plate" not in TEMPLATE
    assert (
        '<div class="visual-surface glass"><div class="chart-head"><h2>'
        not in TEMPLATE
    )


def test_presentation_diagrams_and_facts_compact_without_changing_reading_mode() -> None:
    assert (
        ".presentation-frame .primary-column {\n"
        "      min-height: 0;\n"
        "      grid-template-rows: auto minmax(0,1fr);\n"
        "      align-content: stretch;"
        in TEMPLATE
    )
    assert ".presentation-frame .primary-visual { align-self: stretch; }" in TEMPLATE
    assert (
        ".presentation-frame .diagram-canvas { min-height: 0; height: 100%;"
        in TEMPLATE
    )
    assert (
        ".presentation-frame .diagram-card {\n"
        "      min-height: 0;\n"
        "      grid-template-rows: auto auto auto;\n"
        "      align-content: center;"
        in TEMPLATE
    )
    assert (
        ".presentation-frame .diagram-track:not(.is-dense) .diagram-card {\n"
        "      padding: 14px 10px;\n"
        "      gap: 5px;"
        in TEMPLATE
    )
    assert (
        ".presentation-frame .diagram-track:not(.is-dense) .diagram-card strong {\n"
        "      font-size: clamp(15px,1.05vw,20px);"
        in TEMPLATE
    )
    assert "text-wrap: balance;" in TEMPLATE
    assert (
        ".presentation-frame .diagram-track:not(.is-dense) "
        ".diagram-details span {\n"
        "      font-size: clamp(10px,.7vw,12px);"
        in TEMPLATE
    )
    assert (
        ".presentation-frame .diagram-track:not(.is-dense) "
        ".diagram-connector { flex-basis: 12px; }"
        in TEMPLATE
    )
    assert "const dense = nodes.length > 4;" in TEMPLATE
    assert '${dense ? " is-dense" : ""}' in TEMPLATE
    assert (
        ".presentation-frame .diagram-track.is-dense {\n"
        "      display: grid;\n"
        "      grid-template-columns: repeat(3,minmax(0,1fr));\n"
        "      grid-template-rows: repeat(2,minmax(0,1fr));"
        in TEMPLATE
    )
    assert (
        ".presentation-frame .decision-facts { "
        "grid-template-columns: repeat(2,minmax(0,1fr));"
        in TEMPLATE
    )
    for fact_label in (
        "结构状态 / 决策资格",
        "证据置信",
        "来源范围",
        "使用边界",
    ):
        assert fact_label in TEMPLATE
    assert ".diagram-track.is-dense {" not in TEMPLATE.split(
        ".presentation-frame .diagram-track.is-dense {", 1
    )[0]
    assert (
        ".primary-column { grid-area: primary; min-width: 0; display: grid; "
        "align-content: start; gap: 20px; }"
        in TEMPLATE
    )
    assert TEMPLATE.count("grid-template-rows: auto auto auto;") == 1
    assert "overflow-x: auto" not in TEMPLATE
    assert "overflow-x: scroll" not in TEMPLATE


def test_section_navigation_uses_instant_positioning() -> None:
    assert (
        'behavior:smooth && !reducedMotion ? "smooth" : "instant"'
        in TEMPLATE
    )
    assert (
        'button.addEventListener("click",() => '
        'navigate(Number(button.dataset.navIndex),{smooth:false}))'
        in TEMPLATE
    )


def test_shenzhen_sc_customer_chapter_uses_penguin_island_future_demand_pages() -> None:
    for diagram_id in (
        "SC2-PENGUIN-EVENT-TIMELINE",
        "SC2-PENGUIN-CAPTURE-FUNNEL",
        "SC2-PENGUIN-FUTURE-SEGMENTS",
        "SC2-PENGUIN-PERSONA-SCENARIOS",
        "SC2-PENGUIN-FUNNEL-PRIORITIES",
    ):
        assert diagram_id in BUILD_SCRIPT

    for page_id in ("sc2-03", "sc2-04", "sc2-05", "sc2-06", "sc2-07"):
        assert f'page_id="{page_id}"' in BUILD_SCRIPT

    assert "当前：数万员工通勤" in BUILD_SCRIPT
    assert "规划居住1.75–2.8万人" in BUILD_SCRIPT
    assert "禁止直接用员工总数推导销量" in BUILD_SCRIPT
    assert "以下为定性行为情景，不输出人数、概率或成交率" in BUILD_SCRIPT

    for superseded_title in (
        "SC2 · 四类模拟客群等待验证",
        "SC2 · 数字人五阶段退出机制",
        "SC2 · 客户变量补证优先级",
    ):
        assert superseded_title not in BUILD_SCRIPT

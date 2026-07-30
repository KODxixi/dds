"""Build the Shenzhen Input 2 SC volume from read-only V1 project material."""

from __future__ import annotations

import argparse
import base64
from hashlib import sha256
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from dds.projects import LegacyProjectReportMigrator


FROZEN_AT = "2026-07-24T16:40:21+08:00"

PENGUIN_SOURCE_IDS = (
    "src-penguin-traffic-20260721",
    "src-penguin-first-phase-20260310",
    "src-penguin-capacity-20250814",
    "src-penguin-plan",
    "src-penguin-housing-20250221",
    "src-metro15-20260518",
)

SUPPLEMENTAL_SOURCES = [
    {
        "source_id": "src-penguin-traffic-20260721",
        "source_type": "government_web",
        "title": "搭乘直通车C77线 往返企鹅岛更方便",
        "publisher": "深圳市交通运输局",
        "canonical_url": (
            "https://jtys.sz.gov.cn/jtzx/wycx/gjcx/cxtx/content/"
            "post_12902975.html"
        ),
        "published_at": "2026-07-21",
        "captured_at": "2026-07-24",
        "snapshot_excerpt": (
            "腾讯企鹅岛自2025年入驻以来，数万员工形成日常通勤；"
            "截至发布日园区已开行7条公交线路，覆盖宝安、宝华、"
            "宝体中心、碧海湾、前海湾等地铁站口。"
        ),
    },
    {
        "source_id": "src-penguin-first-phase-20260310",
        "source_type": "government_web",
        "title": "西乡街道2025年工作总结及2026年工作计划",
        "publisher": "深圳市宝安区西乡街道办事处",
        "canonical_url": (
            "https://www.baoan.gov.cn/xxgk/ghjh/ndgzjhjzj/content/"
            "post_12676458.html"
        ),
        "published_at": "2026-03-10",
        "captured_at": "2026-07-24",
        "snapshot_excerpt": (
            "互联网+未来科技城一期已完成竣工验收，形成49.25万平方米"
            "产业用房，腾讯首批员工已陆续入驻。"
        ),
    },
    {
        "source_id": "src-penguin-capacity-20250814",
        "source_type": "government_web",
        "title": "腾讯企鹅岛已建成30%，2025年10月起试运营",
        "publisher": "深圳市发展和改革委员会（来源：深圳发布）",
        "canonical_url": (
            "https://fgw.sz.gov.cn/ztzl/qtztzl/szscjmyjjfzzhfwpt/"
            "xwdt/content/post_12337074.html"
        ),
        "published_at": "2025-08-14",
        "captured_at": "2026-07-24",
        "snapshot_excerpt": (
            "园区占地80.9万平方米、包含五个街区，完全建成后预计"
            "容纳超过8万人办公；发布时整体建设完成约30%。"
        ),
    },
    {
        "source_id": "src-penguin-plan",
        "source_type": "official_planning_document",
        "title": "深圳互联网+未来科技城城市规划公众展示",
        "publisher": "深圳市规划和自然资源局",
        "canonical_url": (
            "https://pnr.sz.gov.cn/attachment/0/268/268536/5600245.pdf"
        ),
        "published_at": "",
        "captured_at": "2026-07-24",
        "snapshot_excerpt": (
            "片区规划就业人口6.4万人、居住人口1.75至2.8万人；"
            "规划研发用房160万平方米、宿舍70万平方米，并要求与"
            "宝安、前海湾区一体化发展。"
        ),
    },
    {
        "source_id": "src-penguin-housing-20250221",
        "source_type": "government_web",
        "title": "企鹅岛首批交付倒计时，腾讯前海新总部最新曝光",
        "publisher": "前海深港现代服务业合作区管理局",
        "canonical_url": (
            "https://qh.sz.gov.cn/sygnan/qhzx/dtzx/content/"
            "post_12011688.html"
        ),
        "published_at": "2025-02-21",
        "captured_at": "2026-07-24",
        "snapshot_excerpt": (
            "一期05街坊包含4000多间公寓及学校、文体等内部配套；"
            "二期总部办公区、会议中心、科技馆计划2028年投用。"
        ),
    },
    {
        "source_id": "src-metro15-20260518",
        "source_type": "government_web",
        "title": "深圳地铁15号线建设进展",
        "publisher": "深圳市交通运输局",
        "canonical_url": (
            "https://jtys.sz.gov.cn/jtzx/wycx/dtcx/cxtx/content/"
            "post_12793853.html"
        ),
        "published_at": "2026-05-18",
        "captured_at": "2026-07-24",
        "snapshot_excerpt": (
            "地铁15号线全长32.2公里、设24座车站，串联前海、南山、"
            "宝安及未来科技城，计划2028年建成通车。"
        ),
    },
]

SUPPLEMENTAL_CLAIMS = [
    {
        "claim_id": "claim-penguin-current-employment",
        "status": "qualified",
        "evidence_type": "observed_fact",
        "statement": (
            "企鹅岛自2025年入驻，至2026年7月已形成数万员工的日常"
            "通勤需求，因此属于现实就业极，不再只是远期区位概念。"
        ),
        "source_refs": [
            "src-penguin-traffic-20260721",
            "src-penguin-first-phase-20260310",
        ],
        "allowed_uses": ["future_demand_event", "customer_hypothesis"],
        "prohibited_uses": ["direct_residential_conversion"],
    },
    {
        "claim_id": "claim-penguin-scale-boundary",
        "status": "qualified",
        "evidence_type": "observed_fact",
        "statement": (
            "规划就业人口为6.4万人，完整园区办公容量约8万人；"
            "两者均为规模边界，不等于本项目可转化购房客户数。"
        ),
        "source_refs": ["src-penguin-plan", "src-penguin-capacity-20250814"],
        "allowed_uses": ["future_demand_event", "scenario_boundary"],
        "prohibited_uses": ["direct_residential_conversion"],
    },
    {
        "claim_id": "claim-penguin-customer-scenario",
        "status": "partial",
        "evidence_type": "model_simulation",
        "statement": (
            "本项目优先验证腾讯管理层、家庭化核心骨干及生态链决策者；"
            "普通员工更可能由园区公寓、租赁或其他总价产品承接。"
        ),
        "source_refs": [
            "src-penguin-housing-20250221",
            "src-penguin-traffic-20260721",
            "src-gcf-hj",
        ],
        "allowed_uses": ["customer_hypothesis", "research_prioritization"],
        "prohibited_uses": [
            "headcount_or_conversion_rate_as_fact",
            "pricing_commitment",
        ],
    },
]


def _find(root: Path, name: str) -> Path:
    matches = [path for path in root.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one {name!r}, found {len(matches)}")
    return matches[0]


def _source_id(path: Path) -> str:
    return f"SRC-{sha256(path.read_bytes()).hexdigest()[:20].upper()}"


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _analysis_diagram(
    *,
    diagram_id: str,
    title: str,
    nodes: list[tuple[str, str, list[str]]],
    footer: str,
    source_refs: list[str],
    sequential: bool = False,
) -> dict[str, Any]:
    return {
        "diagram_id": diagram_id,
        "diagram_type": "adjacency",
        "title": title,
        "program_blocks": [
            {"code": code, "label": label, "details": details}
            for code, label, details in nodes
        ],
        "relations": [
            {"from": nodes[index][0], "to": nodes[index + 1][0]}
            for index in range(len(nodes) - 1)
        ]
        if sequential
        else [],
        "source_note": footer,
        "source_refs": source_refs,
    }


def _render_pdf_page(
    pdftoppm: Path,
    pdf: Path,
    page: int,
    target_dir: Path,
) -> str:
    prefix = target_dir / f"{pdf.stem}-p{page}"
    command = [
        str(pdftoppm),
        "-f",
        str(page),
        "-l",
        str(page),
        "-singlefile",
        "-jpeg",
        "-jpegopt",
        "quality=90",
        "-r",
        "144",
        str(pdf),
        str(prefix),
    ]
    subprocess.run(command, check=True, capture_output=True)
    rendered = prefix.with_suffix(".jpg")
    if not rendered.is_file():
        raise RuntimeError(f"PDF page was not rendered: {pdf} page {page}")
    return _data_uri(rendered, "image/jpeg")


def _page(
    *,
    page_id: str,
    section_id: str,
    page_code: str,
    title: str,
    question: str,
    takeaway: str,
    impact: str,
    source_refs: list[str],
    blocks: list[dict[str, Any]],
    charts: list[dict[str, Any]] | None = None,
    diagrams: list[dict[str, Any]] | None = None,
    evidence_type: str = "analysis_inference",
    layout: str = "visual_board",
) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "chapter_id": section_id.lower(),
        "section_id": section_id,
        "page_code": page_code,
        "display_code": page_code,
        "layout": layout,
        "title": title,
        "decision_question": question,
        "takeaway": takeaway,
        "decision_impact": impact,
        "source_refs": source_refs,
        "blocks": blocks,
        "chart_specs": charts or [],
        "diagram_specs": diagrams or [],
        "unit_status": "ready",
        "visual_evidence": (
            "source_image"
            if any(block.get("type") == "image" for block in blocks)
            else "diagram"
            if diagrams
            else "chart"
        ),
        "evidence_type": evidence_type,
    }


def _image_block(src: str, title: str, source_refs: list[str]) -> dict[str, Any]:
    return {
        "type": "image",
        "src": src,
        "title": title,
        "alt": title,
        "orientation": "cover",
        "source_refs": source_refs,
    }


def build_sc_pages(
    v1_root: Path,
    pdftoppm: Path,
    temp_dir: Path,
) -> list[dict[str, Any]]:
    planning = _find(v1_root, "宝安中心区DY02-01地块规划设计条件研究.pdf")
    planning_ref = _source_id(planning)
    official = _find(v1_root, "规划设计要点.pdf")
    official_ref = _source_id(official)
    parcel = _find(v1_root, "宗地图.pdf")
    parcel_ref = _source_id(parcel)
    initial_reference = _find(v1_root, "初始参考_RGX.pptx")
    initial_reference_ref = _source_id(initial_reference)
    design_brief = _find(v1_root, "宝中方案招标任务书0710.docx")
    design_brief_ref = _source_id(design_brief)
    planning_pages = {
        page: _render_pdf_page(pdftoppm, planning, page, temp_dir)
        for page in (4, 6, 10, 14, 16, 19, 21, 22, 23)
    }
    parcel_page = _render_pdf_page(pdftoppm, parcel, 1, temp_dir)
    official_page = _render_pdf_page(pdftoppm, official, 1, temp_dir)

    pages = [
        _page(
            page_id="sc1-01",
            section_id="SC1",
            page_code="SC1·01",
            title="SC1 · 项目仍处于方案启动前：本轮任务是把约束变成策划输入",
            question="深圳项目现在究竟需要 DDS 解决什么，而不该提前替用户决定什么？",
            takeaway="当前输入由招标任务、规划条件和前期资料构成；本轮按 Input 2 建立市场、客群与场地约束基线，不进行方案评选。",
            impact="冻结 Input 2；任何 A/B/C 方案、主推结论或淘汰理由都必须等待用户正式提交候选方案。",
            source_refs=[planning_ref, official_ref],
            blocks=[
                _image_block(
                    planning_pages[4],
                    "宝安中心区区位原图｜《DY02-01地块规划设计条件研究》第4页",
                    [planning_ref],
                )
            ],
        ),
        _page(
            page_id="sc1-02",
            section_id="SC1",
            page_code="SC1·02",
            title="SC1 · 本轮决策结构：从事实边界到设计任务书",
            question="哪些信息是事实，哪些只是待验证假设，哪些结论当前禁止输出？",
            takeaway="报告必须把法定事实、市场线索和策划假设分层；只有前两者完成交叉验证后，才能转成设计参数。",
            impact="后续 AD 篇只接收已标明来源与置信边界的参数，不再继承代理生成的方案结论。",
            source_refs=[planning_ref, official_ref, "src-hao2025"],
            blocks=[{"type": "narrative", "text": "事实、线索与禁止结论必须分层进入后续策划。"}],
            diagrams=[
                _analysis_diagram(
                    diagram_id="SC1-DECISION-BOUNDARY",
                    title="Input 2 决策边界",
                    nodes=[
                        ("01 QUALIFIED", "已知事实", ["地块与计容指标", "限高与退线", "铁路与连廊接口"]),
                        ("02 REVIEW", "待验证线索", ["价格与去化参照", "目标客群结构", "景观溢价机制"]),
                        ("03 PROHIBITED", "当前禁止", ["自造三套方案", "主推 C+ 结论", "售价与收益承诺"]),
                    ],
                    footer="判断顺序：事实 → 交叉验证 → 参数 → 设计任务；不得从文件名直接推断项目阶段。",
                    source_refs=[planning_ref, official_ref, "src-hao2025"],
                )
            ],
        ),
        _page(
            page_id="sc1-03",
            section_id="SC1",
            page_code="SC1·03",
            title="SC1 · 项目核心矛盾不是“选哪个方案”，而是高地价下什么产品逻辑能成立",
            question="在进入设计前，最需要被验证的价值链是什么？",
            takeaway="高土地成本、滨海资源与严格工程边界共同要求：先验证客群和总价承受，再定义景观捕获与产品配置。",
            impact="把“客群—总价—面积—景观—空间机制”设为下一阶段联合策划的主链。",
            source_refs=["src-land-105e", "src-gcf-hj", planning_ref],
            blocks=[{"type": "narrative", "text": "价值链从成本与市场约束逐级转译为产品和设计任务。"}],
            diagrams=[
                _analysis_diagram(
                    diagram_id="SC1-PROJECT-VALUE-CHAIN",
                    title="项目价值链｜成本—市场—产品—设计",
                    nodes=[
                        ("01", "成本起点", ["竞得总价105.1亿", "楼面价约8.79万/㎡"]),
                        ("02", "市场约束", ["豪宅均价承压", "成交向稀缺产品集中"]),
                        ("03", "产品命题", ["总价必须可承受", "景观必须可感知"]),
                        ("04", "设计任务", ["面宽/楼层/私密性", "会所与归家体验"]),
                    ],
                    footer="市场数字来自现有公开证据备忘，均须在 SC2 中按来源等级重新审视。",
                    source_refs=["src-land-105e", "src-gcf-hj", planning_ref],
                    sequential=True,
                )
            ],
        ),
        _page(
            page_id="sc2-01",
            section_id="SC2",
            page_code="SC2·01",
            title="SC2 · 价格锚点跨越四个证据等级，不能被压成一个售价结论",
            question="现有价格信息能证明什么，不能证明什么？",
            takeaway="17.48万/㎡是单盘网签表现，12–15万/㎡仍是邻盘预计口径，10.2万/㎡是全市媒体统计；三者只能界定研究区间。",
            impact="下一轮补证优先获取宝中同口径网签、备案与分面积段成交，不把预计售价写入定价结论。",
            source_refs=["src-yunxi", "src-gcf-hj", "src-hao2025", "src-land-105e"],
            blocks=[
                {"type": "narrative", "text": "单位：万元/㎡；成交、预计、全市均值与土地成本必须分开阅读。"}
            ],
            charts=[
                {
                    "chart_id": "SC2-PRICE-ANCHORS",
                    "type": "ranked_bar",
                    "title": "现有价格锚点与土地成本",
                    "units": "万元/㎡",
                    "time_window": "2025–2026",
                    "series": [
                        {"label": "深圳湾澐玺·网签均价", "value": 17.48, "note": "单盘"},
                        {"label": "观潮府·预计中值", "value": 13.5, "note": "未取证"},
                        {"label": "深圳豪宅·媒体均价", "value": 10.2, "note": "全市"},
                        {"label": "本项目·成交楼面价", "value": 8.79, "note": "成本锚"},
                    ],
                    "source_refs": ["src-yunxi", "src-gcf-hj", "src-hao2025", "src-land-105e"],
                    "source_note": "不同口径并列展示，不构成项目售价建议。",
                }
            ],
            layout="module_summary",
        ),
        _page(
            page_id="sc2-02",
            section_id="SC2",
            page_code="SC2·02",
            title="SC2 · 市场不是普涨逻辑：稀缺产品有流速，中间产品被选择性放弃",
            question="现有正反证据共同指向什么产品风险？",
            takeaway="澐玺284套网签证明稀缺产品仍有购买力；全市均价同比-2.1%和中间楼层弃选说明普通化产品无法依靠市场托底。",
            impact="产品策划必须同时设置“被选择理由”和“被放弃原因”，不能只列卖点。",
            source_refs=["src-yunxi", "src-hao2025", "src-haoxuan"],
            blocks=[
                {"type": "narrative", "text": "信号强度为证据整理尺度，不是统计概率；正值为支持，负值为反证。"}
            ],
            charts=[
                {
                    "chart_id": "SC2-MARKET-SIGNALS",
                    "type": "diverging_bar",
                    "title": "稀缺支付力与去化压力并存",
                    "units": "证据信号",
                    "series": [
                        {"label": "澐玺全年网签284套", "value": 3},
                        {"label": "一线海景稀缺性", "value": 2},
                        {"label": "豪宅均价同比-2.1%", "value": -2},
                        {"label": "中间楼层弃选", "value": -3},
                    ],
                    "source_refs": ["src-yunxi", "src-hao2025", "src-haoxuan"],
                    "source_note": "媒体和单盘证据仅支持方向判断，待官方网签与项目底表校准。",
                }
            ],
            layout="module_summary",
        ),
        _page(
            page_id="sc2-03",
            section_id="SC2",
            page_code="SC2·03",
            title="SC2 · 企鹅岛已从远期地标变成数万员工实际通勤的就业极",
            question="企鹅岛运营对本项目是景观叙事，还是已经发生的新增需求事件？",
            takeaway="V1 已识别科技总部势能，但未转译为客群；官方资料确认企鹅岛2025年起入驻、2026年已形成数万员工通勤，必须升为本项目一级未来需求变量。",
            impact="把企鹅岛投运节奏单列为客群基线；后续产品与营销判断同时对齐2026现实就业、2028二期与轨道兑现三个时间窗。",
            source_refs=[
                initial_reference_ref,
                design_brief_ref,
                "src-penguin-traffic-20260721",
                "src-penguin-first-phase-20260310",
                "src-penguin-housing-20250221",
                "src-metro15-20260518",
            ],
            blocks=[{"type": "narrative", "text": "事实时间轴：区分已入驻、建设中与规划容量。"}],
            diagrams=[
                _analysis_diagram(
                    diagram_id="SC2-PENGUIN-EVENT-TIMELINE",
                    title="企鹅岛从区位线索到现实需求事件",
                    nodes=[
                        (
                            "V1",
                            "项目资料已提示",
                            ["宝中+前海+滨海+科技", "腾讯总部与金融科创势能"],
                        ),
                        (
                            "2025",
                            "一期竣工入驻",
                            ["49.25万㎡产业用房", "腾讯首批员工陆续入驻"],
                        ),
                        (
                            "2026",
                            "现实就业极形成",
                            ["数万员工日常通勤", "7条公交线路连接周边轨道"],
                        ),
                        (
                            "2028",
                            "二期与轨道窗口",
                            ["总部办公区等计划投用", "地铁15号线计划建成"],
                        ),
                    ],
                    footer="时间节点来自政府公开资料；2028为计划状态，不得提前表述为已兑现。",
                    source_refs=[
                        initial_reference_ref,
                        design_brief_ref,
                        "src-penguin-traffic-20260721",
                        "src-penguin-first-phase-20260310",
                        "src-penguin-housing-20250221",
                        "src-metro15-20260518",
                    ],
                    sequential=True,
                )
            ],
            evidence_type="observed_fact",
        ),
        _page(
            page_id="sc2-04",
            section_id="SC2",
            page_code="SC2·04",
            title="SC2 · 6.4–8万人是需求池上限，不是本项目购房客户数",
            question="企鹅岛就业人口经过哪些筛选，才可能转化为本项目的有效客群？",
            takeaway="园区规划6.4万就业、远期约8万办公容量，但内部规划1.75–2.8万居住人口且一期已有4000多间公寓；外溢需求只来自未被园区住宿吸收、进入家庭改善并达到本项目总价门槛的人群。",
            impact="所有客群规模必须按“就业—外部居住—家庭阶段—宝中选择—总价门槛—项目胜率”逐层校准，禁止直接用员工总数推导销量。",
            source_refs=[
                "src-penguin-plan",
                "src-penguin-capacity-20250814",
                "src-penguin-housing-20250221",
                "src-penguin-traffic-20260721",
            ],
            blocks=[{"type": "narrative", "text": "转化漏斗：每一层都需要独立证据和反证。"}],
            diagrams=[
                _analysis_diagram(
                    diagram_id="SC2-PENGUIN-CAPTURE-FUNNEL",
                    title="企鹅岛就业人口到项目客户的可校准漏斗",
                    nodes=[
                        (
                            "01",
                            "就业需求池",
                            ["当前：数万员工通勤", "边界：规划6.4万/容量约8万"],
                        ),
                        (
                            "02",
                            "园区内部吸收",
                            ["规划居住1.75–2.8万人", "一期4000多间员工公寓"],
                        ),
                        (
                            "03",
                            "外部家庭需求",
                            ["婚育/改善/资产配置触发", "租住、南山原居与宝中竞争"],
                        ),
                        (
                            "04",
                            "本项目可服务客群",
                            ["高管与家庭化核心骨干", "生态链决策者且总价可承受"],
                        ),
                    ],
                    footer="就业与居住规划差额不等于购房需求；外部居住比例、家庭化比例和项目转化率均待客研。",
                    source_refs=[
                        "src-penguin-plan",
                        "src-penguin-capacity-20250814",
                        "src-penguin-housing-20250221",
                        "src-penguin-traffic-20260721",
                    ],
                    sequential=True,
                )
            ],
            evidence_type="model_simulation",
        ),
        _page(
            page_id="sc2-05",
            section_id="SC2",
            page_code="SC2·05",
            title="SC2 · 未来客群必须按岗位与家庭阶段分层，不能笼统写“腾讯员工”",
            question="企鹅岛运营后，哪些人可能成为本项目主力、机会客群或仅形成外部流量？",
            takeaway="本项目最值得争取的是腾讯高管与业务负责人、家庭化高级技术与产品骨干、生态链企业主；年轻普通员工人数最多，但更可能被园区公寓、租赁和其他总价产品承接。",
            impact="产品研究优先围绕前三类验证总价、家庭结构、私密接待、双人办公和通勤；普通员工主要影响租赁、商业和长期改善池，不虚构为当前主力销量。",
            source_refs=[
                design_brief_ref,
                initial_reference_ref,
                "src-penguin-traffic-20260721",
                "src-penguin-housing-20250221",
                "src-yunxi",
            ],
            blocks=[{"type": "narrative", "text": "分层为研究假设，不代表真实占比或购买概率。"}],
            diagrams=[
                _analysis_diagram(
                    diagram_id="SC2-PENGUIN-FUTURE-SEGMENTS",
                    title="企鹅岛未来客群分层｜主力、机会与非主力",
                    nodes=[
                        (
                            "T01 主力验证",
                            "高管与业务负责人",
                            ["家庭资产与近总部需求", "私密接待/安保/全景资源", "数量小但支付上限高"],
                        ),
                        (
                            "T02 核心验证",
                            "高级技术与产品骨干",
                            ["婚育或家庭改善触发", "双人办公/成长空间/稳定通勤", "总价门槛决定转化"],
                        ),
                        (
                            "T03 机会客群",
                            "生态链创始人与决策者",
                            ["业务协同与前海资产配置", "重品牌兑现和流动性", "规模需产业名录补证"],
                        ),
                        (
                            "T04 非当前主力",
                            "年轻及普通员工",
                            ["公寓或租赁优先", "形成商业与长期改善需求", "不支撑高总价主力判断"],
                        ),
                    ],
                    footer="客群分层由项目定位、企鹅岛投运和内部住宿反证共同推导；真实数量须由职级、家庭与居住调查校准。",
                    source_refs=[
                        design_brief_ref,
                        initial_reference_ref,
                        "src-penguin-traffic-20260721",
                        "src-penguin-housing-20250221",
                        "src-yunxi",
                    ],
                )
            ],
            evidence_type="model_simulation",
        ),
        _page(
            page_id="sc2-06",
            section_id="SC2",
            page_code="SC2·06",
            title="SC2 · 数字人推演要回答“何时迁居、为何选择、在哪退出”",
            question="四类企鹅岛相关人群在当前与2028情景下，会怎样处理居住选择？",
            takeaway="高管可能直接比较宝中与深圳湾顶豪；家庭化骨干更可能经历园区公寓或租住后再改善；生态链决策者偏资产与社交网络；普通员工短期不进入本项目总价带。",
            impact="首轮访谈按四条行为路径招募样本，不询问抽象偏好，而验证迁居触发、现居地、通勤、家庭决策、总价上限和退出条件。",
            source_refs=[
                "src-penguin-traffic-20260721",
                "src-penguin-housing-20250221",
                "src-metro15-20260518",
                "src-gcf-hj",
                "src-yunxi",
            ],
            blocks=[{"type": "narrative", "text": "以下为定性行为情景，不输出人数、概率或成交率。"}],
            diagrams=[
                _analysis_diagram(
                    diagram_id="SC2-PENGUIN-PERSONA-SCENARIOS",
                    title="未来客户数字人｜迁居路径与退出条件",
                    nodes=[
                        (
                            "P01",
                            "总部高管家庭",
                            ["触发：核心团队迁驻/家庭近职住", "选择：私密与品牌兑现", "退出：跨湾通勤仍不确定"],
                        ),
                        (
                            "P02",
                            "家庭化核心骨干",
                            ["路径：公寓或租住→改善购房", "选择：家庭空间+双人办公", "退出：总价或教育不匹配"],
                        ),
                        (
                            "P03",
                            "生态链企业主",
                            ["触发：业务协同+资产配置", "选择：会客/圈层/景观稀缺", "退出：流动性与交付风险"],
                        ),
                        (
                            "P04",
                            "年轻技术员工",
                            ["路径：园区公寓/周边租赁", "贡献：商业与未来改善池", "退出：当前总价不可达"],
                        ),
                    ],
                    footer="2028二期与15号线为计划情景；裕安一路跨海联系和真实门到门通勤仍需持续校准。",
                    source_refs=[
                        "src-penguin-traffic-20260721",
                        "src-penguin-housing-20250221",
                        "src-metro15-20260518",
                        "src-gcf-hj",
                        "src-yunxi",
                    ],
                )
            ],
            evidence_type="model_simulation",
        ),
        _page(
            page_id="sc2-07",
            section_id="SC2",
            page_code="SC2·07",
            title="SC2 · 下一轮客研先校准企鹅岛转化漏斗，而不是继续扩写画像",
            question="哪些参数最能改变未来客群与产品判断？",
            takeaway="总价承受、实际门到门通勤、家庭迁居阶段是前三个硬变量；园区公寓替代、教育与私密办公决定外溢需求能否进入本项目。",
            impact="按优先级获取腾讯员工匿名访谈、班车节点拦访、现居分布与家庭阶段；参数未校准前只保留方向性情景。",
            source_refs=[
                "src-penguin-traffic-20260721",
                "src-penguin-housing-20250221",
                "src-metro15-20260518",
                "src-yunxi",
                "src-gcf-hj",
            ],
            blocks=[
                {"type": "narrative", "text": "分值为模型压力测试权重：5=优先验证，1=低优先；不是客群调查结果。"}
            ],
            charts=[
                {
                    "chart_id": "SC2-PENGUIN-FUNNEL-PRIORITIES",
                    "type": "ranked_bar",
                    "title": "企鹅岛未来客群转化参数补证优先级",
                    "units": "优先级",
                    "series": [
                        {"label": "总价可承受", "value": 5, "note": "硬筛选"},
                        {"label": "门到门通勤", "value": 5, "note": "当前有摩擦"},
                        {"label": "家庭迁居阶段", "value": 5, "note": "决定外溢"},
                        {"label": "园区公寓替代", "value": 4, "note": "强反证"},
                        {"label": "教育与代际协住", "value": 4, "note": "待访谈"},
                        {"label": "私密与双人办公", "value": 3, "note": "产品转译"},
                    ],
                    "source_refs": [
                        "src-penguin-traffic-20260721",
                        "src-penguin-housing-20250221",
                        "src-metro15-20260518",
                        "src-yunxi",
                        "src-gcf-hj",
                    ],
                    "source_note": "优先级只用于安排客研，不代表变量系数或购买概率。",
                }
            ],
            evidence_type="model_simulation",
            layout="module_summary",
        ),
        _page(
            page_id="sc3-01",
            section_id="SC3",
            page_code="SC3·01",
            title="SC3 · 500米轨道条件是规划预期，不是现状交通兑现",
            question="区位与交通条件中，哪些已存在，哪些仍属于规划状态？",
            takeaway="地块三面临路、处于宝中核心滨海片区；28号线滨海西站为500米范围内规划站点，不能按已开通价值计入。",
            impact="区位叙事分开表达现状道路、滨海资源与规划轨道，避免提前兑现规划红利。",
            source_refs=[planning_ref, "src-site-gis"],
            blocks=[
                _image_block(
                    planning_pages[6],
                    "交通条件原图｜《DY02-01地块规划设计条件研究》第6页",
                    [planning_ref, "src-site-gis"],
                )
            ],
        ),
        _page(
            page_id="sc3-02",
            section_id="SC3",
            page_code="SC3·02",
            title="SC3 · 11.95万㎡计容由住宅、商业和公共配套共同占用",
            question="进入容量推演前，必须锁定哪些不可挪用的功能指标？",
            takeaway="住宅104600㎡之外，商业10095㎡、幼儿园4100㎡、托育500㎡和物业240㎡均需进入同一容量账本。",
            impact="AD 篇必须以完整功能平衡表起步，不能只用住宅计容反推塔楼。",
            source_refs=[planning_ref, official_ref],
            blocks=[
                _image_block(
                    planning_pages[10],
                    "经济技术指标原图｜《DY02-01地块规划设计条件研究》第10页",
                    [planning_ref, official_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-03",
            section_id="SC3",
            page_code="SC3·03",
            title="SC3 · 80米只是规范控制上限，仍需叠加航空与消防起算条件",
            question="高度参数能否直接作为塔楼设计高度？",
            takeaway="研究文件给出住宅建筑最大值80米，但同时要求满足航空限高；消防登高起算和正式审批条件仍需专项确认。",
            impact="高度进入 AD 容量模型时按上限包络处理，不把80米当作可无条件用满的设计值。",
            source_refs=[planning_ref, official_ref],
            blocks=[
                _image_block(
                    planning_pages[14],
                    "建筑限高原图｜《DY02-01地块规划设计条件研究》第14页",
                    [planning_ref, official_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-04",
            section_id="SC3",
            page_code="SC3·04",
            title="SC3 · 退线叠加后，可建包络比红线面积更值得进入方案前推演",
            question="哪些边界首先压缩塔楼、裙房和公共空间的布置自由度？",
            takeaway="海秀路、海天路二级退线12米，其余侧一级6米、二级9米；退线必须与覆盖率和公共空间同时叠合。",
            impact="下一篇先生成真实坐标下的可建包络，再讨论塔楼数量和底盘形态。",
            source_refs=[planning_ref, parcel_ref],
            blocks=[
                _image_block(
                    planning_pages[16],
                    "建筑退线原图｜《DY02-01地块规划设计条件研究》第16页",
                    [planning_ref, parcel_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-05",
            section_id="SC3",
            page_code="SC3·05",
            title="SC3 · 避免屏风效应是布局原则，不等于已经形成塔楼方案",
            question="规划文件对整体布局提出了什么可执行要求？",
            takeaway="塔楼需预留视线和通风廊道，裙房宜沿金科路形成活力界面；当前只能转译为评价指标，不能替代强排。",
            impact="AD 篇建立面宽、间距、通透率和城市界面评价尺，再由设计团队形成候选路线。",
            source_refs=[planning_ref],
            blocks=[
                _image_block(
                    planning_pages[19],
                    "整体建筑布局原图｜《DY02-01地块规划设计条件研究》第19页",
                    [planning_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-06",
            section_id="SC3",
            page_code="SC3·06",
            title="SC3 · 第六立面是明确设计要求，但其产品价值仍需独立验证",
            question="屋顶景观花园应作为硬任务还是溢价结论？",
            takeaway="规划研究明确建议设置高空景观花园群；它可以进入设计任务书，但不能直接证明售价或去化提升。",
            impact="AD 篇定义屋顶可达性、面积、风环境和运营条件；VA 篇再评估投入与价值。",
            source_refs=[planning_ref],
            blocks=[
                _image_block(
                    planning_pages[21],
                    "第六立面要求原图｜《DY02-01地块规划设计条件研究》第21页",
                    [planning_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-07",
            section_id="SC3",
            page_code="SC3·07",
            title="SC3 · 连廊接口具备明确标高和净宽，是必须前置锁定的工程条件",
            question="与相邻项目衔接有哪些不可后置的参数？",
            takeaway="本项目需建设连接相邻项目的二层公共通道，接口绝对标高10.250米、净宽不小于4米、廊下净高不小于5米。",
            impact="连廊位置和标高进入首轮竖向设计，不得在总图完成后再补接。",
            source_refs=[planning_ref],
            blocks=[
                _image_block(
                    planning_pages[22],
                    "空中公共连廊原图｜《DY02-01地块规划设计条件研究》第22页",
                    [planning_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-08",
            section_id="SC3",
            page_code="SC3·08",
            title="SC3 · 铁路与28号线控制区首先约束地下施工，不应被误写成地上禁建结论",
            question="轨道控制条件目前能支持到什么精度？",
            takeaway="研究文件明确围护结构锚索等施工构件不得侵入铁路和28号线控制区；地上功能禁限建仍需正式文件进一步定位。",
            impact="先取得控制区坐标和专项意见，再判断地下室边界、基坑形式及其对地上布局的传导。",
            source_refs=[planning_ref, official_ref],
            blocks=[
                _image_block(
                    planning_pages[23],
                    "地下空间控制原图｜《DY02-01地块规划设计条件研究》第23页",
                    [planning_ref, official_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-09",
            section_id="SC3",
            page_code="SC3·09",
            title="SC3 · 宗地图是下一阶段几何模型的坐标底板",
            question="下一阶段的可建包络应以什么几何资料为准？",
            takeaway="宗地图提供界址点坐标和三维宗地信息；研究图和手绘示意不得替代坐标底板。",
            impact="AD 篇几何建模绑定宗地图版本与文件哈希，再叠加退线和专项控制区。",
            source_refs=[parcel_ref],
            blocks=[
                _image_block(
                    parcel_page,
                    "宗地界址图原图｜《宗地图》第1页",
                    [parcel_ref],
                )
            ],
        ),
        _page(
            page_id="sc3-10",
            section_id="SC3",
            page_code="SC3·10",
            title="SC3 · 最终规划条件以后续正式许可证为准",
            question="现阶段规划文件的法定使用边界是什么？",
            takeaway="规划设计要点可支持当前指标登记，但文件明确提示最终条件以后续正式核发的建设用地规划许可证为准。",
            impact="建立版本闸门；正式许可证到达后自动触发指标差异复核。",
            source_refs=[official_ref],
            blocks=[
                _image_block(
                    official_page,
                    "规划设计要点批复表｜《规划设计要点》第1页",
                    [official_ref],
                )
            ],
        ),
    ]
    display_titles = {
        "sc1-01": "SC1 · 项目仍处于方案启动前",
        "sc1-02": "SC1 · 从事实边界到设计任务",
        "sc1-03": "SC1 · 高地价下的产品成立条件",
        "sc2-01": "SC2 · 四类价格锚点分口径阅读",
        "sc2-02": "SC2 · 稀缺流速与市场压力并存",
        "sc2-03": "SC2 · 企鹅岛已形成现实就业极",
        "sc2-04": "SC2 · 就业人口到项目客户的转化漏斗",
        "sc2-05": "SC2 · 腾讯未来客群分层",
        "sc2-06": "SC2 · 腾讯客群数字人行为推演",
        "sc2-07": "SC2 · 未来客群关键参数补证",
        "sc3-01": "SC3 · 规划轨道不等于现状兑现",
        "sc3-02": "SC3 · 完整计容与功能账本",
        "sc3-03": "SC3 · 80米限高仍需专项叠加",
        "sc3-04": "SC3 · 退线叠加后的可建包络",
        "sc3-05": "SC3 · 避屏风是指标而不是方案",
        "sc3-06": "SC3 · 第六立面是任务不是溢价",
        "sc3-07": "SC3 · 连廊接口必须前置锁定",
        "sc3-08": "SC3 · 轨道控制先约束地下施工",
        "sc3-09": "SC3 · 宗地图是坐标底板",
        "sc3-10": "SC3 · 正式许可证是版本闸门",
    }
    for page in pages:
        page["display_title"] = display_titles[page["page_id"]]
    return pages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdftoppm", type=Path, required=True)
    args = parser.parse_args()
    v1_root = args.v1_root.resolve()
    output = args.output.resolve()
    with TemporaryDirectory(prefix="dds-shenzhen-sc-") as directory:
        pages = build_sc_pages(v1_root, args.pdftoppm.resolve(), Path(directory))
        result = LegacyProjectReportMigrator().migrate(
            v1_root,
            output,
            project_id="SZ-A002-0113-SC",
            project_name="深圳宝安中心区 A002-0113 前策与约束协同",
            imported_at=FROZEN_AT,
            as_of="2026-07-24",
            selected_mode=2,
            intervention_brief={
                "user_goal": "在方案启动前完成市场、客群、场地与约束基线，形成可供联合策划使用的输入",
                "decision_audience": "项目负责人、策划、设计与投资团队",
                "decision_questions": [
                    "哪些市场与客群假设值得进入产品策划？",
                    "哪些法定和工程条件必须前置进入设计任务书？",
                ],
                "priorities": {
                    "market_and_customer": 0.35,
                    "site_and_constraints": 0.40,
                    "evidence_boundary": 0.25,
                },
                "prohibited_conclusions": [
                    "未经用户提交候选方案而形成的方案比选、主推或淘汰结论",
                    "未经真实成交、客户研究和成本校准的售价、去化或收益承诺",
                ],
                "confirmed_at": FROZEN_AT,
            },
            input_profile={
                "address": "深圳市宝安中心区 A002-0113 宗地",
                "future_demand_sources": list(PENGUIN_SOURCE_IDS),
                "constraint_sources": [
                    {"source_ref": "client://规划条件"},
                    {"source_ref": "client://招标任务书"},
                ],
                "materials": [
                    {"filename": "宝安中心区DY02-01地块规划设计条件研究.pdf"},
                    {"filename": "规划设计要点.pdf"},
                    {"filename": "初始参考_RGX.pptx"},
                ],
            },
            legacy_seed_path=v1_root / "work" / "report_seed.json",
            evidence_batch_path=v1_root / "work" / "evidence_candidate_batch.json",
            page_manifest_override=pages,
            supplemental_sources=SUPPLEMENTAL_SOURCES,
            supplemental_claims=SUPPLEMENTAL_CLAIMS,
        )
    print(result.output_root / "chapter-preview.html")
    print(f"logical_pages={len(result.decision_seed['page_manifest'])}")
    print(
        "selected_mode="
        f"{result.decision_seed['meta']['analysis_profile']['selected_mode']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

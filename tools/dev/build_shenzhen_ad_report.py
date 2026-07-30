"""Build the Shenzhen Input 2 AD design-brief volume from frozen project sources."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZipFile

from dds.projects import LegacyProjectReportMigrator

from build_shenzhen_sc_report import (
    PENGUIN_SOURCE_IDS,
    SUPPLEMENTAL_CLAIMS,
    SUPPLEMENTAL_SOURCES,
    _analysis_diagram,
    _find,
    _image_block,
    _page,
    _render_pdf_page,
    _source_id,
)


FROZEN_AT = "2026-07-24T16:40:21+08:00"


def _pptx_media_data_uri(pptx: Path, member: str) -> str:
    with ZipFile(pptx) as archive:
        payload = archive.read(member)
    suffix = Path(member).suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix)
    if mime is None:
        raise ValueError(f"unsupported PPTX media type: {suffix}")
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def _set_page_metadata(pages: list[dict[str, Any]]) -> None:
    section_titles = {
        "AD1": "定位、容量与可建包络",
        "AD2": "约束驱动策略与评价基线",
        "AD3": "未来客群与产品参数",
        "AD4": "空间机制与技术闭环",
        "AD5": "设计任务书与阶段边界",
    }
    display_titles = {
        "ad1-01": "AD1 · 哪些输入可以进入设计",
        "ad1-02": "AD1 · 完整功能与容量账本",
        "ad1-03": "AD1 · 坐标化可建包络",
        "ad1-04": "AD1 · 首轮竖向控制面",
        "ad2-01": "AD2 · 五项约束必须同时求解",
        "ad2-02": "AD2 · 候选路线统一评价尺",
        "ad3-01": "AD3 · 企鹅岛客群到空间需求",
        "ad3-02": "AD3 · 总价与面积校准链",
        "ad3-03": "AD3 · 会所、商业与公共配套",
        "ad4-01": "AD4 · 案例只迁移空间机制",
        "ad4-02": "AD4 · 体验序列与技术闸门",
        "ad5-01": "AD5 · 向设计团队交付什么",
    }
    for page in pages:
        page["display_title"] = display_titles[page["page_id"]]
        page["section_title"] = section_titles[page["section_id"]]
        page["decision_gate"] = (
            "本篇形成 Input 2 设计任务与评价基线；未收到用户提交的候选方案，"
            "不得输出方案排序、主推或淘汰结论。"
        )


def build_ad_pages(
    v1_root: Path,
    pdftoppm: Path,
    temp_dir: Path,
) -> list[dict[str, Any]]:
    planning = _find(v1_root, "宝安中心区DY02-01地块规划设计条件研究.pdf")
    official = _find(v1_root, "规划设计要点.pdf")
    parcel = _find(v1_root, "宗地图.pdf")
    taskbook = _find(v1_root, "宝中方案招标任务书0710.docx")
    review_points = _find(v1_root, "宝中DY02-01审查要点.docx")
    initial_reference = _find(v1_root, "初始参考_RGX.pptx")

    planning_ref = _source_id(planning)
    official_ref = _source_id(official)
    parcel_ref = _source_id(parcel)
    taskbook_ref = _source_id(taskbook)
    review_ref = _source_id(review_points)
    initial_reference_ref = _source_id(initial_reference)

    parcel_page = _render_pdf_page(pdftoppm, parcel, 1, temp_dir)
    case_image = _pptx_media_data_uri(
        initial_reference,
        "ppt/media/image14.png",
    )

    pages = [
        _page(
            page_id="ad1-01",
            section_id="AD1",
            page_code="AD1·01",
            title="AD1 · 只有三类输入可以进入设计：硬条件、任务要求和显式假设",
            question="SC 篇形成的哪些内容有资格转成设计输入？",
            takeaway="法定指标与接口作为硬边界，任务书要求作为待落实任务，企鹅岛客群作为待校准假设；市场线索和案例不能越权变成形态结论。",
            impact="冻结 AD 输入包与来源哈希；设计团队收到的每项参数都标明“必须满足、需复核或仅供探索”。",
            source_refs=[
                official_ref,
                taskbook_ref,
                review_ref,
                "src-penguin-traffic-20260721",
                "src-penguin-housing-20250221",
            ],
            blocks=[
                {
                    "type": "narrative",
                    "text": "输入资格按来源权威、项目阶段和允许用途分层。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD1-INPUT-ADMISSION",
                    title="从 SC 证据到 AD 设计输入的三层准入",
                    nodes=[
                        (
                            "硬边界",
                            "法定与工程条件",
                            ["计容/限高/退线", "轨道控制与连廊接口", "未经正式变更不得调整"],
                        ),
                        (
                            "项目任务",
                            "甲方任务书要求",
                            ["商业/配套/会所任务", "客户与产品目标", "须经合规、成本和运营复核"],
                        ),
                        (
                            "研究假设",
                            "客群与价值假设",
                            ["企鹅岛未来家庭需求", "总价与面积尚待校准", "只用于提出验证问题"],
                        ),
                        (
                            "禁止越权",
                            "当前不能进入",
                            ["媒体线索直接定价", "案例直接复制形态", "代理自行排序候选方案"],
                        ),
                    ],
                    footer="准入顺序：原始位置可追溯 → 允许用途匹配 → 形成设计参数或验证任务。",
                    source_refs=[
                        official_ref,
                        taskbook_ref,
                        review_ref,
                        "src-penguin-traffic-20260721",
                        "src-penguin-housing-20250221",
                    ],
                    sequential=True,
                )
            ],
        ),
        _page(
            page_id="ad1-02",
            section_id="AD1",
            page_code="AD1·02",
            title="AD1 · 119535㎡必须按完整功能账本进入容量推演",
            question="进入强排前，哪些计容功能必须被同时装入？",
            takeaway="住宅104600㎡之外，商业10095㎡、12班幼儿园4100㎡、托育与物业740㎡均不可遗漏；四类合计恰为119535㎡。",
            impact="冻结功能平衡表；任何容量路线都必须逐项回填，不允许只用住宅面积反推塔楼。",
            source_refs=[official_ref, taskbook_ref, review_ref],
            blocks=[
                {
                    "type": "narrative",
                    "text": "单位：㎡；比例由任务书面积复算，仅用于功能账本核对。",
                }
            ],
            charts=[
                {
                    "chart_id": "AD1-CAPACITY-LEDGER",
                    "type": "ranked_bar",
                    "title": "规定计容建筑面积完整账本",
                    "units": "㎡",
                    "series": [
                        {"label": "住宅", "value": 104600, "note": "87.51%"},
                        {"label": "商业", "value": 10095, "note": "8.45%"},
                        {"label": "12班幼儿园", "value": 4100, "note": "3.43%"},
                        {"label": "托育+物业", "value": 740, "note": "0.62%"},
                    ],
                    "source_refs": [official_ref, taskbook_ref, review_ref],
                    "source_note": "104600 + 10095 + 4100 + 500 + 240 = 119535㎡。",
                }
            ],
            evidence_type="observed_fact",
            layout="module_summary",
        ),
        _page(
            page_id="ad1-03",
            section_id="AD1",
            page_code="AD1·03",
            title="AD1 · 塔楼数量必须等待坐标化可建包络，而不是从红线面积猜测",
            question="当前能否判断塔楼数量、落位和底盘边界？",
            takeaway="宗地图只提供坐标底板；退线、铁路与轨道控制、连廊接口、覆盖率和公共空间尚未在同一坐标系叠合，因此不能锁定塔楼数量。",
            impact="以宗地图界址点建模，叠加全部控制线后输出可建包络版本，再允许设计团队进入容量路线。",
            source_refs=[parcel_ref, official_ref, planning_ref, taskbook_ref],
            blocks=[
                _image_block(
                    parcel_page,
                    "宗地坐标底板｜《宗地图》第1页；后续需叠加退线、轨道与连廊控制",
                    [parcel_ref],
                )
            ],
            evidence_type="observed_fact",
        ),
        _page(
            page_id="ad1-04",
            section_id="AD1",
            page_code="AD1·04",
            title="AD1 · 首轮竖向设计至少要同时锁定四个控制面",
            question="80米限高、抬板、连廊和地下控制如何进入同一竖向模型？",
            takeaway="80米是住宅高度上限而非必然用满值；抬板不超过20米、连廊接口标高10.250米、地下轨道控制共同决定竖向组织。",
            impact="首轮模型分别标出自然地面、消防登高起算、连廊接口和地下禁入边界，任何一项缺失都不进入方案评审。",
            source_refs=[official_ref, taskbook_ref, planning_ref, review_ref],
            blocks=[
                {
                    "type": "narrative",
                    "text": "竖向关系为任务书参数关系，不代表已确定的剖面方案。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD1-VERTICAL-CONTROLS",
                    title="首轮竖向控制面｜非比例关系",
                    nodes=[
                        (
                            "上限面",
                            "住宅高度≤80m",
                            ["仍叠加航空限高", "从消防登高面起算", "不能默认足量用满"],
                        ),
                        (
                            "平台层",
                            "抬板政策≤20m",
                            ["核增条件待复核", "消防爬坡与登高面", "归家与会所竖向联动"],
                        ),
                        (
                            "接口层",
                            "连廊标高10.250m",
                            ["净宽≥4m", "廊下净高≥5m", "与南街坊公共衔接"],
                        ),
                        (
                            "地下层",
                            "铁路与28号线控制",
                            ["施工构件不得侵入", "地下室边界待坐标", "报规前取得专项意见"],
                        ),
                    ],
                    footer="所有参数回绑规划文件和任务书；正式许可证及专项意见到达后触发版本复核。",
                    source_refs=[official_ref, taskbook_ref, planning_ref, review_ref],
                )
            ],
        ),
        _page(
            page_id="ad2-01",
            section_id="AD2",
            page_code="AD2·01",
            title="AD2 · 未来设计不是单解海景，而是同时求解五项相互牵制的任务",
            question="所有候选路线必须同时解决哪些矛盾？",
            takeaway="景观捕获不能牺牲通透率和楼间私密；金科路活跃界面、南侧连廊、幼儿园独立性与地下控制还会共同改变首层和底盘。",
            impact="把五项矛盾写成设计任务书硬任务；任何路线只解决其中一项，都不得进入下一轮。",
            source_refs=[taskbook_ref, review_ref, planning_ref],
            blocks=[
                {
                    "type": "narrative",
                    "text": "以下是必须同时求解的任务，不是预设的空间形态。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD2-CONSTRAINT-CONFLICTS",
                    title="约束驱动的五项联立任务",
                    nodes=[
                        (
                            "景观",
                            "捕获海景与公园视线",
                            ["最大化有效景观面", "避免把远景叙事当成交付", "校核周边全建成遮挡"],
                        ),
                        (
                            "通透",
                            "避免屏风与对视",
                            ["主道路界面不宜>70m板楼", "楼栋间距>18m", "卧室转角窗同步校核"],
                        ),
                        (
                            "城市",
                            "金科路活跃界面",
                            ["主入口与示范界面", "商业界面占比目标待核", "遮阳避雨与慢行连续"],
                        ),
                        (
                            "公共",
                            "连廊与配套独立",
                            ["南侧公共连廊", "幼儿园宜西北且独立", "体育与儿童场地完整落位"],
                        ),
                        (
                            "工程",
                            "地下与消防可实施",
                            ["轨道控制区禁入", "消防车坡道与登高面", "商业/住宅车流隔离"],
                        ),
                    ],
                    footer="五项任务来自任务书与审查要点；其解法由后续设计团队提交。",
                    source_refs=[taskbook_ref, review_ref, planning_ref],
                )
            ],
        ),
        _page(
            page_id="ad2-02",
            section_id="AD2",
            page_code="AD2·02",
            title="AD2 · 当前只建立统一评价尺，不给不存在的候选路线打分",
            question="未来收到设计路线后，怎样做到同口径比较？",
            takeaway="评价必须基于可复算输出：容量闭合、景观可视、通透与对视、公共界面、工程复杂度和产品适配；定性口号不计分。",
            impact="向设计团队下发统一计算表与图纸清单；缺少任一底表的路线只登记为待补，不排序。",
            source_refs=[taskbook_ref, review_ref, parcel_ref, official_ref],
            blocks=[
                {
                    "type": "narrative",
                    "text": "本页定义测量对象和交付物，不生成权重、得分或推荐。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD2-EVALUATION-RULER",
                    title="候选路线统一评价尺｜先量后比",
                    nodes=[
                        (
                            "容量闭合",
                            "逐项功能与指标",
                            ["规定面积差=0", "覆盖率/绿化/公共空间", "户配与核心筒可复算"],
                        ),
                        (
                            "资源兑现",
                            "景观与日照实测",
                            ["各户有效视野", "周边全建成遮挡", "中低楼层反证"],
                        ),
                        (
                            "空间质量",
                            "通透、对视与归家",
                            ["城市通廊", "窗对窗距离", "主客/后勤/商业分流"],
                        ),
                        (
                            "城市责任",
                            "金科路与公共接口",
                            ["活跃界面", "连廊接驳", "幼儿园与公共场地"],
                        ),
                        (
                            "工程实施",
                            "结构、消防与轨道",
                            ["基坑和控制线", "消防登高", "抬板与结构转换"],
                        ),
                        (
                            "产品适配",
                            "客群与总价校准",
                            ["家庭结构", "面积与房间数", "退出条件与替代盘"],
                        ),
                    ],
                    footer="收到候选路线前只冻结定义、单位、计算方法和来源；不预置赢家。",
                    source_refs=[taskbook_ref, review_ref, parcel_ref, official_ref],
                )
            ],
        ),
        _page(
            page_id="ad3-01",
            section_id="AD3",
            page_code="AD3·01",
            title="AD3 · 企鹅岛客群的差异首先体现在家庭决策和空间任务",
            question="未来高管与员工入驻后，哪些空间需求值得进入产品验证？",
            takeaway="高管家庭、家庭化核心骨干和生态链决策者应分别验证私密接待、双人办公、代际与成长空间；普通员工当前更影响商业和长期改善池。",
            impact="户型研究按四类行为路径设置功能原型，但在总价、家庭结构与现居分布校准前不冻结面积段占比。",
            source_refs=[
                taskbook_ref,
                "src-penguin-traffic-20260721",
                "src-penguin-housing-20250221",
                "src-metro15-20260518",
                "src-yunxi",
            ],
            blocks=[
                {
                    "type": "narrative",
                    "text": "分层是产品研究假设，不是客户数量或购买概率。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD3-PENGUIN-SPACE-NEEDS",
                    title="企鹅岛未来客群 × 迁居触发 × 空间任务",
                    nodes=[
                        (
                            "优先验证",
                            "总部高管家庭",
                            [
                                "触发：核心团队迁驻",
                                "空间：私密接待/安保/家政",
                                "反证：深圳湾等替代选择",
                            ],
                        ),
                        (
                            "核心验证",
                            "家庭化高级骨干",
                            [
                                "触发：婚育与改善",
                                "空间：双人办公/成长房/收纳",
                                "反证：总价与教育匹配",
                            ],
                        ),
                        (
                            "机会验证",
                            "生态链决策者",
                            [
                                "触发：业务协同与资产配置",
                                "空间：会客/弹性办公/圈层",
                                "反证：流动性与交付风险",
                            ],
                        ),
                        (
                            "非当前主力",
                            "年轻与普通员工",
                            [
                                "选择：园区公寓或租赁",
                                "贡献：商业与长期改善池",
                                "不得推导当前高总价销量",
                            ],
                        ),
                    ],
                    footer="客群空间任务由已冻结 SC2 行为情景转译；需通过匿名访谈和选择实验校准。",
                    source_refs=[
                        taskbook_ref,
                        "src-penguin-traffic-20260721",
                        "src-penguin-housing-20250221",
                        "src-metro15-20260518",
                        "src-yunxi",
                    ],
                )
            ],
            evidence_type="model_simulation",
        ),
        _page(
            page_id="ad3-02",
            section_id="AD3",
            page_code="AD3·02",
            title="AD3 · 面积段必须由总价、家庭功能和交通核共同反推",
            question="现阶段能否把200–400㎡写成既定产品谱系？",
            takeaway="任务书明确面积及比例需向建设单位确认；在总价上限、家庭房间数、得房率与核心筒效率未校准前，任何固定面积段和户配比例都属于越权。",
            impact="先完成总价—面积—功能—核心筒—货量五联表，再由建设单位确认产品谱系。",
            source_refs=[taskbook_ref, "src-yunxi", "src-gcf-hj"],
            blocks=[
                {
                    "type": "narrative",
                    "text": "校准链只定义变量与验收顺序，不填入未经客研和成本确认的数值。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD3-AREA-CALIBRATION",
                    title="总价到货量的五步校准链",
                    nodes=[
                        (
                            "01",
                            "家庭总价上限",
                            ["首付与现金流", "可接受月供/资金占用", "替代板块与退出条件"],
                        ),
                        (
                            "02",
                            "可承受建筑面积",
                            ["总价÷可验证单价带", "装修及持有成本另列", "不使用预计售价作承诺"],
                        ),
                        (
                            "03",
                            "家庭功能程序",
                            ["常住人数与代际", "办公/家政/社交", "不是简单放大开间"],
                        ),
                        (
                            "04",
                            "楼型与交通核",
                            ["专梯入户目标", "T2/T3效率比较", "后勤流线与得房率"],
                        ),
                        (
                            "05",
                            "户配与货量",
                            ["住宅计容104600㎡", "面积段比例由甲方确认", "数量与首开节奏后置"],
                        ),
                    ],
                    footer="任务书原文：户型面积及比例详询建设单位；本页因此不锁定200–400㎡或任何比例。",
                    source_refs=[taskbook_ref, "src-yunxi", "src-gcf-hj"],
                    sequential=True,
                )
            ],
            evidence_type="model_simulation",
        ),
        _page(
            page_id="ad3-03",
            section_id="AD3",
            page_code="AD3·03",
            title="AD3 · 会所、商业与公共配套要先明确服务对象和运营责任",
            question="底盘内的会所、商业与公共空间分别承担什么任务？",
            takeaway="商业10095㎡和法定公共配套是硬账本；3000㎡私享会所、5000–10000㎡泛架空会所属于任务书要求，仍需合规、成本和运营模型复核。",
            impact="将底盘功能分为硬性配置、项目要求、待验证运营三类；没有责任主体和开放边界的功能不得进入设计定稿。",
            source_refs=[taskbook_ref, official_ref, review_ref],
            blocks=[
                {
                    "type": "narrative",
                    "text": "面积来自任务书与规划指标，不等于已通过报规或已验证价值。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD3-PLINTH-PROGRAM",
                    title="底盘功能分层｜对象、边界与验证责任",
                    nodes=[
                        (
                            "硬性配置",
                            "商业与公共配套",
                            ["商业10095㎡", "幼儿园/托育/物业", "体育1000㎡+儿童1200㎡"],
                        ),
                        (
                            "项目要求",
                            "私享与泛会所",
                            ["私享会所3000㎡", "泛架空5000–10000㎡", "面积口径与核增条件复核"],
                        ),
                        (
                            "空间边界",
                            "公共、业主与商业分流",
                            ["公共连廊保持开放", "住宅归家私密", "商业车库和人流独立"],
                        ),
                        (
                            "运营验证",
                            "谁建设、谁运营、谁付费",
                            ["全周期成本", "开放时段与安保", "功能利用率与退出机制"],
                        ),
                    ],
                    footer="会所和架空空间只先作为体验与运营任务，不直接计入售价或去化贡献。",
                    source_refs=[taskbook_ref, official_ref, review_ref],
                )
            ],
        ),
        _page(
            page_id="ad4-01",
            section_id="AD4",
            page_code="AD4·01",
            title="AD4 · 案例只迁移空间机制，不能替项目选择形态",
            question="保利珠江天悦等案例目前能支持到什么程度？",
            takeaway="抬高盖板、立体归家和景观共享可进入机制库；案例的高度、成本、售价和具体形态均不能直接迁移到80米限高与轨道控制下的本项目。",
            impact="每项案例启发必须补齐“适用条件、不可迁移项、项目验证图纸”后，才能进入设计探索。",
            source_refs=[initial_reference_ref, taskbook_ref],
            blocks=[
                _image_block(
                    case_image,
                    "案例资料原图｜《初始参考_RGX.pptx》第26页嵌入图；仅用于机制研究",
                    [initial_reference_ref],
                )
            ],
        ),
        _page(
            page_id="ad4-02",
            section_id="AD4",
            page_code="AD4·02",
            title="AD4 · 每段客户体验都必须同时通过工程与运营闸门",
            question="到达、归家、会客、观景和屋顶体验如何避免停留在效果图？",
            takeaway="体验序列只有在消防、结构、轨道、风环境、维护和开放边界同时成立时才可进入方案；视觉叙事不能替代技术闭环。",
            impact="为每段体验指定图纸、专项负责人和验收时点；任一硬闸门失败即回到空间任务重新求解。",
            source_refs=[taskbook_ref, review_ref, planning_ref],
            blocks=[
                {
                    "type": "narrative",
                    "text": "体验序列为设计任务，不代表已选定总图、户型或立面。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD4-EXPERIENCE-GATES",
                    title="体验序列 × 技术闸门",
                    nodes=[
                        (
                            "城市到达",
                            "金科路入口与示范界面",
                            [
                                "图纸：道路开口/落客/慢行",
                                "闸门：交叉口与活跃界面",
                                "责任：规划+交通",
                            ],
                        ),
                        (
                            "底盘分流",
                            "公共、商业与住宅分层",
                            [
                                "图纸：首层/二层/地库流线",
                                "闸门：消防与轨道控制",
                                "责任：建筑+结构+机电",
                            ],
                        ),
                        (
                            "私密归家",
                            "大堂、核心筒与后勤",
                            ["图纸：户型/核心筒/服务流线", "闸门：效率与对视", "责任：产品+建筑"],
                        ),
                        (
                            "景观体验",
                            "户内、花园与第六立面",
                            [
                                "图纸：视线/风环境/屋顶功能",
                                "闸门：结构、防水与运维",
                                "责任：景观+结构+物业",
                            ],
                        ),
                    ],
                    footer="所有节点同时给出客户价值、技术条件和运营责任，避免只交效果图。",
                    source_refs=[taskbook_ref, review_ref, planning_ref],
                    sequential=True,
                )
            ],
        ),
        _page(
            page_id="ad5-01",
            section_id="AD5",
            page_code="AD5·01",
            title="AD5 · 本轮最终交付是设计任务书和评价基线，不是主推方案",
            question="SC 与 AD 完成后，应向设计团队正式发出什么？",
            takeaway="Input 2 交付四级任务板：硬约束必须满足、目标项应响应、探索项需验证、越权项明确禁止；收到用户提交的候选方案前不进入方案审查。",
            impact="发出设计任务书、统一底表和补证清单；设计团队提交候选路线后，由用户决定是否另行发起 Input 3。",
            source_refs=[
                official_ref,
                taskbook_ref,
                review_ref,
                parcel_ref,
                "src-penguin-traffic-20260721",
                "src-penguin-housing-20250221",
            ],
            blocks=[
                {
                    "type": "narrative",
                    "text": "阶段交付物采用四级优先顺序，并绑定来源与验收方式。",
                }
            ],
            diagrams=[
                _analysis_diagram(
                    diagram_id="AD5-DESIGN-BRIEF",
                    title="Input 2 设计任务书四级任务板",
                    nodes=[
                        (
                            "必须满足",
                            "法定与工程硬约束",
                            ["完整容量账本", "80m/退线/轨道/连廊", "公共配套与消防"],
                        ),
                        (
                            "应当响应",
                            "市场与客群任务",
                            ["企鹅岛家庭需求验证", "景观/私密/通勤", "总价与面积校准"],
                        ),
                        (
                            "允许探索",
                            "待验证空间机制",
                            ["抬板与立体归家", "会所与第六立面", "案例机制本地化"],
                        ),
                        (
                            "明确禁止",
                            "当前越权输出",
                            ["自造候选路线与得分", "无底表主推或淘汰", "未校准售价/去化承诺"],
                        ),
                    ],
                    footer="下一阶段触发：用户提交可比较的候选方案，并主动确认新的介入任务。",
                    source_refs=[
                        official_ref,
                        taskbook_ref,
                        review_ref,
                        parcel_ref,
                        "src-penguin-traffic-20260721",
                        "src-penguin-housing-20250221",
                    ],
                )
            ],
        ),
    ]
    _set_page_metadata(pages)
    return pages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdftoppm", type=Path, required=True)
    args = parser.parse_args()

    v1_root = args.v1_root.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="dds-shenzhen-ad-",
        dir=output.parent,
    ) as directory:
        pages = build_ad_pages(
            v1_root,
            args.pdftoppm.resolve(),
            Path(directory),
        )
        result = LegacyProjectReportMigrator().migrate(
            v1_root,
            output,
            project_id="SZ-A002-0113-AD",
            project_name="深圳宝安中心区 A002-0113 约束驱动设计任务书",
            imported_at=FROZEN_AT,
            as_of="2026-07-24",
            selected_mode=2,
            intervention_brief={
                "user_goal": (
                    "在已通过 SC 基线之上，把市场、未来客群、法定与工程约束"
                    "转成可供设计团队执行的定位、容量、产品与空间任务"
                ),
                "decision_audience": "项目负责人、策划、产品、设计与投资团队",
                "decision_questions": [
                    "哪些输入有资格进入设计，完整容量和可建包络如何建立？",
                    "企鹅岛未来客群如何转成产品参数与空间验证任务？",
                    "向设计团队交付哪些硬任务、探索项和评价口径？",
                ],
                "priorities": {
                    "capacity_and_geometry": 0.30,
                    "constraint_driven_strategy": 0.25,
                    "future_customer_to_product": 0.25,
                    "space_and_delivery_gates": 0.20,
                },
                "prohibited_conclusions": [
                    "未经用户提交候选方案而形成的方案比较、排序、主推或淘汰",
                    "未经建设单位确认的面积段、户配比例与货量",
                    "未经真实成交、客研、成本与工程校准的售价、去化或溢价承诺",
                ],
                "confirmed_at": FROZEN_AT,
            },
            input_profile={
                "address": "深圳市宝安中心区 A002-0113 宗地",
                "future_demand_sources": list(PENGUIN_SOURCE_IDS),
                "constraint_sources": [
                    {"source_ref": "client://规划设计要点"},
                    {"source_ref": "client://招标任务书"},
                    {"source_ref": "client://审查要点"},
                    {"source_ref": "client://宗地图"},
                ],
                "materials": [
                    {"filename": "宝中方案招标任务书0710.docx"},
                    {"filename": "宝安中心区DY02-01地块规划设计条件研究.pdf"},
                    {"filename": "规划设计要点.pdf"},
                    {"filename": "宗地图.pdf"},
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
    print(f"selected_mode={result.decision_seed['meta']['analysis_profile']['selected_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

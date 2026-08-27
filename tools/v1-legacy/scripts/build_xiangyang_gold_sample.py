"""Build the declarative Xiangyang DDS gold sample through the generic runner.

This module contains project-specific facts only.  It never modifies the
caller-owned ``inbox`` or legacy ``reports`` tree; all declarations and frozen
snapshots are written to ``work`` and compiled delivery files to ``delivery``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from build_project_report import SUPPORTED_PROFILE, build_project_report
except ImportError:  # Imported as scripts.build_xiangyang_gold_sample.
    from scripts.build_project_report import SUPPORTED_PROFILE, build_project_report


PROJECT_NAME = "国投·建华望府（星河国际项目）"
PROJECT_ID = "xiangyang-guotou-jianhua-wangfu"
AS_OF = "2026-07-15"


ORIGINAL_FAMILIES: dict[str, str] = {
    "0710国投建华望府襄阳_汇报文本.pdf": "FAM-DESIGN-0710",
    "项目资料/星河国际基础资料.pdf": "FAM-OWNER-BRIEF",
    "项目资料/设计要求.pdf": "FAM-OWNER-BRIEF",
    "项目资料/20260626襄阳项目疑问-回复.docx": "FAM-OWNER-QA-20260626",
    "项目资料/20260508星河国际项目开发投资测算_成本测算.xlsx": "FAM-FINANCE-20260508",
    "技术规定规范/中心城区“抬板式住宅”规划技术要点(征求意见稿) -0629.doc": "FAM-DECK-DRAFT-0629",
    "技术规定规范/中心城区“抬板式住宅”规划技术要点(征求意见稿) -0629.docx": "FAM-DECK-DRAFT-0629",
    "技术规定规范/针对抬板征询稿补充问题0629.docx": "FAM-DECK-QUESTIONS-0629",
    "技术规定规范/市政府办市人民政府专题会议纪要国土空间规划项目专题会会议纪要(第58期)2025100600.pdf": "FAM-PLANNING-MINUTE-58",
    "技术规定规范/市人民政府办公室关于印发《襄阳市城市规划管理技术规定（试行）》（建筑工程·绿化篇）的通知 - 襄阳市政府信息公开平台.pdf": "FAM-PLANNING-BASE-2021",
    "技术规定规范/襄自然资规〔2020〕1号-襄阳市自然资源和规划局关于印发《襄阳市容积率计算规则》的通知-自然资源和规划局-2020.09.28~2022.09.28.pdf": "FAM-FAR-2020-EXPIRED",
    "技术规定规范/襄阳市自然资源和城乡建设局关于《襄阳市技术规定建筑面宽和建筑退界的优化调整》的通知-自然资源和城乡建设局.pdf": "FAM-WIDTH-SETBACK-2025",
    "技术规定规范/襄阳市自然资源和城乡建设局关于印发《襄阳市城市规划管理技术规定有关居住建筑山墙间距等内容的补充规定》的通知-自然资源和城乡建设局.pdf": "FAM-GABLE-2025",
    "技术规定规范/襄阳市自然资源和规划局关于印发《关于开展城市森林花园建筑(第四代建筑)试点工作的通知》的通知-自然资源和规划局.pdf": "FAM-4THGEN-2023",
    "技术规定规范/襄阳市自然资源和规划局关于印发《襄阳市建设工程日照分析管理办法（试行）》的通知-自然资源和规划局.pdf": "FAM-SUNLIGHT-2023",
    "技术规定规范/第二批建设工程设计方案不合理图集.pdf": "FAM-REVIEW-ATLAS-2024",
}


FAMILY_SOURCE_IDS = {
    "FAM-DESIGN-0710": "SRC-XY-DESIGN",
    "FAM-OWNER-BRIEF": "SRC-XY-OWNER-BRIEF",
    "FAM-OWNER-QA-20260626": "SRC-XY-OWNER-QA",
    "FAM-FINANCE-20260508": "SRC-XY-FINANCE",
    "FAM-DECK-DRAFT-0629": "SRC-XY-DECK-DRAFT",
    "FAM-DECK-QUESTIONS-0629": "SRC-XY-DECK-QUESTIONS",
    "FAM-PLANNING-MINUTE-58": "SRC-XY-PLANNING-MINUTE",
    "FAM-PLANNING-BASE-2021": "SRC-XY-PLANNING-BASE",
    "FAM-FAR-2020-EXPIRED": "SRC-XY-FAR-EXPIRED",
    "FAM-WIDTH-SETBACK-2025": "SRC-XY-WIDTH-SETBACK",
    "FAM-GABLE-2025": "SRC-XY-GABLE",
    "FAM-4THGEN-2023": "SRC-XY-4THGEN",
    "FAM-SUNLIGHT-2023": "SRC-XY-SUNLIGHT",
    "FAM-REVIEW-ATLAS-2024": "SRC-XY-REVIEW-ATLAS",
}


KNOWN_SOURCE_DATES = {
    "SRC-XY-DESIGN": "2026-07-10",
    "SRC-XY-OWNER-QA": "2026-06-26",
    "SRC-XY-FINANCE": "2026-05-08",
    "SRC-XY-DECK-DRAFT": "2026-06-29",
    "SRC-XY-DECK-QUESTIONS": "2026-06-29",
    "SRC-XY-PLANNING-MINUTE": "2025-10-06",
    "SRC-XY-FAR-EXPIRED": "2020-09-28",
    "SRC-OFFICIAL-NBS-H1-2026": "2026-07-15",
    "SRC-OFFICIAL-NBS-PRICE-202606": "2026-07-15",
    "SRC-OFFICIAL-XY-2024": "2025-04-22",
    "SRC-OFFICIAL-XY-POLICY-2026": "2026-01-06",
    "SRC-OFFICIAL-XY-PREFAB-2026": "2026-05-14",
    "SRC-OFFICIAL-XY-MAYDAY-2025": "2025-05-08",
    "SRC-OFFICIAL-XY-QUALITY-2024": "2024-12-31",
    "SRC-OFFICIAL-XY-SUPPORT-2026": "2026-01-30",
    "SRC-MARKET-XY-NEWHOUSE": "2026-07-15",
    "SRC-AMAP-POI": "2026-07-15",
    "SRC-DDS-METHOD": "2026-07-15",
    "SRC-DCBBS-XY-202605": "2026-05-31",
}


SLIDE_PAGES = (
    1, 6, 9, 22, 24, 25, 27, 31, 32, 34, 37, 40, 51, 60, 61,
    65, 68, 69, 70, 71, 72, 73, 74, 82, 83, 86, 89, 91, 93, 95,
    98, 99, 100, 101, 102, 104, 107, 118, 128, 134, 143, 150, 155, 162,
)


TOPICS: dict[str, list[tuple[str, str]]] = {
    "SC1": [
        ("双名项目身份", "报告统一使用“国投·建华望府（星河国际项目）”，旧名只作版本谱系。"),
        ("输入原件谱系", "16份非派生原件构成审计边界，任何TXT、PNG或幻灯片图都不增加权重。"),
        ("证据家族去重", "DOC/DOCX同稿和基础资料/设计要求重叠后，独立证据权重收敛为14个家族。"),
        ("坐标使用边界", "112.135662,32.050012仅是路口代理点，不得冒充红线质心或场地入口。"),
        ("汇报对象锁定", "结论同时服务区域总负责人、投拓、设计管理与建筑总监，不以专业分工割裂决策链。"),
        ("决策问题树", "先回答是否进入、用什么产品、如何落地，再讨论表达和附加价值。"),
        ("冲突事实登记", "货值、日照户数、道路宽度与抬板政策效力均保留双方口径，不做静默平均。"),
        ("使用边界总览", "本版可支持方案收敛与补证任务，不取代规划审批、成本审计和融资批复。"),
    ],
    "SC2": [
        ("全国周期位置", "2026年上半年开发投资、销售面积、销售额与到位资金同时下降，项目必须把现金流安全放在货值想象之前。"),
        ("襄阳年度基本面", "2024年襄阳房地产投资下降8.1%，但商品房销售面积微增0.3%，市场是结构分化而非简单消失。"),
        ("新房面积价格", "2026年6月襄阳各面积段新房指数均弱于去年，90–144㎡环比相对稳定但不代表价格上涨。"),
        ("樊城竞品价格", "周边可观察项目报价约8000–13500元/㎡，只能用于定位参照，不能等同网签成交价。"),
        ("面积段供需代理", "本地楼盘库缺库存和月去化，因此10–15㎡颗粒度仅做“项目覆盖数×持续在售月数代理”，非库存占比。"),
        ("竞品持续在售", "长周期在售与价格口径不一是流速风险线索，必须补网签和库存后才能量化去化。"),
        ("改善客群门槛", "118/130㎡承担基盘，145/168㎡与218–225㎡必须用场景、服务和稀缺性抵消总价门槛。"),
        ("总价敏感区间", "当单价缺成交锚点时，应先以面积梯级管理总价暴露，而不是用虚构溢价弥补产品失配。"),
        ("政策支持边界", "公积金额度和高品质住宅支持可改善购买条件，但东津特定政策不得外推到樊城。"),
        ("装配式建造约束", "中心城区新建建筑装配式要求将反向约束立面、标准化与成本，需以正式通知进一步核定。"),
        ("短期热销辨识", "节假日销售增长只证明特定营销窗口有效，不能直接换算全年去化速度。"),
        ("高品质需求线索", "2024年年末官方信号显示140㎡以上改善客群存在，但项目仍需用价格、户型与流速反证。"),
        ("保障房对照边界", "5995元/㎡保障性住房是支付力背景，不得作为商品住宅的直接竞品定价。"),
        ("目标项目排除", "本地楼盘库对目标项目的售卖状态显然不可靠，必须从竞品样本中排除以防自引证。"),
        ("一线与周边分工", "周边竞品用于判断价格、面积与流速；一线案例只用于空间机制和设计表达参考。"),
        ("成功项目证据", "高流速案例必须同时具有真实去化、价格稳定和产品机制证据，当前只保留待补证案例槽位。"),
        ("滞销反向证据", "长期在售、打折与价格下调项目应进入反证池，但未获得网签时只标为低信度线索。"),
        ("公交教育覆盖", "代理坐标周边125–301m有公交点，小学约315m、中学约558m，仅表达步行距离线索。"),
        ("医疗公共服务", "代理坐标周边医疗点约715–889m，应继续核定等级、入口和真实步行路线。"),
        ("商业休闲半径", "诸葛亮广场约947m，主要商业约955–1311m，项目内部配套应补日常高频而不是复制大型商业。"),
        ("客户决策链路", "产品面积、总价、学校医疗、归家体验与交付风险需在同一决策链里比较。"),
        ("市场机会收口", "机会不是单一大户型或高价，而是118–168㎡产品梯级在总价、品质和流速之间的平衡。"),
    ],
    "SC3": [
        ("A/B地块关系", "A/B两地块必须作为一个开发系统管理道路、分期、公建与归家共享。"),
        ("A地块指标", "A地块容积率3.3调整为2.8、密度24.5%调整为25%，公服不少于2600㎡，住商比9:1。"),
        ("B地块指标", "B地块容积率3.3调整为2.5、密度19.5%调整为25%，住宅计容建面不超过100723㎡。"),
        ("15m道路闸门", "12m仅获原则同意且必须依批复，正式文件前强排仍以15m市政支路为基线。"),
        ("征迁差异处理", "不需原地安置可减少产品夹杂，但地块交付和征迁边界仍需按分期核验。"),
        ("商业上限约束", "A地块商业上限10%，商业只应支撑城市界面与日常服务，不应挤占住宅货值。"),
        ("80m限高约束", "80m限高直接决定层数、标准层效率与立面比例，不能在表达阶段才补算。"),
        ("日照口径冲突", "设计文本记录对外231户，成本表又以86户计算补偿，必须以日照模型与实户表复核。"),
        ("消防闭环要求", "抬板、连桥、下沉会所和板上花园必须同时闭合消防车道、登高面、疏散与救援。"),
        ("人防边界缺口", "当前缺人防等级、建设量、设防范围和异地建设口径，地下室与车位经济性不可定案。"),
        ("抬板政策效力", "征求意见稿不是审批依据；第四代建筑试点可作条件库，但必须取得具名适用回复。"),
        ("四代花园约束", "花园跨两层、净高不低于5.6m、投影不超过6m、占套内不超过40%，应作为同时校核条件。"),
        ("红线标高缺口", "缺GeoJSON红线、测量坐标、地形标高、管线和实际入口，方案图只能视为意向强排。"),
        ("场地边界收口", "法律安全和实测物理先于设计表达；道路、日照、消防、人防未闭合前只做方向性承诺。"),
    ],
    "AD1": [
        ("十轮强排收敛", "十轮强排不按图纸数量并列，而是收敛为同边界、同指标、同货值口径的三个方向。"),
        ("方案1空间逻辑", "方案1·均衡兑现对应原方案1.1，在中轴庭院、城市界面与分期之间保持均衡。"),
        ("方案1货值口径", "方案1图纸页出现17.45亿，分项汇总为17.553255亿，报告以17.55亿为设计测算口径并保留冲突。"),
        ("方案2空间逻辑", "方案2·四代价值对应原方案1.2，以花园体系、连桥与抬板产生差异化。"),
        ("方案2政策成本", "方案2的17.66亿货值增量必须覆盖结构、防水、绿化、运维与审批风险，不以单页溢价做决策。"),
        ("方案3空间逻辑", "方案3·货值进取对应最终“方案2”且源自初始方案7，重点放大货值与形象上限。"),
        ("方案3资金风险", "17.78亿仅是设计阶段货值，更高的大户型和表达投入会放大峰值资金与去化尾部。"),
        ("三方案同口径", "比选必须同时读取容量、货量、总价、土方、地下、消防、日照、人防、成本和运维。"),
        ("共性风险剥离", "15m道路、红线标高和融资成本是三案共性缺口，不应误装成某一方案的优缺点。"),
        ("强排比选结论", "方案1在证据完整度和可回退性上占优；方案2/3只在额外闸门全部关闭后进入下轮。"),
    ],
    "AD2": [
        ("唯一主推方案", "当前唯一主推为方案1·均衡兑现，方案2和3均不与主推并列。"),
        ("主推核心理由", "方案1以较少政策依赖闭合基盘货量、城市界面、分期与可实施性。"),
        ("切换方案2闸门", "只有四代/抬板政策正式适用、消防日照结构闭合、成本修复且价格与流速覆盖增量投入时切换。"),
        ("切换方案3闸门", "方案3除共性闸门外，还必须通过峰值资金、审批和大户型去化压力测试。"),
        ("方案1回退机制", "日照、消防或总价不达标时，方案1应先回退局部楼栋与户型配比，不整盘跳转高投入方向。"),
        ("方案2回退机制", "政策或结构不闭合时，四代花园与连桥必须从主体割离，回退到方案1骨架。"),
        ("方案3回退机制", "峰值资金或大户型认购不达门槛时，方案3不做局部缝补，直接回退方案1。"),
        ("主推决策收口", "下一阶段资源只投入方案1的专项闭合，方案2/3保留为条件触发的备选路径。"),
    ],
    "AD3": [
        ("产品梯级总览", "118/130/145/168/218–225㎡构成从基盘改善到标杆改善的五级产品语言。"),
        ("A地块产品配比", "A地块118㎡与130㎡约各占50%，应作为首开流速和总价可控的基盘。"),
        ("B地块基盘产品", "B地块118+130㎡合计60%，首要任务是保证同面积段的户型差异与库存可调。"),
        ("B地块进阶产品", "B地块145+168㎡合计35%，必须对应更好朝向、景观、归家和公共空间资源。"),
        ("B地块标杆产品", "218–225㎡约5%，只应布置在真实稀缺位，并以小货量和定向蓄客控制尾部风险。"),
        ("118㎡价值任务", "118㎡不能仅做缩小面积，应以三房可变、收纳、家政和公共面宽提升改善感。"),
        ("130㎡核心竞争力", "130㎡是供应最密集的核心战场，必须用南向采光、餐客一体和主卧套系形成可见差异。"),
        ("145㎡机会边界", "145㎡可承接改善跃迁，但必须把房间数、面宽、动静分区与总价同时做对。"),
        ("168㎡资源绑定", "168㎡必须绑定更强景观面、私密电梯厅或会所权益，否则只放大总价。"),
        ("218㎡去化闸门", "218–225㎡必须在客户名单、认购转化与峰值资金三项达标后再进入实施。"),
        ("分期与首开策略", "首开优先118/130㎡与可见示范界面，145㎡以上随真实去化和价格梯度递进。"),
        ("货量动态调度", "户型库应在结构、立面与设备井共性下保留可调窗口，以认筹和网签反馈改变后续货量。"),
    ],
    "AD4": [
        ("总图空间骨架", "主推总图以中轴、组团庭院、城市界面和分期接口构成一张可实施骨架。"),
        ("A/B地块联动", "两地块在道路分隔下仍应通过轴线、界面、服务与分期节奏建立整体识别。"),
        ("抬板三张剖面", "抬板必须同时表达城市入口、中心庭院和住宅单元剖面，才能验证土方、排水、防水与归家。"),
        ("下沉会所闭环", "下沉会所只有在采光、防水、疏散、运营与首开展示同时闭合时才保留。"),
        ("板上花园边界", "板上花园应优先解决可达、土荷载、排水、维修和产权边界，不以绿量表达取代工程闭环。"),
        ("连桥系统任务", "连桥必须证明连接什么高频功能、如何疏散、如何防水和运维，否则仅是高成本符号。"),
        ("消防登高面", "消防登高面、景观、架空层与归家动线必须叠图校核，不得分专业各自成图。"),
        ("人车流线分层", "车行快速入库、人行完整归家、访客示范和后勤服务应用高程与节点分层。"),
        ("归家序列设计", "归家价值从城市街道、社区入口、大堂、庭院到单元逐层递进，不集中在售楼处。"),
        ("示范区永临结合", "示范区优先落在永久入口、会所或首开庭院，减少拆改和一次性表达投入。"),
        ("城市界面分级", "主干道界面建立品牌识别，15m支路控制生活尺度，内部庭院回归静谧与可达。"),
        ("户型与立面联动", "面宽、设备平台、花园、阳台和立面模数应从户型库开始联动，避免后置包装。"),
        ("立面材料分配", "石材、金属与涂料投入应集中在近人界面、入口和视线终点，非关键面用模数和阴影提质。"),
        ("景观投入顺序", "景观先投入排水、遮荫、活动与高频动线，再追加精神性雕塑和低频装置。"),
        ("杭州天奕迁移", "天奕只迁移抬板与下沉会所的空间机制，不迁移杭州地价、客群或溢价结果。"),
        ("天宸上院迁移", "天宸上院用于校准庭院、归家和界面的层次，落地前仍须匹配襄阳气候与成本。"),
        ("时舟里迁移", "时舟里只用于小尺度社区节点和材料克制的表达参考，不直接证明本项目去化。"),
        ("设计任务书收口", "总图、户型、立面、景观、会所和示范区必须分别形成可测指标、责任人和验收图纸。"),
    ],
    "AD5": [
        ("传统空间G0", "当前仅有路口代理坐标，缺红线、真北、标高、入口与地形，传统空间文化固定降级G0。"),
        ("物理事实优先", "日照、噪声、洪涝、风、污染、视野和高差只有实测后才能进入设计或财务。"),
        ("禁止吉凶承诺", "G0不判断青龙白虎、明堂、水口或吉凶，五行八卦和顾问意见不进入售价、ROI或IRR。"),
    ],
    "VA1": [
        ("设计价值溢价", "设计价值溢价不是单一售价增量，而是产品、流速、成本、交付和长期价值的综合最优解。"),
        ("一级底线投入", "法规、消防、日照、人防、防水、结构与基本交付是不得用溢价理由削减的一级底线。"),
        ("二级价值投入", "户型面宽、归家、会所、庭院、遮荫与示范区是能直接改善转化与体验的二级投入。"),
        ("三级表达投入", "立面精饰、艺术装置和低频会所场景属三级表达，必须有小投入、强感知和可回退方案。"),
        ("小成本品质手法", "模数统一、转角阴影、洞口比例、材料收口和灯光层次可以用很小投入改善近人品质。"),
        ("成本体验链路", "每项设计动作都要写明成本位置、客户感知、竞争差异、流速路径与失效条件。"),
        ("上游结论索引", "本章只索引SC2市场机会、SC3工程边界和AD2主推闸门，不重复前策论证。"),
        ("溢价禁入项", "未审批抬板、传统吉凶、一线案例品牌和顾问主观评价均不得直接换算售价。"),
        ("设计价值收口", "资金优先投入一级底线和二级高频价值，三级表达只在预算与转化证据允许时追加。"),
    ],
    "VA2": [
        ("货值口径冲突", "方案1的17.45亿与分项汇总17.553255亿同时保留，财务复核前不选一个方便数字。"),
        ("Excel错引阻断", "成本汇总R8引用空白单元格与管理费单价，该单元格未复核前不得作为融资成本。"),
        ("#REF!错误矩阵", "工作簿至少包含17、21、2、1个#REF!错误的表级分布，所有依赖链应重算而非手工填值。"),
        ("去化数据缺口", "当前缺实际库存、月签约、去化周期和价格折扣，不能用持续在售月数冒充去化。"),
        ("保守情景边界", "保守情景只能定义较低流速、更高折让和更长回款的压力条件，当前不输出伪精确月值。"),
        ("基准情景边界", "基准情景需以周边网签、库存与首开蓄客完成中枢，不以报价均值直接生成。"),
        ("乐观情景边界", "乐观情景只作上限压力测试，不得用未证实溢价单独支撑拿地或转方案。"),
        ("现金流峰值缺口", "开工、地下室、示范区、预售和分期节点未绑定前，峰值资金和回款时间均不可判定。"),
        ("成本敏感性路径", "地下室、抬板结构、防水、园林、装配式与融资成本是下一轮敏感性主变量。"),
        ("售价敏感性路径", "售价敏感性必须分118/130/145/168/218㎡面积段，不用全盘单一均价遮蔽总价差异。"),
        ("IRR禁止输出", "缺真实成本或融资条件且Excel存在错引时，报告明确不输出确定性IRR。"),
        ("财务复核门槛", "只有货值、成本、融资、税费、节奏和去化全部建立版本化输入后，才解锁ROI、IRR与拿地边界。"),
    ],
    "VA3": [
        ("法定风险闭环", "规划负责人应取得道路、抬板、指标和退界的书面适用意见，否则维持方案1保守边界。"),
        ("征迁交地闭环", "投拓与工程负责人应将征迁、场地交付和分期开工写入可验收里程碑。"),
        ("财务错引闭环", "成本与财务负责人应重建R8和#REF!依赖链，形成可复算基线后再解锁投资指标。"),
        ("消防人防闭环", "建筑与专项负责人需完成消防、人防、地库、下沉会所和抬板的一体化叠图审查。"),
        ("市场数据闭环", "营销负责人应补齐9个周边项目的网签、库存、推盘、折扣与月流速。"),
        ("主推切换责任", "方案切换由区域总负责人决策，且必须附闸门证据、峰值资金和回退方案。"),
        ("分期实施闭环", "开发计划应把首开货量、示范区、永临结合、工期和回款联成一条可管理路径。"),
        ("风险闭环总表", "每个风险都必须有责任人、截止日、证据、关闭标准、触发条件与回退路径。"),
    ],
    "CS": [
        ("来源登记总表", "所有页面均引用已登记来源ID，每个ID包含标题、日期、口径、快照和限制。"),
        ("原件与派生链", "16份原件、14个证据家族和32份派生材料均可回溯，但只有家族代表获得独立权重。"),
        ("官方来源等级", "国家统计局、湖北住建与襄阳官方文件作为宏观和规划高信度来源。"),
        ("市场来源等级", "本地楼盘库和商业页只作报价、产品与持续在售线索，不作真实成交。"),
        ("高德快照边界", "高德POI结果绑定路口代理坐标与捕获日，距离与类别需人工抽核。"),
        ("ArchLib使用边界", "L2地产品牌案例只支持设计机制与表达意向，不直接支持襄阳售价或去化。"),
        ("社媒数据状态", "未连接合法社媒采集器，且独立作者不足10人，社媒和现实虚拟人维持missing。"),
        ("方法与代理口径", "10–15㎡面积段仅使用项目覆盖数与持续在售月数代理，不标注库存占比或去化周期。"),
        ("反方证据矩阵", "每项主张保留冲突来源和失效条件，尤其是货值、日照、道路和政策效力。"),
        ("置信度分层", "项目原件、官方统计、市场线索、模型代理和传统解释分层计分，不互相借用信度。"),
        ("决策阻断清单", "红线/标高、道路批复、消防人防、Excel成本错引、融资条件和真实去化仍是主要阻断。"),
        ("证据包冻结状态", "本报告从冻结EvidencePackage离线编译，同包重编必须产生一致报告哈希。"),
    ],
}

# Gold-report presentation layer: one page must add a new decision, evidence
# family or design mechanism. The longer TOPICS registry remains available as
# an evidence drafting pool, while the report uses this director-level cut.
CURATED_TOPIC_TITLES: dict[str, tuple[str, ...]] = {
    "SC1": ("双名项目身份", "输入原件谱系", "冲突事实登记", "使用边界总览"),
    "SC2": ("全国周期位置", "襄阳年度基本面", "新房面积价格", "面积段供需代理", "改善客群门槛", "总价敏感区间", "公交教育覆盖", "市场机会收口"),
    "SC3": ("A/B地块关系", "A地块指标", "B地块指标", "15m道路闸门", "日照口径冲突", "消防闭环要求", "红线标高缺口"),
    "AD1": ("十轮强排收敛", "方案1空间逻辑", "方案2空间逻辑", "方案3空间逻辑", "强排比选结论"),
    "AD2": ("唯一主推方案", "主推核心理由", "切换方案2闸门", "切换方案3闸门"),
    "AD3": ("产品梯级总览", "A地块产品配比", "B地块基盘产品", "B地块进阶产品", "B地块标杆产品", "分期与首开策略"),
    "AD4": ("总图空间骨架", "抬板三张剖面", "下沉会所闭环", "消防登高面", "人车流线分层", "示范区永临结合", "户型与立面联动", "设计任务书收口"),
    "AD5": ("传统空间G0", "禁止吉凶承诺"),
    "VA1": ("设计价值溢价", "一级底线投入", "二级价值投入", "三级表达投入", "成本体验链路"),
    "VA2": ("货值冲突登记", "Excel错引阻断", "去化模型输入", "现金流峰值缺口", "IRR禁止输出", "财务复核门槛"),
    "VA3": ("法定风险闭环", "财务错引闭环", "消防人防闭环", "市场数据闭环", "风险闭环总表"),
    "CS": ("来源登记总表", "原件与派生链", "社媒数据状态", "反方证据矩阵", "决策阻断清单"),
}


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_bytes() != payload:
        path.write_bytes(payload)


def _inbox_paths(project: Path) -> list[str]:
    inbox = project / "inbox"
    return [
        path.relative_to(inbox).as_posix()
        for path in sorted(inbox.rglob("*"))
        if path.is_file()
    ]


def _match_parent(path: str) -> str:
    name = Path(path).name
    if "/extracted_images/" in f"/{path}":
        return next(item for item in ORIGINAL_FAMILIES if item.endswith("第二批建设工程设计方案不合理图集.pdf"))
    if "/extracted_docx_images/" in f"/{path}":
        return next(item for item in ORIGINAL_FAMILIES if item.endswith(" -0629.docx"))
    rules = (
        ("第二批建设工程", "第二批建设工程设计方案不合理图集.pdf"),
        ("市人民政府办公室", "市人民政府办公室关于印发"),
        ("市政府办市人民政府专题", "市政府办市人民政府专题"),
        ("建筑面宽和建筑退界", "建筑面宽和建筑退界"),
        ("居住建筑山墙间距", "居住建筑山墙间距"),
        ("城市森林花园建筑", "城市森林花园建筑"),
        ("建设工程日照分析", "建设工程日照分析"),
        ("容积率计算规则", "容积率计算规则"),
        ("针对抬板征询稿", "针对抬板征询稿补充问题0629.docx"),
        ("中心城区“抬板式住宅”", " -0629.docx"),
    )
    for marker, original_marker in rules:
        if marker in name:
            return next(item for item in ORIGINAL_FAMILIES if original_marker in item)
    raise ValueError(f"no derivative parent rule for {path}")


def _normalization_overrides(project: Path) -> dict[str, Any]:
    paths = _inbox_paths(project)
    originals = [path for path in paths if "/extracted_" not in f"/{path}"]
    if set(originals) != set(ORIGINAL_FAMILIES):
        missing = sorted(set(ORIGINAL_FAMILIES) - set(originals))
        unexpected = sorted(set(originals) - set(ORIGINAL_FAMILIES))
        raise ValueError(f"Xiangyang original input contract drifted; missing={missing}, unexpected={unexpected}")
    derivatives = [path for path in paths if "/extracted_" in f"/{path}"]
    return {
        "schema_version": "dds.normalization-overrides/1.0",
        "derived_from": {f"inbox/{path}": f"inbox/{_match_parent(path)}" for path in derivatives},
        "evidence_family": {f"inbox/{path}": family for path, family in ORIGINAL_FAMILIES.items()},
        "notes": [
            "16 non-extracted originals are frozen into 14 evidence families.",
            "extracted TXT/PNG and report slide images are derivative references only.",
        ],
    }


def _project_manifest_stub(as_of: str) -> dict[str, Any]:
    return {
        "schema_version": "dds.project-manifest/1.0",
        "project_id": PROJECT_ID,
        "name": PROJECT_NAME,
        "aliases": ["星河国际项目", "国投建华望府"],
        "city": "襄阳",
        "district": "樊城区",
        "address": "长虹路以东、春园路以南、建华路以北",
        "site_description": "A/B两地块组成的城市改善住宅项目",
        "coordinate": {
            "wgs84": None,
            "gcj02": [112.135662, 32.050012],
            "precision": "intersection_proxy",
            "limitations": ["仅用于周边POI与公开市场检索，非红线质心。"],
        },
        "status": "concept_scheme_under_review",
        "audience": ["区域总负责人", "投拓负责人", "设计管理负责人", "建筑总监"],
        "as_of": as_of,
    }


def _input_source_entries(as_of: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for path, family in ORIGINAL_FAMILIES.items():
        grouped.setdefault(family, []).append(path)
    claims = {
        "FAM-DESIGN-0710": ["强排方案、户型、货值、日照与表达意向"],
        "FAM-OWNER-BRIEF": ["业主任务、地块范围与产品要求"],
        "FAM-OWNER-QA-20260626": ["80m限高、不原地安置、产品配比与抬板资料状态"],
        "FAM-FINANCE-20260508": ["R8错引、#REF!分布与成本测算边界"],
        "FAM-DECK-DRAFT-0629": ["抬板式住宅征求意见稿"],
        "FAM-DECK-QUESTIONS-0629": ["对抬板征求稿的开放问题"],
        "FAM-PLANNING-MINUTE-58": ["A/B地块容积率、密度、公服、住商比和道路原则意见"],
        "FAM-PLANNING-BASE-2021": ["城市规划管理技术基线"],
        "FAM-FAR-2020-EXPIRED": ["已失效容积率计算规则，仅作版本谱系"],
        "FAM-WIDTH-SETBACK-2025": ["建筑面宽与退界调整"],
        "FAM-GABLE-2025": ["居住建筑山墙间距补充规定"],
        "FAM-4THGEN-2023": ["城市森林花园建筑试点条件"],
        "FAM-SUNLIGHT-2023": ["建设工程日照分析方法与管理边界"],
        "FAM-REVIEW-ATLAS-2024": ["建设工程设计审查反例"],
    }
    result = []
    for family, members in sorted(grouped.items()):
        source_id = FAMILY_SOURCE_IDS[family]
        result.append(
            {
                "source_id": source_id,
                "title": Path(members[0]).name,
                "source_type": "provided_input",
                "trust_tier": "T1_project_input",
                "canonical_ref": f"dds://project/{PROJECT_ID}/evidence-family/{family}",
                "published_at": None,
                "captured_at": f"{as_of}T00:00:00+08:00",
                "snapshot_ref": f"source_snapshots/{source_id}.json",
                "confidence": 0.86,
                "members": [f"inbox/{member}" for member in members],
                "claims": claims[family],
                "limitations": ["项目输入需与审批原件、成本底稿或专项模型复核。"],
            }
        )
    return result


def _external_source_entries(as_of: str) -> list[dict[str, Any]]:
    raw = [
        ("SRC-OFFICIAL-NBS-H1-2026", "2026年上半年全国房地产市场", "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260715_1964126.html", "T1_official", 0.95, ["开发投资-18.0%", "销售面积-11.6%", "销售额-13.6%", "到位资金-20.2%"]),
        ("SRC-OFFICIAL-NBS-PRICE-202606", "2026年6月70城房价分面积指数", "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260715_1964115.html", "T1_official", 0.95, ["襄阳≤90㎡环比99.1", "90–144㎡环比99.9", ">144㎡环比99.2"]),
        ("SRC-OFFICIAL-XY-2024", "2024年襄阳统计公报", "https://tjj.hubei.gov.cn/tjsj/tjgb/ndtjgb/sztjgb/202504/P020250422585645523745.pdf", "T1_official", 0.92, ["房地产投资-8.1%", "商品房销售面积465.6万㎡,+0.3%", "销售额300.5亿元,-3.2%"]),
        ("SRC-OFFICIAL-XY-POLICY-2026", "2026襄阳住房支持政策", "https://zjt.hubei.gov.cn/bmdt/dtyw/szsm/202601/t20260106_5849086.shtml", "T1_official", 0.90, ["公积金最高80万元", "高品质/装配式/现房条件下可上浮30%", "东津专项不外推樊城"]),
        ("SRC-OFFICIAL-XY-PREFAB-2026", "襄阳装配式建造信号", "https://zjt.hubei.gov.cn/bmdt/dtyw/szsm/202605/t20260514_5936341.shtml", "T1_official", 0.84, ["中心城区新建建筑装配式要求，底层正式通知待核"]),
        ("SRC-OFFICIAL-XY-MAYDAY-2025", "2025五一襄阳楼市短窗口", "https://zjt.hubei.gov.cn/bmdt/dtyw/szsm/202505/t20250508_5643209.shtml", "T1_official", 0.78, ["假日销售面积同比+45.6%，不外推全年"]),
        ("SRC-OFFICIAL-XY-QUALITY-2024", "2024年末襄阳高品质住房信号", "https://zjt.hubei.gov.cn/bmdt/dtyw/szsm/202412/t20241231_5484578.shtml", "T1_official", 0.82, ["联投御府310套/超5亿信号", "140㎡+改善客群存在"]),
        ("SRC-OFFICIAL-XY-SUPPORT-2026", "2026襄阳保障性住房", "https://zjt.hubei.gov.cn/bmdt/dtyw/szsm/202601/t20260130_5866830.shtml", "T1_official", 0.88, ["240套,约110㎡,5995元/㎡，非商品住宅竞品"]),
        ("SRC-MARKET-XY-NEWHOUSE", "DDS襄阳本地新房库快照", "dds://dataset/xiangyang-newhouse/20260715", "T2_structured_market", 0.66, ["362条×105字段", "排除目标项目", "库存与真实去化为空"]),
        ("SRC-AMAP-POI", "高德POI路口代理点快照", "dds://amap/poi/112.135662,32.050012/20260715", "T2_gis_proxy", 0.68, ["公交125–301m", "小学315m/中学558m", "医疗715–889m", "商业955–1311m"]),
        ("SRC-COMMERCIAL-MINFA", "民发汉江一品商业列表线索", "https://xiangyang.jiwu.com/loupan/597067.html", "T3_commercial_lead", 0.35, ["当前列表11000元/㎡，与本地库13000元/㎡冲突"]),
        ("SRC-DCBBS-XY-202605", "襄阳房地产市场月报2026年05月", "https://www.dcbbs.com/p-229546.html", "T2_structured_market", 0.82, ["1-5月住宅成交3138套/38.0万㎡", "樊城成交16.13万㎡、库存278.64万㎡、去化96.86个月", "面积段48.2%/30.6%/14.3%/1.3%", "舜山府、联投滨江商务区、盛特一品、汉江序1-5月真实成交与流速"]),
        ("SRC-ARCHLIB-TIANYI", "ArchLib·杭州天奕", "archlib://case/e417", "L2_design_case", 0.72, ["抬板与下沉会所空间机制，不证明襄阳售价"]),
        ("SRC-ARCHLIB-TIANCHEN", "ArchLib·成都天宸上院", "archlib://case/3d472", "L2_design_case", 0.72, ["庭院、归家与界面层次"]),
        ("SRC-ARCHLIB-SHIZHOULI", "ArchLib·杭州时舟里", "archlib://case/df05", "L2_design_case", 0.70, ["小尺度社区节点与材料克制"]),
        ("SRC-DDS-METHOD", "DDS金样本方法与口径", "dds://method/xiangyang-gold/1.0", "T1_method", 0.94, ["SC/AD/VA/CS", "冻结证据包", "代理口径不冒充实测"]),
    ]
    return [
        {
            "source_id": source_id,
            "title": title,
            "canonical_url": url,
            "source_type": "official_web" if tier.startswith("T1_official") else "structured_snapshot",
            "trust_tier": tier,
            "published_at": None,
            "captured_at": f"{as_of}T00:00:00+08:00",
            "snapshot_ref": f"source_snapshots/{source_id}.json",
            "confidence": confidence,
            "claims": claims,
            "limitations": ["仅在登记口径与数据截止日内使用。"],
        }
        for source_id, title, url, tier, confidence, claims in raw
    ]


def _source_snapshot_payload(source: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    return {
        "schema_version": "dds.curated-evidence-snapshot/1.0",
        "source_id": source["source_id"],
        "title": source["title"],
        "canonical_ref": source.get("canonical_ref") or source.get("canonical_url"),
        "published_at": source.get("published_at"),
        "as_of": as_of,
        "status": source.get("status"),
        "trust_tier": source.get("trust_tier"),
        "confidence": source.get("confidence"),
        "claims": deepcopy(source.get("claims") or []),
        "limitations": deepcopy(source.get("limitations") or []),
    }


def _complete_source_metadata(source: Mapping[str, Any], as_of: str) -> dict[str, Any]:
    result = deepcopy(dict(source))
    result["published_at"] = result.get("published_at") or KNOWN_SOURCE_DATES.get(
        str(result.get("source_id") or "")
    )
    result["canonical_ref"] = str(
        result.get("canonical_ref") or result.get("canonical_url") or ""
    )
    result["status"] = str(
        result.get("status")
        or (
            "partial"
            if str(result.get("trust_tier") or "").startswith(("T2_", "T3_", "L2_"))
            else "ready"
        )
    )
    content_hash = _stable_hash(_source_snapshot_payload(result, as_of))
    result["content_hash"] = content_hash
    result["snapshot_hash"] = content_hash
    return result


def _source_registry(as_of: str) -> list[dict[str, Any]]:
    return [
        _complete_source_metadata(source, as_of)
        for source in _input_source_entries(as_of) + _external_source_entries(as_of)
    ]


def _write_source_snapshots(work: Path, sources: Sequence[Mapping[str, Any]], as_of: str) -> None:
    for source in sources:
        snapshot = _source_snapshot_payload(source, as_of)
        snapshot["snapshot_hash"] = _stable_hash(snapshot)
        _write_json(work / str(source["snapshot_ref"]), snapshot)


def _assets(project: Path) -> list[dict[str, Any]]:
    root = project.resolve().parents[0]
    # Renderer resolves locators against the DDS repository; keep only repo-relative paths.
    return [
        {
            "asset_id": f"ASSET-XY-SLIDE-{page:03d}",
            "kind": "image",
            "mime": "image/png",
            "data_or_object_ref": f"Test_襄阳/reports/slides_png/page_{page:03d}.png",
            "rights_status": "caller_authorized_derivative",
            "source_ref": "SRC-XY-DESIGN",
            "derivative_only": True,
            "independent_evidence_weight": 0,
        }
        for page in SLIDE_PAGES
        if (root / "Test_襄阳" / "reports" / "slides_png" / f"page_{page:03d}.png").is_file()
    ]


DATASETS: dict[str, list[dict[str, Any]]] = {
    "SC1": [
        {"label": "原件", "value": 16, "note": "非 extracted"},
        {"label": "证据家族", "value": 14, "note": "独立计权"},
        {"label": "派生材料", "value": 32, "note": "权重0"},
        {"label": "坐标代理点", "value": 1, "note": "intersection_proxy"},
    ],
    "SC2": [
        {"label": "开发投资", "value": -18.0, "note": "2026H1同比%"},
        {"label": "销售面积", "value": -11.6, "note": "2026H1同比%"},
        {"label": "销售额", "value": -13.6, "note": "2026H1同比%"},
        {"label": "到位资金", "value": -20.2, "note": "2026H1同比%"},
    ],
    "SC3": [
        {"label": "A原容积率", "value": 3.3, "note": "会前口径"},
        {"label": "A调整容积率", "value": 2.8, "note": "58期纪要"},
        {"label": "B原容积率", "value": 3.3, "note": "会前口径"},
        {"label": "B调整容积率", "value": 2.5, "note": "58期纪要"},
        {"label": "设计道路", "value": 15, "note": "m，批复前基线"},
    ],
    "AD1": [
        {"label": "方案1", "value": 17.55, "note": "亿元，设计测算"},
        {"label": "方案2", "value": 17.66, "note": "亿元，条件方案"},
        {"label": "方案3", "value": 17.78, "note": "亿元，进取方案"},
    ],
    "AD2": [
        {"label": "方案1当前资格", "value": 1, "note": "唯一主推"},
        {"label": "方案2政策闭合", "value": 0, "note": "blocked"},
        {"label": "方案2成本闭合", "value": 0, "note": "blocked"},
        {"label": "方案3峰值资金", "value": 0, "note": "blocked"},
    ],
    "AD3": [
        {"label": "B·118+130㎡", "value": 60, "note": "计划占比%"},
        {"label": "B·145+168㎡", "value": 35, "note": "计划占比%"},
        {"label": "B·218㎡", "value": 5, "note": "计划占比%"},
    ],
    "AD4": [
        {"label": "工程底线", "value": 100, "note": "优先级"},
        {"label": "高频体验", "value": 85, "note": "优先级"},
        {"label": "示范展示", "value": 65, "note": "优先级"},
        {"label": "表达装置", "value": 35, "note": "优先级"},
    ],
    "AD5": [
        {"label": "红线", "value": 0, "note": "missing"},
        {"label": "真北", "value": 0, "note": "missing"},
        {"label": "标高DEM", "value": 0, "note": "missing"},
        {"label": "实际入口", "value": 0, "note": "missing"},
    ],
    "VA1": [
        {"label": "一级底线", "value": 100, "note": "必投"},
        {"label": "二级价值", "value": 80, "note": "核心投入"},
        {"label": "三级表达", "value": 35, "note": "有条件投入"},
    ],
    "VA2": [
        {"label": "汇总表", "value": 17, "note": "#REF!"},
        {"label": "A3.3表", "value": 21, "note": "#REF!"},
        {"label": "合并表1", "value": 2, "note": "#REF!"},
        {"label": "合并表2", "value": 1, "note": "#REF!"},
    ],
    "VA3": [
        {"label": "审批政策", "value": 5, "note": "高"},
        {"label": "成本财务", "value": 5, "note": "高"},
        {"label": "消防人防", "value": 4, "note": "高"},
        {"label": "市场去化", "value": 4, "note": "高"},
        {"label": "分期实施", "value": 3, "note": "中"},
    ],
    "CS": [
        {"label": "项目原件家族", "value": 14, "note": "T1项目输入"},
        {"label": "官方公开来源", "value": 8, "note": "T1"},
        {"label": "市场/GIS快照", "value": 3, "note": "T2/T3"},
        {"label": "ArchLib设计案例", "value": 3, "note": "L2"},
        {"label": "DDS方法", "value": 1, "note": "T1"},
    ],
}


SOURCE_ROTATION = {
    "SC1": ["SRC-XY-DESIGN", "SRC-XY-OWNER-BRIEF", "SRC-DDS-METHOD"],
    "SC2": ["SRC-OFFICIAL-NBS-H1-2026", "SRC-OFFICIAL-NBS-PRICE-202606", "SRC-OFFICIAL-XY-2024", "SRC-MARKET-XY-NEWHOUSE", "SRC-AMAP-POI", "SRC-OFFICIAL-XY-POLICY-2026", "SRC-OFFICIAL-XY-QUALITY-2024", "SRC-COMMERCIAL-MINFA"],
    "SC3": ["SRC-XY-PLANNING-MINUTE", "SRC-XY-OWNER-QA", "SRC-XY-DESIGN", "SRC-XY-PLANNING-BASE", "SRC-XY-4THGEN", "SRC-XY-SUNLIGHT"],
    "AD1": ["SRC-XY-DESIGN", "SRC-XY-OWNER-BRIEF", "SRC-XY-FINANCE"],
    "AD2": ["SRC-XY-DESIGN", "SRC-XY-FINANCE", "SRC-XY-4THGEN", "SRC-XY-PLANNING-MINUTE"],
    "AD3": ["SRC-XY-OWNER-QA", "SRC-XY-DESIGN", "SRC-MARKET-XY-NEWHOUSE"],
    "AD4": ["SRC-XY-DESIGN", "SRC-XY-OWNER-BRIEF", "SRC-ARCHLIB-TIANYI", "SRC-ARCHLIB-TIANCHEN", "SRC-ARCHLIB-SHIZHOULI", "SRC-AMAP-POI"],
    "AD5": ["SRC-DDS-METHOD", "SRC-XY-DESIGN", "SRC-AMAP-POI"],
    "VA1": ["SRC-XY-DESIGN", "SRC-XY-FINANCE", "SRC-MARKET-XY-NEWHOUSE", "SRC-ARCHLIB-TIANYI"],
    "VA2": ["SRC-XY-FINANCE", "SRC-XY-DESIGN", "SRC-MARKET-XY-NEWHOUSE", "SRC-OFFICIAL-NBS-H1-2026"],
    "VA3": ["SRC-XY-PLANNING-MINUTE", "SRC-XY-FINANCE", "SRC-XY-4THGEN", "SRC-MARKET-XY-NEWHOUSE"],
    "CS": ["SRC-DDS-METHOD", "SRC-XY-DESIGN", "SRC-OFFICIAL-NBS-H1-2026", "SRC-MARKET-XY-NEWHOUSE", "SRC-AMAP-POI"],
}


CHAPTER_ROTATION = {
    "SC1": ["decision"],
    "SC2": ["macro", "market", "competitor_series", "social_intelligence", "persona_evidence", "synthetic_personas"],
    "SC3": ["site"],
    "AD1": ["decision", "site"],
    "AD2": ["decision", "risk"],
    "AD3": ["product"],
    "AD4": ["site", "product"],
    "AD5": ["traditional_spatial_culture"],
    "VA1": ["premium_analysis"],
    "VA2": ["absorption_forecast", "investment_case", "finance"],
    "VA3": ["risk"],
    "CS": ["decision", "risk"],
}


SPECIAL_UNIT_CONTRACTS: dict[str, tuple[str, dict[str, Any]]] = {
    "AD1": (
        "concept_options",
        {"options_count": 3, "comparison_basis": ["容量", "产品", "工程", "成本", "运营"]},
    ),
    "AD2": (
        "recommended_scheme",
        {
            "recommendation": "方案1.1·均衡兑现",
            "rejected_options_count": 2,
            "decision_gate": "方案1.2/2必须完成政策、专项、成本、流速与峰值资金闸门。",
            "fallback": "任一闸门失败即回退方案1.1骨架。",
        },
    ),
    "AD4": (
        "architecture_design",
        {"masterplan": True, "unit_plan": True, "expression_count": 4},
    ),
    "VA1": (
        "design_value_premium",
        {
            "upstream_refs": ["SC2", "AD2", "AD3", "AD4"],
            "design_actions": ["一级底线", "二级价值投入", "三级表达投入"],
            "value_mechanisms": ["体验", "竞争力", "流速", "长期价值"],
            "investment": "成本位置、回退性与价值机制同页登记。",
        },
    ),
    "CS": (
        "confidence_state",
        {"sources": True, "methods": True, "assumptions": True, "confidence": True},
    ),
}


FRAMEWORK_GAPS: dict[str, dict[str, Any]] = {
    "SC2": {
        "title": "市场补证闸门",
        "status": "partial",
        "statement": "已有樊城成交、库存、面积段及四盘流速，可支持条件性主推；仍缺目标项目来访、认筹、净签和有效折扣回测。",
        "action": "营销负责人以首开90天真实漏斗校准48个月基准情景，并逐批验证175/225㎡。",
        "source_refs": ["SRC-DCBBS-XY-202605", "SRC-MARKET-XY-NEWHOUSE", "SRC-DDS-METHOD"],
    },
    "SC3": {
        "title": "场地资料闸门",
        "status": "partial",
        "statement": "缺法定红线、测量坐标、标高、管线和实际入口，空间判断不得提级。",
        "action": "规划与设计负责人取得盖章红线、测量成果、管线和入口书面条件。",
        "source_refs": ["SRC-XY-PLANNING-MINUTE", "SRC-XY-DESIGN"],
    },
    "AD5": {
        "title": "传统空间输入闸门",
        "status": "blocked",
        "statement": "当前仅为G0，缺红线、真北、标高、入口与地形，禁止判断吉凶。",
        "action": "取得G1以上空间输入后再做形势筛查，传统解释仍不得进入财务公式。",
        "source_refs": ["SRC-DDS-METHOD", "SRC-AMAP-POI"],
    },
    "VA2": {
        "title": "财务模型解锁闸门",
        "status": "blocked",
        "statement": "去化已形成66/48/42个月三情景；但货值冲突、R8错引、#REF!与融资条件仍阻断确定性IRR。",
        "action": "成本与财务负责人重建依赖链，以三情景去化重算回款、峰值资金和方案切换边界。",
        "source_refs": ["SRC-XY-FINANCE", "SRC-DCBBS-XY-202605"],
    },
    "VA3": {
        "title": "风险责任闭环闸门",
        "status": "partial",
        "statement": "风险已识别，但责任人签认、截止日、关闭证据和回退触发仍未全部登记。",
        "action": "区域总负责人组织规划、成本、营销、设计与工程完成具名风险销项表。",
        "source_refs": ["SRC-XY-PLANNING-MINUTE", "SRC-XY-FINANCE"],
    },
    "CS": {
        "title": "证据状态闸门",
        "status": "partial",
        "statement": "真实社媒、行为锚点、审批复核与财务复核未齐，证据状态不得升级。",
        "action": "按来源、反证、时间和责任人关闭缺口，并保留partial/blocked审计轨迹。",
        "source_refs": ["SRC-DDS-METHOD", "SRC-XY-DESIGN"],
    },
}


def _legacy_page_manifest(assets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    asset_ids = [str(item["asset_id"]) for item in assets]
    asset_cursor = 0
    for unit, topic_pool in TOPICS.items():
        selected = set(CURATED_TOPIC_TITLES[unit])
        topics = [topic for topic in topic_pool if topic[0] in selected]
        refs = SOURCE_ROTATION[unit]
        chapters = CHAPTER_ROTATION[unit]
        for index, (title, takeaway) in enumerate(topics, start=1):
            source_refs = [refs[(index - 1) % len(refs)], refs[index % len(refs)]]
            source_refs = list(dict.fromkeys(source_refs))
            chart_series = deepcopy(DATASETS[unit])
            chart_type = "ranked_bar"
            if unit in {"SC2", "VA3"}:
                chart_type = "diverging_bar" if unit == "SC2" else "heatmap"
            elif unit == "AD3":
                chart_type = "stacked_bar"
            elif unit == "VA2":
                chart_type = "tornado"
            elif unit == "AD1":
                chart_type = "ranked_bar"
            layout = "spatial_diagram" if unit in {"SC3", "AD4", "AD5"} and index % 2 else "content"
            page_assets: list[str] = []
            if unit in {"AD1", "AD2", "AD3", "AD4"} and asset_cursor < len(asset_ids):
                page_assets = [asset_ids[asset_cursor]]
                asset_cursor += 1
            page_id = f"{unit.lower()}-{index:02d}-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}"
            claim_id = f"CLM-{unit}-{index:02d}"
            decision_question = f"本页的「{title}」如何改变主推或下一步任务？"
            visual_hash = _stable_hash(
                {"unit": unit, "title": title, "question": decision_question, "series": chart_series}
            )
            blocks: list[dict[str, Any]] = [
                {
                    "type": "narrative",
                    "title": "证据→判断→动作",
                    "text": takeaway,
                    "source_refs": source_refs,
                }
            ]
            if layout == "spatial_diagram":
                blocks.append(
                    {
                        "type": "spatial_diagram",
                        "title": title,
                        "diagram_kind": "SPATIAL DECISION FIELD",
                        "layers": [
                            {"label": "法定/安全", "level": "01", "requirements": "先关闭审批与生命安全底线"},
                            {"label": "物理/工程", "level": "02", "requirements": "再核验尺寸、标高、结构和专项"},
                            {"label": "产品/体验", "level": "03", "requirements": "最后分配客户价值与表达资源"},
                        ],
                        "callouts": [
                            {"label": "输入", "text": "仅使用已登记来源"},
                            {"label": "缺口", "text": "未测量或未审批则保持 blocked"},
                            {"label": "交付", "text": "输出责任人、图纸与验收条件"},
                        ],
                        "source_refs": source_refs,
                    }
                )
            status = "ready"
            if unit in {"AD5", "VA2"}:
                status = "blocked"
            elif unit in {"SC2", "SC3", "VA3", "CS"}:
                status = "partial"
            evidence_type = "observed_fact" if unit.startswith("SC") or unit == "CS" else "analysis_inference"
            if unit == "AD5":
                evidence_type = "traditional_interpretation"
            special_role = ""
            special_contract: dict[str, Any] = {}
            if index == 1 and unit in {"SC2", "SC3", "VA3"}:
                # These units contain verified anchor facts plus a separate gap page;
                # the unit is therefore partial, not wholly missing.
                status = "ready"
            if index == 1 and unit in SPECIAL_UNIT_CONTRACTS:
                special_role, special_contract = deepcopy(SPECIAL_UNIT_CONTRACTS[unit])
                status = "ready"
            chart_specs = []
            if index == 1:
                chart_specs = [
                    {
                        "chart_id": f"CHART-{unit}-{index:02d}",
                        "type": chart_type,
                        "title": title,
                        "description": "数值仅按图表注明口径使用，不把代理值冒充实测结果。",
                        "unit": "登记口径",
                        "series": chart_series,
                        "source_refs": source_refs,
                        "evidence_status": "registered",
                    }
                ]
            page = {
                "page_id": page_id,
                "chapter_id": chapters[(index - 1) % len(chapters)],
                "section_id": unit,
                "layout": layout,
                "title": title,
                "display_title": title[:18],
                "decision_question": decision_question,
                "takeaway": takeaway,
                "recommendation": takeaway,
                "claim_ids": [claim_id],
                "observed_evidence_refs": source_refs,
                "counter_evidence_refs": source_refs[-1:],
                "blocks": blocks,
                "chart_specs": chart_specs,
                "asset_refs": page_assets,
                "source_refs": source_refs,
                "confidence": {"score": 0.82 if status == "ready" else 0.62, "level": "high" if status == "ready" else "medium"},
                "evidence_type": evidence_type,
                "decision_eligibility": status == "ready",
                "unit_status": status,
                "unit_role": special_role,
                "unit_contract": special_contract,
                "decision_gate": "仅在本页缺口闭合后才允许改变主推或投资结论。",
                "owner": "区域总负责人/专业责任人",
                "visual_dataset_hash": visual_hash,
                "visual_evidence": "spatial_diagram" if layout == "spatial_diagram" else "chart",
                "story_role": "evidence_appendix" if unit == "CS" else "primary_narrative",
                "appendix_policy": "evidence" if unit == "CS" else "presentation",
                "load_priority": "high" if index <= 2 else "normal",
                "print_policy": {"include": True, "page_break_after": True, "allow_internal_scroll": False},
            }
            pages.append(page)
        gap = FRAMEWORK_GAPS.get(unit)
        if gap:
            index = len(topics) + 1
            title = str(gap["title"])
            source_refs = list(gap["source_refs"])
            status = str(gap["status"])
            statement = str(gap["statement"])
            action = str(gap["action"])
            page_id = f"{unit.lower()}-gap-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}"
            decision_question = f"关闭哪些证据缺口后才能让{unit}进入下一决策级？"
            pages.append(
                {
                    "page_id": page_id,
                    "chapter_id": "framework_gap",
                    "section_id": unit,
                    "layout": "gap",
                    "title": title,
                    "display_title": title,
                    "decision_question": decision_question,
                    "takeaway": f"在“{statement}”闭合前，{unit}保持{status}，不得升级为确定性结论。",
                    "recommendation": action,
                    "claim_ids": [f"CLM-{unit}-GAP"],
                    "observed_evidence_refs": source_refs,
                    "counter_evidence_refs": source_refs[-1:],
                    "blocks": [
                        {
                            "type": "gap",
                            "title": "显式证据缺口",
                            "text": statement,
                            "recommended_action": action,
                            "source_refs": source_refs,
                        }
                    ],
                    "chart_specs": [],
                    "asset_refs": [],
                    "source_refs": source_refs,
                    "confidence": {"score": 0.46 if status == "blocked" else 0.58, "level": "medium"},
                    "evidence_type": "traditional_interpretation" if unit == "AD5" else "analysis_inference",
                    "decision_eligibility": False,
                    "unit_status": status,
                    "unit_role": "",
                    "unit_contract": {},
                    "decision_gate": action,
                    "owner": "区域总负责人/专业责任人",
                    "visual_dataset_hash": _stable_hash(
                        {"unit": unit, "gap": statement, "action": action}
                    ),
                    "visual_evidence": "gap_matrix",
                    "story_role": "evidence_appendix" if unit == "CS" else "primary_narrative",
                    "appendix_policy": "evidence" if unit == "CS" else "presentation",
                    "load_priority": "normal",
                    "print_policy": {"include": True, "page_break_after": True, "allow_internal_scroll": False},
                }
            )
    return pages


def _decision_page_specs() -> list[dict[str, Any]]:
    """Return the 0710-aligned director narrative, not a generic topic deck."""

    def page(
        unit: str,
        title: str,
        question: str,
        takeaway: str,
        *,
        refs: Sequence[str],
        owner: str,
        asset: int | None = None,
        detail: str = "",
        action: str = "",
        status: str = "ready",
        chart_type: str | None = None,
        series: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        return {
            "unit": unit,
            "title": title,
            "question": question,
            "takeaway": takeaway,
            "refs": list(refs),
            "owner": owner,
            "asset": asset,
            "detail": detail,
            "action": action or takeaway,
            "status": status,
            "chart_type": chart_type,
            "series": [dict(item) for item in series],
        }

    market_refs = ["SRC-DCBBS-XY-202605", "SRC-MARKET-XY-NEWHOUSE"]
    design_refs = ["SRC-XY-DESIGN", "SRC-XY-OWNER-BRIEF"]
    return [
        page("SC1", "项目与设计阶段", "这是一份什么阶段、解决什么问题的报告？", "项目已进入概念方案比选与深化，不再按前期空白项目处理。", refs=design_refs, owner="设计管理负责人", asset=1, detail="0710文本已包含十案比选、三案收敛、总图专项、户型、立面和示范区。"),
        page("SC1", "原设计与DDS复核", "原设计结论与DDS复核结论如何分层？", "原设计保留1.1、1.2、2三案；DDS基于市场、工程和资金证据条件性主推1.1。", refs=["SRC-XY-DESIGN", "SRC-DDS-METHOD"], owner="区域总负责人", detail="不把设计团队的候选方案写成审批结论，也不把DDS复核冒充原设计结论。"),

        page("SC2", "樊城市场容量", "樊城市场能否承接1034套改善货量？", "樊城成交韧性优于全市，但96.86个月库存周期决定项目必须分批供货。", refs=market_refs, owner="营销负责人", status="partial", chart_type="diverging_bar", series=[{"label":"樊城成交面积","value":24.3,"note":"同比%"},{"label":"全市成交面积","value":-23.4,"note":"同比%"},{"label":"樊城新增供应","value":-64.4,"note":"同比%"}], detail="2026年1-5月樊城成交16.13万㎡、库存278.64万㎡、去化周期96.86个月。"),
        page("SC2", "面积段真实需求", "哪些面积承担流速，哪些面积形成尾货？", "121/132㎡匹配主流需求；175/225㎡必须从首开主力中剥离。", refs=market_refs, owner="产品负责人", status="partial", chart_type="stacked_bar", series=[{"label":"90-120㎡","value":48.2,"note":"成交占比%"},{"label":"120-140㎡","value":30.6,"note":"成交占比%"},{"label":"140-180㎡","value":14.3,"note":"成交占比%"},{"label":"180-240㎡","value":1.3,"note":"成交占比%"}], detail="2026年5月全市面积成交结构显示，大户型市场容量呈断崖式收窄。"),
        page("SC2", "竞品真实流速", "项目合理的月均流速应落在哪个区间？", "四个樊城改善项目平均20.95套/月，支持48个月基准情景，不支持36个月承诺。", refs=market_refs, owner="营销负责人", status="partial", chart_type="ranked_bar", series=[{"label":"盛特一品","value":24.8,"note":"套/月"},{"label":"联投滨江","value":23.2,"note":"套/月"},{"label":"舜山府","value":21.8,"note":"套/月"},{"label":"汉江序","value":14.0,"note":"套/月"}], detail="竞品1-5月成交419套，均值20.95套/月，中位数21.5套/月。"),
        page("SC2", "市场对方案的判决", "市场证据最终支持哪一个方案方向？", "方案1.1最接近当前成交结构，但套均总价仍高于竞品，必须以小批次验证改善货。", refs=["SRC-DCBBS-XY-202605", "SRC-XY-DESIGN"], owner="区域总负责人", status="partial", chart_type="ranked_bar", series=[{"label":"方案1.1超150万货量","value":61.1,"note":"占比%"},{"label":"市场超150万成交","value":12.5,"note":"占比%"}], detail="方案隐含均价约12,115元/㎡，仅较四盘加权均价高3.45%；风险集中在总价而非单价。"),

        page("SC3", "四个场地问题", "0710方案必须真正解决哪四个地块问题？", "A地块尺度、15m支路割裂、建华路界面和城市可见性，是所有设计动作的唯一上游命题。", refs=design_refs, owner="建筑总监", asset=9, detail="任何抬板、连桥、会所或立面动作都必须回到这四个问题验证。"),
        page("SC3", "法定指标边界", "方案比选必须锁定哪些同口径指标？", "两地块按2.8/2.5容积率、25%密度、80m限高和15m道路基线统一比较。", refs=["SRC-XY-PLANNING-MINUTE", "SRC-XY-DESIGN"], owner="规划负责人", asset=6, detail="A住宅44,167.21㎡、B住宅100,723㎡；早期页与深化页冲突单独登记。"),
        page("SC3", "跨路抬板闸门", "跨15m道路缝合是可实施方案还是设计意向？", "跨路缝合可保留为核心价值手段，但正式批复前不得计入确定货值或工期。", refs=["SRC-XY-PLANNING-MINUTE", "SRC-XY-DECK-DRAFT", "SRC-XY-DECK-QUESTIONS"], owner="报建负责人", status="partial", detail="道路净空、产权、消防、结构、防水、维护和建设责任必须取得具名书面意见。"),

        page("AD1", "十案收敛", "十轮强排真正筛出了什么？", "十案不是十个并列答案，而是筛出原方案一、二、七三条有价值的结构路径。", refs=["SRC-XY-DESIGN"], owner="建筑总监", asset=60, detail="原文本第60页勾选方案一、二、七，并明确方案一、二为综合最优解。"),
        page("AD1", "三案同边界比选", "1.1、1.2、2三案的核心交换关系是什么？", "1.1均衡兑现、1.2四代最大化、2货值上限；三者必须同时接受去化、审批和成本检验。", refs=["SRC-XY-DESIGN", "SRC-XY-FINANCE"], owner="区域总负责人", asset=61, detail="模型货值只用于同假设比较，不等于确认收入。"),
        page("AD1", "原设计结论", "原设计团队究竟推荐了什么？", "原设计将1.1、1.2、2留在最终比选，并只对1.1完成系统深化。", refs=["SRC-XY-DESIGN"], owner="设计管理负责人", asset=51, detail="深化程度证明1.1成熟度最高，但不等于已获审批或唯一决策。"),
        page("AD1", "DDS主推判断", "为什么当前只投入方案1.1继续深化？", "1.1以66%的121/132㎡守住流速，同时保留改善价值，回退成本最低。", refs=["SRC-XY-DESIGN", "SRC-DCBBS-XY-202605", "SRC-XY-FINANCE"], owner="区域总负责人", asset=37, detail="1.2等待四代政策与成本闭合；方案2等待大户型去化与峰值资金验证。"),

        page("AD2", "方案1.1深化基准", "下一轮专项校核以哪张总图为唯一底图？", "以方案1.1深化总图为消防、日照、地库、景观、分期和成本复核的共同基准。", refs=["SRC-XY-DESIGN"], owner="设计管理负责人", asset=65, detail="A/B合计1034套、住宅计容144,890.21㎡；模型面积仍需勾稽104.21㎡差异。"),
        page("AD2", "切换与回退", "什么证据出现时才允许切换方案？", "1.2只因四代价值闭合切换；方案2只因资金与大户型实销闭合切换。", refs=["SRC-XY-DESIGN", "SRC-DCBBS-XY-202605", "SRC-XY-FINANCE"], owner="区域总负责人", detail="任一闸门失败，回退到1.1骨架并停止新增高投入表达。"),

        page("AD3", "产品梯级与货量", "计算面积与定位档位如何避免混用？", "强排按121/132/148/175/225㎡核算，118/130/145/168/218㎡只作为定位标签。", refs=["SRC-XY-DESIGN"], owner="产品负责人", chart_type="stacked_bar", series=[{"label":"121㎡","value":402,"note":"套"},{"label":"132㎡","value":280,"note":"套"},{"label":"148㎡","value":208,"note":"套"},{"label":"175㎡","value":80,"note":"套"},{"label":"225㎡","value":64,"note":"套"}], detail="两套面积体系不得在货量、总价或去化公式中混算。"),
        page("AD3", "普宅流量底盘", "121/132㎡怎样承担现金流而不牺牲改善感？", "用高效核心筒、面宽、收纳和独梯体验提升121/132㎡，而不是继续扩大面积。", refs=["SRC-XY-DESIGN", "SRC-DCBBS-XY-202605"], owner="户型负责人", asset=102, detail="该面积合计682套，是首开和持续推盘的流量底盘。"),
        page("AD3", "四代改善产品", "148/175/225㎡怎样证明溢价而不是放大尾货？", "四代产品必须把赠送、景观和场景兑现为可感知价值，并接受小批量实销验证。", refs=["SRC-XY-DESIGN", "SRC-XY-4THGEN", "SRC-DCBBS-XY-202605"], owner="产品负责人", asset=104, status="partial", detail="225㎡每批8-12套，客户池达到批次2倍后才允许释放。"),
        page("AD3", "首开与动态解锁", "首开怎样兼顾展示、流速与后续价格梯度？", "首批120-140套以121/132㎡为主，175/225㎡不进入主销货盘。", refs=["SRC-DCBBS-XY-202605", "SRC-XY-DESIGN"], owner="营销负责人", asset=107, status="partial", detail="连续两月≥25套/月且175㎡首批90天去化≥60%，才验证225㎡。"),

        page("AD4", "缝合城市母题", "所有空间动作是否共同服务一个设计母题？", "把两块地缝成一座公园，是项目定位、总图、产品和体验的共同母题。", refs=["SRC-XY-DESIGN"], owner="建筑总监", asset=22),
        page("AD4", "跨路缝合", "跨路连接如何补偿A地块尺度并形成整体社区？", "连桥与抬板应优先解决共享配套和空间连续，不能退化为高成本造型。", refs=["SRC-XY-DESIGN", "SRC-XY-PLANNING-MINUTE"], owner="建筑总监", asset=24, status="partial"),
        page("AD4", "240米城市界面", "建华路零散界面怎样转化为项目识别？", "150+75+15m界面通过入口、商业和檐下空间统一，而非依赖材料堆砌。", refs=["SRC-XY-DESIGN"], owner="立面负责人", asset=25),
        page("AD4", "空间生成", "抬板、下沉庭院和空中花园的生成逻辑是否连续？", "先确定双地块核心轴，再布置庭院与大堂，最后用抬板连接二层公共花园。", refs=["SRC-XY-DESIGN"], owner="建筑总监", asset=82),
        page("AD4", "人车流线", "人车分层是否与实际入口、地库和归家节点闭合？", "人行完整归家、车行快速入库，但必须用真实红线、标高和入口重新复核。", refs=["SRC-XY-DESIGN"], owner="交通专项负责人", asset=70, status="partial"),
        page("AD4", "消防专项", "抬板与连桥是否保留连续消防救援条件？", "第71页提供方案意图；登高面、净空、承载和疏散仍须专项签认。", refs=["SRC-XY-DESIGN", "SRC-XY-PLANNING-BASE"], owner="消防专项负责人", asset=71, status="partial"),
        page("AD4", "日照专项", "方案1.1的日照结果能否进入报批承诺？", "现模型称满足规定并影响界外231户，但必须以审批版模型与实户表复核。", refs=["SRC-XY-DESIGN", "SRC-XY-SUNLIGHT"], owner="日照专项负责人", asset=74, status="partial"),
        page("AD4", "下沉会所体验", "下沉会所怎样从效果图变成可运营空间？", "会所保留为项目体验核心，但采光、防水、疏散、运营和永临结合必须同页闭合。", refs=["SRC-XY-DESIGN", "SRC-ARCHLIB-TIANYI"], owner="会所专项负责人", asset=95, status="partial"),

        page("AD5", "传统空间文化G0", "当前输入允许得出哪些传统空间判断？", "只有路口代理点时不判断吉凶；物理高差、风、日照和水只在实测后进入设计。", refs=["SRC-DDS-METHOD", "SRC-AMAP-POI"], owner="建筑总监", status="blocked", detail="传统解释不得进入售价、去化、ROI或IRR。"),

        page("VA1", "立面策略", "立面表达怎样服务四代产品而不脱离成本？", "暖调公建感与条纹IP可保留，但必须落到模数、节点、耐久和材料分区。", refs=["SRC-XY-DESIGN", "SRC-XY-FINANCE"], owner="立面负责人", asset=150),
        page("VA1", "超级城市入口", "入口投入怎样同时获得品牌识别和归家价值？", "入口是240m界面的价值峰值，应优先保证尺度、遮蔽、落客和夜间识别。", refs=["SRC-XY-DESIGN"], owner="示范区负责人", asset=162),
        page("VA1", "设计价值投入", "资金应优先投在哪些可感知且可回退的动作？", "一级守安全交付，二级投高频体验，三级表达只做小成本强感知。", refs=["SRC-XY-DESIGN", "SRC-XY-FINANCE", "SRC-DCBBS-XY-202605"], owner="成本与设计负责人", chart_type="ranked_bar", series=[{"label":"一级底线","value":100,"note":"法规/消防/防水"},{"label":"二级价值","value":80,"note":"户型/归家/庭院"},{"label":"三级表达","value":35,"note":"立面/IP/装置"}], detail="每一笔投入必须绑定客户感知、竞争差异、去化路径和失败回退。"),

        page("VA2", "三情景去化", "方案1.1合理的清盘周期是多少？", "基准48个月、保守66个月、乐观42个月；36个月只能作为压力项。", refs=["SRC-DCBBS-XY-202605", "SRC-XY-FINANCE", "SRC-XY-DESIGN"], owner="营销负责人", status="partial", chart_type="ranked_bar", series=[{"label":"保守","value":15.67,"note":"套/月·66月"},{"label":"基准","value":21.54,"note":"套/月·48月"},{"label":"乐观","value":24.62,"note":"套/月·42月"}], detail="基准同时由旧测算48个月和四盘均值20.95套/月交叉支撑。"),
        page("VA2", "面积段去化任务", "基准情景下每个面积段每月要卖多少？", "121/132㎡承担14.21套/月；225㎡1.33套/月仍可能占据细分市场过高份额。", refs=["SRC-DCBBS-XY-202605", "SRC-XY-DESIGN"], owner="营销负责人", status="partial", chart_type="ranked_bar", series=[{"label":"121㎡","value":8.38,"note":"套/月"},{"label":"132㎡","value":5.83,"note":"套/月"},{"label":"148㎡","value":4.33,"note":"套/月"},{"label":"175㎡","value":1.67,"note":"套/月"},{"label":"225㎡","value":1.33,"note":"套/月"}], detail="大户型按认筹、成交和折扣三项闸门逐批解锁。"),
        page("VA2", "首开与加推节奏", "如何避免一次性形成显性库存？", "首批控制在基准流速5.6-6.5个月货量，后续每批只覆盖2.5-3.5个月实销。", refs=["SRC-DCBBS-XY-202605", "SRC-XY-DESIGN"], owner="营销负责人", status="partial", detail="低于16套/月或有效折扣超过5%时，停止175/225㎡新增供应。"),
        page("VA2", "货值与财务边界", "17.55亿元能否直接进入确定性IRR？", "货值、104.21㎡差异、1148/1034套冲突和Excel断链未统一前，不输出确定性IRR。", refs=["SRC-XY-DESIGN", "SRC-XY-FINANCE"], owner="财务负责人", status="blocked", detail="三情景去化可用于压力测试，但不能替代融资、税费和回款底稿。"),

        page("VA3", "关键落地闸门", "哪些条件会直接改变主推方案？", "跨路审批、红线标高、消防人防、四代计容、成本模型和大户型实销是六个硬闸门。", refs=["SRC-XY-PLANNING-MINUTE", "SRC-XY-FINANCE", "SRC-XY-4THGEN"], owner="区域总负责人", status="partial", detail="每个闸门绑定责任人、截止日、证据和失败回退。"),
        page("VA3", "30·60·90天闭环", "下一轮工作怎样形成可验收闭环？", "30天锁法定输入，60天闭专项成本，90天用客户和去化数据决定是否解锁改善货。", refs=["SRC-DDS-METHOD", "SRC-XY-DESIGN", "SRC-DCBBS-XY-202605"], owner="项目总负责人", status="partial", detail="未满足验收条件时保持1.1基准，不升级方案、不增加高投入表达。"),

        page("CS", "证据与冲突矩阵", "哪些结论已被多源支持，哪些仍只是设计主张？", "市场容量和方案结构可条件决策；跨路、成本、审批和个盘实销仍保持partial/blocked。", refs=["SRC-DDS-METHOD", "SRC-XY-DESIGN", "SRC-DCBBS-XY-202605"], owner="研究负责人", status="ready", detail="17.45/17.55亿、104.21㎡和1148/1034套三组冲突必须在下一版关闭。"),
        page("CS", "结论与行动总表", "区域总负责人现在应批准什么、暂缓什么？", "批准方案1.1专项深化和分批去化验证；暂缓跨路承诺、方案切换及确定性IRR。", refs=["SRC-DDS-METHOD", "SRC-XY-DESIGN", "SRC-DCBBS-XY-202605", "SRC-XY-FINANCE"], owner="区域总负责人", status="partial", detail="报告的价值是把设计方向转为证据、闸门和可回退任务。"),
    ]


def _page_manifest(assets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    asset_ids = {str(item["asset_id"]) for item in assets}
    specs = _decision_page_specs()
    pages: list[dict[str, Any]] = []
    unit_counts: dict[str, int] = {}
    order = ["SC1", "SC2", "SC3", "AD1", "AD2", "AD3", "AD4", "AD5", "VA1", "VA2", "VA3", "CS"]
    for unit in order:
        for spec in (item for item in specs if item["unit"] == unit):
            unit_counts[unit] = unit_counts.get(unit, 0) + 1
            index = unit_counts[unit]
            title = str(spec["title"])
            page_id = f"{unit.lower()}-{index:02d}-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}"
            asset_ref = (
                f"ASSET-XY-SLIDE-{int(spec['asset']):03d}" if spec.get("asset") else ""
            )
            if asset_ref and asset_ref not in asset_ids:
                raise ValueError(f"missing semantic slide asset: {asset_ref}")
            refs = list(dict.fromkeys(str(ref) for ref in spec["refs"]))
            blocks: list[dict[str, Any]] = []
            if asset_ref:
                blocks.append(
                    {
                        "type": "media",
                        "asset_ref": asset_ref,
                        "alt": title,
                        "source_refs": refs,
                    }
                )
            elif spec.get("detail"):
                blocks.append(
                    {
                        "type": "narrative",
                        "title": "决策依据",
                        "text": str(spec["detail"]),
                        "source_refs": refs,
                    }
                )
            chart_specs: list[dict[str, Any]] = []
            if spec.get("chart_type"):
                chart_specs.append(
                    {
                        "chart_id": f"CHART-{unit}-{index:02d}",
                        "type": str(spec["chart_type"]),
                        "title": title,
                        "description": str(spec.get("detail") or spec["takeaway"]),
                        "unit": "登记口径",
                        "series": deepcopy(spec.get("series") or []),
                        "source_refs": refs,
                        "evidence_status": "registered",
                    }
                )
            role, contract = ("", {})
            if index == 1 and unit in SPECIAL_UNIT_CONTRACTS:
                role, contract = deepcopy(SPECIAL_UNIT_CONTRACTS[unit])
            status = str(spec.get("status") or "ready")
            evidence_type = "observed_fact" if unit.startswith("SC") or unit == "CS" else "analysis_inference"
            if unit == "AD5":
                evidence_type = "traditional_interpretation"
            pages.append(
                {
                    "page_id": page_id,
                    "chapter_id": CHAPTER_ROTATION[unit][0],
                    "section_id": unit,
                    "layout": "content",
                    "title": title,
                    "display_title": title,
                    "decision_question": str(spec["question"]),
                    "takeaway": str(spec["takeaway"]),
                    "recommendation": str(spec["action"]),
                    "claim_ids": [f"CLM-{unit}-{index:02d}"],
                    "observed_evidence_refs": refs,
                    "counter_evidence_refs": refs[-1:],
                    "blocks": blocks,
                    "chart_specs": chart_specs,
                    "asset_refs": [asset_ref] if asset_ref else [],
                    "source_refs": refs,
                    "confidence": {"score": 0.82 if status == "ready" else 0.64, "level": "high" if status == "ready" else "medium"},
                    "evidence_type": evidence_type,
                    "decision_eligibility": status == "ready",
                    "unit_status": status,
                    "unit_role": role,
                    "unit_contract": contract,
                    "decision_gate": str(spec["action"]),
                    "owner": str(spec["owner"]),
                    "visual_dataset_hash": _stable_hash({"title": title, "asset": asset_ref, "series": spec.get("series") or []}),
                    "visual_evidence": "source_page" if asset_ref else ("chart" if chart_specs else "decision_matrix"),
                    "story_role": "evidence_appendix" if unit == "CS" else "primary_narrative",
                    "appendix_policy": "evidence" if unit == "CS" else "presentation",
                    "load_priority": "high" if index <= 2 else "normal",
                    "print_policy": {"include": True, "page_break_after": True, "allow_internal_scroll": False},
                }
            )
        # Detailed gaps live in the decision gate and the final CS register.
        # Only one consolidated CS gap page is kept; repeating one gap page per
        # chapter made the report longer without adding a new decision.
        gap = FRAMEWORK_GAPS.get(unit) if unit == "CS" else None
        if not gap:
            continue
        unit_counts[unit] = unit_counts.get(unit, 0) + 1
        index = unit_counts[unit]
        title = str(gap["title"])
        refs = list(gap["source_refs"])
        status = str(gap["status"])
        pages.append(
            {
                "page_id": f"{unit.lower()}-gap-{hashlib.sha1(title.encode('utf-8')).hexdigest()[:8]}",
                "chapter_id": "framework_gap",
                "section_id": unit,
                "layout": "gap",
                "title": title,
                "display_title": title,
                "decision_question": f"{unit}还有哪些信息不能被公开证据替代？",
                "takeaway": str(gap["statement"]),
                "recommendation": str(gap["action"]),
                "claim_ids": [f"CLM-{unit}-GAP"],
                "observed_evidence_refs": refs,
                "counter_evidence_refs": refs[-1:],
                "blocks": [{"type": "gap", "title": "剩余阻断", "text": str(gap["statement"]), "recommended_action": str(gap["action"]), "source_refs": refs}],
                "chart_specs": [],
                "asset_refs": [],
                "source_refs": refs,
                "confidence": {"score": 0.48 if status == "blocked" else 0.60, "level": "medium"},
                "evidence_type": "traditional_interpretation" if unit == "AD5" else "analysis_inference",
                "decision_eligibility": False,
                "unit_status": status,
                "unit_role": "",
                "unit_contract": {},
                "decision_gate": str(gap["action"]),
                "owner": "对应专业负责人",
                "visual_dataset_hash": _stable_hash({"unit": unit, "gap": gap["statement"]}),
                "visual_evidence": "gap_matrix",
                "story_role": "evidence_appendix",
                "appendix_policy": "evidence",
                "load_priority": "normal",
                "print_policy": {"include": True, "page_break_after": True, "allow_internal_scroll": False},
            }
        )
    return pages


def _report_seed(project: Path, as_of: str) -> dict[str, Any]:
    sources = _source_registry(as_of)
    assets = _assets(project)
    pages = _page_manifest(assets)
    competitors = [
        {"name": "舜山府", "period": "2026-01/05", "transaction_units": 109, "monthly_units": 21.8, "transaction_area_sqm": 14312, "transaction_price_cny_sqm": 12702, "average_unit_area_sqm": 131, "average_total_10k_cny": 167},
        {"name": "联投滨江商务区", "period": "2026-01/05", "transaction_units": 116, "monthly_units": 23.2, "transaction_area_sqm": 15310, "transaction_price_cny_sqm": 10721, "average_unit_area_sqm": 132, "average_total_10k_cny": 142},
        {"name": "盛特一品", "period": "2026-01/05", "transaction_units": 124, "monthly_units": 24.8, "transaction_area_sqm": 14010, "transaction_price_cny_sqm": 10488, "average_unit_area_sqm": 113, "average_total_10k_cny": 119},
        {"name": "汉江序", "period": "2026-01/05", "transaction_units": 70, "monthly_units": 14.0, "transaction_area_sqm": 9599, "transaction_price_cny_sqm": 13598, "average_unit_area_sqm": 137, "average_total_10k_cny": 186},
    ]
    return {
        "schema_version": "dds.report-seed/1.0",
        "page_manifest_authoritative": True,
        "status": "partial",
        "decision_eligibility": False,
        "method": "frozen_evidence_package_then_offline_deterministic_compile",
        "project": _project_manifest_stub(as_of),
        "meta": {
            "as_of": as_of,
            "compiled_at": f"{as_of}T00:00:00+08:00",
            "audience": ["开发商区域总负责人", "投拓负责人", "设计管理负责人", "建筑总监"],
            "coordinate_precision": "intersection_proxy",
        },
        "source_registry": sources,
        "asset_registry": assets,
        "page_manifest": pages,
        "decision": {
            "status": "partial",
            "headline": "方案1.1作为条件性主推，先深化、分批卖、用真实数据解锁改善货。",
            "recommendations": ["批准方案1.1进入专项深化", "首开以121/132㎡承担现金流", "暂缓跨路承诺、方案切换及确定性IRR"],
            "gates": ["跨路审批", "消防日照人防", "第四代计容", "成本与融资", "175/225㎡实销"],
            "source_refs": ["SRC-XY-DESIGN", "SRC-DCBBS-XY-202605", "SRC-XY-FINANCE"],
        },
        "site": {
            "status": "partial",
            "constraints": [
                {"name": "A/B地块容积率", "value": "2.8 / 2.5", "status": "registered"},
                {"name": "建筑密度", "value": "25%", "status": "registered"},
                {"name": "限高", "value": "80m", "status": "registered"},
                {"name": "市政支路", "value": "按15m审慎基线", "status": "partial"},
            ],
            "evidence_gaps": ["盖章红线", "统一坐标测量", "真实标高与管线", "跨路构筑物书面审批"],
            "source_refs": ["SRC-XY-PLANNING-MINUTE", "SRC-XY-DESIGN"],
        },
        "macro_context": {
            "status": "partial",
            "as_of": as_of,
            "national": {"investment_yoy": -18.0, "sales_area_yoy": -11.6, "sales_value_yoy": -13.6, "funds_yoy": -20.2},
            "xiangyang_2024": {"investment_yoy": -8.1, "sales_area_10k_sqm": 465.6, "sales_area_yoy": 0.3, "sales_value_100m_cny": 300.5, "sales_value_yoy": -3.2},
            "source_refs": ["SRC-OFFICIAL-NBS-H1-2026", "SRC-OFFICIAL-XY-2024"],
        },
        "competitor_series": {
            "status": "partial",
            "target_project_excluded": True,
            "items": competitors,
            "period": "2026-01/05",
            "weighted_transaction_price_cny_sqm": 11711,
            "weighted_average_unit_area_sqm": 127,
            "weighted_average_total_10k_cny": 148.8,
            "monthly_units_mean": 20.95,
            "monthly_units_median": 21.5,
            "source_refs": ["SRC-DCBBS-XY-202605"],
        },
        "market": {
            "status": "partial",
            "indicators": [
                {"name": "襄阳1-5月住宅成交", "value": 38.0, "unit": "万㎡", "yoy_pct": -23.4},
                {"name": "樊城1-5月住宅成交", "value": 16.13, "unit": "万㎡", "yoy_pct": 24.3},
                {"name": "樊城库存", "value": 278.64, "unit": "万㎡"},
                {"name": "樊城去化周期", "value": 96.86, "unit": "月"},
            ],
            "area_bands": [
                {"band": "90-120㎡", "share_pct": 48.2},
                {"band": "120-140㎡", "share_pct": 30.6},
                {"band": "140-180㎡", "share_pct": 14.3},
                {"band": "180-240㎡", "share_pct": 1.3},
            ],
            "total_price_bands": {"100-150万元_share_pct": 42.9, "above_150万元_share_pct": 12.5},
            "source_refs": ["SRC-DCBBS-XY-202605"],
        },
        "social_intelligence": {
            "status": "missing",
            "window_days": [30, 90, 180],
            "independent_author_count": 0,
            "reason": "合法社媒采集器未连接；独立作者少于10人时禁止聚合共识。",
            "decision_eligibility": False,
        },
        "persona_evidence_profiles": [],
        "synthetic_personas": {"status": "missing", "evidence_confidence": 0.0, "simulation_stability": None},
        "traditional_spatial_culture": {
            "status": "partial",
            "input_level": "G0",
            "decision_eligibility": False,
            "physical_observations": ["仅有路口代理坐标，可做周边POI检索。"],
            "evidence_gaps": ["红线", "真北", "标高/DEM", "实际入口", "现场罗盘"],
            "traditional_readings": [],
            "prohibited_uses": ["不判断吉凶", "不进入售价/ROI/IRR/拿地价"],
            "source_refs": ["SRC-DDS-METHOD", "SRC-AMAP-POI"],
        },
        "concept_options": {
            "options": [
                {"scheme_id": "scheme-1.1", "name": "方案1.1·均衡兑现", "legacy_ref": "0710最终方案1.1", "status": "recommended", "goods_value_100m_cny": 17.55},
                {"scheme_id": "scheme-1.2", "name": "方案1.2·四代价值", "legacy_ref": "0710最终方案1.2", "status": "conditional", "goods_value_100m_cny": 17.66},
                {"scheme_id": "scheme-2", "name": "方案2·货值上限", "legacy_ref": "0710最终方案2/源自初始方案7", "status": "blocked", "goods_value_100m_cny": 17.78},
            ],
            "source_refs": ["SRC-XY-DESIGN", "SRC-XY-FINANCE"],
        },
        "recommended_scheme": {
            "scheme_id": "scheme-1.1",
            "name": "方案1.1·均衡兑现",
            "unique_recommendation": True,
            "scheme_2_gate": ["四代/抬板正式适用", "消防日照结构闭合", "成本修复", "售价与去化覆盖增量投入"],
            "scheme_3_gate": ["方案2全部闸门", "峰值资金通过", "审批通过", "大户型去化通过"],
            "fallback": "任一闸门失败即回退方案1.1骨架或其保守版。",
            "source_refs": ["SRC-XY-DESIGN", "SRC-XY-PLANNING-MINUTE", "SRC-XY-FINANCE"],
        },
        "architecture_design": {
            "status": "partial",
            "required_packages": ["总图", "户型", "立面", "景观", "会所", "示范区", "消防", "人防", "竖向/土方"],
            "archlib_boundary": "仅作设计机制与表达参考，不作价格/去化证据。",
        },
        "product": {
            "status": "partial",
            "scheme_id": "scheme-1.1",
            "unit_mix": [
                {"area_sqm": 121, "units": 402, "share_pct": 38.9, "role": "首开现金流"},
                {"area_sqm": 132, "units": 280, "share_pct": 27.1, "role": "主流改善"},
                {"area_sqm": 148, "units": 208, "share_pct": 20.1, "role": "价值锚点"},
                {"area_sqm": 175, "units": 80, "share_pct": 7.7, "role": "条件解锁"},
                {"area_sqm": 225, "units": 64, "share_pct": 6.2, "role": "小批验证"},
            ],
            "source_refs": ["SRC-XY-DESIGN", "SRC-DCBBS-XY-202605"],
        },
        "design_value_premium": {
            "status": "partial",
            "tiers": ["一级底线", "二级价值投入", "三级表达投入"],
            "chain": "成本→体验→竞争力→流速/长期价值",
            "financial_boundary": "未校验设计语言不直接换算售价。",
        },
        "premium_analysis": {"status": "partial", "decision_eligibility": False, "reason": "缺真实成交、流速与增量成本锚点。"},
        "absorption_forecast": {
            "status": "partial",
            "model_type": "market_calibrated_scenario",
            "total_units": 1034,
            "residential_area_sqm": 144890.21,
            "average_unit_area_sqm": 140.13,
            "scenarios": [
                {"name": "保守", "sellout_months": 66, "monthly_units": 15.67, "monthly_area_sqm": 2195, "fancheng_share_pct": 6.8},
                {"name": "基准", "sellout_months": 48, "monthly_units": 21.54, "monthly_area_sqm": 3019, "fancheng_share_pct": 9.4},
                {"name": "乐观", "sellout_months": 42, "monthly_units": 24.62, "monthly_area_sqm": 3450, "fancheng_share_pct": 10.7},
            ],
            "stress_case": {"name": "36个月压力项", "sellout_months": 36, "monthly_units": 28.72, "monthly_area_sqm": 4021, "fancheng_share_pct": 12.5, "baseline_eligible": False},
            "unit_band_monthly": {"121": 8.38, "132": 5.83, "148": 4.33, "175": 1.67, "225": 1.33},
            "evidence_confidence": 0.68,
            "simulation_stability": 0.92,
            "decision_eligibility": False,
            "limitations": ["缺目标项目真实来访与认筹转化", "折扣与退房口径仍待首开回测", "模型是条件性推演而非销售承诺"],
            "source_refs": ["SRC-DCBBS-XY-202605", "SRC-XY-DESIGN", "SRC-XY-FINANCE"],
        },
        "investment_case": {
            "status": "blocked",
            "irr_status": "blocked",
            "goods_value_conflict": {"display_100m_cny": 17.45, "sum_100m_cny": 17.553255, "rounded_reference_100m_cny": 17.55},
            "excel_blockers": {"R8": "错引空白I25与管理费单价H27", "#REF!": {"汇总": 17, "A3.3": 21, "合并1": 2, "合并2": 1}},
            "prohibited_output": "不输出确定性IRR",
            "decision_eligibility": False,
            "source_refs": ["SRC-XY-FINANCE", "SRC-XY-DESIGN"],
        },
        "finance": {
            "status": "blocked",
            "evidence_gaps": ["R8错引", "31处#REF!", "17.45/17.55亿元货值冲突", "1148/1034套版本冲突", "融资与税费口径未冻结"],
            "allowed_use": "仅使用66/48/42个月去化做回款压力测试，不输出确定性IRR。",
            "source_refs": ["SRC-XY-FINANCE", "SRC-XY-DESIGN", "SRC-DCBBS-XY-202605"],
        },
        "risk": {
            "status": "partial",
            "items": ["规划/道路", "征迁/交地", "财务错引", "消防/人防", "市场/去化", "分期/实施"],
        },
        "evidence_gaps": [
            {"gap_id": "GAP-REDLINE", "status": "blocked", "severity": "decision_blocking", "blocking_section": "SC3/AD1/AD5", "statement": "缺法定红线、测量坐标与标高。", "owner": "规划设计负责人", "recommended_action": "取得盖章红线、统一坐标系测量成果、真北与场地标高文件。", "acceptance": "红线闭合且坐标、真北、标高可由测绘成果交叉复核。"},
            {"gap_id": "GAP-ROAD", "status": "blocked", "severity": "decision_blocking", "blocking_section": "SC3/AD1/AD2", "statement": "12m道路尚无最终批复，仍按15m基线。", "owner": "报建负责人", "recommended_action": "取得市政支路最终规划批复及道路断面、实施时序。", "acceptance": "盖章批复明确道路宽度、红线位置、标高与建设责任。"},
            {"gap_id": "GAP-FINANCE", "status": "blocked", "severity": "decision_blocking", "blocking_section": "VA2", "statement": "R8与#REF!依赖链未修复。", "owner": "成本财务负责人", "recommended_action": "在原件副本中追溯R8和全部#REF!，复核融资条件及17.45/17.55亿元口径。", "acceptance": "公式审计无断链，货值、成本、融资与现金流口径勾稽一致。"},
            {"gap_id": "GAP-ABSORPTION", "status": "partial", "severity": "confidence_affecting", "blocking_section": "SC2/VA2", "statement": "已有城市/樊城容量、竞品流速及三情景模型；仍缺目标项目真实来访、认筹、净签、折扣与分面积段回测。", "owner": "营销负责人", "recommended_action": "用首开90天真实漏斗和分面积段净签校准三情景模型。", "acceptance": "来访—认筹—净签—退房—折扣逐周可复算，连续90天验证基准情景及175/225㎡闸门。"},
            {"gap_id": "GAP-SOCIAL", "status": "missing", "severity": "confidence_affecting", "blocking_section": "SC2/AD3", "statement": "合法社媒采集器未连接。", "owner": "客研负责人", "recommended_action": "接入公开或授权导出的社媒内容，脱敏去重并区分购房者、中介、开发商与媒体。", "acceptance": "至少10名独立作者且来源、日期、权利状态和反方证据可审计。"},
        ],
        "action_register": [
            {"action_id": "ACT-REDLINE", "gap_ref": "GAP-REDLINE", "action": "补齐法定红线与测绘基准", "owner": "规划设计负责人", "stage": "方案深化前", "trigger": "进入同边界强排复核前", "acceptance": "红线闭合且坐标、真北、标高可由测绘成果交叉复核。", "fallback": "保持坐标代理状态，不输出精确强排、日照或风水判断。", "status": "open"},
            {"action_id": "ACT-ROAD", "gap_ref": "GAP-ROAD", "action": "锁定市政支路法定条件", "owner": "报建负责人", "stage": "方案比选前", "trigger": "道路宽度或位置影响货值与消防组织时", "acceptance": "盖章批复明确道路宽度、红线位置、标高与建设责任。", "fallback": "继续按15m审慎基线测算，不释放道路缩窄收益。", "status": "open"},
            {"action_id": "ACT-FINANCE", "gap_ref": "GAP-FINANCE", "action": "修复财务模型断链并统一货值口径", "owner": "成本财务负责人", "stage": "投资决策前", "trigger": "输出IRR、峰值资金或方案财务排序前", "acceptance": "公式审计无断链，货值、成本、融资与现金流口径勾稽一致。", "fallback": "仅保留条件性边界，不输出确定性IRR。", "status": "open"},
            {"action_id": "ACT-ABSORPTION", "gap_ref": "GAP-ABSORPTION", "action": "用真实首开数据校准三情景模型", "owner": "营销负责人", "stage": "首开后90天", "trigger": "175/225㎡解锁、加推或回款模型更新前", "acceptance": "来访—认筹—净签—退房—折扣逐周可复算，连续90天验证基准情景及大户型闸门。", "fallback": "沿用66/48/42个月压力区间；低于16套/月或折扣超过5%即停止新增大户型。", "status": "open"},
            {"action_id": "ACT-SOCIAL", "gap_ref": "GAP-SOCIAL", "action": "建立合法C端证据样本", "owner": "客研负责人", "stage": "客群校准前", "trigger": "使用社媒观点调整客群与产品偏好前", "acceptance": "至少10名独立作者且来源、日期、权利状态和反方证据可审计。", "fallback": "沿用先验画像并明确missing，不生成C端共识。", "status": "open"},
        ],
        "allowed_outputs": ["方案收敛", "主推与闸门", "补证任务", "条件性设计价值判断"],
    }


def _prepare(project: Path, as_of: str) -> None:
    inbox = project / "inbox"
    if not inbox.is_dir():
        raise FileNotFoundError(f"Xiangyang inbox does not exist: {inbox}")
    work = project / "work"
    work.mkdir(exist_ok=True)
    overrides = _normalization_overrides(project)
    manifest = _project_manifest_stub(as_of)
    sources = _source_registry(as_of)
    seed = _report_seed(project, as_of)
    _write_json(work / "normalization_overrides.json", overrides)
    _write_json(work / "project_manifest.json", manifest)
    _write_json(work / "report_seed.json", seed)
    _write_source_snapshots(work, sources, as_of)


def _finalize_named_delivery(
    result: dict[str, Any], *, project: Path, as_of: str
) -> dict[str, Any]:
    """Publish the stable business-facing filename without deleting the generic export."""
    source = Path(result["paths"]["portable_single_file"])
    alias = project / "delivery" / f"{as_of.replace('-', '')}_国投建华望府_DDS前策研判_发送版.html"
    payload = source.read_bytes()
    if not alias.is_file() or alias.read_bytes() != payload:
        alias.write_bytes(payload)
    hashes_path = project / "delivery" / "hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    hashes.setdefault("artifacts", {})[alias.name] = hashlib.sha256(payload).hexdigest()
    _write_json(hashes_path, hashes)
    result["paths"]["portable_single_file"] = str(alias)
    result["paths"]["hashes"] = str(hashes_path)
    return result


def build_xiangyang_gold_sample(
    *,
    project_dir: str | Path,
    as_of: str = AS_OF,
    owner_id: str = "local",
    profile: str = SUPPORTED_PROFILE,
) -> dict[str, Any]:
    project = Path(project_dir).expanduser().resolve()
    _prepare(project, as_of)
    result = build_project_report(
        project_dir=project,
        as_of=as_of,
        owner_id=owner_id,
        research="cached",
        profile=profile,
        exports=("interactive", "portable_single_file"),
    )
    return _finalize_named_delivery(result, project=project, as_of=as_of)


def compile_xiangyang_gold_sample(
    *,
    project_dir: str | Path,
    as_of: str = AS_OF,
    owner_id: str = "local",
    profile: str = SUPPORTED_PROFILE,
) -> dict[str, Any]:
    project = Path(project_dir).expanduser().resolve()
    result = build_project_report(
        project_dir=project,
        as_of=as_of,
        owner_id=owner_id,
        research="off",
        profile=profile,
        exports=("interactive", "portable_single_file"),
        compile_only=True,
    )
    return _finalize_named_delivery(result, project=project, as_of=as_of)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Xiangyang DDS gold sample")
    parser.add_argument("--project-dir", default=str(Path(__file__).resolve().parents[1] / "Test_襄阳"))
    parser.add_argument("--as-of", default=AS_OF)
    parser.add_argument("--owner-id", default="local")
    parser.add_argument("--profile", default=SUPPORTED_PROFILE)
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args(argv)
    result = (
        compile_xiangyang_gold_sample(project_dir=args.project_dir, as_of=args.as_of, owner_id=args.owner_id, profile=args.profile)
        if args.compile_only
        else build_xiangyang_gold_sample(project_dir=args.project_dir, as_of=args.as_of, owner_id=args.owner_id, profile=args.profile)
    )
    print(json.dumps({"package_hash": result["package_hash"], "report_document_hash": result["report_document_hash"], "paths": result["paths"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

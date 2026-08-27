# 建筑行业全流程与多项目类型标准提取字段清单

本文件为 DDS 投拓与设计决策数据准备系统（DDS-Inbox-Pipeline）的**双键特征提取规范 (Dual-Key Extraction Specification)**。

Agent 在执行 Phase 3 属性提取时，须同时根据项目的**项目类型 (PROJECT_TYPE)** 和**文件生命周期阶段 (LIFECYCLE_STAGE)** 动态组合并调度字段提取模板。

---

## 一、通用核心字段（任何阶段与类型均须提取）

| 字段名 | 数据库字段 (schema key) | 类别 | 说明 |
|:---|:---|:---|:---|
| 楼盘名称 | `project_name` | 标识 | 包含括号别名，如"国投·建华望府 (星河国际)" |
| 城市名称 | `city` | 标识 | 须与 Vault 城市索引一致（如：襄阳、三亚） |
| 区域名称 | `district` | 标识 | 行政区划名称（如：樊城区） |
| 开发商 | `developer` | 标识 | 建设单位/投资主体法人全称 |
| 项目地址 | `address` | 空间 | 街道门牌级地址或地块四至描述 |
| 经度_高德 | `lng_gaode` | 空间 | GCJ-02 经度坐标（由高德 API 补齐） |
| 纬度_高德 | `lat_gaode` | 空间 | GCJ-02 纬度坐标（由高德 API 补齐） |
| 项目类型 | `project_type` | 标识 | 记录为：`RESIDENTIAL` / `PUBLIC_BUILDING` / `TOWNSHIP_PLANNING` / `RENOVATION` / `URBAN_DESIGN` |
| 所处阶段 | `lifecycle_stage` | 标识 | 记录当前资料所处的最高生命周期阶段（`STAGE_FEASIBILITY` 到 `STAGE_OPERATION_MAINT`） |

---

## 二、维度键 1：按生命周期阶段动态调度 (Lifecycle-Based Fields)

根据判定文件所属的**生命周期阶段**，重点提取并填充以下技术与经济数据：

### 1. 前期策划与可行性研究 (`STAGE_FEASIBILITY`)
重点关注项目可行性、投资回报和土地指标。
*   **拿地楼面价 (`floor_price`)**：元/㎡（楼面地价）
*   **土地总价 (`land_total_price`)**：万元/亿元
*   **预期销售均价 (`price_raw`)**：元/㎡
*   **项目总投资 (`total_investment`)**：万元/亿元
*   **内部收益率 (`irr`)**：财务测算表中 IRR 指标（%）
*   **静态投资回收期 (`payback_period`)**：年限

### 2. 方案/概念设计 (`STAGE_CONCEPT_SCHEME`)
重点关注建筑形态、立面风格、规划红线与配套指标。
*   **规划容积率 (`floor_area_ratio`)**：如 2.59
*   **建筑密度 (`building_density`)**：建筑基底面积占比（%）
*   **绿地率 (`green_ratio`)**：绿化覆盖比例（%）
*   **建筑限高 (`height_limit`)**：米（m）
*   **建筑风格 (`arch_style`)**：现代极简、新中式、大都会、公建化立面等（对齐 ArchLib）
*   **立面主材质 (`facade_material`)**：铝板、石材、超白玻璃幕墙、真石漆等（对齐 ArchLib）
*   **色调倾向 (`color_tone`)**：暖色/米金、冷色/银灰、深灰/黑色等
*   **日照标准 (`sunlight_standard`)**：最不利点日照小时（如：大寒日不低于1小时）

### 3. 初步设计阶段 (`STAGE_PRELIMINARY`)
重点关注各专业技术经济指标与总体概算。
*   **工程概算造价 (`estimated_cost`)**：初步设计概算总造价
*   **地上建筑面积 (`above_ground_area`)**：㎡
*   **地下建筑面积 (`under_ground_area`)**：㎡
*   **主要结构形式 (`structural_type`)**：框架、框剪、钢结构等
*   **消防等级 (`fire_safety_level`)**：一类高层/二类高层等
*   **变配电总容量 (`electrical_capacity`)**：kVA

### 4. 施工图设计阶段 (`STAGE_CONSTRUCTION_DWG`)
重点关注工程强条审查、施工图预算和详图参数。
*   **图审违反强条数 (`review_violations_count`)**：强条缺陷数量
*   **施工图预算造价 (`construction_budget_cost`)**：万元
*   **混凝土强度等级 (`concrete_grade`)**：如 C30/C40/C50
*   **外墙保温材质 (`insulation_material`)**：如岩棉板、挤塑聚苯板 (XPS)

### 5. 招投标与采购阶段 (`STAGE_TENDERING`)
重点关注工程量清单明细与分包单价限价。
*   **招标控制价 (`tender_limit_price`)**：招标上限价（元/万元）
*   **清单综合单价 (`boq_unit_price`)**：重点分部分项工程（如土石方、钢筋混凝土）的清单单价
*   **暂估价项目金额 (`provisional_sum`)**：合同中暂列或暂估价项目总额

### 6. 施工建设阶段 (`STAGE_CONSTRUCTION`)
重点关注现场技术变更、造价调整与进度控制。
*   **累计变更造价 (`cumulative_change_cost`)**：已确认设计变更累计造价
*   **主要变更原因 (`change_reason`)**：设计图纸错误/业主需求调整/现场不可抗力
*   **工期偏差 (`schedule_deviation`)**：实际进度对比计划偏差天数

### 7. 竣工验收阶段 (`STAGE_HANDOVER`)
重点关注竣工实测数据与验收批文。
*   **竣工备案实测面积 (`handover_measured_area`)**：最终实测总面积（㎡）
*   **面积偏差率 (`area_deviation_rate`)**：(实测面积 - 规划报建面积) / 规划报建面积（%）
*   **消防验收结论 (`fire_acceptance_status`)**：合格/限期整改批复文号

### 8. 运营维护阶段 (`STAGE_OPERATION_MAINT`)
重点关注设备资产台账与运维基准。
*   **机电设备总台账数 (`mep_assets_count`)**：设备资产数量
*   **年均能耗指标 (`annual_energy_consumption`)**：kWh/㎡/年
*   **主要维护周期 (`maintenance_cycle`)**：关键设备（如电梯、冷水机组）的大修/保养周期

---

## 三、维度键 2：按项目类型动态调度 (Project-Type Fields)

根据判定的**项目类型**，重点提取并填充以下专属设计与规划参数：

### 1. RESIDENTIAL (住宅项目)
*   **四代住宅特征 (`is_4th_gen`)**：是否属于第四代住宅（"是"/"否"）
*   **挑高空中绿化露台 (`has_sky_garden`)**：有无错层挑高外挑花园露台及外挑尺度（m）
*   **首层抬板架空设计 (`has_raised_platform`)**：是否采用防洪/景观抬板及抬高高度（m）
*   **户型配比明细 (`unit_mix`)**：主力面积段套数分布（JSON 数组）
*   **安置回迁面积 (`resettlement_area`)**：项目内包含的回迁安置房总面积（㎡）

### 2. PUBLIC_BUILDING (公建项目)
*   **公建功能类别 (`public_building_type`)**：写字楼/商业街/文化中心/学校/医院/体育馆等
*   **绿色建筑星级 (`green_building_level`)**：三星级/二星级/一星级
*   **车位配比指标 (`parking_ratio`)**：每百平米建筑面积配备车位数
*   **公共大堂层高 (`lobby_height`)**：主大堂净高/层高（m）

### 3. TOWNSHIP_PLANNING (乡镇/乡村规划项目)
*   **规划编制总面积 (`planning_area`)**：平方公里（k㎡）或公顷
*   **规划人口容量 (`population_scale`)**：常住与游客容量预测（万人）
*   **新增建设用地红线 (`new_construction_land`)**：公顷
*   **乡村主导产业 (`leading_industry`)**：生态农业/民俗旅游/手工业等
*   **传统村落保护要素 (`village_heritage`)**：古建筑保护点数、传统风貌管控带范围

### 4. RENOVATION (城市更新/改造项目)
*   **更新改造模式 (`renewal_mode`)**：整体拆建/微改造提升/历史风貌修缮
*   **现状保留与修缮率 (`preservation_rate`)**：现状保留建筑面积占比（%）
*   **历史风貌保护红线 (`heritage_protection`)**：文物保护红线范围与修缮技术规范
*   **改建功能置换明细 (`function_swap`)**：原业态与改造后新业态对照

### 5. URBAN_DESIGN (城市设计项目)
*   **城市空间结构 (`spatial_structure`)**：空间规划轴线与功能核心布局描述
*   **重要景观视廊 (`view_corridor`)**：重点视线通廊范围与高度限制
*   **天际线控制高度 (`skyline_control`)**：地标节点建筑控制高度（m）
*   **公共开敞空间比例 (`public_realm_ratio`)**：公园、水系、广场面积占比（%）

---

## 四、ArchLib 检索方向匹配逻辑

Agent 提取完双维属性后，在交付报告中推荐的 ArchLib 检索方向须通过以下逻辑进行**交叉匹配**：

```
IF PROJECT_TYPE == RESIDENTIAL:
    IF has_sky_garden == True: 
        推荐标签 -> arch_style: 现代极简/新中式 | scene_part: 错层露台/空中花园
    IF has_raised_platform == True:
        推荐标签 -> scene_part: 抬板底盘/架空层/入口门头
        
IF PROJECT_TYPE == PUBLIC_BUILDING:
    推荐标签 -> arch_style: 现代公建/大都会 | scene_part: 整体外观/入口门头/中庭大堂 | facade_material: 玻璃幕墙/金属板

IF PROJECT_TYPE == RENOVATION:
    推荐标签 -> arch_style: 工业风/工业遗存 | facade_material: 红砖/清水混凝土/耐候钢板

IF PROJECT_TYPE == TOWNSHIP_PLANNING:
    推荐标签 -> arch_style: 新中式/地方风貌/野奢 | view_type: 鸟瞰/航拍
```

---

## 五、输出 `project_profile.json` 样例

以一个**初步设计阶段的公建项目**为例，其 `project_profile.json` 输出格式如下：

```json
{
  "楼盘名称": "襄阳高新区市民文化中心",
  "城市名称": "襄阳",
  "开发商": "襄阳投资发展集团有限公司",
  "最新价格": 0,
  "项目地址": "高新区东风大道与二环路交汇处",
  "经度_高德": 112.1554,
  "纬度_高德": 32.0876,
  "project_type": "PUBLIC_BUILDING",
  "lifecycle_stage": "STAGE_PRELIMINARY",
  "公建类别": "文体/市民中心",
  "结构形式": "钢桁架+钢筋混凝土框剪结构",
  "绿建星级": "二星级",
  "工程概算造价": "4.5亿元",
  "地上建筑面积": 45000,
  "地下建筑面积": 15000,
  "主要配套功能": "含1200座大剧院、市民图书馆、多功能规划展厅",
  "核心立面材质": "超白中空双银玻璃幕墙 + 阳极氧化铝板",
  "_source_lifecycle_stage": "20260630 初步设计总说明说明书",
  "_source_工程概算造价": "20260702 初步设计概算汇总表",
  "_conflicts": [],
  "_extracted_at": "2026-07-15T17:19:00",
  "_version": 2
}
```

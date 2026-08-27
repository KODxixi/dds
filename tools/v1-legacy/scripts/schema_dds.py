# -*- coding: utf-8 -*-
"""
DDS 楼盘数据 canonical schema（完整维度体系单一真值源）
================================================================
设计原则（关键，勿违背）：
  1. **零 breakage**：完全保留现有 56 列的中文列名与顺序（query_local.py /
     report_parcel.py / ingest_vault.py 等 ~20 个脚本按中文列名读取，绝不可改名）。
  2. **additive 超集**：用户 13 大类 ~99 维 taxonomy 中，凡现有 56 列已覆盖的概念
     映射到既有列（不重复造列）；未覆盖的维度作为「新列」追加到尾部。
  3. **单一真值源**：列名/顺序/中英映射/类别/采集来源标记全部由本模块定义，
     其它脚本 import 本模块，不得各自硬编码列表。

采集来源标记：
  [采] 外部采集    [内] 内部台账    [VLM] 案例库逐图打标    [算] DDS 计算输出
"""
from __future__ import annotations

# ── 既有系统 56 列（byte-for-byte，含重复的「建筑类型」与历史遗留列） ──────────────
LEGACY_HEADERS = [
    "楼盘ID", "楼盘名称", "城市名称", "城市ID", "区域名称", "区域ID",
    "子区域名称", "子区域ID", "地址", "环线位置", "最新价格", "参考价格",
    "房贷计算信息", "面积范围", "占地面积", "建筑面积", "房间面积信息",
    "户型文本描述", "全部户型", "开盘日期", "开盘日期备注", "开盘时间",
    "交房时间", "发证时间", "建筑类型", "建筑类型", "产权年限", "容积率",
    "绿化率", "规划户数", "装修情况", "工程进度", "物业类型", "物业公司",
    "物业管理费", "物业特色", "车位数", "车位比", "销售状态", "销售标题",
    "租售标题", "预售证号", "绑定楼栋", "开发商", "开发商品牌", "投资商",
    "供电", "供水", "百度地图纬度", "百度地图经度", "400电话",
    "所有标签列表", "标签列表", "默认图片", "规交信息", "售楼处地址",
]

# ── 完整维度 taxonomy：(类别, key, 中文名, 来源标记) ──────────────────────────────
# 顺序 = 用户提供的 13 大类原始顺序。
TAXONOMY = [
    # 一、标识与编号
    ("一·标识与编号", "id", "小区编号", "采"),
    ("一·标识与编号", "name", "小区名称", "采"),
    ("一·标识与编号", "list_name", "小区拼音名称", "采"),
    ("一·标识与编号", "extend_search_name_list", "检索名称集合", "采"),
    ("一·标识与编号", "tags", "小区标签", "采"),
    ("一·标识与编号", "default_photo", "小区默认图片", "采"),
    # 二、地理与区位
    ("二·地理与区位", "city_id", "城市ID", "采"),
    ("二·地理与区位", "city_name", "城市名称", "采"),
    ("二·地理与区位", "city_list_name", "城市拼音简称", "采"),
    ("二·地理与区位", "long_city_list_name", "城市拼音全称", "采"),
    ("二·地理与区位", "area_id", "区县ID", "采"),
    ("二·地理与区位", "area_name", "区县名称", "采"),
    ("二·地理与区位", "district_list_name", "区县拼音名称", "采"),
    ("二·地理与区位", "trading_area_id", "商圈ID", "采"),
    ("二·地理与区位", "trading_area_name", "商圈名称", "采"),
    ("二·地理与区位", "shangquan_list_name", "商圈拼音名称", "采"),
    ("二·地理与区位", "address", "小区地址", "采"),
    ("二·地理与区位", "ring_location", "环线位置", "采"),
    ("二·地理与区位", "blng", "百度经度", "采"),
    ("二·地理与区位", "blat", "百度纬度", "采"),
    ("二·地理与区位", "land_use_type", "用地条件和用地类型", "采"),
    ("二·地理与区位", "surroundings", "周边配套", "采"),
    # 三、价格与金融
    ("三·价格与金融", "price_info_price", "房价/最新价格", "采"),
    ("三·价格与金融", "price_ref", "参考价格", "采"),
    ("三·价格与金融", "price_info_market_sentiment", "房价市场状况", "采"),
    ("三·价格与金融", "area_avg_nearby_price", "周边均价", "采"),
    ("三·价格与金融", "mortgage_info", "房贷计算信息", "算"),
    ("三·价格与金融", "prop_info_sale_num", "在售数量", "采"),
    ("三·价格与金融", "prop_info_rent_num", "在租数量", "采"),
    # 四、成本与投资（内部 / DDS）
    ("四·成本与投资", "land_cost", "拿地价", "内"),
    ("四·成本与投资", "civil_cost", "土建成本", "内"),
    ("四·成本与投资", "facade_cost", "立面造价", "内"),
    ("四·成本与投资", "roi", "投资回报比", "算"),
    ("四·成本与投资", "premium_value", "溢价率及价值挖掘", "算"),
    ("四·成本与投资", "rent_sale_ratio", "租售比", "算"),
    # 五、产品与规划
    ("五·产品与规划", "estate_type", "房地产类型", "采"),
    ("五·产品与规划", "build_type_str", "建筑类型", "采"),
    ("五·产品与规划", "ship_type_str", "商品类型", "采"),
    ("五·产品与规划", "extend_type", "物业类型", "采"),
    ("五·产品与规划", "property_types", "物业类型集合", "采"),
    ("五·产品与规划", "years_of_property_rights", "产权年限", "采"),
    ("五·产品与规划", "plot_ratio", "容积率", "采"),
    ("五·产品与规划", "extend_landscaping_atio", "绿化率", "采"),
    ("五·产品与规划", "land_area", "占地面积", "采"),
    ("五·产品与规划", "build_area", "建筑面积", "采"),
    ("五·产品与规划", "extend_total_area", "小区面积", "采"),
    ("五·产品与规划", "area_range", "面积范围", "采"),
    ("五·产品与规划", "extend_total_house_hold_num", "总户数/规划户数", "采"),
    ("五·产品与规划", "is_4th_gen", "是否四代住宅", "采"),
    ("五·产品与规划", "decoration", "装修情况", "采"),
    ("五·产品与规划", "layout_logic", "规划排布逻辑", "VLM"),
    # 六、户型
    ("六·户型", "units_all", "全部户型", "采"),
    ("六·户型", "units_main", "主力成交户型", "采"),
    ("六·户型", "unit_desc", "各户型文本描述", "采"),
    ("六·户型", "unit_efficiency", "各户型对应得房率", "采"),
    ("六·户型", "room_area_info", "房间面积信息", "采"),
    # 七、车位与市政配套
    ("七·车位与市政", "parking_space", "停车位", "采"),
    ("七·车位与市政", "parking_rate", "车位比", "采"),
    ("七·车位与市政", "parking_space_management_fee", "车位管理费", "采"),
    ("七·车位与市政", "unified_heating", "统一供暖", "采"),
    ("七·车位与市政", "hydropower", "供水供电", "采"),
    ("七·车位与市政", "extend_water_power_supply", "用水类型", "采"),
    # 八、时间与工程进度
    ("八·时间与进度", "completion_time", "竣工时间", "采"),
    ("八·时间与进度", "open_date", "开盘日期", "采"),
    ("八·时间与进度", "open_date_note", "开盘日期备注", "采"),
    ("八·时间与进度", "open_time", "开盘时间", "采"),
    ("八·时间与进度", "handover_date", "交房时间", "采"),
    ("八·时间与进度", "cert_date", "发证时间", "采"),
    ("八·时间与进度", "presale_cert", "预售证号", "采"),
    ("八·时间与进度", "progress", "工程进度", "采"),
    ("八·时间与进度", "bound_buildings", "绑定楼栋", "采"),
    ("八·时间与进度", "planning_delivery_info", "规交信息", "采"),
    # 九、主体与运营
    ("九·主体与运营", "developers", "开发商", "采"),
    ("九·主体与运营", "investor", "投资商", "采"),
    ("九·主体与运营", "extend_property_company", "物业公司", "采"),
    ("九·主体与运营", "extend_property_money", "物业费", "采"),
    ("九·主体与运营", "extend_property_features", "物业特色", "采"),
    ("九·主体与运营", "arch_firm", "建筑设计方", "采"),
    ("九·主体与运营", "landscape_firm", "景观设计方", "采"),
    ("九·主体与运营", "sales_office_addr", "售楼处地址", "采"),
    ("九·主体与运营", "phone_400", "400电话", "采"),
    # 十、销售与市场
    ("十·销售与市场", "sale_status", "销售状态", "采"),
    ("十·销售与市场", "sale_title", "销售标题", "采"),
    ("十·销售与市场", "rent_sale_title", "租售标题", "采"),
    ("十·销售与市场", "sales_script", "销售话术", "算"),
    ("十·销售与市场", "sell_through", "去化周期", "算"),
    ("十·销售与市场", "customer_profile", "客群画像", "算"),
    # 十一、评价与评分
    ("十一·评价与评分", "grade_score", "综合评分", "采"),
    ("十一·评价与评分", "grade_list", "模块评分列表", "采"),
    ("十一·评价与评分", "grade_items", "模块评分项", "采"),
    ("十一·评价与评分", "extend_merits", "优点", "采"),
    ("十一·评价与评分", "extend_demerits", "缺点", "采"),
    ("十一·评价与评分", "industry_review", "业内评价", "内"),
    # 十二、设计与体验（ArchLib 案例库联动）
    ("十二·设计与体验", "design_style", "设计风格", "VLM"),
    ("十二·设计与体验", "style_material", "风格材料", "VLM"),
    ("十二·设计与体验", "demo_area_config", "示范区配置", "VLM"),
    ("十二·设计与体验", "clubhouse_config", "公区会所配置", "VLM"),
    ("十二·设计与体验", "global_innovation", "全局创新点", "算"),
    # 十三、战略与定位（DDS）
    ("十三·战略与定位", "macro_position", "所在地段未来宏观定位", "算"),
    # ──────────────────────────────────────────────────────────────
    # Schema v2.0 新增维度（2026-07-09，对标 CRIC 极客问道 162 字段 + 地产经纪人审查补遗）
    # 追加 10 大类 ~143 列，全部 additive（只追加尾部、缺失留空、不破坏现有 105 列）
    # 来源标记：采=外部采集 内=内部台账 VLM=案例库逐图打标 算=DDS 计算输出
    # ──────────────────────────────────────────────────────────────
    # 十四、交通与配套距离（CRIC 基础信息补充，高德 POI 精算）
    ("十四·交通与配套", "nearest_subway_station", "最近地铁站", "算"),
    ("十四·交通与配套", "nearest_subway_distance", "地铁站距离_m", "算"),
    ("十四·交通与配套", "nearest_subway_lines", "地铁线路", "算"),
    ("十四·交通与配套", "nearest_school_name", "最近学校名称", "算"),
    ("十四·交通与配套", "nearest_school_distance", "学校距离_m", "算"),
    ("十四·交通与配套", "nearest_school_type", "学校类型", "算"),
    ("十四·交通与配套", "nearest_hospital_name", "最近医院名称", "算"),
    ("十四·交通与配套", "nearest_hospital_distance", "医院距离_m", "算"),
    ("十四·交通与配套", "nearest_mall_name", "最近商业体名称", "算"),
    ("十四·交通与配套", "nearest_mall_distance", "商业体距离_m", "算"),
    ("十四·交通与配套", "commute_time_center", "到市中心通勤_分钟", "算"),
    ("十四·交通与配套", "commute_time_cbd", "到CBD通勤_分钟", "算"),
    ("十四·交通与配套", "bus_stops_500m", "500m内公交站数", "算"),
    ("十四·交通与配套", "poi_count_1km", "1km内POI总数", "算"),
    ("十四·交通与配套", "poi_richness_score", "配套丰富度评分", "算"),
    # 十五、品牌与信用（CRIC 项目看点核心 + 物业评价）
    ("十五·品牌与信用", "developer_credit_rating", "开发商信用评级", "内"),
    ("十五·品牌与信用", "developer_delivery_rate", "开发商交付率_pct", "内"),
    ("十五·品牌与信用", "developer_delay_rate", "开发商延期交付率_pct", "内"),
    ("十五·品牌与信用", "developer_default_count", "开发商违约次数", "内"),
    ("十五·品牌与信用", "developer_complaint_count", "开发商投诉次数", "内"),
    ("十五·品牌与信用", "developer_project_count", "开发商累计项目数", "内"),
    ("十五·品牌与信用", "developer_total_area", "开发商累计开发面积_万㎡", "内"),
    ("十五·品牌与信用", "developer_rating_benchmark", "开发商行业排名", "内"),
    ("十五·品牌与信用", "investor_credit_rating", "投资商信用评级", "内"),
    ("十五·品牌与信用", "property_company_rating", "物业公司评级", "内"),
    ("十五·品牌与信用", "property_company_project_count", "物业公司管理项目数", "内"),
    ("十五·品牌与信用", "arch_firm_name", "建筑设计方名称", "采"),
    ("十五·品牌与信用", "arch_firm_qualification", "设计方资质等级", "采"),
    ("十五·品牌与信用", "construction_firm", "施工总承包方", "采"),
    ("十五·品牌与信用", "construction_firm_qualification", "施工方资质等级", "采"),
    ("十五·品牌与信用", "supervisor_firm", "监理单位", "采"),
    ("十五·品牌与信用", "supervisor_firm_qualification", "监理方资质等级", "采"),
    ("十五·品牌与信用", "delivery_credit_score", "交付信用综合评分", "算"),
    ("十五·品牌与信用", "property_fee_collection_rate", "物业费收缴率_pct", "内"),
    ("十五·品牌与信用", "property_complaint_count", "物业投诉次数", "内"),
    ("十五·品牌与信用", "owner_satisfaction_score", "业主满意度评分", "内"),
    ("十五·品牌与信用", "property_service_years", "物业服务年限", "内"),
    # 十六、产品力与稀缺资源（CRIC 项目看点扩展 + 学区深度）
    ("十六·产品力与稀缺", "product_score", "产品力综合评分", "算"),
    ("十六·产品力与稀缺", "location_value_score", "地段价值评分", "算"),
    ("十六·产品力与稀缺", "life_support_score", "生活配套评分", "算"),
    ("十六·产品力与稀缺", "price_system_score", "价格体系评分", "算"),
    ("十六·产品力与稀缺", "value_potential_score", "价值潜力评分", "算"),
    ("十六·产品力与稀缺", "is_low_density", "是否低密住宅", "算"),
    ("十六·产品力与稀缺", "landscaping_grade", "景观等级", "VLM"),
    ("十六·产品力与稀缺", "facade_material_grade", "立面材质等级", "VLM"),
    ("十六·产品力与稀缺", "public_area_grade", "公区品质等级", "VLM"),
    ("十六·产品力与稀缺", "smart_home_level", "智能家居等级", "采"),
    ("十六·产品力与稀缺", "energy_efficiency_grade", "能效等级", "采"),
    ("十六·产品力与稀缺", "green_building_grade", "绿色建筑等级", "采"),
    ("十六·产品力与稀缺", "assembly_rate", "装配率_pct", "采"),
    ("十六·产品力与稀缺", "is_school_district", "是否学区房", "算"),
    ("十六·产品力与稀缺", "school_district_name", "对应学区", "算"),
    ("十六·产品力与稀缺", "primary_school_name", "对应小学名称", "采"),
    ("十六·产品力与稀缺", "primary_school_rank", "对应小学排名", "采"),
    ("十六·产品力与稀缺", "middle_school_name", "对应初中名称", "采"),
    ("十六·产品力与稀缺", "middle_school_rank", "对应初中排名", "采"),
    ("十六·产品力与稀缺", "school_district_range", "学区划片范围", "采"),
    ("十六·产品力与稀缺", "school_enrollment_policy", "入学政策限制", "采"),
    ("十六·产品力与稀缺", "school_district_risk", "学区变动风险", "采"),
    ("十六·产品力与稀缺", "scenic_view", "景观资源", "采"),
    # 十七、户型明细（CRIC 户型信息 17 字段对标）
    ("十七·户型明细", "unit_type_detail", "户型明细表_JSON", "采"),
    ("十七·户型明细", "main_unit_area", "主力户型面积_㎡", "采"),
    ("十七·户型明细", "main_unit_rooms", "主力户型室数", "采"),
    ("十七·户型明细", "main_unit_bathrooms", "主力户型卫数", "采"),
    ("十七·户型明细", "main_unit_orientation", "主力户型朝向", "采"),
    ("十七·户型明细", "main_unit_face_width", "主力户型面宽_m", "采"),
    ("十七·户型明细", "main_unit_depth", "主力户型进深_m", "采"),
    ("十七·户型明细", "efficiency_rate", "得房率_pct", "采"),
    ("十七·户型明细", "decoration_brand", "精装品牌", "采"),
    ("十七·户型明细", "decoration_standard", "精装标准_元每㎡", "采"),
    ("十七·户型明细", "delivery_standard", "交付标准", "采"),
    ("十七·户型明细", "floor_height", "层高_m", "采"),
    # 十八、多媒体与可视化（CRIC 多媒体资料 14 字段对标）
    ("十八·多媒体资料", "real_photo_url", "实景图URL", "采"),
    ("十八·多媒体资料", "rendering_url", "效果图URL", "采"),
    ("十八·多媒体资料", "video_url", "项目视频URL", "采"),
    ("十八·多媒体资料", "vr_url", "VR看房URL", "采"),
    ("十八·多媒体资料", "sample_room_url", "样板间图URL", "采"),
    ("十八·多媒体资料", "sand_table_url", "沙盘图URL", "采"),
    ("十八·多媒体资料", "floor_plan_url", "户型图URL", "采"),
    ("十八·多媒体资料", "aerial_photo_url", "航拍图URL", "采"),
    ("十八·多媒体资料", "location_map_url", "区位图URL", "采"),
    ("十八·多媒体资料", "supporting_map_url", "配套图URL", "采"),
    ("十八·多媒体资料", "traffic_map_url", "交通图URL", "采"),
    ("十八·多媒体资料", "media_count", "多媒体素材总数", "算"),
    # 十九、成交与市场（L1 真实成交 + CMA 可比实例 + DOM + 市场情绪）
    ("十九·成交与市场", "avg_transaction_price", "区域成交均价_元每㎡", "内"),
    ("十九·成交与市场", "transaction_count_30d", "近30天成交套数", "内"),
    ("十九·成交与市场", "transaction_count_90d", "近90天成交套数", "内"),
    ("十九·成交与市场", "absorption_rate", "去化率_pct", "算"),
    ("十九·成交与市场", "inventory_months", "库存去化周期_月", "算"),
    ("十九·成交与市场", "price_trend_6m", "6个月价格趋势_pct", "算"),
    ("十九·成交与市场", "price_trend_12m", "12个月价格趋势_pct", "算"),
    ("十九·成交与市场", "resale_price_avg", "周边二手均价_元每㎡", "算"),
    ("十九·成交与市场", "rental_yield", "租金回报率_pct", "算"),
    ("十九·成交与市场", "market_heat_index", "市场热度指数", "算"),
    ("十九·成交与市场", "supply_demand_ratio", "供需比", "算"),
    ("十九·成交与市场", "transaction_data_source", "成交数据来源", "内"),
    # — CMA 可比实例字段（地产经纪人 CMA 核心需求）—
    ("十九·成交与市场", "comparable_sale_date", "可比实例_成交日期", "内"),
    ("十九·成交与市场", "comparable_sale_price", "可比实例_成交均价", "内"),
    ("十九·成交与市场", "comparable_list_price", "可比实例_挂牌价", "内"),
    ("十九·成交与市场", "comparable_dom", "可比实例_挂牌天数", "内"),
    ("十九·成交与市场", "comparable_sp_lp_ratio", "可比实例_挂牌成交比", "算"),
    # — DOM 与价格调整历史（经纪人核心市场指标）—
    ("十九·成交与市场", "first_list_date", "首次挂牌日期", "内"),
    ("十九·成交与市场", "current_dom", "当前挂牌天数", "算"),
    ("十九·成交与市场", "price_adjustment_count", "价格调整次数", "内"),
    ("十九·成交与市场", "last_price_adjustment_pct", "最近调价幅度_pct", "内"),
    ("十九·成交与市场", "price_history_json", "历史挂牌价记录_JSON", "内"),
    # — SP/LP% 挂牌成交比（定价策略核心指标）—
    ("十九·成交与市场", "region_sp_lp_ratio", "区域挂牌成交比_pct", "算"),
    ("十九·成交与市场", "subarea_sp_lp_ratio", "板块挂牌成交比_pct", "算"),
    ("十九·成交与市场", "type_sp_lp_ratio", "同类型挂牌成交比_pct", "算"),
    # — Pending 已签约待过户（最强市场信号）—
    ("十九·成交与市场", "pending_count", "板块待过户套数", "内"),
    ("十九·成交与市场", "pending_avg_price", "板块待过户均价_元每㎡", "内"),
    ("十九·成交与市场", "pending_months", "待过户去化周期_月", "算"),
    # — 吸纳率与市场周期（买卖方市场判定）—
    ("十九·成交与市场", "active_listings_count", "在售套数", "内"),
    ("十九·成交与市场", "monthly_avg_sales_12m", "近12月月均成交套数", "算"),
    ("十九·成交与市场", "absorption_months", "吸纳率_月", "算"),
    ("十九·成交与市场", "market_cycle", "市场周期判定", "算"),
    # — 市场情绪与带看量（领先指标）—
    ("十九·成交与市场", "weekly_showing_count", "周带看次数", "采"),
    ("十九·成交与市场", "weekly_inquiry_count", "周咨询次数", "采"),
    ("十九·成交与市场", "showing_mom_change", "带看环比变化_pct", "算"),
    # 二十、租赁市场（投资分析核心数据管道）
    ("二十·租赁市场", "avg_rent_monthly", "区域平均租金_元每月每套", "采"),
    ("二十·租赁市场", "avg_rent_per_sqm", "区域平均租金_元每㎡每月", "采"),
    ("二十·租赁市场", "vacancy_rate", "空置率_pct", "采"),
    ("二十·租赁市场", "rent_yoy_growth", "租金同比涨幅_pct", "算"),
    ("二十·租赁市场", "rent_sale_ratio_v2", "租金售价比", "算"),
    ("二十·租赁市场", "grm_years", "GRM_年", "算"),
    ("二十·租赁市场", "cap_rate_pct", "cap_rate_pct", "算"),
    ("二十·租赁市场", "rental_data_source", "租金数据来源", "采"),
    # 二十一、政策与金融（房贷利率与购房限制监控）
    ("二十一·政策与金融", "lpr_5y", "当前LPR_5年期_pct", "采"),
    ("二十一·政策与金融", "first_home_rate", "首套利率_pct", "采"),
    ("二十一·政策与金融", "second_home_rate", "二套利率_pct", "采"),
    ("二十一·政策与金融", "first_home_down_pct", "首套首付比例_pct", "采"),
    ("二十一·政策与金融", "second_home_down_pct", "二套首付比例_pct", "采"),
    ("二十一·政策与金融", "purchase_restriction", "限购政策摘要", "采"),
    ("二十一·政策与金融", "policy_update_date", "政策更新日期", "采"),
    # 二十二、二手房市场（全国扩展，目前仅济南）
    ("二十二·二手房市场", "resale_listing_price", "二手房挂牌均价_元每㎡", "采"),
    ("二十二·二手房市场", "resale_transaction_price", "二手房成交均价_元每㎡", "采"),
    ("二十二·二手房市场", "resale_dom", "二手房挂牌天数", "采"),
    ("二十二·二手房市场", "resale_sp_lp_ratio", "二手房挂牌成交比_pct", "算"),
    ("二十二·二手房市场", "resale_price_trend_6m", "二手房价趋势_6月_pct", "算"),
    ("二十二·二手房市场", "resale_data_source", "二手房数据来源", "采"),
    # 二十三、人口与产业（区域价值评估基本面）
    ("二十三·人口与产业", "district_population", "区域常住人口_万", "采"),
    ("二十三·人口与产业", "population_net_inflow", "人口净流入_万每年", "采"),
    ("二十三·人口与产业", "population_median_age", "人口年龄中位数", "采"),
    ("二十三·人口与产业", "district_gdp", "区域GDP_亿元", "采"),
    ("二十三·人口与产业", "tertiary_industry_pct", "第三产业占比_pct", "采"),
    ("二十三·人口与产业", "dominant_industry", "区域主导产业", "采"),
    ("二十三·人口与产业", "employment_population", "就业人口_万", "采"),
    ("二十三·人口与产业", "disposable_income", "人均可支配收入_万元", "采"),
]

# ── taxonomy key → 既有 56 列中文列名（语义等价，避免重复造列） ───────────────────
# 凡未列于此处的 key 即为「新增列」，物理列名取其中文名。
KEY_TO_LEGACY = {
    "id": "楼盘ID",
    "name": "楼盘名称",
    "tags": "标签列表",
    "default_photo": "默认图片",
    "city_id": "城市ID",
    "city_name": "城市名称",
    "area_id": "区域ID",
    "area_name": "区域名称",
    "trading_area_id": "子区域ID",
    "trading_area_name": "子区域名称",
    "address": "地址",
    "ring_location": "环线位置",
    "blng": "百度地图经度",
    "blat": "百度地图纬度",
    "price_info_price": "最新价格",
    "price_ref": "参考价格",
    "mortgage_info": "房贷计算信息",
    "build_type_str": "建筑类型",
    "extend_type": "物业类型",
    "years_of_property_rights": "产权年限",
    "plot_ratio": "容积率",
    "extend_landscaping_atio": "绿化率",
    "land_area": "占地面积",
    "build_area": "建筑面积",
    "area_range": "面积范围",
    "extend_total_house_hold_num": "规划户数",
    "decoration": "装修情况",
    "units_all": "全部户型",
    "room_area_info": "房间面积信息",
    "parking_space": "车位数",
    "parking_rate": "车位比",
    "open_date": "开盘日期",
    "open_date_note": "开盘日期备注",
    "open_time": "开盘时间",
    "handover_date": "交房时间",
    "cert_date": "发证时间",
    "presale_cert": "预售证号",
    "progress": "工程进度",
    "bound_buildings": "绑定楼栋",
    "planning_delivery_info": "规交信息",
    "developers": "开发商",
    "investor": "投资商",
    "extend_property_company": "物业公司",
    "extend_property_money": "物业管理费",
    "extend_property_features": "物业特色",
    "sales_office_addr": "售楼处地址",
    "phone_400": "400电话",
    "sale_status": "销售状态",
    "sale_title": "销售标题",
    "rent_sale_title": "租售标题",
}

# 既有 56 列里、taxonomy 未单独建 key 的「遗留专属列」（保留，勿删）：
#   户型文本描述, 建筑类型(第二个,重复), 开发商品牌, 供电, 供水, 所有标签列表


def physical_col(key: str) -> str:
    """taxonomy key → 该字段在 CSV 中的物理列名。"""
    if key in KEY_TO_LEGACY:
        return KEY_TO_LEGACY[key]
    for _cat, k, cn, _src in TAXONOMY:
        if k == key:
            return cn
    raise KeyError(f"未知 taxonomy key: {key}")


# 新增列（taxonomy 中未映射到 legacy 的字段，按 taxonomy 顺序）
NEW_COLUMNS = [cn for _cat, k, cn, _src in TAXONOMY if k not in KEY_TO_LEGACY]

# 完整 canonical 列顺序 = 56 legacy + 新增列
FULL_HEADERS = LEGACY_HEADERS + NEW_COLUMNS


def build_schema_records():
    """生成 schema 定义记录（供写 JSON）。"""
    recs = []
    for cat, key, cn, src in TAXONOMY:
        phys = physical_col(key)
        recs.append({
            "category": cat,
            "key": key,
            "chinese": cn,
            "source": src,
            "physical_column": phys,
            "is_new_column": key not in KEY_TO_LEGACY,
        })
    return recs


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    # 校验：新增列不得与 legacy 列重名
    dup = [c for c in NEW_COLUMNS if c in LEGACY_HEADERS]
    assert not dup, f"新增列与 legacy 列重名: {dup}"
    # 校验：FULL_HEADERS 中新增列无重复（legacy 的「建筑类型」重复是历史允许的）
    assert len(NEW_COLUMNS) == len(set(NEW_COLUMNS)), "新增列内部有重复"

    print(f"LEGACY 列数      : {len(LEGACY_HEADERS)}")
    print(f"taxonomy 维度数  : {len(TAXONOMY)}")
    print(f"映射到 legacy    : {len(KEY_TO_LEGACY)}")
    print(f"新增列数         : {len(NEW_COLUMNS)}")
    print(f"FULL 列数        : {len(FULL_HEADERS)}")
    print(f"新增列: {NEW_COLUMNS}")

    out = Path(__file__).resolve().parent.parent / "data" / "loupan_schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "2.0",
        "description": "DDS 楼盘完整维度 canonical schema v2.0（additive 超集，零 breakage；对标 CRIC 极客问道 162 字段 + 地产经纪人审查补遗；23 大类 242 维）",
        "legacy_headers": LEGACY_HEADERS,
        "full_headers": FULL_HEADERS,
        "new_columns": NEW_COLUMNS,
        "n_legacy": len(LEGACY_HEADERS),
        "n_full": len(FULL_HEADERS),
        "n_taxonomy": len(TAXONOMY),
        "fields": build_schema_records(),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出 schema 定义: {out}")

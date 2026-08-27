"""Add fourth-generation housing benchmark cases to the library."""
import json, sys
from pathlib import Path

lib_path = Path(__file__).resolve().parent.parent / "data" / "benchmark_library.json"
lib = json.loads(lib_path.read_text(encoding="utf-8"))
cases = lib.get("cases", [])

fourth_gen = [
    {
        "id": "chengdu_qiyun_4th",
        "name": "成都七一城市森林花园",
        "developer": "七一置业",
        "designer": "清华大学建筑设计研究院",
        "city": "成都",
        "city_tier": 1.5,
        "year": 2019,
        "tier": "品质改善",
        "product_type": "四代住宅·空中花园高层",
        "headline": "中国首个交付第四代住宅——空中庭院+垂直森林",
        "positioning_tags": ["四代宅", "空中花园", "垂直绿化", "错层露台", "立体生态"],
        "scores": {"demo_zone": 7.5, "floorplan": 9.0, "podium_lift": 8.0, "display_zone": 7.0, "sunken_club": 6.5, "policy_leverage": 9.5, "open_floor": 9.5, "landscape": 9.2, "facade": 8.8, "brand_ops": 7.0},
        "moves": ["奇偶错层空中花园:每户赠送30-50㎡露台挑高6m", "垂直森林体系:种植乔木+灌木+藤蔓空中庭院可种树", "自动灌溉+防水排水系统:解决空中花园养护和渗漏痛点", "政策红利最大化:空中花园不计容/半计容实现100%+得房率"],
        "key_tactics": ["错层露台", "空中庭院", "垂直绿化", "高赠送率", "政策红利"],
        "awards": ["中国首个第四代住宅交付项目"],
        "data_quality": "case_study", "confidence": 0.85,
        "sources": [{"title": "成都七一森林花园-第四代住宅实践与探索", "url": "https://www.archdaily.cn/cn/953214"}],
        "category": "现象级产品",
        "layer2_ops": ["错层露台结构", "空中花园防水", "垂直绿化养护"],
        "layer3_proof": ["实景交付照片", "住户满意度调查", "二手房溢价数据"],
        "signature_dims": ["四代宅", "空中花园", "垂直绿化"],
        "proof_evidence": "2019年交付二手房均价比周边高15-20%"
    },
    {
        "id": "wuhan_longfor_4th",
        "name": "武汉龙湖·空中院子",
        "developer": "龙湖集团",
        "designer": "基准方中建筑设计",
        "city": "武汉",
        "city_tier": 1.5,
        "year": 2023,
        "tier": "高端改善",
        "product_type": "四代住宅·洋房+小高",
        "headline": "低密四代宅标杆——奇偶错层露台+独立入户花园",
        "positioning_tags": ["四代宅", "错层露台", "洋房", "独立入户", "龙湖"],
        "scores": {"demo_zone": 8.5, "floorplan": 9.2, "podium_lift": 7.5, "display_zone": 8.0, "sunken_club": 8.0, "policy_leverage": 9.0, "open_floor": 9.3, "landscape": 9.0, "facade": 8.5, "brand_ops": 8.5},
        "moves": ["洋房+小高全系四代宅:奇偶层错层露台每户独立空中花园", "3-4F洋房独立电梯入户+首层花园叠墅体验平层化", "立面绿化系统:露台种植槽+垂直绿墙+屋顶花园三重绿化", "龙湖品牌+物业赋能:空中花园统一养护解决业主维护顾虑"],
        "key_tactics": ["奇偶错层", "洋房四代化", "独立入户", "统一养护"],
        "awards": [],
        "data_quality": "case_study", "confidence": 0.80,
        "sources": [{"title": "龙湖空中院子-武汉第四代住宅实践", "url": "https://www.gooood.cn/"}],
        "category": "现象级产品",
        "layer2_ops": ["错层露台结构设计", "露台防水与排水", "空中花园物业养护"],
        "layer3_proof": ["开盘去化数据", "客户购买动机调研"],
        "signature_dims": ["四代宅", "空中花园", "独立入户"],
        "proof_evidence": "龙湖品牌+第四代住宅概念开盘去化率>80%"
    },
    {
        "id": "fuzhou_rongqiao_4th",
        "name": "福州融侨·天空森林",
        "developer": "融侨集团",
        "designer": "同济大学建筑设计研究院",
        "city": "福州",
        "city_tier": 2.0,
        "year": 2023,
        "tier": "高端改善",
        "product_type": "四代住宅·台地洋房+小高",
        "headline": "台地四代宅——利用坡地高差实现层层退台+户户花园",
        "positioning_tags": ["四代宅", "台地", "退台", "层层花园", "坡地"],
        "scores": {"demo_zone": 8.2, "floorplan": 9.0, "podium_lift": 8.0, "display_zone": 8.0, "sunken_club": 7.8, "policy_leverage": 9.0, "open_floor": 9.2, "landscape": 9.5, "facade": 8.5, "brand_ops": 7.5},
        "moves": ["利用坡地高差:层层退台+每户大露台退台花园不计容", "山地四代宅:坡地减少开挖台地式布局与四代宅完美结合", "户型定制化:不同标高层不同户型策略最大化景观溢价", "立面植被+挡墙绿化:退台花园+台地挡墙形成立体绿化系统"],
        "key_tactics": ["坡地台地", "层层退台", "植被立面", "退台不计容"],
        "awards": [],
        "data_quality": "case_study", "confidence": 0.80,
        "sources": [{"title": "融侨天空森林-台地上的第四代住宅", "url": "https://www.tjad.cn/"}],
        "category": "现象级产品",
        "layer2_ops": ["台地+退台结构", "坡地排水", "退台花园防水"],
        "layer3_proof": ["项目实景交付", "台地四代宅得房率数据"],
        "signature_dims": ["四代宅", "台地", "退台", "层层花园"],
        "proof_evidence": "台地地形+四代宅叠加得房率可达110%+"
    },
    {
        "id": "changsha_zhonghai_4th",
        "name": "长沙中海·天空之院",
        "developer": "中海地产",
        "designer": "上海天华建筑设计",
        "city": "长沙",
        "city_tier": 2.0,
        "year": 2024,
        "tier": "品质改善",
        "product_type": "四代住宅·高层+小高",
        "headline": "中海四代宅首作——6m挑高空中庭院+LDKG一体化",
        "positioning_tags": ["四代宅", "LDKG", "挑高露台", "中海", "空中庭院"],
        "scores": {"demo_zone": 8.0, "floorplan": 8.8, "podium_lift": 7.0, "display_zone": 7.5, "sunken_club": 7.5, "policy_leverage": 8.5, "open_floor": 9.0, "landscape": 8.8, "facade": 8.0, "brand_ops": 8.0},
        "moves": ["6m挑高露台:客厅+主卧双露台LDKG一体化贯通室内外", "140-180㎡全四房:每个卧室都有阳台/露台居住平权", "空中花园错层设计:奇偶层露台位置交替保证隐私", "中海精装体系升级:露台防水/排水/防根穿刺纳入精装标准"],
        "key_tactics": ["6m挑高露台", "LDKG", "全卧室阳台", "露台精装标准化"],
        "awards": [],
        "data_quality": "case_study", "confidence": 0.80,
        "sources": [{"title": "中海天空之院-长沙第四代住宅", "url": "https://www.zhonghai.com/"}],
        "category": "现象级产品",
        "layer2_ops": ["6m挑高结构", "LDKG空间组织", "露台精装标准"],
        "layer3_proof": ["户型客户满意度"],
        "signature_dims": ["四代宅", "LDKG", "挑高露台"],
        "proof_evidence": "中海2024年产品系升级四代宅成为主力方向"
    },
]

existing = {c["id"] for c in cases}
added = 0
for c in fourth_gen:
    if c["id"] not in existing:
        cases.append(c)
        existing.add(c["id"])
        added += 1

lib["meta"]["total_cases"] = len(cases)
lib_path.write_text(json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Added {added} 四代宅 cases. Total: {len(cases)}")

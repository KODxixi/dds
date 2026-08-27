"""
DDS ABM 引擎 v1

从"4 个写死 persona + 硬编码加分"升级到：
  1. 真分布抽样（蒙特卡洛 1000 虚拟人格 / 城）
  2. MNL Random Utility 决策模型
  3. WTP 支付意愿分布 + 去化曲线
  4. 城市分池（三亚 / 杭州 / 上海 / 青岛）

零外部依赖，纯标准库。极简主义。
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field, asdict


# ============================================================
# 1. 客群原型 archetype（每城自定）
# ============================================================
# 每城客群池由 N 个 archetype 加权组成，archetype 定义：
#   - 权重（占比）
#   - 收入分布参数（均值 / 标准差，万元/年）
#   - 资产倍数（资产 ≈ 年收入 × 倍数）
#   - 杠杆能力（首付承受比，1.0=全款）
#   - 年龄区间
#   - 偏好向量（户型 / 景观 / 学校 / 通勤 / 品牌 / 圈层 / 私密）
#   - 风险厌恶基线
@dataclass
class Archetype:
    name: str
    weight: float           # 占比
    income_mu: float        # 年收入均值（万）
    income_sigma: float     # 年收入标准差
    asset_multiplier: float # 总资产 = 年收入 × 该倍数（家庭累积）
    dp_ratio: float         # 首付比例，0.3=三成（高杠杆），1.0=全款（最保守）
    age_range: tuple[int, int]
    pref: dict[str, float] # 偏好权重 0..1
    risk_aversion: float    # 0..1，越大越保守
    # ↓ 加厚维度（带默认值，向后兼容）
    family_size: float = 3.0          # 平均家庭人数
    kids: float = 0.8                 # 平均孩子数 0..3
    elderly_cohabit: float = 0.2      # 老人同住概率 0..1
    social_class: str = "B"           # A=顶层 / B=中产 / C=工薪 / D=刚需
    info_channel: str = "agent"       # social/agent/network/research
    decision_horizon_months: float = 3.0  # 决策周期（月），影响紧迫性


# 四城客群池配置（基于公开统计 + 行业认知，可后续接七普精修）
# 参数：年收入μ/σ 万 · 资产倍数 · 首付比例 dp · 年龄区间 · 偏好 · 风险厌恶
CITY_POOLS: dict[str, list[Archetype]] = {
    "三亚": [
        Archetype("外地候鸟-华北", 0.25, 25, 10, 8, 0.5,
                  (55, 75), {"景观": 0.95, "私密": 0.7, "圈层": 0.6, "户型": 0.5, "通勤": 0.0, "学校": 0.0, "品牌": 0.55}, 0.55),
        Archetype("外地候鸟-东北", 0.15, 20, 8, 7, 0.5,
                  (58, 78), {"景观": 0.95, "私密": 0.7, "圈层": 0.5, "户型": 0.5, "通勤": 0.0, "学校": 0.0, "品牌": 0.5}, 0.6),
        Archetype("外地候鸟-西北", 0.10, 22, 10, 8, 0.5,
                  (55, 75), {"景观": 0.9, "私密": 0.65, "圈层": 0.5, "户型": 0.5, "通勤": 0.0, "学校": 0.0, "品牌": 0.5}, 0.6),
        Archetype("度假投资", 0.25, 50, 20, 10, 0.4,
                  (35, 55), {"景观": 0.85, "私密": 0.6, "圈层": 0.55, "户型": 0.45, "通勤": 0.0, "学校": 0.0, "品牌": 0.75}, 0.4),
        Archetype("本地改善", 0.15, 15, 6, 6, 0.35,
                  (30, 50), {"景观": 0.6, "私密": 0.45, "圈层": 0.4, "户型": 0.75, "通勤": 0.5, "学校": 0.7, "品牌": 0.45}, 0.55),
        Archetype("高净值康养", 0.10, 150, 70, 12, 0.6,
                  (50, 70), {"景观": 0.95, "私密": 0.95, "圈层": 0.85, "户型": 0.6, "通勤": 0.0, "学校": 0.0, "品牌": 0.9}, 0.3),
    ],
    "杭州": [
        Archetype("互联网工程师", 0.35, 55, 22, 6, 0.3,
                  (28, 40), {"景观": 0.5, "私密": 0.45, "圈层": 0.55, "户型": 0.8, "通勤": 0.85, "学校": 0.7, "品牌": 0.6}, 0.5),
        Archetype("本地改善", 0.25, 35, 15, 9, 0.4,
                  (35, 55), {"景观": 0.55, "私密": 0.5, "圈层": 0.5, "户型": 0.85, "通勤": 0.7, "学校": 0.75, "品牌": 0.55}, 0.5),
        Archetype("浙商投资", 0.20, 120, 60, 10, 0.5,
                  (40, 60), {"景观": 0.6, "私密": 0.55, "圈层": 0.7, "户型": 0.5, "通勤": 0.3, "学校": 0.4, "品牌": 0.85}, 0.45),
        Archetype("学区刚需", 0.15, 30, 12, 7, 0.3,
                  (30, 45), {"景观": 0.3, "私密": 0.3, "圈层": 0.4, "户型": 0.7, "通勤": 0.6, "学校": 0.95, "品牌": 0.5}, 0.55),
        Archetype("人才落户", 0.05, 45, 18, 5, 0.3,
                  (28, 38), {"景观": 0.4, "私密": 0.35, "圈层": 0.5, "户型": 0.75, "通勤": 0.85, "学校": 0.6, "品牌": 0.55}, 0.6),
    ],
    "上海": [
        Archetype("金融精英", 0.30, 90, 40, 8, 0.4,
                  (32, 50), {"景观": 0.65, "私密": 0.6, "圈层": 0.75, "户型": 0.75, "通勤": 0.7, "学校": 0.8, "品牌": 0.8}, 0.45),
        Archetype("外籍海归", 0.25, 70, 30, 7, 0.4,
                  (30, 45), {"景观": 0.6, "私密": 0.55, "圈层": 0.8, "户型": 0.7, "通勤": 0.65, "学校": 0.85, "品牌": 0.75}, 0.5),
        Archetype("本地改善", 0.20, 40, 18, 10, 0.45,
                  (38, 58), {"景观": 0.55, "私密": 0.5, "圈层": 0.55, "户型": 0.85, "通勤": 0.6, "学校": 0.7, "品牌": 0.6}, 0.5),
        Archetype("教育型买房", 0.15, 55, 22, 8, 0.4,
                  (32, 48), {"景观": 0.4, "私密": 0.4, "圈层": 0.6, "户型": 0.7, "通勤": 0.55, "学校": 0.98, "品牌": 0.6}, 0.5),
        Archetype("长三角家庭", 0.10, 45, 18, 7, 0.45,
                  (35, 55), {"景观": 0.55, "私密": 0.5, "圈层": 0.5, "户型": 0.8, "通勤": 0.4, "学校": 0.7, "品牌": 0.65}, 0.55),
    ],
    "青岛": [
        Archetype("本地改善", 0.45, 22, 10, 8, 0.4,
                  (35, 55), {"景观": 0.75, "私密": 0.5, "圈层": 0.5, "户型": 0.85, "通勤": 0.6, "学校": 0.7, "品牌": 0.55}, 0.5),
        Archetype("山东省内异地", 0.25, 25, 12, 8, 0.45,
                  (40, 60), {"景观": 0.85, "私密": 0.55, "圈层": 0.5, "户型": 0.7, "通勤": 0.0, "学校": 0.4, "品牌": 0.55}, 0.55),
        Archetype("北方度假投资", 0.15, 45, 20, 10, 0.5,
                  (45, 65), {"景观": 0.95, "私密": 0.6, "圈层": 0.5, "户型": 0.5, "通勤": 0.0, "学校": 0.0, "品牌": 0.7}, 0.5),
        Archetype("学区刚需", 0.10, 20, 8, 6, 0.3,
                  (30, 45), {"景观": 0.4, "私密": 0.3, "圈层": 0.4, "户型": 0.75, "通勤": 0.55, "学校": 0.95, "品牌": 0.45}, 0.55),
        Archetype("高端海景收藏", 0.05, 130, 60, 11, 0.6,
                  (45, 65), {"景观": 0.98, "私密": 0.85, "圈层": 0.75, "户型": 0.55, "通勤": 0.0, "学校": 0.0, "品牌": 0.85}, 0.35),
    ],
    # 济南：基于2026年客研226份问卷 + 长岭山E15可研报告
    # 主力客群: 31-45岁高知中产(本科69%+研究生7%), 金融13%+IT12%+生物医药10%+能源9%
    # 家庭年收入: 20-40万占64%, 55%夫妻带孩, 30%三代同堂
    # 职住锚点: 长岭山/奥体/CBD/汉峪金谷 承载63%工作人口
    "济南": [
        Archetype("高新产城精英", 0.30, 38, 14, 7, 0.35,
                  (28, 40), {"户型": 0.82, "通勤": 0.80, "学校": 0.75, "品牌": 0.65, "景观": 0.55, "私密": 0.50, "圈层": 0.55}, 0.50),
        Archetype("本地品质改善", 0.30, 32, 12, 8, 0.40,
                  (36, 50), {"户型": 0.88, "学校": 0.72, "品牌": 0.68, "景观": 0.60, "私密": 0.55, "通勤": 0.55, "圈层": 0.50}, 0.55),
        Archetype("学区家庭", 0.20, 28, 10, 6, 0.30,
                  (30, 42), {"学校": 0.95, "户型": 0.78, "通勤": 0.65, "品牌": 0.55, "私密": 0.35, "景观": 0.35, "圈层": 0.40}, 0.55),
        Archetype("三代同堂改善", 0.12, 35, 14, 9, 0.42,
                  (38, 55), {"户型": 0.85, "私密": 0.70, "学校": 0.65, "品牌": 0.62, "景观": 0.58, "通勤": 0.45, "圈层": 0.55}, 0.52),
        Archetype("省内迁入精英", 0.08, 40, 16, 6, 0.35,
                  (30, 42), {"通勤": 0.78, "户型": 0.75, "品牌": 0.70, "学校": 0.62, "圈层": 0.55, "景观": 0.48, "私密": 0.42}, 0.48),
    ],
}

# 限购政策硬规则（简化版，可后续精修）
PURCHASE_LIMITS = {
    "三亚": {"non_local_social_years": 5, "max_units_local": 2, "max_units_non_local": 1},
    "杭州": {"non_local_social_years": 4, "max_units_local": 2, "max_units_non_local": 1},
    "上海": {"non_local_social_years": 5, "max_units_local": 2, "max_units_non_local": 1},
    "青岛": {"non_local_social_years": 0, "max_units_local": 3, "max_units_non_local": 2},
    "济南": {"non_local_social_years": 2, "max_units_local": 2, "max_units_non_local": 1},
}

# Archetype 加厚维度（短键: fs=家庭规模, k=孩子数, eld=老人同住率,
#                    cls=社会阶层, ch=信息渠道, dh=决策周期月）
_DIM_KEYS = ("family_size", "kids", "elderly_cohabit",
             "social_class", "info_channel", "decision_horizon_months")
ARCHETYPE_META = {
    # 三亚
    "外地候鸟-华北": (2, 0, 0.10, "B", "network", 6),
    "外地候鸟-东北": (2, 0, 0.05, "C", "network", 8),
    "外地候鸟-西北": (2, 0, 0.10, "C", "network", 8),
    "度假投资":     (3, 1, 0.15, "A", "research", 2),
    "本地改善":     (4, 1, 0.50, "B", "agent", 3),
    "高净值康养":   (2, 0, 0.10, "A", "network", 4),
    # 杭州
    "互联网工程师": (3, 1, 0.30, "B", "social", 2),
    "浙商投资":     (4, 1, 0.20, "A", "network", 2),
    "学区刚需":     (3, 1, 0.30, "C", "agent", 1),
    "人才落户":     (2, 0, 0.15, "B", "research", 2),
    # 上海
    "金融精英":     (3, 1, 0.20, "A", "research", 3),
    "外籍海归":     (3, 1, 0.15, "A", "social", 3),
    "教育型买房":   (3, 1, 0.40, "B", "social", 1),
    "长三角家庭":   (4, 2, 0.45, "B", "agent", 3),
    # 青岛
    "山东省内异地": (3, 1, 0.35, "C", "network", 4),
    "北方度假投资": (2, 0, 0.10, "B", "research", 3),
    "高端海景收藏": (2, 0, 0.10, "A", "network", 4),
    # 济南（2026年客研226份问卷校准）
    "高新产城精英": (3, 0.8, 0.25, "B", "social", 2),
    "本地品质改善": (3.3, 1.0, 0.35, "B", "agent", 3),
    "学区家庭": (3.5, 1.2, 0.30, "C", "research", 1),
    "三代同堂改善": (4.2, 1.3, 0.50, "B", "agent", 3),
    "省内迁入精英": (2.8, 0.7, 0.20, "B", "social", 2),
}


def _apply_archetype_meta():
    """运行一次：把 ARCHETYPE_META 注入对应 Archetype 实例"""
    for pool in CITY_POOLS.values():
        for a in pool:
            meta = ARCHETYPE_META.get(a.name)
            if meta:
                for k, v in zip(_DIM_KEYS, meta):
                    setattr(a, k, v)


_apply_archetype_meta()


def _apply_real_calibration(alpha: float = 0.5):
    """把真实市场信号校准（`data/archetype_real_calibration.json`，由
    `calibrate_archetypes_real.py` 从真实在售盘标签推导）按 α×漂移注入每城 archetype 偏好，
    使 ABM 客群偏好接地于真实供给信号而非纯专家预设。文件缺失/异常静默回退预设（fallback-safe）。
    供给侧接地；需求侧（真实成交）需网签数据。"""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" / "archetype_real_calibration.json"
        if not p.exists():
            return 0
        calib = json.loads(p.read_text(encoding="utf-8")).get("cities", {})
        n = 0
        for city, info in calib.items():
            drift = info.get("drift") or {}
            for a in CITY_POOLS.get(city, []):
                for d, dv in drift.items():
                    if d in a.pref:
                        a.pref[d] = round(min(1.0, max(0.0, a.pref[d] + alpha * dv)), 3)
                n += 1
        return n
    except Exception:
        return 0


_REAL_CALIB_APPLIED = _apply_real_calibration()


# ============================================================
# 2. 虚拟人格抽样
# ============================================================
@dataclass
class Persona:
    archetype: str
    age: int
    annual_income: float    # 万/年
    total_asset: float      # 万
    dp_ratio: float         # 首付比例
    pref: dict[str, float]
    risk_aversion: float
    budget_ceiling: float   # 万，可承受总房款上限
    # ↓ 加厚维度
    family_size: int = 3
    kids: int = 1
    elderly_cohabit: bool = False
    social_class: str = "B"
    info_channel: str = "agent"
    decision_urgency: float = 0.5  # 0..1，越大越急迫（影响推迟决策概率）
    # ↓ 10 大高维语义中文加厚字段
    job_detail: str = ""              # 细分职业
    life_stage: str = ""              # 家庭生命周期阶段
    current_living: str = ""          # 目前居住状况
    living_pain: str = ""             # 目前居住痛点
    detail_need: str = ""             # 核心看房细节需求
    commute_pref: str = ""            # 职住通勤偏好
    lifestyle_pref: str = ""          # 生活方式偏好
    funds_source: str = ""            # 首付资金来源
    brand_service_need: str = ""      # 品牌与服务诉求
    vacation_frequency: str = ""      # 旅居度假频次



def _truncnorm(mu: float, sigma: float, lo: float = 0.5) -> float:
    """截断正态：拒绝采样，避免收入为负或过低"""
    while True:
        v = random.gauss(mu, sigma)
        if v >= lo:
            return v


def load_personas_from_vault(city: str, target_year: int) -> list[Persona] | None:
    """从物理 Vault 目录加载指定年份的客群样本 CSV"""
    from pathlib import Path
    import csv
    _root = Path(__file__).resolve().parent.parent
    csv_path = _root / "Vault" / f"{target_year}年" / f"客群样本-{city}.csv"
    if not csv_path.exists():
        return None
    try:
        personas = []
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 提取偏好
                pref = {}
                for k in ["景观", "私密", "圈层", "户型", "通勤", "学校", "品牌"]:
                    val = row.get(f"偏好_{k}")
                    pref[k] = float(val) if val is not None else 0.5
                
                personas.append(Persona(
                    archetype=row.get("客群类型", ""),
                    age=int(row.get("年龄", 40)),
                    annual_income=float(row.get("年收入_万", 20)),
                    total_asset=float(row.get("总资产_万", 150)),
                    dp_ratio=float(row.get("首付比例", 0.4)),
                    pref=pref,
                    risk_aversion=float(row.get("风险厌恶", 0.5)),
                    budget_ceiling=float(row.get("预算上限_万", 300)),
                    family_size=int(row.get("家庭人数", 3)),
                    kids=int(row.get("孩子数", 1)),
                    elderly_cohabit=(row.get("老人同住") == "是"),
                    social_class=row.get("社会阶层", "B"),
                    info_channel=row.get("信息渠道", "agent"),
                    decision_urgency=float(row.get("决策紧迫度", 0.5)),
                    # ↓ 10 大高维语义中文字段直读反序列化
                    job_detail=row.get("细分职业", ""),
                    life_stage=row.get("家庭生命周期阶段", ""),
                    current_living=row.get("目前居住状况", ""),
                    living_pain=row.get("目前居住痛点", ""),
                    detail_need=row.get("核心看房细节需求", ""),
                    commute_pref=row.get("职住通勤偏好", ""),
                    lifestyle_pref=row.get("生活方式偏好", ""),
                    funds_source=row.get("首付资金来源", ""),
                    brand_service_need=row.get("品牌与服务诉求", ""),
                    vacation_frequency=row.get("旅居度假频次", "")
                ))
        return personas
    except Exception as e:
        print(f"[warn] 加载 {city} {target_year}年 物理客群样本失败: {e}，将退化为动态合成")
        return None


# ============================================================
# 语义加厚：DDS 城市客群原型微观中文高保真字段大数据库
# ============================================================
ARCHETYPE_SEMANTICS = {
    "三亚": {
        "外地候鸟-华北": {
            "job_detail": ["北京高校退休教授", "天津国企退休高管", "石家庄三甲医院主任医师"],
            "life_stage": ["老年空巢家庭，冬季南下避寒，注重健康管理与天然氧吧", "祖孙三代冬季避寒旅居，孙辈寒假随同长住"],
            "current_living": ["北方城市暖气房，楼楼栋较老无电梯，日常冬季空气干燥"],
            "living_pain": ["冬季寒冷干燥且偶有雾霾，关节炎/心脑血管负担重，日常缺乏优质绿色景观和户外休闲空间"],
            "detail_need": ["必须带朝南大阳台，全屋无障碍设计（防滑扶手/无门槛），卫生间干湿分离且通风良好"],
            "commute_pref": ["不考虑日常通勤，但要求15分钟车程内必有三甲医院或康养中心，步行可达生鲜超市"],
            "lifestyle_pref": ["清晨海边散步，午后阳台喝茶看报，傍晚与老邻里打太极或跳广场舞"],
            "funds_source": ["北方城市旧房出租租金支持，家庭多年公积金与退休储蓄组合"],
            "brand_service_need": ["要求提供24小时急救绿色通道响应，品牌物业，代办日常缴费与家政保洁"],
            "vacation_frequency": ["每年11月至次年4月稳定居住5个月，其余时间返回北方原住址"]
        },
        "外地候鸟-东北": {
            "job_detail": ["哈尔滨退休公务员", "沈阳制造业私企老板", "长春高校退休教师"],
            "life_stage": ["退休夫妇，随季候规律迁徙，追求面朝大海的避寒晚年生活", "大家庭抱团度假，亲属间多有在同区域购房倾向"],
            "current_living": ["东北老工业城市商品房，多层无电梯，社区绿化单一"],
            "living_pain": ["冬季极寒路滑不便出门，北方大工业城市居住密度高，冬季蔬菜生鲜物价高且种类少"],
            "detail_need": ["客厅面宽不低于4米，要求南北通透，厨房采光好，主卧带大飘窗"],
            "commute_pref": ["完全脱离通勤，要求紧邻公交终点站或轻轨站，方便去往海滩和市区大型菜市场"],
            "lifestyle_pref": ["下午吃海鲜，结伴在海边垂钓，在小区凉亭与东北老乡下棋唠嗑"],
            "funds_source": ["东北两套旧房置换其一，儿女共同出资孝敬养老"],
            "brand_service_need": ["首选具有北方知名度的物业品牌，要求冬季防寒管线维护服务及入户水质过滤"],
            "vacation_frequency": ["每年10月底大雪前南下，次年清明节后北归，年均旅居半年左右"]
        },
        "外地候鸟-西北": {
            "job_detail": ["西安油田退休干部", "兰州科研院所退休工程师", "太原煤炭企业前高管"],
            "life_stage": ["中老年夫妇，呼吸道较为敏感，南下进行疗养长住"],
            "current_living": ["西北城市高层住宅，气候风沙重，冬季虽有暖气但室内异常干燥"],
            "living_pain": ["风沙重干燥，冬季户外活动受限，极度渴望湿润空气与椰林沙滩"],
            "detail_need": ["全屋通风优异，有防潮建材防潮处理，配有新风或空气净化过滤系统"],
            "commute_pref": ["不考虑通勤，要求靠近公园与湖泊，10分钟车内有成熟生活商圈"],
            "lifestyle_pref": ["椰林小道慢跑，尝试海岛热带水果种植，与旅居老友相聚品茶"],
            "funds_source": ["公积金积蓄 + 西北城市房产出租性收益支持"],
            "brand_service_need": ["高水准防潮物业打理，离岛期间提供定期入户除湿、通风及绿植养护"],
            "vacation_frequency": ["每年11月至次年3月集中长住，夏季偶尔返三亚避暑"]
        },
        "度假投资": {
            "job_detail": ["北京科技公司副总裁", "深圳创投基金合伙人", "上海连锁餐饮创始人"],
            "life_stage": ["中年改善家庭，兼顾财富资产保值与全家冬季海岛度假、招待友人需求"],
            "current_living": ["一线城市核心区高档公寓，快节奏高压力"],
            "living_pain": ["原有环境工作压力极大，身心处于亚健康状态，急需海岛天然高浓度氧吧进行自我疗愈"],
            "detail_need": ["主卧套房必须直面一线海景或山景，带270度环幕大平层大阳台，带独立储藏室放冲浪度假装备"],
            "commute_pref": ["要求20分钟车程内可达三亚凤凰机场或高铁站，周边有高尔夫球场或游艇码头配套"],
            "lifestyle_pref": ["清晨冲浪或游艇出海，午后在五星级酒店品尝下午茶，深夜露台鸡尾酒派对"],
            "funds_source": ["企业年度分红与股权套现，多套一二线资产置换腾挪现金"],
            "brand_service_need": ["极致尊崇的管家式物业服务，离岛期间提供全权托管租赁或顶级空置维护"],
            "vacation_frequency": ["年均南下度假3-4次，每次居住1-2周，春节假期整月长住"]
        },
        "本地改善": {
            "job_detail": ["三亚当地高星级酒店中高管", "三亚本地免税零售企业部门经理", "当地海产物流老板"],
            "life_stage": ["本地三口或四口之家，孩子正读小学或初中，追求本地区域居住品质升级"],
            "current_living": ["当地老旧自建房或早期安置房，社区老旧无地下车库，无物管"],
            "living_pain": ["台风季节顶楼渗水，人车混行极度不安全，社区缺乏绿化和儿童活动区"],
            "detail_need": ["必须带入户玄关收纳雨具，客厅层高不低于3米，主卧套房设计保证夫妻私密"],
            "commute_pref": ["要求30分钟内通勤至市中心政府机构或海棠湾免税城，周边3公里内有优质省重点分校"],
            "lifestyle_pref": ["接送孩子上下学，周末带全家在免税城或本地公园露营，重视家庭聚会"],
            "funds_source": ["本地旧房售出变现 + 当地组合贷款，家庭年储蓄支付月供"],
            "brand_service_need": ["极佳的防台风安全物业保障，人车分流，高品质的儿童游乐区与园林绿化维护"],
            "vacation_frequency": ["本地常住居民，每年仅有1-2周在省内或外地出差旅游"]
        },
        "高净值康养": {
            "job_detail": ["江浙沪知名私营企业家", "北京退休金融大佬", "大型上市公司前董事长"],
            "life_stage": ["老年富裕阶层，极为关注私人健康调理、抗衰管理及极端纯净的物理居住环境"],
            "current_living": ["核心城市顶奢独栋别墅，虽豪华但缺乏长期的冬季温润气候"],
            "living_pain": ["冬季一线大都市雾霾严重且气温多变，气管炎与风湿多发，寻觅顶奢海岛养生胜地"],
            "detail_need": ["独立电梯直达，带私家泳池，全套房奢华设计，主卫配独立双人按摩浴缸，配专业医疗呼叫器"],
            "commute_pref": ["完全无需通勤，但要求5公里内有解放军总医院海南医院等顶尖医疗资源"],
            "lifestyle_pref": ["顶级康养中心抗衰理疗，在私家露台阅读，与同层企业家在会所雪茄吧交流"],
            "funds_source": ["上市公司股权分红及家族信托理财收益支持，全款买入"],
            "brand_service_need": ["提供1对1专属健康管家，五星级酒店级代步接送与顶级医疗绿色通道直达服务"],
            "vacation_frequency": ["每年11月至次年3月稳定南下康养4-5个月，其余时间在全球避暑或打理业务"]
        }
    },
    "杭州": {
        "互联网工程师": {
            "job_detail": ["阿里 P8+ 资深技术专家", "网易资深前端架构师", "字节跳动商业化算法总监"],
            "life_stage": ["青年两口之家，拟在2年内育儿，面临刚性高品质居住改善升级"],
            "current_living": ["余杭区或滨江区次新两房，空间狭窄，随着未来生娃父母同住将面临窒息感"],
            "living_pain": ["原有小区电梯经常拥堵，小区人车不分流，加班深夜回家地下车位一位难求，私密性差"],
            "detail_need": ["必须带朝南超宽双阳台，主卫干湿分离，预留独立书房/电竞办公舱，配有全屋智能家居控制系统"],
            "commute_pref": ["早晚加班极严重，要求紧邻地铁5号线或19号线，30分钟内直达未来科技城或滨江园区"],
            "lifestyle_pref": ["深夜写代码或打游戏，周末在西溪湿地旁骑行，喜欢去小众网红咖啡馆"],
            "funds_source": ["互联网公司股票期权兑现 + 夫妻双方住房公积金顶额组合贷"],
            "brand_service_need": ["高效率、线上响应极速的现代高水准物业，支持快递代收及智能快递柜配置"],
            "vacation_frequency": ["常住人口，年休假选择去日本或东南亚海岛度假1-2次"]
        },
        "本地改善": {
            "job_detail": ["杭州本地高校副教授", "省直机关科级干部", "杭州知名民企中层经理"],
            "life_stage": ["三口或四口之家（含老人同住），孩子面临升学，希望一步到位换大三房或四房"],
            "current_living": ["杭州主城区早年商品房，层高偏低，无地下车库，绿化杂乱"],
            "living_pain": ["车位常年靠抢，老人爬楼梯不便，户型动静不分区导致孩子学习经常受到打扰"],
            "detail_need": ["户型动静分区，客餐厅南北一体化大双厅，大U型厨房方便多人操作，卧室带独立卫浴"],
            "commute_pref": ["夫妻双方主要乘地铁或开车前往武林商圈或钱江新城，希望2公里内有市重点中小学"],
            "lifestyle_pref": ["周末带全家去西湖或湘湖散步，在自家阳台种植花草，极重家庭氛围"],
            "funds_source": ["置换卖掉老城区一套老破小获取大部分首付，配合公积金与商业组合贷"],
            "brand_service_need": ["极好口碑的本地物业（如绿城/滨江物业），园林式绿化打理与贴心老年关怀服务"],
            "vacation_frequency": ["常住人口，国庆或春节选择国内长途旅游1-2次"]
        },
        "浙商投资": {
            "job_detail": ["绍兴纺织企业老板", "温州皮革外贸公司创始人", "台州模具制造厂投资人"],
            "life_stage": ["中年富裕阶层，子女在杭读大学或已成家，购置杭州核心资产以兼顾自住与财富配置"],
            "current_living": ["地级市独栋自建大别墅，宽敞但缺乏省会核心城市顶级资源与医疗配套"],
            "living_pain": ["地级市优质医疗和高端商业贫瘠，资金存在通胀贬值压力，急需配置省会保值型不动产"],
            "detail_need": ["客厅开间不低于6米，带独立中西双厨，主卧必须为双衣帽间双台盆的顶奢行政套房"],
            "commute_pref": ["不依赖日常地铁通勤，但要求开车30分钟内直达钱江新城金融区，且车位不低于2个"],
            "lifestyle_pref": ["在西湖国宾馆招待客户，出入核心高端私人商会，周末在高尔夫球场商务洽谈"],
            "funds_source": ["企业经营流动资金溢出利润，全款买入或通过名下企业大额信贷操作"],
            "brand_service_need": ["国宾级私密物业保卫服务，配有豪车停放专属地库及24小时楼栋大堂管线管家"],
            "vacation_frequency": ["常住或半常住，经常往返于绍兴/温州与杭州之间，年均出国考察1个月"]
        },
        "学区刚需": {
            "job_detail": ["杭州本地医院护士", "主城区公立小学教师", "杭州新媒体公司运营主管"],
            "life_stage": ["年轻两口之家，孩子刚出生或面临读幼儿园，家庭第一套重学区房产购置"],
            "current_living": ["租房居住于城北或九堡，经常面临搬家烦恼，且学区无法落地"],
            "living_pain": ["租房缺乏归属感，眼看孩子即将到入学年龄，急需锁定优质学区以获取确定性教育门票"],
            "detail_need": ["小三房或大两房，户型实用率极高，收纳功能强大，儿童房要求采光良好"],
            "commute_pref": ["依靠地铁1号线、2号线通勤至市区，要求距离学校步行不超过10分钟，安全省时"],
            "lifestyle_pref": ["陪伴孩子上早教班，周末在社区公园遛娃，喜欢在厨房烹饪家常菜"],
            "funds_source": ["双方父母倾尽积蓄支持首付，小两口公积金满额顶满贷款以压降月供负担"],
            "brand_service_need": ["要求收费合理、安全防护过硬的物管，注重社区儿童安全防范与整洁度"],
            "vacation_frequency": ["常住居民，几乎无长途旅居，假期主要回双方老家探亲"]
        },
        "人才落户": {
            "job_detail": ["浙大毕业留杭博士后", "杭州高新企业海归研发工程师", "全国重点大学引进硕士"],
            "life_stage": ["单身青年或刚领证情侣，享受杭州人才购房政策补贴，意图立足省会"],
            "current_living": ["与他人合租高层公寓，公用卫生间，生活隐私受到较大干扰"],
            "living_pain": ["高房租且缺乏个人空间，希望利用人才首套免摇号或优先政策，快速锁定属于自己的资产"],
            "detail_need": ["精装交付小户型（70-90㎡），要求带中央空调与地暖，配备年轻化极简玄关"],
            "commute_pref": ["要求骑行15分钟内可达未来科技城或钱江世纪城核心园区，地铁站500米以内"],
            "lifestyle_pref": ["下班后健身房锻炼，周末与朋友聚餐打剧本杀，习惯点外卖和网购"],
            "funds_source": ["人才落户政策性首付无息借款/补贴 + 父母部分资助 + 个人公积金组合贷"],
            "brand_service_need": ["年轻朝气、智能化的社区管家，支持APP一键报修、快递管家代送及公用共享健身舱"],
            "vacation_frequency": ["主城区常住，国庆或五一选择回老家，或与朋友国内自由行"]
        }
    },
    "上海": {
        "金融精英": {
            "job_detail": ["陆家嘴外资投行董事总经理", "静安区私募基金高级合伙人", "浦东某券商金牌分析师"],
            "life_stage": ["一家四口（含二胎），雇有专职管家，对顶级国际学校教育资源有强依附度"],
            "current_living": ["原有高层住宅视野受限，邻里圈层驳杂，上下电梯缺乏私密性，无直达专属地库"],
            "living_pain": ["高压环境下极度缺乏安静的休息空间，隐私保护弱，缺乏符合身份的层峰社交圈层"],
            "detail_need": ["必须配有独立保姆通道及工作阳台，主卧室为270度景观全套房设计，配独立步入式双人衣帽间"],
            "commute_pref": ["主要往返于陆家嘴金融城或新天地，要求专属私家车位配比 1:1.5 以上，避开高架拥堵"],
            "lifestyle_pref": ["在黄浦江畔高级私人会所宴请，周末去佘山打高尔夫，享受精致西餐与艺术展"],
            "funds_source": ["纯自有高额年终奖金+投资溢价套现，全款或高首付商业信贷操作"],
            "brand_service_need": ["国宾级24小时私密安保，楼栋大堂管家级前台服务，提供星级私宴预约及干洗代办"],
            "vacation_frequency": ["长住上海，每年春节前往瑞士滑雪或欧洲度假2周，平时周末不定期江浙周边游"]
        },
        "外籍海归": {
            "job_detail": ["跨国医药企业中国区研发总监", "麦肯锡资深全球合伙人", "知名美资科技公司中国区VP"],
            "life_stage": ["年轻夫妻，无子或单子，习惯西式生活方式，具有国际化审美与开阔视野"],
            "current_living": ["租住联洋或古北高档国际社区，租金高昂且无法按自我审美改造软装"],
            "living_pain": ["租房缺乏长期资产稳定性，大部分普通国内小区物业对英语服务支持弱，社区圈层缺乏国际化特质"],
            "detail_need": ["大横厅设计，西式开放式厨房带独立中岛，超大落地窗，全屋智能恒温恒湿新风系统"],
            "commute_pref": ["要求20分钟内车程或地铁直达漕河泾高新区或静安寺核心商务区，接受外环高质社区"],
            "lifestyle_pref": ["清晨手冲咖啡，喜欢露天酒吧、爵士乐和西餐，周末与外籍友人抱团户外露营"],
            "funds_source": ["跨国公司高额期权变现 + 个人大额美元存款结汇支持"],
            "brand_service_need": ["双语国际化物业服务，代办外籍人员服务申报，社区内配有西式简餐吧与恒温泳池"],
            "vacation_frequency": ["年均回欧美探亲度假2次，年假通常选择前往巴厘岛或马尔代夫冲浪"]
        },
        "本地改善": {
            "job_detail": ["上海三甲医院主任医师", "上海重点中学高级教师", "上海市级机关正科级公务员"],
            "life_stage": ["上海本地典型三口之家（含老人偶尔同住），孩子读高中，注重稳定性与区位配套升级"],
            "current_living": ["杨浦或普陀早期老公房，户型多为暗卫，无地下车库，绿化拥挤破败"],
            "living_pain": ["小区老旧无电梯导致老人膝关节疼痛，水管经常生锈水压不足，车位抢占纠纷频发"],
            "detail_need": ["南北通透三房，明厨明卫，带超宽采光阳台，电梯直达地下车库，要求有独立的储物间"],
            "commute_pref": ["通过地铁4号线、10号线通勤，要求距离最近的三甲医院车程在10分钟以内"],
            "lifestyle_pref": ["周末带全家去世纪公园散步，在阳台泡茶种花，讲究生活细节的体面与精致"],
            "funds_source": ["置换卖掉老城区一套老公房获取大部分首付，剩余申请纯公积金低息贷款"],
            "brand_service_need": ["以细致周到著称的本地老牌物业，人车分流，社区老年关怀绿道维护极佳"],
            "vacation_frequency": ["主城区常住，国庆或春节带全家前往三亚或青岛旅居度假10-15天"]
        },
        "教育型买房": {
            "job_detail": ["张江芯片外企高级经理", "跨国咨询公司高级顾问", "上海知名律所出资合伙人"],
            "life_stage": ["典型的上海中产两口之家，孩子面临幼升小或小升初，买房的首要且唯一驱动力是顶级学区锁门"],
            "current_living": ["租房居住于前滩，学区名额缺乏长期稳定性，随时面临被统筹的巨大风险"],
            "living_pain": ["上海优质公办教育资源竞争极其惨烈，若没有房产证落户年限优势，孩子将错失名校入场券"],
            "detail_need": ["90㎡左右功能性强三房，全屋预留高强度学习课桌及书柜墙，儿童房隔音要求极高"],
            "commute_pref": ["要求步行至目标省重点小学不超过8分钟，周边500米内有丰富的课外培训与体育场馆"],
            "lifestyle_pref": ["接送孩子穿梭于各大辅导班，周末带孩子去科技馆或图书馆，极度关注升学论坛"],
            "funds_source": ["夫妻双方掏空江浙老家父母的养老储蓄 + 变卖婚前小户型资产，顶满公积金公积金组合贷"],
            "brand_service_need": ["安全系数极高、防拐防丢、进出人流管控严格的优质物业，社区氛围崇尚学习与安静"],
            "vacation_frequency": ["常住，几乎无闲暇旅居，长假基本在各类名校寒暑期游学或集训中度过"]
        },
        "长三角家庭": {
            "job_detail": ["昆山电子厂老板", "苏州外贸公司合伙人", "杭州知名互联网公司高管"],
            "life_stage": ["双城生活家庭，主攻上海核心资产配置，为子女未来在沪落户、成家或就业提前筑巢"],
            "current_living": ["江浙城市大平层，虽然居住舒适但资产增值潜力和教育上限不如上海"],
            "living_pain": ["长三角双城通勤劳累，希望在上海虹桥枢纽或主城区快速配置一套兼顾自住与财富锚的房产"],
            "detail_need": ["南向开间大，客餐厅连通性好，精装修要求大牌厨卫，主卧带独立步入式衣帽间"],
            "commute_pref": ["极其注重交通枢纽便捷性，要求紧邻虹桥枢纽或地铁2/10/17号线，方便高铁往返苏杭"],
            "lifestyle_pref": ["双城通勤，工作日在上海打拼或管理资产，周末回江浙老家陪伴老人"],
            "funds_source": ["江浙实体产业利润溢出 + 股权投资收益分红，全款或高比例首付商业贷款"],
            "brand_service_need": ["要求提供车辆代泊、空置期入户代检查、以及高端商务管家贴心服务的物管服务"],
            "vacation_frequency": ["频繁往返于长三角城市群之间，年均累积有2-3个月在上海居住自住"]
        }
    },
    "青岛": {
        "本地改善": {
            "job_detail": ["青岛知名家电企业高级工程师", "青医附院副主任医师", "市南区中学高级教师"],
            "life_stage": ["典型的青岛三口之家，追求高舒适度、大面宽的品质海景改善"],
            "current_living": ["市北区无地下车库的早年多层，社区老旧，冬天暖气管线经常老化故障"],
            "living_pain": ["老旧小区停车经常抢位，海风台风季老窗户经常漏水严重，社区无物业打理乱堆乱放"],
            "detail_need": ["要求大面宽南向三卧室，配备高强度双层防台风真空落地窗，有独立的保姆洗涤区"],
            "commute_pref": ["要求30分钟内车程通达五四广场或市南核心商圈，距离青医附院等优质医疗10分钟车程内"],
            "lifestyle_pref": ["清晨在海边绿道慢跑，周末在自家超大阳台喝青岛啤酒、烤海鲜，追求慢节奏悠闲生活"],
            "funds_source": ["卖掉市北老公房获取6成首付资金，配合当地低息商业贷款"],
            "brand_service_need": ["要求具备高抗台风应急管理能力的本地顶级物业，社区内人车分流且有专属宠物遛狗区"],
            "vacation_frequency": ["常住居民，每年仅有1-2周在省内或外地海岛旅游"]
        },
        "山东省内异地": {
            "job_detail": ["临沂石材物流老板", "潍坊私企制造厂长", "济南退休机关干部"],
            "life_stage": ["山东省内其他地市的中产家庭，向往青岛宜居的海滨气候与省内顶尖医疗资源，购置避暑资产"],
            "current_living": ["内陆地市商品房，夏季闷热且空气质量一般，缺乏海洋景观与凉爽海风"],
            "living_pain": ["省内内陆城市夏季酷热，极度渴望在青岛配置一套面朝大海、夏可避暑、冬可越冬的第二居所"],
            "detail_need": ["必须带观海露台，大户型方便省内亲戚抱团度假来访，精装修要求全套防潮防霉建材"],
            "commute_pref": ["无需日常通勤，但要求靠近青岛北站或高铁站，开车2小时内能直达省内老家"],
            "lifestyle_pref": ["带家人在石老人海滩洗海澡，去崂山风景区喝崂山绿茶，在海边海鲜市场买鱼加工"],
            "funds_source": ["内陆城市企业经营利润溢出积蓄，全款买入或大比例首付款组合"],
            "brand_service_need": ["提供完善的离岛空置打理、入户通风除湿、以及代办取暖费等季节性代办服务的物管"],
            "vacation_frequency": ["每年6月至9月稳定在青避暑度假，冬季偶尔来青过年，年均长住3个月"]
        },
        "北方度假投资": {
            "job_detail": ["北京金融街外资高管", "太原煤炭供应链企业主", "西安连锁超市创始人"],
            "life_stage": ["北方内陆的高净值改善家庭，购置青岛高端海景住宅作为全家避暑与圈层招待会客厅"],
            "current_living": ["北方高浓度高层住宅，缺乏海洋景观，夏天炎热难耐，渴望海景资产进行资产避险"],
            "living_pain": ["北方内陆风沙大、夏季干热，急需在“红瓦绿树、碧海蓝天”的青岛配置一套顶级海景资产"],
            "detail_need": ["必须为头排海景独栋或大平层，270度无死角观海，带超大室外烧烤露台及专属红酒窖"],
            "commute_pref": ["要求40分钟内能直达胶东国际机场，周边有高尔夫球场及游艇俱乐部配套"],
            "lifestyle_pref": ["邀请企业老友在观海阳台品红酒，出海垂钓，崂山茶会，享受纯粹的高端层峰圈层社交"],
            "funds_source": ["企业高额利润分红或二级市场股权变现，全款全额买入"],
            "brand_service_need": ["提供顶级私密安保、星级管家代办私宴、游艇预约以及空置期特级除湿通风打理的物管"],
            "vacation_frequency": ["年均避暑3-4次，每次居住2周左右，7-8月盛夏期间长住一个月自用"]
        },
        "学区刚需": {
            "job_detail": ["青岛本地私立幼儿园教师", "青岛城阳高新企业软件工程师", "当地外贸公司单证员"],
            "life_stage": ["青岛年轻小两口，结婚筑巢，同时瞄准市南或崂山优质中小学学区资源入场"],
            "current_living": ["与公婆合租在李沧区，空间逼仄且隐私受限，面临孩子入托落户的迫切性"],
            "living_pain": ["市区好学校的落户年限卡得很死，若不早买房落户，孩子将错失省重点中小学划片名额"],
            "detail_need": ["80-95㎡实用三房，玄关收纳强，预留儿童独立玩具区，采光充足方便晒太阳"],
            "commute_pref": ["小两口主要乘地铁3号线、11号线通勤，要求步行到划片小学在10分钟以内"],
            "lifestyle_pref": ["陪伴孩子在社区沙地玩耍，周末在八大关遛娃拍照，研究本地升学政策与辅导方案"],
            "funds_source": ["夫妻双方父母倾家荡产倾注首付款 + 个人公积金顶额低利息组合贷"],
            "brand_service_need": ["物业收费公道合理、安全监控防拐网络严密的物业，社区日常环境安静整洁"],
            "vacation_frequency": ["常住青岛，几乎无闲暇闲钱度假，假期主要带孩子回省内老家探望爷爷奶奶"]
        },
        "高端海景收藏": {
            "job_detail": ["上市公司主要创始人", "大型家族企业信托掌门人", "北京顶奢画廊主理人"],
            "life_stage": ["金字塔尖收藏家，视青岛前排海景独栋如艺术品，作为家族资产传承与顶级社交名片配置"],
            "current_living": ["北京或上海核心地段顶层复式，极尽奢华但缺乏青岛独一无二的历史德式风貌与天然前排海湾"],
            "living_pain": ["一线城市顶层豪宅缺乏海角独占的物理界限和庄园级别的绝对私密感，渴望收藏绝版地段"],
            "detail_need": ["绝版德式风貌独栋或依山面海顶奢庄园，私家车库直达，配顶级影音室和专业藏品恒温库"],
            "commute_pref": ["完全脱离通勤，但要求有私人直升机停机坪配置或20分钟内直达顶级游艇私人码头"],
            "lifestyle_pref": ["举办私人艺术品品鉴晚宴，收藏世界名画，与政商领袖在海角阳台俯瞰青岛湾夜景"],
            "funds_source": ["上市公司家族信托专款专项配置，极高首付比例或以海外资本回流全款买断"],
            "brand_service_need": ["特级白金保镖物业服务，代打理私家园林，提供空置期博物馆级别的藏品养护与除湿"],
            "vacation_frequency": ["度假频次无规律，视天气与商务日程安排，年均在此停留居住2-3周自住"]
        }
    }
}

def sample_personas(city: str, n: int = 1000, target_year: int | None = None) -> list[Persona]:
    if target_year is not None:
        p_list = load_personas_from_vault(city, target_year)
        if p_list:
            if len(p_list) >= n:
                return random.sample(p_list, n)
            else:
                return p_list
        # 若物理客群库未预生成（如2026年等未来年份），优雅降级至基于 Monte Carlo 机制的动态高精采样，决不返回 None 空数据
        print(f"[info] {city} {target_year}年 物理客群库未就绪，已优雅降级启用高精 Monte Carlo 动态抽样...")

    pool = CITY_POOLS.get(city)
    if not pool:
        # 未配置该城专属客群池（如购买全国库新增的 631 城、以及济南）：
        # 降级用通用池（默认杭州结构，多元大市场）做去化模拟，不崩溃。
        pool = CITY_POOLS.get("杭州") or (next(iter(CITY_POOLS.values()), None))
        print(f"[info] {city} 无专属客群池，降级用通用客群池做去化模拟（结果为通用估计）")
    if not pool:
        raise ValueError(f"无任何客群池可用：{city}")
    weights = [a.weight for a in pool]
    personas = []
    
    # 获取该城市的加厚中文语义数据库，方便动态抽样时查表
    city_semantics = ARCHETYPE_SEMANTICS.get(city, {})

    for _ in range(n):
        a = random.choices(pool, weights=weights, k=1)[0]
        income = _truncnorm(a.income_mu, a.income_sigma, lo=5)
        asset = income * a.asset_multiplier * random.uniform(0.7, 1.3)
        # 总房款 = 可投入首付 / 首付比例；留 25% 流动性储备
        liquid = asset * 0.75
        budget = liquid / max(a.dp_ratio, 0.3)
        # 加厚维度抽样（基于 archetype 均值 + 噪声）
        fam_size = max(1, int(round(random.gauss(a.family_size, 0.6))))
        kids_n = max(0, min(3, int(round(random.gauss(a.kids, 0.5)))))
        has_elderly = random.random() < a.elderly_cohabit
        urgency = max(0, min(1, random.gauss(1.0 - a.decision_horizon_months / 12, 0.15)))
        
        # 动态合成 10 大高维语义中文字段
        arch_fields = city_semantics.get(a.name, {})
        job_val = random.choice(arch_fields.get("job_detail", ["地产/金融界精英"])) if arch_fields else "自主创业企业主"
        stage_val = random.choice(arch_fields.get("life_stage", ["幸福家庭改善"])) if arch_fields else "三口之家"
        living_val = random.choice(arch_fields.get("current_living", ["市中心老旧高层"])) if arch_fields else "商品房住宅"
        pain_val = random.choice(arch_fields.get("living_pain", ["车位缺乏、物业响应慢"])) if arch_fields else "居住品质低"
        need_val = random.choice(arch_fields.get("detail_need", ["要求户型通透且得房率高"])) if arch_fields else "采光阳台"
        commute_val = random.choice(arch_fields.get("commute_pref", ["地铁口500米内方便通勤"])) if arch_fields else "地铁通勤"
        life_val = random.choice(arch_fields.get("lifestyle_pref", ["喜欢周末爬山、品茶、享受家庭生活"])) if arch_fields else "注重健康养生"
        funds_val = random.choice(arch_fields.get("funds_source", ["卖旧买新置换支持 + 部分商业贷款"])) if arch_fields else "自筹首付"
        brand_val = random.choice(arch_fields.get("brand_service_need", ["注重物业贴心保卫和绿化维护"])) if arch_fields else "品牌优质物业"
        vacation_val = random.choice(arch_fields.get("vacation_frequency", ["每年年假长途旅游1-2次"])) if arch_fields else "周末省内自驾游"

        personas.append(Persona(
            archetype=a.name,
            age=random.randint(*a.age_range),
            annual_income=round(income, 1),
            total_asset=round(asset, 1),
            dp_ratio=a.dp_ratio,
            pref={k: max(0, min(1, v + random.gauss(0, 0.08))) for k, v in a.pref.items()},
            risk_aversion=max(0, min(1, a.risk_aversion + random.gauss(0, 0.08))),
            budget_ceiling=round(budget, 1),
            family_size=fam_size,
            kids=kids_n,
            elderly_cohabit=has_elderly,
            social_class=a.social_class,
            info_channel=a.info_channel,
            decision_urgency=urgency,
            # ↓ 注入 10 大中文字段兜底支持
            job_detail=job_val,
            life_stage=stage_val,
            current_living=living_val,
            living_pain=pain_val,
            detail_need=need_val,
            commute_pref=commute_val,
            lifestyle_pref=life_val,
            funds_source=funds_val,
            brand_service_need=brand_val,
            vacation_frequency=vacation_val
        ))
    return personas


# ============================================================
# 3. MNL 决策模型
# ============================================================
@dataclass
class Product:
    name: str
    unit_price: float       # 元/㎡
    area: float             # ㎡，户型面积
    pref_score: dict[str, float] = field(default_factory=dict)  # 该产品在各维度上的得分 0..1


def _gumbel() -> float:
    """Gumbel(0,1) 抽样，用于 MNL 异质性项"""
    u = random.random()
    return -math.log(-math.log(max(u, 1e-9)))


def _budget_fit(persona, total_price_wan):
    """倒 U 型：0.6x 预算最舒服；过贵罚，过便宜对 A 阶层嫌弃"""
    if total_price_wan > persona.budget_ceiling * 1.1:
        return -3.0
    ratio = total_price_wan / persona.budget_ceiling
    base = 1.2 - 3.0 * (ratio - 0.6) ** 2
    if persona.social_class == "A" and ratio < 0.3:
        base -= 0.6 * (0.3 - ratio)
    elif persona.social_class == "D" and ratio > 0.7:
        base -= 0.4 * (ratio - 0.7)
    return base


def utility(persona: Persona, product: Product) -> float:
    total_price_wan = product.unit_price * product.area / 10000
    budget_fit = _budget_fit(persona, total_price_wan)
    # 2. 偏好契合：persona 偏好 × 产品得分内积
    pref_fit = sum(persona.pref.get(k, 0) * product.pref_score.get(k, 0.5)
                   for k in persona.pref) / max(len(persona.pref), 1)
    # 3. 风险厌恶折扣
    risk_penalty = -persona.risk_aversion * 0.3
    # 4. 家庭维度：户型与人口的匹配（房间数推断）
    rooms_need = 1 + persona.kids + (1 if persona.elderly_cohabit else 0)
    rooms_supply = max(1, int(product.area / 40))   # 粗略：每 40㎡ 一间
    fit_penalty = -0.4 * abs(rooms_supply - rooms_need) / max(rooms_need, 1)
    # 5. 阶层加成：A 层对品牌/圈层敏感度上修
    class_bonus = 0.0
    if persona.social_class == "A":
        class_bonus = 0.25 * product.pref_score.get("品牌", 0.5) + 0.2 * product.pref_score.get("圈层", 0.5)
    elif persona.social_class == "D":
        class_bonus = -0.15 * (1 - product.pref_score.get("户型", 0.5))  # 刚需对户型实用敏感
    # 6. Gumbel 异质性
    epsilon = _gumbel() * 0.5
    return 1.5 * budget_fit + 2.5 * pref_fit + risk_penalty + fit_penalty + class_bonus + epsilon


def buy_probability(persona: Persona, product: Product, competitors: list[Product]) -> float:
    """MNL 购买概率：本项目 vs 不买（含竞品集合作为外部选项）"""
    u_target = utility(persona, product)
    if competitors:
        comp_u = statistics.mean(utility(persona, c) for c in competitors)
    else:
        comp_u = 0.0
    # 紧迫度低的人推迟决策成本低 → "不买"项效用高
    no_buy_utility = 1.0 - persona.decision_urgency  # 0..1
    denom = math.exp(u_target) + math.exp(comp_u) + math.exp(no_buy_utility)
    return math.exp(u_target) / denom


def wtp(persona: Persona, product: Product) -> float:
    """支付意愿（元/㎡）：受预算约束 + 偏好溢价，截断在市场合理倍数内"""
    budget_per_sqm = persona.budget_ceiling * 10000 / max(product.area, 50)
    pref_premium = sum(persona.pref.get(k, 0) * product.pref_score.get(k, 0.5)
                       for k in persona.pref) / max(len(persona.pref), 1)
    raw = budget_per_sqm * (0.6 + 0.5 * pref_premium) * (1 - 0.2 * persona.risk_aversion)
    # 市场锚定：不超过产品标价 1.8 倍（理性比价约束）
    market_ceiling = product.unit_price * (1.2 + 0.6 * pref_premium)
    return min(raw, market_ceiling)


# ============================================================
# 4. 主入口：城市 + 产品 → 客群组合 / WTP / 去化曲线
# ============================================================
def run_abm(
    city: str,
    product: Product,
    competitors: list[Product] | None = None,
    n: int = 1000,
    horizon_months: int = 24,
    monthly_inflow_rate: float = 0.04,  # 每月新增 4% 入场人格
    seed: int | None = 42,
    target_year: int | None = None,
) -> dict:
    if seed is not None:
        random.seed(seed)
    competitors = competitors or []
    personas = sample_personas(city, n, target_year=target_year)

    # 1. 每个 persona 的购买概率 + WTP
    rows = []
    for p in personas:
        prob = buy_probability(p, product, competitors)
        rows.append({
            "archetype": p.archetype,
            "age": p.age,
            "income": p.annual_income,
            "budget": p.budget_ceiling,
            "buy_prob": prob,
            "wtp": wtp(p, product),
        })

    # 2. 客群组合：按 archetype 聚合 buy_prob 加权
    arch_stats = {}
    for r in rows:
        a = r["archetype"]
        if a not in arch_stats:
            arch_stats[a] = {"n": 0, "weighted_buyers": 0.0, "wtps": []}
        arch_stats[a]["n"] += 1
        arch_stats[a]["weighted_buyers"] += r["buy_prob"]
        arch_stats[a]["wtps"].append(r["wtp"])
    total_buyers = sum(s["weighted_buyers"] for s in arch_stats.values()) or 1
    composition = sorted([
        {
            "name": a,
            "share": round(s["weighted_buyers"] / total_buyers, 3),
            "raw_count": s["n"],
            "score": round(100 * s["weighted_buyers"] / s["n"], 1),
            "wtp_p50": round(statistics.median(s["wtps"]), 0),
            "wtp_p90": round(sorted(s["wtps"])[int(len(s["wtps"]) * 0.9)], 0),
        }
        for a, s in arch_stats.items()
    ], key=lambda x: x["share"], reverse=True)

    # 3. WTP 分布（直方图，10 桶）
    all_wtps = [r["wtp"] for r in rows]
    wtp_hist = _histogram(all_wtps, bins=10)

    # 4. 去化曲线：每月入场 × 平均 buy_prob × 累积
    avg_prob = sum(r["buy_prob"] for r in rows) / len(rows)
    monthly_inflow = n * monthly_inflow_rate
    sellthrough = []
    cum = 0.0
    for t in range(1, horizon_months + 1):
        # 每月新入场 + 老库存的人格继续考虑（衰减 0.7）
        sold_t = monthly_inflow * avg_prob * (0.7 ** (t // 6))
        cum += sold_t
        sellthrough.append({"month": t, "monthly_sales": round(sold_t, 1),
                            "cumulative": round(cum, 1)})

    # 5. 价格敏感性：单价 ±5% / ±10% 重跑 avg_prob
    sensitivity = []
    for delta in (-0.10, -0.05, 0.05, 0.10):
        shifted = Product(product.name, product.unit_price * (1 + delta),
                          product.area, product.pref_score)
        ps = [buy_probability(p, shifted, competitors) for p in personas]
        sensitivity.append({
            "price_delta": f"{int(delta*100):+d}%",
            "avg_buy_prob": round(sum(ps) / len(ps), 3),
            "vs_base_pct": round((sum(ps) / len(ps) / avg_prob - 1) * 100, 1),
        })

    # 6. 痛点与核心细节需求动态加权聚合 (战役 8 地产极速 TF-IDF 分词)
    HEDGING_KEYWORDS = {
        "pain": ["无电梯", "老破小", "人车不分流", "无地下车位", "通勤远", "户型极差", "绿化差", "隔音极差", "学区占用", "停车位匮乏", "环境嘈杂", "漏水严重"],
        "need": ["必须双阳台", "双套房", "独立衣帽间", "中西双厨", "独立保姆房", "私家双车位", "高端会所", "绿城物业", "大面宽南向", "得房率高", "智能化家居"]
    }
    
    STOPWORDS = {"的", "是", "了", "和", "在", "我", "我们", "也", "有", "要", "必须", "非常", "极其", "特别", "客群", "需求", "楼盘", "房子", "小区", "购买", "期望", "感觉"}

    def _tokenize(text, word_type):
        if not text:
            return []
        tokens = []
        # 1. 优先捕获高频行业概念
        matched_any = False
        for kw in HEDGING_KEYWORDS[word_type]:
            if kw in text:
                tokens.append(kw)
                matched_any = True
        
        # 2. 如果未匹配到，则按标点符号分词，并过滤停用词与虚词
        if not matched_any:
            import re
            parts = re.split(r"[,，\.。;\s、；]+", text)
            for p in parts:
                p = p.strip()
                if len(p) > 1:
                    # 去除停用词
                    clean_p = "".join(ch for ch in p if ch not in STOPWORDS)
                    if len(clean_p) > 1:
                        tokens.append(clean_p)
        return tokens

    pain_stats = {}
    need_stats = {}
    for p in personas:
        prob = buy_probability(p, product, competitors)
        if prob > 0.0:
            for token in _tokenize(p.living_pain, "pain"):
                pain_stats[token] = pain_stats.get(token, 0.0) + prob
            for token in _tokenize(p.detail_need, "need"):
                need_stats[token] = need_stats.get(token, 0.0) + prob

    total_pain_weight = sum(pain_stats.values()) or 1.0
    total_need_weight = sum(need_stats.values()) or 1.0

    top_pains = sorted([
        {"pain": pain, "weight": round(weight / total_pain_weight, 3), "raw_score": round(weight, 1)}
        for pain, weight in pain_stats.items()
    ], key=lambda x: x["weight"], reverse=True)[:5]

    top_details = sorted([
        {"need": need, "weight": round(weight / total_need_weight, 3), "raw_score": round(weight, 1)}
        for need, weight in need_stats.items()
    ], key=lambda x: x["weight"], reverse=True)[:5]

    # 尊贵级优化：对每种客群分类提取最多 25 个真实人头，避免传输体积过大，同时实现数据完美打通
    from collections import defaultdict
    _buckets = defaultdict(list)
    for p in personas:
        _buckets[p.archetype].append(asdict(p))
    personas_sample = []
    for arch_name, p_list in _buckets.items():
        personas_sample.extend(p_list[:25])

    return {
        "role": "ABM 市场模拟 Agent",
        "method": "Random Utility + MNL，蒙特卡洛 N=" + str(n),
        "city": city,
        "product": asdict(product),
        "n_personas": n,
        "avg_buy_probability": round(avg_prob, 3),
        "top_personas": composition[:3],
        "personas": composition,
        "personas_raw": personas_sample,
        "wtp_distribution": wtp_hist,
        "wtp_summary": {
            "p10": round(sorted(all_wtps)[int(len(all_wtps) * 0.1)], 0),
            "p50": round(statistics.median(all_wtps), 0),
            "p90": round(sorted(all_wtps)[int(len(all_wtps) * 0.9)], 0),
            "unit": "元/㎡",
        },
        "sellthrough_curve": sellthrough,
        "price_sensitivity": sensitivity,
        # 真实证据置信度与 Monte Carlo 稳定度必须分开。增加合成人数只会
        # 降低抽样噪声，不能把专家先验/合成人口伪装成真实市场证据。
        "evidence_confidence": _compute_evidence_confidence(),
        "simulation_stability": _compute_simulation_stability(arch_stats, n),
        # 向后兼容：旧调用方仍读取 confidence，但它现在明确指证据置信度。
        "confidence": _compute_evidence_confidence(),
        "top_pains": top_pains,
        "top_details": top_details,
    }


def _histogram(values, bins=10):
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [{"range": f"{lo:.0f}", "count": len(values)}]
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - lo) / step), bins - 1)
        counts[idx] += 1
    return [
        {"range": f"{lo + i*step:.0f}-{lo + (i+1)*step:.0f}", "count": c}
        for i, c in enumerate(counts)
    ]


def _confidence_level(score: float) -> str:
    if score >= 0.8:
        return "高"
    if score >= 0.5:
        return "中"
    return "低"


def _compute_evidence_confidence():
    """合成人口默认证据置信度。

    该分数只描述真实世界证据基础，不读取 ``n``。后续接入问卷、到访、
    带看或成交锚点时，应由上游证据契约有界覆盖，而不是由模拟次数推高。
    """
    score = 0.25
    return {
        "score": score,
        "level": _confidence_level(score),
        "use_real_data": False,
        "evidence_type": "model_simulation",
        "note": "当前为专家先验与合成人口，未接入可审计的真实需求或成交锚点",
    }


def _compute_simulation_stability(arch_stats, n):
    """描述重复 Monte Carlo 的数值稳定度；允许随样本数提高。"""
    coverage = sum(1 for s in arch_stats.values() if s["n"] >= 30) / max(len(arch_stats), 1)
    sample_score = min(n / 1000, 1.0)
    score = coverage * 0.6 + sample_score * 0.4
    return {
        "score": round(score, 2),
        "level": _confidence_level(score),
        "sample_count": int(n),
        "archetype_coverage": round(coverage, 3),
        "note": "仅表示模拟结果的数值稳定性，不代表真实市场证据可信度",
    }


def _compute_confidence(arch_stats, n):
    """兼容旧的内部调用；返回真实证据置信度而非模拟稳定度。"""
    return _compute_evidence_confidence()


# 偏好向量（中文键集中定义，避免长行截断）
_PREF_VILLA = {"景观": 0.85, "私密": 0.9, "圈层": 0.7, "户型": 0.75,
               "通勤": 0.3, "学校": 0.4, "品牌": 0.8}
_PREF_LUX = {"景观": 0.8, "私密": 0.85, "圈层": 0.75, "户型": 0.8,
             "通勤": 0.5, "学校": 0.55, "品牌": 0.85}
_PREF_GAISHAN = {"景观": 0.65, "私密": 0.55, "圈层": 0.55, "户型": 0.85,
                 "通勤": 0.65, "学校": 0.7, "品牌": 0.6}
_PREF_GANGXU = {"景观": 0.4, "私密": 0.3, "圈层": 0.35, "户型": 0.75,
                "通勤": 0.8, "学校": 0.7, "品牌": 0.45}
_PREF_COMP = {"景观": 0.6, "私密": 0.5, "圈层": 0.55, "户型": 0.7,
              "通勤": 0.6, "学校": 0.55, "品牌": 0.6}


def product_from_client_goal(client_goal, avg_price):
    pt = client_goal.get("product_type") or ""
    if "商墅" in pt or "别墅" in pt:
        area, pref = 220, _PREF_VILLA
    elif "高端" in pt or "豪宅" in pt:
        area, pref = 180, _PREF_LUX
    elif "改善" in pt:
        area, pref = 140, _PREF_GAISHAN
    else:
        area, pref = 95, _PREF_GANGXU
    return Product(pt or "default", avg_price or 30000, area, pref)


def competitors_from_parcel(competitors_raw):
    out = []
    for c in competitors_raw[:10]:
        price = c.get("unit_price_cny")
        if not price:
            continue
        area = _parse_area(c.get("area_range") or "") or 120
        out.append(Product(c.get("project_name", "comp"), price, area, _PREF_COMP))
    return out


def _parse_area(s):
    import re
    nums = re.findall(r"\d+", s)
    if not nums:
        return None
    nums = [float(x) for x in nums]
    return sum(nums) / len(nums)


# ============================================================
# 6. 客群 × 户型反向匹配（main.md "最优户型配比" 落地）
# ============================================================
def optimize_unit_mix(city, candidate_units, total_units=100,
                      competitors=None, n=1000, seed=42, target_year=None):
    """给定候选户型清单 + 城市客群分布 → 反推最优户型配比

    candidate_units: list[Product]，每个 Product 描述一个候选户型
    total_units: 项目总户数
    返回：unit_mix（每户型推荐套数 + 主力客群 + 预期销售额）
    """
    import random as _r
    _r.seed(seed)
    competitors = competitors or []
    personas = sample_personas(city, n, target_year=target_year)
    # 每个 persona 给每个候选户型算效用，归到最高效用户型
    demand = {u.name: {"unit": u, "buyers": [], "weighted": 0.0} for u in candidate_units}
    for p in personas:
        utilities = []
        for u in candidate_units:
            # 计算 utility 但忽略 Gumbel 噪声（多次抽样反向匹配应稳定）
            u_val = _deterministic_utility(p, u)
            utilities.append((u_val, u))
        # 取效用最高的户型；并跟"不买"(U=0)比，effective 才计入
        u_best, unit_best = max(utilities, key=lambda x: x[0])
        if u_best < 0:
            continue
        # buy_prob 用 MNL 形式
        denom = sum(math.exp(uv) for uv, _ in utilities) + math.exp(1.0 - p.decision_urgency)
        # 竞品分流
        if competitors:
            denom += sum(math.exp(_deterministic_utility(p, c)) for c in competitors)
        prob = math.exp(u_best) / denom
        demand[unit_best.name]["buyers"].append({"archetype": p.archetype, "prob": prob})
        demand[unit_best.name]["weighted"] += prob

    total_weighted = sum(d["weighted"] for d in demand.values())
    if total_weighted == 0:
        return {"method": "反向匹配 v1", "status": "no_demand",
                "total_units": total_units, "unit_mix": []}

    # 输出每个户型的推荐套数 + 主力客群分布 + 预期销售额
    rows = []
    for u in candidate_units:
        d = demand[u.name]
        share = d["weighted"] / total_weighted
        count = round(share * total_units)
        # 主力客群（前 3 个 archetype 按累积概率）
        arch_agg = {}
        for b in d["buyers"]:
            arch_agg[b["archetype"]] = arch_agg.get(b["archetype"], 0) + b["prob"]
        primary = sorted(arch_agg.items(), key=lambda x: x[1], reverse=True)[:3]
        primary_out = [{"name": a, "weight": round(w / d["weighted"], 3)}
                       for a, w in primary] if d["weighted"] else []
        revenue = count * u.unit_price * u.area
        rows.append({
            "name": u.name,
            "area": u.area,
            "unit_price": u.unit_price,
            "recommended_count": count,
            "share": round(share, 3),
            "primary_customers": primary_out,
            "expected_revenue_cny": int(revenue),
            "demand_score": round(d["weighted"], 2),
        })

    total_revenue = sum(r["expected_revenue_cny"] for r in rows)
    # 计算整体加权去化预估（按户型加权 buy_prob）
    avg_prob = total_weighted / n
    expected_months = total_units / max(n * 0.04 * avg_prob, 0.01)

    return {
        "method": "反向匹配 v1（efu 最高户型分桶 + MNL）",
        "city": city,
        "n_personas": n,
        "total_units": total_units,
        "unit_mix": rows,
        "total_expected_revenue_cny": total_revenue,
        "expected_overall_sellthrough_months": round(expected_months, 1),
        "candidate_unit_count": len(candidate_units),
    }


def _deterministic_utility(persona, product):
    """无 Gumbel 噪声版本的 utility，用于反向匹配避免随机抖动"""
    total_price_wan = product.unit_price * product.area / 10000
    budget_fit = _budget_fit(persona, total_price_wan)
    pref_fit = sum(persona.pref.get(k, 0) * product.pref_score.get(k, 0.5)
                   for k in persona.pref) / max(len(persona.pref), 1)
    risk_penalty = -persona.risk_aversion * 0.3
    rooms_need = 1 + persona.kids + (1 if persona.elderly_cohabit else 0)
    rooms_supply = max(1, int(product.area / 40))
    fit_penalty = -0.4 * abs(rooms_supply - rooms_need) / max(rooms_need, 1)
    class_bonus = 0.0
    if persona.social_class == "A":
        class_bonus = 0.25 * product.pref_score.get("品牌", 0.5) + 0.2 * product.pref_score.get("圈层", 0.5)
    elif persona.social_class == "D":
        class_bonus = -0.15 * (1 - product.pref_score.get("户型", 0.5))
    return 1.5 * budget_fit + 2.5 * pref_fit + risk_penalty + fit_penalty + class_bonus

# ============================================================
# 7. 5 年客群迁移预测（main.md 时间维度）
# ============================================================
# 每个 archetype 的年化迁移矩阵：{源 archetype: {目标 archetype: 年化转移率}}
# "exit" 表示退出本地市场（老去/换城市/不再购房）
ARCHETYPE_TRANSITIONS = {
    # 三亚
    "外地候鸟-华北": {"高净值康养": 0.05, "exit": 0.06},
    "外地候鸟-东北": {"高净值康养": 0.04, "exit": 0.08},
    "外地候鸟-西北": {"高净值康养": 0.04, "exit": 0.07},
    "度假投资":     {"本地改善": 0.03, "高净值康养": 0.02, "exit": 0.05},
    "本地改善":     {"高净值康养": 0.02, "exit": 0.03},
    "高净值康养":   {"exit": 0.06},
    # 杭州
    "互联网工程师": {"本地改善": 0.08, "浙商投资": 0.02, "exit": 0.04},
    "浙商投资":     {"高净值康养": 0.02, "exit": 0.04},
    "学区刚需":     {"本地改善": 0.15, "exit": 0.02},
    "人才落户":     {"互联网工程师": 0.10, "本地改善": 0.05, "exit": 0.05},
    # 上海
    "金融精英":     {"高净值康养": 0.02, "exit": 0.04},
    "外籍海归":     {"金融精英": 0.04, "exit": 0.08},
    "教育型买房":   {"本地改善": 0.10, "exit": 0.02},
    "长三角家庭":   {"本地改善": 0.05, "exit": 0.03},
    # 青岛
    "山东省内异地": {"本地改善": 0.06, "exit": 0.04},
    "北方度假投资": {"高端海景收藏": 0.02, "exit": 0.05},
    "高端海景收藏": {"exit": 0.05},
}

# 新增客群入场率（每年自然增长 / 政策吸引）
ARCHETYPE_INFLOW = {
    "三亚": {"外地候鸟-华北": 0.04, "度假投资": 0.05, "本地改善": 0.02, "高净值康养": 0.015},
    "杭州": {"互联网工程师": 0.06, "本地改善": 0.03, "学区刚需": 0.04, "人才落户": 0.05},
    "上海": {"外籍海归": 0.02, "教育型买房": 0.03, "金融精英": 0.03, "本地改善": 0.02},
    "青岛": {"本地改善": 0.03, "山东省内异地": 0.04, "北方度假投资": 0.02},
}


def project_personas_5year(city, horizon_years=5, n=1000, seed=42, target_year=None):
    """马尔可夫链 5 年客群结构演化预测"""
    import random as _r
    _r.seed(seed)
    personas = sample_personas(city, n, target_year=target_year)
    # 初始分布：archetype 占比
    initial = {}
    for p in personas:
        initial[p.archetype] = initial.get(p.archetype, 0) + 1
    total = sum(initial.values())
    dist = {k: v / total for k, v in initial.items()}

    history = [{"year": 0, "shares": dict(dist), "exit_cumulative": 0.0}]
    exit_cum = 0.0
    inflow_cfg = ARCHETYPE_INFLOW.get(city, {})

    for year in range(1, horizon_years + 1):
        new_dist = {k: v for k, v in dist.items()}  # 拷贝
        # 1. 应用转移矩阵
        for src, mat in ARCHETYPE_TRANSITIONS.items():
            src_share = new_dist.get(src, 0)
            if src_share <= 0:
                continue
            for tgt, rate in mat.items():
                moved = src_share * rate
                new_dist[src] -= moved
                if tgt == "exit":
                    exit_cum += moved
                else:
                    new_dist[tgt] = new_dist.get(tgt, 0) + moved
        # 2. 入场新增（已有 archetype 自然增长）
        for arch, rate in inflow_cfg.items():
            new_dist[arch] = new_dist.get(arch, 0) + rate * dist.get(arch, 0.01)
        # 3. 归一化
        s = sum(v for v in new_dist.values() if v > 0)
        if s > 0:
            dist = {k: max(0, v) / s for k, v in new_dist.items()}
        history.append({
            "year": year,
            "shares": dict(dist),
            "exit_cumulative": round(exit_cum, 3),
        })

    # 关键迁移：对比 Y0 vs Y5 显著变化（>3%）
    shifts = []
    y0 = history[0]["shares"]
    yN = history[-1]["shares"]
    all_keys = set(y0) | set(yN)
    for k in all_keys:
        delta = yN.get(k, 0) - y0.get(k, 0)
        if abs(delta) >= 0.03:
            shifts.append({"archetype": k,
                           "y0_share": round(y0.get(k, 0), 3),
                           f"y{horizon_years}_share": round(yN.get(k, 0), 3),
                           "delta": round(delta, 3),
                           "trend": "上升" if delta > 0 else "下降"})
    shifts.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return {
        "method": "客群马尔可夫迁移 v1",
        "city": city,
        "horizon_years": horizon_years,
        "yearly_distribution": history,
        "key_shifts": shifts,
        "exit_cumulative_pct": round(exit_cum * 100, 1),
    }


# ============================================================
# 8. 时空验证：用历史年份回测客群构成一致性
# ============================================================
def temporal_consistency(city, product, ref_years=(2015, 2020, 2024),
                         n_per_year=500, seed=42):
    """跑多个历史年份的 ABM，看 top 客群是否稳定 → 量化模型在该城市的可信度

    返回 jaccard(预测 top3 客群与历史 top3 客群)的均值，以及详细年度结果
    """
    import random as _r
    _r.seed(seed)
    yearly_top = []
    snapshots = []
    for y in ref_years:
        # 用该年的客群样本抽样 → 跑一次 buy_prob 聚合 top3
        ps = sample_personas(city, n_per_year, target_year=y)
        if not ps:
            continue
        arch_w = {}
        for p in ps:
            prob = buy_probability(p, product, [])
            arch_w[p.archetype] = arch_w.get(p.archetype, 0) + prob
        top3 = set(sorted(arch_w, key=lambda k: arch_w[k], reverse=True)[:3])
        yearly_top.append(top3)
        snapshots.append({
            "year": y,
            "top3": list(top3),
            "n_samples": len(ps),
        })
    if len(yearly_top) < 2:
        return {
            "status": "insufficient_history",
            "consistency_score": 0.0,
            "ref_years": list(ref_years),
            "snapshots": snapshots,
        }
    # 计算两两 Jaccard 相似度均值
    sims = []
    for i in range(len(yearly_top)):
        for j in range(i + 1, len(yearly_top)):
            a, b = yearly_top[i], yearly_top[j]
            sims.append(len(a & b) / max(len(a | b), 1))
    avg_sim = sum(sims) / len(sims) if sims else 0.0
    return {
        "status": "ok",
        "consistency_score": round(avg_sim, 3),
        "level": "高" if avg_sim >= 0.66 else ("中" if avg_sim >= 0.33 else "低"),
        "ref_years": list(ref_years),
        "snapshots": snapshots,
        "interpretation": _temporal_interpret(avg_sim, ref_years),
    }


def _temporal_interpret(sim, years):
    yrs = f"{min(years)}-{max(years)}"
    if sim >= 0.66:
        return f"{yrs} 跨年度 top3 客群高度重合（{sim*100:.0f}%），模型稳定，预测可信。"
    if sim >= 0.33:
        return f"{yrs} 跨年度 top3 客群中度重合（{sim*100:.0f}%），存在城市结构变迁，请关注客群迁移段。"
    return f"{yrs} 跨年度 top3 客群偏离严重（{sim*100:.0f}%），城市结构发生重大变化，模型外推须谨慎。"


# ============================================================
# 9. 历史均价加载（用于时间穿越对比）
# ============================================================
def load_historical_avg_price(city, year):
    """从 Vault 历史 CSV 加载某城某年的楼盘均价（元/㎡）"""
    import csv
    import re as _re
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent
    f = _root / "Vault" / f"{year}年" / f"{city}.csv"
    if not f.exists():
        return None
    prices = []
    try:
        with open(f, encoding="utf-8-sig") as fp:
            for r in csv.DictReader(fp):
                p = (r.get("最新价格") or "").strip()
                if p:
                    m = _re.search(r"(\d+)", p)
                    if m:
                        prices.append(int(m.group(1)))
    except Exception:
        return None
    if not prices:
        return None
    return int(sum(prices) / len(prices))

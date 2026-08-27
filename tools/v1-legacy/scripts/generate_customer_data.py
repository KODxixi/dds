# -*- coding: utf-8 -*-
"""
DDS 客群数据生成与物理落盘引擎
将 ABM 引擎中的客群原型 + 蒙特卡洛虚拟人口样本，按城市×年份物理落盘到 Vault。
每个年份目录生成两种 CSV：
  1. 客群画像-{城市}.csv  — 客群原型汇总（Archetype 级别，每城 5-6 行）
  2. 客群样本-{城市}.csv  — 蒙特卡洛虚拟人口样本（每城每年 500-2000 人）

年份演化逻辑：
  - 收入随时间按城市 GDP 增长率回归（越早收入越低）
  - 客群结构比例按马尔可夫迁移矩阵演化（早年外地候鸟少、本地刚需多）
  - 限购政策按历史节点切换
"""
import csv
import math
import os
import random
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent / "Vault"

# ── 城市客群原型定义（与 abm_engine.py 保持一致） ──

CITY_ARCHETYPES = {
    "三亚": [
        {"名称": "外地候鸟-华北", "占比": 0.25, "年收入均值_万": 25, "年收入标准差_万": 10, "资产倍数": 8, "首付比例": 0.5, "年龄下限": 55, "年龄上限": 75, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.10, "社会阶层": "B", "信息渠道": "network", "决策周期_月": 6, "风险厌恶": 0.55, "偏好_景观": 0.95, "偏好_私密": 0.70, "偏好_圈层": 0.60, "偏好_户型": 0.50, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.55},
        {"名称": "外地候鸟-东北", "占比": 0.15, "年收入均值_万": 20, "年收入标准差_万": 8, "资产倍数": 7, "首付比例": 0.5, "年龄下限": 58, "年龄上限": 78, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.05, "社会阶层": "C", "信息渠道": "network", "决策周期_月": 8, "风险厌恶": 0.60, "偏好_景观": 0.95, "偏好_私密": 0.70, "偏好_圈层": 0.50, "偏好_户型": 0.50, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.50},
        {"名称": "外地候鸟-西北", "占比": 0.10, "年收入均值_万": 22, "年收入标准差_万": 10, "资产倍数": 8, "首付比例": 0.5, "年龄下限": 55, "年龄上限": 75, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.10, "社会阶层": "C", "信息渠道": "network", "决策周期_月": 8, "风险厌恶": 0.60, "偏好_景观": 0.90, "偏好_私密": 0.65, "偏好_圈层": 0.50, "偏好_户型": 0.50, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.50},
        {"名称": "度假投资", "占比": 0.25, "年收入均值_万": 50, "年收入标准差_万": 20, "资产倍数": 10, "首付比例": 0.4, "年龄下限": 35, "年龄上限": 55, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.15, "社会阶层": "A", "信息渠道": "research", "决策周期_月": 2, "风险厌恶": 0.40, "偏好_景观": 0.85, "偏好_私密": 0.60, "偏好_圈层": 0.55, "偏好_户型": 0.45, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.75},
        {"名称": "本地改善", "占比": 0.15, "年收入均值_万": 15, "年收入标准差_万": 6, "资产倍数": 6, "首付比例": 0.35, "年龄下限": 30, "年龄上限": 50, "家庭人数": 4, "孩子数": 1, "老人同住率": 0.50, "社会阶层": "B", "信息渠道": "agent", "决策周期_月": 3, "风险厌恶": 0.55, "偏好_景观": 0.60, "偏好_私密": 0.45, "偏好_圈层": 0.40, "偏好_户型": 0.75, "偏好_通勤": 0.50, "偏好_学校": 0.70, "偏好_品牌": 0.45},
        {"名称": "高净值康养", "占比": 0.10, "年收入均值_万": 150, "年收入标准差_万": 70, "资产倍数": 12, "首付比例": 0.6, "年龄下限": 50, "年龄上限": 70, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.10, "社会阶层": "A", "信息渠道": "network", "决策周期_月": 4, "风险厌恶": 0.30, "偏好_景观": 0.95, "偏好_私密": 0.95, "偏好_圈层": 0.85, "偏好_户型": 0.60, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.90},
    ],
    "上海": [
        {"名称": "金融精英", "占比": 0.30, "年收入均值_万": 90, "年收入标准差_万": 40, "资产倍数": 8, "首付比例": 0.4, "年龄下限": 32, "年龄上限": 50, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.20, "社会阶层": "A", "信息渠道": "research", "决策周期_月": 3, "风险厌恶": 0.45, "偏好_景观": 0.65, "偏好_私密": 0.60, "偏好_圈层": 0.75, "偏好_户型": 0.75, "偏好_通勤": 0.70, "偏好_学校": 0.80, "偏好_品牌": 0.80},
        {"名称": "外籍海归", "占比": 0.25, "年收入均值_万": 70, "年收入标准差_万": 30, "资产倍数": 7, "首付比例": 0.4, "年龄下限": 30, "年龄上限": 45, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.15, "社会阶层": "A", "信息渠道": "social", "决策周期_月": 3, "风险厌恶": 0.50, "偏好_景观": 0.60, "偏好_私密": 0.55, "偏好_圈层": 0.80, "偏好_户型": 0.70, "偏好_通勤": 0.65, "偏好_学校": 0.85, "偏好_品牌": 0.75},
        {"名称": "本地改善", "占比": 0.20, "年收入均值_万": 40, "年收入标准差_万": 18, "资产倍数": 10, "首付比例": 0.45, "年龄下限": 38, "年龄上限": 58, "家庭人数": 4, "孩子数": 2, "老人同住率": 0.45, "社会阶层": "B", "信息渠道": "agent", "决策周期_月": 3, "风险厌恶": 0.50, "偏好_景观": 0.55, "偏好_私密": 0.50, "偏好_圈层": 0.55, "偏好_户型": 0.85, "偏好_通勤": 0.60, "偏好_学校": 0.70, "偏好_品牌": 0.60},
        {"名称": "教育型买房", "占比": 0.15, "年收入均值_万": 55, "年收入标准差_万": 22, "资产倍数": 8, "首付比例": 0.4, "年龄下限": 32, "年龄上限": 48, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.40, "社会阶层": "B", "信息渠道": "social", "决策周期_月": 1, "风险厌恶": 0.50, "偏好_景观": 0.40, "偏好_私密": 0.40, "偏好_圈层": 0.60, "偏好_户型": 0.70, "偏好_通勤": 0.55, "偏好_学校": 0.98, "偏好_品牌": 0.60},
        {"名称": "长三角家庭", "占比": 0.10, "年收入均值_万": 45, "年收入标准差_万": 18, "资产倍数": 7, "首付比例": 0.45, "年龄下限": 35, "年龄上限": 55, "家庭人数": 4, "孩子数": 2, "老人同住率": 0.45, "社会阶层": "B", "信息渠道": "agent", "决策周期_月": 3, "风险厌恶": 0.55, "偏好_景观": 0.55, "偏好_私密": 0.50, "偏好_圈层": 0.50, "偏好_户型": 0.80, "偏好_通勤": 0.40, "偏好_学校": 0.70, "偏好_品牌": 0.65},
    ],
    "杭州": [
        {"名称": "互联网工程师", "占比": 0.35, "年收入均值_万": 55, "年收入标准差_万": 22, "资产倍数": 6, "首付比例": 0.3, "年龄下限": 28, "年龄上限": 40, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.30, "社会阶层": "B", "信息渠道": "social", "决策周期_月": 2, "风险厌恶": 0.50, "偏好_景观": 0.50, "偏好_私密": 0.45, "偏好_圈层": 0.55, "偏好_户型": 0.80, "偏好_通勤": 0.85, "偏好_学校": 0.70, "偏好_品牌": 0.60},
        {"名称": "本地改善", "占比": 0.25, "年收入均值_万": 35, "年收入标准差_万": 15, "资产倍数": 9, "首付比例": 0.4, "年龄下限": 35, "年龄上限": 55, "家庭人数": 4, "孩子数": 1, "老人同住率": 0.50, "社会阶层": "B", "信息渠道": "agent", "决策周期_月": 3, "风险厌恶": 0.50, "偏好_景观": 0.55, "偏好_私密": 0.50, "偏好_圈层": 0.50, "偏好_户型": 0.85, "偏好_通勤": 0.70, "偏好_学校": 0.75, "偏好_品牌": 0.55},
        {"名称": "浙商投资", "占比": 0.20, "年收入均值_万": 120, "年收入标准差_万": 60, "资产倍数": 10, "首付比例": 0.5, "年龄下限": 40, "年龄上限": 60, "家庭人数": 4, "孩子数": 1, "老人同住率": 0.20, "社会阶层": "A", "信息渠道": "network", "决策周期_月": 2, "风险厌恶": 0.45, "偏好_景观": 0.60, "偏好_私密": 0.55, "偏好_圈层": 0.70, "偏好_户型": 0.50, "偏好_通勤": 0.30, "偏好_学校": 0.40, "偏好_品牌": 0.85},
        {"名称": "学区刚需", "占比": 0.15, "年收入均值_万": 30, "年收入标准差_万": 12, "资产倍数": 7, "首付比例": 0.3, "年龄下限": 30, "年龄上限": 45, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.30, "社会阶层": "C", "信息渠道": "agent", "决策周期_月": 1, "风险厌恶": 0.55, "偏好_景观": 0.30, "偏好_私密": 0.30, "偏好_圈层": 0.40, "偏好_户型": 0.70, "偏好_通勤": 0.60, "偏好_学校": 0.95, "偏好_品牌": 0.50},
        {"名称": "人才落户", "占比": 0.05, "年收入均值_万": 45, "年收入标准差_万": 18, "资产倍数": 5, "首付比例": 0.3, "年龄下限": 28, "年龄上限": 38, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.15, "社会阶层": "B", "信息渠道": "research", "决策周期_月": 2, "风险厌恶": 0.60, "偏好_景观": 0.40, "偏好_私密": 0.35, "偏好_圈层": 0.50, "偏好_户型": 0.75, "偏好_通勤": 0.85, "偏好_学校": 0.60, "偏好_品牌": 0.55},
    ],
    "青岛": [
        {"名称": "本地改善", "占比": 0.45, "年收入均值_万": 22, "年收入标准差_万": 10, "资产倍数": 8, "首付比例": 0.4, "年龄下限": 35, "年龄上限": 55, "家庭人数": 4, "孩子数": 1, "老人同住率": 0.35, "社会阶层": "B", "信息渠道": "agent", "决策周期_月": 3, "风险厌恶": 0.50, "偏好_景观": 0.75, "偏好_私密": 0.50, "偏好_圈层": 0.50, "偏好_户型": 0.85, "偏好_通勤": 0.60, "偏好_学校": 0.70, "偏好_品牌": 0.55},
        {"名称": "山东省内异地", "占比": 0.25, "年收入均值_万": 25, "年收入标准差_万": 12, "资产倍数": 8, "首付比例": 0.45, "年龄下限": 40, "年龄上限": 60, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.35, "社会阶层": "C", "信息渠道": "network", "决策周期_月": 4, "风险厌恶": 0.55, "偏好_景观": 0.85, "偏好_私密": 0.55, "偏好_圈层": 0.50, "偏好_户型": 0.70, "偏好_通勤": 0.00, "偏好_学校": 0.40, "偏好_品牌": 0.55},
        {"名称": "北方度假投资", "占比": 0.15, "年收入均值_万": 45, "年收入标准差_万": 20, "资产倍数": 10, "首付比例": 0.5, "年龄下限": 45, "年龄上限": 65, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.10, "社会阶层": "B", "信息渠道": "research", "决策周期_月": 3, "风险厌恶": 0.50, "偏好_景观": 0.95, "偏好_私密": 0.60, "偏好_圈层": 0.50, "偏好_户型": 0.50, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.70},
        {"名称": "学区刚需", "占比": 0.10, "年收入均值_万": 20, "年收入标准差_万": 8, "资产倍数": 6, "首付比例": 0.3, "年龄下限": 30, "年龄上限": 45, "家庭人数": 3, "孩子数": 1, "老人同住率": 0.30, "社会阶层": "C", "信息渠道": "agent", "决策周期_月": 1, "风险厌恶": 0.55, "偏好_景观": 0.40, "偏好_私密": 0.30, "偏好_圈层": 0.40, "偏好_户型": 0.75, "偏好_通勤": 0.55, "偏好_学校": 0.95, "偏好_品牌": 0.45},
        {"名称": "高端海景收藏", "占比": 0.05, "年收入均值_万": 130, "年收入标准差_万": 60, "资产倍数": 11, "首付比例": 0.6, "年龄下限": 45, "年龄上限": 65, "家庭人数": 2, "孩子数": 0, "老人同住率": 0.10, "社会阶层": "A", "信息渠道": "network", "决策周期_月": 4, "风险厌恶": 0.35, "偏好_景观": 0.98, "偏好_私密": 0.85, "偏好_圈层": 0.75, "偏好_户型": 0.55, "偏好_通勤": 0.00, "偏好_学校": 0.00, "偏好_品牌": 0.85},
    ],
}

# 城市收入年均增长率（用于时间回归）
INCOME_GROWTH = {
    "上海": 0.085,
    "杭州": 0.09,
    "青岛": 0.07,
    "三亚": 0.065,
}

# 客群结构年代演化系数（相对于2026基准的调整）
# 核心逻辑：越早年代，外地客群占比越低，本地刚需占比越高
def get_year_archetype_adjustment(city, archetype_name, target_year):
    """返回该客群在目标年份的占比调整乘数"""
    years_back = 2026 - target_year
    
    # 外地/投资类客群：越早越少
    if "外地" in archetype_name or "候鸟" in archetype_name:
        return max(0.1, 1.0 - years_back * 0.04)
    elif "投资" in archetype_name or "收藏" in archetype_name:
        return max(0.15, 1.0 - years_back * 0.035)
    elif "海归" in archetype_name or "外籍" in archetype_name:
        return max(0.1, 1.0 - years_back * 0.05)
    elif "互联网" in archetype_name or "人才" in archetype_name:
        # 互联网/人才：2015 年以后才大量涌入
        if target_year < 2010:
            return 0.1
        elif target_year < 2015:
            return 0.4
        else:
            return max(0.6, 1.0 - years_back * 0.02)
    elif "康养" in archetype_name:
        # 康养：近年才兴起
        if target_year < 2015:
            return 0.15
        else:
            return max(0.4, 1.0 - years_back * 0.03)
    # 本地改善/学区刚需：越早占比越高
    elif "本地" in archetype_name or "刚需" in archetype_name:
        return min(1.8, 1.0 + years_back * 0.025)
    elif "学区" in archetype_name:
        return min(1.5, 1.0 + years_back * 0.02)
    elif "浙商" in archetype_name or "金融" in archetype_name:
        return max(0.3, 1.0 - years_back * 0.02)
    elif "长三角" in archetype_name or "省内" in archetype_name:
        return max(0.4, 1.0 - years_back * 0.015)
    else:
        return 1.0


# 限购政策历史节点
PURCHASE_LIMIT_HISTORY = {
    "上海": {
        # 年份: (本地最大套数, 外地最大套数, 外地社保年限)
        2026: (2, 1, 5), 2020: (2, 1, 5), 2016: (2, 1, 5),
        2011: (2, 1, 2), 2010: (3, 2, 1), 2005: (99, 99, 0),
        1995: (99, 99, 0),
    },
    "杭州": {
        2026: (2, 1, 4), 2022: (2, 1, 4), 2017: (2, 1, 2),
        2011: (2, 1, 1), 2010: (3, 2, 0), 2005: (99, 99, 0),
        1995: (99, 99, 0),
    },
    "青岛": {
        2026: (3, 2, 0), 2018: (2, 1, 1), 2017: (2, 1, 0),
        2010: (99, 99, 0), 1995: (99, 99, 0),
    },
    "三亚": {
        2026: (2, 1, 5), 2018: (2, 1, 5), 2017: (2, 1, 2),
        2010: (3, 2, 0), 2005: (99, 99, 0), 1995: (99, 99, 0),
    },
}

def get_purchase_limit(city, target_year):
    """获取指定年份的限购政策"""
    limits = PURCHASE_LIMIT_HISTORY.get(city, {})
    for year in sorted(limits.keys(), reverse=True):
        if target_year >= year:
            local_max, nonlocal_max, social_years = limits[year]
            return {"本地最大套数": local_max, "外地最大套数": nonlocal_max, "外地社保年限要求": social_years}
    return {"本地最大套数": 99, "外地最大套数": 99, "外地社保年限要求": 0}


# ── 画像 CSV 表头 ──
PROFILE_HEADERS = [
    "城市", "年份", "客群名称", "占比", "年收入均值_万", "年收入标准差_万",
    "资产倍数", "首付比例", "年龄下限", "年龄上限", "家庭人数", "孩子数",
    "老人同住率", "社会阶层", "信息渠道", "决策周期_月", "风险厌恶",
    "偏好_景观", "偏好_私密", "偏好_圈层", "偏好_户型", "偏好_通勤",
    "偏好_学校", "偏好_品牌",
    "限购_本地最大套数", "限购_外地最大套数", "限购_外地社保年限",
]

# ── 样本 CSV 表头 ──
SAMPLE_HEADERS = [
    "样本ID", "城市", "年份", "客群类型", "年龄", "性别",
    "年收入_万", "总资产_万", "首付比例", "预算上限_万",
    "家庭人数", "孩子数", "老人同住", "社会阶层", "信息渠道",
    "决策紧迫度", "风险厌恶",
    "偏好_景观", "偏好_私密", "偏好_圈层", "偏好_户型",
    "偏好_通勤", "偏好_学校", "偏好_品牌",
    "购房目的", "意向户型", "意向面积_㎡", "支付意愿_元每㎡",
    # ↓ 10 大高维语义中文加厚字段
    "细分职业", "家庭生命周期阶段", "目前居住状况", "目前居住痛点",
    "核心看房细节需求", "职住通勤偏好", "生活方式偏好",
    "首付资金来源", "品牌与服务诉求", "旅居度假频次"
]


def truncnorm(mu, sigma, lo=0.5):
    """截断正态分布采样"""
    for _ in range(100):
        v = random.gauss(mu, sigma)
        if v >= lo:
            return v
    return lo


def synthesize_semantics(city, target_year, arch_name, age, kids, elderly, social_class, purpose, unit_type, area, prefs):
    """为微观主体动态进行中文有血有肉的人格语义微雕合成"""
    # 1. 细分职业
    job = "自由职业"
    if "金融" in arch_name:
        if target_year >= 2010:
            job = random.choice(["陆家嘴外资投行VP", "券商研究所资深有色分析师", "量化私募合伙人", "信托财富管理总监"])
        else:
            job = random.choice(["商业银行支行信贷科长", "老牌证券公司营业部经理", "申银万国证券分析员"])
    elif "外籍" in arch_name or "海归" in arch_name:
        if target_year >= 2010:
            job = random.choice(["跨国车企前瞻设计总监", "外资制药巨头亚太研发主管", "麦肯锡资深咨询顾问"])
        else:
            job = random.choice(["外企驻沪办事处代表", "合资日化企业技术主管"])
    elif "互联网" in arch_name or "工程师" in arch_name:
        if target_year >= 2010:
            job = random.choice(["阿里 P7 资深技术专家", "大厂 AI 大模型算法总监", "电商系统架构师", "独角兽前端负责人"])
        else:
            job = random.choice(["电信局宽带技术主管", "早期网络科技公司程序员", "系统网络集成工程师"])
    elif "浙商" in arch_name or "民营" in arch_name:
        if target_year >= 2010:
            job = random.choice(["绍兴纺织印染厂老板", "义乌小商品外贸巨头", "温州阀门制造厂投资人"])
        else:
            job = random.choice(["温州鞋厂老板", "浙中皮具厂合伙人"])
    elif "候鸟" in arch_name or "度假" in arch_name or "收藏" in arch_name or "康养" in arch_name:
        if "华北" in arch_name or "北京" in arch_name:
            job = random.choice(["北京三甲医院退休教授", "央企总部退休总工", "高校资深硕导"])
        elif "东北" in arch_name:
            job = random.choice(["沈阳大型重工企业退休副厂长", "哈尔滨高校退休处长", "哈尔滨私营物流老板"])
        elif "西北" in arch_name:
            job = random.choice(["西安设计院退休总建筑师", "兰州煤炭物流运输企业主"])
        else:
            job = random.choice(["上市公司退休联合创始人", "知名中医药连锁投资人", "高端海景资产收藏家"])
    else:
        # 通用改善/刚需
        if target_year >= 2010:
            job = random.choice(["三甲医院主治医师", "重点中学高级教师", "区级税务局一级主办", "国企中层管理", "智能新能源车企研发骨干"])
        else:
            job = random.choice(["区财政局科员", "市第二中学地理教师", "国企车间技术主任", "邮政局局长秘书"])

    # 2. 家庭生命周期阶段
    if age < 30 and kids == 0:
        stage = "单身贵族首套"
    elif age <= 35 and kids == 0:
        stage = "新婚过渡两居"
    elif kids == 1:
        stage = "三口之家(幼龄段)" if age < 38 else "三口之家(学龄段)"
    elif kids >= 2:
        stage = "二胎黄金期中大户型"
    elif age >= 55:
        stage = "高知退休康养"
    else:
        stage = "成熟期核心家庭"

    # 3. 目前居住状况
    if social_class == "A":
        living = random.choice(["陆家嘴核心区江景大平层", "西湖边高精平层", "市中心私密洋房住宅"])
    elif social_class == "B":
        living = f"{city}中环品质两居，车位及空间极其拥挤"
    else:
        living = f"与父母同住，三代人拥挤在旧区小户型老房中"

    # 4. 目前居住痛点
    if kids >= 1 and social_class in ("B", "C"):
        pain = "小区无地下车库且人车不分流，小孩在楼下玩耍或者推婴儿车极为不安全。"
    elif elderly == "是":
        pain = "当前房屋属于旧多层无电梯房，父母年纪渐高每天爬楼梯极为吃力。"
    elif social_class == "A":
        pain = "无高端私密会所及管家式物业配套，圈层鱼龙混杂，高净值居住体验差。"
    else:
        pain = "老小区隔音差，邻里滋扰严重，且车位严重匮乏，每晚加班回来抢车位像打仗。"

    # 5. 核心看房细节需求
    if prefs.get("私密", 0.5) > 0.7 or "别墅" in unit_type:
        detail = "要求独立电梯入户，主次卧动线分明，配备独立保姆房和私密后院。"
    elif prefs.get("景观", 0.5) > 0.7:
        detail = "要求有超大面宽南向双阳台（观景家政分离），主卧大飘窗能270度直面自然景观。"
    elif prefs.get("户型", 0.5) > 0.7:
        detail = "讲究超高得房率（超85%），主卧套房必须配备独立步入式衣帽间与干湿分离双卫。"
    elif prefs.get("学校", 0.5) > 0.7:
        detail = "核心要求地块属于第一梯队名校学区，且周边500米内解决幼小衔接教育配套。"
    else:
        detail = "要求采光通透，全明户型，带多功能嵌入式收纳墙以最大化空间利用。"

    # 6. 职住通勤偏好
    if "三亚" in city:
        commute = "无需日常通勤，但要求30分钟内快捷通达凤凰机场或高铁站。"
    elif "上海" in city:
        commute = "主要在陆家嘴/徐家汇CBD上班，首选地铁2/9号线沿线，通勤半小时内。"
    elif "杭州" in city:
        commute = "在阿里西溪园区/钱江新城上班，期望有便利的高架或地铁接驳。"
    else:
        commute = "在市中心核心写字楼工作，首选半小时核心通勤圈内。"

    # 7. 生活方式偏好
    if social_class == "A":
        lifestyle = "周末常去高端商圈消费，热衷打网球和马术，重度手冲咖啡及黑胶唱片发烧友。"
    elif "三亚" in city:
        lifestyle = "崇尚度假慢生活，清晨沙滩漫步，午后椰林下午茶，酷爱轻户外及后海冲浪。"
    elif social_class == "B":
        lifestyle = "倡导轻户外，周末常带家人自驾野营或山系徒步，注重生活情调与手作咖啡。"
    else:
        lifestyle = "爱猫狗，喜欢烹饪和插花，主打经济舒适的生活小确幸。"

    # 8. 首付资金来源
    if purpose in ("自住-首套", "婚房"):
        funds = "夫妻双方积蓄 + 双方父母出资赞助首付 + 公积金商业组合贷款"
    elif social_class == "A" or "别墅" in unit_type:
        funds = "名下已有企业股权套现或分红现金，一次性全款付清，基本不加杠杆"
    else:
        funds = "名下持有一套旧两居，置换回款 65% + 个人理财储蓄补充 + 公积金商业贷"

    # 9. 品牌与服务诉求
    if prefs.get("品牌", 0.5) > 0.7:
        brand_req = "非仁恒、绿城、万科等金牌开发商不买，极度看重高端精装质量与顶级物业的增值口碑。"
    else:
        brand_req = "重性价比，不盲信大牌，但要求物业管理有序、绿化修剪及时、人防技防规范。"

    # 10. 旅居度假频次
    if "三亚" in city or "青岛" in city:
        if "候鸟" in arch_name or "旅居" in arch_name or "养老" in arch_name or "度假" in arch_name:
            frequency = "每年11月至次年3月到此度过漫长冬季，极其依赖周边的康养及适老化服务。"
        else:
            frequency = "本地常住人口，四季生活规律，度假频次为常规年假出行。"
    else:
        frequency = "常年本地稳定居住，工作日职住通勤动线规则单一。"

    return {
        "细分职业": job,
        "家庭生命周期阶段": stage,
        "目前居住状况": living,
        "目前居住痛点": pain,
        "核心看房细节需求": detail,
        "职住通勤偏好": commute,
        "生活方式偏好": lifestyle,
        "首付资金来源": funds,
        "品牌与服务诉求": brand_req,
        "旅居度假频次": frequency
    }


def generate_samples(city, archetypes, target_year, n_samples):
    """为指定城市和年份生成蒙特卡洛虚拟人口样本"""
    samples = []
    growth = INCOME_GROWTH.get(city, 0.07)
    years_back = 2026 - target_year
    income_factor = 1.0 / ((1 + growth) ** years_back)  # 收入时间回归
    
    # 构建加权选择列表
    weights = [a["占比"] for a in archetypes]
    
    for i in range(n_samples):
        # 加权随机选择客群原型
        arch = random.choices(archetypes, weights=weights, k=1)[0]
        
        # 收入抽样（历史回归）
        income_2026 = truncnorm(arch["年收入均值_万"], arch["年收入标准差_万"], lo=3)
        income = income_2026 * income_factor
        
        # 资产计算
        asset = income * arch["资产倍数"] * random.uniform(0.7, 1.3)
        dp = arch["首付比例"]
        budget = asset * 0.75 / max(dp, 0.3)
        
        # 年龄
        age = random.randint(arch["年龄下限"], arch["年龄上限"])
        
        # 性别
        sex = random.choice(["男", "女"])
        
        # 家庭维度
        fam_size = max(1, int(round(random.gauss(arch["家庭人数"], 0.6))))
        kids = max(0, min(3, int(round(random.gauss(arch["孩子数"], 0.5)))))
        elderly = "是" if random.random() < arch["老人同住率"] else "否"
        
        # 决策紧迫度
        urgency = max(0, min(1, random.gauss(1.0 - arch["决策周期_月"] / 12, 0.15)))
        
        # 偏好（加噪声）
        prefs = {}
        for k in ["景观", "私密", "圈层", "户型", "通勤", "学校", "品牌"]:
            base = arch.get(f"偏好_{k}", 0.5)
            prefs[k] = max(0, min(1, base + random.gauss(0, 0.08)))
        
        # 购房目的推断
        if "投资" in arch["名称"] or "收藏" in arch["名称"]:
            purpose = random.choice(["投资-长持", "投资-短期", "度假"])
        elif "候鸟" in arch["名称"] or "康养" in arch["名称"]:
            purpose = random.choice(["养老", "度假"])
        elif "学区" in arch["名称"] or "教育" in arch["名称"]:
            purpose = "学区房"
        elif "改善" in arch["名称"]:
            purpose = "自住-改善"
        elif "刚需" in arch["名称"] or age < 35:
            purpose = random.choice(["自住-首套", "婚房"])
        else:
            purpose = random.choice(["自住-首套", "自住-改善"])
        
        # 意向户型推断
        if budget > 500:
            unit_type = random.choice(["四室两厅", "别墅", "复式"])
            area = random.randint(160, 350)
        elif budget > 300:
            unit_type = random.choice(["三室两厅", "四室两厅"])
            area = random.randint(120, 200)
        elif budget > 150:
            unit_type = random.choice(["两室两厅", "三室一厅", "三室两厅"])
            area = random.randint(90, 150)
        else:
            unit_type = random.choice(["一室一厅", "两室一厅", "两室两厅"])
            area = random.randint(50, 100)
        
        # 支付意愿
        wtp = budget * 10000 / max(area, 50) * random.uniform(0.5, 0.9)
        
        # 合成语义文本加厚字段
        semantics = synthesize_semantics(
            city=city, target_year=target_year, arch_name=arch["名称"],
            age=age, kids=kids, elderly=elderly, social_class=arch["社会阶层"],
            purpose=purpose, unit_type=unit_type, area=area, prefs=prefs
        )
        
        sample_dict = {
            "样本ID": f"{city[:1]}_{target_year}_{i+1:05d}",
            "城市": city,
            "年份": target_year,
            "客群类型": arch["名称"],
            "年龄": age,
            "性别": sex,
            "年收入_万": round(income, 1),
            "总资产_万": round(asset, 1),
            "首付比例": round(dp, 2),
            "预算上限_万": round(budget, 1),
            "家庭人数": fam_size,
            "孩子数": kids,
            "老人同住": elderly,
            "社会阶层": arch["社会阶层"],
            "信息渠道": arch["信息渠道"],
            "决策紧迫度": round(urgency, 2),
            "风险厌恶": round(max(0, min(1, arch["风险厌恶"] + random.gauss(0, 0.08))), 2),
            "偏好_景观": round(prefs["景观"], 2),
            "偏好_私密": round(prefs["私密"], 2),
            "偏好_圈层": round(prefs["圈层"], 2),
            "偏好_户型": round(prefs["户型"], 2),
            "偏好_通勤": round(prefs["通勤"], 2),
            "偏好_学校": round(prefs["学校"], 2),
            "偏好_品牌": round(prefs["品牌"], 2),
            "购房目的": purpose,
            "意向户型": unit_type,
            "意向面积_㎡": area,
            "支付意愿_元每㎡": int(wtp),
        }
        
        # 合入 10 大语义属性列
        sample_dict.update(semantics)
        samples.append(sample_dict)
    
    return samples



def process_city(city, dry_run=False):
    """处理单城市全年份客群数据生成"""
    archetypes_base = CITY_ARCHETYPES[city]
    growth = INCOME_GROWTH.get(city, 0.07)
    total_profiles = 0
    total_samples = 0
    
    print(f"\n{'='*50}")
    print(f" 正在生成城市 【{city}】 的客群数据 (1995-2026)...")
    print(f"{'='*50}")
    
    for target_year in range(1995, 2027):
        year_dir = VAULT / f"{target_year}年"
        year_dir.mkdir(parents=True, exist_ok=True)
        
        years_back = 2026 - target_year
        income_factor = 1.0 / ((1 + growth) ** years_back)
        
        # 1. 调整客群占比
        adjusted = []
        for arch in archetypes_base:
            a = dict(arch)
            adj = get_year_archetype_adjustment(city, a["名称"], target_year)
            a["占比"] = arch["占比"] * adj
            # 收入时间回归
            a["年收入均值_万"] = round(arch["年收入均值_万"] * income_factor, 1)
            a["年收入标准差_万"] = round(arch["年收入标准差_万"] * income_factor, 1)
            adjusted.append(a)
        
        # 归一化占比
        total_weight = sum(a["占比"] for a in adjusted)
        for a in adjusted:
            a["占比"] = round(a["占比"] / total_weight, 3)
        
        # 获取限购政策
        limits = get_purchase_limit(city, target_year)
        
        # 2. 写客群画像 CSV
        profile_file = year_dir / f"客群画像-{city}.csv"
        with open(profile_file, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(PROFILE_HEADERS)
            for a in adjusted:
                row = [
                    city, target_year, a["名称"], a["占比"],
                    a["年收入均值_万"], a["年收入标准差_万"], a["资产倍数"], a["首付比例"],
                    a["年龄下限"], a["年龄上限"], a["家庭人数"], a["孩子数"],
                    a["老人同住率"], a["社会阶层"], a["信息渠道"], a["决策周期_月"], a["风险厌恶"],
                    a["偏好_景观"], a["偏好_私密"], a["偏好_圈层"], a["偏好_户型"],
                    a["偏好_通勤"], a["偏好_学校"], a["偏好_品牌"],
                    limits["本地最大套数"], limits["外地最大套数"], limits["外地社保年限要求"],
                ]
                writer.writerow(row)
        total_profiles += len(adjusted)
        
        # 3. 生成蒙特卡洛样本
        # 样本数量与年代活跃度成正比（升级3-5倍，提供海量彭博级数据）
        if target_year < 2000:
            n_samples = 1000
        elif target_year < 2005:
            n_samples = 1500
        elif target_year < 2010:
            n_samples = 2000
        elif target_year < 2015:
            n_samples = 3000
        elif target_year < 2020:
            n_samples = 4000
        else:
            n_samples = 5000

        
        random.seed(hash(f"{city}_{target_year}_客群") % 2**32)
        samples = generate_samples(city, adjusted, target_year, n_samples)
        
        sample_file = year_dir / f"客群样本-{city}.csv"
        with open(sample_file, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(SAMPLE_HEADERS)
            for s in samples:
                row = [s.get(h, "") for h in SAMPLE_HEADERS]
                writer.writerow(row)
        total_samples += len(samples)
        
        if not dry_run:
            print(f"  [+] {target_year}年: 画像 {len(adjusted)} 条 | 样本 {len(samples)} 条")
    
    print(f"\n [OK] 城市 【{city}】 客群数据生成完成！")
    print(f"      画像记录: {total_profiles} 条 | 样本记录: {total_samples} 条")
    return total_profiles, total_samples


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DDS 客群数据生成与物理落盘引擎")
    parser.add_argument("--city", choices=["上海", "杭州", "青岛", "三亚"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    print("\n" + "=" * 64)
    print(" [*] DDS 客群数据生成引擎启动")
    print("     输出: 客群画像-{城市}.csv + 客群样本-{城市}.csv")
    print("     维度: 收入/资产/偏好/限购/家庭/购房目的/支付意愿")
    print("=" * 64)
    
    cities = [args.city] if args.city else ["上海", "杭州", "青岛", "三亚"]
    grand_profiles = 0
    grand_samples = 0
    
    for city in cities:
        try:
            p, s = process_city(city, dry_run=args.dry_run)
            grand_profiles += p
            grand_samples += s
        except Exception as e:
            print(f" [error] 处理城市 【{city}】 失败: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 64)
    print(f" [OK] 全部完成！")
    print(f"      画像总计: {grand_profiles} 条 | 样本总计: {grand_samples} 条")
    print(f"      文件路径: Vault/{{年份}}年/客群画像-{{城市}}.csv")
    print(f"                Vault/{{年份}}年/客群样本-{{城市}}.csv")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
multimodal_valuation.py — 多模态房产估值模型原型

背景：AIPM 报告 Section 6.4 建议引入多模态估值（Huang et al. 2025），
结合文本属性+图片+GIS 空间特征进行房产估值。当前 DDS 的 premium_engine.py
仅用结构化数值特征。ArchLib 有 ~9000 张图片 + VLM 标签，可作为多模态输入。

三模态特征融合：
  value = w1*structured_score + w2*visual_score + w3*spatial_score
  默认权重：w1=0.5, w2=0.25, w3=0.25（可配置）

设计原则（对齐 DDS"禁止脑补"）：
  - 纯标准库 + numpy 实现，不依赖深度学习框架
  - 视觉特征无 ArchLib 数据时优雅降级为仅结构化
  - 所有输出标注 SOURCE/SOURCE_URL/SOURCE_NOTE
  - 模块可独立运行并出 demo 报告

用法：
  python scripts/multimodal_valuation.py --city 三亚 --project "某某楼盘"
  python scripts/multimodal_valuation.py --city 三亚 --weights 0.5,0.25,0.25
  python scripts/multimodal_valuation.py --city 三亚 --benchmark
  python scripts/multimodal_valuation.py --city 三亚 --output report.json
  python scripts/multimodal_valuation.py --demo  # 内置 demo 运行

参考：
  - Huang et al. (2025) "Multimodal Real Estate Valuation"
  - DDS premium_engine.py（六维驱动）
  - DDS archlib_vision.py（VLM 标签聚合）
  - DDS schema_dds.py（253 列规范）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

# ── 项目路径 ──────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

# ── 常量 ──────────────────────────────────────────────────────────────────────
DEFAULT_WEIGHTS = (0.5, 0.25, 0.25)  # w1, w2, w3
SCORE_RANGE = (0, 100)
ARCHLIB_DEFAULT = r"D:/ArchLib"

# ============================================================================
#  数据来源标注工具
# ============================================================================

SOURCE_REGISTRY = {
    "structured": {
        "SOURCE": "DDS premium_engine (六维驱动)",
        "SOURCE_URL": "internal://scripts/premium_engine.py",
        "SOURCE_NOTE": (
            "基于结构化特征（区位/产品力/品牌/稀缺/市场/政策）的六维驱动估值，"
            "数据源为 Vault CSV（253 列 Schema v2.0），部分维度标注待接入。"
        ),
    },
    "visual": {
        "SOURCE": "ArchLib 逐图 VLM 打标（images_claude.jsonl）",
        "SOURCE_URL": "internal://D:/ArchLib/_检索系统/data/images_claude.jsonl",
        "SOURCE_NOTE": (
            "视觉特征来自 ArchLib 案例库的 VLM 逐图打标（立面档次/景观品质/公区配置/设计创新），"
            "打标进行中，覆盖随增长。缺失时降级为仅结构化。"
        ),
    },
    "spatial": {
        "SOURCE": "DDS gnn_spatial.py（空间依赖修正 + 邻居价格加权）",
        "SOURCE_URL": "internal://scripts/gnn_spatial.py",
        "SOURCE_NOTE": (
            "空间特征基于 GIS 坐标 + Haversine 邻居加权均价，"
            "GNN embedding 待接入（当前为 GNN stub）。"
        ),
    },
    "fusion": {
        "SOURCE": "DDS multimodal_valuation.py（加权融合）",
        "SOURCE_URL": "internal://scripts/multimodal_valuation.py",
        "SOURCE_NOTE": (
            "三模态加权融合：value = w1*structured + w2*visual + w3*spatial，"
            "默认权重 0.5/0.25/0.25，参考 Huang et al. (2025) 多模态估值框架。"
        ),
    },
}


def _source_block(modality: str) -> dict:
    """返回指定模态的数据来源标注块。"""
    return dict(SOURCE_REGISTRY.get(modality, {}))


# ============================================================================
#  Haversine 距离（纯 numpy）
# ============================================================================

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """计算两点间球面距离（米）。"""
    R = 6371000.0
    phi1, phi2 = np.radians([lat1, lat2])
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lng2 - lng1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return float(R * 2 * np.arctan2(np.sqrt(a), np.sqrt(max(1 - a, 0))))


# ============================================================================
#  PART 1: 结构化特征评分（复用 premium_engine 六维驱动）
# ============================================================================

class StructuredFeatureScorer:
    """基于 253 列结构化特征 + 六维驱动模型的评分器。

    六维驱动：区位 / 产品力 / 品牌 / 稀缺 / 市场 / 政策
    每维输出 0-100 分 + 证据 + 置信度。
    """

    SIX_DIMS = [
        ("location", "区位升值", 0.25),
        ("product", "产品力", 0.20),
        ("brand", "品牌运营", 0.15),
        ("scarcity", "稀缺资源", 0.15),
        ("market", "市场供需", 0.15),
        ("policy", "政策代差", 0.10),
    ]

    def __init__(self, parcel_data: dict | None = None,
                 market_context: dict | None = None):
        self.parcel_data = parcel_data or {}
        self.market_context = market_context or {}

    def _f(self, x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    def _get(self, *keys) -> Any:
        for k in keys:
            v = self.parcel_data.get(k)
            if v is not None and v != "":
                return v
        return None

    def _score_location(self) -> dict:
        """区位升值：基于坐标/区域/环线/价格趋势。"""
        evidence = []
        score = 50.0  # 基准分
        confidence = 0.3

        # 环线位置（越靠内环越高）
        ring = self._get("环线位置", "ring_location")
        if ring:
            ring_str = str(ring).lower()
            if "内环" in ring_str or "一环" in ring_str or "二环" in ring_str:
                score += 25
            elif "三环" in ring_str:
                score += 15
            elif "四环" in ring_str or "五环" in ring_str:
                score += 5
            evidence.append(f"环线={ring}")
            confidence += 0.1

        # 坐标有效性
        lat = self._get("百度地图纬度", "blat")
        lng = self._get("百度地图经度", "blng")
        if lat and lng:
            evidence.append(f"坐标=({lat},{lng})")
            confidence += 0.1

        # 区域均价
        area_price = self._get("周边均价", "area_avg_nearby_price")
        if area_price:
            score += 10
            evidence.append(f"周边均价={area_price}")
            confidence += 0.05

        # 价格趋势（来自 market_context）
        trend = self.market_context.get("price_trend_6m") or self.market_context.get("price_trend_12m")
        if trend is not None:
            t = self._f(trend)
            if t > 0:
                score += min(10, t * 2)
            elif t < 0:
                score += max(-10, t * 2)
            evidence.append(f"价格趋势={t}%")
            confidence += 0.1

        # 人口/产业数据
        pop = self._get("区域常住人口_万", "district_population")
        gdp = self._get("区域GDP_亿元", "district_gdp")
        income = self._get("人均可支配收入_万元", "disposable_income")
        if pop:
            evidence.append(f"常住人口={pop}万")
            confidence += 0.05
        if gdp:
            evidence.append(f"GDP={gdp}亿")
            confidence += 0.05
        if income:
            evidence.append(f"人均收入={income}万")
            confidence += 0.05

        return {
            "key": "location", "cn": "区位升值",
            "score": round(min(max(score, 0), 100), 1),
            "confidence": round(min(confidence, 1.0), 2),
            "evidence": "; ".join(evidence) if evidence else "无结构化区位信号",
            "status": "ok" if evidence else "待接入",
            "source": _source_block("structured"),
        }

    def _score_product(self) -> dict:
        """产品力：容积率/绿化率/户型/装修/层高/得房率。"""
        evidence = []
        score = 50.0
        confidence = 0.2

        # 容积率（越低越好，别墅<1.0，高层<3.0）
        far = self._get("容积率", "plot_ratio")
        if far:
            f = self._f(far)
            if f < 1.0:
                score += 20
            elif f < 2.0:
                score += 10
            elif f < 3.0:
                score += 0
            else:
                score -= 5
            evidence.append(f"容积率={f}")
            confidence += 0.1

        # 绿化率
        green = self._get("绿化率", "extend_landscaping_atio")
        if green:
            g = self._f(str(green).replace("%", ""))
            if g > 35:
                score += 10
            elif g > 25:
                score += 5
            evidence.append(f"绿化率={g}%")
            confidence += 0.05

        # 装修
        deco = self._get("装修情况", "decoration")
        if deco:
            deco_str = str(deco)
            if "精装" in deco_str or "豪装" in deco_str:
                score += 10
            elif "毛坯" in deco_str:
                score -= 5
            evidence.append(f"装修={deco_str}")
            confidence += 0.05

        # 得房率
        eff = self._get("得房率_pct", "efficiency_rate")
        if eff:
            e = self._f(eff)
            if e > 80:
                score += 10
            elif e > 75:
                score += 5
            evidence.append(f"得房率={e}%")
            confidence += 0.05

        # 层高
        fh = self._get("层高_m", "floor_height")
        if fh:
            f = self._f(fh)
            if f >= 3.1:
                score += 5
            evidence.append(f"层高={f}m")
            confidence += 0.05

        # 精装标准
        ds = self._get("精装标准_元每㎡", "decoration_standard")
        if ds:
            d = self._f(ds)
            if d >= 5000:
                score += 10
            elif d >= 2000:
                score += 5
            evidence.append(f"精装标准={d}元/㎡")
            confidence += 0.05

        # 建筑类型
        btype = self._get("建筑类型", "build_type_str")
        if btype:
            evidence.append(f"建筑类型={btype}")
            confidence += 0.03

        return {
            "key": "product", "cn": "产品力",
            "score": round(min(max(score, 0), 100), 1),
            "confidence": round(min(confidence, 1.0), 2),
            "evidence": "; ".join(evidence) if evidence else "无产品力信号",
            "status": "ok" if evidence else "待接入",
            "source": _source_block("structured"),
        }

    def _score_brand(self) -> dict:
        """品牌运营：开发商/物业/信用/交付率。"""
        evidence = []
        score = 50.0
        confidence = 0.2

        dev = self._get("开发商", "开发商品牌", "developer")
        if dev:
            dev_str = str(dev)
            evidence.append(f"开发商={dev_str}")
            confidence += 0.05
            # 知名开发商加分
            top_devs = ["万科", "保利", "华润", "中海", "招商", "绿城", "龙湖", "金茂", "融创",
                       "碧桂园", "恒大", "绿地", "世茂", "旭辉", "仁恒", "滨江", "建发"]
            for td in top_devs:
                if td in dev_str:
                    score += 10
                    break

        # 物业
        pm = self._get("物业公司", "物业特色")
        if pm:
            evidence.append(f"物业={pm}")
            confidence += 0.05

        # 物业费（高物业费=高端）
        pm_fee = self._get("物业管理费")
        if pm_fee:
            fee = self._f(str(pm_fee).replace("元", "").replace("/㎡", "").replace("月", ""))
            if fee > 5:
                score += 10
            elif fee > 3:
                score += 5
            evidence.append(f"物业费={fee}元")
            confidence += 0.05

        # 信用评级
        credit = self._get("开发商信用评级", "developer_credit_rating")
        if credit:
            cr = str(credit).upper()
            if "AAA" in cr or "AA" in cr:
                score += 15
            elif "A" in cr:
                score += 8
            elif "B" in cr:
                score -= 5
            evidence.append(f"信用评级={credit}")
            confidence += 0.1

        # 交付率
        delivery = self._get("开发商交付率_pct", "developer_delivery_rate")
        if delivery:
            d = self._f(delivery)
            if d >= 95:
                score += 10
            elif d >= 80:
                score += 5
            elif d < 60:
                score -= 15
            evidence.append(f"交付率={d}%")
            confidence += 0.1

        return {
            "key": "brand", "cn": "品牌运营",
            "score": round(min(max(score, 0), 100), 1),
            "confidence": round(min(confidence, 1.0), 2),
            "evidence": "; ".join(evidence) if evidence else "无品牌信号",
            "status": "ok" if evidence else "待接入",
            "source": _source_block("structured"),
        }

    def _score_scarcity(self) -> dict:
        """稀缺资源：低密/学区/景观/车位比。"""
        evidence = []
        score = 50.0
        confidence = 0.2

        # 低密
        is_low = self._get("是否低密住宅", "is_low_density")
        if is_low:
            score += 15
            evidence.append("低密住宅")
            confidence += 0.1

        # 学区
        is_school = self._get("是否学区房", "is_school_district")
        school_name = self._get("对应学区", "school_district_name")
        primary = self._get("对应小学名称", "primary_school_name")
        if is_school or school_name or primary:
            score += 15
            evidence.append(f"学区={school_name or primary or '是'}")
            confidence += 0.1

        # 景观
        scenic = self._get("景观资源", "scenic_view")
        if scenic:
            scenic_str = str(scenic)
            score += 10
            evidence.append(f"景观={scenic_str}")
            confidence += 0.05

        # 车位比
        pr = self._get("车位比", "parking_rate")
        if pr:
            p = self._f(str(pr).replace("1:", ""))
            if p >= 1.5:
                score += 10
            elif p >= 1.0:
                score += 5
            evidence.append(f"车位比=1:{p}")
            confidence += 0.05

        # 四代住宅
        is_4th = self._get("是否四代住宅", "is_4th_gen")
        if is_4th:
            score += 10
            evidence.append("四代住宅")
            confidence += 0.05

        return {
            "key": "scarcity", "cn": "稀缺资源",
            "score": round(min(max(score, 0), 100), 1),
            "confidence": round(min(confidence, 1.0), 2),
            "evidence": "; ".join(evidence) if evidence else "无稀缺信号",
            "status": "ok" if evidence else "待接入",
            "source": _source_block("structured"),
        }

    def _score_market(self) -> dict:
        """市场供需：去化率/库存/供需比/挂牌成交比。"""
        evidence = []
        score = 50.0
        confidence = 0.2

        # 去化率
        abs_rate = self._get("去化率_pct", "absorption_rate")
        if abs_rate:
            a = self._f(abs_rate)
            if a > 80:
                score += 15
            elif a > 50:
                score += 8
            elif a < 30:
                score -= 10
            evidence.append(f"去化率={a}%")
            confidence += 0.1

        # 库存去化周期
        inv = self._get("库存去化周期_月", "inventory_months")
        if inv:
            i = self._f(inv)
            if i < 6:
                score += 10
            elif i < 12:
                score += 5
            elif i > 24:
                score -= 10
            evidence.append(f"库存周期={i}月")
            confidence += 0.1

        # 供需比
        sdr = self._get("供需比", "supply_demand_ratio")
        if sdr:
            s = self._f(sdr)
            if s < 0.8:
                score += 10
            elif s > 1.5:
                score -= 5
            evidence.append(f"供需比={s}")
            confidence += 0.05

        # 挂牌成交比
        splp = self._get("区域挂牌成交比_pct", "region_sp_lp_ratio")
        if splp:
            sp = self._f(splp)
            if sp > 95:
                score += 5
            evidence.append(f"SP/LP={sp}%")
            confidence += 0.05

        # 市场热度
        heat = self._get("市场热度指数", "market_heat_index")
        if heat:
            evidence.append(f"市场热度={heat}")
            confidence += 0.03

        # 市场周期
        cycle = self._get("市场周期判定", "market_cycle")
        if cycle:
            cyc = str(cycle)
            if "卖方" in cyc or "上升" in cyc:
                score += 10
            elif "买方" in cyc or "下降" in cyc:
                score -= 5
            evidence.append(f"市场周期={cyc}")
            confidence += 0.05

        return {
            "key": "market", "cn": "市场供需",
            "score": round(min(max(score, 0), 100), 1),
            "confidence": round(min(confidence, 1.0), 2),
            "evidence": "; ".join(evidence) if evidence else "无市场信号",
            "status": "ok" if evidence else "待接入",
            "source": _source_block("structured"),
        }

    def _score_policy(self) -> dict:
        """政策代差：四代住宅/绿色建筑/LPR/限购。"""
        evidence = []
        score = 50.0
        confidence = 0.15

        # 四代住宅
        is_4th = self._get("是否四代住宅", "is_4th_gen")
        if is_4th:
            score += 15
            evidence.append("四代住宅政策红利")
            confidence += 0.1

        # 绿色建筑等级
        green = self._get("绿色建筑等级", "green_building_grade")
        if green:
            g = str(green)
            if "三星" in g or "3星" in g:
                score += 10
            elif "二星" in g or "2星" in g:
                score += 5
            evidence.append(f"绿建等级={g}")
            confidence += 0.05

        # 装配率
        assembly = self._get("装配率_pct", "assembly_rate")
        if assembly:
            evidence.append(f"装配率={assembly}%")
            confidence += 0.05

        # LPR
        lpr = self._get("当前LPR_5年期_pct", "lpr_5y")
        if lpr:
            l = self._f(lpr)
            if l < 4.0:
                score += 5  # 低利率环境利好
            evidence.append(f"LPR={l}%")
            confidence += 0.05

        # 限购
        restrict = self._get("限购政策摘要", "purchase_restriction")
        if restrict:
            evidence.append(f"限购政策={restrict}")
            confidence += 0.05

        return {
            "key": "policy", "cn": "政策代差",
            "score": round(min(max(score, 0), 100), 1),
            "confidence": round(min(confidence, 1.0), 2),
            "evidence": "; ".join(evidence) if evidence else "无政策信号",
            "status": "ok" if evidence else "待接入",
            "source": _source_block("structured"),
        }

    def score_all(self) -> dict:
        """运行全部六维评分，返回结构化得分摘要。"""
        dims = []
        total_score = 0.0
        total_weight = 0.0
        total_confidence = 0.0
        dim_count = 0

        scorers = {
            "location": self._score_location,
            "product": self._score_product,
            "brand": self._score_brand,
            "scarcity": self._score_scarcity,
            "market": self._score_market,
            "policy": self._score_policy,
        }

        for key, cn, weight in self.SIX_DIMS:
            dim = scorers[key]()
            dim["weight"] = weight
            dims.append(dim)
            total_score += dim["score"] * weight
            total_weight += weight
            total_confidence += dim["confidence"]
            dim_count += 1

        if total_weight > 0:
            total_score = total_score / total_weight
        avg_confidence = total_confidence / dim_count if dim_count else 0

        evidenced = sum(1 for d in dims if d["status"] == "ok")

        return {
            "modality": "structured",
            "score": round(total_score, 1),
            "confidence": round(avg_confidence, 2),
            "dimensions": dims,
            "evidenced_dims": f"{evidenced}/{dim_count}",
            "status": "ok" if evidenced > 0 else "degraded",
            "source": _source_block("structured"),
        }


# ============================================================================
#  PART 2: 视觉特征评分（基于 VLM 标签）
# ============================================================================

# VLM 标签 → 0-100 分映射表（透明规则，可审计）
VLM_TAG_SCORE_MAP = {
    # ── 立面档次 ──
    "立面": {
        "豪宅立面": 85, "高端立面": 75, "品质立面": 60,
        "普通立面": 40, "老旧立面": 20, "现代立面": 55,
        "古典立面": 55, "玻璃幕墙": 70, "石材立面": 72,
        "铝板幕墙": 68, "涂料立面": 35, "陶板立面": 65,
        "金属格栅": 68, "清水混凝土": 60, "GRC幕墙": 65,
        "干挂石材": 75, "真石漆": 40,
    },
    # ── 景观品质 ──
    "景观": {
        "顶级景观": 90, "高端景观": 75, "品质景观": 60,
        "基础景观": 40, "无景观": 15, "海景": 80,
        "江景": 75, "湖景": 70, "山景": 65, "园景": 55,
        "水系景观": 72, "泳池景观": 68, "庭院景观": 65,
        "屋顶花园": 70, "空中花园": 72, "下沉庭院": 68,
        "水景": 65, "中庭景观": 60,
    },
    # ── 公区配置 ──
    "公区": {
        "五星大堂": 85, "精装大堂": 70, "品质大堂": 55,
        "基础大堂": 35, "豪华会所": 80, "品质会所": 60,
        "恒温泳池": 72, "健身房": 55, "儿童游乐": 55,
        "架空层": 58, "泛会所": 60, "入户大堂": 65,
        "地库精装": 68, "无感通行": 60, "智能安防": 55,
    },
    # ── 设计创新 ──
    "设计": {
        "未来主义": 85, "参数化设计": 82, "解构主义": 78,
        "自然有机": 72, "热带现代": 68, "Art-Deco": 75,
        "新中式": 65, "现代简约": 55, "原创设计": 70,
        "大师设计": 85, "国际事务所": 80, "先锋设计": 78,
        "生态建筑": 70, "第四代住宅": 80, "垂直森林": 82,
        "曲线立面": 72, "超流体": 75, "极简主义": 60,
    },
    # ── 材质品质 ──
    "材质": {
        "石材": 72, "实木": 70, "木格栅": 68, "金属格栅": 68,
        "陶板": 65, "陶棍": 65, "清水混凝土": 60, "夯土": 62,
        "GRC": 65, "GFRC": 65, "编织": 58, "穿孔板": 55,
        "铜": 75, "铜饰": 78, "不锈钢": 55, "玻璃": 50,
        "涂料": 35, "真石漆": 40, "铝板": 62, "石材干挂": 75,
    },
    # ── 色调 ──
    "色调": {
        "暖色": 55, "米金": 62, "大地色": 58, "深色": 55,
        "黑铜": 68, "白色": 50, "灰色": 50,
    },
}

# 维度 → 标签类别映射
VLM_DIM_TO_CATEGORY = {
    "facade": ("立面档次", ["立面", "材质", "色调"]),
    "landscape": ("景观品质", ["景观"]),
    "public_area": ("公区配置", ["公区"]),
    "design_innovation": ("设计创新", ["设计"]),
}


class VisualFeatureScorer:
    """基于 ArchLib VLM 标签的视觉特征评分器。

    输入：archlib_vision.py 的 search/evidence 输出
    输出：0-100 视觉得分 + 四维分解 + 标签证据
    """

    VLM_DIMS = [
        ("facade", "立面档次", 0.35),
        ("landscape", "景观品质", 0.25),
        ("public_area", "公区配置", 0.20),
        ("design_innovation", "设计创新", 0.20),
    ]

    def __init__(self, archlib_projects: list[dict] | None = None,
                 vlm_evidence: dict | None = None,
                 archlib: str = ARCHLIB_DEFAULT):
        self.archlib_projects = archlib_projects or []
        self.vlm_evidence = vlm_evidence or {}
        self.archlib = archlib
        self._available = bool(self.archlib_projects) or bool(self.vlm_evidence)

    def _tag_to_score(self, tag: str, category: str) -> float:
        """将 VLM 标签映射到 0-100 分。"""
        for cat, mapping in VLM_TAG_SCORE_MAP.items():
            if tag in mapping:
                return mapping[tag]
            # 模糊匹配
            for key, val in mapping.items():
                if key in tag or tag in key:
                    return val
        return 50.0  # 默认中等分

    def _extract_tags_from_project(self, project: dict) -> dict[str, list[str]]:
        """从项目聚合数据中提取各维度标签。"""
        tags = {
            "facade": [],
            "landscape": [],
            "public_area": [],
            "design_innovation": [],
        }
        materials = project.get("materials", [])
        colors = project.get("colors", [])
        styles = project.get("styles", [])
        scenes = project.get("scenes", [])
        keywords = project.get("design_keywords", [])

        # 立面 = 材质 + 色调
        tags["facade"].extend(materials)
        tags["facade"].extend(colors)

        # 景观 = 场景
        tags["landscape"].extend(scenes)

        # 公区 = 场景（大堂/会所类）
        public_scenes = [s for s in scenes if any(k in s for k in
                         ["大堂", "会所", "架空", "泳池", "地库", "入口", "门头"])]
        tags["public_area"].extend(public_scenes)

        # 设计创新 = 风格 + 关键词
        tags["design_innovation"].extend(styles)
        tags["design_innovation"].extend(keywords)

        return tags

    def _extract_tags_from_evidence(self, evidence: dict) -> dict[str, list[str]]:
        """从 design_evidence 输出中提取各维度标签。"""
        tags = {
            "facade": [],
            "landscape": [],
            "public_area": [],
            "design_innovation": [],
        }
        dims = evidence.get("dimensions", {})
        # premium -> 设计创新
        if "premium" in dims:
            tags["design_innovation"].extend(dims["premium"].get("design_moves", []))
        # quality -> 立面
        if "quality" in dims:
            tags["facade"].extend(dims["quality"].get("design_moves", []))
        # comfort -> 景观
        if "comfort" in dims:
            tags["landscape"].extend(dims["comfort"].get("design_moves", []))
        # upgrade -> 公区
        if "upgrade" in dims:
            tags["public_area"].extend(dims["upgrade"].get("design_moves", []))
        return tags

    _VLM_DIM_CN = {k: cn for k, cn, _ in VLM_DIMS}

    def _score_dimension(self, dim_key: str, tags: list[str]) -> dict:
        """对单个视觉维度评分。"""
        if not tags:
            return {
                "key": dim_key,
                "cn": self._VLM_DIM_CN.get(dim_key, dim_key),
                "score": 50.0,
                "confidence": 0.0,
                "matched_tags": [],
                "tag_evidence": [],
                "status": "待接入",
            }

        scores = []
        tag_evidence = []
        for tag in tags:
            s = self._tag_to_score(tag, dim_key)
            scores.append(s)
            tag_evidence.append({"tag": tag, "score": s})

        avg_score = float(np.mean(scores)) if scores else 50.0
        # 置信度 = 标签数量 / 5（封顶 1.0）
        confidence = min(len(tags) / 5.0, 1.0)

        return {
            "key": dim_key,
            "cn": self._VLM_DIM_CN.get(dim_key, dim_key),
            "score": round(avg_score, 1),
            "confidence": round(confidence, 2),
            "matched_tags": tags[:10],
            "tag_evidence": tag_evidence[:10],
            "status": "ok",
        }

    def score_all(self) -> dict:
        """运行全部四维视觉评分。"""
        if not self._available:
            return {
                "modality": "visual",
                "score": None,
                "confidence": 0.0,
                "dimensions": [],
                "status": "degraded",
                "degraded_reason": "ArchLib VLM 标签数据不可用，降级为仅结构化",
                "source": _source_block("visual"),
            }

        # 提取标签
        all_tags: dict[str, list[str]] = {
            "facade": [], "landscape": [], "public_area": [], "design_innovation": []
        }

        if self.vlm_evidence:
            evidence_tags = self._extract_tags_from_evidence(self.vlm_evidence)
            for k in all_tags:
                all_tags[k].extend(evidence_tags.get(k, []))

        if self.archlib_projects:
            for proj in self.archlib_projects[:5]:  # 最多取 5 个项目
                proj_tags = self._extract_tags_from_project(proj)
                for k in all_tags:
                    all_tags[k].extend(proj_tags.get(k, []))

        dims = []
        total_score = 0.0
        total_weight = 0.0
        total_confidence = 0.0
        dim_count = 0

        for key, cn, weight in self.VLM_DIMS:
            dim = self._score_dimension(key, all_tags.get(key, []))
            dim["weight"] = weight
            dims.append(dim)
            total_score += dim["score"] * weight
            total_weight += weight
            total_confidence += dim["confidence"]
            dim_count += 1

        if total_weight > 0:
            total_score = total_score / total_weight
        avg_confidence = total_confidence / dim_count if dim_count else 0

        evidenced = sum(1 for d in dims if d["status"] == "ok")

        return {
            "modality": "visual",
            "score": round(total_score, 1),
            "confidence": round(avg_confidence, 2),
            "dimensions": dims,
            "evidenced_dims": f"{evidenced}/{dim_count}",
            "matched_projects": len(self.archlib_projects),
            "status": "ok" if evidenced > 0 else "degraded",
            "source": _source_block("visual"),
        }


# ============================================================================
#  PART 3: 空间特征评分（基于 GNN / 邻居价格加权）
# ============================================================================

class SpatialFeatureScorer:
    """空间特征评分器：GIS 邻居价格加权 + GNN 空间依赖修正。

    输入：目标楼盘坐标 + 周边楼盘列表（含坐标和价格）
    输出：0-100 空间得分 + 邻居统计 + 空间自相关修正
    """

    DEFAULT_SEARCH_RADIUS = 3000  # 默认搜索半径 3km

    def __init__(self, target_lat: float | None = None,
                 target_lng: float | None = None,
                 neighbors: list[dict] | None = None,
                 search_radius: float = DEFAULT_SEARCH_RADIUS,
                 gnn_enabled: bool = False):
        self.target_lat = target_lat
        self.target_lng = target_lng
        self.neighbors = neighbors or []
        self.search_radius = search_radius
        self.gnn_enabled = gnn_enabled
        self._available = (target_lat is not None and target_lng is not None
                           and len(self.neighbors) > 0)

    def _find_neighbors(self) -> list[dict]:
        """Haversine 距离筛选搜索半径内的邻居。"""
        if not self._available:
            return []
        result = []
        for nb in self.neighbors:
            nlat = nb.get("lat") or nb.get("blat") or nb.get("纬度")
            nlng = nb.get("lng") or nb.get("blng") or nb.get("经度")
            if nlat is None or nlng is None:
                continue
            try:
                dist = haversine(self.target_lat, self.target_lng,
                                 float(nlat), float(nlng))
            except (ValueError, TypeError):
                continue
            if dist <= self.search_radius:
                nb["_distance"] = round(dist, 0)
                result.append(nb)
        # 按距离排序
        result.sort(key=lambda x: x.get("_distance", float("inf")))
        return result

    def _inverse_distance_weight(self, neighbors: list[dict]) -> float:
        """反距离加权平均价格。"""
        if not neighbors:
            return 0.0
        total_weight = 0.0
        weighted_price = 0.0
        for nb in neighbors:
            price = nb.get("price") or nb.get("最新价格") or nb.get("avg_price") or 0
            try:
                price = float(price)
            except (ValueError, TypeError):
                continue
            dist = nb.get("_distance", 1.0)
            # 反距离权重，加 epsilon 防除零
            w = 1.0 / max(dist, 10.0)
            weighted_price += price * w
            total_weight += w
        if total_weight > 0:
            return weighted_price / total_weight
        return 0.0

    def _gnn_spatial_correction(self, base_score: float, neighbors: list[dict]) -> tuple[float, dict]:
        """GNN 空间依赖修正（stub）。

        当 gnn_spatial.py 可用时，调用其空间依赖修正函数。
        当前为 stub：基于邻居价格的标准差做缩放修正。
        """
        gnn_info = {
            "gnn_available": False,
            "gnn_method": "stub（基于邻居价格标准差修正）",
            "gnn_note": "gnn_spatial.py 待实现；当前为 stub 回退。"
        }

        if not neighbors:
            return base_score, gnn_info

        prices = []
        for nb in neighbors:
            p = nb.get("price") or nb.get("最新价格") or nb.get("avg_price") or 0
            try:
                prices.append(float(p))
            except (ValueError, TypeError):
                continue

        if len(prices) < 2:
            return base_score, gnn_info

        prices_arr = np.array(prices)
        mean_p = float(np.mean(prices_arr))
        std_p = float(np.std(prices_arr))

        if mean_p > 0:
            cv = std_p / mean_p  # 变异系数
            # 变异系数高 → 空间异质性强 → 轻微下调
            # 变异系数低 → 空间同质性强 → 轻微上调
            correction = 1.0 - (cv - 0.2) * 0.5
            correction = max(0.85, min(1.15, correction))
            gnn_info["gnn_available"] = True
            gnn_info["gnn_method"] = "stub CV-based correction"
            gnn_info["cv"] = round(cv, 3)
            gnn_info["correction_factor"] = round(correction, 3)
            return base_score * correction, gnn_info

        return base_score, gnn_info

    def score_all(self) -> dict:
        """运行空间特征评分。"""
        if not self._available:
            return {
                "modality": "spatial",
                "score": None,
                "confidence": 0.0,
                "neighbors": [],
                "neighbor_count": 0,
                "status": "degraded",
                "degraded_reason": "无目标坐标或无邻居数据，空间评分不可用",
                "source": _source_block("spatial"),
            }

        nearby = self._find_neighbors()
        if not nearby:
            return {
                "modality": "spatial",
                "score": None,
                "confidence": 0.0,
                "neighbors": [],
                "neighbor_count": 0,
                "search_radius_m": self.search_radius,
                "status": "degraded",
                "degraded_reason": f"搜索半径 {self.search_radius}m 内无邻居",
                "source": _source_block("spatial"),
            }

        # 反距离加权均价
        idw_price = self._inverse_distance_weight(nearby)

        # 邻居价格统计
        prices = []
        for nb in nearby:
            p = nb.get("price") or nb.get("最新价格") or nb.get("avg_price") or 0
            try:
                prices.append(float(p))
            except (ValueError, TypeError):
                pass

        price_stats = {}
        if prices:
            prices_arr = np.array(prices)
            price_stats = {
                "mean": round(float(np.mean(prices_arr)), 0),
                "median": round(float(np.median(prices_arr)), 0),
                "std": round(float(np.std(prices_arr)), 0),
                "min": round(float(np.min(prices_arr)), 0),
                "max": round(float(np.max(prices_arr)), 0),
                "count": len(prices),
            }

        # 空间得分：基于 IDW 价格与目标价格的关系
        target_price = None
        # 尝试从邻居中找自身
        for nb in nearby:
            if nb.get("_distance", float("inf")) < 1:
                target_price = nb.get("price")
                break

        # 空间得分基础：IDW 价格在邻居价格分布中的百分位
        base_score = 50.0
        if price_stats and idw_price > 0:
            mean_p = price_stats["mean"]
            std_p = price_stats["std"] if price_stats["std"] > 0 else 1
            z_score = (idw_price - mean_p) / std_p
            # 将 z-score 映射到 0-100
            base_score = 50.0 + z_score * 15.0
            base_score = max(0, min(100, base_score))

        # GNN 修正
        gnn_info = {}
        if self.gnn_enabled:
            base_score, gnn_info = self._gnn_spatial_correction(base_score, nearby)

        # 置信度基于邻居数量
        n = len(nearby)
        confidence = min(n / 10.0, 0.9) if n > 0 else 0.0

        neighbor_summary = []
        for nb in nearby[:10]:
            p = nb.get("price") or nb.get("最新价格") or nb.get("avg_price") or 0
            name = nb.get("name") or nb.get("楼盘名称") or nb.get("project") or ""
            neighbor_summary.append({
                "name": str(name)[:30],
                "distance_m": nb.get("_distance", 0),
                "price": p,
            })

        return {
            "modality": "spatial",
            "score": round(base_score, 1),
            "confidence": round(confidence, 2),
            "idw_price": round(idw_price, 0) if idw_price else None,
            "price_stats": price_stats,
            "neighbor_count": len(nearby),
            "neighbors": neighbor_summary,
            "search_radius_m": self.search_radius,
            "gnn_info": gnn_info,
            "status": "ok",
            "source": _source_block("spatial"),
        }


# ============================================================================
#  PART 4: 多模态融合
# ============================================================================

class MultimodalValuation:
    """多模态房产估值模型。

    三模态加权融合：
      value = w1*structured_score + w2*visual_score + w3*spatial_score

    参考：Huang et al. (2025) "Multimodal Real Estate Valuation"
    """

    def __init__(self, weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
                 parcel_data: dict | None = None,
                 market_context: dict | None = None,
                 archlib_projects: list[dict] | None = None,
                 vlm_evidence: dict | None = None,
                 target_lat: float | None = None,
                 target_lng: float | None = None,
                 neighbors: list[dict] | None = None,
                 gnn_enabled: bool = False,
                 archlib_path: str = ARCHLIB_DEFAULT):
        w1, w2, w3 = weights
        total = w1 + w2 + w3
        if abs(total - 1.0) > 0.001:
            # 归一化
            w1, w2, w3 = w1 / total, w2 / total, w3 / total
        self.weights = (w1, w2, w3)
        self.parcel_data = parcel_data or {}
        self.market_context = market_context or {}
        self.archlib_projects = archlib_projects or []
        self.vlm_evidence = vlm_evidence or {}
        self.target_lat = target_lat
        self.target_lng = target_lng
        self.neighbors = neighbors or []
        self.gnn_enabled = gnn_enabled
        self.archlib_path = archlib_path

        # 初始化三个评分器
        self.structured = StructuredFeatureScorer(parcel_data, market_context)
        self.visual = VisualFeatureScorer(archlib_projects, vlm_evidence, archlib_path)
        self.spatial = SpatialFeatureScorer(target_lat, target_lng, neighbors,
                                            gnn_enabled=gnn_enabled)

    def evaluate(self) -> dict:
        """运行完整多模态估值。"""
        t0 = time.time()

        # 三模态分别评分
        struct_result = self.structured.score_all()
        visual_result = self.visual.score_all()
        spatial_result = self.spatial.score_all()

        w1, w2, w3 = self.weights

        # 融合得分
        scores = []
        weights_used = []
        modalities_active = []

        if struct_result["score"] is not None:
            scores.append(struct_result["score"] * w1)
            weights_used.append(w1)
            modalities_active.append("structured")

        if visual_result["score"] is not None:
            scores.append(visual_result["score"] * w2)
            weights_used.append(w2)
            modalities_active.append("visual")
        else:
            # 视觉不可用时，权重重新分配给结构化
            if struct_result["score"] is not None:
                scores.append(struct_result["score"] * w2)
                weights_used.append(w2)

        if spatial_result["score"] is not None:
            scores.append(spatial_result["score"] * w3)
            weights_used.append(w3)
            modalities_active.append("spatial")
        else:
            # 空间不可用时，权重重新分配给结构化
            if struct_result["score"] is not None:
                scores.append(struct_result["score"] * w3)
                weights_used.append(w3)

        # 归一化
        total_w = sum(weights_used)
        if total_w > 0:
            fusion_score = sum(scores) / total_w
        else:
            fusion_score = 50.0

        # 融合置信度：加权平均各模态置信度
        confidences = []
        conf_weights = []
        if struct_result["score"] is not None:
            confidences.append(struct_result["confidence"])
            conf_weights.append(w1)
        if visual_result["score"] is not None:
            confidences.append(visual_result["confidence"])
            conf_weights.append(w2)
        if spatial_result["score"] is not None:
            confidences.append(spatial_result["confidence"])
            conf_weights.append(w3)

        fusion_confidence = (
            sum(c * w for c, w in zip(confidences, conf_weights)) / sum(conf_weights)
            if conf_weights else 0.0
        )

        # 基线对比：仅结构化
        baseline_score = struct_result["score"] if struct_result["score"] is not None else 50.0
        delta_pct = (
            round((fusion_score - baseline_score) / baseline_score * 100, 1)
            if baseline_score > 0 else 0.0
        )

        elapsed_ms = round((time.time() - t0) * 1000, 0)

        result = {
            "model": "MultimodalValuation v1.0",
            "reference": "Huang et al. (2025) Multimodal Real Estate Valuation",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_ms": elapsed_ms,
            "weights": {"structured": w1, "visual": w2, "spatial": w3},
            "modalities_active": modalities_active,
            "fusion_score": round(fusion_score, 1),
            "fusion_confidence": round(fusion_confidence, 2),
            "baseline": {
                "modality": "structured_only",
                "score": baseline_score,
                "confidence": struct_result["confidence"],
            },
            "delta_pct": delta_pct,
            "delta_note": (
                f"多模态融合 vs 仅结构化基线：{'+' if delta_pct >= 0 else ''}{delta_pct}%。"
                f"视觉和空间特征{'提供了额外信息' if delta_pct != 0 else '未显著改变估值'}。"
            ),
            "modalities": {
                "structured": struct_result,
                "visual": visual_result,
                "spatial": spatial_result,
            },
            "source": _source_block("fusion"),
            "disclaimer": (
                "本估值为多模态模型原型推演，非市场实测或承诺。视觉特征来自 ArchLib VLM 打标（进行中），"
                "空间特征基于邻居价格加权（GNN 为 stub），部分维度标注待接入。"
                "不构成投资建议。"
            ),
        }

        return result

    def to_report(self, result: dict | None = None) -> str:
        """生成可读报告文本。"""
        if result is None:
            result = self.evaluate()
        lines = []
        lines.append("=" * 70)
        lines.append("  DDS 多模态房产估值报告")
        lines.append("  MultimodalValuation v1.0 | Huang et al. (2025)")
        lines.append("=" * 70)
        lines.append(f"  时间: {result['timestamp']}")
        lines.append(f"  耗时: {result['elapsed_ms']}ms")
        lines.append(f"  活跃模态: {', '.join(result['modalities_active'])}")
        lines.append("")

        w = result["weights"]
        lines.append(f"  权重: 结构化={w['structured']:.2f}  视觉={w['visual']:.2f}  空间={w['spatial']:.2f}")
        lines.append("")

        lines.append(f"  ★ 多模态融合得分: {result['fusion_score']:.1f} / 100")
        lines.append(f"     置信度: {result['fusion_confidence']:.2f}")
        lines.append(f"  ★ 基线（仅结构化）: {result['baseline']['score']:.1f} / 100")
        lines.append(f"     Δ = {'+' if result['delta_pct'] >= 0 else ''}{result['delta_pct']}%")
        lines.append("")

        lines.append("-" * 70)
        lines.append("  模态分解")
        lines.append("-" * 70)

        for mod_key, mod_label in [("structured", "结构化"), ("visual", "视觉"), ("spatial", "空间")]:
            mod = result["modalities"].get(mod_key, {})
            score = mod.get("score")
            if score is None:
                lines.append(f"\n  [{mod_label}] 不可用")
                if mod.get("degraded_reason"):
                    lines.append(f"    原因: {mod['degraded_reason']}")
                continue
            lines.append(f"\n  [{mod_label}] 得分={score:.1f}  置信度={mod.get('confidence', 0):.2f}  状态={mod.get('status')}")
            dims = mod.get("dimensions", [])
            if dims:
                for d in dims:
                    s = d.get("score", 0)
                    bar = "█" * int(s / 5) + "░" * (20 - int(s / 5))
                    lines.append(f"    {d.get('cn', d.get('key', '')):10s}  [{bar}]  {s:.1f}  (权重={d.get('weight', 0):.2f})")
                    if d.get("evidence"):
                        lines.append(f"           证据: {d['evidence'][:80]}")

        lines.append("")
        lines.append("-" * 70)
        lines.append("  数据来源")
        lines.append("-" * 70)
        for mod_key in ["structured", "visual", "spatial", "fusion"]:
            src = SOURCE_REGISTRY.get(mod_key, {})
            lines.append(f"  [{mod_key}] {src.get('SOURCE', 'N/A')}")

        lines.append("")
        lines.append(f"  免责声明: {result['disclaimer']}")
        lines.append("=" * 70)

        return "\n".join(lines)


# ============================================================================
#  PART 5: 数据加载辅助函数
# ============================================================================

def load_parcel_from_csv(city: str, project_name: str | None = None) -> dict | None:
    """从 Vault CSV 加载楼盘结构化数据。"""
    vault_dir = _PROJECT_ROOT / "Vault" / "2026新楼盘"
    csv_path = vault_dir / f"新楼盘-{city}.csv"

    if not csv_path.exists():
        print(f"[WARN] 未找到城市数据: {csv_path}", file=sys.stderr)
        return None

    # 使用标准库 csv 读取（避免 pandas 依赖）
    import csv
    with open(csv_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        print(f"[WARN] {city} 数据为空", file=sys.stderr)
        return None

    if project_name:
        for row in rows:
            name = row.get("楼盘名称", "") or row.get("小区名称", "")
            if project_name in name:
                return dict(row)
        print(f"[WARN] 未找到项目 '{project_name}'，返回第一条数据", file=sys.stderr)
        return dict(rows[0])

    return dict(rows[0])


def load_archlib_visual(archlib_path: str = ARCHLIB_DEFAULT,
                        business: str | None = None,
                        tier: str | None = None,
                        top_n: int = 5) -> tuple[list[dict], dict | None]:
    """加载 ArchLib 视觉特征（聚合项目 + design_evidence）。"""
    try:
        from archlib_vision import load_images, aggregate_projects, query, design_evidence
    except ImportError:
        print("[WARN] archlib_vision.py 不可用，视觉模态降级", file=sys.stderr)
        return [], None

    images = load_images(archlib_path)
    if not images:
        print("[WARN] ArchLib 无打标数据，视觉模态降级", file=sys.stderr)
        return [], None

    projects = aggregate_projects(images)
    if not projects:
        return [], None

    # 检索匹配项目
    matched = query(business=business, tier=tier, top_n=top_n,
                    archlib=archlib_path, projects=projects)
    # 获取设计证据
    evidence = design_evidence(business=business, tier=tier, top_n=top_n,
                               archlib=archlib_path, projects=projects)

    return matched, evidence


def load_spatial_neighbors(city: str, lat: float | None = None,
                           lng: float | None = None,
                           radius: float = 3000) -> list[dict]:
    """从 Vault CSV 加载周边邻居楼盘。"""
    if lat is None or lng is None:
        return []

    vault_dir = _PROJECT_ROOT / "Vault" / "2026新楼盘"
    csv_path = vault_dir / f"新楼盘-{city}.csv"

    if not csv_path.exists():
        return []

    import csv
    neighbors = []
    with open(csv_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            blat = row.get("百度地图纬度", "") or row.get("blat", "")
            blng = row.get("百度地图经度", "") or row.get("blng", "")
            if not blat or not blng:
                continue
            try:
                nlat, nlng = float(blat), float(blng)
            except (ValueError, TypeError):
                continue
            dist = haversine(lat, lng, nlat, nlng)
            if dist <= radius:
                price = row.get("最新价格", "") or row.get("参考价格", "")
                neighbors.append({
                    "name": row.get("楼盘名称", row.get("小区名称", "")),
                    "lat": nlat, "lng": nlng,
                    "price": price,
                    "_distance": dist,
                })

    neighbors.sort(key=lambda x: x.get("_distance", float("inf")))
    return neighbors


# ============================================================================
#  PART 6: CLI 接口
# ============================================================================

def _build_demo_parcel() -> dict:
    """构建 demo 楼盘数据，用于独立运行验证。"""
    return {
        "楼盘名称": "海棠湾·云栖",
        "楼盘ID": "DEMO-SY-001",
        "城市名称": "三亚",
        "区域名称": "海棠区",
        "地址": "三亚市海棠区海棠南路88号",
        "环线位置": "外环以外",
        "百度地图纬度": "18.41040",
        "百度地图经度": "109.71899",
        "最新价格": "42000",
        "参考价格": "42000元/㎡",
        "周边均价": "38000",
        "容积率": "1.2",
        "绿化率": "42",
        "建筑类型": "板楼,联排别墅",
        "装修情况": "精装修",
        "物业公司": "绿城物业",
        "物业管理费": "6.5元/㎡/月",
        "开发商": "绿城中国",
        "开发商品牌": "绿城",
        "是否四代住宅": "是",
        "是否低密住宅": "是",
        "是否学区房": "否",
        "景观资源": "海景",
        "得房率_pct": "85",
        "层高_m": "3.15",
        "精装标准_元每㎡": "8000",
        "车位比": "1:1.8",
        "绿色建筑等级": "三星",
        "装配率_pct": "65",
        "开发商信用评级": "AAA",
        "开发商交付率_pct": "97",
        "去化率_pct": "78",
        "库存去化周期_月": "8",
        "供需比": "0.7",
        "区域挂牌成交比_pct": "94",
        "市场周期判定": "卖方市场",
        "当前LPR_5年期_pct": "3.85",
        "限购政策摘要": "非本地户籍限购1套",
        "区域常住人口_万": "35",
        "区域GDP_亿元": "280",
        "人均可支配收入_万元": "4.8",
    }


def _build_demo_neighbors() -> list[dict]:
    """构建 demo 邻居数据。"""
    return [
        {"name": "海棠湾·仁恒皇冠", "lat": 18.410, "lng": 109.720, "price": 45000},
        {"name": "海棠湾·保利瑰丽", "lat": 18.412, "lng": 109.718, "price": 48000},
        {"name": "海棠湾·亚特兰蒂斯", "lat": 18.408, "lng": 109.722, "price": 52000},
        {"name": "海棠湾·君悦", "lat": 18.415, "lng": 109.716, "price": 40000},
        {"name": "海棠湾·威斯汀", "lat": 18.406, "lng": 109.715, "price": 38000},
        {"name": "海棠湾·洲际", "lat": 18.418, "lng": 109.725, "price": 43000},
        {"name": "海棠湾·艾迪逊", "lat": 18.405, "lng": 109.710, "price": 46000},
        {"name": "海棠湾·普通住宅A", "lat": 18.420, "lng": 109.730, "price": 28000},
    ]


def _build_demo_archlib() -> tuple[list[dict], dict]:
    """构建 demo ArchLib 视觉数据。"""
    projects = [
        {
            "project": "安澜上海",
            "dds_business": "高层豪宅",
            "tier": "顶豪",
            "image_count": 45,
            "materials": ["石材", "铜饰", "金属格栅", "玻璃幕墙"],
            "colors": ["暖色", "米金"],
            "styles": ["现代简约", "Art-Deco"],
            "scenes": ["入口/门头", "中庭/大堂", "景观庭院", "泳池/水景"],
            "design_keywords": ["石材干挂", "入户大堂", "铜饰", "暖光", "挑檐", "格栅"],
            "one_liners": ["安澜上海——精工品质与Art-Deco美学的当代演绎"],
            "cover": "D:/ArchLib/13_高层豪宅·顶豪/安澜上海/cover.jpg",
        },
        {
            "project": "嘉佰道",
            "dds_business": "高层豪宅",
            "tier": "顶豪",
            "image_count": 38,
            "materials": ["石材", "铝板", "金属格栅"],
            "colors": ["深色", "黑铜"],
            "styles": ["现代简约"],
            "scenes": ["入口/门头", "中庭/大堂", "泳池/水景"],
            "design_keywords": ["干挂石材", "铝板幕墙", "恒温泳池", "豪华会所"],
            "one_liners": ["嘉佰道——城市核心区顶豪标杆"],
            "cover": "D:/ArchLib/13_高层豪宅·顶豪/嘉佰道/cover.jpg",
        },
        {
            "project": "华发香山湖壹号",
            "dds_business": "公寓住宅",
            "tier": "改善",
            "image_count": 25,
            "materials": ["真石漆", "涂料", "玻璃"],
            "colors": ["暖色", "白色"],
            "styles": ["现代简约"],
            "scenes": ["中庭/大堂", "景观庭院"],
            "design_keywords": ["现代立面", "品质景观", "中庭", "架空层"],
            "one_liners": ["华发香山湖壹号——改善型住宅的品质之选"],
            "cover": "D:/ArchLib/11_公寓住宅/华发香山湖壹号/cover.jpg",
        },
    ]
    evidence = {
        "matched": 3,
        "query": {"business": "高层豪宅", "tier": "顶豪"},
        "dimensions": {
            "premium": {
                "label": "溢价率（设计差异化记忆点）",
                "design_moves": ["Art-Deco", "现代简约", "石材干挂", "铜饰", "暖光", "挑檐"],
            },
            "quality": {
                "label": "品质感（高质感主材 + 基调）",
                "design_moves": ["石材", "铜饰", "金属格栅", "米金", "暖色", "格栅"],
            },
            "comfort": {
                "label": "舒适度（人居场景）",
                "design_moves": ["景观庭院", "泳池/水景", "水景", "绿植", "庭院", "棕榈"],
            },
            "upgrade": {
                "label": "豪宅改善（尊贵礼序）",
                "design_moves": ["入口/门头", "中庭/大堂", "入户大堂", "格栅", "暖光", "石材"],
            },
        },
        "cases": [
            {"project": "安澜上海", "tier": "顶豪", "dds_business": "高层豪宅",
             "image_count": 45, "one_liner": "精工品质与Art-Deco美学的当代演绎",
             "materials": ["石材", "铜饰", "金属格栅"]},
            {"project": "嘉佰道", "tier": "顶豪", "dds_business": "高层豪宅",
             "image_count": 38, "one_liner": "城市核心区顶豪标杆",
             "materials": ["石材", "铝板", "金属格栅"]},
        ],
        "caveat": "证据来自 ArchLib 逐图 VLM 打标（进行中，覆盖随打标增长）。",
    }
    return projects, evidence


def main():
    ap = argparse.ArgumentParser(
        description="DDS 多模态房产估值模型 (MultimodalValuation v1.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/multimodal_valuation.py --demo
  python scripts/multimodal_valuation.py --city 三亚 --project "海棠湾"
  python scripts/multimodal_valuation.py --city 三亚 --weights 0.5,0.25,0.25
  python scripts/multimodal_valuation.py --city 三亚 --benchmark
  python scripts/multimodal_valuation.py --city 三亚 --output report.json
        """,
    )
    ap.add_argument("--city", default=None, help="城市名称")
    ap.add_argument("--project", default=None, help="项目名称（模糊匹配）")
    ap.add_argument("--weights", default=None,
                    help="三模态权重，逗号分隔，如 0.5,0.25,0.25")
    ap.add_argument("--benchmark", action="store_true",
                    help="对比单模态 vs 多模态")
    ap.add_argument("--output", default=None, help="输出 JSON 报告路径")
    ap.add_argument("--demo", action="store_true",
                    help="使用内置 demo 数据运行")
    ap.add_argument("--no-visual", action="store_true",
                    help="禁用视觉模态（仅结构化）")
    ap.add_argument("--no-spatial", action="store_true",
                    help="禁用空间模态（仅结构化）")
    ap.add_argument("--gnn", action="store_true",
                    help="启用 GNN 空间修正（当前为 stub）")
    ap.add_argument("--archlib", default=ARCHLIB_DEFAULT,
                    help="ArchLib 路径")
    ap.add_argument("--radius", type=float, default=3000,
                    help="空间搜索半径（米）")
    ap.add_argument("--report", action="store_true",
                    help="输出可读文本报告")

    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # ── 解析权重 ──
    weights = DEFAULT_WEIGHTS
    if args.weights:
        parts = [float(x.strip()) for x in args.weights.split(",")]
        if len(parts) == 3:
            weights = tuple(parts)
        else:
            print("[ERROR] 权重格式错误，应为 w1,w2,w3", file=sys.stderr)
            sys.exit(1)

    # ── 解析模式 ──
    if args.no_visual:
        weights = (weights[0] + weights[1], 0.0, weights[2])
    if args.no_spatial:
        weights = (weights[0] + weights[2], weights[1], 0.0)

    # ── 加载数据 ──
    if args.demo:
        print("[INFO] 使用内置 demo 数据", file=sys.stderr)
        parcel_data = _build_demo_parcel()
        neighbors = _build_demo_neighbors() if not args.no_spatial else []
        archlib_projects, vlm_evidence = ([] , {}) if args.no_visual else _build_demo_archlib()
        target_lat = float(parcel_data["百度地图纬度"])
        target_lng = float(parcel_data["百度地图经度"])
        city = "三亚"
        project_name = "海棠湾·云栖"
    elif args.city:
        city = args.city
        project_name = args.project
        print(f"[INFO] 加载 {city} 数据...", file=sys.stderr)
        parcel_data = load_parcel_from_csv(city, project_name) or {}
        if parcel_data:
            lat = parcel_data.get("百度地图纬度", "") or parcel_data.get("blat", "")
            lng = parcel_data.get("百度地图经度", "") or parcel_data.get("blng", "")
            try:
                target_lat = float(lat) if lat else None
                target_lng = float(lng) if lng else None
            except (ValueError, TypeError):
                target_lat, target_lng = None, None
        else:
            target_lat, target_lng = None, None

        # 加载邻居
        if not args.no_spatial and target_lat is not None:
            neighbors = load_spatial_neighbors(city, target_lat, target_lng, args.radius)
        else:
            neighbors = []

        # 加载 ArchLib
        if not args.no_visual:
            archlib_projects, vlm_evidence = load_archlib_visual(
                args.archlib, top_n=5)
        else:
            archlib_projects, vlm_evidence = [], {}
        project_name = args.project or (parcel_data.get("楼盘名称", "") or
                                        parcel_data.get("小区名称", ""))
    else:
        print("[ERROR] 需要 --city 或 --demo", file=sys.stderr)
        sys.exit(1)

    # ── 构建模型 ──
    model = MultimodalValuation(
        weights=weights,
        parcel_data=parcel_data,
        archlib_projects=archlib_projects,
        vlm_evidence=vlm_evidence,
        target_lat=target_lat,
        target_lng=target_lng,
        neighbors=neighbors,
        gnn_enabled=args.gnn,
        archlib_path=args.archlib,
    )

    result = model.evaluate()

    # ── 输出 ──
    if args.report or args.demo:
        print(model.to_report(result))

    print(f"\n[RESULT] 融合得分={result['fusion_score']:.1f}  "
          f"基线={result['baseline']['score']:.1f}  "
          f"Δ={result['delta_pct']:+.1f}%  "
          f"置信度={result['fusion_confidence']:.2f}  "
          f"活跃={result['modalities_active']}")

    if args.benchmark:
        print("\n" + "=" * 50)
        print("  单模态 vs 多模态对比")
        print("=" * 50)
        mappings = [
            ("structured", "仅结构化"),
            ("visual", "仅视觉"),
            ("spatial", "仅空间"),
        ]
        for mod_key, label in mappings:
            mod = result["modalities"].get(mod_key, {})
            s = mod.get("score")
            if s is None:
                print(f"  {label:12s}  不可用")
            else:
                print(f"  {label:12s}  {s:6.1f}  (置信度={mod.get('confidence', 0):.2f})")
        print(f"  {'多模态融合':12s}  {result['fusion_score']:6.1f}  (置信度={result['fusion_confidence']:.2f})")

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = _PROJECT_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[OUTPUT] 报告已保存: {out_path}")

    return result


# ============================================================================
#  PART 7: premium_engine 集成接口
# ============================================================================

def multimodal_enhance(parcel_report: dict, client_goal: dict,
                       archlib_projects: list[dict] | None = None,
                       vlm_evidence: dict | None = None,
                       neighbors: list[dict] | None = None,
                       weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
                       gnn_enabled: bool = False) -> dict:
    """为 premium_engine 提供多模态增强的集成接口。

    供 premium_engine.py 调用，在决策报告中增加"多模态估值"段。

    参数:
        parcel_report: report_parcel.py 输出的地块报告
        client_goal: 客户目标（含 expected_price 等）
        archlib_projects: ArchLib 聚合项目列表
        vlm_evidence: archlib_vision.design_evidence() 输出
        neighbors: 周边邻居楼盘列表
        weights: 三模态权重
        gnn_enabled: 是否启用 GNN 修正

    返回:
        multimodal_result dict，可直接嵌入决策报告 JSON
    """
    parcel_data = parcel_report.get("parcel_data") or {}
    market = parcel_report.get("market") or {}

    # 提取坐标
    lat = parcel_data.get("百度地图纬度") or parcel_data.get("blat")
    lng = parcel_data.get("百度地图经度") or parcel_data.get("blng")
    try:
        lat = float(lat) if lat else None
        lng = float(lng) if lng else None
    except (ValueError, TypeError):
        lat, lng = None, None

    model = MultimodalValuation(
        weights=weights,
        parcel_data=parcel_data,
        market_context=market,
        archlib_projects=archlib_projects,
        vlm_evidence=vlm_evidence,
        target_lat=lat,
        target_lng=lng,
        neighbors=neighbors,
        gnn_enabled=gnn_enabled,
    )

    result = model.evaluate()

    # 精简版输出（适合嵌入报告）
    return {
        "multimodal_valuation": {
            "fusion_score": result["fusion_score"],
            "fusion_confidence": result["fusion_confidence"],
            "baseline_structured": result["baseline"]["score"],
            "delta_pct": result["delta_pct"],
            "modalities_active": result["modalities_active"],
            "weights": result["weights"],
            "structured_dimensions": [
                {"key": d["key"], "cn": d["cn"], "score": d["score"], "status": d["status"]}
                for d in result["modalities"]["structured"].get("dimensions", [])
            ],
            "visual_dimensions": [
                {"key": d["key"], "cn": d["cn"], "score": d["score"], "status": d["status"]}
                for d in result["modalities"]["visual"].get("dimensions", [])
            ],
            "spatial_neighbor_count": result["modalities"]["spatial"].get("neighbor_count", 0),
            "source": result["source"],
            "disclaimer": result["disclaimer"],
            "full_result": result,  # 完整结果（可裁剪）
        }
    }


if __name__ == "__main__":
    main()
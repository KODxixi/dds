# -*- coding: utf-8 -*-
"""
Property Graph 知识图谱 — AIPM Section 6.6 建议
====================================================
将楼盘/地块/开发商/政策/客群等实体构建为属性图（Property Graph），
支持复杂关系查询、图分析、可视化导出。

架构：LlamaIndex 2024 PropertyGraph 范式
  - 节点：Project / LandParcel / Developer / Policy / CustomerSegment / POI
  - 边：COMPETES_WITH / DEVELOPED_BY / LOCATED_IN / AFFECTED_BY / TARGETS / NEAR
  - 底层：纯标准库 + numpy 实现（邻接表 + 节点属性字典），不依赖 networkx/igraph

对标论文：Property Graph Index (LlamaIndex, 2024) + 地产空间图建模

用法：
  python scripts/property_graph_kg.py --build --city 三亚
  python scripts/property_graph_kg.py --query competitors --project "某楼盘" --k 5
  python scripts/property_graph_kg.py --query submarket --project "某楼盘" --radius 3.0
  python scripts/property_graph_kg.py --analyze --city 三亚
  python scripts/property_graph_kg.py --export --format mermaid --city 三亚

集成点：
  - gnn_spatial.py 复用图构建逻辑
  - dds_decision_engine.py 增加 kg_insights 段
  - benchmark_engine.py match/extrapolate 图关系增强
"""

from __future__ import annotations
import argparse
import csv as _csv
import json
import math
import sys
import os
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

# ── 数据来源标注 ──
SOURCE = "Vault/2026新楼盘/ 购买数据 + query_local.py DuckDB 查询"
SOURCE_URL = "购买结构化数据（全国新楼盘库 ~128K行 632城，Schema v2.0 253列）"
SOURCE_NOTE = "Property Graph KG 基于楼盘坐标/属性构建图结构，节点属性对齐 Schema v2.0，边权重基于 Haversine 距离/价格差/客群重叠度"

ROOT = Path(__file__).resolve().parent.parent
VAULT_NEW = ROOT / "Vault" / "2026新楼盘"
DATA_OUT = ROOT / "data_out" / "property_graph"


# ═══════════════════════════════════════════════════════════════════
# SECTION 1: 基础工具函数
# ═══════════════════════════════════════════════════════════════════

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两点间球面距离（km）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))


def _safe_float(v, default=0.0) -> float:
    """安全提取浮点数"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    try:
        return float(str(v).replace(",", "").replace("元/㎡", "").replace("万元/套", "").replace("%", "").strip() or 0)
    except (ValueError, TypeError):
        return default


def _safe_str(v) -> str:
    """安全提取字符串"""
    if v is None:
        return ""
    return str(v).strip()


def _price_value(price_raw, ref_price) -> float:
    """从最新价格/参考价格推断单价（元/㎡）"""
    rp = _safe_str(ref_price)
    p = _safe_float(price_raw)
    if p <= 0:
        return 0.0
    if "万元/套" in rp:
        return p * 10000 / 100.0  # 粗略估计：假设 ~100㎡
    return p


def _parse_csv_value(val) -> any:
    """解析 CSV 字段值，处理 JSON 数组/对象"""
    s = _safe_str(val).strip()
    if not s:
        return s
    if s.startswith("[") or s.startswith("{"):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return s
    return s


# ═══════════════════════════════════════════════════════════════════
# SECTION 2: 轻量图数据结构（邻接表 + 节点属性字典）
# ═══════════════════════════════════════════════════════════════════

class PropertyGraph:
    """Property Graph 核心数据结构。

    节点：{node_id: {type, properties}}
    边：{(src, dst, edge_type): {weight, properties}}
    邻接表：{node_id: [(dst, edge_type, weight), ...]}
    """

    def __init__(self):
        self.nodes: dict[str, dict] = {}          # node_id → {type, ...props}
        self.edges: dict[tuple, dict] = {}        # (src, dst, edge_type) → {weight, ...props}
        self.adj: dict[str, list] = defaultdict(list)  # node_id → [(dst, edge_type, weight), ...]
        self.meta: dict = {
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "source_note": SOURCE_NOTE,
            "generated_at": datetime.now().isoformat(),
        }

    # ── 节点管理 ──
    def add_node(self, node_id: str, node_type: str, **properties):
        """添加节点。若已存在则合并属性（不覆盖已有）。"""
        if node_id in self.nodes:
            existing = self.nodes[node_id]
            if existing.get("type") != node_type:
                # 类型冲突时保留原类型
                pass
            for k, v in properties.items():
                if k not in existing or existing[k] is None or existing[k] == "" or existing[k] == 0.0:
                    existing[k] = v
            return
        self.nodes[node_id] = {"type": node_type}
        self.nodes[node_id].update(properties)

    def get_node(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    def get_nodes_by_type(self, node_type: str) -> list[str]:
        return [nid for nid, nd in self.nodes.items() if nd.get("type") == node_type]

    # ── 边管理 ──
    def add_edge(self, src: str, dst: str, edge_type: str, weight: float = 1.0, bidirectional: bool = False, **properties):
        """添加有向边。若 bidirectional=True 则同时添加反向边。"""
        key = (src, dst, edge_type)
        if key in self.edges:
            # 更新权重（取较大值）
            self.edges[key]["weight"] = max(self.edges[key].get("weight", 0), weight)
            return
        self.edges[key] = {"weight": weight}
        self.edges[key].update(properties)
        self.adj[src].append((dst, edge_type, weight))

        if bidirectional:
            rev_key = (dst, src, edge_type)
            if rev_key not in self.edges:
                self.edges[rev_key] = {"weight": weight}
                self.edges[rev_key].update(properties)
                self.adj[dst].append((src, edge_type, weight))

    def get_edge_weight(self, src: str, dst: str, edge_type: str = None) -> float:
        if edge_type:
            edge = self.edges.get((src, dst, edge_type))
            return edge["weight"] if edge else 0.0
        # 查所有边类型，取最大权重
        max_w = 0.0
        for (s, d, _), e in self.edges.items():
            if s == src and d == dst:
                max_w = max(max_w, e.get("weight", 0))
        return max_w

    def get_neighbors(self, node_id: str, edge_type: str = None) -> list[tuple[str, str, float]]:
        """获取邻居列表 [(dst, edge_type, weight), ...]"""
        if edge_type:
            return [(d, et, w) for d, et, w in self.adj.get(node_id, []) if et == edge_type]
        return list(self.adj.get(node_id, []))

    # ── 统计 ──
    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def stats(self) -> dict:
        type_counts = defaultdict(int)
        for nd in self.nodes.values():
            type_counts[nd.get("type", "Unknown")] += 1
        edge_type_counts = defaultdict(int)
        for (_, _, et) in self.edges:
            edge_type_counts[et] += 1
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "node_types": dict(type_counts),
            "edge_types": dict(edge_type_counts),
        }

    # ── 序列化 ──
    def to_dict(self) -> dict:
        """转为 JSON 可序列化字典"""
        edges_serialized = []
        for (src, dst, et), props in self.edges.items():
            edges_serialized.append({
                "source": src, "target": dst, "edge_type": et,
                "weight": props.get("weight", 1.0),
                "properties": {k: v for k, v in props.items() if k != "weight"},
            })
        return {
            "meta": self.meta,
            "nodes": self.nodes,
            "edges": edges_serialized,
        }

    def to_json(self, path: str | Path):
        """导出 JSON 图结构"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def to_mermaid(self) -> str:
        """导出 Mermaid 可视化源码"""
        lines = ["graph TD"]
        node_ids_map = {}
        aliases = {}
        i = 0
        for nid, nd in self.nodes.items():
            alias = f"N{i}"
            aliases[nid] = alias
            node_ids_map[nid] = i
            ntype = nd.get("type", "?")
            name = nd.get("name", nd.get("project_name", nid))[:20]
            label = f"{alias}[{ntype}: {name}]"
            lines.append(f"    {label}")
            i += 1

        for (src, dst, et), props in self.edges.items():
            a_src = aliases.get(src, src)
            a_dst = aliases.get(dst, dst)
            w = props.get("weight", 1.0)
            lines.append(f"    {a_src} -->|{et} ({w:.2f})| {a_dst}")

        return "\n".join(lines)

    def to_adjacency_matrix(self) -> tuple[np.ndarray, list[str]]:
        """导出邻接矩阵（CSV 可用）"""
        node_ids = sorted(self.nodes.keys())
        n = len(node_ids)
        idx_map = {nid: i for i, nid in enumerate(node_ids)}
        mat = np.zeros((n, n), dtype=np.float64)
        for (src, dst, _), props in self.edges.items():
            if src in idx_map and dst in idx_map:
                mat[idx_map[src], idx_map[dst]] = props.get("weight", 1.0)
        return mat, node_ids


# ═══════════════════════════════════════════════════════════════════
# SECTION 3: 图构建 — 从 Vault CSV 自动构建城市级属性图
# ═══════════════════════════════════════════════════════════════════

def build_from_vault(city: str, csv_dir: Path = None, radius_km: float = 3.0,
                     include_poi: bool = True, include_developer: bool = True) -> PropertyGraph:
    """从 Vault CSV 自动构建城市级 Property Graph。

    Args:
        city: 城市名称
        csv_dir: CSV 目录，默认 Vault/2026新楼盘/
        radius_km: 竞品边半径阈值（km）
        include_poi: 是否构建 POI 配套边（基于 CSV 中配套信息）
        include_developer: 是否构建开发商关系边

    Returns:
        PropertyGraph 实例
    """
    if csv_dir is None:
        csv_dir = VAULT_NEW

    g = PropertyGraph()
    g.meta["city"] = city
    g.meta["radius_km"] = radius_km
    g.meta["build_method"] = "build_from_vault"

    # Step 1: 加载楼盘节点
    csv_path = csv_dir / f"新楼盘-{city}.csv"
    if not csv_path.exists():
        print(f"[error] 未找到 {csv_path}")
        return g

    project_nodes = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            name = _safe_str(row.get("楼盘名称", ""))
            if not name:
                continue
            lat = _safe_float(row.get("百度地图纬度") or row.get("BD-09纬度"))
            lng = _safe_float(row.get("百度地图经度") or row.get("BD-09经度"))
            if lat == 0 or lng == 0:
                continue

            node_id = f"project:{name}"
            price_raw = _safe_float(row.get("最新价格"))
            price_ref = _safe_str(row.get("参考价格"))
            unit_price = _price_value(price_raw, price_ref)

            # 提取关键属性（对齐 Schema v2.0）
            plot_ratio = _safe_float(row.get("容积率"))
            green_rate = _safe_float(row.get("绿化率"))
            area = _safe_float(row.get("建筑面积"))
            units = _safe_float(row.get("规划户数"))
            developer = _safe_str(row.get("开发商"))
            developer_brand = _safe_str(row.get("开发商品牌"))
            developer_effective = developer_brand or developer  # 优先用品牌名
            district = _safe_str(row.get("区域名称"))
            sub_district = _safe_str(row.get("子区域名称"))
            address = _safe_str(row.get("地址"))
            sale_status = _safe_str(row.get("销售状态"))
            decoration = _safe_str(row.get("装修情况"))
            property_type = _safe_str(row.get("物业类型"))
            property_company = _safe_str(row.get("物业公司"))
            tenure = _safe_str(row.get("产权年限"))
            tags = _safe_str(row.get("标签列表"))
            all_tags = _safe_str(row.get("所有标签列表"))
            arch_firm = _safe_str(row.get("建筑设计方"))
            landscape_firm = _safe_str(row.get("景观设计方"))
            construction_firm = _safe_str(row.get("施工总承包方"))
            investor = _safe_str(row.get("投资商"))

            props = {
                "name": name, "lat": lat, "lng": lng,
                "unit_price": unit_price, "price_raw": price_raw,
                "price_ref": price_ref,
                "plot_ratio": plot_ratio, "green_rate": green_rate,
                "area": area, "units": units,
                "developer": developer, "developer_brand": developer_brand,
                "developer_effective": developer_effective,
                "district": district,
                "sub_district": sub_district, "address": address,
                "sale_status": sale_status, "decoration": decoration,
                "property_type": property_type, "property_company": property_company,
                "tenure": tenure, "tags": tags, "all_tags": all_tags,
                "arch_firm": arch_firm, "landscape_firm": landscape_firm,
                "construction_firm": construction_firm, "investor": investor,
                "source": "Vault CSV, Schema v2.0",
                "source_url": f"Vault/2026新楼盘/新楼盘-{city}.csv",
            }
            g.add_node(node_id, "Project", **props)
            project_nodes.append(node_id)

    print(f"[KG] 已加载 {len(project_nodes)} 个 Project 节点")

    # Step 2: 构建竞品边（COMPETES_WITH）
    build_competition_edges(g, project_nodes, radius_km)

    # Step 3: 构建开发商节点和边（DEVELOPED_BY）
    if include_developer:
        build_developer_edges(g, project_nodes)

    # Step 4: 构建区位边（LOCATED_IN）
    build_location_edges(g, project_nodes)

    # Step 5: 构建 POI 配套边（NEAR）
    if include_poi:
        build_poi_edges(g, project_nodes)

    return g


def build_competition_edges(g: PropertyGraph, project_nodes: list[str], radius_km: float = 3.0):
    """基于 Haversine 距离构建竞品边（COMPETES_WITH）。

    边权重 = exp(-distance / scale) * (1 - 价格相似度惩罚)
    """
    n = len(project_nodes)
    if n < 2:
        return

    # 提取坐标和价格
    coords = []
    prices = []
    for pid in project_nodes:
        nd = g.get_node(pid)
        if nd:
            coords.append((nd.get("lat", 0), nd.get("lng", 0)))
            prices.append(nd.get("unit_price", 0))
        else:
            coords.append((0, 0))
            prices.append(0)

    scale = radius_km / 2.0  # 距离衰减尺度
    for i in range(n):
        for j in range(i + 1, n):
            lat1, lng1 = coords[i]
            lat2, lng2 = coords[j]
            if lat1 == 0 or lat2 == 0:
                continue
            d = haversine(lat1, lng1, lat2, lng2)
            if d <= radius_km:
                # 距离权重
                dist_w = math.exp(-d / scale)

                # 价格相似度权重（价格越接近，竞争越激烈）
                p1, p2 = prices[i], prices[j]
                if p1 > 0 and p2 > 0:
                    price_ratio = min(p1, p2) / max(p1, p2)
                    price_w = price_ratio  # 0.5-1.0
                else:
                    price_w = 0.5

                weight = dist_w * (0.5 + 0.5 * price_w)

                g.add_edge(
                    project_nodes[i], project_nodes[j],
                    "COMPETES_WITH", weight=round(weight, 4),
                    bidirectional=True,
                    distance_km=round(d, 3),
                    price_similarity=round(price_w, 3),
                )

    edge_count = sum(1 for (s, d, et) in g.edges if et == "COMPETES_WITH")
    print(f"[KG] 已构建 {edge_count} 条 COMPETES_WITH 边 (半径={radius_km}km)")


def build_developer_edges(g: PropertyGraph, project_nodes: list[str]):
    """基于开发商字段构建关系边（DEVELOPED_BY）。

    创建 Developer 节点，连接 Project --DEVELOPED_BY--> Developer。
    同一开发商的多项目自动形成开发商布局网络。
    """
    # 收集开发商（优先用品牌名，品牌为空则用实际公司名）
    developer_projects = defaultdict(list)
    developer_actual = {}  # brand → actual company name
    for pid in project_nodes:
        nd = g.get_node(pid)
        if not nd:
            continue
        dev_effective = nd.get("developer_effective", "")
        dev_actual = nd.get("developer", "")
        if dev_effective:
            developer_projects[dev_effective].append(pid)
            if dev_actual and dev_actual != dev_effective:
                developer_actual[dev_effective] = dev_actual

    for dev_name, projects in developer_projects.items():
        dev_id = f"developer:{dev_name}"
        g.add_node(dev_id, "Developer",
                   name=dev_name,
                   actual_company=developer_actual.get(dev_name, dev_name),
                   project_count=len(projects),
                   source="Vault CSV 开发商/开发商品牌字段",
                   source_url="Vault/2026新楼盘/ 开发商列")

        for pid in projects:
            nd = g.get_node(pid)
            project_count = max(1, len(projects))
            g.add_edge(pid, dev_id, "DEVELOPED_BY", weight=1.0 / project_count,
                       bidirectional=False,
                       developer_name=dev_name)

        # 同一开发商的不同项目之间建立弱关联（同品牌项目）
        for i in range(len(projects)):
            for j in range(i + 1, len(projects)):
                g.add_edge(projects[i], projects[j], "SAME_DEVELOPER",
                           weight=0.3, bidirectional=True,
                           developer_name=dev_name)

    dev_count = len(g.get_nodes_by_type("Developer"))
    edge_count = sum(1 for (s, d, et) in g.edges if et == "DEVELOPED_BY")
    same_dev_count = sum(1 for (s, d, et) in g.edges if et == "SAME_DEVELOPER")
    print(f"[KG] 已构建 {dev_count} 个 Developer 节点, {edge_count} 条 DEVELOPED_BY 边, {same_dev_count} 条 SAME_DEVELOPER 边")


def build_location_edges(g: PropertyGraph, project_nodes: list[str]):
    """构建区位边（LOCATED_IN）。

    项目连接至 District 节点和 SubDistrict 节点。
    """
    districts = set()
    sub_districts = set()

    for pid in project_nodes:
        nd = g.get_node(pid)
        if not nd:
            continue
        dist = nd.get("district", "")
        sub = nd.get("sub_district", "")
        if dist:
            districts.add(dist)
            g.add_edge(pid, f"district:{dist}", "LOCATED_IN", weight=1.0)

        if sub:
            sub_districts.add(sub)
            g.add_edge(pid, f"sub_district:{sub}", "LOCATED_IN", weight=1.0,
                       bidirectional=False)

    # 添加区域节点
    for d in districts:
        g.add_node(f"district:{d}", "District", name=d, source="Vault CSV 区域名称列")
    for s in sub_districts:
        g.add_node(f"sub_district:{s}", "SubDistrict", name=s, source="Vault CSV 子区域名称列")

    print(f"[KG] 已构建 {len(districts)} 个 District 节点, {len(sub_districts)} 个 SubDistrict 节点")


def build_poi_edges(g: PropertyGraph, project_nodes: list[str]):
    """构建配套邻近边（NEAR）。

    从 CSV 配套列提取 POI 信息（学校/医院/商业/地铁），
    构建 Project --NEAR--> POI 关系。
    """
    poi_types = {
        "学校": ["学校", "education", "school"],
        "医院": ["医院", "healthcare", "hospital"],
        "商业": ["购物中心", "商业街", "商场", "mall", "shopping"],
        "地铁": ["地铁", "subway", "metro"],
        "公园": ["公园", "park", "green"],
    }

    poi_count = 0
    for pid in project_nodes:
        nd = g.get_node(pid)
        if not nd:
            continue
        tags = nd.get("tags", "") + " " + nd.get("all_tags", "")
        # 规交信息（地铁相关）
        subway_json = nd.get("subway_json", "")

        for poi_cat, keywords in poi_types.items():
            for kw in keywords:
                if kw in tags:
                    poi_id = f"poi:{poi_cat}:{nd.get('district', '')}"
                    g.add_node(poi_id, "POI",
                               name=f"{nd.get('district', '')}-{poi_cat}",
                               poi_category=poi_cat,
                               source="Vault CSV 标签/规交信息",
                               source_url="Vault/2026新楼盘/ 标签列表+规交信息列")
                    g.add_edge(pid, poi_id, "NEAR", weight=0.5,
                               bidirectional=False,
                               poi_category=poi_cat)
                    poi_count += 1
                    break  # 每个类别只建一条边

    print(f"[KG] 已构建 {poi_count} 条 NEAR 边")


# ═══════════════════════════════════════════════════════════════════
# SECTION 4: 图查询
# ═══════════════════════════════════════════════════════════════════

def get_competitors(g: PropertyGraph, project_name: str, k: int = 5) -> list[dict]:
    """K 近邻竞品查询。

    返回与该楼盘有 COMPETES_WITH 边的竞品，按权重降序。
    """
    node_id = f"project:{project_name}"
    if node_id not in g.nodes:
        # 模糊匹配
        for nid, nd in g.nodes.items():
            if nd.get("type") == "Project" and project_name in nd.get("name", ""):
                node_id = nid
                break
        else:
            return []

    neighbors = g.get_neighbors(node_id, "COMPETES_WITH")
    # 去重并排序（COMPETES_WITH 是双向边，取 inbound+outbound）
    seen = set()
    results = []
    for dst, et, w in neighbors:
        if dst in seen:
            continue
        seen.add(dst)
        nd = g.get_node(dst)
        if nd:
            results.append({
                "name": nd.get("name", dst),
                "weight": round(w, 4),
                "unit_price": nd.get("unit_price", 0),
                "district": nd.get("district", ""),
                "distance_km": _edge_prop(g, node_id, dst, "COMPETES_WITH", "distance_km", None),
                "price_similarity": _edge_prop(g, node_id, dst, "COMPETES_WITH", "price_similarity", None),
            })

    results.sort(key=lambda x: -x["weight"])
    return results[:k]


def _edge_prop(g: PropertyGraph, src: str, dst: str, et: str, key: str, default=None):
    edge = g.edges.get((src, dst, et))
    if edge:
        return edge.get(key, default)
    edge = g.edges.get((dst, src, et))
    if edge:
        return edge.get(key, default)
    return default


def get_developer_portfolio(g: PropertyGraph, developer_name: str) -> list[dict]:
    """开发商城市布局。

    返回该开发商在城中的所有项目及其属性。
    支持模糊匹配：可匹配品牌名或实际公司名。
    """
    dev_id = f"developer:{developer_name}"
    if dev_id not in g.nodes:
        # 模糊匹配：找包含 developer_name 的 Developer 节点
        matched = None
        for nid, nd in g.nodes.items():
            if nd.get("type") == "Developer":
                name = nd.get("name", "")
                actual = nd.get("actual_company", "")
                if developer_name in name or developer_name in actual or name in developer_name:
                    matched = nid
                    break
        if matched:
            dev_id = matched
        else:
            return []

    dev_nd = g.get_node(dev_id)
    dev_display_name = dev_nd.get("name", developer_name) if dev_nd else developer_name

    results = []
    # 查 DEVELOPED_BY 的入边（Project → Developer）
    for (src, dst, et), props in g.edges.items():
        if et == "DEVELOPED_BY" and dst == dev_id:
            nd = g.get_node(src)
            if nd:
                results.append({
                    "name": nd.get("name", src),
                    "unit_price": nd.get("unit_price", 0),
                    "district": nd.get("district", ""),
                    "sale_status": nd.get("sale_status", ""),
                    "property_type": nd.get("property_type", ""),
                    "plot_ratio": nd.get("plot_ratio", 0),
                    "area": nd.get("area", 0),
                })

    # 如果 DEVELOPED_BY 边不够，也查 SAME_DEVELOPER 边
    if not results:
        for (src, dst, et), props in g.edges.items():
            if et == "SAME_DEVELOPER":
                nd_src = g.get_node(src)
                nd_dst = g.get_node(dst)
                if nd_src and nd_src.get("developer_effective", "") == dev_display_name:
                    nd = g.get_node(dst)
                    if nd:
                        results.append({
                            "name": nd.get("name", dst),
                            "unit_price": nd.get("unit_price", 0),
                            "district": nd.get("district", ""),
                            "sale_status": nd.get("sale_status", ""),
                            "property_type": nd.get("property_type", ""),
                            "plot_ratio": nd.get("plot_ratio", 0),
                            "area": nd.get("area", 0),
                        })

    results.sort(key=lambda x: -x["unit_price"])
    return results


def get_submarket(g: PropertyGraph, project_name: str, radius_km: float = 3.0) -> dict:
    """子市场提取。

    提取以目标楼盘为中心、radius_km 范围内的所有竞品，
    计算子市场统计（均价、产品结构、竞争强度）。
    """
    node_id = f"project:{project_name}"
    source_nd = g.get_node(node_id)
    if not source_nd:
        for nid, nd in g.nodes.items():
            if nd.get("type") == "Project" and project_name in nd.get("name", ""):
                node_id = nid
                source_nd = nd
                break
        else:
            return {"error": f"未找到楼盘: {project_name}"}

    src_lat = source_nd.get("lat", 0)
    src_lng = source_nd.get("lng", 0)

    # 通过 Haversine 距离筛选子市场成员
    members = []
    for nid, nd in g.nodes.items():
        if nd.get("type") != "Project" or nid == node_id:
            continue
        lat = nd.get("lat", 0)
        lng = nd.get("lng", 0)
        if lat == 0 or lng == 0:
            continue
        d = haversine(src_lat, src_lng, lat, lng)
        if d <= radius_km:
            members.append({
                "name": nd.get("name", nid),
                "distance_km": round(d, 3),
                "unit_price": nd.get("unit_price", 0),
                "district": nd.get("district", ""),
                "sale_status": nd.get("sale_status", ""),
                "developer": nd.get("developer", ""),
            })

    members.sort(key=lambda x: x["distance_km"])

    prices = [m["unit_price"] for m in members if m["unit_price"] > 0]
    developers = set(m["developer"] for m in members if m["developer"])

    return {
        "center_project": project_name,
        "radius_km": radius_km,
        "member_count": len(members),
        "avg_price": round(np.mean(prices), 0) if prices else 0,
        "median_price": round(np.median(prices), 0) if prices else 0,
        "price_std": round(np.std(prices), 0) if prices else 0,
        "price_range": [round(min(prices), 0), round(max(prices), 0)] if prices else [0, 0],
        "developer_count": len(developers),
        "members": members,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "source_note": f"子市场基于 Haversine 距离 {radius_km}km 筛选，价格来自 Vault CSV 最新价格列",
    }


def find_similar_projects(g: PropertyGraph, project_name: str, k: int = 5) -> list[dict]:
    """相似楼盘查询（图嵌入 + 属性距离混合相似度）。

    使用加权混合：0.4 * 图嵌入余弦相似度 + 0.6 * 属性欧氏距离相似度。
    属性包括：价格、容积率、绿化率、建筑面积、规划户数。
    """
    node_id = f"project:{project_name}"
    if node_id not in g.nodes:
        for nid, nd in g.nodes.items():
            if nd.get("type") == "Project" and project_name in nd.get("name", ""):
                node_id = nid
                break
        else:
            return []

    project_ids = sorted(g.get_nodes_by_type("Project"))
    if len(project_ids) < 2:
        return []

    n = len(project_ids)
    idx_map = {pid: i for i, pid in enumerate(project_ids)}
    target_idx = idx_map.get(node_id)
    if target_idx is None:
        return []

    # ── 1) 图嵌入相似度 ──
    adj = np.zeros((n, n), dtype=np.float64)
    for (src, dst, _), props in g.edges.items():
        if src in idx_map and dst in idx_map:
            w = props.get("weight", 1.0)
            adj[idx_map[src], idx_map[dst]] = w
            adj[idx_map[dst], idx_map[src]] = w

    embedding_dim = min(16, n - 1)
    graph_sim = np.ones(n) * 0.5  # 默认 0.5
    if embedding_dim >= 2:
        try:
            U, S, Vt = np.linalg.svd(adj, full_matrices=False)
            embeddings = U[:, :embedding_dim] * S[:embedding_dim]
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            embeddings = embeddings / norms
            target_vec = embeddings[target_idx]
            graph_sim = embeddings @ target_vec
            # 归一化到 [0, 1]
            g_min, g_max = graph_sim.min(), graph_sim.max()
            if g_max > g_min:
                graph_sim = (graph_sim - g_min) / (g_max - g_min)
        except np.linalg.LinAlgError:
            pass

    # ── 2) 属性相似度 ──
    attrs = []
    attr_names = ["unit_price", "plot_ratio", "green_rate", "area", "units"]
    for pid in project_ids:
        nd = g.get_node(pid)
        if nd:
            attrs.append([nd.get(a, 0) or 0 for a in attr_names])
        else:
            attrs.append([0] * len(attr_names))
    attrs = np.array(attrs, dtype=np.float64)

    # 标准化
    for j in range(attrs.shape[1]):
        col = attrs[:, j]
        if col.max() > col.min():
            attrs[:, j] = (col - col.min()) / (col.max() - col.min())
        else:
            attrs[:, j] = 0.5

    target_attr = attrs[target_idx]
    attr_dists = np.sqrt(((attrs - target_attr) ** 2).sum(axis=1))
    attr_max = attr_dists.max() if attr_dists.max() > 0 else 1.0
    attr_sim = 1.0 - attr_dists / attr_max  # 距离越小越相似

    # ── 3) 混合相似度 ──
    hybrid_sim = 0.4 * graph_sim + 0.6 * attr_sim

    results = []
    for i in range(n):
        if i == target_idx:
            continue
        pid = project_ids[i]
        nd = g.get_node(pid)
        results.append({
            "name": nd.get("name", pid),
            "similarity": round(float(hybrid_sim[i]), 4),
            "graph_sim": round(float(graph_sim[i]), 4),
            "attr_sim": round(float(attr_sim[i]), 4),
            "unit_price": nd.get("unit_price", 0),
            "district": nd.get("district", ""),
            "developer": nd.get("developer", ""),
        })

    results.sort(key=lambda x: -x["similarity"])
    return results[:k]


def trace_supply_chain(g: PropertyGraph, project_name: str) -> dict:
    """追溯开发→设计→施工链。

    从 Project 节点出发，追溯 DEVELOPED_BY 边（开发商）→
    关联设计方/施工方（从节点属性提取）。
    """
    node_id = f"project:{project_name}"
    source_nd = g.get_node(node_id)
    if not source_nd:
        for nid, nd in g.nodes.items():
            if nd.get("type") == "Project" and project_name in nd.get("name", ""):
                node_id = nid
                source_nd = nd
                break
        else:
            return {"error": f"未找到楼盘: {project_name}"}

    chain = {
        "project": source_nd.get("name", project_name),
        "developer": source_nd.get("developer", ""),
        "investor": source_nd.get("investor", ""),
        "architect": source_nd.get("arch_firm", ""),
        "landscape": source_nd.get("landscape_firm", ""),
        "construction": source_nd.get("construction_firm", ""),
        "property_company": source_nd.get("property_company", ""),
        "source": "Vault CSV 开发商/投资商/设计方/施工方/物业公司列",
        "source_url": "Vault/2026新楼盘/ Schema v2.0",
    }

    # 查找开发商的其他项目（同品牌项目）
    dev_name = source_nd.get("developer", "")
    if dev_name:
        chain["sibling_projects"] = []
        dev_id = f"developer:{dev_name}"
        for nid, nd in g.nodes.items():
            if nd.get("type") == "Project" and nd.get("developer") == dev_name and nid != node_id:
                chain["sibling_projects"].append({
                    "name": nd.get("name", nid),
                    "district": nd.get("district", ""),
                    "sale_status": nd.get("sale_status", ""),
                })

    return chain


# ═══════════════════════════════════════════════════════════════════
# SECTION 5: 图分析
# ═══════════════════════════════════════════════════════════════════

def pagerank(g: PropertyGraph, alpha: float = 0.85, max_iter: int = 100, tol: float = 1e-6) -> dict[str, float]:
    """PageRank 中心性。

    识别市场定价锚点（PR 值高的楼盘是区域定价参考锚）。

    Args:
        g: PropertyGraph 实例
        alpha: 阻尼因子
        max_iter: 最大迭代次数
        tol: 收敛阈值
    """
    node_ids = sorted(g.nodes.keys())
    n = len(node_ids)
    if n == 0:
        return {}

    idx_map = {nid: i for i, nid in enumerate(node_ids)}

    # 构建转移矩阵（基于 COMPETES_WITH 边权重）
    M = np.zeros((n, n), dtype=np.float64)
    for (src, dst, _), props in g.edges.items():
        if src in idx_map and dst in idx_map:
            M[idx_map[dst], idx_map[src]] += props.get("weight", 1.0)  # 列归一化

    # 列归一化
    col_sums = M.sum(axis=0)
    for j in range(n):
        if col_sums[j] > 0:
            M[:, j] /= col_sums[j]
        else:
            M[:, j] = 1.0 / n  # 悬垂节点

    # 带阻尼的 PageRank
    pr = np.ones(n) / n
    teleport = np.ones(n) / n

    for _ in range(max_iter):
        prev_pr = pr.copy()
        pr = alpha * M @ pr + (1 - alpha) * teleport
        if np.abs(pr - prev_pr).sum() < tol:
            break

    return {node_ids[i]: round(float(pr[i]), 6) for i in range(n)}


def pagerank_projects(g: PropertyGraph) -> list[dict]:
    """Project 节点的 PageRank 排名（用于识别市场定价锚点）"""
    pr = pagerank(g)
    results = []
    for nid, nd in g.nodes.items():
        if nd.get("type") == "Project":
            results.append({
                "name": nd.get("name", nid),
                "pagerank": pr.get(nid, 0),
                "unit_price": nd.get("unit_price", 0),
                "district": nd.get("district", ""),
                "developer": nd.get("developer", ""),
            })
    results.sort(key=lambda x: -x["pagerank"])
    return results


def community_detection(g: PropertyGraph, max_communities: int = 10) -> dict[str, int]:
    """社区检测（简化 Louvain 算法）。

    基于 modularity 优化的贪心社区检测，识别子市场聚集。

    Args:
        g: PropertyGraph 实例
        max_communities: 最大社区数（用于控制粒度）

    Returns:
        {node_id: community_id}
    """
    project_ids = sorted(g.get_nodes_by_type("Project"))
    n = len(project_ids)
    if n < 2:
        return {pid: 0 for pid in project_ids}

    idx_map = {pid: i for i, pid in enumerate(project_ids)}

    # 构建加权邻接矩阵 + 总边权
    A = np.zeros((n, n), dtype=np.float64)
    for (src, dst, _), props in g.edges.items():
        if src in idx_map and dst in idx_map:
            w = props.get("weight", 1.0)
            A[idx_map[src], idx_map[dst]] += w
            A[idx_map[dst], idx_map[src]] += w

    m = A.sum() / 2.0  # 总边权
    if m < 1e-9:
        return {pid: 0 for pid in project_ids}

    # 初始化：每个节点一个社区
    communities = list(range(n))
    k_i = A.sum(axis=1)  # 节点度

    changed = True
    max_iterations = 50
    iteration = 0

    while changed and iteration < max_iterations:
        changed = False
        iteration += 1

        # 随机顺序遍历节点
        order = np.random.permutation(n)
        for i in order:
            # 当前社区
            current_comm = communities[i]
            # 邻居社区
            neighbor_comms = defaultdict(float)
            for j in range(n):
                if A[i, j] > 0:
                    neighbor_comms[communities[j]] += A[i, j]

            # 移除从当前社区的贡献
            neighbor_comms[current_comm] -= A[i, i]  # 自环

            # 最佳移动
            best_comm = current_comm
            best_delta = 0.0

            for comm, w_ni in neighbor_comms.items():
                if comm == current_comm:
                    continue
                # modularity delta = (w_ni - k_i * sigma_tot / (2m)) / m
                sigma_tot = sum(k_i[j] for j in range(n) if communities[j] == comm)
                delta = (w_ni - k_i[i] * sigma_tot / (2 * m)) / m
                if delta > best_delta:
                    best_delta = delta
                    best_comm = comm

            if best_comm != current_comm:
                communities[i] = best_comm
                changed = True

    # 重新编号社区 ID
    unique_comms = {}
    comm_map = {}
    for comm in communities:
        if comm not in unique_comms:
            unique_comms[comm] = len(unique_comms)
        comm_map[comm] = unique_comms[comm]

    return {project_ids[i]: comm_map[communities[i]] for i in range(n)}


def community_summary(g: PropertyGraph, min_size: int = 3) -> list[dict]:
    """社区检测摘要。只返回规模 >= min_size 的社区。"""
    comm = community_detection(g)
    if not comm:
        return []

    # 按社区分组
    groups = defaultdict(list)
    for nid, cid in comm.items():
        nd = g.get_node(nid)
        if nd:
            groups[cid].append({
                "name": nd.get("name", nid),
                "unit_price": nd.get("unit_price", 0),
                "district": nd.get("district", ""),
                "lat": nd.get("lat", 0),
                "lng": nd.get("lng", 0),
            })

    results = []
    for cid, members in sorted(groups.items()):
        if len(members) < min_size:
            continue
        prices = [m["unit_price"] for m in members if m["unit_price"] > 0]
        districts = set(m["district"] for m in members if m["district"])
        results.append({
            "community_id": cid,
            "size": len(members),
            "avg_price": round(np.mean(prices), 0) if prices else 0,
            "price_range": [round(min(prices), 0), round(max(prices), 0)] if prices else [0, 0],
            "districts": list(districts),
            "members": [m["name"] for m in members],
        })
    results.sort(key=lambda x: -x["size"])
    return results


def shortest_path(g: PropertyGraph, source_name: str, target_name: str) -> dict:
    """最短路径：两个楼盘之间的竞争传导链。

    使用 Dijkstra 算法，边权重 = 1 - normalized_weight（距离越近权重越大 → 路径越短）。
    """
    src_id = f"project:{source_name}"
    tgt_id = f"project:{target_name}"

    for nid, nd in g.nodes.items():
        if nd.get("type") == "Project":
            if source_name in nd.get("name", ""):
                src_id = nid
            if target_name in nd.get("name", ""):
                tgt_id = nid

    if src_id not in g.nodes or tgt_id not in g.nodes:
        return {"error": f"未找到楼盘: {source_name} 或 {target_name}"}

    if src_id == tgt_id:
        return {"path": [source_name], "distance": 0.0, "hops": 0}

    # Dijkstra
    dist = {nid: float("inf") for nid in g.nodes}
    prev = {nid: None for nid in g.nodes}
    dist[src_id] = 0.0
    pq = [(0.0, src_id)]

    while pq:
        d, u = min(pq, key=lambda x: x[0])
        pq.remove((d, u))

        if d > dist[u]:
            continue

        if u == tgt_id:
            break

        for v, et, w in g.adj.get(u, []):
            # 边成本 = 1/w（权重越大距离越近）
            if w > 0:
                cost = 1.0 / w
            else:
                cost = float("inf")
            alt = dist[u] + cost
            if alt < dist[v]:
                dist[v] = alt
                prev[v] = (u, et, w)
                pq.append((alt, v))

    if dist[tgt_id] == float("inf"):
        return {"error": "两楼盘之间无竞争传导路径", "source": source_name, "target": target_name}

    # 重建路径
    path = []
    current = tgt_id
    while current != src_id:
        u, et, w = prev[current]
        nd = g.get_node(current)
        path.append({
            "node": nd.get("name", current) if nd else current,
            "from": g.get_node(u).get("name", u) if g.get_node(u) else u,
            "edge_type": et,
            "weight": round(w, 4),
        })
        current = u

    path_rev = list(reversed(path))
    hops = len(path_rev)

    # 路径节点名称
    node_names = [source_name]
    for step in path_rev:
        node_names.append(step["node"])

    return {
        "source": source_name,
        "target": target_name,
        "hops": hops,
        "total_cost": round(dist[tgt_id], 4),
        "path": node_names,
        "path_details": path_rev,
        "source_note": "边成本 = 1/weight, 权重越大(竞争越强)路径越短",
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 6: 综合分析
# ═══════════════════════════════════════════════════════════════════

def analyze_city(g: PropertyGraph) -> dict:
    """城市级综合分析：PageRank + 社区检测 + 统计摘要。

    Returns:
        {
            "stats": {...},
            "top_pagerank": [...],
            "communities": [...],
            "market_anchors": [...],
        }
    """
    stats = g.stats()

    # PageRank
    pr_projects = pagerank_projects(g)
    top_pr = pr_projects[:10]

    # 社区检测
    communities = community_summary(g)

    # 市场定价锚点（PR 高 + 价格高的楼盘，使用相对阈值）
    pr_values = [p["pagerank"] for p in pr_projects if p["pagerank"] > 0]
    if pr_values:
        pr_threshold = np.percentile(pr_values, 90)  # Top 10% PR
    else:
        pr_threshold = 0.001
    market_anchors = [
        p for p in pr_projects
        if p["pagerank"] >= pr_threshold and p["unit_price"] > 0
    ][:5]

    return {
        "stats": stats,
        "top_pagerank": top_pr,
        "communities": communities,
        "market_anchors": market_anchors,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "source_note": "PageRank 识别市场定价锚点；社区检测基于 modularity 优化识别子市场聚集",
        "generated_at": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 7: 集成接口 — 供其他模块调用
# ═══════════════════════════════════════════════════════════════════

# 全局缓存（按城市缓存图实例）
_graph_cache: dict[str, PropertyGraph] = {}


def get_or_build(city: str, radius_km: float = 3.0) -> PropertyGraph:
    """获取或构建城市级 PropertyGraph（带缓存）。

    供 gnn_spatial.py / dds_decision_engine.py / benchmark_engine.py 调用。
    """
    cache_key = f"{city}_{radius_km}"
    if cache_key in _graph_cache:
        return _graph_cache[cache_key]

    g = build_from_vault(city, radius_km=radius_km)
    _graph_cache[cache_key] = g
    return g


def kg_insights(city: str, project_name: str = None, radius_km: float = 3.0) -> dict:
    """决策引擎集成接口：生成 KG 洞察段。

    供 dds_decision_engine.py 的 run_decision_engine 调用，
    在报告 JSON 中增加 kg_insights 段。

    Returns:
        {
            "competitors": [...],
            "submarket": {...},
            "similar_projects": [...],
            "supply_chain": {...},
            "market_anchors": [...],
            "communities": [...],
        }
    """
    g = get_or_build(city, radius_km)

    insights = {
        "city": city,
        "graph_stats": g.stats(),
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "source_note": SOURCE_NOTE,
    }

    if project_name:
        insights["competitors"] = get_competitors(g, project_name, k=5)
        insights["submarket"] = get_submarket(g, project_name, radius_km)
        insights["similar_projects"] = find_similar_projects(g, project_name, k=5)
        insights["supply_chain"] = trace_supply_chain(g, project_name)

    # 市场级分析
    pr_projects = pagerank_projects(g)
    pr_values = [p["pagerank"] for p in pr_projects if p["pagerank"] > 0]
    pr_threshold = np.percentile(pr_values, 90) if pr_values else 0.001
    insights["market_anchors"] = [p for p in pr_projects if p["pagerank"] >= pr_threshold and p["unit_price"] > 0][:5]
    insights["communities"] = community_summary(g, min_size=3)[:5]

    return insights


def enhance_benchmark_match(g: PropertyGraph, project_name: str, k: int = 3) -> list[dict]:
    """benchmark_engine.py 集成接口：为 match/extrapolate 提供图关系增强。

    返回与目标项目图结构相似的竞品，作为标杆匹配的补充证据。
    """
    similar = find_similar_projects(g, project_name, k=k)
    return [{
        "name": s["name"],
        "graph_similarity": s["similarity"],
        "unit_price": s["unit_price"],
        "district": s["district"],
        "note": "基于图嵌入余弦相似度（Property Graph KG）",
    } for s in similar]


def clear_cache():
    """清除图缓存"""
    _graph_cache.clear()


# ═══════════════════════════════════════════════════════════════════
# SECTION 8: CLI 接口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Property Graph 知识图谱 — AIPM Section 6.6",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/property_graph_kg.py --build --city 三亚
  python scripts/property_graph_kg.py --build --city 三亚 --radius 5.0
  python scripts/property_graph_kg.py --query competitors --project "佳兆业海棠伴山" --city 三亚 --k 5
  python scripts/property_graph_kg.py --query submarket --project "佳兆业海棠伴山" --city 三亚 --radius 3.0
  python scripts/property_graph_kg.py --query portfolio --developer "佳兆业集团" --city 三亚
  python scripts/property_graph_kg.py --query similar --project "佳兆业海棠伴山" --city 三亚 --k 5
  python scripts/property_graph_kg.py --query supply-chain --project "佳兆业海棠伴山" --city 三亚
  python scripts/property_graph_kg.py --query path --source "A楼盘" --target "B楼盘" --city 三亚
  python scripts/property_graph_kg.py --analyze --city 三亚
  python scripts/property_graph_kg.py --export --format mermaid --city 三亚
  python scripts/property_graph_kg.py --export --format json --city 三亚
  python scripts/property_graph_kg.py --export --format csv --city 三亚
        """,
    )

    parser.add_argument("--build", action="store_true", help="从 Vault CSV 构建城市级属性图")
    parser.add_argument("--query", choices=["competitors", "portfolio", "submarket", "similar", "supply-chain", "path"],
                        help="图查询类型")
    parser.add_argument("--analyze", action="store_true", help="城市级综合分析（PageRank + 社区检测）")
    parser.add_argument("--export", action="store_true", help="导出图结构")
    parser.add_argument("--city", help="城市名称")
    parser.add_argument("--project", help="目标楼盘名称")
    parser.add_argument("--source", help="源楼盘名称（最短路径查询）")
    parser.add_argument("--target", help="目标楼盘名称（最短路径查询）")
    parser.add_argument("--developer", help="开发商名称（portfolio 查询）")
    parser.add_argument("--k", type=int, default=5, help="返回结果数（默认 5）")
    parser.add_argument("--radius", type=float, default=3.0, help="半径阈值 km（默认 3.0）")
    parser.add_argument("--format", choices=["json", "mermaid", "csv"], default="json",
                        help="导出格式（默认 json）")
    parser.add_argument("--csv-dir", default="Vault/2026新楼盘", help="CSV 目录")
    parser.add_argument("--output-dir", default="data_out/property_graph", help="输出目录")

    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    csv_dir = root / args.csv_dir
    out_dir = root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Build ──
    if args.build:
        if not args.city:
            print("[error] --build 需要 --city")
            return
        print(f"[KG] 构建 {args.city} Property Graph...")
        g = build_from_vault(args.city, csv_dir, radius_km=args.radius)
        stats = g.stats()
        print(f"\n=== 图统计 ===")
        print(f"  节点数: {stats['node_count']}")
        print(f"  边数:   {stats['edge_count']}")
        print(f"  节点类型: {stats['node_types']}")
        print(f"  边类型:   {stats['edge_types']}")

        # 自动缓存
        cache_key = f"{args.city}_{args.radius}"
        _graph_cache[cache_key] = g

        # 自动导出 JSON
        g.to_json(out_dir / f"{args.city}_property_graph.json")
        print(f"\n[done] 图已缓存，JSON 已导出到 {out_dir / f'{args.city}_property_graph.json'}")
        return

    # ── Query ──
    if args.query:
        if not args.city:
            print("[error] 查询需要 --city")
            return
        g = get_or_build(args.city, args.radius)

        if args.query == "competitors":
            if not args.project:
                print("[error] competitors 查询需要 --project")
                return
            results = get_competitors(g, args.project, k=args.k)
            print(f"\n=== {args.project} 的 K={args.k} 近邻竞品 ===")
            for r in results:
                dist_str = f" {r['distance_km']}km" if r.get("distance_km") else ""
                print(f"  {r['name']}: 权重={r['weight']}, 单价={r['unit_price']:.0f}{dist_str}")

        elif args.query == "portfolio":
            if not args.developer:
                print("[error] portfolio 查询需要 --developer")
                return
            results = get_developer_portfolio(g, args.developer)
            print(f"\n=== {args.developer} 城市布局 ({len(results)} 个项目) ===")
            for r in results:
                print(f"  {r['name']}: {r['district']} | {r['property_type']} | "
                      f"单价={r['unit_price']:.0f} | {r['sale_status']}")

        elif args.query == "submarket":
            if not args.project:
                print("[error] submarket 查询需要 --project")
                return
            result = get_submarket(g, args.project, args.radius)
            print(f"\n=== {args.project} 子市场 (半径={args.radius}km) ===")
            print(f"  成员数: {result['member_count']}")
            print(f"  均价: {result['avg_price']:.0f} 元/㎡")
            print(f"  中位价: {result['median_price']:.0f} 元/㎡")
            print(f"  价格区间: {result['price_range']}")
            print(f"  开发商数: {result['developer_count']}")
            for m in result.get("members", [])[:10]:
                print(f"    {m['name']}: {m['distance_km']}km, {m['unit_price']:.0f}元/㎡, {m['developer']}")

        elif args.query == "similar":
            if not args.project:
                print("[error] similar 查询需要 --project")
                return
            results = find_similar_projects(g, args.project, k=args.k)
            print(f"\n=== 与 {args.project} 最相似的 K={args.k} 个楼盘 ===")
            for r in results:
                print(f"  {r['name']}: 相似度={r['similarity']:.4f}, "
                      f"单价={r['unit_price']:.0f}, {r['district']}")

        elif args.query == "supply-chain":
            if not args.project:
                print("[error] supply-chain 查询需要 --project")
                return
            result = trace_supply_chain(g, args.project)
            print(f"\n=== {args.project} 开发→设计→施工链 ===")
            for k, v in result.items():
                if k == "sibling_projects":
                    print(f"  同品牌项目 ({len(v)} 个):")
                    for sp in v:
                        print(f"    - {sp['name']} ({sp['district']}, {sp['sale_status']})")
                elif k not in ("source", "source_url"):
                    print(f"  {k}: {v}")

        elif args.query == "path":
            if not args.source or not args.target:
                print("[error] path 查询需要 --source 和 --target")
                return
            result = shortest_path(g, args.source, args.target)
            if "error" in result:
                print(f"[error] {result['error']}")
            else:
                print(f"\n=== {args.source} → {args.target} 竞争传导链 ===")
                print(f"  跳数: {result['hops']}")
                print(f"  总成本: {result['total_cost']}")
                print(f"  路径: {' -> '.join(result['path'])}")
                for step in result.get("path_details", []):
                    print(f"    {step['from']} --{step['edge_type']}--> {step['node']} (w={step['weight']})")

        return

    # ── Analyze ──
    if args.analyze:
        if not args.city:
            print("[error] --analyze 需要 --city")
            return
        g = get_or_build(args.city, args.radius)
        analysis = analyze_city(g)

        print(f"\n=== {args.city} KG 综合分析 ===")
        print(f"\n[图统计]")
        print(f"  节点数: {analysis['stats']['node_count']}")
        print(f"  边数:   {analysis['stats']['edge_count']}")
        print(f"  节点类型: {analysis['stats']['node_types']}")
        print(f"  边类型:   {analysis['stats']['edge_types']}")

        print(f"\n[PageRank 定价锚点 TOP 10]")
        for p in analysis["top_pagerank"]:
            print(f"  {p['name']}: PR={p['pagerank']:.6f}, 单价={p['unit_price']:.0f}, {p['district']}")

        print(f"\n[社区检测]")
        for c in analysis["communities"]:
            print(f"  社区 {c['community_id']}: {c['size']}个楼盘, "
                  f"均价={c['avg_price']:.0f}, 区域={c['districts']}")

        print(f"\n[市场定价锚点]")
        for a in analysis["market_anchors"]:
            print(f"  {a['name']}: PR={a['pagerank']:.6f}, 单价={a['unit_price']:.0f}")

        # 保存分析结果
        out_path = out_dir / f"{args.city}_analysis.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[done] 分析结果已保存到 {out_path}")
        return

    # ── Export ──
    if args.export:
        if not args.city:
            print("[error] --export 需要 --city")
            return
        g = get_or_build(args.city, args.radius)

        if args.format == "json":
            path = out_dir / f"{args.city}_property_graph.json"
            g.to_json(path)
            print(f"[done] JSON 导出到 {path}")

        elif args.format == "mermaid":
            mermaid_src = g.to_mermaid()
            path = out_dir / f"{args.city}_property_graph.mermaid"
            with open(path, "w", encoding="utf-8") as f:
                f.write(mermaid_src)
            print(f"[done] Mermaid 导出到 {path}")
            print(f"\n--- Mermaid 源码预览（前 30 行）---")
            for line in mermaid_src.split("\n")[:30]:
                print(line)

        elif args.format == "csv":
            mat, node_ids = g.to_adjacency_matrix()
            path = out_dir / f"{args.city}_adjacency_matrix.csv"
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = _csv.writer(f)
                writer.writerow([""] + node_ids)
                for i, nid in enumerate(node_ids):
                    writer.writerow([nid] + [round(mat[i, j], 4) for j in range(len(node_ids))])
            print(f"[done] CSV 邻接矩阵导出到 {path}")

        return

    # ── 无参数：打印帮助 ──
    parser.print_help()


if __name__ == "__main__":
    main()
"""
GNN 空间依赖模型 — AIPM Phase 3 建议
基于竞品楼盘的空间位置关系构建图结构，捕获空间依赖对价格的影响

原理：
  1. 将城市内楼盘构建为图 G=(V,E)
  2. 节点 V = 每个楼盘，特征 = 价格/容积率/绿化率/面积等
  3. 边 E = Haversine 距离 < 阈值（默认 3km）的楼盘之间
  4. 边权重 = exp(-distance / scale)，距离越近权重越大
  5. 图卷积聚合邻居信息 → 价格修正

对标论文：Riveros et al. (2024) PD-TGCN — Scalable Property Valuation via Graph-based DL

用法：
  python scripts/gnn_spatial.py --city 三亚 --radius 3.0
  python scripts/gnn_spatial.py --city 三亚 --radius 5.0 --output

架构：纯 numpy + 标准库（无 torch 依赖），可作为 PyTorch Geometric 的轻量降级方案
"""

from __future__ import annotations
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

# ── 数据来源标注 ──
SOURCE = "Vault/2026新楼盘/ 购买数据 + query_local.py DuckDB 查询"
SOURCE_URL = "购买结构化数据（全国新楼盘库 ~128K行 632城）"
SOURCE_NOTE = "GNN 空间依赖模型基于楼盘坐标和属性构建图结构，不依赖外部 API"


# Haversine 距离
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
        return float(str(v).replace(",", "").replace("元/㎡", "").replace("%", "").strip() or 0)
    except (ValueError, TypeError):
        return default


def load_nodes(city: str, csv_dir: Path) -> tuple[list[dict], np.ndarray]:
    """从 Vault CSV 加载楼盘节点"""
    import csv as _csv

    csv_path = csv_dir / f"新楼盘-{city}.csv"
    if not csv_path.exists():
        print(f"[error] 未找到 {csv_path}")
        return [], np.array([])

    nodes = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            lat = _safe_float(row.get("百度地图纬度") or row.get("纬度") or row.get("BD-09纬度"))
            lng = _safe_float(row.get("百度地图经度") or row.get("经度") or row.get("BD-09经度"))
            if lat == 0 or lng == 0:
                continue
            price = _safe_float(row.get("最新价格"))
            plot_ratio = _safe_float(row.get("容积率"))
            green_rate = _safe_float(row.get("绿化率"))
            area = _safe_float(row.get("建筑面积"))
            units = _safe_float(row.get("规划户数"))
            parking = _safe_float(row.get("车位数"))

            nodes.append({
                "name": row.get("楼盘名称", ""),
                "lat": lat, "lng": lng,
                "price": price,
                "plot_ratio": plot_ratio,
                "green_rate": green_rate,
                "area": area,
                "units": units,
                "parking": parking,
            })

    if not nodes:
        return [], np.array([])

    # 特征矩阵：标准化
    features = np.array([
        [n["price"], n["plot_ratio"], n["green_rate"], n["area"], n["units"], n["parking"]]
        for n in nodes
    ], dtype=np.float64)

    # 标准化到 [0,1]
    for j in range(features.shape[1]):
        col = features[:, j]
        if col.max() > col.min():
            features[:, j] = (col - col.min()) / (col.max() - col.min())
        else:
            features[:, j] = 0.5

    return nodes, features


def build_graph(nodes: list[dict], radius_km: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    """构建邻接矩阵和边权重矩阵"""
    n = len(nodes)
    adj = np.zeros((n, n), dtype=np.float64)
    edges = []

    for i in range(n):
        for j in range(i + 1, n):
            d = haversine(nodes[i]["lat"], nodes[i]["lng"], nodes[j]["lat"], nodes[j]["lng"])
            if d <= radius_km:
                w = math.exp(-d / 1.0)  # 距离衰减 scale=1km
                adj[i, j] = w
                adj[j, i] = w
                edges.append((i, j, d, w))

    return adj, np.array(edges, dtype=np.float64) if edges else np.zeros((0, 4))


def graph_convolution(features: np.ndarray, adj: np.ndarray, iterations: int = 3) -> np.ndarray:
    """轻量图卷积：邻居加权平均聚合"""
    n = features.shape[0]
    if n == 0:
        return features

    H = features.copy()
    D_inv = np.zeros((n, n))
    for i in range(n):
        deg = adj[i].sum()
        if deg > 0:
            D_inv[i, i] = 1.0 / deg

    for _ in range(iterations):
        # 邻居聚合：H_new = (1-alpha) * H + alpha * D^-1 * A * H
        H = 0.5 * H + 0.5 * D_inv @ adj @ H

    return H


def price_correction(nodes: list[dict], features: np.ndarray, conv_features: np.ndarray, adj: np.ndarray) -> list[dict]:
    """基于图卷积的价格修正（空间平滑）"""
    results = []
    for i, n in enumerate(nodes):
        # 原始价格（标准化后）
        orig_price_norm = features[i, 0]
        # 图卷积后的价格（融合邻居信息）
        conv_price_norm = conv_features[i, 0]

        # 邻居数量
        neighbor_count = int((adj[i] > 0).sum())

        # 价格偏差：正=被邻居拉高（说明竞品更高价），负=被邻居拉低（说明竞品更低价）
        if orig_price_norm > 0:
            price_deviation = (conv_price_norm - orig_price_norm) / orig_price_norm
        else:
            price_deviation = 0.0

        # 修正后价格（原始价格 * 邻居影响）
        orig_price = n["price"]
        if orig_price > 0 and neighbor_count >= 2:
            corrected_price = orig_price * (1 + 0.3 * price_deviation)
        else:
            corrected_price = orig_price

        results.append({
            "name": n["name"],
            "lat": n["lat"],
            "lng": n["lng"],
            "original_price": round(orig_price),
            "corrected_price": round(corrected_price),
            "price_deviation_pct": round(price_deviation * 100, 1),
            "neighbor_count": neighbor_count,
            "spatial_signal": "↑ 被高价值邻居拉高" if price_deviation > 0.05 else (
                "↓ 被低价值邻居拉低" if price_deviation < -0.05 else "→ 与邻居价格一致"
            ),
        })

    return results


def compute_spatial_stats(results: list[dict]) -> dict:
    """空间统计摘要"""
    if not results:
        return {"status": "no_data"}

    prices = [r["original_price"] for r in results if r["original_price"] > 0]
    corrections = [r["price_deviation_pct"] for r in results if r["neighbor_count"] >= 2]
    neighbor_counts = [r["neighbor_count"] for r in results]

    return {
        "node_count": len(results),
        "has_price": sum(1 for p in prices if p > 0),
        "avg_neighbors": round(np.mean(neighbor_counts), 1) if neighbor_counts else 0,
        "max_neighbors": int(max(neighbor_counts)) if neighbor_counts else 0,
        "isolated_nodes": sum(1 for n in neighbor_counts if n == 0),
        "avg_price_correction_pct": round(np.mean(corrections), 1) if corrections else 0,
        "price_std": round(np.std(prices), 0) if prices else 0,
        "price_std_corrected": round(np.std([r["corrected_price"] for r in results if r["corrected_price"] > 0]), 0) if results else 0,
        "outliers": [
            r for r in results
            if abs(r["price_deviation_pct"]) > 20 and r["neighbor_count"] >= 3
        ][:10],
    }


def main():
    parser = argparse.ArgumentParser(description="GNN 空间依赖模型")
    parser.add_argument("--city", required=True, help="城市名称")
    parser.add_argument("--radius", type=float, default=3.0, help="邻域半径 km (默认 3.0)")
    parser.add_argument("--iterations", type=int, default=3, help="图卷积迭代次数 (默认 3)")
    parser.add_argument("--output", action="store_true", help="输出 JSON 到 data_out/")
    parser.add_argument("--csv-dir", default="Vault/2026新楼盘", help="CSV 目录")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    csv_dir = root / args.csv_dir

    print(f"[GNN] 加载 {args.city} 楼盘数据...")
    nodes, features = load_nodes(args.city, csv_dir)
    if not nodes:
        print("[error] 无有效楼盘数据")
        return

    print(f"[GNN] 节点数: {len(nodes)}, 有效价格: {sum(1 for n in nodes if n['price'] > 0)}")
    print(f"[GNN] 构建空间图 (半径={args.radius}km)...")
    adj, edges = build_graph(nodes, args.radius)

    edge_count = int((adj > 0).sum() / 2)
    print(f"[GNN] 边数: {edge_count}, 平均度: {adj.sum()/len(nodes):.1f}")
    print(f"[GNN] 图卷积 (迭代={args.iterations})...")
    conv_features = graph_convolution(features, adj, args.iterations)

    print(f"[GNN] 价格修正...")
    results = price_correction(nodes, features, conv_features, adj)
    stats = compute_spatial_stats(results)

    print(f"\n=== 空间依赖统计 ===")
    print(f"  节点数:       {stats['node_count']}")
    print(f"  有价格:       {stats['has_price']}")
    print(f"  平均邻居:     {stats['avg_neighbors']}")
    print(f"  最大邻居:     {stats['max_neighbors']}")
    print(f"  孤立节点:     {stats['isolated_nodes']}")
    print(f"  平均价格修正: {stats['avg_price_correction_pct']:.1f}%")
    print(f"  价格标准差(原始): {stats['price_std']:.0f}")
    print(f"  价格标准差(修正): {stats['price_std_corrected']:.0f}")

    if stats.get("outliers"):
        print(f"\n=== 空间异常楼盘 (偏差>20%, 邻居>=3) ===")
        for r in stats["outliers"]:
            print(f"  {r['name']}: 原始{r['original_price']}→修正{r['corrected_price']} ({r['price_deviation_pct']:+.1f}%) {r['spatial_signal']}")

    if args.output:
        out_dir = root / "data_out" / "gnn_spatial"
        out_dir.mkdir(parents=True, exist_ok=True)
        output = {
            "meta": {
                "city": args.city,
                "radius_km": args.radius,
                "iterations": args.iterations,
                "generated_at": datetime.now().isoformat(),
                "source": SOURCE,
                "source_url": SOURCE_URL,
                "source_note": SOURCE_NOTE,
            },
            "stats": stats,
            "results": results,
        }
        out_path = out_dir / f"{args.city}_gnn_spatial.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n[done] 输出: {out_path}")


if __name__ == "__main__":
    main()
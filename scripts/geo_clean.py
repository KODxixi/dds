"""
Vault 坐标清洗模块 — 实现 T1 数据治理任务
剔除越界/脏坐标，加 bool 列 geo_valid（非破坏）
"""
import math
import pandas as pd


CHINA_BBOX = (3.0, 54.0, 73.0, 135.0)  # (lat_lo, lat_hi, lng_lo, lng_hi)
CITY_MAX_KM = {
    "三亚": 80,
    "杭州": 130,
    "上海": 120,
    "青岛": 130,
}


def city_center(df: pd.DataFrame) -> tuple[float, float] | None:
    """
    数据自适应中心：取 geo 初判有效点的经纬中位数
    返回 (lat_median, lng_median)，如无有效点返回 None
    """
    try:
        lat_lo, lat_hi, lng_lo, lng_hi = CHINA_BBOX
        valid = df[
            (df["lat"].notna()) & (df["lng"].notna()) &
            (df["lat"] != 0) & (df["lng"] != 0) &
            (df["lat"] >= lat_lo) & (df["lat"] <= lat_hi) &
            (df["lng"] >= lng_lo) & (df["lng"] <= lng_hi)
        ]
        if valid.empty:
            return None
        return (valid["lat"].median(), valid["lng"].median())
    except Exception:
        return None


def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """计算两点间 Haversine 距离（单位：km）"""
    radius = 6371.0088
    lng1, lat1, lng2, lat2 = map(math.radians, [lng1, lat1, lng2, lat2])
    dlng = lng2 - lng1
    dlat = lat2 - lat1
    a = (
        math.sin(dlat / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


def clean_coords(df: pd.DataFrame, city: str) -> pd.DataFrame:
    """
    为 DataFrame 加 bool 列 geo_valid（不删行不改源）
    invalid = lat/lng 非数 | ==0 | 出 CHINA_BBOX | 距中位中心 > CITY_MAX_KM[city]
    """
    df = df.copy()  # 非破坏
    lat_lo, lat_hi, lng_lo, lng_hi = CHINA_BBOX
    max_km = CITY_MAX_KM.get(city, 130)

    # 初判：是否在 CHINA_BBOX 内且非零非空
    in_bbox = (
        (df["lat"].notna()) & (df["lng"].notna()) &
        (df["lat"] != 0) & (df["lng"] != 0) &
        (df["lat"] >= lat_lo) & (df["lat"] <= lat_hi) &
        (df["lng"] >= lng_lo) & (df["lng"] <= lng_hi)
    )

    # 计算到中位中心的距离
    center = city_center(df)
    if center is not None:
        lat_center, lng_center = center
        distances = df.apply(
            lambda row: (
                haversine_km(row["lng"], row["lat"], lng_center, lat_center)
                if in_bbox[row.name]
                else float("inf")
            ),
            axis=1,
        )
        within_radius = distances <= max_km
    else:
        # 无有效中心点：所有 in_bbox 的点都是无效的（无法判定距离）
        # 这种极端情况表示数据质量很差，保守起见全标为无效
        within_radius = pd.Series([False] * len(df), index=df.index)

    df["geo_valid"] = in_bbox & within_radius
    return df

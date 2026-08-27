"""Vault 坐标质量分类（非破坏）。

``geo_status`` 保留异常原因，``geo_valid`` 继续提供向后兼容的布尔结果。
"""
import math
import pandas as pd


CHINA_BBOX = (3.0, 54.0, 73.0, 135.0)  # (lat_lo, lat_hi, lng_lo, lng_hi)
CITY_MAX_KM = {
    "上海": 120,
    "青岛": 130,
}

# 两个业务城市使用明确的预期覆盖范围，不再以中心半径误伤远郊/供应商市场圈。
# (lat_lo, lat_hi, lng_lo, lng_hi)
EXPECTED_SCOPE_BBOX = {
    # 杭州市域，覆盖淳安、建德等西部县市。
    "杭州": (28.95, 30.65, 118.0, 120.75),
    # 供应商“三亚市场”口径：三亚、乐东、陵水、万宁、东方；排除海口。
    "三亚": (18.0, 19.65, 108.4, 111.1),
}


def city_center(df: pd.DataFrame) -> tuple[float, float] | None:
    """
    数据自适应中心：取 geo 初判有效点的经纬中位数
    返回 (lat_median, lng_median)，如无有效点返回 None
    """
    try:
        lat = pd.to_numeric(df["lat"], errors="coerce")
        lng = pd.to_numeric(df["lng"], errors="coerce")
        lat_lo, lat_hi, lng_lo, lng_hi = CHINA_BBOX
        valid = (
            lat.notna() & lng.notna() &
            lat.ne(0) & lng.ne(0) &
            lat.between(lat_lo, lat_hi) & lng.between(lng_lo, lng_hi)
        )
        if not valid.any():
            return None
        return (float(lat[valid].median()), float(lng[valid].median()))
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
    添加 ``geo_status`` 和 ``geo_valid``，不删行、不修改输入 DataFrame。

    状态优先级：missing -> zero -> outside_china ->
    outside_expected_scope/centroid_outlier -> valid。
    """
    df = df.copy()
    lat = pd.to_numeric(df["lat"], errors="coerce")
    lng = pd.to_numeric(df["lng"], errors="coerce")
    lat_lo, lat_hi, lng_lo, lng_hi = CHINA_BBOX

    missing = lat.isna() | lng.isna()
    zero = ~missing & (lat.eq(0) | lng.eq(0))
    in_china = lat.between(lat_lo, lat_hi) & lng.between(lng_lo, lng_hi)
    outside_china = ~missing & ~zero & ~in_china
    candidate = ~missing & ~zero & ~outside_china

    status = pd.Series("valid", index=df.index, dtype="object")
    status.loc[missing] = "missing"
    status.loc[zero] = "zero"
    status.loc[outside_china] = "outside_china"

    scope = EXPECTED_SCOPE_BBOX.get(city)
    if scope is not None:
        scope_lat_lo, scope_lat_hi, scope_lng_lo, scope_lng_hi = scope
        in_scope = (
            lat.between(scope_lat_lo, scope_lat_hi) &
            lng.between(scope_lng_lo, scope_lng_hi)
        )
        status.loc[candidate & ~in_scope] = "outside_expected_scope"
    else:
        center = city_center(pd.DataFrame({"lat": lat, "lng": lng}, index=df.index))
        if center is not None:
            lat_center, lng_center = center
            max_km = CITY_MAX_KM.get(city, 130)
            distances = pd.Series(
                [
                    haversine_km(row_lng, row_lat, lng_center, lat_center)
                    if is_candidate else float("inf")
                    for row_lat, row_lng, is_candidate in zip(lat, lng, candidate)
                ],
                index=df.index,
            )
            status.loc[candidate & distances.gt(max_km)] = "centroid_outlier"

    df["geo_status"] = status
    df["geo_valid"] = status.eq("valid")
    return df

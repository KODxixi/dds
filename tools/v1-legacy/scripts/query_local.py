"""
DDS 本地查询模块 — DuckDB + 安居客 CSV
读取 Vault/2026新楼盘/*.csv，零配置

用法：
  python query_local.py avg-price --city 三亚 --district 海棠区
  python query_local.py competitors --city 杭州 --district 滨江区
  python query_local.py parcel --city 三亚 --district 海棠区
  python query_local.py status --city 三亚
  python query_local.py subway --city 杭州 --district 滨江区

依赖：pip install duckdb pandas tabulate
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    import duckdb
    import pandas as pd
except ImportError:
    print("缺少依赖，运行：pip install duckdb pandas tabulate")
    sys.exit(1)

# ── 数据路径 ──────────────────────────────────────────────────────────────────

VAULT = Path(__file__).resolve().parent.parent / "Vault"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _tos_fallback_download(relative_path: str) -> str | None:
    """云端模式：本地文件不存在时从 TOS 下载到本地缓存。

    仅当 DDS_MODE=cloud 时激活。下载路径：~/.dds_cache/ 或 DDS_TOS_CACHE_DIR。
    返回本地缓存文件路径，下载失败返回 None。
    """
    if os.environ.get("DDS_MODE", "local") != "cloud":
        return None

    cache_dir = os.environ.get("DDS_TOS_CACHE_DIR", "")
    if not cache_dir:
        cache_dir = str(Path.home() / ".dds_cache")
    local_cache = Path(cache_dir) / relative_path.replace("/", os.sep)

    if local_cache.exists():
        return str(local_cache).replace("\\", "/")

    # 尝试 import TOS 客户端
    cloud_dir = _PROJECT_ROOT / "cloud" / "volcengine"
    if not cloud_dir.exists():
        return None
    sys.path.insert(0, str(cloud_dir))
    try:
        import tos_client
    except ImportError:
        return None

    cfg = tos_client.get_tos_config()
    if not all(cfg.values()):
        return None

    bucket = cfg["bucket"]
    object_key = f"vault/{relative_path}"

    ok = tos_client.download_one(bucket, object_key, local_cache)
    if ok:
        print(f"[info] TOS fallback: downloaded {object_key} -> {local_cache}")
        return str(local_cache).replace("\\", "/")
    return None

CURRENT_YEAR = "2026"

CITY_FILES = {
    "三亚": "新楼盘-三亚.csv",
    "杭州": "新楼盘-杭州.csv",
    "上海": "新楼盘-上海.csv",
    "青岛": "新楼盘-青岛.csv",
    "济南": "新楼盘-济南.csv",
}

_POOL_PREFIX = "新楼盘-"


def preferred_duckdb_source(csv_path: str | Path) -> str:
    """返回 DuckDB 数据源，避免较旧的 Parquet 静默遮蔽新 CSV。"""
    csv_file = Path(csv_path)
    parquet_file = csv_file.with_suffix(".parquet")

    use_parquet = parquet_file.exists() and (
        not csv_file.exists()
        or parquet_file.stat().st_mtime_ns >= csv_file.stat().st_mtime_ns
    )
    selected = parquet_file if use_parquet else csv_file
    sql_path = str(selected).replace("\\", "/").replace("'", "''")
    reader = "read_parquet" if use_parquet else "read_csv_auto"
    options = "" if use_parquet else ", header=true"
    return f"{reader}('{sql_path}'{options})"


def list_pool_cities() -> set[str]:
    """数据池中有楼盘文件的全部城市（Vault/2026新楼盘/新楼盘-*.csv）。
    供 app.py / report_parcel 动态放行任意有数据的城市，避免硬编码白名单。"""
    cities = set(CITY_FILES.keys())
    d = VAULT / "2026新楼盘"
    try:
        for p in d.glob(_POOL_PREFIX + "*.csv"):
            name = p.stem[len(_POOL_PREFIX):]
            # 排除二手房小区等非新盘文件
            if (
                name
                and not name.startswith("二手房")
                and not name.endswith("_imputed")
            ):
                cities.add(name)
    except Exception:
        pass
    return cities


def get_csv(city: str, year: str = None) -> str | None:
    """返回城市 CSV 路径（DuckDB 格式），模糊匹配，支持直名与年份分桶"""
    target_year = year or CURRENT_YEAR

    # T2: 当 target_year == CURRENT_YEAR(2026) 时，优先 2026新楼盘（全量 479 优于 51 子集）
    if target_year == CURRENT_YEAR:
        for key, fname in CITY_FILES.items():
            if city in key or key in city:
                p_new_loupan = VAULT / "2026新楼盘" / fname
                if p_new_loupan.exists():
                    return str(p_new_loupan).replace("\\", "/")

    # 1. 优先尝试新规范：Vault/{year}年/{city}.csv
    p_new = VAULT / f"{target_year}年" / f"{city}.csv"
    if p_new.exists():
        return str(p_new).replace("\\", "/")

    # 2. 兼容2026新楼盘老规范：Vault/2026新楼盘/新楼盘-{city}.csv
    if target_year == CURRENT_YEAR:
        p_old = VAULT / "2026新楼盘" / f"新楼盘-{city}.csv"
        if p_old.exists():
            return str(p_old).replace("\\", "/")

    # 3. 动态模糊匹配对应年份下的所有 CSV 文件
    year_dir = VAULT / f"{target_year}年"
    if year_dir.exists():
        for p in year_dir.glob("*.csv"):
            c_name = p.stem
            if city in c_name or c_name in city:
                return str(p).replace("\\", "/")

    # 4. TOS fallback：云端模式从 TOS 下载到本地缓存
    tos_rel = f"2026新楼盘/新楼盘-{city}.csv"
    tos_path = _tos_fallback_download(tos_rel)
    if tos_path:
        return tos_path

    return None



def load(city: str, year: str = None) -> "duckdb.DuckDBPyRelation | None":
    """加载城市 CSV 为 DuckDB relation，统一重命名关键列"""
    f = get_csv(city, year=year)
    if not f:
        print(f"[warn] 找不到 {city} 的数据文件，支持：{list(CITY_FILES)}")
        return None
    
    read_source = preferred_duckdb_source(f)

    try:
        rel = duckdb.query(f"""
            SELECT
                "楼盘名称"    AS project_name,
                "城市名称"    AS city,
                "区域名称"    AS district,
                "子区域名称"  AS sub_district,
                "地址"        AS address,
                "最新价格"    AS price_raw,
                "参考价格"    AS price_label,
                -- 单价（元/㎡）
                CASE WHEN "参考价格" LIKE '%元/㎡%'
                     THEN TRY_CAST("最新价格" AS DOUBLE)
                     ELSE NULL END AS unit_price_cny,
                -- 总价（万元/套）
                CASE WHEN "参考价格" LIKE '%万元/套%'
                     THEN TRY_CAST("最新价格" AS DOUBLE)
                     ELSE NULL END AS total_price_wan,
                "面积范围"    AS area_range,
                "全部户型"    AS room_types,
                "容积率"      AS floor_area_ratio,
                "绿化率"      AS green_ratio,
                "规划户数"    AS planned_units,
                "装修情况"    AS decoration,
                "销售状态"    AS sales_status,
                "开发商"      AS developer,
                TRY_CAST("百度地图纬度" AS DOUBLE) AS lat,
                TRY_CAST("百度地图经度" AS DOUBLE) AS lng,
                "规交信息"    AS subway_json,
                "物业类型"    AS property_type,
                "产权年限"    AS tenure,
                "开盘日期"    AS open_date,
                "交房时间"    AS delivery_date,
                "标签列表"    AS tags
            FROM {read_source}
        """)
        return rel
    except Exception as e:
        print(f"[warn] 加载失败: {e}")
        return None


def load_community(city: str) -> "duckdb.DuckDBPyRelation | None":
    """加载城市「二手房小区/参考层」(Vault/2026新楼盘/二手房小区-{city}.csv)。
    供 DDS agent 取「同板块二手价 / 小区评分 / 在售在租 / 配套口碑」证据。
    目前覆盖：济南（8024 小区）。无该城文件返回 None。"""
    base = VAULT / "2026新楼盘" / f"二手房小区-{city}.csv"
    if not base.exists():
        # TOS fallback：云端模式从 TOS 下载
        tos_rel = f"2026新楼盘/二手房小区-{city}.csv"
        tos_path = _tos_fallback_download(tos_rel)
        if not tos_path:
            return None
        base = Path(tos_path)
    src = preferred_duckdb_source(base)
    try:
        return duckdb.query(f"""
            SELECT
                "楼盘名称"        AS community_name,
                "城市名称"        AS city,
                "区域名称"        AS district,
                "子区域名称"      AS sub_district,
                "地址"            AS address,
                TRY_CAST("最新价格" AS DOUBLE)   AS resale_price_cny,
                TRY_CAST("综合评分" AS DOUBLE)   AS grade_score,
                "优点"            AS merits,
                "缺点"            AS demerits,
                "在售数量"        AS on_sale_num,
                "在租数量"        AS on_rent_num,
                TRY_CAST("容积率" AS DOUBLE)     AS floor_area_ratio,
                "绿化率"          AS green_ratio,
                "规划户数"        AS total_units,
                TRY_CAST("百度地图纬度" AS DOUBLE) AS lat,
                TRY_CAST("百度地图经度" AS DOUBLE) AS lng,
                "物业类型"        AS property_type,
                "物业类型集合"    AS property_types,
                "竣工时间"        AS completion_time,
                "物业管理费"      AS property_fee,
                "物业公司"        AS property_company,
                "统一供暖"        AS unified_heating,
                "用水类型"        AS water_type,
                "标签列表"        AS tags
            FROM {src}
        """)
    except Exception as e:
        print(f"[warn] 加载小区层失败: {e}")
        return None



def show(df: "pd.DataFrame", title: str = "") -> None:
    if title:
        print(f"\n{'─'*64}")
        print(f"  {title}")
        print(f"{'─'*64}")
    if df is None or df.empty:
        print("  暂无数据")
        return
    try:
        from tabulate import tabulate
        print(tabulate(df, headers="keys", tablefmt="rounded_outline",
                       showindex=False, floatfmt=".1f", numalign="right"))
    except ImportError:
        print(df.to_string(index=False))
    print(f"  共 {len(df)} 条")


# ── Query 1：片区均价 ─────────────────────────────────────────────────────────

def q_avg_price(city: str, district: str = None) -> None:
    """在售楼盘挂牌均价（元/㎡）"""
    rel = load(city)
    if rel is None: return
    d = f"AND district LIKE '%{district}%'" if district else ""
    df = duckdb.query(f"""
        SELECT
            district                              AS 区域,
            sub_district                          AS 子区域,
            COUNT(*)                              AS 楼盘数,
            ROUND(AVG(unit_price_cny), 0)         AS 均价_元㎡,
            ROUND(MIN(unit_price_cny), 0)         AS 最低_元㎡,
            ROUND(MAX(unit_price_cny), 0)         AS 最高_元㎡,
            COUNT(CASE WHEN sales_status='在售' THEN 1 END) AS 在售数
        FROM rel
        WHERE unit_price_cny > 0
          {d}
        GROUP BY district, sub_district
        ORDER BY 均价_元㎡ DESC NULLS LAST
        LIMIT 20
    """).df()
    show(df, f"{city}{district or ''} 在售楼盘挂牌均价")


# ── Query 2：竞品对比 ─────────────────────────────────────────────────────────

def q_competitors(city: str, district: str = None) -> None:
    """片区楼盘竞品横向对比"""
    rel = load(city)
    if rel is None: return
    d = f"AND district LIKE '%{district}%'" if district else ""
    df = duckdb.query(f"""
        SELECT
            project_name                          AS 楼盘,
            sub_district                          AS 子区域,
            sales_status                          AS 状态,
            COALESCE(
                CASE WHEN unit_price_cny  > 0
                     THEN CONCAT(CAST(ROUND(unit_price_cny,0) AS VARCHAR), '元/㎡')
                END,
                CASE WHEN total_price_wan > 0
                     THEN CONCAT(CAST(total_price_wan AS VARCHAR), '万/套')
                END,
                '—'
            )                                     AS 价格,
            area_range                            AS 面积段,
            room_types                            AS 主力户型,
            floor_area_ratio                      AS 容积率,
            green_ratio                           AS 绿化率,
            decoration                            AS 装修,
            developer                             AS 开发商
        FROM rel
        WHERE sales_status IN ('在售','尾盘')
          {d}
        ORDER BY unit_price_cny DESC NULLS LAST, total_price_wan DESC NULLS LAST
        LIMIT 30
    """).df()
    show(df, f"{city}{district or ''} 在售竞品对比")


# ── Query 3：销售状态分布 ─────────────────────────────────────────────────────

def q_status(city: str, district: str = None) -> None:
    """各区域销售状态分布（在售/尾盘/售罄）"""
    rel = load(city)
    if rel is None: return
    d = f"AND district LIKE '%{district}%'" if district else ""
    df = duckdb.query(f"""
        SELECT
            district                AS 区域,
            sales_status            AS 销售状态,
            COUNT(*)                AS 楼盘数,
            ROUND(COUNT(*) * 100.0
                / SUM(COUNT(*)) OVER (PARTITION BY district), 1) AS 占比_pct
        FROM rel
        WHERE sales_status IS NOT NULL AND sales_status != ''
          {d}
        GROUP BY district, sales_status
        ORDER BY district, 楼盘数 DESC
    """).df()
    show(df, f"{city}{district or ''} 销售状态分布")


# ── Query 4：地铁配套 ─────────────────────────────────────────────────────────

def q_subway(city: str, district: str = None, max_dist: int = 1000) -> None:
    """地铁 {max_dist}m 内楼盘（GIS 分析）"""
    rel = load(city)
    if rel is None: return
    d = f"AND district LIKE '%{district}%'" if district else ""

    raw = duckdb.query(f"""
        SELECT project_name, district, sub_district, sales_status,
               unit_price_cny, area_range, room_types, subway_json
        FROM rel
        WHERE subway_json IS NOT NULL AND subway_json NOT IN ('', '[]')
          {d}
    """).df()

    rows = []
    for _, r in raw.iterrows():
        try:
            stations = json.loads(r["subway_json"])
            if not stations: continue
            nearest = min(stations, key=lambda x: int(x.get("distance_raw") or 9999))
            dist = int(nearest.get("distance_raw") or 9999)
            if dist > max_dist: continue
            rows.append({
                "楼盘":     r["project_name"],
                "区域":     r["district"],
                "状态":     r["sales_status"],
                "均价_元㎡": r["unit_price_cny"],
                "面积段":   r["area_range"],
                "最近地铁站": nearest.get("station_name"),
                "线路":     nearest.get("subway_name"),
                "距离_m":   dist,
            })
        except Exception:
            continue
    df = pd.DataFrame(rows).sort_values("距离_m") if rows else pd.DataFrame()
    show(df, f"{city}{district or ''} 地铁{max_dist}m内楼盘")


# ── Query 5：户型分布 ─────────────────────────────────────────────────────────

def q_room_dist(city: str, district: str = None) -> None:
    """主力户型分布（市场供给结构）"""
    rel = load(city)
    if rel is None: return
    d = f"AND district LIKE '%{district}%'" if district else ""

    raw = duckdb.query(f"""
        SELECT room_types, district, unit_price_cny
        FROM rel
        WHERE room_types IS NOT NULL AND room_types != ''
          AND sales_status IN ('在售','尾盘')
          {d}
    """).df()

    counter: dict = {}
    for _, r in raw.iterrows():
        for rt in str(r["room_types"]).split(","):
            rt = rt.strip()
            if rt:
                counter[rt] = counter.get(rt, 0) + 1
    if not counter:
        show(pd.DataFrame(), f"{city} 户型分布")
        return
    df = pd.DataFrame(sorted(counter.items(), key=lambda x: -x[1]),
                      columns=["户型", "楼盘数"])
    df["占比%"] = (df["楼盘数"] / df["楼盘数"].sum() * 100).round(1)
    show(df, f"{city}{district or ''} 在售楼盘户型供给分布")


# ── Query 6：地块综合画像（MVP 核心）────────────────────────────────────────

def q_parcel_profile(city: str, district: str = None) -> None:
    """地块综合画像 — 输入城市/片区，输出竞品全景"""
    print(f"\n{'═'*64}")
    print(f"  DDS 地块画像 — {city} {district or ''}")
    print(f"{'═'*64}")
    q_avg_price(city, district)
    q_status(city, district)
    q_competitors(city, district)
    q_subway(city, district, max_dist=1000)
    q_room_dist(city, district)
    print(f"\n{'═'*64}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "avg-price":   q_avg_price,
    "competitors": q_competitors,
    "status":      q_status,
    "subway":      q_subway,
    "room-dist":   q_room_dist,
    "parcel":      q_parcel_profile,
}


def main():
    global CURRENT_YEAR
    parser = argparse.ArgumentParser(
        description="DDS 本地查询 — 安居客/房天下历史与新房楼盘数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python query_local.py parcel --city 三亚 --district 海棠区
  python query_local.py avg-price --city 杭州 --year 2023
  python query_local.py competitors --city 三亚 --district 海棠区 --year 2018
  python query_local.py subway --city 杭州 --district 滨江区 --year 2025
  python query_local.py status --city 三亚
        """
    )
    parser.add_argument("command", choices=COMMANDS.keys())
    parser.add_argument("--city",     default="三亚")
    parser.add_argument("--district", default=None)
    parser.add_argument("--max-dist", type=int, default=1000, dest="max_dist")
    parser.add_argument("--year",     default="2026", help="数据年份，例如 2025、2024 (默认2026)")
    args = parser.parse_args()

    CURRENT_YEAR = args.year

    import inspect
    fn  = COMMANDS[args.command]
    sig = inspect.signature(fn)
    kw  = {}
    for p in sig.parameters:
        if p == "city":     kw["city"]     = args.city
        if p == "district": kw["district"] = args.district
        if p == "max_dist": kw["max_dist"] = args.max_dist
    fn(**kw)


if __name__ == "__main__":
    main()

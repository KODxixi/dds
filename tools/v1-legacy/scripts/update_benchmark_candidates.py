# -*- coding: utf-8 -*-
"""Build a sourced premium new-project candidate layer from the city pool.

The existing Vault/全国标杆新楼盘/全国标杆新楼盘.* files are unverified seed rows.
This script leaves them untouched and writes a separate L2 candidate dataset
derived from the purchased city-level new-house pool.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CITY_POOL = VAULT / "2026新楼盘"
BENCHMARK_DIR = VAULT / "全国标杆新楼盘"
TODAY = date.today().isoformat()

TARGET_CITIES = [
    "北京",
    "上海",
    "天津",
    "深圳",
    "广州",
    "杭州",
    "成都",
    "重庆",
    "西安",
    "南京",
    "苏州",
    "武汉",
    "青岛",
    "济南",
    "厦门",
    "宁波",
    "合肥",
    "长沙",
    "郑州",
    "佛山",
    "东莞",
    "珠海",
    "无锡",
    "常州",
    "福州",
    "泉州",
    "南通",
    "烟台",
    "唐山",
    "温州",
    "徐州",
    "大连",
    "沈阳",
    "绍兴",
    "昆明",
    "石家庄",
    "潍坊",
    "扬州",
    "南昌",
    "盐城",
    "长春",
    "嘉兴",
    "金华",
    "台州",
    "临沂",
    "惠州",
    "襄阳",
    "太原",
    "贵阳",
    "南宁",
    "哈尔滨",
    "兰州",
    "海口",
    "乌鲁木齐",
    "洛阳",
    "淄博",
    "泰州",
    "芜湖",
    "保定",
    "廊坊",
    "中山",
    "湖州",
]
TEXT_COLS = [
    "楼盘名称",
    "城市名称",
    "区域名称",
    "子区域名称",
    "地址",
    "建筑类型",
    "建筑类型.1",
    "物业类型",
    "物业特色",
    "销售标题",
    "开发商",
    "开发商品牌",
    "所有标签列表",
    "标签列表",
    "周边配套",
    "设计风格",
    "示范区配置",
    "公区会所配置",
    "全局创新点",
    "所在地段未来宏观定位",
]

QUALITY_KEYWORDS = [
    "豪宅",
    "高端",
    "改善",
    "低密",
    "洋房",
    "叠墅",
    "别墅",
    "大平层",
    "滨江",
    "江景",
    "湖景",
    "公园",
    "TOD",
    "轨交",
    "地铁",
    "会所",
    "四代",
    "科技住宅",
    "恒温",
    "恒湿",
    "幕墙",
    "绿城",
    "滨江集团",
    "华润",
    "中海",
    "招商",
    "保利",
    "万科",
    "龙湖",
    "建发",
    "仁恒",
    "金茂",
    "越秀",
]

BRAND_KEYWORDS = [
    "绿城",
    "滨江",
    "华润",
    "中海",
    "招商",
    "保利",
    "万科",
    "龙湖",
    "建发",
    "仁恒",
    "金茂",
    "越秀",
    "融创",
]

EXCLUDE_RE = re.compile(r"商铺|写字楼|办公|车位|产业园|厂房|商办")
RESIDENTIAL_RE = re.compile(r"住宅|别墅|洋房|叠拼|联排|大平层|改善|豪宅")


def _read_city(city: str) -> pd.DataFrame | None:
    parquet_path = CITY_POOL / f"新楼盘-{city}.parquet"
    csv_path = CITY_POOL / f"新楼盘-{city}.csv"
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return None


def _first_number(value: object) -> float | None:
    text = "" if pd.isna(value) else str(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0))


def _numeric(series: pd.Series) -> pd.Series:
    return series.map(_first_number)


def _text_frame(df: pd.DataFrame) -> pd.Series:
    usable = [col for col in TEXT_COLS if col in df.columns]
    if not usable:
        return pd.Series("", index=df.index)
    return df[usable].fillna("").astype(str).agg(" ".join, axis=1)


def _keyword_hits(text: str) -> list[str]:
    return [keyword for keyword in QUALITY_KEYWORDS if keyword.lower() in text.lower()]


def _grade(score: float) -> str:
    if score >= 82:
        return "A"
    if score >= 68:
        return "B"
    return "C"


def _selection_basis(row: pd.Series) -> str:
    basis = []
    price_pct = row.get("__price_pct")
    if pd.notna(price_pct):
        if price_pct >= 0.85:
            basis.append("城市高价分位")
        elif price_pct >= 0.65:
            basis.append("城市价格中上分位")
    keywords = str(row.get("__keywords", "")).strip()
    if keywords:
        basis.append(f"关键词:{keywords}")
    if row.get("__brand_hits", 0) > 0:
        basis.append("品牌开发商")
    far = row.get("__far_value")
    if pd.notna(far) and far <= 2.0:
        basis.append("低容积率")
    green = row.get("__green_value")
    if pd.notna(green) and green >= 35:
        basis.append("高绿化率")
    if bool(row.get("__has_rail", False)):
        basis.append("轨交/TOD配套")
    if bool(row.get("__has_large_product", False)):
        basis.append("改善型产品")
    return "；".join(basis[:5]) if basis else "价格/基础信息综合排序"

def _score_city(city: str, per_city: int) -> pd.DataFrame:
    df = _read_city(city)
    if df is None or df.empty or "最新价格" not in df.columns:
        return pd.DataFrame()

    source_total = len(df)
    work = df.copy()
    work["__price"] = _numeric(work["最新价格"])
    priced_count = int((work["__price"].notna() & (work["__price"] > 0)).sum())
    work = work[work["__price"].notna() & (work["__price"] > 0)].copy()
    if work.empty:
        return pd.DataFrame()

    text = _text_frame(work)
    residential_mask = text.str.contains(RESIDENTIAL_RE, na=False)
    exclude_mask = text.str.contains(EXCLUDE_RE, na=False) & ~residential_mask
    work = work[~exclude_mask].copy()
    text = text.loc[work.index]
    if work.empty:
        return pd.DataFrame()

    eligible_count = len(work)
    city_price_median = float(work["__price"].median()) if eligible_count else None
    work["__price_pct"] = work["__price"].rank(pct=True)
    hit_lists = text.map(_keyword_hits)
    work["__keyword_hits"] = hit_lists.map(len)
    work["__keywords"] = hit_lists.map(lambda items: "、".join(items))

    brand_hits = text.map(lambda value: sum(1 for brand in BRAND_KEYWORDS if brand.lower() in value.lower()))
    floor_area_ratio = _numeric(work["容积率"]) if "容积率" in work.columns else pd.Series(index=work.index, dtype=float)
    green_rate = _numeric(work["绿化率"]) if "绿化率" in work.columns else pd.Series(index=work.index, dtype=float)

    far_score = pd.Series(0.0, index=work.index)
    far_score = far_score.mask(floor_area_ratio <= 2.5, 4)
    far_score = far_score.mask(floor_area_ratio <= 2.0, 7)
    far_score = far_score.mask(floor_area_ratio <= 1.5, 10)

    green_score = pd.Series(0.0, index=work.index)
    green_score = green_score.mask(green_rate >= 30, 3)
    green_score = green_score.mask(green_rate >= 35, 5)

    rail_hits = text.str.contains(r"TOD|轨交|地铁", case=False, regex=True, na=False)
    rail_score = rail_hits.astype(float) * 5
    large_product_hits = text.str.contains(r"大平层|大户型|叠墅|别墅|洋房", regex=True, na=False)
    large_product_score = large_product_hits.astype(float) * 5
    work["__brand_hits"] = brand_hits
    work["__far_value"] = floor_area_ratio
    work["__green_value"] = green_rate
    work["__has_rail"] = rail_hits
    work["__has_large_product"] = large_product_hits

    work["候选评分"] = (
        work["__price_pct"] * 55
        + work["__keyword_hits"].clip(upper=6) * 4
        + brand_hits.clip(upper=2) * 5
        + far_score
        + green_score
        + rail_score
        + large_product_score
    ).round(2)
    work["候选价格分位"] = work["__price_pct"].round(4)
    work["候选城市样本量"] = source_total
    work["候选城市有效价格样本量"] = priced_count
    work["候选城市可筛选住宅样本量"] = eligible_count
    work["候选价格指数_城市中位数=1"] = (work["__price"] / city_price_median).round(3) if city_price_median else None
    work["候选入选依据"] = work.apply(_selection_basis, axis=1)
    work["候选核验状态"] = "L2自动筛选，未人工逐盘核验"
    work["候选等级"] = work["候选评分"].map(_grade)
    work["候选命中关键词"] = work["__keywords"]
    work["候选来源文件"] = f"Vault/2026新楼盘/新楼盘-{city}.parquet"
    work["候选来源层级"] = "L2: 用户购买新盘主池派生"
    work["候选生成日期"] = TODAY
    work["候选规则说明"] = "城市内价格分位 + 高端/改善/低密/轨交/品牌关键词 + 容积率/绿化率加权；候选排序非官方评级"

    selected = work[(work["候选价格分位"] >= 0.65) | (work["__keyword_hits"] >= 2)].copy()
    selected = selected.sort_values(["候选评分", "__price"], ascending=[False, False])
    selected = selected.drop_duplicates(subset=[c for c in ["城市名称", "楼盘名称", "地址"] if c in selected.columns])
    return selected.head(per_city)


def _write_benchmark_manifest(candidates: pd.DataFrame, city_counts: pd.Series) -> None:
    lines = [
        "# 全国标杆新楼盘 Manifest",
        "",
        f"- **最近更新**: {TODAY}",
        "- **原始种子文件**: `全国标杆新楼盘.csv(.parquet)`，10 行，模型生成占位，未核验，不作为真值。",
        "- **新增候选文件**: `优质新楼盘候选-重点城市.csv(.parquet)`，从 `Vault/2026新楼盘/新楼盘-{城市}` 购买主池派生。",
        "- **信任等级**: L2（购买数据派生），不是官方排名；每行保留来源、规则、样本量、价格指数、入选依据与核验状态。",
        "- **筛选口径**: 城市内价格分位 + 高端/改善/低密/轨交/品牌关键词 + 容积率/绿化率加权；剔除明显商办/车位/厂房类项目；每城保留 20 条候选。",
        f"- **候选总量**: {len(candidates)} 条；覆盖 {city_counts.shape[0]} 个重点城市。",
        "",
        "## 城市分布",
        "",
        "| 城市 | 候选项目数 |",
        "| --- | ---: |",
    ]
    for city, count in city_counts.items():
        lines.append(f"| {city} | {int(count)} |")
    lines.append("")
    lines.append("## 来源标注")
    lines.append("")
    lines.append("- 目录级来源已登记到 `Vault/_sources.csv(.parquet)`。")
    lines.append("- 行级来源字段: `候选来源文件` / `候选来源层级` / `候选生成日期` / `候选规则说明`。\n- 行级判断字段: `候选城市样本量` / `候选城市有效价格样本量` / `候选城市可筛选住宅样本量` / `候选价格指数_城市中位数=1` / `候选入选依据` / `候选核验状态`。")
    (BENCHMARK_DIR / "_manifest.md").write_text("\n".join(lines), encoding="utf-8")


def _write_city_index(candidates: pd.DataFrame) -> None:
    rows = []
    for city, group in candidates.groupby("城市名称", dropna=False):
        rows.append(
            {
                "城市": city,
                "候选项目数": len(group),
                "A级候选数": int((group["候选等级"] == "A").sum()),
                "价格中位数_元㎡": round(float(pd.to_numeric(group["最新价格"], errors="coerce").median()), 0),
                "最高候选评分": round(float(group["候选评分"].max()), 2),
                "有parquet": "是",
            }
        )
    pd.DataFrame(rows).sort_values(["候选项目数", "最高候选评分"], ascending=[False, False]).to_csv(
        BENCHMARK_DIR / "_cities_index.csv",
        index=False,
        encoding="utf-8-sig",
    )


def _upsert_sources(row_count: int) -> None:
    path = VAULT / "_sources.csv"
    cols = ["数据层", "文件", "来源标题", "发布机构或来源", "来源URL", "发布日期", "信任等级", "记录数", "入库日期", "备注"]
    if path.exists():
        sources = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    else:
        sources = pd.DataFrame(columns=cols)
    sources = sources[[col for col in cols if col in sources.columns]]
    for col in cols:
        if col not in sources.columns:
            sources[col] = ""
    file_key = "全国标杆新楼盘/优质新楼盘候选-重点城市.csv"
    sources = sources[sources["文件"] != file_key].copy()
    sources = pd.concat(
        [
            sources,
            pd.DataFrame(
                [
                    {
                        "数据层": "全国标杆新楼盘",
                        "文件": file_key,
                        "来源标题": "购买新盘主池派生优质新楼盘候选",
                        "发布机构或来源": "用户购买数据 / DDS规则派生",
                        "来源URL": "",
                        "发布日期": "",
                        "信任等级": "L2",
                        "记录数": str(row_count),
                        "入库日期": TODAY,
                        "备注": "基于 Vault/2026新楼盘/新楼盘-{城市}.parquet；行级字段保留来源文件、生成日期、规则说明、样本量、价格指数、入选依据和核验状态。",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    sources.to_csv(path, index=False, encoding="utf-8-sig")
    sources.to_parquet(VAULT / "_sources.parquet", index=False)


def _upsert_root_manifest(candidates: pd.DataFrame) -> None:
    path = VAULT / "_manifest.csv"
    manifest = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    target_path = "全国标杆新楼盘\\优质新楼盘候选-重点城市.csv"
    manifest = manifest[manifest["path"] != target_path].copy()

    price = pd.to_numeric(candidates["最新价格"], errors="coerce") if "最新价格" in candidates else pd.Series(dtype=float)
    lat = pd.to_numeric(candidates["百度地图纬度"], errors="coerce") if "百度地图纬度" in candidates else pd.Series(dtype=float)
    lon = pd.to_numeric(candidates["百度地图经度"], errors="coerce") if "百度地图经度" in candidates else pd.Series(dtype=float)
    geo_rate = float((lat.notna() & lon.notna()).mean()) if len(candidates) else 0.0

    row = {
        "year": "2026",
        "city": "全国重点城市",
        "dtype": "优质新楼盘候选",
        "path": target_path,
        "rows": str(len(candidates)),
        "usable_price_rows": str(int(price.notna().sum())),
        "usable_price_rate": f"{float(price.notna().mean()):.4f}" if len(candidates) else "0",
        "geo_valid_rate": f"{geo_rate:.4f}",
        "source": "purchased_newhouse_pool_derived",
        "trust": "L2",
        "is_synthetic": "False",
    }
    manifest = pd.concat([manifest, pd.DataFrame([row])], ignore_index=True)
    manifest.to_csv(path, index=False, encoding="utf-8-sig")
    manifest.to_parquet(VAULT / "_manifest.parquet", index=False)
    (VAULT / "_manifest.json").write_text(
        json.dumps(manifest.to_dict(orient="records"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build(per_city: int = 20) -> pd.DataFrame:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    frames = [_score_city(city, per_city) for city in TARGET_CITIES]
    candidates = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    if candidates.empty:
        raise RuntimeError("未生成任何优质新楼盘候选")

    drop_cols = [col for col in candidates.columns if col.startswith("__")]
    candidates = candidates.drop(columns=drop_cols)

    csv_path = BENCHMARK_DIR / "优质新楼盘候选-重点城市.csv"
    parquet_path = BENCHMARK_DIR / "优质新楼盘候选-重点城市.parquet"
    candidates.to_csv(csv_path, index=False, encoding="utf-8-sig")
    candidates.to_parquet(parquet_path, index=False)

    city_counts = candidates["城市名称"].value_counts().sort_index()
    _write_city_index(candidates)
    _write_benchmark_manifest(candidates, city_counts)
    _upsert_sources(len(candidates))
    _upsert_root_manifest(candidates)
    return candidates


def main() -> None:
    candidates = build()
    print(f"wrote {len(candidates)} candidate rows to {BENCHMARK_DIR}")
    print(candidates.groupby("城市名称").size().sort_index().to_string())


if __name__ == "__main__":
    main()

"""
房天下（Fang.com）新房数据抓取
用于补充 DDS 竞品分析的联网数据源 — 提供具体户型面积、单价区间
"""
import json
import re
import time
import random
from datetime import datetime
from pathlib import Path
from urllib import request
from urllib.parse import urlencode, quote

ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = ROOT / "data_out" / "fang"

# Fang.com 城市代码映射
CITY_CODES = {
    "三亚": "sanya",
    "杭州": "hangzhou",
    "上海": "shanghai",
    "青岛": "qingdao",
}


def fang_search(city: str, keyword: str = "") -> list[dict]:
    """
    搜索房天下新房项目。
    返回: [{name, address, price, area_range, room_types, rooms_detail, url}]
    """
    city_code = CITY_CODES.get(city, city)
    results = []

    try:
        # 房天下新房搜索页
        search_url = f"https://{city_code}.newhouse.fang.com/house/s/"
        params = {}
        if keyword:
            params["keyword"] = keyword

        req = request.Request(
            search_url + (f"?{urlencode(params)}" if params else ""),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        with request.urlopen(req, timeout=15) as r:
            html = r.read().decode("gbk", errors="replace")

        # 解析楼盘列表
        # 房天下列表页结构: <div class="nlc_details"> 包含每个楼盘卡片
        cards = re.findall(
            r'<div[^>]*class="[^"]*nlc_details[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*nlc_details|$)',
            html, re.DOTALL
        )

        for card in cards[:20]:
            item = {}

            # 楼盘名称
            name_m = re.search(r'<a[^>]*class="[^"]*nlcd_name[^"]*"[^>]*>(.*?)</a>', card, re.DOTALL)
            if name_m:
                item["project_name"] = re.sub(r'<[^>]+>', '', name_m.group(1)).strip()

            # 链接
            link_m = re.search(r'<a[^>]*href="([^"]*)"[^>]*class="[^"]*nlcd_name', card)
            if link_m:
                href = link_m.group(1)
                if href.startswith("//"):
                    href = "https:" + href
                item["url"] = href

            # 价格
            price_m = re.search(r'(?:均价|价格|售价)[：:]\s*(\d+(?:\.\d+)?)\s*(?:元/㎡|元/平米|万)', card)
            if not price_m:
                price_m = re.search(r'<span[^>]*class="[^"]*price[^"]*"[^>]*>(\d+(?:\.\d+)?)', card)
            if price_m:
                try:
                    item["unit_price_cny"] = float(price_m.group(1))
                except ValueError:
                    pass

            # 地址
            addr_m = re.search(r'<span[^>]*class="[^"]*address[^"]*"[^>]*>(.*?)</span>', card, re.DOTALL)
            if addr_m:
                item["address"] = re.sub(r'<[^>]+>', '', addr_m.group(1)).strip()

            # 户型信息
            rooms_m = re.findall(r'(?:户型|房型)[：:]\s*([^<]+)', card)
            if rooms_m:
                item["room_types"] = rooms_m[0].strip()

            if item.get("project_name"):
                item["source"] = "fang"
                results.append(item)

    except Exception as e:
        print(f"[fang] Search failed for {city}: {e}")

    return results


def fang_project_detail(url: str) -> dict:
    """
    抓取房天下项目详情页，提取具体户型面积。
    返回: {rooms_detail: [{name, area, price}]}
    """
    detail = {"rooms_detail": []}

    try:
        req = request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        with request.urlopen(req, timeout=15) as r:
            html = r.read().decode("gbk", errors="replace")

        # 户型详情 — Fang.com 户型列表通常在 house type list 区域
        # 结构: <div class="housetype"> 或 <li class="huxing">
        room_blocks = re.findall(
            r'<li[^>]*class="[^"]*huxing[^"]*"[^>]*>(.*?)</li>',
            html, re.DOTALL
        )
        if not room_blocks:
            room_blocks = re.findall(
                r'<div[^>]*class="[^"]*housetype[^"]*"[^>]*>(.*?)</div>',
                html, re.DOTALL
            )

        for block in room_blocks[:10]:
            room = {}

            # 户型名称
            name_m = re.search(r'(?:<a[^>]*>|<span[^>]*>)(\d+室[^<]*(?:户型|厅|卫)[^<]*)', block)
            if name_m:
                room["name"] = name_m.group(1).strip()

            # 面积
            area_m = re.search(r'(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*[㎡平米]', block)
            if not area_m:
                area_m = re.search(r'(\d+(?:\.\d+)?)\s*[㎡平米]', block)
            if area_m:
                try:
                    if area_m.lastindex and area_m.lastindex >= 2:
                        room["area_min"] = float(area_m.group(1))
                        room["area_max"] = float(area_m.group(2))
                    else:
                        room["area"] = float(area_m.group(1))
                except (ValueError, IndexError):
                    pass

            if room:
                detail["rooms_detail"].append(room)

    except Exception as e:
        print(f"[fang] Detail failed for {url}: {e}")

    return detail


def enrich_with_fang(projects: list[dict], city: str) -> list[dict]:
    """
    用房天下数据增强竞品列表 — 补充具体户型面积。
    """
    if not projects:
        return projects

    # 搜索房天下同城项目
    fang_results = fang_search(city, "")

    # 按名称匹配，补充户型详情
    for proj in projects:
        name = proj.get("project_name", "")
        if not name:
            continue

        # 模糊匹配房天下项目
        for fang_proj in fang_results:
            fang_name = fang_proj.get("project_name", "")
            # 取核心名称匹配（去掉后缀如"三期"、"东区"等）
            core_name = re.sub(r'[（(].*?[)）]|[一二三四五六七八九十]+期|[东西南北]区$', '', name).strip()
            fang_core = re.sub(r'[（(].*?[)）]|[一二三四五六七八九十]+期|[东西南北]区$', '', fang_name).strip()

            if core_name and fang_core and (core_name in fang_core or fang_core in core_name):
                # 获取详情页户型
                if fang_proj.get("url"):
                    detail = fang_project_detail(fang_proj["url"])
                    if detail.get("rooms_detail"):
                        proj["rooms_detail"] = detail["rooms_detail"]
                        proj["fang_url"] = fang_proj["url"]
                        proj["fang_price"] = fang_proj.get("unit_price_cny")

                # 补充缺失的价格
                if not proj.get("unit_price_cny") and fang_proj.get("unit_price_cny"):
                    proj["unit_price_cny"] = fang_proj["unit_price_cny"]
                    proj["price_source"] = "fang补充"

                break
        time.sleep(random.uniform(0.3, 0.8))

    return projects


def parse_area_to_rooms(area_range: str, room_types: str) -> list[dict]:
    """
    从面积段和户型字符串推断每类户型的大致面积。
    例: area_range="62-118㎡", room_types="1室户型,2室户型,3室户型"
    返回: [{name: "1室户型", area: "62-80㎡"}, {name: "2室户型", area: "80-100㎡"}, {name: "3室户型", area: "100-118㎡"}]
    """
    if not area_range or not room_types:
        return []

    # 解析面积范围
    area_match = re.search(r'(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)', area_range)
    if not area_match:
        return []

    area_min = float(area_match.group(1))
    area_max = float(area_match.group(2))
    rooms = [r.strip() for r in room_types.replace('户型', '').split(',') if r.strip()]

    if not rooms:
        return []

    # 按户型数量均分面积区间
    n = len(rooms)
    gap = (area_max - area_min) / n

    result = []
    for i, room in enumerate(rooms):
        room_min = round(area_min + gap * i)
        room_max = round(area_min + gap * (i + 1))
        result.append({
            "name": room + "户型",
            "area": f"{room_min}-{room_max}㎡",
            "area_min": room_min,
            "area_max": room_max,
        })
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="房天下新房数据抓取")
    parser.add_argument("--city", default="三亚", help="城市名称")
    parser.add_argument("--keyword", default="", help="搜索关键词")
    parser.add_argument("--detail", help="项目详情 URL")
    parser.add_argument("--test-parse", action="store_true", help="测试面积解析")
    args = parser.parse_args()

    if args.test_parse:
        # 测试
        test_cases = [
            ("62-118㎡", "1室户型,2室户型,3室户型"),
            ("102-123㎡", "3室户型,4室户型,别墅户型"),
            ("85-88㎡", "2室户型"),
            ("46-196㎡", "别墅户型"),
            ("100-140㎡", "三室两厅,四室两厅"),
        ]
        for ar, rt in test_cases:
            rooms = parse_area_to_rooms(ar, rt)
            print(f"{ar} + {rt} → {json.dumps(rooms, ensure_ascii=False)}")
        return

    if args.detail:
        detail = fang_project_detail(args.detail)
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return

    results = fang_search(args.city, args.keyword)
    print(f"找到 {len(results)} 个楼盘")
    for r in results[:10]:
        print(f"  {r.get('project_name')} | {r.get('unit_price_cny', '—')}元/㎡ | {r.get('address', '—')}")


if __name__ == "__main__":
    main()

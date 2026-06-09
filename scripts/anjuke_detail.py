"""
Anjuke 项目详情抓取 — 获取具体户型面积（非面积段）

Anjuke 搜索 → 匹配项目 → 抓取户型详情页 → 提取 "3室2厅 120㎡" 格式数据
"""
import re
import time
import random
from urllib import request
from urllib.parse import urlencode, quote


def anjuke_search_project(city: str, project_name: str) -> dict | None:
    """
    在 Anjuke 搜索项目，返回详情页 URL 和摘要信息。
    """
    city_codes = {"三亚": "sanya", "杭州": "hangzhou", "上海": "shanghai", "青岛": "qingdao"}
    city_code = city_codes.get(city, city)

    try:
        search_url = f"https://{city_code}.fang.anjuke.com/loupan/search/"
        params = {"keyword": project_name}
        full_url = f"{search_url}?{urlencode(params)}"

        req = request.Request(full_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        with request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")

        # 解析搜索结果列表
        # Anjuke 项目卡片通常在 <div class="list-item"> 或 <a class="lp-name"> 中
        items = re.findall(
            r'<a[^>]*class="[^"]*lp-name[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )

        for href, name_html in items[:5]:
            name = re.sub(r'<[^>]+>', '', name_html).strip()
            # 匹配项目名（忽略后缀差异）
            core = re.sub(r'[（(].*?[)）]|[一二三四五六七八九十]+期$|[东南西北]区$', '', project_name).strip()
            core_found = re.sub(r'[（(].*?[)）]|[一二三四五六七八九十]+期$|[东南西北]区$', '', name).strip()

            if core and core_found and (core in core_found or core_found in core):
                url = href if href.startswith("http") else f"https:{href}"
                return {"name": name, "url": url, "matched_name": project_name}

    except Exception as e:
        print(f"[anjuke] search failed for {project_name}: {e}")

    return None


def anjuke_room_details(detail_url: str) -> list[dict]:
    """
    从 Anjuke 项目详情页抓取具体户型面积。
    返回: [{name: "3室2厅", area: 120, area_str: "120㎡"}, ...]
    """
    rooms = []

    try:
        req = request.Request(detail_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        with request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")

        # Anjuke 户型区域常见结构：
        # <span class="huxing-name">3室2厅</span> ... <span class="huxing-area">120㎡</span>
        # 或 <div class="room-info"><span>3室2厅</span><b>120㎡</b></div>
        # 或表格形式 <td>3室2厅</td><td>120㎡</td>

        # Pattern 1: huxing/house-type blocks with name + area
        pat1 = re.findall(
            r'(?:户型名称|户型)[：:]*\s*(\S+?)\s*.*?(?:面积|建筑面积)[：:]*\s*(\d+(?:\.\d+)?)\s*[㎡平米]',
            html
        )
        for name, area_str in pat1:
            rooms.append({"name": name.strip(), "area": float(area_str), "area_str": f"{area_str}㎡"})

        # Pattern 2: room cards with class names containing huxing/house
        if not rooms:
            blocks = re.findall(
                r'<(?:div|li|span)[^>]*class="[^"]*(?:huxing|house-type|room-type|housetype)[^"]*"[^>]*>(.*?)</(?:div|li|span)>',
                html, re.DOTALL
            )
            for block in blocks[:20]:
                # 提取户型名
                name_m = re.search(r'(\d+室[^<]*(?:厅|卫)?[^<]{0,10})', block)
                # 提取面积
                area_m = re.search(r'(\d+(?:\.\d+)?)\s*[㎡平米]', block)
                if name_m and area_m:
                    rooms.append({
                        "name": name_m.group(1).strip(),
                        "area": float(area_m.group(1)),
                        "area_str": f"{area_m.group(1)}㎡",
                    })

        # Pattern 3: table rows with room type + area
        if not rooms:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                for i, cell in enumerate(cells):
                    room_m = re.search(r'(\d+室[^<]*厅[^<]*)', cell)
                    if room_m and i + 1 < len(cells):
                        area_m = re.search(r'(\d+(?:\.\d+)?)\s*[㎡平米]', cells[i + 1])
                        if area_m:
                            rooms.append({
                                "name": room_m.group(1).strip(),
                                "area": float(area_m.group(1)),
                                "area_str": f"{area_m.group(1)}㎡",
                            })

    except Exception as e:
        print(f"[anjuke] detail failed for {detail_url}: {e}")

    return rooms


def enrich_with_anjuke(city: str, project_name: str) -> list[dict]:
    """
    为单个项目补充 Anjuke 详情页的具体户型面积。
    返回 rooms_detail 列表，失败返回空。
    """
    result = anjuke_search_project(city, project_name)
    if not result:
        return []

    rooms = anjuke_room_details(result["url"])
    if rooms:
        # 去重（按面积）
        seen = set()
        unique = []
        for r in rooms:
            key = f"{r['name']}_{r['area']}"
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    return []


# ── CLI test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    city = sys.argv[1] if len(sys.argv) > 1 else "三亚"
    name = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "海棠月色"

    print(f"Searching Anjuke: {city} - {name}")
    result = anjuke_search_project(city, name)
    if result:
        print(f"  Found: {result['name']}")
        print(f"  URL: {result['url']}")

        rooms = anjuke_room_details(result["url"])
        if rooms:
            print(f"  Rooms ({len(rooms)}):")
            for r in rooms:
                print(f"    {r['name']} — {r['area_str']}")
        else:
            print("  No room details found on page")
    else:
        print("  Not found on Anjuke")

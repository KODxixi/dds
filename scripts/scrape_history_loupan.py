# -*- coding: utf-8 -*-
"""
DDS 历史楼盘数据抓取归档模块 — 专注于三亚、上海、杭州、青岛
利用房天下 (Fang.com) 完整新楼盘与已售罄库，映射 2010 - 2025 年历史楼盘，对齐 56 个核心字段并按年份归档。
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
import subprocess
from pathlib import Path
from urllib.parse import urlencode

# ── 基础配置 ──────────────────────────────────────────────────────────────────

VAULT = Path(__file__).resolve().parent.parent / "Vault"
AMAP_KEY = os.environ.get("AMAP_KEY") or "YOUR_AMAP_KEY_HERE"  # 从 .env 设置 AMAP_KEY

CITY_CODES = {
    "三亚": "sanya",
    "上海": "sh",
    "杭州": "hz",
    "青岛": "qd",
}

CITY_IDS = {
    "三亚": "51",
    "上海": "12",
    "杭州": "15",
    "青岛": "21",
}

CSV_HEADERS = [
    "楼盘ID", "楼盘名称", "城市名称", "城市ID", "区域名称", "区域ID", 
    "子区域名称", "子区域ID", "地址", "环线位置", "最新价格", "参考价格", 
    "房贷计算信息", "面积范围", "占地面积", "建筑面积", "房间面积信息", 
    "户型文本描述", "全部户型", "开盘日期", "开盘日期备注", "开盘时间", 
    "交房时间", "发证时间", "建筑类型", "建筑类型", "产权年限", "容积率", 
    "绿化率", "规划户数", "装修情况", "工程进度", "物业类型", "物业公司", 
    "物业管理费", "物业特色", "车位数", "车位比", "销售状态", "销售标题", 
    "租售标题", "预售证号", "绑定楼栋", "开发商", "开发商品牌", "投资商", 
    "供电", "供水", "百度地图纬度", "百度地图经度", "400电话", 
    "所有标签列表", "标签列表", "默认图片", "规交信息", "售楼处地址"
]

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def get_html(url: str, retries: int = 3) -> str:
    """请求网页源码，带 requests 双向稳定防封 + 系统级 curl 强力备用"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    ]
    headers = {
        "User-Agent": random.choice(uas),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.fang.com/",
    }
    
    for attempt in range(1, retries + 1):
        # ── 模式 A：使用带长超时和忽略证书的 requests ──
        try:
            resp = requests.get(url, headers=headers, timeout=20, verify=False)
            if "charset=gbk" in resp.text.lower() or "charset=gb2312" in resp.text.lower():
                resp.encoding = "gbk"
            else:
                resp.encoding = "utf-8"
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
        except Exception as e:
            pass
            
        # ── 模式 B：使用系统级 curl.exe 备用 ──
        try:
            cmd = [
                "curl", "-s", "-k", "-L",
                "--max-time", "20",
                "-H", f"User-Agent: {headers['User-Agent']}",
                "-H", "Accept-Language: zh-CN,zh;q=0.9",
                "-H", "Referer: https://www.fang.com/",
                url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", errors="replace")
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except Exception as e:
            pass
            
        time.sleep(attempt * 2 + random.random())
        
    raise RuntimeError(f"请求 {url} 失败，已尝试所有备用网络引擎。")


def geocode_address(address: str, city: str) -> tuple[str, str]:
    """使用本地高德 API 获取经纬度"""
    try:
        import requests
        params = {
            "address": address,
            "city": city,
            "key": AMAP_KEY
        }
        url = "https://restapi.amap.com/v3/geocode/geo"
        resp = requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "1" and data.get("geocodes"):
                loc = data["geocodes"][0]["location"]
                lng, lat = loc.split(",")
                return lat, lng  # 返回纬度, 经度
    except Exception as e:
        # 备用模式：带超时的系统级 curl 强力捕获
        try:
            url_full = f"https://restapi.amap.com/v3/geocode/geo?{urlencode(params)}"
            cmd = ["curl", "-s", "--max-time", "10", url_full]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode == 0 and result.stdout.strip():
                resp = json.loads(result.stdout)
                if resp.get("status") == "1" and resp.get("geocodes"):
                    loc = resp["geocodes"][0]["location"]
                    lng, lat = loc.split(",")
                    return lat, lng
        except Exception:
            pass
    return "", ""


# ── 解析器 ───────────────────────────────────────────────────────────────────

def parse_fang_list(html: str, city_code: str) -> list[dict]:
    """使用黄金链接提取法则提取这一页里所有的楼盘 ID 和链接"""
    pattern = rf'https?://{city_code}\.newhouse\.fang\.com/loupan/(\d+)\.htm'
    pids = list(set(re.findall(pattern, html)))
    items = []
    for pid in pids:
        items.append({
            "id": pid,
            "detail_url": f"https://{city_code}.newhouse.fang.com/loupan/{pid}.htm"
        })
    return items


def fetch_fang_details(detail_url: str) -> dict:
    """请求房天下详细参数页面，并解析提取全部字段"""
    details = {
        "far": "", "green": "", "units": "", "property_company": "", 
        "property_fee": "", "developer": "", "build_type": "", 
        "open_date": "", "delivery_date": "", "year": 0, "name": "",
        "status": "售罄", "price": "", "price_label": "价格待定",
        "address": "", "district": "", "bizcircle": ""
    }
    
    # 房天下的详细参数页面是 /housedetail.htm
    url = detail_url.replace(".htm", "/housedetail.htm")
    
    try:
        html = get_html(url)
        
        # 1. 优先使用原生的带标签 html 提取名称和含有特殊标签结构的字段
        title_m = re.search(r'<title>([^<]+?)(?:基本信息|售楼处电话|物业费|开发商|\-三亚房天下|\-房天下|\s*详情)</title>', html)
        if title_m:
            details["name"] = title_m.group(1).strip()
        else:
            h1_m = re.search(r'<h1>([^<]+)</h1>', html)
            details["name"] = h1_m.group(1).strip() if h1_m else ""
            
        dist_m = re.search(r'<a[^>]*href="[^"]*/house/s/([a-z]+)/"[^>]*>([^<]+区)楼盘</a>', html)
        details["district"] = dist_m.group(2).strip() if dist_m else ""

        # 2. 注入换行符并剥离所有 HTML 标签以获取纯文本，防止文本粘连和字间距标签（例如 <i>）干扰
        text = html
        for tag in ['</div>', '</li>', '</p>', '</td>', '</tr>', '<br>', '<br/>']:
            text = text.replace(tag, '\n')
        text = re.sub(r'<[^>]+>', '', text)
        
        # 3. 在干净的纯文本中精准提取其他属性
        # 销售状态
        status_m = re.search(r'销售状态[：:]\s*([^\s\n|]+)', text)
        details["status"] = status_m.group(1).strip() if status_m else "售罄"
        
        # 价格
        price_m = re.search(r'单价[：:]\s*(\d+)', text)
        if not price_m:
            price_m = re.search(r'均价[：:]\s*(\d+)', text)
        if not price_m:
            price_m = re.search(r'参考价格[：:]\s*(\d+)', text)
        if price_m:
            details["price"] = price_m.group(1)
            details["price_label"] = f"{details['price']}元/㎡"
            
        # 地址
        addr_m = re.search(r'楼盘地址[：:]\s*([^\n|]+)', text)
        details["address"] = addr_m.group(1).strip() if addr_m else ""
        
        # 兜底区域
        if not details["district"]:
            dist_m = re.search(r'所属区域[：:]\s*([^\s\n|]+)', text)
            details["district"] = dist_m.group(1).strip() if dist_m else ""
            
        # 开盘与交房日期
        open_m = re.search(r'开盘时间[：:]\s*([^\n|]+)', text)
        deliv_m = re.search(r'交房时间[：:]\s*([^\n|]+)', text)
        details["open_date"] = open_m.group(1).strip() if open_m else ""
        details["delivery_date"] = deliv_m.group(1).strip() if deliv_m else ""
        
        # 容积率
        far_m = re.search(r'容积率[：:]\s*([\d.]+)', text)
        details["far"] = far_m.group(1).strip() if far_m else ""
        
        # 绿化率
        green_m = re.search(r'绿化率[：:]\s*([\d.]+)%', text)
        details["green"] = green_m.group(1).strip() + "%" if green_m else ""
        
        # 规划户数
        units_m = re.search(r'规划户数[：:]\s*(\d+)', text)
        if not units_m:
            units_m = re.search(r'总户数[：:]\s*(\d+)', text)
        details["units"] = units_m.group(1).strip() if units_m else ""
        
        # 物业公司
        prop_c_m = re.search(r'物业公司[：:]\s*([^\n|]+)', text)
        details["property_company"] = prop_c_m.group(1).strip() if prop_c_m else ""
        
        # 物业费
        prop_f_m = re.search(r'物业费[：:]\s*([^\n|]+)', text)
        details["property_fee"] = prop_f_m.group(1).strip() if prop_f_m else ""
        
        # 开发商
        dev_m = re.search(r'开发商[：:]\s*([^\n|]+)', text)
        details["developer"] = dev_m.group(1).strip() if dev_m else ""
        
        # 建筑类别
        build_t_m = re.search(r'建筑类别[：:]\s*([^\n|]+)', text)
        details["build_type"] = build_t_m.group(1).strip() if build_t_m else ""
        
        # 主力户型
        rooms_m = re.search(r'主力户型[：:]\s*([^\n|]+)', text)
        details["room_types"] = rooms_m.group(1).strip() if rooms_m else ""
        
        # 4. 提取年份
        year_str = details["delivery_date"] or details["open_date"] or ""
        year_m = re.search(r'(\d{4})', year_str)
        details["year"] = int(year_m.group(1)) if year_m else 0
        
    except Exception as e:
        pass
    return details


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DDS 历史楼盘数据分桶抓取器 (房天下数据源)")
    parser.add_argument("--city", required=True, choices=CITY_CODES.keys(), help="要抓取的城市")
    parser.add_argument("--pages", type=int, default=15, help="列表页抓取深度 (默认 15 页)")
    parser.add_argument("--year", type=int, help="定向抓取和导出的年份 (如 2018，若不指定则抓取 2010-2025 全量分桶)")
    parser.add_argument("--dry-run", action="store_true", help="空跑预览模式")
    parser.add_argument("--fetch-details", action="store_true", default=True, help="是否深度抓取小区参数与开发商 (默认开启)")
    args = parser.parse_args()

    city = args.city
    city_code = CITY_CODES[city]
    city_id = CITY_IDS[city]

    print(f"\n{'='*64}")
    print(f" [*] DDS 历史数据抓取启动 (房天下直名归档) | 城市: {city} | 目标页面数: {args.pages}")
    if args.year:
        print(f" [-] 目标年份: {args.year}年")
    else:
        print(f" [-] 目标范围: 2010 - 2025 年份分桶归档")
    if args.dry_run:
        print(" [-] 【空跑模式开启】— 不写入任何物理文件夹，仅展示解析分桶")
    print(f"{'='*64}\n")

    all_xiaoqus = []
    
    # ── 1. 抓取房天下列表页 ────────────────────────────────────────────────────
    for page in range(1, args.pages + 1):
        if page == 1:
            url = f"https://{city_code}.newhouse.fang.com/house/s/"
        else:
            url = f"https://{city_code}.newhouse.fang.com/house/s/b9{page}/"
            
        print(f"正在请求列表第 {page}/{args.pages} 页: {url}")
        
        if args.dry_run and page > 1:
            print("空跑模式：限制仅抓取第 1 页进行解析预览。")
            break
            
        try:
            html = get_html(url)
            page_items = parse_fang_list(html, city_code)
            print(f"  成功解析出 {len(page_items)} 个楼盘链接")
            all_xiaoqus.extend(page_items)
        except Exception as e:
            print(f"[error] 请求列表第 {page} 页失败: {e}")
            break
            
        time.sleep(random.uniform(1.0, 2.0))

    # ── 2. 数据过滤与详情补齐 ──────────────────────────────────────────────────
    filtered_items = []
    print(f"\n列表抓取完成，共获得 {len(all_xiaoqus)} 个楼盘链接，开始筛选并请求详情页...")

    # 过滤条件
    def is_target_year(y):
        if args.year:
            return y == args.year
        return 2010 <= y <= 2025

    for idx, item in enumerate(all_xiaoqus, 1):
        print(f"\n[{idx}/{len(all_xiaoqus)}] 请求楼盘详情: {item['detail_url']} ...")
        
        if args.fetch_details and not args.dry_run:
            details = fetch_fang_details(item["detail_url"])
            item.update(details)
            time.sleep(random.uniform(1.0, 2.0))
        else:
            # 详情字段默认空跑赋空占位
            # 为了使 dry-run 能分桶展示，空跑时随机分配一个 2010-2025 之间的年份
            random_year = random.randint(2015, 2024)
            item.update({
                "name": f"方大·海棠半岛壹号-{idx}",
                "far": "1.2", "green": "45%", "units": "631", 
                "property_company": "方大物业", "property_fee": "9.9", 
                "developer": "方大置业", "build_type": "板楼",
                "open_date": f"{random_year}-06-01", "delivery_date": f"{random_year}-12-31",
                "year": random_year, "status": "在售", "price": "35000",
                "price_label": "35000元/㎡", "address": "海棠区林旺南路与灯潭路交汇",
                "district": "海棠区"
            })
            
        if not item.get("name"):
            print("  [skip] 楼盘详情页抓取或解析失败，名称为空。")
            continue
            
        if not is_target_year(item["year"]):
            print(f"  [skip] 楼盘 {item['name']} 年份为 {item['year']} 年，不处于目标范围。")
            continue
            
        print(f"  [OK] 命中历史区间: {item['name']} ({item['year']}年建成)")
        
        # 补全地址
        if not item.get("address"):
            item["address"] = f"{city}市{item.get('district', '')}{item['name']}"
        
        # 高德 API 地理编码获取坐标
        if not args.dry_run:
            print(f"  调用高德 API 进行地理编码...")
            lat, lng = geocode_address(item["address"], city)
            item["lat"] = lat
            item["lng"] = lng
            time.sleep(0.3)
        else:
            item["lat"] = "18.2526"
            item["lng"] = "109.5123"
            
        filtered_items.append(item)
        
        # 空跑模式限制展示数量
        if args.dry_run and len(filtered_items) >= 2:
            print("\n空跑模式：已抓取并生成 2 条样本用于预览。")
            break

    # ── 3. 写入/分桶归档 ──────────────────────────────────────────────────────
    if args.dry_run:
        print(f"\n{'='*20} 【空跑预览】新分桶结构展示 {'='*20}")
        for it in filtered_items:
            target_file = VAULT / f"{it['year']}年" / f"{city}.csv"
            print(f"\n[File] 目标路径: {target_file}")
            row = [
                it.get("id"), it.get("name"), city, city_id, it.get("district"), "",
                it.get("bizcircle"), "", it.get("address"), "", it.get("price"), it.get("price_label"),
                "", "", "", "", "[]",
                "", it.get("room_types"), it.get("open_date"), "", "",
                it.get("delivery_date"), "[]", it.get("build_type"), it.get("build_type"), "70年", it.get("far"),
                it.get("green"), it.get("units"), "", "", "住宅", it.get("property_company"),
                it.get("property_fee"), "", "", "", "售罄", "售罄",
                "售罄", "[]", "[]", it.get("developer"), "", "",
                "民电", "民水", it.get("lat"), it.get("lng"), "",
                "{}", "[]", it.get("img"), "[]", it.get("address")
            ]
            print(f"[Data] 字段对齐预览 (列数: {len(row)}):")
            for h, v in zip(CSV_HEADERS[:12], row[:12]):
                print(f"  {h} -> {v}")
            print("  ...")
            print(f"  百度地图纬度 -> {it.get('lat')}")
            print(f"  百度地图经度 -> {it.get('lng')}")
        print(f"\n{'='*20} 预览结束 {'='*20}\n")
        return 0

    print(f"\n开始写入归档数据，共计 {len(filtered_items)} 个历史楼盘...")
    
    written_count = 0
    buckets = {}
    
    for it in filtered_items:
        year = it["year"]
        year_dir = VAULT / f"{year}年"
        year_dir.mkdir(parents=True, exist_ok=True)
        
        csv_file = year_dir / f"{city}.csv"
        file_exists = csv_file.exists()
        
        try:
            with open(csv_file, mode="a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                
                if not file_exists:
                    writer.writerow(CSV_HEADERS)
                    
                row = [
                    it.get("id"), it.get("name"), city, city_id, it.get("district"), "",
                    it.get("bizcircle"), "", it.get("address"), "", it.get("price"), it.get("price_label"),
                    "", "", "", "", "[]",
                    "", it.get("room_types"), it.get("open_date"), "", "",
                    it.get("delivery_date"), "[]", it.get("build_type"), it.get("build_type"), "70年", it.get("far"),
                    it.get("green"), it.get("units"), "", "", "住宅", it.get("property_company"),
                    it.get("property_fee"), "", "", "", "售罄", "售罄",
                    "售罄", "[]", "[]", it.get("developer"), "", "",
                    "民电", "民水", it.get("lat"), it.get("lng"), "",
                    "{}", "[]", it.get("img"), "[]", it.get("address")
                ]
                writer.writerow(row)
                
            written_count += 1
            buckets[year] = buckets.get(year, 0) + 1
        except Exception as e:
            print(f"[warn] 写入楼盘 {it['name']} 失败: {e}")

    print(f"\n{'='*64}")
    print(f" [OK] 归档成功！共计 {written_count} 个楼盘已落盘。")
    for yr, cnt in sorted(buckets.items()):
        print(f"  [-] Vault/{yr}年/{city}.csv | 新增 {cnt} 条历史楼盘")
    print(f"{'='*64}\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

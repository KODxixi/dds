#!/usr/bin/env python3
"""
Image Hunter v4.0  —  小红书 + 微信公众号 原图归档工具
用法:
    python index.py "<URL>" [项目名] [设计方] [项目所在地] [风格]
"""

import sys, re, hashlib, shutil, tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape

try:
    import requests
except ImportError:
    print("错误: pip install requests"); sys.exit(1)

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    print("⚠️ 未装 Pillow，落盘将保留原格式（pip install pillow 后可转 PNG）")

# ============ 配置 ============

ARCHIG_ROOT       = Path(r"D:\ArchLib")
OBSIDIAN_FALLBACK = Path(r"C:\Users\shiguanyu\Documents\Obsidian\Gnano\Reference")
MIN_IMAGE_SIZE    = 30 * 1024   # 30KB 过滤缩略图
MAX_WORKERS       = 4
REQUEST_TIMEOUT   = 30

HEADERS_XHS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.xiaohongshu.com/",
}

HEADERS_WX = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://mp.weixin.qq.com/",
}

CATEGORY_MAP = {
    "07-商业办公-Commercial":     ["商业","商业街","街区","商圈","商铺","店铺","市集","集市","快闪店","商业广场","购物中心","办公","写字楼","综合体","总部","非标商业"],
    "08-非标商业-CommercialSpecial": ["非标商业","主理人","买手店","概念店","生活方式"],
    "11-城市设计-UrbanDesign":    ["城市设计","城市规划","总体规划","未来城市","超级综合体"],
    "01-度假酒店-Resort":         ["酒店","度假","民宿","resort","度假酒店","度假村","精品酒店"],
    "02-文化建筑-Cultural":       ["文化","博物馆","美术馆","图书馆","展览","艺术馆","展览馆","科技馆","纪念馆","文化馆","规划馆","音乐厅","大剧院","歌剧院"],
    "03-别墅-Villa":              ["别墅","villa","独栋","联排"],
    "04-居住建筑-Residential":    ["住宅","公寓","居住","高层","低密","安置房","公租房"],
    "05-高层豪宅-HighRiseLuxury": ["豪宅","高层豪宅","超高层住宅","天际线"],
    "06-示范区-Showroom":         ["示范区","展示区","售楼处","样板间","展示中心"],
    "09-规划公建-PublicPlanning": ["公共","公园","茶室","社区中心","体育馆","体育场","游泳馆","会展中心","会议中心","教育","学校","医疗","医院","寺庙","教堂","宗教"],
    "10-改造建筑-Renovation":     ["改造","更新","活化","旧改","微更新"],
    "12-室内设计-Interior":       ["室内","软装","硬装","精装"],
    "20-顶级事务所-NotableFirms": ["扎哈","zaha","kpf","som","big","aedas","mad","oma","mvr dv","foster","heatherwick"],
    "21-概念方案-Concept":        ["概念方案","概念设计","竞赛方案","投标"],
    "80-AI素材-AIGC":             ["ai生成","midjourney","stablediffusion","sd","aigc"],
}

KNOWN_LOCATIONS = [
    "迪拜","新加坡","东京","伦敦","纽约","巴黎","米兰","悉尼",
    "北京","上海","广州","深圳","杭州","成都","重庆","南京","苏州","武汉",
    "天津","西安","长沙","厦门","青岛","大连","宁波","无锡","合肥","佛山",
    "东莞","珠海","昆明","福州","济南","郑州","海口","三亚","贵阳","南宁",
    "温州","常州","绍兴","嘉兴","惠州","中山","天目湖","安吉","德清","湖州",
]

KNOWN_DESIGNERS = [
    "扎哈","扎哈哈迪德","Zaha Hadid","ZHA",
    "gad","goa","line+","Aedas","AECOM","SOM","KPF","BIG","MAD","OMA","MVRDV",
    "Foster","Heatherwick","Kengo Kuma","隈研吾",
    "安藤忠雄","贝聿铭","矶崎新","伊东丰雄","妹岛和世",
    "大象","gad杰地设计","goa大象设计","九源",
    "天华","日清","水石","华东院","中建","中信","基准方中","上海日清",
    "万科","龙湖","保利","绿城","融创","华润","招商","金地","中梁","旭辉",
]

DESIGNER_BLACKLIST_CONTEXTS = {
    "招商": ["招商回本","招商周期","招商落地","商业招商","招商情况","招商完成"],
    "华润": ["华润万家","华润超市"],
}

PROJECT_NAME_BLACKLIST = {
    "现在城","城市","中心城市","大城市","城市中","城市里","城市更新",
    "商业","商业街","商业中心","商圈","商业广场",
}

PROJECT_VERB_PREFIXES = ["适配","打造","建设","改造","设计","规划","提升","做","搞","来","不抢"]
PROJECT_BAD_PREFIXES  = ["不","没","无","非"]

AUTHOR_BLACKLIST = ["网","赏析","日记","精选","门户","室内设计","建筑设计","官方","资讯",
                    "小助手","君","大赏","集锦","案例","美图","分享","整理"]

BANNED_WORDS = [
    "绝了","神仙","天花板","yyds","炸裂","封神","王炸","上头","救命","离谱",
    "来袭","驾到","强烈推荐","不容错过","独家揭秘","全网首发",
    "秒杀","碾压","降维打击","史上最","颠覆","重新定义",
    "太美了","美到窒息","惊为天人","简直了","梦幻","仙境",
]

BUILDING_TYPES = [
    "度假酒店","精品酒店","商业综合体","超高层","文化中心","美术馆","博物馆",
    "住宅","豪宅","别墅","公寓","写字楼","办公","学校","医院","体育馆",
    "酒店","民宿","度假村","resort",
]


# ============ 共用工具函数 ============

def clean_name(s, max_len=20):
    if not s: return ""
    for w in BANNED_WORDS: s = s.replace(w, "")
    s = re.sub(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF☀-⛿✀-➿]+", "", s)
    s = re.sub(r"[!！?？|｜#＃@＠\[\]【】{}（）()《》<>\\/:*\"<>|]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        for sep in ["，", ",", "。", " ", "、", "·"]:
            idx = s.rfind(sep, 0, max_len)
            if idx > max_len // 2:
                s = s[:idx]; break
        else:
            s = s[:max_len]
    return s.strip("，,。、· ")


# ArchLib 业态单轴分类（对齐 _检索系统/taxonomy.yaml，8大类/21业态；返回 "X0_大类/XY_业态"）
TAXO_RULES = [
    ("30_营销空间/31_售楼中心展示区", ["售楼","示范区","展示区","样板间","样板房","营销中心","交付大区","展示中心","售楼处","会所"]),
    ("10_居住/12_别墅·私人住宅", ["别墅","villa","独栋","联排","双拼","叠墅","合院","院墅","私宅","私人住宅"]),
    ("10_居住/13_高层豪宅·顶豪", ["豪宅","顶豪","大平层","空中院墅","臻邸"]),
    ("20_酒店旅居/24_民宿·精品", ["民宿","山居","乡野","乡居"]),
    ("20_酒店旅居/23_野奢庄园", ["野奢","营地","帐篷酒店","庄园"]),
    ("20_酒店旅居/22_旅游度假", ["度假","resort","度假村","度假综合体","温泉","康养","海岛","山地度假","森林度假","滨海度假","高尔夫"]),
    ("20_酒店旅居/21_城市酒店", ["酒店","hotel","精品酒店","设计酒店","商务酒店","豪华酒店","电竞酒店"]),
    ("40_商业办公/42_非标商业街区", ["非标商业","商业街","街区","奥莱","购物村","文创","艺术街区","主题商业","沉浸","文旅商业","旗舰店","买手店","品牌店","主理人"]),
    ("40_商业办公/41_商业综合体", ["综合体","购物中心","mall","太古里","裙房","商业广场","商圈","shopping"]),
    ("40_商业办公/44_文旅地产", ["文旅综合体","特色小镇","主题乐园","文旅"]),
    ("40_商业办公/43_办公·产业园", ["办公","写字楼","总部","产业园","企业园","联合办公","创意办公","大厦","office"]),
    ("50_文化公共/51_艺术文化空间", ["美术馆","博物馆","音乐厅","剧院","歌剧院","演艺","文化中心","展馆","国家馆","图书馆","艺术馆","展览馆","文化馆","设计中心","艺术中心"]),
    ("50_文化公共/52_教育·医疗", ["学校","校园","幼儿园","医院","疗养","医疗","教育"]),
    ("50_文化公共/53_工业·基建·交通", ["厂房","产业基地","交通枢纽","站房","机场","高铁","桥","构筑物","观景平台","市政","会展","体育馆","体育场","展览中心"]),
    ("60_城市·更新/62_历史遗址更新", ["历史街区","里份","文保","历史遗址","遗址活化"]),
    ("60_城市·更新/61_城市更新", ["城市更新","老旧","微更新","工业遗存","旧改","活化","更新"]),
    ("60_城市·更新/63_城市·规划设计", ["城市设计","片区规划","概念规划","总体规划","总图","城市规划"]),
    ("70_室内/71_室内空间", ["室内","样板房","餐饮","餐厅","咖啡","零售空间","大堂","宴会","软装","硬装","精装","interior"]),
    ("10_居住/11_公寓住宅", ["公寓","住宅","高层","洋房","叠拼","塔楼","板楼","小高层","居住","apartment","mansion"]),
    ("80_专项库/81_概念方案", ["概念方案","概念设计","竞赛","投标","未建成","unbuilt","意向集"]),
]

def classify_category(text, style=""):
    tl = (text + " " + style).lower()
    for path, kws in TAXO_RULES:
        if any(kw.lower() in tl for kw in kws):
            return path
    return "90_资源·非案例/98_待分类"



def find_location(text):
    return next((loc for loc in KNOWN_LOCATIONS if loc in text), "")


def find_designer(text):
    flat = re.sub(r"[·・]", "", text)
    for d in KNOWN_DESIGNERS:
        if d.lower() in flat.lower():
            if d in DESIGNER_BLACKLIST_CONTEXTS:
                if any(ctx in flat for ctx in DESIGNER_BLACKLIST_CONTEXTS[d]):
                    continue
            return d
    return ""


def download_image(url, save_path, index, headers):
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, stream=True)
        if r.status_code != 200:
            print(f"  ⚠️ {index} HTTP {r.status_code}"); return False
        data = r.content
        if len(data) < MIN_IMAGE_SIZE:
            print(f"  ⏭️ {index} 太小 ({len(data)//1024}KB)"); return False
        save_path.write_bytes(data)
        print(f"  ✅ {index} ({len(data)//1024}KB)"); return True
    except Exception as e:
        print(f"  ❌ {index} {e}"); return False


def batch_download(imgs, headers):
    tmp = Path(tempfile.mkdtemp(prefix="img_hunter_"))
    done = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {
            ex.submit(download_image, u, tmp / f"img_{i}.jpg", i, headers): (i, tmp / f"img_{i}.jpg")
            for i, u in enumerate(imgs, 1)
        }
        for f in as_completed(futs):
            i, p = futs[f]
            if f.result(): done.append((i, p))
    # 按内容去重
    seen, unique = set(), []
    for i, p in sorted(done):
        h = hashlib.md5(p.read_bytes()).hexdigest()
        if h not in seen:
            seen.add(h); unique.append((i, p))
        else:
            p.unlink()
    removed = len(done) - len(unique)
    if removed:
        print(f"🗑️  移除 {removed} 张重复")
    return unique, tmp


def save_as_png(src_path, dest_no_ext):
    """把临时图片落盘为 PNG（dest_no_ext 不含扩展名）。
    成功返回 .png 路径；无 Pillow 或转换失败则回退为原始 .jpg 落盘。"""
    src_path = str(src_path)
    if _HAS_PIL:
        out = str(dest_no_ext) + ".png"
        try:
            with Image.open(src_path) as im:
                if im.mode in ("P", "LA"):
                    im = im.convert("RGBA")
                elif im.mode == "CMYK":
                    im = im.convert("RGB")
                im.save(out, "PNG")
            try:
                Path(src_path).unlink()
            except OSError:
                pass
            return out
        except Exception as e:
            print(f"  ⚠️ PNG 转换失败，保留原图: {e}")
    out = str(dest_no_ext) + ".jpg"
    shutil.move(src_path, out)
    return out


def do_archive(done_files, proj, loc, des, style, all_text):
    cat   = classify_category(f"{all_text} {proj}", style)
    loc   = clean_name(loc,   10) or "未知"
    proj  = clean_name(proj,  25) or "未知"
    des   = clean_name(des,   15) or "未知"
    style = clean_name(style, 10)
    dest  = (ARCHIG_ROOT if ARCHIG_ROOT.exists() else OBSIDIAN_FALLBACK) / cat / f"{loc}_{proj}_{des}"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"📁 归档: {dest}\n🏷️  分类: {cat}")
    for i, p in done_files:
        save_as_png(p, dest / f"{proj}_{i:02d}")
    return dest


# ============ 小红书专用 ============

def xhs_resolve(url):
    if "xhslink.com" in url:
        try:
            r = requests.head(url, headers=HEADERS_XHS, allow_redirects=True, timeout=REQUEST_TIMEOUT)
            return r.url
        except Exception as e:
            print(f"⚠️ 短链解析失败: {e}")
    return url


def xhs_extract(html):
    meta = {"title": "", "author": "", "description": "", "images": []}
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
    if m:
        meta["title"] = re.sub(r"\s*[-–—]\s*小红书.*$", "", m.group(1).strip())
    nd = re.search(r'"desc"\s*:\s*"([^"]{20,})"', html)
    if nd:
        meta["description"] = nd.group(1).replace("\\n", "\n").strip()
    else:
        dm = re.search(r'name=["\']description["\'][^>]*content=["\']([^"\'>]*)', html)
        if dm: meta["description"] = dm.group(1).strip()
    am = re.search(r'"nickname"\s*:\s*"([^"]+)"', html)
    if am: meta["author"] = am.group(1)
    og = re.findall(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
    meta["images"].extend(og)
    for u in re.findall(r'"url"\s*:\s*"(https?://[^"]*(?:sns-webpic|ci\.xiaohongshu\.com)[^"]*)"', html):
        u = re.sub(r"\?imageView2.*$", "", u).replace("\\u002F", "/")
        if u not in meta["images"]: meta["images"].append(u)
    for u in re.findall(r'"(https?://sns-webpic-qc\.xhscdn\.com/\d+/[^"]+)"', html):
        u = re.sub(r"\?imageView2.*$", "", u)
        if u not in meta["images"]: meta["images"].append(u)
    return meta


def xhs_project_name(title, description):
    all_text = f"{title} {description}"
    for sep in [" | ", " |", "| ", "|", "：", ":", " - ", " -", "- ", "—"]:
        if sep in title:
            for part in [p.strip() for p in title.split(sep)]:
                if len(part) > 2 and "小红书" not in part:
                    if not any(d.lower() == part.lower() for d in KNOWN_DESIGNERS):
                        if part not in KNOWN_LOCATIONS:
                            return clean_name(part, 20)
    quoted = re.findall(r"[《「【]([^》」】]{2,18})[》」】]", all_text)
    if quoted: return clean_name(quoted[0], 20)
    pattern = (r"([一-龥]{2,10}(?:图书馆|美术馆|博物馆|艺术馆|展览馆|科技馆|纪念馆|文化馆|规划馆"
               r"|音乐厅|大剧院|歌剧院|体育馆|体育场|游泳馆|会展中心|会议中心|中心"
               r"|酒店|府|院|城|苑|庭|湾|岸|阁|墅|宅|园|堂|居|舍|坊|里|台|筑|汇|悦|锦|华|玺|壹|雅|和|境))")
    for m in re.findall(pattern, all_text):
        if m in PROJECT_NAME_BLACKLIST or len(m) < 3: continue
        if any(m.startswith(p) for p in PROJECT_VERB_PREFIXES + PROJECT_BAD_PREFIXES): continue
        return clean_name(m, 15)
    found_loc = find_location(all_text)
    found_des = find_designer(all_text)
    found_typ = next((bt for bt in BUILDING_TYPES if bt in all_text), "")
    if found_loc and (found_des or found_typ):
        parts = [found_loc]
        if found_des: parts.append(found_des)
        if found_typ and found_typ not in (found_des or ""): parts.append(found_typ)
        return "_".join(parts)
    return clean_name(title, 20)


# ============ 微信专用 ============

def wx_extract_title(html):
    for pat in [r'var\s+msg_title\s*=\s*"([^"]+)"',
                r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"',
                r'<title[^>]*>(.*?)</title>']:
        m = re.search(pat, html, re.DOTALL)
        if m:
            t = unescape(m.group(1).strip())
            return re.sub(r"\s*[-–—]\s*微信.*$", "", t)
    return "untitled"


def wx_extract_images(html):
    block_m = re.search(r'id="js_content"(.*?)(?:id="js_editor_insertimages"|</article)', html, re.DOTALL)
    block = block_m.group(1) if block_m else html
    seen, imgs = set(), []
    for u in re.findall(r'src="(https?://mmbiz\.qpic\.cn/[^"]+)"', block):
        u = unescape(u)
        u = re.sub(r"/(\d{2,4})\?", "/0?", u)   # /640? → /0? 取原图
        if u not in seen: seen.add(u); imgs.append(u)
    return imgs


# ============ 平台检测 ============

def detect_platform(url):
    if "mp.weixin.qq.com" in url:
        return "wx"
    if "xhslink.com" in url or "xiaohongshu.com" in url:
        return "xhs"
    return None


# ============ 主流程 ============

def main():
    if len(sys.argv) < 2:
        print("用法: python index.py <URL> [项目名] [设计方] [所在地] [风格]")
        sys.exit(1)

    url         = sys.argv[1]
    manual_proj = sys.argv[2] if len(sys.argv) > 2 else ""
    manual_des  = sys.argv[3] if len(sys.argv) > 3 else ""
    manual_loc  = sys.argv[4] if len(sys.argv) > 4 else ""
    manual_styl = sys.argv[5] if len(sys.argv) > 5 else ""

    platform = detect_platform(url)
    if not platform:
        print(f"❌ 不支持的 URL（仅限微信公众号 / 小红书）: {url}")
        sys.exit(1)

    print(f"🔍 Image Hunter v4.0  [{platform.upper()}]")
    print(f"📎 {url}")

    # 获取页面
    if platform == "xhs":
        url = xhs_resolve(url)
        headers = HEADERS_XHS
    else:
        headers = HEADERS_WX

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.encoding = "utf-8"
        html = resp.text
    except Exception as e:
        print(f"❌ 获取页面失败: {e}"); sys.exit(1)

    # 提取元数据 & 图片
    if platform == "xhs":
        meta      = xhs_extract(html)
        title     = meta["title"]
        author    = meta["author"]
        desc      = meta["description"]
        imgs      = meta["images"]
        is_bl     = any(w in author for w in AUTHOR_BLACKLIST)
        auto_proj = xhs_project_name(title, desc)
        auto_des  = find_designer(f"{title} {desc}") or ("" if is_bl else author)
        auto_loc  = find_location(f"{title} {desc}")
    else:
        title     = wx_extract_title(html)
        imgs      = wx_extract_images(html)
        auto_proj = title
        auto_des  = ""
        auto_loc  = find_location(title)

    print(f"📝 标题: {title}")
    print(f"🖼️  找到 {len(imgs)} 张图片")
    if not imgs:
        print("❌ 未找到图片"); sys.exit(1)

    # 下载 & 去重
    done, tmp = batch_download(imgs, headers)
    print(f"📦 有效: {len(done)} 张")
    if not done:
        print("❌ 无有效图片"); shutil.rmtree(tmp, ignore_errors=True); sys.exit(1)

    # 归档（手动参数优先）
    proj = manual_proj or auto_proj or title
    des  = manual_des  or auto_des  or "未知"
    loc  = manual_loc  or auto_loc  or "未知"

    dest = do_archive(done, proj, loc, des, manual_styl, f"{title} {proj}")
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*50}")
    print(f"✅ 归档完成!")
    print(f"📁 {dest}")
    print(f"🖼️  {len(done)}/{len(imgs)} 张")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

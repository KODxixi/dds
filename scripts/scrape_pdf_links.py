"""
DDS PDF 链接抓取与下载脚本
从 normalized JSON 中提取详情页，访问详情页解析并下载 PDF 附件
支持上传至 GCS 并维护下载清单

用法：
  python scrape_pdf_links.py --source landchina --date 2026/05/11
  python scrape_pdf_links.py --input path/to/file_normalized.json --source landchina
  python scrape_pdf_links.py --dry-run
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request, error
from urllib.parse import urljoin, urlparse

# 依赖项说明：内置库 + 外部 shell 调用 gcloud

TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ── 配置 ────────────────────────────────────────────────────────────────────

BASE_DIRS = {
    "landchina":    Path(r"C:\Users\shiguanyu\DDS\data_out\landchina\normalized"),
    "hangzhou":     Path(r"C:\Users\shiguanyu\DDS\data_out\hangzhou\normalized"),
    "sanya":        Path(r"C:\Users\shiguanyu\DDS\data_out\sanya\normalized"),
}

PDF_RAW_ROOT = Path(r"C:\Users\shiguanyu\DDS\data_out\pdfs\raw")
MANIFEST_ROOT = Path(r"C:\Users\shiguanyu\DDS\data_out\pdfs\manifests")

# ── 辅助函数 ────────────────────────────────────────────────────────────────

def http_get(url: str, retries: int = 3, is_binary: bool = False):
    req = request.Request(url, headers={"User-Agent": UA})
    for i in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=30) as r:
                if is_binary:
                    return r.read()
                return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception as e:
            if i == retries:
                raise RuntimeError(f"访问 {url} 失败: {e}")
            time.sleep(i * 2 + random.random())
    raise RuntimeError("Unreachable")


def gcs_upload(local_path: Path, gcs_uri: str) -> bool:
    """调用 gcloud 上传至 GCS"""
    try:
        cmd = ["gcloud", "storage", "cp", str(local_path), gcs_uri]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except Exception as e:
        print(f"[警告] GCS 上传失败 ({local_path.name}): {e}")
        return False


def find_pdf_urls(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取 PDF 链接"""
    urls = []
    # 匹配 href="...", src="..." 等包含 .pdf 的路径
    matches = re.findall(r'(?:href|src)=["\']([^"\']+\.pdf[^"\']*)["\']', html, re.IGNORECASE)
    for m in matches:
        full_url = urljoin(base_url, m)
        if full_url not in urls:
            urls.append(full_url)
            
    # 备选：有的附件链接可能没有以 .pdf 结尾，但文字包含 PDF。简单正则：
    anchors = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', html, re.IGNORECASE)
    for link, text in anchors:
        if "pdf" in text.lower() and ".doc" not in link.lower():
            full_url = urljoin(base_url, link)
            if full_url not in urls:
                urls.append(full_url)
                
    return urls

# ── 数据源特定逻辑 ─────────────────────────────────────────────────────────

def get_landchina_html(parcel_id: str) -> str:
    """
    LandChina 逻辑：根据 prompt 拼接 detail_url 并尝试抓取。
    注意：LandChina 是 SPA 架构，直接抓取 #/ 链接可能需要解析 API 接口。
    这里采用与 enrich_detail.py 类似的方式尝试详情接口获取附件信息。
    """
    # 构造前端页面 URL
    page_url = f"https://www.landchina.com/#/landSupplyDetail?id={parcel_id}"
    # 备选：LandChina 后端 API 通常在 api.landchina.com
    # 考虑到环境限制，我们先尝试获取该 HTML，如果没有发现链接，
    # 构造一个通用的详情文本搜索，也可以直接使用 enrich_detail.py 中提到的 API 端点尝试。
    try:
        # 尝试 API
        import hashlib
        def _lc_hash(endpoint: str) -> str:
            return hashlib.md5(f"{UA}{datetime.now(TZ).day}{endpoint}".encode()).hexdigest()
        
        api_url = "https://api.landchina.com/tGygg/transfer/detail"
        payload = json.dumps({"id": parcel_id}).encode()
        req = request.Request(api_url, data=payload, method="POST", 
                              headers={
                                  "User-Agent": UA,
                                  "Content-Type": "application/json;charset=UTF-8",
                                  "Hash": _lc_hash("detail"),
                                  "Origin": "https://www.landchina.com",
                                  "Referer": page_url
                              })
        with request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
            # 检查 json 中是否有 fjList (附件列表) 或 content 里的 URL
            content = json.dumps(data, ensure_ascii=False)
            return content
    except Exception as e:
        # API 失败，回退直接抓网页（如果有 SSR）
        try: return http_get(page_url)
        except: return ""


def extract_source_html(source: str, item: dict) -> str:
    """获取详情页/API响应内容以供解析 PDF 链接"""
    detail_url = item.get("detail_url")
    parcel_id = item.get("parcel_id", "")
    
    if source == "landchina":
        if not parcel_id: return ""
        return get_landchina_html(parcel_id)
    elif detail_url:
        try:
            return http_get(detail_url)
        except Exception as e:
            print(f"[错误] 抓取详情页失败 ({detail_url}): {e}")
            return ""
    return ""

# ── 主处理循环 ──────────────────────────────────────────────────────────────

def process_item(item: dict, source: str, bucket: str, dry_run: bool) -> list[dict]:
    parcel_id = item.get("parcel_id")
    if not parcel_id:
        # 如果没有 ID，尝试用清洗过的标题或 URL 占位
        if item.get("detail_url"):
            parcel_id = hashlib.md5(item["detail_url"].encode()).hexdigest()
        else:
            return []
            
    # 清洗 ID 防止路径非法
    safe_id = re.sub(r'[\\/:*?"<>|]', "_", str(parcel_id))
    
    # 获取来源 HTML 并解析 PDF
    html_content = extract_source_html(source, item)
    if not html_content:
        return []
        
    base_url = item.get("detail_url") or "https://www.landchina.com/"
    pdf_urls = find_pdf_urls(html_content, base_url)
    
    if not pdf_urls:
        return []
        
    now = datetime.now(TZ)
    day_path = now.strftime("%Y/%m/%d")
    
    results = []
    for idx, pdf_url in enumerate(pdf_urls):
        # 处理多个 PDF 情况，追加索引
        suffix = f"_{idx}" if idx > 0 else ""
        filename = f"{safe_id}{suffix}.pdf"
        
        local_dir = PDF_RAW_ROOT / source / day_path
        local_path = local_dir / filename
        gcs_dir = f"{bucket}/real_estate_data/pdfs/raw/{source}/{day_path}"
        gcs_uri = f"{gcs_dir}/{filename}"
        
        if dry_run:
            print(f"[DryRun] 发现 PDF: {pdf_url} -> {local_path}")
            results.append({"parcel_id": parcel_id, "pdf_url": pdf_url, "status": "dry-run"})
            continue
            
        try:
            print(f"[处理] 下载中: {pdf_url}")
            # 执行下载
            pdf_bytes = http_get(pdf_url, is_binary=True)
            
            # 保存本地
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(pdf_bytes)
            
            # 上传 GCS
            upload_ok = gcs_upload(local_path, gcs_uri)
            
            manifest_entry = {
                "parcel_id": parcel_id,
                "source": source,
                "pdf_url": pdf_url,
                "local_path": str(local_path),
                "gcs_uri": gcs_uri if upload_ok else None,
                "downloaded_at": datetime.now(timezone.utc).isoformat() + "Z",
                "file_size_bytes": len(pdf_bytes),
                "status": "success"
            }
            results.append(manifest_entry)
            
            # 稍微休眠防屏蔽
            time.sleep(random.uniform(1.0, 3.0))
            
        except Exception as e:
            print(f"[失败] 下载 {pdf_url} 错误: {e}")
            results.append({
                "parcel_id": parcel_id,
                "source": source,
                "pdf_url": pdf_url,
                "status": "error",
                "error": str(e)
            })
            
    return results

# ── 主程序 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DDS PDF 抓取下载脚本")
    parser.add_argument("--source", required=True, choices=["landchina", "hangzhou", "sanya"], help="数据源")
    parser.add_argument("--input", help="指定输入的 normalized JSON 文件路径")
    parser.add_argument("--date", help="按日期目录扫描 normalized，格式 YYYY/MM/DD")
    parser.add_argument("--bucket", default="gs://dds-data-lake", help="GCS 桶名称")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划，不实际下载/上传")
    
    args = parser.parse_args()
    
    print("="*50)
    print(f"启动 PDF 抓取任务 | 源: {args.source} | DryRun: {args.dry_run}")
    print("="*50)
    
    # 1. 解析输入源文件
    input_files = []
    if args.input:
        input_files = [Path(args.input)]
    elif args.date:
        target_dir = BASE_DIRS[args.source] / args.date
        if target_dir.exists():
            input_files = list(target_dir.glob("**/*_normalized.json"))
    else:
        # 默认遍历全部 normalized
        if BASE_DIRS[args.source].exists():
            input_files = list(BASE_DIRS[args.source].rglob("*_normalized.json"))
            
    if not input_files:
        print(f"[警告] 未找到输入文件，路径 {BASE_DIRS[args.source]} 是否存在？")
        # 为演示和调试，允许程序直接结束，而不报错
        return
        
    print(f"[信息] 共发现 {len(input_files)} 个输入源文件待处理...")
    
    # 2. 遍历读取文件条目
    manifests = []
    for file_path in input_files:
        print(f"读取文件: {file_path.name}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                items = json.load(f)
                if not isinstance(items, list):
                    items = [items]
                    
            for it in items:
                res = process_item(it, args.source, args.bucket, args.dry_run)
                manifests.extend(res)
                
        except Exception as e:
            print(f"[错误] 处理文件 {file_path} 异常: {e}")
            
    # 3. 写入汇总 Manifest
    if not args.dry_run and manifests:
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_file = MANIFEST_ROOT / f"manifest_{args.source}_{now_str}.json"
        MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)
        
        with open(manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifests, f, ensure_ascii=False, indent=2)
            
        print(f"\n[完成] 处理结束，清单已保存至: {manifest_file}")
        
        # 尝试将清单本身也传到 GCS
        gcs_upload(manifest_file, f"{args.bucket}/real_estate_data/shared/manifests/{manifest_file.name}")
    else:
        print(f"\n[结束] 本轮没有产生下载或处于 Dry-Run 状态。")

if __name__ == "__main__":
    import hashlib
    main()

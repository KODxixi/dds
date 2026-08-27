"""完整本地化 kmlovemilk.cn 的公开静态数据与媒体资源。

产物按 raw / media / normalized / indexes 分层，支持断点续传与幂等重跑。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests


BASE_URL = "https://www.kmlovemilk.cn/"
JSON_SEEDS = [
    "data.json", "image-map.json", "assets-data.json", "benchmark-data.json",
    "cases-data.json", "detail-data.json", "market-data.json", "policy-data.json",
    "design_firms_full.json", "design_ranking.json", "china.json",
]
STATIC_SEEDS = ["", "manifest.webmanifest", "registerSW.js", "icon-192x192.png"]
URL_RE = re.compile(r"https?://[^\"'`<>\\\s)]+")
ASSET_RE = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']|(?:import\(|from\s*)[\"'`]([^\"'`]+)[\"'`]")
MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif", ".bmp",
    ".mp4", ".webm", ".mov", ".pdf", ".ppt", ".pptx", ".doc", ".docx",
    ".xls", ".xlsx", ".zip",
}
UA = "Mozilla/5.0 (compatible; DDSKnowledgeCollector/1.0; +local-archive)"
_tls = threading.local()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def session() -> requests.Session:
    if not hasattr(_tls, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "*/*"})
        _tls.session = s
    return _tls.session


def request(url: str, *, stream: bool = False, retries: int = 4) -> requests.Response:
    error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session().get(url, timeout=(15, 120), stream=stream, allow_redirects=True)
            if response.status_code in {429, 500, 502, 503, 504}:
                raise requests.HTTPError(f"retryable HTTP {response.status_code}")
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            time.sleep(min(2 ** attempt, 12))
    raise RuntimeError(f"下载失败 {url}: {error}")


def safe_name(value: str, fallback: str = "item") -> str:
    value = unquote(value).strip().replace("\x00", "")
    value = re.sub(r"[<>:\"/\\|?*\r\n]+", "_", value)
    return value[:160].rstrip(". ") or fallback


def json_dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def fetch_seed(output: Path, relative: str) -> tuple[bytes, dict[str, str]]:
    url = urljoin(BASE_URL, relative)
    response = request(url)
    body = response.content
    name = relative or "index.html"
    target = output / "raw" / "site" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return body, {k.lower(): v for k, v in response.headers.items()}


def mirror_frontend(output: Path) -> list[str]:
    queue = list(STATIC_SEEDS)
    seen: set[str] = set()
    saved: list[str] = []
    while queue:
        relative = queue.pop(0).lstrip("/")
        if relative in seen:
            continue
        seen.add(relative)
        try:
            body, _ = fetch_seed(output, relative)
        except Exception:
            continue
        saved.append(relative or "index.html")
        if Path(urlparse(relative).path).suffix.lower() not in {"", ".html", ".js", ".css", ".webmanifest"}:
            continue
        text = body.decode("utf-8", errors="replace")
        for match in ASSET_RE.finditer(text):
            ref = next((g for g in match.groups() if g), "")
            if not ref or ref.startswith(("data:", "#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(urljoin(BASE_URL, relative), ref)
            if urlparse(absolute).netloc == urlparse(BASE_URL).netloc:
                queue.append(urlparse(absolute).path.lstrip("/"))
    return saved


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def normalize(output: Path, datasets: dict[str, Any]) -> dict[str, int]:
    root = output / "normalized"
    data = datasets["data.json"]
    image_map = datasets["image-map.json"].get("imageMap", {})
    items: list[dict[str, Any]] = []
    tags: dict[tuple[str, str], dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    retrieved = now_iso()

    for category in data.get("categories", []):
        category_name = category.get("name", "未分类")
        for developer in category.get("developers", []):
            developer_name = developer.get("name", "未知开发商")
            strategy = developer.get("strategy", "")
            for line in developer.get("lines", []):
                line_name = line.get("line", "未命名产品线")
                for project in line.get("projects", []):
                    project_name = project.get("displayName") or project.get("name") or "未命名项目"
                    key = f"{category_name}/{developer_name}/{line_name}/{project.get('name', project_name)}"
                    canonical = urljoin(BASE_URL, "/project/" + "/".join(
                        requests.utils.quote(x, safe="") for x in [category_name, developer_name, line_name, project.get("name", project_name)]
                    ))
                    item_id = stable_id("kmlovemilk", key)
                    raw_tags = [category_name, developer_name, line_name]
                    if project.get("tier"):
                        raw_tags.append(str(project["tier"]))
                    design = project.get("design") or line.get("design") or ""
                    designers = [x.strip() for x in re.split(r"[|｜]", design) if x.strip()]
                    raw_tags.extend(designers)
                    media_urls = image_map.get(key, [])
                    content_parts = [
                        f"项目：{project_name}", f"开发商：{developer_name}", f"企业类型：{category_name}",
                        f"产品线：{line_name}", f"产品档次：{project.get('tier', '')}",
                        f"设计单位：{design}", f"产品线描述：{line.get('description', '')}",
                        f"开发商策略：{strategy}",
                    ]
                    content_text = "\n".join(x for x in content_parts if not x.endswith("："))
                    item = {
                        "id": item_id, "entity_type": "project", "title": project_name,
                        "canonical_url": canonical, "source_site": "kmlovemilk.cn", "source_key": key,
                        "category_path": [category_name, developer_name, line_name],
                        "tags_raw": raw_tags, "tags_normalized": list(dict.fromkeys(raw_tags)),
                        "developer": developer_name, "product_line": line_name,
                        "tier": project.get("tier"), "design": design, "designers": designers,
                        "summary": line.get("description", ""), "content_text": content_text,
                        "media_source_urls": media_urls, "media_ids": [],
                        "provenance": {"source_url": canonical, "retrieved_at": retrieved, "trust_level": "L3", "license_note": "仅作本地研究归档，权利归原作者。"},
                        "quality": {"has_text": bool(content_text), "has_media": bool(media_urls), "needs_review": False},
                    }
                    items.append(item)
                    chunks.append({
                        "chunk_id": stable_id(item_id, "0", content_text), "item_id": item_id,
                        "title": project_name, "section_heading": "项目总览", "text": content_text,
                        "canonical_url": canonical, "category_path": item["category_path"],
                        "tags": item["tags_normalized"], "media_source_urls": media_urls,
                        "source": "kmlovemilk", "content_hash": hashlib.sha256(content_text.encode()).hexdigest(),
                    })
                    for tag_type, name in [("category", category_name), ("developer", developer_name), ("product_line", line_name), ("tier", str(project.get("tier", "")))] + [("designer", x) for x in designers]:
                        if not name:
                            continue
                        tag_id = stable_id(tag_type, name)
                        tags.setdefault((tag_type, name), {"tag_id": tag_id, "name_raw": name, "name_normalized": name, "tag_type": tag_type, "item_count": 0})
                        tags[(tag_type, name)]["item_count"] += 1
                        edges.append({"item_id": item_id, "tag_id": tag_id, "tag_type": tag_type})

    write_jsonl(root / "items.jsonl", items)
    write_jsonl(root / "tags.jsonl", list(tags.values()))
    write_jsonl(root / "item_tag_edges.jsonl", edges)
    write_jsonl(output / "indexes" / "chunks.jsonl", chunks)
    json_dump(root / "catalog.json", {"generated_at": retrieved, "items": items})
    return {"items": len(items), "tags": len(tags), "tag_edges": len(edges), "chunks": len(chunks)}


def collect_urls(datasets: dict[str, Any], frontend_root: Path) -> tuple[list[str], dict[str, list[str]]]:
    owners: dict[str, list[str]] = {}
    image_map = datasets["image-map.json"].get("imageMap", {})
    for owner, urls in image_map.items():
        for url in urls:
            owners.setdefault(url, []).append(owner)

    for path in frontend_root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 20_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for url in URL_RE.findall(text):
            url = url.rstrip(".,;]")
            ext = Path(urlparse(url).path).suffix.lower()
            if ext in MEDIA_EXTS:
                owners.setdefault(url, []).append(f"frontend:{path.name}")
    return sorted(owners), owners


def download_one(url: str, owners: list[str], media_root: Path, tmp_root: Path) -> dict[str, Any]:
    url_id = hashlib.sha256(url.encode()).hexdigest()
    state_file = tmp_root / f"{url_id}.json"
    if state_file.exists():
        try:
            record = json.loads(state_file.read_text(encoding="utf-8"))
            if record.get("status") == "ok" and (media_root.parent.parent / record["local_path"]).exists():
                record["owners"] = owners
                return record
        except Exception:
            pass
    partial = tmp_root / f"{url_id}.part"
    record: dict[str, Any] = {"source_url": url, "owners": owners, "retrieved_at": now_iso()}
    try:
        response = request(url, stream=True)
        digest = hashlib.sha256()
        total = 0
        with partial.open("wb") as handle:
            for block in response.iter_content(1024 * 1024):
                if not block:
                    continue
                handle.write(block)
                digest.update(block)
                total += len(block)
        sha = digest.hexdigest()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        suffix = Path(urlparse(response.url).path).suffix.lower()
        if suffix not in MEDIA_EXTS:
            suffix = mimetypes.guess_extension(content_type) or ".bin"
        destination = media_root / sha[:2] / f"{sha}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            partial.unlink(missing_ok=True)
        else:
            os.replace(partial, destination)
        record.update({
            "media_id": sha, "sha256": sha, "status": "ok", "bytes": total,
            "mime": content_type, "http_status": response.status_code,
            "final_url": response.url, "local_path": destination.relative_to(media_root.parent.parent).as_posix(),
            "original_name": safe_name(Path(unquote(urlparse(url).path)).name, sha + suffix),
        })
    except Exception as exc:
        partial.unlink(missing_ok=True)
        record.update({"status": "error", "error": str(exc)})
    json_dump(state_file, record)
    return record


def download_media(output: Path, urls: list[str], owners: dict[str, list[str]], workers: int) -> dict[str, int]:
    media_root = output / "media" / "objects"
    tmp_root = output / "crawl_state" / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    tmp_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_one, url, owners[url], media_root, tmp_root): url for url in urls}
        total = len(futures)
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            records.append(future.result())
            if index % 100 == 0 or index == total:
                ok = sum(r.get("status") == "ok" for r in records)
                print(f"media {index}/{total} ok={ok} error={index-ok}", flush=True)
    # 原站部分 image-map URL 将 haochan 误写成 haochain；保留原 URL，自动修复后归档。
    repaired_records: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") != "ok" and "haochainpintuku-" in record["source_url"]:
            original_url = record["source_url"]
            corrected_url = original_url.replace("haochainpintuku-", "haochanpintuku-")
            repaired = download_one(corrected_url, record["owners"], media_root, tmp_root)
            if repaired.get("status") == "ok":
                repaired["resolved_url"] = corrected_url
                repaired["source_url"] = original_url
                repaired["source_url_repaired"] = True
                original_state = tmp_root / f"{hashlib.sha256(original_url.encode()).hexdigest()}.json"
                json_dump(original_state, repaired)
                repaired_records.append(repaired)
                continue
        repaired_records.append(record)
    records = repaired_records
    records.sort(key=lambda x: x["source_url"])
    write_jsonl(output / "normalized" / "media.jsonl", records)
    errors = [r for r in records if r.get("status") != "ok"]
    write_jsonl(output / "errors.jsonl", errors)
    return {"media_urls": len(records), "media_ok": len(records) - len(errors), "media_errors": len(errors), "media_bytes": sum(r.get("bytes", 0) for r in records)}


def attach_media_ids(output: Path) -> None:
    media_by_url: dict[str, str] = {}
    media_file = output / "normalized" / "media.jsonl"
    if not media_file.exists():
        return
    for line in media_file.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("status") == "ok" and row.get("media_id"):
            media_by_url[row["source_url"]] = row["media_id"]
    items_file = output / "normalized" / "items.jsonl"
    items = [json.loads(line) for line in items_file.read_text(encoding="utf-8").splitlines() if line]
    for item in items:
        item["media_ids"] = [media_by_url[url] for url in item.get("media_source_urls", []) if url in media_by_url]
        item["quality"]["localized_media_count"] = len(item["media_ids"])
        item["quality"]["missing_media_count"] = len(item.get("media_source_urls", [])) - len(item["media_ids"])
    write_jsonl(items_file, items)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("Vault/建筑案例内容/kmlovemilk"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = now_iso()

    frontend = mirror_frontend(output)
    datasets: dict[str, Any] = {}
    raw_headers: dict[str, Any] = {}
    for name in JSON_SEEDS:
        body, headers = fetch_seed(output, name)
        datasets[name] = json.loads(body.decode("utf-8-sig"))
        raw_headers[name] = headers
    normalized_counts = normalize(output, datasets)
    urls, owners = collect_urls(datasets, output / "raw" / "site")
    media_counts = {"media_urls": len(urls), "media_ok": 0, "media_errors": 0, "media_bytes": 0}
    if not args.metadata_only:
        media_counts = download_media(output, urls, owners, max(1, args.workers))
        attach_media_ids(output)

    manifest = {
        "schema_version": "1.0", "source": BASE_URL, "started_at": started, "completed_at": now_iso(),
        "user_agent": UA, "scope": "公开静态 JSON、前端资源、图片与文档链接的完整本地化",
        "robots_note": "站点 /robots.txt 返回 SPA 首页，未提供有效 robots 规则。",
        "json_seeds": JSON_SEEDS, "frontend_files": frontend, "headers": raw_headers,
        "counts": {**normalized_counts, **media_counts},
        "paths": {"raw": "raw/", "media": "media/objects/", "items": "normalized/items.jsonl", "chunks": "indexes/chunks.jsonl", "errors": "errors.jsonl"},
        "agent_collection_suggestion": "dds_arch_case_content",
    }
    json_dump(output / "manifest.json", manifest)
    readme = f"""# kmlovemilk 本地知识包\n\n来源：{BASE_URL}\n采集完成：{manifest['completed_at']}\n\n- `raw/site/`：原始 JSON、HTML、JS、CSS 与 PWA 文件\n- `media/objects/`：按 SHA-256 内容寻址的图片/附件\n- `normalized/items.jsonl`：项目实体\n- `normalized/tags.jsonl`：分类、开发商、产品线、档次、设计单位标签\n- `normalized/item_tag_edges.jsonl`：实体—标签关系\n- `normalized/media.jsonl`：源 URL—本地文件—项目归属映射\n- `indexes/chunks.jsonl`：面向 Agent/RAG 的稳定分块\n- `manifest.json`：范围、数量、响应头与可复现信息\n- `errors.jsonl`：失败资源，重跑同一命令可断点补齐\n\n本地文件不依赖原站在线资源。内容仅用于内部研究，版权及相关权利归原作者。\n"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    return 0 if media_counts["media_errors"] == 0 or args.metadata_only else 2


if __name__ == "__main__":
    raise SystemExit(main())

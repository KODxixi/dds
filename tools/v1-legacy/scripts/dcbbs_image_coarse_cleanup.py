from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path("Vault/DCBBS").resolve()
DB = ROOT / "normalized" / "dcbbs.sqlite3"
OUT = ROOT / "image_coarse_summary.html"


IMAGE_TABLES = {
    "preview_files": ("doc_id", "preview_index"),
    "resource_media_files": ("doc_id", "media_index"),
    "news_media_files": ("article_id", "media_index"),
}


def human_size(value: int | None) -> str:
    if not value:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def safe_unlink(relative_path: str | None) -> bool:
    if not relative_path:
        return False
    target = (ROOT / relative_path).resolve()
    if ROOT not in target.parents and target != ROOT:
        raise RuntimeError(f"refuse to delete outside archive root: {target}")
    if target.exists() and target.is_file():
        target.unlink()
        return True
    return False


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def cleanup_invalid(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for table, keys in IMAGE_TABLES.items():
        invalid = rows(
            conn,
            f"SELECT rowid,local_path FROM {table} WHERE status!='ok'",
        )
        deleted_files = 0
        for row in invalid:
            if safe_unlink(row["local_path"]):
                deleted_files += 1
        conn.execute(f"DELETE FROM {table} WHERE status!='ok'")
        report[table] = {"deleted_rows": len(invalid), "deleted_files": deleted_files}
    conn.commit()
    return report


def table_summary(conn: sqlite3.Connection, table: str) -> dict:
    by_type = Counter()
    by_ext = Counter()
    by_month = Counter()
    by_domain = Counter()
    bucket = Counter()
    total_rows = 0
    total_bytes = 0

    for row in conn.execute(
        f"SELECT source_url,local_path,content_type,byte_count FROM {table} WHERE status='ok'"
    ):
        total_rows += 1
        byte_count = int(row["byte_count"] or 0)
        total_bytes += byte_count
        by_type[row["content_type"] or "unknown"] += 1
        parsed = urlparse(row["source_url"] or "")
        by_domain[parsed.netloc or "unknown"] += 1
        local_path = row["local_path"] or ""
        by_ext[Path(local_path).suffix.lower() or "unknown"] += 1
        parts = Path(local_path).parts
        month = "unknown"
        for i in range(len(parts) - 1):
            if len(parts[i]) == 4 and parts[i].isdigit() and i + 1 < len(parts):
                if len(parts[i + 1]) == 2 and parts[i + 1].isdigit():
                    month = f"{parts[i]}-{parts[i + 1]}"
                    break
        by_month[month] += 1
        if byte_count < 50_000:
            bucket["<50KB"] += 1
        elif byte_count < 200_000:
            bucket["50-200KB"] += 1
        elif byte_count < 1_000_000:
            bucket["200KB-1MB"] += 1
        else:
            bucket[">=1MB"] += 1

    return {
        "rows": total_rows,
        "bytes": total_bytes,
        "by_type": by_type,
        "by_ext": by_ext,
        "by_month": by_month,
        "by_domain": by_domain,
        "bucket": bucket,
    }


def news_article_summary(conn: sqlite3.Connection) -> list[dict]:
    result = []
    for row in conn.execute(
        """
        SELECT n.article_id,n.published_date,n.title,COUNT(*) AS image_count,
               COALESCE(SUM(m.byte_count),0) AS bytes
        FROM news_media_files m
        JOIN news n ON n.article_id=m.article_id
        WHERE m.status='ok'
        GROUP BY n.article_id,n.published_date,n.title
        ORDER BY image_count DESC, bytes DESC
        LIMIT 20
        """
    ):
        result.append(dict(row))
    return result


def directory_sizes() -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for name in ["previews", "resource_media", "news_media", "external_news_media", "open_sources"]:
        base = ROOT / name
        files = 0
        size = 0
        if base.exists():
            for path in base.rglob("*"):
                if path.is_file():
                    files += 1
                    size += path.stat().st_size
        result[name] = (files, size)
    return result


def counter_rows(counter: Counter, limit: int = 12) -> str:
    body = []
    for key, count in counter.most_common(limit):
        body.append(f"<tr><td><code>{html_escape(str(key))}</code></td><td>{count:,}</td></tr>")
    return "\n".join(body) or "<tr><td>无</td><td>0</td></tr>"


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(cleanup: dict, summaries: dict, top_articles: list[dict], dirs: dict) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cards = []
    for table, title in [
        ("preview_files", "资源公开预览图"),
        ("resource_media_files", "资源海报/缩略图"),
        ("news_media_files", "新闻内嵌图片"),
    ]:
        s = summaries[table]
        c = cleanup[table]
        cards.append(
            f"""
            <article class="card">
              <div class="label">{title}</div>
              <div class="value">{s['rows']:,}</div>
              <div class="note">有效体积 {human_size(s['bytes'])}；已删无效记录 {c['deleted_rows']:,} 条，残留文件 {c['deleted_files']:,} 个</div>
            </article>
            """
        )

    table_blocks = []
    for table, title in [
        ("preview_files", "资源公开预览图"),
        ("resource_media_files", "资源海报/缩略图"),
        ("news_media_files", "新闻内嵌图片"),
    ]:
        s = summaries[table]
        table_blocks.append(
            f"""
            <section>
              <h2>{title}</h2>
              <div class="grid">
                <div><h3>格式</h3><table>{counter_rows(s['by_type'])}</table></div>
                <div><h3>扩展名</h3><table>{counter_rows(s['by_ext'])}</table></div>
                <div><h3>月份</h3><table>{counter_rows(s['by_month'])}</table></div>
                <div><h3>体积桶</h3><table>{counter_rows(s['bucket'])}</table></div>
              </div>
            </section>
            """
        )

    dir_rows = "\n".join(
        f"<tr><td><code>{name}/</code></td><td>{files:,}</td><td>{human_size(size)}</td></tr>"
        for name, (files, size) in dirs.items()
    )
    article_rows = "\n".join(
        f"<tr><td><code>{row['article_id']}</code></td><td>{html_escape(row['published_date'])}</td>"
        f"<td>{html_escape(row['title'][:80])}</td><td>{row['image_count']:,}</td><td>{human_size(row['bytes'])}</td></tr>"
        for row in top_articles
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DCBBS 图片粗颗粒解析</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0f1117; --panel:#171b24; --line:#354054; --text:#e7eaf0; --muted:#9aa4b2; --ok:#57d68d; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.5; }}
    main {{ max-width:1320px; margin:0 auto; padding:30px 26px 48px; }}
    h1 {{ margin:0 0 8px; font-size:28px; }}
    h2 {{ margin:0 0 14px; font-size:18px; }}
    h3 {{ margin:0 0 8px; color:var(--muted); font-size:13px; }}
    .subtitle {{ margin:0 0 22px; color:var(--muted); }}
    .cards {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-bottom:16px; }}
    .card, section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:15px; }}
    .label {{ color:var(--muted); font-size:12px; }}
    .value {{ font-size:28px; font-weight:750; margin:5px 0; }}
    .note {{ color:var(--muted); font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    section {{ margin-top:14px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    td, th {{ border-bottom:1px solid var(--line); padding:7px 8px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-weight:600; }}
    code {{ color:#d6e2ff; background:#111622; border:1px solid #2b3445; border-radius:5px; padding:1px 5px; font-family:"Cascadia Mono",Consolas,monospace; font-size:12px; }}
    .ok {{ color:var(--ok); }}
    @media (max-width:1000px) {{ .cards,.grid {{ grid-template-columns:1fr; }} main {{ padding:22px 16px 36px; }} }}
  </style>
</head>
<body>
<main>
  <h1>DCBBS 图片粗颗粒解析</h1>
  <p class="subtitle">生成时间：{generated} Asia/Shanghai。规则：只统计 <span class="ok">status=ok</span> 的有效图片；无效图片记录已从数据库删除，若有本地残留文件也已删除。</p>
  <div class="cards">{''.join(cards)}</div>
  <section>
    <h2>图片目录体积</h2>
    <table><thead><tr><th>目录</th><th>文件数</th><th>体积</th></tr></thead><tbody>{dir_rows}</tbody></table>
  </section>
  {''.join(table_blocks)}
  <section>
    <h2>新闻图片最多的文章 Top 20</h2>
    <table><thead><tr><th>ID</th><th>日期</th><th>标题</th><th>图片数</th><th>体积</th></tr></thead><tbody>{article_rows}</tbody></table>
  </section>
</main>
</body>
</html>
"""


def main() -> None:
    if not DB.exists():
        raise SystemExit(f"database not found: {DB}")
    conn = sqlite3.connect(DB, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    cleanup = cleanup_invalid(conn)
    summaries = {table: table_summary(conn, table) for table in IMAGE_TABLES}
    top_articles = news_article_summary(conn)
    dirs = directory_sizes()
    OUT.write_text(render_html(cleanup, summaries, top_articles, dirs), encoding="utf-8")
    print(json.dumps({"cleanup": cleanup, "output": str(OUT)}, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()

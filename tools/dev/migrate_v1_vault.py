"""Build the compact DDS V2 data layout from the read-only V1 Vault.

The migration is copy-only: it never mutates or deletes the V1 source. Outputs
are built in a staging directory and promoted only after validation succeeds.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Iterable

import duckdb


LISTING_DUPLICATE_COLUMN = "建筑类型.1"
LISTING_CANONICAL_COLUMN = "建筑类型_补充"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _build_listings(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    count = 0
    try:
        for source_path in sorted(source.glob("*.parquet")):
            columns = [
                row[0]
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(source_path)}')"
                ).fetchall()
            ]
            selections = []
            for column in columns:
                if column == LISTING_DUPLICATE_COLUMN:
                    selections.append(
                        f"{_quoted(column)} AS {_quoted(LISTING_CANONICAL_COLUMN)}"
                    )
                else:
                    selections.append(_quoted(column))
            target_path = destination / source_path.name
            connection.execute(
                f"COPY (SELECT {', '.join(selections)} "
                f"FROM read_parquet('{_sql_path(source_path)}')) "
                f"TO '{_sql_path(target_path)}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            count += 1
    finally:
        connection.close()
    return count


def _copy_parquets(source: Path, destination: Path) -> int:
    count = 0
    for source_path in sorted(source.glob("*.parquet")):
        _copy_file(source_path, destination / source_path.name)
        count += 1
    return count


def _copy_purchased(source: Path, destination: Path) -> int:
    count = 0
    for source_path in sorted(source.glob("*.csv")):
        _copy_file(source_path, destination / source_path.name)
        count += 1
    _copy_file(source / "_manifest.md", destination / "README.md")
    return count


def _copy_architecture_knowledge(source: Path, destination: Path) -> dict[str, int]:
    copied: dict[str, int] = {}
    for directory in ("normalized", "indexes"):
        count = 0
        for source_path in sorted((source / directory).glob("*.jsonl")):
            _copy_file(source_path, destination / directory / source_path.name)
            count += 1
        copied[directory] = count
    for name in ("manifest.json", "README.md", "QA_REPORT.md", "errors.jsonl"):
        _copy_file(source / name, destination / name)
    return copied


def _write_jsonl_gz(
    connection: sqlite3.Connection,
    query: str,
    destination: Path,
    *,
    batch_size: int = 1_000,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    cursor = connection.execute(query)
    columns = [item[0] for item in cursor.description]
    count = 0
    with gzip.open(destination, "wt", encoding="utf-8", newline="\n") as handle:
        while rows := cursor.fetchmany(batch_size):
            for row in rows:
                handle.write(
                    json.dumps(
                        dict(zip(columns, row)),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                count += 1
    return count


def _export_dcbbs(source: Path, destination: Path) -> dict[str, Any]:
    database = source / "normalized" / "dcbbs.sqlite3"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"DCBBS SQLite quick_check failed: {quick_check}")
        news_count = _write_jsonl_gz(
            connection,
            """
            SELECT article_id, source_url, title, published_at, published_date,
                   category, author, author_url, views, summary, keywords_json,
                   body_text, rights_notice, usage_scope, fetched_at
            FROM news
            WHERE trim(coalesce(title, '')) <> ''
              AND trim(coalesce(body_text, '')) <> ''
            ORDER BY published_date, article_id
            """,
            destination / "news.jsonl.gz",
        )
        resource_count = _write_jsonl_gz(
            connection,
            """
            SELECT doc_id, source_url, mobile_url, title, normalized_title,
                   upload_date, document_format, page_count, file_size, cost,
                   uploader, uploader_url, categories_json, keywords_json,
                   description, preview_text, preview_public, rights_notice,
                   usage_scope, fetched_at
            FROM resources
            WHERE trim(coalesce(title, '')) <> ''
            ORDER BY upload_date, doc_id
            """,
            destination / "resources.jsonl.gz",
        )
        match_count = _write_jsonl_gz(
            connection,
            """
            SELECT doc_id, source_url, publisher, match_type, evidence,
                   source_scope, checked_at
            FROM open_source_matches
            ORDER BY doc_id, source_url
            """,
            destination / "open_source_matches.jsonl.gz",
        )
        statuses = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT status, count(*) FROM crawl_items GROUP BY status"
            )
        }
    finally:
        connection.close()
    return {
        "quick_check": quick_check,
        "news": news_count,
        "resources": resource_count,
        "open_source_matches": match_count,
        "crawl_status": statuses,
        "excluded": [
            "raw HTML",
            "preview images",
            "news images",
            "resource media",
            "body HTML",
            "image/link URL arrays",
            "crawler logs and historical manifests",
        ],
        "access_level": "restricted/internal-research",
    }


def _iter_files(root: Path) -> Iterable[Path]:
    return (path for path in root.rglob("*") if path.is_file())


def _validate_and_manifest(staging: Path, source: Path) -> dict[str, Any]:
    files = []
    for path in sorted(_iter_files(staging)):
        files.append(
            {
                "path": path.relative_to(staging).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "source_root": str(source),
        "migration_mode": "copy-only-curated",
        "source_deleted": False,
        "files": files,
        "total_files": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
    }
    manifest_path = staging / "manifests" / "migration-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def migrate(source: Path, data_root: Path) -> dict[str, Any]:
    source = source.resolve()
    data_root = data_root.resolve()
    staging = data_root / ".v1-migration-stage"
    targets = [data_root / name for name in ("raw", "curated", "knowledge", "manifests")]
    if not source.is_dir():
        raise FileNotFoundError(source)
    conflicts = [path for path in targets if path.exists()]
    if conflicts:
        raise FileExistsError(f"migration targets already exist: {conflicts}")
    if staging.exists():
        raise FileExistsError(f"staging directory already exists: {staging}")
    staging.mkdir(parents=True)
    try:
        result: dict[str, Any] = {
            "purchased_files": _copy_purchased(
                source / "_purchased", staging / "raw" / "purchased"
            ),
            "listing_files": _build_listings(
                source / "2026新楼盘", staging / "curated" / "listings"
            ),
            "transaction_files": _copy_parquets(
                source / "成交数据", staging / "curated" / "transactions"
            ),
            "macro_files": _copy_parquets(
                source / "宏观数据", staging / "curated" / "macro"
            ),
            "land_files": _copy_parquets(
                source / "土地数据", staging / "curated" / "land"
            ),
            "architecture": _copy_architecture_knowledge(
                source / "建筑案例内容" / "kmlovemilk",
                staging / "knowledge" / "architecture-cases",
            ),
            "dcbbs": _export_dcbbs(
                source / "DCBBS", staging / "knowledge" / "dcbbs"
            ),
        }
        for name, source_name in (
            ("v1-root-manifest.csv", "_manifest.csv"),
            ("v1-sources.csv", "_sources.csv"),
        ):
            _copy_file(source / source_name, staging / "manifests" / name)
        manifest = _validate_and_manifest(staging, source)
        result["manifest_files"] = manifest["total_files"]
        result["manifest_bytes"] = manifest["total_bytes"]
        for target in targets:
            staged = staging / target.name
            if staged.exists():
                staged.replace(target)
        staging.rmdir()
        return result
    except Exception:
        # Keep staging for diagnosis and resumability; never touch V1.
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    result = migrate(args.source, args.data_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

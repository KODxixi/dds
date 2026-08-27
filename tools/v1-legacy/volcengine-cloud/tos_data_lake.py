#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS TOS 数据湖管理器。

管理 dds-data-lake Bucket 的四层结构（raw/normalized/vault/governance）
和 6 条数据管道的元数据。

用法:
    python tos_data_lake.py --init          # 初始化数据湖元数据
    python tos_data_lake.py --list           # 列出数据湖各层对象
    python tos_data_lake.py --list --layer raw  # 只列出 raw 层
    python tos_data_lake.py --pipeline land  # 列出某管道的所有文件
    python tos_data_lake.py --state land     # 查询管道状态
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import tos_client

# ── 6 条数据管道元数据 ──────────────────────────────────────────
PIPELINES: list[dict[str, Any]] = [
    {
        "name": "transactions",
        "label": "管道一·网签成交",
        "tos_prefix": "raw/transactions/",
        "cron": "0 2 * * *",
        "frequency": "每日 02:00",
        "source": "各城市住建局网签公示",
        "output_fields": ["成交均价", "DOM", "CMA可比实例", "SP-LP%", "Pending", "吸纳率", "带看量"],
        "local_scripts": ["scripts/scrape_hangzhou_deals.py", "scripts/scrape_sanya_deals.py"],
        "status": "partially_ready",
    },
    {
        "name": "land",
        "label": "管道二·土地出让",
        "tos_prefix": "raw/land/",
        "cron": "0 3 * * 1",
        "frequency": "每周一 03:00",
        "source": "LandChina API + PDF 解析",
        "output_fields": ["宗地编号", "楼面价", "溢价率", "竞得人"],
        "local_scripts": ["scripts/scrape_landchina_year.py", "scripts/scrape_hangzhou.py", "scripts/scrape_sanya.py"],
        "status": "ready",
    },
    {
        "name": "construction_cost",
        "label": "管道三·建安成本",
        "tos_prefix": "raw/construction_cost/",
        "cron": "0 4 1 * *",
        "frequency": "每月 04:00",
        "source": "各省住建厅工程造价信息",
        "output_fields": ["主体成本", "装饰成本", "安装成本", "室外工程成本"],
        "local_scripts": ["scripts/ingest_purchased.py"],
        "status": "building",
    },
    {
        "name": "developer_credit",
        "label": "管道四·开发商信用",
        "tos_prefix": "raw/developer_credit/",
        "cron": "0 5 1 * *",
        "frequency": "每月 05:00",
        "source": "住建部信用档案+法院失信+房协评级",
        "output_fields": ["信用评级", "交付率", "延期率", "投诉"],
        "local_scripts": ["scripts/ingest_purchased.py"],
        "status": "building",
    },
    {
        "name": "rental",
        "label": "管道五·租赁市场",
        "tos_prefix": "raw/rental/",
        "cron": "0 6 * * 1",
        "frequency": "每周一 06:00",
        "source": "贝壳租房/自如/58同城",
        "output_fields": ["区域租金", "空置率", "GRM", "cap_rate"],
        "local_scripts": [],
        "status": "planned",
    },
    {
        "name": "policy",
        "label": "管道六·政策与金融",
        "tos_prefix": "raw/policy/",
        "cron": "0 7 * * 1",
        "frequency": "每周一 07:00",
        "source": "央行LPR/各城市住建局限购政策",
        "output_fields": ["LPR", "首付比例", "限购摘要"],
        "local_scripts": [],
        "status": "planned",
    },
]

PIPELINE_BY_NAME: dict[str, dict] = {p["name"]: p for p in PIPELINES}

# 数据湖四层
LAYERS = ["raw", "normalized", "vault", "governance"]

# Schema 版本
SCHEMA_VERSION = "2.0"


def _init_env() -> None:
    """加载 .env.volcengine 环境变量并禁用代理。"""
    env_file = Path(__file__).parent / ".env.volcengine"
    tos_client.load_dotenv(env_file)
    tos_client.disable_proxy()


def _get_client_and_bucket():
    """获取 TOS 客户端和 bucket 名称。"""
    _init_env()
    cfg = tos_client.get_tos_config()
    bucket = cfg["bucket"]
    if not bucket:
        print("ERROR: DDS_TOS_BUCKET 未设置", file=sys.stderr)
        sys.exit(1)
    client = tos_client.get_thread_local_client()
    return client, bucket


def _list_tos_objects(prefix: str, max_items: int = 5000) -> list[dict[str, Any]]:
    """分页列出 TOS 对象。返回 [{key, size, last_modified}, ...]。"""
    client, bucket = _get_client_and_bucket()
    objects: list[dict[str, Any]] = []
    marker = ""
    while True:
        resp = client.list_objects(bucket, prefix=prefix, marker=marker, max_keys=200)
        contents = getattr(resp, "contents", None) or []
        for obj in contents:
            objects.append({
                "key": getattr(obj, "key", ""),
                "size": getattr(obj, "size", 0) or getattr(obj, "content_length", 0) or 0,
                "last_modified": str(getattr(obj, "last_modified", "")),
            })
            if len(objects) >= max_items:
                return objects
        is_truncated = getattr(resp, "is_truncated", False)
        marker = getattr(resp, "next_marker", "") or ""
        if not is_truncated or not marker:
            break
    return objects


def list_layers(layer: str | None = None) -> dict[str, list[dict]]:
    """列出数据湖各层（或指定层）的对象。"""
    layers = [layer] if layer else LAYERS
    result: dict[str, list[dict]] = {}
    for l in layers:
        prefix = f"{l}/"
        objs = _list_tos_objects(prefix)
        result[l] = objs
    return result


def list_pipeline_data(pipeline_name: str) -> list[dict]:
    """列出某管道在 TOS 上的所有文件。"""
    if pipeline_name not in PIPELINE_BY_NAME:
        available = list(PIPELINE_BY_NAME.keys())
        print(f"ERROR: 未知管道 '{pipeline_name}'，可选: {available}", file=sys.stderr)
        return []
    p = PIPELINE_BY_NAME[pipeline_name]
    return _list_tos_objects(p["tos_prefix"])


def init_data_lake() -> dict[str, Any]:
    """初始化数据湖元数据。

    写入以下 _meta/ 文件：
    - schema_version.json: Schema 版本与数据湖结构描述
    - pipeline_state.json: 6 条管道的初始状态
    - data_lineage.json: 数据血缘追踪记录（空）
    """
    client, bucket = _get_client_and_bucket()
    now = datetime.now().isoformat(timespec="seconds")

    # ── schema_version.json ──
    schema_version = {
        "schema": "DDS-TOS-DataLake",
        "version": SCHEMA_VERSION,
        "updated": now,
        "description": "DDS TOS 数据湖 Schema 版本",
        "layers": LAYERS,
        "pipelines": [p["name"] for p in PIPELINES],
        "pipeline_labels": {p["name"]: p["label"] for p in PIPELINES},
    }

    # ── pipeline_state.json ──
    pipeline_state: dict[str, Any] = {
        "updated": now,
        "pipelines": {},
    }
    for p in PIPELINES:
        pipeline_state["pipelines"][p["name"]] = {
            "label": p["label"],
            "last_run": None,
            "file_count": 0,
            "checkpoint": None,
            "status": "initialized",
            "cron": p["cron"],
            "frequency": p["frequency"],
            "tos_prefix": p["tos_prefix"],
        }

    # ── data_lineage.json ──
    data_lineage = {
        "schema": "DDS-DataLineage",
        "version": "1.0",
        "updated": now,
        "lineage": [],
        "description": "数据血缘追踪记录，每条记录包含 source → transform → target",
    }

    meta_files = {
        "_meta/schema_version.json": schema_version,
        "_meta/pipeline_state.json": pipeline_state,
        "_meta/data_lineage.json": data_lineage,
    }

    uploaded: list[str] = []
    for key, data in meta_files.items():
        content = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            client.put_object(
                bucket, key,
                content=content.encode("utf-8"),
                content_type="application/json",
            )
            uploaded.append(key)
            print(f"INIT  {key}")
        except Exception as exc:
            print(f"ERROR writing {key}: {type(exc).__name__}: {exc}", file=sys.stderr)

    return {"initialized": uploaded, "timestamp": now, "bucket": bucket}


def pipeline_state(pipeline_name: str) -> dict[str, Any]:
    """获取管道状态（最后运行时间、文件数、checkpoint）。

    从 TOS _meta/pipeline_state.json 读取记录状态，
    同时实际列出 TOS 上该管道路径下的文件以获得真实文件数。
    """
    if pipeline_name not in PIPELINE_BY_NAME:
        return {"error": f"未知管道 '{pipeline_name}'，可选: {list(PIPELINE_BY_NAME.keys())}"}

    p = PIPELINE_BY_NAME[pipeline_name]

    # 从 TOS 读取 pipeline_state.json
    client, bucket = _get_client_and_bucket()
    state_key = "_meta/pipeline_state.json"
    try:
        resp = client.get_object(bucket, state_key)
        content = resp.read().decode("utf-8")
        state_data = json.loads(content)
    except Exception:
        state_data = {"pipelines": {}}

    p_state = state_data.get("pipelines", {}).get(pipeline_name, {})
    # 补充管道元数据
    p_state["pipeline"] = {
        "name": p["name"],
        "label": p["label"],
        "cron": p["cron"],
        "frequency": p["frequency"],
        "tos_prefix": p["tos_prefix"],
        "source": p["source"],
        "output_fields": p["output_fields"],
        "local_scripts": p["local_scripts"],
        "status": p["status"],
    }
    # 实际列出 TOS 上的文件
    files = list_pipeline_data(pipeline_name)
    p_state["actual_file_count"] = len(files)
    p_state["files"] = [
        {"key": f["key"], "size": f["size"]} for f in files[:50]
    ]
    return p_state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS TOS 数据湖管理器 — 管理四层结构与 6 条管道元数据"
    )
    parser.add_argument("--init", action="store_true", help="初始化数据湖元数据（写 _meta/ 三个 JSON）")
    parser.add_argument("--list", action="store_true", help="列出数据湖各层对象")
    parser.add_argument("--layer", help="只列出指定层 (raw/normalized/vault/governance)")
    parser.add_argument("--pipeline", help="列出某管道在 TOS 上的所有文件")
    parser.add_argument("--state", help="查询管道状态")
    args = parser.parse_args()

    if args.init:
        result = init_data_lake()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.list:
        layers_data = list_layers(args.layer)
        for layer_name, objs in layers_data.items():
            print(f"\n{'=' * 60}")
            print(f"Layer: {layer_name}/  ({len(objs)} objects)")
            print(f"{'=' * 60}")
            for obj in objs[:20]:
                size_kb = obj.get("size", 0) / 1024
                print(f"  {obj['key']}  ({size_kb:.1f} KB)")
            if len(objs) > 20:
                print(f"  ... and {len(objs) - 20} more")
        return 0

    if args.pipeline:
        files = list_pipeline_data(args.pipeline)
        p = PIPELINE_BY_NAME.get(args.pipeline, {})
        print(f"\nPipeline '{args.pipeline}' ({p.get('label', '?')}): {len(files)} files")
        print(f"{'=' * 60}")
        print(f"  TOS 路径: {p.get('tos_prefix', '?')}")
        print(f"  数据源:   {p.get('source', '?')}")
        print(f"  输出字段: {', '.join(p.get('output_fields', []))}")
        print(f"{'=' * 60}")
        for f in files[:30]:
            size_kb = f.get("size", 0) / 1024
            print(f"  {f['key']}  ({size_kb:.1f} KB)")
        if len(files) > 30:
            print(f"  ... and {len(files) - 30} more")
        return 0

    if args.state:
        state = pipeline_state(args.state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0

    # 默认：打印帮助 + 管道列表
    parser.print_help()
    print("\n可用管道:")
    for p in PIPELINES:
        print(f"  {p['name']:20s}  {p['label']}  ({p['frequency']})  [{p['status']}]")
    print("\n数据湖四层:", ", ".join(LAYERS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

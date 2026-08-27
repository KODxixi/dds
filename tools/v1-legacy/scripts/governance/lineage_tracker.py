# -*- coding: utf-8 -*-
"""
OpenLineage 风格数据血缘追踪器
===============================
功能：运行时自动捕获数据流水线的输入输出关系，构建血缘 DAG。

核心组件
--------
- **LineageGraph**：节点（Dataset/Transform/Report）+ 边（转换关系）的 DAG 表示
- **RunContext**：每次运行的上下文（run_id、timestamp、参数）
- **LineageTracker**：上下文管理器 + 装饰器 + 手动标记，零侵入性

节点类型
--------
- ``Dataset``：数据集节点（CSV/Parquet/JSON/数据库表）
- ``Transform``：转换节点（脚本/函数/管道）
- ``Report``：输出节点（报告/决策书）

输出格式
--------
- JSON 格式的血缘事件（对齐 OpenLineage run/facet/job/dataset 结构）
- Marimo 兼容的 CSV 血缘表
- Mermaid 流程图源码（可视化）

设计原则
--------
- 纯标准库实现，不依赖 openlineage 包
- 零侵入性：装饰器和上下文管理器不影响原函数行为
- 默认启用，可通过 ``DDS_LINEAGE_ENABLED=0`` 关闭
- 血缘数据写入 ``data_out/lineage/`` 目录
- 与 ``build_provenance.py``（T3 静态溯源）互补：本模块管运行时，build_provenance 管静态

CLI
---
    # 查看血缘图（指定城市）
    python scripts/governance/lineage_tracker.py --graph --city 三亚

    # 追踪脚本执行并记录血缘
    python scripts/governance/lineage_tracker.py --trace scripts/report_parcel.py --args "--city 三亚 --district 海棠区"

    # 可视化血缘图输出为 HTML
    python scripts/governance/lineage_tracker.py --visualize --output lineage.html

    # 自测
    python scripts/governance/lineage_tracker.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

# ═══════════════════════════════════════════════════════════════════════
# 项目路径
# ═══════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LINEAGE_DIR = PROJECT_ROOT / "data_out" / "lineage"
LINEAGE_DIR.mkdir(parents=True, exist_ok=True)

# 环境变量：是否启用血缘追踪
LINEAGE_ENABLED = os.environ.get("DDS_LINEAGE_ENABLED", "1") != "0"

# 线程本地存储：当前活跃的 tracker 实例
_LOCAL = threading.local()

# ═══════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    """返回 ISO 8601 格式的当前 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_hash(s: str, length: int = 8) -> str:
    """对字符串做 SHA-256 并截取前 length 位。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:length]


def _generate_run_id() -> str:
    """生成唯一 run_id（UUID4 + 时间戳前缀）。"""
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:12]}"


def _fingerprint_file(path: Path) -> Optional[Dict[str, Any]]:
    """
    计算文件指纹（文件大小 + 前 4KB 的 SHA-256 + 修改时间）。
    不存在的文件返回 None。
    """
    if not path.exists():
        return None
    try:
        stat = path.stat()
        with open(path, "rb") as f:
            head = f.read(4096)
        return {
            "size_bytes": stat.st_size,
            "sha256_head": hashlib.sha256(head).hexdigest(),
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        }
    except Exception:
        return None


def _detect_format(path: Path) -> str:
    """根据文件扩展名推断数据格式。"""
    suffix = path.suffix.lower()
    mapping = {
        ".csv": "CSV",
        ".parquet": "PARQUET",
        ".json": "JSON",
        ".jsonl": "JSONL",
        ".xlsx": "EXCEL",
        ".xls": "EXCEL",
        ".db": "DUCKDB",
        ".sqlite": "SQLITE",
        ".md": "MARKDOWN",
        ".html": "HTML",
        ".pdf": "PDF",
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
    }
    return mapping.get(suffix, "UNKNOWN")


# ═══════════════════════════════════════════════════════════════════════
# 节点类型
# ═══════════════════════════════════════════════════════════════════════

class LineageNode:
    """血缘图中的节点基类。"""

    def __init__(
        self,
        node_id: str,
        name: str,
        node_type: str,
        *,
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.node_id = node_id
        self.name = name
        self.node_type = node_type  # "dataset" | "transform" | "report"
        self.source = source
        self.source_url = source_url
        self.source_note = source_note
        self.metadata = metadata or {}
        self.created_at = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "node_type": self.node_type,
            "source": self.source,
            "source_url": self.source_url,
            "source_note": self.source_note,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.node_id[:12]}..., {self.node_type})>"


class DatasetNode(LineageNode):
    """数据集节点：CSV/Parquet/JSON/数据库表。"""

    def __init__(
        self,
        name: str,
        path: Union[str, Path],
        *,
        format_: Optional[str] = None,
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        row_count: Optional[int] = None,
        schema_columns: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        path = Path(path)
        if format_ is None:
            format_ = _detect_format(path)
        node_id = _short_hash(f"dataset:{str(path)}")
        super().__init__(
            node_id=node_id,
            name=name,
            node_type="dataset",
            source=source,
            source_url=source_url,
            source_note=source_note,
            metadata=metadata,
        )
        self.path = path
        self.format = format_
        self.row_count = row_count
        self.schema_columns = schema_columns or []
        self.fingerprint = _fingerprint_file(path)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "path": str(self.path),
            "format": self.format,
            "row_count": self.row_count,
            "schema_columns": self.schema_columns,
            "fingerprint": self.fingerprint,
        })
        return d


class TransformNode(LineageNode):
    """转换节点：脚本/函数/管道。"""

    def __init__(
        self,
        name: str,
        *,
        script_path: Optional[str] = None,
        function_name: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        runtime_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        node_id = _short_hash(f"transform:{name}:{_now_iso()}")
        super().__init__(
            node_id=node_id,
            name=name,
            node_type="transform",
            source=source,
            source_url=source_url,
            source_note=source_note,
            metadata=metadata,
        )
        self.script_path = script_path
        self.function_name = function_name
        self.args = args or {}
        self.runtime_seconds = runtime_seconds

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "script_path": self.script_path,
            "function_name": self.function_name,
            "args": self.args,
            "runtime_seconds": self.runtime_seconds,
        })
        return d


class ReportNode(LineageNode):
    """输出节点：报告/决策书/可视化。"""

    def __init__(
        self,
        name: str,
        path: Union[str, Path],
        *,
        report_type: str = "unknown",
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        path = Path(path)
        node_id = _short_hash(f"report:{str(path)}:{_now_iso()}")
        super().__init__(
            node_id=node_id,
            name=name,
            node_type="report",
            source=source,
            source_url=source_url,
            source_note=source_note,
            metadata=metadata,
        )
        self.path = path
        self.report_type = report_type

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "path": str(self.path),
            "report_type": self.report_type,
        })
        return d


# ═══════════════════════════════════════════════════════════════════════
# 边（转换关系）
# ═══════════════════════════════════════════════════════════════════════

class LineageEdge:
    """血缘图中的边：从源节点到目标节点的转换关系。"""

    def __init__(
        self,
        edge_id: str,
        source_id: str,
        target_id: str,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.edge_id = edge_id
        self.source_id = source_id
        self.target_id = target_id
        self.label = label
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "label": self.label,
            "metadata": self.metadata,
        }


# ═══════════════════════════════════════════════════════════════════════
# LineageGraph — DAG 表示
# ═══════════════════════════════════════════════════════════════════════

class LineageGraph:
    """血缘 DAG：节点集合 + 边集合 + 拓扑排序。"""

    def __init__(self, name: str = "default"):
        self.name = name
        self.nodes: Dict[str, LineageNode] = {}
        self.edges: List[LineageEdge] = []
        self._next_edge_id = 0

    # ── 节点操作 ────────────────────────────────────────────────────

    def add_node(self, node: LineageNode) -> LineageNode:
        """添加节点（幂等：同 node_id 已存在则跳过）。"""
        if node.node_id not in self.nodes:
            self.nodes[node.node_id] = node
        return self.nodes[node.node_id]

    def get_node(self, node_id: str) -> Optional[LineageNode]:
        """按 node_id 获取节点。"""
        return self.nodes.get(node_id)

    def find_nodes_by_type(self, node_type: str) -> List[LineageNode]:
        """按类型筛选节点。"""
        return [n for n in self.nodes.values() if n.node_type == node_type]

    def find_dataset_by_path(self, path: Union[str, Path]) -> Optional[DatasetNode]:
        """按路径查找数据集节点。"""
        path_str = str(Path(path))
        for n in self.nodes.values():
            if isinstance(n, DatasetNode) and str(n.path) == path_str:
                return n
        return None

    # ── 边操作 ────────────────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        label: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LineageEdge:
        """添加边（幂等：同 source→target 已存在则跳过）。"""
        # 检查是否已存在相同边
        for e in self.edges:
            if e.source_id == source_id and e.target_id == target_id:
                return e

        edge_id = f"edge_{self._next_edge_id:04d}"
        self._next_edge_id += 1
        edge = LineageEdge(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            label=label,
            metadata=metadata,
        )
        self.edges.append(edge)
        return edge

    def add_read_edge(self, dataset: DatasetNode, transform: TransformNode) -> LineageEdge:
        """添加「读取」边：dataset → transform。"""
        self.add_node(dataset)
        self.add_node(transform)
        return self.add_edge(dataset.node_id, transform.node_id, label="READ")

    def add_write_edge(self, transform: TransformNode, dataset_or_report: LineageNode) -> LineageEdge:
        """添加「写入」边：transform → dataset/report。"""
        self.add_node(transform)
        self.add_node(dataset_or_report)
        return self.add_edge(transform.node_id, dataset_or_report.node_id, label="WRITE")

    # ── 拓扑排序 ──────────────────────────────────────────────────

    def topological_order(self) -> List[LineageNode]:
        """
        返回节点的拓扑排序列表（Kahn 算法）。
        用于确定执行顺序或可视化布局。
        """
        in_degree: Dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: Dict[str, List[str]] = {nid: [] for nid in self.nodes}

        for edge in self.edges:
            if edge.source_id in adj and edge.target_id in in_degree:
                adj[edge.source_id].append(edge.target_id)
                in_degree[edge.target_id] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result: List[LineageNode] = []

        while queue:
            nid = queue.pop(0)
            if nid in self.nodes:
                result.append(self.nodes[nid])
            for neighbor in adj.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    # ── 统计 ──────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """返回图统计信息。"""
        type_counts: Dict[str, int] = {}
        for n in self.nodes.values():
            type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1

        return {
            "name": self.name,
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": type_counts,
            "dataset_nodes": len(self.find_nodes_by_type("dataset")),
            "transform_nodes": len(self.find_nodes_by_type("transform")),
            "report_nodes": len(self.find_nodes_by_type("report")),
        }

    # ── 序列化 ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """导出为字典。"""
        return {
            "name": self.name,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "stats": self.stats(),
        }

    def to_openlineage(self, run_id: str, run_context: Optional[RunContext] = None) -> Dict[str, Any]:
        """
        导出为 OpenLineage 风格的事件单。
        结构对齐 OpenLineage spec: eventType, eventTime, run, job, inputs, outputs.
        """
        event_time = _now_iso()
        run_facets = {}
        if run_context:
            run_facets = {
                "environment-properties": {
                    "environment": run_context.params,
                },
            }

        transforms = self.find_nodes_by_type("transform")
        dataset_nodes = self.find_nodes_by_type("dataset")
        report_nodes = self.find_nodes_by_type("report")

        # 构建 inputs/outputs
        inputs = []
        outputs = []

        for ds in dataset_nodes:
            obj = {
                "namespace": "dds",
                "name": ds.name,
                "facets": {
                    "dataSource": {
                        "name": str(ds.path),
                        "uri": ds.path.resolve().as_uri(),
                    },
                    "schema": {
                        "fields": [{"name": c, "type": "string"} for c in ds.schema_columns],
                    },
                },
            }
            if ds.source:
                obj["facets"]["dataSource"]["source"] = ds.source
            if ds.source_url:
                obj["facets"]["dataSource"]["source_url"] = ds.source_url
            if ds.source_note:
                obj["facets"]["dataSource"]["source_note"] = ds.source_note
            if ds.fingerprint:
                obj["facets"]["dataSource"]["fingerprint"] = ds.fingerprint
            inputs.append(obj)

        for rp in report_nodes:
            outputs.append({
                "namespace": "dds",
                "name": rp.name,
                "facets": {
                    "outputStatistics": {
                        "path": str(rp.path),
                        "reportType": rp.report_type,
                    },
                },
            })

        # 构建 job 信息
        job_name = ",".join([t.name for t in transforms]) if transforms else "unknown"
        job_facets = {}
        if transforms and transforms[0].source:
            job_facets["source"] = transforms[0].source
        if transforms and transforms[0].source_url:
            job_facets["source_url"] = transforms[0].source_url
        if transforms and transforms[0].source_note:
            job_facets["source_note"] = transforms[0].source_note

        return {
            "eventType": "COMPLETE" if transforms else "START",
            "eventTime": event_time,
            "run": {
                "runId": run_id,
                "facets": run_facets,
            },
            "job": {
                "namespace": "dds",
                "name": job_name,
                "facets": job_facets,
            },
            "inputs": inputs,
            "outputs": outputs,
        }

    def to_csv_rows(self) -> List[Dict[str, str]]:
        """
        导出为 Marimo 兼容的 CSV 行列表。
        每行 = 一条边 + 对应的源/目标节点信息。
        """
        rows = []
        for edge in self.edges:
            source_node = self.nodes.get(edge.source_id)
            target_node = self.nodes.get(edge.target_id)
            rows.append({
                "edge_id": edge.edge_id,
                "edge_label": edge.label,
                "source_id": edge.source_id,
                "source_name": source_node.name if source_node else "",
                "source_type": source_node.node_type if source_node else "",
                "source_path": getattr(source_node, "path", "") if isinstance(source_node, (DatasetNode, ReportNode)) else "",
                "source_format": getattr(source_node, "format", "") if isinstance(source_node, DatasetNode) else "",
                "source_source": source_node.source if source_node else "",
                "source_url": source_node.source_url if source_node else "",
                "source_note": source_node.source_note if source_node else "",
                "target_id": edge.target_id,
                "target_name": target_node.name if target_node else "",
                "target_type": target_node.node_type if target_node else "",
                "target_path": getattr(target_node, "path", "") if isinstance(target_node, (DatasetNode, ReportNode)) else "",
                "target_format": getattr(target_node, "format", "") if isinstance(target_node, DatasetNode) else "",
                "target_source": target_node.source if target_node else "",
                "target_url": target_node.source_url if target_node else "",
                "target_note": target_node.source_note if target_node else "",
            })
        return rows

    def to_mermaid(self, direction: str = "LR") -> str:
        """
        导出为 Mermaid flowchart 源码（用于可视化）。
        节点用不同颜色区分类型，边标注 READ/WRITE。
        """
        lines = [f"flowchart {direction}", ""]
        node_id_to_label: Dict[str, str] = {}

        # 定义节点样式
        for nid, node in self.nodes.items():
            # 安全的节点 ID（Mermaid 不支持特殊字符）
            safe_id = f"N{_short_hash(nid, 8)}"
            node_id_to_label[nid] = safe_id
            label = node.name.replace('"', '\\"')

            if node.node_type == "dataset":
                shape = f'{safe_id}["{label}"]'
                lines.append(f"    {shape}:::dataset")
            elif node.node_type == "transform":
                shape = f'{safe_id}["{label}"]'
                lines.append(f"    {shape}:::transform")
            elif node.node_type == "report":
                shape = f'{safe_id}["{label}"]'
                lines.append(f"    {shape}:::report")

            # 添加来源标注作为节点注释
            if node.source:
                src_label = node.source.replace('"', '\\"')
                src_id = f"{safe_id}_SRC"
                lines.append(f'    {src_id}["SOURCE: {src_label}"]:::source')
                lines.append(f"    {safe_id} -.->|来源| {src_id}")

        lines.append("")

        # 绘制边
        for edge in self.edges:
            src_label = node_id_to_label.get(edge.source_id)
            tgt_label = node_id_to_label.get(edge.target_id)
            if src_label and tgt_label:
                edge_style = "-->"
                if edge.label == "READ":
                    edge_style = "-- read -->"
                elif edge.label == "WRITE":
                    edge_style = "-- write -->"
                edge_text = edge.label.replace('"', '\\"') if edge.label else ""
                lines.append(f"    {src_label} {edge_style}|{edge_text}| {tgt_label}")

        # 添加样式定义
        lines.extend([
            "",
            "    classDef dataset fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1",
            "    classDef transform fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#bf360c",
            "    classDef report fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20",
            "    classDef source fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1px,stroke-dasharray:3,color:#4a148c",
        ])

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# RunContext — 每次运行的上下文
# ═══════════════════════════════════════════════════════════════════════

class RunContext:
    """每次血缘追踪运行的上下文。"""

    def __init__(
        self,
        run_id: Optional[str] = None,
        *,
        pipeline_name: str = "",
        city: str = "",
        params: Optional[Dict[str, Any]] = None,
    ):
        self.run_id = run_id or _generate_run_id()
        self.pipeline_name = pipeline_name
        self.city = city
        self.params = params or {}
        self.start_time = _now_iso()
        self.end_time: Optional[str] = None
        self.graph = LineageGraph(name=pipeline_name or "unnamed")
        self._duration: Optional[float] = None

    def mark_complete(self):
        """标记运行完成。"""
        self.end_time = _now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "city": self.city,
            "params": self.params,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self._duration,
        }

    def to_openlineage(self) -> Dict[str, Any]:
        """导出为 OpenLineage 风格事件。"""
        return self.graph.to_openlineage(self.run_id, self)


# ═══════════════════════════════════════════════════════════════════════
# LineageTracker — 核心追踪器（单例）
# ═══════════════════════════════════════════════════════════════════════

class LineageTracker:
    """
    血缘追踪器的全局单例。

    使用方式
    --------
    ::

        # 方式一：上下文管理器（推荐）
        with LineageTracker.run(pipeline_name="决策引擎", city="三亚") as tracker:
            tracker.track_read("Vault/2026新楼盘/新楼盘-三亚.csv")
            result = do_work()
            tracker.track_write("data_out/reports/decision/report.json")

        # 方式二：装饰器
        @track_lineage
        def my_pipeline(city, data):
            ...

        # 方式三：手动标记
        tracker = LineageTracker.get()
        tracker.track_read("path/to/input.csv")
        tracker.track_write("path/to/output.json")

        # 检查是否启用
        if LineageTracker.is_enabled():
            ...
    """

    _instance: Optional[LineageTracker] = None
    _lock = threading.Lock()

    def __init__(self):
        self._active_context: Optional[RunContext] = None
        self._context_stack: List[RunContext] = []
        self._enabled = LINEAGE_ENABLED

    @classmethod
    def get(cls) -> LineageTracker:
        """获取全局单例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def is_enabled(cls) -> bool:
        """检查血缘追踪是否启用。"""
        return cls.get()._enabled

    # ── 上下文管理 ─────────────────────────────────────────────────

    def push_context(self, ctx: RunContext):
        """压入新的运行上下文。"""
        self._context_stack.append(ctx)
        self._active_context = ctx

    def pop_context(self) -> Optional[RunContext]:
        """弹出运行上下文，返回弹出的上下文。"""
        if self._context_stack:
            ctx = self._context_stack.pop()
            if self._context_stack:
                self._active_context = self._context_stack[-1]
            else:
                self._active_context = None
            return ctx
        return None

    @property
    def active_context(self) -> Optional[RunContext]:
        return self._active_context

    @property
    def graph(self) -> Optional[LineageGraph]:
        """当前活跃的图。"""
        return self._active_context.graph if self._active_context else None

    @contextmanager
    def run(
        self,
        pipeline_name: str = "",
        city: str = "",
        params: Optional[Dict[str, Any]] = None,
    ):
        """
        上下文管理器：包裹整个管道运行，自动记录开始/结束。

        用法::

            with tracker.run(pipeline_name="report_parcel", city="三亚") as t:
                t.track_read("input.csv")
                t.track_write("output.json")
        """
        if not self._enabled:
            yield self
            return

        ctx = RunContext(
            pipeline_name=pipeline_name,
            city=city,
            params=params,
        )
        self.push_context(ctx)
        t_start = time.perf_counter()

        try:
            yield self
        except Exception:
            # 即使出错也记录血缘
            ctx.mark_complete()
            ctx._duration = time.perf_counter() - t_start
            self._persist_run(ctx)
            raise
        else:
            ctx.mark_complete()
            ctx._duration = time.perf_counter() - t_start
            self._persist_run(ctx)
        finally:
            self.pop_context()

    # ── 读写标记 ──────────────────────────────────────────────────

    def track_read(
        self,
        path: Union[str, Path],
        *,
        name: Optional[str] = None,
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        transform_name: Optional[str] = None,
        schema_columns: Optional[List[str]] = None,
    ) -> Optional[DatasetNode]:
        """
        标记读取一个数据集文件。

        参数:
            path: 文件路径
            name: 节点名称（默认使用文件名）
            source: 数据来源
            source_url: 来源 URL
            source_note: 来源备注
            transform_name: 关联的转换节点名称
            schema_columns: Schema 列名列表
        """
        if not self._enabled or not self._active_context:
            return None

        path = Path(path)
        name = name or path.stem or path.name
        dataset = DatasetNode(
            name=name,
            path=path,
            source=source,
            source_url=source_url,
            source_note=source_note,
            schema_columns=schema_columns,
        )
        self._active_context.graph.add_node(dataset)

        # 如果指定了 transform_name，创建或获取 transform 节点
        if transform_name:
            transform = self._get_or_create_transform(transform_name)
            self._active_context.graph.add_read_edge(dataset, transform)

        return dataset

    def track_write(
        self,
        path: Union[str, Path],
        *,
        name: Optional[str] = None,
        report_type: str = "unknown",
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        transform_name: Optional[str] = None,
    ) -> Optional[LineageNode]:
        """
        标记写入一个输出文件（报告或数据集）。

        参数:
            path: 文件路径
            name: 节点名称（默认使用文件名）
            report_type: 报告类型（如 "decision", "parcel", "pipeline"）
            source: 数据来源
            source_url: 来源 URL
            source_note: 来源备注
            transform_name: 关联的转换节点名称
        """
        if not self._enabled or not self._active_context:
            return None

        path = Path(path)
        name = name or path.stem or path.name

        # 判断是报告还是数据集
        fmt = _detect_format(path)
        if fmt in ("CSV", "PARQUET", "JSON", "JSONL", "EXCEL", "DUCKDB", "SQLITE"):
            node: LineageNode = DatasetNode(
                name=name,
                path=path,
                format_=fmt,
                source=source,
                source_url=source_url,
                source_note=source_note,
            )
        else:
            node = ReportNode(
                name=name,
                path=path,
                report_type=report_type,
                source=source,
                source_url=source_url,
                source_note=source_note,
            )

        self._active_context.graph.add_node(node)

        # 如果指定了 transform_name，创建或获取 transform 节点
        if transform_name:
            transform = self._get_or_create_transform(transform_name)
            self._active_context.graph.add_write_edge(transform, node)

        return node

    def track_transform(
        self,
        name: str,
        *,
        script_path: Optional[str] = None,
        function_name: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        source: str = "",
        source_url: str = "",
        source_note: str = "",
        runtime_seconds: Optional[float] = None,
    ) -> Optional[TransformNode]:
        """
        标记一个转换节点。

        参数:
            name: 转换名称（如脚本名或函数名）
            script_path: 脚本路径
            function_name: 函数名
            args: 调用参数
            source: 数据来源
            source_url: 来源 URL
            source_note: 来源备注
            runtime_seconds: 运行时长
        """
        if not self._enabled or not self._active_context:
            return None

        transform = TransformNode(
            name=name,
            script_path=script_path,
            function_name=function_name,
            args=args,
            source=source,
            source_url=source_url,
            source_note=source_note,
            runtime_seconds=runtime_seconds,
        )
        self._active_context.graph.add_node(transform)
        return transform

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _get_or_create_transform(self, name: str) -> TransformNode:
        """获取或创建转换节点。"""
        graph = self._active_context.graph
        for n in graph.nodes.values():
            if isinstance(n, TransformNode) and n.name == name:
                return n
        transform = TransformNode(name=name)
        graph.add_node(transform)
        return transform

    def _persist_run(self, ctx: RunContext):
        """将一次运行的完整血缘记录持久化到磁盘。"""
        if not self._enabled:
            return
        try:
            # 确保目录存在
            run_dir = LINEAGE_DIR / ctx.run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            # 写入 OpenLineage JSON
            ol_path = run_dir / "run.json"
            with open(ol_path, "w", encoding="utf-8") as f:
                json.dump(ctx.to_openlineage(), f, indent=2, ensure_ascii=False)

            # 写入图结构 JSON
            graph_path = run_dir / "graph.json"
            with open(graph_path, "w", encoding="utf-8") as f:
                json.dump(ctx.graph.to_dict(), f, indent=2, ensure_ascii=False)

            # 写入 CSV 血缘表
            csv_rows = ctx.graph.to_csv_rows()
            if csv_rows:
                csv_path = run_dir / "lineage_table.csv"
                with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(csv_rows)

            # 写入 Mermaid 源码
            mermaid_path = run_dir / "lineage.mermaid"
            with open(mermaid_path, "w", encoding="utf-8") as f:
                f.write(ctx.graph.to_mermaid())

            # 更新全局索引
            self._update_global_index(ctx)

        except Exception as e:
            print(f"[lineage] 持久化失败: {e}", file=sys.stderr)

    def _update_global_index(self, ctx: RunContext):
        """更新全局血缘索引文件（追加模式）。"""
        index_path = LINEAGE_DIR / "_index.jsonl"
        entry = {
            "run_id": ctx.run_id,
            "pipeline_name": ctx.pipeline_name,
            "city": ctx.city,
            "start_time": ctx.start_time,
            "end_time": ctx.end_time,
            "duration_seconds": ctx._duration,
            "stats": ctx.graph.stats(),
        }
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════════════
# 全局追踪器实例
# ═══════════════════════════════════════════════════════════════════════

def get_tracker() -> LineageTracker:
    """获取全局 LineageTracker 实例。"""
    return LineageTracker.get()


# ═══════════════════════════════════════════════════════════════════════
# @track_lineage 装饰器
# ═══════════════════════════════════════════════════════════════════════

def track_lineage(
    pipeline_name: str = "",
    city: str = "",
    auto_capture_args: bool = True,
):
    """
    装饰器：自动记录函数调用的输入参数和返回值。

    用法::

        @track_lineage(pipeline_name="report_parcel", city="三亚")
        def generate_report(city, district, expected_price):
            ...

    参数:
        pipeline_name: 管道名称（默认使用函数名）
        city: 城市名
        auto_capture_args: 是否自动捕获函数参数作为追踪上下文
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracker = get_tracker()
            if not tracker._enabled:
                return func(*args, **kwargs)

            # 确定 pipeline_name
            pname = pipeline_name or func.__name__

            # 提取参数
            func_args: Dict[str, Any] = {}
            if auto_capture_args:
                try:
                    sig = inspect.signature(func)
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    func_args = dict(bound.arguments)
                    # 过滤掉不可序列化的参数
                    func_args = {
                        k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v
                        for k, v in func_args.items()
                    }
                except Exception:
                    func_args = {}

            # 提取 city
            city_val = city or func_args.get("city", "")

            with tracker.run(pipeline_name=pname, city=city_val, params=func_args):
                # 记录转换节点
                tracker.track_transform(
                    name=pname,
                    function_name=func.__name__,
                    args=func_args,
                )
                t_start = time.perf_counter()
                result = func(*args, **kwargs)
                t_end = time.perf_counter()

                # 自动捕获返回值中的文件路径
                _auto_capture_result(tracker, result)

                return result

        return wrapper

    return decorator


def _auto_capture_result(tracker: LineageTracker, result: Any):
    """自动从返回值中提取文件路径并记录到血缘图中。"""
    if result is None:
        return

    # 如果是字符串路径
    if isinstance(result, str) and Path(result).exists():
        tracker.track_write(result)
        return

    # 如果是字典，递归查找路径
    if isinstance(result, dict):
        for key, value in result.items():
            if isinstance(value, str) and Path(value).exists():
                tracker.track_write(value, transform_name=f"output_{key}")
            elif isinstance(value, dict):
                _auto_capture_result(tracker, value)

    # 如果是列表
    if isinstance(result, list):
        for item in result:
            if isinstance(item, str) and Path(item).exists():
                tracker.track_write(item)


# ═══════════════════════════════════════════════════════════════════════
# 可视化输出
# ═══════════════════════════════════════════════════════════════════════

def generate_visualization_html(
    graph: LineageGraph,
    title: str = "DDS 数据血缘图",
) -> str:
    """
    生成独立 HTML 血缘可视化页面（内嵌 Mermaid.js）。
    """
    mermaid_src = graph.to_mermaid(direction="TB")
    stats = graph.stats()

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5;
            color: #333;
        }}
        .header {{
            background: #1a1a2e;
            color: #fff;
            padding: 24px 32px;
        }}
        .header h1 {{ font-size: 24px; font-weight: 600; }}
        .header .stats {{
            margin-top: 12px;
            display: flex;
            gap: 24px;
            font-size: 14px;
            color: #aaa;
        }}
        .header .stats span {{ color: #e94560; font-weight: 600; }}
        .legend {{
            display: flex;
            gap: 24px;
            padding: 16px 32px;
            background: #fff;
            border-bottom: 1px solid #e0e0e0;
            font-size: 13px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .legend-dot {{
            width: 14px;
            height: 14px;
            border-radius: 3px;
            border: 2px solid;
        }}
        .legend-dot.dataset {{ background: #e3f2fd; border-color: #1565c0; }}
        .legend-dot.transform {{ background: #fff3e0; border-color: #e65100; }}
        .legend-dot.report {{ background: #e8f5e9; border-color: #2e7d32; }}
        .legend-dot.source {{ background: #f3e5f5; border-color: #7b1fa2; }}
        .diagram-container {{
            padding: 32px;
            background: #fff;
            margin: 16px 32px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow-x: auto;
        }}
        .mermaid {{ text-align: center; }}
        .footer {{
            padding: 16px 32px;
            font-size: 12px;
            color: #999;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{title}</h1>
        <div class="stats">
            <div>节点总数: <span>{stats["total_nodes"]}</span></div>
            <div>边总数: <span>{stats["total_edges"]}</span></div>
            <div>数据集: <span>{stats["dataset_nodes"]}</span></div>
            <div>转换: <span>{stats["transform_nodes"]}</span></div>
            <div>报告: <span>{stats["report_nodes"]}</span></div>
        </div>
    </div>
    <div class="legend">
        <div class="legend-item"><div class="legend-dot dataset"></div> 数据集</div>
        <div class="legend-item"><div class="legend-dot transform"></div> 转换/管道</div>
        <div class="legend-item"><div class="legend-dot report"></div> 报告/输出</div>
        <div class="legend-item"><div class="legend-dot source"></div> 来源标注</div>
    </div>
    <div class="diagram-container">
        <pre class="mermaid">
{mermaid_src}
        </pre>
    </div>
    <div class="footer">
        生成时间: {_now_iso()} | DDS Lineage Tracker | OpenLineage 兼容
    </div>
    <script>
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'default',
            flowchart: {{ useMaxWidth: true, htmlLabels: true, curve: 'basis' }},
            securityLevel: 'loose',
        }});
    </script>
</body>
</html>"""
    return html


# ═══════════════════════════════════════════════════════════════════════
# CLI 接口
# ═══════════════════════════════════════════════════════════════════════

def _list_runs(city: str = "") -> List[Dict[str, Any]]:
    """列出所有血缘运行记录。"""
    index_path = LINEAGE_DIR / "_index.jsonl"
    if not index_path.exists():
        return []
    runs = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if city and entry.get("city", "").lower() != city.lower():
                    continue
                runs.append(entry)
            except json.JSONDecodeError:
                continue
    return runs


def _build_global_graph(city: str = "") -> LineageGraph:
    """
    从所有运行记录构建全局血缘图。
    合并所有 run 的 graph.json。
    """
    global_graph = LineageGraph(name=f"global{'_' + city if city else ''}")

    runs = _list_runs(city=city)
    for run_entry in runs:
        run_id = run_entry["run_id"]
        graph_path = LINEAGE_DIR / run_id / "graph.json"
        if not graph_path.exists():
            continue
        try:
            with open(graph_path, "r", encoding="utf-8") as f:
                gdata = json.load(f)
        except Exception:
            continue

        # 导入节点
        for nid, ndata in gdata.get("nodes", {}).items():
            node_type = ndata.get("node_type", "dataset")
            if node_type == "dataset":
                node = DatasetNode(
                    name=ndata.get("name", ""),
                    path=ndata.get("path", ""),
                    format_=ndata.get("format", ""),
                    source=ndata.get("source", ""),
                    source_url=ndata.get("source_url", ""),
                    source_note=ndata.get("source_note", ""),
                    schema_columns=ndata.get("schema_columns", []),
                )
            elif node_type == "transform":
                node = TransformNode(
                    name=ndata.get("name", ""),
                    script_path=ndata.get("script_path"),
                    function_name=ndata.get("function_name"),
                    source=ndata.get("source", ""),
                    source_url=ndata.get("source_url", ""),
                    source_note=ndata.get("source_note", ""),
                )
            elif node_type == "report":
                node = ReportNode(
                    name=ndata.get("name", ""),
                    path=ndata.get("path", ""),
                    report_type=ndata.get("report_type", "unknown"),
                    source=ndata.get("source", ""),
                    source_url=ndata.get("source_url", ""),
                    source_note=ndata.get("source_note", ""),
                )
            else:
                continue
            global_graph.add_node(node)

        # 导入边
        for edge_data in gdata.get("edges", []):
            global_graph.add_edge(
                source_id=edge_data.get("source_id", ""),
                target_id=edge_data.get("target_id", ""),
                label=edge_data.get("label", ""),
                metadata=edge_data.get("metadata"),
            )

    return global_graph


def cmd_graph(args):
    """查看血缘图（指定城市）。"""
    city = args.city or ""
    runs = _list_runs(city=city)
    if not runs:
        print(f"[INFO] 没有血缘运行记录（city={city or '全部'}）")
        return

    global_graph = _build_global_graph(city=city)
    stats = global_graph.stats()
    print(f"\n=== 血缘图统计 ===")
    print(f"  运行记录: {len(runs)}")
    print(f"  节点总数: {stats['total_nodes']}")
    print(f"  边总数:   {stats['total_edges']}")
    print(f"  数据集:   {stats['dataset_nodes']}")
    print(f"  转换:     {stats['transform_nodes']}")
    print(f"  报告:     {stats['report_nodes']}")
    print()

    # 打印最近的运行记录
    print(f"=== 最近运行记录 ===")
    runs_sorted = sorted(runs, key=lambda r: r.get("start_time", ""), reverse=True)[:10]
    for r in runs_sorted:
        print(f"  [{r['run_id']}] {r.get('pipeline_name', '')} | "
              f"city={r.get('city', '')} | "
              f"nodes={r.get('stats', {}).get('total_nodes', 0)} | "
              f"{r.get('start_time', '')}")

    # 输出 Mermaid
    print(f"\n=== Mermaid 流程图 ===")
    print(global_graph.to_mermaid())

    if args.output:
        out_path = Path(args.output)
        if out_path.suffix == ".html":
            html = generate_visualization_html(global_graph, title="DDS 数据血缘图")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\n[OK] 可视化 HTML 已保存: {out_path}")
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(global_graph.to_mermaid())
            print(f"\n[OK] Mermaid 源码已保存: {out_path}")


def cmd_trace(args):
    """追踪脚本执行并记录血缘。"""
    script_path = args.trace
    script_args = args.args or ""

    if not os.path.isfile(script_path):
        print(f"[ERROR] 脚本不存在: {script_path}")
        sys.exit(1)

    tracker = get_tracker()
    if not tracker._enabled:
        print("[WARN] 血缘追踪已禁用（DDS_LINEAGE_ENABLED=0），直接执行脚本")
        cmd = [sys.executable, script_path] + (script_args.split() if script_args else [])
        subprocess.run(cmd)
        return

    run_id = _generate_run_id()
    print(f"[INFO] 血缘追踪启动: run_id={run_id}")

    with tracker.run(
        pipeline_name=Path(script_path).stem,
        city=args.city or "",
        params={"script": script_path, "args": script_args},
    ):
        t_start = time.perf_counter()

        # 记录脚本读取
        tracker.track_transform(
            name=Path(script_path).stem,
            script_path=script_path,
            args={"args": script_args},
        )

        # 执行脚本
        cmd = [sys.executable, script_path] + (script_args.split() if script_args else [])
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )

        t_end = time.perf_counter()

        # 输出结果
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        if result.returncode != 0:
            print(f"[ERROR] 脚本退出码: {result.returncode}")
        else:
            print(f"[OK] 脚本执行完成，耗时 {t_end - t_start:.1f}s")

    print(f"[INFO] 血缘记录已保存: {LINEAGE_DIR / run_id}")


def cmd_visualize(args):
    """可视化血缘图输出为 HTML。"""
    city = args.city or ""
    global_graph = _build_global_graph(city=city)
    if not global_graph.nodes:
        print(f"[INFO] 没有血缘数据（city={city or '全部'}），无法生成可视化")
        return

    out_path = Path(args.output) if args.output else LINEAGE_DIR / "lineage_visualization.html"
    html = generate_visualization_html(global_graph, title="DDS 数据血缘图")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] 血缘可视化已保存: {out_path}")
    print(f"  节点: {len(global_graph.nodes)}, 边: {len(global_graph.edges)}")


def cmd_selftest():
    """自测：验证核心功能。"""
    print("=== LineageTracker 自测 ===")
    failures = 0

    # 测试 1: 基本 RunContext
    print("\n[TEST 1] RunContext 创建")
    try:
        ctx = RunContext(pipeline_name="test", city="三亚")
        assert ctx.run_id, "run_id 为空"
        assert ctx.pipeline_name == "test"
        assert ctx.city == "三亚"
        assert ctx.graph is not None
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 测试 2: LineageGraph 节点和边
    print("\n[TEST 2] LineageGraph 节点和边操作")
    try:
        graph = LineageGraph(name="test")
        ds = DatasetNode(name="input", path="/tmp/input.csv")
        tr = TransformNode(name="process")
        rp = ReportNode(name="output", path="/tmp/output.md")

        graph.add_node(ds)
        graph.add_node(tr)
        graph.add_node(rp)
        graph.add_edge(ds.node_id, tr.node_id, "READ")
        graph.add_edge(tr.node_id, rp.node_id, "WRITE")

        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 测试 3: 拓扑排序
    print("\n[TEST 3] 拓扑排序")
    try:
        order = graph.topological_order()
        assert len(order) == 3
        print(f"  PASS (order: {[n.name for n in order]})")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 测试 4: 上下文管理器
    print("\n[TEST 4] 上下文管理器（track_read/track_write）")
    try:
        tracker = get_tracker()
        with tracker.run(pipeline_name="test_pipeline", city="三亚"):
            tracker.track_read("/tmp/input.csv", name="输入数据")
            tracker.track_write("/tmp/report.md", name="输出报告", report_type="decision")
            assert tracker.active_context is not None
            assert len(tracker.active_context.graph.nodes) == 2
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        traceback.print_exc()
        failures += 1

    # 测试 5: 装饰器
    print("\n[TEST 5] @track_lineage 装饰器")
    try:
        @track_lineage(pipeline_name="decorator_test", city="三亚")
        def sample_pipeline(city, district):
            return {"city": city, "district": district}

        result = sample_pipeline(city="三亚", district="海棠区")
        assert result == {"city": "三亚", "district": "海棠区"}
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        traceback.print_exc()
        failures += 1

    # 测试 6: 输出格式
    print("\n[TEST 6] 输出格式（JSON / CSV / Mermaid）")
    try:
        graph2 = LineageGraph(name="format_test")
        ds = DatasetNode(name="input", path="/tmp/input.csv", source="安居客", source_url="https://example.com", source_note="测试数据")
        tr = TransformNode(name="process", source="内部", source_url="", source_note="")
        rp = ReportNode(name="output", path="/tmp/output.md", source="DDS", source_url="", source_note="")

        graph2.add_node(ds)
        graph2.add_node(tr)
        graph2.add_node(rp)
        graph2.add_edge(ds.node_id, tr.node_id, "READ")
        graph2.add_edge(tr.node_id, rp.node_id, "WRITE")

        # JSON
        ol_json = graph2.to_openlineage(run_id="test")
        assert "eventType" in ol_json
        assert "run" in ol_json
        assert "job" in ol_json
        assert "inputs" in ol_json
        assert "outputs" in ol_json

        # CSV
        csv_rows = graph2.to_csv_rows()
        assert len(csv_rows) == 2

        # Mermaid
        mermaid = graph2.to_mermaid()
        assert "flowchart" in mermaid
        assert "classDef" in mermaid

        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        traceback.print_exc()
        failures += 1

    # 测试 7: 文件指纹
    print("\n[TEST 7] 文件指纹")
    try:
        tmp_file = LINEAGE_DIR / "_test_fingerprint.txt"
        tmp_file.write_text("test content for fingerprint", encoding="utf-8")
        fp = _fingerprint_file(tmp_file)
        assert fp is not None
        assert "size_bytes" in fp
        assert "sha256_head" in fp
        assert "mtime" in fp
        tmp_file.unlink()
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 测试 8: 环境变量控制
    print("\n[TEST 8] DDS_LINEAGE_ENABLED 环境变量")
    try:
        # 模拟禁用：直接设置 _enabled 为 False
        tracker_main = get_tracker()
        saved_enabled = tracker_main._enabled
        tracker_main._enabled = False
        assert not tracker_main._enabled
        assert not LineageTracker.is_enabled()
        # 恢复
        tracker_main._enabled = saved_enabled
        assert LineageTracker.is_enabled() == saved_enabled
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 测试 9: 幂等性（重复添加节点/边）
    print("\n[TEST 9] 幂等性")
    try:
        graph3 = LineageGraph(name="idempotent_test")
        ds1 = DatasetNode(name="input", path="/tmp/input.csv")
        graph3.add_node(ds1)
        graph3.add_node(ds1)  # 重复添加
        assert len(graph3.nodes) == 1
        print("  PASS")
    except Exception as e:
        print(f"  FAIL: {e}")
        failures += 1

    # 汇总
    print(f"\n{'='*40}")
    if failures == 0:
        print("自测全部通过!")
    else:
        print(f"自测失败: {failures} 项")
    print(f"{'='*40}")

    return failures


def main():
    parser = argparse.ArgumentParser(
        description="DDS OpenLineage 风格数据血缘追踪器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python lineage_tracker.py --graph --city 三亚
  python lineage_tracker.py --trace scripts/report_parcel.py --args "--city 三亚 --district 海棠区"
  python lineage_tracker.py --visualize --output lineage.html
  python lineage_tracker.py --selftest
        """,
    )

    parser.add_argument("--graph", action="store_true", help="查看血缘图（可指定城市）")
    parser.add_argument("--trace", type=str, metavar="SCRIPT", help="追踪脚本执行并记录血缘")
    parser.add_argument("--args", type=str, default="", help="传递给被追踪脚本的参数")
    parser.add_argument("--visualize", action="store_true", help="可视化血缘图输出为 HTML")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径")
    parser.add_argument("--city", type=str, default="", help="按城市过滤")
    parser.add_argument("--selftest", action="store_true", help="运行自测")

    args = parser.parse_args()

    if args.selftest:
        failures = cmd_selftest()
        sys.exit(failures)
    elif args.trace:
        cmd_trace(args)
    elif args.visualize:
        cmd_visualize(args)
    elif args.graph:
        cmd_graph(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 数据管道调度器（本地数据/治理契约 + FC 旁路治理管道）。

支持两种运行模式：
  - local（默认）：本地 subprocess 执行抓取脚本
  - fc：通过火山引擎函数计算（FC）远程触发治理/归一化函数

用法:
    python pipeline_scheduler.py --list              # 列出所有调度
    python pipeline_scheduler.py --check              # 检查到期管道
    python pipeline_scheduler.py --run land            # 运行指定管道（dry-run）
    python pipeline_scheduler.py --run land --execute  # 真正执行
    python pipeline_scheduler.py --run governance --mode fc --city 三亚  # FC 触发治理
    python pipeline_scheduler.py --run normalize --mode fc --city 三亚    # FC 触发归一化
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import request, error as urllib_error

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import tos_client

# ── 血缘追踪（T10+）──
try:
    from scripts.governance.lineage_tracker import get_tracker, LineageGraph
    _LINEAGE_ENABLED = True
except ImportError:
    _LINEAGE_ENABLED = False

# ── 6 条管道调度配置 ────────────────────────────────────────────
# 每个管道的 source 标注来源机构/平台，source_url 标注具体网址（如有）
PIPELINES: list[dict[str, Any]] = [
    {
        "name": "transactions",
        "label": "管道一·网签成交",
        "cron": "0 2 * * *",
        "frequency": "每日 02:00",
        "source": "各城市住建局网签公示",
        "source_url": "https://zjw.wuhan.gov.cn/（武汉市住房和城市更新局）; https://fgj.hangzhou.gov.cn/（杭州市住房保障和房产管理局）; https://zj.sanya.gov.cn/（三亚市住房和城乡建设局）",
        "source_note": "框架就绪+手动数据导入模式。支持 --csv 导入 CSV 文件、--json 导入 JSON 手动录入。输出归一化到 data_out/transactions/。合规：严禁未经授权爬取商业平台。可选采集方法：scripts/webvoyager_scraper.py（WebVoyager 范式，替代传统 CSS 爬虫，AI 驱动语义理解 + 三阶段验证流水线）。",
        "tos_prefix": "raw/transactions/",
        "output_fields": ["成交均价", "DOM", "CMA可比实例", "SP-LP%", "Pending", "吸纳率", "带看量"],
        "local_scripts": ["scripts/scrape_transactions.py"],
        "status": "ready",
    },
    {
        "name": "land",
        "label": "管道二·土地出让",
        "cron": "0 3 * * 1",
        "frequency": "每周一 03:00",
        "source": "LandChina API + 杭州/三亚公共资源交易中心",
        "source_url": "https://api.landchina.com/tGygg/transfer/list（土地市场网 API）; https://www.hzctc.cn/jyxx/004001/（杭州公共资源交易中心）; https://ggzy.hainan.gov.cn/（海南公共资源交易平台）",
        "source_note": "可选采集方法：scripts/webvoyager_scraper.py --template land_auction（WebVoyager 范式，AI 语义理解替代传统 CSS/XPath 爬虫，三阶段验证流水线）。",
        "tos_prefix": "raw/land/",
        "output_fields": ["宗地编号", "楼面价", "溢价率", "竞得人"],
        "local_scripts": ["scripts/scrape_landchina_year.py", "scripts/scrape_hangzhou.py", "scripts/scrape_sanya.py"],
        "status": "ready",
    },
    {
        "name": "construction_cost",
        "label": "管道三·建安成本",
        "cron": "0 4 1 * *",
        "frequency": "每月 04:00",
        "source": "各省住建厅工程造价信息 + 购买结构化数据",
        "source_url": "各省住建厅官网工程造价栏目（如 https://zjt.hunan.gov.cn/ 湖南省住建厅）; 购买数据供应商待记录",
        "source_note": "当前入库脚本为 ingest_purchased.py（购买数据），非实时抓取；需补充各省住建厅工程造价信息抓取",
        "tos_prefix": "raw/construction_cost/",
        "output_fields": ["主体成本", "装饰成本", "安装成本", "室外工程成本"],
        "local_scripts": [],  # 待建设独立抓取脚本
        "status": "building",
    },
    {
        "name": "developer_credit",
        "label": "管道四·开发商信用",
        "cron": "0 5 1 * *",
        "frequency": "每月 05:00",
        "source": "住建部信用档案 + 法院失信被执行人名单 + 房协评级",
        "source_url": "https://www.mohurd.gov.cn/（住建部信用档案）; https://zxgk.court.gov.cn/（中国执行信息公开网-失信被执行人）; 中国房地产业协会 http://www.fangchan.com/",
        "source_note": "当前无独立抓取脚本，Phase 3 建设",
        "tos_prefix": "raw/developer_credit/",
        "output_fields": ["信用评级", "交付率", "延期率", "投诉"],
        "local_scripts": [],  # 待建设独立抓取脚本
        "status": "building",
    },
    {
        "name": "rental",
        "label": "管道五·租赁市场",
        "cron": "0 6 * * 1",
        "frequency": "每周一 06:00",
        "source": "贝壳租房 / 自如 / 58同城",
        "source_url": "https://bj.ke.com/zufang/（贝壳租房）; https://www.ziroom.com/（自如）; https://www.58.com/（58同城）",
        "source_note": "合规路径：贝壳开放平台 API（https://open.ke.com/），覆盖 100+ 城市、433 字段标签；严禁未经授权爬取",
        "tos_prefix": "raw/rental/",
        "output_fields": ["区域租金", "空置率", "GRM", "cap_rate"],
        "local_scripts": [],
        "status": "planned",
    },
    {
        "name": "policy",
        "label": "管道六·政策与金融",
        "cron": "0 7 * * 1",
        "frequency": "每周一 07:00",
        "source": "央行 LPR / 各城市住建局限购政策",
        "source_url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/（央行 LPR 公告）; 各城市住建局官网政策公告栏",
        "source_note": "框架就绪+手动数据导入模式。支持 --lpr-csv 导入 LPR 历史 CSV、--policy-json 导入政策 JSON。输出归一化到 data_out/policy/。合规：严禁未经授权爬取商业平台。",
        "tos_prefix": "raw/policy/",
        "output_fields": ["LPR", "首付比例", "限购摘要"],
        "local_scripts": ["scripts/scrape_policy.py"],
        "status": "ready",
    },
    {
        "name": "t9_feedback",
        "label": "治理·T9 反馈循环",
        "cron": "0 8 * * 1",
        "frequency": "每周一 08:00",
        "source": "field_usage.jsonl（来源：CEO 用户权重聚合） + quality_scores.json（来源：T8 质量评估）",
        "source_url": "本地派生数据，非外部抓取",
        "source_note": "T9 反馈循环：字段使用频率 × 质量缺口 → 优先补采队列；输入是 T8 质量评估输出 + CEO 用户权重记录",
        "tos_prefix": "governance/t9_priority/",
        "output_fields": ["priority", "recommendation", "quality_gap", "normalized_freq"],
        "local_scripts": ["scripts/governance/t9_feedback_loop.py"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "drift_monitor",
        "label": "治理·漂移监控",
        "cron": "0 9 * * 1",
        "frequency": "每周一 09:00",
        "source": "DDS 治理系统 T10 漂移监控（reference vs current 六维对比）",
        "source_url": "本地派生数据，非外部抓取；参考基线来自 governance/baseline/，当前数据来自 Vault/",
        "source_note": "漂移监控：六维度对比（价格分布/字段填充率/城市覆盖/坐标有效性/Schema 一致性/数据量），检测数据分布漂移。输出 governance/drift/ 报告。参数 --check。",
        "tos_prefix": "governance/drift/",
        "output_fields": ["price_drift", "fill_rate_drift", "city_coverage_drift", "coordinate_drift", "schema_drift", "data_volume_drift"],
        "local_scripts": ["scripts/governance/drift_monitor.py"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "p1_daily_gate",
        "label": "治理·P1 日度门禁",
        "cron": "0 7 * * *",
        "frequency": "每日 07:00",
        "source": "DDS 治理系统三级质量门禁（P1 入库前检查）",
        "source_url": "本地派生数据，非外部抓取；quality_gates.py --level P1",
        "source_note": "P1 日度门禁：入库前质量检查，拦截脏数据进入 Vault。参数 --level P1。与 P2 周度门禁、P0 入库前门禁组成三级门禁体系。",
        "tos_prefix": "governance/gates/p1/",
        "output_fields": ["gate_status", "violations", "pass_rate", "blocked_items"],
        "local_scripts": ["scripts/governance/quality_gates.py"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "p2_weekly_gate",
        "label": "治理·P2 周度门禁",
        "cron": "0 10 * * 1",
        "frequency": "每周一 10:00",
        "source": "DDS 治理系统三级质量门禁（P2 周度审计）",
        "source_url": "本地派生数据，非外部抓取；quality_gates.py --level P2",
        "source_note": "P2 周度门禁：周度质量审计，检查数据一致性/完整性/时效性。参数 --level P2。在 P1 日度门禁基础上增加趋势分析和深度检查。",
        "tos_prefix": "governance/gates/p2/",
        "output_fields": ["gate_status", "violations", "pass_rate", "trend_analysis"],
        "local_scripts": ["scripts/governance/quality_gates.py"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "t6_health_check",
        "label": "治理·T6 数据体检",
        "cron": "0 8 * * 1",
        "frequency": "每周一 08:00",
        "source": "DDS 治理系统 T6 数据体检表（data_health_check.py）",
        "source_url": "本地派生数据，非外部抓取；全量扫描 Vault/ 目录",
        "source_note": "T6 数据体检表：全量扫描 Vault 目录，输出 Excel 报告（每 sheet 带溯源）。覆盖文件数、行数、缺失率、异常数、健康评分等维度。",
        "tos_prefix": "governance/t6_health/",
        "output_fields": ["total_files", "total_rows", "missing_rate", "anomaly_count", "health_score"],
        "local_scripts": ["scripts/data_health_check.py"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "t10_lifecycle",
        "label": "治理·T10 生命周期",
        "cron": "0 2 1 * *",
        "frequency": "每月 1 日 02:00",
        "source": "DDS 治理系统 T10 数据生命周期管理（t10_lifecycle.py）",
        "source_url": "本地派生数据，非外部抓取；TOS 存储生命周期策略自动沉降",
        "source_note": "T10 生命周期管理：TOS 存储自动沉降（标准→低频→归档→冷归档）。参数 --execute。按数据年龄和访问频率自动分级，优化存储成本。",
        "tos_prefix": "governance/t10_lifecycle/",
        "output_fields": ["storage_tier", "objects_migrated", "cost_savings", "next_review"],
        "local_scripts": ["scripts/governance/t10_lifecycle.py"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "intelligence_publish",
        "label": "情报·TOS 快照发布",
        "cron": "15 6 * * *",
        "frequency": "每日 06:15（显式执行）",
        "source": "公开、用户提供或合法授权的情报快照规范",
        "source_url": "本地或 ECS 容器输入目录，非自动采集器",
        "source_note": (
            "发布契约已就绪；默认仅 dry-run。只有上游显式提供来源、权利状态、"
            "采集时间与生命周期状态后，--execute 才写入 TOS。"
        ),
        "tos_prefix": "raw/intelligence/",
        "output_fields": ["sha256", "object_key", "byte_size", "status", "version_id"],
        "local_scripts": ["cloud/volcengine/sync_intelligence_snapshots.py"],
        "local_args": ["--input", "data_out/intelligence/pending"],
        "execute_args": ["--execute"],
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "macro_intelligence",
        "label": "情报·宏观数据契约",
        "cron": "0 6 * * 1",
        "frequency": "按授权数据到达触发",
        "source": "官方统计、政策、土地、金融与建筑行业来源（待逐源授权接入）",
        "source_url": "未绑定自动来源",
        "source_note": "目录和 Schema 契约已就绪；宏观采集器尚未接入，不能自动抓取或宣称数据 ready。",
        "tos_prefix": "raw/intelligence/macro/",
        "output_fields": ["captured_at", "rights_status", "source_refs", "status", "payload"],
        "local_scripts": [],
        "status": "contract_ready",
        "enabled": False,
    },
    {
        "name": "social_intelligence",
        "label": "情报·社媒数据契约",
        "cron": "0 6 * * *",
        "frequency": "按合法授权数据到达触发",
        "source": "小红书、抖音、微信公众号公开或授权数据（当前未接入）",
        "source_url": "未绑定合法采集入口",
        "source_note": "存储目录和脱敏契约存在，但社媒采集器 blocked；不得绕过登录、反爬或访问控制。",
        "tos_prefix": "raw/intelligence/social/",
        "output_fields": ["captured_at", "rights_status", "source_refs", "status", "payload"],
        "local_scripts": [],
        "status": "blocked",
        "enabled": False,
    },
]

PIPELINE_BY_NAME: dict[str, dict] = {p["name"]: p for p in PIPELINES}

# ── FC 云端治理/归一化管道配置 ───────────────────────────────────
FC_PIPELINES = [
    {
        "name": "governance",
        "label": "云端治理·T7-T8-门禁",
        "function_name": "dds-pipeline-governance",
        "description": "TOS vault CSV → T7 清洗 → T8 质量评估 → quality_gates 门禁 → governance/",
        "trigger": "vault/ 新 CSV 上传自动触发",
        "status": "ready",
    },
    {
        "name": "normalize",
        "label": "云端归一化·CSV→Parquet",
        "function_name": "dds-pipeline-normalize",
        "description": "TOS vault CSV → DuckDB → normalized/ Parquet + _meta/data_lineage.json",
        "trigger": "vault/ 新 CSV 上传自动触发",
        "status": "ready",
    },
    {
        "name": "t9_feedback",
        "label": "云端治理·T9 反馈循环",
        "function_name": "dds-pipeline-t9-feedback",
        "description": "field_usage.jsonl × quality_scores.json → 优先补采队列 priority_fill_queue.csv",
        "trigger": "T8 quality_scores.json 更新后自动触发；每周一 08:00 定时",
        "source": "data_out/ceo_learning/field_usage.jsonl（来源：CEO 用户权重聚合） + "
                  "data_out/governance/t8_quality/quality_scores.json（来源：T8 质量评估）",
        "source_url": "本地派生数据，非外部抓取",
        "local_script": "scripts/governance/t9_feedback_loop.py",
        "cron": "0 8 * * 1",
        "frequency": "每周一 08:00",
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "drift_monitor",
        "label": "云端治理·漂移监控",
        "function_name": "dds-pipeline-drift-monitor",
        "description": "TOS vault/ 数据六维漂移检测（价格/填充率/城市覆盖/坐标/Schema/数据量）→ governance/drift/",
        "trigger": "TOS vault/ CSV 上传 + 每周一 09:00 定时",
        "source": "DDS 治理系统 T10 漂移监控（reference vs current 六维对比）",
        "source_url": "本地派生数据，非外部抓取；参考基线来自 governance/baseline/，当前数据来自 Vault/",
        "local_script": "scripts/governance/drift_monitor.py",
        "cron": "0 9 * * 1",
        "frequency": "每周一 09:00",
        "status": "ready",
        "enabled": True,
    },
    {
        "name": "p1_daily_gate",
        "label": "云端治理·P1 日度门禁",
        "function_name": "dds-pipeline-p1-gate",
        "description": "每日 07:00 定时触发 P1 日度质量门禁，入库前检查 → governance/gates/p1/",
        "trigger": "每日 07:00 定时",
        "source": "DDS 治理系统三级质量门禁（P1 入库前检查）",
        "source_url": "本地派生数据，非外部抓取；quality_gates.py --level P1",
        "local_script": "scripts/governance/quality_gates.py",
        "cron": "0 7 * * *",
        "frequency": "每日 07:00",
        "status": "ready",
        "enabled": True,
    },
]
FC_PIPELINE_BY_NAME: dict[str, dict] = {p["name"]: p for p in FC_PIPELINES}


def _parse_cron(expr: str) -> dict[str, Any]:
    """解析 5 字段 unix cron 表达式为 dict。

    字段: minute hour day-of-month month day-of-week
    支持 * 和具体数字。不支持范围(1-5)和列表(1,3,5)。
    """
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"无效的 cron 表达式: {expr}（需要 5 个字段）")
    return {
        "minute": int(parts[0]),
        "hour": int(parts[1]),
        "day_of_month": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def _date_matches_cron(cron: dict, dt: datetime) -> bool:
    """检查 datetime 的日期部分是否匹配 cron 的 day-of-month/month/day-of-week。

    cron 标准：当 day-of-month 和 day-of-week 都指定时，满足任一即匹配（OR）。
    本项目 6 条管道均未同时指定两者，但此处仍按标准实现。
    """
    dom = cron["day_of_month"]
    dow = cron["day_of_week"]
    month = cron["month"]

    # 月份匹配
    month_match = True
    if month != "*":
        month_match = dt.month == int(month)

    # day-of-month 匹配
    dom_match = True
    if dom != "*":
        dom_match = dt.day == int(dom)

    # day-of-week 匹配
    # cron: Sunday=0, Monday=1, ..., Saturday=6
    # Python: Monday=0, ..., Sunday=6
    dow_match = True
    if dow != "*":
        cron_dow = int(dow)
        py_dow = (cron_dow - 1) % 7
        dow_match = dt.weekday() == py_dow

    # 当 dom 和 dow 都指定时，OR 关系（cron 标准）
    if dom != "*" and dow != "*":
        return month_match and (dom_match or dow_match)
    return month_match and dom_match and dow_match


def get_schedule() -> list[dict]:
    """返回所有管道的调度配置。"""
    return [
        {
            "name": p["name"],
            "label": p["label"],
            "cron": p["cron"],
            "frequency": p["frequency"],
            "source": p["source"],
            "source_url": p.get("source_url", ""),
            "source_note": p.get("source_note", ""),
            "tos_prefix": p["tos_prefix"],
            "output_fields": p["output_fields"],
            "local_scripts": p["local_scripts"],
            "status": p["status"],
        }
        for p in PIPELINES
    ]


def next_run(pipeline_name: str, after: datetime | None = None) -> datetime:
    """计算管道下次运行时间（基于 cron 表达式）。

    从 after 的下一分钟开始，逐日搜索匹配 cron 的最近日期+时间。
    最多搜索 366 天。
    """
    if pipeline_name not in PIPELINE_BY_NAME:
        raise ValueError(f"未知管道 '{pipeline_name}'，可选: {list(PIPELINE_BY_NAME.keys())}")
    p = PIPELINE_BY_NAME[pipeline_name]
    if after is None:
        after = datetime.now()
    cron = _parse_cron(p["cron"])

    # 从 after 当天开始逐日搜索
    candidate_date = after.date()
    for _ in range(366):
        # 构造当天的候选时间点
        candidate_dt = datetime(
            candidate_date.year, candidate_date.month, candidate_date.day,
            cron["hour"], cron["minute"],
        )
        if _date_matches_cron(cron, candidate_dt) and candidate_dt > after:
            return candidate_dt
        candidate_date += timedelta(days=1)

    # fallback：一年内未找到（理论上不会发生）
    return after


def _load_pipeline_state() -> dict:
    """从 TOS _meta/pipeline_state.json 读取管道状态。

    如果 TOS 不可用或文件不存在，返回空 dict。
    """
    try:
        env_file = Path(__file__).parent / ".env.volcengine"
        tos_client.load_dotenv(env_file)
        tos_client.disable_proxy()
        cfg = tos_client.get_tos_config()
        bucket = cfg["bucket"]
        if not bucket:
            return {}
        client = tos_client.get_thread_local_client()
        resp = client.get_object(bucket, "_meta/pipeline_state.json")
        content = resp.read().decode("utf-8")
        return json.loads(content)
    except Exception:
        return {}


def check_due(pipeline_name: str) -> bool:
    """检查管道是否到期（基于 pipeline_state.json 的 last_run）。

    - last_run 为 None（从未运行）→ 到期
    - last_run 后的下次计划时间 <= 当前时间 → 到期
    - 否则未到期
    """
    if pipeline_name not in PIPELINE_BY_NAME:
        return False
    state = _load_pipeline_state()
    p_state = state.get("pipelines", {}).get(pipeline_name, {})
    last_run_str = p_state.get("last_run")
    if not last_run_str:
        return True  # 从未运行过，应当到期
    try:
        last_run = datetime.fromisoformat(last_run_str)
    except (ValueError, TypeError):
        return True
    nxt = next_run(pipeline_name, after=last_run)
    return nxt <= datetime.now()


def run_pipeline(
    pipeline_name: str,
    dry_run: bool = True,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    """运行管道。

    dry_run=True（默认）：只打印将执行的抓取脚本命令，不真正执行。
    dry_run=False：真正执行脚本（subprocess），最长等待 3600 秒。
    """
    if pipeline_name not in PIPELINE_BY_NAME:
        return {"error": f"未知管道 '{pipeline_name}'，可选: {list(PIPELINE_BY_NAME.keys())}"}
    p = PIPELINE_BY_NAME[pipeline_name]
    scripts = p["local_scripts"]
    now = datetime.now().isoformat(timespec="seconds")

    # ── 血缘追踪 ──
    tracker = None
    if _LINEAGE_ENABLED and not dry_run and scripts:
        try:
            tracker = get_tracker()
            tracker.push_context(type("RunContext", (), {
                "run_id": f"pipeline_{pipeline_name}_{datetime.now().strftime('%Y%m%dT%H%M%S')}",
                "pipeline_name": pipeline_name,
                "city": "",
                "params": {"label": p["label"], "frequency": p["frequency"]},
                "graph": None,
            })())
            tracker.active_context.graph = LineageGraph(name=pipeline_name)
            tracker.track_transform(
                name=pipeline_name,
                script_path=", ".join(scripts) if scripts else "",
                source=p["source"],
                source_url=p.get("source_url", ""),
                source_note=p.get("source_note", ""),
            )
            tracker.track_read(
                p["tos_prefix"],
                name=f"TOS:{p['label']}",
                source=p["source"],
                source_url=p.get("source_url", ""),
                source_note=p.get("source_note", ""),
                transform_name=pipeline_name,
            )
        except Exception:
            pass

    command_parts: list[list[str]] = []
    for script in scripts:
        cmd = [sys.executable, str(PROJECT_ROOT / script)]
        script_args = [str(value) for value in p.get("local_args", [])]
        if input_path is not None and "--input" in script_args:
            input_index = script_args.index("--input") + 1
            if input_index >= len(script_args):
                raise ValueError(f"管道 {pipeline_name} 的 --input 参数缺少默认值")
            script_args[input_index] = str(input_path)
        if not dry_run:
            script_args.extend(str(value) for value in p.get("execute_args", []))
        cmd.extend(script_args)
        command_parts.append(cmd)
    commands = [subprocess.list2cmdline(parts) for parts in command_parts]

    result: dict[str, Any] = {
        "pipeline": pipeline_name,
        "label": p["label"],
        "mode": "dry-run" if dry_run else "execute",
        "timestamp": now,
        "commands": commands,
        "scripts": scripts,
        "status": p["status"],
        "source": p["source"],
        "source_url": p.get("source_url", ""),
        "source_note": p.get("source_note", ""),
        "output_fields": p["output_fields"],
        "tos_prefix": p["tos_prefix"],
        "next_run": next_run(pipeline_name).isoformat(timespec="seconds"),
    }

    if dry_run:
        print(f"\n[DRY-RUN] {p['label']} ({pipeline_name})")
        print(f"  状态:     {p['status']}")
        print(f"  数据源:   {p['source']}")
        if p.get("source_url"):
            print(f"  来源 URL: {p['source_url']}")
        if p.get("source_note"):
            print(f"  备注:     {p['source_note']}")
        print(f"  TOS 路径: {p['tos_prefix']}")
        print(f"  输出字段: {', '.join(p['output_fields'])}")
        print(f"  调度:     {p['cron']} ({p['frequency']})")
        print(f"  下次运行: {result['next_run']}")
        if commands:
            print(f"  将执行的命令:")
            for cmd in commands:
                print(f"    $ {cmd}")
        else:
            print(f"  (无本地脚本，管道待建设)")
    else:
        if not scripts:
            print(f"[SKIP] {p['label']}: 无本地脚本可执行")
            result["executed"] = False
            return result
        print(f"\n[EXECUTE] {p['label']} ({pipeline_name})")
        exec_results = []
        for script, cmd_parts, display_cmd in zip(scripts, command_parts, commands):
            print(f"  $ {display_cmd}")
            try:
                proc = subprocess.run(
                    cmd_parts,
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
                if proc.returncode == 0:
                    print(f"    OK (exit 0)")
                    exec_results.append({"script": script, "exit_code": 0, "status": "ok"})
                else:
                    print(f"    FAILED (exit {proc.returncode})")
                    print(f"    stderr: {proc.stderr[:500]}")
                    exec_results.append({
                        "script": script, "exit_code": proc.returncode,
                        "status": "failed", "stderr": proc.stderr[:500],
                    })
            except Exception as exc:
                print(f"    ERROR: {type(exc).__name__}: {exc}")
                exec_results.append({
                    "script": script, "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
        result["executed"] = True
        result["exec_results"] = exec_results

    # ── 血缘追踪：持久化 ──
    if tracker and tracker.active_context and not dry_run:
        try:
            tracker.track_write(
                p["tos_prefix"],
                name=f"管道输出:{p['label']}",
                report_type="pipeline",
                source=p["source"],
                source_url=p.get("source_url", ""),
                source_note=p.get("source_note", ""),
                transform_name=pipeline_name,
            )
            tracker.active_context.mark_complete()
            tracker._persist_run(tracker.active_context)
            tracker.pop_context()
        except Exception:
            pass

    return result


# ═══════════════════════════════════════════════════════════════════
#  FC 远程调用
# ═══════════════════════════════════════════════════════════════════

def _get_fc_config() -> dict[str, str]:
    """获取 FC 配置。"""
    prefix = os.environ.get("DDS_FC_FUNCTION_PREFIX", "dds-pipeline")
    endpoint = os.environ.get("DDS_FC_ENDPOINT", "cn-shanghai.fc.volcengine.com")
    timeout = os.environ.get("DDS_FC_TIMEOUT", "300")
    return {
        "prefix": prefix,
        "endpoint": endpoint,
        "timeout": int(timeout),
    }


def _invoke_fc(function_name: str, payload: dict, dry_run: bool = True) -> dict[str, Any]:
    """通过 HTTP API 调用火山引擎 FC 函数。

    使用火山引擎 FC HTTP 触发器调用格式。
    如果 dry_run=True，只打印调用计划不实际执行。

    参考: https://www.volcengine.com/docs/6791/1329820
    """
    fc_cfg = _get_fc_config()
    full_name = f"{fc_cfg['prefix']}-{function_name}" if fc_cfg['prefix'] else function_name

    result: dict[str, Any] = {
        "function": full_name,
        "mode": "dry-run" if dry_run else "execute",
        "payload": payload,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    if dry_run:
        print(f"\n[FC DRY-RUN] {full_name}")
        print(f"  Endpoint: {fc_cfg['endpoint']}")
        print(f"  Payload:   {json.dumps(payload, ensure_ascii=False)}")
        result["status"] = "dry-run"
        return result

    # 构建 FC HTTP 触发器 URL
    # 火山引擎 FC HTTP 触发器格式: https://{function_id}.{region}.fc.volcengine.com/{path}
    url = f"https://{fc_cfg['endpoint']}/invoke/{full_name}"

    print(f"\n[FC EXECUTE] {full_name}")
    print(f"  URL:      {url}")
    print(f"  Payload:  {json.dumps(payload, ensure_ascii=False)}")

    try:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Fc-Invocation-Type": "Sync",  # 同步调用
            },
            method="POST",
        )

        with request.urlopen(req, timeout=fc_cfg["timeout"]) as resp:
            body = resp.read().decode("utf-8")
            status_code = resp.status
            result["http_status"] = status_code
            result["response"] = json.loads(body) if body else {}
            result["status"] = "ok" if 200 <= status_code < 300 else "failed"

        print(f"  HTTP {status_code}: {json.dumps(result['response'], ensure_ascii=False)[:200]}")
        return result

    except urllib_error.HTTPError as exc:
        result["http_status"] = exc.code
        result["error"] = f"HTTP {exc.code}: {exc.reason}"
        result["status"] = "failed"
        print(f"  [!!] {result['error']}")
        return result

    except urllib_error.URLError as exc:
        result["error"] = f"Connection error: {exc.reason}"
        result["status"] = "failed"
        print(f"  [!!] {result['error']}")
        return result

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["status"] = "failed"
        print(f"  [!!] {result['error']}")
        return result


def run_fc_pipeline(pipeline_name: str, city: str,
                    dry_run: bool = True) -> dict[str, Any]:
    """运行 FC 云端治理/归一化管道。

    pipeline_name: 'governance' 或 'normalize'
    city: 目标城市名
    """
    if pipeline_name not in FC_PIPELINE_BY_NAME:
        return {
            "error": f"未知 FC 管道 '{pipeline_name}'，可选: {list(FC_PIPELINE_BY_NAME.keys())}"
        }

    p = FC_PIPELINE_BY_NAME[pipeline_name]
    now = datetime.now().isoformat(timespec="seconds")

    # 构建 FC 事件 payload（模拟 TOS 事件）
    payload = {
        "bucket": os.environ.get("DDS_TOS_BUCKET", "dds-data-lake"),
        "object_key": f"vault/2026新楼盘/新楼盘-{city}.csv",
    }

    print(f"\n{'=' * 60}")
    print(f"FC 管道: {p['label']} ({pipeline_name})")
    print(f"函数:    {p['function_name']}")
    print(f"城市:    {city}")
    print(f"模式:    {'dry-run' if dry_run else 'execute'}")
    print(f"{'=' * 60}")

    result = _invoke_fc(pipeline_name, payload, dry_run=dry_run)
    result["pipeline"] = pipeline_name
    result["label"] = p["label"]
    result["city"] = city

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS 数据管道调度器 — cron 调度、到期检查、本地/FC 运行"
    )
    parser.add_argument("--list", action="store_true", help="列出所有调度配置")
    parser.add_argument("--check", action="store_true", help="检查到期管道")
    parser.add_argument("--run", help="运行指定管道（默认 dry-run）")
    parser.add_argument("--mode", type=str, default="local", choices=["local", "fc"],
                        help="运行模式：local=本地 subprocess，fc=远程 FC 调用（默认 local）")
    parser.add_argument("--city", type=str, default=None,
                        help="城市名（FC 模式指定目标城市）")
    parser.add_argument("--input", type=str, default=None,
                        help="intelligence_publish 的 JSON 文件或目录")
    parser.add_argument("--dry-run", action="store_true", default=True, help="模拟运行（默认）")
    parser.add_argument("--execute", action="store_true", help="真正执行（非 dry-run）")
    args = parser.parse_args()

    if args.list:
        schedules = get_schedule()
        print(f"\nDDS 数据管道调度配置 ({len(schedules)} 条本地 + {len(FC_PIPELINES)} 条 FC):")
        print(f"{'=' * 80}")
        print(f"\n── 本地管道 ──")
        for s in schedules:
            nxt = next_run(s["name"])
            print(f"\n  {s['label']} ({s['name']})")
            print(f"    cron:       {s['cron']}")
            print(f"    频率:       {s['frequency']}")
            print(f"    数据源:     {s['source']}")
            if s.get("source_url"):
                print(f"    来源 URL:   {s['source_url']}")
            if s.get("source_note"):
                print(f"    备注:       {s['source_note']}")
            print(f"    TOS 路径:   {s['tos_prefix']}")
            print(f"    输出字段:   {', '.join(s['output_fields'])}")
            scripts_str = ", ".join(s["local_scripts"]) or "(待建设)"
            print(f"    本地脚本:   {scripts_str}")
            print(f"    状态:       {s['status']}")
            print(f"    下次运行:   {nxt.isoformat(timespec='seconds')}")
        print(f"\n── FC 云端管道 ──")
        for p in FC_PIPELINES:
            print(f"\n  {p['label']} ({p['name']})")
            print(f"    函数:       {p['function_name']}")
            print(f"    触发:       {p['trigger']}")
            print(f"    说明:       {p['description']}")
            print(f"    状态:       {p['status']}")
        return 0

    if args.check:
        now_str = datetime.now().isoformat(timespec="seconds")
        print(f"\nDDS 管道到期检查 ({now_str}):")
        print(f"{'=' * 70}")
        any_due = False
        for p in PIPELINES:
            due = check_due(p["name"])
            nxt = next_run(p["name"])
            marker = " <-- DUE" if due else ""
            print(f"  {p['name']:20s}  cron={p['cron']:12s}  next={nxt.isoformat(timespec='minutes')}{marker}")
            if due:
                any_due = True
        if any_due:
            print(f"\n有到期管道，建议运行:")
            print(f"  python pipeline_scheduler.py --run <name>")
        else:
            print(f"\n无到期管道。")
        return 0

    if args.run:
        # FC 模式：触发云端治理/归一化
        if args.mode == "fc":
            if args.run in FC_PIPELINE_BY_NAME:
                if not args.city:
                    print("错误：FC 模式需要 --city 参数指定目标城市")
                    return 1
                dry = not args.execute
                result = run_fc_pipeline(args.run, args.city, dry_run=dry)
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return 0
            else:
                print(f"错误：FC 模式仅支持 FC 管道: {list(FC_PIPELINE_BY_NAME.keys())}")
                print(f"  当前 --run: {args.run}")
                return 1

        # 本地模式：原有逻辑
        dry = not args.execute
        result = run_pipeline(args.run, dry_run=dry, input_path=args.input)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    # 默认：打印帮助 + 管道列表
    parser.print_help()
    print("\n数据管道（本地）:")
    for p in PIPELINES:
        print(f"  {p['name']:20s}  {p['label']}  ({p['frequency']})  [{p['status']}]")
    print("\nFC 云端管道（--mode fc）:")
    for p in FC_PIPELINES:
        print(f"  {p['name']:20s}  {p['label']}  [{p['status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

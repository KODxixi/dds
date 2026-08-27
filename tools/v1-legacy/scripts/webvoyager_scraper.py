# -*- coding: utf-8 -*-
"""
DDS WebVoyager 范式 AI 驱动网页数据采集模块。

对标论文：WebVoyager (He et al., ACL 2024) — 基于视觉的端到端网页代理。
在 DDS 中适配为「requests + BeautifulSoup 主路径 + LLM 语义增强」的混合架构，
无需外部浏览器自动化框架（Selenium/Playwright）。

三阶段提取流水线：
  Phase 1: 页面结构分析（HTML 语义区域识别）
  Phase 2: 结构化字段提取（BeautifulSoup 解析 + 模板匹配）
  Phase 3: LLM 验证（Ark 校验数据完整性 + 标注置信度）

降级路径：
  L1: requests + BS4 自动提取（主路径）
  L2: LLM 语义增强（服务端 Ark Responses API）
  L3: 手动模板（JSON 配置文件驱动）

内置模板：
  - housing_price：楼盘名称/价格/面积/地址/开发商
  - land_auction：宗地编号/位置/面积/容积率/成交价/竞得人

用法:
  python scripts/webvoyager_scraper.py --url "https://..." --goal "提取楼盘名称、价格、面积" --output result.json
  python scripts/webvoyager_scraper.py --template housing_price --url "https://..." --output result.json
  python scripts/webvoyager_scraper.py --dry-run  # 输出模板示例，验证模块可用性

数据来源标注：所有输出带 SOURCE / SOURCE_URL / SOURCE_NOTE 字段，对齐 DDS 三级信任体系。
默认 dry-run 模式，实际抓取需 --execute。单线程 + 随机 sleep，遵守 robots.txt。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urljoin

# DDS 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 可选依赖 ──────────────────────────────────────────────────────
try:
    import requests
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from ark_runtime import get_ark_runtime
    HAS_ARK_RUNTIME = True
except ImportError:
    try:
        from .ark_runtime import get_ark_runtime
        HAS_ARK_RUNTIME = True
    except ImportError:
        HAS_ARK_RUNTIME = False

# ── 时间戳 ─────────────────────────────────────────────────────────
from datetime import timedelta as _td
UTC8 = timezone(_td(hours=8))


def _ts() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════════
#  内置抓取模板
# ═══════════════════════════════════════════════════════════════════

TEMPLATES: dict[str, dict[str, Any]] = {
    "housing_price": {
        "name": "housing_price",
        "label": "楼盘价格信息采集",
        "goal": "提取楼盘名称、价格、面积、地址、开发商",
        "target_fields": [
            {"key": "project_name", "label": "楼盘名称", "type": "string", "selector_hints": ["h1", ".project-name", ".loupan-name", "[class*=name]", "title"]},
            {"key": "price", "label": "价格", "type": "string", "selector_hints": [".price", ".unit-price", "[class*=price]", ".total-price", "span.price"]},
            {"key": "area_range", "label": "面积范围", "type": "string", "selector_hints": [".area", ".build-area", "[class*=area]", ".house-area"]},
            {"key": "address", "label": "地址", "type": "string", "selector_hints": [".address", ".location", "[class*=addr]", ".project-addr"]},
            {"key": "developer", "label": "开发商", "type": "string", "selector_hints": [".developer", "[class*=dev]", ".company", ".developer-name"]},
        ],
        "source": "网页采集（WebVoyager 范式）",
        "source_note": "L2 高信任：网页结构化数据采集，需 LLM 语义验证。非政府一手数据，置信度上限 0.7。",
        "trust_level": "L2",
    },
    "land_auction": {
        "name": "land_auction",
        "label": "土地出让信息采集",
        "goal": "提取宗地编号、位置、面积、容积率、成交价、竞得人",
        "target_fields": [
            {"key": "parcel_id", "label": "宗地编号", "type": "string", "selector_hints": [".parcel-id", ".land-id", "[class*=编号]", "td:contains('宗地')"]},
            {"key": "location", "label": "位置", "type": "string", "selector_hints": [".location", ".address", "[class*=位置]", "[class*=坐落]"]},
            {"key": "land_area", "label": "土地面积", "type": "string", "selector_hints": [".land-area", ".area", "[class*=面积]", "td:contains('㎡')"]},
            {"key": "plot_ratio", "label": "容积率", "type": "string", "selector_hints": [".plot-ratio", "[class*=容积率]", "td:contains('容积率')"]},
            {"key": "final_price", "label": "成交价", "type": "string", "selector_hints": [".final-price", ".deal-price", "[class*=成交价]", "[class*=总价]"]},
            {"key": "winner", "label": "竞得人", "type": "string", "selector_hints": [".winner", ".bidder", "[class*=竞得]", "[class*=竞买人]", ".company"]},
        ],
        "source": "网页采集（WebVoyager 范式）",
        "source_note": "L1 绝对信任：土地出让公告为政府一手数据。需 PDF 解析补充验证。",
        "trust_level": "L1",
    },
}

# ═══════════════════════════════════════════════════════════════════
#  Utility
# ═══════════════════════════════════════════════════════════════════

def _safe_slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text).strip("_")[:60] or "scrape"


def _random_sleep(min_s: float = 0.3, max_s: float = 1.5):
    """单线程友好延迟。"""
    time.sleep(random.uniform(min_s, max_s))


def _resolve_output_path(output: str) -> Path:
    p = Path(output)
    if not p.is_absolute():
        p = ROOT / "data_out" / "webvoyager" / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ═══════════════════════════════════════════════════════════════════
#  robots.txt 检查
# ═══════════════════════════════════════════════════════════════════

def check_robots(url: str, user_agent: str = "DDS-WebVoyager/1.0") -> dict[str, Any]:
    """检查目标 URL 是否允许抓取。

    返回 dict: {allowed: bool, robots_url: str, can_fetch: bool, crawl_delay: float|None}
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    result = {
        "allowed": True,
        "robots_url": robots_url,
        "can_fetch": True,
        "crawl_delay": None,
    }
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        result["can_fetch"] = rp.can_fetch(user_agent, url)
        delay = rp.crawl_delay(user_agent)
        if delay is not None:
            result["crawl_delay"] = delay
        if not result["can_fetch"]:
            result["allowed"] = False
    except Exception:
        # robots.txt 不可用时默认允许，但标记为未验证
        result["robots_note"] = "robots.txt 不可用，默认允许"
    return result


# ═══════════════════════════════════════════════════════════════════
#  Phase 1: 页面结构分析（HTML 语义区域识别）
# ═══════════════════════════════════════════════════════════════════

def _analyze_page_structure(html: str, url: str) -> dict[str, Any]:
    """分析 HTML 页面结构，识别候选数据区域。

    模拟 WebVoyager 的截图分析——通过 HTML 标签/class 语义推断页面结构。
    返回：页面元信息 + 候选区域列表。
    """
    soup = BeautifulSoup(html, "html.parser")

    # 移除不可见元素
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer"]):
        tag.decompose()

    # 提取页面元信息
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)

    meta_desc = ""
    for meta in soup.find_all("meta"):
        if meta.get("name", "").lower() in ("description", "keywords"):
            meta_desc += (meta.get("content", "") or "") + " "

    # 识别候选数据区域
    candidates = []
    # 优先级容器：table, ul/ol, div 含 class 名暗示数据的
    for tag_name, hint_patterns in [
        ("table", [r"price", r"list", r"data", r"result", r"grid", r"house", r"land", r"project"]),
        ("ul", [r"list", r"result", r"item", r"house", r"project", r"land"]),
        ("ol", [r"list", r"result", r"item"]),
        ("div", [r"list", r"result", r"item", r"card", r"house", r"project", r"land", r"price", r"detail", r"info"]),
    ]:
        for el in soup.find_all(tag_name):
            cls = " ".join(el.get("class", [])) + " " + (el.get("id", "") or "")
            if any(re.search(pat, cls, re.I) for pat in hint_patterns):
                text_sample = el.get_text(" ", strip=True)[:200]
                candidates.append({
                    "tag": tag_name,
                    "class": el.get("class", []),
                    "id": el.get("id", ""),
                    "text_sample": text_sample,
                    "child_count": len(el.find_all(recursive=False)),
                })

    # 去重：按 text_sample 前缀去重
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = c["text_sample"][:80]
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    # 如果没找到候选区域，用 body 作为 fallback
    if not unique_candidates:
        body_text = soup.body.get_text(" ", strip=True)[:500] if soup.body else ""
        unique_candidates = [{
            "tag": "body",
            "class": [],
            "id": "",
            "text_sample": body_text,
            "child_count": 0,
            "fallback": True,
        }]

    return {
        "url": url,
        "title": title,
        "meta_description": meta_desc.strip()[:200],
        "candidate_regions": unique_candidates[:10],
        "total_candidates_found": len(candidates),
    }


# ═══════════════════════════════════════════════════════════════════
#  Phase 2: 结构化字段提取（BS4 解析 + 可访问性树模拟）
# ═══════════════════════════════════════════════════════════════════

def _extract_field(soup: BeautifulSoup, field_def: dict[str, Any]) -> Optional[str]:
    """根据字段定义的选择器提示提取值。

    模拟 WebVoyager 的 accessibility tree 解析——通过标签语义和文本模式匹配。
    """
    hints = field_def.get("selector_hints", [])
    label = field_def.get("label", "")

    for hint in hints:
        # CSS 选择器试匹配
        try:
            matches = soup.select(hint)
            for m in matches:
                text = m.get_text(" ", strip=True)
                if text and len(text) > 1:
                    return text[:500]
        except Exception:
            pass

    # 文本模式匹配：查找包含 label 的相邻元素
    # 例如 "价格：35000元/㎡" 中的 "35000元/㎡"
    label_patterns = [
        re.compile(rf"{re.escape(label)}\s*[：:]\s*(.+?)(?:\s|$)", re.I),
        re.compile(rf"{re.escape(label)}\s*[：:]?\s*(.+?)(?:元|万|㎡|m²|亩|公顷)", re.I),
    ]
    all_text = soup.get_text(" ", strip=True)
    for pat in label_patterns:
        m = pat.search(all_text)
        if m:
            val = m.group(1).strip()
            if val:
                return val[:500]

    # 查找 dt/dd 或 th/td 配对
    for dt in soup.find_all(["dt", "th", "label"]):
        dt_text = dt.get_text(strip=True)
        if label in dt_text or re.search(re.escape(label), dt_text, re.I):
            next_el = dt.find_next(["dd", "td", "span", "div", "p"])
            if next_el:
                val = next_el.get_text(" ", strip=True)
                if val and val != dt_text:
                    return val[:500]

    return None


def _extract_structured_data(html: str, template: dict[str, Any]) -> dict[str, Any]:
    """从 HTML 中按模板定义提取结构化字段。"""
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for field_def in template["target_fields"]:
        key = field_def["key"]
        value = _extract_field(soup, field_def)
        fields[key] = {
            "label": field_def["label"],
            "value": value,
            "extracted": value is not None,
        }
    return fields


# ═══════════════════════════════════════════════════════════════════
#  Phase 3: LLM 验证（Ark 语义校验）
# ═══════════════════════════════════════════════════════════════════

def _get_llm_client() -> Optional[Any]:
    """获取服务端 Ark runtime；配置细节不会进入采集结果。"""
    if not HAS_ARK_RUNTIME:
        return None
    try:
        return get_ark_runtime()
    except Exception:
        return None


def _validate_ark_verification(
    payload: object,
    extracted: dict[str, Any],
) -> dict[str, Any]:
    """Bound Ark output to known fields and keep it as a human-review suggestion."""
    allowed = {"confidence", "corrections", "missing_fields", "overall_quality", "notes"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Ark verification payload shape is invalid")
    field_names = set(extracted)
    confidence = payload.get("confidence") or {}
    corrections = payload.get("corrections") or {}
    missing = payload.get("missing_fields") or []
    if not isinstance(confidence, dict) or set(confidence) - field_names:
        raise ValueError("Ark confidence keys are invalid")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 1
        for value in confidence.values()
    ):
        raise ValueError("Ark confidence values are invalid")
    if not isinstance(corrections, dict) or set(corrections) - field_names:
        raise ValueError("Ark correction keys are invalid")
    if any(
        isinstance(value, (dict, list)) or len(str(value)) > 2_000
        for value in corrections.values()
    ):
        raise ValueError("Ark correction values are invalid")
    if (
        not isinstance(missing, list)
        or any(not isinstance(value, str) or value not in field_names for value in missing)
    ):
        raise ValueError("Ark missing fields are invalid")
    quality = str(payload.get("overall_quality") or "unknown")
    if quality not in {"excellent", "good", "fair", "poor", "unknown"}:
        raise ValueError("Ark quality label is invalid")
    notes = str(payload.get("notes") or "")
    if len(notes) > 2_000:
        raise ValueError("Ark notes are too long")
    return {
        "verified": False,
        "ark_reviewed": True,
        "review_required": True,
        "confidence": {key: float(value) for key, value in confidence.items()},
        "corrections": {key: str(value) for key, value in corrections.items()},
        "missing_fields": list(missing),
        "overall_quality": quality,
        "notes": notes,
    }


def _build_web_source_inputs(
    extracted: dict[str, Any], page_text: str
) -> list[dict[str, Any]]:
    snippet = str(page_text or "")[:3000]
    if not snippet:
        raise ValueError("Ark review requires a non-empty source text span")
    structured = json.dumps(
        extracted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return [
        {
            "locator": {
                "kind": "paragraph",
                "paragraph_id": "page-text-0-3000",
            },
            "content_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
        },
        {
            "locator": {
                "kind": "json_pointer",
                "json_pointer": "/phase2_extracted",
            },
            "content_sha256": hashlib.sha256(structured.encode("utf-8")).hexdigest(),
        },
    ]


def _apply_ark_review(
    extracted: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Attach Ark suggestions without changing deterministic extraction values."""
    reviewed = deepcopy(extracted)
    corrections = review.get("corrections") or {}
    confidence = review.get("confidence") or {}
    for key, field_data in reviewed.items():
        field_data["confidence"] = confidence.get(
            key, 0.5 if field_data.get("extracted") else 0.0
        )
        if key in corrections and corrections[key]:
            field_data["ark_suggested_value"] = corrections[key]
            field_data["review_required"] = True
    return reviewed


def _llm_verify(extracted: dict[str, Any], page_text: str, template: dict[str, Any]) -> dict[str, Any]:
    """用 LLM 校验提取数据的完整性和可信度。

    向 Ark 发送页面文本片段 + 已提取字段，请求：
      1. 验证每个字段的值是否正确
      2. 补充可能遗漏的字段
      3. 标注每个字段的置信度 (0-1)
    """
    client = _get_llm_client()
    if client is None:
        return {
            "verified": False,
            "note": "Ark 不可用，跳过语义验证并保留规则提取结果",
            "confidence": {k: (0.5 if v["extracted"] else 0.0) for k, v in extracted.items()},
            "corrections": {},
            "missing_fields": [],
        }

    # 构建 prompt
    fields_desc = "\n".join(
        f"  - {k}（{v['label']}）: {v['value'] or '未提取到'}"
        for k, v in extracted.items()
    )
    prompt = f"""你是一个地产数据采集校验助手。请校验以下网页提取结果的准确性。

【采集目标】{template.get('goal', '')}
【目标字段】
{fields_desc}

【网页内容片段】（前 3000 字符）
{page_text[:3000]}

请返回 JSON 格式（仅 JSON，不要其他文字）：
{{
  "confidence": {{"字段名": 0.0-1.0的置信度}},
  "corrections": {{"字段名": "修正后的值（仅当原值明显错误时）"}},
  "missing_fields": ["遗漏的字段名"],
  "overall_quality": "excellent|good|fair|poor",
  "notes": "简要说明"
}}"""

    try:
        content = client.complete(
            prompt,
            instructions="只依据输入网页片段校验字段；只输出 JSON，不得补写未提供的经营事实。",
            max_output_tokens=1024,
            temperature=0.0,
            json_object=True,
        )
        # 提取 JSON
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            review = _validate_ark_verification(json.loads(json_match.group()), extracted)
            review["source_inputs"] = _build_web_source_inputs(extracted, page_text)
            return review
        return {"verified": False, "note": "Ark 未返回有效 JSON"}
    except Exception:
        return {"verified": False, "note": "Ark 调用失败，保留规则提取结果"}


# ═══════════════════════════════════════════════════════════════════
#  核心类 WebVoyagerScraper
# ═══════════════════════════════════════════════════════════════════

class WebVoyagerScraper:
    """AI 驱动的网页数据采集器。

    实现 WebVoyager (ACL 2024) 范式在 DDS 中的适配：
    用 requests + BeautifulSoup 替代浏览器自动化，
    用 LLM 做语义理解和数据校验。

    属性:
        user_agent: HTTP User-Agent 头
        timeout: 请求超时 (秒)
        respect_robots: 是否遵守 robots.txt
    """

    def __init__(
        self,
        user_agent: str = "DDS-WebVoyager/1.0 (Data Decision System; +https://github.com/dds)",
        timeout: int = 15,
        respect_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def fetch(self, url: str) -> dict[str, Any]:
        """获取网页内容。

        返回 dict: {status_code, html, text, headers, elapsed_ms, error}
        """
        result: dict[str, Any] = {
            "url": url,
            "status_code": None,
            "html": "",
            "text": "",
            "headers": {},
            "elapsed_ms": 0,
            "error": None,
        }
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            result["status_code"] = resp.status_code
            result["headers"] = dict(resp.headers)
            result["elapsed_ms"] = round(resp.elapsed.total_seconds() * 1000)
            if resp.status_code == 200:
                # 尝试检测编码
                resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
                result["html"] = resp.text
                soup = BeautifulSoup(resp.text, "html.parser")
                result["text"] = soup.get_text(" ", strip=True)[:10000]
            else:
                result["error"] = f"HTTP {resp.status_code}"
        except requests.exceptions.Timeout:
            result["error"] = "请求超时"
        except requests.exceptions.ConnectionError as exc:
            result["error"] = f"连接失败: {exc}"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    def scrape(
        self,
        url: str,
        goal: str,
        template_name: Optional[str] = None,
        execute: bool = False,
    ) -> dict[str, Any]:
        """执行完整的三阶段抓取流水线。

        Args:
            url: 目标网页 URL
            goal: 自然语言提取目标描述
            template_name: 可选预置模板名 (housing_price / land_auction)
            execute: 是否真正执行（默认 False = dry-run）

        Returns:
            结构化采集结果 dict
        """
        ts = _ts()

        # ── 确定模板 ──
        template = None
        if template_name and template_name in TEMPLATES:
            template = TEMPLATES[template_name]
        elif template_name:
            # 尝试从外部 JSON 文件加载
            template_path = Path(template_name)
            if not template_path.is_absolute():
                template_path = ROOT / template_name
            if template_path.exists():
                template = json.loads(template_path.read_text(encoding="utf-8"))
            else:
                return {"error": f"模板不存在: {template_name}", "available_templates": list(TEMPLATES.keys())}

        # ── Dry-run 模式 ──
        if not execute:
            return self._dry_run_result(url, goal, template, ts)

        # ── 依赖检查 ──
        if not HAS_BS4:
            return {
                "error": "缺少依赖: pip install requests beautifulsoup4",
                "mode": "dry-run 仍可用，输出模板示例",
            }

        # ── robots.txt ──
        robots_result = None
        if self.respect_robots:
            robots_result = check_robots(url, self.user_agent)
            if not robots_result["can_fetch"]:
                return {
                    "error": "robots.txt 禁止抓取此 URL",
                    "robots": robots_result,
                    "timestamp": ts,
                }

        # ── Phase 1: 页面结构分析 ──
        _random_sleep(0.5, 1.0)
        fetch_result = self.fetch(url)
        if fetch_result["error"]:
            return {
                "error": f"页面获取失败: {fetch_result['error']}",
                "status_code": fetch_result["status_code"],
                "timestamp": ts,
            }

        _random_sleep(0.3, 0.8)
        structure = _analyze_page_structure(fetch_result["html"], url)

        # ── Phase 2: 结构化字段提取 ──
        if template is None:
            # 无模板时，用通用提取
            template = {
                "name": "auto",
                "label": "自动提取",
                "goal": goal,
                "target_fields": [
                    {"key": "title", "label": "标题", "type": "string", "selector_hints": ["h1", "title", ".title"]},
                    {"key": "main_content", "label": "主要内容", "type": "string", "selector_hints": ["article", "main", ".content", ".post"]},
                ],
                "source": "网页采集（WebVoyager 范式）",
                "source_note": "通用自动提取，无特定模板",
                "trust_level": "L3",
            }

        extracted = _extract_structured_data(fetch_result["html"], template)

        # ── Phase 3: LLM 验证 ──
        _random_sleep(0.5, 1.5)
        llm_result = _llm_verify(extracted, fetch_result["text"], template)

        # Ark 只给建议与排序信号，不自动改写规则提取值。
        extracted = _apply_ark_review(extracted, llm_result)

        result: dict[str, Any] = {
            "meta": {
                "module": "webvoyager_scraper",
                "version": "1.0.0",
                "paradigm": "WebVoyager (ACL 2024) adapted for DDS",
                "phases": ["structure_analysis", "field_extraction", "llm_verification"],
                "scraped_at": ts,
                "url": url,
                "goal": goal,
                "template": template["name"],
                "trust_level": template.get("trust_level", "L3"),
                "execution_mode": "execute",
            },
            "source": {
                "SOURCE": template.get("source", "网页采集（WebVoyager 范式）"),
                "SOURCE_URL": url,
                "SOURCE_NOTE": template.get("source_note", ""),
                "robots_check": robots_result,
            },
            "fetch": {
                "status_code": fetch_result["status_code"],
                "elapsed_ms": fetch_result["elapsed_ms"],
                "content_length": len(fetch_result["html"]),
            },
            "phase1_structure": {
                "title": structure["title"],
                "meta_description": structure["meta_description"],
                "candidate_regions_count": len(structure["candidate_regions"]),
            },
            "phase2_extracted": extracted,
            "phase3_llm_verification": llm_result,
            "summary": {
                "fields_extracted": sum(1 for v in extracted.values() if v["extracted"]),
                "fields_total": len(extracted),
                "llm_verified": llm_result.get("verified", False),
                "ark_reviewed": llm_result.get("ark_reviewed", False),
                "review_required": llm_result.get("review_required", False),
                "overall_quality": llm_result.get("overall_quality", "unknown"),
            },
        }

        return result

    def _dry_run_result(
        self, url: str, goal: str, template: Optional[dict], ts: str
    ) -> dict[str, Any]:
        """生成 dry-run 示例结果。"""
        if template:
            fields = {
                f["key"]: {
                    "label": f["label"],
                    "value": f"[示例值] {f['label']}示例",
                    "extracted": True,
                    "confidence": 0.85,
                }
                for f in template["target_fields"]
            }
        else:
            fields = {
                "title": {"label": "标题", "value": "[示例值] 页面标题", "extracted": True, "confidence": 0.9},
                "main_content": {"label": "主要内容", "value": "[示例值] 页面正文摘要", "extracted": True, "confidence": 0.7},
            }

        return {
            "meta": {
                "module": "webvoyager_scraper",
                "version": "1.0.0",
                "paradigm": "WebVoyager (ACL 2024) adapted for DDS",
                "phases": ["structure_analysis", "field_extraction", "llm_verification"],
                "scraped_at": ts,
                "url": url,
                "goal": goal,
                "template": template["name"] if template else "auto",
                "trust_level": template.get("trust_level", "L3") if template else "L3",
                "execution_mode": "dry-run",
                "dry_run_note": "此为 dry-run 示例输出。实际抓取请加 --execute。",
            },
            "source": {
                "SOURCE": template.get("source", "网页采集（WebVoyager 范式）") if template else "网页采集（WebVoyager 范式）",
                "SOURCE_URL": url,
                "SOURCE_NOTE": template.get("source_note", "dry-run 示例") if template else "dry-run 示例",
                "robots_check": None,
            },
            "fetch": {
                "status_code": 200,
                "elapsed_ms": 0,
                "content_length": 0,
                "note": "dry-run: 未实际请求",
            },
            "phase1_structure": {
                "title": "[dry-run] 页面标题",
                "meta_description": "[dry-run] 页面描述",
                "candidate_regions_count": 3,
            },
            "phase2_extracted": fields,
            "phase3_llm_verification": {
                "verified": False,
                "note": "dry-run: 未调用 LLM",
                "overall_quality": "dry-run",
            },
            "summary": {
                "fields_extracted": len(fields),
                "fields_total": len(fields),
                "llm_verified": False,
                "overall_quality": "dry-run",
            },
        }


# ═══════════════════════════════════════════════════════════════════
#  降级路径：传统 requests + BS4 爬虫
# ═══════════════════════════════════════════════════════════════════

def traditional_scrape(url: str, goal: str) -> dict[str, Any]:
    """纯传统爬虫降级路径（无 LLM，无模板）。

    当 WebVoyager 三阶段流水线不可用时，回退到此方法。
    """
    if not HAS_BS4:
        return {"error": "传统爬虫需要 BeautifulSoup4: pip install requests beautifulsoup4"}

    ts = _ts()
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "DDS-Traditional/1.0"})
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "timestamp": ts}
        soup = BeautifulSoup(resp.text, "html.parser")
        # 提取页面标题
        title = soup.title.get_text(strip=True) if soup.title else ""
        # 提取所有文本
        text = soup.get_text(" ", strip=True)[:5000]
        # 提取所有链接
        links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            link_text = a.get_text(strip=True)
            if link_text:
                links.append({"text": link_text[:100], "href": href})
        # 提取表格
        tables = []
        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows:
                tables.append(rows)

        return {
            "meta": {
                "module": "webvoyager_scraper",
                "method": "traditional_fallback",
                "scraped_at": ts,
                "url": url,
                "goal": goal,
            },
            "source": {
                "SOURCE": "传统爬虫降级路径",
                "SOURCE_URL": url,
                "SOURCE_NOTE": "无 LLM 语义增强，纯 HTML 解析。trust_level=L3 辅助决策。",
            },
            "extracted": {
                "title": title,
                "text_preview": text,
                "links_count": len(links),
                "links": links[:20],
                "tables_count": len(tables),
                "tables": tables[:5],
            },
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "timestamp": ts}


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS WebVoyager 范式 AI 驱动网页数据采集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # dry-run 验证（输出模板示例，不实际请求）
  python scripts/webvoyager_scraper.py --dry-run

  # 使用内置模板 dry-run
  python scripts/webvoyager_scraper.py --template housing_price --url "https://example.com/loupan" --dry-run

  # 实际抓取（需 --execute）
  python scripts/webvoyager_scraper.py --template housing_price --url "https://example.com/loupan" --output result.json --execute

  # 自定义目标
  python scripts/webvoyager_scraper.py --url "https://..." --goal "提取楼盘名称、价格、面积" --output result.json --execute

  # 列出可用模板
  python scripts/webvoyager_scraper.py --list-templates

  # 传统降级爬虫
  python scripts/webvoyager_scraper.py --url "https://..." --traditional --output result.json
        """,
    )
    parser.add_argument("--url", help="目标网页 URL")
    parser.add_argument("--goal", default="", help="自然语言提取目标描述")
    parser.add_argument("--template", help=f"内置模板名: {list(TEMPLATES.keys())} 或外部 JSON 模板路径")
    parser.add_argument("--output", default="", help="输出 JSON 文件路径（默认 data_out/webvoyager/<timestamp>.json）")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，输出模板示例（默认）")
    parser.add_argument("--execute", action="store_true", help="真正执行抓取")
    parser.add_argument("--traditional", action="store_true", help="使用传统降级爬虫（requests + BS4，无 LLM）")
    parser.add_argument("--list-templates", action="store_true", help="列出所有内置模板")
    parser.add_argument("--no-robots", action="store_true", help="不检查 robots.txt")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP 请求超时秒数（默认 15）")
    parser.add_argument("--user-agent", default="DDS-WebVoyager/1.0", help="自定义 User-Agent")
    args = parser.parse_args()

    # ── 列出模板 ──
    if args.list_templates:
        print(f"\nDDS WebVoyager 内置采集模板 ({len(TEMPLATES)} 个):")
        print("=" * 60)
        for name, t in TEMPLATES.items():
            print(f"\n  {name}")
            print(f"    标签:     {t['label']}")
            print(f"    目标:     {t['goal']}")
            print(f"    信任级别: {t['trust_level']}")
            print(f"    字段:     {', '.join(f['label'] for f in t['target_fields'])}")
            print(f"    备注:     {t['source_note']}")
        print(f"\n用法: python scripts/webvoyager_scraper.py --template <name> --url <URL> --output result.json [--execute]")
        return 0

    # ── 纯 dry-run 验证 ──
    if args.dry_run and not args.url:
        scraper = WebVoyagerScraper()
        result = scraper.scrape(
            url="https://example.com/loupan/123",
            goal="提取楼盘名称、价格、面积、地址、开发商",
            template_name="housing_price",
            execute=False,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n[OK] dry-run 验证通过。模块可独立运行。")
        print(f"  实际抓取示例: python scripts/webvoyager_scraper.py --template housing_price --url <URL> --output result.json --execute")
        return 0

    # ── 需要 URL 的操作 ──
    if not args.url:
        parser.error("需要 --url 参数，或使用 --dry-run 验证模块")

    # ── 传统降级路径 ──
    if args.traditional:
        result = traditional_scrape(args.url, args.goal or "通用采集")
        output_path = _resolve_output_path(args.output or f"{_safe_slug(args.url)}_{datetime.now(UTC8).strftime('%Y%m%d_%H%M%S')}.json")
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n[OK] 已保存至: {output_path}")
        return 0

    # ── WebVoyager 主路径 ──
    scraper = WebVoyagerScraper(
        user_agent=args.user_agent,
        timeout=args.timeout,
        respect_robots=not args.no_robots,
    )

    execute = bool(args.execute)

    if not args.goal and not args.template:
        # 无 goal 无 template，自动使用通用模式
        args.goal = "自动提取页面主要内容"

    result = scraper.scrape(
        url=args.url,
        goal=args.goal,
        template_name=args.template,
        execute=execute,
    )

    # ── 输出 ──
    if args.output:
        output_path = _resolve_output_path(args.output)
    else:
        ts_slug = datetime.now(UTC8).strftime("%Y%m%d_%H%M%S")
        output_path = _resolve_output_path(f"{_safe_slug(args.url)}_{ts_slug}.json")

    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not execute:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n[Dry-Run] 模板示例已保存至: {output_path}")
        print(f"  实际抓取请加 --execute")
    else:
        summary = result.get("summary", {})
        quality = summary.get("overall_quality", "unknown")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n[Execute] 采集完成:")
        print(f"  字段提取: {summary.get('fields_extracted', 0)}/{summary.get('fields_total', 0)}")
        print(f"  LLM 验证: {'已通过' if summary.get('llm_verified') else '未通过/跳过'}")
        print(f"  整体质量: {quality}")
        print(f"  已保存至: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

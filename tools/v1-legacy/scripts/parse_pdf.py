"""
DDS PDF 解析管道
依赖安装：pip install pdfplumber pdf2image pytesseract Pillow volcengine-python-sdk[ark]
注意：OCR 依赖系统环境安装有 tesseract 可执行文件，pdf2image 依赖 poppler 工具链。

功能：
1. 第一级：pdfplumber 文本提取 + 正则
2. 第二级：pdf2image 转图片 + pytesseract OCR 识别 + 正则
3. 第三级：Ark 多模态模型兜底解析
"""

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

import pdfplumber
from pdf2image import convert_from_path
import pytesseract
from PIL import Image
try:
    from ark_runtime import get_ark_runtime
except ModuleNotFoundError:  # Package import: ``import scripts.parse_pdf``.
    from .ark_runtime import get_ark_runtime

# ── 全局配置 ─────────────────────────────────────────────────────────────────

TZ = timezone(timedelta(hours=8))

# 正则提取模式（按 Prompt 要求）
PATTERNS = {
    "floor_area_ratio":  r"容积率[：:]\s*([0-9.]+)",
    "coverage_ratio":    r"建筑密度[：:]\s*([0-9.]+)\s*%",
    "green_ratio":       r"绿地率[：:]\s*([0-9.]+)\s*%",
    "building_height_m": r"限高[：:]\s*([0-9.]+)\s*米",
    "area_sqm":          r"用地面积[：:]\s*([0-9,.]+)\s*平方米",
    "starting_price_cny":r"起始价[：:]\s*([0-9,.]+)\s*[万亿元]", # 匹配中文正则后需要转换
    "supply_years":      r"出让年限[：:]\s*(\d+)\s*年",
    "bid_deadline":      r"截.*?止.*?日期[：:]\s*(\d{4}[年\-]\d{1,2}[月\-]\d{1,2})",
}

POLICY_KEYWORDS = {
    "TOD":        ["TOD", "轨道交通上盖", "地铁上盖"],
    "限价":       ["限价", "价格上限", "不得超过"],
    "配建":       ["配建", "须配套", "保障性住房", "公租房"],
    "产业用地":   ["产业", "研发", "总部经济"],
    "文旅":       ["文旅", "旅游", "度假"],
    "限购":       ["限购", "购房资格"],
}

# 输出目录结构
OUTPUT_ROOT = Path(r"D:\Vault-assets\AI_Projects\DDS\data_out\pdfs\parsed")
ERROR_LOG = Path(r"D:\Vault-assets\AI_Projects\DDS\data_out\pdfs\errors.jsonl")

# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def clean_numeric(val_str):
    """清洗数值字符串中的逗号、空格"""
    if not val_str: return None
    clean = re.sub(r'[^\d.]', '', val_str.replace(',', ''))
    try:
        return float(clean)
    except:
        return None

def extract_by_regex(text: str) -> dict:
    """使用全局正则字典从文本中提取初步数据"""
    result = {}
    for key, pattern in PATTERNS.items():
        m = re.search(pattern, text)
        if m:
            val = m.group(1).strip()
            # 数值类型清洗
            if key in ["floor_area_ratio", "coverage_ratio", "green_ratio", "building_height_m", "area_sqm", "supply_years"]:
                result[key] = clean_numeric(val)
            else:
                result[key] = val # 暂时保留原字符串，等之后归一化处理
    
    # 计算匹配到的字段数
    result["_match_count"] = len(result)
    return result

def get_policy_tags(text: str) -> list[str]:
    """基于关键词匹配自动打标签"""
    tags = []
    for tag, keywords in POLICY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                tags.append(tag)
                break
    return tags

def gcs_upload(local_path: Path, gcs_uri: str) -> bool:
    """上传至 GCS"""
    try:
        cmd = ["gcloud", "storage", "cp", str(local_path), gcs_uri]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except:
        return False


def _sanitize_error_message(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)https?://[^\s\"'<>]+", "[external-url-redacted]", text)
    text = re.sub(
        r"(?i)(?:[a-z]:[\\/][^\s\"'<>]+|file://[^\s\"'<>]+)",
        "[local-path-redacted]",
        text,
    )
    return text[:2_000]


def log_error(pdf_path: Path, step: str, error_msg: str):
    """将解析失败记录写入 errors.jsonl"""
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(TZ).isoformat(),
        "source_name": pdf_path.name,
        "source_ref_sha256": hashlib.sha256(str(pdf_path).encode("utf-8")).hexdigest(),
        "step": step,
        "error": _sanitize_error_message(error_msg),
    }
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ── 核心解析步骤 ──────────────────────────────────────────────────────────────

def parse_level1_pdfplumber(pdf_path: str) -> tuple[str, dict]:
    """第一级：pdfplumber 文本抽取"""
    text_content = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: text_content.append(t)
        full_text = "\n".join(text_content)
        data = extract_by_regex(full_text)
        return full_text, data
    except Exception as e:
        return "", {"_match_count": 0, "error": str(e)}

def parse_level2_ocr(pdf_path: str) -> tuple[str, dict]:
    """第二级：OCR (pdf2image + pytesseract)"""
    try:
        # 仅渲染前 10 页避免超长 PDF 解析过慢
        pages = convert_from_path(pdf_path, 300, last_page=10)
        text_content = []
        for page_img in pages:
            # pytesseract 需要指定中文包 chi_sim
            text = pytesseract.image_to_string(page_img, lang='chi_sim')
            text_content.append(text)
        full_text = "\n".join(text_content)
        data = extract_by_regex(full_text)
        return full_text, data
    except Exception as e:
        return "", {"_match_count": 0, "error": str(e)}

def parse_level3_ark(pdf_path: str, fallback_text: str = "") -> dict:
    """第三级：Ark Responses 多模态兜底；失败时不切换其他供应商。"""
    try:
        # 将首页转为 base64 图片（一般关键规划条件在首页或次页表格）
        # 为了控制 API payload，只发前 3 页
        images = convert_from_path(pdf_path, 150, last_page=3)
        content = []
        image_payloads: list[bytes] = []
        
        # 如果之前抽取的文本杂乱，也可作为辅助文本传入
        if fallback_text:
            content.append({
                "type": "input_text",
                "text": f"以下是通过机器 OCR 获取的部分不可靠文本，供参考：\n{fallback_text[:1000]}"
            })
            
        for img in images:
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=80)
            image_payload = buffered.getvalue()
            image_payloads.append(image_payload)
            img_b64 = base64.b64encode(image_payload).decode("utf-8")
            content.append({
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{img_b64}",
            })
            
        # 组装 Prompt 指导模型输出结构化 JSON
        prompt = """
请从所提供的土地招拍挂文件的页面图像中，精确提取关键规划和财务参数。
要求以纯 JSON 格式返回，不要包含 markdown 标记或解释性文字，严格对应以下结构，未找到的字段填 null：

{
  "city": "城市",
  "district": "区县",
  "land_use": "土地用途",
  "area_sqm": 数字, // 用地面积，单位平方米
  "floor_area_ratio": 数字, // 容积率
  "coverage_ratio": 数字, // 建筑密度（如30%应填0.3）
  "green_ratio": 数字, // 绿地率（如35%应填0.35）
  "building_height_m": 数字, // 建筑限高，单位米
  "supply_years": 数字, // 出让年限，单位年
  "starting_price_cny": 数字, // 起始价，单位元 (注意单位换算：万元、亿元)
  "deposit_cny": 数字, // 保证金，单位元
  "listing_start_date": "YYYY-MM-DD", // 挂牌起始时间
  "bid_deadline": "YYYY-MM-DD", // 截止时间
  "planning_conditions": "完整规划条件的汇总或核心原文",
  "constraints": ["所有的限制性约束文本段落列表", ...],
  "opportunities": ["政策利好或可上浮条款列表", ...]
}
请根据上下文仔细审题。尤其是涉及金额单位时请换算成基础货币单位【元】。
"""
        content.append({"type": "input_text", "text": prompt})

        runtime = get_ark_runtime()
        resp_text = runtime.complete(
            [{"role": "user", "content": content}],
            instructions="只依据输入页面提取字段；只输出 JSON，不得补写未出现的规划或经营事实。",
            max_output_tokens=4096,
            temperature=0,
            json_object=True,
        )
        # 尝试提取 JSON 块
        json_match = re.search(r'(\{[\s\S]*\})', resp_text)
        if json_match:
            validated = _validate_ark_pdf_payload(json.loads(json_match.group(1)))
        else:
            validated = _validate_ark_pdf_payload(json.loads(resp_text))
        # Locator is generated by DDS from the exact submitted bytes. The model
        # cannot supply or override this metadata because it is outside the
        # accepted Ark response schema.
        validated["_source_inputs"] = _build_pdf_source_inputs(
            image_payloads, fallback_text=fallback_text
        )
        return validated
            
    except Exception:
        return {
            "error": "Ark 多模态解析不可用",
            "baseline": True,
            "reason_code": "ark_unavailable",
        }


_ARK_PDF_NUMERIC_RANGES = {
    "area_sqm": (0.0, 1_000_000_000.0),
    "floor_area_ratio": (0.0, 20.0),
    "coverage_ratio": (0.0, 1.0),
    "green_ratio": (0.0, 1.0),
    "building_height_m": (0.0, 1_000.0),
    "supply_years": (0.0, 100.0),
    "starting_price_cny": (0.0, 1_000_000_000_000_000.0),
    "deposit_cny": (0.0, 1_000_000_000_000_000.0),
}
_ARK_PDF_TEXT_FIELDS = {"city", "district", "land_use", "planning_conditions"}
_ARK_PDF_DATE_FIELDS = {"listing_start_date", "bid_deadline"}
_ARK_PDF_LIST_FIELDS = {"constraints", "opportunities"}
_ARK_PDF_STRICTLY_POSITIVE_FIELDS = {
    "area_sqm",
    "floor_area_ratio",
    "building_height_m",
    "supply_years",
    "starting_price_cny",
}
_ARK_PDF_ALLOWED_FIELDS = (
    set(_ARK_PDF_NUMERIC_RANGES)
    | _ARK_PDF_TEXT_FIELDS
    | _ARK_PDF_DATE_FIELDS
    | _ARK_PDF_LIST_FIELDS
)


def _build_pdf_source_inputs(
    page_payloads: list[bytes], *, fallback_text: str = ""
) -> list[dict]:
    if not page_payloads or len(page_payloads) > 3:
        raise ValueError("PDF source locator requires one to three submitted pages")
    sources = []
    for page_number, payload in enumerate(page_payloads, start=1):
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("PDF source locator page payload is invalid")
        sources.append(
            {
                "locator": {"kind": "page", "page": page_number},
                "content_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    fallback_snippet = str(fallback_text or "")[:1000]
    if fallback_snippet:
        sources.append(
            {
                "locator": {
                    "kind": "paragraph",
                    "paragraph_id": "ocr-fallback-0-1000",
                },
                "content_sha256": hashlib.sha256(
                    fallback_snippet.encode("utf-8")
                ).hexdigest(),
            }
        )
    return sources


def _validate_ark_pdf_payload(payload: object) -> dict:
    """Validate Ark extraction as a review draft before it can fill missing fields."""
    if not isinstance(payload, dict) or set(payload) - _ARK_PDF_ALLOWED_FIELDS:
        raise ValueError("Ark PDF payload shape is invalid")
    validated: dict = {}
    for key, value in payload.items():
        if value is None:
            validated[key] = None
            continue
        if key in _ARK_PDF_NUMERIC_RANGES:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Ark PDF numeric field is invalid")
            low, high = _ARK_PDF_NUMERIC_RANGES[key]
            number = float(value)
            if not low <= number <= high:
                raise ValueError("Ark PDF numeric field is outside allowed range")
            if key in _ARK_PDF_STRICTLY_POSITIVE_FIELDS and number <= 0:
                raise ValueError("Ark PDF numeric field must be positive")
            validated[key] = value
        elif key in _ARK_PDF_TEXT_FIELDS:
            if not isinstance(value, str) or len(value) > 10_000:
                raise ValueError("Ark PDF text field is invalid")
            validated[key] = value.strip()
        elif key in _ARK_PDF_DATE_FIELDS:
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Ark PDF date field is invalid")
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Ark PDF date field is invalid") from exc
            validated[key] = value
        elif key in _ARK_PDF_LIST_FIELDS:
            if (
                not isinstance(value, list)
                or len(value) > 100
                or any(not isinstance(item, str) or len(item) > 2_000 for item in value)
            ):
                raise ValueError("Ark PDF list field is invalid")
            validated[key] = [item.strip() for item in value]
    return validated

# ── 处理与组装 ──────────────────────────────────────────────────────────────

def process_single_pdf(pdf_path: Path, source: str, bucket: str) -> dict:
    print(f"正在处理: {pdf_path.name}")
    
    start_t = time.time()
    parcel_id = pdf_path.stem # 默认文件名即 parcel_id
    
    # 默认初始化空结构
    final_data = {
        "source": "pdf_extract",
        "category": "land_tender_document",
        "parse_method": "pending",
        "parcel_id": parcel_id,
        "source_pdf_gcs": f"{bucket}/real_estate_data/pdfs/raw/{source}/.../{pdf_path.name}", # 实际由外部修正或占位
        "parsed_at": datetime.now(timezone.utc).isoformat() + "Z",
        "city": None,
        "district": None,
        "land_use": None,
        "area_sqm": None,
        "floor_area_ratio": None,
        "coverage_ratio": None,
        "green_ratio": None,
        "building_height_m": None,
        "supply_years": None,
        "starting_price_cny": None,
        "deposit_cny": None,
        "listing_start_date": None,
        "bid_deadline": None,
        "planning_conditions": None,
        "policy_tags": [],
        "constraints": [],
        "opportunities": [],
        "confidence": "low",
        "raw_text": "",
        "raw_record": {}
    }
    
    # ============ 阶段 1: pdfplumber ============
    method = "pdfplumber"
    text, extracted = parse_level1_pdfplumber(str(pdf_path))
    
    # ============ 阶段 2: OCR Fallback ============
    # 判定：文本量 < 50 字
    if len(text.strip()) < 50:
        print(" -> 触发 OCR 识别")
        method = "ocr"
        text, extracted = parse_level2_ocr(str(pdf_path))
        
    final_data["raw_text"] = text
    final_data["policy_tags"] = get_policy_tags(text)
    
    # 将正则初提结果映射给主对象
    for k, v in extracted.items():
        if not k.startswith("_") and k in final_data:
            final_data[k] = v
            
    # ============ 阶段 3: Ark Responses 多模态 Fallback ============
    # 判定：前两级全失败，或者匹配成功的字段数 < 3 (不含 _match_count 和 error)
    match_count = extracted.get("_match_count", 0)
    
    if match_count < 3:
        print(f" -> 正则命中过低({match_count})，触发 Ark 多模态兜底")
        llm_result = parse_level3_ark(str(pdf_path), text)
        if "error" not in llm_result:
            method = "ark_responses_multimodal"
            source_inputs = llm_result.get("_source_inputs")
            ark_draft = {
                key: value for key, value in llm_result.items() if key != "_source_inputs"
            }
            # Ark 结果只是待复核草稿：只能填空，不覆盖规则/OCR 已提取值。
            for k, v in ark_draft.items():
                if k in final_data and final_data[k] in (None, "", []):
                    # 处理大模型的百分比转换逻辑等脏活
                    if k in ["coverage_ratio", "green_ratio"] and isinstance(v, (int, float)) and v > 1:
                        final_data[k] = round(v / 100, 4)
                    else:
                        final_data[k] = v
            final_data["raw_record"]["ark_raw"] = ark_draft
            if source_inputs:
                final_data["raw_record"]["ark_source_inputs"] = source_inputs
            final_data["confidence"] = "medium"
            final_data["review_required"] = True
            final_data["model_source_role"] = "ark_draft"
        else:
            print(f" -> Ark 多模态调用失败: {llm_result['error']}")
            log_error(pdf_path, "ark_responses_multimodal", llm_result['error'])
    else:
        final_data["confidence"] = "medium" if method == "ocr" else "high"

    final_data["parse_method"] = method
    
    # 处理日期规范化等后期清理 (在此处扩充，如清洗 starting_price_cny 带有中文时)
    if isinstance(final_data["starting_price_cny"], str):
        price_str = final_data["starting_price_cny"]
        # 处理亿元、万元字样
        m_num = re.search(r'([0-9,.]+)', price_str.replace(',', ''))
        if m_num:
            base_val = float(m_num.group(1))
            if "亿" in price_str: base_val *= 100000000
            elif "万" in price_str: base_val *= 10000
            final_data["starting_price_cny"] = base_val
            
    print(f"解析完毕 ({method}) | 用时: {time.time() - start_t:.2f}秒")
    return final_data

# ── 主控制循环 ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DDS PDF 解析工具")
    parser.add_argument("--input", required=True, help="单个 PDF 文件路径 或 目录路径")
    parser.add_argument("--source", required=True, choices=["landchina", "hangzhou", "sanya"], help="来源系统")
    parser.add_argument("--bucket", default="gs://dds-data-lake", help="GCS 桶名称")
    parser.add_argument("--force", action="store_true", help="强制重新解析已存在的文件")
    parser.add_argument("--no-upload", action="store_true", help="不自动上传 GCS")
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    
    # 1. 获取 PDF 文件列表
    pdf_files = []
    if input_path.is_file():
        if input_path.suffix.lower() == '.pdf':
            pdf_files.append(input_path)
    elif input_path.is_dir():
        pdf_files = list(input_path.rglob("*.pdf"))
    
    if not pdf_files:
        print(f"未找到符合要求的 PDF 文件: {args.input}")
        return
        
    print(f"发现 {len(pdf_files)} 个 PDF 等待解析...")
    
    # 2. 遍历执行
    now = datetime.now(TZ)
    date_path = now.strftime("%Y/%m/%d")
    
    success_count = 0
    
    for pdf in pdf_files:
        try:
            parcel_id = pdf.stem
            out_dir = OUTPUT_ROOT / args.source / date_path
            out_path = out_dir / f"{parcel_id}_parsed.json"
            
            if out_path.exists() and not args.force:
                print(f"[跳过] 文件已解析过: {out_path.name}")
                continue
                
            # 解析逻辑
            result = process_single_pdf(pdf, args.source, args.bucket)
            
            # 记录真实文件的 GCS 源（假设在 raw 目录下同样的日期结构，可通过输入路径推测）
            # 如果无法推测，使用外部注入的 GCS 链接
            
            # 落盘保存
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
                
            # 上传至 GCS
            if not args.no_upload:
                gcs_dest = f"{args.bucket}/real_estate_data/pdfs/parsed/{args.source}/{date_path}/{out_path.name}"
                upload_ok = gcs_upload(out_path, gcs_dest)
                if upload_ok:
                    print(f" -> 已上传 GCS: {gcs_dest}")
            
            success_count += 1
            
        except Exception as e:
            print(f"[主流程错误] 处理 {pdf.name} 崩溃: {str(e)}")
            log_error(pdf, "main_loop", str(e))
            traceback.print_exc()
            
    print(f"\n任务完成. 成功解析 {success_count}/{len(pdf_files)} 个文件。")
    if ERROR_LOG.exists():
        print(f"请检查错误日志: {ERROR_LOG}")

if __name__ == "__main__":
    main()

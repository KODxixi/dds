"""DDS Inbox Scanner — 递归扫描 inbox 目录，支持建筑行业全流程（全生命周期 + 专业学科）文件分类。

用法:
    python inbox_scanner.py <inbox_path> [--out manifest.json]
    python inbox_scanner.py --selftest
"""

import json
import os
import re
import sys
from pathlib import Path

# ── 生命周期阶段规则 ──────────────────────────────────────

STAGES = {
    "STAGE_FEASIBILITY": {
        "patterns": [r"估算", r"测算", r"可研", r"可行性", r"地块", r"拿地", r"投资", r"回笼", r"财务", r"竞品"],
        "desc": "前期策划与可行性研究"
    },
    "STAGE_CONCEPT_SCHEME": {
        "patterns": [r"方案", r"汇报", r"概念", r"日照", r"文本", r"设计说明书", r"效果图"],
        "desc": "方案/概念设计"
    },
    "STAGE_PRELIMINARY": {
        "patterns": [r"初步设计", r"概算", r"消防专题", r"专项技术"],
        "desc": "初步设计"
    },
    "STAGE_CONSTRUCTION_DWG": {
        "patterns": [r"施工图", r"详图", r"大样", r"强条", r"审查意见", r"图审", r"计算书"],
        "desc": "施工图设计"
    },
    "STAGE_TENDERING": {
        "patterns": [r"招标", r"投标", r"清单", r"控制价", r"招标文件", r"工程量清单", r"BOQ", r"限价"],
        "desc": "招投标与采购"
    },
    "STAGE_CONSTRUCTION": {
        "patterns": [r"施工方案", r"设计变更", r"联系单", r"施工组织", r"现场照片", r"协调记录"],
        "desc": "施工建设"
    },
    "STAGE_HANDOVER": {
        "patterns": [r"竣工图", r"竣工验收", r"消防验收", r"交付标准", r"验收意见"],
        "desc": "竣工验收与交付"
    },
    "STAGE_OPERATION_MAINT": {
        "patterns": [r"运维", r"维护手册", r"设备台账", r"能耗报告", r"物业管理"],
        "desc": "运营维护与资产管理"
    }
}

# ── 专业学科分类规则 ──────────────────────────────────────

DISCIPLINES = {
    "DISC_PLANNING": {
        "patterns": [r"控规", r"红线", r"退界", r"规划", r"总平面", r"总图"],
        "desc": "规划/总图"
    },
    "DISC_ARCH": {
        "patterns": [r"建筑", r"户型", r"立面", r"剖面", r"日照", r"外立面", r"效果图", r"方案"],
        "desc": "建筑"
    },
    "DISC_STR": {
        "patterns": [r"结构", r"钢筋", r"混凝土", r"荷载", r"抗震", r"地基", r"基础"],
        "desc": "结构"
    },
    "DISC_MEP": {
        "patterns": [r"给排水", r"消防", r"暖通", r"空调", r"新风", r"配电", r"强电", r"弱电", r"智能化"],
        "desc": "机电/设备"
    },
    "DISC_COST": {
        "patterns": [r"概算", r"预算", r"成本", r"清单", r"控制价", r"造价", r"估算"],
        "desc": "概预算/成本"
    },
    "DISC_BIM": {
        "patterns": [r"BIM", r"Revit", r"模型", r"IFC", r"数字孪生"],
        "desc": "BIM/数字孪生"
    },
    "DISC_CIVIL": {
        "patterns": [r"基坑", r"地勘", r"勘察", r"土石方", r"市政", r"管网"],
        "desc": "市政/岩土"
    }
}

SKIP_DIRS = {"extracted_images", "extracted_docx_images", "extracted_txt",
             "__pycache__", ".git", "node_modules"}
SKIP_EXTS = {".tmp", ".bak", ".log", ".pyc"}


def match_dimension(name: str, mapping: dict, default_val: str) -> tuple[str, float]:
    """通用匹配逻辑，返回维度编码 and 置信度。"""
    best_key = default_val
    best_score = 0.0

    for key, info in mapping.items():
        score = 0.0
        for pat in info["patterns"]:
            if re.search(pat, name):
                score = 0.8
                break
        if score > best_score:
            best_score = score
            best_key = key

    return best_key, best_score


def classify_file_matrix(filepath: str, size_mb: float) -> dict:
    """按生命周期和专业双重维度分类。"""
    name = Path(filepath).stem
    ext = Path(filepath).suffix.lower()

    if ext in SKIP_EXTS:
        return {"stage": "SKIP", "discipline": "SKIP", "category": "SKIP", "confidence": 1.0}

    # 1. 匹配生命周期阶段
    stage, stage_conf = match_dimension(name, STAGES, "STAGE_FEASIBILITY")
    # 2. 匹配专业学科
    discipline, disc_conf = match_dimension(name, DISCIPLINES, "DISC_ARCH")

    # 针对后缀做细调
    if ext in {".xlsx", ".xls", ".xlsm"}:
        discipline = "DISC_COST"
        disc_conf = max(disc_conf, 0.7)
    elif ext in {".ifc", ".rvt"}:
        discipline = "DISC_BIM"
        disc_conf = max(disc_conf, 0.9)

    # 组合最终推荐分类名
    category_map = {
        ("STAGE_FEASIBILITY", "DISC_COST"): "FEAS_COST_MODEL",
        ("STAGE_CONCEPT_SCHEME", "DISC_ARCH"): "CONCEPT_PRESENTATION",
        ("STAGE_PRELIMINARY", "DISC_COST"): "PRELIM_BUDGET",
        ("STAGE_CONSTRUCTION_DWG", "DISC_ARCH"): "CONSTR_DRAWING",
        ("STAGE_CONSTRUCTION_DWG", "DISC_STR"): "CONSTR_DRAWING",
        ("STAGE_TENDERING", "DISC_COST"): "TENDER_BOQ",
        ("STAGE_CONSTRUCTION", "DISC_ARCH"): "CONST_CHANGE",
        ("STAGE_HANDOVER", "DISC_ARCH"): "HANDOVER_DOC",
        ("STAGE_OPERATION_MAINT", "DISC_BIM"): "OPS_MANUAL",
    }
    
    category = category_map.get((stage, discipline), f"{stage[6:]}_{discipline[5:]}")
    confidence = round((stage_conf + disc_conf) / 2.0, 2)
    if confidence == 0.0:
        confidence = 0.2  # 兜底默认值

    return {
        "stage": stage,
        "stage_desc": STAGES.get(stage, {}).get("desc", "未知阶段"),
        "discipline": discipline,
        "discipline_desc": DISCIPLINES.get(discipline, {}).get("desc", "未知专业"),
        "category": category,
        "confidence": confidence
    }


def scan_inbox(inbox_path: str) -> list[dict]:
    """递归扫描 inbox 目录，返回双维分类清单。"""
    manifest = []
    inbox = Path(inbox_path)

    if not inbox.is_dir():
        raise FileNotFoundError(f"inbox 目录不存在: {inbox_path}")

    for root, dirs, files in os.walk(inbox):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in sorted(files):
            fpath = Path(root) / fname
            ext = fpath.suffix.lower()
            if ext in SKIP_EXTS:
                continue

            size_bytes = fpath.stat().st_size
            size_mb = round(size_bytes / (1024 * 1024), 2)
            rel_path = str(fpath.relative_to(inbox))

            classification = classify_file_matrix(str(fpath), size_mb)

            manifest.append({
                "path": rel_path,
                "filename": fname,
                "ext": ext,
                "size_mb": size_mb,
                **classification
            })

    return manifest


def print_summary(manifest: list[dict]) -> None:
    print(f"\n{'='*75}")
    print(f"  DDS Inbox Scanner — 建筑行业全流程扫描完成")
    print(f"{'='*75}")
    print(f"  文件总数: {len(manifest)}  |  总大小: {sum(m['size_mb'] for m in manifest):.1f} MB")
    print(f"{'─'*75}")
    print(f"  {'文件名':<35} | {'项目阶段':<12} | {'专业':<8} | {'类别':<10}")
    print(f"{'─'*75}")
    for m in manifest:
        fn = m["filename"]
        if len(fn) > 35:
            fn = fn[:32] + "..."
        print(f"  {fn:<35} | {m['stage_desc'][:10]:<12} | {m['discipline_desc'][:8]:<8} | {m['category']}")
    print(f"{'='*75}\n")


def selftest():
    """自测：验证全流程分类逻辑。"""
    test_cases = [
        ("0710国投建华望府襄阳_汇报文本.pdf", 207.0, "STAGE_CONCEPT_SCHEME", "DISC_ARCH"),
        ("20260508星河国际项目开发投资测算_成本测算.xlsx", 0.7, "STAGE_FEASIBILITY", "DISC_COST"),
        ("初步设计总说明与概算书.pdf", 22.0, "STAGE_PRELIMINARY", "DISC_COST"),
        ("住宅楼结构施工图说明.dwg", 4.5, "STAGE_CONSTRUCTION_DWG", "DISC_STR"),
        ("工程量清单控制价.xlsx", 1.3, "STAGE_TENDERING", "DISC_COST"),
        ("设计变更联系单-01.docx", 0.1, "STAGE_CONSTRUCTION", "DISC_ARCH"),
        ("2号楼竣工消防验收合格证.pdf", 0.8, "STAGE_HANDOVER", "DISC_MEP"),
        ("给排水管道运行维护手册.docx", 2.3, "STAGE_OPERATION_MAINT", "DISC_MEP")
    ]

    passed = 0
    for fname, size_mb, exp_stage, exp_disc in test_cases:
        res = classify_file_matrix(fname, size_mb)
        ok = (res["stage"] == exp_stage) and (res["discipline"] == exp_disc)
        status = "✅" if ok else "❌"
        print(f"  {status} {fname[:25]:<26} → 阶段: {res['stage'][6:]:<12} 专业: {res['discipline'][5:]:<10} (置信度: {res['confidence']:.0%})")
        if ok:
            passed += 1

    print(f"\n  结果: {passed}/{len(test_cases)} 通过")
    if passed == len(test_cases):
        print("  [PASS] 建筑全流程分类自测全部通过！")
    else:
        print("  [FAIL] 分类逻辑未对齐！")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("用法: python inbox_scanner.py <inbox_path> [--out manifest.json]")
        print("      python inbox_scanner.py --selftest")
        sys.exit(1)

    if sys.argv[1] == "--selftest":
        selftest()
        return

    inbox_path = sys.argv[1]
    out_path = None
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            out_path = sys.argv[idx + 1]

    manifest = scan_inbox(inbox_path)
    print_summary(manifest)

    # 导出
    if not out_path:
        out_path = Path(inbox_path).parent / "inbox_manifest.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  清单已写入: {out_path}")


if __name__ == "__main__":
    main()

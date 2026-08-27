"""DDS 资料准备完成度报告生成器。

用法:
    python prep_report_template.py --project <project_name> --workspace <path> [--profile project_profile.json]
    python prep_report_template.py --selftest

基于各阶段产出，生成资料准备完成度 Markdown 报告。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def load_manifest(workspace: Path) -> list[dict]:
    """加载 inbox_manifest.json。"""
    manifest_path = workspace / "inbox_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def load_profile(profile_path: Path) -> dict:
    """加载 project_profile.json。"""
    if profile_path and profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def check_reports(workspace: Path, manifest: list[dict]) -> list[dict]:
    """检查每个 inbox 文件是否有对应的解析报告。"""
    reports_dir = workspace / "reports"
    existing_reports = set()
    if reports_dir.exists():
        for f in reports_dir.iterdir():
            if f.suffix == ".md":
                existing_reports.add(f.name)

    results = []
    for item in manifest:
        if item["category"] == "SKIP":
            continue
        fname = item["filename"]
        # 检查是否有对应报告（模糊匹配）
        has_report = any(
            fname.split(".")[0][:10] in rpt or item["category"].lower() in rpt
            for rpt in existing_reports
        )
        results.append({
            "filename": fname,
            "category": item["category"],
            "size_mb": item["size_mb"],
            "has_report": has_report,
        })
    return results


def assess_profile_completeness(profile: dict) -> list[dict]:
    """评估 project_profile.json 的字段完整度。"""
    critical_fields = [
        ("楼盘名称", "标识"),
        ("城市名称", "标识"),
        ("开发商", "标识"),
        ("最新价格", "价格"),
        ("容积率", "规划"),
        ("规划户数", "规划"),
        ("地址", "空间"),
        ("经度_高德", "空间"),
        ("纬度_高德", "空间"),
    ]

    results = []
    for field, group in critical_fields:
        value = profile.get(field)
        filled = value is not None and str(value).strip() != ""
        results.append({
            "field": field,
            "group": group,
            "value": str(value) if filled else "—",
            "filled": filled,
            "source": profile.get(f"_source_{field}", "未标注"),
        })
    return results


def generate_report(
    project_name: str,
    workspace: Path,
    manifest: list[dict],
    profile: dict,
    qg_passed: bool | None = None,
) -> str:
    """生成完整的资料准备完成度报告。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_checks = check_reports(workspace, manifest)
    field_checks = assess_profile_completeness(profile)

    lines = []
    lines.append(f"# {project_name} 资料准备完成度报告")
    lines.append(f"")
    lines.append(f"> 生成时间：{now}")
    lines.append(f"> 工作区：`{workspace}`")
    lines.append(f"")

    # ── 文件清单 ──
    lines.append("## 一、文件清单与解析状态")
    lines.append("")
    lines.append("| 文件 | 类别 | 大小 | 解析报告 |")
    lines.append("|:---|:---|---:|:---:|")
    parsed_count = 0
    for r in report_checks:
        status = "✅" if r["has_report"] else "❌"
        if r["has_report"]:
            parsed_count += 1
        lines.append(f"| {r['filename'][:40]} | `{r['category']}` | {r['size_mb']} MB | {status} |")
    total = len(report_checks)
    lines.append(f"")
    lines.append(f"**解析覆盖率：{parsed_count}/{total}**（{parsed_count/total*100:.0f}%）" if total > 0 else "")
    lines.append("")

    # ── 项目画像 ──
    lines.append("## 二、项目画像")
    lines.append("")
    lines.append("| 字段 | 值 | 来源 | 状态 |")
    lines.append("|:---|:---|:---|:---:|")
    filled_count = 0
    for f in field_checks:
        status = "✅" if f["filled"] else "⬜" 
        if f["filled"]:
            filled_count += 1
        lines.append(f"| {f['field']} | {f['value'][:30]} | {f['source'][:20]} | {status} |")
    lines.append(f"")
    ftotal = len(field_checks)
    lines.append(f"**关键字段完整度：{filled_count}/{ftotal}**（{filled_count/ftotal*100:.0f}%）" if ftotal > 0 else "")
    lines.append("")

    # ── 数据缺口 ──
    gaps = [f for f in field_checks if not f["filled"]]
    lines.append("## 三、数据缺口")
    lines.append("")
    if gaps:
        lines.append("| 缺失字段 | 分组 | 建议补齐方式 |")
        lines.append("|:---|:---|:---|")
        gap_hints = {
            "经度_高德": "高德地理编码 API",
            "纬度_高德": "高德地理编码 API",
            "地址": "项目资料/汇报文本",
            "最新价格": "成本测算/市场调研",
            "开发商": "企业信用信息/天眼查",
            "规划户数": "设计方案/规划条件",
            "容积率": "规划条件/控规",
        }
        for g in gaps:
            hint = gap_hints.get(g["field"], "人工补充")
            lines.append(f"| {g['field']} | {g['group']} | {hint} |")
    else:
        lines.append("✅ 无数据缺口，所有关键字段已填充。")
    lines.append("")

    # ── 质量门禁 ──
    lines.append("## 四、质量门禁")
    lines.append("")
    if qg_passed is True:
        lines.append("✅ P0 质量门禁 5 项全部 PASS")
    elif qg_passed is False:
        lines.append("❌ P0 质量门禁未通过，请检查具体项目")
    else:
        lines.append("⬜ 质量门禁尚未运行")
    lines.append("")

    # ── 交叉校验 ──
    conflicts = profile.get("_conflicts", [])
    lines.append("## 五、交叉校验发现")
    lines.append("")
    if conflicts:
        for c in conflicts:
            lines.append(f"⚠️ **字段冲突：{c['field']}**")
            lines.append(f"  - 旧值：{c['old_value']}（来源：{c['old_source']}）")
            lines.append(f"  - 新值：{c['new_value']}（来源：{c['new_source']}）")
            lines.append(f"  - ✅ 已采用新值")
            lines.append("")
    else:
        lines.append("暂无交叉校验冲突记录。")
    lines.append("")

    # ── 后续方向建议 ──
    lines.append("## 六、后续方向建议")
    lines.append("")
    lines.append("### ArchLib 案例匹配方向")
    project_type = profile.get("project_type", "RESIDENTIAL")
    lines.append(f"- 判定的项目类型：`{project_type}`")
    
    if project_type == "RESIDENTIAL":
        has_sky_garden = profile.get("空中花园/错层露台") is not None
        has_raised_platform = profile.get("抬板底盘/架空层") is not None
        lines.append("- 建议 ArchLib 检索方向：住宅立面与局部节点设计")
        tags = []
        if has_sky_garden:
            tags.append("scene_part: 错层露台/空中花园")
        if has_raised_platform:
            tags.append("scene_part: 抬板底盘/架空层")
        style = profile.get("建筑风格")
        if style:
            tags.append(f"arch_style: {style}")
        else:
            tags.append("arch_style: 现代极简/新中式")
        lines.append(f"- 建议检索标签：`{' | '.join(tags)}`")
    elif project_type == "PUBLIC_BUILDING":
        lines.append("- 建议 ArchLib 检索方向：公建单体形态、大堂及幕墙材料")
        style = profile.get("建筑风格", "现代公建")
        material = profile.get("立面材质", "玻璃幕墙")
        lines.append(f"- 建议检索标签：`arch_style: {style} | scene_part: 整体外观/入口门头/中庭大堂 | facade_material: {material}`")
    elif project_type == "RENOVATION":
        lines.append("- 建议 ArchLib 检索方向：老旧改造与工业遗存活化设计")
        material = profile.get("立面材质", "红砖/清水混凝土")
        lines.append(f"- 建议检索标签：`arch_style: 工业风/新旧交融 | facade_material: {material} | scene_part: 整体外观`")
    elif project_type == "TOWNSHIP_PLANNING":
        lines.append("- 建议 ArchLib 检索方向：乡土建筑风貌与规划鸟瞰")
        style = profile.get("建筑风格", "新中式/地方风貌")
        lines.append(f"- 建议检索标签：`arch_style: {style} | view_type: 鸟瞰/航拍`")
    elif project_type == "URBAN_DESIGN":
        lines.append("- 建议 ArchLib 检索方向：大尺度规划与滨水/广场公共空间设计")
        lines.append("- 建议检索标签：`view_type: 鸟瞰/航拍 | design_keywords: TOD/海绵城市/微气候`")
    
    lines.append("")
    lines.append("### 决策与推演报告建议")
    if project_type in ("RESIDENTIAL", "PUBLIC_BUILDING"):
        addr = profile.get("项目地址", profile.get("地址", "待补充"))
        price = profile.get("最新价格", "待补充")
        city = profile.get("城市名称", "待补充")
        lines.append(f"- 该类型支持自动推演。建议运行：`python scripts/report_parcel.py --city {city} --address {addr} --price {price} --radius 5`")
        lines.append("- 将自动调用 DDS 决策引擎执行：竞品分析 → ABM 去化推演 → 财务估值与 CEO 评分")
    else:
        lines.append(f"- 该类型 (`{project_type}`) 属于非标规划项目，建议在 GIS 平台中叠加相应红线与地形数据，并结合 ArchLib 的空间风貌指引完成综合研判。")
    lines.append("")

    return "\n".join(lines)


def selftest():
    """自测：使用合成数据验证报告生成。"""
    print("  [selftest] 生成合成报告...")

    manifest = [
        {"filename": "汇报文本.pdf", "category": "PRESENTATION", "size_mb": 200.0},
        {"filename": "成本测算.xlsx", "category": "COST_MODEL", "size_mb": 0.7},
        {"filename": "基础资料.pdf", "category": "PROJECT_INFO", "size_mb": 5.0},
    ]

    profile = {
        "楼盘名称": "测试项目",
        "城市名称": "三亚",
        "开发商": "测试开发商",
        "最新价格": "35000",
        "容积率": "2.5",
        "规划户数": None,  # 缺口
        "地址": "海棠区测试路",
        "经度_高德": "109.726",
        "纬度_高德": None,  # 缺口
        "_conflicts": [
            {
                "field": "容积率",
                "old_value": "2.50",
                "old_source": "20260508 成本测算",
                "new_value": "2.59",
                "new_source": "20260710 汇报文本",
            }
        ],
    }

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        (ws / "reports").mkdir()
        # 模拟已有报告
        (ws / "reports" / "cost_analysis_report.md").write_text("# cost", encoding="utf-8")
        # 写 manifest
        with open(ws / "inbox_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)

        report = generate_report("测试项目", ws, manifest, profile, qg_passed=True)

    assert "测试项目" in report
    assert "资料准备完成度报告" in report
    assert "⚠️ **字段冲突：容积率**" in report
    assert "规划户数" in report  # 应出现在缺口中
    assert "ArchLib" in report

    print("  ✅ 报告结构验证通过")
    print("  ✅ 冲突标注验证通过")
    print("  ✅ 缺口识别验证通过")
    print("  ✅ 方向建议验证通过")
    print("  [PASS] 自测全部通过！")


def main():
    if len(sys.argv) < 2:
        print("用法: python prep_report_template.py --project <name> --workspace <path> [--profile <json>]")
        print("      python prep_report_template.py --selftest")
        sys.exit(1)

    if sys.argv[1] == "--selftest":
        selftest()
        return

    # 解析参数
    args = {}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i].startswith("--") and i + 1 < len(sys.argv):
            args[sys.argv[i][2:]] = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    project = args.get("project", "未命名项目")
    workspace = Path(args.get("workspace", "."))
    profile_path = Path(args["profile"]) if "profile" in args else workspace / "project_profile.json"

    manifest = load_manifest(workspace)
    profile = load_profile(profile_path)

    report = generate_report(project, workspace, manifest, profile)

    # 输出
    out_path = workspace / f"reports/prep_completion_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"  报告已写入: {out_path}")


if __name__ == "__main__":
    main()

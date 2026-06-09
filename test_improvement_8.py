#!/usr/bin/env python3
"""
改进 8 验证脚本：CEO 权重可视化面板增强
验证所有功能是否完整实现
"""

import json
import re
from pathlib import Path

def check_improvements_8():
    """检查改进 8 的实现完整性"""

    print("=" * 60)
    print("改进 8：CEO 权重可视化面板增强 — 代码检查")
    print("=" * 60)

    dds_html = Path("index.html").read_text(encoding="utf-8")
    dds_app = Path("app.py").read_text(encoding="utf-8")

    checks = []

    # ═══════════════════════════════════════════════════════════
    # 前端检查
    # ═══════════════════════════════════════════════════════════

    print("\n【前端实现检查】")

    # 1. CSS 样式
    css_checks = [
        (".ceo-weight-panel", "权重面板 CSS"),
        (".ceo-weight-group", "权重组 CSS"),
        (".ceo-weight-row", "权重行 CSS"),
        (".ceo-weight-preview", "权重预览 CSS"),
        (".ceo-weight-history", "权重历史 CSS"),
        (".ceo-weight-buttons", "按钮组 CSS"),
    ]

    for css_class, desc in css_checks:
        found = css_class in dds_html
        status = "✅" if found else "❌"
        print(f"  {status} {desc}")
        checks.append(("CSS: " + desc, found))

    # 2. HTML 结构
    html_checks = [
        ("ceoWeightPanel", "权重面板 ID"),
        ("ceoWeightGroup", "权重组 ID"),
        ("ceoWeightPreview", "权重预览 ID"),
        ("ceoWeightHistory", "权重历史 ID"),
        ("ceoWeightRecommend", "推荐按钮 ID"),
        ("ceoWeightSave", "保存按钮 ID"),
    ]

    for elem_id, desc in html_checks:
        found = elem_id in dds_html
        status = "✅" if found else "❌"
        print(f"  {status} {desc}")
        checks.append(("HTML: " + desc, found))

    # 3. JavaScript 函数
    js_checks = [
        ("initCeoWeightPanel", "权重面板初始化"),
        ("renderCeoWeightPanel", "权重滑块渲染"),
        ("applyCeoWeightPreview", "权重预览更新"),
        ("saveCeoWeightConfig", "权重配置保存"),
        ("loadCeoWeightHistory", "权重历史加载"),
        ("applySavedWeights", "应用保存的权重"),
        ("recommendCeoWeights", "权重推荐"),
        ("_ceoWeightState", "权重状态对象"),
    ]

    for func_name, desc in js_checks:
        found = func_name in dds_html
        status = "✅" if found else "❌"
        print(f"  {status} {desc}")
        checks.append(("JS: " + desc, found))

    # 4. 事件绑定
    event_checks = [
        ("initCeoWeightPanel()", "在 initCeoPanel 中调用权重面板初始化"),
        ("ceoWeightRecommend.*addEventListener", "推荐按钮事件"),
        ("ceoWeightSave.*addEventListener", "保存按钮事件"),
        ("addEventListener.*applyCeoWeightPreview", "滑块输入事件"),
    ]

    for pattern, desc in event_checks:
        found = bool(re.search(pattern, dds_html, re.DOTALL))
        status = "✅" if found else "❌"
        print(f"  {status} {desc}")
        checks.append(("Events: " + desc, found))

    # ═══════════════════════════════════════════════════════════
    # 后端检查
    # ═══════════════════════════════════════════════════════════

    print("\n【后端实现检查】")

    backend_checks = [
        ("def api_ceo_reweight", "CEO 权重 API 端点"),
        ("custom_weights = data.get", "自定义权重参数支持"),
        ("CEO_WEIGHT_PRESETS\\[.*custom", "自定义权重临时注入"),
        ("light_configs.*invest.*design.*finance", "光效配置"),
        ("lights.*light_configs", "光效返回"),
    ]

    for pattern, desc in backend_checks:
        found = bool(re.search(pattern, dds_app, re.DOTALL))
        status = "✅" if found else "❌"
        print(f"  {status} {desc}")
        checks.append(("Backend: " + desc, found))

    # ═══════════════════════════════════════════════════════════
    # 功能完整性检查
    # ═══════════════════════════════════════════════════════════

    print("\n【功能完整性检查】")

    feature_checks = [
        ("权重滑块", ["input[type=range]", "ceo-weight-row"], dds_html),
        ("权重预览", ["ceo-weight-preview", "weights.invest"], dds_html),
        ("历史记录", ["_ddsWeightHistory", "localStorage"], dds_html),
        ("推荐功能", ["recommendCeoWeights", "risk_grade"], dds_html),
        ("保存功能", ["saveCeoWeightConfig", "ceoReweight.*weights"], dds_html),
        ("Light 映射", ["light_configs", "ambient.*directional"], dds_app),
    ]

    for feature, patterns, text in feature_checks:
        found = all(p in text for p in patterns)
        status = "✅" if found else "❌"
        print(f"  {status} {feature}")
        checks.append(("Feature: " + feature, found))

    # ═══════════════════════════════════════════════════════════
    # 汇总统计
    # ═══════════════════════════════════════════════════════════

    total = len(checks)
    passed = sum(1 for _, result in checks if result)

    print("\n" + "=" * 60)
    print(f"检查结果：{passed}/{total} 通过")
    print("=" * 60)

    if passed == total:
        print("✅ 改进 8 代码完整性检查 PASSED")
        return True
    else:
        print(f"❌ 有 {total - passed} 项检查未通过")
        failed = [name for name, result in checks if not result]
        print("\n未通过的检查项：")
        for item in failed:
            print(f"  - {item}")
        return False


if __name__ == "__main__":
    success = check_improvements_8()
    exit(0 if success else 1)

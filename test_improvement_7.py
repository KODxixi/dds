#!/usr/bin/env python3
"""
改进 7 代码验证脚本
验证前端埋点函数和后端 APM 端点的代码完整性
"""

import re
import json
from pathlib import Path

def check_file_content(filepath, patterns, description):
    """检查文件中是否包含指定的模式"""
    print(f"\n✅ {description}")
    print("─" * 60)

    try:
        content = Path(filepath).read_text(encoding='utf-8')
    except Exception as e:
        print(f"✗ 无法读取文件: {e}")
        return False

    all_found = True
    for pattern_name, pattern_regex in patterns.items():
        if re.search(pattern_regex, content, re.IGNORECASE | re.DOTALL):
            print(f"  ✓ {pattern_name}")
        else:
            print(f"  ✗ {pattern_name}")
            all_found = False

    return all_found

# 前端埋点检查
frontend_patterns = {
    "DDS_METRICS 全局对象": r"const DDS_METRICS = \{",
    "recordMetric() 函数": r"function recordMetric\(",
    "flushMetrics() 函数": r"function flushMetrics\(",
    "DOMContentLoaded 监听": r"document\.addEventListener\('DOMContentLoaded'",
    "页面 load 事件监听": r"window\.addEventListener\('load'",
    "LCP 性能监控": r"largest-contentful-paint",
    "地图初始化计时": r"map_loca_init",
    "CEO 权重交互埋点": r"ceo_reweight_total",
    "热力图聚合埋点": r"heatmap_aggregation",
    "定期上报间隔": r"setInterval\(flushMetrics,",
    "页面卸载上报": r"beforeunload.*flushMetrics",
}

# 后端 APM 检查
backend_patterns = {
    "/api/metrics 端点": r"@app\.route\(\"/api/metrics\"",
    "metrics POST 处理": r"def api_metrics\(\)",
    "JSONL 存储": r"metrics.*\.jsonl",
    "前端指标文件": r"frontend\.jsonl",
    "后端指标文件": r"backend\.jsonl",
    "_req_log 性能记录": r"backend\.jsonl.*_req_log",
    "API 端点排除": r"request\.path != \"/api/metrics\"",
    "状态码记录": r"status_code.*duration_ms",
    "时间戳记录": r"datetime\.now\(\)\.isoformat\(\)",
}

print("🧪 改进 7 代码完整性验证")
print("=" * 60)

# 验证前端代码
frontend_ok = check_file_content(
    'index.html',
    frontend_patterns,
    "前端埋点代码验证"
)

# 验证后端代码
backend_ok = check_file_content(
    'app.py',
    backend_patterns,
    "后端 APM 代码验证"
)

# 统计结果
print("\n" + "=" * 60)
if frontend_ok and backend_ok:
    print("✅ 改进 7 代码完整性验证通过！")
    print("\n📊 验证统计：")
    print(f"  - 前端埋点检查: ✓ {len(frontend_patterns)} 项")
    print(f"  - 后端 APM 检查: ✓ {len(backend_patterns)} 项")
    print("\n🚀 可以启动服务进行集成测试")
else:
    print("⚠ 改进 7 代码存在缺陷：")
    if not frontend_ok:
        print("  - 前端埋点代码不完整")
    if not backend_ok:
        print("  - 后端 APM 代码不完整")

# 检查 metrics 目录结构
print("\n📁 检查数据目录结构：")
metrics_dir = Path("data_out/metrics")
if metrics_dir.exists():
    print(f"  ✓ {metrics_dir} 目录存在")
    for file in metrics_dir.glob("*.jsonl"):
        lines = len([l for l in file.read_text().split('\n') if l.strip()])
        print(f"    - {file.name}: {lines} 条记录")
else:
    print(f"  ℹ {metrics_dir} 目录将在首次请求时创建")

print("\n" + "=" * 60)

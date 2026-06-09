# DDS 地图优化 — 改进 7 性能监控埋点与 APM 接入

**计划日期**：2026-05-30  
**预计耗时**：2-3 小时  
**优先级**：中（改进 1-6 完成后的可选增强）

---

## 改进 7 目标

在 DDS 地图系统中添加性能监控埋点和 APM（应用性能管理）接入，以便：
1. 实时监控前端页面加载、交互、渲染性能
2. 追踪后端 API 响应时间和吞吐量
3. 识别性能瓶颈（热力图聚合、CEO 权重计算等）
4. 支持性能数据的可视化和告警

---

## 核心需求

### 前端监控（index.html）

#### 1. 页面加载性能
- [ ] 记录 DOMContentLoaded、Load 事件时间
- [ ] 测量 First Contentful Paint (FCP) 和 Largest Contentful Paint (LCP)
- [ ] 记录首屏地图初始化耗时
- [ ] 追踪脚本加载和执行时间

#### 2. 交互性能
- [ ] CEO 权重切换时间（改进 5）
  - API 请求时间
  - 光效过渡时间（应 ≤1.2s）
  - 页面重绘时间
- [ ] 热力图聚合等级切换时间（改进 6）
  - 聚合算法执行时间
  - 数据源更新时间
  - GPU 渲染时间

#### 3. GPU 和内存监控
- [ ] 每秒帧率（FPS）
- [ ] 热力图渲染时间（per frame）
- [ ] DOM 节点数变化
- [ ] 内存占用趋势（如浏览器 API 支持）

#### 4. 地图事件监控
- [ ] 缩放事件耗时
- [ ] 拖拽事件响应时间
- [ ] 竞品标记加载时间
- [ ] POI 数据加载时间

### 后端监控（app.py）

#### 1. API 响应时间
- [ ] `/api/report` 总耗时 + 各阶段耗时
  - 地块数据查询（query_local.py）
  - 高德 GIS 调用（gis_amap.py）
  - 决策引擎运算（dds_decision_engine.py）
- [ ] `/api/ceo_reweight` 权重计算耗时
- [ ] `/api/chat` 流式聊天首字延迟（TTFB）和完整耗时

#### 2. 吞吐量和并发
- [ ] 同时在线请求数
- [ ] 每分钟请求数（RPS）
- [ ] 排队请求数

#### 3. 资源使用
- [ ] CPU 占用率（Flask 进程）
- [ ] 内存占用（堆大小）
- [ ] 数据库连接池使用率
- [ ] LLM API 调用次数和成功率

#### 4. 错误监控
- [ ] 5xx 错误率和错误消息
- [ ] 超时请求数
- [ ] LLM 后端降级次数

---

## 技术方案

### 前端：两层埋点架构

#### 层 1：原生 API（浏览器性能 API）
```javascript
// Performance.now() - 高精度计时
const t0 = performance.now();
// ... 执行操作
const elapsed = performance.now() - t0;

// PerformanceObserver - 自动监控页面事件
const observer = new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    console.log(`${entry.name}: ${entry.duration}ms`);
  }
});
observer.observe({ entryTypes: ['paint', 'largest-contentful-paint', 'layout-shift'] });

// Web Vitals 指标
const getCLS = () => { /* 累计布局偏移 */ };
const getFID = () => { /* 首次输入延迟 */ };
const getLCP = () => { /* 最大内容绘制 */ };
```

#### 层 2：自定义埋点（DDS 特定操作）
```javascript
// 定义埋点上报函数
function logMetric(eventName, duration, metadata = {}) {
  const payload = {
    event: eventName,
    duration: duration,
    timestamp: Date.now(),
    userAgent: navigator.userAgent,
    ...metadata
  };
  
  // 发送到后端 /api/metrics endpoint
  navigator.sendBeacon('/api/metrics', JSON.stringify(payload));
}

// 埋点使用示例
const t0 = performance.now();
_applyLightTransition(targetLights, 1200);  // 改进 5
logMetric('ceo_reweight_transition', performance.now() - t0, { preset });

const t1 = performance.now();
const newHeatmapGeo = _aggregateHeatmapData(competitors, newLevel);  // 改进 6
logMetric('heatmap_aggregation', performance.now() - t1, { 
  level: newLevel, 
  input_count: competitors.length,
  output_count: newHeatmapGeo.features.length
});
```

### 后端：APM 框架选择

#### 选项 A：轻量级内存日志 + 本地 JSON 存储
**优点**：无外部依赖，部署简单  
**缺点**：无法跨进程聚合，本地存储有限
```python
import time
from collections import deque

class PerformanceLogger:
    def __init__(self, max_entries=10000):
        self.entries = deque(maxlen=max_entries)
    
    @contextmanager
    def measure(self, operation_name, **metadata):
        t0 = time.time()
        try:
            yield
        finally:
            duration = (time.time() - t0) * 1000
            self.entries.append({
                'operation': operation_name,
                'duration_ms': duration,
                'timestamp': datetime.now().isoformat(),
                **metadata
            })
    
    def export_json(self):
        # 导出为 JSON，后续可用 Grafana 可视化
        return json.dumps(list(self.entries), indent=2)

perf_logger = PerformanceLogger()
```

#### 选项 B：OpenTelemetry + Jaeger（分布式追踪）
**优点**：行业标准，支持分布式链路追踪，功能强大  
**缺点**：需部署 Jaeger 服务，学习成本高
```python
from opentelemetry import trace, metrics
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

jaeger_exporter = JaegerExporter(
    agent_host_name="localhost",
    agent_port=6831,
)
trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(jaeger_exporter)
)

@app.route("/api/report", methods=["POST"])
def api_report():
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("api_report") as span:
        # 记录关键操作
        with tracer.start_as_current_span("geocode"):
            lng, lat = geocode(city, address)
        with tracer.start_as_current_span("query_competitors"):
            competitors = query_local_competitors(city, lng, lat, radius)
        # ...
```

#### 推荐方案
**先用选项 A（轻量级日志）**，快速实现并验证，后期若需要可升级到 OpenTelemetry。

### 数据导出与可视化

#### 前端指标导出
```javascript
// 每 5 分钟或达到 100 条记录时上报
const FLUSH_INTERVAL = 5 * 60 * 1000;
const BATCH_SIZE = 100;

let buffer = [];
function flushMetrics() {
  if (buffer.length === 0) return;
  fetch('/api/metrics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ metrics: buffer })
  }).then(() => { buffer = []; });
}

setInterval(flushMetrics, FLUSH_INTERVAL);
```

#### 后端数据存储
```python
# 存储为 JSONL（JSON Lines），便于 Grafana、Kibana 接入
@app.route("/api/metrics", methods=["POST"])
def api_metrics():
    metrics = request.get_json().get('metrics', [])
    with open('data_out/metrics/frontend.jsonl', 'a') as f:
        for m in metrics:
            f.write(json.dumps(m) + '\n')
    return jsonify({"status": "ok"})
```

#### 可视化方案
1. **Grafana + JSON 数据源**：实时看板
2. **柱状图**：每个时间窗口的 API 平均响应时间
3. **热力图**：CEO 权重操作的耗时分布
4. **告警规则**：响应时间 > 5s 时告警

---

## 实施分阶段计划

### 第一阶段：前端基础埋点（1h）

#### 1.1 添加性能计时函数
```javascript
// index.html 顶部
const DDS_METRICS = {
  pageLoadStart: performance.now(),
  events: []
};

function recordMetric(eventName, duration, metadata = {}) {
  DDS_METRICS.events.push({
    event: eventName,
    duration_ms: duration.toFixed(2),
    timestamp: new Date().toISOString(),
    ...metadata
  });
  if (DDS_METRICS.events.length >= 100) {
    flushMetrics();
  }
}

async function flushMetrics() {
  if (DDS_METRICS.events.length === 0) return;
  const events = DDS_METRICS.events.splice(0);
  try {
    await fetch('/api/metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ metrics: events })
    });
    console.log(`[APM] 已上报 ${events.length} 条记录`);
  } catch (e) {
    console.error('[APM] 上报失败', e);
  }
}

// 定期上报（5 分钟）
setInterval(flushMetrics, 5 * 60 * 1000);
```

#### 1.2 页面加载性能（第一屏）
```javascript
document.addEventListener('DOMContentLoaded', () => {
  const t = performance.now() - DDS_METRICS.pageLoadStart;
  recordMetric('page_dom_ready', t);
});

window.addEventListener('load', () => {
  const t = performance.now() - DDS_METRICS.pageLoadStart;
  recordMetric('page_fully_loaded', t);
});
```

#### 1.3 地图初始化性能
```javascript
// 地图初始化前后添加计时
const mapInitStart = performance.now();
const map = new Loca.Map({...});
const mapInitEnd = performance.now();
recordMetric('map_initialization', mapInitEnd - mapInitStart);
```

### 第二阶段：交互埋点（1h）

#### 2.1 CEO 权重切换（改进 5）
```javascript
// ceoReweight() 函数中添加
async function ceoReweight() {
  const t0 = performance.now();
  try {
    const response = await fetch('/api/ceo_reweight', {...});
    const data = await response.json();
    
    recordMetric('api_ceo_reweight', performance.now() - t0, {
      preset: preset,
      status: response.status
    });
    
    // 光效过渡计时
    const transitionStart = performance.now();
    _applyLightTransition(data.lights);
    recordMetric('light_transition', performance.now() - transitionStart, {
      duration_expected: 1200
    });
  } catch (e) {
    recordMetric('api_ceo_reweight_error', performance.now() - t0, {
      error: e.message
    });
  }
}
```

#### 2.2 热力图聚合（改进 6）
```javascript
const aggregationSelector = document.getElementById('heatmapAggregation');
if (aggregationSelector) {
  aggregationSelector.addEventListener('change', function () {
    const t0 = performance.now();
    const newLevel = this.value;
    
    // 聚合算法计时
    const newHeatmapGeo = _aggregateHeatmapData(
      window._heatmapRawCompetitors, 
      newLevel
    );
    const aggregationTime = performance.now() - t0;
    
    window._heatmapSource.setData(newHeatmapGeo);
    if (window._locaContainer) {
      window._locaContainer.render();
    }
    
    recordMetric('heatmap_aggregation', aggregationTime, {
      level: newLevel,
      input_count: window._heatmapRawCompetitors.length,
      output_count: newHeatmapGeo.features.length,
      reduction_percent: (100 * (1 - newHeatmapGeo.features.length / window._heatmapRawCompetitors.length)).toFixed(1)
    });
  });
}
```

### 第三阶段：后端 APM（1h）

#### 3.1 添加 /api/metrics 端点
```python
@app.route("/api/metrics", methods=["POST"])
def api_metrics():
    """接收前端上报的性能指标"""
    metrics = request.get_json(silent=True) or {}
    events = metrics.get("metrics", [])
    
    if not events:
        return jsonify({"status": "ok"}), 200
    
    # 存储为 JSONL
    metrics_dir = ROOT / "data_out" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    
    with open(metrics_dir / "frontend.jsonl", "a") as f:
        for event in events:
            f.write(_json.dumps(event) + "\n")
    
    log.info("[APM] 已保存 %d 条前端性能记录", len(events))
    return jsonify({"status": "ok"}), 200
```

#### 3.2 后端 API 性能日志
```python
# app.py 中修改 _req_log 函数
@app.after_request
def _req_log(resp):
    """每个请求统一记录性能数据"""
    dt = (time.time() - getattr(request, "_t0", time.time())) * 1000
    
    # 写入后端性能日志
    if request.path.startswith("/api/"):
        metrics_dir = ROOT / "data_out" / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)
        
        with open(metrics_dir / "backend.jsonl", "a") as f:
            f.write(_json.dumps({
                "endpoint": request.path,
                "method": request.method,
                "status_code": resp.status_code,
                "duration_ms": round(dt, 2),
                "timestamp": datetime.now().isoformat(),
                "remote_addr": request.remote_addr
            }) + "\n")
        
        # 日志输出
        log.info("[REQ] %s %s -> %d %.0fms", request.method, request.path, resp.status_code, dt)
    
    return resp
```

#### 3.3 性能数据导出脚本
```python
# scripts/export_metrics.py
import json
import pandas as pd
from pathlib import Path

def aggregate_metrics(source_file):
    """聚合性能指标"""
    data = []
    with open(source_file) as f:
        for line in f:
            data.append(json.loads(line))
    
    df = pd.DataFrame(data)
    
    # 按端点统计
    if 'endpoint' in df.columns:
        summary = df.groupby('endpoint')['duration_ms'].agg(['count', 'mean', 'max', 'min'])
        print(f"\n后端 API 性能统计：\n{summary}")
    
    if 'event' in df.columns:
        summary = df.groupby('event')['duration_ms'].agg(['count', 'mean', 'max', 'min'])
        print(f"\n前端事件性能统计：\n{summary}")

if __name__ == "__main__":
    aggregate_metrics(Path(__file__).parent.parent / "data_out/metrics/backend.jsonl")
    aggregate_metrics(Path(__file__).parent.parent / "data_out/metrics/frontend.jsonl")
```

---

## 验证与测试

### 单元测试
```python
# tests/test_metrics.py
def test_metrics_endpoint():
    response = client.post('/api/metrics', json={
        'metrics': [
            {'event': 'test_event', 'duration_ms': 100}
        ]
    })
    assert response.status_code == 200
    assert (Path(__file__).parent.parent / 'data_out/metrics/frontend.jsonl').exists()
```

### 集成测试
1. 打开前端页面，生成报告
2. 执行 CEO 权重切换和热力图聚合操作
3. 查看 `data_out/metrics/` 目录是否有数据
4. 运行 `export_metrics.py` 验证数据聚合

### 可视化验证
1. 将 JSON 导入 Grafana
2. 创建仪表板展示关键指标
3. 设置告警规则

---

## 文件清单

| 文件/目录 | 改动 | 说明 |
|-----------|------|------|
| `index.html` | +50 行 | 前端埋点函数、事件监听 |
| `app.py` | +30 行 | `/api/metrics` 端点、后端日志 |
| `scripts/export_metrics.py` | 新建 | 性能数据聚合和导出脚本 |
| `data_out/metrics/` | 新建 | 前端和后端性能日志目录 |

---

## 后续优化方向

### 短期（已实施后 1 周）
1. [ ] 定制 Grafana 仪表板
2. [ ] 添加告警规则（响应时间 > 5s）
3. [ ] 分析热力图聚合的性能曲线

### 中期（1-2 周）
1. [ ] 升级至 OpenTelemetry + Jaeger
2. [ ] 支持分布式追踪（前后端链路）
3. [ ] LLM API 性能监控

### 长期（1 个月）
1. [ ] 机器学习异常检测
2. [ ] 性能趋势预测
3. [ ] 自动扩缩容决策

---

**状态**：改进 7 计划就绪  
**优先级**：中（可选增强）  
**下一步**：按上述三个阶段逐步实施


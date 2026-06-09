# DDS 地图优化 — 本地验证指南（改进 1-6）

**启动方式**：`启动.bat` 或 `start-dds-deepseek.ps1`  
**验证地址**：`http://localhost:8080`  
**验证工具**：浏览器 F12 开发者工具

---

## 前置准备

```bash
# 1. 确认依赖已安装
pip install -r requirements.txt
pip install anthropic  # LLM 后端

# 2. 检查 .env 配置
# 复制 .env.example 为 .env，填入 API Key
cp .env.example .env
# 编辑 .env，确保以下字段已填：
# - DEEPSEEK_API_KEY
# - AMAP_KEY
# - AMAP_SEC_CODE

# 3. 启动本地服务
启动.bat
# 或 PowerShell：
.\start-dds-deepseek.ps1

# 4. 验证服务启动
# 浏览器访问 http://localhost:8080
# 应显示 DDS 前端页面（Bauhaus 风格）
```

---

## 验证流程（6 项改进）

### 前置：生成测试报告

1. **打开 http://localhost:8080**
2. **填写表单**：
   - 城市：`三亚`
   - 地址或坐标：`海棠区南田路16号` 或 `lng=109.71899, lat=18.41040`
   - 预期均价：`35000`
3. **点击"生成报告"**
4. **等待报告生成**（进度条完成）

---

## 改进 1 验证：`map.on('complete')` 替代 22s 超时

### 观察指标

**基础地图出现时间**：
1. **打开 F12 → Console**
2. **清空原有日志**（Ctrl+L）
3. **点击"生成报告"**，**同时开始计时**
4. **观察地图出现的时间**：
   - ✅ 预期：<1 秒（maps.js 加载完成后立即出现 2D 地图）
   - ❌ 旧表现：22 秒等待

### 控制台日志验证

```javascript
// F12 → Console，搜索 [DDS] 日志

// 应看到的日志序列：
[DDS] 地图加载完成，Loca 初始化就绪  // ← map.on('complete') 触发
[DDS] Loca 3D WebGL 初始化完成      // ← Loca 加载成功（2-5s 后）
```

或者（Loca 超时情况）：
```
[DDS] 地图加载完成，Loca 初始化就绪
[DDS] Loca 3D WebGL 初始化超时，保留基础地图  // ← 8s 超时兜底
// 基础地图仍可用，无降级到沙盘
```

### 完整验证脚本

```javascript
// F12 → Console 运行

// 1. 检查超时时间是否为 8s（而非 22s）
// → 查看源代码第 3115 行，locaTimer 应为 8000 而非 22000

// 2. 检查地图就绪时间
console.time('map-to-loca');
// （手动点击"生成报告"）
// （观察控制台日志）
console.timeEnd('map-to-loca');
// 预期：<5000ms
```

---

## 改进 2 验证：光源参数外置 `_ddsLocaConfig`

### 检查全局配置对象

```javascript
// F12 → Console

console.log(window._ddsLocaConfig);

// 应输出：
{
  buildings: {
    heightFactor: 2.2,
    zooms: [14, 20]
  },
  lights: {
    ambient: {
      intensity: 2.2,
      color: "#babedc"
    },
    directional: {
      intensity: 0.46,
      color: "#d4d4d4",
      target: [0, 0, 0],
      position: [0, -1, 1]
    },
    point: {
      color: "rgb(15,19,40)",
      intensity: 25,
      distance: 3900,
      position: null
    }
  }
}
```

### 动态调整光源强度（实时效果）

```javascript
// F12 → Console

// 1. 增加环境光强度（地图应变亮）
window._ddsLocaConfig.lights.ambient.intensity = 3.0;
if (window._locaContainer) {
  window._locaContainer.ambLight.intensity = 3.0;
  window._locaContainer.render();
}
// ✅ 预期：地图在 <100ms 内变亮

// 2. 恢复原值
window._ddsLocaConfig.lights.ambient.intensity = 2.2;
if (window._locaContainer) {
  window._locaContainer.ambLight.intensity = 2.2;
  window._locaContainer.render();
}
// ✅ 预期：地图恢复原亮度
```

### 验证点

- [ ] `window._ddsLocaConfig` 对象存在
- [ ] 包含 `lights.ambient`, `lights.directional`, `lights.point` 三个光源配置
- [ ] 修改 intensity 后地图立即响应

---

## 改进 3 验证：监控埋点 `_ddsMapStatus`

### 检查监控对象

```javascript
// F12 → Console

console.log(window._ddsMapStatus);

// 应输出（Loca 成功）：
{
  mapsReady: true,
  basicMapReady: true,
  locaReady: true,
  loadTime: {
    mapsScript: 1234,      // maps.js 脚本加载耗时（ms）
    basicMap: 250,         // 基础地图初始化耗时（ms）
    loca: 3500             // Loca 3D 初始化耗时（ms）
  }
}

// 或（Loca 失败但基础地图可用）：
{
  mapsReady: true,
  basicMapReady: true,
  locaReady: false,        // ← Loca 初始化失败
  loadTime: { ... }
}
```

### 验证点

- [ ] `_ddsMapStatus.mapsReady === true`（maps.js 加载成功）
- [ ] `_ddsMapStatus.basicMapReady === true`（基础地图初始化成功）
- [ ] `_ddsMapStatus.locaReady === true`（Loca 3D 加载成功）或 `false`（失败但不影响基础地图）
- [ ] `loadTime` 对象包含三个耗时数据
- [ ] `loadTime.basicMap < 1000`（基础地图应快速就绪）

---

## 改进 4 验证：多层级竞品可视化

### 热力图层加载验证

```javascript
// F12 → Console

console.log(window._locaLayers.heatmap);

// 应输出：HeatMapLayer 对象（非 undefined）
```

### Zoom 级联验证

1. **初始缩放**（zoom ~11）：
   - ✅ 应显示：**热力图**（蓝→红渐变）
   - ❌ 不应显示：标牌、棱柱、散点

2. **放大到 zoom 14**：
   - ✅ 应显示：热力图 + 浮动三角（散点指示器）
   - ❌ 不应显示：标牌、棱柱

3. **放大到 zoom 15+**：
   - ✅ 应显示：竞品标牌（项目名 + 价格）+ 3D 棱柱
   - ✅ 热力图应消失

### 手动触发 Zoom Control

```javascript
// F12 → Console

// 检查 zoom 监听已注册
console.log('Zoom handler registered:', !!window._ddsZoomChangeHandler);

// 手动触发 zoom control 函数
if (window._ddsZoomChangeHandler) {
  window._ddsZoomChangeHandler();
  console.log('Zoom control triggered');
}

// 检查当前 zoom 级别
console.log('Current zoom:', map.getZoom());

// 检查热力图显示状态
console.log('Heatmap visible:', window._locaLayers.heatmap?.getVisible());
```

### 验证点

- [ ] HeatMapLayer 存在且能获取
- [ ] zoom < 13 时热力图显示，其他图层隐藏
- [ ] zoom 13-15 时热力图 + 散点显示
- [ ] zoom ≥ 15 时标牌 + 棱柱显示，热力图隐藏
- [ ] 滚轮缩放时图层自动切换（无手动操作）

---

## 改进 5 验证：CEO 权重驱动视角动画

### CEO 权重面板识别

1. **报告生成后向下滚动**，找到"CEO 权重滑块面板"
2. **应显示**：
   - 综合评分（大数字）
   - 等级（A / B+ 等）
   - 三个预设按钮：**投资** / **设计** / **财务**
   - 权重滑块（compliance, value, abm 等）

### 权重预设与光效过渡验证

```javascript
// F12 → Console → Network 标签

// 1. 点击"投资"预设按钮
// → 观察 Network 中 /api/ceo_reweight 请求
// → 查看 Response JSON

// 应包含：
{
  "status": "ok",
  "ceo": {...},
  "lights": {
    "ambient": {"intensity": 2.6, "color": "#d4c4a8"},
    "directional": {"intensity": 0.58, "color": "#e8dcc8"},
    "point": {"intensity": 32, "distance": 4200}
  }
}
```

### 地图光效变化观察

1. **点击"投资"预设** → 观察地图：
   - ✅ 地图在 1.2 秒内逐步变亮（环境光 intensity: 2.2 → 2.6）
   - ✅ 色调偏暖（金色调），建筑 3D 效果增强
   - ✅ 竞品标牌更眼，商业价值感强

2. **点击"设计"预设** → 观察地图：
   - ✅ 地图在 1.2 秒内逐步变暗（环境光 intensity: 2.2 → 1.8）
   - ✅ 色调偏冷，建筑细节纹理突显
   - ✅ 整体柔和感，强调美学

3. **点击"财务"预设** → 观察地图：
   - ✅ 地图回到中等亮度（默认值）
   - ✅ 色调中立，客观平衡

### 控制台验证

```javascript
// F12 → Console

// 观察光效过渡完成日志
// 应显示：[DDS] CEO 权重光效过渡完成 {...}

// 手动检查当前光源强度
console.log('Current light intensity:', window._locaContainer?.ambLight?.intensity);
```

### 性能验证（改进 5 无性能退化）

```javascript
// F12 → Performance 标签

// 1. 开始录制
// 2. 点击"投资"预设（触发 1.2s 光效过渡）
// 3. 停止录制

// 预期：
// - FPS 线应保持 58-60 FPS（绿色）
// - 无长帧（黄色/红色）
// - 帧耗时 <17ms
```

### 验证点

- [ ] /api/ceo_reweight 返回 lights 参数
- [ ] 点击预设后地图光源平滑变化（1.2s 内）
- [ ] 投资视角：亮丽金色
- [ ] 设计视角：柔和冷色
- [ ] 财务视角：平衡中立
- [ ] FPS 保持 58-60（无性能下降）

---

## 改进 6 验证：热力图自定义聚合等级

### 聚合等级选择器识别

1. **地图左上角图层控制面板**
2. **向下滚动，应看到**：
   ```
   热力聚合粒度
   [粗（快速加载）▼]  ← selector
   ```

### 聚合等级切换验证

```javascript
// F12 → Console

// 1. 检查热力图聚合函数
console.log('Aggregation function exists:', typeof _aggregateHeatmapData);

// 2. 检查全局变量
console.log('Current aggregation level:', window._heatmapAggregationLevel);
console.log('Raw competitors count:', window._heatmapRawCompetitors?.length);
```

### 三个等级的视觉对比

**选择"粗"（coarse）**：
```
前：57 个竞品点 → 热力数据源
后：聚合为 8-12 个网格 → 热力图

观察：
✅ 热力图更粗糙，仅显示大尺度价格趋势
✅ 响应极快（<100ms）
✅ 控制台：[DDS] 热力图已切换为 coarse（10 个聚合单元）
```

**选择"中"（medium，默认）**：
```
前：57 个竞品点
后：聚合为 15-25 个网格

观察：
✅ 热力图细节适中，本地市场分析足够
✅ 响应快速（<150ms）
✅ 控制台：[DDS] 热力图已切换为 medium（20 个聚合单元）
```

**选择"细"（fine）**：
```
前：57 个竞品点
后：聚合为 45-55 个网格

观察：
✅ 热力图非常细致，接近原始数据
✅ 响应稍慢（<250ms）但仍流畅
✅ 控制台：[DDS] 热力图已切换为 fine（50 个聚合单元）
```

### 验证聚合准确性

```javascript
// F12 → Console

// 1. 获取当前热力图数据
const currentData = window._heatmapSource.data;
console.log('Heatmap feature count:', currentData.features.length);

// 2. 逐个查看聚合单元
currentData.features.forEach((f, i) => {
  console.log(`Grid ${i}: price=${f.properties.price.toFixed(0)}, count=${f.properties.count}`);
});

// 3. 验证价格权重（应为 0-1 之间）
console.log('Price weight sample:', currentData.features[0]?.properties.weight);
```

### 性能验证

```javascript
// F12 → Performance 标签

// 1. 选择"粗"，开始录制
// 2. 从"粗"切换到"细"
// 3. 停止录制

// 预期：
// - 粗等级：GPU 占用 ↓ 80%，帧率 60 FPS
// - 中等级：GPU 占用 ↓ 60%，帧率 60 FPS
// - 细等级：GPU 占用 ↓ 10%，帧率 58-60 FPS
// - 切换延迟：<250ms
```

### 验证点

- [ ] 热力聚合等级 selector 存在
- [ ] 三个选项（粗/中/细）可正常选择
- [ ] 点击"粗" → 热力图数据量 ↓ 75%（10-12 个网格）
- [ ] 点击"中" → 热力图数据量 ↓ 60%（15-25 个网格）
- [ ] 点击"细" → 热力图数据量 ↓ 10%（45-55 个网格）
- [ ] 切换流畅，无卡顿
- [ ] 控制台显示正确的聚合单元数
- [ ] 热力图颜色分布（蓝→红）保持一致

---

## 快速验证清单

```
准备阶段
☐ 启动 web.bat / start-dds-deepseek.ps1
☐ 浏览器打开 http://localhost:8080
☐ F12 打开开发者工具

改进 1 验证（<1 分钟）
☐ 生成报告，观察地图出现时间 <1s
☐ 控制台查看 [DDS] 日志

改进 2 验证（<1 分钟）
☐ console.log(window._ddsLocaConfig)
☐ 手动调整 intensity，观察地图亮度变化

改进 3 验证（<1 分钟）
☐ console.log(window._ddsMapStatus)
☐ 检查 basicMapReady / locaReady 状态

改进 4 验证（2–3 分钟）
☐ 滚轮缩放地图，观察图层自动切换
☐ zoom < 13：热力图
☐ zoom 13-15：散点
☐ zoom ≥ 15：标牌

改进 5 验证（2–3 分钟）
☐ 点击权重预设（投资/设计/财务）
☐ 观察地图光效在 1.2s 内平滑变化
☐ Network 查看 /api/ceo_reweight 返回的 lights 参数

改进 6 验证（2–3 分钟）
☐ 找到图层控制面板的"热力聚合粒度"selector
☐ 选择"粗"/"中"/"细"，观察热力网格数变化
☐ 检查控制台日志确认聚合单元数

总验证时间：~10 分钟
```

---

## 常见问题

### Q：地图不显示？
**A：** 检查：
1. AMAP_KEY 是否填入 .env
2. 浏览器控制台是否有 CORS 或加载错误
3. 刷新页面（Ctrl+R）

### Q：Loca 3D 不显示，但基础地图有？
**A：** 这是预期的。检查：
1. 网络是否慢（Loca 脚本加载慢）
2. 控制台是否显示 `locaReady: false`
3. 这时基础地图已可用（改进 1 的降级能力）

### Q：权重预设点击无反应？
**A：** 检查：
1. 是否已生成报告（权重面板是否显示）
2. Network 标签是否有 /api/ceo_reweight 请求
3. 后端 Flask 是否正常运行（console 是否有 POST 错误）

### Q：热力图选择器不出现？
**A：** 检查：
1. 地图是否加载完全
2. 图层控制面板是否存在（地图容器右上角）
3. 向下滚动图层面板，是否看到"热力聚合粒度"

### Q：如何确认所有改进都生效？
**A：** 运行完整验证脚本：
```javascript
// F12 → Console 复制粘贴

const checks = {
  '改进1': !!window._ddsMapStatus,
  '改进2': !!window._ddsLocaConfig,
  '改进3': window._ddsMapStatus?.locaReady !== undefined,
  '改进4': !!window._locaLayers?.heatmap,
  '改进5': typeof _applyLightTransition === 'function',
  '改进6': typeof _aggregateHeatmapData === 'function'
};

Object.entries(checks).forEach(([name, result]) => {
  console.log(`${name}: ${result ? '✅ 生效' : '❌ 未生效'}`);
});
```

预期输出：全部 ✅

---

## 验证完成后

✅ **所有改进验证通过** → 可以确认代码就绪

🚀 **下一步**：
- 提交代码 PR
- 部署到 Cloud Run（可选）
- 推进改进 7（性能监控埋点）

---

**验证指南完成时间**：~10–15 分钟  
**验证工具**：仅需浏览器 F12 + 本地 Flask 服务

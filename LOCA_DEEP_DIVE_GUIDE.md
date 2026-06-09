# 高德 Loca v2 深度学习指南

**范围**：官方文档 + DDS 应用场景  
**日期**：2026-05-30  
**目标**：掌握 Loca v2 架构、性能优化、数据处理最佳实践

---

## 第一部分：Loca v2 核心架构

### 1. 版本关系与兼容性

| JS API 版本 | Loca 版本 | 状态 | 用途 |
|------------|---------|------|------|
| v1.4.x | v1.3.x | 过时 | ❌ 不推荐 |
| **v2.0** | **v2.0.0** | 当前 | ✅ 新项目 |

**DDS 现状**：使用 JSAPI v2.0 + Loca v2.0.0（正确）

### 2. 加载方式对标

**官方推荐**：Loader 方式（解决依赖顺序）
```javascript
import AMapLoader from '@amap/amap-jsapi-loader';

AMapLoader.load({
  "key": "您的key",
  "version": "2.0",
  "Loca": { "version": "2.0.0" }
}).then((AMap) => {
  // 初始化
}).catch(e => console.log(e));
```

**DDS 现状**：直接 `<script>` 标签（适合简单场景）
```html
<script src="https://webapi.amap.com/maps?v=2.0&key=..."></script>
<script src="https://webapi.amap.com/loca?v=2.0.0&key=..."></script>
```

**比较**：
- Loader：处理依赖顺序自动化，适合 npm 工程
- 脚本标签：简单直接，适合单页面应用（DDS 场景）
- **DDS 可保持当前方式**，已改为异步加载

### 3. 初始化流程（官方标准）

```javascript
// 1. 创建地图
var map = new AMap.Map('container', {
  center: [lng, lat],
  zoom: 12,
  viewMode: '3D',  // ⚠️ 必须 3D，否则 Loca 失去高度信息
  showLabel: false,
  mapStyle: 'amap://styles/...'
});

// 2. 创建 Loca 实例
var loca = new Loca.Container({ map });

// 3. 等待地图完全加载
map.on('complete', function () {
  // 在此处初始化 Loca 图层
  initLocaLayers(loca);
  
  // 启动动画
  loca.animate.start();
});
```

**DDS 差距**：
- ✓ 创建地图：正确
- ✓ 创建 Loca 容器：正确
- ❌ 未利用 `map.on('complete')` 事件（当前硬超时 22s）
- ⚠️ 动画启动时序未最优化

---

## 第二部分：Loca v2 的图层系统

### 图层类型与应用场景

| 图层类型 | 英文 | 适用场景 | 性能 | DDS 应用 |
|---------|------|---------|------|---------|
| **圆点** | PointLayer / RoundPointLayer | 聚合点、POI | ⚡ 高 | ✓ 竞品位置 |
| **贴地点** | ScatterLayer | 贴地呼吸、脉冲 | ⚡ 高 | 可用于配套动画 |
| **图标点** | IconLayer | 海量图标（性能优化） | ⚡⚡ 最高 | 可升级竞品渲染 |
| **线** | LineLayer | 路线、轨迹（支持高度） | ⚡ 高 | 未用 |
| **连接线** | LinkLayer | 关系可视化（高亮） | ⚡ 高 | 未用 |
| **脉冲线** | PulseLine | 轨迹动画、流向 | ⚡ 高 | 未用 |
| **标牌点** | ZMarkerLayer | 标注文字、复杂信息 | ⚠️ 中 | ✓ 竞品名称/价格 |
| **多边形** | PolygonLayer | 3D 建筑、分区 | ⚡ 高 | ✓ 3D 楼体 |
| **热力** | HeatMapLayer | 密度分布、聚合 | ⚡ 高 | ✓ 可用于价格热力 |
| **网格** | GridLayer | 网格聚合统计 | ⚡⚡ 最高 | 可用于楼盘密度 |
| **蜂窝** | HexagonLayer | 蜂窝聚合（美观） | ⚡⚡ 最高 | 可用于楼盘聚合 |

**DDS 现状分析**：
- 使用 `ZMarkerLayer` 显示竞品标牌 ✓ 正确
- 使用 `PointLayer` 为后备（若无 Loca） ✓ 正确
- **未充分利用** IconLayer（海量优化）、HeatMapLayer（价格热力）

### 关键参数详解（以 ZMarkerLayer 为例）

```javascript
const zMarker = new Loca.ZMarkerLayer({
  zIndex: 120,        // 图层堆叠顺序（值越大越靠前）
  depth: false,       // 深度测试（false = 不遮挡）
  visible: true,      // 可见性
  alwaysFront: true,  // 始终在最前（忽略深度）
});

zMarker.setStyle({
  content: (i, feature) => {
    // 动态生成 HTML 标记
    const price = feature.properties.price;
    return `<div>${feature.properties.name} ${price}万</div>`;
  },
  unit: 'px',         // 像素单位（不是地理单位）
  size: [160, 48],    // 标牌大小
  altitude: 8,        // 离地高度（米）
  rotation: 0,        // 旋转角度
});
```

**DDS 优化建议**：
- `unit: 'px'` ✓ 已用（避免高缩放时变形）
- `alwaysFront: true` ✓ 已用（标牌始终可见）
- 可增加 `altitude` 避免被楼体遮挡

---

## 第三部分：性能优化最佳实践

### 3.1 数据源优化

**当前 DDS 做法**（`index.html` 中）：
```javascript
const compGeo = buildCompetitorGeoJSON(combined);
_compSource = new Loca.GeoJSONSource({ data: compGeo });
```

**问题与改进**：
| 问题 | 当前 | 改进方案 |
|------|------|---------|
| **数据量大时渲染卡顿** | 动态构建 ~100 个点 GeoJSON | 分页渲染（显示前 50 个，懒加载） |
| **重复构建浪费** | 每次页面刷新重建 | 缓存 GeoJSON，仅更新数据 |
| **图层更新低效** | `setSource()` 替换整个源 | 用 `updateFeatures()` 增量更新 |

**改进代码示例**：
```javascript
// 方案 1：分页渲染（仅显示前 50）
const visibleComps = combined.slice(0, 50);
const compGeo = buildCompetitorGeoJSON(visibleComps);

// 方案 2：缓存与增量更新
if (!_compSource) {
  _compSource = new Loca.GeoJSONSource({ data: compGeo });
} else {
  // Loca v2 不支持原生增量，但可以通过图层可见性控制性能
  _compSource.setData(compGeo);
}
```

### 3.2 光源优化

**官方高效光源配置**（参考演示）：
```javascript
loca.ambLight = {
  intensity: 2.2,          // 环境光强度（推荐 1.5–2.5）
  color: '#babedc',        // 偏冷色（协调整体）
};
loca.dirLight = {
  intensity: 0.46,         // 平行光强度
  color: '#d4d4d4',        // 柔和白色
  position: [0, -1, 1],    // 光源方向（高德推荐值）
  target: [0, 0, 0],       // 光源指向
};
loca.pointLight = {
  color: 'rgb(15,19,40)',  // 点光冷蓝（强化 3D 感）
  position: [lng, lat, 2600], // 地块上空
  intensity: 25,           // 强度（推荐 10–30）
  distance: 3900,          // 光照衰减距离
};
```

**DDS 当前**：硬编码在 `addLocaEnhancement` 中  
**改进**：外置到 `window._ddsLocaConfig`（见前文 IMMEDIATE_IMPROVEMENTS.md）

### 3.3 渲染优化

**关键优化点**：

1. **禁用不必要的标签**
```javascript
const map = new AMap.Map('map', {
  showLabel: false,  // ✓ DDS 已用（减少渲染）
  showBuildingBlock: false,  // 隐藏楼块（如不需要）
});
```

2. **限制可视化数据量**
```javascript
// 竞品太多时分层显示
const competitors = data.slice(0, 50);  // 仅显示前 50
const otherComps = data.slice(50);      // 隐藏其他
```

3. **懒加载图层**
```javascript
// 地图初始时不加 Loca，用户交互后再加
map.on('zoomchange', function(e) {
  if (e.zoom > 13 && !_locaReady) {
    addLocaEnhancement(r);  // 用户缩放到城市级别时触发
  }
});
```

### 3.4 动画优化

```javascript
// 官方推荐：用 ViewControl 而非 setZoom/setCenter
loca.viewControl.addAnimates([{
  pitch: {
    value: 45,  // 目标倾斜角
    duration: 2000,  // 动画时长（毫秒）
    timing: [0, 0, 0.8, 1],  // 贝塞尔曲线（ease-out）
  },
  zoom: {
    value: 15,
    duration: 2000,
    timing: [0, 0, 0.8, 1],
  },
}], callback);  // 动画完成后回调
```

**DDS 当前**：无动画（基础地图为静态 2D）  
**改进方向**：若启用 3D，可加平滑镜头动画增强体验

---

## 第四部分：DDS 特化优化方案

### 方案 1：多层级竞品可视化

**问题**：竞品 50+ 个，全显示拥挤；隐藏信息丢失。  
**方案**：按缩放级别分层
```javascript
map.on('zoomchange', function(e) {
  const zoom = e.zoom;
  
  if (zoom < 13) {
    // 城市级：仅显示热力图（聚合）
    heatmapLayer.show();
    zMarkerLayer.hide();
    iconLayer.hide();
  } else if (zoom < 15) {
    // 区域级：显示圆点 + 热力
    iconLayer.show();
    heatmapLayer.show();
    zMarkerLayer.hide();
  } else {
    // 街区级：显示详细标牌
    zMarkerLayer.show();
    iconLayer.hide();
    heatmapLayer.hide();
  }
});
```

### 方案 2：价格热力分布

**目标**：用颜色编码竞品价格（红=贵，蓝=便宜）  
**实现**：使用 HeatMapLayer
```javascript
const priceHeatmap = new Loca.HeatMapLayer({
  zIndex: 50,
  opacity: 0.7,
  blend: 'lighter',
});

const priceData = competitors.map(c => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [c.lng, c.lat] },
  properties: { value: c.unit_price_cny / 10000 }  // 万元/㎡
}));

const priceSource = new Loca.GeoJSONSource({
  data: { type: 'FeatureCollection', features: priceData }
});

priceHeatmap.setSource(priceSource);
priceHeatmap.setStyle({
  value: (index, feature) => feature.properties.value,
  color: ['#0051ba', '#37b7c3', '#aac858', '#ffa500', '#d02020'],  // 蓝→红渐变
  minValue: 10000,  // 1万元/㎡
  maxValue: 50000,  // 5万元/㎡
  radius: 80,
});

loca.add(priceHeatmap);
```

### 方案 3：CEO 权重驱动的视角动画

**目标**：不同权重预设对应不同的地图视角和光效  
**实现**：
```javascript
const presetAnimations = {
  'invest': {
    // 投资侧：强化商业配套，向东北倾斜（商业区方向）
    pitch: 35,
    rotation: -45,
    lights: { ambLight: { intensity: 2.5 }, ... }
  },
  'design': {
    // 设计侧：强化建筑形态，俯视视角
    pitch: 50,
    rotation: 0,
    lights: { ambLight: { intensity: 1.8 }, ... }
  },
  'finance': {
    // 融资侧：均衡视角
    pitch: 42,
    rotation: 0,
    lights: { ambLight: { intensity: 2.2 }, ... }
  }
};

function applyCEOPreset(preset) {
  const anim = presetAnimations[preset];
  
  // 应用光效
  window._ddsLocaConfig.lights.ambient.intensity = anim.lights.ambLight.intensity;
  if (_locaContainer) {
    _locaContainer.ambLight = anim.lights.ambLight;
  }
  
  // 应用视角动画
  loca.viewControl.addAnimates([{
    pitch: { value: anim.pitch, duration: 1500, timing: [0, 0, 0.8, 1] },
    rotation: { value: anim.rotation, duration: 1500, timing: [0, 0, 0.8, 1] },
  }]);
}
```

### 方案 4：实时数据更新（无 Loca 重建）

**问题**：CEO 权重变化时，需要重新渲染竞品层（卡顿）。  
**方案**：复用图层，仅更新样式
```javascript
function updateCompetitorStyle(ceoWeights) {
  // 不重新构建源，仅改样式
  _zMarkerLayer.setStyle({
    content: (i, feature) => {
      const comp = feature.properties;
      // 根据权重动态着色
      const score = calcCompetitorScore(comp, ceoWeights);
      const color = scoreToColor(score);  // 权重 → 颜色
      return `<div style="color:${color}">${comp.name}</div>`;
    },
  });
  
  // 更新渲染（官方 API）
  _zMarkerLayer.updateData();  // 若支持增量
}
```

---

## 第五部分：DDS 路线图

### 立即实施（改进 1–3）

**改进 1**：用 `map.on('complete')` 替代 22s 超时  
```javascript
// 当前：setTimeout(..., 22000)
// 改为：
map.on('complete', function () {
  addLocaEnhancement(r);
});
```
**收益**：加载时间 ↓ 80%，精确触发

**改进 2**：光源参数外置  
**收益**：CEO 权重集成、调试工具支持

**改进 3**：监控埋点  
**收益**：性能数据、故障诊断

### 后续迭代（方案 1–4）

| 方案 | 优先级 | 工作量 | 收益 | 依赖 |
|------|--------|--------|------|------|
| 多层级竞品可视化 | 🔴 高 | 1.5h | 信息密度 ↑ | 改进 1 |
| 价格热力分布 | 🟡 中 | 1h | 价格洞察 | 改进 1 |
| CEO 权重动画 | 🟡 中 | 2h | 决策可视化 | 改进 2–3 |
| 实时数据更新 | 🟢 低 | 1h | 交互流畅度 | 改进 1 |

---

## 总结：DDS 与官方的差异及改进方向

| 维度 | 官方做法 | DDS 现状 | 改进建议 |
|------|---------|---------|---------|
| **脚本加载** | 推荐 Loader（npm） | 直接脚本标签 | 保持当前（已改异步） |
| **地图就绪** | `map.on('complete')` | 硬超时 22s | ✓ 改进 1 |
| **光源管理** | 对象字面量配置 | 硬编码 | ✓ 改进 2 |
| **监控** | 无 | 无 | ✓ 改进 3 |
| **图层利用** | 多种图层 | 仅 ZMarker + Point | 逐步升级方案 1–4 |
| **数据量** | <10000 点 | ~50–100 竞品 | 分页 + 缓存 |
| **动画** | ViewControl 支持 | 无 | 权重驱动视角（低优） |

---

**关键洞察**：
- DDS 改造方向（基础地图必出、Loca 可选）**超越官方**健壮性
- 官方示例侧重美观，DDS 侧重可靠性——互补而非冲突
- 后续优化沿官方推荐方向（多图层、大数据聚合）可获得显著收益
- CEO 权重与 Loca 光效绑定是 DDS 的**独特创新点**，值得深化

---

**推荐行动**：
1. ✅ 本周：完成改进 1–3（0.5–2 天）
2. 📅 下周：启动方案 1（多层级竞品可视化）
3. 📅 后续：逐步评估方案 2–4 的投资回报率

# 高德 Loca v2 官方实现对标与最佳实践

**来源**：https://lbs.amap.com/demo/loca-v2/demos/cat-view-control/view-control  
**日期**：2026-05-30

---

## 官方实现流程（参考示例）

```javascript
// 1. 同步加载脚本
<script src="https://webapi.amap.com/maps?v=2.0&key=..."></script>
<script src="https://webapi.amap.com/loca?v=2.0.0&key=..."></script>

// 2. 初始化地图
var map = new AMap.Map('map', {
  viewMode: '3D',
  zoom: 11.8,
  center: [121.304018, 31.217688],
  mapStyle: 'amap://styles/...',
});

// 3. 初始化 Loca 容器
var loca = new Loca.Container({ map });

// 4. 配置光源（环境光、平行光、点光）
loca.ambLight = { intensity: 2.2, color: '#babedc' };
loca.dirLight = { intensity: 0.46, ... };
loca.pointLight = { ... };

// 5. 创建数据源与图层
var geo = new Loca.GeoJSONSource({ url: '...' });
var pl = new Loca.PolygonLayer({ zIndex: 120, ... });
pl.setSource(geo);
pl.setStyle({ topColor: '#111', ... });
pl.setLoca(loca);

// 6. 等待地图加载完成，启动动画
map.on('complete', function () {
  loca.animate.start();
  setTimeout(animate, 2000);
});
```

---

## 官方实现特点

| 特点 | 说明 |
|------|------|
| **脚本加载** | 同步 `<script>` 标签，假设总是成功 |
| **初始化顺序** | AMap → Loca → 配置光源 → 创建图层 → 启动动画 |
| **错误处理** | **无**（官方假设网络条件良好） |
| **超时控制** | **无**（官方未考虑超时情况） |
| **降级方案** | **无**（如果 Loca 失败，整体失败） |
| **生命周期钩子** | 使用 `map.on('complete')` 等待地图就绪 |
| **性能优化** | Dat.js 调试工具，实时调整参数 |

---

## DDS 改造 vs 官方实现

### 1. 脚本加载策略

**官方**：同步，假设成功
```html
<script src="...maps..."></script>
<script src="...loca..."></script>
```

**DDS 改造**：异步，分步加载，独立错误处理
```javascript
// 先加 maps
mapsScript.onload = () => {
  initBasicMap(r);      // 立即出现 2D
  _loadLocaScript(...); // 异步加 Loca（可选）
};
mapsScript.onerror = () => initVectorSandbox(r); // 降级
```

**改进点**：用户立即看到基础地图，而非等待 Loca 加载。

---

### 2. 初始化顺序

**官方**（脆弱）：
```
AMap → Loca → 图层 → 动画
└─ 任何环节失败，整体失败
```

**DDS 改造**（健壮）：
```
maps.js 加载 ✓
  ├─ initBasicMap(2D 地图) ✓ [必出]
  └─ 异步 addLocaEnhancement(3D) [可选]
      ├─ 成功 → 3D 地图
      └─ 失败 → 保留 2D
```

**改进点**：基础地图与 Loca 3D 解耦，Loca 失败不影响可用性。

---

### 3. 生命周期管理

**官方推荐**：`map.on('complete')`
```javascript
map.on('complete', function () {
  loca.animate.start();
});
```

**DDS 当前**：硬性 22 秒超时
```javascript
var locaTimer = setTimeout(() => {
  console.warn('Loca init timeout');
  // 保留基础地图
}, 22000);
```

**建议改进**：结合两者
```javascript
map.on('complete', function () {
  // 22秒超时兜底
  const locaTimer = setTimeout(() => {
    if (!_locaContainer) {
      console.warn('Loca timeout, keeping basic map');
    }
  }, 22000);
  
  // Loca 初始化成功后清除超时
  addLocaEnhancement(r);
});
```

---

### 4. 光源配置

**官方做法**：通过对象字面量配置，易于调试
```javascript
loca.ambLight = { intensity: 2.2, color: '#babedc' };
loca.dirLight = { intensity: 0.46, color: '#d4d4d4', ... };
loca.pointLight = { color: 'rgb(15,19,40)', position: [...], ... };
```

**DDS 当前**：代码中硬编码，难以调整
```javascript
// initLoca 中的硬编码光源参数
```

**建议改进**：将光源参数外置，便于 CEO 权重或配置系统调整。

---

### 5. 数据源管理

**官方**：使用 `Loca.GeoJSONSource`
```javascript
var geo = new Loca.GeoJSONSource({
  url: 'https://a.amap.com/.../sh_building_center.json'
});
```

**DDS 当前**：动态构建 GeoJSON
```javascript
const compGeo = buildCompetitorGeoJSON(combined);
const parcelGeo = buildParcelGeoJSON(parcelLng, parcelLat, ...);
_compSource = new Loca.GeoJSONSource({ data: compGeo });
_parcelSource = new Loca.GeoJSONSource({ data: parcelGeo });
```

**对比**：
- 官方：从 CDN 或服务器加载，适合大规模数据
- DDS：客户端动态生成，适合实时数据变化
- **两种都可行**，DDS 的动态方式更灵活

---

## 最佳实践建议

### ✓ 保留（DDS 做法更健壮）

1. **异步分步加载** — 先 maps，再 Loca
2. **基础地图优先渲染** — 2D 地图确保出现
3. **独立错误处理** — Loca 失败不影响基础地图
4. **降级方案** — maps.js 失败时用矢量沙盘

### ⚠ 可改进

1. **超时管理** — 配合 `map.on('complete')` 而非硬性 22s
2. **光源参数外置** — 便于动态调整和 CEO 权重集成
3. **数据源监听** — 添加 GeoJSON 加载失败处理
4. **性能调试工具** — 集成类似官方 Dat.js 的参数调试面板

### ✗ 不建议采用（官方假设过于乐观）

1. 同步脚本加载 — 网络不稳定时阻塞
2. 无错误处理 — 生产环境需健壮性
3. 硬性依赖 Loca — 应作为可选增强

---

## 改造后的目标架构

```
initMap(r)
  ├─ 检查 maps.js 是否已加载
  ├─ 如否：异步加载 maps.js
  │   └─ 成功 → initBasicMap(r) [立即]
  │           → map.on('complete', () => {
  │               addLocaEnhancement(r) [异步]
  │             })
  │   └─ 失败 → initVectorSandbox(r) [兜底]
  │
  └─ 如是：直接 initBasicMap(r)
           → map.on('complete', () => {
               addLocaEnhancement(r)
             })

addLocaEnhancement(r)
  ├─ 尝试创建 Loca 容器
  ├─ 配置光源（可外置参数）
  ├─ 创建图层（竞品、地块、配套）
  ├─ 启动 loca.animate.start()
  └─ 失败 → 仅 warn，基础地图已渲染，不影响

initBasicMap(r)
  └─ 创建 2D AMap.Map
      ├─ 竞品标注（Marker）
      ├─ 配套 POI（Marker）
      └─ 地块中心（Marker）
```

---

## 验证与监控

**浏览器控制台日志**：
```javascript
[DDS] maps.js loading...
[DDS] maps.js loaded, initializing basic map
[DDS] basic map ready (competitors: 57, amenities: 24)
[DDS] Loca enhancement starting...
[DDS] Loca ready, applying 3D view
// 或
[DDS] Loca init timeout/error, keeping basic 2D map
```

**关键指标**（可加埋点）：
- 基础地图出现时间（期望 <2s）
- Loca 加载时间（期望 2–5s）
- Loca 失败率（目标 <5%）
- 用户看到 3D 的比例（目标 >95%）

---

## 总结

DDS 的改造方向**正确且超过官方实现**：
- ✅ 健壮性更好（错误处理）
- ✅ 用户体验更优（基础地图立即出现）
- ✅ 降级策略完整（3 级兜底：3D → 2D → 沙盘）

建议后续迭代在以下方向增强：
1. 超时管理与 `map.on('complete')` 结合
2. 光源参数外置与 CEO 权重集成
3. 数据源加载失败处理
4. 性能监控与埋点

---

**来源对标日期**：2026-05-30  
**高德官方示例**：Loca v2 - 镜头动画示例  
**DDS 改造阶段**：P0 完成，P1–P2 后续迭代

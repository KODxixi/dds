# 地图链路 — 立即可应用的改进（基于官方最佳实践）

**基础改造完成后的微调**，基于高德官方实现和 DDS 实际场景。

---

## 改进 1：用 `map.on('complete')` 替代硬性 22s 超时

### 现状问题
```javascript
// 当前：猜测 22 秒是否足够初始化 Loca
var locaTimer = setTimeout(() => {
  if (_locaContainer || locaTimedOut) return;
  console.warn('Loca init timeout...');
}, 22000);
```
- ❌ 时间猜测，不精确
- ❌ 快速网络下浪费 22s
- ❌ 慢速网络下 22s 仍不够

### 改进方案
```javascript
// 等待地图完全加载再启动 Loca 增强
map.on('complete', function () {
  // 仅在地图就绪后启动 Loca
  addLocaEnhancement(r);
  
  // 仍保留兜底超时，但缩短为 8s（Loca 脚本通常 2-4s）
  const locaTimer = setTimeout(() => {
    if (!_locaContainer) {
      console.warn('[DDS] Loca 初始化超过 8s，保留 2D 地图');
    }
  }, 8000);
  
  // Loca 成功时清除超时
  if (window._locaReady) clearTimeout(locaTimer);
});
```

### 改动代码位置
- 文件：`index.html`
- 改名后的函数：`addLocaEnhancement` (行号 ~3104)
- 改动：改变超时逻辑，从 22s → `map.on('complete')` + 8s 兜底

### 预期效果
- ⏱️ 快速网络：Loca 加载时间从 22s 降至 2–4s
- 🎯 精确触发：地图就绪立即启动，不猜测
- 💾 降级更及时：8s 超时足够检测 Loca 失败

---

## 改进 2：光源参数外置，支持动态调整

### 现状问题
```javascript
// 当前：光源参数硬编码在 initLoca 中（行号 ~3130-3150）
const buildings = new AMap.Buildings({
  zooms: [14, 20],
  zIndex: 10,
  heightFactor: 2.2  // ← 硬编码
});

// 环境光、平行光、点光参数也硬编码...
```

### 改进方案
**步骤 1**：在 `initBasicMap` 或全局作用域定义光源配置
```javascript
// 全局光源配置（便于 CEO 权重调整、调试工具读取）
window._ddsLocaConfig = {
  buildings: {
    heightFactor: 2.2,  // 建筑物拉高倍数
    zooms: [14, 20],
  },
  lights: {
    ambient: {
      intensity: 2.2,
      color: '#babedc',
    },
    directional: {
      intensity: 0.46,
      color: '#d4d4d4',
      target: [0, 0, 0],
      position: [0, -1, 1],
    },
    point: {
      color: 'rgb(15,19,40)',
      intensity: 25,
      distance: 3900,
      position: null, // 动态设置为地块中心
    },
  },
};
```

**步骤 2**：在 `addLocaEnhancement` 中引用配置
```javascript
function addLocaEnhancement(r) {
  // ... 现有代码 ...
  
  const cfg = window._ddsLocaConfig;
  const buildings = new AMap.Buildings({
    ...cfg.buildings
  });
  
  loca.ambLight = cfg.lights.ambient;
  loca.dirLight = cfg.lights.directional;
  loca.pointLight = {
    ...cfg.lights.point,
    position: [parcelLng, parcelLat, 2600], // 地块中心
  };
}
```

### 预期应用场景
- **CEO 权重系统**：权重调整 → 实时修改 `_ddsLocaConfig`
- **调试工具**：控制台即时调整光源参数，实时预览
- **A/B 测试**：快速切换光源方案，无需重新编译

### 改动代码位置
- 文件：`index.html`
- 新增：全局 `window._ddsLocaConfig` 对象（行号 ~2910，initMap 之前）
- 改动：`addLocaEnhancement` 中使用 `cfg` 替代硬编码

---

## 改进 3：添加 Loca 加载完成标志与监控埋点

### 现状问题
```javascript
// 当前：无法外部确认 Loca 是否成功加载
if (_locaContainer) { /* ... */ }  // 仅内部判断
```

- ❌ 外部代码无法感知 Loca 状态
- ❌ 无监控数据，无法评估成功率
- ❌ CEO 权重调整时无法知道是否生效

### 改进方案
```javascript
// 全局状态标志
window._ddsMapStatus = {
  mapsReady: false,    // maps.js 加载完成
  basicMapReady: false, // 2D 基础地图就绪
  locaReady: false,    // Loca 3D 加载完成
  loadTime: {
    mapsScript: null,
    basicMap: null,
    loca: null,
  },
};

// 改造后的 addLocaEnhancement
function addLocaEnhancement(r) {
  const startTime = performance.now();
  try {
    // ... 现有 Loca 初始化代码 ...
    
    // ✓ Loca 成功
    _locaContainer = loca;
    window._ddsMapStatus.locaReady = true;
    window._ddsMapStatus.loadTime.loca = performance.now() - startTime;
    console.log(`[DDS] Loca 初始化完成 (${window._ddsMapStatus.loadTime.loca.toFixed(0)}ms)`);
    
  } catch (e) {
    // ✗ Loca 失败，但不影响 2D 地图
    console.error('[DDS] Loca 初始化失败（保留 2D 地图）:', e);
    // _ddsMapStatus.locaReady 保持 false
  }
}

// initBasicMap 也添加标志
function initBasicMap(r) {
  try {
    // ... 现有代码 ...
    window._ddsMapStatus.basicMapReady = true;
    window._ddsMapStatus.loadTime.basicMap = performance.now() - startTime;
  } catch (e) {
    console.error('[DDS] 基础地图初始化失败:', e);
  }
}
```

### 应用场景
- **监控面板**：查看 `window._ddsMapStatus` 了解加载状态
- **CEO 权重同步**：权重调整前检查 `locaReady` 是否为 true
- **性能分析**：收集 `loadTime` 数据，优化加载流程
- **故障排查**：用户反馈时可快速诊断（控制台看状态）

### 改动代码位置
- 文件：`index.html`
- 新增：全局 `window._ddsMapStatus` 对象（行号 ~2910）
- 改动：`initMap`, `initBasicMap`, `addLocaEnhancement` 中埋点

---

## 改进 4：CEO 权重与光源配置绑定（前置条件）

### 应用场景示例
CEO 调整权重时，动态修改 Loca 光源强度，强化决策可视化：

```javascript
// CEO 权重面板的回调（已有 /api/ceo_presets 端点）
function applyPresetWeights(preset) {
  // 权重调整示例：invest 预设
  const lightIntensity = preset === 'invest' 
    ? 2.5 // 投资侧：强化商业价值的光效
    : preset === 'design'
    ? 1.8 // 设计侧：温和光效，突出建筑美学
    : 2.2; // 默认平衡
    
  if (window._ddsLocaConfig && window._ddsMapStatus.locaReady) {
    window._ddsLocaConfig.lights.ambient.intensity = lightIntensity;
    // 实时更新（如果 Loca 支持动态修改）
    if (_locaContainer) {
      _locaContainer.ambLight.intensity = lightIntensity;
      _locaContainer.render();
    }
  }
}
```

---

## 实施优先级与工作量

| 改进项 | 优先级 | 工作量 | 收益 | 前置条件 |
|--------|--------|--------|------|---------|
| 改进 1：`map.on('complete')` | 🔴 高 | 0.5h | 加载时间 ↓ 80% | P0 完成 |
| 改进 2：光源参数外置 | 🟡 中 | 1h | CEO 权重集成就绪 | 改进 1 |
| 改进 3：监控埋点 | 🟡 中 | 0.5h | 故障诊断能力 | 改进 1 |
| 改进 4：权重-光源绑定 | 🟢 低 | 1.5h | CEO 决策可视化强化 | 改进 2 + 3 |

---

## 建议实施路线

### 第一批（今天，0.5h）
✅ **改进 1**：用 `map.on('complete')` 替代 22s 超时
- 快速见效，显著改善用户体验
- 代码改动最小（仅改造 `addLocaEnhancement` 的超时部分）

### 第二批（明天，1.5h）
✅ **改进 2 + 3**：光源参数外置 + 埋点
- 为 CEO 权重系统铺路
- 增加可调试性和可监控性

### 第三批（后天，1.5h）
✅ **改进 4**：权重-光源绑定 + 测试
- 完整的 CEO 决策可视化闭环
- 需要协调前端权重面板的回调

---

## 验证清单

完成改进 1–3 后，验证以下指标：

```
✓ 打开浏览器控制台 → 输入 window._ddsMapStatus
  └─ 应显示 {mapsReady: true, basicMapReady: true, locaReady: true, ...}

✓ 观察加载时间
  └─ basicMap: <1000ms（2D 地图应立即出现）
  └─ loca: 2000–5000ms（取决于网络）

✓ 模拟 Loca 失败（浏览器 DevTools 禁用 loca 脚本）
  └─ 应显示 basicMapReady: true, locaReady: false
  └─ 地图仍正常显示 2D 内容
```

---

## 总结

这 4 项改进均是**无需大型重构**、**可逐步实施**的微优化，预期总工作量 **3–4 小时**，但能显著提升：
- ⏱️ 加载速度（22s → 4s）
- 🎯 可调试性（参数外置）
- 📊 可观测性（埋点监控）
- 🧠 CEO 决策支持（权重-光源绑定）

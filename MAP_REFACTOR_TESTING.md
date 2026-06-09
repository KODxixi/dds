# 地图链路改造 — 本地验证清单

改造已完成。按以下步骤在本地验证：

## 前置环境

```bash
# 1. 确保依赖已安装
pip install -r requirements.txt
pip install anthropic  # LLM 后端

# 2. 配置 .env（复制 .env.example 并填入 API Key）
cp .env.example .env
# 填入：DEEPSEEK_API_KEY, AMAP_KEY, AMAP_SEC_CODE

# 3. 启动 Flask 服务
python app.py
# → http://localhost:8080
```

## 测试场景 1：基础地图必出（核心改进）

**步骤**：
1. 在浏览器打开 `http://localhost:8080`
2. 填写地块表单：
   - 城市：`三亚`
   - 地址或坐标：`海棠区南田路16号` 或 `lng=109.71899, lat=18.41040`
   - 预期均价：`35000`
3. 点击"生成报告"
4. **关键观察**：地图区应立即显示 **2D 地图 + 竞品圆点 + 配套 POI**
   - ✓ 如果看到 2D 地图（蓝色竞品点、配套设施标记），改造成功
   - ✗ 如果看到矢量拓扑沙盘（没有 AMap 图层），说明基础地图初始化失败

## 测试场景 2：Loca 3D 增强（可选，成功更好）

**步骤**：
1. 在上述 2D 地图基础上，**等待 2–5 秒**
2. **期望**：地图升级为 **3D 视图**（建筑物 3D 白模、倾斜 pitch）
3. **打开浏览器控制台**（F12 → Console）：
   - 搜索关键词 `[DDS]`
   - 确认**没有任何 error**（仅出现 info/warn）
4. **结果**：
   - ✓ 2D 升级为 3D，控制台无 error → Loca 加载成功
   - ✓ 保留 2D，控制台显示 `[DDS] Loca 3D WebGL 初始化...` warn → Loca 超时/失败，但基础地图保留（**改造成功！**）
   - ✗ 地图降级为沙盘，控制台有 error → 旧架构跌穿（改造失败）

## 测试场景 3：控制台日志验证

**打开浏览器 DevTools**（F12），切换到 Console 标签，过滤 `[DDS]`：

**应该看到的日志序列**（成功情况）：
```
[DDS] Loca enhancement skipped, basic map already rendered successfully
[DDS WebGL Map] 极速数据驱动更新完毕，用时 <10ms
```

或（Loca 失败情况）：
```
[DDS] Loca enhancement skipped, basic map already rendered successfully
[DDS] Loca 3D WebGL 初始化超时，保留基础地图
```

**不应该出现的日志**（表示旧架构）：
- ❌ `Fallback map init failed, fall back to Vector Topology Sandbox`
- ❌ 任何 JavaScript error 关于 `initFallbackMap` 或 `initLoca undefined`

## 测试场景 4：离线降级（沙盘兜底）

**模拟 maps.js 加载失败**（可选，仅技术验证）：
1. 在浏览器 DevTools 中打开 Network 标签
2. 找到 `https://webapi.amap.com/maps?v=2.0&key=...` 请求
3. 右键 → Block URL，模拟加载失败
4. 刷新页面
5. **期望**：显示 **矢量拓扑沙盘**（SVG 网格 + 拓扑线）
6. **验证**：这是唯一允许降级的场景（真正离线）

## 数据验证

**竞品列表完整性**：
1. 打开报告下方的"周边竞品"部分
2. **应该显示**：
   - 本地楼盘库竞品（来源标签：`DDS本地数据库`）
   - 配套信息（学校、医院、地铁、商业等）
   - 全量列表（不限于前 30 个）
3. **不应该出现** `nan` 或 `undefined` 字样

## 预期成果

| 指标 | 旧代码 | 新代码 | 验证方法 |
|------|--------|--------|---------|
| Loca 失败时用户看到的内容 | 沙盘（空白） | 2D 地图（可用） | 场景 2 |
| 地图容器被多次清空 | 是（跌穿） | 否（平稳） | 控制台无 error |
| 基础地图渲染时间 | 依赖 Loca（不可靠） | 立即（可靠） | 场景 1 → 2 秒内出现 2D |
| 竞品列表显示完整性 | 仅前 30 个 | 全量列出 | 周边竞品区滚动 |

## 问题排查

**如果看到沙盘而非 2D 地图**：
1. 检查浏览器 Console：是否有 error 说 `initBasicMap is not defined`
2. 确认 `index.html` 改动已保存（确认 `initBasicMap` 函数存在，行号 ~3714）
3. 刷新浏览器缓存（Ctrl+Shift+R 或清空本地存储）

**如果 Loca 3D 也无法显示**（且有 error）：
1. 检查 AMAP_KEY 和 AMAP_SEC_CODE 是否正确
2. 查看浏览器 Network 标签：Loca 脚本是否返回 200
3. 查看 console 中是否有 CORS 或安全性错误

**如果地图完全不出现**：
1. 检查 Network 标签：地块坐标是否为 null（说明 GIS 地理编码失败）
2. 检查后端日志：`app.py` 的标准输出是否有错误
3. 尝试用不同城市/地址重新生成报告

## 预计开发时间

- 代码改造：已完成（0.5 天）
- 本地测试验证：~30 分钟
- 线上部署验证：~1 小时（含 Cloud Run 部署）

---

**反馈方式**：
- 改造成功 ✓：提交 PR 标记为 ready
- 发现 bug ❌：提交 issue 附控制台日志 + Network 记录

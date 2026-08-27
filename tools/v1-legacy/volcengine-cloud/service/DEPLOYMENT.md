# DDS 云端部署指南

## 生产部署架构

```
浏览器 → garchos.sbai.asia → EIP → Caddy → GarchOS(:8000)
                                             └─ HMAC 代理 → DDS(127.0.0.1:8080)

私有 TOS Bucket ↔ DDS 容器缓存／动态报告同步
```

容器启动后，`DDS_MODE=cloud` 模式下 `query_local.py` 从 TOS 下载 Vault 数据到本地缓存 `/app/cache`，
然后 Flask 在容器内的 8080 端口提供 API 与报告页面。宿主机仅把该端口绑定到
`127.0.0.1:8080`，由同一台 ECS 上既有的 GarchOS/Caddy 代理访问。

**DDS 不直接开放公网端口。** 80/443 继续由 GarchOS/Caddy 独占；GarchOS 按既有
6 请求头 HMAC 契约访问 DDS。TOS 是数据与报告资产底座，不是 Web 运行时。

同一 Compose 中的 `dds-sync` 侧车每 15 分钟扫描 `/app/data_out`，以 SHA-256
内容寻址方式把报告、项目、指标和学习事件增量同步到 TOS；内容对象、manifest
和 complete marker 全部成功后才将该批次标记为完成。

### 两种部署模式

| 模式 | 命令 | 适用场景 |
|------|------|----------|
| 候选镜像构建 | `docker compose -f docker-compose.build.yml build` | 只构建，不启动服务 |
| GarchOS 集成运行 | `docker compose up -d --pull never` | 仅接受 `repository@sha256:...`；监听 `127.0.0.1:8080` |
| 旧版 Nginx 调试栈 | `docker compose -f docker-compose.full.yml up -d --pull never` | 仅本机调试；运行镜像仍锁定 digest |

## 本地预检

在本地机器执行环境就绪检查：

```powershell
python cloud/volcengine/volcengine_env_check.py
```

确认以下检查通过：
- TOS SDK 已安装（`tos>=2.8.0`）
- `.env.volcengine` 中必需的环境变量已填写
- 本地 Vault 数据目录存在

## 云端部署步骤

### 1. 创建 TOS Bucket

在火山引擎控制台创建 `dds-data-lake` Bucket（cn-shanghai 区域）：

```
dds-data-lake/
├── vault/                    # Vault 数据（636 城楼盘 CSV/Parquet）
├── releases/
│   └── current.json          # 发布指针
└── governance/               # T1-T10 治理输出
```

### 2. 创建 IAM 子账号

创建子账号并授予 TOS 读写权限（`tos:FullAccess` 或最小权限策略），获取 Access Key ID 和 Secret Access Key。

### 3. 上传 Vault 数据到 TOS

将本地 `Vault/` 目录上传到 TOS `dds-data-lake/vault/` 路径：

```powershell
python cloud/volcengine/tos_client.py upload-dir --local Vault --remote vault/
```

### 4. 构建、推送并解析不可变 digest

```bash
cd cloud/volcengine/service
export DDS_BUILD_IMAGE_REPOSITORY=registry.example.com/private/dds
export DDS_BUILD_IMAGE_TAG=<unique-commit-build-id>
docker compose -f docker-compose.build.yml build --pull
docker compose -f docker-compose.build.yml push
```

由受信 CI/镜像仓库读取推送结果的 `sha256`，审批记录必须绑定扫描结果与 SBOM。运行前只注入：

```text
DDS_IMAGE_REPOSITORY=registry.example.com/private/dds
DDS_IMAGE_DIGEST=sha256:<64-hex-digest>
```

运行 Compose 不含 `build:`，不能在部署主机重新构建或用任何可变镜像引用替代 digest。

### 5. 配置 .env.dds-cloud

```bash
cd cloud/volcengine/service
cp .env.dds-cloud.example .env.dds-cloud
```

填入以下密钥（仅在云端主机上操作，切勿提交）：

```text
DDS_TOS_ACCESS_KEY_ID=<你的 TOS Access Key ID>
DDS_TOS_SECRET_ACCESS_KEY=<你的 TOS Secret Access Key>
AMAP_KEY=<高德 Web 服务 Key>
AMAP_JS_KEY=<高德 JS API Key>
AMAP_SECURITY_CODE=<高德 JS 安全密钥>
DDS_ARK_API_KEY=<方舟服务端 Key>
DDS_ARK_MODEL_ALIAS=<稳定主模型别名>
DDS_ARK_FALLBACK_MODEL_ALIASES=<可选，同能力别名，逗号分隔>
DDS_ARK_BASE_URL=<可选，留空使用 SDK 默认>
DDS_ARK_TIMEOUT_SECONDS=90
DDS_ARK_MAX_OUTPUT_TOKENS=6000
DDS_ARK_MAX_ATTEMPTS=3
```

### 6. 启动服务

```bash
# 单服务模式
docker compose up -d --pull never

# 或旧版本机调试模式（Flask + Nginx，不用于生产）
docker compose -f docker-compose.full.yml up -d --pull never
```

### 7. 验证健康检查

```bash
# 检查容器状态
docker compose ps

# 健康端点
curl http://127.0.0.1:8080/api/health
```

预期返回：

```json
{
  "ok": true,
  "cities": 636,
  "mode": "cloud",
  "issues": [],
  "warnings": []
}
```

## 报告生成测试

```bash
curl -X POST http://127.0.0.1:8080/api/report \
  -H 'Content-Type: application/json' \
  -d '{"city":"三亚","address":"海棠区南田路16号","expected_price":35000}'
```

## API 路由一览

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 前端 Web 界面（Bauhaus 风格 + Loca 3D 地图） |
| `/api/health` | GET | 容器健康检查 |
| `/api/map_config` | GET | 高德地图配置 |
| `/api/cities` | GET | 数据池城市列表 |
| `/api/areas` | GET | 城市区域列表 |
| `/api/report` | POST | 地块报告生成 |
| `/api/chat` | POST | 同步 AI 对话 |
| `/api/chat_stream` | POST | 流式 AI 对话（SSE） |
| `/api/supplement` | POST | 线上证据补充 |
| `/api/ceo_reweight` | POST | CEO 权重调整 |
| `/api/ceo_presets` | GET | 权重预设列表 |
| `/api/ceo_record_weights` | POST | 记录权重选择 |
| `/api/ceo_learned_weights` | GET/POST | 获取/提交学习后权重 |

## 发布规则

- **不发布 `current.json` 直到验证门禁通过**（数据完整性 + 健康检查 + 冒烟测试）
- 回滚是指针式：重新发布历史版本的 `current.json`，不删除 TOS 对象
- 发布新数据时先上传到 `dds/releases/<version>/`，验证通过后再更新 `current.json` 指针

## 停止 / 重启

```bash
# 重启 Flask 服务
docker compose restart dds-app

# 停止全部服务
docker compose down

# 全栈模式
docker compose -f docker-compose.full.yml restart dds-app
docker compose -f docker-compose.full.yml down
```

## 数据卷说明

| 卷名 | 挂载点 | 用途 |
|------|--------|------|
| `dds-cache` | `/app/cache` | TOS 下载数据的本地缓存 |
| `dds-vault` | `/app/Vault` | 本地楼盘数据持久化（云端模式优先 TOS，本地数据作热缓存） |
| `dds-data-out` | `/app/data_out` | 报告、项目、指标和学习事件的动态产物，供增量同步到 TOS |

## GarchOS/Caddy 集成

生产请求链固定为 `Caddy → GarchOS(:8000) → DDS(127.0.0.1:8080)`。Caddy 不应
绕过母站直接把 DDS 暴露给浏览器；GarchOS 负责登录态、权限和 6 请求头 HMAC 签名。

## 旧版 Nginx 调试代理

### 独立站点（nginx.conf.example）

仅用于本机兼容性调试，不再作为火山云生产入口：

```bash
cp nginx.conf.example /etc/nginx/conf.d/dds.conf
# 修改 server_name 和 SSL 证书路径
nginx -t && nginx -s reload
```

### 调试栈 Docker 内部（nginx.full.conf）

用于 `docker-compose.full.yml`，仅绑定 `127.0.0.1:8081`，不得占用公网 80/443。

## 故障排查

### 容器启动失败

```bash
# 查看日志
docker compose logs dds-app

# 常见问题：
# - startup_check 报缺少环境变量 → 检查 .env.dds-cloud 是否完整
# - TOS 连接失败 → 检查网络/代理设置，确认 DDS_TOS_DISABLE_PROXY=1
# - 数据加载失败 → 确认 TOS Bucket 中 vault/ 路径有数据
```

### 健康检查不通过

```bash
# 手动调用健康端点
curl -v http://127.0.0.1:8080/api/health

# 检查返回的 issues 字段
# - "缺少环境变量" → .env.dds-cloud 未配置完整
# - "import 失败" → 停止发布，回到独立 build 流水线生成新 digest 并重新验收
# - "数据缺失" → TOS 中无数据或缓存未同步
```

### 报告生成超时

检查 DDS 健康结果中的 Ark reason code、`DDS_ARK_TIMEOUT_SECONDS` 与服务端用量审计。不得在部署主机用旧供应商变量或把 Key 写入命令历史；需要真实调用时只运行受控的 Ark smoke，并生成签名验收证明。

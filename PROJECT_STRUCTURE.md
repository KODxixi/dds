# DDS V2 项目结构

DDS V2 只采用 `src` 布局。根目录不再保留第二份 `dds/`，文档描述与磁盘结构以本文件为准。

```text
DDS_v2/
├── src/dds/              # 唯一正式 Python 包
├── tests/
│   ├── unit/             # 纯模块与契约测试
│   ├── integration/      # 跨模块、数据适配与服务测试
│   ├── e2e/              # 完整编排与垂直切片
│   ├── golden/           # 报告编译基准测试
│   └── fixtures/         # 固定测试输入
├── tools/dev/            # 非正式交付入口的开发诊断工具
├── docs/
│   ├── architecture/     # 当前架构说明
│   ├── plans/            # 历史与实施计划
│   ├── handoffs/         # 唯一 handoff 位置
│   └── research/         # 调研材料
├── data/                 # 运行数据边界，不进入 wheel
├── pyproject.toml        # 包、依赖与工具配置
└── uv.lock               # 可复现依赖锁
```

## 边界

- `src/dds` 是唯一代码真相；不得恢复根级 `dds/`。
- `docs/handoffs` 是唯一 handoff 位置；根级 `agent_handoffs` 已废弃。
- `data/projects`、`data/cache`、`data/exports` 是本地或可重建产物，Git 忽略。
- `.venv`、缓存、覆盖率、构建目录和字节码均为可重建内容。
- DDS V1 Vault 只读，通过环境变量注入，不迁入本仓库。

## 标准验收

```powershell
uv sync --dev
uv run ruff check --no-cache src tests tools
uv run pytest -p no:cacheprovider
```

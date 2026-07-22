# DDS V2 结构清理 Handoff

日期：2026-07-22

## 当前磁盘真相

- 正式源码仅位于 `src/dds`。
- 测试按 `unit`、`integration`、`e2e`、`golden`、`fixtures` 分层。
- 文档和 handoff 仅位于 `docs`，根级 `agent_handoffs` 已移除。
- 开发诊断工具仅位于 `tools/dev`。
- 依赖由 `pyproject.toml` 与 `uv.lock` 管理。
- 根级旧源码、旧安装脚本、散落报告、构建目录和缓存均已清理。

## 已验证

```text
uv run ruff check --no-cache src tests tools  -> passed
uv run pytest -p no:cacheprovider             -> 130 passed
```

## 剩余产品工作

这些不是目录清理错误：

1. 正式浏览器 QA 仍需在真实交付产物上运行，并绑定 HTML hash。
2. 覆盖率当前约 72%，若要达到原计划的 80%，需补充 renderer、report_document、官方数据适配器等行为测试。
3. V1 Vault 与输出目录仍需由运行环境显式配置。

## 禁止回退

- 不得重新加入测试 `sys.path.insert`。
- 不得恢复根级 `dds/`、`setup.py` 或 `agent_handoffs/`。
- 不得把运行报告、缓存或本地数据库提交到 Git。

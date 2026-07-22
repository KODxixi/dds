# Changelog

## 2026-07-22

- 统一为 `src/dds` 可安装包布局，移除根级重复源码和失效 `setup.py`。
- 将测试整理为 `unit`、`integration`、`e2e`、`golden` 与 `fixtures`。
- 删除测试中的 `sys.path` 注入，统一使用 `uv sync --dev` 和锁文件。
- 将计划、研究和 handoff 统一收敛到 `docs/`。
- 将开发调试脚本收敛到 `tools/dev`，删除失效工具和散落产物。
- 清理构建目录、pytest 缓存和 Python 字节码。
- 完整质量门结果：Ruff 通过，130 项 pytest 通过。

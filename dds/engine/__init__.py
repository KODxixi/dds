"""引擎层 - 报告契约校验与生成逻辑。

核心组件：
- ContractEnforcer: 契约强制校验，确保输出永远符合 DDS 规范
"""

from dds.engine.contract_enforcer import ContractEnforcer

__all__ = [
    "ContractEnforcer",
]

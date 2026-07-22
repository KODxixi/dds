"""Contract validation engine.

ContractEnforcer evaluates the four gates and rejects output that does not meet
the DDS truth and delivery contract.
"""

from dds.engine.contract_enforcer import ContractEnforcer

__all__ = [
    "ContractEnforcer",
]

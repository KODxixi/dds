"""VA1 auditable premium ranges with fail-closed readiness."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence


class PremiumScope(str, Enum):
    SELLING_PRICE = "selling_price"
    SELL_THROUGH = "sell_through"
    LAND_TRANSACTION = "land_transaction"


class PremiumStatus(str, Enum):
    ASSESSABLE = "assessable"
    NOT_ASSESSABLE = "not_assessable"


@dataclass(frozen=True, slots=True)
class PremiumDriver:
    action_id: str
    design_action: str
    experience_mechanism: str
    low_rate: Decimal | float | str
    base_rate: Decimal | float | str
    high_rate: Decimal | float | str
    incremental_cost_cny: Decimal | float | str | None
    evidence_refs: tuple[str, ...]
    method: str

    def rates(self) -> tuple[Decimal, Decimal, Decimal]:
        rates = tuple(Decimal(str(value)) for value in (self.low_rate, self.base_rate, self.high_rate))
        if not (Decimal("-1") < rates[0] <= rates[1] <= rates[2]):
            raise ValueError("premium rates must be ordered low <= base <= high and > -1")
        return rates  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class PremiumScenario:
    name: str
    combined_rate: Decimal
    resulting_unit_price_cny_m2: Decimal
    gross_incremental_value_cny: Decimal
    incremental_cost_cny: Decimal
    net_incremental_value_cny: Decimal
    formula: str


@dataclass(frozen=True, slots=True)
class PremiumResult:
    status: PremiumStatus
    scope: PremiumScope
    scenarios: tuple[PremiumScenario, ...]
    evidence_refs: tuple[str, ...]
    assumptions: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    gaps: tuple[str, ...]
    actions: tuple[str, ...]


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PremiumEngine:
    """Translate evidenced drivers into scenarios; never invent a missing input."""

    def assess(
        self,
        *,
        scope: PremiumScope,
        baseline_unit_price_cny_m2: Decimal | float | str | None,
        saleable_area_m2: Decimal | float | str | None,
        baseline_evidence_refs: Sequence[str],
        drivers: Sequence[PremiumDriver],
    ) -> PremiumResult:
        gaps: list[str] = []
        actions: list[str] = []
        if scope is not PremiumScope.SELLING_PRICE:
            gaps.append(f"{scope.value} 模型尚未提供结果变量和验证方法。")
            actions.append("提供对应结果变量、基准组、成本和反事实方法。")
        if baseline_unit_price_cny_m2 is None:
            gaps.append("缺少可比基准单价。")
            actions.append("引用 SC2 有效竞品或经确认的成交基准。")
        if saleable_area_m2 is None:
            gaps.append("缺少可售面积，无法计算价值增量。")
            actions.append("从已勾稽的产品方案提供可售面积。")
        if not baseline_evidence_refs:
            gaps.append("基准价格没有 EvidenceRecord 引用。")
            actions.append("冻结基准组来源和统计公式。")
        if not drivers:
            gaps.append("缺少设计动作、体验机制和成本驱动项。")
            actions.append("建立至少一个可审计的设计动作链。")

        total_cost = Decimal("0")
        rates: list[tuple[Decimal, Decimal, Decimal]] = []
        driver_refs: list[str] = []
        for driver in drivers:
            try:
                rates.append(driver.rates())
            except (ValueError, ArithmeticError) as exc:
                gaps.append(f"驱动项 {driver.action_id} 的溢价区间非法：{exc}")
                continue
            if driver.incremental_cost_cny is None:
                gaps.append(f"驱动项 {driver.action_id} 缺少增量成本。")
            else:
                cost = Decimal(str(driver.incremental_cost_cny))
                if cost < 0:
                    gaps.append(f"驱动项 {driver.action_id} 的增量成本不能为负数。")
                else:
                    total_cost += cost
            if not driver.method.strip():
                gaps.append(f"驱动项 {driver.action_id} 缺少测算方法。")
            if not driver.evidence_refs:
                gaps.append(f"驱动项 {driver.action_id} 没有 EvidenceRecord 引用。")
            driver_refs.extend(driver.evidence_refs)

        if gaps:
            return PremiumResult(
                status=PremiumStatus.NOT_ASSESSABLE,
                scope=scope,
                scenarios=(),
                evidence_refs=tuple(dict.fromkeys([*baseline_evidence_refs, *driver_refs])),
                assumptions=(),
                counter_evidence=("任一基准、结果变量、成本、方法或来源缺失时禁止输出溢价数字。",),
                gaps=tuple(dict.fromkeys(gaps)),
                actions=tuple(dict.fromkeys(actions)),
            )

        baseline = Decimal(str(baseline_unit_price_cny_m2))
        area = Decimal(str(saleable_area_m2))
        if baseline <= 0 or area <= 0:
            return PremiumResult(
                status=PremiumStatus.NOT_ASSESSABLE,
                scope=scope,
                scenarios=(),
                evidence_refs=tuple(dict.fromkeys([*baseline_evidence_refs, *driver_refs])),
                assumptions=(),
                counter_evidence=("非正的基准单价或可售面积不能进入溢价计算。",),
                gaps=("基准单价与可售面积必须为正数。",),
                actions=("核验 SC2 价格和 AD3 面积口径。",),
            )

        # Multiplicative composition avoids silently treating interacting design
        # drivers as independent additive percentage points.
        combined = []
        for index in range(3):
            multiplier = Decimal("1")
            for rate in rates:
                multiplier *= Decimal("1") + rate[index]
            combined.append(multiplier - Decimal("1"))
        scenarios: list[PremiumScenario] = []
        for name, rate in zip(("conservative", "base", "optimistic"), combined):
            resulting_price = _money(baseline * (Decimal("1") + rate))
            gross_value = _money((resulting_price - baseline) * area)
            cost = _money(total_cost)
            scenarios.append(
                PremiumScenario(
                    name=name,
                    combined_rate=rate.quantize(Decimal("0.000001")),
                    resulting_unit_price_cny_m2=resulting_price,
                    gross_incremental_value_cny=gross_value,
                    incremental_cost_cny=cost,
                    net_incremental_value_cny=_money(gross_value - cost),
                    formula=(
                        "combined_rate=product(1+driver_rate)-1; "
                        "unit_price=baseline*(1+combined_rate); "
                        "net=(unit_price-baseline)*saleable_area-incremental_cost"
                    ),
                )
            )
        return PremiumResult(
            status=PremiumStatus.ASSESSABLE,
            scope=scope,
            scenarios=tuple(scenarios),
            evidence_refs=tuple(dict.fromkeys([*baseline_evidence_refs, *driver_refs])),
            assumptions=(
                "驱动项按乘法组合，所有费率均视为经证据支持的场景参数。",
                "结果是反事实场景区间，不是已实现售价或土地成交溢价。",
            ),
            counter_evidence=("若实际客户支付意愿或销售流速不支持驱动费率，应回撤至零溢价情景。",),
            gaps=(),
            actions=("用后续成交与去化数据回测并版本化参数。",),
        )


__all__ = [
    "PremiumDriver",
    "PremiumEngine",
    "PremiumResult",
    "PremiumScenario",
    "PremiumScope",
    "PremiumStatus",
]

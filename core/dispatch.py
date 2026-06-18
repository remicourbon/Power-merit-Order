"""Merit-order market clearing -- the price-formation mechanism.

Units are stacked by increasing SRMC and dispatched until demand is met. Under
marginal (pay-as-clear) pricing the clearing price equals the SRMC of the last
unit called -- the *marginal unit*. Every cheaper (infra-marginal) unit earns
an inframarginal rent equal to ``(price - its SRMC) x MW dispatched``.

The clearing price is exactly the shadow price (dual) of the demand-balance
constraint of the equivalent dispatch LP: the marginal cost of serving one more
MW. We compute it by sorting rather than by calling an LP, because for this
single-balance problem the sort is the exact dual and is far easier to defend on
a whiteboard. (The battery problem, which has inter-temporal constraints, does
go through an LP -- see battery.py.)

This is the power analogue of GPW / netback: the price is set at the margin,
while the value is read off unit by unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core.data_models import Fuel, Unit
from core.marginal_cost import short_run_marginal_cost


class DispatchError(ValueError):
    """Raised on inconsistent dispatch inputs."""


@dataclass(frozen=True)
class DispatchResult:
    clearing_price: float                       # EUR / MWh_e (= demand dual)
    marginal_unit: str | None                   # technology key, or None if lost load
    dispatch: Mapping[str, float] = field(default_factory=dict)   # tech -> MW
    unserved_mw: float = 0.0
    inframarginal_rent: float = 0.0             # total EUR/h earned above SRMC


def merit_order(units: Mapping[str, Unit], fuels: Mapping[str, Fuel],
                co2_price: float) -> list[tuple[str, Unit, float]]:
    """`(technology, unit, SRMC)` triples sorted by increasing SRMC.

    The ordering used both to clear the market and to draw the merit-order
    step chart (cumulative capacity on x, SRMC on y).
    """
    triples = [
        (tech, u, short_run_marginal_cost(u, fuels[u.fuel], co2_price))
        for tech, u in units.items()
    ]
    triples.sort(key=lambda t: t[2])
    return triples


def clear_market(units: Mapping[str, Unit], fuels: Mapping[str, Fuel],
                 co2_price: float, demand_mw: float,
                 available_mw: Mapping[str, float],
                 value_of_lost_load: float = 3000.0) -> DispatchResult:
    """Clear the market for a single hour.

    `available_mw` is the usable capacity of each technology this hour (firm
    units at nameplate, renewables scaled by their capacity factor). A
    technology missing from the map is treated as unavailable (0 MW).

    If demand exceeds total available capacity the market is short: the price
    is set to ``value_of_lost_load`` and the shortfall is reported as
    ``unserved_mw``.
    """
    if demand_mw < 0:
        raise DispatchError("demand must be non-negative")
    unknown = set(available_mw) - set(units)
    if unknown:
        raise DispatchError(f"available_mw has unknown technologies: {unknown}")

    stack = []
    for tech, u, srmc in merit_order(units, fuels, co2_price):
        stack.append((tech, srmc, max(available_mw.get(tech, 0.0), 0.0)))

    dispatch: dict[str, float] = {}
    remaining = demand_mw
    marginal_unit: str | None = None
    clearing_price = 0.0
    for tech, srmc, cap in stack:
        if remaining <= 1e-9:
            dispatch[tech] = 0.0
            continue
        used = min(cap, remaining)
        dispatch[tech] = used
        remaining -= used
        if used > 0:
            marginal_unit = tech
            clearing_price = srmc

    if remaining > 1e-6:
        clearing_price = value_of_lost_load
        marginal_unit = None

    rent = sum(max(clearing_price - srmc, 0.0) * dispatch.get(tech, 0.0)
               for tech, srmc, _cap in stack)

    return DispatchResult(
        clearing_price=clearing_price,
        marginal_unit=marginal_unit,
        dispatch=dispatch,
        unserved_mw=max(remaining, 0.0),
        inframarginal_rent=rent,
    )

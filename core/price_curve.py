"""Hourly price curve -- the engine output.

For each hour we (1) build the available capacity of every technology
(renewables scaled by their capacity factor, firm units at nameplate) and (2)
clear the market on that hour's demand. Looping over the day yields the price
curve P(t). As solar peaks at midday the residual demand collapses, the marginal
unit slides down the stack, and the price drops -- the mechanism behind the
"duck curve". This P(t) is what the battery arbitrages in battery.py.

Pure functions over the frozen dataclasses. The CO2 price and any fuel-price
overrides arrive already baked into the `fuels` mapping by the caller
(system.py), so this module has no notion of a scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.data_models import Fuel, Profile, System, Unit
from core.dispatch import clear_market
from core.profiles import available_mw, demand_mw_series, renewable_mw


@dataclass(frozen=True)
class HourResult:
    hour: int
    demand_mw: float
    price: float                       # EUR / MWh_e
    marginal_unit: str | None
    renewable_mw: float                # solar + wind dispatched
    residual_demand_mw: float          # demand net of dispatched renewables
    unserved_mw: float
    dispatch: Mapping[str, float]


def compute_price_curve(system: System, units: Mapping[str, Unit],
                        fuels: Mapping[str, Fuel], co2_price: float,
                        demand_profile: Profile, solar_profile: Profile,
                        wind_profile: Profile,
                        value_of_lost_load: float = 3000.0) -> list[HourResult]:
    """Run the merit-order clearing for every hour of the day."""
    demand = demand_mw_series(system, demand_profile)
    results: list[HourResult] = []
    for t in range(len(demand)):
        avail = available_mw(system, units,
                             solar_profile.values[t], wind_profile.values[t])
        res = clear_market(units, fuels, co2_price, demand[t], avail,
                           value_of_lost_load=value_of_lost_load)
        ren = renewable_mw(res.dispatch, units)
        results.append(HourResult(
            hour=t,
            demand_mw=demand[t],
            price=res.clearing_price,
            marginal_unit=res.marginal_unit,
            renewable_mw=ren,
            residual_demand_mw=demand[t] - ren,
            unserved_mw=res.unserved_mw,
            dispatch=res.dispatch,
        ))
    return results

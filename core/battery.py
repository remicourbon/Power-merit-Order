"""The battery arbitrage LP. The second engine of the project.

Mathematical formulation (the blackboard version, to be defended verbatim):

  Decision variables (per hour t, dt = 1 h)
    c_t   >= 0           charge power, MW
    d_t   >= 0           discharge power, MW
    soc_t in [0, E]      state of charge at the END of hour t, MWh

  Objective (EUR over the horizon, since MW x EUR/MWh x h)
    max  sum_t  P(t) * (d_t - c_t) * dt   [ - k_deg * d_t * dt ]

  Constraints                                              name
    soc_t = soc_{t-1} + eta_c*c_t*dt - d_t/eta_d*dt        "soc_t"
    soc_{T-1} = soc_0   (cyclic)                           "cyclic"
    0 <= c_t, d_t <= P  ;  0 <= soc_t <= E   (bounds)

Modelling notes (defend these):
- No binary variable is needed. Because the round-trip efficiency
  eta_rt = eta_c * eta_d < 1 makes simultaneous charge/discharge strictly
  wasteful, the LP optimum never does both at once -- the model stays a pure
  LP (not a MILP), exactly as the crude project keeps vessel choice outside
  the LP to stay linear.
- The dual of the "soc_t" balance is the marginal value of one more MWh stored
  in the battery at hour t -- the storage "water value". It is the analogue of
  the crude LP's sulfur shadow price: a price the model discovers, not one it is
  given. We export it per hour.
- Price-taker: the battery does not move P(t). Valid for a small battery; a
  large one would feed back into residual demand (out of scope).
- Perfect foresight: P(t) is known in advance, so the objective is the
  theoretical UPPER BOUND on arbitrage revenue, not a tradeable strategy. This
  is the storage analogue of "forward curves are hedgeable prices, not
  forecasts".

The break-even price ratio is 1 / eta_rt: a cycle is only worthwhile when
sell / buy exceeds it -- the storage analogue of a spark spread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import pulp


class OptimisationError(ValueError):
    """Raised on inconsistent optimisation inputs."""


@dataclass(frozen=True)
class BatteryResult:
    status: str                              # "Optimal", "Infeasible", ...
    charge_mw: tuple[float, ...]
    discharge_mw: tuple[float, ...]
    soc_mwh: tuple[float, ...]
    energy_value_eur_mwh: tuple[float, ...]  # dual of each soc_t balance
    profit_eur: float
    equivalent_cycles: float
    breakeven_spread_ratio: float            # 1 / eta_rt
    avg_buy_price: float | None
    avg_sell_price: float | None
    shadow_prices: Mapping[str, float]       # {constraint_name: dual}

    @property
    def optimal(self) -> bool:
        return self.status == "Optimal"


def optimise_battery(prices: Sequence[float], power_mw: float, energy_mwh: float,
                     round_trip_pct: float = 85.0, dt: float = 1.0,
                     soc_init: float = 0.0, cyclic: bool = True,
                     degradation_cost: float = 0.0) -> BatteryResult:
    """Optimise the charge/discharge schedule over `prices`.

    `round_trip_pct` is split evenly into one-way charge and discharge
    efficiencies (sqrt), so eta_c = eta_d = sqrt(round_trip).
    """
    if len(prices) == 0:
        raise OptimisationError("prices must be non-empty")
    if power_mw <= 0 or energy_mwh <= 0:
        raise OptimisationError("power_mw and energy_mwh must be > 0")
    if not 0 < round_trip_pct <= 100:
        raise OptimisationError("round_trip_pct must be in (0, 100]")

    eta = math.sqrt(round_trip_pct / 100.0)
    eta_rt = eta * eta
    T = len(prices)

    prob = pulp.LpProblem("battery_arbitrage", pulp.LpMaximize)
    c = [pulp.LpVariable(f"c_{t}", lowBound=0, upBound=power_mw) for t in range(T)]
    d = [pulp.LpVariable(f"d_{t}", lowBound=0, upBound=power_mw) for t in range(T)]
    soc = [pulp.LpVariable(f"soc_{t}", lowBound=0, upBound=energy_mwh) for t in range(T)]

    # --- objective ---------------------------------------------------------
    revenue = pulp.lpSum(prices[t] * (d[t] - c[t]) * dt for t in range(T))
    degradation = pulp.lpSum(degradation_cost * d[t] * dt for t in range(T))
    prob += revenue - degradation

    # --- constraints (named, so duals are addressable) ---------------------
    for t in range(T):
        prev = soc_init if t == 0 else soc[t - 1]
        prob += (soc[t] == prev + eta * c[t] * dt - d[t] * (1.0 / eta) * dt, f"soc_{t}")
    if cyclic:
        prob += (soc[T - 1] == soc_init, "cyclic")

    # --- solve --------------------------------------------------------------
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        empty = tuple([0.0] * T)
        return BatteryResult(status, empty, empty, empty, empty, 0.0, 0.0,
                             1.0 / eta_rt, None, None, MappingProxyType({}))

    charge = tuple(float(c[t].value() or 0.0) for t in range(T))
    discharge = tuple(float(d[t].value() or 0.0) for t in range(T))
    soc_vals = tuple(float(soc[t].value() or 0.0) for t in range(T))

    duals = {name: con.pi for name, con in prob.constraints.items()
             if con.pi is not None}
    energy_value = tuple(duals.get(f"soc_{t}", 0.0) for t in range(T))

    profit = sum(prices[t] * (discharge[t] - charge[t]) * dt for t in range(T))
    profit -= sum(degradation_cost * discharge[t] * dt for t in range(T))

    sell_energy = sum(discharge[t] * dt for t in range(T))
    buy_energy = sum(charge[t] * dt for t in range(T))
    cycles = sell_energy / energy_mwh
    avg_buy = (sum(prices[t] * charge[t] * dt for t in range(T)) / buy_energy
               if buy_energy > 1e-9 else None)
    avg_sell = (sum(prices[t] * discharge[t] * dt for t in range(T)) / sell_energy
                if sell_energy > 1e-9 else None)

    return BatteryResult(
        status=status,
        charge_mw=charge,
        discharge_mw=discharge,
        soc_mwh=soc_vals,
        energy_value_eur_mwh=energy_value,
        profit_eur=profit,
        equivalent_cycles=cycles,
        breakeven_spread_ratio=1.0 / eta_rt,
        avg_buy_price=avg_buy,
        avg_sell_price=avg_sell,
        shadow_prices=MappingProxyType(duals),
    )

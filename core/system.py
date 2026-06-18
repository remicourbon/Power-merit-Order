"""The orchestrator: from a system + a scenario to prices and battery value.

Mirrors the crude project's decision.py. Given a system (a stylized fleet), a
SCENARIO (the editable market inputs: CO2 price and fuel-price overrides) and a
battery, it:

  1. applies the scenario to the fuels (a parallel shift of fuel prices), so the
     rest of the pipeline sees plain Fuel objects and never knows about sliders;
  2. computes the hourly price curve from the merit order (price_curve.py);
  3. arbitrages that curve with the battery LP (battery.py);

and returns one rich, frozen SystemResult that is everything the UI needs.

A `system_override` (a System built live from sliders) lets the sandbox feed a
custom fleet without touching the data files -- the exact pattern decision.py
uses for the Marseille sandbox via `config_override`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Mapping, Optional

from core.battery import BatteryResult, optimise_battery
from core.data_models import Battery, Dataset, Fuel, System, Unit
from core.price_curve import HourResult, compute_price_curve


class SystemError(ValueError):
    """Raised when a system cannot be evaluated."""


@dataclass(frozen=True)
class Scenario:
    """The editable market inputs (the sidebar sliders)."""
    co2_price: float = 75.0
    fuel_price_overrides: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if self.co2_price < 0:
            raise SystemError("co2_price must be >= 0")
        object.__setattr__(self, "fuel_price_overrides",
                           dict(self.fuel_price_overrides))


@dataclass(frozen=True)
class SystemResult:
    system_key: str
    system_name: str
    scenario: Scenario
    units: Mapping[str, Unit]
    fuels: Mapping[str, Fuel]               # after scenario overrides
    price_curve: tuple[HourResult, ...]
    battery: BatteryResult

    @property
    def prices(self) -> list[float]:
        return [h.price for h in self.price_curve]

    @property
    def avg_price(self) -> float:
        p = self.prices
        return sum(p) / len(p) if p else 0.0

    @property
    def min_price(self) -> float:
        return min(self.prices) if self.price_curve else 0.0

    @property
    def max_price(self) -> float:
        return max(self.prices) if self.price_curve else 0.0


def effective_fuels(ds: Dataset, scenario: Scenario) -> dict[str, Fuel]:
    """Apply the scenario's fuel-price overrides to the dataset fuels.

    Emission factors are untouched (physical constants); only prices move.
    """
    unknown = set(scenario.fuel_price_overrides) - set(ds.fuels)
    if unknown:
        raise SystemError(f"override for unknown fuels: {unknown}")
    out = {}
    for key, fuel in ds.fuels.items():
        if key in scenario.fuel_price_overrides:
            out[key] = dataclasses.replace(
                fuel, price_eur_mwh_th=scenario.fuel_price_overrides[key])
        else:
            out[key] = fuel
    return out


def evaluate_system(ds: Dataset, system_key: str, scenario: Scenario,
                    battery_key: Optional[str] = None,
                    battery_override: Optional[Battery] = None,
                    system_override: Optional[System] = None) -> SystemResult:
    """Full pipeline: price the merit order, then optimise the battery.

    `system_override` replaces the YAML system entirely (the sandbox path).
    `battery_override` likewise replaces the chosen battery class.
    """
    if system_override is not None:
        system = system_override
    else:
        if system_key not in ds.systems:
            raise SystemError(f"unknown system '{system_key}'")
        system = ds.systems[system_key]

    if battery_override is not None:
        battery = battery_override
    else:
        if battery_key is None or battery_key not in ds.batteries:
            raise SystemError(f"unknown battery '{battery_key}'")
        battery = ds.batteries[battery_key]

    fuels = effective_fuels(ds, scenario)
    units = {tech: ds.units[tech] for tech in system.capacities_gw}
    demand_p = ds.profile(system.demand_profile, "demand")
    solar_p = ds.profile(system.solar_profile, "solar")
    wind_p = ds.profile(system.wind_profile, "wind")

    curve = compute_price_curve(system, units, fuels, scenario.co2_price,
                                demand_p, solar_p, wind_p)
    result = optimise_battery([h.price for h in curve], battery.power_mw,
                              battery.energy_mwh, battery.round_trip_pct)

    return SystemResult(
        system_key=system_key,
        system_name=system.name,
        scenario=scenario,
        units=units,
        fuels=fuels,
        price_curve=tuple(curve),
        battery=result,
    )

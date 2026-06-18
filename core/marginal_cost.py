"""Short-run marginal cost (SRMC) and fuel switching.

The SRMC is the variable cost of one extra MWh of electricity. It combines a
property of the fuel (price + carbon intensity) with two properties of the unit
(efficiency, variable O&M)::

    SRMC = (fuel.price + fuel.emission_factor * co2_price) / eta + VOM

Fuel cost and carbon cost are added at the THERMAL level (per MWh_th) and then
divided by the efficiency eta to express the result per MWh_e. This is the
power-market analogue of a refining crack / spark spread: a unit runs whenever
the clearing price exceeds its SRMC.

Design rule (see README): emission factors come from the fuel, not the unit, so
two units burning the same fuel share one emission factor and differ only by
efficiency. The CO2 price is the single carbon input and is passed in by the
caller (a scenario slider), never read from a data file.
"""

from __future__ import annotations

from core.data_models import Fuel, Unit


class MarginalCostError(ValueError):
    """Raised on inconsistent marginal-cost inputs."""


def short_run_marginal_cost(unit: Unit, fuel: Fuel, co2_price: float) -> float:
    """Carbon-adjusted SRMC of `unit` burning `fuel`, in EUR / MWh_e.

    `co2_price` is the EUA price in EUR per tonne CO2.
    """
    if fuel.key != unit.fuel:
        raise MarginalCostError(
            f"unit '{unit.key}' burns '{unit.fuel}', got fuel '{fuel.key}'")
    if co2_price < 0:
        raise MarginalCostError("co2_price must be >= 0")
    thermal_cost = fuel.price_eur_mwh_th + fuel.emission_factor_tco2_mwh_th * co2_price
    return thermal_cost / unit.efficiency + unit.variable_om


def fuel_switching_co2_price(unit_a: Unit, fuel_a: Fuel,
                             unit_b: Unit, fuel_b: Fuel) -> float | None:
    """CO2 price (EUR/tCO2) at which two units have equal SRMC.

    Solving SRMC_a(p) = SRMC_b(p) for the CO2 price p::

        (fp_a + EF_a*p)/eta_a + vom_a = (fp_b + EF_b*p)/eta_b + vom_b

        p = [ (fp_b/eta_b + vom_b) - (fp_a/eta_a + vom_a) ]
            / [ EF_a/eta_a - EF_b/eta_b ]

    Returns None when the two SRMC curves are parallel in CO2 (equal carbon
    intensity per MWh_e): no crossing exists. A negative result means the
    curves only cross at a non-physical (negative) CO2 price.
    """
    fp_a, ef_a, eta_a = fuel_a.price_eur_mwh_th, fuel_a.emission_factor_tco2_mwh_th, unit_a.efficiency
    fp_b, ef_b, eta_b = fuel_b.price_eur_mwh_th, fuel_b.emission_factor_tco2_mwh_th, unit_b.efficiency

    intercept = (fp_b / eta_b + unit_b.variable_om) - (fp_a / eta_a + unit_a.variable_om)
    slope = ef_a / eta_a - ef_b / eta_b
    if slope == 0:
        return None
    return intercept / slope

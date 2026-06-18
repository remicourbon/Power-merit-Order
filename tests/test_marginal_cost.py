"""Tests for core.marginal_cost. Reference numbers are hand-computed."""

import math

import pytest

from core.data_models import Fuel, Unit
from core.marginal_cost import (MarginalCostError, fuel_switching_co2_price,
                               short_run_marginal_cost)

GAS = Fuel("natural_gas", 35.0, 0.20)
COAL = Fuel("hard_coal", 12.0, 0.34)
URANIUM = Fuel("uranium", 2.0, 0.0)
NONE = Fuel("none", 0.0, 0.0)

CCGT = Unit("gas_ccgt", "gas_ccgt", 0.55, 2.0, "natural_gas", "firm")
OCGT = Unit("gas_ocgt", "gas_ocgt", 0.38, 3.0, "natural_gas", "firm")
COAL_U = Unit("coal", "coal", 0.40, 3.0, "hard_coal", "firm")
NUKE = Unit("nuclear", "nuclear", 0.33, 1.0, "uranium", "firm")
SOLAR = Unit("solar", "solar", 1.0, 0.0, "none", "solar")


def test_ccgt_srmc_known_value():
    # (35 + 0.20*75)/0.55 + 2 = 50/0.55 + 2
    assert short_run_marginal_cost(CCGT, GAS, 75.0) == pytest.approx(50/0.55 + 2)


def test_coal_srmc_known_value():
    # (12 + 0.34*75)/0.40 + 3 = 37.5/0.40 + 3
    assert short_run_marginal_cost(COAL_U, COAL, 75.0) == pytest.approx(37.5/0.40 + 3)


def test_nuclear_srmc_around_seven():
    # (2 + 0)/0.33 + 1 ~ 7.06
    assert short_run_marginal_cost(NUKE, URANIUM, 75.0) == pytest.approx(2/0.33 + 1)


def test_renewable_srmc_is_zero():
    assert short_run_marginal_cost(SOLAR, NONE, 75.0) == 0.0


def test_higher_co2_raises_thermal_srmc():
    assert short_run_marginal_cost(COAL_U, COAL, 100) > short_run_marginal_cost(COAL_U, COAL, 10)


def test_co2_does_not_affect_carbon_free_unit():
    assert short_run_marginal_cost(NUKE, URANIUM, 0) == short_run_marginal_cost(NUKE, URANIUM, 150)


def test_wrong_fuel_raises():
    with pytest.raises(MarginalCostError):
        short_run_marginal_cost(CCGT, COAL, 75.0)


def test_negative_co2_raises():
    with pytest.raises(MarginalCostError):
        short_run_marginal_cost(CCGT, GAS, -1.0)


def test_fuel_switching_equalises_srmc():
    p = fuel_switching_co2_price(CCGT, GAS, COAL_U, COAL)
    assert p is not None
    assert 60 < p < 75    # ~67 EUR/t for these inputs
    assert short_run_marginal_cost(CCGT, GAS, p) == pytest.approx(
        short_run_marginal_cost(COAL_U, COAL, p))


def test_fuel_switching_none_when_parallel():
    # two gas units share the fuel -> identical carbon intensity differences
    # only via efficiency; pick efficiencies that make slope zero is impossible
    # for same fuel unless equal, so use equal efficiency to force parallel.
    a = Unit("a", "gas_ccgt", 0.50, 1.0, "natural_gas", "firm")
    b = Unit("b", "gas_ocgt", 0.50, 5.0, "natural_gas", "firm")
    assert fuel_switching_co2_price(a, GAS, b, GAS) is None

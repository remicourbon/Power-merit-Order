"""Tests for core.dispatch. SRMCs are chosen to be clean round numbers."""

import pytest

from core.data_models import Fuel, Unit
from core.dispatch import DispatchError, clear_market, merit_order

# A tiny fleet with SRMCs 0 (wind), 20 (nuclear-like), 80 (peaker).
FUELS = {
    "free": Fuel("free", 0.0, 0.0),
    "mid_fuel": Fuel("mid_fuel", 20.0, 0.0),
    "peak_fuel": Fuel("peak_fuel", 80.0, 0.0),
}
UNITS = {
    "wind": Unit("wind", "wind", 1.0, 0.0, "free", "wind"),
    "mid": Unit("mid", "nuclear", 1.0, 0.0, "mid_fuel", "firm"),
    "peak": Unit("peak", "gas_ocgt", 1.0, 0.0, "peak_fuel", "firm"),
}
AVAIL = {"wind": 100.0, "mid": 100.0, "peak": 100.0}


def test_merit_order_sorted():
    triples = merit_order(UNITS, FUELS, 0.0)
    assert [t[0] for t in triples] == ["wind", "mid", "peak"]
    assert [t[2] for t in triples] == [0.0, 20.0, 80.0]


def test_marginal_unit_sets_the_price():
    # demand 150: wind (100) full, mid (50) marginal -> price 20
    res = clear_market(UNITS, FUELS, 0.0, 150.0, AVAIL)
    assert res.marginal_unit == "mid"
    assert res.clearing_price == pytest.approx(20.0)
    assert res.dispatch["wind"] == pytest.approx(100.0)
    assert res.dispatch["mid"] == pytest.approx(50.0)


def test_inframarginal_rent():
    # price 20; wind earns (20-0)*100 = 2000; mid earns 0
    res = clear_market(UNITS, FUELS, 0.0, 150.0, AVAIL)
    assert res.inframarginal_rent == pytest.approx(2000.0)


def test_peaker_sets_price_at_high_demand():
    res = clear_market(UNITS, FUELS, 0.0, 250.0, AVAIL)
    assert res.marginal_unit == "peak"
    assert res.clearing_price == pytest.approx(80.0)
    assert res.unserved_mw == 0.0


def test_scarcity_triggers_value_of_lost_load():
    res = clear_market(UNITS, FUELS, 0.0, 400.0, AVAIL, value_of_lost_load=3000.0)
    assert res.marginal_unit is None
    assert res.clearing_price == 3000.0
    assert res.unserved_mw == pytest.approx(100.0)


def test_low_renewable_availability_pushes_up_the_stack():
    # wind only 30 MW this hour -> demand 50 spills onto mid
    res = clear_market(UNITS, FUELS, 0.0, 50.0, {"wind": 30.0, "mid": 100.0, "peak": 100.0})
    assert res.marginal_unit == "mid"
    assert res.dispatch["wind"] == pytest.approx(30.0)
    assert res.dispatch["mid"] == pytest.approx(20.0)


def test_unknown_technology_in_availability_raises():
    with pytest.raises(DispatchError):
        clear_market(UNITS, FUELS, 0.0, 50.0, {"hydro": 10.0})

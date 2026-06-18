"""Tests for core.battery.

Pins the economic behaviour: buy low / sell high, no simultaneous
charge+discharge, round-trip loss erodes profit, the break-even spread gates
trading, cyclicity, bounds, and the exported state-of-charge duals.
"""

import math

import pytest

from core.battery import OptimisationError, optimise_battery


def test_charges_low_discharges_high():
    # two cheap hours then two expensive; lossless, cyclic
    r = optimise_battery([10, 10, 50, 50], power_mw=10, energy_mwh=10,
                         round_trip_pct=100.0)
    assert r.optimal
    assert r.profit_eur == pytest.approx(400.0, rel=1e-4)  # (50-10)*10 MWh
    assert r.charge_mw[0] > 0 and r.discharge_mw[0] == 0
    assert r.discharge_mw[2] > 0 and r.charge_mw[2] == 0


def test_no_simultaneous_charge_and_discharge():
    r = optimise_battery([5, 8, 40, 12, 60, 7, 55, 9], 10, 20, round_trip_pct=90.0)
    for c, d in zip(r.charge_mw, r.discharge_mw):
        assert c < 1e-6 or d < 1e-6


def test_round_trip_loss_reduces_profit():
    lossless = optimise_battery([10, 10, 50, 50], 10, 10, round_trip_pct=100.0)
    lossy = optimise_battery([10, 10, 50, 50], 10, 10, round_trip_pct=81.0)
    assert lossy.profit_eur < lossless.profit_eur


def test_no_trade_when_spread_below_breakeven():
    # round-trip 81% -> break-even ratio 1/0.81 = 1.235; 10->11 is too small
    r = optimise_battery([10, 10, 11, 11], 10, 10, round_trip_pct=81.0)
    assert r.profit_eur == pytest.approx(0.0, abs=1e-6)
    assert r.breakeven_spread_ratio == pytest.approx(1/0.81)


def test_cyclic_returns_to_initial_soc():
    r = optimise_battery([5, 40, 8, 55, 7], 10, 20, soc_init=0.0, cyclic=True)
    assert r.soc_mwh[-1] == pytest.approx(0.0, abs=1e-6)


def test_soc_stays_within_bounds():
    r = optimise_battery([5, 6, 50, 4, 60, 3, 55], 10, 15, round_trip_pct=90.0)
    for s in r.soc_mwh:
        assert -1e-6 <= s <= 15 + 1e-6


def test_energy_value_duals_are_exported():
    r = optimise_battery([10, 10, 50, 50], 10, 10, round_trip_pct=100.0)
    assert len(r.energy_value_eur_mwh) == 4
    # stored energy is worth something while the battery is cycling
    assert any(abs(v) > 1e-6 for v in r.energy_value_eur_mwh)
    assert "cyclic" in r.shadow_prices


def test_zero_power_raises():
    with pytest.raises(OptimisationError):
        optimise_battery([1, 2, 3], 0, 10)


def test_empty_prices_raises():
    with pytest.raises(OptimisationError):
        optimise_battery([], 10, 10)

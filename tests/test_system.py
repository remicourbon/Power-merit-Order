"""Tests for core.system, the orchestrator."""

import dataclasses
from pathlib import Path

import pytest

from core.data_models import Battery, System, load_dataset
from core.system import (Scenario, SystemError, effective_fuels,
                        evaluate_system)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def ds():
    return load_dataset(DATA_DIR)


def test_evaluate_system_runs_end_to_end(ds):
    r = evaluate_system(ds, "continental", Scenario(), battery_key="grid_4h")
    assert len(r.price_curve) == 24
    assert r.battery.optimal
    assert r.min_price <= r.avg_price <= r.max_price


def test_effective_fuels_applies_overrides(ds):
    sc = Scenario(co2_price=75.0, fuel_price_overrides={"natural_gas": 60.0})
    fuels = effective_fuels(ds, sc)
    assert fuels["natural_gas"].price_eur_mwh_th == 60.0
    # emission factor untouched
    assert fuels["natural_gas"].emission_factor_tco2_mwh_th == \
        ds.fuels["natural_gas"].emission_factor_tco2_mwh_th
    # other fuels untouched
    assert fuels["hard_coal"].price_eur_mwh_th == ds.fuels["hard_coal"].price_eur_mwh_th


def test_higher_gas_price_raises_gas_set_prices(ds):
    cheap = evaluate_system(ds, "gas_and_wind",
                            Scenario(fuel_price_overrides={"natural_gas": 20.0}),
                            battery_key="grid_4h")
    dear = evaluate_system(ds, "gas_and_wind",
                           Scenario(fuel_price_overrides={"natural_gas": 60.0}),
                           battery_key="grid_4h")
    assert dear.avg_price > cheap.avg_price


def test_system_override_runs_a_custom_fleet(ds):
    base = ds.systems["sandbox"]
    custom = dataclasses.replace(base, capacities_gw={**base.capacities_gw, "solar": 30.0})
    r = evaluate_system(ds, "sandbox", Scenario(), battery_key="grid_4h",
                        system_override=custom)
    assert r.battery.optimal


def test_battery_override_changes_result(ds):
    big = Battery("custom", power_mw=400, energy_mwh=1600, round_trip_pct=90.0)
    small = ds.batteries["grid_1h"]
    r_big = evaluate_system(ds, "iberian", Scenario(), battery_override=big)
    r_small = evaluate_system(ds, "iberian", Scenario(), battery_override=small)
    # more energy + power should not earn less on the same price curve
    assert r_big.battery.profit_eur >= r_small.battery.profit_eur


def test_unknown_system_raises(ds):
    with pytest.raises(SystemError):
        evaluate_system(ds, "atlantis", Scenario(), battery_key="grid_4h")


def test_unknown_battery_raises(ds):
    with pytest.raises(SystemError):
        evaluate_system(ds, "continental", Scenario(), battery_key="nope")

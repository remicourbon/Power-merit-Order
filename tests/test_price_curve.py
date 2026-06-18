"""Tests for core.price_curve, exercised on the real stylized dataset."""

from pathlib import Path

import pytest

from core.data_models import load_dataset
from core.price_curve import compute_price_curve

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def ds():
    return load_dataset(DATA_DIR)


def _curve(ds, system_key, co2=75.0):
    s = ds.systems[system_key]
    units = {t: ds.units[t] for t in s.capacities_gw}
    return compute_price_curve(
        s, units, ds.fuels, co2,
        ds.profile(s.demand_profile, "demand"),
        ds.profile(s.solar_profile, "solar"),
        ds.profile(s.wind_profile, "wind"))


def test_curve_has_24_hours(ds):
    curve = _curve(ds, "continental")
    assert len(curve) == 24
    assert [h.hour for h in curve] == list(range(24))


def test_midday_cheaper_than_evening_peak(ds):
    # solar pushes the midday marginal unit down the stack (the duck curve)
    curve = _curve(ds, "continental")
    assert curve[12].price <= curve[18].price


def test_residual_below_demand_when_renewables_run(ds):
    curve = _curve(ds, "iberian")
    assert curve[12].renewable_mw > 0
    assert curve[12].residual_demand_mw < curve[12].demand_mw


def test_zero_co2_does_not_raise_prices(ds):
    high = sum(h.price for h in _curve(ds, "continental", co2=150.0))
    low = sum(h.price for h in _curve(ds, "continental", co2=0.0))
    assert low <= high


def test_nuclear_heavy_system_is_cheaper_than_thermal_led(ds):
    nuke = sum(h.price for h in _curve(ds, "continental")) / 24
    gas = sum(h.price for h in _curve(ds, "gas_and_wind")) / 24
    assert nuke < gas

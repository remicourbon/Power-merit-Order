"""Tests for core.data_models.

Every guarantee the loader makes is pinned here: the real data loads, each
validation rule rejects a broken entry with a file-tagged message, dataclasses
are immutable, and cross-file referential integrity is enforced.
"""

from pathlib import Path

import pytest

from core.data_models import (Battery, DataValidationError, Fuel, Profile,
                              System, Unit, load_dataset)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def ds():
    return load_dataset(DATA_DIR)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_real_dataset_loads(ds):
    assert set(ds.systems) == {"continental", "iberian", "gas_and_wind", "sandbox"}
    assert "natural_gas" in ds.fuels
    assert ds.units["gas_ccgt"].fuel == "natural_gas"


def test_battery_duration_property(ds):
    assert ds.batteries["grid_4h"].duration_h == pytest.approx(4.0)
    assert ds.batteries["grid_1h"].duration_h == pytest.approx(1.0)


def test_dataset_is_immutable(ds):
    with pytest.raises(Exception):
        ds.systems["continental"] = None  # mapping proxy is read-only


def test_profile_lookup_checks_kind(ds):
    assert ds.profile("double_peak", "demand").kind == "demand"
    with pytest.raises(DataValidationError):
        ds.profile("double_peak", "solar")     # wrong kind
    with pytest.raises(DataValidationError):
        ds.profile("does_not_exist", "demand")


# --------------------------------------------------------------------------
# Field-level validation
# --------------------------------------------------------------------------

def test_unit_rejects_bad_efficiency():
    with pytest.raises(DataValidationError) as e:
        Unit("x", "gas_ccgt", 1.5, 2.0, "natural_gas", "firm")
    assert "efficiency" in str(e.value)


def test_unit_rejects_unknown_availability():
    with pytest.raises(DataValidationError):
        Unit("x", "gas_ccgt", 0.5, 2.0, "natural_gas", "sometimes")


def test_fuel_rejects_negative_emission_factor():
    with pytest.raises(DataValidationError):
        Fuel("bad", price_eur_mwh_th=10.0, emission_factor_tco2_mwh_th=-0.1)


def test_profile_rejects_wrong_length():
    with pytest.raises(DataValidationError) as e:
        Profile("p", "demand", tuple([0.5] * 23))
    assert "expected 24" in str(e.value)


def test_profile_rejects_out_of_range_values():
    with pytest.raises(DataValidationError):
        Profile("p", "solar", tuple([1.2] + [0.0] * 23))


def test_system_rejects_unknown_technology():
    with pytest.raises(DataValidationError):
        System("s", "S", "double_peak", "solar_temperate", "wind_steady",
               60.0, {"fusion": 10.0})


def test_battery_rejects_zero_power():
    with pytest.raises(DataValidationError):
        Battery("b", power_mw=0.0, energy_mwh=100.0, round_trip_pct=85.0)


# --------------------------------------------------------------------------
# Referential integrity
# --------------------------------------------------------------------------

def test_unknown_fuel_reference_is_caught(tmp_path):
    _write_minimal_dataset(tmp_path)
    (tmp_path / "units.yaml").write_text(
        "gas_ccgt:\n  technology: gas_ccgt\n  efficiency: 0.55\n"
        "  variable_om: 2.0\n  fuel: mystery_fuel\n  availability: firm\n")
    with pytest.raises(DataValidationError) as e:
        load_dataset(tmp_path)
    assert "mystery_fuel" in str(e.value)


def test_profile_kind_mismatch_in_system_is_caught(tmp_path):
    _write_minimal_dataset(tmp_path)
    # point the demand_profile slot at a solar-kind profile
    (tmp_path / "systems.yaml").write_text(
        "s:\n  name: S\n  demand_profile: a_solar\n  solar_profile: a_solar\n"
        "  wind_profile: a_wind\n  peak_demand_gw: 10\n"
        "  capacities_gw:\n    gas_ccgt: 10\n")
    with pytest.raises(DataValidationError) as e:
        load_dataset(tmp_path)
    assert "expected 'demand'" in str(e.value)


def _write_minimal_dataset(d: Path) -> None:
    """A tiny but valid dataset; individual tests then corrupt one file."""
    (d / "fuels.yaml").write_text(
        "natural_gas:\n  price_eur_mwh_th: 35\n  emission_factor_tco2_mwh_th: 0.2\n"
        "none:\n  price_eur_mwh_th: 0\n  emission_factor_tco2_mwh_th: 0\n")
    (d / "units.yaml").write_text(
        "gas_ccgt:\n  technology: gas_ccgt\n  efficiency: 0.55\n"
        "  variable_om: 2.0\n  fuel: natural_gas\n  availability: firm\n"
        "solar:\n  technology: solar\n  efficiency: 1.0\n  variable_om: 0.0\n"
        "  fuel: none\n  availability: solar\n"
        "wind:\n  technology: wind\n  efficiency: 1.0\n  variable_om: 0.0\n"
        "  fuel: none\n  availability: wind\n")
    vals = "[" + ", ".join(["0.5"] * 24) + "]"
    (d / "profiles.yaml").write_text(
        f"a_demand:\n  kind: demand\n  values: {vals}\n"
        f"a_solar:\n  kind: solar\n  values: {vals}\n"
        f"a_wind:\n  kind: wind\n  values: {vals}\n")
    (d / "systems.yaml").write_text(
        "s:\n  name: S\n  demand_profile: a_demand\n  solar_profile: a_solar\n"
        "  wind_profile: a_wind\n  peak_demand_gw: 10\n"
        "  capacities_gw:\n    gas_ccgt: 10\n")
    (d / "batteries.yaml").write_text(
        "grid_4h:\n  power_mw: 200\n  energy_mwh: 800\n  round_trip_pct: 85\n")

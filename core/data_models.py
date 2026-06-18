"""Data models and YAML loaders for power-merit-order.

All static data lives in YAML files under data/. This module loads them into
frozen dataclasses and validates aggressively at load time: a broken YAML file
must be diagnosable in seconds (file + key in every error message), never at
solve time.

Design choices (see README):
- Emission factors live on the FUEL (a chemical property); efficiency and
  variable O&M live on the UNIT (engineering properties). The SRMC formula in
  marginal_cost.py combines them, so each number sits where it physically belongs.
- A `System` is a stylized fleet: technology keys -> installed GW, a peak demand
  and three profile references (demand, solar, wind). Capacities reference the
  units library; profiles reference the profiles library; everything is checked
  for referential integrity at load time.
- Profiles are normalised 24-hour shapes. Demand profiles are fractions of peak;
  solar/wind profiles are capacity factors. The `kind` field lets a system slot
  each profile into the right role and lets validation reject a mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

HOURS_PER_DAY = 24
TECHNOLOGIES = ("nuclear", "coal", "gas_ccgt", "gas_ocgt", "solar", "wind")
AVAILABILITIES = ("firm", "solar", "wind")
PROFILE_KINDS = ("demand", "solar", "wind")


class DataValidationError(Exception):
    """Raised when a data file fails validation."""


def _fail(file: str, key: str, msg: str) -> None:
    raise DataValidationError(f"[{file}] '{key}': {msg}")


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Fuel:
    key: str
    price_eur_mwh_th: float        # variable fuel cost, EUR / MWh thermal
    emission_factor_tco2_mwh_th: float   # tCO2 per MWh thermal burned

    def __post_init__(self):
        f = "fuels.yaml"
        if self.price_eur_mwh_th < 0:
            _fail(f, self.key, "price_eur_mwh_th must be >= 0")
        if self.emission_factor_tco2_mwh_th < 0:
            _fail(f, self.key, "emission_factor_tco2_mwh_th must be >= 0")


@dataclass(frozen=True)
class Unit:
    key: str
    technology: str
    efficiency: float              # electrical eta, MWh_e / MWh_th
    variable_om: float             # EUR / MWh_e
    fuel: str                      # key into fuels.yaml
    availability: str              # 'firm' | 'solar' | 'wind'

    def __post_init__(self):
        f = "units.yaml"
        if not 0 < self.efficiency <= 1.0:
            _fail(f, self.key, f"efficiency={self.efficiency} must be in (0, 1]")
        if self.variable_om < 0:
            _fail(f, self.key, "variable_om must be >= 0")
        if self.availability not in AVAILABILITIES:
            _fail(f, self.key,
                  f"availability='{self.availability}' not in {AVAILABILITIES}")


@dataclass(frozen=True)
class Profile:
    key: str
    kind: str                      # 'demand' | 'solar' | 'wind'
    values: tuple[float, ...]      # 24 normalised values in [0, 1]

    def __post_init__(self):
        f = "profiles.yaml"
        if self.kind not in PROFILE_KINDS:
            _fail(f, self.key, f"kind='{self.kind}' not in {PROFILE_KINDS}")
        if len(self.values) != HOURS_PER_DAY:
            _fail(f, self.key,
                  f"has {len(self.values)} values, expected {HOURS_PER_DAY}")
        bad = [v for v in self.values if not 0.0 <= v <= 1.0]
        if bad:
            _fail(f, self.key, f"values must be in [0, 1]; offending: {bad[:3]}")
        object.__setattr__(self, "values", tuple(float(v) for v in self.values))


@dataclass(frozen=True)
class System:
    key: str
    name: str
    demand_profile: str
    solar_profile: str
    wind_profile: str
    peak_demand_gw: float
    capacities_gw: Mapping[str, float]
    sandbox: bool = False

    def __post_init__(self):
        f = "systems.yaml"
        if self.peak_demand_gw <= 0:
            _fail(f, self.key, "peak_demand_gw must be > 0")
        if not self.capacities_gw:
            _fail(f, self.key, "capacities_gw must be a non-empty mapping")
        for tech, gw in self.capacities_gw.items():
            if tech not in TECHNOLOGIES:
                _fail(f, self.key, f"unknown technology '{tech}' in capacities_gw")
            if gw < 0:
                _fail(f, self.key, f"capacity for '{tech}' must be >= 0")
        object.__setattr__(self, "capacities_gw",
                           MappingProxyType(dict(self.capacities_gw)))


@dataclass(frozen=True)
class Battery:
    key: str
    power_mw: float
    energy_mwh: float
    round_trip_pct: float

    def __post_init__(self):
        f = "batteries.yaml"
        if self.power_mw <= 0:
            _fail(f, self.key, "power_mw must be > 0")
        if self.energy_mwh <= 0:
            _fail(f, self.key, "energy_mwh must be > 0")
        if not 0 < self.round_trip_pct <= 100:
            _fail(f, self.key,
                  f"round_trip_pct={self.round_trip_pct} outside (0, 100]")

    @property
    def duration_h(self) -> float:
        return self.energy_mwh / self.power_mw


# --------------------------------------------------------------------------
# Dataset container with referential integrity
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Dataset:
    fuels: Mapping[str, Fuel]
    units: Mapping[str, Unit]
    profiles: Mapping[str, Profile]
    systems: Mapping[str, System]
    batteries: Mapping[str, Battery]

    def profile(self, key: str, expected_kind: str) -> Profile:
        if key not in self.profiles:
            raise DataValidationError(
                f"[profiles.yaml] no profile '{key}'")
        p = self.profiles[key]
        if p.kind != expected_kind:
            raise DataValidationError(
                f"[profiles.yaml] '{key}': kind '{p.kind}', "
                f"expected '{expected_kind}'")
        return p

    def system_units(self, system_key: str) -> dict[str, Unit]:
        """The technologies present in a system, as {technology: Unit}."""
        system = self.systems[system_key]
        return {tech: self.units[tech] for tech in system.capacities_gw}

    def validate_referential_integrity(self) -> None:
        """Cross-file checks. Called by load_dataset; idempotent."""
        for u in self.units.values():
            if u.fuel not in self.fuels:
                _fail("units.yaml", u.key,
                      f"fuel='{u.fuel}' not found in fuels.yaml")

        for s in self.systems.values():
            for tech in s.capacities_gw:
                if tech not in self.units:
                    _fail("systems.yaml", s.key,
                          f"technology '{tech}' not found in units.yaml")
            for role, pkey in (("demand", s.demand_profile),
                               ("solar", s.solar_profile),
                               ("wind", s.wind_profile)):
                if pkey not in self.profiles:
                    _fail("systems.yaml", s.key,
                          f"{role}_profile '{pkey}' not found in profiles.yaml")
                if self.profiles[pkey].kind != role:
                    _fail("systems.yaml", s.key,
                          f"{role}_profile '{pkey}' has kind "
                          f"'{self.profiles[pkey].kind}', expected '{role}'")
            # A system with a solar/wind capacity but no such unit available
            # would silently drop that capacity; catch it.
            for tech in ("solar", "wind"):
                if s.capacities_gw.get(tech, 0) > 0 and \
                        self.units.get(tech) is None:
                    _fail("systems.yaml", s.key,
                          f"has {tech} capacity but no '{tech}' unit")


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    try:
        with open(path) as fh:
            content = yaml.safe_load(fh)
    except FileNotFoundError:
        raise DataValidationError(f"[{path.name}] file not found at {path}")
    except yaml.YAMLError as exc:
        raise DataValidationError(f"[{path.name}] invalid YAML: {exc}")
    if not isinstance(content, dict) or not content:
        raise DataValidationError(f"[{path.name}] must be a non-empty mapping")
    return content


def _build(cls, file: str, key: str, payload: dict, **extra):
    """Instantiate a dataclass from a YAML payload with friendly errors."""
    if not isinstance(payload, dict):
        _fail(file, key, "entry must be a mapping")
    try:
        return cls(key=key, **payload, **extra)
    except TypeError as exc:
        _fail(file, key, f"bad or missing field ({exc})")


def load_fuels(path: Path) -> dict[str, Fuel]:
    return {k: _build(Fuel, path.name, k, v) for k, v in _read_yaml(path).items()}


def load_units(path: Path) -> dict[str, Unit]:
    return {k: _build(Unit, path.name, k, v) for k, v in _read_yaml(path).items()}


def load_profiles(path: Path) -> dict[str, Profile]:
    out = {}
    for k, v in _read_yaml(path).items():
        if isinstance(v, dict) and isinstance(v.get("values"), list):
            v = {**v, "values": tuple(v["values"])}
        out[k] = _build(Profile, path.name, k, v)
    return out


def load_systems(path: Path) -> dict[str, System]:
    return {k: _build(System, path.name, k, v)
            for k, v in _read_yaml(path).items()}


def load_batteries(path: Path) -> dict[str, Battery]:
    return {k: _build(Battery, path.name, k, v)
            for k, v in _read_yaml(path).items()}


def load_dataset(data_dir: str | Path) -> Dataset:
    """Load and fully validate the whole data directory. The single entry
    point the rest of the codebase should use."""
    d = Path(data_dir)
    ds = Dataset(
        fuels=MappingProxyType(load_fuels(d / "fuels.yaml")),
        units=MappingProxyType(load_units(d / "units.yaml")),
        profiles=MappingProxyType(load_profiles(d / "profiles.yaml")),
        systems=MappingProxyType(load_systems(d / "systems.yaml")),
        batteries=MappingProxyType(load_batteries(d / "batteries.yaml")),
    )
    ds.validate_referential_integrity()
    return ds

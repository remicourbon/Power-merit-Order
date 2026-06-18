"""From normalised profiles to physical MW.

This module owns the translation of the dimensionless 24-hour shapes
(profiles.yaml) into the MW quantities the dispatch needs, given a system's peak
demand and installed capacities. It is the only place that knows demand profiles
are fractions of peak while solar/wind profiles are capacity factors.

Pure functions over the frozen dataclasses; no dispatch, no LP, no UI.
"""

from __future__ import annotations

from typing import Mapping

from core.data_models import HOURS_PER_DAY, Profile, System, Unit

GW_TO_MW = 1000.0


class ProfileError(ValueError):
    """Raised on inconsistent profile inputs."""


def demand_mw_series(system: System, demand_profile: Profile) -> list[float]:
    """Hourly demand in MW: peak demand scaled by the demand shape."""
    if demand_profile.kind != "demand":
        raise ProfileError(f"profile '{demand_profile.key}' is not a demand profile")
    peak_mw = system.peak_demand_gw * GW_TO_MW
    return [peak_mw * frac for frac in demand_profile.values]


def available_mw(system: System, units: Mapping[str, Unit],
                 solar_cf: float, wind_cf: float) -> dict[str, float]:
    """Usable capacity of each technology for one hour, in MW.

    Firm units sit at nameplate; solar and wind are scaled by their
    capacity factor for the hour. Technologies the system does not own are
    simply absent from the result.
    """
    out: dict[str, float] = {}
    for tech, gw in system.capacities_gw.items():
        cap_mw = gw * GW_TO_MW
        avail = units[tech].availability
        if avail == "solar":
            out[tech] = cap_mw * solar_cf
        elif avail == "wind":
            out[tech] = cap_mw * wind_cf
        else:  # firm
            out[tech] = cap_mw
    return out


def renewable_mw(dispatch: Mapping[str, float], units: Mapping[str, Unit]) -> float:
    """Total dispatched solar + wind, in MW (for residual-demand reporting)."""
    return sum(mw for tech, mw in dispatch.items()
               if units[tech].availability in ("solar", "wind"))

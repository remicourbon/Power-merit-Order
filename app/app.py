"""Power merit-order & battery arbitrage -- Streamlit app.

No business logic lives here: every number comes from core/. The sidebar builds
a System (or a sandbox override), a Scenario and a Battery; core.system.evaluate
turns them into a price curve and a battery schedule, and the three pages render
the result.

Run from the project root:  python run.py   (or streamlit run app/app.py)
"""

from __future__ import annotations

import dataclasses
import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_models import Battery, load_dataset                       # noqa: E402
from core.dispatch import clear_market, merit_order                      # noqa: E402
from core.marginal_cost import fuel_switching_co2_price                  # noqa: E402
from core.profiles import available_mw                                   # noqa: E402
from core.system import Scenario, evaluate_system                        # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

TECH_LABEL = {"nuclear": "Nuclear", "coal": "Coal", "gas_ccgt": "Gas CCGT",
              "gas_ocgt": "Gas OCGT", "solar": "Solar", "wind": "Wind"}
TECH_COLOR = {"nuclear": "#7F77DD", "coal": "#888780", "gas_ccgt": "#378ADD",
              "gas_ocgt": "#D85A30", "solar": "#EF9F27", "wind": "#1D9E75"}
TECH_ORDER = ["nuclear", "solar", "wind", "coal", "gas_ccgt", "gas_ocgt"]

st.set_page_config(page_title="Power Merit Order & Battery", page_icon="⚡", layout="wide")


@st.cache_resource(show_spinner=False)
def dataset():
    return load_dataset(DATA_DIR)


ds = dataset()

# ==========================================================================
# Sidebar: system, scenario, battery
# ==========================================================================
st.sidebar.title("Power Merit Order")
page = st.sidebar.radio("Page", ["Price curve", "Battery behaviour", "Reference data"])

st.sidebar.header("System")
system_key = st.sidebar.selectbox(
    "System", list(ds.systems),
    format_func=lambda k: ds.systems[k].name)
system = ds.systems[system_key]

system_override = None
if system.sandbox:
    st.sidebar.caption("Sandbox: toggle technologies, set capacities and peak "
                       "demand. The merit order rebuilds live.")
    peak = float(st.sidebar.slider("Peak demand (GW)", 10, 90,
                                   int(system.peak_demand_gw)))
    caps = {}
    for tech in TECH_ORDER:
        present_default = tech in system.capacities_gw
        on = st.sidebar.checkbox(TECH_LABEL[tech], value=present_default,
                                 key=f"on_{tech}")
        if on:
            default = float(system.capacities_gw.get(tech, 10.0))
            caps[tech] = float(st.sidebar.slider(
                f"{TECH_LABEL[tech]} capacity (GW)", 0, 80, int(default),
                key=f"cap_{tech}"))
    if not caps:
        st.sidebar.error("Select at least one technology.")
        st.stop()
    system_override = dataclasses.replace(system, peak_demand_gw=peak,
                                          capacities_gw=caps)
else:
    caps_txt = ", ".join(f"{TECH_LABEL[t]} {gw:.0f}"
                         for t, gw in system.capacities_gw.items())
    st.sidebar.caption(f"Peak {system.peak_demand_gw:.0f} GW · {caps_txt}")

st.sidebar.header("Fuel & carbon")
gas = float(st.sidebar.slider("Gas price (TTF, EUR/MWh_th)", 5, 80,
                              int(ds.fuels["natural_gas"].price_eur_mwh_th)))
coal = float(st.sidebar.slider("Coal price (EUR/MWh_th)", 4, 40,
                               int(ds.fuels["hard_coal"].price_eur_mwh_th)))
co2 = float(st.sidebar.slider("CO2 price (EUA, EUR/t)", 0, 150, 75))
scenario = Scenario(co2_price=co2,
                    fuel_price_overrides={"natural_gas": gas, "hard_coal": coal})

st.sidebar.header("Battery")
battery_key = st.sidebar.selectbox(
    "Class", list(ds.batteries),
    format_func=lambda k: f"{k} ({ds.batteries[k].duration_h:.0f} h)")
base_bat = ds.batteries[battery_key]
customise = st.sidebar.checkbox("Customise battery", value=False)
battery_override = None
if customise:
    bp = float(st.sidebar.slider("Power (MW)", 10, 1000, int(base_bat.power_mw)))
    be = float(st.sidebar.slider("Energy (MWh)", 50, 4000, int(base_bat.energy_mwh)))
    rt = float(st.sidebar.slider("Round-trip efficiency (%)", 50, 100,
                                 int(base_bat.round_trip_pct)))
    battery_override = Battery("custom", bp, be, rt)

result = evaluate_system(
    ds, system_key, scenario,
    battery_key=None if battery_override else battery_key,
    battery_override=battery_override, system_override=system_override)
curve = result.price_curve
prices = result.prices
active_system = system_override or system


# ==========================================================================
# Chart builders
# ==========================================================================
def price_residual_chart() -> go.Figure:
    hours = [h.hour for h in curve]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hours, y=prices, name="Price (EUR/MWh)",
                             mode="lines+markers", line=dict(color="#378ADD", width=3)))
    fig.add_trace(go.Scatter(x=hours, y=[h.residual_demand_mw/1000 for h in curve],
                             name="Residual demand (GW)", mode="lines", yaxis="y2",
                             line=dict(color="#D85A30", width=2, dash="dot")))
    fig.add_trace(go.Scatter(x=hours, y=[h.demand_mw/1000 for h in curve],
                             name="Demand (GW)", mode="lines", yaxis="y2",
                             line=dict(color="#888780", width=1, dash="dash")))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(title="EUR/MWh"),
                      yaxis2=dict(title="GW", overlaying="y", side="right", showgrid=False),
                      legend=dict(orientation="h", y=1.12), hovermode="x unified")
    return fig


def merit_order_chart(hour: int):
    s = active_system
    units = {t: ds.units[t] for t in s.capacities_gw}
    fuels = result.fuels
    solar_cf = ds.profile(s.solar_profile, "solar").values[hour]
    wind_cf = ds.profile(s.wind_profile, "wind").values[hour]
    avail = available_mw(s, units, solar_cf, wind_cf)
    demand_mw = curve[hour].demand_mw
    res = clear_market(units, fuels, co2, demand_mw, avail)

    fig = go.Figure()
    x = 0.0
    for tech, _u, srmc in merit_order(units, fuels, co2):
        cap = avail.get(tech, 0.0) / 1000.0
        if cap <= 0:
            continue
        fig.add_shape(type="rect", x0=x, x1=x + cap, y0=0, y1=srmc,
                      fillcolor=TECH_COLOR[tech], line=dict(color="white", width=1),
                      opacity=0.9)
        fig.add_annotation(x=x + cap / 2, y=srmc, text=TECH_LABEL[tech],
                           showarrow=False, yshift=8, font=dict(size=10))
        x += cap
    fig.add_vline(x=demand_mw/1000, line=dict(color="#A32D2D", width=2, dash="dash"),
                  annotation_text=f"demand {demand_mw/1000:.1f} GW", annotation_position="top")
    fig.add_hline(y=res.clearing_price, line=dict(color="#A32D2D", width=1, dash="dot"),
                  annotation_text=f"price {res.clearing_price:.1f}", annotation_position="right")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10),
                      xaxis_title="Cumulative available capacity (GW)",
                      yaxis_title="SRMC (EUR/MWh)", showlegend=False)
    return fig, res


def battery_schedule_chart() -> go.Figure:
    hours = list(range(len(prices)))
    b = result.battery
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=hours, y=[-c for c in b.charge_mw], name="Charge (MW)",
                         marker_color="#378ADD"), secondary_y=False)
    fig.add_trace(go.Bar(x=hours, y=list(b.discharge_mw), name="Discharge (MW)",
                         marker_color="#1D9E75"), secondary_y=False)
    fig.add_trace(go.Scatter(x=hours, y=prices, name="Price (EUR/MWh)",
                             mode="lines+markers", line=dict(color="#A32D2D", width=3)),
                  secondary_y=True)
    fig.update_layout(height=400, barmode="relative", margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.12), hovermode="x unified")
    fig.update_yaxes(title_text="charge (-) / discharge (+) MW", secondary_y=False)
    fig.update_yaxes(title_text="EUR/MWh", secondary_y=True, showgrid=False)
    return fig


def soc_chart() -> go.Figure:
    hours = list(range(len(prices)))
    b = result.battery
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=hours, y=list(b.soc_mwh), mode="lines", fill="tozeroy",
                             line=dict(color="#7F77DD", width=2), name="SOC (MWh)"),
                  secondary_y=False)
    fig.add_trace(go.Scatter(x=hours, y=list(b.energy_value_eur_mwh), mode="lines",
                             line=dict(color="#EF9F27", width=2, dash="dot"),
                             name="Energy value (EUR/MWh)"), secondary_y=True)
    cap = battery_override.energy_mwh if battery_override else base_bat.energy_mwh
    fig.add_hline(y=cap, line=dict(color="#888780", dash="dash"),
                  annotation_text="capacity")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10),
                      legend=dict(orientation="h", y=1.15), hovermode="x unified")
    fig.update_yaxes(title_text="SOC (MWh)", secondary_y=False)
    fig.update_yaxes(title_text="EUR/MWh", secondary_y=True, showgrid=False)
    return fig


# ==========================================================================
# Pages
# ==========================================================================
if page == "Price curve":
    st.title(f"Price curve — {active_system.name}")
    st.caption("Each hour's price is the SRMC of the last unit called to meet "
               "demand. As solar peaks at midday the marginal unit slides down "
               "the stack and the price drops — the duck curve.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Min price", f"{result.min_price:.0f} €/MWh")
    c2.metric("Max price", f"{result.max_price:.0f} €/MWh")
    c3.metric("Mean", f"{result.avg_price:.0f} €/MWh")
    c4.metric("Intraday spread", f"{result.max_price - result.min_price:.0f} €/MWh")

    st.plotly_chart(price_residual_chart(), use_container_width=True)

    if any(h.unserved_mw > 0 for h in curve):
        st.warning("Some hours are short of capacity (price set to value of lost "
                   "load). Add dispatchable capacity or lower peak demand.")

    st.subheader("Merit-order snapshot")
    hour = st.slider("Hour of day", 0, 23, 18)
    fig, res = merit_order_chart(hour)
    st.plotly_chart(fig, use_container_width=True)
    mu = TECH_LABEL.get(res.marginal_unit, "lost load (scarcity)")
    st.info(f"Hour {hour}: marginal unit **{mu}**, clearing price "
            f"**{res.clearing_price:.1f} €/MWh**, inframarginal rent "
            f"**{res.inframarginal_rent/1000:.0f} k€/h**.")

    units = {t: ds.units[t] for t in active_system.capacities_gw}
    if "gas_ccgt" in units and "coal" in units:
        p = fuel_switching_co2_price(units["gas_ccgt"], result.fuels["natural_gas"],
                                     units["coal"], result.fuels["hard_coal"])
        if p is not None and p > 0:
            cheaper = "gas" if co2 >= p else "coal"
            st.caption(f"Coal↔gas switching CO₂ price ≈ **{p:.0f} €/t**. At the "
                       f"current {co2:.0f} €/t, **{cheaper}** is the cheaper "
                       f"thermal unit — the carbon-driven switching point.")

elif page == "Battery behaviour":
    b = result.battery
    st.title("Battery behaviour")
    st.caption("The battery arbitrages the price curve: charge when prices are "
               "low, discharge when high, within power, energy and round-trip "
               "limits. The schedule is an LP with perfect foresight — a "
               "theoretical upper bound, not a tradeable strategy.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Arbitrage profit", f"{b.profit_eur:,.0f} €/day")
    c2.metric("Equivalent cycles", f"{b.equivalent_cycles:.2f}")
    ratio = (b.avg_sell_price / b.avg_buy_price) if (b.avg_buy_price and b.avg_sell_price) else None
    c3.metric("Achieved price ratio", f"{ratio:.2f}×" if ratio else "—",
              help="Average sell price / average buy price.")
    c4.metric("Break-even ratio", f"{b.breakeven_spread_ratio:.2f}×",
              help="1 / round-trip efficiency: the minimum sell/buy ratio worth cycling for.")

    if not b.optimal:
        st.error(f"Battery LP status: {b.status}.")
    else:
        if ratio is not None and ratio < b.breakeven_spread_ratio:
            st.warning("Achieved ratio is below break-even — round-trip losses eat the spread.")
        st.plotly_chart(battery_schedule_chart(), use_container_width=True)
        st.subheader("State of charge and the value of stored energy")
        st.caption("The dotted line is the shadow price of the storage balance: "
                   "the marginal value of one more MWh in the battery each hour — "
                   "the storage analogue of a refinery's sulfur dual.")
        st.plotly_chart(soc_chart(), use_container_width=True)

        with st.expander("The optimisation, in one screen"):
            st.latex(r"\text{soc}(t) = \text{soc}(t-1) + \eta_c\, c(t)\,\Delta t - \frac{d(t)}{\eta_d}\,\Delta t")
            st.latex(r"\max \sum_t P(t)\,\big(d(t) - c(t)\big)\,\Delta t")
            st.markdown(
                "Bounds: `0 ≤ c,d ≤ power`, `0 ≤ soc ≤ energy`, and `soc(T)=soc(0)` "
                "(cyclic). No binary variable is needed: with round-trip efficiency "
                "below 1, charging and discharging in the same hour is always "
                "wasteful, so the LP optimum never does both — the model stays a pure LP.")

elif page == "Reference data":
    st.title("Reference data")
    st.caption("Everything is stylized, hand-chosen input — not a download from "
               "ENTSO-E. The dispatch and the battery LP are identical whether "
               "the numbers are stylized or real, so the modelling is fully "
               "demonstrated while the assumptions stay explicit and defensible.")

    t_sys, t_units, t_fuels, t_prof, t_bat, t_assume = st.tabs(
        ["Systems", "Units", "Fuels & carbon", "Profiles", "Batteries", "Assumptions"])

    with t_sys:
        st.subheader("Power systems")
        rows = []
        for k, s in ds.systems.items():
            rows.append({"System": s.name, "Peak (GW)": s.peak_demand_gw,
                         "Sandbox": s.sandbox,
                         **{TECH_LABEL[t]: s.capacities_gw.get(t, 0) for t in TECH_ORDER}})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with t_units:
        st.subheader("Generation technologies")
        rows = [{"Technology": TECH_LABEL[k], "Efficiency η": u.efficiency,
                 "Variable O&M (€/MWh)": u.variable_om, "Fuel": u.fuel,
                 "Availability": u.availability} for k, u in ds.units.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.latex(r"\text{SRMC} = \frac{P_\text{fuel} + \text{EF}\times P_{\text{CO}_2}}{\eta} + \text{VOM}")
        st.caption("Fuel cost and carbon cost are added at the thermal level, then "
                   "divided by η to express the result per MWh of electricity.")

    with t_fuels:
        st.subheader("Fuels (default prices, fixed emission factors)")
        rows = [{"Fuel": k, "Price (€/MWh_th)": f.price_eur_mwh_th,
                 "Emission factor (tCO₂/MWh_th)": f.emission_factor_tco2_mwh_th}
                for k, f in ds.fuels.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("Prices are editable via the sidebar; emission factors are "
                   "physical constants and never change.")

    with t_prof:
        st.subheader("Hourly profiles")
        fig = go.Figure()
        for k, p in ds.profiles.items():
            scale = 100 if p.kind != "demand" else 100
            fig.add_trace(go.Scatter(x=list(range(24)), y=[v*scale for v in p.values],
                                     mode="lines", name=f"{k} ({p.kind})"))
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10),
                          yaxis_title="% (fraction of peak / capacity factor)",
                          xaxis_title="Hour", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, use_container_width=True)

    with t_bat:
        st.subheader("Battery classes")
        rows = [{"Class": k, "Power (MW)": b.power_mw, "Energy (MWh)": b.energy_mwh,
                 "Duration (h)": b.duration_h, "Round-trip (%)": b.round_trip_pct}
                for k, b in ds.batteries.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with t_assume:
        st.subheader("Assumptions stated transparently")
        st.markdown(
            "- **Marginal (pay-as-clear) pricing**; infra-marginal units earn rent.\n"
            "- **No inter-temporal generator constraints** (no start-up costs, "
            "ramping, min run times, must-run). A real merit order diverges from "
            "observed prices precisely because of these.\n"
            "- **No interconnection, reservoir-hydro optimisation, or reserve markets.**\n"
            "- **Battery is a price-taker** with **perfect foresight** — its profit "
            "is a theoretical upper bound, not a realisable trading result.\n"
            "- **Renewables bid at zero**; no negative prices are modelled (a natural "
            "v2 extension).\n"
            "- **All inputs are stylized, not real data.** The natural v2 is to swap "
            "in real ENTSO-E capacities, load and renewable generation and calibrate "
            "P(t) against day-ahead prices — showing the gap, not hiding it.")

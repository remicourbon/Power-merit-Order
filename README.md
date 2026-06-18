# power-merit-order

A stylized power-market model. It builds an electricity **price curve** from
first principles using the **merit order** of a generating fleet, then
**arbitrages that curve with a battery** via a linear program.

The question it answers, concretely: *given a fleet, fuel prices and a carbon
price, what does the hourly price look like — and how much can a battery earn
moving energy from the cheap midday hours to the expensive evening peak?* The
price is not downloaded; it is produced unit by unit, so every number can be
decomposed: each hour's price is set by a specific marginal unit, and the
battery's profit comes from a specific buy/sell spread on a curve the model
itself generated.

## Architecture

```
data/        YAML only -- fuels, units, profiles, systems, batteries.
             Validated aggressively at load time.
core/
  data_models.py   frozen dataclasses + loaders + referential integrity
  marginal_cost.py carbon-adjusted SRMC, coal<->gas fuel-switching CO2 price
  dispatch.py      merit-order clearing: marginal unit, price, inframarginal rent
  profiles.py      normalised shapes -> hourly MW (demand, solar, wind)
  price_curve.py   loops the dispatch over the day to produce P(t) -- the engine
  battery.py       the battery arbitrage LP (PuLP) -- full formulation in docstring
  system.py        orchestrator: system + scenario -> price curve -> battery
tests/       51 unit tests; hand-computed reference numbers in comments
app/         Streamlit, 3 pages (Price curve / Battery behaviour / Reference
             data) -- no business logic in the UI
```

Run: `python run.py` (installs deps on first run, fixes sys.path, launches
Streamlit). Or manually:
`pip install -r requirements.txt && python -m pytest && python -m streamlit run app/app.py`

## The two layers

```
fuels, CO2, fleet, profiles
        │
        ▼
  marginal_cost ──► dispatch (merit order) ──► price_curve P(t) ──► battery (LP)
  SRMC per unit     clearing price =           hourly engine        charge/discharge
                    marginal unit's SRMC        output               schedule + P&L
```

`price_curve` is the pivot: the engine output the battery layer consumes.

## Vocabulary (used consistently across all pages)

- **SRMC (short-run marginal cost)** — the variable cost of one extra MWh:
  `(fuel price + emission factor × CO2 price) / efficiency + variable O&M`.
  Fuel and carbon are costed at the thermal level (per MWh_th) then divided by
  efficiency to land per MWh_e. The power-market analogue of a spark spread.
- **Merit order** — units stacked by increasing SRMC. Cumulative capacity on
  the x-axis, SRMC on the y-axis; the demand line picks out the marginal unit.
- **Marginal unit / clearing price** — under pay-as-clear pricing the price
  equals the SRMC of the last unit dispatched. That clearing price is exactly
  the shadow price (dual) of the demand-balance constraint.
- **Inframarginal rent** — `(price − SRMC) × MW` earned by every cheaper unit;
  the power analogue of GPW / netback.
- **Residual demand** — demand net of must-run renewables. As solar peaks the
  residual collapses and the marginal unit slides down the stack (the duck curve).
- **Break-even price ratio** — `1 / round-trip efficiency`: the minimum
  sell/buy ratio that makes a battery cycle worthwhile. Storage's spark spread.
- **Energy value (storage dual)** — the shadow price of the state-of-charge
  balance: the marginal value of one more MWh stored each hour. The storage
  analogue of a refinery's sulfur dual — a price the model discovers, not one
  it is given.

## The merit order in one paragraph

Each unit's SRMC is computed from its fuel (price + carbon intensity) and its
own efficiency and variable O&M. Units are sorted cheapest-first and dispatched
against the hour's demand; the last one called sets the price for everyone
(pay-as-clear). Renewables enter at zero SRMC but only at their hourly capacity
factor, so they push the marginal unit down the stack at midday and let it climb
back to a gas peaker in the evening. Looping the clearing over 24 hours produces
the price curve P(t).

## The battery LP in one paragraph

Decision variables per hour: charge, discharge, and state of charge. Objective:
maximise `Σ price × (discharge − charge)`. Constraints: a state-of-charge
balance with one-way efficiencies `√(round-trip)`, power and energy bounds, and
a cyclic condition (`SOC at end = SOC at start`). No binary variable is needed:
with round-trip efficiency below 1, charging and discharging in the same hour is
always wasteful, so the optimum never does both — the model stays a pure LP, the
same way vessel choice is kept outside the crude LP to avoid a MILP. The duals
of the state-of-charge balances are exported as the marginal value of stored
energy.

## Systems (archetypes + a live sandbox)

Four stylized fleets. Three are fixed (loaded from YAML); **sandbox is a live
fleet** whose technologies, capacities and peak demand are set from the app via
a `system_override` passed to `system.evaluate` — the data files stay untouched.

- **Continental — nuclear-heavy**: cheap, flat baseload; deep midday trough.
- **Iberian — solar-heavy**: prices collapse to the nuclear floor at midday,
  spike in the evening when solar is gone — the steepest duck, biggest arbitrage.
- **Gas & wind — thermal-led**: gas sets the price most hours; a tight spread,
  so storage earns little. The instructive counter-example.
- **Sandbox**: build your own fleet and watch the curve and the battery respond.

## Simplifications (assumed, deliberate)

Stylized inputs are a choice, not a limitation to hide. The dispatch and the
battery LP are identical whether the numbers are stylized or sourced.

- **Marginal (pay-as-clear) pricing.**
- **No inter-temporal generator constraints**: no start-up costs, ramp rates,
  minimum run times, or must-run obligations. A real merit order diverges from
  observed prices largely because of these.
- **No interconnection, reservoir-hydro optimisation, or reserve markets.**
- **Battery is a price-taker** (does not move P(t)) with **perfect foresight**
  (the LP knows the whole curve): its profit is a theoretical **upper bound**,
  not a tradeable result — the storage analogue of "forward curves are hedgeable
  prices, not forecasts".
- **Renewables bid at zero**; no negative prices are modelled.
- **Emission factors are fixed physical constants**; only fuel and CO2 prices
  are editable.
- **All inputs are stylized and illustrative**, flagged as such in the YAML.

The natural v2 is to swap in real ENTSO-E capacities, load and renewable
generation and calibrate P(t) against day-ahead prices — measuring the gap
between a textbook merit order and observed prices, not hiding it. Negative
prices and a large-battery feedback on residual demand are further extensions.

## Sources

- Fuel and technology parameters: order-of-magnitude from public energy
  references; editable defaults, flagged in the YAML.
- Everything is illustrative. Replace with sourced data (ENTSO-E for load,
  capacity and generation; EUA and TTF for carbon and gas) before relying on
  outputs.

This is a decision-support and learning project, not a trading system.

# US Data Center Intelligence Map

A professional, interactive map of data centers across the United States. Each
site carries:

- **Status** — constructed / under construction / planned
- **Construction approval** — approved / permitted / pending
- **Power** — capacity, consumed, available (headroom)
- **Grid connection** — serving utility, substation, voltage (kV),
  interconnection capacity (MW), and interconnection status
- **Ownership** — operator, owner / parent, and the **legal entity** (SPV /
  filing entity) where publicly identifiable
- **Tenants** — principal occupier(s) (`self` for owner-occupied hyperscale)
- **Commercials** — lease type and indicative **rent** ($/kW/month) where the
  site leases space
- **Estimated capex (USD)**

Click any site to open a full **profile drawer** with all of the above.

![status legend: green = constructed, amber = under construction, blue = planned]

## Quick start

No third-party packages required — just Python 3.8+.

```bash
python3 app.py
```

This will:

1. **Download** mapped data-center locations across the US from OpenStreetMap
   (Overpass API).
2. **Enrich / merge** them with a curated dataset that carries the power and
   value figures OSM doesn't have (`data/datacenters_curated.json`).
3. Write the combined `data/datacenters.json`.
4. Serve the interactive map and open it in your browser
   (`http://127.0.0.1:8000/index.html`).

### Options

```bash
python3 app.py --no-download    # use the curated dataset only (works offline)
python3 app.py --no-browser     # don't auto-open a browser
python3 app.py --port 9000      # pick a port
python3 app.py --build-only     # just (re)build data/datacenters.json and exit
```

If the live download fails (no network, rate-limited, etc.) the app
automatically falls back to the curated dataset so the map still works.

## Using the map

- **Top bar** shows live KPIs (sites shown, capacity, grid interconnect, capex)
  for the current filter.
- **Color** = status (green constructed, amber under construction, blue planned).
- **Circle size** = the metric chosen in "Size markers by" (capex, power
  capacity, grid interconnect, consumed, or available power).
- **Filters**: status checkboxes, **operator** dropdown, **construction
  approval** dropdown, and free-text **search** across name, operator, owner,
  tenant, utility, city, and state. "Reset filters" clears everything.
- The bottom-left panel totals capacity / grid interconnection / capex and shows
  a consumed-vs-available power bar for whatever is currently visible.
- **Click any site** to open the full profile drawer (power, ownership & legal
  entity, tenants, grid connection, and commercials/rent). `Esc` closes it.

## Connectivity, power & geography layers

Toggle these overlays from the left panel:

- **Cable corridors** — metro-to-metro internet links drawn as lines whose
  thickness reflects how well-connected the endpoints are (weighted live by
  PeeringDB network counts aggregated per metro).
- **Interconnection hubs** — live from **PeeringDB**. Each peering facility is
  sized by the number of networks present (`net_count`); the bigger/brighter the
  purple dot, the better-connected the location (e.g. Equinix Ashburn ~500
  networks, One Wilshire LA, Equinix Chicago/Miami, Hurricane Electric Fremont).
- **Submarine cables** — major US landing stations with the systems that land
  there (curated, approximate).
- **Fiber backbone** — approximate long-haul terrestrial corridors between
  metros (curated).
- **Power / central stations** — major generating stations (nuclear / hydro /
  gas) that feed nearby campuses, including power-adjacent AI builds.
- **Major rivers** — simplified courses of key rivers used for cooling-water
  context (Ohio, Columbia, Colorado, Mississippi, Tennessee, Missouri).

Each site profile also now carries **power authority (ISO/RTO)**, **water
source**, **on-site / dedicated power** (gas/nuclear/hydro), and **nearby
routes / fiber** fields.

## Live data & refresh (does it pick up news?)

Re-running `python3 app.py` **re-downloads the live sources every time**:

- **OpenStreetMap (Overpass)** — mapped data-center locations.
- **PeeringDB** — interconnection facilities.

So the open/live layers stay current on each build. The **curated figures**
(capacity, owners, tenants, rent, status, grid) are maintained by hand — there
is no free real-time "data-center news" feed, so news-driven facts are updated
by editing the curated JSON (or by a future importer). To refresh on a schedule,
run the build on a cron / CI job, e.g. `python3 app.py --build-only` nightly.

## Site feasibility scoring

Every site gets a **0–100 feasibility score** (shown in the profile drawer, and
selectable as a marker size/colour). It's a weighted composite of:

| Factor | Weight | Source |
|--------|--------|--------|
| Power cost | 22% | EIA industrial ¢/kWh by state |
| Power / grid availability | 18% | interconnection status + headroom |
| Connectivity | 20% | distance to nearest fiber metro |
| Land cost | 10% | relative land-price index by state |
| State / local incentives | 12% | state data-center incentive level |
| Low hazard (complexity) | 10% | flood + seismic + hurricane + water stress |
| Long-term viability | 8% | connectivity + grid blend |

Factor tables live in `data/factors_curated.json` (editable). Each site's drawer
shows the score, a component breakdown, and the underlying **FEMA/NOAA-style
hazards** (flood, seismic, hurricane, water stress), **power cost**, **land
index**, and **incentives**. Filter with "Min feasibility", colour markers by
feasibility, or size markers by score. *(All factors are best-effort
state-level estimates for visualization, not site surveys.)*

## Cable & fiber dimensioning

Two *different* connectivity layers (not tiers of the same thing):

- **Fiber backbone** — real **physical long-haul** fiber routes. These carry an
  internal tier: **Tier-1** trunk routes are drawn thicker (high capacity),
  **Tier-2** regional routes thinner.
- **Cable corridors** — a **logical interconnection mesh** between metros,
  weighted by PeeringDB peering density (how much markets interconnect, not a
  physical cable).
- **Submarine cables** flag major high-capacity systems (drawn thicker).

The **"⚡ High-capacity only"** toggle filters all three to the high-capacity
set: Tier-1 long-haul fiber, major subsea cables, and the busiest corridors.
Route popups show **indicative capacity** (Tier-1 fiber ~100–400 Tbps, Tier-2
~10–100 Tbps, major subsea ~100–300+ Tbps), and an on-map legend explains that
line thickness ≈ capacity.

## Land & site-factor choropleth

The **State choropleth** layer ("Land & site factors") shades US states by a
selectable factor — **site attractiveness** (a composite of power cost, land,
incentives and hazards), **power cost**, **land cost**, **incentives**, **water
stress**, **flood**, or **seismic** risk — on a red→amber→green ramp (red =
worse, green = better) with a matching legend. State data joins the live US
states GeoJSON with `data/factors_curated.json`.

## Coverage vs US total

The stats panel shows how the mapped capacity compares to the whole country:

- **In map — operational** — sum of capacity for *constructed* sites in the
  dataset (~17 GW).
- **In map — pipeline** — under-construction + planned capacity (~36 GW), mostly
  the announced AI buildout.
- **Est. US operational total** — external benchmark (~**50 GW** online end-2025
  per FERC / Utility Dive; Goldman Sachs cites ~31 GW demand, S&P/451 ~62 GW
  grid power — methodologies differ). Edit `us_benchmark` in
  `data/datacenters_curated.json` to adjust.

So the notable-operator layer covers roughly **half of estimated US operational
capacity** (~25 GW of ~50 GW), plus a large announced pipeline. The rest is the
long tail of smaller/edge facilities (partly captured by the live OpenStreetMap
layer, which adds locations but rarely power figures).

## Data sources & accuracy

- **OpenStreetMap (Overpass API)** — locations of mapped data centers, plus any
  `operator`, `owner`, `voltage`, and `power` tags present. Good for *where*
  facilities are; it rarely carries financial, tenant, or grid data.
- **PeeringDB API** — US interconnection facilities with network/exchange counts,
  powering the interconnection-hubs layer.
- **Curated dataset** (`data/datacenters_curated.json`) — notable facilities and
  AI/cloud campuses with best-effort **public estimates** for status, approval,
  power, grid connection (utility / substation / voltage / interconnection),
  ownership, legal entity, tenants, lease type, rent, and capex. These are
  compiled from company announcements, utility interconnection filings, permit
  dockets, and press reporting.
- **Curated connectivity** (`data/connectivity_curated.json`) — submarine cable
  landing stations and long-haul fiber corridors (approximate).

> ⚠️ All power, grid, ownership, tenant, rent, and value numbers are
> **illustrative estimates** compiled for visualization, **not** authoritative
> figures. Verify against primary sources before relying on them. Edit
> `data/datacenters_curated.json` (schema v2) to add sites or refine numbers,
> then re-run `python3 app.py --build-only`.

### Curated record schema (v2)

```jsonc
{
  "name": "...", "operator": "...", "owner": "...", "legal_entity": "...",
  "tenants": ["self (AWS)"],                 // or ["multi-tenant: ..."]
  "city": "...", "state": "..", "lat": 0.0, "lng": 0.0,
  "status": "constructed|under_construction|planned",
  "permit_status": "approved|permitted|pending",
  "capacity_mw": 0, "consumed_mw": 0, "value_usd": 0, "year": 2026,
  "utility": "...", "substation": "...", "voltage_kv": 230,
  "interconnection_mw": 0, "interconnection_status": "energized|approved|in queue",
  "lease_type": "owner-occupied|wholesale|colocation",
  "rent_per_kw_month": 130, "annual_rent_usd": null
}
```

## Files

| File                               | Purpose                                            |
|------------------------------------|----------------------------------------------------|
| `app.py`                           | Downloads + merges data (OSM + PeeringDB), serves the map |
| `index.html`                       | The interactive Leaflet map UI + profile drawer + layers |
| `data/datacenters_curated.json`    | Editable curated dataset (schema v2)               |
| `data/connectivity_curated.json`   | Editable cable landings + fiber backbone           |
| `data/datacenters.json`            | Generated combined dataset the map reads           |
| `data/interconnection.json`        | Generated connectivity layer (PeeringDB + curated) |

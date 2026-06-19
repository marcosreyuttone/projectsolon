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

## Data sources & accuracy

- **OpenStreetMap (Overpass API)** — locations of mapped data centers, plus any
  `operator`, `owner`, `voltage`, and `power` tags present. Good for *where*
  facilities are; it rarely carries financial, tenant, or grid data.
- **Curated dataset** (`data/datacenters_curated.json`) — notable facilities and
  AI/cloud campuses with best-effort **public estimates** for status, approval,
  power, grid connection (utility / substation / voltage / interconnection),
  ownership, legal entity, tenants, lease type, rent, and capex. These are
  compiled from company announcements, utility interconnection filings, permit
  dockets, and press reporting.

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
| `app.py`                           | Downloads + merges data, then serves the map       |
| `index.html`                       | The interactive Leaflet map UI + profile drawer    |
| `data/datacenters_curated.json`    | Editable curated dataset (schema v2)               |
| `data/datacenters.json`            | Generated combined dataset the map reads           |

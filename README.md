# US Data Center Map

An interactive map of data centers across the United States showing, for each
site: **status** (constructed / under construction / planned), **power
capacity**, **power consumed**, **power available** (headroom), and an
**estimated value (USD)**.

![status legend: green = constructed, orange = under construction, blue = planned]

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

- **Color** = status (green constructed, orange under construction, blue planned).
- **Circle size** = the metric chosen in the "Size markers by" dropdown
  (value, power capacity, power consumed, or power available).
- **Status checkboxes** filter what's shown.
- **Search** by name, operator, city, or state.
- The bottom-left panel totals capacity / consumed / available power and value
  for whatever is currently visible, with a consumed-vs-available bar.
- Click any circle for full per-site details.

## Data sources & accuracy

- **OpenStreetMap (Overpass API)** — locations of mapped data centers. Good for
  *where* facilities are; it rarely carries power or financial data.
- **Curated dataset** (`data/datacenters_curated.json`) — notable facilities and
  markets with best-effort **public estimates** for status, power capacity (MW),
  power consumed (MW), and capital value (USD).

> ⚠️ The power and value numbers are illustrative estimates compiled for
> visualization, **not** authoritative figures. Edit
> `data/datacenters_curated.json` to add sites or refine numbers, then re-run
> `python3 app.py --build-only`.

## Files

| File                               | Purpose                                            |
|------------------------------------|----------------------------------------------------|
| `app.py`                           | Downloads + merges data, then serves the map       |
| `index.html`                       | The interactive Leaflet map UI                     |
| `data/datacenters_curated.json`    | Editable curated dataset (power/value/status)      |
| `data/datacenters.json`            | Generated combined dataset the map reads           |

#!/usr/bin/env python3
"""
US Data Center Map
==================

Downloads information about data centers across the United States, enriches it
with a curated dataset of power-capacity / power-consumption / investment-value
figures, and serves an interactive Leaflet map in your browser.

Data sources
------------
* OpenStreetMap (Overpass API) -- live download of mapped data-center
  locations across the US (telecom=data_center, building/landuse=data_center).
  This gives broad geographic coverage of *where* data centers are.
* Curated dataset (data/datacenters_curated.json) -- notable facilities and
  markets with best-effort public estimates for status (constructed / under
  construction / planned), power capacity (MW), power consumed (MW), and
  capital value (USD). OSM does not carry these business figures, so they are
  supplied here. Treat them as illustrative, not authoritative.

If the live download fails (no network, rate-limited, etc.) the app falls back
to the curated dataset alone so the map always works.

Usage
-----
    python3 app.py                 # download + build + serve + open browser
    python3 app.py --no-download    # skip Overpass, use curated data only
    python3 app.py --no-browser     # don't auto-open a browser
    python3 app.py --port 9000      # choose the local port
    python3 app.py --build-only      # just write data/datacenters.json and exit
"""

import argparse
import json
import math
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
CURATED_PATH = os.path.join(DATA_DIR, "datacenters_curated.json")
OUTPUT_PATH = os.path.join(DATA_DIR, "datacenters.json")
CONNECTIVITY_CURATED = os.path.join(DATA_DIR, "connectivity_curated.json")
INTERCONNECT_OUTPUT = os.path.join(DATA_DIR, "interconnection.json")
FACTORS_PATH = os.path.join(DATA_DIR, "factors_curated.json")
ISO_PATH = os.path.join(DATA_DIR, "iso_curated.json")

# PeeringDB public API: facilities carry lat/lng plus net_count (number of
# networks present) and ix_count (number of internet exchanges) -- a strong
# proxy for how well-interconnected a location is.
PEERINGDB_FAC = "https://www.peeringdb.com/api/fac?country=US"
# Only keep facilities with at least this many networks to surface real
# interconnection hubs and keep the map legible.
IX_MIN_NETS = 5

# TeleGeography open submarine cable map (all cables worldwide as GeoJSON).
SUBMARINE_CABLES_URL = "https://www.submarinecablemap.com/api/v3/cable/cable-geo.json"
# Keep cables that pass near US coasts (lat, lng window incl. HI).
US_CABLE_BBOX = (15.0, -162.0, 52.0, -64.0)

# Open US states GeoJSON for the state-factor choropleth.
US_STATES_URL = "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json"
STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR",
}

# Public Overpass mirrors; tried in order until one responds.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Continental-US-ish bounding box (south, west, north, east). Covers lower 48.
US_BBOX = (24.5, -125.0, 49.5, -66.5)

# Query mapped data centers. Centers/ways collapsed to a single point each.
OVERPASS_QUERY = """
[out:json][timeout:90];
(
  node["telecom"="data_center"]({bbox});
  way["telecom"="data_center"]({bbox});
  node["building"="data_center"]({bbox});
  way["building"="data_center"]({bbox});
  way["landuse"="data_center"]({bbox});
);
out tags center 800;
""".strip()


def log(msg):
    print(f"[datacenter-map] {msg}", flush=True)


def load_curated():
    with open(CURATED_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for d in raw.get("datacenters", []):
        out.append(normalize(d, source="curated"))
    return out


def load_benchmark():
    """External estimate of total US data-center capacity (coverage reference)."""
    try:
        with open(CURATED_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("us_benchmark")
    except (FileNotFoundError, ValueError):
        return None


def normalize(d, source):
    """Coerce a record into the common schema used by the map."""
    capacity = float(d.get("capacity_mw") or 0)
    consumed = float(d.get("consumed_mw") or 0)
    status = (d.get("status") or "unknown").lower()
    # Headroom only meaningful for operational sites.
    if status == "constructed":
        available = max(capacity - consumed, 0.0)
    else:
        available = 0.0
    # Tenants may arrive as a list or a comma-separated string.
    tenants = d.get("tenants") or []
    if isinstance(tenants, str):
        tenants = [t.strip() for t in tenants.split(",") if t.strip()]
    rent = d.get("rent_per_kw_month")
    return {
        "name": d.get("name") or "Unnamed data center",
        "operator": d.get("operator") or "",
        "owner": d.get("owner") or "",
        "legal_entity": d.get("legal_entity") or "",
        "tenants": tenants,
        "city": d.get("city") or "",
        "state": d.get("state") or "",
        "lat": float(d["lat"]),
        "lng": float(d["lng"]),
        "status": status,
        "permit_status": (d.get("permit_status") or "").lower(),
        "capacity_mw": round(capacity, 1),
        "consumed_mw": round(consumed, 1),
        "available_mw": round(available, 1),
        "value_usd": float(d.get("value_usd") or 0),
        "year": d.get("year") or None,
        # Grid connection
        "utility": d.get("utility") or "",
        "iso_rto": d.get("iso_rto") or "",
        "substation": d.get("substation") or "",
        "voltage_kv": float(d["voltage_kv"]) if d.get("voltage_kv") else None,
        "interconnection_mw": float(d["interconnection_mw"]) if d.get("interconnection_mw") else None,
        "interconnection_status": (d.get("interconnection_status") or "").lower(),
        # Site context
        "water_source": d.get("water_source") or "",
        "onsite_power": d.get("onsite_power") or "",
        "near_route": d.get("near_route") or "",
        # Commercials
        "lease_type": (d.get("lease_type") or "").lower(),
        "rent_per_kw_month": float(rent) if rent not in (None, "") else None,
        "annual_rent_usd": float(d["annual_rent_usd"]) if d.get("annual_rent_usd") else None,
        "source": source,
    }


def fetch_overpass(timeout=100):
    """Download data center locations from OpenStreetMap. Returns a list or []."""
    south, west, north, east = US_BBOX
    bbox = f"{south},{west},{north},{east}"
    query = OVERPASS_QUERY.format(bbox=bbox)
    data = urlencode({"data": query}).encode("utf-8")
    headers = {
        "User-Agent": "us-datacenter-map/1.0 (educational visualization)",
        "Accept": "application/json",
    }
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            log(f"Downloading data centers from {endpoint} ...")
            req = Request(endpoint, data=data, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            elements = payload.get("elements", [])
            log(f"  -> received {len(elements)} OSM elements")
            return parse_overpass(elements)
        except (HTTPError, URLError, TimeoutError, ValueError) as e:
            log(f"  ! {endpoint} failed: {e}")
            time.sleep(1)
    log("All Overpass mirrors failed; using curated data only.")
    return []


def parse_overpass(elements):
    out = []
    for el in elements:
        tags = el.get("tags", {})
        if "center" in el:
            lat, lng = el["center"]["lat"], el["center"]["lon"]
        elif "lat" in el:
            lat, lng = el["lat"], el["lon"]
        else:
            continue
        # Best-effort status / capacity from OSM tags (rarely present).
        status = "constructed"
        if tags.get("construction") or tags.get("building") == "construction":
            status = "under_construction"
        if tags.get("proposed"):
            status = "planned"
        capacity = parse_power_mw(tags, ("power_capacity", "power", "capacity"))
        # Grid hints occasionally present on OSM features.
        voltage_kv = None
        vraw = tags.get("voltage") or ""
        vnum = "".join(ch for ch in vraw.split(";")[0] if ch.isdigit() or ch == ".")
        if vnum:
            try:
                v = float(vnum)
                voltage_kv = v / 1000.0 if v > 1000 else v  # volts -> kV
            except ValueError:
                pass
        operator = tags.get("operator") or tags.get("brand") or tags.get("network") or ""
        out.append(normalize({
            "name": tags.get("name") or operator or "OSM data center",
            "operator": operator,
            "owner": tags.get("owner") or tags.get("brand") or "",
            "city": tags.get("addr:city") or "",
            "state": tags.get("addr:state") or "",
            "lat": lat,
            "lng": lng,
            "status": status,
            "permit_status": "",
            "capacity_mw": capacity,
            "consumed_mw": 0,
            "value_usd": 0,
            "utility": tags.get("operator:power") or "",
            "voltage_kv": voltage_kv,
        }, source="openstreetmap"))
    return out


def parse_power_mw(tags, keys):
    """Pull a megawatt figure out of free-form OSM power tags. Returns float MW."""
    for key in keys:
        v = tags.get(key, "")
        num = "".join(ch for ch in v if ch.isdigit() or ch == ".")
        if not num:
            continue
        try:
            val = float(num)
        except ValueError:
            continue
        low = v.lower()
        if "gw" in low:
            val *= 1000.0
        elif "kw" in low:
            val /= 1000.0
        return val
    return 0.0


def dedupe(records):
    """Drop OSM points that sit within ~12km of a curated facility (likely dups)."""
    curated = [r for r in records if r["source"] == "curated"]
    others = [r for r in records if r["source"] != "curated"]
    kept = list(curated)
    for r in others:
        clash = False
        for c in curated:
            if abs(r["lat"] - c["lat"]) < 0.12 and abs(r["lng"] - c["lng"]) < 0.12:
                clash = True
                break
        if not clash:
            kept.append(r)
    return kept


def load_factors():
    try:
        with open(FACTORS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def load_iso():
    try:
        with open(ISO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def iso_for_state(state, iso_data):
    """Return the ISO/RTO queue record for a state, or None."""
    if not iso_data or not state:
        return None
    abbr = iso_data.get("state_iso", {}).get(state)
    if not abbr:
        return None
    rec = dict(iso_data.get("iso", {}).get(abbr, {}))
    if rec:
        rec["iso"] = abbr
    return rec or None


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def nearest_metro_km(lat, lng, metros):
    """Rough great-circle distance (km) to the nearest connectivity metro."""
    best = 9e9
    for m in metros:
        dlat = (lat - m["lat"]) * 111.0
        dlng = (lng - m["lng"]) * 111.0 * math.cos(math.radians(lat))
        d = (dlat * dlat + dlng * dlng) ** 0.5
        if d < best:
            best = d
    return best


def score_feasibility(rec, factors, metros, iso_data=None):
    """0-100 data-center site-feasibility score with a component breakdown.
    Uses state factor tables (power/land/incentives/hazards) + connectivity
    distance + grid interconnection status + ISO/RTO queue availability."""
    if not factors:
        return None
    st = factors["states"].get(rec.get("state"))
    used_default = st is None
    st = st or factors["defaults"]
    pc_lo, pc_hi = factors.get("power_cost_range", [5.5, 16.0])

    # Power cost: cheaper -> higher score.
    power = _clamp(100 * (pc_hi - st["power_cost"]) / (pc_hi - pc_lo))
    # Land: cheaper -> higher score.
    land = _clamp(100 - st["land_index"])
    # Connectivity: closer to a major interconnection metro -> higher.
    dist = nearest_metro_km(rec["lat"], rec["lng"], metros) if metros else 300
    connectivity = _clamp(100 - dist / 6.0)
    # Grid / power availability: interconnection status + headroom, blended
    # with the region's ISO/RTO queue availability (can you actually get power?).
    gmap = {"energized": 90, "approved": 72, "in queue": 45, "": 62}
    status_score = gmap.get(rec.get("interconnection_status", ""), 62)
    if rec.get("status") == "constructed" and (rec.get("available_mw") or 0) > 0:
        status_score = min(100, status_score + 8)
    iso = iso_for_state(rec.get("state"), iso_data)
    if iso:
        grid = 0.6 * status_score + 0.4 * _clamp(iso["availability"] * 20)
    else:
        grid = status_score
    # Local / state support.
    incentives = _clamp(st["incentives"] * 20)
    # Complexity: hazards -> lower score is worse.
    hazard = st["flood"] + st["seismic"] + st["hurricane"] + st["water_stress"]
    complexity = _clamp(100 - hazard * 5)
    # Long-term viability: connectivity + grid headroom blend.
    viability = _clamp(0.55 * connectivity + 0.45 * grid)

    w = factors["weights"]
    score = (power * w["power_cost"] + grid * w["grid"] + connectivity * w["connectivity"]
             + land * w["land"] + incentives * w["incentives"] + complexity * w["complexity"]
             + viability * w["viability"])
    comp = {k: round(v) for k, v in {
        "power": power, "grid": grid, "connectivity": connectivity, "land": land,
        "incentives": incentives, "complexity": complexity, "viability": viability,
    }.items()}
    out = {
        "score": round(score),
        "components": comp,
        "factors": {
            "power_cost_cents_kwh": st["power_cost"], "land_index": st["land_index"],
            "incentives": st["incentives"], "flood": st["flood"], "seismic": st["seismic"],
            "hurricane": st["hurricane"], "water_stress": st["water_stress"],
            "nearest_metro_km": round(dist) if metros else None,
            "estimated": used_default,
        },
    }
    if iso:
        out["grid_queue"] = {
            "iso": iso["iso"], "name": iso.get("name"), "queue_gw": iso.get("queue_gw"),
            "avg_wait_years": iso.get("avg_wait_years"), "availability": iso.get("availability"),
            "note": iso.get("note"),
        }
    return out


def build_dataset(download=True):
    curated = load_curated()
    log(f"Loaded {len(curated)} curated data centers.")
    osm = fetch_overpass() if download else []
    records = dedupe(curated + osm)

    # Attach site-feasibility scores.
    factors = load_factors()
    iso_data = load_iso()
    metros = load_connectivity().get("metros", [])
    # Score only curated sites: OpenStreetMap points lack the power/grid detail
    # to score meaningfully, and scoring them all bloats the payload.
    scored = 0
    for r in records:
        fs = score_feasibility(r, factors, metros, iso_data) if r["source"] == "curated" else None
        r["feasibility"] = fs
        if fs:
            scored += 1
    log(f"Scored feasibility for {scored} sites.")

    totals = summarize(records)
    dataset = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": ["OpenStreetMap (Overpass API)", "curated dataset"]
        if osm else ["curated dataset"],
        "counts": totals,
        "us_benchmark": load_benchmark(),
        "datacenters": records,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)
    log(f"Wrote {len(records)} data centers to {OUTPUT_PATH}")
    log(f"  constructed={totals['constructed']} "
        f"under_construction={totals['under_construction']} "
        f"planned={totals['planned']}")
    log(f"  total capacity={totals['capacity_mw']:.0f} MW  "
        f"consumed={totals['consumed_mw']:.0f} MW  "
        f"available={totals['available_mw']:.0f} MW  "
        f"value=${totals['value_usd']/1e9:.1f}B")
    return dataset


# --------------------------------------------------------------------------- #
# Connectivity / interconnection layer
# --------------------------------------------------------------------------- #

def fetch_peeringdb(timeout=60, min_nets=IX_MIN_NETS):
    """Download US interconnection facilities from PeeringDB. Returns a list."""
    headers = {
        "User-Agent": "us-datacenter-map/1.0 (educational visualization)",
        "Accept": "application/json",
    }
    try:
        log(f"Downloading interconnection facilities from PeeringDB ...")
        req = Request(PEERINGDB_FAC, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as e:
        log(f"  ! PeeringDB failed: {e}; skipping interconnection facilities.")
        return []
    out = []
    for f in payload.get("data", []):
        lat, lng = f.get("latitude"), f.get("longitude")
        if lat is None or lng is None:
            continue
        nets = f.get("net_count") or 0
        if nets < min_nets:
            continue
        out.append({
            "name": f.get("name") or "Facility",
            "operator": f.get("org_name") or "",
            "city": f.get("city") or "",
            "state": f.get("state") or "",
            "lat": float(lat),
            "lng": float(lng),
            "net_count": int(nets),
            "ix_count": int(f.get("ix_count") or 0),
            "source": "peeringdb",
        })
    out.sort(key=lambda r: r["net_count"], reverse=True)
    log(f"  -> kept {len(out)} interconnection hubs (>= {min_nets} networks)")
    return out


def fetch_submarine_cables(timeout=45):
    """Download all submarine cables (TeleGeography) and keep those touching US
    coasts. Returns a list of {name, paths:[[[lat,lng],...],...]}."""
    s, w, n, e = US_CABLE_BBOX
    headers = {"User-Agent": "us-datacenter-map/1.0 (educational visualization)"}
    try:
        log("Downloading submarine cables from TeleGeography ...")
        req = Request(SUBMARINE_CABLES_URL, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            gj = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as e:
        log(f"  ! submarine cables failed: {e}; skipping.")
        return []
    out = []
    for feat in gj.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "MultiLineString":
            continue
        name = (feat.get("properties") or {}).get("name") or "Submarine cable"
        paths, touches = [], False
        for seg in geom.get("coordinates", []):
            # seg is a list of [lon, lat]; downsample to keep payload small.
            pts = [[round(c[1], 3), round(c[0], 3)] for c in seg[::3]] or \
                  [[round(c[1], 3), round(c[0], 3)] for c in seg]
            if any(s <= p[0] <= n and w <= p[1] <= e for p in pts):
                touches = True
            paths.append(pts)
        if touches:
            out.append({"name": name, "paths": paths})
    log(f"  -> kept {len(out)} US-touching submarine cables")
    return out


def fetch_states_choropleth(timeout=45):
    """US states GeoJSON joined with factor data + a composite 'attractiveness'
    score per state, for the land/site-factor choropleth layer."""
    factors = load_factors()
    if not factors:
        return None
    iso_data = load_iso()
    headers = {"User-Agent": "us-datacenter-map/1.0 (educational visualization)"}
    try:
        log("Downloading US states GeoJSON ...")
        req = Request(US_STATES_URL, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            gj = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as e:
        log(f"  ! states GeoJSON failed: {e}; skipping choropleth.")
        return None
    pc_lo, pc_hi = factors.get("power_cost_range", [5.5, 16.0])
    kept = []
    for feat in gj.get("features", []):
        nm = (feat.get("properties") or {}).get("name", "")
        abbr = STATE_ABBR.get(nm)
        st = factors["states"].get(abbr) if abbr else None
        if not st:
            continue
        power = _clamp(100 * (pc_hi - st["power_cost"]) / (pc_hi - pc_lo))
        land = _clamp(100 - st["land_index"])
        inc = _clamp(st["incentives"] * 20)
        hazard = st["flood"] + st["seismic"] + st["hurricane"] + st["water_stress"]
        low_haz = _clamp(100 - hazard * 5)
        attractiveness = round(0.35 * power + 0.15 * land + 0.20 * inc + 0.30 * low_haz)
        iso = iso_for_state(abbr, iso_data)
        feat["properties"] = {
            "name": nm, "st": abbr, "attractiveness": attractiveness,
            "power_cost": st["power_cost"], "land_index": st["land_index"],
            "incentives": st["incentives"], "flood": st["flood"],
            "seismic": st["seismic"], "hurricane": st["hurricane"],
            "water_stress": st["water_stress"],
            "iso": iso["iso"] if iso else None,
            "grid_availability": iso["availability"] if iso else None,
            "queue_gw": iso.get("queue_gw") if iso else None,
            "avg_wait_years": iso.get("avg_wait_years") if iso else None,
        }
        kept.append(feat)
    log(f"  -> built choropleth for {len(kept)} states")
    return {"type": "FeatureCollection", "features": kept}


def load_connectivity():
    """Curated connectivity context: cable landings, backbone, metro hubs,
    cable corridors, power stations, and rivers."""
    try:
        with open(CONNECTIVITY_CURATED, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw
    except (FileNotFoundError, ValueError) as e:
        log(f"  ! could not load {CONNECTIVITY_CURATED}: {e}")
        return {}


def attach_metro_connectivity(metros, facilities):
    """Sum PeeringDB network counts onto each curated metro hub (within ~0.6deg)
    so cable corridors can be weighted by live interconnection density."""
    for m in metros:
        nets = 0
        for f in facilities:
            if abs(f["lat"] - m["lat"]) < 0.6 and abs(f["lng"] - m["lng"]) < 0.6:
                nets += f["net_count"]
        m["net_count"] = nets
    return metros


def build_corridors(metros, corridor_pairs, knn=3):
    """Build weighted cable corridors from curated metro pairs PLUS an automatic
    k-nearest-neighbour mesh, so the whole connectivity web is shown (not just
    the main links)."""
    by_id = {m["id"]: m for m in metros}
    edges = set()
    for a, b in corridor_pairs:
        if a in by_id and b in by_id:
            edges.add(tuple(sorted((a, b))))
    # k-nearest neighbours by great-circle-ish distance.
    for m in metros:
        dists = sorted(
            ((((m["lat"] - o["lat"]) ** 2 + (m["lng"] - o["lng"]) ** 2), o["id"])
             for o in metros if o["id"] != m["id"]))
        for _, oid in dists[:knn]:
            edges.add(tuple(sorted((m["id"], oid))))
    out = []
    for a, b in edges:
        ma, mb = by_id[a], by_id[b]
        weight = min(ma.get("net_count", 0), mb.get("net_count", 0))
        out.append({
            "from": ma["name"], "to": mb["name"],
            "path": [[ma["lat"], ma["lng"]], [mb["lat"], mb["lng"]]],
            "weight": weight,
        })
    return out


# Keywords identifying major high-capacity (modern multi-Tbps) submarine cables.
MAJOR_CABLE_KEYWORDS = (
    "marea", "dunant", "grand", "amitie", "amitié", "anjana", "nuvem", "firmina",
    "curie", "junior", "jupiter", "faster", "plcn", "pacific light", "bay to bay",
    "bifrost", "echo", "topaz", "new cross pacific", "ncp", "sea-us", "seabras",
    "monet", "tannat", "brusa", "havfrue", "aec", "confluence", "gold data",
    "tgn", "pacific crossing", "tata", "google", "meta", "2africa",
)


def _path_len_km(path):
    total = 0.0
    for (a_lat, a_lng), (b_lat, b_lng) in zip(path, path[1:]):
        dlat = (a_lat - b_lat) * 111.0
        dlng = (a_lng - b_lng) * 111.0 * math.cos(math.radians((a_lat + b_lat) / 2))
        total += (dlat * dlat + dlng * dlng) ** 0.5
    return total


def build_interconnection(download=True):
    facilities = fetch_peeringdb() if download else []
    conn = load_connectivity()
    cable_landings = conn.get("cable_landings", [])
    backbone = conn.get("backbone", [])
    # Dimension terrestrial backbone routes: tier 1 (primary, long-haul) vs
    # tier 2 (regional), by route length.
    for r in backbone:
        L = _path_len_km(r.get("path", []))
        r["length_km"] = round(L)
        r["tier"] = 1 if L >= 1400 else 2
        # Indicative design capacity (modern DWDM long-haul, illustrative).
        r["capacity"] = "~100–400 Tbps" if r["tier"] == 1 else "~10–100 Tbps"
    metros = attach_metro_connectivity(conn.get("metros", []), facilities)
    corridors = build_corridors(metros, conn.get("corridor_pairs", []))
    power_stations = conn.get("power_stations", [])
    rivers = conn.get("rivers", [])
    states = fetch_states_choropleth() if download else None
    submarine_cables = fetch_submarine_cables() if download else []
    # Dimension submarine cables: flag major high-capacity systems.
    for c in submarine_cables:
        nm = c["name"].lower()
        c["major"] = any(k in nm for k in MAJOR_CABLE_KEYWORDS)
        c["capacity"] = "~100–300+ Tbps" if c["major"] else "up to tens of Tbps"
    counts = {
        "facilities": len(facilities),
        "cable_landings": len(cable_landings),
        "submarine_cables": len(submarine_cables),
        "backbone_routes": len(backbone),
        "metros": len(metros),
        "corridors": len(corridors),
        "power_stations": len(power_stations),
        "rivers": len(rivers),
        "networks_total": sum(f["net_count"] for f in facilities),
        "top_metro_nets": facilities[0]["net_count"] if facilities else 0,
    }
    dataset = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": (["PeeringDB", "TeleGeography"] if facilities else []) + ["curated connectivity"],
        "counts": counts,
        "facilities": facilities,
        "cable_landings": cable_landings,
        "submarine_cables": submarine_cables,
        "backbone": backbone,
        "metros": metros,
        "corridors": corridors,
        "power_stations": power_stations,
        "rivers": rivers,
        "states": states,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INTERCONNECT_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)
    log(f"Wrote interconnection layer to {INTERCONNECT_OUTPUT}")
    log(f"  facilities={counts['facilities']}  corridors={counts['corridors']}  "
        f"submarine_cables={counts['submarine_cables']}  power_stations={counts['power_stations']}  "
        f"rivers={counts['rivers']}")
    return dataset


def summarize(records):
    out = {
        "total": len(records),
        "constructed": 0,
        "under_construction": 0,
        "planned": 0,
        "capacity_mw": 0.0,
        "consumed_mw": 0.0,
        "available_mw": 0.0,
        "value_usd": 0.0,
        "interconnection_mw": 0.0,
        "operators": 0,
        "permit_approved": 0,
    }
    operators = set()
    out["operational_capacity_mw"] = 0.0
    out["pipeline_capacity_mw"] = 0.0
    for r in records:
        out[r["status"]] = out.get(r["status"], 0) + 1
        out["capacity_mw"] += r["capacity_mw"]
        out["consumed_mw"] += r["consumed_mw"]
        out["available_mw"] += r["available_mw"]
        out["value_usd"] += r["value_usd"]
        out["interconnection_mw"] += r.get("interconnection_mw") or 0
        if r["status"] == "constructed":
            out["operational_capacity_mw"] += r["capacity_mw"]
        else:
            out["pipeline_capacity_mw"] += r["capacity_mw"]
        if r.get("permit_status") == "approved":
            out["permit_approved"] += 1
        if r.get("operator"):
            operators.add(r["operator"])
    out["operators"] = len(operators)
    return out


def serve(port, open_browser=True):
    os.chdir(HERE)
    handler = SimpleHTTPRequestHandler
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/index.html"
    log(f"Serving map at {url}  (Ctrl+C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down.")
        httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(description="US Data Center interactive map")
    ap.add_argument("--no-download", action="store_true",
                    help="skip the OpenStreetMap download, use curated data only")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not auto-open a browser")
    ap.add_argument("--build-only", action="store_true",
                    help="only build data/datacenters.json, then exit")
    ap.add_argument("--port", type=int, default=8000, help="local server port")
    args = ap.parse_args()

    build_dataset(download=not args.no_download)
    build_interconnection(download=not args.no_download)
    if args.build_only:
        return
    serve(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

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
        "substation": d.get("substation") or "",
        "voltage_kv": float(d["voltage_kv"]) if d.get("voltage_kv") else None,
        "interconnection_mw": float(d["interconnection_mw"]) if d.get("interconnection_mw") else None,
        "interconnection_status": (d.get("interconnection_status") or "").lower(),
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


def build_dataset(download=True):
    curated = load_curated()
    log(f"Loaded {len(curated)} curated data centers.")
    osm = fetch_overpass() if download else []
    records = dedupe(curated + osm)

    totals = summarize(records)
    dataset = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": ["OpenStreetMap (Overpass API)", "curated dataset"]
        if osm else ["curated dataset"],
        "counts": totals,
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
    for r in records:
        out[r["status"]] = out.get(r["status"], 0) + 1
        out["capacity_mw"] += r["capacity_mw"]
        out["consumed_mw"] += r["consumed_mw"]
        out["available_mw"] += r["available_mw"]
        out["value_usd"] += r["value_usd"]
        out["interconnection_mw"] += r.get("interconnection_mw") or 0
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
    if args.build_only:
        return
    serve(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

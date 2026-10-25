"""Step 2 – normalise raw adverts and add objective geo metrics.

  python enrich.py      # data/raw_details.json -> data/listings.json

Per listing: total rent, rooms, floor, heating, HWB class, full text, image list,
coordinates (Willhaben or Nominatim geocode), walking time to the office,
best *direct* (no transfer) Wiener Linien connection from GTFS, nearby parks.
"""
import csv
import html
import io
import json
import math
import re
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict

from config import DATA, OFFICE_LAT, OFFICE_LON, PRICE_MAX, PRICE_MIN

GTFS_ZIP = DATA / "gtfs" / "gtfs.zip"
PATTERNS = DATA / "gtfs" / "patterns.json"
PARKS = DATA / "geo" / "parks.json"
GEOCACHE = DATA / "geo" / "geocode.json"
UA = "willhaben-flat-eval/1.0 (personal use)"

WALK_M_PER_MIN = 80          # ~4.8 km/h
DETOUR = 1.3                 # street network vs. straight line
STOP_RADIUS = 900            # max walk to/from a stop (m)
AVG_WAIT_MIN = 4
ROUTE_TYPES = {"0": "Straßenbahn", "1": "U-Bahn", "2": "Bahn", "3": "Bus", "7": "Badner Bahn"}


def dist_m(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(a))


def walk_min(m):
    return m * DETOUR / WALK_M_PER_MIN


# --- GTFS: one representative stop sequence (with ride times) per route/direction/shape ---
def build_patterns():
    z = zipfile.ZipFile(GTFS_ZIP)
    rd = lambda n: csv.DictReader(io.TextIOWrapper(z.open(n), "utf-8-sig"))
    routes = {r["route_id"]: (r["route_short_name"], r["route_type"]) for r in rd("routes.txt")}
    stops = {s["stop_id"]: (s["stop_name"], float(s["stop_lat"]), float(s["stop_lon"])) for s in rd("stops.txt")}
    rep = {}
    for t in rd("trips.txt"):
        rep.setdefault((t["route_id"], t["direction_id"], t.get("shape_id", "")), t["trip_id"])
    wanted = {tid: key for key, tid in rep.items()}
    seqs = defaultdict(list)
    print("scanning stop_times.txt (one-off, ~1-2 min)...", flush=True)
    for row in rd("stop_times.txt"):
        if row["trip_id"] in wanted:
            h, m, s = map(int, row["departure_time"].split(":"))
            seqs[row["trip_id"]].append((int(row["stop_sequence"]), row["stop_id"], h * 3600 + m * 60 + s))
    patterns = []
    for tid, seq in seqs.items():
        route_id = wanted[tid][0]
        name, rtype = routes[route_id]
        seq.sort()
        patterns.append({"line": name, "type": ROUTE_TYPES.get(rtype, rtype),
                         "stops": [[sid, t] for _, sid, t in seq]})
    PATTERNS.write_text(json.dumps({"stops": stops, "patterns": patterns}), encoding="utf8")
    print(f"{len(patterns)} line patterns cached")


_gtfs = None


def gtfs():
    global _gtfs
    if _gtfs is None:
        if not PATTERNS.exists():
            build_patterns()
        _gtfs = json.loads(PATTERNS.read_text(encoding="utf8"))
        stops = _gtfs["stops"]
        _gtfs["office_walk"] = {sid: walk_min(dist_m(OFFICE_LAT, OFFICE_LON, la, lo))
                                for sid, (_, la, lo) in stops.items()
                                if dist_m(OFFICE_LAT, OFFICE_LON, la, lo) <= STOP_RADIUS}
    return _gtfs


def direct_connections(lat, lon):
    """All direct (single vehicle) connections home -> office, best per line."""
    g = gtfs()
    stops, owalk = g["stops"], g["office_walk"]
    hwalk = {sid: walk_min(dist_m(lat, lon, la, lo)) for sid, (_, la, lo) in stops.items()
             if abs(la - lat) < 0.012 and abs(lo - lon) < 0.018 and dist_m(lat, lon, la, lo) <= STOP_RADIUS}
    best = {}
    for p in g["patterns"]:
        if p["line"].startswith("N"):  # night buses don't count for a daily commute
            continue
        seq = p["stops"]
        for i, (a, ta) in enumerate(seq):
            if a not in hwalk:
                continue
            for b, tb in seq[i + 1:]:
                if b in owalk:
                    ride = (tb - ta) / 60
                    total = hwalk[a] + AVG_WAIT_MIN + ride + owalk[b]
                    if p["line"] not in best or total < best[p["line"]]["total_min"]:
                        best[p["line"]] = {"line": p["line"], "type": p["type"], "from": stops[a][0], "to": stops[b][0],
                                           "walk_to_stop_min": round(hwalk[a]), "ride_min": round(ride),
                                           "walk_from_stop_min": round(owalk[b]), "total_min": round(total)}
    return sorted(best.values(), key=lambda c: c["total_min"])


# --- parks (Stadt Wien OGD PARKINFOOGD, point per park with area) ---
_parks = None


def parks_near(lat, lon, radius=600):
    global _parks
    if _parks is None:
        _parks = []
        for f in json.loads(PARKS.read_text(encoding="utf8"))["features"]:
            lo, la = f["geometry"]["coordinates"][:2]
            area = int(re.sub(r"\D", "", f["properties"].get("FLAECHE") or "0") or 0)
            _parks.append((f["properties"]["ANL_NAME"], la, lo, area))
    near = sorted(((round(dist_m(lat, lon, la, lo)), n, a) for n, la, lo, a in _parks), key=lambda x: x[0])
    within = [{"name": n, "dist_m": d, "area_m2": a} for d, n, a in near if d <= radius]
    big = next(({"name": n, "dist_m": d, "area_m2": a} for d, n, a in near if a >= 20000), None)
    return {"parks_within_600m": within[:8], "park_area_within_600m_m2": sum(p["area_m2"] for p in within),
            "nearest_big_park": big}


# --- geocoding fallback ---
def geocode(query):
    cache = json.loads(GEOCACHE.read_text(encoding="utf8")) if GEOCACHE.exists() else {}
    if query not in cache:
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
        try:
            res = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30))
        except Exception as e:  # rate limit etc. – fall back to postcode centroid, retry next run
            print(f"  geocode failed for {query!r}: {e}")
            time.sleep(2)
            return None
        cache[query] = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
        GEOCACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf8")
        time.sleep(1.1)
    return cache[query]


# --- normalisation ---
def strip_html(s):
    s = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<li[^>]*>", "• ", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\xa0]+", " ", s)).strip()


def num(v):
    if v is None:
        return None
    v = str(v).strip()
    if re.fullmatch(r"\d+\.\d+", v):  # "868.24"
        return float(v)
    v = v.replace(".", "").replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", v)
    return float(m.group()) if m else None


def parse_floor(v):
    if not v:
        return None
    s = str(v).lower()
    if "dg" in s or "dach" in s:
        m = re.search(r"\d+", s)
        return int(m.group()) if m else 6
    if s.startswith(("eg", "erd", "hochpar")):
        return 0
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def normalise(rec):
    ad = rec["advertDetails"]
    A = {a["name"]: (a["values"] if len(a["values"]) > 1 else (a["values"] or [None])[0]) for a in ad["attributes"]["attribute"]}
    texts = {"Beschreibung": strip_html(A.get("DESCRIPTION"))}
    for k, v in A.items():
        if k.startswith("GENERAL_TEXT_ADVERT/"):
            texts[k.split("/", 1)[1]] = strip_html(v)
    for k in ("LOCATION_INFO/TEXT", "PROJECT_INFO/TEXT"):
        if A.get(k):
            texts[k] = strip_html(A[k] if isinstance(A[k], str) else "\n".join(A[k]))
    total = num(A.get("RENTAL_PRICE/TOTAL_ENCUMBRANCE"))
    rent = num(A.get("RENTAL_PRICE/PER_MONTH")) or num(A.get("PRICE"))
    imgs = (ad.get("advertImageList") or {}).get("advertImage") or []
    addr = ad.get("advertAddressDetails") or {}
    coords = A.get("COORDINATES")
    lat = lon = None
    geo_src = None
    if coords:
        lat, lon = map(float, coords.split(","))
        geo_src = "willhaben" + (f" (±{A['POSITION_RADIUS_METERS']} m)" if A.get("POSITION_RADIUS_METERS") else "")
    street = A.get("LOCATION/ADDRESS_1")
    if lat is None and street:
        g = geocode(f"{street}, {addr.get('postCode')} Wien")
        if g:
            lat, lon = g
            geo_src = "geocoded street"
    return {
        "id": rec["id"],
        "url": rec["url"],
        "title": ad["description"],
        "price_total": total or rent,
        "price_label": "Gesamtbelastung" if total else "Miete (lt. Inserat)",
        "price_net": num(A.get("RENTAL_PRICE/PER_MONTH_NET")),
        "deposit": A.get("ADDITIONAL_COST/DEPOSIT"),
        "fee": A.get("ADDITIONAL_COST/FEE"),
        "rooms": num(A.get("NO_OF_ROOMS")),
        "area_m2": num(A.get("ESTATE_SIZE/LIVING_AREA") or A.get("ESTATE_SIZE")),
        "floor_raw": A.get("FLOOR") or A.get("UNIT_TITLE"),
        "floor": parse_floor(A.get("FLOOR")),
        "heating": ", ".join(A["HEATING"]) if isinstance(A.get("HEATING"), list) else A.get("HEATING"),
        "hwb": num(A.get("ENERGY_HWB")),
        "hwb_class": A.get("ENERGY_HWB_CLASS"),
        "fgee_class": A.get("ENERGY_FGEE_CLASS"),
        "features": A.get("ESTATE_PREFERENCE") if isinstance(A.get("ESTATE_PREFERENCE"), list) else [A["ESTATE_PREFERENCE"]] if A.get("ESTATE_PREFERENCE") else [],
        "free_area": A.get("FREE_AREA/FREE_AREA_TYPE_AND_AREA"),
        "building_type": A.get("BUILDING_TYPE"),
        "condition": A.get("BUILDING_CONDITION"),
        "construction_year": A.get("CONSTRUCTION_YEAR"),
        "term": A.get("DURATION/TERMLIMITTEXT") or A.get("DURATION/HASTERMLIMIT"),
        "available": A.get("AVAILABLE_DATE") or A.get("AVAILABLE_NOW") or A.get("AVAILABLE_DATE_FREETEXT"),
        "is_project": "PROJECT_ID" in A and "PROPERTY_TYPE" not in A,
        "district": addr.get("postalName"),
        "postcode": addr.get("postCode"),
        "street": street,
        "lat": lat, "lon": lon, "geo_source": geo_src,
        "agency": A.get("CONTACT/COMPANYNAME"),
        "published": ad.get("firstPublishedDate"),
        "texts": texts,
        "images": [{"url": i.get("referenceImageUrl") or i.get("mainImageUrl"), "caption": i.get("description")} for i in imgs],
        "virtual_tour": A.get("INFOLINK/URL") or A.get("VIRTUAL_VIEW_LINK/URL"),
    }


def main():
    raw = json.loads((DATA / "raw_details.json").read_text(encoding="utf8"))
    listings = [normalise(rec) for rec in raw]
    by_pc = defaultdict(list)
    for L in listings:
        if L["lat"] is not None:
            by_pc[L["postcode"]].append((L["lat"], L["lon"]))
    out = []
    for L in listings:
        if L["lat"] is None and by_pc.get(L["postcode"]):
            pts = by_pc[L["postcode"]]
            L["lat"], L["lon"] = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            L["geo_source"] = "district centre (approx., no address given)"
        if L["lat"] is not None:
            office_m = dist_m(L["lat"], L["lon"], OFFICE_LAT, OFFICE_LON)
            L["office_dist_km"] = round(office_m / 1000, 2)
            L["walk_to_office_min"] = round(walk_min(office_m))
            L["direct_transit"] = direct_connections(L["lat"], L["lon"])[:5]
            L["green"] = parks_near(L["lat"], L["lon"])
        L["in_price_range"] = L["price_total"] is not None and PRICE_MIN <= L["price_total"] <= PRICE_MAX
        out.append(L)
    (DATA / "listings.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"{len(out)} listings -> data/listings.json  (in price range: {sum(l['in_price_range'] for l in out)})")


if __name__ == "__main__":
    main()

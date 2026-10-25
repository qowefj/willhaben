"""Step 3 – rule-based pre-score from attributes + text (no photos).

Used to choose the shortlist that gets the (expensive) photo analysis, and as the
fallback score for listings that are not photo-analysed.

  python prescore.py [N]   # writes data/prescore.json and data/shortlist.json (top N, default SHORTLIST_SIZE)
"""
import json
import re
import sys

from config import DATA, SHORTLIST_SIZE

# Criterion -> max points (sum 100). Shared with evaluate.py / dashboard.py.
WEIGHTS = {
    "floor": 18,        # high floor (5+ / DG)
    "sun": 14,          # west / south-west (sunset) or east
    "aircon": 10,       # Klimaanlage
    "heating": 14,      # own gas boiler (Gasetagenheizung / Therme)
    "location": 18,     # walk <= 60 min or direct line to office + good district
    "green": 10,        # parks / trees nearby
    "energy": 8,        # HWB class
    "rooms": 8,         # 2 rooms preferred
}
LABELS = {
    "floor": "Высокий этаж", "sun": "Солнце / закат", "aircon": "Кондиционер", "heating": "Свой котёл",
    "location": "Дорога до офиса и район", "green": "Зелень и парки", "energy": "Энергокласс", "rooms": "Комнаты",
}
GOOD_DISTRICTS = {"1060": 1.0, "1070": 1.0, "1040": 0.8, "1050": 0.8, "1120": 0.7, "1030": 0.6, "1080": 0.6, "1090": 0.6,
                  "1130": 0.6, "1180": 0.6, "1190": 0.6, "1010": 0.6, "1020": 0.5, "1150": 0.4, "1140": 0.5,
                  "1100": 0.4, "1230": 0.4, "1160": 0.4, "1170": 0.5}
HWB_CLASS = {"A++": 1.0, "A+": 1.0, "A": 0.95, "B": 0.8, "C": 0.6, "D": 0.35, "E": 0.15, "F": 0.05, "G": 0}


def text_of(L):
    return " ".join(v for v in L["texts"].values() if v).lower() + " " + " ".join(L["features"]).lower()


def has(t, *pats):
    return any(re.search(p, t) for p in pats)


def commute_ok(L):
    walk = L.get("walk_to_office_min")
    direct = L.get("direct_transit") or []
    return (walk is not None and walk <= 60) or bool(direct)


def prescore(L):
    t = text_of(L)
    s, why = {}, {}

    f = L["floor"]
    dg = has(t, r"dachgescho", r"\bdg\b", r"penthouse", r"mansard")
    s["floor"] = 1.0 if (f is not None and f >= 6) or dg else 0.85 if f == 5 else 0.5 if f == 4 else 0.3 if f == 3 else 0.1 if f is not None else 0.2
    why["floor"] = f"Этаж: {L['floor_raw'] or '?'}" + (" (DG в тексте)" if dg else "")

    west = has(t, r"west", r"abendsonne", r"sonnenuntergang")
    east = has(t, r"\bost(seitig|ausrichtung|lage)?\b", r"morgensonne")
    south = has(t, r"süd", r"sonnig")
    s["sun"] = 1.0 if west else 0.7 if east else 0.45 if south else 0.25
    why["sun"] = "текст: " + ("запад/вечернее солнце" if west else "восток" if east else "юг/солнечно" if south else "ориентация не указана")

    ac = has(t, r"klima(anlage|tisiert|gerät)", r"split", r"kühlung", r"\bklima\b")
    s["aircon"] = 1.0 if ac else 0.0
    why["aircon"] = "кондиционер упомянут" if ac else "кондиционер не упомянут"

    h = (L["heating"] or "").lower()
    own = has(h + " " + t, r"gasetagen", r"etagenheizung", r"therme", r"gaskombi", r"gas-?zentralheizung \(etage")
    district_heat = has(h + " " + t, r"fernw", r"fernheiz")
    central = has(h, r"hauszentral|zentralheizung")
    s["heating"] = 1.0 if own and not district_heat else 0.7 if "gas" in h else 0.0 if district_heat or central else 0.3
    why["heating"] = f"Отопление: {L['heating'] or 'не указано'}" + (" / Therme в тексте" if own else "")

    walk = L.get("walk_to_office_min")
    direct = L.get("direct_transit") or []
    best = direct[0]["total_min"] if direct else None
    c = 0.0
    if walk is not None and walk <= 60:
        c = max(c, 1.0 if walk <= 35 else 0.85)
    if best is not None:
        c = max(c, 1.0 if best <= 25 else 0.85 if best <= 35 else 0.7 if best <= 45 else 0.5)
    dq = GOOD_DISTRICTS.get(L["postcode"], 0.3)
    s["location"] = 0.75 * c + 0.25 * dq
    why["location"] = (f"пешком ~{walk} мин" if walk is not None else "нет координат") + (
        f"; без пересадок: {direct[0]['line']} ~{best} мин" if direct else "; прямой линии нет")

    g = L.get("green") or {}
    area = g.get("park_area_within_600m_m2", 0)
    s["green"] = min(1.0, area / 40000) if area else 0.1
    why["green"] = f"парки в 600 м: {area:,} м²".replace(",", " ")

    hc = (L["hwb_class"] or "").upper()
    s["energy"] = HWB_CLASS.get(hc, 0.2)
    why["energy"] = f"HWB {L['hwb'] or '?'} класс {hc or '—'}"

    r = L["rooms"]
    s["rooms"] = 1.0 if r and r >= 2 else 0.55 if r == 1 or (r and r > 1) else 0.4
    why["rooms"] = f"{r or '?'} комн., {L['area_m2'] or '?'} м²"

    pts = {k: round(s[k] * WEIGHTS[k], 1) for k in WEIGHTS}
    return {"id": L["id"], "total": round(sum(pts.values()), 1), "points": pts, "why": why, "commute_ok": commute_ok(L)}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else SHORTLIST_SIZE
    listings = json.loads((DATA / "listings.json").read_text(encoding="utf8"))
    scores = {L["id"]: prescore(L) for L in listings}
    (DATA / "prescore.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1), encoding="utf8")
    eligible = [L for L in listings if L["in_price_range"] and not L["is_project"] and len(L["images"]) >= 3
                and scores[L["id"]]["commute_ok"]]
    eligible.sort(key=lambda L: -scores[L["id"]]["total"])
    ids = [L["id"] for L in eligible[:n]]
    (DATA / "shortlist.json").write_text(json.dumps({"ids": ids}, indent=1), encoding="utf8")
    print(f"{len(eligible)} eligible (price, commute, photos); shortlist {len(ids)}:")
    for L in eligible[:n]:
        sc = scores[L["id"]]
        print(f"  {sc['total']:5.1f}  {L['id']}  {L['price_total']:.0f}€  {L['district']}  floor={L['floor_raw']}  heat={L['heating']}  imgs={len(L['images'])}")


if __name__ == "__main__":
    main()

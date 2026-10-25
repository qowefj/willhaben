"""Watch for NEW (or newly price-cut) iPhone 17/18 Pro Max >= 512 GB deals.

Sources: Willhaben (all of Austria, via the Apify actor, newest first) + Bazos.sk (Bratislava area, direct).
Only ads cheaper than the best deal found on 2026-10-25 for the same model + storage are reported.

  python monitor.py            # one check; prints JSON {"hits": [...], "stats": {...}}
  python monitor.py --baseline # mark everything currently listed as seen, report nothing

State: data/monitor/seen.json  ({"wh": {id: price}, "bz": {id: price}}), hits log: data/monitor/hits.jsonl
"""
import json
import re
import sys
import time

import apify_api as api
import bazos_analyze
import bazos_scan
import iphone_analyze
from config import DATA
from iphone_scan import MODELS
from scrape import PROXY_RESI, run_actor

STATE = DATA / "monitor"
STATE.mkdir(exist_ok=True)
SEEN = STATE / "seen.json"

# Best credible deal found on 2026-10-25 per (model, storage GB) – a hit must be strictly cheaper.
BEST_TODAY = {
    ("17", 512): 1099,    # Willhaben, dealer Krems, 95 % battery
    ("17", 1024): 1100,   # Willhaben, Leoben (photos checked)
    ("17", 2048): 1399,   # Bazos, Bratislava, frame dent
    ("18", 512): 1599,    # Willhaben, EDV-4U, unused
    ("18", 1024): 1749,   # Willhaben, Handy Volt, like new + warranty
    ("18", 2048): 2099,   # Willhaben dealers, sealed
}
MIN_PRICE = 500   # below this it is an accessory, a "buy" ad or a placeholder price


def is_hit(model, storage, price):
    if not (model and storage and price) or storage < 512 or price < MIN_PRICE:
        return False
    best = BEST_TODAY.get((model, min(storage, 2048)))
    return best is not None and price < best


def check_willhaben(seen, baseline):
    hits, n_new = [], 0
    for model, (slug, _) in MODELS.items():
        url = f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/apple/{slug}?rows=90&sort=1"
        run = run_actor({"mode": "list", "searchUrls": [url], "proxy": PROXY_RESI, "maxPages": 1,
                         "pushSummaries": True, "skipIds": list(seen)},
                        f"monitor-iphone{model}-pro-max-austria-new")
        items = api.dataset_items(run["defaultDatasetId"])
        details = {x["id"]: x for x in items if not x.get("summary")}
        for s in (x for x in items if x.get("summary")):
            price = float(s["price"]) if s.get("price") else None
            old = seen.get(s["id"])
            if s["id"] in details:                       # brand-new advert, full details fetched
                n_new += 1
                p = iphone_analyze.parse(details[s["id"]], model)
                if not baseline and not (p["defect"] or p["not_sale"] or p["other_model"]) \
                        and is_hit(model, p["storage"], p["price"]):
                    hits.append({"source": "willhaben", "model": model, **p, "reason": "new"})
            elif old and price and price < old:          # known advert, price cut
                storage = iphone_analyze.storage_of(s["title"] or "", "")
                if not baseline and is_hit(model, storage, price):
                    hits.append({"source": "willhaben", "model": model, "id": s["id"], "title": s["title"],
                                 "price": price, "old_price": old, "storage": storage,
                                 "url": f"https://www.willhaben.at/iad/{s['seo']}", "reason": "price cut"})
            if price:
                seen[s["id"]] = price
    return hits, n_new


def check_bazos(seen, baseline, pages=2):
    hits, n_new = [], 0
    rx = re.compile(r"(?<!\d)1[78]\s*-?\s*pro\s*-?\s*max", re.I)
    for q in bazos_scan.QUERIES:
        for offset in range(0, pages * 20, 20):
            for ad in bazos_scan.search_page(q, offset):
                if not (rx.search(ad["title"]) or rx.search(ad["snippet"])) or not bazos_analyze.NEAR_BA(ad["psc"]):
                    continue
                price = float(re.sub(r"\D", "", ad["price_text"]) or 0) or None
                old = seen.get(ad["id"])
                if ad["id"] not in seen:
                    n_new += 1
                    if not baseline:
                        p = bazos_analyze.parse(bazos_scan.detail(ad))
                        if not (p["defect"] or p["not_sale"]) and is_hit(p["model"], p["storage"], p["price"]):
                            hits.append({"source": "bazos", **p, "reason": "new"})
                elif old and price and price < old and not baseline:
                    p = bazos_analyze.parse(bazos_scan.detail(ad))
                    if not (p["defect"] or p["not_sale"]) and is_hit(p["model"], p["storage"], p["price"]):
                        hits.append({"source": "bazos", **p, "old_price": old, "reason": "price cut"})
                if price:
                    seen[ad["id"]] = price
    return hits, n_new


def main():
    baseline = "--baseline" in sys.argv
    state = json.loads(SEEN.read_text(encoding="utf8")) if SEEN.exists() else {"wh": {}, "bz": {}}
    if baseline:   # also treat everything from today's full scans as known
        for d in ("iphone", "iphone18"):
            f = DATA / d / "raw.json"
            if f.exists():
                for x in json.loads(f.read_text(encoding="utf8")):
                    state["wh"].setdefault(x["id"], float(iphone_analyze.attr(x["advertDetails"], "PRICE")[0] or 0))
        f = DATA / "bazos" / "raw.json"   # bumped ("topované") old Bazos ads reappear on page 1
        if f.exists():
            for x in json.loads(f.read_text(encoding="utf8")):
                state["bz"].setdefault(x["id"], float(re.sub(r"\D", "", x["price_text"]) or 0))
    wh_hits, wh_new = check_willhaben(state["wh"], baseline)
    bz_hits, bz_new = check_bazos(state["bz"], baseline)
    SEEN.write_text(json.dumps(state), encoding="utf8")
    hits = wh_hits + bz_hits
    for h in hits:
        h["best_today"] = BEST_TODAY.get((h.get("model"), min(h.get("storage") or 0, 2048)))
    with open(STATE / "hits.jsonl", "a", encoding="utf8") as f:
        for h in hits:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M"), **h}, ensure_ascii=False) + "\n")
    print(json.dumps({"hits": hits, "stats": {"willhaben_new": wh_new, "bazos_new": bz_new,
                                              "baseline": baseline}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

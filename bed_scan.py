"""Single beds 90x200 (frame, optionally with a good mattress) on Willhaben marketplace, Vienna – same private actor.

  python bed_scan.py collect [--resi]   # search + detail pages -> data/bed/raw.json
  python bed_scan.py photos ID [ID..]   # photos of chosen ads -> data/bed/photos/<id>/NN.jpg
"""
import json
import sys
import urllib.parse

import apify_api as api
from config import DATA
from scrape import PROXY_DC, PROXY_RESI, run_actor, save_jpeg

OUT = DATA / "bed"
OUT.mkdir(exist_ok=True)
# Keyword search, areaId=900 = Wien. "bett 90 x 200" matches ~2200 unrelated ads (tokens OR-ed), so it's left out.
BASE = "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?areaId=900&rows=90&sort=1&keyword="
SEARCH_URLS = [BASE + urllib.parse.quote(k) for k in ("bett 90x200", "90x200", "bettgestell 90", "einzelbett", "bettrahmen 90")]


def collect(proxy):
    run = run_actor({"mode": "list", "searchUrls": SEARCH_URLS, "proxy": proxy, "maxPages": 15, "maxDetails": 3000},
                    "bed-90x200-wien-list")
    items = api.dataset_items(run["defaultDatasetId"])
    log = api.run_log(run["id"])
    print("\n".join(l for l in log.splitlines() if "Search page" in l or "FAILED" in l)[-3000:])
    path = OUT / "raw.json"
    have = {x["id"]: x for x in json.loads(path.read_text(encoding="utf8"))} if path.exists() else {}
    have.update({x["id"]: x for x in items})
    path.write_text(json.dumps(list(have.values()), ensure_ascii=False), encoding="utf8")
    print(f"+{len(items)} adverts, {len(have)} unique -> data/bed/raw.json")


def photos(ids, proxy):
    raw = {x["id"]: x for x in json.loads((OUT / "raw.json").read_text(encoding="utf8"))}
    jobs = [{"key": f"img-{i}-{n:02d}", "url": img.get("referenceImageUrl") or img.get("mainImageUrl")}
            for i in ids
            for n, img in enumerate((raw[i]["advertDetails"].get("advertImageList") or {}).get("advertImage") or [])]
    run = run_actor({"mode": "images", "images": jobs, "proxy": proxy}, "bed-90x200-wien-photos")
    for j in jobs:
        _, i, n = j["key"].split("-")
        (OUT / "photos" / i).mkdir(parents=True, exist_ok=True)
        blob = api.call("GET", f"/key-value-stores/{run['defaultKeyValueStoreId']}/records/{urllib.parse.quote(j['key'])}", raw=True)
        save_jpeg(blob, OUT / "photos" / i / f"{n}.jpg")
    print(f"downloaded {len(jobs)} images -> data/bed/photos/")


if __name__ == "__main__":
    args = sys.argv[1:] or ["collect"]
    proxy = PROXY_RESI if "--resi" in args else PROXY_DC
    if args[0] == "collect":
        collect(proxy)
    elif args[0] == "photos":
        photos([a for a in args[1:] if a.isdigit()], PROXY_RESI)

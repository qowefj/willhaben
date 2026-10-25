"""PlayStation 5 / PS5 Pro consoles on Willhaben marketplace, all of Austria – collect via the same private actor.

  python ps5_scan.py collect [--resi]   # search + detail pages -> data/ps5/raw.json
  python ps5_scan.py photos ID [ID..]   # photos of chosen ads -> data/ps5/photos/<id>/NN.jpg
"""
import json
import sys
import urllib.parse

import apify_api as api
from config import DATA
from scrape import PROXY_DC, PROXY_RESI, run_actor, save_jpeg

OUT = DATA / "ps5"
OUT.mkdir(exist_ok=True)
# Konsolen > Playstation > Playstation 5 (holds PS5, Slim, Digital and Pro; games/controllers have own categories).
SEARCH_URL = "https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/playstation/playstation-5-5008953?rows=90&sort=1"


def collect(proxy):
    run = run_actor({"mode": "list", "searchUrls": [SEARCH_URL], "proxy": proxy, "maxPages": 30, "maxDetails": 3000}, "ps5")
    items = api.dataset_items(run["defaultDatasetId"])
    log = api.run_log(run["id"])
    print("\n".join(l for l in log.splitlines() if "Search page" in l or "FAILED" in l)[-3000:])
    (OUT / "raw.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf8")
    print(f"saved {len(items)} adverts -> data/ps5/raw.json")


def photos(ids, proxy):
    raw = {x["id"]: x for x in json.loads((OUT / "raw.json").read_text(encoding="utf8"))}
    jobs = [{"key": f"img-{i}-{n:02d}", "url": img.get("referenceImageUrl") or img.get("mainImageUrl")}
            for i in ids
            for n, img in enumerate((raw[i]["advertDetails"].get("advertImageList") or {}).get("advertImage") or [])]
    run = run_actor({"mode": "images", "images": jobs, "proxy": proxy}, "photos")
    for j in jobs:
        _, i, n = j["key"].split("-")
        (OUT / "photos" / i).mkdir(parents=True, exist_ok=True)
        blob = api.call("GET", f"/key-value-stores/{run['defaultKeyValueStoreId']}/records/{urllib.parse.quote(j['key'])}", raw=True)
        save_jpeg(blob, OUT / "photos" / i / f"{n}.jpg")
    print(f"downloaded {len(jobs)} images -> data/ps5/photos/")


if __name__ == "__main__":
    args = sys.argv[1:] or ["collect"]
    proxy = PROXY_RESI if "--resi" in args else PROXY_DC
    if args[0] == "collect":
        collect(proxy)
    elif args[0] == "photos":
        photos([a for a in args[1:] if a.isdigit()], PROXY_RESI)

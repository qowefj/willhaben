"""iPhone Pro Max (>=512 GB) on Willhaben marketplace, all of Austria – collect via the same private actor.

  python iphone_scan.py collect [--model 18] [--resi]   # search + detail pages -> data/<dir>/raw.json
  python iphone_scan.py photos ID [ID..] [--model 18]   # photos of chosen ads -> data/<dir>/photos/<id>/NN.jpg
"""
import json
import sys
import urllib.parse

import apify_api as api
from config import DATA
from scrape import PROXY_DC, PROXY_RESI, run_actor, save_jpeg

# Willhaben's own model category (keyword search pulls in cases, laptops, other models).
MODELS = {
    "17": ("iphone-17-pro-max-5015998", "iphone"),
    "18": ("iphone-18-pro-max-5017003", "iphone18"),
}


def model_arg(args):
    return args[args.index("--model") + 1] if "--model" in args else "17"


def out_dir(model):
    d = DATA / MODELS[model][1]
    d.mkdir(exist_ok=True)
    return d


def collect(model, proxy):
    url = f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/apple/{MODELS[model][0]}?rows=90&sort=1"
    run = run_actor({"mode": "list", "searchUrls": [url], "proxy": proxy, "maxPages": 15},
                    f"iphone{model}-pro-max-austria-list")
    items = api.dataset_items(run["defaultDatasetId"])
    log = api.run_log(run["id"])
    print("\n".join(l for l in log.splitlines() if "Search page" in l or "FAILED" in l)[-3000:])
    (out_dir(model) / "raw.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf8")
    print(f"saved {len(items)} adverts -> {out_dir(model) / 'raw.json'}")


def photos(model, ids, proxy):
    out = out_dir(model)
    raw = {x["id"]: x for x in json.loads((out / "raw.json").read_text(encoding="utf8"))}
    jobs = [{"key": f"img-{i}-{n:02d}", "url": img.get("referenceImageUrl") or img.get("mainImageUrl")}
            for i in ids
            for n, img in enumerate((raw[i]["advertDetails"].get("advertImageList") or {}).get("advertImage") or [])]
    run = run_actor({"mode": "images", "images": jobs, "proxy": proxy},
                    f"iphone{model}-pro-max-photos")
    for j in jobs:
        _, i, n = j["key"].split("-")
        (out / "photos" / i).mkdir(parents=True, exist_ok=True)
        blob = api.call("GET", f"/key-value-stores/{run['defaultKeyValueStoreId']}/records/{urllib.parse.quote(j['key'])}", raw=True)
        save_jpeg(blob, out / "photos" / i / f"{n}.jpg")
    print(f"downloaded {len(jobs)} images -> {out / 'photos'}")


if __name__ == "__main__":
    args = sys.argv[1:] or ["collect"]
    model = model_arg(args)
    proxy = PROXY_RESI if "--resi" in args else PROXY_DC
    if args[0] == "collect":
        collect(model, proxy)
    elif args[0] == "photos":
        ids = [a for a in args[1:] if a.isdigit() and len(a) > 4]
        photos(model, ids, PROXY_RESI)

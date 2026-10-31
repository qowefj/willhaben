"""iPhone Pro Max (>=512 GB) on Willhaben marketplace, all of Austria – collect via the fetcher on the docker host.

  python iphone_scan.py collect [--model 18]            # search + detail pages -> data/<dir>/raw.json
  python iphone_scan.py photos ID [ID..] [--model 18]   # photos of chosen ads -> data/<dir>/photos/<id>/NN.jpg
"""
import json
import sys

import host_api
from config import DATA
from scrape import save_jpeg

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


def collect(model):
    url = f"https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz/apple/{MODELS[model][0]}?rows=90&sort=1"
    items = host_api.crawl({"searchUrls": [url], "maxPages": 15}, f"iphone{model}")
    (out_dir(model) / "raw.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf8")
    print(f"saved {len(items)} adverts -> {out_dir(model) / 'raw.json'}")


def photos(model, ids):
    out = out_dir(model)
    raw_file = out / "raw.json"
    raw = {x["id"]: x for x in json.loads(raw_file.read_text(encoding="utf8"))} if raw_file.exists() else {}
    missing = [i for i in ids if i not in raw]   # e.g. a fresh checkout in the cloud routine
    if missing:
        urls = [f"https://www.willhaben.at/iad/object?adId={i}" for i in missing]
        raw.update((x["id"], x) for x in host_api.crawl({"detailUrls": urls}, "photos"))
    n_ok = 0
    for i in ids:
        if i not in raw:
            print(f"  ad {i} not found, skipped")
            continue
        (out / "photos" / i).mkdir(parents=True, exist_ok=True)
        for n, img in enumerate((raw[i]["advertDetails"].get("advertImageList") or {}).get("advertImage") or []):
            save_jpeg(host_api.image(img.get("referenceImageUrl") or img.get("mainImageUrl")),
                      out / "photos" / i / f"{n:02d}.jpg")
            n_ok += 1
    print(f"downloaded {n_ok} images -> {out / 'photos'}")


if __name__ == "__main__":
    args = sys.argv[1:] or ["collect"]
    model = model_arg(args)
    if args[0] == "collect":
        collect(model)
    elif args[0] == "photos":
        ids = [a for a in args[1:] if a.isdigit() and len(a) > 4]
        photos(model, ids)

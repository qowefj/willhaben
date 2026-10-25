"""iPhone 17/18 Pro Max on bazos.sk (all of Slovakia; Bratislava area is filtered later in bazos_analyze.py).

Bazos does not block this machine, so it is fetched directly (no Apify).

  python bazos_scan.py collect     # search pages + detail pages -> data/bazos/raw.json
  python bazos_scan.py photos ID [ID..]   # photos of chosen ads -> data/bazos/photos/<id>/NN.jpg
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

from config import DATA
from scrape import save_jpeg

OUT = DATA / "bazos"
OUT.mkdir(exist_ok=True)
BASE = "https://mobil.bazos.sk/"
QUERIES = ["iphone 17 pro max", "iphone 18 pro max"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
DELAY = 0.7   # be polite: sequential requests


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "sk,de;q=0.8,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    time.sleep(DELAY)
    return body if binary else body.decode("utf8", "replace")


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", s))).strip()


def search(query):
    ads, offset, total = [], 0, None
    while total is None or offset < total:
        cards, total = search_page(query, offset, with_total=True)
        ads += cards
        print(f"  {query!r} offset {offset}: {len(cards)} cards (total {total})", flush=True)
        if not cards:
            break
        offset += 20
    return ads


def search_page(query, offset=0, with_total=False):
    """One results page (20 ads, newest first, TOP ads pinned on top)."""
    params = {"hledat": query, "rubriky": "mobil", "hlokalita": "", "humkreis": 25,
              "cenaod": "", "cenado": "", "kitx": "ne", "crp": offset or ""}
    page = get(BASE + "?" + urllib.parse.urlencode(params))
    m = re.search(r"inzerátov z ([\d\s\xa0]+)", page)
    total = int(re.sub(r"\D", "", m.group(1))) if m else 0
    ads = []
    for c in page.split('<div class="inzeraty inzeratyflex">')[1:]:
        link = re.search(r'href="(/inzerat/(\d+)/[^"]+)"', c)
        ads.append({
            "id": link.group(2), "url": "https://mobil.bazos.sk" + link.group(1),
            "title": clean(re.search(r"class=nadpis><a[^>]*>(.*?)</a>", c, re.S).group(1)),
            "snippet": clean((re.search(r"class=popis>(.*?)</div>", c, re.S) or [None, ""])[1]),
            "price_text": clean(re.search(r'class="inzeratycena">(.*?)</div>', c, re.S).group(1)),
            "town": clean(re.search(r'class="inzeratylok">(.*?)<br>', c, re.S).group(1)),
            "psc": re.sub(r"\D", "", re.search(r'class="inzeratylok">.*?<br>(.*?)</div>', c, re.S).group(1)),
            "date": (re.search(r"\[(\d+\.\d+\.\s*\d{4})\]", c) or [None, None])[1],
            "views": int(re.sub(r"\D", "", re.search(r'class="inzeratyview">(.*?)</div>', c, re.S).group(1)) or 0),
            "top": "ztop" in c, "query": query,
        })
    return (ads, total) if with_total else ads


def detail(ad):
    page = get(ad["url"])
    desc = re.search(r"<div class=popisdetail>(.*?)</div>", page, re.S)
    ad["desc"] = clean(desc.group(1)) if desc else ""
    ad["name"] = clean((re.search(r"Meno:.*?<b>(.*?)</b>", page, re.S) or [None, ""])[1])
    ad["images"] = list(dict.fromkeys(re.findall(r'data-flickity-lazyload="([^"]+)"', page)))
    return ad


def collect():
    seen = {}
    for q in QUERIES:
        for ad in search(q):
            seen.setdefault(ad["id"], ad)
    # Bazos search is fuzzy (chargers, other models) -> keep only 17/18 Pro Max before fetching details.
    rx = re.compile(r"(?<!\d)1[78]\s*-?\s*pro\s*-?\s*max", re.I)
    ads = [a for a in seen.values() if rx.search(a["title"]) or rx.search(a["snippet"])]
    print(f"{len(seen)} unique ads, {len(ads)} are 17/18 Pro Max; fetching detail pages…")
    for i, ad in enumerate(ads):
        try:
            detail(ad)
        except Exception as e:
            print("  detail failed", ad["id"], e)
        if i % 50 == 0:
            print(f"  {i}/{len(ads)}", flush=True)
    (OUT / "raw.json").write_text(json.dumps(ads, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"saved {len(ads)} ads -> {OUT / 'raw.json'}")


def photos(ids):
    raw = {x["id"]: x for x in json.loads((OUT / "raw.json").read_text(encoding="utf8"))}
    for i in ids:
        (OUT / "photos" / i).mkdir(parents=True, exist_ok=True)
        for n, url in enumerate(raw[i].get("images") or []):
            save_jpeg(get(url, binary=True), OUT / "photos" / i / f"{n:02d}.jpg")
        print(i, len(raw[i].get("images") or []), "images")


if __name__ == "__main__":
    args = sys.argv[1:] or ["collect"]
    if args[0] == "collect":
        collect()
    elif args[0] == "photos":
        photos(args[1:])

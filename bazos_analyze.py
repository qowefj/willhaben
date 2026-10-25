"""Parse data/bazos/raw.json -> listings.json + undervaluation ranking (>=512 GB, Bratislava area).

Fair price = median asking price over ALL of Slovakia for the same model / storage / condition
(the Bratislava sample alone is too small); only Bratislava + nearby towns are ranked.

  python bazos_analyze.py [min_gb]
"""
import json
import re
import statistics
import sys

from bazos_scan import OUT
from iphone_analyze import storage_of

SEALED = r"nerozbal|zapečat|zapecat|neotvoren|nepoužit|nepouzit|zabalen|sealed|v\s*fólii|v\s*folii|\bnov[ýy]\b(?!\s*model)"
LIKENEW = r"ako\s*nov|stav\s*nov|bez\s*(škrab|skrab|poškod|poskod|známok|znamok)|perfektn|100\s*%\s*stav|top\s*stav|výborn|vyborn"
DEFECT = r"rozbit|prasknut|nefunk|poškoden|poskoden|icloud\s*(lock|zamk|blok)|zablokovan|na\s*diely|vymenen[ýy]\s*displej|neoriginal"
NEGATED = r"bez\s+[\wáäčďéíĺľňóôŕšťúýž\s,]{0,25}?(poškoden|poskoden)\w*"   # "bez škrabancov a poškodení"
NOT_SALE = r"\bkúpim|\bkupim|hľadám|hladam|prenáj|prenaj|splátk|splatk|\b(obal|kryt|puzdro|sklo|fólia|folia)\s*(na|pre)\b|\bkryt\b|\bpuzdro\b"
SWAP = r"vymen[íi]m|výmen|vymena"
# Bratislava (8xx xx) + Malacky/Stupava/Pezinok/Modra/Senec (900–903) + Šamorín (931): ~30 km radius.
NEAR_BA = lambda psc: psc[:1] == "8" or psc[:3] in {"900", "901", "902", "903", "931"}


def parse(ad):
    title, text = ad["title"], f"{ad['title']}\n{ad.get('desc') or ad['snippet']}"
    low = text.lower()
    other = re.search(r"(?<!\d)1[78]\s*-?\s*pro\b(?!\s*-?\s*max)", title, re.I) and not re.search(r"pro\s*-?\s*max", title, re.I)
    m = re.search(r"(?<!\d)(1[78])\s*-?\s*pro\s*-?\s*max", title, re.I) \
        or ("iphone" in title.lower() and re.search(r"(?<!\d)(1[78])\s*-?\s*pro\s*-?\s*max", text, re.I))
    price = re.sub(r"[^\d]", "", ad["price_text"])
    bat = re.search(r"(batér|bater|kapacit|zdravie|health)[^\d\n]{0,40}(\d{2,3})\s*%", text, re.I) \
        or re.search(r"(\d{2,3})\s*%\s*(batér|bater|kapacit|zdravie)", text, re.I)
    bat = int(next(g for g in bat.groups() if g.isdigit())) if bat else None
    return {
        "id": ad["id"], "url": ad["url"], "title": title, "model": m.group(1) if m and not other else None,
        "price": float(price) if price else None, "price_text": ad["price_text"],
        "storage": storage_of(title, ad.get("desc") or ad["snippet"]),
        "cond": "sealed" if re.search(SEALED, low) else "likenew" if re.search(LIKENEW, low) else "used",
        "battery": bat if bat and 70 <= bat <= 100 else None,
        "defect": bool(re.search(DEFECT, re.sub(NEGATED, " ", low))),
        "not_sale": bool(re.search(NOT_SALE, title.lower()) or re.search(r"\bk[úu]pim\b", low[:len(title) + 80])),
        "swap": bool(re.search(SWAP, low)),
        "warranty": bool(re.search(r"zĂˇruk|záruk|zaruk|faktúr|faktur|bloček|blocek|doklad", low)),
        "town": ad["town"], "psc": ad["psc"], "near_ba": NEAR_BA(ad["psc"]),
        "date": ad["date"], "views": ad["views"], "top": ad["top"], "name": ad.get("name"),
        "n_images": len(ad.get("images") or []), "desc": (ad.get("desc") or ad["snippet"]).strip(),
    }


def main(min_gb=512):
    rows = [parse(x) for x in json.loads((OUT / "raw.json").read_text(encoding="utf8"))]
    (OUT / "listings.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf8")
    clean = [r for r in rows if r["model"] and r["storage"] and r["price"] and r["price"] >= 300
             and not r["defect"] and not r["not_sale"]]
    groups = {}
    for r in clean:
        groups.setdefault((r["model"], r["storage"], r["cond"]), []).append(r["price"])
        groups.setdefault((r["model"], r["storage"], "*"), []).append(r["price"])
    print("Median asking price, all Slovakia (n):")
    for k in sorted(groups):
        print(f"  {k[0]} Pro Max {k[1]:>5} GB {k[2]:<8} n={len(groups[k]):>3}  median={statistics.median(groups[k]):>6.0f}")
    out = []
    for r in clean:
        if r["storage"] < min_gb or not r["near_ba"]:
            continue
        g = groups[(r["model"], r["storage"], r["cond"])]
        fair = statistics.median(g if len(g) >= 4 else groups[(r["model"], r["storage"], "*")])
        if r["battery"] and r["cond"] != "sealed":
            fair *= 1 - max(0, 95 - r["battery"]) * 0.01
        r["fair"], r["discount"] = round(fair), round(1 - r["price"] / fair, 3)
        out.append(r)
    out.sort(key=lambda r: -r["discount"])
    (OUT / "ranked.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"\n{len(rows)} ads, {len(clean)} usable, {len(out)} with >= {min_gb} GB in Bratislava area")
    for r in out:
        print(f"{r['discount']:+.0%} {r['price']:>6.0f}/{r['fair']:<5} {r['model']} {r['storage']:>4}GB {r['cond']:<7} "
              f"bat={r['battery'] or '-':<3} {'S' if r['swap'] else ' '} {r['date']} {r['town'][:14]:<14} {r['id']} {r['title'][:55]}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 512)

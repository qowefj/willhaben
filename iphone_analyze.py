"""Parse data/<dir>/raw.json -> listings.json + undervaluation ranking for >=512 GB.

  python iphone_analyze.py [--model 18] [min_gb]

Fair price = median asking price of comparable ads (same storage, same condition class: sealed / like-new / used),
adjusted for battery health and warranty. Discount = 1 - price / fair.
"""
import html
import json
import re
import statistics
import sys

from iphone_scan import model_arg, out_dir

STORAGE = [(2048, r"\b2\s*(tb|tr|t)\b|2000\s*gb|2048\s*gb"), (1024, r"\b1\s*tb|1000\s*gb|1024\s*gb|1\s*terabyte"),
           (512, r"512\s*(gb|g\b|gigabyte)?"), (256, r"256\s*(gb|g\b|gigabyte)?")]
SEALED = r"versiegelt|sealed|ovp\s*(neu|versiegelt)|neu\s*(&|und)?\s*ovp|originalverpackt\s*(&|und)?\s*neu|unge[öo]ffnet|brand\s*new|nagelneu|fabriksneu|werksversiegelt|werksoffen|\bneu\b(?!\s*(gekauft|preis|np))|unbenutzt"
LIKENEW = r"wie\s*neu|neuwertig|kaum\s*benutzt|keine\s*(kratzer|gebrauchsspuren)|makellos|top\s*zustand|perfekt(em)?\s*zustand"
DEFECT = r"defekt|sprung|gebrochen|kaputt|riss\b|face\s*id\s*(geht\s*)?nicht|icloud\s*(gesperrt|sperre)|gesperrt|ersatzteil|bastler|display\s*(schaden|tausch)|wasserschaden"
NOT_SALE = r"\bsuche\b|ankauf|kaufe\s*(ihr|dein|euer)|\bmiete\b|\bvermiet|ratenzahlung|leasing|attrappe|dummy|nur\s*(die\s*)?(ovp|verpackung|karton|box)\b|h[üu]lle\s*f[üu]r"
SWAP = r"\btausch(e)?\b|\bswap\b"


def attr(ad, name):
    for a in ad["attributes"]["attribute"]:
        if a["name"] == name:
            return a.get("values") or []
    return []


def text_of(ad):
    desc = " ".join(attr(ad, "DESCRIPTION"))
    desc = html.unescape(re.sub(r"<br\s*/?>", "\n", desc))
    return re.sub(r"<[^>]+>", " ", desc)


def storage_of(title, desc):
    for src in (title, desc):
        s = src.lower()
        for gb, rx in STORAGE:
            if re.search(rx, s):
                return gb
    return None


def battery_of(text):
    m = re.search(r"(akku|batterie|battery|kapazit[äa]t|zustand)[^\d\n]{0,40}(\d{2,3})\s*%", text, re.I) \
        or re.search(r"(\d{2,3})\s*%\s*(akku|batterie|battery|kapazit)", text, re.I)
    if not m:
        return None
    v = int(next(g for g in m.groups() if g and g.isdigit()))
    return v if 70 <= v <= 100 else None


def parse(item, model="17"):
    ad = item["advertDetails"]
    title = ad["description"]
    desc = text_of(ad)
    full = f"{title}\n{desc}"
    low = full.lower()
    dealer = attr(ad, "ISPRIVATE")[:1] != ["1"]
    # Dealer descriptions are full of boilerplate (insurance, repairs, Ankauf, Ratenzahlung) -> title only.
    boiler = title.lower() if dealer else low
    price = float((attr(ad, "PRICE") or ["0"])[0])
    cond = "sealed" if re.search(SEALED, low) else "likenew" if re.search(LIKENEW, low) else "used"
    named = re.findall(r"(?<!\d)(1\d)\s*-?\s*pro", title.lower())
    old = attr(ad, "OLD_PRICE")
    addr = ad.get("advertAddressDetails") or {}
    return {
        "id": item["id"], "url": item["url"], "title": title, "price": price,
        "old_price": float(old[0]) if old else None,
        "storage": storage_of(title, desc), "cond": cond, "battery": battery_of(full),
        "defect": bool(re.search(DEFECT, boiler)), "not_sale": bool(re.search(NOT_SALE, boiler)),
        "other_model": bool(named) and model not in named,   # misfiled in this category
        "swap": bool(re.search(SWAP, low)),
        "warranty": bool(re.search(r"garantie|gew[äa]hrleistung|applecare|rechnung", low)),
        "dealer": dealer,
        "company": (attr(ad, "CONTACT/COMPANY") or [None])[0],
        "place": f"{addr.get('postCode', '')} {addr.get('postalName') or addr.get('municipality') or ''}".strip(),
        "province": addr.get("province"),
        "published": ad.get("firstPublishedDate"), "changed": ad.get("changedDate"),
        "n_images": len((ad.get("advertImageList") or {}).get("advertImage") or []),
        "desc": desc.strip(),
    }


def main(model="17", min_gb=512):
    OUT = out_dir(model)
    rows = [parse(x, model) for x in json.loads((OUT / "raw.json").read_text(encoding="utf8"))]
    (OUT / "listings.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf8")
    clean = [r for r in rows if r["storage"] and not r["defect"] and not r["not_sale"] and not r["other_model"] and r["price"] >= 300]
    # Fair price per (storage, condition) – median; fallback to storage median.
    groups = {}
    for r in clean:
        groups.setdefault((r["storage"], r["cond"]), []).append(r["price"])
        groups.setdefault((r["storage"], "*"), []).append(r["price"])
    print("Median asking price (n):")
    for k in sorted(groups):
        v = groups[k]
        print(f"  {k[0]:>5} GB {k[1]:<8} n={len(v):>3}  median={statistics.median(v):>7.0f}  "
              f"p25={statistics.quantiles(v, n=4)[0] if len(v) > 3 else min(v):>7.0f}")
    out = []
    for r in clean:
        if r["storage"] < min_gb:
            continue
        g = groups[(r["storage"], r["cond"])]
        fair = statistics.median(g if len(g) >= 4 else groups[(r["storage"], "*")])
        if r["battery"] and r["cond"] != "sealed":
            fair *= 1 - max(0, 95 - r["battery"]) * 0.01   # ~1% per point below 95%
        r["fair"] = round(fair)
        r["discount"] = round(1 - r["price"] / fair, 3)
        out.append(r)
    out.sort(key=lambda r: -r["discount"])
    (OUT / "ranked.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"\n{len(rows)} ads, {len(clean)} usable, {len(out)} with >= {min_gb} GB")
    for r in out[:30]:
        print(f"{r['discount']:+.0%} {r['price']:>6.0f}/{r['fair']:<5} {r['storage']:>4}GB {r['cond']:<7} "
              f"bat={r['battery'] or '-':<3} {'D' if r['dealer'] else 'P'} {'S' if r['swap'] else ' '} "
              f"{r['published'][:10]} {r['place'][:22]:<22} {r['id']} {r['title'][:60]}")


if __name__ == "__main__":
    nums = [a for a in sys.argv[1:] if a.isdigit() and int(a) >= 256]
    main(model_arg(sys.argv), int(nums[0]) if nums else 512)

"""Parse data/ps5/raw.json -> data/ps5/listings.json + undervaluation ranking for PS5 / PS5 Pro.

Fair price = median asking price of comparable ads (same model x edition x condition), plus a rough value
for bundled extras (2nd controller, disc drive for digital consoles, VR2). Discount = 1 - price / fair.
"""
import html
import json
import re
import statistics

from config import DATA

OUT = DATA / "ps5"
PRO = r"(ps|playstation|play\s*station)\s*5\s*pro\b|ps5pro|\bpro\s*(konsole|2\s*tb|digital)"
NOT_PRO = r"kein(e)?\s*pro|nicht\s*(die\s*)?pro|slim\s*/\s*pro|&\s*pro|pro\s*(controller|edge)|(ps|playstation)\s*4\s*pro"
# Category also holds accessories, VR, other generations – keep only actual consoles.
OTHER_ITEM = (r"(ps|playstation|play\s*station)\s*[1-4]\b|portable|\bportal\b|lenkrad|\bssd\b|m\.2|standf|ständer|abdeckung|"
              r"cover|faceplate|\bvr\s*2|\bvr2|psvr|edge\s*controller|wolverine|nacon|kontrollor|headset|kopfh[öo]rer|"
              r"beamer|spiele?\b.*\bpro\s*ps5|^\s*(ps5\s*)?(spiele|games)|originalverpackung$|lenkrad|g29")
CONSOLE = r"(ps|playstation|play\s*station)\s*5|konsole"
SLIM = r"\bslim\b"
DIGITAL = r"digital|ohne\s*(disk|disc|laufwerk)|without\s*disc"
DISC = r"disc\s*edition|disk\s*edition|mit\s*(disk|disc|laufwerk)|blu.?ray\s*laufwerk|standard\s*edition|\bdisc\b|\bdisk\b"
DRIVE = r"(disc|disk|blu.?ray)[\s-]*(laufwerk|drive)|laufwerk"
SEALED = r"versiegelt|sealed|unge[öo]ffnet|nagelneu|brand\s*new|originalverpackt\s*(&|und)?\s*neu|neu\s*(&|und)?\s*ovp|\bneu\b.{0,20}\bovp\b|nie\s*(benutzt|ausgepackt)"
LIKENEW = r"wie\s*neu|neuwertig|kaum\s*(benutzt|gespielt|verwendet)|wenig\s*(benutzt|gespielt)|top\s*zustand|keine\s*(kratzer|gebrauchsspuren)|makellos|perfekt"
DEFECT = r"defekt|kaputt|bastler|ersatzteil|f[üu]r\s*teile|kein\s*bild|startet\s*nicht|geht\s*nicht|blue\s*light|bluelight|hdmi.{0,20}(defekt|kaputt|tausch)|überhitz|gesperrt|gebannt|banned|wasserschaden|reparatur"
NOT_SALE = r"\bsuche\b|ankauf|kaufe\s*(ihre|deine|eure)|\bmiete\b|\bvermiet|\bleihe|\bnur\s*(die\s*)?(ovp|verpackung|karton|box|controller|cover|h[üu]lle|st[äa]nder|laufwerk)\b|\bleere?\s*(ovp|verpackung|box)|faceplate|\bcover\b|ständer|halterung|ladestation|skin\b|aufkleber|portal\b|remote\s*player"
SWAP = r"\btausch(e|en)?\b|\bswap\b"
CONTROLLERS = r"(2|zwei|3|drei|4|vier)\s*(x\s*)?(dualsense\s*)?controller|zweite[rn]?\s*controller|zus[äa]tzliche[rn]?\s*controller|extra\s*controller|\+\s*controller|dualsense\s*edge"
VR = r"\bvr\s*2\b|\bvr2\b|psvr\s*2|playstation\s*vr"


def attr(ad, name):
    for a in ad["attributes"]["attribute"]:
        if a["name"] == name:
            return a.get("values") or []
    return []


def text_of(ad):
    desc = " ".join(attr(ad, "DESCRIPTION"))
    desc = html.unescape(re.sub(r"<br\s*/?>", "\n", desc))
    return re.sub(r"<[^>]+>", " ", desc)


def parse(item):
    ad = item["advertDetails"]
    title = ad["description"]
    desc = text_of(ad)
    low, tlow = f"{title}\n{desc}".lower(), title.lower()
    dealer = attr(ad, "ISPRIVATE")[:1] != ["1"]
    check = tlow if dealer else low          # dealer descriptions = boilerplate (repairs, Ankauf, insurance)
    # Model: title first (descriptions often mention "nicht Pro", "wie die Pro" etc.)
    is_pro = lambda s: bool(re.search(PRO, s)) and not re.search(NOT_PRO, s)
    model = "pro" if is_pro(tlow) else "slim" if re.search(SLIM, tlow) else None
    if not model:
        model = "pro" if is_pro(desc.lower()[:300]) else "slim" if re.search(SLIM, low) else "fat"
    # Console = title names a PS5 and isn't an accessory / other-generation item (bundles "PS5 + VR2" stay).
    other_gen = re.search(r"(ps|playstation|play\s*station)\s*[1-4]\b", tlow) and not re.search(r"(ps|playstation|play\s*station)\s*5", tlow)
    console = bool(re.search(CONSOLE, tlow)) and not other_gen and \
        not (re.search(OTHER_ITEM, tlow) and not re.search(r"\+|\bmit\b|inkl|bundle|\bund\b", tlow))
    if model == "pro":
        edition = "digital"                  # Pro ships without drive; drive counted as an extra
    else:
        edition = "digital" if re.search(DIGITAL, tlow) or (not re.search(DISC, tlow) and re.search(DIGITAL, low)) else "disc"
    extras = {
        "drive": edition == "digital" and bool(re.search(DRIVE, low)) and not re.search(r"ohne\s*(disc|disk)?\s*laufwerk", low),
        "controller2": bool(re.search(CONTROLLERS, low)),
        "vr2": bool(re.search(VR, low)),
    }
    cond = "sealed" if re.search(SEALED, low) else "likenew" if re.search(LIKENEW, low) else "used"
    old = attr(ad, "OLD_PRICE")
    addr = ad.get("advertAddressDetails") or {}
    return {
        "id": item["id"], "url": item["url"], "title": title, "price": float((attr(ad, "PRICE") or ["0"])[0]),
        "old_price": float(old[0]) if old else None, "console": console, "model": model, "edition": edition, "cond": cond,
        "extras": [k for k, v in extras.items() if v],
        "games": len(re.findall(r"\bspiele?\b|\bgames?\b", low)) > 0,
        "defect": bool(re.search(DEFECT, check)), "not_sale": bool(re.search(NOT_SALE, check)),
        "swap": bool(re.search(SWAP, check)), "dealer": dealer,
        "company": (attr(ad, "CONTACT/COMPANY") or [None])[0],
        "place": f"{addr.get('postCode', '')} {addr.get('postalName') or addr.get('municipality') or ''}".strip(),
        "published": ad.get("firstPublishedDate"), "changed": ad.get("changedDate"),
        "n_images": len((ad.get("advertImageList") or {}).get("advertImage") or []),
        "desc": desc.strip(),
    }


EXTRA_VALUE = {"drive": 70, "controller2": 40, "vr2": 200}


def main():
    rows = [parse(x) for x in json.loads((OUT / "raw.json").read_text(encoding="utf8"))]
    (OUT / "listings.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf8")
    clean = [r for r in rows if r["console"] and not r["defect"] and not r["not_sale"] and r["price"] >= 150]
    groups = {}
    for r in clean:
        if "vr2" in r["extras"]:
            continue                         # VR bundles distort the base price
        groups.setdefault((r["model"], r["edition"], r["cond"]), []).append(r["price"])
        groups.setdefault((r["model"], r["edition"], "*"), []).append(r["price"])
    print("Median asking price (n):")
    for k in sorted(groups):
        v = groups[k]
        q = statistics.quantiles(v, n=4) if len(v) > 3 else [min(v), 0, max(v)]
        print(f"  {k[0]:<5} {k[1]:<8} {k[2]:<8} n={len(v):>4}  median={statistics.median(v):>5.0f}  p25={q[0]:>5.0f}  p75={q[2]:>5.0f}")
    out = []
    for r in clean:
        g = groups.get((r["model"], r["edition"], r["cond"]), [])
        base = statistics.median(g if len(g) >= 5 else groups[(r["model"], r["edition"], "*")])
        r["fair"] = round(base + sum(EXTRA_VALUE[e] for e in r["extras"]))
        r["discount"] = round(1 - r["price"] / r["fair"], 3)
        out.append(r)
    out.sort(key=lambda r: -r["discount"])
    (OUT / "ranked.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf8")
    print(f"\n{len(rows)} ads, {len(clean)} usable")
    for model in ("pro", "slim", "fat"):
        print(f"\n--- {model} ---")
        for r in [x for x in out if x["model"] == model][:15]:
            print(f"{r['discount']:+.0%} {r['price']:>5.0f}/{r['fair']:<4} {r['edition']:<7} {r['cond']:<7} "
                  f"{','.join(r['extras']):<18} {'D' if r['dealer'] else 'P'}{'S' if r['swap'] else ' '} "
                  f"{r['published'][:10]} {r['place'][:20]:<20} {r['id']} {r['title'][:55]}")


if __name__ == "__main__":
    main()

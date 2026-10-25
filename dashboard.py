"""Step 5 – single self-contained HTML dashboard, sorted by score.

  python dashboard.py [--only-evaluated] [--ids ID ...]   -> dashboard.html
"""
import base64
import html
import io
import json
import sys
from datetime import datetime

from PIL import Image

from config import DATA, EVALS, IMAGES, OFFICE_NAME, PRICE_MAX, PRICE_MIN, ROOT
from prescore import LABELS, WEIGHTS

E = html.escape
THUMB_W = 560
MAX_THUMBS = 8


def thumb_data_uri(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail((THUMB_W, THUMB_W))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pick_photos(L, ev):
    """Floor plan + boiler/AC photos first (if the AI flagged them), then the rest in order."""
    files = sorted((IMAGES / L["id"]).glob("*.jpg")) if (IMAGES / L["id"]).exists() else []
    by_idx = {int(p.stem): p for p in files}
    first = []
    if ev:
        d = ev.get("detected", {})
        for key in ("floorplan_photo", "boiler_seen"):
            v = d.get(key)
            try:
                first.append(int(str(v).strip("#").split(",")[0]))
            except (TypeError, ValueError):
                pass
    order = [i for i in first if i in by_idx] + [i for i in sorted(by_idx) if i not in first]
    notes = {}
    for n in (ev or {}).get("photo_notes", []):
        try:
            notes[int(str(n.get("idx")).strip("#"))] = n.get("what", "")
        except (TypeError, ValueError):
            pass
    return [(i, by_idx[i], notes.get(i, "")) for i in order[:MAX_THUMBS]], len(files)


def merged_scores(L, pre, ev):
    rows = {}
    for k, mx in WEIGHTS.items():
        if ev and k in ev.get("scores", {}):
            s = ev["scores"][k]
            pts = max(0.0, min(float(s.get("points", 0)), mx))
            rows[k] = (pts, mx, s.get("evidence", ""))
        else:
            rows[k] = (pre["points"][k], mx, pre["why"][k])
    return rows


def score_class(v):
    return "hi" if v >= 70 else "mid" if v >= 50 else "lo"


def card(rank, L, pre, ev):
    rows = merged_scores(L, pre, ev)
    total = round(sum(r[0] for r in rows.values()))
    pics, n_photos = pick_photos(L, ev)
    transit = L.get("direct_transit") or []
    walk = L.get("walk_to_office_min")
    green = L.get("green") or {}
    parks = ", ".join(f"{p['name']} ({p['dist_m']} м)" for p in green.get("parks_within_600m", [])[:3]) or "нет в 600 м"

    bars = "".join(
        f'<div class="crit"><div class="crit-h"><span>{E(LABELS[k])}</span><b>{pts:g}<i>/{mx}</i></b></div>'
        f'<div class="bar"><span class="{score_class(100 * pts / mx)}" style="width:{100 * pts / mx:.0f}%"></span></div>'
        f'<p>{E(str(why))}</p></div>'
        for k, (pts, mx, why) in rows.items())

    photos = "".join(
        f'<figure><img loading="lazy" src="{thumb_data_uri(p)}" alt="Фото #{i}"><figcaption>#{i} {E(note)}</figcaption></figure>'
        for i, p, note in pics) or '<p class="muted">Фото не загружены</p>'

    if ev:
        li = lambda xs: "".join(f"<li>{E(str(x))}</li>" for x in xs)
        d = ev.get("detected", {})
        detected = "".join(f"<dt>{E(k)}</dt><dd>{E(str(v))}</dd>" for k, v in [
            ("Этаж", d.get("floor")), ("Ориентация", d.get("orientation")), ("Компас на плане", d.get("compass_on_plan")),
            ("Кондиционер", d.get("aircon")), ("Отопление", d.get("heating_type")), ("Котёл на фото", d.get("boiler_seen")),
            ("Вид из окон", d.get("window_view")), ("Состояние", d.get("condition"))] if v not in (None, ""))
        ai = (f'<div class="verdict">{E(ev.get("verdict", ""))}</div>'
              f'<div class="pc"><div><h4 class="good">Что хорошо</h4><ul>{li(ev.get("pros", []))}</ul></div>'
              f'<div><h4 class="bad">Что плохо</h4><ul>{li(ev.get("cons", []))}</ul></div></div>'
              + (f'<div class="disc"><h4>Расхождения текст ↔ фото</h4><ul>{li(ev["discrepancies"])}</ul></div>' if ev.get("discrepancies") else "")
              + f'<details><summary>Что ИИ распознал</summary><dl>{detected}</dl></details>')
        badge = '<span class="badge ai">ИИ: фото + текст</span>'
    else:
        ai = '<p class="muted">Фото ещё не анализировались ИИ — балл по атрибутам и тексту.</p>'
        badge = '<span class="badge">только текст</span>'

    if L.get("duplicate_of"):
        badge += f'<span class="badge warn">дубликат объявления {E(L["duplicate_of"])}</span>'
    if "reserviert" in (L["title"] or "").lower():
        badge += '<span class="badge warn">reserviert — вероятно, уже занята</span>'
    chips = "".join(f'<span class="chip" title="{E(c["from"])} → {E(c["to"])}">{E(c["line"])} · {c["total_min"]} мин</span>'
                    for c in transit[:4]) or '<span class="chip warn">нет прямой линии</span>'
    meta = " · ".join(x for x in [
        f"{L['rooms']:g} комн." if L.get("rooms") else None,
        f"{L['area_m2']:g} м²" if L.get("area_m2") else None,
        f"этаж {L['floor_raw']}" if L.get("floor_raw") else None,
        L.get("heating"), f"HWB {L['hwb_class']}" if L.get("hwb_class") else None] if x)
    return f"""
<article class="card" data-score="{total}" data-ai="{1 if ev else 0}" data-price="{L['price_total'] or 0}">
  <header>
    <div class="rank">#{rank}</div>
    <div class="ring {score_class(total)}" style="--p:{total}"><span>{total}</span></div>
    <div class="head">
      <h2><a href="{E(L['url'])}" target="_blank" rel="noopener">{E(L['title'])}</a></h2>
      <div class="sub"><b class="price">{L['price_total']:.0f} €</b> <span class="muted">{E(L['price_label'])}</span> ·
        {E(L['district'] or '')}{(' · ' + E(L['street'])) if L.get('street') else ''}</div>
      <div class="sub muted">{E(meta)}</div>
    </div>
    <div class="actions">{badge}<a class="btn" href="{E(L['url'])}" target="_blank" rel="noopener">Открыть на willhaben ↗</a></div>
  </header>
  <div class="commute"><span>🚶 до офиса ~{walk if walk is not None else '?'} мин</span>{chips}{'<span class="chip warn">адрес не указан — гео по центру района, приблизительно</span>' if (L.get('geo_source') or '').startswith('district') else ''}<span class="muted">🌳 {E(parks)}</span></div>
  <div class="photos">{photos}</div>
  {f'<p class="muted small">Показано {len(pics)} из {n_photos} фото</p>' if n_photos > len(pics) else ''}
  <div class="body">
    <section class="scores">{bars}</section>
    <section class="ai">{ai}</section>
  </div>
</article>"""


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#66707d;--line:#e3e6ea;--accent:#2457d6;--good:#1e8a4c;--mid:#c58a00;--bad:#c23b2e;--chip:#eef2fb}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1e24;--fg:#e8eaee;--muted:#9aa3ad;--line:#2b313a;--accent:#7aa2ff;--good:#4cc27e;--mid:#e0b03a;--bad:#ff7a6b;--chip:#232a36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:26px;margin:0 0 4px}.muted{color:var(--muted)}.small{font-size:12px;margin:4px 0 0}
.top{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;align-items:flex-end;margin-bottom:18px}
.legend{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.legend span{background:var(--chip);border-radius:20px;padding:2px 10px;font-size:12px}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.controls label{font-size:13px;color:var(--muted)}select,input{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin:0 0 18px}
.card header{display:grid;grid-template-columns:auto auto 1fr auto;gap:14px;align-items:center}
.rank{font-weight:700;color:var(--muted);font-size:18px}
.ring{--c:var(--good);width:62px;height:62px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--c) calc(var(--p)*1%),var(--line) 0)}
.ring span{background:var(--card);width:48px;height:48px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:19px}
.ring.mid{--c:var(--mid)}.ring.lo{--c:var(--bad)}
h2{font-size:17px;margin:0 0 2px;line-height:1.3}h2 a{color:var(--fg);text-decoration:none}h2 a:hover{color:var(--accent)}
.price{font-size:17px}.sub{font-size:14px}
.actions{display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.btn{background:var(--accent);color:#fff;text-decoration:none;padding:7px 12px;border-radius:8px;font-size:13px;white-space:nowrap}
.badge{font-size:11px;border:1px solid var(--line);border-radius:20px;padding:1px 8px;color:var(--muted)}.badge.ai{border-color:var(--accent);color:var(--accent)}.badge.warn{border-color:var(--bad);color:var(--bad)}
.commute{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0;font-size:13px}
.chip{background:var(--chip);border-radius:20px;padding:2px 10px}.chip.warn{color:var(--bad)}
.photos{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
.photos figure{margin:0;flex:0 0 220px;scroll-snap-align:start}
.photos img{width:220px;height:160px;object-fit:cover;border-radius:8px;display:block;cursor:zoom-in;background:var(--line)}
.photos figcaption{font-size:11px;color:var(--muted);line-height:1.3;margin-top:3px;max-height:4em;overflow:hidden}
.body{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:22px;margin-top:14px}
.crit{margin-bottom:9px}.crit-h{display:flex;justify-content:space-between;font-size:13px}.crit-h i{color:var(--muted);font-style:normal;font-weight:400}
.bar{height:6px;background:var(--line);border-radius:4px;overflow:hidden;margin:3px 0}.bar span{display:block;height:100%}
.bar .hi{background:var(--good)}.bar .mid{background:var(--mid)}.bar .lo{background:var(--bad)}
.crit p{margin:0;font-size:12px;color:var(--muted);line-height:1.35}
.verdict{background:var(--chip);border-radius:10px;padding:10px 12px;margin-bottom:12px;font-weight:500}
.pc{display:grid;grid-template-columns:1fr 1fr;gap:14px}h4{margin:0 0 4px;font-size:14px}h4.good{color:var(--good)}h4.bad{color:var(--bad)}
.ai ul{margin:0;padding-left:18px;font-size:13.5px}.ai li{margin-bottom:3px}
.disc{margin-top:10px}.disc h4{color:var(--mid)}
details{margin-top:10px;font-size:13px}summary{cursor:pointer;color:var(--accent)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:8px 0 0}dt{color:var(--muted)}dd{margin:0}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;place-items:center;z-index:9;cursor:zoom-out}#lb img{max-width:94vw;max-height:90vh;border-radius:8px}
@media (max-width:820px){.card header{grid-template-columns:auto 1fr}.rank{display:none}.actions{grid-column:1/-1;flex-direction:row;align-items:center}.body,.pc{grid-template-columns:1fr}}
"""

JS = """
const lb=document.getElementById('lb'),lbi=lb.querySelector('img');
document.addEventListener('click',e=>{if(e.target.matches('.photos img')){lbi.src=e.target.src;lb.style.display='grid'}else if(e.target.closest('#lb'))lb.style.display='none'});
const cards=[...document.querySelectorAll('.card')],list=document.getElementById('list');
function apply(){const min=+document.getElementById('min').value||0,ai=document.getElementById('ai').checked,s=document.getElementById('sort').value;
 cards.sort((a,b)=>s==='price'?a.dataset.price-b.dataset.price:(b.dataset.ai-a.dataset.ai)||(b.dataset.score-a.dataset.score)).forEach(c=>{list.appendChild(c);c.style.display=(+c.dataset.score>=min&&(!ai||c.dataset.ai==='1'))?'':'none'});}
['min','ai','sort'].forEach(id=>document.getElementById(id).addEventListener('input',apply));
"""


def main():
    args = sys.argv[1:]
    listings = {L["id"]: L for L in json.loads((DATA / "listings.json").read_text(encoding="utf8"))}
    pre = json.loads((DATA / "prescore.json").read_text(encoding="utf8"))
    evals = {p.stem: json.loads(p.read_text(encoding="utf8")) for p in EVALS.glob("*.json")}
    if "--ids" in args:
        ids = args[args.index("--ids") + 1:]
    elif "--only-evaluated" in args:
        ids = list(evals)
    else:
        ids = [i for i, L in listings.items() if L["in_price_range"] and not L["is_project"] and pre[i]["commute_ok"]]
    rows = []
    for i in ids:
        L, ev = listings[i], evals.get(i)
        total = sum(merged_scores(L, pre[i], ev)[k][0] for k in WEIGHTS)
        rows.append((total, L, ev))
    rows.sort(key=lambda r: (r[2] is None, -r[0]))  # photo-analysed first: text-only scores are optimistic guesses
    seen = {}
    for _, L, _ in rows:  # same offer posted twice (identical title + price) -> flag the lower-ranked copy
        key = (L["title"].strip().lower(), L["price_total"])
        if key in seen:
            L["duplicate_of"] = seen[key]
        else:
            seen[key] = L["id"]
    cards = "".join(card(n + 1, L, pre[L["id"]], ev) for n, (_, L, ev) in enumerate(rows))
    legend = "".join(f"<span>{E(LABELS[k])} — {v}</span>" for k, v in WEIGHTS.items())
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Квартиры в Вене — рейтинг</title><style>{CSS}</style></head><body><div class="wrap">
<div class="top"><div><h1>Аренда в Вене: рейтинг квартир</h1>
<div class="muted">willhaben.at · {PRICE_MIN}–{PRICE_MAX} € · 1–2 комнаты · {len(rows)} квартир ({sum(1 for r in rows if r[2])} проанализированы ИИ по фото) ·
офис: {E(OFFICE_NAME)} · сформировано {datetime.now():%d.%m.%Y %H:%M}</div>
<div class="legend">{legend}</div></div>
<div class="controls"><label>Мин. балл <input id="min" type="number" value="0" min="0" max="100" style="width:64px"></label>
<label><input id="ai" type="checkbox"> только с ИИ-анализом фото</label>
<label>Сортировка <select id="sort"><option value="score">по баллу</option><option value="price">по цене</option></select></label></div></div>
<div id="list">{cards}</div></div><div id="lb"><img alt=""></div><script>{JS}</script></body></html>"""
    out = ROOT / "dashboard.html"
    out.write_text(page, encoding="utf8")
    print(f"{len(rows)} cards -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

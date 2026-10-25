"""Step 4 – multimodal AI evaluation (photos + text + attributes + geo facts).

  python evaluate.py prompts [ID ...]   # write data/evals/<id>.prompt.md (for Claude Code sub-agents)
  python evaluate.py api [ID ...]       # call the Claude API directly (needs ANTHROPIC_API_KEY)
  (no IDs -> shortlist)

Each evaluation is saved as data/evals/<id>.json following SCHEMA below.
"""
import base64
import json
import sys
import urllib.request

from config import ANTHROPIC_API_KEY, DATA, EVALS, IMAGES, OFFICE_NAME, VISION_MODEL
from prescore import LABELS, WEIGHTS

RUBRIC = f"""
Ты — эксперт по аренде жилья в Вене. Оцени квартиру для арендатора по 100-балльной шкале.
Используй ВСЁ: атрибуты, полный текст объявления, объективные гео-данные (рассчитаны из GTFS Wiener Linien
и открытых данных Stadt Wien) и КАЖДУЮ фотографию. Сопоставляй фото с текстом и ищи расхождения.

Критерии и максимум баллов:
- floor (макс {WEIGHTS['floor']}): высокий этаж. 6+ или мансарда/DG = максимум; 5 = почти максимум; 4 ≈ половина; ниже — мало.
  Проверь атрибут, текст и вид из окон на фото (видно ли, что высоко: крыши, небо, верхушки деревьев).
- sun (макс {WEIGHTS['sun']}): ориентация. Запад/юго-запад (закат) = максимум, восток = хорошо, юг = средне,
  только север/двор = мало. Ищи в тексте, по освещению на фото и ОБЯЗАТЕЛЬНО стрелку компаса (N/S/O/W) на плане (Grundriss),
  если план есть — определи, куда выходят окна гостиной/спальни.
- aircon (макс {WEIGHTS['aircon']}): стационарный кондиционер (Klimaanlage / Split). Ищи в тексте и внутренние блоки
  сплит-систем под потолком на фото. Мобильный кондиционер — частично. Нет признаков — 0.
- heating (макс {WEIGHTS['heating']}): нужно ИНДИВИДУАЛЬНОЕ отопление — свой газовый котёл (Gasetagenheizung / Therme / Gaskombitherme).
  Fernwärme и Hauszentralheizung/Zentralheizung = 0–2 балла. Ищи котёл (Therme) на фото ванной/кухни/коридора.
  Fußbodenheizung без указания источника — неизвестно (около 4), если на фото не видно котла.
- location (макс {WEIGHTS['location']}): до офиса {OFFICE_NAME} пешком ≤ 60 мин ИЛИ одним видом транспорта без пересадок
  (используй рассчитанные direct_transit — это реальные линии из расписания). Чем быстрее, тем лучше (≤ 25 мин — отлично).
  Учти качество района (6, 7 — отлично; приятные зелёные части 4, 5, 12 — хорошо).
- green (макс {WEIGHTS['green']}): парки/деревья рядом (используй park-данные) + деревья/зелень в виде из окон на фото.
- energy (макс {WEIGHTS['energy']}): класс HWB: A+/A — максимум, B — хорошо, C — средне, D и хуже — мало, не указан — 1–2.
- rooms (макс {WEIGHTS['rooms']}): 2 комнаты — максимум, 1 комната/Garçonnière — около половины. Учти площадь и планировку.

Будь строгим и честным: не придумывай то, чего не видно. Если данных нет — так и скажи и ставь осторожный балл.
Пиши по-русски, кратко и конкретно, ссылайся на номера фото (#0, #1, ...).
"""

SCHEMA = {
    "id": "string",
    "scores": {k: {"points": f"number 0..{v}", "max": v, "evidence": "1-2 предложения: откуда вывод (текст / фото #N / план / гео)"}
               for k, v in WEIGHTS.items()},
    "detected": {
        "floor": "что установлено про этаж", "orientation": "N/NO/O/SO/S/SW/W/NW/неизвестно + источник",
        "floorplan_photo": "номер фото с планом или null", "compass_on_plan": "куда указывает стрелка севера, или null",
        "aircon": "да / нет / мобильный / неизвестно", "heating_type": "тип отопления по итогам анализа",
        "boiler_seen": "номер фото с котлом или null", "window_view": "что видно из окон",
        "condition": "состояние квартиры по фото", "furnished": "мебель / кухня",
    },
    "pros": ["конкретные плюсы (5-8 пунктов)"],
    "cons": ["конкретные минусы и риски (4-8 пунктов)"],
    "discrepancies": ["расхождения текста и фото, сомнительные моменты"],
    "photo_notes": [{"idx": "номер фото", "what": "что на фото и что важно"}],
    "verdict": "2-3 предложения: итог — стоит ли смотреть квартиру и почему",
}


def listing_facts(L):
    keep = ["id", "url", "title", "price_total", "price_label", "price_net", "deposit", "fee", "rooms", "area_m2",
            "floor_raw", "heating", "hwb", "hwb_class", "fgee_class", "features", "free_area", "building_type",
            "condition", "construction_year", "term", "available", "district", "street", "geo_source",
            "office_dist_km", "walk_to_office_min", "direct_transit", "green", "agency"]
    facts = {k: L.get(k) for k in keep}
    facts["image_captions"] = {i: im["caption"] for i, im in enumerate(L["images"]) if im["caption"]}
    return facts


def photos(L):
    d = IMAGES / L["id"]
    return sorted(d.glob("*.jpg")) if d.exists() else []


def build_prompt(L):
    texts = "\n\n".join(f"### {k}\n{v}" for k, v in L["texts"].items() if v)
    return (f"{RUBRIC}\n\n## Факты (атрибуты + гео)\n```json\n{json.dumps(listing_facts(L), ensure_ascii=False, indent=1)}\n```\n\n"
            f"## Полный текст объявления\n{texts}\n\n"
            f"## Ответ\nВерни ТОЛЬКО JSON по схеме (points — числа, сумма не нужна):\n```json\n{json.dumps(SCHEMA, ensure_ascii=False, indent=1)}\n```\n")


def cmd_prompts(listings):
    for L in listings:
        imgs = photos(L)
        body = build_prompt(L) + "\n## Фотографии (файлы, по порядку #0..)\n" + "\n".join(
            f"- #{int(p.stem)}: {p}" for p in imgs)
        (EVALS / f"{L['id']}.prompt.md").write_text(body, encoding="utf8")
        print(f"{L['id']}: {len(imgs)} photos -> data/evals/{L['id']}.prompt.md")


def cmd_api(listings):
    if not ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY not set – use `python evaluate.py prompts` and Claude Code sub-agents instead")
    for L in listings:
        content = []
        for p in photos(L):
            content.append({"type": "text", "text": f"Фото #{int(p.stem)}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(p.read_bytes()).decode()}})
        content.append({"type": "text", "text": build_prompt(L)})
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({"model": VISION_MODEL, "max_tokens": 8000,
                             "messages": [{"role": "user", "content": content}]}).encode(),
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        out = json.load(urllib.request.urlopen(req, timeout=600))
        text = "".join(b.get("text", "") for b in out["content"])
        result = json.loads(text[text.index("{"): text.rindex("}") + 1])
        result["id"], result["model"] = L["id"], VISION_MODEL
        (EVALS / f"{L['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf8")
        print(f"{L['id']}: evaluated")


if __name__ == "__main__":
    mode, ids = sys.argv[1], sys.argv[2:]
    listings = {L["id"]: L for L in json.loads((DATA / "listings.json").read_text(encoding="utf8"))}
    ids = ids or json.loads((DATA / "shortlist.json").read_text())["ids"]
    {"prompts": cmd_prompts, "api": cmd_api}[mode]([listings[i] for i in ids])

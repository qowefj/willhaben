# Willhaben Wien – поиск и ИИ-оценка квартир

Пайплайн: Apify → нормализация + гео (GTFS Wiener Linien, парки Stadt Wien) → предварительный балл →
ИИ-анализ фото и текста (Claude Opus 5.5) → `dashboard.html`.

```bash
python scrape.py deploy                 # один раз: собрать приватный актор willhaben-full в Apify
python scrape.py list                   # все объявления по фильтру (config.SEARCH_URL) + полные карточки
python scrape.py retry                  # повторить заблокированные (403) через residential-прокси
python enrich.py                        # data/listings.json (+ одноразовый кэш GTFS ~1–2 мин)
python prescore.py 40                   # балл по тексту/атрибутам + shortlist топ-40
python scrape.py images --shortlist --resi   # фото shortlist в data/photos/<id>/
python evaluate.py api                  # ИИ-оценка через Claude API (нужен ANTHROPIC_API_KEY в .env)
#   или: python evaluate.py prompts     # промпты для субагентов Claude Code → data/evals/<id>.json
python dashboard.py                     # dashboard.html
```

Заметки
- Willhaben блокирует IP этого компьютера (VPN), поэтому всё скачивание идёт внутри Apify.
- Актор `ePVPxoMoeG0aaGIez` (из задания) отдаёт только данные из выдачи и требует $5 лимита на запуск —
  вместо него используется приватный актор из `apify_actor/`.
- Веса критериев: `prescore.py` → `WEIGHTS`; рубрика для ИИ: `evaluate.py` → `RUBRIC`.
- Время до офиса: пешком (по прямой × 1.3, 4.8 км/ч) и прямые линии без пересадок из расписания GTFS
  (ночные N-линии исключены; Badner Bahn в GTFS Wiener Linien нет).

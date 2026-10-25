"""Shared configuration for the Willhaben Vienna rental pipeline."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
IMAGES = DATA / "photos"
EVALS = DATA / "evals"
for d in (DATA, IMAGES, EVALS):
    d.mkdir(parents=True, exist_ok=True)


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-opus-5-5")

# Actor requested in the brief (pay-per-event, search-card data only). Kept for reference / optional use.
ORIGINAL_ACTOR = "ePVPxoMoeG0aaGIez"
# Private actor deployed from ./apify_actor (full advert details + images).
OWN_ACTOR_NAME = "willhaben-full"

# --- Search filters ---------------------------------------------------------
PRICE_MIN, PRICE_MAX = 700, 900          # Gesamtmiete / Gesamtbelastung EUR per month
ROOMS = (1, 2)                            # 2 preferred
SEARCH_URL = (
    "https://www.willhaben.at/iad/immobilien/mietwohnungen/wien"
    f"?PRICE_FROM={PRICE_MIN}&PRICE_TO={PRICE_MAX}"
    "&NO_OF_ROOMS_BUCKET=1X1&NO_OF_ROOMS_BUCKET=2X2&rows=90"
)

# --- Office ------------------------------------------------------------------
OFFICE_NAME = "Concircle Österreich, Wienerbergstraße 11, 1100 Wien (Vienna Twin Towers)"
OFFICE_LAT, OFFICE_LON = 48.16717, 16.34549   # OSM geocode of Wienerbergstraße 11

# --- Shortlist for the (expensive) photo analysis ---------------------------
SHORTLIST_SIZE = int(os.environ.get("SHORTLIST_SIZE", "40"))
MAX_IMAGES_PER_AD = 24

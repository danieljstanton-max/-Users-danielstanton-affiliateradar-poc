"""Configuration + mock/real mode resolution.

No third-party dependencies: we read a local .env file by hand so the PoC runs
on a bare `python3` with nothing installed.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# DATA_DIR / DB_PATH are env-overridable so production (e.g. Render) can point
# them at a persistent disk. Real environment variables are honoured at import
# time; locally they're unset and we fall back to the repo's ./data.
DATA_DIR = Path(os.environ["AFFSWAP_DATA_DIR"]) if os.environ.get("AFFSWAP_DATA_DIR") else ROOT / "data"
DB_PATH = Path(os.environ["AFFSWAP_DB_PATH"]) if os.environ.get("AFFSWAP_DB_PATH") else DATA_DIR / "affiliateradar.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCREENSHOT_DIR = Path(os.environ["AFFSWAP_SCREENSHOT_DIR"]) if os.environ.get("AFFSWAP_SCREENSHOT_DIR") else DATA_DIR / "screenshots"


def _load_dotenv() -> None:
    """Minimal .env loader (KEY=VALUE lines; # comments; no export/quotes fuss)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        # real environment wins over .env
        os.environ.setdefault(key, val)


_load_dotenv()


# --- DataForSEO -------------------------------------------------------------
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")
DATAFORSEO_BASE = os.environ.get("DATAFORSEO_BASE", "https://api.dataforseo.com")

# --- Screenshot provider ----------------------------------------------------
# provider one of: screenshotone | urlbox | mock
SCREENSHOT_PROVIDER = os.environ.get("SCREENSHOT_PROVIDER", "screenshotone")
SCREENSHOT_KEY = os.environ.get("SCREENSHOT_KEY", "")
SCREENSHOT_SECRET = os.environ.get("SCREENSHOT_SECRET", "")  # urlbox uses key+secret


# --- CometChat (community chat) ---------------------------------------------
# App ID + Region are client-safe. The REST API Key is FULL-ACCESS and must be
# server-only — it is read here (a backend), never shipped to the app.
COMETCHAT_APP_ID = os.environ.get("COMETCHAT_APP_ID", "")
COMETCHAT_REGION = os.environ.get("COMETCHAT_REGION", "eu")
COMETCHAT_REST_API_KEY = os.environ.get("COMETCHAT_REST_API_KEY", "")


# --- Affswap access gate ----------------------------------------------------
# On approval every member gets a free trial with full access. When it expires
# they must hold at least 1 swap to browse site data / stats / reviews / traffic
# — so nobody lurks without being active. Spending your last swap locks browsing
# until you buy or earn another.
TRIAL_HOURS = 48

# --- Affswap plans (swap allowances) ----------------------------------------
# Contacts are swapped, never sold. A "swap" is one contact unlocked via a
# completed mutual trade — each side spends one swap when a match completes.
# swaps=None means unlimited. Top-ups are bought at SWAP_TOPUP_PRICE a go.
SWAP_PLANS = {
    "standard":  {"label": "Standard",  "price": "£0",     "swaps": 2},
    "pro":       {"label": "Pro",       "price": "£29.99", "swaps": 10},
    "unlimited": {"label": "Unlimited", "price": "£79.99", "swaps": None},
}
SWAP_TOPUP_PRICE = "£2.99"

# Early-bird launch pricing on Unlimited: first N seats get the founder
# price, then it flips to the standard Unlimited price for everyone after.
# Counted against every swap_accounts row currently on the unlimited plan.
UNLIMITED_EARLYBIRD_SEATS = 100
UNLIMITED_EARLYBIRD_PRICE = "£49.99"
UNLIMITED_STANDARD_PRICE = "£79.99"

# Review loyalty: this many APPROVED reviews earns 1 free swap (skipped for the
# unlimited plan, which already has infinite swaps).
REVIEWS_PER_SWAP = 5


# --- Google Sheets mirror (affiliate DB + swap log) -------------------------
# The affiliate catalogue and every completed swap are mirrored to a Google
# Sheet through a tiny Apps Script Web App (no Google libraries — we POST JSON
# with urllib). Set BOTH to go LIVE; leave blank to run in MOCK mode, which
# writes local CSVs under data/sheets/ so you can see/import exactly what would
# sync. See docs/google-sheets-integration.md.
SHEETS_WEBAPP_URL = os.environ.get("SHEETS_WEBAPP_URL", "")
SHEETS_SECRET = os.environ.get("SHEETS_SECRET", "")


def sheets_is_live() -> bool:
    return bool(SHEETS_WEBAPP_URL and SHEETS_SECRET)


def dataforseo_is_live() -> bool:
    """Real DataForSEO calls only when BOTH login and password are set."""
    return bool(DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD)


def cometchat_is_live() -> bool:
    """Real CometChat REST calls only when App ID + Region + REST API Key are set."""
    return bool(COMETCHAT_APP_ID and COMETCHAT_REGION and COMETCHAT_REST_API_KEY)


def screenshot_is_live() -> bool:
    # 'free' = keyless PoC capture (thum.io / mShots); paid providers need a key.
    return SCREENSHOT_PROVIDER == "free" or (bool(SCREENSHOT_KEY) and SCREENSHOT_PROVIDER != "mock")


def mode_banner() -> str:
    dfs = "LIVE" if dataforseo_is_live() else "MOCK (fixtures)"
    shot = "LIVE" if screenshot_is_live() else "MOCK (placeholder)"
    cc = "LIVE" if cometchat_is_live() else "MOCK"
    sh = "LIVE" if sheets_is_live() else "MOCK (local CSV)"
    return (f"DataForSEO: {dfs}   |   Screenshots: {shot}   |   "
            f"CometChat: {cc}   |   Sheets: {sh}")

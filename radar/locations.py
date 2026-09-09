"""Geography — data-driven for ALL geos, not a hardcoded shortlist.

Two facts make worldwide coverage a lookup rather than a special case:
  * DataForSEO's country location_code == 2000 + the ISO 3166-1 numeric code.
    So GB (826) -> 2826, US (840) -> 2840, DE (276) -> 2276, and so on for
    every country. No per-country wiring needed.
  * A flag emoji is just the country's two ISO letters as Unicode regional
    indicators, so we can render a flag for any ISO code.

`COUNTRIES` below is the curated worldwide iGaming market set the product ships
with; adding a market is one row. Everything else (codes, flags, reverse
lookups) derives from it.
"""
from __future__ import annotations

# (ISO alpha-2, ISO numeric, display name). Curated worldwide iGaming markets.
COUNTRIES: list[tuple[str, int, str]] = [
    ("GB", 826, "United Kingdom"), ("IE", 372, "Ireland"),
    ("US", 840, "United States"),  ("CA", 124, "Canada"),
    ("DE", 276, "Germany"),        ("AT", 40,  "Austria"),
    ("CH", 756, "Switzerland"),    ("FR", 250, "France"),
    ("ES", 724, "Spain"),          ("PT", 620, "Portugal"),
    ("IT", 380, "Italy"),          ("NL", 528, "Netherlands"),
    ("BE", 56,  "Belgium"),        ("SE", 752, "Sweden"),
    ("NO", 578, "Norway"),         ("FI", 246, "Finland"),
    ("DK", 208, "Denmark"),        ("PL", 616, "Poland"),
    ("GR", 300, "Greece"),         ("RO", 642, "Romania"),
    ("CZ", 203, "Czechia"),        ("HU", 348, "Hungary"),
    ("AU", 36,  "Australia"),      ("NZ", 554, "New Zealand"),
    ("JP", 392, "Japan"),          ("IN", 356, "India"),
    ("ZA", 710, "South Africa"),   ("NG", 566, "Nigeria"),
    ("BR", 76,  "Brazil"),         ("MX", 484, "Mexico"),
    ("AR", 32,  "Argentina"),      ("CL", 152, "Chile"),
    ("CO", 170, "Colombia"),       ("PE", 604, "Peru"),
    ("PH", 608, "Philippines"),    ("ID", 360, "Indonesia"),
    ("TR", 792, "Turkey"),         ("UA", 804, "Ukraine"),
]

_ISO_TO_NUM = {iso: num for iso, num, _ in COUNTRIES}
_ISO_TO_NAME = {iso: name for iso, _, name in COUNTRIES}
_NUM_TO_ISO = {num: iso for iso, num, _ in COUNTRIES}

# Default language per market (real deployments store this per country/vertical).
LANG = {
    "DE": "de", "AT": "de", "CH": "de", "FR": "fr", "ES": "es", "PT": "pt",
    "IT": "it", "NL": "nl", "BE": "nl", "SE": "sv", "NO": "no", "FI": "fi",
    "DK": "da", "PL": "pl", "GR": "el", "RO": "ro", "CZ": "cs", "HU": "hu",
    "JP": "ja", "BR": "pt", "MX": "es", "AR": "es", "CL": "es", "CO": "es",
    "PE": "es", "TR": "tr", "UA": "uk", "ID": "id",
}


def code_for(iso: str) -> int | None:
    """DataForSEO location_code for a country ISO alpha-2 (works for any listed geo)."""
    num = _ISO_TO_NUM.get(iso.upper())
    return None if num is None else 2000 + num


def iso_for(location_code: int) -> str:
    return _NUM_TO_ISO.get(int(location_code) - 2000, str(location_code))


def flag(iso: str) -> str:
    """Flag emoji from ISO alpha-2 — regional-indicator letters. Any country."""
    iso = (iso or "").upper()
    if len(iso) != 2 or not iso.isalpha():
        return "🏳️"
    return chr(0x1F1E6 + ord(iso[0]) - 65) + chr(0x1F1E6 + ord(iso[1]) - 65)


def name(iso: str) -> str:
    return _ISO_TO_NAME.get(iso.upper(), iso)


def language_for(iso: str) -> str:
    return LANG.get(iso.upper(), "en")


def all_isos() -> list[str]:
    return [iso for iso, _, _ in COUNTRIES]


# Backwards-compatible view used by older call sites: {code: (iso, name, flag)}
LOCATIONS = {2000 + num: (iso, nm, flag(iso)) for iso, num, nm in COUNTRIES}

# A small "major markets" set used to enrich per-site traffic origin without
# querying every country on every refresh (see refresh.py).
MAJOR_MARKETS = ["US", "GB", "DE", "CA", "AU", "BR", "IN"]

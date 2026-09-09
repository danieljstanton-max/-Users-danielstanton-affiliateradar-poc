"""Website display names.

Affswap shows a clean website NAME on every list/button (e.g. "CasinoPilot",
"Top Casino Reviews") — the full URL is kept for the click-through profile. The
name is human-owned (`sites.display_name`): an operator can type the exact brand
("Gambling.com", "Livescore"). When none is set we derive a sensible name from
the domain, so the app never shows a raw URL as a label.

This mirrors the `brand()` helper in the front-end preview (screens.html) so the
mock and the real API agree.
"""
from __future__ import annotations

import re

# Known-brand overrides for our example domains (nice casing the deriver can't
# infer from a concatenated stem). In production the human-owned display_name
# in the DB is the real source; this is only a fallback nicety.
_KNOWN = {
    "topcasinoreviews": "Top Casino Reviews", "casinopilot": "CasinoPilot",
    "slotwise": "SlotWise", "reelmentor": "ReelMentor", "oddscompass": "OddsCompass",
    "topbonus": "TopBonus", "spinwise": "SpinWise", "acepunter": "AcePunter",
    "jackpotradar": "JackpotRadar", "slotspilot": "SlotsPilot", "betscout": "BetScout",
    "luckyreview": "LuckyReview", "casinoguide": "CasinoGuide", "bonusradar": "BonusRadar",
    "betmentor": "BetMentor", "spinpalace": "SpinPalace", "oddsguru": "OddsGuru",
    "wagerwise": "WagerWise", "casinoradar": "CasinoRadar", "luckypunter": "LuckyPunter",
    "ghostaffiliate": "Ghost Media", "shadyslots": "ShadySlots",
}
# trailing "-uk" / "-de" market suffixes to drop (the market is shown separately)
_CC = re.compile(r"-(uk|ie|de|se|br|us|ca|au|fr|it|es|pl|jp|nz|za)$", re.I)


def display_name(domain: str, override: str | None = None) -> str:
    """The website name to show on a button. `override` is the human-owned
    display_name (wins when set). Otherwise derive a clean name from the domain."""
    if override:
        return override
    if not domain:
        return ""
    s = re.sub(r"^https?://", "", domain, flags=re.I)
    s = re.sub(r"^www\.", "", s, flags=re.I).split("/")[0]
    s = re.sub(r"\.[a-z.]+$", "", s, flags=re.I)   # drop TLD (.example, .com, .co.uk…)
    s = _CC.sub("", s)                              # drop market suffix
    key = s.replace("-", "").lower()
    if key in _KNOWN:
        return _KNOWN[key]
    return " ".join(w[:1].upper() + w[1:] for w in s.split("-") if w)

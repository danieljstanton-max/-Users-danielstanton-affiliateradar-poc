"""Per-site keyword-profile signals from DataForSEO ranked_keywords.

For each published affiliate we pull the keywords it actually ranks for, then
derive and store (in site_signals):
  * landing_url    — the page that ranks for its top GAMBLING keyword, so the
                     app's 'Visit site' deep-links to the gambling section (e.g.
                     znaki.fm/pl/kasyna/ instead of the news homepage).
  * gambling_pct   — share of traffic from gambling keywords (a quality score).
  * self_brand_pct — share of traffic from its own brand name. An operator (a
                     sportsbook/casino) ranks mostly for itself; an affiliate
                     ranks for many OTHER brands + comparison terms.

Resumable: each domain's raw result is cached to data/kw_cache/<domain>.json, so
a re-run (after a daily spend-limit stop) skips what's already fetched — free.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .locations import code_for, language_for
from .providers.dataforseo import DataForSEOClient, DataForSEOError

# iGaming relevance markers (substring match, lowercased). Casino/slots AND
# sports-betting/tips — because a sports-betting affiliate ranks for team names,
# fixtures and "predictions", not the word "casino".
GAMBLE = (
    # casino / slots / poker
    "casino", "casin", "kasyn", "kasino", "slot", "poker", "bingo", "gambl",
    "spielothek", "spielbank", "ruleta", "roulette", "blackjack", "jackpot",
    "freispiele", "vegas", "spins", "lotto", "baccarat", "bono", "bonus",
    # betting / bookmaker / tips (multilingual)
    "bet", "wett", "odds", "bookmaker", "tipster", "stake", "sportsbook",
    "betting", "wetten", "zaklady", "weddenschap", "apuesta", "aposta", "aposto",
    "scommess", "pari", "parion", "prognos", "pronostic", "predict", "tips",
    "quote", "livescore", "handicap", "accumulator", "acca", "punter",
    # sports the betting affiliates cover
    "football", "soccer", "fussball", "futbol", "futebol", "calcio", "tennis",
    "basketball", " nba", " nfl", " nhl", " mlb", " ufc", "boxing", "cricket",
    "rugby", "hockey", "golf", "formula", " f1", "premier league", "laliga",
    "la liga", "serie a", "bundesliga", "ligue 1", "champions league",
    "world cup", "copa", "horse racing", "greyhound", "darts",
    # Finnish / Nordic
    "vedonly", "vedonlyönti", "rahapel", "uhkapel", "veikka", "nettikasino",
    "kasino", "kolikkopel", "ilmaiskierro", "talletusbonu", "veto",
    # Polish
    "bukmach", "zaklad", "zakład", "kasyno", "ruletka", "automaty", "zdrapki",
    # Croatian / Serbian / Slovenian
    "kladionic", "lutrij", "kladenje", "kockanje", "sportske",
    # Turkish
    "bahis", "iddaa", "kumar", "rulet", "casib", "giris",
    # Romanian
    "cazino", "cazinou", "pacanele", "pacanea", "pariuri", "rotiri", "sloturi",
    "invartiri", "jocuri de noroc", "ruleta",
    # German / Dutch extra
    "spielautomat", "gokkast", "wedden", "goksite",
)
# brand tokens that are really generic iGaming words — skip operator detection
GENERIC_BRAND = {"casino", "casinos", "bet", "bets", "betting", "gambling",
                 "gamble", "slots", "slot", "poker", "bingo", "odds", "wetten",
                 "kasyno", "sportsbook", "apostas", "scommesse", "lotto", "games",
                 "game", "spins", "vegas", "jackpot", "sportwetten", "pronosticos",
                 "pronostico", "wett", "tips", "bookmakers", "betsson"}


def _is_gambling(k: str) -> bool:
    k = " " + k.lower() + " "
    return any(t in k for t in GAMBLE)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# a gambling word IN THE DOMAIN NAME is a strong signal it's a gambling site,
# even when its keyword profile reads low (brand-name or non-Latin keywords).
DOMAIN_GAMBLE = ("casino", "cassino", "kasino", "kasyn", "kasyno", "casin", "gambl",
                 "poker", "bingo", "slot", "roulette", "blackjack", "jackpot",
                 "betting", "sportsbook", "bookmaker", "bookie", "wetten", "wett",
                 "apuesta", "aposta", "scommess", "pronostic", "wager", "spins",
                 "spin", "odds", "vegas", "bahis", "kumar", "bet", "vedonly",
                 "rahapel", "veikka", "bukmach", "zaklad", "kladionic", "lutrij",
                 "iddaa", "casib", "veto", "toto",
                 "cazino", "cazinou", "pacanele", "pariuri")
_GAMBLE_TLD = {"casino", "bet", "poker", "bingo", "lotto", "vegas", "games"}


def _gambling_domain(dom: str) -> bool:
    parts = dom.lower().split(".")
    sld = _norm(parts[0])
    tld = parts[-1]
    return tld in _GAMBLE_TLD or any(t in sld for t in DOMAIN_GAMBLE)


# '*.partners' / '*affiliates*' domains are an operator's affiliate-PROGRAM
# sign-up page (B2B), not an affiliate site — always flag for removal.
_PROGRAM_TOKENS = ("partners", "partner", "affiliates", "affiliate", "partnerprogram",
                   "affiliateprogram", "partnerprogramm", "partenaires", "afiliados",
                   "affiliati", "partnerskie", "partneri")


def _affiliate_program(dom: str) -> bool:
    d = dom.lower()
    tld = d.rsplit(".", 1)[-1]
    # a partner/affiliate TLD (banzai.partners), or the word in the domain body
    return tld in _PROGRAM_TOKENS or any(t in d for t in ("partners", "affiliates",
                                                           "partnerprogram", "affiliateprogram"))


def _derive_signals(dom: str, kws: list) -> tuple:
    """(landing_url, gambling_pct, self_brand_pct, is_operator) from a domain's
    ranked keywords."""
    total = sum(k["etv"] for k in kws) or 0.0
    gtv = sum(k["etv"] for k in kws if _is_gambling(k["keyword"]))
    gambling_pct = round(gtv / total * 100, 1) if total else 0.0
    gk = sorted([k for k in kws if _is_gambling(k["keyword"]) and k.get("url")],
                key=lambda k: k["etv"], reverse=True)
    if gk:
        landing = gk[0]["url"]
    elif kws and kws[0].get("url"):
        landing = kws[0]["url"]
    else:
        landing = f"https://{dom}/"
    brand = _norm(dom.split(".")[0])
    if brand in GENERIC_BRAND or len(brand) < 4:
        self_brand = None
    else:
        stv = sum(k["etv"] for k in kws if brand in _norm(k["keyword"]))
        self_brand = round(stv / total * 100, 1) if total else 0.0
    is_operator = 1 if (self_brand is not None and self_brand >= 55) else 0
    # off-topic = low relevance AND the domain doesn't itself say gambling AND we
    # actually have keyword data. Operators are flagged regardless of domain.
    off_topic = (gambling_pct < 15) and not _gambling_domain(dom) and len(kws) >= 5
    flagged = 1 if (off_topic or is_operator or _affiliate_program(dom)) else 0
    return landing, gambling_pct, self_brand, is_operator, flagged


def rederive_from_cache(conn, cache_dir: str | None = None) -> dict:
    """Recompute site_signals from the cached ranked_keywords — NO API calls.
    Use after tuning the classifier. Preserves the review column."""
    cache = Path(cache_dir) if cache_dir else (config.DATA_DIR / "kw_cache")
    conn.execute("CREATE TABLE IF NOT EXISTS site_signals ("
                 "site_id INTEGER PRIMARY KEY, landing_url TEXT, gambling_pct REAL, "
                 "self_brand_pct REAL, is_operator INTEGER, flagged INTEGER, "
                 "keywords_total INTEGER, analyzed_at TEXT)")
    for col in ("flagged INTEGER", "review TEXT"):
        try:
            conn.execute(f"ALTER TABLE site_signals ADD COLUMN {col}")
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sites = conn.execute("SELECT id, domain FROM sites").fetchall()
    done = 0
    for s in sites:
        cf = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", s["domain"]) + ".json")
        if not cf.exists():
            continue
        kws = json.loads(cf.read_text())
        landing, g, sb, op, fl = _derive_signals(s["domain"], kws)
        conn.execute(
            "INSERT INTO site_signals(site_id, landing_url, gambling_pct, self_brand_pct, "
            "is_operator, flagged, keywords_total, analyzed_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id) DO UPDATE SET landing_url=excluded.landing_url, "
            "gambling_pct=excluded.gambling_pct, self_brand_pct=excluded.self_brand_pct, "
            "is_operator=excluded.is_operator, flagged=excluded.flagged, "
            "keywords_total=excluded.keywords_total, analyzed_at=excluded.analyzed_at",
            (s["id"], landing, g, sb, op, fl, len(kws), now))
        done += 1
    conn.commit()
    return {"rederived": done}


def auto_review_offtopic(conn, cache_dir: str | None = None) -> dict:
    """Auto-Remove the CLEARLY off-topic flagged live sites: very low relevance,
    a non-gambling domain, AND readable Latin keywords that aren't gambling. SPARE
    anything that might be gambling-but-unreadable — non-Latin markets (Japanese,
    Greek, Cyrillic…) or gambling-ish domains — leaving those for a human."""
    from . import ownership
    cache = Path(cache_dir) if cache_dir else (config.DATA_DIR / "kw_cache")
    # extra domain tokens that hint gambling (spare these even at 0% relevance)
    maybe = DOMAIN_GAMBLE + ("casi", "tierce", "pmu", "games", "gaming", "spiel",
                             "luck", "fortune", "jackpot", "spin", "kasyn", "bahis",
                             "kumar", "giris", "casibom", "punt", "wager", "toto")

    def maybe_dom(dom):
        return any(t in _norm(dom.split(".")[0]) for t in maybe)

    def non_latin(kws):
        top = sorted(kws, key=lambda k: k.get("etv", 0), reverse=True)[:5]
        nl = sum(1 for k in top if any(ord(c) > 0x036F for c in (k.get("keyword") or "")))
        return nl >= 2

    rows = conn.execute(
        "SELECT s.id, s.domain FROM site_signals sg JOIN sites s ON s.id=sg.site_id "
        "WHERE s.classification='affiliate' AND sg.flagged=1 "
        "AND (sg.review IS NULL OR sg.review='') AND sg.gambling_pct < 8").fetchall()
    removed, spared = [], []
    for r in rows:
        dom = r["domain"]
        cf = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", dom) + ".json")
        try:
            kws = json.loads(cf.read_text())
        except Exception:
            kws = []
        if _gambling_domain(dom) or maybe_dom(dom) or non_latin(kws):
            spared.append(dom)
            continue
        ownership.set_classification(conn, r["id"], "rejected", admin="auto-review")
        conn.execute("UPDATE site_signals SET review='rejected' WHERE site_id=?", (r["id"],))
        removed.append(dom)
    conn.commit()
    return {"removed": removed, "spared": spared}


def triage_candidates(conn, client: DataForSEOClient | None = None,
                      cache_dir: str | None = None, limit: int = 100,
                      progress=None) -> dict:
    """Auto-decide the SEO-discovery candidate queue using the same keyword-profile
    signals we use for the live catalogue:
      * REJECT  — off-topic (news/gov/general) or operators (not affiliates),
      * APPROVE — clear gambling affiliates (gambling domain, or ≥30% relevance),
      * LEAVE   — anything ambiguous stays a candidate for a human.
    Resumable/cached; writes site_signals so approved ones get deep-links too."""
    from . import ownership
    client = client or DataForSEOClient()
    cache = Path(cache_dir) if cache_dir else (config.DATA_DIR / "kw_cache")
    cache.mkdir(parents=True, exist_ok=True)
    conn.execute("CREATE TABLE IF NOT EXISTS site_signals ("
                 "site_id INTEGER PRIMARY KEY, landing_url TEXT, gambling_pct REAL, "
                 "self_brand_pct REAL, is_operator INTEGER, flagged INTEGER, "
                 "keywords_total INTEGER, analyzed_at TEXT)")
    for col in ("flagged INTEGER", "review TEXT"):
        try:
            conn.execute(f"ALTER TABLE site_signals ADD COLUMN {col}")
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cands = conn.execute("SELECT id, domain, top_country, etv FROM sites "
                         "WHERE classification='candidate' ORDER BY (etv IS NULL), etv DESC").fetchall()
    approved = rejected = left = 0
    for s in cands:
        dom, sid = s["domain"], s["id"]
        cf = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", dom) + ".json")
        if cf.exists():
            kws = json.loads(cf.read_text())
        else:
            iso = s["top_country"] or "US"
            try:
                kws = client.ranked_keywords(dom, code_for(iso) or code_for("US"),
                                             language_for(iso) or "en", limit=limit)
            except DataForSEOError as e:
                conn.commit()
                return {"approved": approved, "rejected": rejected, "left": left,
                        "stopped": str(e)}
            cf.write_text(json.dumps(kws))
        landing, gp, sb, op, flagged = _derive_signals(dom, kws)
        conn.execute(
            "INSERT INTO site_signals(site_id, landing_url, gambling_pct, self_brand_pct, "
            "is_operator, flagged, keywords_total, analyzed_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id) DO UPDATE SET landing_url=excluded.landing_url, "
            "gambling_pct=excluded.gambling_pct, self_brand_pct=excluded.self_brand_pct, "
            "is_operator=excluded.is_operator, flagged=excluded.flagged, "
            "keywords_total=excluded.keywords_total, analyzed_at=excluded.analyzed_at",
            (sid, landing, gp, sb, op, flagged, len(kws), now))
        if flagged:
            ownership.set_classification(conn, sid, "rejected", admin="triage")
            rejected += 1
        elif (_gambling_domain(dom) or gp >= 30) and len(kws) >= 5:
            ownership.set_classification(conn, sid, "affiliate", admin="triage")
            conn.execute("UPDATE sites SET published_at=? WHERE id=? AND published_at IS NULL",
                         (now, sid))
            approved += 1
        else:
            left += 1
        if progress and (approved + rejected + left) % 20 == 0:
            progress(approved + rejected + left, len(cands))
        if (approved + rejected + left) % 50 == 0:
            conn.commit()
    conn.commit()
    return {"candidates": len(cands), "approved": approved, "rejected": rejected, "left": left}


def build_site_signals(conn, client: DataForSEOClient | None = None,
                       cache_dir: str | None = None, limit: int = 100,
                       progress=None) -> dict:
    client = client or DataForSEOClient()
    cache = Path(cache_dir) if cache_dir else (config.DATA_DIR / "kw_cache")
    cache.mkdir(parents=True, exist_ok=True)
    conn.execute("CREATE TABLE IF NOT EXISTS site_signals ("
                 "site_id INTEGER PRIMARY KEY, landing_url TEXT, gambling_pct REAL, "
                 "self_brand_pct REAL, is_operator INTEGER, flagged INTEGER, "
                 "keywords_total INTEGER, analyzed_at TEXT)")
    for col in ("flagged INTEGER", "review TEXT"):
        try:
            conn.execute(f"ALTER TABLE site_signals ADD COLUMN {col}")
        except Exception:
            pass
    sites = conn.execute(
        "SELECT id, domain, top_country FROM sites WHERE classification='affiliate' "
        "ORDER BY etv DESC").fetchall()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done = fetched = 0
    for s in sites:
        dom, sid = s["domain"], s["id"]
        iso = s["top_country"] or "US"
        cf = cache / (re.sub(r"[^A-Za-z0-9._-]", "_", dom) + ".json")
        if cf.exists():
            kws = json.loads(cf.read_text())
        else:
            code = code_for(iso) or code_for("US")
            lang = language_for(iso) or "en"
            try:
                kws = client.ranked_keywords(dom, code, lang, limit=limit)
            except DataForSEOError as e:
                conn.commit()
                return {"sites": len(sites), "analyzed": done, "fetched": fetched,
                        "stopped": str(e)}
            cf.write_text(json.dumps(kws))
            fetched += 1

        landing, gambling_pct, self_brand, is_operator, flagged = _derive_signals(dom, kws)

        conn.execute(
            "INSERT INTO site_signals(site_id, landing_url, gambling_pct, self_brand_pct, "
            "is_operator, flagged, keywords_total, analyzed_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id) DO UPDATE SET landing_url=excluded.landing_url, "
            "gambling_pct=excluded.gambling_pct, self_brand_pct=excluded.self_brand_pct, "
            "is_operator=excluded.is_operator, flagged=excluded.flagged, "
            "keywords_total=excluded.keywords_total, analyzed_at=excluded.analyzed_at",
            (sid, landing, gambling_pct, self_brand, is_operator, flagged, len(kws), now))
        done += 1
        if progress and done % 20 == 0:
            progress(done, len(sites), fetched)
        if done % 50 == 0:
            conn.commit()
    conn.commit()
    return {"sites": len(sites), "analyzed": done, "fetched": fetched}

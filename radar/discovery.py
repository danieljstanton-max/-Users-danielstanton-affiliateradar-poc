"""Discovery pipeline.

For each (country, vertical): run the localised keyword set through the SERP
endpoint, collect and de-duplicate ranking domains into the master list, and
record every appearance as a discovery_hit. New domains enter as 'candidate'
(source='api') for human classification before they can trigger alerts.

Well-known non-affiliate domains (Wikipedia, regulators, operators) are skipped
by an ignore-list so the candidate queue stays clean — but the final
affiliate/not decision is always a human's.
"""
from __future__ import annotations

import re
import sqlite3

from . import keywords, ownership, tagging
from .db import upsert_site, now_iso
from .locations import code_for, iso_for
from .providers.dataforseo import DataForSEOClient

# ---------------------------------------------------------------------------
# Queue hygiene (user rule 2026-08-25): the "New affiliate" queue must surface
# GENUINE affiliate / comparison / review sites ONLY. We drop NOISE outright and
# AUTO-REJECT operators — the real-money brands an affiliate promotes are NOT
# affiliates. Genuine affiliates + true unknowns stay candidates for review.
# Heuristic + curated lists; the human queue is still the backstop.
# ---------------------------------------------------------------------------
NOISE_SUFFIXES = (".gov.uk", ".gov", ".gov.au", ".gouv.fr", ".ac.uk", ".edu", ".news")
NOISE_DOMAINS = {
    # search / platforms / app stores
    "google.com", "google.co.uk", "play.google.com", "apps.apple.com", "apple.com",
    "bing.com", "duckduckgo.com", "microsoft.com", "amazon.com", "amazon.co.uk",
    # review platforms / UGC / social / encyclopaedia
    "trustpilot.com", "uk.trustpilot.com", "reddit.com", "quora.com", "medium.com",
    "facebook.com", "youtube.com", "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "pinterest.com", "pinterest.co.uk", "linkedin.com",
    "en.wikipedia.org", "wikipedia.org",
    # regulators / safer-gambling
    "gamblingcommission.gov.uk", "gamstop.co.uk", "begambleaware.org", "gamcare.org.uk",
    # news / media
    "independent.co.uk", "thesun.co.uk", "mirror.co.uk", "dailymail.co.uk",
    "theguardian.com", "bbc.co.uk", "bbc.com", "telegraph.co.uk", "standard.co.uk",
    "express.co.uk", "metro.co.uk", "talksport.com", "radiotimes.com", "yahoo.com",
    "liverpoolecho.co.uk", "manchestereveningnews.co.uk", "thenationonlineng.net",
    "esportsinsider.com",
    # tools / events / misc
    "online-stopwatch.com", "bingocardcreator.com", "shotgun.live", "eventbrite.co.uk",
    # Canadian + global news / media
    "globalnews.ca", "nationalpost.com", "vancouversun.com", "ottawacitizen.com",
    "calgaryherald.com", "lfpress.com", "torontosun.com", "thestar.com", "ctvnews.ca",
    "cbc.ca", "montrealgazette.com", "edmontonjournal.com", "theglobeandmail.com",
    "hollywoodpq.com", "journaldemontreal.com", "narcity.com", "dailyhive.com",
    "cnn.com", "nytimes.com", "forbes.com", "businessinsider.com",
    "basketusa.com", "mercatolive.fr", "topmercato.com", "sport.fr",
    # Italian sports / news / tech media (rank for gambling terms, not affiliates)
    "gazzetta.it", "corrieredellosport.it", "tuttosport.com", "calciomercato.com",
    "dazn.com", "diretta.it", "aranzulla.it", "fcinter1908.it", "tribuna.com",
    "calcioefinanza.it", "truffa.net", "gioconews.it", "corriere.it", "repubblica.it",
    "ilsole24ore.com", "ansa.it", "fanpage.it", "ilmessaggero.it",
    # German news / tech / sports media + gambling-help forums (not affiliates)
    "imdb.com", "bild.de", "chip.de", "sport.de", "sport1.de", "gutefrage.net",
    "computerbild.de", "augsburger-allgemeine.de", "mopo.de", "blockchainwelt.de",
    "forum-gluecksspielsucht.de", "europeangaming.eu", "spiegel.de", "focus.de",
    "welt.de", "kicker.de", "stern.de", "t-online.de", "sport.bild.de",
    # Spain
    "marca.com", "as.com", "mundodeportivo.com", "sport.es", "elpais.com",
    "elmundo.es", "abc.es", "20minutos.es", "elespanol.com", "larazon.es", "okdiario.com",
    # Netherlands / Belgium
    "telegraaf.nl", "ad.nl", "nu.nl", "nos.nl", "volkskrant.nl", "voetbalprimeur.nl",
    "vi.nl", "hln.be", "nieuwsblad.be", "dhnet.be", "lesoir.be", "rtbf.be",
    "sudinfo.be", "lavenir.net", "voetbalkrant.com", "sporza.be",
    # Nordics
    "aftonbladet.se", "expressen.se", "dn.se", "svd.se", "svt.se", "di.se", "gp.se",
    "omni.se", "iltalehti.fi", "is.fi", "hs.fi", "yle.fi", "mtvuutiset.fi",
    "bt.dk", "ekstrabladet.dk", "dr.dk", "tv2.dk", "politiken.dk", "bold.dk",
    "tipsbladet.dk", "vg.no", "dagbladet.no", "nrk.no", "aftenposten.no",
    "tv2.no", "nettavisen.no",
    # Romania / Portugal / Greece / Poland / Czech
    "gsp.ro", "prosport.ro", "digisport.ro", "libertatea.ro", "fanatik.ro", "adevarul.ro",
    "record.pt", "abola.pt", "ojogo.pt", "publico.pt", "cmjornal.pt", "dn.pt", "sapo.pt",
    "sport24.gr", "gazzetta.gr", "sdna.gr", "in.gr", "protothema.gr", "newsit.gr",
    "sport.pl", "wp.pl", "onet.pl", "interia.pl", "meczyki.pl", "weszlo.com",
    "sport.cz", "idnes.cz", "novinky.cz", "seznamzpravy.cz", "blesk.cz", "denik.cz",
    # Switzerland / Austria / France
    "20min.ch", "blick.ch", "nzz.ch", "tagesanzeiger.ch", "watson.ch", "rts.ch",
    "srf.ch", "letemps.ch", "24heures.ch", "krone.at", "oe24.at", "derstandard.at",
    "kurier.at", "laola1.at", "kleinezeitung.at", "heute.at",
    "lequipe.fr", "lefigaro.fr", "lemonde.fr", "leparisien.fr", "football365.fr",
    "foot01.com", "20minutes.fr", "sofoot.com", "actu.fr",
    # media / forums / non-gambling surfaced by the bulk EU sweep
    "onefootball.com", "tvmatchen.nu", "suomi24.fi", "suomifutis.com", "pelit.com",
    "tvsporten.dk", "esports.gg", "antena3.ro", "jn.pt", "noticiasaominuto.com",
    "portaldaqueixa.com", "lance.com.br", "gazetaesportiva.com", "metropoles.com",
    "businessinsider.com.pl", "thesun.ie", "irishracing.com", "toffeeweb.com",
    "thecelticstar.com", "tenis-zive.cz", "fotbalzpravy.cz", "fotbalportal.cz",
    "itnetwork.cz", "swissinfo.ch", "gutegutscheine.ch", "latele.ch", "ligaportal.at",
    "sport-oesterreich.at", "grazia.fr", "auto-moto.com", "consoglobe.com",
    "gamalive.com", "ilboursa.com", "egamersworld.com", "mundiario.com",
    "cryptonews.com", "sabiasque.pt", "mesterbold.dk", "ottelut.com", "agones.gr",
    "livesport.cz", "observador.pt", "admin.ch", "meinbezirk.at", "esportnow.pl",
    "planetagracza.pl", "zagranie.com", "lubsport.pl", "tietarteve.com", "estafa.info",
    "przegladsportowy.onet.pl", "ruik.cz", "warda.at", "grheute.ch",
    # travel / listings / regulators / responsible-gambling
    "destinationontario.com", "yelp.com", "yelp.ca",
    "agco.ca", "igamingontario.ca", "rg.org", "connexontario.ca", "responsiblegambling.org",
}

# market-agnostic platform / sports-data / social noise (matched as substrings so
# per-country variants like ca.trustpilot.com and tripadvisor.ca are all caught)
_NOISE_SUBSTR = (
    "trustpilot", "tripadvisor", "wikipedia", "reddit", "quora", "medium.com",
    "livescore", "flashscore", "sofascore", "whoscored", "eurosport", "transfermarkt",
    "rotowire", "dailyfaceoff", "topendsports", "bleacherreport", "sportingnews",
    "goal.com", "espn", "sofifa",
    # sports / esports / video-game media + CDN / site-builders (not affiliates)
    "tntsports", "foxsports", "cbssports", "skysports", "nbcsports", "hltv",
    "squawka", "gamereactor", "walkingfootball", "amazonaws", "cloudfront",
    "blogspot", "wordpress", "wixsite", "weebly", "sportbible", "givemesport",
    "bild.de", "gutefrage", "computerbild", "imdb.", "blockchainwelt", "spielsucht",
)

# Real-money OPERATOR brands (seed — grows as operators confirm rejections).
OPERATOR_DOMAINS = {
    "foxybingo.com", "galabingo.com", "meccabingo.com", "buzzbingo.com",
    "sunbingo.co.uk", "heartbingo.co.uk", "skybingo.com", "kittybingo.com",
    "winkbingo.com", "fabulousbingo.co.uk", "majesticbingo.com", "mirrorbingo.com",
    "spingenie.com", "playtopia.com", "bingoblitz.com", "bingocash.com",
    "takeabreakbingo.co.uk", "slingo.com", "mrq.com", "admiralcasino.co.uk",
    "tombola.co.uk", "jackpotjoy.com", "costabingo.com", "virginbingo.com",
    "paddypower.com", "betfair.com", "williamhill.com", "bet365.com", "888.com",
    "888bingo.com", "ladbrokes.com", "coral.co.uk", "unibet.co.uk", "betfred.com",
    "skyvegas.com",
    # Canada lottery + operators
    "olg.ca", "playnow.com", "espacejeux.com", "alc.ca", "lotoquebec.com",
    "betrivers.ca", "pointsbet.ca", "northstarbets.ca", "proline.ca",
    # global sportsbooks / casinos / poker (brand domains without a gambling word)
    "betmgm.com", "betmgm.ca", "caesars.com", "fanduel.com", "fanduel.ca",
    "draftkings.com", "pokerstars.com", "pokerstars.ca", "partypoker.com",
    "partycasino.com", "888poker.com", "888casino.com", "ggpoker.com", "ggpoker.ca",
    "leovegas.com", "betvictor.com", "playojo.com", "tonybet.com", "powerplay.com",
    "sportsinteraction.com", "spincasino.com", "jackpotcity.com", "betway.com",
    "betway.ca", "unibet.com", "unibet.ca", "bwin.com", "bet365.com", "bet365.ca",
    "betano.ca", "casumo.com", "rivalry.com", "stake.com",
    # short/ambiguous EU operator brands (exact domains / subdomains)
    "toto.nl", "sts.pl", "chance.cz", "winner.ro", "paf.com", "paf.es", "opap.gr",
    "fdj.fr", "pmu.fr", "vbet.com", "casino.at", "jackpots.ch", "svenskaspel.se",
    "atg.se",
}

# Affiliate/comparison signals in the domain stem — these WIN over the operator
# heuristic (a "bingo" domain that also says which/best/compare is an affiliate).
_AFFILIATE_HINTS = (
    # English
    "which", "compar", "review", "rating", "best", "toprated", "toplist", "guide",
    "list", "sites", "casinos", "bonus", "expert", "tips", "insider", "finder",
    "picks", "deals", "offers", "free", "daily", "hub", "affiliat", "gambling",
    "oddschecker", "askgamblers", "loquax", "vso", "vegasslots", "mag",
    # "best" across EU languages (de/nl/sv/da/no/fi/es/it/pt/fr/pl/cz/hr/ro)
    "beste", "basta", "bästa", "bedste", "parhaat", "mejor", "migliori", "miglior",
    "melhores", "meilleur", "najlepsz", "nejlep", "najbolj", "bune",
    # "review / compare / test" across EU languages
    "recens", "resen", "reseñas", "anmeldelser", "arvostelut", "avalia", "analis",
    "análises", "recenz", "kritik", "avis", "erfahrung", "vergleich", "jamfor",
    "jämför", "test", "topp",
    # local "casinos" plurals — list/comparison signals
    "casinon", "kasinot", "kasyna", "cazinouri", "casina", "kasina",
    "nettcasino", "nettikasino",
    # local "guide / legal / safe / licensed-list" signals
    "guid", "legal", "sicur", "italian", "aams", "seguro", "fiable", "serios",
    "licens", "licenc", "vergunning",
)
# well-known AFFILIATE brands whose names carry no generic signal — protect them
_AFFILIATE_DOMAINS = {
    "casinomeister.com", "thepogg.com", "casino.guru", "casinoguru.com",
    "casino.org", "casinos.com", "onlinecasinos.com", "casinocity.com",
    "latestcasinobonuses.com", "bettingexpert.com", "gamblingsites.com",
    "wizardofodds.com", "vegasslotsonline.com", "casinogrounds.com",
    "fruityslots.com", "casinolyze.co.uk", "assopoker.com", "ammazzacasino.com",
    "casinoguru-it.com",
}
# distinctive OPERATOR brand tokens — checked as substrings (AFTER affiliate hints,
# so "unibetreview.com" stays an affiliate) to catch every TLD variant of a brand
# (bwin.fr, 1xbet.ca, netbet.fr…) without listing them all.
_OPERATOR_SUBSTR = (
    "1xbet", "betsson", "leovegas", "jackpotcity", "pokerstars", "draftkings",
    "fanduel", "betmgm", "pointsbet", "betrivers", "partypoker", "partycasino",
    "ggpoker", "casumo", "betway", "unibet", "bwin", "tonybet", "betsafe",
    "betiton", "netbet", "betvictor", "playojo", "spincasino", "betano",
    "rivalry", "sportsinteraction", "betfirst", "williamhill", "ladbrokes",
    # pan-European + Nordic/DACH/South-EU operator brands
    "mrgreen", "svenskaspel", "casinocosmopol", "comeon", "nordicbet", "grosvenor",
    "virgingames", "virgincasino", "betfred", "paddypower", "betfair", "skybet",
    "skyvegas", "genting", "32red", "mansioncasino", "tipico", "interwetten",
    "betathome", "bet-at-home", "novibet", "stoiximan", "dafabet", "videoslots",
    "redbet", "cherrycasino", "wunderino", "drueckglueck", "sunmaker", "admiralbet",
    "fortuna", "tipsport", "superbet", "efbet", "ninjacasino", "speedycasino",
    "hajper", "mariacasino", "pinnacle", "marathonbet", "parimatch", "mostbet",
    "sazka", "betclic", "winamax", "betuk", "betgoodwin", "fabulousvegas",
    # Italian operators
    "sisal", "goldbet", "lottomatica", "eurobet", "starvegas", "starcasino",
    "giocodigitale", "betflag", "planetwin", "begame", "snai.it",
    # German operators
    "stargames", "novoline", "novomatic", "oddset", "888sport", "gauselmann",
    "jackpotpiraten", "merkurbets",
    # Spain
    "codere", "luckia", "sportium", "retabet", "kirolbet", "botemania", "wanabet",
    "marcaapuestas", "casinobarcelona", "granmadrid", "yobingo", "versus.es",
    # Netherlands / Belgium
    "hollandcasino", "jackscasino", "betcity", "fairplay", "bingoal", "kansino",
    "onecasino", "napoleon", "goldenpalace", "circus.be", "circus.nl", "711.be",
    # Nordics (SE/FI/DK/NO)
    "miljonlotteriet", "veikkaus", "danskespil", "tivolicasino", "royalcasino",
    "spillehallen", "norsktipping", "rikstoto", "karjala", "suomiautomaatti",
    "kolikkopelit", "bet25",
    # Romania / Portugal / Greece / Poland / Czech
    "casapariurilor", "publicwin", "princesscasino", "maxbet", "getsbet",
    "placard", "solverde", "casinoportugal", "nossaaposta", "bacanaplay",
    "pamestoixima", "winmasters", "sportingbet", "casinoloutraki", "fonbet",
    "totalcasino", "betfan", "forbet", "pzbuk", "synottip", "sazkabet",
    # Switzerland / Austria / France
    "swisscasinos", "mycasino", "casino777", "casinobarriere", "grandcasino",
    "win2day", "parionssport", "genybet", "zebet",
    # more EU operators surfaced by the bulk sweep
    "expekt", "allwyn", "swisslos", "lottoland", "estorilsol", "scooore", "pasino",
    "7melons", "foxbet", "bethome", "betmarket", "kingbet", "footballbet",
    "tostoixima", "stoiximatora", "jokerbet", "lebull", "betnation", "jocpacanele",
    "lvbet", "etoto", "fuksiarz", "admiral", "goldenvegas", "betmaster", "kingsbet",
    "bet365", "luckyseven", "loro.ch", "jacks.nl", "888.ro",
)
# a brandy real-money-gambling domain (with no affiliate signal) reads as operator.
# NB: "betting" is deliberately NOT here — it appears in comparison affiliates too
# (e.g. canadasportsbetting.ca); betting operators are caught by the blocklist.
_OPERATOR_RE = re.compile(
    r"(bingo|casino|slots?|slingo|poker|spins?|jackpot|wager|roulette|blackjack)")

# UNLICENSED / self-exclusion-bypass rule (user, 2026-08-25, extended EU 2026-09):
# sites built around operators WITHOUT a local licence / that let players bypass the
# national self-exclusion scheme are a compliance risk everywhere. Reject them even
# when affiliate-shaped (this runs BEFORE the affiliate-signal check). Per market:
#   UK GamStop · SE Spelpaus · DE OASIS · DK ROFUS · NL CRUKS · NO/FI/IT/ES/PT/CZ/PL
_UNLICENSED_RE = re.compile(
    r"(non|not|without|beyond|off|sans)-?gam"           # UK: not-on-gamstop
    r"|gamstop-?(free|alt|altern)"
    r"|utan-?(svensk-?licens|spelpaus|licens|konto)"    # SE  (utan svensk licens / spelpaus)
    r"|ohne-?(lizenz|oasis|limit)"                      # DE  (ohne lizenz / oasis)
    r"|uden-?(rofus|dansk-?licens|licens)"              # DK  (uden om rofus)
    r"|zonder-?(cruks|vergunning)"                      # NL  (zonder cruks)
    r"|uten-?(norsk-?lisens|lisens)"                    # NO
    r"|ilman-?(rekister|lisenssi)"                      # FI
    r"|senza-?licenza|non-?aams|senza-?adm"             # IT  (senza licenza / non AAMS)
    r"|sin-?licencia"                                   # ES
    r"|sem-?licen(c|ç)a"                                # PT
    r"|bez-?licence|bez-?licencji",                     # CZ / PL
    re.I)


def queue_verdict(domain: str) -> str:
    """'noise' -> drop · 'unlicensed' -> auto-reject (grey-market / self-exclusion
    bypass) · 'operator' -> auto-reject · 'affiliate' -> keep as candidate (also the
    default for genuine unknowns, so nothing real is lost)."""
    d = (domain or "").lower()
    if d.startswith("www."):
        d = d[4:]
    if not d:
        return "noise"
    if d in NOISE_DOMAINS or any(d.endswith(sfx) for sfx in NOISE_SUFFIXES):
        return "noise"
    if any(sub in d for sub in _NOISE_SUBSTR):     # platform / sports-data / social
        return "noise"
    if _UNLICENSED_RE.search(d):                   # compliance — before affiliate check
        return "unlicensed"
    if d in _AFFILIATE_DOMAINS:                     # known major affiliate brand
        return "affiliate"
    if any(h in d for h in _AFFILIATE_HINTS):     # affiliate signal (multilingual) wins
        return "affiliate"
    if d in OPERATOR_DOMAINS or any(d.endswith("." + op) for op in OPERATOR_DOMAINS):
        return "operator"
    if any(op in d for op in _OPERATOR_SUBSTR):    # operator brand token, any TLD
        return "operator"
    _parts = d.split(".")                          # operator product subdomain
    if len(_parts) >= 3 and _parts[0] in (
            "sportsbook", "casino", "bingo", "poker", "slots", "betting"):
        return "operator"
    if _OPERATOR_RE.search(d):                     # brandy gambling domain
        return "operator"
    return "affiliate"


def _is_ignorable(domain: str) -> bool:
    """Only NOISE is skipped outright at ingest; operators are still ingested but
    auto-rejected (audit trail + recoverable if mis-called)."""
    return queue_verdict(domain) == "noise"


def discover(conn: sqlite3.Connection, country: str, vertical: str,
             client: DataForSEOClient | None = None) -> dict:
    """Run discovery for one (country, vertical). Returns a summary dict."""
    client = client or DataForSEOClient()
    location_code = code_for(country)
    if location_code is None:
        raise ValueError(f"unknown country: {country}")

    kw_set = keywords.keywords_for(country, vertical)
    if not kw_set:
        return {"country": country, "vertical": vertical, "keywords": 0,
                "domains": 0, "new_candidates": 0, "hits": 0}

    seen_domains: set[str] = set()
    new_candidates = 0
    rejected_ops = 0
    hit_count = 0

    for keyword, language in kw_set:
        items = client.serp_organic(keyword, location_code, language)
        for it in items:
            domain = (it.get("domain") or "").lower()
            verdict = queue_verdict(domain)
            if verdict == "noise":
                continue

            existed = conn.execute("SELECT id FROM sites WHERE domain = ?",
                                   (domain,)).fetchone()
            site_id = upsert_site(conn, domain, source="api")
            if not existed:
                new_candidates += 1
                if verdict in ("operator", "unlicensed"):
                    # operators are real-money brands (not affiliates); unlicensed
                    # sites promote grey-market brands / self-exclusion bypass — reject
                    # on sight so the queue stays clean (recoverable in Rejected)
                    ownership.set_classification(conn, site_id, "rejected",
                                                 admin=f"discovery:auto-{verdict}")
                    rejected_ops += 1
            seen_domains.add(domain)

            # record the SERP appearance (evidence trail)
            conn.execute(
                """INSERT INTO discovery_hits
                   (site_id, keyword, country, language, vertical, rank_absolute, seen_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (site_id, keyword, country.upper(), language, vertical,
                 it.get("rank_absolute"), now_iso()),
            )
            hit_count += 1

            # vertical is discovery-derived (machine-owned); add_auto_vertical
            # skips any site an operator has overridden in the back office.
            tagging.add_auto_vertical(conn, site_id, vertical)

            # best SERP rank is API-owned; safe to track here at ingest
            rank = it.get("rank_absolute")
            if rank is not None:
                cur = conn.execute("SELECT rank_best FROM sites WHERE id = ?",
                                   (site_id,)).fetchone()["rank_best"]
                if cur is None or rank < cur:
                    conn.execute("UPDATE sites SET rank_best = ? WHERE id = ?",
                                 (rank, site_id))

    conn.commit()
    return {"country": country.upper(), "vertical": vertical,
            "keywords": len(kw_set), "domains": len(seen_domains),
            "new_candidates": new_candidates, "auto_rejected_operators": rejected_ops,
            "hits": hit_count}

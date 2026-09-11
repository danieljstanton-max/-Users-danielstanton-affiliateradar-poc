"""Per-(country, vertical) keyword dictionaries — worldwide.

From the brief: use REAL local terms, not literal translations, curated per
(country, vertical) and refined over time. Hand-curated dictionaries exist for
the launch markets below; every other geo falls back to a generated English
seed so the pipeline RUNS worldwide from day one, flagged as `seed` so the back
office knows it still needs a native-language pass.
"""
from __future__ import annotations

from . import locations

VERTICALS = ("casino", "sportsbook", "bingo", "poker")


def _kw(lang: str, *terms: str) -> list[tuple[str, str]]:
    """Compact helper: attach one language to many keywords."""
    return [(t, lang) for t in terms]


# Hand-curated launch markets. keyed by (ISO, vertical) -> [(keyword, lang)].
CURATED: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("GB", "bingo"): _kw("en",
        "best online bingo uk", "online bingo rooms", "online bingo bonus",
        "best online bingo", "new bingo sites uk", "best bingo sites uk",
        "free bingo no deposit", "bingo welcome offer", "best bingo bonuses uk",
        "online bingo real money", "new bingo sites 2025",
        "bingo sites with free spins", "mobile bingo apps uk", "slingo sites uk",
        "90 ball bingo online",
        # --- bigger UK bingo search (affiliate/comparison intents) ---
        "bingo sites not on gamstop", "bingo sites with free spins on registration",
        "best new bingo sites", "top bingo sites uk", "bingo no wagering",
        "£5 deposit bingo sites", "free bingo no deposit no card details",
        "75 ball bingo sites", "bingo sites reviews", "safe bingo sites uk",
        "bingo comparison uk", "best slingo sites"),
    ("GB", "poker"): _kw("en",
        "best poker sites uk", "best online poker uk", "online poker real money uk",
        "poker sites uk", "top poker sites uk", "best poker rooms",
        "poker sign up bonus", "poker welcome bonus uk", "poker no deposit bonus",
        "poker rakeback deals", "best rakeback poker sites", "poker freeroll passwords",
        "texas holdem online real money", "online poker reviews", "poker room reviews",
        "poker strategy", "how to play poker online", "fastest payout poker sites",
        "poker sites not on gamstop", "new poker sites 2025",
        "poker deposit bonus uk", "safe online poker uk"),
    ("CA", "casino"): [("best online casino canada", "en"), ("canadian online casino", "en"),
                       ("new casino sites canada", "en"), ("online casino ontario", "en"),
                       ("best casino ontario", "en"), ("online casino real money canada", "en"),
                       ("casino en ligne", "fr"), ("meilleur casino en ligne", "fr"),
                       ("casino en ligne québec", "fr"), ("casino en ligne argent réel", "fr")],
    ("CA", "sportsbook"): [("best betting sites canada", "en"), ("sports betting canada", "en"),
                           ("ontario sports betting", "en"), ("best sportsbook ontario", "en"),
                           ("paris sportifs en ligne", "fr"), ("meilleur site de paris sportifs", "fr")],
    ("AU", "casino"): [("best online casino australia", "en"), ("pokies online", "en")],
    ("NZ", "casino"): [("best online casino nz", "en")],
    ("BR", "sportsbook"): [("melhores casas de apostas", "pt"), ("bônus de aposta", "pt")],
}

# ---------------------------------------------------------------------------
# EUROPE ROLLOUT — native keyword sets per market (operator-provided, 2026-09).
# Casino + sportsbook per market, in the local language(s). Merged into CURATED
# below (overriding older seeds). Bilingual/trilingual markets (BE, CH) carry all
# their languages in one set. NOTE: France online casino is prohibited — its
# casino terms surface grey-market sites, which the unlicensed filter rejects.
# ---------------------------------------------------------------------------
_EU: dict[str, dict[str, list[tuple[str, str]]]] = {
    "GB": {
        "casino": _kw("en", "best online casinos", "online casino reviews", "best casino sites",
                      "new online casinos", "online casinos 2026", "casino bonuses",
                      "no deposit casino bonus", "fast payout casinos"),
        "sportsbook": _kw("en", "best betting sites", "sportsbook reviews", "best sportsbooks",
                          "online sports betting"),
    },
    "IT": {
        "casino": _kw("it", "migliori casinò online", "recensioni casinò online", "migliori siti casinò",
                      "nuovi casinò online", "casinò online 2026", "bonus casinò",
                      "bonus casinò senza deposito", "casinò con prelievi veloci"),
        "sportsbook": _kw("it", "migliori siti scommesse", "recensioni siti scommesse",
                          "migliori bookmaker", "scommesse sportive online"),
    },
    "DE": {
        "casino": _kw("de", "beste Online Casinos", "Online Casino Test", "Online Casino Erfahrungen",
                      "beste Casino Seiten", "neue Online Casinos", "Online Casinos 2026",
                      "Casino Bonus ohne Einzahlung", "Casinos mit schneller Auszahlung"),
        "sportsbook": _kw("de", "beste Wettanbieter", "Sportwetten Anbieter Test",
                          "beste Sportwetten Anbieter", "Online Sportwetten"),
    },
    "ES": {
        "casino": _kw("es", "mejores casinos online", "reseñas de casinos online",
                      "mejores páginas de casino", "nuevos casinos online", "casinos online 2026",
                      "bonos de casino", "bono de casino sin depósito", "casinos con pagos rápidos"),
        "sportsbook": _kw("es", "mejores casas de apuestas", "reseñas de casas de apuestas",
                          "mejores casas de apuestas deportivas", "apuestas deportivas online"),
    },
    "NL": {
        "casino": _kw("nl", "beste online casino's", "online casino reviews", "beste casinosites",
                      "nieuwe online casino's", "online casino's 2026", "casino bonus",
                      "casino bonus zonder storting", "casino's met snelle uitbetaling"),
        "sportsbook": _kw("nl", "beste goksites", "beste bookmakers", "bookmaker reviews",
                          "online sportweddenschappen"),
    },
    "SE": {
        "casino": _kw("sv", "bästa online casinon", "online casino recensioner", "bästa casinosajter",
                      "nya online casinon", "online casinon 2026", "casino bonus",
                      "casino bonus utan insättning", "casinon med snabba uttag"),
        "sportsbook": _kw("sv", "bästa bettingsidor", "recensioner av bettingsidor",
                          "bästa spelbolag", "betting online"),
    },
    "FI": {
        "casino": _kw("fi", "parhaat nettikasinot", "nettikasino arvostelut", "parhaat kasinosivustot",
                      "uudet nettikasinot", "nettikasinot 2026", "kasinobonus",
                      "kasinobonus ilman talletusta", "nopean kotiutuksen kasinot"),
        "sportsbook": _kw("fi", "parhaat vedonlyöntisivustot", "vedonlyöntisivustojen arvostelut",
                          "parhaat vedonlyöntiyhtiöt", "urheiluvedonlyönti netissä"),
    },
    "DK": {
        "casino": _kw("da", "bedste online casinoer", "online casino anmeldelser", "bedste casinosider",
                      "nye online casinoer", "online casinoer 2026", "casino bonus",
                      "casino bonus uden indbetaling", "casinoer med hurtig udbetaling"),
        "sportsbook": _kw("da", "bedste bettingsider", "anmeldelser af bettingsider",
                          "bedste bookmakere", "sportsbetting online"),
    },
    "RO": {
        "casino": _kw("ro", "cele mai bune cazinouri online", "recenzii cazinouri online",
                      "cele mai bune site-uri de cazinouri", "cazinouri online noi",
                      "cazinouri online 2026", "bonus cazino", "bonus cazino fără depunere",
                      "cazinouri cu retrageri rapide"),
        "sportsbook": _kw("ro", "cele mai bune site-uri de pariuri", "recenzii case de pariuri",
                          "cele mai bune case de pariuri", "pariuri sportive online"),
    },
    "PT": {
        "casino": _kw("pt", "melhores casinos online", "avaliações de casinos online",
                      "melhores sites de casino", "novos casinos online", "casinos online 2026",
                      "bónus de casino", "bónus de casino sem depósito", "casinos com levantamentos rápidos"),
        "sportsbook": _kw("pt", "melhores casas de apostas", "análises de casas de apostas",
                          "melhores sites de apostas", "apostas desportivas online"),
    },
    "GR": {
        "casino": _kw("el", "καλύτερα online καζίνο", "κριτικές online καζίνο",
                      "καλύτερες ιστοσελίδες καζίνο", "νέα online καζίνο", "online καζίνο 2026",
                      "μπόνους καζίνο", "μπόνους καζίνο χωρίς κατάθεση", "καζίνο με γρήγορες αναλήψεις"),
        "sportsbook": _kw("el", "καλύτερες στοιχηματικές εταιρείες", "κριτικές στοιχηματικών εταιρειών",
                          "καλύτερα στοιχηματικά sites", "online αθλητικό στοίχημα"),
    },
    "PL": {
        "casino": _kw("pl", "najlepsze kasyna online", "recenzje kasyn online", "najlepsze strony kasynowe",
                      "nowe kasyna online", "kasyna online 2026", "bonus kasynowy",
                      "bonus bez depozytu", "kasyna z szybkimi wypłatami"),
        "sportsbook": _kw("pl", "najlepsi bukmacherzy", "recenzje bukmacherów",
                          "najlepsze strony bukmacherskie", "zakłady sportowe online"),
    },
    "IE": {
        "casino": _kw("en", "best online casinos Ireland", "Irish casino reviews",
                      "best casino sites Ireland", "new online casinos Ireland",
                      "online casinos Ireland 2026", "casino bonuses Ireland", "fast payout casinos Ireland"),
        "sportsbook": _kw("en", "best betting sites Ireland", "Irish betting site reviews",
                          "best bookmakers Ireland", "sports betting Ireland", "free bets Ireland"),
    },
    "CZ": {
        "casino": _kw("cs", "nejlepší online kasina", "recenze online kasin", "nejlepší casino stránky",
                      "nová online kasina", "online kasina 2026", "casino bonus",
                      "casino bonus bez vkladu", "kasina s rychlými výběry"),
        "sportsbook": _kw("cs", "nejlepší sázkové kanceláře", "recenze sázkových kanceláří",
                          "nejlepší sázkové stránky", "online sportovní sázení"),
    },
    "BE": {  # bilingual: Dutch + French
        "casino": _kw("nl", "beste online casino's België", "online casino reviews België",
                      "beste casinosites België", "nieuwe online casino's België",
                      "online casino België 2026", "casino bonus België", "casino zonder storting België",
                      "snelle uitbetaling casino België", "legale online casino's België")
                  + _kw("fr", "meilleurs casinos en ligne Belgique", "avis casinos Belgique",
                        "meilleurs sites de casino Belgique", "nouveaux casinos en ligne Belgique",
                        "casino Belgique 2026", "bonus casino Belgique", "bonus sans dépôt Belgique",
                        "casino retrait rapide Belgique", "casino légal Belgique"),
        "sportsbook": _kw("nl", "beste goksites België", "beste bookmakers België",
                          "sportweddenschappen België")
                      + _kw("fr", "meilleurs sites de paris Belgique", "avis bookmakers Belgique",
                            "paris sportifs Belgique"),
    },
    "CH": {  # trilingual: German + French + Italian
        "casino": _kw("de", "beste Online Casinos Schweiz", "Online Casino Test Schweiz",
                      "Casino Erfahrungen Schweiz", "neue Online Casinos Schweiz",
                      "Online Casinos Schweiz 2026", "Casino Bonus Schweiz", "Casino ohne Einzahlung Schweiz",
                      "schnelle Auszahlung Casino Schweiz", "legale Online Casinos Schweiz")
                  + _kw("fr", "meilleurs casinos en ligne Suisse", "avis casinos Suisse",
                        "nouveaux casinos Suisse", "casino en ligne Suisse 2026", "bonus casino Suisse",
                        "bonus sans dépôt Suisse", "casino retrait rapide Suisse", "casino légal Suisse",
                        "sites de casino Suisse")
                  + _kw("it", "migliori casinò online Svizzera", "recensioni casinò Svizzera",
                        "nuovi casinò online Svizzera", "casinò online Svizzera 2026",
                        "bonus casinò Svizzera", "bonus senza deposito Svizzera",
                        "casinò prelievi veloci Svizzera", "casinò legali Svizzera", "siti casinò Svizzera"),
        "sportsbook": _kw("de", "beste Wettanbieter Schweiz", "Sportwetten Schweiz", "Wettanbieter Test Schweiz")
                      + _kw("fr", "meilleurs sites de paris Suisse", "avis bookmakers Suisse",
                            "paris sportifs Suisse")
                      + _kw("it", "migliori siti scommesse Svizzera", "bookmaker Svizzera",
                            "scommesse sportive Svizzera"),
    },
    "AT": {
        "casino": _kw("de", "beste Online Casinos Österreich", "Online Casino Test Österreich",
                      "Online Casino Erfahrungen Österreich", "neue Online Casinos Österreich",
                      "Online Casinos Österreich 2026", "Casino Bonus Österreich",
                      "Casino ohne Einzahlung Österreich", "Casinos mit schneller Auszahlung Österreich"),
        "sportsbook": _kw("de", "beste Wettanbieter Österreich", "Sportwetten Anbieter Österreich",
                          "beste Sportwetten Österreich", "Online Sportwetten Österreich"),
    },
    "HR": {
        "casino": _kw("hr", "najbolja online casina", "recenzije online casina", "najbolje casino stranice",
                      "nova online casina", "online casina 2026", "casino bonus",
                      "casino bonus bez depozita", "casina s brzom isplatom"),
        "sportsbook": _kw("hr", "najbolje kladionice", "recenzije kladionica",
                          "najbolje stranice za klađenje", "sportsko klađenje online"),
    },
    "FR": {  # online casino PROHIBITED in France — casino terms surface grey-market (filtered)
        "casino": _kw("fr", "meilleurs casinos en ligne", "avis casinos en ligne", "meilleurs sites de casino",
                      "nouveaux casinos en ligne", "casinos en ligne 2026", "bonus casino",
                      "bonus casino sans dépôt"),
        "sportsbook": _kw("fr", "meilleurs sites de paris", "avis sites de paris", "meilleurs bookmakers",
                          "paris sportifs en ligne", "sites de paris sportifs", "comparatif bookmakers"),
    },
    "NO": {
        "casino": _kw("no", "beste nettcasinoer", "anmeldelser av nettcasino", "beste casinosider",
                      "nye nettcasinoer", "nettcasinoer 2026", "casino bonus",
                      "casino bonus uten innskudd", "casinoer med raske uttak"),
        "sportsbook": _kw("no", "beste bettingsider", "anmeldelser av bettingsider",
                          "beste bookmakere", "sportsbetting på nett"),
    },
}

for _iso, _verts in _EU.items():
    for _vert, _kws in _verts.items():
        CURATED[(_iso, _vert)] = _kws


def _seed_keywords(iso: str, vertical: str) -> list[tuple[str, str]]:
    """Generated English seed so discovery works in any geo before curation."""
    lang = "en"
    cc = locations.name(iso)
    templates = {
        "casino":     [f"best online casino {cc.lower()}", f"new casino sites {iso.lower()}"],
        "sportsbook": [f"best betting sites {cc.lower()}", f"sports betting {iso.lower()}"],
        "bingo":      [f"best online bingo {cc.lower()}"],
        "poker":      [f"best poker sites {cc.lower()}"],
    }
    return [(kw, lang) for kw in templates.get(vertical, [])]


def keywords_for(country: str, vertical: str) -> list[tuple[str, str]]:
    return CURATED.get((country.upper(), vertical.lower())) or _seed_keywords(country.upper(), vertical.lower())


def is_curated(country: str, vertical: str) -> bool:
    return (country.upper(), vertical.lower()) in CURATED


def markets() -> list[tuple[str, str]]:
    """Curated (country, vertical) pairs — the demo/`discover --all` default sweep."""
    return sorted(k for k, v in CURATED.items() if v)


def world_markets(vertical: str = "casino") -> list[tuple[str, str]]:
    """Every known geo for one vertical — the worldwide sweep."""
    return [(iso, vertical) for iso in locations.all_isos()]

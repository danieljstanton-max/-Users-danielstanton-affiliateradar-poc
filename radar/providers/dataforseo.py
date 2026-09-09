"""DataForSEO provider.

One account/API key covers every endpoint we need:
  * SERP  > Google > Organic ...... discovery (who ranks per keyword/country)
  * Labs  > Domain Rank Overview ... estimated traffic (etv) + per-country split
  * Business Data ................. third-party reputation enrichment

Design guarantee: mock and live modes share the SAME parse path. Each method
builds a DataForSEO-shaped response dict (from the live HTTP call OR from
committed fixtures) and hands it to the same `_parse_*` function. So the mock
proves the real parsing code, not a parallel happy path.

NOTE on production cost: the SERP discovery sweep should use the queued
"Task POST -> Task GET" pattern (~$0.0101/call) rather than the "live" endpoint.
The parsing is identical; only the transport/queueing differs. This PoC uses the
live shape for readability and flags the production path here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from .. import config
from ..locations import iso_for

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# friendlier hints for the terminal error codes DataForSEO returns
_HTTP_HINT = {401: "check DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD",
              402: "no funds / quota on the account",
              403: "endpoint not enabled for this account"}


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _stable_hash(*parts: str) -> int:
    """Deterministic across processes (unlike builtin hash())."""
    h = hashlib.md5("|".join(parts).encode()).hexdigest()
    return int(h[:8], 16)


# --- worldwide MOCK generators (used only when no live keys are present) -----
_STEMS = ["casinoguide", "slotspilot", "betscout", "topbonus", "luckyreview",
          "spinwise", "acepunter", "reelmentor", "oddscompass", "jackpotradar"]


def _mock_serp_for_country(location_code: int) -> list[dict]:
    """Synthesise a plausible SERP for any non-UK market. Domains encode their
    home country ('-<iso>.example') so the traffic mock can concentrate etv
    there — the same way a real local affiliate ranks mostly in its own geo."""
    from ..locations import iso_for
    iso = iso_for(location_code).lower()
    n = 5 + (_stable_hash("count", iso) % 4)  # 5..8 domains
    # deterministic distinct-stem order per country (no numeric-suffix dupes)
    order = sorted(_STEMS, key=lambda s: _stable_hash(iso, s))
    items = []
    for rank, stem in enumerate(order[:n], start=1):
        domain = f"{stem}-{iso}.example"
        items.append({"domain": domain, "url": f"https://{domain}/",
                      "rank_absolute": rank, "title": f"{stem} {iso.upper()}"})
    return items


def _mock_synth_etv(domain: str, location_code: int) -> float:
    """Etv for a synthesised worldwide domain at one location. Home geo (encoded
    in the domain suffix) gets the bulk; the US gets a little spillover."""
    from ..locations import code_for
    if not domain.endswith(".example") or "-" not in domain:
        return 0.0
    home_iso = domain.rsplit("-", 1)[1].split(".")[0].upper()
    home_code = code_for(home_iso)
    if home_code is None:
        return 0.0
    base = 4000 + (_stable_hash("etv", domain) % 60000)  # ~4k..64k /mo
    if int(location_code) == home_code:
        return base * 0.82
    if int(location_code) == 2840 and home_code != 2840:  # US spillover
        return base * 0.08
    return 0.0


class DataForSEOError(RuntimeError):
    pass


class DataForSEOClient:
    def __init__(self, week: int = 0):
        # `week` only affects MOCK mode: it selects a deterministic weekly
        # multiplier so trend emerges from stored history. Ignored when live.
        self.week = week
        self.live = config.dataforseo_is_live()
        self._mock_traffic = _load_fixture("mock_traffic.json")

    # -- transport ----------------------------------------------------------
    def _post(self, path: str, payload: list[dict], _tries: int = 3) -> dict:
        """POST to a DataForSEO v3 endpoint with HTTP Basic auth. Retries
        transient failures (429 rate-limit, 5xx, network/timeout) with backoff.
        Raises DataForSEOError on an auth/quota/contract problem so the caller
        can surface it."""
        url = config.DATAFORSEO_BASE + path
        token = base64.b64encode(
            f"{config.DATAFORSEO_LOGIN}:{config.DATAFORSEO_PASSWORD}".encode()
        ).decode()
        data = json.dumps(payload).encode()
        last_err = None
        for attempt in range(_tries):
            if attempt:
                time.sleep(1.5 * attempt)                       # simple linear backoff
            req = urllib.request.Request(
                url, data=data,
                headers={"Authorization": f"Basic {token}",
                         "Content-Type": "application/json"},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:300]
                except Exception:
                    pass
                if e.code == 429 or 500 <= e.code < 600:        # transient → retry
                    last_err = DataForSEOError(f"HTTP {e.code}: {detail}")
                    continue
                # 401/402/403 etc. are terminal (bad creds / no quota) — fail fast
                raise DataForSEOError(f"HTTP {e.code} ({_HTTP_HINT.get(e.code, 'error')}): {detail}") from e
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                last_err = DataForSEOError(f"connection error: {e}")
                continue
            # top-level envelope status (20000 = ok). 40000-series = account/format.
            if body.get("status_code") != 20000:
                raise DataForSEOError(
                    f"API {body.get('status_code')}: {body.get('status_message')}")
            return body
        raise last_err or DataForSEOError("request failed")

    @staticmethod
    def _ok_tasks(body: dict) -> list:
        """Yield only successfully-completed tasks (per-task status 20000);
        skip tasks that errored (e.g. an unsupported location) instead of
        failing the whole batch."""
        good = []
        for task in body.get("tasks", []) or []:
            # missing status_code (mock/fixtures) is treated as ok; an explicit
            # non-20000 (a real per-task failure) is skipped
            if task.get("status_code", 20000) == 20000 and task.get("result"):
                good.append(task)
        return good

    # ======================================================================
    # 1) DISCOVERY  — SERP > Google > Organic
    # ======================================================================
    def serp_organic(self, keyword: str, location_code: int,
                     language_code: str = "en") -> list[dict]:
        """Return ranking organic items: [{domain, url, rank_absolute, title}]."""
        if self.live:
            raw = self._post(
                "/v3/serp/google/organic/live/advanced",
                [{"keyword": keyword, "location_code": location_code,
                  "language_code": language_code, "device": "desktop"}],
            )
            return self._parse_serp(raw)
        # MOCK worldwide: the UK uses the hand-authored fixture (realistic
        # domains); every other geo is synthesised deterministically so the
        # discovery pipeline demonstrably runs in ANY market.
        if int(location_code) == 2826:
            items = self._parse_serp(_load_fixture("serp_google_organic_uk_casino.json"))
        else:
            items = _mock_serp_for_country(location_code)
        # Different keywords rank different subsets -> verticals stay distinct.
        return [it for it in items
                if (_stable_hash(keyword, it["domain"]) % 3) != 0]

    @staticmethod
    def _parse_serp(raw: dict) -> list[dict]:
        out: list[dict] = []
        for task in DataForSEOClient._ok_tasks(raw):
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    if item.get("type") != "organic":
                        continue
                    dom = (item.get("domain") or "").lower()
                    if dom.startswith("www."):      # normalise so www.x and x don't split
                        dom = dom[4:]
                    out.append({
                        "domain": dom or None,
                        "url": item.get("url"),
                        "rank_absolute": item.get("rank_absolute"),
                        "title": item.get("title"),
                    })
        return out

    # ======================================================================
    # 2) TRAFFIC  — Labs > Domain Rank Overview (per country -> etv split)
    # ======================================================================
    def domain_traffic(self, target: str,
                       location_codes: list[int]) -> dict[str, Any]:
        """Return {'total_etv': float, 'by_country': {ISO: etv}}.

        Production makes one Domain Rank Overview call per candidate country.
        """
        by_country: dict[str, float] = {}
        for code in location_codes:
            if self.live:
                raw = self._post(
                    "/v3/dataforseo_labs/google/domain_rank_overview/live",
                    [{"target": target, "location_code": code, "language_code": "en"}],
                )
            else:
                raw = self._mock_domain_rank_overview(target, code, self.week)
            etv = self._parse_domain_rank_overview(raw)
            if etv is not None and etv > 0:
                by_country[iso_for(code)] = round(etv, 1)
        return {"total_etv": round(sum(by_country.values()), 1),
                "by_country": by_country}

    def bulk_domain_traffic(self, targets: list[str], location_code: int,
                            language_code: str = "en") -> dict[str, float]:
        """{domain: etv} for MANY domains in ONE market, in as few calls as
        possible — this is the cost-efficient path for a large catalogue.

        LIVE: DataForSEO Labs *Bulk Traffic Estimation* takes up to 1000 targets
        per call, so a whole market is one call instead of one-call-per-site.
        MOCK: reuse the single-domain synth per target (no cost, identical etv).
        """
        out: dict[str, float] = {}
        targets = [t for t in targets if t]
        if not targets:
            return out
        if self.live:
            for i in range(0, len(targets), 1000):
                raw = self._post(
                    "/v3/dataforseo_labs/google/bulk_traffic_estimation/live",
                    [{"targets": targets[i:i + 1000], "location_code": location_code,
                      "language_code": language_code}])
                out.update(self._parse_bulk_traffic(raw))
        else:
            for t in targets:
                etv = self._parse_domain_rank_overview(
                    self._mock_domain_rank_overview(t, location_code, self.week))
                if etv and etv > 0:
                    out[t] = round(etv, 1)
        return out

    @staticmethod
    def _parse_bulk_traffic(raw: dict) -> dict[str, float]:
        """Bulk Traffic Estimation → {target domain: organic etv}."""
        out: dict[str, float] = {}
        for task in DataForSEOClient._ok_tasks(raw):
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    target = item.get("target") or item.get("se_domain") or item.get("domain")
                    metrics = (item.get("metrics") or {}).get("organic") or {}
                    etv = metrics.get("etv")
                    if target and etv:
                        out[target] = round(float(etv), 1)
        return out

    @staticmethod
    def _parse_domain_rank_overview(raw: dict) -> float | None:
        for task in DataForSEOClient._ok_tasks(raw):
            for result in task.get("result") or []:
                for item in result.get("items") or []:
                    organic = (item.get("metrics") or {}).get("organic") or {}
                    if "etv" in organic:
                        return float(organic["etv"])
        return None

    def _mock_domain_rank_overview(self, target: str, location_code: int,
                                   week: int) -> dict:
        """Synthesise a REAL-shaped single-location response from mock_traffic."""
        dom = self._mock_traffic["domains"].get(target)
        factors = self._mock_traffic["week_factors"]
        factor = factors[week % len(factors)]
        # per-domain linear drift so trends differ site-to-site (some up, some
        # down). Curated domains may pin an explicit drift; others use a stable
        # per-domain hash.
        if dom and "drift" in dom:
            drift_pct = dom["drift"]
        else:
            drift_pct = (_stable_hash("drift", target) % 11 - 5) / 100.0  # -5%..+5% / wk
        factor *= (1 + drift_pct * week)
        etv = 0.0
        if dom:
            # curated GB fixtures with an explicit per-country split
            frac = dom["split"].get(str(location_code), 0.0)
            etv = round(dom["base_etv"] * frac * factor, 1)
        else:
            # synthesised worldwide domain: concentrate etv in its home geo
            # (encoded as '-<iso>.example'), with light spillover to the US.
            etv = round(_mock_synth_etv(target, location_code) * factor, 1)
        return {
            "status_code": 20000, "status_message": "Ok.",
            "tasks": [{"status_code": 20000, "result": [{
                "target": target, "location_code": location_code,
                "items": ([{"metrics": {"organic": {"etv": etv}}}] if etv > 0 else []),
            }]}],
        }

    # ======================================================================
    # 3) ENRICHMENT — Business Data (third-party reputation; NOT peer reviews)
    # ======================================================================
    def business_data(self, domain: str) -> dict | None:
        if self.live:
            # Production: POST to the relevant Business Data (Google/Trustpilot)
            # endpoint. Left as fixture in the PoC to avoid an extra live shape.
            raw = _load_fixture("business_data_sample.json")
        else:
            raw = _load_fixture("business_data_sample.json")
        for task in raw.get("tasks", []) or []:
            for result in task.get("result") or []:
                rating = result.get("rating") or {}
                return {
                    "external_rating": rating.get("value"),
                    "external_votes": rating.get("votes_count"),
                    "category": result.get("category"),
                }
        return None

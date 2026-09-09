# Affswap (AffiliateRadar) — Data-Layer Proof of Concept

> **Product direction: Affswap.** The app is a peer-to-peer *swap* network —
> managers trade affiliate contacts rather than buy them (contacts are hidden
> until a mutual swap completes). The data layer below is the foundation it runs
> on; the swap engine that sits on top is documented under
> [**Affswap — the peer-to-peer swap engine**](#affswap--the-peer-to-peer-swap-engine).

A runnable proof of concept for the data layer (per the Developer Brief). It
exists to de-risk the hardest, most expensive parts of the product **before**
any UI is built:

1. **Discovery** — SERP → de-duplicate ranking domains → a `candidate` list for
   human classification.
2. **Traffic & trend** — Domain Rank Overview → a **weekly etv snapshot** stored
   in our own DB → **trend computed from our own history** (no repeated paid
   historical calls).
3. **Data ownership** — a refresh updates **API-owned fields only** and can
   **never** overwrite human-owned classification or contacts. This is enforced
   structurally, not by convention.

It also wires the **screenshot** provider and the **Business Data** enrichment
call, and assembles the exact JSON shapes the front end would render for the
*country list* and *site profile* screens.

## Runs with zero setup

No Node, no database server, no `pip install`. Pure **Python 3.9+ standard
library** (`sqlite3` + `urllib`). It starts in **MOCK mode** against committed
fixtures, so you can see the whole pipeline work today.

```bash
python3 -m radar demo
```

That resets the DB, runs discovery, builds four weeks of trend history, captures
screenshots, prints the two screens, and runs the **prove-ownership** proof.

### Individual commands

```bash
python3 -m radar initdb --reset
python3 -m radar discover --world --vertical casino   # sweep EVERY known geo
python3 -m radar discover --all                       # curated launch markets
python3 -m radar refresh --build-history              # weeks 0..3 -> real trend
python3 -m radar screenshot --all
python3 -m radar show home                            # flag grid (markets + counts + traffic)
python3 -m radar show movers --country AU             # 90-day risers & fallers
python3 -m radar show blacklist                       # flagged sites + reasons
python3 -m radar show country --country GB --vertical casino --sort traffic
python3 -m radar show country --country GB --vertical casino --sort reviews
python3 -m radar show country --country GB --sort blacklisted
python3 -m radar show profile --domain casinopilot-uk.example --contact
python3 -m radar prove-ownership                      # the headline guarantee
python3 -m radar admin                                # back-office web panel

python3 -m radar swap demo                            # Affswap: match → agree → transfer → ledger
python3 -m radar swap matches --manager "Sarah K"     # a manager's matches + swaps left
python3 -m radar swap ledger                           # operator audit trail (who sent what to whom)
```

Add `--json` to any `show` command to see the raw front-end payload.

## Back office (the human-judgement surface)

```bash
python3 -m radar admin        # http://127.0.0.1:8765
```

A localhost-only web panel (stdlib `http.server`, no dependencies) over the same
SQLite DB, with two jobs:

- **New affiliate queue** — discovery surfaces every domain that ranks; most
  aren't affiliates. The queue is the *holding area* where a human confirms the
  real ones. **Approve** publishes the site to the app with its traffic and
  auto-captures a screenshot; **Reject** hides it. Nothing reaches the public
  app until it's approved.
- **All sites** — edit classification and the three outreach channels (email /
  Telegram / Teams) for any site.
- **Bulk import** — upload a CSV (`url, country, vertical, company,
  contact_name, email, telegram, teams, status`), matched by domain, with a
  **preview before commit**. Creates new sites (`source=manual`) or updates
  existing ones; contact channels are *merged*, never wiped. Download a template
  from the import page.

- **Blacklist** — upload a CSV of bad actors (`url, reason` + optional
  `company`). Blacklisted sites are **shown to users on a warning screen with the
  reason** and **excluded from every recommended list** (home, country, movers).
  Kept in its own table, so a site can be both a known affiliate and flagged, and
  a refresh can never clear it. Remove with one click.

Everything the back office writes goes through the **human-owned** path, so the
weekly API refresh updates traffic only and never overwrites it.

## Home & country browse

Home is a **country-flag grid** (`countries_index`): every market with published
affiliates, its affiliate count, total monthly traffic, and a marker for how many
sites are blacklisted there. Tapping a market opens its **affiliate list**, which
filters by **vertical tab** (Casino / Sportsbook / Bingo / Poker) and sorts three
ways:

- **Traffic** — affiliates by estimated monthly organic traffic
- **Reviews** — affiliates by peer star rating (`site_reviews` aggregate, Phase 2)
- **Flagged** — the blacklisted sites in that market, each with its reason

Blacklisted sites are excluded from the Traffic/Reviews lists (and from Home/Movers)
and only surface under **Flagged** — a warning, never a recommendation.

## Multi-region tagging — a site files under every market it draws traffic from

A market isn't "where we discovered the site" — it's **everywhere the site
actually pulls organic traffic**. On each refresh, DataForSEO's per-country
traffic split is read for every site and each market above a share threshold
(`REGION_MIN_SHARE`, plus the top market always) is written to `site_regions`
(API-owned). So Livescore, with UK / DE / SE / RU traffic, shows in **all four**
country lists — not just its discovery geo.

- **Membership** for the home flag-grid, country browse and Market Movers now
  comes from `site_regions` (country) × `site_verticals` (vertical); the etv shown
  in a country is that market's own etv, so market totals never double-count.
- **The profile** shows a *Traffic by market* breakdown; country rows show an
  *"also in 🇮🇪 🇺🇸"* hint.
- **Any site joins the pool automatically** — the refresh iterates every row in
  `sites`, so a discovered, imported, or member-submitted site gets its regions
  on the next run. Trigger it from the back office (**All sites → ↻ Refresh
  traffic now**) or let the weekly job do it. Production swaps the per-location
  loop for a single bulk traffic-by-country call (marked CONFIRM in `refresh.py`).

## Community chat (CometChat) — backend + moderation

The verified-managers community chat is built **backend-first and mock-first**: it
runs with no credentials, and flips to the real CometChat REST API the moment you
set `COMETCHAT_APP_ID` / `COMETCHAT_REGION` / `COMETCHAT_REST_API_KEY` in `.env` —
no code change. The mobile app is a thin client (see
[`docs/react-native-client.md`](docs/react-native-client.md)); everything
security-critical lives here on the server.

```bash
python3 -m radar chat demo                 # end-to-end: seed → verified session ✓ → unverified 403 → report → ban
python3 -m radar chat provision            # create the 7 rooms as CometChat groups
python3 -m radar chat session --manager "Sarah K"   # mint a gated session (or 403 if not verified)
python3 -m radar chat reports              # the operator moderation queue
```

- **The gate** (`radar/chat/service.py: issue_session`) — our DB is the only authority
  on who may chat; every session re-checks verification and mints a **rotated**
  per-user auth token with the server-only REST key. The client never holds the key
  and never self-authenticates.
- **Identity** is pseudonymous in CometChat (opaque `cc_uid`, server-owned handle);
  real identity stays in our DB. The "verified ✓" badge is authoritative from us,
  not a spoofable metadata flag.
- **App-facing API** (in the admin server for the PoC): `POST /api/chat/session`,
  `POST /api/chat/report`.
- **Moderation** — the back office **Chat reports** tab is the operator queue
  (report → server-fetched evidence → remove / kick / ban / dismiss via REST),
  reusing the same pattern as the affiliate review queue. Satisfies Apple 1.2.

Full spec + the "confirm against CometChat docs" checklist:
[`docs/cometchat-integration.md`](docs/cometchat-integration.md).

## Website names (not URLs) on every button

Lists and buttons show a clean **website name** — "CasinoPilot", "Top Casino
Reviews", "Gambling.com" — in the app's title font, never a raw URL. The full URL
is saved and only revealed on the click-through profile. The name is a
human-owned `sites.display_name` (set inline in the back office or via a
`website_name` CSV column); when none is set, `radar/branding.py` derives a tidy
name from the domain, so a button is never a bare URL. Because `display_name` is
human-owned it's outside the refresh allow-list — the weekly API job can't touch
it. `views.*` emit both `name` and `domain`; the app renders `name`.

## Sign-up & member approvals

Affswap is closed to verified affiliate managers, so **sign-up is an application**,
not instant access (`radar/members.py`). A prospective member provides three
legitimacy signals — **LinkedIn** (OAuth), a confirmed **work email** on a real
company domain (free providers like gmail are refused at submit), and the
**company + affiliate site** they manage — and lands in the back-office **Member
approvals** queue as `pending`.

```bash
python3 -m radar members demo     # apply (incl. a refused gmail) → review → approve → trial
python3 -m radar members list
```

- **Manual review** — an operator approves each application in the Member
  approvals tab; approval is blocked until both automated signals (LinkedIn +
  confirmed work email) are in.
- **Approval starts the trial** — `approve` calls `swaps.approve_manager`, which
  flips the member to `verified`, stamps `approved_at` (the 48-hour trial clock)
  and opens their swap wallet. No card is taken — the paywall below takes over
  when the trial ends.
- **Plug-in points** — the LinkedIn OAuth handshake and the work-email send are
  external (like `alerts.deliver`); the resulting `linkedin_verified` /
  `work_email_confirmed` flags flow into the same gate the app already trusts.
- **App-facing API**: `POST /api/signup` (submit), `POST /api/signup/confirm-email`
  (the emailed link). The app's *Join Affswap* screen maps onto `/api/signup`.

## Access gate — 48-hour trial, then browsing needs a swap

Nobody lurks. On approval a member gets a **48-hour free trial** with full access
(browse sites, stats, reviews, traffic, chat). When it expires they must **hold at
least 1 swap** to keep browsing — so access tracks activity, not just sign-up.
`swaps.access(conn, manager_id)` is the single gate the app checks; it returns one
of:

- `trial` — inside the 48h window from `approved_at` (full access)
- `active` — trial over, holds ≥ 1 swap (or is on the Unlimited plan)
- `locked` — trial over, 0 swaps: browsing blocked until they buy (£3.99) or earn one
- `banned` / `unverified` — no access

Spending a swap to complete a match can drop you to zero: when you're on your
**last** swap the gate flags `warn_last_swap` ("this is your last swap…"), and the
completion returns a `notices` entry the moment it locks you (`"that was your last
swap — browsing is locked until you buy or earn 1 more"`). The Unlimited plan never
locks. App-facing: `POST /api/swap/access` (and `access` is embedded in the
`/api/swap/matches` response); CLI: `python3 -m radar swap access --manager "…"`.

The **left preview phone** demonstrates all of it — the *Access* toggle switches
between Trial / Active / Last swap / Locked, showing the trial banner, the
last-swap warning, and the full "your trial has ended — get a swap" lock screen.

## Affswap — the peer-to-peer swap engine

The product pivots from *browse contacts* to **swap contacts**. Affiliate
managers never see a contact for free and never buy one — they **trade**. Each
manager registers the affiliates they already **have** a contact for and the
ones they **want**. When two managers each hold what the other wants, that's a
**mutual match**; when **both** press *Agree to swap*, the two contacts are
transferred to each other and each spends one swap from their plan.

```bash
python3 -m radar swap demo     # end-to-end: seed → 3 matches → both agree → transfer → ledger → ban
```

- **Matching** (`radar/swaps.py: find_matches`) — a mutual swap exists between
  managers *m* and *n* when (*m* wants a site *n* has) **and** (*n* wants a site
  *m* has). Matches are stored canonically (one active match per pair) so the
  same pairing is never duplicated.
- **Agree → transfer** (`agree` / `_transfer`) — the **second** agree triggers
  the exchange: two `swap_ledger` rows (one per direction), each side's swap
  allowance is decremented, and the match is marked `completed`. If either side
  is out of swaps the match stays agreed-but-pending and reports `needs_swaps`
  so the app can prompt a top-up — nothing transfers until both can pay a swap.
- **Plans** (`config.SWAP_PLANS`) — Standard £9.99 / 3 swaps, Pro £19.99 / 10,
  Unlimited £49.99. Extra swaps are £3.99 each (`buy_extra`) and are consumed
  after the monthly allowance.
- **Swaps ledger** (back office → **Swaps ledger**) — every contact that changed
  hands, in both directions: *when · from · to · website · the exact contact
  string sent*. If a manager sends wrong or dead information, **Flag** the
  transfer or **Flag & ban sender** — the ban reuses the same `status='banned'`
  the chat gate honours, so it also cuts them out of the community chat and any
  future swaps.
- **Loyalty — earn a free swap** (`submit_contribution` → `on_site_published`):
  the only way to get a swap without paying. A manager submits an affiliate that
  isn't listed yet (URL, country, vertical, why it matters); it enters the normal
  **New affiliate queue** as a `candidate`. **The reward is granted only when an
  admin approves the site** in that queue (+1 swap to the submitter) — never on
  submission, and nothing at all for a site that's rejected or still pending.
  Duplicates and already-listed sites are refused. The back-office **Rewards**
  tab is a read-only ledger of who earned what.
- **App-facing API** (in the admin server for the PoC): `POST /api/swap/have`,
  `/api/swap/want`, `/api/swap/matches`, `/api/swap/agree`, `/api/swap/buy`,
  `/api/swap/contribute`.

**You can Want / Have any site, but a swap only completes once the affiliate is
admin-approved.** Registering a want or have (and matching) works on any site,
including one still in the review queue — but `_transfer` refuses to hand over
contacts until **both** affiliates are classified `affiliate`. A fully-agreed
swap that's waiting on approval returns `pending_approval` and stays put;
the moment an admin approves the site in the queue, `settle_ready_matches`
completes it automatically. So people express interest early, but nothing
actually trades until an admin has approved the site.

No contact details are ever published in the app — a contact only passes between
two managers when a swap completes. The site profile shows **Have / Want**, never
a contact, and the loyalty page rewards adding *sites*, not contacts.

Managers are the same **verified** identities as the community chat
(`chat_managers`) — only verified managers can agree to a swap. Tables:
`swap_accounts`, `swap_wants`, `swap_haves`, `swap_matches`, `swap_ledger`.

## Market Movers

`show movers --country <ISO> [--vertical <v>]` (and the Market Movers screen)
surfaces the **top 5 risers and top 5 fallers** for a chosen market over ~90 days,
computed from our own weekly etv snapshots — no extra API calls. Like the country
browse, it has **Casino / Sportsbook / Bingo / Poker tabs**: `market_movers(...,
vertical=…)` restricts the movers to a single vertical (the screen's tab). The demo
builds ~13 weekly snapshots (≈90 days) so this and the short-term trend both have
real history.

## Worldwide by design

Geography is data, not code. A country's DataForSEO `location_code` is
`2000 + its ISO-3166 numeric code`, and its flag is just its two ISO letters as
regional-indicator emoji — so **any** country works without per-country wiring.
`radar/locations.py` ships a curated worldwide iGaming market set; adding a
market is one row. Discovery loops keyword × country × language; refresh queries
each site's discovery geos plus the major markets to find its traffic origin.

## Bulk import + Google Sheets mirror

Load a known list of sites and mirror the whole catalogue to a Google Sheet.

```bash
python3 -m radar import my-1000-sites.csv    # bulk-load a CSV of vetted sites
python3 -m radar refresh                      # (live) pull traffic + geos for every site
python3 -m radar sheets sync                   # mirror the catalogue + swaps log to a sheet
```

- **Bulk import** (`radar import <csv>`, or the back-office **Bulk import** tab)
  loads sites by domain — `url` is the only required column; `status=affiliate`
  publishes them straight away. Ideal for seeding a large vetted list; the
  **refresh runs against every site**, so one run validates DataForSEO on domains
  you already know are right (traffic + multi-region geos come back per site).
- **Google Sheets mirror** (`radar/sheets.py`) keeps a living, shareable copy: an
  **Affiliates** tab (one row per site — domain, name, verticals, status, total
  etv, primary geo, and the full per-geo traffic split) and a **Swaps** tab that
  **appends the instant a contact is shared**. No Google libraries — it POSTs to a
  tiny Apps Script Web App (see [`docs/google-sheets-integration.md`](docs/google-sheets-integration.md));
  blank `.env` keeps it in MOCK mode, writing the same rows to `data/sheets/*.csv`.
  The sheet is a mirror for browsing/sharing — the database stays the source of truth.

## Going live (real APIs)

The same code hits the real APIs the moment credentials are present — **no code
change**. Copy `.env.example` to `.env` and fill it in:

```
DATAFORSEO_LOGIN=...
DATAFORSEO_PASSWORD=...
SCREENSHOT_PROVIDER=screenshotone
SCREENSHOT_KEY=...
```

`python3 -m radar initdb` prints the active mode. **Mock and live share the same
parse path** — mock builds a real-shaped API response from `radar/fixtures/` and
feeds it through the identical `_parse_*` code — so the PoC validates the real
parsing, not a parallel happy path.

> Put keys in `.env` only. Don't paste them into chat, and don't commit `.env`
> (it's git-ignored).

## The ownership guarantee, made structural

`radar/ownership.py` defines an **allow-list** of the only columns a refresh may
write (`etv, top_country, rank_best, trend_pct, trend_dir, last_api_refresh`).
`apply_api_update()` is the single write path the refresh uses, and it **raises
`OwnershipViolation`** if handed any other column. Human-owned data
(`classification`, `source`, `human_notes`, and the entire `site_contacts`
table) is not on the list, so a refresh physically cannot touch it. `change_log`
records every write with its `owner` (`api` | `human`) for audit.

`prove-ownership` demonstrates all of it: a human classifies a site and adds a
contact, two API refreshes run with different traffic numbers, and the output
shows human fields **unchanged** while API fields update — plus the guardrail
refusing an illegal write, and a manual-only site surviving intact.

## Endpoint mapping (from the brief)

| App need | Source / endpoint | Code |
|---|---|---|
| Discover who ranks | DataForSEO SERP › Google › Organic | `providers/dataforseo.py: serp_organic` |
| Estimated traffic (etv) + per-country split | DataForSEO Labs › Domain Rank Overview (Live) | `domain_traffic` |
| % change vs last month | Own weekly etv snapshots + trend | `refresh.py: _compute_trend` |
| Homepage screenshots | ScreenshotOne / Urlbox | `providers/screenshot.py` |
| Company / reputation enrichment | DataForSEO Business Data | `dataforseo.py: business_data` |

**Production note:** the discovery sweep should use DataForSEO's queued *Task
POST → Task GET* pattern (~$0.0101/call) rather than the `live` endpoint used
here for readability. Parsing is identical; only the transport differs.

## Layout

```
radar/
  schema.sql          # DB schema — columns grouped by OWNER (api vs human)
  config.py           # .env loader + mock/live resolution
  db.py               # sqlite access, upsert_site, change_log
  ownership.py        # THE allow-list guardrail (apply_api_update)
  discovery.py        # SERP sweep -> dedup -> candidates + evidence trail
  refresh.py          # weekly snapshot + trend from own history
  views.py            # country-list / site-profile front-end payloads
  keywords.py         # per-(country,vertical) local keyword dictionaries
  locations.py        # DataForSEO location codes <-> ISO + flags
  providers/
    dataforseo.py     # SERP + Labs + Business Data (mock == live parse path)
    screenshot.py     # ScreenshotOne/Urlbox adapter + placeholder fallback
  fixtures/           # faithful sample API responses (illustrative domains only)
```

## Alerts engine

Members arm alert **rules** from the app (`radar/alerts.py`): a country (or all
markets), one or more verticals, the triggers they care about — a **new**
affiliate detected, traffic **up**, traffic **down** — and how they're notified
(push / email / Telegram).

```bash
python3 -m radar alerts demo                 # arm rules → evaluate live data → deliver
python3 -m radar alerts add --manager "Sarah K" --country GB \
        --verticals casino,sportsbook --triggers new,up,down --delivery push,email
python3 -m radar alerts evaluate --manager "Sarah K"   # preview what would fire (no send)
python3 -m radar alerts fire --manager "Sarah K"       # fire + deliver (idempotent)
```

- **The matcher** (`evaluate`) is the engine tick — run it after each weekly
  refresh. For every active rule it finds the affiliates in that market/vertical
  whose current state matches a trigger (recent `published_at` → *new*;
  `trend_dir` up/down → *traffic*) and fires an event. It reads the exact fields
  the refresh writes — no parallel data path.
- **Idempotent delivery** — an event is recorded once per `(rule, site, trigger)`,
  so re-running the tick never re-notifies for something already sent.
- **The one plug-in point is `deliver()`** — a tiny, side-effect-free function
  that today just acknowledges the channels. Wire the real providers there (push →
  APNs/FCM, email → SMTP/SendGrid, Telegram → Bot API) and nothing else changes.
- **App-facing API**: `POST /api/alerts/{save,list,toggle,delete,evaluate}` — the
  app's *Arm alert* builder maps straight onto `/api/alerts/save`.

Tables: `alert_rules`, `alert_events`.

## What this PoC deliberately does **not** cover

Payment capture/billing, peer reviews & moderation, and the web UI. Those are
Phase-2/3 in the brief; this is the data foundation they build on. The remaining
integrations are isolated, documented plug-in points: alert **delivery**
(`alerts.deliver` — push/email/Telegram) and sign-up's two external steps (the
**LinkedIn OAuth** handshake and the **work-email send** in `radar/members.py`).

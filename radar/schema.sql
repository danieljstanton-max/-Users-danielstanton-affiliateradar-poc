-- AffiliateRadar PoC — database schema
--
-- The central design rule from the brief:
--   "Separate API-owned fields from human-owned fields and never let one
--    overwrite the other. A refresh must never wipe a human-entered contact."
--
-- We encode ownership STRUCTURALLY so it is impossible to break by accident:
--   * API-owned facts live in columns/tables written ONLY by the refresh job.
--   * Human-owned facts live in columns/tables written ONLY by admin actions.
--   * A refresh physically cannot touch human tables — it never issues writes
--     against them (see radar/ownership.py for the enforced write surface).

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- sites: one row per domain. Columns are grouped by OWNER.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sites (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    domain           TEXT NOT NULL UNIQUE,

    -- ---- HUMAN-OWNED (set by admins; refresh must NEVER write these) --------
    -- classification: neither DataForSEO nor any SEO tool tags a site as an
    -- affiliate. A human confirms it. New domains start as 'candidate'.
    classification   TEXT NOT NULL DEFAULT 'candidate'
                       CHECK (classification IN ('candidate','affiliate','rejected')),
    -- how the site entered the catalogue. 'manual' sites have no API presence
    -- and must survive every refresh untouched.
    source           TEXT NOT NULL DEFAULT 'api'
                       CHECK (source IN ('api','manual')),
    human_notes      TEXT,
    -- the website's brand NAME shown on every list/button (e.g. "CasinoPilot").
    -- Human-owned: an operator types the real brand; NULL falls back to a name
    -- derived from the domain (see radar/branding.py). The full URL is only
    -- revealed on the click-through profile.
    display_name     TEXT,
    -- set the moment a human approves the site as an affiliate. NULL while it
    -- sits in the review queue. Drives the "NEW" badge (recently published).
    published_at     TEXT,

    -- ---- API-OWNED (written ONLY by the weekly refresh) --------------------
    etv              REAL,      -- current estimated monthly organic traffic
    top_country      TEXT,      -- ISO code of the country contributing most etv
    rank_best        INTEGER,   -- best absolute SERP rank seen for the domain
    trend_pct        REAL,      -- % change vs baseline, computed from own history
    trend_dir        TEXT CHECK (trend_dir IN ('up','down','flat') OR trend_dir IS NULL),
    last_api_refresh TEXT,      -- ISO timestamp of last successful API write

    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- site_verticals: which vertical(s) a site is tagged with.
--   source='auto'  — machine-tagged (discovery keyword / domain classifier /
--                    bulk import placeholder). Freely re-derived; a refresh or
--                    re-tag may replace these.
--   source='human' — set by an operator in the back office. AUTHORITATIVE: the
--                    auto-tagger never touches a site that has any human row.
-- "Machine proposes, human corrects, the correction sticks."
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_verticals (
    site_id   INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    vertical  TEXT NOT NULL CHECK (vertical IN ('casino','sportsbook','bingo','poker')),
    source    TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto','human')),
    PRIMARY KEY (site_id, vertical)
);

-- ---------------------------------------------------------------------------
-- site_contacts: HUMAN-OWNED. Sourced by manual upload / inline edit, matched
-- by domain. Deliberately a SEPARATE TABLE so the refresh write surface never
-- includes it. This is the "a refresh must never wipe a contact" guarantee,
-- made structural.
-- ---------------------------------------------------------------------------
-- We do outreach by message, not phone: the reachable channels are email,
-- Telegram and Teams. Any subset may be known for a given site.
CREATE TABLE IF NOT EXISTS site_contacts (
    site_id          INTEGER PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
    company_name     TEXT,
    contact_name     TEXT,
    contact_email    TEXT,
    contact_telegram TEXT,
    contact_teams    TEXT,
    uploaded_by      TEXT,       -- admin user / import batch id
    uploaded_at      TEXT NOT NULL,
    updated_at       TEXT
);

-- ---------------------------------------------------------------------------
-- traffic_snapshots: API-OWNED weekly history. Each weekly pull appends the
-- current etv so trend is computed from OUR OWN history — no repeated paid
-- historical calls. (iso_week is the Monday of that ISO week.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS traffic_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id      INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    iso_week     TEXT NOT NULL,   -- e.g. '2026-08-03'
    etv          REAL NOT NULL,
    top_country  TEXT,
    captured_at  TEXT NOT NULL,
    UNIQUE (site_id, iso_week)
);

-- ---------------------------------------------------------------------------
-- site_regions: API-OWNED. Every market a site actually pulls organic traffic
-- from, with that market's estimated monthly etv, derived from the DataForSEO
-- per-country traffic split on each refresh. A site "files under" EVERY country
-- here — so Livescore (UK + DE + SE + RU traffic) shows in all four markets, not
-- just where it was discovered. Rewritten each refresh; a refresh can only touch
-- API-owned data, so this never affects human classification/contacts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_regions (
    site_id    INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    country    TEXT NOT NULL,          -- ISO market the traffic comes from
    etv        REAL,                   -- the site's estimated monthly etv in THIS market
    is_primary INTEGER NOT NULL DEFAULT 0,  -- 1 for the top traffic market
    updated_at TEXT NOT NULL,
    PRIMARY KEY (site_id, country)
);

-- ---------------------------------------------------------------------------
-- discovery_hits: evidence trail of where a domain appeared in the SERP.
-- Feeds classification review and vertical assignment.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_hits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id      INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    keyword      TEXT NOT NULL,
    country      TEXT NOT NULL,
    language     TEXT NOT NULL,
    vertical     TEXT NOT NULL,
    rank_absolute INTEGER,
    seen_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- screenshots: API-OWNED but refreshed far less often (homepages rarely
-- change) — on discovery, then monthly / on-change.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS screenshots (
    site_id      INTEGER PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
    source_url   TEXT NOT NULL,
    image_ref    TEXT NOT NULL,   -- local path (PoC) or CDN url (prod)
    provider     TEXT,
    captured_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- change_log: audit of every field write with its OWNER. Lets the
-- prove-ownership demo show, per field, that no API write ever carried an
-- owner of 'api' into a human-owned field.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity      TEXT NOT NULL,    -- 'sites' | 'site_contacts' | ...
    entity_id   INTEGER NOT NULL,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    owner       TEXT NOT NULL CHECK (owner IN ('api','human')),
    source      TEXT,             -- 'dataforseo:labs', 'admin:csv_import', ...
    changed_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- blacklist: HUMAN-OWNED warnings. A blacklisted site is shown to users on a
-- dedicated screen WITH the uploaded reason, and excluded from the recommended
-- lists. Separate table so a site can be both a known affiliate and flagged,
-- and so a refresh can never touch it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS blacklist (
    site_id   INTEGER PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
    reason    TEXT NOT NULL,
    country   TEXT,        -- human-owned market this warning applies to (ISO)
    added_by  TEXT,
    added_at  TEXT NOT NULL,
    updated_at TEXT
);

-- ---------------------------------------------------------------------------
-- site_reviews: PEER-generated aggregate (Phase 2). Rating 1-5 + count, from
-- verified affiliate managers. Human/peer-owned — a refresh never touches it.
-- Individual reviews would live in their own table; the app sorts on this.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_reviews (
    site_id      INTEGER PRIMARY KEY REFERENCES sites(id) ON DELETE CASCADE,
    rating       REAL,     -- average peer star rating, 1.0 .. 5.0
    review_count INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT
);

-- ---------------------------------------------------------------------------
-- reviews: INDIVIDUAL peer reviews (human/peer-owned; a refresh never touches
-- them). A verified manager rates a site 1-5 with optional notes; it enters as
-- 'pending' and an operator approves/rejects it in the back office. site_reviews
-- above is just a cache of AVG(rating)+COUNT over the APPROVED rows here.
-- reward_granted marks a review that has already paid out a loyalty swap, so the
-- "5 approved reviews = 1 free swap" reward can never double-grant.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id        INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    manager_id     INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body           TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','approved','rejected')),
    reward_granted INTEGER NOT NULL DEFAULT 0,   -- 1 once this review paid a loyalty swap
    created_at     TEXT NOT NULL,
    resolved_at    TEXT,
    resolved_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_site   ON reviews(site_id, status);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_mgr    ON reviews(manager_id, status);

-- ---------------------------------------------------------------------------
-- COMMUNITY CHAT (CometChat) — operator-side records. CometChat holds the
-- messages/realtime; we hold the gate (verification), the uid↔identity map,
-- the rooms, and the moderation queue. Real identity never goes to CometChat.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_managers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cc_uid        TEXT UNIQUE NOT NULL,       -- opaque CometChat UID (never derived from PII)
    handle        TEXT NOT NULL,              -- server-owned display name (e.g. 'Sarah K')
    sector        TEXT,                       -- casino/sportsbook/… (display)
    years         INTEGER,
    -- verification gate (the ONLY source of truth for "may chat")
    linkedin_verified   INTEGER NOT NULL DEFAULT 0,
    work_email_confirmed INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','verified','banned','rejected')),
    -- real identity + application details stay here, never sent to CometChat.
    -- Sign-up requires all three legitimacy signals: LinkedIn (linkedin_verified),
    -- a confirmed company work email (work_email_confirmed), and the company +
    -- affiliate site they manage — an admin reviews them in the Member approvals
    -- queue before the member is verified.
    real_name     TEXT,
    work_email    TEXT,
    linkedin_url  TEXT,
    company       TEXT,
    site_url      TEXT,               -- the affiliate site they manage
    applied_at    TEXT,               -- when they submitted the application
    -- when an admin approved this member. The 48h free-trial clock starts here;
    -- after it expires, browsing requires holding at least 1 swap.
    approved_at   TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_rooms (
    guid        TEXT PRIMARY KEY,             -- e.g. 'room_casino'
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'private',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    room_guid     TEXT,
    message_id    TEXT,
    reported_uid  TEXT,
    reporter_uid  TEXT,
    reason        TEXT,
    snapshot      TEXT,                        -- server-fetched message text (evidence)
    status        TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','actioned','dismissed')),
    action        TEXT,                        -- remove_message | kick | ban | dismiss
    resolved_by   TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
);

-- ---------------------------------------------------------------------------
-- AFFSWAP — peer-to-peer contact swapping.
--
-- The pivot: contacts are never sold, they are SWAPPED. A verified manager
-- registers the affiliates they already HAVE a contact for, and the ones they
-- WANT a contact for. When two managers each hold what the other wants, that's
-- a mutual match. BOTH must Agree; on the second Agree the two contacts are
-- transferred to each other and a ledger row is written for each direction — so
-- an operator can trace exactly who sent what to whom and ban bad actors who
-- send wrong information.
--
-- Managers are the verified chat_managers (reused — same identity, same gate).
-- The affiliates being swapped are sites. Nothing here is API-owned; a refresh
-- never touches these tables.
-- ---------------------------------------------------------------------------

-- One swap wallet per manager: which plan they're on and how much they've used.
CREATE TABLE IF NOT EXISTS swap_accounts (
    manager_id   INTEGER PRIMARY KEY REFERENCES chat_managers(id) ON DELETE CASCADE,
    plan         TEXT NOT NULL DEFAULT 'standard'
                   CHECK (plan IN ('standard','pro','unlimited')),
    swaps_used   INTEGER NOT NULL DEFAULT 0,   -- used against the monthly allowance
    extra_swaps  INTEGER NOT NULL DEFAULT 0,   -- top-ups bought at £3.99 (used after allowance)
    period_start TEXT NOT NULL,                -- allowance resets from here
    updated_at   TEXT
);

-- Affiliates a manager WANTS a contact for.
CREATE TABLE IF NOT EXISTS swap_wants (
    manager_id INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    site_id    INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (manager_id, site_id)
);

-- Affiliates a manager HAS a contact for (and will trade).
CREATE TABLE IF NOT EXISTS swap_haves (
    manager_id INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    site_id    INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (manager_id, site_id)
);

-- A mutual match. Stored canonically with a_manager < b_manager so the same
-- pairing is never duplicated. a_gets_site is what a_manager RECEIVES (b has it,
-- a wants it); b_gets_site is what b_manager receives.
CREATE TABLE IF NOT EXISTS swap_matches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    a_manager    INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    b_manager    INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    a_gets_site  INTEGER NOT NULL REFERENCES sites(id),
    b_gets_site  INTEGER NOT NULL REFERENCES sites(id),
    a_agreed     INTEGER NOT NULL DEFAULT 0,
    b_agreed     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'ready'
                   CHECK (status IN ('ready','completed','void')),
    created_at   TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (a_manager, b_manager, a_gets_site, b_gets_site)
);

-- The transfer record: one row per contact that actually changed hands. This is
-- the operator's audit trail — website + the exact contact string as sent, from
-- whom, to whom. flagged/flag_reason let an operator mark a bad transfer (wrong
-- info) and ban the sender.
CREATE TABLE IF NOT EXISTS swap_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id       INTEGER REFERENCES swap_matches(id) ON DELETE SET NULL,
    from_manager   INTEGER NOT NULL REFERENCES chat_managers(id),
    to_manager     INTEGER NOT NULL REFERENCES chat_managers(id),
    site_id        INTEGER NOT NULL REFERENCES sites(id),
    domain         TEXT NOT NULL,           -- website name, snapshotted at transfer
    contact_snapshot TEXT NOT NULL,         -- email / telegram / teams, exactly as sent
    flagged        INTEGER NOT NULL DEFAULT 0,
    flag_reason    TEXT,
    transferred_at TEXT NOT NULL
);

-- Loyalty: a manager submits an affiliate site that isn't listed yet. Once an
-- operator approves it as genuinely new, the submitter is granted a FREE swap
-- (and the site drops into the New-affiliate review queue). This is the only way
-- to earn swaps without buying — it grows the network.
CREATE TABLE IF NOT EXISTS swap_contributions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id   INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    domain       TEXT NOT NULL,
    country      TEXT,               -- ISO or as-typed
    vertical     TEXT,
    comment      TEXT,               -- "why it matters"
    status       TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected')),
    reward_swaps INTEGER NOT NULL DEFAULT 1,   -- free swaps granted on approval
    site_id      INTEGER REFERENCES sites(id), -- linked once the site is created
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT
);

-- ---------------------------------------------------------------------------
-- ALERTS — a member arms a rule (country + verticals + triggers + delivery).
-- The matcher (radar/alerts.py: evaluate) runs after a refresh: it finds the
-- affiliates in the rule's market/vertical whose state matches a trigger (a new
-- affiliate detected, traffic up, or traffic down) and fires an event, which is
-- "delivered" through the chosen channels. Delivery is the one plug-in point
-- (push / email / Telegram providers); everything else is real.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id  INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,
    country     TEXT,               -- ISO market, or NULL = all markets
    verticals   TEXT NOT NULL,      -- CSV subset of casino,sportsbook,bingo,poker
    triggers    TEXT NOT NULL,      -- CSV subset of new,up,down
    delivery    TEXT NOT NULL,      -- CSV subset of push,email,telegram
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER REFERENCES alert_rules(id) ON DELETE CASCADE,
    manager_id  INTEGER NOT NULL,
    site_id     INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    domain      TEXT,
    event       TEXT NOT NULL,      -- new | up | down
    detail      TEXT,               -- e.g. "+24%" or "published 2h ago"
    delivered   TEXT,               -- CSV of channels the send was dispatched to
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hits_site   ON discovery_hits(site_id);
CREATE INDEX IF NOT EXISTS idx_snap_site   ON traffic_snapshots(site_id, iso_week);
CREATE INDEX IF NOT EXISTS idx_sites_class ON sites(classification);
CREATE INDEX IF NOT EXISTS idx_alert_rules_mgr ON alert_rules(manager_id);
CREATE INDEX IF NOT EXISTS idx_swap_wants_site  ON swap_wants(site_id);
CREATE INDEX IF NOT EXISTS idx_swap_haves_site  ON swap_haves(site_id);
CREATE INDEX IF NOT EXISTS idx_swap_ledger_from ON swap_ledger(from_manager);

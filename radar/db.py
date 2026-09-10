"""SQLite access + schema init. Stdlib only."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Idempotent, in-place schema migrations for an existing DB. Additive only
    (SQLite ALTER ADD COLUMN); safe to run on every init. Returns what changed."""
    applied: list[str] = []
    # site_verticals.source — distinguishes machine-tagged ('auto') verticals
    # from a human back-office override ('human'). Existing rows default to
    # 'auto' so the auto-tagger may correct the bulk-import placeholders.
    if not _column_exists(conn, "site_verticals", "source"):
        conn.execute(
            "ALTER TABLE site_verticals ADD COLUMN source TEXT NOT NULL DEFAULT 'auto'")
        applied.append("site_verticals.source")
    # chat_managers.password_hash — pbkdf2 password store. Nullable so members
    # created by the old flow (or by invitation without a password) still work.
    if not _column_exists(conn, "chat_managers", "password_hash"):
        conn.execute("ALTER TABLE chat_managers ADD COLUMN password_hash TEXT")
        applied.append("chat_managers.password_hash")
    # chat_managers.last_login_at — for the account view; also useful in the
    # back office. Nullable.
    if not _column_exists(conn, "chat_managers", "last_login_at"):
        conn.execute("ALTER TABLE chat_managers ADD COLUMN last_login_at TEXT")
        applied.append("chat_managers.last_login_at")
    # chat_managers.linkedin_sub — the stable LinkedIn user id (OIDC 'sub')
    # returned by their userinfo endpoint. Lets a returning LinkedIn user
    # land back on the same row even if they change their email. Nullable.
    if not _column_exists(conn, "chat_managers", "linkedin_sub"):
        conn.execute("ALTER TABLE chat_managers ADD COLUMN linkedin_sub TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cm_linkedin_sub ON chat_managers(linkedin_sub)")
        applied.append("chat_managers.linkedin_sub")
    # chat_managers.avatar_url — LinkedIn 'picture' claim, or any future
    # avatar upload we support. Nullable; the app falls back to gradient
    # initials when this is empty.
    if not _column_exists(conn, "chat_managers", "avatar_url"):
        conn.execute("ALTER TABLE chat_managers ADD COLUMN avatar_url TEXT")
        applied.append("chat_managers.avatar_url")
    # share_events — tracks the "share on LinkedIn" growth loop. Each row is
    # one honor-system claim; the /api/share/claim endpoint checks the
    # cooldown before inserting.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='share_events'").fetchone() is None:
        conn.executescript(
            "CREATE TABLE share_events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " manager_id INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,"
            " kind TEXT NOT NULL DEFAULT 'linkedin',"
            " reward_swaps INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT NOT NULL);"
            "CREATE INDEX idx_share_events_mgr ON share_events(manager_id, created_at DESC);")
        applied.append("share_events")
    # Non-affiliate domains that made it in as 'affiliate' but shouldn't be
    # in the network. Flip them to 'rejected' — one of the values the
    # existing CHECK constraint on sites.classification allows. Idempotent:
    # once flipped, the SELECT no-ops on subsequent boots.
    # Add new entries to this tuple as needed.
    _NOT_AFFILIATES = ("fotmob.com",)
    for dom in _NOT_AFFILIATES:
        row = conn.execute(
            "SELECT id FROM sites WHERE domain=? AND classification='affiliate'",
            (dom,)).fetchone()
        if row:
            conn.execute(
                "UPDATE sites SET classification='rejected' WHERE domain=?",
                (dom,))
            applied.append(f"rejected {dom}")
    # reviews: individual peer reviews (site_reviews above stays an aggregate cache)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviews'").fetchone() is None:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS reviews ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,"
            " manager_id INTEGER NOT NULL REFERENCES chat_managers(id) ON DELETE CASCADE,"
            " rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5), body TEXT,"
            " status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),"
            " reward_granted INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT);"
            "CREATE INDEX IF NOT EXISTS idx_reviews_site ON reviews(site_id, status);"
            "CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);"
            "CREATE INDEX IF NOT EXISTS idx_reviews_mgr ON reviews(manager_id, status);")
        applied.append("reviews")
    return applied


def init_db(reset: bool = False) -> None:
    if reset and config.DB_PATH.exists():
        config.DB_PATH.unlink()
    conn = connect()
    try:
        conn.executescript(config.SCHEMA_PATH.read_text())
        migrate(conn)
        conn.commit()
    finally:
        conn.close()


def upsert_site(conn: sqlite3.Connection, domain: str, source: str = "api") -> int:
    """Insert a site if new (as a 'candidate'); return its id. Never changes
    an existing row's human-owned fields."""
    row = conn.execute("SELECT id FROM sites WHERE domain = ?", (domain,)).fetchone()
    if row:
        return row["id"]
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO sites (domain, source, created_at, updated_at) VALUES (?,?,?,?)",
        (domain, source, ts, ts),
    )
    return cur.lastrowid


def log_change(
    conn: sqlite3.Connection,
    entity: str,
    entity_id: int,
    field: str,
    old_value,
    new_value,
    owner: str,
    source: str,
) -> None:
    conn.execute(
        """INSERT INTO change_log
           (entity, entity_id, field, old_value, new_value, owner, source, changed_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (entity, entity_id, field,
         None if old_value is None else str(old_value),
         None if new_value is None else str(new_value),
         owner, source, now_iso()),
    )

"""Peer reviews + review loyalty.

A verified manager reviews an affiliate site (rating 1-5 + notes); it enters as
'pending' and an operator approves/rejects it in the back office. Approving
recomputes the site's ★ aggregate (site_reviews) and feeds the loyalty reward:

    every REVIEWS_PER_SWAP approved reviews earns the manager 1 free swap,
    UNLESS they're on the unlimited plan (they already have infinite swaps).

Free swaps are credited through the same primitive the site-contribution reward
uses (swaps.grant_swaps → swap_accounts.extra_swaps). Double-granting is blocked
by the per-review reward_granted flag: a swap is only ever paid for a review that
hasn't paid one before, and rejecting reviews can't farm swaps back.
"""
from __future__ import annotations

import sqlite3

from . import config, ownership, swaps
from .db import now_iso


class ReviewError(RuntimeError):
    pass


def submit(conn: sqlite3.Connection, manager_id: int, site, rating, body=None) -> dict:
    """A verified manager submits a review. Enters as 'pending'. One active
    (pending/approved) review per manager per site."""
    if not swaps._is_verified(conn, int(manager_id)):
        raise ReviewError("only verified managers can review")
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ReviewError("rating must be a whole number 1-5")
    if not 1 <= rating <= 5:
        raise ReviewError("rating must be between 1 and 5")
    try:
        site_id = swaps._site_id(conn, site)
    except swaps.SwapError:
        raise ReviewError("unknown site")
    dup = conn.execute(
        "SELECT 1 FROM reviews WHERE manager_id=? AND site_id=? AND status IN ('pending','approved')",
        (manager_id, site_id)).fetchone()
    if dup:
        raise ReviewError("you've already reviewed this site")
    cur = conn.execute(
        "INSERT INTO reviews (site_id, manager_id, rating, body, created_at) VALUES (?,?,?,?,?)",
        (site_id, manager_id, rating, (body or "").strip() or None, now_iso()))
    conn.commit()
    return {"id": cur.lastrowid, "site_id": site_id, "status": "pending",
            "note": "queued for moderation"}


def _grant_review_loyalty(conn: sqlite3.Connection, manager_id: int) -> dict:
    """Grant any owed review-loyalty swaps to a manager. Idempotent: a swap is
    paid only for approved reviews not yet flagged reward_granted, and never on
    the unlimited plan."""
    n = config.REVIEWS_PER_SWAP
    approved = conn.execute(
        "SELECT COUNT(*) c FROM reviews WHERE manager_id=? AND status='approved'",
        (manager_id,)).fetchone()["c"]
    acct = swaps.ensure_account(conn, manager_id)
    unlimited = swaps.plan_allowance(acct["plan"]) is None
    granted = 0
    if not unlimited:
        already = conn.execute(
            "SELECT COUNT(*) c FROM reviews WHERE manager_id=? AND reward_granted=1",
            (manager_id,)).fetchone()["c"]
        diff = (approved // n) - already
        if diff > 0:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM reviews WHERE manager_id=? AND status='approved' AND reward_granted=0 "
                "ORDER BY resolved_at, id LIMIT ?", (manager_id, diff))]
            for rid in ids:
                conn.execute("UPDATE reviews SET reward_granted=1 WHERE id=?", (rid,))
            if ids:
                swaps.grant_swaps(conn, manager_id, len(ids))  # credits extra_swaps + commits
                granted = len(ids)
    return {"granted": granted, "approved": approved, "unlimited": unlimited,
            "to_next": None if unlimited else (n - approved % n)}


def moderate(conn: sqlite3.Connection, review_id: int, action: str,
             admin: str = "backoffice") -> dict:
    """Approve or reject a pending review. Recomputes the ★ aggregate on any
    change; approving may grant loyalty swaps. Idempotent for already-resolved
    reviews."""
    r = conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
    if not r:
        raise ReviewError("unknown review")
    if r["status"] != "pending":
        return {"status": r["status"], "granted": 0, "note": "already resolved"}
    new = "approved" if action == "approve" else "rejected"
    conn.execute("UPDATE reviews SET status=?, resolved_at=?, resolved_by=? WHERE id=?",
                 (new, now_iso(), admin, review_id))
    ownership.recompute_review_summary(conn, r["site_id"])
    out = {"status": new, "granted": 0, "to_next": None, "approved": None}
    if new == "approved":
        loy = _grant_review_loyalty(conn, r["manager_id"])
        out.update(granted=loy["granted"], to_next=loy["to_next"], approved=loy["approved"])
    conn.commit()
    return out


def loyalty_progress(conn: sqlite3.Connection, manager_id: int) -> dict:
    """The member's loyalty state — both earn paths (reviews + site suggestions)
    on one payload. Loyalty is inactive on the unlimited plan."""
    acct = swaps.ensure_account(conn, manager_id)
    a = swaps.access(conn, manager_id)
    unlimited = bool(a.get("unlimited")) or swaps.plan_allowance(acct["plan"]) is None
    n = config.REVIEWS_PER_SWAP
    approved = conn.execute(
        "SELECT COUNT(*) c FROM reviews WHERE manager_id=? AND status='approved'",
        (manager_id,)).fetchone()["c"]
    contrib = {"pending": 0, "approved": 0, "rejected": 0}
    for row in conn.execute(
            "SELECT status, COUNT(*) c FROM swap_contributions WHERE manager_id=? GROUP BY status",
            (manager_id,)):
        if row["status"] in contrib:
            contrib[row["status"]] = row["c"]
    contrib_swaps = conn.execute(
        "SELECT COALESCE(SUM(reward_swaps),0) r FROM swap_contributions "
        "WHERE manager_id=? AND status='approved'", (manager_id,)).fetchone()["r"]
    review_swaps = conn.execute(
        "SELECT COUNT(*) c FROM reviews WHERE manager_id=? AND reward_granted=1",
        (manager_id,)).fetchone()["c"]  # actually-granted swaps (0 for unlimited)
    return {
        "plan": acct["plan"],
        "loyalty_active": not unlimited,
        "approved_reviews": approved,
        "reviews_to_next_swap": None if unlimited else (n - approved % n),
        "free_swaps_from_reviews": review_swaps,
        "free_swaps_earned": review_swaps + contrib_swaps,
        "contributions": contrib,
        "swaps_left": a.get("swaps"),
    }

"""Back-office web admin — the human-judgement surface over the same DB.

A localhost-only site management panel (stdlib http.server; no dependencies).
Everything it writes goes through the HUMAN-OWNED code path
(ownership.set_classification / upsert_contact), so the weekly API refresh can
never overwrite it. This is the "back office over the same database" from the
brief, scoped to the two things you update as you go: classification and the
three outreach channels (email / Telegram / Teams).

    python3 -m radar admin            # serves http://127.0.0.1:8765
    python3 -m radar admin --port 9000
"""
from __future__ import annotations

import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ownership
from .db import connect, now_iso
from .locations import flag

CHANNELS = ("email", "telegram", "teams")


def _capture_screenshot_if_missing(conn, site_id: int, domain: str) -> bool:
    """On approval a site publishes with a homepage screenshot. Capture one if
    we don't already have it. Returns True if a capture happened."""
    if conn.execute("SELECT 1 FROM screenshots WHERE site_id=?", (site_id,)).fetchone():
        return False
    from .providers.screenshot import ScreenshotClient
    res = ScreenshotClient().capture(f"https://{domain}/", domain)
    conn.execute(
        """INSERT INTO screenshots (site_id, source_url, image_ref, provider, captured_at)
           VALUES (?,?,?,?,?)""",
        (site_id, f"https://{domain}/", res["image_ref"], res["provider"], now_iso()))
    return True


_IMG_PNG = bytes.fromhex("89504e470d0a1a0a")
_IMG_JPEG = bytes.fromhex("ffd8ff")


def _save_manual_screenshot(conn, site_id: int, data_uri: str) -> bool:
    """Store an operator-uploaded homepage image (a `data:image/...;base64,...`
    URI from the upload form) as the site's screenshot, marked provider='manual'
    so the auto-capture NEVER overwrites it — the human-wins rule, applied to
    screenshots. Validates PNG/JPEG magic bytes. Returns True on success."""
    import base64
    from . import config
    if not data_uri:
        return False
    b64 = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
    try:
        raw = base64.b64decode(b64)
    except (ValueError, Exception):
        return False
    if len(raw) < 500 or not (raw[:8] == _IMG_PNG or raw[:3] == _IMG_JPEG):
        return False  # not a real PNG/JPEG
    ext = "jpg" if raw[:3] == _IMG_JPEG else "png"
    dom = conn.execute("SELECT domain FROM sites WHERE id=?", (site_id,)).fetchone()["domain"]
    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.SCREENSHOT_DIR / f"{dom.replace('/', '_')}.{ext}"
    dest.write_bytes(raw)
    conn.execute(
        """INSERT INTO screenshots (site_id, source_url, image_ref, provider, captured_at)
           VALUES (?,?,?,?,?) ON CONFLICT(site_id) DO UPDATE SET
             image_ref=excluded.image_ref, provider=excluded.provider,
             captured_at=excluded.captured_at""",
        (site_id, f"https://{dom}/", str(dest), "manual", now_iso()))
    return True

CSS = """
:root{--bg:#F1F5FB;--card:#fff;--card2:#F4F8FC;--line:#E4EBF4;
 --ink:#14213B;--ink2:#5B6B84;--ink3:#93A0B5;--blue:#4E97E6;--blue-d:#2E77CC;--blue-dd:#205CA6;--blue-050:#ECF3FD;
 --amber:#F5A524;--gold:#2E77CC;--up:#12A150;--down:#E5484D;
 --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;
 --sans:'Manrope',-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;
 background:radial-gradient(900px 500px at 100% -6%,#DCEAFB,transparent 60%),radial-gradient(700px 480px at 0 0,#E7F0FB,transparent 55%),var(--bg)}
.topbar{max-width:1040px;margin:0 auto;padding:18px 24px 0;display:flex;align-items:center;gap:11px}
.brand{display:inline-flex;align-items:center;gap:8px}
.blogo{font-weight:800;font-size:18px;letter-spacing:-.3px;color:var(--ink)}.blogo span{color:var(--blue-d)}
.bo{color:var(--ink3);font:700 11px var(--mono);letter-spacing:.14em;text-transform:uppercase;border-left:1px solid var(--line);padding-left:11px}
.wrap{max-width:1040px;margin:0 auto;padding:20px 24px 80px}
a{color:var(--blue-d);text-decoration:none}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--blue-dd);font-weight:800}
h1{font-size:26px;font-weight:800;letter-spacing:-.02em;margin:8px 0 4px}
.sub{color:var(--ink2);font-size:14px;margin:0 0 8px}
.mode{font-family:var(--mono);font-size:11px;color:var(--ink3)}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 26px}
.stat{background:#fff;border:1px solid var(--line);border-radius:14px;padding:13px 16px;min-width:120px;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.stat .k{font-size:11px;color:var(--ink2);letter-spacing:.04em;text-transform:uppercase}
.stat .v{font-family:var(--mono);font-size:22px;font-weight:700;margin-top:5px}
.stat .v.warn{color:var(--amber)}
table{width:100%;border-collapse:collapse;font-size:13.5px;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}
th{text-align:left;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--ink3);font-weight:800;padding:12px;border-bottom:1px solid var(--line)}
td{padding:12px;border-bottom:1px solid var(--line);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#F7FAFE}
.domain{font-weight:700}
.etv{font-family:var(--mono);font-variant-numeric:tabular-nums}
.src{font-family:var(--mono);font-size:11px;color:var(--ink2)}
.badge{display:inline-block;font-size:10.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;padding:3px 9px;border-radius:999px}
.badge.affiliate{color:var(--up);background:#E6F6EC}
.badge.candidate{color:#9A6400;background:#FCF0D9}
.badge.rejected{color:var(--down);background:#FCEBEC}
.chan{display:inline-flex;gap:5px}
.chip{width:26px;height:22px;border-radius:7px;display:inline-grid;place-items:center;font-size:11px;font-weight:800;border:1px solid var(--line);color:var(--ink3);background:#fff}
.chip.on{color:#fff;background:var(--blue);border-color:transparent}
.editlink{font-size:12.5px}
form.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:22px;max-width:560px;box-shadow:0 10px 30px -18px rgba(16,28,52,.25)}
label{display:block;font-size:12px;color:var(--ink2);font-weight:700;margin:14px 0 6px;letter-spacing:.02em}
input,select{width:100%;background:#fff;border:1px solid var(--line);border-radius:10px;color:var(--ink);font-family:var(--sans);font-size:14px;padding:10px 12px}
input:focus,select:focus{outline:2px solid rgba(78,151,230,.4);border-color:var(--blue)}
.hint{font-size:11.5px;color:var(--ink3);margin-top:5px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.actions{display:flex;gap:12px;margin-top:24px;align-items:center}
button{background:linear-gradient(120deg,#5AA0EA,#2E77CC);color:#fff;border:0;border-radius:11px;font-weight:800;font-size:14px;padding:11px 22px;cursor:pointer;font-family:var(--sans);box-shadow:0 10px 22px -12px rgba(46,119,204,.7)}
.note{background:#E6F6EC;border:1px solid #BFE6CE;color:#0E7A3D;border-radius:11px;padding:11px 14px;font-size:13px;margin-bottom:20px}
.owner-note{font-size:12px;color:var(--ink2);border-left:3px solid var(--blue);background:var(--blue-050);border-radius:0 10px 10px 0;padding:10px 12px;margin:16px 0}
.nav{display:flex;gap:4px;margin:8px 0 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.nav a{padding:10px 13px;font-size:13.5px;font-weight:700;color:var(--ink2);border-bottom:2px solid transparent;margin-bottom:-1px}
.nav a.active{color:var(--blue-d);border-bottom-color:var(--blue)}
.nav a:hover{color:var(--ink)}
.nav .count{font-family:var(--mono);font-size:11px;background:var(--blue);color:#fff;border-radius:999px;padding:1px 7px;margin-left:6px;font-weight:800}
.qcard{display:flex;gap:16px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:12px;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.qthumb{width:96px;height:64px;border-radius:10px;flex:0 0 auto;display:grid;place-items:center;font-weight:800;color:#fff;font-size:20px;background:linear-gradient(150deg,#5AA0EA,#2E77CC)}
.qmain{flex:1;min-width:0}
.qdomain{font-size:16px;font-weight:800}
.qmeta{font-size:12px;color:var(--ink2);margin-top:5px;display:flex;gap:14px;flex-wrap:wrap}
.qmeta b{color:var(--ink);font-family:var(--mono);font-weight:700}
.qmeta .ev{color:var(--ink3)}
.ev{color:var(--ink3)}
.mono{font-family:var(--mono)}
.qactions{display:flex;gap:9px;flex:0 0 auto}
.btn{border:0;border-radius:10px;font-weight:800;font-size:13px;padding:10px 16px;cursor:pointer;font-family:var(--sans)}
.btn.approve{background:var(--up);color:#fff}
.btn.reject{background:#fff;color:var(--down);border:1px solid #F3B4B2}
.btn.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}
.empty{text-align:center;color:var(--ink3);padding:60px 20px;font-size:14px}
.empty .big{font-size:34px;margin-bottom:10px}
.import-grid{display:grid;grid-template-columns:1fr;gap:18px;max-width:820px}
textarea{width:100%;min-height:180px;background:#fff;border:1px solid var(--line);border-radius:12px;color:var(--ink);font-family:var(--mono);font-size:12.5px;padding:12px;line-height:1.5;resize:vertical}
.filepick{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.filepick label.file{background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 14px;font-size:13px;font-weight:700;cursor:pointer;color:var(--ink)}
.filepick input[type=file]{display:none}
.cols{font-size:12.5px;color:var(--ink2);line-height:1.9}
.cols code{font-family:var(--mono);color:var(--blue-d);background:var(--blue-050);padding:1px 6px;border-radius:5px}
.cols .req{color:var(--up)}
.pv-counts{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 18px}
.pv{background:#fff;border:1px solid var(--line);border-radius:12px;padding:11px 16px;min-width:96px}
.pv .k{font-size:11px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}
.pv .v{font-family:var(--mono);font-size:20px;font-weight:700;margin-top:4px}
.pv.create .v{color:var(--up)} .pv.update .v{color:var(--amber)} .pv.error .v{color:var(--down)}
.rowbadge{font-size:10.5px;font-weight:800;letter-spacing:.03em;text-transform:uppercase;padding:2px 8px;border-radius:999px}
.rowbadge.create{color:var(--up);background:#E6F6EC}
.rowbadge.update{color:#9A6400;background:#FCF0D9}
.rowbadge.error{color:var(--down);background:#FCEBEC}
"""


def _nav(active: str, queue_count: int) -> str:
    def tab(key, href, label, badge=""):
        cls = "active" if active == key else ""
        return f"<a class='{cls}' href='{href}'>{label}{badge}</a>"
    qbadge = f"<span class='count'>{queue_count}</span>" if queue_count else ""
    _SUB = ("(SELECT site_id FROM swap_contributions WHERE status='pending' "
            "AND site_id IS NOT NULL)")
    return ("<div class='nav'>"
            + tab("queue", "/", "New affiliate · SEO", _count_badge(
                f"SELECT COUNT(*) n FROM sites WHERE classification='candidate' AND id NOT IN {_SUB}"))
            + tab("subs", "/queue-members", "New affiliate · Submitted", _count_badge(
                f"SELECT COUNT(*) n FROM sites WHERE classification='candidate' AND id IN {_SUB}"))
            + tab("members", "/members", "Member approvals", _count_badge(
                "SELECT COUNT(*) n FROM chat_managers WHERE status='pending' "
                "AND linkedin_verified=1 AND work_email_confirmed=1"))
            + tab("sites", "/sites", "All sites")
            + tab("shots", "/screenshots", "Homepages", _count_badge(
                "SELECT COUNT(*) n FROM sites s WHERE s.classification='affiliate' "
                "AND s.id NOT IN (SELECT site_id FROM blacklist) "
                "AND s.id NOT IN (SELECT site_id FROM screenshots WHERE provider<>'mock') "
                "AND EXISTS (SELECT 1 FROM site_regions rg WHERE rg.site_id=s.id "
                "AND rg.etv > (CASE WHEN s.etv>=10000 THEN 1500 ELSE 500 END))"))
            + tab("import", "/import", "Bulk import")
            + tab("blacklist", "/blacklist", "Blacklist")
            + tab("chat", "/chat", "Chat reports")
            + tab("reviews", "/reviews", "Reviews", _count_badge(
                "SELECT COUNT(*) n FROM reviews WHERE status='pending'"))
            + tab("swaps", "/swaps", "Swaps ledger")
            + tab("rewards", "/rewards", "Rewards", _count_badge(
                "SELECT COUNT(*) n FROM swap_contributions WHERE status='pending'"))
            + "</div>")


def _count_badge(sql: str) -> str:
    # cheap pending-count badge for a nav tab
    try:
        conn = connect()
        try:
            n = conn.execute(sql).fetchone()["n"]
        finally:
            conn.close()
    except Exception:
        n = 0
    return f"<span class='count'>{n}</span>" if n else ""


FAVICON = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0NCA0NCIgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByb2xlPSJpbWciIGFyaWEtbGFiZWw9IkFmZnN3YXAiPgogIDxkZWZzPgogICAgPGxpbmVhckdyYWRpZW50IGlkPSJ0aWxlIiB4MT0iMCIgeTE9IjAiIHgyPSIxIiB5Mj0iMSI+CiAgICAgIDxzdG9wIG9mZnNldD0iMCIgc3RvcC1jb2xvcj0iIzVBQTBFQSIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMyRTc3Q0MiLz4KICAgIDwvbGluZWFyR3JhZGllbnQ+CiAgPC9kZWZzPgogIDxyZWN0IHdpZHRoPSI0NCIgaGVpZ2h0PSI0NCIgcng9IjExIiBmaWxsPSJ1cmwoI3RpbGUpIi8+CiAgPHBhdGggZD0iTTExIDkgTDE4IDIyIEwxMSAzNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjRkZGRkZGIiBzdHJva2Utd2lkdGg9IjQuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CiAgPHBhdGggZD0iTTMzIDkgTDI2IDIyIEwzMyAzNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjQkJEOEY4IiBzdHJva2Utd2lkdGg9IjQuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+Cjwvc3ZnPgo="
MARK_SVG = ("<svg viewBox='0 0 44 44' width='24' height='24' fill='none' stroke-linecap='round' "
            "stroke-linejoin='round' stroke-width='4.8' style='flex:0 0 auto'>"
            "<path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/>"
            "<path d='M33 9 L26 22 L33 35' stroke='#7FB4F2'/></svg>")


def _page(body: str, title: str = "Affswap · Back office") -> bytes:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<link rel='icon' type='image/svg+xml' href='{FAVICON}'>"
            f"<link rel='preconnect' href='https://fonts.googleapis.com'>"
            f"<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>"
            f"<title>{title}</title><style>{CSS}</style></head><body>"
            f"<div class='topbar'><span class='brand'>{MARK_SVG}"
            f"<span class='blogo'>Aff<span>swap</span></span></span><span class='bo'>Back office</span></div>"
            f"<div class='wrap'>{body}</div></body></html>").encode()


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _queue_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) n FROM sites WHERE classification='candidate'").fetchone()["n"]


def _manager_row(conn, handle):
    if not handle:
        return None
    return conn.execute("SELECT * FROM chat_managers WHERE handle=? OR cc_uid=?",
                        (handle, handle)).fetchone()


def _queue_page(conn, kind: str = "seo", flash: str = "") -> bytes:
    """The review queue, split two ways: 'seo' = candidates DataForSEO discovered;
    'submitted' = candidates a member recommended (a pending swap_contribution).
    Both are candidates awaiting the same human approval — approving a submitted
    one also grants the submitter +1 swap."""
    from . import config, swaps
    sub = kind == "submitted"
    membership = ("id IN" if sub else "id NOT IN") + \
        " (SELECT site_id FROM swap_contributions WHERE status='pending' AND site_id IS NOT NULL)"
    rows = conn.execute(f"""
        SELECT s.id, s.domain, s.etv, s.top_country, s.rank_best,
               (SELECT COUNT(*) FROM discovery_hits h WHERE h.site_id=s.id) AS hits,
               (SELECT h2.keyword FROM discovery_hits h2 WHERE h2.site_id=s.id
                  ORDER BY h2.rank_absolute LIMIT 1) AS top_keyword,
               (SELECT 1 FROM screenshots sc WHERE sc.site_id=s.id) AS has_shot
        FROM sites s
        WHERE s.classification='candidate' AND s.{membership}
        ORDER BY (s.etv IS NULL), s.etv DESC
    """).fetchall()
    subs_by_site = {c["site_id"]: c for c in swaps.list_contributions(conn, "pending")} if sub else {}

    h1 = "New affiliates · member-submitted" if sub else "New affiliates · SEO discovery"
    intro = ("Affiliates that members recommended for a free swap — a manager is vouching, so these "
             "are high-signal. Each is still a candidate: approving publishes the site <b>and</b> grants "
             "the submitter +1 swap; rejecting pays nothing." if sub else
             "Domains DataForSEO found ranking for your keywords — most aren't affiliates. Confirm the "
             "real ones here. Approving publishes the site to the app with its traffic and screenshot; "
             "rejecting hides it for good.")
    back = "/queue-members" if sub else "/"
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        f"<h1>{h1}</h1>",
        f"<p class='sub'>{intro}</p>",
        f"<p class='mode'>{config.mode_banner()}</p>",
        _nav("subs" if sub else "queue", 0),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not rows:
        msg = ("No member submissions waiting." if sub
               else "Queue is clear — every discovered site has been reviewed.")
        body.append(f"<div class='empty'><div class='big'>{'🎁' if sub else '✓'}</div>{msg}</div>")

    for r in rows:
        etv = f"{int(r['etv']):,}/mo" if r["etv"] else "no traffic yet"
        fl = flag(r["top_country"]) if r["top_country"] else ""
        rank = f"#{r['rank_best']}" if r["rank_best"] else "—"
        shot = "screenshot ready" if r["has_shot"] else "screenshot on approve"
        if sub:
            c = subs_by_site.get(r["id"], {})
            mkt = (flag(c.get("country")) + " " + c["country"]) if c.get("country") else "—"
            meta = (f"<span>submitted by <b>{_esc(c.get('by', '—'))}</b></span>"
                    f"<span class='ev'>{_esc(mkt)} · {_esc(c.get('vertical') or '—')}</span>"
                    f"<span class='ev' style='color:var(--gold)'>+{c.get('reward', 1)} swap on approval</span>"
                    f"<span class='ev'>{shot}</span>")
            reason = (f"<div class='freason' style='margin-top:8px;font-size:12.5px;color:var(--ink)'>"
                      f"<span style='font-family:var(--mono);font-size:9px;letter-spacing:.08em;color:var(--ink3);font-weight:700'>WHY IT MATTERS</span><br>"
                      f"{_esc(c.get('comment'))}</div>") if c.get("comment") else ""
        else:
            meta = (f"<span>{fl} <b>{etv}</b></span><span>best rank <b>{rank}</b></span>"
                    f"<span class='ev'>ranks for “{_esc(r['top_keyword'] or '—')}” · {r['hits']} SERP hits</span>"
                    f"<span class='ev'>{shot}</span>")
            reason = ""
        body.append(
            f"<div class='qcard'>"
            f"<div class='qthumb'>{'🎁' if sub else _esc(r['domain'][0].upper())}</div>"
            f"<div class='qmain'><div class='qdomain'>{_esc(r['domain'])}</div>"
            f"<div class='qmeta'>{meta}</div>{reason}</div>"
            f"<div class='qactions'>"
            f"<form method='post' action='/review?id={r['id']}'>"
            f"<input type='hidden' name='action' value='approve'>"
            f"<input type='hidden' name='from' value='{back}'>"
            f"<button class='btn approve' type='submit'>✓ Approve{' · +swap' if sub else ''}</button></form>"
            f"<form method='post' action='/review?id={r['id']}'>"
            f"<input type='hidden' name='action' value='reject'>"
            f"<input type='hidden' name='from' value='{back}'>"
            f"<button class='btn reject' type='submit'>Reject</button></form>"
            f"<a class='btn secondary' href='/site?id={r['id']}'>Details</a>"
            f"</div></div>")
    return _page("".join(body), title=h1)


def _list_page(conn, flash: str = "") -> bytes:
    from . import config
    rows = conn.execute("""
        SELECT s.id, s.domain, s.classification, s.source, s.etv, s.top_country,
               c.contact_email, c.contact_telegram, c.contact_teams
        FROM sites s LEFT JOIN site_contacts c ON c.site_id = s.id
        ORDER BY (s.etv IS NULL), s.etv DESC
    """).fetchall()

    total = len(rows)
    affiliates = sum(1 for r in rows if r["classification"] == "affiliate")
    candidates = sum(1 for r in rows if r["classification"] == "candidate")
    missing = sum(1 for r in rows if not (r["contact_email"] or r["contact_telegram"]
                                          or r["contact_teams"]))

    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>All sites</h1>",
        "<p class='sub'>Every site in the catalogue. Classify, and add outreach channels. Everything you "
        "save here is human-owned — the weekly API refresh updates traffic only and never touches it.</p>",
        f"<p class='mode'>{config.mode_banner()}</p>",
        _nav("sites", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    body.append(
        "<div class='stats'>"
        f"<div class='stat'><div class='k'>Sites</div><div class='v'>{total}</div></div>"
        f"<div class='stat'><div class='k'>Affiliates</div><div class='v'>{affiliates}</div></div>"
        f"<div class='stat'><div class='k'>Candidates</div><div class='v'>{candidates}</div></div>"
        f"<div class='stat'><div class='k'>Missing contact</div>"
        f"<div class='v {'warn' if missing else ''}'>{missing}</div></div>"
        "</div>")
    body.append(
        "<form method='post' action='/refresh' style='margin:0 0 10px'>"
        "<button class='btn secondary' type='submit'>↻ Refresh traffic now</button>"
        "<span class='hint' style='margin-left:11px'>Re-pulls DataForSEO for every site and re-tags "
        "the markets it draws traffic from. Runs automatically once a week.</span></form>")
    body.append(
        "<form method='post' action='/sheets-sync' style='margin:0 0 16px'>"
        "<button class='btn secondary' type='submit'>⤓ Sync to Google Sheet</button>"
        "<span class='hint' style='margin-left:11px'>Mirrors the whole catalogue (with per-geo traffic) "
        "and the swaps log to your Google Sheet · "
        f"{'LIVE' if config.sheets_is_live() else 'MOCK — writes local CSVs'}. Swaps also update the sheet live as they happen.</span></form>")

    body.append("<table><thead><tr><th>Domain</th><th>Status</th><th>Traffic</th>"
                "<th>Markets</th><th>Source</th><th>Channels</th><th></th></tr></thead><tbody>")
    for r in rows:
        etv = f"{int(r['etv']):,}" if r["etv"] else "—"
        fl = flag(r["top_country"]) if r["top_country"] else ""
        regs = conn.execute("SELECT country, is_primary FROM site_regions WHERE site_id=? "
                            "ORDER BY is_primary DESC, etv DESC", (r["id"],)).fetchall()
        markets = (" ".join(flag(x["country"]) for x in regs)
                   + (f" <span class='ev'>{len(regs)}</span>" if len(regs) > 1 else "")) if regs else "—"
        chips = "".join(
            f"<span class='chip {'on' if r['contact_'+ch] else ''}' "
            f"title='{ch}{': '+_esc(r['contact_'+ch]) if r['contact_'+ch] else ' — not listed'}'>"
            f"{ch[0].upper()}</span>"
            for ch in CHANNELS)
        body.append(
            f"<tr><td class='domain'>{_esc(r['domain'])}</td>"
            f"<td><span class='badge {r['classification']}'>{r['classification']}</span></td>"
            f"<td class='etv'>{fl} {etv}</td>"
            f"<td>{markets}</td>"
            f"<td class='src'>{r['source']}</td>"
            f"<td><span class='chan'>{chips}</span></td>"
            f"<td class='editlink'><a href='/site?id={r['id']}'>Edit ›</a></td></tr>")
    body.append("</tbody></table>")
    return _page("".join(body))


def _edit_page(conn, site_id: int) -> bytes:
    s = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if not s:
        return _page("<h1>Not found</h1><a href='/'>← back</a>")
    c = conn.execute("SELECT * FROM site_contacts WHERE site_id=?", (site_id,)).fetchone()

    def opt(v):
        sel = " selected" if s["classification"] == v else ""
        return f"<option value='{v}'{sel}>{v}</option>"

    def cv(col):
        return _esc(c[col]) if c and c[col] is not None else ""

    from .branding import display_name as _brand
    derived = _brand(s["domain"])

    regs = conn.execute("SELECT country, etv, is_primary FROM site_regions WHERE site_id=? "
                        "ORDER BY etv DESC", (site_id,)).fetchall()
    if regs:
        chips = " ".join(
            f"<span style='display:inline-block;background:var(--card2);border:1px solid "
            f"{'var(--gold)' if x['is_primary'] else 'var(--line)'};border-radius:999px;"
            f"padding:2px 9px;margin:0 5px 5px 0;font-size:11.5px'>{flag(x['country'])} "
            f"{_esc(x['country'])} · {int(x['etv']):,}/mo{' ★' if x['is_primary'] else ''}</span>"
            for x in regs)
        regions_note = (f"<div class='owner-note'><b>Traffic markets</b> (API-owned · from the weekly "
                        f"refresh · ★ primary): the site files under each of these.<br>{chips}</div>")
    else:
        regions_note = ("<div class='owner-note'>No traffic markets tagged yet — these populate on the "
                        "next refresh (hit <b>↻ Refresh traffic now</b> on All sites, or wait for the weekly run).</div>")

    # verticals — auto-tagged unless a human has overridden them here
    vrows = conn.execute("SELECT vertical, source FROM site_verticals WHERE site_id=?",
                         (site_id,)).fetchall()
    verts = {r["vertical"] for r in vrows}
    v_human = any(r["source"] == "human" for r in vrows)
    vboxes = "".join(
        "<label style='display:inline-flex;align-items:center;gap:6px;margin:0 16px 6px 0;"
        f"font-weight:400'><input type='checkbox' name='vertical' value='{v}'"
        f"{' checked' if v in verts else ''}> {v}</label>"
        for v in ("casino", "sportsbook", "bingo", "poker"))
    vhint = ("<b>Human-set</b> — auto-tagging will never change these." if v_human
             else "Auto-tagged from the domain. Tick/untick and <b>Save</b> to set them yourself — "
                  "your choice is then locked from future auto-tagging.")

    # homepage screenshot (embedded so no static route is needed)
    shot_html = ""
    shot = conn.execute("SELECT image_ref, provider FROM screenshots WHERE site_id=?",
                        (site_id,)).fetchone()
    if shot and shot["provider"] and shot["provider"] != "mock":
        try:
            import base64 as _b64
            with open(shot["image_ref"], "rb") as fh:
                raw = fh.read()
            if len(raw) > 500:
                mime = "image/jpeg" if raw[:3] == bytes.fromhex("ffd8ff") else "image/png"
                uri = f"data:{mime};base64," + _b64.b64encode(raw).decode()
                lbl = ("<b style='color:var(--gold)'>manually uploaded</b>" if shot["provider"] == "manual"
                       else f"auto-captured · {_esc(shot['provider'])}")
                shot_html = (f"<div class='owner-note'><b>Homepage</b> ({lbl}):"
                             f"<br><img src='{uri}' alt='' style='margin-top:7px;width:100%;max-width:540px;"
                             f"border-radius:11px;border:1px solid var(--line)'></div>")
        except OSError:
            pass

    # client-side: read the chosen image file into the hidden base64 field so the
    # normal urlencoded form carries it (no multipart parsing needed server-side).
    upload_js = ("<script>(function(){var f=document.getElementById('shotfile'),"
                 "h=document.getElementById('shotb64'),n=document.getElementById('shotname');"
                 "if(!f)return;f.addEventListener('change',function(){var x=f.files&&f.files[0];"
                 "if(!x)return;if(x.size>6000000){alert('Image too large (max ~6MB).');f.value='';return;}"
                 "var r=new FileReader();r.onload=function(){h.value=r.result;"
                 "if(n)n.textContent='Ready to save: '+x.name;};r.readAsDataURL(x);});})();</script>")

    body = f"""
      <div class='eyebrow'><a href='/'>← Back office</a></div>
      <h1>{_esc(s['domain'])}</h1>
      <p class='sub'>Traffic & trend are API-owned (read-only here). You own the name, classification and contact.</p>
      {shot_html}
      {regions_note}
      <div class='owner-note'>Saving writes through the human-owned path. The next weekly refresh
        will update this site's traffic but leave everything below exactly as you set it.</div>
      <form class='card' method='post' action='/site?id={site_id}'>
        <label>Website name <span class='hint' style='font-weight:400'>— shown on every list button; the URL above is saved for the profile</span></label>
        <input name='display_name' value='{_esc(s['display_name'] or '')}' placeholder='{_esc(derived)} (auto)'>
        <label>Classification</label>
        <select name='classification'>{opt('candidate')}{opt('affiliate')}{opt('rejected')}</select>
        <div class='hint'>Only <b>affiliate</b> sites are shown to subscribers and can trigger alerts.</div>
        <label>Verticals</label>
        <div style='margin:3px 0 2px'>{vboxes}</div>
        <div class='hint'>{vhint}</div>
        <label>Homepage screenshot <span class='hint' style='font-weight:400'>— upload manually if auto-capture is blocked</span></label>
        <input type='file' accept='image/png,image/jpeg' id='shotfile' style='color:var(--ink2)'>
        <input type='hidden' name='screenshot_b64' id='shotb64'>
        <div class='hint' id='shotname'>Some sites (Cloudflare / anti-bot) block auto-capture. Upload a PNG/JPG — it's saved as <b>manual</b> and the weekly capture won't overwrite it.</div>
        <div class='row2'>
          <div><label>Company name</label><input name='company_name' value='{cv('company_name')}'></div>
          <div><label>Contact name</label><input name='contact_name' value='{cv('contact_name')}'></div>
        </div>
        <label>Email</label>
        <input name='contact_email' value='{cv('contact_email')}' placeholder='deals@example.com'>
        <label>Telegram</label>
        <input name='contact_telegram' value='{cv('contact_telegram')}' placeholder='@handle'>
        <label>Teams</label>
        <input name='contact_teams' value='{cv('contact_teams')}' placeholder='email or org link'>
        <div class='hint'>Leave a channel blank if you don't have it — its button greys out in the app.</div>
        <div class='actions'><button type='submit'>Save</button><a href='/'>Cancel</a></div>
      </form>
      {upload_js}
    """
    return _page(body, title=f"Edit · {s['domain']}")


def _screenshots_page(conn, flash: str = "") -> bytes:
    """The Homepages queue — visible affiliates that still lack a real homepage
    image (usually anti-bot sites that blocked auto-capture). Each row links out
    to the live site and takes an inline manual upload."""
    from .branding import display_name as _brand
    rows = conn.execute(
        """SELECT s.id, s.domain, s.display_name, s.etv, s.top_country
           FROM sites s
           WHERE s.classification='affiliate'
             AND s.id NOT IN (SELECT site_id FROM blacklist)
             AND s.id NOT IN (SELECT site_id FROM screenshots WHERE provider <> 'mock')
             AND EXISTS (SELECT 1 FROM site_regions rg WHERE rg.site_id=s.id
                         AND rg.etv > (CASE WHEN s.etv>=10000 THEN 1500 ELSE 500 END))
           ORDER BY (s.etv IS NULL), s.etv DESC""").fetchall()
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Homepages · missing screenshots</h1>",
        "<p class='sub'>Visible affiliates that don't have a real homepage image yet — usually "
        "anti-bot sites that blocked auto-capture. <b>Open the live site</b>, grab a screenshot, and "
        "<b>upload</b> it here. An uploaded image is marked <b>manual</b>, shows on the site instantly, "
        "and the weekly capture never overwrites it.</p>",
        f"<p class='mode'><b>{len(rows)}</b> site(s) need a homepage image.</p>",
        _nav("shots", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not rows:
        body.append("<div class='empty'><div class='big'>🖼️</div>"
                    "Every visible site has a homepage image.</div>")
        return _page("".join(body), title="Homepages")

    a_style = ("display:inline-block;padding:8px 13px;border:1px solid var(--line);border-radius:9px;"
               "color:var(--ink);text-decoration:none;font-weight:600;font-size:13px")
    up_style = ("display:inline-block;padding:8px 13px;border:1px solid var(--gold);border-radius:9px;"
                "color:var(--gold);cursor:pointer;font-weight:600;font-size:13px")
    for r in rows:
        name = _brand(r["domain"], r["display_name"])
        url = f"https://{_esc(r['domain'])}/"
        et = f"{int(r['etv']):,}" if r["etv"] else "—"
        fl = flag(r["top_country"]) if r["top_country"] else "🏳️"
        body.append(
            "<div class='qcard'><div class='qmain'>"
            f"<div class='qdomain'>{_esc(name)} <span class='ev'>· {_esc(r['domain'])}</span></div>"
            f"<div class='qmeta'><span>{fl} {et}/mo</span></div></div>"
            "<div style='display:flex;gap:8px;align-items:center'>"
            f"<a href='{url}' target='_blank' rel='noopener noreferrer' style='{a_style}'>Open site ↗</a>"
            f"<label style='{up_style}'>Upload image<input type='file' accept='image/png,image/jpeg' "
            f"data-id='{r['id']}' style='display:none'></label>"
            "</div></div>")
    body.append(
        "<script>document.querySelectorAll('input[type=file][data-id]').forEach(function(inp){"
        "inp.addEventListener('change',function(){var f=inp.files&&inp.files[0];if(!f)return;"
        "if(f.size>6000000){alert('Image too large (max ~6MB)');inp.value='';return;}"
        "var lbl=inp.closest('label');if(lbl)lbl.firstChild.textContent='Uploading…';"
        "var rd=new FileReader();rd.onload=function(){"
        "var b='id='+encodeURIComponent(inp.dataset.id)+'&screenshot_b64='+encodeURIComponent(rd.result);"
        "fetch('/screenshot-upload',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:b})"
        ".then(function(res){if(res.ok){location.href='/screenshots?flash='+encodeURIComponent('Homepage uploaded \\u2713 — marked manual, safe from re-capture.');}"
        "else{alert('Upload failed — is it a PNG/JPG under 6MB?');location.reload();}});};rd.readAsDataURL(f);});});</script>")
    return _page("".join(body), title="Homepages")


def _import_page(conn, csv_text: str = "", flash: str = "") -> bytes:
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Bulk import</h1>",
        "<p class='sub'>Upload a CSV to add sites or update existing ones (matched by domain). "
        "Import only writes human-owned data — classification, market, verticals and contact "
        "channels — so it never collides with the API refresh.</p>",
        _nav("import", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    body.append(
        "<div class='import-grid'>"
        "<form method='post' action='/import/preview'>"
        "<div class='filepick'>"
        "<label class='file'>Choose CSV file<input type='file' accept='.csv,text/csv' "
        "onchange=\"const f=this.files[0]; if(f){const r=new FileReader(); "
        "r.onload=e=>document.getElementById('csv').value=e.target.result; r.readAsText(f);}\"></label>"
        "<span class='hint'>…or paste below. <a href='/import/sample'>Download template</a></span>"
        "</div>"
        f"<textarea id='csv' name='csv' placeholder='Paste CSV here…'>{_esc(csv_text)}</textarea>"
        "<div class='actions'><button type='submit'>Preview import</button></div>"
        "</form>"
        "<div class='cols'><b>Columns</b> (case-insensitive; only url is required):<br>"
        "<code class='req'>url</code> the site (domain or full URL) · "
        "<code>country</code> ISO or name · <code>vertical</code> casino/sportsbook/bingo/poker · "
        "<code>company</code> · <code>contact_name</code> · <code>email</code> · "
        "<code>telegram</code> · <code>teams</code> · <code>status</code> affiliate/candidate/rejected "
        "(default affiliate)</div>"
        "</div>")
    return _page("".join(body), title="Bulk import")


def _preview_page(conn, csv_text: str) -> bytes:
    from . import importer
    rows, errs = importer.parse_csv(csv_text)
    if errs:
        return _import_page(conn, csv_text=csv_text, flash=errs[0])
    planned = importer.plan(conn, rows)
    n_create = sum(1 for p in planned if p["action"] == "create")
    n_update = sum(1 for p in planned if p["action"] == "update")
    n_error = sum(1 for p in planned if p["action"] == "error")

    body = [
        "<div class='eyebrow'><a href='/import'>← Bulk import</a></div>",
        "<h1>Preview</h1>",
        "<p class='sub'>Review before committing. Rows with errors are skipped; everything else is applied.</p>",
        _nav("import", _queue_count(conn)),
        "<div class='pv-counts'>"
        f"<div class='pv create'><div class='k'>Create</div><div class='v'>{n_create}</div></div>"
        f"<div class='pv update'><div class='k'>Update</div><div class='v'>{n_update}</div></div>"
        f"<div class='pv error'><div class='k'>Skip (errors)</div><div class='v'>{n_error}</div></div>"
        "</div>",
        "<table><thead><tr><th>#</th><th>Domain</th><th>Action</th><th>Details</th></tr></thead><tbody>",
    ]
    for p in planned:
        dom = _esc(p["domain"] or p["raw_url"])
        body.append(
            f"<tr><td class='src'>{p['row']}</td><td class='domain'>{dom}</td>"
            f"<td><span class='rowbadge {p['action']}'>{p['action']}</span></td>"
            f"<td class='src' style='color:{'var(--down)' if p['action']=='error' else 'var(--ink2)'}'>{_esc(p['message'])}</td></tr>")
    body.append("</tbody></table>")

    if n_create or n_update:
        body.append(
            "<form method='post' action='/import/commit' style='margin-top:22px'>"
            f"<textarea name='csv' style='display:none'>{_esc(csv_text)}</textarea>"
            f"<div class='actions'><button type='submit'>Commit {n_create + n_update} rows</button>"
            "<a href='/import'>Back</a></div></form>")
    else:
        body.append("<div class='actions' style='margin-top:22px'><a class='cbtn secondary' "
                    "href='/import' style='padding:10px 16px;border-radius:10px'>Back</a></div>")
    return _page("".join(body), title="Import preview")


def _blacklist_page(conn, csv_text: str = "", flash: str = "") -> bytes:
    rows = conn.execute(
        """SELECT s.id, s.domain, b.reason, b.added_at, c.company_name
           FROM blacklist b JOIN sites s ON s.id=b.site_id
           LEFT JOIN site_contacts c ON c.site_id=s.id
           ORDER BY b.added_at DESC""").fetchall()
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Blacklist</h1>",
        "<p class='sub'>Flagged affiliates shown to users on a warning screen with the reason. "
        "Blacklisted sites are removed from every recommended list. Upload is human-owned — a refresh never clears it.</p>",
        _nav("blacklist", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    # upload form
    body.append(
        "<div class='import-grid'>"
        "<form method='post' action='/blacklist/preview'>"
        "<div class='filepick'>"
        "<label class='file'>Choose CSV file<input type='file' accept='.csv,text/csv' "
        "onchange=\"const f=this.files[0]; if(f){const r=new FileReader(); "
        "r.onload=e=>document.getElementById('csv').value=e.target.result; r.readAsText(f);}\"></label>"
        "<span class='hint'>columns: <code style='font-family:var(--mono);color:var(--gold)'>url, reason</code>"
        " (company optional). <a href='/blacklist/sample'>Download template</a></span>"
        "</div>"
        f"<textarea id='csv' name='csv' placeholder='url,reason,company&#10;bad-affiliate.example,\"reason here\",'>{_esc(csv_text)}</textarea>"
        "<div class='actions'><button type='submit'>Preview blacklist</button></div>"
        "</form></div>")
    # current list
    body.append(f"<h2 style='font-size:16px;margin:26px 0 12px'>Currently blacklisted ({len(rows)})</h2>")
    if not rows:
        body.append("<div class='empty'>Nothing blacklisted yet.</div>")
    else:
        body.append("<table><thead><tr><th>Domain</th><th>Reason</th><th>Company</th><th></th></tr></thead><tbody>")
        for r in rows:
            body.append(
                f"<tr><td class='domain'>{_esc(r['domain'])}</td>"
                f"<td style='max-width:360px'>{_esc(r['reason'])}</td>"
                f"<td class='src'>{_esc(r['company_name'] or '—')}</td>"
                f"<td><form method='post' action='/blacklist/remove?id={r['id']}'>"
                f"<button class='btn reject' type='submit'>Remove</button></form></td></tr>")
        body.append("</tbody></table>")
    return _page("".join(body), title="Blacklist")


def _blacklist_preview_page(conn, csv_text: str) -> bytes:
    from . import importer
    rows, errs = importer.parse_blacklist_csv(csv_text)
    if errs:
        return _blacklist_page(conn, csv_text=csv_text, flash=errs[0])
    planned = importer.plan_blacklist(conn, rows)
    n_create = sum(1 for p in planned if p["action"] == "create")
    n_update = sum(1 for p in planned if p["action"] == "update")
    n_error = sum(1 for p in planned if p["action"] == "error")
    body = [
        "<div class='eyebrow'><a href='/blacklist'>← Blacklist</a></div>",
        "<h1>Preview blacklist</h1>",
        _nav("blacklist", _queue_count(conn)),
        "<div class='pv-counts'>"
        f"<div class='pv create'><div class='k'>Add</div><div class='v'>{n_create}</div></div>"
        f"<div class='pv update'><div class='k'>Update reason</div><div class='v'>{n_update}</div></div>"
        f"<div class='pv error'><div class='k'>Skip</div><div class='v'>{n_error}</div></div>"
        "</div>",
        "<table><thead><tr><th>#</th><th>Domain</th><th>Action</th><th>Reason</th></tr></thead><tbody>",
    ]
    for p in planned:
        body.append(
            f"<tr><td class='src'>{p['row']}</td><td class='domain'>{_esc(p['domain'] or p['raw_url'])}</td>"
            f"<td><span class='rowbadge {p['action']}'>{p['action']}</span></td>"
            f"<td class='src' style='color:{'var(--down)' if p['action']=='error' else 'var(--ink2)'};max-width:340px'>{_esc(p['message'])}</td></tr>")
    body.append("</tbody></table>")
    if n_create or n_update:
        body.append(
            "<form method='post' action='/blacklist/commit' style='margin-top:22px'>"
            f"<textarea name='csv' style='display:none'>{_esc(csv_text)}</textarea>"
            f"<div class='actions'><button type='submit'>Commit {n_create + n_update} rows</button>"
            "<a href='/blacklist'>Back</a></div></form>")
    else:
        body.append("<div class='actions' style='margin-top:22px'><a href='/blacklist'>Back</a></div>")
    return _page("".join(body), title="Blacklist preview")


def _chat_page(conn, flash: str = "") -> bytes:
    from . import config
    from .chat import service
    reports = service.list_reports(conn, "open")
    # resolve reported uid -> real identity (operator-only, from OUR db)
    def real(uid):
        r = conn.execute("SELECT handle, real_name FROM chat_managers WHERE cc_uid=?", (uid,)).fetchone()
        return f"{r['handle']} ({r['real_name']})" if r else uid
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Chat reports</h1>",
        "<p class='sub'>Community-chat moderation. Reports land here with the message text our server "
        "fetched from CometChat (never the reporter's copy). Acting here calls CometChat with the "
        "server-only REST key. Satisfies Apple 1.2 (report → act) with the same queue you already run.</p>",
        f"<p class='mode'>{config.mode_banner()}</p>",
        _nav("chat", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not reports:
        body.append("<div class='empty'><div class='big'>✓</div>No open chat reports.</div>")
    for r in reports:
        body.append(
            f"<div class='qcard'>"
            f"<div class='qmain'><div class='qdomain'>{_esc(r['room_guid'])} · {_esc(r['reason'] or '')}</div>"
            f"<div class='qmeta'><span>reported <b>{_esc(real(r['reported_uid']))}</b></span>"
            f"<span class='ev'>by {_esc(real(r['reporter_uid']))}</span>"
            f"<span class='ev'>msg {_esc(r['message_id'] or '')}</span></div>"
            f"<div class='freason' style='margin-top:8px;font-size:12.5px;color:var(--ink)'>"
            f"<span style='font-family:var(--mono);font-size:9px;letter-spacing:.08em;color:var(--down);font-weight:700'>EVIDENCE</span><br>"
            f"{_esc(r['snapshot'] or '')}</div></div>"
            f"<div class='qactions'>"
            + "".join(f"<form method='post' action='/chat-moderate?id={r['id']}'>"
                      f"<input type='hidden' name='action' value='{a}'>"
                      f"<button class='btn {cls}' type='submit'>{label}</button></form>"
                      for a, cls, label in [
                          ("remove_message", "reject", "Remove msg"),
                          ("kick", "reject", "Kick"),
                          ("ban", "reject", "Ban"),
                          ("dismiss", "secondary", "Dismiss")])
            + "</div></div>")
    return _page("".join(body), title="Chat reports")


def _swaps_page(conn, flash: str = "") -> bytes:
    from . import swaps
    rows = swaps.ledger(conn)
    matches = conn.execute(
        "SELECT COUNT(*) n FROM swap_matches WHERE status='ready'").fetchone()["n"]
    done = conn.execute(
        "SELECT COUNT(*) n FROM swap_matches WHERE status='completed'").fetchone()["n"]
    flagged = sum(1 for r in rows if r["flagged"])
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Swaps ledger</h1>",
        "<p class='sub'>Every contact that has changed hands, in both directions. This is the audit "
        "trail for the swap economy: who sent which website + contact to whom. If a manager sends "
        "wrong or dead information, flag the transfer and ban them — the ban also cuts them out of the "
        "community chat and any future swaps.</p>",
        f"<p class='mode'>Open matches (both not yet agreed): <b>{matches}</b> &nbsp;·&nbsp; "
        f"Completed swaps: <b>{done}</b> &nbsp;·&nbsp; Flagged transfers: <b>{flagged}</b></p>",
        _nav("swaps", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not rows:
        body.append("<div class='empty'><div class='big'>🔄</div>No swaps have completed yet.</div>")
        return _page("".join(body), title="Swaps ledger")
    body.append("<table><thead><tr>"
                "<th>When</th><th>From</th><th>To</th><th>Website</th>"
                "<th>Contact sent</th><th>Status</th><th></th></tr></thead><tbody>")
    for r in rows:
        when = _esc((r["at"] or "")[:16].replace("T", " "))
        status = ("<span style='color:var(--down);font-weight:700'>⚑ flagged</span>"
                  + (f"<br><span class='ev'>{_esc(r['flag_reason'])}</span>" if r["flag_reason"] else "")
                  ) if r["flagged"] else "<span style='color:var(--ok,#3aa66f)'>ok</span>"
        if r["flagged"]:
            action = "<span class='ev'>banned/flagged</span>"
        else:
            action = (
                f"<form method='post' action='/swap-flag?id={r['id']}' style='display:flex;gap:6px;flex-wrap:wrap'>"
                f"<input name='reason' placeholder='reason (e.g. wrong contact)' "
                f"style='font-size:11px;padding:4px 6px;min-width:150px'>"
                f"<button class='btn secondary' name='ban' value='0' type='submit'>Flag</button>"
                f"<button class='btn reject' name='ban' value='1' type='submit'>Flag &amp; ban sender</button>"
                f"</form>")
        body.append(
            f"<tr><td class='mono' style='font-size:11px'>{when}</td>"
            f"<td><b>{_esc(r['from'])}</b></td><td><b>{_esc(r['to'])}</b></td>"
            f"<td class='mono'>{_esc(r['domain'])}</td>"
            f"<td style='font-size:12px'>{_esc(r['contact'])}</td>"
            f"<td>{status}</td><td>{action}</td></tr>")
    body.append("</tbody></table>")
    return _page("".join(body), title="Swaps ledger")


def _rewards_page(conn, flash: str = "") -> bytes:
    from . import swaps
    from .locations import flag
    pending = swaps.list_contributions(conn, "pending")
    approved = swaps.list_contributions(conn, "approved")
    rejected = swaps.list_contributions(conn, "rejected")
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Rewards</h1>",
        "<p class='sub'>Managers earn a free swap by adding an affiliate that isn't listed yet. "
        "A submission enters the <a href='/queue-members'>New affiliate · Submitted</a> queue — "
        f"the swap is granted <b>only when you approve the site there</b> (+{swaps.REWARD_SWAPS} swap "
        "to the submitter). Nothing is paid out for a site that's rejected or still pending. "
        "This tab is the read-only reward ledger.</p>",
        f"<p class='mode'>Pending review: <b>{len(pending)}</b> &nbsp;·&nbsp; "
        f"Approved (swap granted): <b>{len(approved)}</b> &nbsp;·&nbsp; Rejected: <b>{len(rejected)}</b></p>",
        _nav("rewards", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not (pending or approved or rejected):
        body.append("<div class='empty'><div class='big'>🎁</div>No submissions yet.</div>")
        return _page("".join(body), title="Rewards")

    STATUS = {"pending": ("PENDING REVIEW", "var(--ink3)"),
              "approved": ("+1 SWAP GRANTED", "var(--up,#3aa66f)"),
              "rejected": ("REJECTED — NO REWARD", "var(--down)")}
    body.append("<table><thead><tr><th>Affiliate</th><th>Submitted by</th>"
                "<th>Market</th><th>Vertical</th><th>Why it matters</th><th>Status</th></tr></thead><tbody>")
    for c in pending + approved + rejected:
        mkt = (flag(c["country"]) + " " + c["country"]) if c["country"] else "—"
        label, colour = STATUS.get(c["status"], (c["status"].upper(), "var(--ink3)"))
        body.append(
            f"<tr><td class='mono'><b>{_esc(c['domain'])}</b></td>"
            f"<td>{_esc(c['by'])}</td><td>{_esc(mkt)}</td><td>{_esc(c['vertical'] or '—')}</td>"
            f"<td style='font-size:12px;max-width:280px'>{_esc(c['comment'] or '—')}</td>"
            f"<td style='color:{colour};font-weight:700;font-size:11px'>{label}</td></tr>")
    body.append("</tbody></table>")
    return _page("".join(body), title="Rewards")


def _reviews_page(conn, flash: str = "") -> bytes:
    rows = conn.execute(
        "SELECT r.id, s.domain, cm.handle, r.rating, r.body "
        "FROM reviews r JOIN sites s ON s.id = r.site_id "
        "JOIN chat_managers cm ON cm.id = r.manager_id "
        "WHERE r.status='pending' ORDER BY r.created_at").fetchall()
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Reviews</h1>",
        "<p class='sub'>Peer reviews awaiting moderation. <b>Approve</b> to publish it to the site's "
        "★ rating and credit the reviewer's loyalty — every 5 approved reviews earns them a free swap "
        "(skipped on the Unlimited plan). <b>Reject</b> low-effort or fake reviews.</p>",
        f"<p class='mode'>Awaiting review: <b>{len(rows)}</b></p>",
        _nav("reviews", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not rows:
        body.append("<div class='empty'><div class='big'>★</div>No reviews awaiting moderation.</div>")
        return _page("".join(body), title="Reviews")
    for r in rows:
        rv = int(r["rating"])
        stars = "★" * rv + "☆" * (5 - rv)
        actions = (
            f"<form method='post' action='/review-moderate?id={r['id']}'>"
            "<input type='hidden' name='action' value='approve'>"
            "<button class='btn approve' type='submit'>Approve</button></form>"
            f"<form method='post' action='/review-moderate?id={r['id']}'>"
            "<input type='hidden' name='action' value='reject'>"
            "<button class='btn reject' type='submit'>Reject</button></form>")
        body.append(
            "<div class='qcard'><div class='qmain'>"
            f"<div class='qdomain'>{_esc(r['domain'])} "
            f"<span class='ev'>· <span style='color:#F5A524'>{stars}</span></span></div>"
            f"<div class='qmeta'><span>by <b>{_esc(r['handle'] or '—')}</b></span></div>"
            f"<div class='qmeta' style='margin-top:6px'>{_esc(r['body'] or '(no notes)')}</div>"
            "</div><div class='qactions'>" + actions + "</div></div>")
    return _page("".join(body), title="Reviews")


def _members_page(conn, flash: str = "") -> bytes:
    from . import members
    pending = members.list_applications(conn, "pending")
    verified = members.list_applications(conn, "verified")
    rejected = members.list_applications(conn, "rejected")
    ck = ("<svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
          "stroke-width='3' stroke-linecap='round' stroke-linejoin='round'><path d='M20 6L9 17l-5-5'/></svg>")
    def sig(ok, label):
        c = "var(--ok,#3aa66f)" if ok else "var(--ink3)"
        mark = ck if ok else "○"
        return f"<span style='color:{c};font-weight:600'>{mark} {label}</span>"
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Member approvals</h1>",
        "<p class='sub'>Sign-ups are applications, not instant access. Every member must clear the "
        "legitimacy signals — <b>LinkedIn</b>, a confirmed <b>work email</b> (no free providers), and the "
        "<b>company</b> they work for. Approving starts their 48-hour free trial and opens "
        "their swap wallet. Reject anyone who doesn't check out.</p>",
        f"<p class='mode'>Awaiting review: <b>{len(pending)}</b> &nbsp;·&nbsp; "
        f"Verified members: <b>{len(verified)}</b> &nbsp;·&nbsp; Rejected: <b>{len(rejected)}</b></p>",
        _nav("members", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not pending:
        body.append("<div class='empty'><div class='big'>🪪</div>No applications awaiting review.</div>")
        return _page("".join(body), title="Member approvals")
    for a in pending:
        yrs = f" · {a['years']}y" if a["years"] else ""
        li = _esc(a["linkedin_url"] or "")
        ready = a["ready_for_review"]
        actions = (
            f"<form method='post' action='/member-approve?id={a['id']}'>"
            f"<button class='btn approve' type='submit'{'' if ready else ' disabled'}>Approve · start trial</button></form>"
            f"<form method='post' action='/member-reject?id={a['id']}'>"
            f"<button class='btn reject' type='submit'>Reject</button></form>")
        body.append(
            f"<div class='qcard'><div class='qmain'>"
            f"<div class='qdomain'>{_esc(a['real_name'])} <span class='ev'>· {_esc(a['handle'])}</span></div>"
            f"<div class='qmeta'><span><b>{_esc(a['company'] or '—')}</b></span>"
            f"<span class='ev'>{_esc(a['sector'] or '—')}{yrs}</span></div>"
            f"<div class='qmeta' style='margin-top:6px'>{sig(a['linkedin_verified'],'LinkedIn')}"
            f"<span class='ev'>{sig(a['work_email_confirmed'], _esc(a['work_email'] or 'work email'))}</span>"
            + ("" if ready else "<span class='ev' style='color:var(--down)'>waiting on verification</span>")
            + "</div>"
            + (f"<div class='qmeta' style='margin-top:4px'><a href='https://{li}' target='_blank' rel='noopener' class='ev'>{li}</a></div>" if li else "")
            + "</div><div class='qactions'>" + actions + "</div></div>")
    return _page("".join(body), title="Member approvals")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, payload: bytes, code: int = 200, headers=None,
              content_type: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj).encode(), code=code,
                   content_type="application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        flash = qs.get("flash", [""])[0]
        conn = connect()
        try:
            if parsed.path == "/":
                self._send(_queue_page(conn, "seo", flash=flash))
            elif parsed.path == "/queue-members":
                self._send(_queue_page(conn, "submitted", flash=flash))
            elif parsed.path == "/sites":
                self._send(_list_page(conn, flash=flash))
            elif parsed.path == "/screenshots":
                self._send(_screenshots_page(conn, flash=flash))
            elif parsed.path == "/site":
                self._send(_edit_page(conn, int(qs["id"][0])))
            elif parsed.path == "/import":
                self._send(_import_page(conn, flash=flash))
            elif parsed.path == "/import/sample":
                from . import importer
                self._send(importer.TEMPLATE.encode(),
                           content_type="text/csv; charset=utf-8",
                           headers={"Content-Disposition":
                                    "attachment; filename=affiliateradar_import_template.csv"})
            elif parsed.path == "/chat":
                self._send(_chat_page(conn, flash=flash))
            elif parsed.path == "/swaps":
                self._send(_swaps_page(conn, flash=flash))
            elif parsed.path == "/rewards":
                self._send(_rewards_page(conn, flash=flash))
            elif parsed.path == "/members":
                self._send(_members_page(conn, flash=flash))
            elif parsed.path == "/reviews":
                self._send(_reviews_page(conn, flash=flash))
            elif parsed.path == "/blacklist":
                self._send(_blacklist_page(conn, flash=flash))
            elif parsed.path == "/blacklist/sample":
                from . import importer
                self._send(importer.BLACKLIST_TEMPLATE.encode(),
                           content_type="text/csv; charset=utf-8",
                           headers={"Content-Disposition":
                                    "attachment; filename=affiliateradar_blacklist_template.csv"})
            else:
                self._send(_page("<h1>404</h1><a href='/'>← back</a>"), code=404)
        finally:
            conn.close()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())

        def g(k):
            v = form.get(k, [""])[0].strip()
            return v or None

        conn = connect()
        try:
            # --- manual homepage upload (from the Homepages tab, via fetch) ---
            if parsed.path == "/screenshot-upload":
                try:
                    sid = int(form.get("id", ["0"])[0])
                except ValueError:
                    sid = 0
                ok = False
                if sid and form.get("screenshot_b64", [""])[0]:
                    ok = _save_manual_screenshot(conn, sid, form["screenshot_b64"][0])
                    conn.commit()
                self._send(b"ok" if ok else b"fail", code=(200 if ok else 400),
                           content_type="text/plain")
                return
            # --- CSV bulk import (no site id) ------------------------------
            if parsed.path == "/import/preview":
                self._send(_preview_page(conn, form.get("csv", [""])[0]))
                return
            if parsed.path == "/import/commit":
                from . import importer
                rows, errs = importer.parse_csv(form.get("csv", [""])[0])
                if errs:
                    self._send(_import_page(conn, flash=errs[0])); return
                summary = importer.apply(conn, rows, uploaded_by="csv_import")
                msg = (f"Imported: {summary['created']} created, {summary['updated']} updated, "
                       f"{summary['skipped']} skipped.")
                self._send(b"", code=303,
                           headers={"Location": "/sites?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/blacklist/preview":
                self._send(_blacklist_preview_page(conn, form.get("csv", [""])[0]))
                return
            if parsed.path == "/blacklist/commit":
                from . import importer
                rows, errs = importer.parse_blacklist_csv(form.get("csv", [""])[0])
                if errs:
                    self._send(_blacklist_page(conn, flash=errs[0])); return
                summary = importer.apply_blacklist(conn, rows, added_by="csv_blacklist")
                msg = (f"Blacklist: {summary['created']} added, {summary['updated']} updated, "
                       f"{summary['skipped']} skipped.")
                self._send(b"", code=303,
                           headers={"Location": "/blacklist?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/blacklist/remove":
                ownership.remove_from_blacklist(conn, int(qs["id"][0]), admin="backoffice")
                conn.commit()
                self._send(b"", code=303,
                           headers={"Location": "/blacklist?flash=" + urllib.parse.quote("Removed from blacklist.")})
                return

            # --- community chat ------------------------------------------------
            if parsed.path == "/chat-moderate":            # operator moderation action
                from .chat import service
                service.moderate(conn, int(qs["id"][0]), form.get("action", ["dismiss"])[0], by="backoffice")
                self._send(b"", code=303,
                           headers={"Location": "/chat?flash=" + urllib.parse.quote("Report actioned via CometChat.")})
                return
            if parsed.path == "/api/chat/session":         # APP-FACING: mint a gated session
                from .chat import service
                from .chat.cometchat import CometChatClient
                who = (form.get("manager", [""])[0]) or (qs.get("manager", [""])[0])
                m = service.manager_by_handle(conn, who)
                if not m:
                    self._json({"error": "unknown_manager"}, 404); return
                try:
                    s = service.issue_session(conn, m["id"], CometChatClient())
                    self._json({k: s[k] for k in ("appId", "region", "uid", "authToken", "allowedGroups")})
                except service.NotVerified as e:
                    self._json({"error": "NOT_VERIFIED", "status": str(e)}, 403)
                return
            if parsed.path == "/api/chat/report":          # APP-FACING: a report lands
                from .chat import service
                g = lambda k: form.get(k, [""])[0]
                rid = service.ingest_report(conn, g("room_guid"), g("message_id"),
                                            g("reported_uid"), g("reporter_uid"), g("reason"))
                self._json({"ok": True, "report_id": rid})
                return

            # --- Affswap ------------------------------------------------------
            if parsed.path == "/swap-flag":                # operator: flag / ban
                from . import swaps
                ban = form.get("ban", ["0"])[0] == "1"
                reason = (form.get("reason", [""])[0].strip() or "flagged by operator")
                res = swaps.flag_transfer(conn, int(qs["id"][0]), reason, ban=ban)
                msg = ("Flagged transfer" + (f" and banned {res['banned']}." if res.get("banned") else "."))
                self._send(b"", code=303,
                           headers={"Location": "/swaps?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/api/review":                # APP-FACING: submit a review
                from . import reviews
                gg = lambda k: form.get(k, [""])[0] or qs.get(k, [""])[0]
                m = _manager_row(conn, gg("manager"))
                if not m:
                    self._json({"error": "unknown_manager"}, 404); return
                site = gg("domain") or gg("site_id")
                if site.isdigit():
                    site = int(site)
                try:
                    self._json({"ok": True, **reviews.submit(conn, m["id"], site, gg("rating"), gg("body"))})
                except reviews.ReviewError as e:
                    self._json({"error": str(e)}, 400)
                return
            if parsed.path == "/api/loyalty":               # APP-FACING: loyalty progress
                from . import reviews
                gg = lambda k: form.get(k, [""])[0] or qs.get(k, [""])[0]
                m = _manager_row(conn, gg("manager"))
                if not m:
                    self._json({"error": "unknown_manager"}, 404); return
                self._json(reviews.loyalty_progress(conn, m["id"]))
                return
            if parsed.path.startswith("/api/swap/"):        # APP-FACING swap endpoints
                from . import swaps
                gg = lambda k: form.get(k, [""])[0] or qs.get(k, [""])[0]
                who = gg("manager")
                m = _manager_row(conn, who)
                if not m and parsed.path != "/api/swap/matches":
                    self._json({"error": "unknown_manager"}, 404); return
                try:
                    if parsed.path == "/api/swap/have":
                        swaps.register_have(conn, m["id"], gg("domain"))
                        self._json({"ok": True})
                    elif parsed.path == "/api/swap/want":
                        swaps.register_want(conn, m["id"], gg("domain"))
                        self._json({"ok": True})
                    elif parsed.path == "/api/swap/contribute":
                        self._json(swaps.submit_contribution(
                            conn, m["id"], gg("url") or gg("domain"),
                            gg("country"), gg("vertical"), gg("comment")))
                    elif parsed.path == "/api/swap/access":
                        self._json(swaps.access(conn, m["id"]))
                    elif parsed.path == "/api/swap/matches":
                        if not m:
                            self._json({"error": "unknown_manager"}, 404); return
                        swaps.find_matches(conn, m["id"])
                        self._json({"access": swaps.access(conn, m["id"]),
                                    "swaps_left": swaps.remaining(conn, m["id"]),
                                    "matches": swaps.list_matches(conn, m["id"])})
                    elif parsed.path == "/api/swap/agree":
                        self._json(swaps.agree(conn, int(gg("match")), m["id"]))
                    elif parsed.path == "/api/swap/buy":
                        self._json({"ok": True, "swaps_left": swaps.buy_extra(conn, m["id"], 1)})
                    else:
                        self._json({"error": "not_found"}, 404)
                except swaps.SwapError as e:
                    self._json({"error": str(e)}, 400)
                return
            # --- member approvals (sign-up review) -----------------------------
            if parsed.path == "/member-approve":
                from . import members
                res = members.approve(conn, int(qs["id"][0]), by="backoffice")
                msg = (f"Approved {res['handle']} — verified, 48-hour trial started."
                       if res.get("ok") else f"Could not approve: {res.get('error')}")
                self._send(b"", code=303,
                           headers={"Location": "/members?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/member-reject":
                from . import members
                members.reject(conn, int(qs["id"][0]), by="backoffice")
                self._send(b"", code=303,
                           headers={"Location": "/members?flash=" + urllib.parse.quote("Application rejected.")})
                return
            # --- review moderation (approve → recompute ★ + loyalty reward) ----
            if parsed.path == "/review-moderate":
                from . import reviews
                action = form.get("action", ["reject"])[0]
                try:
                    res = reviews.moderate(conn, int(qs["id"][0]), action, admin="backoffice")
                    if res.get("status") == "approved":
                        msg = "Review approved."
                        if res.get("granted"):
                            msg += f" +{res['granted']} free swap earned by the reviewer 🎁"
                    else:
                        msg = "Review rejected."
                except reviews.ReviewError as e:
                    msg = f"Could not moderate: {e}"
                self._send(b"", code=303,
                           headers={"Location": "/reviews?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/refresh":                     # manual weekly-refresh trigger
                from .refresh import refresh_all, LATEST_WEEK
                r = refresh_all(conn, week_index=LATEST_WEEK)
                msg = (f"Refreshed {r['updated']} sites from DataForSEO — traffic, trend and market "
                       f"tags updated ({r['iso_week']}).")
                self._send(b"", code=303,
                           headers={"Location": "/sites?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/sheets-sync":                 # mirror catalogue + swaps to Google Sheet
                from . import sheets
                r = sheets.sync_all(conn)
                a, s = r["affiliates"], r["swaps"]
                where = (" → " + a["file"]) if a.get("file") else ""
                msg = (f"Synced to Google Sheet [{a['mode']}]: {a.get('rows','?')} affiliates + "
                       f"{s.get('rows','?')} swap rows{where}.")
                self._send(b"", code=303,
                           headers={"Location": "/sites?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/api/signup":                  # APP-FACING: submit an application
                from . import members
                g2 = lambda k: form.get(k, [""])[0]
                try:
                    a = members.apply(conn, real_name=g2("real_name"), handle=g2("handle"),
                                      work_email=g2("work_email"), linkedin_url=g2("linkedin_url"),
                                      company=g2("company"), site_url=g2("site_url"),
                                      sector=g2("sector") or None,
                                      years=int(g2("years")) if g2("years").isdigit() else None)
                    self._json({"ok": True, "status": "pending", "member_id": a["id"],
                                "next": "confirm your work email, then an admin reviews your application"})
                except members.SignupError as e:
                    self._json({"ok": False, "error": str(e)}, 400)
                return
            if parsed.path == "/api/signup/confirm-email":     # APP-FACING: email link clicked
                from . import members
                members.confirm_email(conn, int(form.get("member_id", ["0"])[0]))
                self._json({"ok": True, "work_email_confirmed": True})
                return
            if parsed.path.startswith("/api/alerts/"):        # APP-FACING alert rules
                from . import alerts
                gg = lambda k: form.get(k, [""])[0] or qs.get(k, [""])[0]
                csvarg = lambda k: [x for x in gg(k).split(",") if x]
                try:
                    if parsed.path == "/api/alerts/save":
                        m = _manager_row(conn, gg("manager"))
                        if not m:
                            self._json({"error": "unknown_manager"}, 404); return
                        self._json(alerts.save_rule(conn, m["id"], gg("country"),
                                   csvarg("verticals"), csvarg("triggers"), csvarg("delivery")))
                    elif parsed.path == "/api/alerts/list":
                        m = _manager_row(conn, gg("manager"))
                        if not m:
                            self._json({"error": "unknown_manager"}, 404); return
                        self._json({"rules": alerts.list_rules(conn, m["id"])})
                    elif parsed.path == "/api/alerts/toggle":
                        alerts.set_active(conn, int(gg("id")), gg("active") not in ("0", "false", ""))
                        self._json({"ok": True})
                    elif parsed.path == "/api/alerts/delete":
                        alerts.delete_rule(conn, int(gg("id")))
                        self._json({"ok": True})
                    elif parsed.path == "/api/alerts/evaluate":
                        m = _manager_row(conn, gg("manager"))
                        self._json({"fired": alerts.evaluate(conn, m["id"] if m else None)})
                    else:
                        self._json({"error": "not_found"}, 404)
                except alerts.AlertError as e:
                    self._json({"error": str(e)}, 400)
                return

            site_id = int(qs["id"][0])
            if parsed.path == "/review":
                # holding-area decision: approve -> publish, reject -> hide
                action = form.get("action", [""])[0]
                domain = conn.execute("SELECT domain FROM sites WHERE id=?",
                                      (site_id,)).fetchone()["domain"]
                from . import swaps
                if action == "approve":
                    ownership.set_classification(conn, site_id, "affiliate", admin="backoffice")
                    shot = _capture_screenshot_if_missing(conn, site_id, domain)
                    conn.commit()
                    # loyalty: this approval is the ONLY moment a reward swap is granted
                    reward = swaps.on_site_published(conn, site_id, by="backoffice")
                    # any swaps that were agreed but waiting on this site now complete
                    settled = swaps.settle_ready_matches(conn, site_id)
                    msg = (f"Approved {domain} — now live in the app"
                           + (" (screenshot captured)" if shot else "")
                           + (f" · +{reward['granted']} swap to {reward['to']}" if reward else "")
                           + (f" · {len(settled)} pending swap(s) completed" if settled else "") + ".")
                else:
                    ownership.set_classification(conn, site_id, "rejected", admin="backoffice")
                    conn.commit()
                    swaps.on_site_rejected(conn, site_id, by="backoffice")  # no reward
                    msg = f"Rejected {domain} — hidden from the app."
                back = form.get("from", ["/"])[0] or "/"
                sep = "&" if "?" in back else "?"
                self._send(b"", code=303,
                           headers={"Location": back + sep + "flash=" + urllib.parse.quote(msg)})
                return

            # /site edit save
            ownership.set_display_name(conn, site_id, g("display_name"), admin="backoffice")
            ownership.set_classification(conn, site_id,
                                         form.get("classification", ["candidate"])[0],
                                         admin="backoffice")
            # verticals: a multi-checkbox group — read the whole list (NOT g(),
            # which returns only the first). Empty selection is ignored so a
            # stray save never wipes a site's verticals.
            ownership.set_verticals(conn, site_id, form.get("vertical", []),
                                    admin="backoffice")
            # manual homepage upload (marked 'manual' → auto-capture never clobbers it)
            if form.get("screenshot_b64", [""])[0]:
                _save_manual_screenshot(conn, site_id, form["screenshot_b64"][0])
            ownership.upsert_contact(
                conn, site_id,
                company_name=g("company_name"), contact_name=g("contact_name"),
                contact_email=g("contact_email"), contact_telegram=g("contact_telegram"),
                contact_teams=g("contact_teams"), uploaded_by="backoffice")
            conn.commit()
        finally:
            conn.close()
        self._send(b"", code=303,
                   headers={"Location": "/sites?flash=" + urllib.parse.quote("Saved. Human-owned — safe from the next refresh.")})


def serve(port: int = 8765) -> None:
    addr = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(addr, _Handler)
    print(f"Back office running at http://127.0.0.1:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    import sys
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)

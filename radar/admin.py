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

import hmac
import html
import json
import os
import re
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
/* ---- sidebar shell (left nav opens into the main page) ---- */
.layout{display:flex;min-height:100vh;align-items:flex-start}
.sidebar{position:sticky;top:0;flex:0 0 250px;width:250px;height:100vh;overflow-y:auto;
 background:rgba(255,255,255,.72);backdrop-filter:blur(8px);border-right:1px solid var(--line);
 padding:16px 14px;display:flex;flex-direction:column}
.side-brand{display:flex;align-items:center;gap:9px;padding:8px 10px 16px;border-bottom:1px solid var(--line);margin-bottom:8px}
.sb-name{font-weight:800;font-size:18px;letter-spacing:-.3px;color:var(--ink)}.sb-name span{color:var(--blue-d)}
.sb-tag{margin-left:auto;font:800 9px var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--blue-dd);background:var(--blue-050);padding:3px 7px;border-radius:6px}
.side-nav{display:flex;flex-direction:column;gap:2px}
.side-group{font:800 10px var(--sans);letter-spacing:.11em;text-transform:uppercase;color:var(--ink3);padding:15px 10px 6px}
.side-item{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:10px;font-size:13.5px;font-weight:700;color:var(--ink2);position:relative;transition:background .12s,color .12s}
.side-item svg{width:17px;height:17px;flex:0 0 auto;opacity:.8}
.side-item .si-label{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.side-item:hover{background:var(--card2);color:var(--ink)}
.side-item.active{background:var(--blue-050);color:var(--blue-dd)}
.side-item.active svg{opacity:1;color:var(--blue-d)}
.side-item.active::before{content:"";position:absolute;left:-14px;top:7px;bottom:7px;width:3px;border-radius:0 3px 3px 0;background:var(--blue-d)}
.side-item .count{font-family:var(--mono);font-size:10.5px;background:var(--blue);color:#fff;border-radius:999px;padding:1px 7px;font-weight:800;margin-left:auto}
.side-item.active .count{background:var(--blue-d)}
.side-foot{margin-top:auto;padding:14px 11px 4px;font:600 11px var(--mono);color:var(--ink3);border-top:1px solid var(--line)}
.main{flex:1;min-width:0}
.main-inner{max-width:1060px;margin:0 auto;padding:26px 32px 90px}
@media (max-width:880px){
 .layout{flex-direction:column}
 .sidebar{position:static;width:100%;height:auto;flex:none;border-right:0;border-bottom:1px solid var(--line)}
 .side-nav{flex-direction:row;flex-wrap:wrap;gap:3px}
 .side-group{width:100%;padding:8px 10px 2px}
 .side-item.active::before{display:none}
 .side-foot{display:none}
 .main-inner{padding:20px 18px 60px}
}
"""


def _nav(active: str, queue_count: int = 0) -> str:
    # The sidebar is now rendered centrally by _page(); each page just declares
    # which item is active via an invisible marker, so no page body changed.
    return f"<!--BO-ACT:{active}-->"


def _ic(p: str) -> str:
    return ("<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.9' "
            f"stroke-linecap='round' stroke-linejoin='round'>{p}</svg>")


ICONS = {
    "queue":     _ic("<circle cx='11' cy='11' r='7'/><path d='M21 21l-4-4'/>"),
    "subs":      _ic("<path d='M4 6h16v12H4z'/><path d='M4 12h5l2 2h2l2-2h5'/>"),
    "members":   _ic("<circle cx='9' cy='8' r='3.2'/><path d='M3.5 20c0-3.2 2.5-5.5 5.5-5.5c1.5 0 2.9.6 3.9 1.5'/><path d='M15.5 15.5l2 2 3.5-3.5'/>"),
    "fullmembers": _ic("<circle cx='8' cy='9' r='3'/><path d='M2.5 19c0-3 2.4-5 5.5-5s5.5 2 5.5 5'/><circle cx='17' cy='8' r='2.3'/><path d='M15.6 13.6c2.1.3 3.9 2 3.9 4.4'/>"),
    "curation":  _ic("<path d='M3 5h18l-7 8v6l-4-2v-4z'/>"),
    "reviews":   _ic("<path d='M12 3.6l2.5 5.1 5.6.8-4 3.9 1 5.6L12 16.4 6.9 19l1-5.6-4-3.9 5.6-.8z'/>"),
    "feedback":  _ic("<path d='M4 5h16v10H9l-4 4v-4H4z'/><path d='M8 9h8M8 12h5'/>"),
    "shots":     _ic("<rect x='3' y='5' width='18' height='14' rx='2'/><circle cx='8.5' cy='10' r='1.6'/><path d='M21 16l-5-4-8 6'/>"),
    "sites":     _ic("<circle cx='12' cy='12' r='9'/><path d='M3 12h18M12 3c2.6 3 2.6 15 0 18M12 3c-2.6 3-2.6 15 0 18'/>"),
    "import":    _ic("<path d='M12 15V4M8 8l4-4 4 4'/><path d='M5 20h14'/>"),
    "blacklist": _ic("<circle cx='12' cy='12' r='9'/><path d='M6 6l12 12'/>"),
    "swaps":     _ic("<path d='M4 8h13l-3-3'/><path d='M20 16H7l3 3'/>"),
    "rewards":   _ic("<rect x='4' y='9' width='16' height='11' rx='1.5'/><path d='M4 13h16M12 9v11'/><path d='M12 9c-2.2 0-4-1-4-2.6C8 5 9.8 5.4 12 9c2.2-3.6 4-4 4-2.6C16 8 14.2 9 12 9z'/>"),
    "chat":      _ic("<path d='M5 5h14v10H9l-4 4z'/>"),
}

# grouped left-nav: (section, [(key, href, label, count_sql_or_None)])
_SUB = ("(SELECT site_id FROM swap_contributions WHERE status='pending' AND site_id IS NOT NULL)")
_SIDEBAR = [
    ("Review", [
        ("queue", "/", "New affiliate · SEO",
         f"SELECT COUNT(*) n FROM sites WHERE classification='candidate' AND id NOT IN {_SUB}"),
        ("subs", "/queue-members", "Submitted",
         f"SELECT COUNT(*) n FROM sites WHERE classification='candidate' AND id IN {_SUB}"),
        ("members", "/members", "Member approvals",
         "SELECT COUNT(*) n FROM chat_managers WHERE status='pending' "
         "AND linkedin_verified=1 AND work_email_confirmed=1"),
        ("curation", "/curation", "Curation",
         "SELECT COUNT(*) n FROM site_signals sg JOIN sites s ON s.id=sg.site_id "
         "WHERE s.classification='affiliate' AND (sg.review IS NULL OR sg.review='') AND sg.flagged=1"),
        ("reviews", "/reviews", "Reviews",
         "SELECT COUNT(*) n FROM reviews WHERE status='pending'"),
        ("feedback", "/feedback", "Feedback",
         "SELECT COUNT(*) n FROM feedback WHERE status='new'"),
        ("shots", "/screenshots", "Homepages",
         "SELECT COUNT(*) n FROM sites s WHERE s.classification='affiliate' "
         "AND s.id NOT IN (SELECT site_id FROM blacklist) "
         "AND s.id NOT IN (SELECT site_id FROM screenshots WHERE provider<>'mock') "
         "AND EXISTS (SELECT 1 FROM site_regions rg WHERE rg.site_id=s.id "
         "AND rg.etv > (CASE WHEN s.etv>=10000 THEN 1500 ELSE 500 END))"),
    ]),
    ("Catalogue", [
        ("sites", "/sites", "All sites", None),
        ("import", "/import", "Bulk import", None),
        ("blacklist", "/blacklist", "Blacklist", None),
    ]),
    ("Network", [
        ("fullmembers", "/full-members", "Members",
         "SELECT COUNT(*) n FROM chat_managers WHERE status='verified'"),
        ("swaps", "/swaps", "Swaps ledger", None),
        ("rewards", "/rewards", "Rewards",
         "SELECT COUNT(*) n FROM swap_contributions WHERE status='pending'"),
        ("chat", "/chat", "Chat reports", None),
    ]),
]


def _sidebar(active: str) -> str:
    out = ["<aside class='sidebar'>",
           f"<a class='side-brand' href='/'>{MARK_SVG}"
           "<span class='sb-name'>Aff<span>swap</span></span>"
           "<span class='sb-tag'>Back office</span></a>",
           "<nav class='side-nav'>"]
    for group, rows in _SIDEBAR:
        out.append(f"<div class='side-group'>{group}</div>")
        for key, href, label, sql in rows:
            cls = " active" if key == active else ""
            badge = _count_badge(sql) if sql else ""
            out.append(f"<a class='side-item{cls}' href='{href}'>{ICONS.get(key,'')}"
                       f"<span class='si-label'>{label}</span>{badge}</a>")
    out.append("</nav><div class='side-foot'><a href='/logout'>Sign out</a> · Affswap admin</div></aside>")
    return "".join(out)


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
    # Each page declares its active nav item via an invisible <!--BO-ACT:key-->
    # marker (emitted by _nav). Pull it out, then render the shared left sidebar.
    m = re.search(r"<!--BO-ACT:([a-z]+)-->", body)
    active = m.group(1) if m else ""
    body = re.sub(r"<!--BO-ACT:[a-z]+-->", "", body)
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<link rel='icon' type='image/svg+xml' href='{FAVICON}'>"
            f"<link rel='preconnect' href='https://fonts.googleapis.com'>"
            f"<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>"
            f"<title>{title}</title><style>{CSS}</style></head><body>"
            f"<div class='layout'>{_sidebar(active)}"
            f"<main class='main'><div class='main-inner'>{body}</div></main>"
            f"</div></body></html>").encode()


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _login_page(error: str = "") -> bytes:
    """Back-office sign-in — no sidebar (the visitor isn't authed yet)."""
    err = (f"<div class='note' style='background:#FCEBEC;border-color:#F3B4B2;color:#B4232A'>{_esc(error)}</div>"
           if error else "")
    inner = (
        "<div style='max-width:400px;margin:9vh auto 0;padding:0 20px'>"
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:24px'>{MARK_SVG}"
        "<span class='blogo'>Aff<span>swap</span></span>"
        "<span class='bo'>Back office</span></div>"
        "<h1 style='font-size:24px'>Sign in</h1>"
        "<p class='sub'>Admins sign in with their Affswap email and password.</p>"
        + err +
        "<form class='card' method='post' action='/login' style='max-width:none;margin-top:14px'>"
        "<label style='margin-top:0'>Email or username</label>"
        "<input name='email' type='text' autofocus autocomplete='username'>"
        "<label>Password</label>"
        "<input name='password' type='password' autocomplete='current-password'>"
        "<div class='actions'><button type='submit' style='width:100%'>Sign in</button></div>"
        "</form></div>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<link rel='icon' type='image/svg+xml' href='{FAVICON}'>"
            f"<title>Sign in · Affswap back office</title><style>{CSS}</style></head>"
            f"<body>{inner}</body></html>").encode()


def _login_subject(conn, email: str, pw: str):
    """Resolve a back-office login to 'owner', 'm:<id>', or None."""
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_pass = os.environ.get("ADMIN_PASS", "")
    if admin_pass and email == admin_user and hmac.compare_digest(pw, admin_pass):
        return "owner"
    _ensure_member_cols(conn)
    from . import auth
    r = conn.execute(
        "SELECT id, password_hash, COALESCE(is_admin,0) AS is_admin, status "
        "FROM chat_managers WHERE lower(work_email)=lower(?)", (email,)).fetchone()
    if r and r["is_admin"] and r["status"] == "verified" and auth.verify_password(pw, r["password_hash"]):
        return f"m:{r['id']}"
    return None


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
            f"<div class='qmain'><div class='qdomain'>{_esc(r['domain'])}"
            f"<a href='https://{_esc(r['domain'])}/' target='_blank' rel='noopener noreferrer' "
            f"style='font-size:12px;font-weight:700;color:var(--blue-d);margin-left:10px'>Visit&nbsp;↗</a></div>"
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

    # deep-link (page that ranks for the top gambling keyword) if we have one,
    # else the plain homepage — so the back office can jump straight to the site.
    _sig = conn.execute("SELECT landing_url FROM site_signals WHERE site_id=?",
                        (site_id,)).fetchone()
    _visit = (_sig["landing_url"] if _sig and _sig["landing_url"]
              else f"https://{s['domain']}/")
    _is_deep = bool(_sig and _sig["landing_url"]
                    and _sig["landing_url"].rstrip("/") != f"https://{s['domain']}")
    visit_html = (
        f"<div style='margin:2px 0 16px;display:flex;gap:10px;align-items:center'>"
        f"<a class='btn secondary' href='{_esc(_visit)}' target='_blank' "
        f"rel='noopener noreferrer'>Visit website ↗</a>"
        f"<span class='hint' style='font-weight:400'>"
        f"{'deep-linked to the gambling section' if _is_deep else 'homepage'}</span></div>")

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
      {visit_html}
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

    # --- live & test matches (with a reset control for testing) ---------------
    allm = swaps.all_matches(conn)
    if allm:
        body.append("<h2 style='margin:22px 0 6px;font-size:16px'>Matches</h2>")
        body.append("<p class='sub' style='margin-top:0'>Every mutual match, live and completed. "
                    "<b>Reset</b> returns a match to the un-confirmed state so both members can walk "
                    "through the swap again — it refunds the swap each side spent, clears the contacts "
                    "they entered, and keeps the pairing so it reappears as &lsquo;action needed&rsquo;. "
                    "Handy for testing.</p>")
        body.append("<table><thead><tr><th>Match</th><th>Between</th><th>Exchange</th>"
                    "<th>Agreed</th><th>Contacts entered</th><th>Status</th><th></th></tr></thead><tbody>")
        for mm in allm:
            st = {"ready": "<span style='color:var(--ink3)'>ready</span>",
                  "completed": "<span style='color:var(--up,#3aa66f);font-weight:700'>✓ completed</span>",
                  "void": "<span style='color:var(--ink3)'>void</span>"}.get(mm["status"], mm["status"])
            agreed = ("✓ " + _esc(mm["a"]) if mm["a_agreed"] else "· " + _esc(mm["a"])) + "<br>" + \
                     ("✓ " + _esc(mm["b"]) if mm["b_agreed"] else "· " + _esc(mm["b"]))
            contacts = "<br>".join(filter(None, [
                (_esc(mm["a"]) + ": " + _esc(mm["a_contact"])) if mm["a_contact"] else "",
                (_esc(mm["b"]) + ": " + _esc(mm["b_contact"])) if mm["b_contact"] else ""])) or "<span class='ev'>none yet</span>"
            reset = (f"<form method='post' action='/swap-reset?id={mm['id']}' "
                     f"onsubmit=\"return confirm('Reset match #{mm['id']} so both sides confirm again?')\">"
                     f"<button class='btn secondary' type='submit'>↺ Reset</button></form>")
            body.append(
                f"<tr><td class='mono'>#{mm['id']}</td>"
                f"<td><b>{_esc(mm['a'])}</b> ↔ <b>{_esc(mm['b'])}</b></td>"
                f"<td class='mono' style='font-size:11px'>{_esc(mm['a'])} gets {_esc(mm['a_gets'])}<br>"
                f"{_esc(mm['b'])} gets {_esc(mm['b_gets'])}</td>"
                f"<td style='font-size:11px'>{agreed}</td>"
                f"<td style='font-size:11px'>{contacts}</td>"
                f"<td>{st}</td><td>{reset}</td></tr>")
        body.append("</tbody></table>")

    if not rows:
        body.append("<h2 style='margin:22px 0 6px;font-size:16px'>Transfer ledger</h2>")
        body.append("<div class='empty'><div class='big'>🔄</div>No swaps have completed yet.</div>")
        return _page("".join(body), title="Swaps ledger")
    body.append("<h2 style='margin:22px 0 6px;font-size:16px'>Transfer ledger</h2>")
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


def _member_sig(ok, label):
    ck = ("<svg width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
          "stroke-width='3' stroke-linecap='round' stroke-linejoin='round'><path d='M20 6L9 17l-5-5'/></svg>")
    c = "#3aa66f" if ok else "var(--ink3)"
    return f"<span style='color:{c};font-weight:600'>{ck if ok else '○'} {label}</span>"


def _members_page(conn, flash: str = "") -> bytes:
    """APPROVALS ONLY — people trying to sign up who still need a human yes/no.
    Approved/verified members live on their own page (/full-members)."""
    from . import members
    pending = members.list_applications(conn, "pending")
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Member approvals</h1>",
        "<p class='sub'>Sign-ups awaiting your yes/no. Each must clear the legitimacy signals — "
        "<b>LinkedIn</b>, a confirmed <b>work email</b> (no free providers), and their <b>company</b>. "
        "Approving starts their 48-hour trial and opens their swap wallet. Approved members move to "
        "<a href='/full-members'>Members</a>.</p>",
        f"<p class='mode'>Awaiting review: <b>{len(pending)}</b></p>",
        _nav("members", _queue_count(conn)),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not pending:
        body.append("<div class='empty'><div class='big'>🪪</div>No sign-ups awaiting approval."
                    "<div class='hint' style='margin-top:8px'>Approved members are under "
                    "<a href='/full-members'>Members</a>.</div></div>")
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
            f"<div class='qmeta' style='margin-top:6px'>{_member_sig(a['linkedin_verified'],'LinkedIn')}"
            f"<span class='ev'>{_member_sig(a['work_email_confirmed'], _esc(a['work_email'] or 'work email'))}</span>"
            + ("" if ready else "<span class='ev' style='color:var(--down)'>waiting on verification</span>")
            + "</div>"
            + (f"<div class='qmeta' style='margin-top:4px'><a href='https://{li}' target='_blank' rel='noopener' class='ev'>{li}</a></div>" if li else "")
            + "</div><div class='qactions'>" + actions + "</div></div>")
    return _page("".join(body), title="Member approvals")


def _full_members_page(conn, flash: str = "") -> bytes:
    """The member DIRECTORY — everyone approved/verified. Click through to manage."""
    from . import members
    verified = members.list_applications(conn, "verified")
    rejected = members.list_applications(conn, "rejected")
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Members</h1>",
        "<p class='sub'>Approved, verified members of the network. Click any member to manage their "
        "account — gift swaps, reset password, block chat or email, suspend. New sign-ups awaiting "
        "approval are under <a href='/members'>Member approvals</a>.</p>",
        f"<p class='mode'>Verified: <b>{len(verified)}</b> &nbsp;·&nbsp; Rejected: <b>{len(rejected)}</b></p>",
        _nav("fullmembers", 0),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not verified:
        body.append("<div class='empty'><div class='big'>👥</div>No verified members yet.</div>")
        return _page("".join(body), title="Members")
    rws = []
    for a in verified:
        rws.append(
            "<tr>"
            f"<td><a href='/member?id={a['id']}'><b>{_esc(a['real_name'] or a['handle'] or '—')}</b></a>"
            f"<div class='ev' style='font-size:11.5px'>@{_esc(a['handle'] or '')}</div></td>"
            f"<td>{_esc(a['company'] or '—')}</td>"
            f"<td class='src'>{_esc(a['work_email'] or '—')}</td>"
            f"<td>{_member_sig(a['linkedin_verified'],'LinkedIn')} &nbsp; {_member_sig(a['work_email_confirmed'],'email')}</td>"
            f"<td class='src'>{_esc((a['applied_at'] or '')[:10])}</td>"
            f"<td><a class='editlink' href='/member?id={a['id']}'>Manage ›</a></td></tr>")
    body.append(
        "<table><thead><tr><th>Member</th><th>Company</th><th>Work email</th>"
        "<th>Signals</th><th>Joined</th><th></th></tr></thead><tbody>"
        + "".join(rws) + "</tbody></table>")
    return _page("".join(body), title="Members")


def _ensure_member_cols(conn) -> None:
    """Additive, idempotent — the admin columns for member management. Existing
    prod DBs get them on the first back-office member view (no formal migration)."""
    for col in ("is_admin INTEGER DEFAULT 0", "chat_blocked INTEGER DEFAULT 0",
                "email_blocked INTEGER DEFAULT 0"):
        try:
            conn.execute("ALTER TABLE chat_managers ADD COLUMN " + col)
        except Exception:
            pass


def _member_detail_page(conn, mid: int, flash: str = "", temp_pw: str = "") -> bytes:
    """A member's profile + operator controls: gift swaps, reset password, make
    admin, block chat / email, suspend."""
    _ensure_member_cols(conn)
    from . import swaps
    m = conn.execute("SELECT * FROM chat_managers WHERE id=?", (mid,)).fetchone()
    if not m:
        return _page(_nav("members", 0) + "<div class='eyebrow'><a href='/members'>← Members</a></div>"
                     "<h1>Member not found</h1>", title="Member")

    def g(k):
        try:
            return m[k]
        except Exception:
            return None

    try:
        acc = swaps.access(conn, mid) or {}
    except Exception:
        acc = {}
    name = _esc(g("real_name") or g("handle") or "—")
    handle = _esc(g("handle") or "")
    status = g("status") or "—"
    is_admin, chat_blk, email_blk = bool(g("is_admin")), bool(g("chat_blocked")), bool(g("email_blocked"))
    li = _esc(g("linkedin_url") or "")
    swaps_lbl = ("∞ unlimited" if acc.get("unlimited")
                 else (str(acc.get("swaps")) if acc.get("swaps") is not None else "—"))
    plan = _esc(acc.get("plan") or "standard")
    av_url = g("avatar_url")
    av = (f"<img src='{_esc(av_url)}' alt='' style='width:100%;height:100%;object-fit:cover;border-radius:16px'>"
          if av_url else _esc((g("real_name") or g("handle") or "?")[0].upper()))
    badge_cls = "affiliate" if status == "verified" else ("rejected" if status in ("rejected", "suspended") else "candidate")

    def info(label, val):
        return (f"<div><div style='font-size:11px;color:var(--ink3);text-transform:uppercase;"
                f"letter-spacing:.04em'>{label}</div><div style='font-weight:700;margin-top:2px'>{val}</div></div>")

    def toggle(field, on, act_on, act_off, desc):
        return (f"<form method='post' action='/member-flag?id={mid}&field={field}' "
                "style='display:flex;justify-content:space-between;align-items:center;gap:14px;"
                "padding:13px 0;border-bottom:1px solid var(--line)'>"
                f"<div><div style='font-weight:700'>{desc}</div>"
                f"<div class='hint' style='margin-top:2px'>Currently: <b>{'ON' if on else 'off'}</b></div></div>"
                f"<button class='btn secondary' type='submit'>{act_off if on else act_on}</button></form>")

    body = [
        _nav("members", 0),
        "<div class='eyebrow'><a href='/members'>← Members</a></div>",
        "<div style='display:flex;gap:16px;align-items:center;margin:6px 0 18px'>"
        f"<div class='qthumb' style='width:64px;height:64px;font-size:26px;border-radius:16px'>{av}</div>"
        f"<div><h1 style='margin:0'>{name}</h1>"
        f"<div class='sub' style='margin:3px 0 0'>@{handle} &nbsp;<span class='badge {badge_cls}'>{_esc(status)}</span>"
        + ("&nbsp;<span class='badge' style='color:var(--blue-dd);background:#EAF1FF'>ADMIN</span>" if is_admin else "")
        + ("&nbsp;<span class='badge rejected'>CHAT BLOCKED</span>" if chat_blk else "")
        + ("&nbsp;<span class='badge rejected'>EMAIL BLOCKED</span>" if email_blk else "")
        + "</div></div></div>",
    ]
    if temp_pw:
        body.append("<div class='note' style='background:#FFF7E6;border-color:#F5D998;color:#8A5A00'>"
                    f"Temporary password for {name}: <b style='font-family:var(--mono);font-size:15px'>{_esc(temp_pw)}</b>"
                    " — share it securely. They can change it after signing in.</div>")
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")

    joined = _esc((g("applied_at") or g("created_at") or "")[:10])
    last = _esc((g("last_login_at") or "")[:16].replace("T", " "))
    lilink = f"<a href='https://{li}' target='_blank' rel='noopener'>{li}</a>" if li else "—"
    body.append(
        "<div class='owner-note' style='border:1px solid var(--line);border-left:3px solid var(--blue);"
        "display:grid;grid-template-columns:repeat(3,1fr);gap:18px 16px;padding:18px'>"
        + info("Work email", _esc(g("work_email") or "—"))
        + info("Company", _esc(g("company") or "—"))
        + info("Sector", _esc(g("sector") or "—"))
        + info("Swap balance", f"{swaps_lbl} <span class='ev'>· {plan}</span>")
        + info("Joined", joined or "—")
        + info("Last login", last or "—")
        + info("LinkedIn", lilink)
        + info("Signals", ("LinkedIn ✓ " if g("linkedin_verified") else "LinkedIn ✗ ")
               + ("email ✓" if g("work_email_confirmed") else "email ✗"))
        + info("Member ID", str(mid))
        + "</div>")

    # Swap lists — what they WANT vs what they HAVE, so you can test matches.
    wants = conn.execute(
        "SELECT s.id, s.domain, s.top_country, s.etv FROM swap_wants w "
        "JOIN sites s ON s.id=w.site_id WHERE w.manager_id=? ORDER BY (s.etv IS NULL), s.etv DESC",
        (mid,)).fetchall()
    haves = conn.execute(
        "SELECT s.id, s.domain, s.top_country, s.etv FROM swap_haves h "
        "JOIN sites s ON s.id=h.site_id WHERE h.manager_id=? ORDER BY (s.etv IS NULL), s.etv DESC",
        (mid,)).fetchall()

    def _swaplist(rows, empty):
        if not rows:
            return f"<div class='hint' style='padding:8px 0'>{empty}</div>"
        return "".join(
            f"<a href='/site?id={r['id']}' style='display:flex;justify-content:space-between;gap:10px;"
            "align-items:center;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px'>"
            f"<span>{(flag(r['top_country']) + ' ') if r['top_country'] else ''}{_esc(r['domain'])}</span>"
            f"<span class='ev' style='font-family:var(--mono)'>{int(r['etv'] or 0):,}/mo</span></a>"
            for r in rows)

    body.append(
        "<div class='card' style='max-width:none;margin-top:16px'>"
        "<label style='margin-top:0'>Swap lists <span class='hint' style='font-weight:400'>"
        "— what this member wants vs. has, for testing matches</span></label>"
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:26px;margin-top:4px'>"
        f"<div><div style='font-weight:800;color:var(--blue-d);margin-bottom:2px'>Wants · {len(wants)}</div>"
        + _swaplist(wants, "Nothing on their Want list yet.") + "</div>"
        f"<div><div style='font-weight:800;color:var(--up);margin-bottom:2px'>Has · {len(haves)}</div>"
        + _swaplist(haves, "Nothing on their Have list yet.") + "</div>"
        "</div></div>")

    # company — editable here so an operator can fill it in (it shows in chat)
    body.append(
        f"<form class='card' method='post' action='/member-company?id={mid}' style='max-width:none;margin-top:16px'>"
        "<label style='margin-top:0'>Company <span class='hint' style='font-weight:400'>"
        "— shown next to this member in chat &amp; on their profile</span></label>"
        "<div style='display:flex;gap:10px'>"
        f"<input name='company' value='{_esc(g('company') or '')}' placeholder='Company name' style='flex:1'>"
        "<button type='submit'>Save</button></div></form>")

    body.append(
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px'>"
        f"<form class='card' method='post' action='/member-gift?id={mid}' style='max-width:none;margin:0'>"
        "<label style='margin-top:0'>Gift swaps</label>"
        "<div style='display:flex;gap:10px;align-items:center'>"
        "<input type='number' name='count' value='1' min='1' max='999' style='max-width:110px'>"
        "<button type='submit'>Gift swaps</button></div>"
        "<div class='hint'>Adds swap credits to this member's wallet immediately.</div></form>"
        f"<form class='card' method='post' action='/member-reset-pw?id={mid}' style='max-width:none;margin:0'>"
        "<label style='margin-top:0'>Password</label>"
        "<button type='submit' class='btn secondary'>Reset password</button>"
        "<div class='hint'>Sets a new temporary password and shows it once so you can share it. "
        "LinkedIn sign-ins are unaffected.</div></form>"
        "</div>")

    body.append(
        "<div class='card' style='max-width:none;margin-top:16px'>"
        "<label style='margin-top:0'>Role &amp; access</label>"
        + toggle("is_admin", is_admin, "Make admin", "Remove admin",
                 "Admin — marks this member as an operator")
        + toggle("chat_blocked", chat_blk, "Block chat", "Unblock chat",
                 "Chat — a blocked member can't open a chat session")
        + toggle("email_blocked", email_blk, "Block email", "Unblock email",
                 "Email — a blocked member receives no alert emails")
        + toggle("status", status == "suspended", "Suspend member", "Reactivate member",
                 "Account status — suspending revokes access")
        + "</div>")

    body.append(
        "<div class='card' style='max-width:none;margin-top:16px;border-color:#F3B4B2'>"
        "<label style='margin-top:0;color:var(--down)'>Danger zone</label>"
        "<div class='hint' style='margin:2px 0 12px'>Permanently delete this member and all their "
        "data — swaps, matches, reviews, chat messages, feedback. This cannot be undone.</div>"
        f"<form method='post' action='/member-delete?id={mid}' onsubmit=\"return confirm('Permanently "
        "delete this member and ALL their data? This cannot be undone.');\">"
        "<button class='btn reject' type='submit'>Delete member permanently</button></form></div>")

    return _page("".join(body), title=f"Member · {name}")


def _feedback_page(conn, flash: str = "") -> bytes:
    """Member feedback + feature requests, newest/unreviewed first."""
    conn.execute("CREATE TABLE IF NOT EXISTS feedback ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, manager_id INTEGER, "
                 "kind TEXT DEFAULT 'feedback', body TEXT NOT NULL, "
                 "status TEXT NOT NULL DEFAULT 'new', created_at TEXT, "
                 "resolved_at TEXT, resolved_by TEXT)")
    rows = conn.execute(
        "SELECT f.id, f.body, f.status, f.created_at, f.manager_id, "
        "       c.real_name, c.handle, c.company "
        "FROM feedback f LEFT JOIN chat_managers c ON c.id=f.manager_id "
        "ORDER BY (f.status='new') DESC, f.created_at DESC").fetchall()
    new_n = sum(1 for r in rows if r["status"] == "new")
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Feedback &amp; feature requests</h1>",
        "<p class='sub'>What members are telling us and asking for. New items first — "
        "mark them reviewed as you work through them.</p>",
        f"<p class='mode'>New: <b>{new_n}</b> &nbsp;·&nbsp; Total: <b>{len(rows)}</b></p>",
        _nav("feedback", 0),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not rows:
        body.append("<div class='empty'><div class='big'>💬</div>No feedback yet.</div>")
    for r in rows:
        who = _esc(r["real_name"] or r["handle"] or "—")
        comp = _esc(r["company"] or "")
        when = _esc((r["created_at"] or "")[:16].replace("T", " "))
        isnew = r["status"] == "new"
        mid = r["manager_id"]
        who_html = (f"<a href='/member?id={mid}'>{who}</a>" if mid else who)
        act = (f"<form method='post' action='/feedback-act?id={r['id']}' style='display:inline'>"
               f"<input type='hidden' name='action' value='{'reviewed' if isnew else 'reopen'}'>"
               f"<button class='btn secondary' type='submit'>{'Mark reviewed' if isnew else 'Reopen'}</button></form>"
               f"<form method='post' action='/feedback-act?id={r['id']}' style='display:inline'>"
               f"<input type='hidden' name='action' value='delete'>"
               f"<button class='btn reject' type='submit'>Delete</button></form>")
        body.append(
            "<div class='qcard'><div class='qmain'>"
            "<div class='qmeta' style='margin-bottom:7px'><b>" + who_html + "</b>"
            + (f"<span class='ev'>{comp}</span>" if comp else "")
            + f"<span class='ev'>{when}</span>"
            + ("" if isnew else "<span class='badge affiliate'>reviewed</span>")
            + "</div>"
            + f"<div style='font-size:14px;color:var(--ink);white-space:pre-wrap;line-height:1.5'>{_esc(r['body'])}</div>"
            + "</div><div class='qactions'>" + act + "</div></div>")
    return _page("".join(body), title="Feedback")


def _curation_page(conn, flash: str = "") -> bytes:
    """Quality-review queue: sites the keyword-profile signals flagged as likely
    OPERATORS (rank mostly for their own brand) or LOW gambling relevance. Flags,
    not verdicts — a human confirms Keep or Remove. Nothing is auto-deleted."""
    import json as _json
    import re as _re
    from . import config
    for col in ("review TEXT", "flagged INTEGER"):
        try:
            conn.execute(f"ALTER TABLE site_signals ADD COLUMN {col}")
        except Exception:
            pass
    try:
        rows = conn.execute("""
            SELECT s.id, s.domain, s.etv, s.top_country,
                   sg.gambling_pct, sg.self_brand_pct, sg.is_operator, sg.landing_url
            FROM site_signals sg JOIN sites s ON s.id = sg.site_id
            WHERE s.classification='affiliate'
              AND (sg.review IS NULL OR sg.review='')
              AND sg.flagged=1
            ORDER BY sg.gambling_pct ASC, sg.self_brand_pct DESC
        """).fetchall()
    except Exception:
        rows = []
    body = [
        "<div class='eyebrow'>Affswap · Back office</div>",
        "<h1>Curation · quality review</h1>",
        "<p class='sub'>Sites the keyword-profile signals flagged — likely <b>operators</b> "
        "(rank mostly for their own brand) or <b>low gambling relevance</b>. These are flags, "
        "not verdicts: a sports-betting site can read low because it ranks for team names. "
        "Open the gambling page to check, then Keep or Remove. Nothing is deleted automatically.</p>",
        _nav("curation", 0),
    ]
    if flash:
        body.append(f"<div class='note'>{_esc(flash)}</div>")
    if not rows:
        body.append("<div class='empty'><div class='big'>✓</div>No flagged sites to review "
                    "(or the analysis hasn't run yet).</div>")
    for r in rows:
        etv = f"{int(r['etv']):,}/mo" if r["etv"] else "no traffic"
        fl = flag(r["top_country"]) if r["top_country"] else ""
        reasons = []
        if r["is_operator"]:
            reasons.append(f"<span class='ev' style='color:#C53034'>likely operator · "
                           f"{r['self_brand_pct']}% own-brand searches</span>")
        if (r["gambling_pct"] or 0) < 40:
            reasons.append(f"<span class='ev' style='color:#B45309'>gambling relevance "
                           f"{r['gambling_pct']}%</span>")
        kwline = ""
        try:
            safe = _re.sub(r"[^A-Za-z0-9._-]", "_", r["domain"]) + ".json"
            kws = _json.loads((config.DATA_DIR / "kw_cache" / safe).read_text())
            kws.sort(key=lambda k: k.get("etv", 0), reverse=True)
            top = ", ".join(_esc(k["keyword"]) for k in kws[:4])
            kwline = (f"<div class='freason' style='margin-top:8px;font-size:12px;color:var(--ink3)'>"
                      f"ranks for: {top}</div>")
        except Exception:
            pass
        land = r["landing_url"] or ("https://" + r["domain"] + "/")
        body.append(
            f"<div class='qcard'>"
            f"<div class='qthumb'>{_esc(r['domain'][0].upper())}</div>"
            f"<div class='qmain'><div class='qdomain'>{_esc(r['domain'])} "
            f"<a href='{_esc(land)}' target='_blank' rel='noopener' "
            f"style='font-size:12px;font-weight:600'>↗ gambling page</a></div>"
            f"<div class='qmeta'><span>{fl} <b>{etv}</b></span>{''.join(reasons)}</div>{kwline}</div>"
            f"<div class='qactions'>"
            f"<form method='post' action='/curation-act?id={r['id']}'>"
            f"<input type='hidden' name='action' value='keep'>"
            f"<button class='btn approve' type='submit'>✓ Keep</button></form>"
            f"<form method='post' action='/curation-act?id={r['id']}'>"
            f"<input type='hidden' name='action' value='remove'>"
            f"<button class='btn reject' type='submit'>Remove</button></form>"
            f"<a class='btn secondary' href='/site?id={r['id']}'>Details</a>"
            f"</div></div>")
    return _page("".join(body), title="Curation")


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
            if parsed.path == "/login":
                self._send(_login_page())
            elif parsed.path == "/logout":
                from . import auth
                self._send(b"", code=303, headers={"Location": "/login",
                           "Set-Cookie": auth.clear_admin_cookie_header()})
            elif parsed.path == "/":
                self._send(_queue_page(conn, "seo", flash=flash))
            elif parsed.path == "/queue-members":
                self._send(_queue_page(conn, "submitted", flash=flash))
            elif parsed.path == "/sites":
                self._send(_list_page(conn, flash=flash))
            elif parsed.path == "/curation":
                self._send(_curation_page(conn, flash=flash))
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
            elif parsed.path == "/full-members":
                self._send(_full_members_page(conn, flash=flash))
            elif parsed.path == "/member":
                self._send(_member_detail_page(conn, int(qs["id"][0]), flash=flash))
            elif parsed.path == "/reviews":
                self._send(_reviews_page(conn, flash=flash))
            elif parsed.path == "/feedback":
                self._send(_feedback_page(conn, flash=flash))
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
            if parsed.path == "/login":                       # back-office sign-in
                from . import auth
                email = (form.get("email", [""])[0] or "").strip()
                pw = form.get("password", [""])[0] or ""
                subject = _login_subject(conn, email, pw)
                if subject:
                    self._send(b"", code=303, headers={
                        "Location": "/",
                        "Set-Cookie": auth.set_admin_cookie_header(auth.make_admin_cookie(subject))})
                else:
                    self._send(_login_page("Wrong email/password, or that account isn't an admin."),
                               code=401)
                return
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
            # --- curation: keep / remove a flagged site -------------------
            if parsed.path == "/curation-act":
                try:
                    sid = int(qs.get("id", ["0"])[0])
                except ValueError:
                    sid = 0
                action = form.get("action", [""])[0]
                if sid:
                    try:
                        conn.execute("ALTER TABLE site_signals ADD COLUMN review TEXT")
                    except Exception:
                        pass
                    if action == "remove":
                        ownership.set_classification(conn, sid, "rejected", admin="curation")
                        conn.execute("UPDATE site_signals SET review='rejected' WHERE site_id=?", (sid,))
                        msg = "Removed from the network."
                    else:
                        conn.execute("UPDATE site_signals SET review='kept' WHERE site_id=?", (sid,))
                        msg = "Kept in the network."
                    conn.commit()
                else:
                    msg = "No site selected."
                self._send(b"", code=303,
                           headers={"Location": "/curation?flash=" + urllib.parse.quote(msg)})
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
                try:                                            # operator chat block
                    blk = conn.execute("SELECT chat_blocked FROM chat_managers WHERE id=?",
                                       (m["id"],)).fetchone()
                    if blk and blk["chat_blocked"]:
                        self._json({"error": "CHAT_BLOCKED"}, 403); return
                except Exception:
                    pass
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
            if parsed.path == "/swap-reset":               # operator: reset a match for testing
                from . import swaps
                try:
                    r = swaps.reset_match(conn, int(qs["id"][0]))
                    pair = " ↔ ".join(r["pair"])
                    msg = (f"Reset match #{r['match_id']} ({pair}) — both must confirm again"
                           + (f"; refunded 1 swap to {', '.join(r['refunded'])}" if r["refunded"] else "") + ".")
                except swaps.SwapError as e:
                    msg = f"Could not reset match: {e}"
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
            if parsed.path == "/member-gift":                 # operator: gift swap credits
                from . import swaps
                mid = int(qs["id"][0])
                try:
                    n = int((form.get("count", ["1"])[0]) or 1)
                except ValueError:
                    n = 1
                n = max(1, min(n, 999))
                left = swaps.grant_swaps(conn, mid, n)
                conn.commit()
                self._send(b"", code=303, headers={"Location": f"/member?id={mid}&flash=" +
                           urllib.parse.quote(f"Gifted {n} swap(s). Balance: {left}.")})
                return
            if parsed.path == "/member-reset-pw":             # operator: set a temp password
                from . import auth
                import secrets
                mid = int(qs["id"][0])
                temp = secrets.token_urlsafe(9)
                conn.execute("UPDATE chat_managers SET password_hash=? WHERE id=?",
                             (auth.hash_password(temp), mid))
                conn.commit()
                # render the page directly (200) so the temp password never rides in a URL
                self._send(_member_detail_page(conn, mid, temp_pw=temp))
                return
            if parsed.path == "/member-flag":                 # operator: toggle admin/blocks/status
                _ensure_member_cols(conn)
                mid = int(qs["id"][0])
                field = qs.get("field", [""])[0]
                if field == "status":
                    cur = conn.execute("SELECT status FROM chat_managers WHERE id=?", (mid,)).fetchone()["status"]
                    new = "verified" if cur == "suspended" else "suspended"
                    conn.execute("UPDATE chat_managers SET status=? WHERE id=?", (new, mid))
                    msg = "Member reactivated." if new == "verified" else "Member suspended."
                elif field in ("is_admin", "chat_blocked", "email_blocked"):
                    cur = conn.execute(f"SELECT {field} AS v FROM chat_managers WHERE id=?", (mid,)).fetchone()["v"] or 0
                    conn.execute(f"UPDATE chat_managers SET {field}=? WHERE id=?", (0 if cur else 1, mid))
                    nm = {"is_admin": "Admin rights", "chat_blocked": "Chat block", "email_blocked": "Email block"}[field]
                    msg = f"{nm} {'removed' if cur else 'applied'}."
                else:
                    msg = "Unknown action."
                conn.commit()
                self._send(b"", code=303, headers={"Location": f"/member?id={mid}&flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/feedback-act":                # feedback: reviewed / reopen / delete
                fid = int(qs["id"][0])
                action = form.get("action", [""])[0]
                if action == "reviewed":
                    conn.execute("UPDATE feedback SET status='reviewed', resolved_at=?, "
                                 "resolved_by='backoffice' WHERE id=?", (now_iso(), fid))
                    msg = "Marked reviewed."
                elif action == "reopen":
                    conn.execute("UPDATE feedback SET status='new', resolved_at=NULL WHERE id=?", (fid,))
                    msg = "Reopened."
                elif action == "delete":
                    conn.execute("DELETE FROM feedback WHERE id=?", (fid,))
                    msg = "Feedback deleted."
                else:
                    msg = "Unknown action."
                conn.commit()
                self._send(b"", code=303, headers={"Location": "/feedback?flash=" + urllib.parse.quote(msg)})
                return
            if parsed.path == "/member-company":              # operator sets a member's company
                mid = int(qs["id"][0])
                company = (form.get("company", [""])[0] or "").strip()
                conn.execute("UPDATE chat_managers SET company=? WHERE id=?", (company or None, mid))
                conn.commit()
                self._send(b"", code=303, headers={"Location": f"/member?id={mid}&flash=" +
                           urllib.parse.quote("Company updated." if company else "Company cleared.")})
                return
            if parsed.path == "/member-delete":               # operator: permanently delete a member
                from . import members
                res = members.delete(conn, int(qs["id"][0]))
                msg = (f"Deleted {res['deleted']} and all their data." if res.get("ok")
                       else f"Could not delete: {res.get('error')}")
                self._send(b"", code=303, headers={"Location": "/full-members?flash=" + urllib.parse.quote(msg)})
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
                    # fetch this site's traffic NOW so it can appear immediately —
                    # a no-op in MOCK mode (then it shows after the next refresh).
                    try:
                        from . import refresh as _refresh
                        tr = _refresh.refresh_one_site(conn, site_id)
                    except Exception:
                        tr = None
                    if tr and tr.get("markets"):
                        vis = f" — live now ({int(tr['etv']):,}/mo, top {tr['top']})"
                    elif tr and tr.get("skipped") == "not_live":
                        vis = " — will show after the next traffic refresh (add DataForSEO creds for instant traffic)"
                    else:
                        vis = " — approved; appears once its traffic clears the floor on a refresh"
                    msg = (f"Approved {domain}{vis}"
                           + (" · screenshot captured" if shot else "")
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

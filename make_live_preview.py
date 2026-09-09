#!/usr/bin/env python3
"""Generate preview-live.html — the member app rendered on LIVE DB data.

Unlike screens.html (a hand-built static mock with placeholder data), this reads
the real catalogue through radar/views.py — the exact payloads the app consumes —
so what you see is the live 787-site data with the per-country listing gate and
the auto-tagged verticals applied. Stdlib only.

    python3 make_live_preview.py            # -> preview-live.html (GB country tab)
    python3 make_live_preview.py DE         # pick a different country tab
"""
from __future__ import annotations

import base64
import html
import sys
from pathlib import Path

from radar import views
from radar.db import connect
from radar.locations import flag, name

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "preview-live.html"

_SHOTS: dict[str, str | None] = {}


def shot_uri(conn, domain: str) -> str | None:
    """Real homepage screenshot for a domain as a self-contained data URI (so the
    preview renders anywhere), or None if we haven't captured one."""
    if domain in _SHOTS:
        return _SHOTS[domain]
    uri = None
    row = conn.execute(
        """SELECT sc.image_ref FROM screenshots sc JOIN sites s ON s.id = sc.site_id
           WHERE s.domain = ? AND sc.provider <> 'mock'""", (domain,)).fetchone()
    if row and row["image_ref"]:
        try:
            b = Path(row["image_ref"]).read_bytes()
            if len(b) > 15_000:
                uri = "data:image/png;base64," + base64.b64encode(b).decode()
        except OSError:
            uri = None
    _SHOTS[domain] = uri
    return uri


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def kfmt(n) -> str:
    """Compact traffic number: 115,237,813 -> 115.2M, 1,315,770 -> 1.3M, 8,700 -> 8.7k."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return f"{int(n)}"


def trend_html(chip: str, direction) -> str:
    cls = {"up": "up", "down": "down"}.get(direction or "", "flat")
    return f"<span class='trend {cls}'>{esc(chip)}</span>"


def vchips(verticals) -> str:
    return "".join(f"<span class='vchip'>{esc(v)}</span>" for v in (verticals or []))


# ---------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------
def home_screen(conn) -> str:
    idx = views.countries_index(conn)
    home = views.world_home(conn, limit=8)
    markets = idx["countries"][:6]

    market_rows = "".join(
        f"""<a class='row'>
              <span class='rflag'>{flag(m['iso'])}</span>
              <span class='rmain'><b>{esc(m['name'])}</b>
                <span class='rsub'>{m['count']} sites · {kfmt(m['total_etv'])}/mo</span></span>
              <span class='chev'>›</span>
            </a>""" for m in markets)

    aff_rows = ""
    for a in home["affiliates"]:
        u = shot_uri(conn, a["domain"])
        thumb = f"<span class='thumb'><img src='{u}' alt=''></span>" if u else "<span class='thumb none'></span>"
        aff_rows += f"""<a class='row'>
              <span class='rank'>{a['rank']}</span>
              {thumb}
              <span class='rmain'><b>{esc(a['name'])}</b>
                <span class='rsub'>{flag(a['origin']['iso'])} {esc(a['origin']['name'])} · {kfmt(a['etv'])}/mo</span></span>
              {trend_html(a['trend'], a['trend_dir'])}
            </a>"""

    return f"""
      <div class='app-logo'>Aff<span>swap</span></div>
      <div class='app-tag'>iGaming Affiliate Swap Network</div>
      <div class='live-pill'>● LIVE · {idx['markets']} markets · DataForSEO</div>
      <div class='seclabel'>Top markets</div>
      <div class='group'>{market_rows}</div>
      <div class='seclabel'>Top affiliates worldwide</div>
      <div class='group'>{aff_rows}</div>
    """


def country_screen(conn, iso: str) -> str:
    data = views.country_list(conn, iso, sort="traffic")
    cards = []
    for c in data["cards"][:12]:  # preview caps the scroll; the app paginates
        also = "".join(f"<span class='mini'>{m['flag']}</span>" for m in c["also_in"][:4])
        new = "<span class='newbadge'>NEW</span>" if c.get("is_new") else ""
        u = shot_uri(conn, c["domain"])
        banner = f"<div class='cshot'><img src='{u}' alt=''></div>" if u else ""
        cards.append(f"""
          <a class='card'>
            {banner}
            <div class='ctop'>
              <b>{esc(c['name'])}</b>{new}
              {trend_html(c['trend'], c['trend_dir'])}
            </div>
            <div class='cmeta'>
              <span class='big'>{kfmt(c['etv'])}<span class='per'>/mo here</span></span>
              <span class='origin'>{c['traffic_origin']['flag']} top</span>
            </div>
            <div class='crow'>{vchips(c['verticals'])}<span class='also'>{('also '+also) if also else ''}</span></div>
          </a>""")
    body = "".join(cards) or "<div class='empty'>No sites clear the traffic bar in this market yet.</div>"
    h = data["country"]
    return f"""
      <div class='cty-head'><span class='cty-flag'>{flag(h['iso'])}</span>
        <div><b>{esc(h['name'])}</b><span class='cty-sub'>{data['count']} affiliates · live traffic</span></div></div>
      <div class='vtabs'>{''.join(f"<span class='vtab{' on' if t=='All' else ''}'>{t}</span>" for t in ('All','Casino','Sportsbook','Bingo','Poker'))}</div>
      {body}
    """


def profile_screen(conn, domain: str) -> str:
    p = views.site_profile(conn, domain, published_only=False)
    if not p:
        return "<div class='empty'>No profile.</div>"
    regs = p["regions"][:6]
    maxe = max([r["etv"] or 0 for r in regs], default=1) or 1
    reg_rows = "".join(
        f"""<div class='breg'>
             <span class='bflag'>{r['flag']}</span>
             <span class='bname'>{esc(r['name'])}{' ★' if r['primary'] else ''}</span>
             <span class='bar'><span style='width:{max(6, round((r['etv'] or 0)/maxe*100))}%'></span></span>
             <span class='betv'>{kfmt(r['etv'])}</span>
           </div>""" for r in regs)
    u = shot_uri(conn, p["domain"])
    hero = f"<div class='pf-shot'><img src='{u}' alt=''></div>" if u else ""
    return f"""
      {hero}
      <div class='pf-head'>
        <b>{esc(p['name'])}</b>
        <span class='pf-dom'>{esc(p['domain'])}</span>
      </div>
      <div class='pf-hero'>
        <div class='pf-etv'>{kfmt(p['etv'])}<span class='per'>/mo total</span></div>
        {trend_html(p['trend'], p.get('trend_dir'))}
      </div>
      <div class='crow'>{vchips(p['verticals'])}</div>
      <div class='seclabel'>Traffic by market</div>
      <div class='group pad'>{reg_rows}</div>
      <div class='pf-contact'>🔒 Contact unlocked by swapping</div>
    """


def pick_profile_domain(conn, iso: str) -> str:
    """A good hero for the profile phone: the top-traffic visible site in `iso`."""
    d = views.country_list(conn, iso, sort="traffic")
    if d["cards"]:
        return d["cards"][0]["domain"]
    w = views.world_home(conn, limit=1)
    return w["affiliates"][0]["domain"] if w["affiliates"] else ""


def phone(label: str, inner: str) -> str:
    return f"""
      <figure class='phone'>
        <div class='screen'>
          <div class='statusbar'><span>9:41</span><span>▂▄▆ 100%</span></div>
          <div class='scroll'>{inner}</div>
        </div>
        <figcaption>{esc(label)}</figcaption>
      </figure>"""


CSS = """
:root{--bg:#04050c;--card:rgba(16,26,52,.6);--card2:rgba(22,34,66,.8);--line:rgba(80,196,255,.3);
  --ink:#eaf4ff;--ink2:#94afde;--ink3:#5a6e97;--cyan:#38c6ff;--up:#2cf5d0;--down:#ff4d8f;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--mono:ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--ink);
  background:radial-gradient(1200px 800px at 15% -8%,rgba(56,198,255,.16),transparent 58%),
             radial-gradient(1000px 700px at 100% 0,rgba(90,120,255,.12),transparent 55%),
             linear-gradient(180deg,#06080f,#04050c);min-height:100vh}
.wrap{max-width:1160px;margin:0 auto;padding:34px 20px 60px}
.eyebrow{font:600 12px/1 var(--mono);letter-spacing:.22em;color:var(--cyan);text-transform:uppercase}
h1{margin:.35em 0 .1em;font-size:30px;letter-spacing:-.5px}
h1 span{color:var(--cyan);text-shadow:0 0 26px rgba(56,198,255,.6)}
.lead{color:var(--ink2);max-width:760px;line-height:1.5;margin:0 0 6px}
.legend{color:var(--ink3);font-size:12.5px;margin-bottom:26px}
.legend b{color:var(--cyan)}
.phones{display:flex;gap:34px;flex-wrap:wrap;justify-content:center}
.phone{margin:0;width:326px;max-width:100%}
.phone figcaption{text-align:center;color:var(--ink2);font:600 11px/1.4 var(--mono);
  letter-spacing:.12em;text-transform:uppercase;margin-top:12px}
.screen{position:relative;border-radius:40px;padding:14px 13px 20px;height:660px;overflow:hidden;
  background:linear-gradient(180deg,#080d1f,#05070f);
  box-shadow:0 40px 90px -24px rgba(56,198,255,.4),0 0 0 1px rgba(90,200,255,.32),0 0 44px -6px rgba(56,198,255,.32)}
.statusbar{display:flex;justify-content:space-between;font:600 11px var(--mono);color:var(--ink3);padding:2px 8px 10px}
.scroll{height:100%;overflow-y:auto;padding-bottom:40px;scrollbar-width:none}
.scroll::-webkit-scrollbar{display:none}
.app-logo{text-align:center;font-weight:800;font-size:24px;letter-spacing:-.5px;margin-top:6px}
.app-logo span{color:var(--cyan);text-shadow:0 0 22px rgba(56,198,255,.7)}
.app-tag{text-align:center;color:var(--ink3);font:600 9.5px var(--mono);letter-spacing:.2em;text-transform:uppercase;margin-top:2px}
.live-pill{width:max-content;margin:12px auto 4px;font:600 10.5px var(--mono);color:var(--up);
  border:1px solid rgba(44,245,208,.4);border-radius:999px;padding:4px 11px}
.seclabel{color:var(--cyan);font:700 10.5px var(--mono);letter-spacing:.16em;text-transform:uppercase;
  margin:20px 4px 9px;text-shadow:0 0 14px rgba(56,198,255,.4)}
.group{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.group.pad{padding:6px 4px}
.row{display:flex;align-items:center;gap:11px;padding:11px 13px;text-decoration:none;color:var(--ink);
  border-bottom:1px solid rgba(80,196,255,.12)}
.row:last-child{border-bottom:none}
.rflag{font-size:20px}.rank{width:20px;text-align:center;color:var(--cyan);font:700 14px var(--mono);text-shadow:0 0 12px rgba(56,198,255,.6)}
.rmain{flex:1;display:flex;flex-direction:column;gap:2px;min-width:0}
.rmain b{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rsub{color:var(--ink2);font-size:11.5px}
.chev{color:var(--ink3);font-size:18px}
.trend{font:700 11px var(--mono);white-space:nowrap;padding:2px 7px;border-radius:999px}
.trend.up{color:var(--up);background:rgba(44,245,208,.12)}
.trend.down{color:var(--down);background:rgba(255,77,143,.12)}
.trend.flat{color:var(--ink3);background:rgba(120,140,170,.12)}
.cty-head{display:flex;align-items:center;gap:12px;margin:8px 4px 4px}
.cty-flag{font-size:30px}
.cty-head b{font-size:18px}.cty-sub{display:block;color:var(--ink2);font-size:11.5px}
.vtabs{display:flex;gap:6px;overflow-x:auto;margin:14px 0 12px;padding-bottom:2px}
.vtab{white-space:nowrap;font:600 11.5px var(--sans);color:var(--ink2);border:1px solid var(--line);
  border-radius:999px;padding:5px 12px}
.vtab.on{color:#7fe4ff;background:rgba(56,198,255,.13);box-shadow:inset 0 0 0 1px var(--cyan),0 0 18px rgba(56,198,255,.5)}
.card{display:block;text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);
  border-radius:15px;padding:12px 13px;margin-bottom:10px}
.ctop{display:flex;align-items:center;gap:8px}
.ctop b{font-size:15px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cmeta{display:flex;align-items:baseline;gap:12px;margin:7px 0 8px}
.big{font:800 22px var(--sans);color:#fff}
.per{font:600 10px var(--mono);color:var(--ink3);margin-left:4px}
.origin{color:var(--ink2);font-size:12px}
.crow{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.vchip{font:700 9.5px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:#7fe4ff;
  border:1px solid rgba(56,198,255,.4);border-radius:999px;padding:3px 8px;background:rgba(56,198,255,.07)}
.also{color:var(--ink3);font-size:11px;margin-left:2px}.mini{font-size:13px}
.newbadge{font:700 8.5px var(--mono);color:#04121a;background:var(--cyan);border-radius:4px;padding:2px 5px}
.empty{color:var(--ink3);text-align:center;padding:40px 10px;font-size:13px}
.pf-head{margin:8px 4px 12px}.pf-head b{font-size:20px}
.pf-dom{display:block;color:var(--ink3);font:600 12px var(--mono);margin-top:2px}
.pf-hero{display:flex;align-items:baseline;gap:14px;margin:4px 4px 12px}
.pf-etv{font:800 30px var(--sans);color:#fff}
.breg{display:flex;align-items:center;gap:9px;padding:7px 8px}
.bflag{font-size:16px}.bname{width:96px;font-size:12px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{flex:1;height:7px;background:rgba(80,196,255,.12);border-radius:999px;overflow:hidden}
.bar span{display:block;height:100%;background:linear-gradient(90deg,#38c6ff,#5ad6ff);box-shadow:0 0 10px rgba(56,198,255,.7)}
.betv{width:52px;text-align:right;font:700 11px var(--mono);color:var(--ink2)}
.pf-contact{margin:16px 4px 0;text-align:center;color:var(--ink2);font-size:12px;
  border:1px dashed var(--line);border-radius:12px;padding:12px}
.thumb{width:46px;height:32px;border-radius:7px;overflow:hidden;flex:0 0 auto;border:1px solid var(--line);background:#0a1224}
.thumb img{width:100%;height:100%;object-fit:cover;object-position:top}
.thumb.none{background:linear-gradient(135deg,#0c1730,#0a1020)}
.cshot{margin:-1px -2px 10px;border-radius:11px;overflow:hidden;border:1px solid var(--line);height:106px;background:#0a1224}
.cshot img{width:100%;height:100%;object-fit:cover;object-position:top;display:block}
.pf-shot{border-radius:13px;overflow:hidden;border:1px solid var(--line);height:150px;margin:6px 2px 12px;background:#0a1224;box-shadow:0 0 26px -6px rgba(56,198,255,.45)}
.pf-shot img{width:100%;height:100%;object-fit:cover;object-position:top;display:block}
@media (max-width:520px){.screen{height:600px}}
"""


def main() -> None:
    iso = (sys.argv[1] if len(sys.argv) > 1 else "GB").upper()
    conn = connect()
    try:
        prof_dom = pick_profile_domain(conn, iso)
        home = home_screen(conn)
        country = country_screen(conn, iso)
        profile = profile_screen(conn, prof_dom) if prof_dom else "<div class='empty'>No data.</div>"
        n = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    finally:
        conn.close()

    doc = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Affswap — live app preview</title><style>{CSS}</style></head><body>
<div class='wrap'>
  <div class='eyebrow'>Affswap · live app preview</div>
  <h1>The app, on <span>real traffic</span></h1>
  <p class='lead'>Rendered from the live catalogue through the same <code>views.py</code> payloads the
    app consumes — {n} sites, live DataForSEO traffic, auto-tagged verticals, and the per-country
    listing gate (500/mo · 1,500/mo for large sites) all applied.</p>
  <p class='legend'>Country tab shown: <b>{esc(name(iso))} {flag(iso)}</b> · only sites that clear the
    traffic bar appear — everything else stays in the back office.</p>
  <div class='phones'>
    {phone("Home · markets + top affiliates", home)}
    {phone(f"{name(iso)} tab · gated + verticals", country)}
    {phone("Site profile · traffic by market", profile)}
  </div>
</div></body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT.name} ({len(doc):,} bytes) · country={iso} · profile={prof_dom}")


if __name__ == "__main__":
    main()

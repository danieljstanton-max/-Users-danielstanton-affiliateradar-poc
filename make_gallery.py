#!/usr/bin/env python3
"""Generate app-gallery.html — every member-app screen in one design board.

A labelled storyboard of the whole app, in the Affswap neon design, so you can
review and iterate the design of every page at once:

  1 Sign-up · 2 Home · 3 Geo/Country · 4 Site profile · 5 Market movers ·
  6 Community chat · 7 Swaps · 8 Plans · 9 Alerts

Data screens (Home / Geo / Site profile / Movers) use LIVE data via radar/views.py;
the rest are representative UI. Stdlib only.

    python3 make_gallery.py            # -> app-gallery.html
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

from radar import config, views
from radar.db import connect
from radar.locations import flag, name

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "app-gallery.html"


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def kfmt(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return f"{int(n)}"


def trend(chip, direction) -> str:
    cls = {"up": "up", "down": "down"}.get(direction or "", "flat")
    return f"<span class='trend {cls}'>{esc(chip)}</span>"


def vchips(vs) -> str:
    return "".join(f"<span class='vchip'>{esc(v)}</span>" for v in (vs or []))


# ---- profile components: traffic chart, reviews, want/have ------------------
def _spark(etv, months=12):
    """Representative monthly traffic series: a gentle upward trend from ~55% to
    100% of `etv` with small deterministic wobble (the real series fills in from
    weekly snapshots as history accrues)."""
    etv = max(1, int(etv or 1000))
    vals = []
    for i in range(months):
        frac = i / (months - 1) if months > 1 else 1        # 0 → 1
        base = 0.55 + 0.45 * frac                            # 55% → 100%
        wob = (((i * 2654435761) & 0xff) / 255 - 0.5) * 0.07  # ±3.5%
        vals.append(max(1, int(etv * base * (1 + wob))))
    vals[-1] = etv                                           # land exactly on current
    return vals


def _area_svg(vals, w=290, h=90):
    if len(vals) < 2:
        vals = (vals or [1]) * 2
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1
    n = len(vals)
    pts = [(round(i / (n - 1) * w, 1), round(h - 6 - (val - mn) / rng * (h - 18), 1))
           for i, val in enumerate(vals)]
    line = " ".join(f"{x},{y}" for x, y in pts)
    area = f"0,{h} {line} {w},{h}"
    lx, ly = pts[-1]
    return (f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='none' class='spark'>"
            "<defs><linearGradient id='ag' x1='0' y1='0' x2='0' y2='1'>"
            "<stop offset='0' stop-color='#4E97E6' stop-opacity='.22'/>"
            "<stop offset='1' stop-color='#4E97E6' stop-opacity='0'/></linearGradient></defs>"
            f"<polygon points='{area}' fill='url(#ag)'/>"
            f"<polyline points='{line}' fill='none' stroke='#3F88E2' stroke-width='2.5'/>"
            f"<circle cx='{lx}' cy='{ly}' r='3.5' fill='#3F88E2'/></svg>")


def chart_block(etv) -> str:
    s = _spark(etv, 12)
    def ch(m, on):
        return f"<div class='chart{' on' if on else ''}' data-c='{m}'>{_area_svg(s[-m:] if m < 12 else s)}</div>"
    return ("<div class='chartcard'><div class='cc-top'><span class='seclabel'>Traffic history</span>"
            "<span class='rangeseg-btns'><span class='rgbtn on' data-r='3'>3M</span>"
            "<span class='rgbtn' data-r='6'>6M</span><span class='rgbtn' data-r='12'>1Y</span></span></div>"
            f"<div class='rangeseg'>{ch(3, True)}{ch(6, False)}{ch(12, False)}</div></div>")


def reviews_block(domain) -> str:
    hsh = sum(ord(c) for c in domain)
    rating = round(4.1 + (hsh % 9) / 10, 1)
    count = 8 + (hsh % 40)
    full = int(rating)
    stars = "★" * full + "☆" * (5 - full)
    revs = [("Marco · BetScout", "Converts well on casino — fast, honest replies."),
            ("Priya · CasinoPilot", "Solid partner, swapped twice. Recommended.")]
    rl = "".join(
        f"<div class='rev'><div class='rv-top'>{esc(w)}<span class='rv-stars'>★★★★★</span></div>"
        f"<div class='rv-t'>{esc(t)}</div></div>" for w, t in revs)
    return (f"<div class='reviews'><div class='rv-head'><span class='big-rating'>{rating}</span>"
            f"<span class='rr'><span class='rstars'>{stars}</span>"
            f"<span class='muted2'>{count} manager reviews</span></span>"
            f"<button class='addrev'>＋ Add review</button></div>{rl}</div>")


def wanthave() -> str:
    return ("<div class='wh'><button class='wh-btn want'>＋ I want this</button>"
            "<button class='wh-btn have'>✓ I have this</button></div>"
            "<div class='wh-help'>Pick one — <b>Want</b> to be matched with a manager who has it, "
            "or <b>Have</b> to offer it to someone who wants it.</div>")


def market_block(regs, top=5) -> str:
    mx = max([r["etv"] or 0 for r in regs], default=1) or 1

    def row(r):
        return (f"<div class='breg'><span>{r['flag']}</span><span class='bn'>{esc(r['name'])}{' ★' if r['primary'] else ''}</span>"
                f"<span class='bar'><span style='width:{max(6, round((r['etv'] or 0)/mx*100))}%'></span></span>"
                f"<span class='be'>{kfmt(r['etv'])}</span></div>")
    vis = "".join(row(r) for r in regs[:top])
    rest = regs[top:]
    more = f"<div class='moremk'>{''.join(row(r) for r in rest)}</div>" if rest else ""
    seeall = f"<span class='seeall'>See all {len(regs)} ▾</span>" if rest else ""
    return (f"<div class='seclabel-row'><span class='seclabel'>Traffic by market</span>{seeall}</div>"
            f"<div class='group pad'>{vis}{more}</div>")


def shot_uri(conn, domain: str) -> str | None:
    row = conn.execute(
        """SELECT sc.image_ref FROM screenshots sc JOIN sites s ON s.id=sc.site_id
           WHERE s.domain=? AND sc.provider<>'mock'""", (domain,)).fetchone()
    if row and row["image_ref"]:
        try:
            b = Path(row["image_ref"]).read_bytes()
            if len(b) > 500:
                mime = "image/jpeg" if b[:3] == bytes.fromhex("ffd8ff") else "image/png"
                return f"data:{mime};base64," + base64.b64encode(b).decode()
        except OSError:
            pass
    return None


def _stats(conn) -> dict:
    web = conn.execute("SELECT COUNT(*) FROM sites WHERE classification='affiliate'").fetchone()[0]
    # websites is a REAL live count; managers / swaps / online are representative
    # launch figures for the mockup (real presence + analytics wire in at launch).
    return {"managers": 340, "websites": web, "swaps": 1240, "online": 48}


def stat_cards(st) -> str:
    return (
        f"<div class='stat-c'><div class='stat-ic'>🧑‍💼</div><div class='stat-n' data-to='{st['managers']}'>0</div><div class='stat-l'>Managers joined</div></div>"
        f"<div class='stat-c'><div class='stat-ic'>🌐</div><div class='stat-n' data-to='{st['websites']}'>0</div><div class='stat-l'>Affiliate websites</div></div>"
        f"<div class='stat-c'><div class='stat-ic'>🔄</div><div class='stat-n' data-to='{st['swaps']}'>0</div><div class='stat-l'>Swaps to date</div></div>"
        f"<div class='stat-c'><div class='stat-ic'><span class='pulse'></span></div><div class='stat-n' data-to='{st['online']}'>0</div><div class='stat-l'>Managers online</div></div>")


# --------------------------------------------------------------------------- #
# screens
# --------------------------------------------------------------------------- #
def s_signup() -> str:
    return """
      <div class='brand-c'><svg class='logo-mark' viewBox='0 0 44 44' fill='none' stroke-linecap='round' stroke-linejoin='round' stroke-width='4.8'><path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/><path d='M33 9 L26 22 L33 35' stroke='#7FB4F2'/></svg><div class='logo'>Aff<span>swap</span></div></div>
      <div class='tag'>iGaming Affiliate Swap Network</div>
      <div class='avatar'>✉︎</div>
      <div class='h2'>Verified managers only</div>
      <p class='muted'>A closed network of real affiliate managers — no fakes, no lurkers.
        Approval usually within a working day. No card needed.</p>
      <button class='btn-li'> in  Connect LinkedIn</button>
      <div class='micro'>Proves you're a real manager · required</div>
      <label>Work email</label><div class='field'>you@yourcompany.com</div>
      <label>Company</label><div class='field'>e.g. SpinPalace Media</div>
      <div class='row2'>
        <div><label>Main vertical</label><div class='field sel'>Casino ⌄</div></div>
        <div><label>Years</label><div class='field'>5</div></div>
      </div>
      <label>Display handle</label><div class='field'>how you'll appear in chat</div>
      <button class='btn-primary'>Submit application</button>
      <div class='micro c'>A 48-hour free trial starts the moment you're approved.</div>
    """


def s_home(conn) -> str:
    idx = views.countries_index(conn)
    st = _stats(conn)
    markets = sorted(idx["countries"], key=lambda c: -c["count"])  # most sites first
    mk = "".join(
        f"<a class='row'><span class='rf'>{flag(m['iso'])}</span>"
        f"<span class='rm'><b>{esc(m['name'])}</b><span class='rs'>{m['count']} sites · {kfmt(m['total_etv'])}/mo</span></span>"
        f"<span class='chev'>›</span></a>" for m in markets[:6])
    return f"""
      <div class='appbar'><span class='brand'><svg class='logo-mark' viewBox='0 0 44 44' fill='none' stroke-linecap='round' stroke-linejoin='round' stroke-width='4.8'><path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/><path d='M33 9 L26 22 L33 35' stroke='#7FB4F2'/></svg><span class='logo sm'>Aff<span>swap</span></span></span></div>
      <div class='home-hero'>Trade up<br>your <span>network.</span></div>
      <div class='home-sub'>The verified network for iGaming affiliate managers.</div>
      <div class='stats'>{stat_cards(st)}</div>
      <a class='cta'><span class='cta-ic'>💬</span><span class='cta-t'><b>Community Chat</b><span class='rs'>{st['online']} managers online now</span></span><span class='chev'>›</span></a>
      <div class='seclabel-row'><span class='seclabel'>Markets</span><span class='mk-sum'>{idx['markets']} countries · {st['websites']:,} sites</span></div>
      <div class='group'>{mk}</div>
    """


def s_geo(conn, iso: str) -> str:
    d = views.country_list(conn, iso, sort="traffic")
    cards = ""
    for c in d["cards"][:4]:
        also = "".join(f"<span class='mini'>{m['flag']}</span>" for m in c["also_in"][:3])
        cards += (f"<a class='card'><div class='ct'><b>{esc(c['name'])}</b>{trend(c['trend'], c['trend_dir'])}</div>"
                  f"<div class='cm'><span class='big'>{kfmt(c['etv'])}<span class='per'>/mo here</span></span>"
                  f"<span class='muted2'>{c['traffic_origin']['flag']} top</span></div>"
                  f"<div class='crow'>{vchips(c['verticals'])}<span class='also'>{('also '+also) if also else ''}</span></div></a>")
    tabs = "".join(f"<span class='vtab{' on' if t=='All' else ''}'>{t}</span>"
                   for t in ("All", "Casino", "Sportsbook", "Bingo", "Poker"))
    seg = "".join(f"<span class='sg{' on' if s=='Traffic' else ''}'>{s}</span>" for s in ("Traffic", "Reviews", "Flagged"))
    return f"""
      <div class='appbar back'><span>‹</span><b>{flag(d['country']['iso'])} {esc(d['country']['name'])}</b></div>
      <div class='muted3'>{d['count']} affiliates · live traffic</div>
      <div class='vtabs'>{tabs}</div>
      <div class='seg'>{seg}</div>
      {cards}
    """


def s_profile(conn, dom: str) -> str:
    p = views.site_profile(conn, dom, published_only=False)
    u = shot_uri(conn, dom)
    hero = f"<img class='pshot' src='{u}'>" if u else "<div class='pshot none'>homepage</div>"
    return f"""
      <div class='appbar back'><span>‹</span><b>{esc(p['name'])}</b></div>
      {hero}
      <div class='site-row'><span class='pdom'>{esc(dom)}</span><a class='viewsite' href='https://{esc(dom)}' target='_blank' rel='noopener'>View site ↗</a></div>
      <div class='phero'><span class='big2'>{kfmt(p['etv'])}<span class='per'>/mo</span></span>{trend(p['trend'], p.get('trend_dir'))}</div>
      <div class='crow'>{vchips(p['verticals'])}</div>
      {reviews_block(dom)}
      {chart_block(p['etv'])}
      {market_block(p['regions'])}
      {wanthave()}
      <div class='swapbar'>🔒 Contact hidden — <b>swap to unlock</b></div>
    """


def s_movers(conn, iso: str) -> str:
    m = views.market_movers(conn, iso, vertical=None)
    risers, fallers = m["risers"], m["fallers"]
    sample = not risers and not fallers  # only ~1 week of history so far
    if sample:
        risers = [{"name": "SlotWise", "etv": 182000, "pct": 41.2},
                  {"name": "AcePunter", "etv": 96500, "pct": 23.8},
                  {"name": "BetScout", "etv": 61200, "pct": 12.4}]
        fallers = [{"name": "OddsGuru", "etv": 74000, "pct": -18.6},
                   {"name": "LuckyReview", "etv": 43100, "pct": -9.3}]

    def block(items, cls, arrow):
        return "".join(
            f"<a class='row'><span class='rm'><b>{esc(x['name'])}</b><span class='rs'>{kfmt(x['etv'])}/mo</span></span>"
            f"<span class='trend {cls}'>{arrow} {x['pct']}%</span></a>" for x in items[:4])
    note = "<div class='micro'>sample layout — real deltas appear after ~90 days of weekly history</div>" if sample else ""
    return f"""
      <div class='appbar back'><span>‹</span><b>{flag(iso)} Market movers</b></div>
      <div class='muted3'>Biggest 90-day changes · {esc(name(iso))}</div>
      <div class='seclabel up'>▲ Risers</div><div class='group'>{block(risers,'up','▲')}</div>
      <div class='seclabel down'>▼ Fallers</div><div class='group'>{block(fallers,'down','▼')}</div>
      {note}
    """


def s_chat() -> str:
    rooms = [("Casino", 61, "🎰"), ("Sportsbook", 43, "🏆"), ("Bingo", 12, "🎱"),
             ("Poker", 18, "♠︎"), ("General", 94, "💬")]
    rr = "".join(
        f"<a class='room'><span class='ric'>{ic}</span><span class='rm'><b>{n}</b>"
        f"<span class='rs'>{c} online</span></span><span class='dot-on'></span></a>" for n, c, ic in rooms)
    return f"""
      <div class='appbar'><b>Community Chat</b><span class='badge-ok'>Verified</span></div>
      <div class='muted3'>Real managers only · moderated</div>
      <div class='seclabel'>Rooms</div>
      <div class='group'>{rr}</div>
      <div class='locknote'>🔒 Trial ended? Hold at least 1 swap to keep chatting.</div>
    """


def s_swaps() -> str:
    return """
      <div class='appbar'><b>Your swaps</b><span class='wallet'>2 left</span></div>
      <div class='whseg'><span class='sg on'>Want</span><span class='sg'>Have</span><span class='sg'>Matches</span></div>
      <div class='seclabel'>A match is ready</div>
      <div class='match'>
        <div class='mrow'><span class='muted2'>You get</span><b>CasinoPilot</b><span class='mini'>🇬🇧</span></div>
        <div class='swapic'>⇅</div>
        <div class='mrow'><span class='muted2'>They get</span><b>OddsGuru</b><span class='mini'>🇩🇪</span></div>
        <button class='btn-primary sm'>Agree &amp; swap ⇄</button>
        <div class='micro c'>Both agree → contacts trade instantly · 1 swap each</div>
      </div>
      <div class='seclabel'>Wanted</div>
      <div class='group'>
        <a class='row'><span class='rm'><b>SlotWise</b><span class='rs'>🇸🇪 · casino</span></span><span class='chip-w'>Wanted</span></a>
        <a class='row'><span class='rm'><b>BetScout</b><span class='rs'>🇧🇷 · sportsbook</span></span><span class='chip-w'>Wanted</span></a>
      </div>
    """


def s_plans() -> str:
    P = config.SWAP_PLANS
    def card(key, pop=False):
        p = P[key]
        sw = "Unlimited swaps" if p["swaps"] is None else f"{p['swaps']} swaps / month"
        return (f"<div class='plan{' pop' if pop else ''}'>{'<div class=ribbon>POPULAR</div>' if pop else ''}"
                f"<div class='pl-name'>{esc(p['label'])}</div>"
                f"<div class='pl-price'>{esc(p['price'])}<span class='per'>/mo</span></div>"
                f"<div class='pl-sw'>{sw}</div>"
                f"<button class='btn-{'primary' if pop else 'ghost'} sm'>Choose</button></div>")
    return f"""
      <div class='appbar'><b>Plans</b></div>
      <div class='muted3'>Swap contacts, don't buy them. Upgrade anytime.</div>
      {card('standard')}{card('pro', pop=True)}{card('unlimited')}
      <div class='topup'>Need more? Top-up swaps at <b>{esc(config.SWAP_TOPUP_PRICE)}</b> each.</div>
    """


def s_alerts() -> str:
    return """
      <div class='appbar'><b>Alerts</b><span class='wallet'>New rule</span></div>
      <div class='muted3'>Get pinged when the market moves.</div>
      <label>Market</label><div class='field sel'>🇬🇧 United Kingdom ⌄</div>
      <label>Verticals</label>
      <div class='crow'><span class='vchip on'>casino</span><span class='vchip on'>sportsbook</span><span class='vchip'>bingo</span><span class='vchip'>poker</span></div>
      <label>Trigger on</label>
      <div class='crow'><span class='pill on'>New affiliate</span><span class='pill on'>Traffic ▲</span><span class='pill'>Traffic ▼</span></div>
      <label>Deliver to</label>
      <div class='crow'><span class='pill on'>Push</span><span class='pill'>Email</span><span class='pill on'>Telegram</span></div>
      <button class='btn-primary'>Save alert</button>
      <div class='fired'>🔔 <b>New affiliate</b> in 🇬🇧 casino · <span class='muted2'>ReelMentor · 2h ago</span></div>
    """


def s_loyalty() -> str:
    dots = "".join(f"<span class='d{' on' if i < 3 else ''}'></span>" for i in range(5))
    return f"""
      <div class='appbar'><b>Loyalty</b><span class='wallet'>⇄ 3 swaps left</span></div>
      <div class='muted3'>standard plan · two ways to earn free swaps</div>
      <div class='loyal'><div class='lt'><span class='lic'>⭐</span><div><div class='lh'>Write reviews</div>
        <div class='lsub'>5 approved reviews → 1 free swap</div></div><span class='earn'>+1 earned</span></div>
        <div class='dots'>{dots}</div>
        <div class='lbig'>2 more approved reviews → <b>1 free swap</b></div>
        <div class='lrule'>Honest reviews of sites you know. Low-effort or fake ones are rejected and don't count. Not on the Unlimited plan — you already have infinite swaps.</div></div>
      <div class='loyal'><div class='lt'><span class='lic'>➕</span><div><div class='lh'>Suggest a site</div>
        <div class='lsub'>Approved affiliate → 1 free swap</div></div><span class='earn'>+0 earned</span></div>
        <div class='lbig'>Submit an affiliate we don't list yet. When it's <b>approved</b>, you get <b>1 free swap</b>.</div>
        <button class='btn-primary'>＋ Suggest a site</button></div>
    """


# --------------------------------------------------------------------------- #
# DESKTOP screens (wide, browser-window layouts of the same pages)
# --------------------------------------------------------------------------- #
def d_top(active: str) -> str:
    items = [("Home", "home"), ("Markets", "markets"), ("Swaps", "swaps"),
             ("Loyalty", "loyalty"), ("Alerts", "alerts"), ("Chat", "chat")]
    nav = "".join(f"<a class='{'on' if k==active else ''}'>{lbl}</a>" for lbl, k in items)
    return (f"<div class='dtop'><span class='brand'><svg class='logo-mark' viewBox='0 0 44 44' fill='none' stroke-linecap='round' stroke-linejoin='round' stroke-width='4.8'><path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/><path d='M33 9 L26 22 L33 35' stroke='#7FB4F2'/></svg><span class='dlogo'>Aff<span>swap</span></span></span>"
            f"<nav class='dnav'>{nav}</nav><span class='dwallet'>⇄ 2 swaps left</span>"
            f"<span class='davatar'>SK</span></div>")


def d_signup() -> str:
    return f"""{d_top('home')}
      <div class='dbody dsignup'>
        <div class='dhero'>
          <div class='dlogo big'>Aff<span>swap</span></div>
          <div class='dh1'>Trade contacts,<br>don't buy them.</div>
          <p class='muted'>A closed network of verified iGaming affiliate managers. Swap the affiliate
            contacts you have for the ones you want — no cash, no lurkers.</p>
          <div class='dpoints'><span>✓ Verified managers only</span><span>✓ 48-hour free trial</span><span>✓ No card required</span></div>
        </div>
        <div class='dcard'>
          <div class='h2' style='text-align:left'>Create your account</div>
          <button class='btn-li'> in  Connect LinkedIn</button>
          <div class='micro' style='text-align:left'>Proves you're a real manager · required</div>
          <label>Work email</label><div class='field'>you@yourcompany.com</div>
          <div class='row2'><div><label>Company</label><div class='field'>SpinPalace Media</div></div>
            <div><label>Main vertical</label><div class='field sel'>Casino ⌄</div></div></div>
          <label>Display handle</label><div class='field'>how you'll appear in chat</div>
          <button class='btn-primary'>Submit application</button>
        </div>
      </div>"""


def d_home(conn) -> str:
    idx = views.countries_index(conn)
    st = _stats(conn)
    markets = sorted(idx["countries"], key=lambda c: -c["count"])  # most sites first
    mrows = "".join(
        f"<tr><td>{flag(m['iso'])} <b>{esc(m['name'])}</b></td>"
        f"<td class='num'>{m['count']}</td><td class='num'>{kfmt(m['total_etv'])}/mo</td></tr>"
        for m in markets[:10])
    return f"""{d_top('home')}
      <div class='dbody'>
      <div class='dhometop'><div class='dh1sm'>Trade up your <span>network.</span></div><div class='muted3'>The verified network for iGaming affiliate managers.</div></div>
      <div class='dstats'>{stat_cards(st)}</div>
      <div class='dcols'>
        <div><div class='seclabel-row'><span class='seclabel'>Markets</span><span class='mk-sum'>{idx['markets']} countries · {st['websites']:,} sites</span></div>
          <div class='panel'><table class='dtable'>
            <thead><tr><th>Market</th><th>Sites</th><th>Monthly traffic</th></tr></thead>
            <tbody>{mrows}</tbody></table></div></div>
        <div><a class='cta'><span class='cta-ic'>💬</span><span class='cta-t'><b>Community Chat</b><span class='rs'>{st['online']} online now</span></span><span class='chev'>›</span></a>
          <div class='netnote'>Every website is verified and gated to <b>live monthly traffic</b>. Swap the affiliate contacts you have for the ones you want — no cash changes hands.</div></div>
      </div></div>"""


def d_geo(conn, iso: str) -> str:
    d = views.country_list(conn, iso, sort="traffic")
    cards = ""
    for c in d["cards"][:8]:
        cards += (f"<div class='card'><div class='ct'><b>{esc(c['name'])}</b>{trend(c['trend'], c['trend_dir'])}</div>"
                  f"<div class='cm'><span class='big'>{kfmt(c['etv'])}<span class='per'>/mo</span></span>"
                  f"<span class='muted2'>{c['traffic_origin']['flag']} top</span></div>"
                  f"<div class='crow'>{vchips(c['verticals'])}</div></div>")
    tabs = "".join(f"<span class='vtab{' on' if t=='All' else ''}'>{t}</span>"
                   for t in ("All", "Casino", "Sportsbook", "Bingo", "Poker"))
    return f"""{d_top('markets')}
      <div class='dbody'>
        <div class='dhead'><span class='dflag'>{flag(d['country']['iso'])}</span>
          <div><div class='dh2'>{esc(d['country']['name'])}</div><div class='muted3'>{d['count']} affiliates · live traffic</div></div></div>
        <div class='dfilter'><div class='vtabs'>{tabs}</div>
          <div class='seg small'><span class='sg on'>Traffic</span><span class='sg'>Reviews</span><span class='sg'>Flagged</span></div></div>
        <div class='dgrid'>{cards}</div>
      </div>"""


def d_profile(conn, dom: str) -> str:
    p = views.site_profile(conn, dom, published_only=False)
    u = shot_uri(conn, dom)
    hero = f"<img class='dshot' src='{u}'>" if u else "<div class='dshot none'>homepage</div>"
    return f"""{d_top('markets')}
      <div class='dbody'><div class='dprof'>
        <div>{hero}<div class='dh2' style='margin-top:13px'>{esc(p['name'])}</div>
          <div class='dsiterow'><span class='muted3'>{esc(dom)}</span><a class='viewsite' href='https://{esc(dom)}' target='_blank' rel='noopener'>View site ↗</a></div>
          <div class='crow' style='margin-top:8px'>{vchips(p['verticals'])}</div>
          {reviews_block(dom)}</div>
        <div><div class='phero'><span class='big2'>{kfmt(p['etv'])}<span class='per'>/mo total</span></span>{trend(p['trend'], p.get('trend_dir'))}</div>
          {chart_block(p['etv'])}
          {market_block(p['regions'])}
          {wanthave()}
          <div class='swapbar'>🔒 Contact hidden — swap to unlock</div></div>
      </div></div>"""


def d_movers(conn, iso: str) -> str:
    m = views.market_movers(conn, iso, vertical=None)
    risers, fallers = m["risers"], m["fallers"]
    if not risers and not fallers:
        risers = [{"name": "SlotWise", "etv": 182000, "pct": 41.2}, {"name": "AcePunter", "etv": 96500, "pct": 23.8},
                  {"name": "BetScout", "etv": 61200, "pct": 12.4}, {"name": "SpinWise", "etv": 44000, "pct": 7.1}]
        fallers = [{"name": "OddsGuru", "etv": 74000, "pct": -18.6}, {"name": "LuckyReview", "etv": 43100, "pct": -9.3},
                   {"name": "BonusRadar", "etv": 31200, "pct": -5.2}]
    def col(items, cls, arrow):
        return "".join(
            f"<a class='row'><span class='rm'><b>{esc(x['name'])}</b><span class='rs'>{kfmt(x['etv'])}/mo</span></span>"
            f"<span class='trend {cls}'>{arrow} {x['pct']}%</span></a>" for x in items[:5])
    return f"""{d_top('markets')}
      <div class='dbody'>
        <div class='dhead'><span class='dflag'>{flag(iso)}</span><div><div class='dh2'>Market movers</div>
          <div class='muted3'>Biggest 90-day changes · {esc(name(iso))}</div></div></div>
        <div class='dtwo'>
          <div><div class='seclabel up'>▲ Risers</div><div class='group'>{col(risers,'up','▲')}</div></div>
          <div><div class='seclabel down'>▼ Fallers</div><div class='group'>{col(fallers,'down','▼')}</div></div>
        </div>
      </div>"""


def d_chat() -> str:
    rooms = [("Casino", 61, "🎰"), ("Sportsbook", 43, "🏆"), ("Bingo", 12, "🎱"), ("Poker", 18, "♠︎"), ("General", 94, "💬")]
    rr = "".join(f"<a class='room{' on' if n=='Casino' else ''}'><span class='ric'>{ic}</span>"
                 f"<span class='rm'><b>{n}</b><span class='rs'>{c} online</span></span></a>" for n, c, ic in rooms)
    msgs = [("Priya · CasinoPilot", "Anyone got a contact for a DE sportsbook affiliate?", 0),
            ("You", "I can swap OddsGuru for it 👍", 1),
            ("Marco · BetScout", "Swapped with Priya last week — solid.", 0)]
    mm = "".join(f"<div class='msg{' me' if me else ''}'><div class='who'>{esc(w)}</div><div class='bub'>{esc(t)}</div></div>"
                 for w, t, me in msgs)
    return f"""{d_top('chat')}
      <div class='dbody'><div class='dchat'>
        <div class='droom-list'><div class='seclabel'>Rooms</div><div class='group'>{rr}</div></div>
        <div class='dchat-main'><div class='dchat-head'>🎰 Casino · <span class='badge-ok'>Verified</span></div>
          <div class='dchat-body'>{mm}</div>
          <div class='dcomposer'><span class='field' style='flex:1'>Message #casino…</span>
            <button class='btn-primary sm' style='width:auto;margin:0;padding:10px 18px'>Send</button></div></div>
      </div></div>"""


def d_swaps() -> str:
    return f"""{d_top('swaps')}
      <div class='dbody'>
        <div class='dhead'><div><div class='dh2'>Your swaps</div><div class='muted3'>Trade the contacts you have for the ones you want.</div></div>
          <span class='dwallet' style='margin-left:auto'>2 swaps left</span></div>
        <div class='dthree'>
          <div><div class='seclabel'>Want</div><div class='group'>
            <a class='row'><span class='rm'><b>SlotWise</b><span class='rs'>🇸🇪 · casino</span></span><span class='chip-w'>Wanted</span></a>
            <a class='row'><span class='rm'><b>BetScout</b><span class='rs'>🇧🇷 · sportsbook</span></span><span class='chip-w'>Wanted</span></a></div></div>
          <div><div class='seclabel'>Have</div><div class='group'>
            <a class='row'><span class='rm'><b>CasinoPilot</b><span class='rs'>🇬🇧 · casino</span></span><span class='chip-h'>Have</span></a>
            <a class='row'><span class='rm'><b>OddsGuru</b><span class='rs'>🇩🇪 · sportsbook</span></span><span class='chip-h'>Have</span></a></div></div>
          <div><div class='seclabel'>Match ready</div>
            <div class='match'><div class='mrow'><span class='muted2'>You get</span><b>CasinoPilot</b></div>
              <div class='swapic'>⇅</div><div class='mrow'><span class='muted2'>They get</span><b>OddsGuru</b></div>
              <button class='btn-primary sm'>Agree &amp; swap ⇄</button></div></div>
        </div>
      </div>"""


def d_plans() -> str:
    P = config.SWAP_PLANS
    def card(key, pop=False):
        p = P[key]
        sw = "Unlimited swaps" if p["swaps"] is None else f"{p['swaps']} swaps / month"
        return (f"<div class='plan{' pop' if pop else ''}'>{'<div class=ribbon>POPULAR</div>' if pop else ''}"
                f"<div class='pl-name'>{esc(p['label'])}</div><div class='pl-price'>{esc(p['price'])}<span class='per'>/mo</span></div>"
                f"<div class='pl-sw'>{sw}</div><button class='btn-{'primary' if pop else 'ghost'} sm'>Choose {esc(p['label'])}</button></div>")
    return f"""{d_top('swaps')}
      <div class='dbody' style='text-align:center'>
        <div class='dh2'>Simple plans. Swap, don't buy.</div>
        <div class='muted3' style='margin-bottom:18px'>Every plan includes verified chat, alerts and the full catalogue.</div>
        <div class='dthree' style='max-width:880px;margin:0 auto'>{card('standard')}{card('pro', True)}{card('unlimited')}</div>
        <div class='topup' style='max-width:440px;margin:18px auto 0'>Need more? Top-up swaps at <b>{esc(config.SWAP_TOPUP_PRICE)}</b> each.</div>
      </div>"""


def d_alerts() -> str:
    fired = "".join(
        f"<a class='row'><span class='rm'><b>{t}</b><span class='rs'>{dt}</span></span><span class='trend {c}'>{pc}</span></a>"
        for t, dt, c, pc in [("New affiliate · 🇬🇧 casino", "ReelMentor · 2h ago", "flat", "NEW"),
                             ("Traffic ▲ · 🇩🇪 sportsbook", "Wetten · 5h ago", "up", "▲ 22%"),
                             ("Traffic ▼ · 🇺🇸 casino", "Chipy · 1d ago", "down", "▼ 11%")])
    return f"""{d_top('alerts')}
      <div class='dbody'>
        <div class='dh2'>Alerts</div><div class='muted3' style='margin-bottom:16px'>Get pinged when your markets move.</div>
        <div class='dtwo'>
          <div class='dcard'><div class='seclabel'>New rule</div>
            <label>Market</label><div class='field sel'>🇬🇧 United Kingdom ⌄</div>
            <label>Verticals</label><div class='crow'><span class='vchip on'>casino</span><span class='vchip on'>sportsbook</span><span class='vchip'>bingo</span><span class='vchip'>poker</span></div>
            <label>Trigger on</label><div class='crow'><span class='pill on'>New affiliate</span><span class='pill on'>Traffic ▲</span><span class='pill'>Traffic ▼</span></div>
            <label>Deliver to</label><div class='crow'><span class='pill on'>Push</span><span class='pill'>Email</span><span class='pill on'>Telegram</span></div>
            <button class='btn-primary'>Save alert</button></div>
          <div><div class='seclabel'>Recent alerts</div><div class='group'>{fired}</div></div>
        </div>
      </div>"""


def d_loyalty() -> str:
    dots = "".join(f"<span class='d{' on' if i < 3 else ''}'></span>" for i in range(5))
    return f"""{d_top('loyalty')}
      <div class='dbody'>
        <div class='dhead'><span class='dflag'>🎁</span><div><div class='dh2'>Loyalty</div>
          <div class='muted3'>standard plan · 3 swaps left · two ways to earn free swaps</div></div></div>
        <div class='dtwo'>
          <div class='loyal'><div class='lt'><span class='lic'>⭐</span><div><div class='lh'>Write reviews</div>
            <div class='lsub'>5 approved reviews → 1 free swap</div></div><span class='earn'>+1 earned</span></div>
            <div class='dots'>{dots}</div>
            <div class='lbig'>2 more approved reviews → <b>1 free swap</b></div>
            <div class='lrule'>Honest reviews of sites you know. Low-effort or fake ones are rejected and don't count. Loyalty is off on the Unlimited plan.</div></div>
          <div class='loyal'><div class='lt'><span class='lic'>➕</span><div><div class='lh'>Suggest a site</div>
            <div class='lsub'>Approved affiliate → 1 free swap</div></div><span class='earn'>+0 earned</span></div>
            <div class='lbig'>Submit an affiliate we don't list yet. When it's <b>approved</b>, you get <b>1 free swap</b>.</div>
            <button class='btn-primary'>＋ Suggest a site</button></div>
        </div>
      </div>"""


CSS = """
:root{--bg:#F1F5FB;--card:#fff;--card2:#F7FAFE;--line:#E4EBF4;
 --ink:#14213B;--ink2:#5B6B84;--ink3:#93A0B5;--blue:#4E97E6;--blue-d:#2E77CC;--blue-dd:#205CA6;--blue-050:#ECF3FD;
 --up:#12A150;--down:#E5484D;--gold:#F5A524;
 --sans:'Manrope',-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--mono:ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--ink);
 background:radial-gradient(900px 500px at 100% -6%,#DCEAFB,transparent 60%),
  radial-gradient(700px 480px at 0 0,#E7F0FB,transparent 55%),var(--bg)}
.wrap{max-width:1500px;margin:0 auto;padding:30px 22px 70px}
.eyebrow{font:800 11.5px var(--sans);letter-spacing:.16em;color:var(--blue-dd);text-transform:uppercase}
h1{margin:.3em 0 .1em;font-size:30px;font-weight:800;letter-spacing:-.6px}
h1 span{color:var(--blue-d)}
.lead{color:var(--ink2);max-width:900px;line-height:1.5;margin:0 0 8px}
.flow{color:var(--ink3);font:700 12.5px var(--sans);margin:2px 0 26px}
.flow b{color:var(--blue-d)}
.board{display:flex;flex-wrap:wrap;gap:30px;justify-content:center}
figure{margin:0;width:300px}
figcaption{margin-top:12px;text-align:center}
figcaption .n{color:var(--blue-d);font:800 12px var(--sans)}
figcaption .t{color:var(--ink);font-weight:800;font-size:13.5px;margin-left:4px}
figcaption .d{display:block;color:var(--ink3);font-size:11.5px;margin-top:2px}
.phone{border-radius:40px;padding:11px;background:#fff;box-shadow:0 30px 60px -24px rgba(16,28,52,.3),0 0 0 1px var(--line)}
.screen{position:relative;height:600px;overflow-y:auto;border-radius:31px;padding:16px 15px 26px;
 background:linear-gradient(180deg,#F7FAFE,#eef4fb);scrollbar-width:none}
.screen::-webkit-scrollbar{display:none}
.logo{text-align:center;font-weight:800;font-size:22px;letter-spacing:-.4px;color:var(--ink)}
.logo span{color:var(--blue-d)}.logo.sm{font-size:17px;margin:0}
.brand{display:inline-flex;align-items:center;gap:8px}
.brand-c{display:flex;flex-direction:column;align-items:center;gap:7px;margin-top:4px}
.logo-mark{width:22px;height:22px;flex:0 0 auto;display:block}
.dtop .logo-mark{width:26px;height:26px}.brand-c .logo-mark{width:32px;height:32px}
.dlogo{font-weight:800;font-size:19px;letter-spacing:-.4px;color:var(--ink)}.dlogo span{color:var(--blue-d)}
.tag{text-align:center;color:var(--ink3);font:700 8.5px var(--sans);letter-spacing:.16em;text-transform:uppercase;margin:2px 0 12px}
.avatar{width:56px;height:56px;margin:6px auto 10px;border-radius:16px;background:var(--blue-050);
 border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:24px;color:var(--blue-d)}
.h2{text-align:center;font-weight:800;font-size:18px}
.muted{color:var(--ink2);font-size:12px;line-height:1.5;text-align:center;margin:6px 2px 12px}
.muted2{color:var(--ink2);font-size:11.5px}.muted3{color:var(--ink3);font-size:11.5px;margin:2px 2px 8px}
.micro{color:var(--ink3);font-size:10.5px;text-align:center;margin:5px 0}.micro.c{margin-top:10px}
label{display:block;color:var(--ink2);font-size:11px;font-weight:700;margin:10px 2px 5px}
.field{background:#fff;border:1px solid var(--line);border-radius:11px;padding:11px 12px;color:var(--ink3);font-size:12.5px}
.field.sel{color:var(--ink);display:flex;justify-content:space-between}
.row2{display:flex;gap:9px}.row2>div{flex:1}
.btn-li{width:100%;margin:12px 0 2px;background:#1667c2;border:0;color:#fff;font-weight:800;font-size:13px;padding:12px;border-radius:11px;cursor:pointer}
.btn-primary{width:100%;margin-top:14px;background:linear-gradient(120deg,#5AA0EA,#2E77CC);border:0;color:#fff;
 font-weight:800;font-size:13.5px;padding:12px;border-radius:12px;cursor:pointer;box-shadow:0 14px 26px -12px rgba(46,119,204,.7)}
.btn-primary.sm{margin-top:10px;padding:10px;font-size:12.5px}
.btn-ghost{width:100%;background:#fff;border:1px solid var(--blue);color:var(--blue-dd);font-weight:800;font-size:12.5px;padding:10px;border-radius:12px;cursor:pointer}
.btn-ghost.sm{padding:9px}
.appbar{display:flex;align-items:center;gap:8px;font-size:16px;font-weight:800;padding:2px 2px 12px}
.appbar.back span{color:var(--blue-d);font-size:20px}.appbar .badge-ok{margin-left:auto}
.badge-ok{background:#E6F6EC;color:var(--up);font:800 9px var(--sans);border-radius:6px;padding:3px 7px}
.wallet{margin-left:auto;background:var(--blue-050);color:var(--blue-dd);font:800 10px var(--sans);border:1px solid var(--line);border-radius:999px;padding:3px 9px}
.cta{display:flex;align-items:center;gap:11px;background:linear-gradient(120deg,#4E97E6,#2E77CC);border:0;border-radius:14px;padding:13px 14px;margin:4px 0 6px;text-decoration:none;color:#fff;box-shadow:0 14px 26px -12px rgba(46,119,204,.6)}
.cta-ic{font-size:20px}.cta-t{display:flex;flex-direction:column}.cta .chev{color:#fff;margin-left:auto}
.seclabel{color:var(--ink3);font:800 10px var(--sans);letter-spacing:.12em;text-transform:uppercase;margin:16px 4px 8px}
.seclabel.up{color:var(--up)}.seclabel.down{color:var(--down)}
.group{background:#fff;border:1px solid var(--line);border-radius:15px;overflow:hidden;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.group.pad{padding:5px 4px}
.row{display:flex;align-items:center;gap:10px;padding:11px 12px;text-decoration:none;color:var(--ink);border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:none}
.rf{font-size:19px}.rank{width:18px;text-align:center;color:var(--blue-d);font:800 13px var(--sans)}
.rm{flex:1;display:flex;flex-direction:column;gap:1px;min-width:0}.rm b{font-size:13.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rs{color:var(--ink2);font-size:11px}.chev{color:var(--ink3);font-size:17px}
.trend{font:800 10.5px var(--sans);white-space:nowrap;padding:2px 7px;border-radius:999px}
.trend.up{color:var(--up);background:#E6F6EC}.trend.down{color:var(--down);background:#FCEBEC}
.trend.flat{color:var(--ink3);background:#EEF2F0}
.vtabs{display:flex;gap:6px;overflow-x:auto;margin:10px 0 9px;padding-bottom:2px}
.vtab{white-space:nowrap;font:700 11px var(--sans);color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:5px 11px;background:#fff}
.vtab.on{color:#fff;background:var(--blue);border-color:var(--blue)}
.seg,.whseg{display:flex;gap:5px;background:#fff;border:1px solid var(--line);border-radius:11px;padding:4px;margin:0 0 11px}
.sg{flex:1;text-align:center;font:700 11.5px var(--sans);color:var(--ink2);padding:6px;border-radius:8px}
.sg.on{background:var(--blue);color:#fff}
.card{display:block;text-decoration:none;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:14px;padding:11px 12px;margin-bottom:9px;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.ct{display:flex;align-items:center;gap:8px}.ct b{font-size:14px;font-weight:700;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cm{display:flex;align-items:baseline;gap:10px;margin:6px 0 7px}
.big{font:800 20px var(--sans);color:var(--ink)}.big2{font:800 28px var(--sans);color:var(--ink)}
.per{font:700 9.5px var(--sans);color:var(--ink3);margin-left:3px}
.crow{display:flex;flex-wrap:wrap;align-items:center;gap:5px}
.vchip{font:800 9px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--blue-dd);border-radius:999px;padding:3px 8px;background:var(--blue-050)}
.vchip.on{background:#d8e9fc}
.pill{font:700 10.5px var(--sans);color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:5px 11px;background:#fff}
.pill.on{color:#fff;background:var(--blue);border-color:var(--blue)}
.also{color:var(--ink3);font-size:10.5px}.mini{font-size:12px}
.pshot{width:100%;height:140px;object-fit:cover;object-position:top;border-radius:13px;border:1px solid var(--line);margin:2px 0 10px;box-shadow:0 8px 20px -12px rgba(16,28,52,.25)}
.pshot.none{display:flex;align-items:center;justify-content:center;color:var(--ink3);font:700 11px var(--sans);background:var(--blue-050)}
.phero{display:flex;align-items:baseline;gap:11px;margin:2px 2px 9px}
.breg{display:flex;align-items:center;gap:8px;padding:6px 6px}.breg .bn{width:82px;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{flex:1;height:7px;background:#E9F0FA;border-radius:999px;overflow:hidden}
.bar span{display:block;height:100%;background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.be{width:46px;text-align:right;font:800 10.5px var(--sans);color:var(--ink2)}
.swapbar{margin:14px 0 0;text-align:center;color:var(--ink2);font-size:12px;border:1px dashed var(--line);border-radius:12px;padding:11px}
.room{display:flex;align-items:center;gap:11px;padding:11px 12px;text-decoration:none;color:var(--ink);border-bottom:1px solid var(--line)}
.room:last-child{border-bottom:none}.ric{font-size:19px}
.dot-on{width:8px;height:8px;border-radius:50%;background:var(--up)}
.locknote,.topup,.fired{margin-top:14px;text-align:center;color:var(--ink2);font-size:12px;border:1px dashed var(--line);border-radius:12px;padding:11px}
.fired{text-align:left;border-style:solid;background:#fff}
.match{background:#fff;border:1.5px solid var(--blue);border-radius:15px;padding:13px;box-shadow:0 14px 30px -16px rgba(46,119,204,.5)}
.mrow{display:flex;align-items:center;gap:8px;font-size:14px}.mrow b{font-weight:800}.mrow .muted2{width:56px}
.swapic{text-align:center;color:var(--blue-d);font-size:20px;margin:3px 0}
.chip-w{background:var(--blue-050);color:var(--blue-dd);font:800 9px var(--sans);border-radius:6px;padding:3px 7px}
.plan{position:relative;background:#fff;border:1px solid var(--line);border-radius:15px;padding:13px 14px;margin-bottom:11px;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.plan.pop{border-color:var(--blue);box-shadow:0 16px 30px -16px rgba(46,119,204,.5)}
.ribbon{position:absolute;top:-9px;right:12px;background:var(--blue);color:#fff;font:800 8.5px var(--sans);border-radius:5px;padding:3px 7px}
.pl-name{font-weight:800;font-size:14px}.pl-price{font:800 22px var(--sans);color:var(--ink);margin:3px 0}
.pl-sw{color:var(--ink2);font-size:11.5px;margin-bottom:9px}
.chartcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:11px 12px 9px;margin:12px 0;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.cc-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}.cc-top .seclabel{margin:0}
.rangeseg-btns .rgbtn{display:inline-block;cursor:pointer;font:800 9.5px var(--sans);color:var(--ink2);border:1px solid var(--line);border-radius:7px;padding:3px 8px;margin-left:4px}
.rangeseg-btns .rgbtn.on{color:#fff;background:var(--blue);border-color:var(--blue)}
.rangeseg{margin-top:8px}.chart{display:none}.chart.on{display:block}
.spark{width:100%;height:82px;display:block}
.reviews{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 13px;margin:12px 0;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.rv-head{display:flex;align-items:center;gap:11px;margin-bottom:6px}
.big-rating{font:800 28px var(--sans);color:var(--ink)}
.rr{display:flex;flex-direction:column}.rstars{color:var(--gold);font-size:13px;letter-spacing:1px}
.rev{border-top:1px solid var(--line);padding:7px 0 1px}
.rv-top{display:flex;justify-content:space-between;gap:8px;font:700 11px var(--sans);color:var(--ink2)}
.rv-stars{color:var(--gold);font-size:10.5px;white-space:nowrap}.rv-t{font-size:11.5px;color:var(--ink);margin-top:2px}
.addrev{margin-left:auto;cursor:pointer;border:1.5px solid var(--blue);color:var(--blue-dd);background:var(--blue-050);font:800 10.5px var(--sans);border-radius:10px;padding:7px 10px;white-space:nowrap}
.home-hero{font:800 27px/1.05 var(--sans);letter-spacing:-.8px;margin:8px 2px 3px}.home-hero span{color:var(--blue-d)}
.home-sub{color:var(--ink2);font-size:12.5px;margin:0 2px 12px}
.dhometop{margin-bottom:16px}.dh1sm{font:800 26px var(--sans);letter-spacing:-.6px}.dh1sm span{color:var(--blue-d)}
.loyal{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:12px;box-shadow:0 8px 22px -16px rgba(16,28,52,.28)}
.lt{display:flex;align-items:center;gap:11px;margin-bottom:10px}
.lic{width:40px;height:40px;flex:0 0 auto;border-radius:12px;background:var(--blue-050);display:flex;align-items:center;justify-content:center;font-size:20px}
.lh{font:800 15px var(--sans)}.lsub{color:var(--ink3);font-size:11.5px}
.earn{margin-left:auto;background:#E6F6EC;color:var(--up);font:800 10px var(--sans);border-radius:999px;padding:4px 9px;white-space:nowrap}
.dots{display:flex;gap:6px;margin-bottom:10px}.dots .d{flex:1;height:8px;border-radius:999px;background:#E4ECF6}.dots .d.on{background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.lbig{font:800 14px var(--sans);line-height:1.35;margin-bottom:5px}.lbig b{color:var(--blue-d)}
.lrule{color:var(--ink2);font-size:11.5px;line-height:1.5}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0 6px}
.dstats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:2px 0 20px}
.stat-c{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:0 6px 18px -14px rgba(16,28,52,.25)}
.stat-ic{font-size:19px;line-height:1;margin-bottom:6px;min-height:21px}
.stat-n{font:800 26px var(--sans);letter-spacing:-.6px;color:var(--blue-dd);line-height:1}
.stat-l{color:var(--ink2);font:700 11px var(--sans);margin-top:4px}
.pulse{display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--up);box-shadow:0 0 0 0 rgba(18,161,80,.5);animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(18,161,80,.45)}70%{box-shadow:0 0 0 10px rgba(18,161,80,0)}100%{box-shadow:0 0 0 0 rgba(18,161,80,0)}}
.mk-sum{color:var(--ink3);font:700 11px var(--mono)}
.netnote{margin-top:14px;color:var(--ink2);font-size:12.5px;line-height:1.6;background:var(--blue-050);border:1px solid #DCEAFB;border-radius:14px;padding:14px}
.site-row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 2px 8px}
.pdom{color:var(--ink3);font:600 12px var(--mono)}
.viewsite{text-decoration:none;font:800 11px var(--sans);color:var(--blue-dd);background:var(--blue-050);border:1px solid #CFE1F8;border-radius:9px;padding:6px 11px;white-space:nowrap}
.dsiterow{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:3px}
.seclabel-row{display:flex;align-items:center;justify-content:space-between;margin:16px 4px 8px}
.seclabel-row .seclabel{margin:0}
.seeall{cursor:pointer;color:var(--blue-d);font:800 11px var(--sans);white-space:nowrap}
.moremk{display:none}
.wh-help{color:var(--ink2);font-size:11px;line-height:1.5;margin:8px 2px 0;text-align:center}
.wh{display:flex;gap:9px;margin:12px 0 0}
.wh-btn{flex:1;cursor:pointer;border:1.5px solid var(--line);background:#fff;color:var(--ink);font-weight:800;font-size:12.5px;padding:11px;border-radius:12px}
.wh-btn.want{border-color:var(--blue);color:var(--blue-dd);background:var(--blue-050)}.wh-btn.have{border-color:#8FE3C4;color:var(--up);background:#EAF7F0}
.switch{position:sticky;top:0;z-index:9;padding:12px 0 18px;background:linear-gradient(180deg,#F1F5FBf5,#F1F5FBcc);backdrop-filter:blur(8px)}
.seg2{display:inline-flex;background:#fff;border:1px solid var(--line);border-radius:12px;padding:4px;gap:4px;box-shadow:0 4px 14px -8px rgba(16,28,52,.18)}
.switch .seg{display:inline-flex;background:#fff;border:1px solid var(--line);border-radius:12px;padding:4px;gap:4px;box-shadow:0 4px 14px -8px rgba(16,28,52,.18);margin:0}
.switch .seg button{cursor:pointer;border:0;background:transparent;color:var(--ink2);font:800 13.5px var(--sans);padding:9px 17px;border-radius:9px}
.switch .seg button.on{background:var(--blue);color:#fff}
.board-d{display:flex;flex-direction:column;gap:38px;align-items:center}
.dfig{width:100%;max-width:1160px}
.dwin{border-radius:16px;overflow:hidden;border:1px solid var(--line);background:#fff;box-shadow:0 34px 70px -34px rgba(16,28,52,.3)}
.dchrome{height:40px;display:flex;align-items:center;gap:8px;padding:0 15px;background:#F4F8FC;border-bottom:1px solid var(--line)}
.ddot{width:11px;height:11px;border-radius:50%}
.durl{flex:1;margin-left:10px;height:24px;border-radius:8px;background:#fff;border:1px solid var(--line);color:var(--ink3);font:700 12px var(--sans);display:flex;align-items:center;padding:0 12px}
.dtop{display:flex;align-items:center;gap:24px;padding:15px 24px;border-bottom:1px solid var(--line)}
.dnav{display:flex;gap:22px}.dnav a{color:var(--ink2);font-weight:700;font-size:14px;text-decoration:none;cursor:pointer}.dnav a.on{color:var(--blue-d)}
.dwallet{background:var(--blue-050);color:var(--blue-dd);font:800 12px var(--sans);border-radius:999px;padding:6px 13px}
.dtop .dwallet{margin-left:auto}
.davatar{width:34px;height:34px;border-radius:50%;background:var(--blue-050);display:flex;align-items:center;justify-content:center;font:800 12px var(--sans);color:var(--blue-dd)}
.dbody{padding:24px 26px 30px;min-height:380px}
.dcols{display:grid;grid-template-columns:1.9fr 1fr;gap:22px}
.dtwo{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.dthree{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.dgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:15px}
.panel{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}
.dtable{width:100%;border-collapse:collapse;font-size:13.5px}
.dtable th{text-align:left;color:var(--ink3);font:800 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:12px 14px;border-bottom:1px solid var(--line)}
.dtable td{padding:12px 14px;border-bottom:1px solid var(--line)}.dtable tr:last-child td{border-bottom:none}
.dtable .rk{color:var(--blue-d);font:800 13px var(--sans)}.dtable .num{font:800 13px var(--sans)}
.dhead{display:flex;align-items:center;gap:14px;margin-bottom:16px}
.dflag{font-size:34px}.dh1{font:800 34px var(--sans);line-height:1.1;margin:10px 0 12px}.dh2{font:800 22px var(--sans)}
.dfilter{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px}
.seg.small{margin:0;display:inline-flex}.seg.small .sg{padding:6px 14px;border-radius:8px;font:700 11.5px var(--sans);color:var(--ink2)}.seg.small .sg.on{background:var(--blue);color:#fff}
.dprof{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.dshot{width:100%;height:230px;object-fit:cover;object-position:top;border-radius:14px;border:1px solid var(--line);box-shadow:0 10px 24px -14px rgba(16,28,52,.3)}
.dshot.none{display:flex;align-items:center;justify-content:center;color:var(--ink3);background:var(--blue-050);font:700 12px var(--sans);height:230px;border-radius:14px}
.dsignup{display:grid;grid-template-columns:1.1fr .9fr;gap:34px;align-items:center}
.dhero .muted{text-align:left}
.dpoints{display:flex;flex-direction:column;gap:8px;margin-top:16px;color:var(--up);font-weight:700;font-size:13.5px}
.dcard{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px;box-shadow:0 10px 26px -18px rgba(16,28,52,.25)}
.dchat{display:grid;grid-template-columns:250px 1fr;gap:18px;height:430px}
.droom-list{overflow:auto}.room.on{background:var(--blue-050)}
.dchat-main{display:flex;flex-direction:column;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}
.dchat-head{padding:13px 16px;border-bottom:1px solid var(--line);font-weight:800}
.dchat-body{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.msg .who{color:var(--ink3);font:700 11px var(--sans);margin-bottom:3px}
.msg .bub{background:#F4F8FC;border:1px solid var(--line);border-radius:12px;padding:9px 12px;font-size:13px;max-width:78%;display:inline-block}
.msg.me{text-align:right}.msg.me .bub{background:var(--blue-050);border-color:#CFE1F8}
.dcomposer{display:flex;gap:10px;padding:12px 14px;border-top:1px solid var(--line);align-items:center}
.chip-h{background:#EAF7F0;color:var(--up);font:800 9px var(--sans);border-radius:6px;padding:3px 7px}
@media(max-width:1024px){.dcols,.dprof,.dsignup,.dchat{grid-template-columns:1fr}.dgrid{grid-template-columns:1fr 1fr}}
@media(max-width:520px){figure{width:88vw;max-width:340px}}
"""

SCREENS = [
    ("1", "Sign-up", "verified managers only", "s_signup"),
    ("2", "Home", "network counters + markets", "s_home"),
    ("3", "Geo / Country", "gated list + vertical tabs", "s_geo"),
    ("4", "Site profile", "screenshot + traffic by market", "s_profile"),
    ("5", "Market movers", "90-day risers & fallers", "s_movers"),
    ("6", "Community chat", "verified rooms", "s_chat"),
    ("7", "Swaps", "want / have / matches", "s_swaps"),
    ("8", "Plans", "swap allowances", "s_plans"),
    ("9", "Loyalty", "reviews → free swaps", "s_loyalty"),
    ("10", "Alerts", "rule builder", "s_alerts"),
]


def phone(n, title, desc, inner) -> str:
    return (f"<figure><div class='phone'><div class='screen'>{inner}</div></div>"
            f"<figcaption><span class='n'>{n}</span><span class='t'>{esc(title)}</span>"
            f"<span class='d'>{esc(desc)}</span></figcaption></figure>")


def dwin(n, title, desc, inner) -> str:
    slug = title.lower().split("/")[0].split()[0]
    return (f"<figure class='dfig'><div class='dwin'>"
            f"<div class='dchrome'><span class='ddot' style='background:#ff5f57'></span>"
            f"<span class='ddot' style='background:#febc2e'></span><span class='ddot' style='background:#28c840'></span>"
            f"<span class='durl'>affswap.app/{esc(slug)}</span></div>{inner}</div>"
            f"<figcaption><span class='n'>{n}</span><span class='t'>{esc(title)}</span>"
            f"<span class='d'>{esc(desc)}</span></figcaption></figure>")


TOGGLE_JS = """
(function(){
  var seg=document.getElementById('seg'),bm=document.getElementById('board-mobile'),bd=document.getElementById('board-desktop');
  seg.addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;
    var v=b.dataset.v;[].forEach.call(seg.children,function(x){x.classList.toggle('on',x===b);});
    bm.style.display=(v==='mobile')?'flex':'none';bd.style.display=(v==='desktop')?'flex':'none';window.scrollTo(0,0);});
  document.querySelectorAll('.chartcard').forEach(function(cc){
    var btns=cc.querySelectorAll('.rgbtn'),charts=cc.querySelectorAll('.chart');
    btns.forEach(function(btn){btn.addEventListener('click',function(){
      btns.forEach(function(x){x.classList.toggle('on',x===btn);});
      charts.forEach(function(c){c.classList.toggle('on',c.dataset.c===btn.dataset.r);});});});
  });
  document.querySelectorAll('.seeall').forEach(function(b){
    var orig=b.textContent;
    b.addEventListener('click',function(){
      var grp=b.closest('.seclabel-row').nextElementSibling;
      var more=grp?grp.querySelector('.moremk'):null; if(!more)return;
      var hidden=(more.style.display===''||more.style.display==='none');
      more.style.display=hidden?'block':'none'; b.textContent=hidden?'Show less ▴':orig;});
  });
  document.querySelectorAll('.stat-n').forEach(function(el){
    var to=+el.getAttribute('data-to')||0,start=null,dur=950;
    function step(ts){if(!start)start=ts;var p=Math.min(1,(ts-start)/dur);var e=1-Math.pow(1-p,3);
      el.textContent=Math.round(to*e).toLocaleString('en-GB');if(p<1)requestAnimationFrame(step);}
    requestAnimationFrame(step);
  });
})();
"""


def main() -> None:
    conn = connect()
    try:
        # pick a good geo + hero site
        geo = "GB"
        prof = (views.country_list(conn, geo, sort="traffic")["cards"] or [{}])[0].get("domain") or "fotmob.com"
        builders = {
            "s_signup": lambda: s_signup(),
            "s_home": lambda: s_home(conn),
            "s_geo": lambda: s_geo(conn, geo),
            "s_profile": lambda: s_profile(conn, prof),
            "s_movers": lambda: s_movers(conn, geo),
            "s_chat": lambda: s_chat(),
            "s_swaps": lambda: s_swaps(),
            "s_plans": lambda: s_plans(),
            "s_loyalty": lambda: s_loyalty(),
            "s_alerts": lambda: s_alerts(),
        }
        dbuilders = {
            "s_signup": lambda: d_signup(),
            "s_home": lambda: d_home(conn),
            "s_geo": lambda: d_geo(conn, geo),
            "s_profile": lambda: d_profile(conn, prof),
            "s_movers": lambda: d_movers(conn, geo),
            "s_chat": lambda: d_chat(),
            "s_swaps": lambda: d_swaps(),
            "s_plans": lambda: d_plans(),
            "s_loyalty": lambda: d_loyalty(),
            "s_alerts": lambda: d_alerts(),
        }
        phones = "".join(phone(n, t, d, builders[fn]()) for n, t, d, fn in SCREENS)
        desktops = "".join(dwin(n, t, d, dbuilders[fn]()) for n, t, d, fn in SCREENS)
        nsites = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    finally:
        conn.close()

    doc = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>
<link rel='icon' type='image/svg+xml' href='static/favicon.svg'>
<title>Affswap — app storyboard (all screens)</title><style>{CSS}</style></head><body>
<div class='wrap'>
  <div class='eyebrow'>Affswap · app storyboard · all screens</div>
  <h1>Every screen, <span>one board</span></h1>
  <p class='lead'>The whole member app in the pale-blue design, for reviewing the look end to end. Data screens
    (Home / Geo / Site profile / Movers) render from the live {nsites}-site catalogue; the rest are
    representative UI you can redline. The Site profile carries the full design — traffic graph
    (3M/6M/1Y), manager reviews, and Want/Have.</p>
  <div class='flow'>Flow: <b>Sign-up → Home → Geo → Site profile → Request swap</b> · plus Chat · Plans · Alerts · Movers</div>
  <div class='switch'><span class='seg' id='seg'><button data-v='mobile' class='on'>📱 Mobile</button><button data-v='desktop'>🖥 Desktop</button></span>
    <span style='color:var(--ink3);font:600 12px var(--mono);margin-left:14px'>same pages · two layouts</span></div>
  <div class='board' id='board-mobile'>{phones}</div>
  <div class='board-d' id='board-desktop' style='display:none'>{desktops}</div>
</div>
<script>{TOGGLE_JS}</script>
</body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT.name} ({len(doc):,} bytes) · {len(SCREENS)} screens ×2 layouts · geo={geo} profile={prof}")


if __name__ == "__main__":
    main()

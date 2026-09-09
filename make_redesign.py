#!/usr/bin/env python3
"""Generate redesign-test.html — a DESIGN DIRECTION test (light + mint).

Not the whole app — just Home and the Site profile, rendered in a new light,
premium "mint" look (mint-green palette + Manrope, inspired by mint.io) so we can
agree the direction before rolling it across every screen. Mobile + desktop.

    python3 make_redesign.py            # -> redesign-test.html
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

# chart accent (set per theme in main)
_A = {"fill": "#00C5A3", "stroke": "#00B593"}

# theme = swap the mint hexes in the CSS for another accent family. Neutrals/up
# stay green-tinted for 'mint'; the map re-tints them for 'blue'.
BLUE_MAP = {
    "#00C5A3": "#4E97E6", "#00A184": "#2E77CC", "#00775F": "#205CA6",
    "#33D6B8": "#7EB2F0", "#E7FAF3": "#ECF3FD", "#E9F6F0": "#EAF1FB",
    "#E3EEE8": "#E4EBF4", "#F2F7F4": "#F1F5FB", "#DDF3EA": "#DCEAFB",
    "#E8F1FB": "#E7F0FB", "#F7FBF9": "#F7FAFE", "#eef6f2": "#eef3fb",
    "#EAF7F0": "#EAF1FB", "#8FE3C4": "#A9CBF3", "#EAF3EE": "#E9F0FA",
    "rgba(0,197,163": "rgba(78,151,230", "rgba(0,161,132": "rgba(46,119,204",
    "rgba(16,40,30": "rgba(16,28,52",
}
BLUE_CHART = {"fill": "#4E97E6", "stroke": "#3F88E2"}


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


def tpill(chip, d) -> str:
    cls = {"up": "up", "down": "down"}.get(d or "", "flat")
    return f"<span class='tp {cls}'>{esc(chip)}</span>"


def chips(vs) -> str:
    return "".join(f"<span class='chip'>{esc(v)}</span>" for v in (vs or []))


def mark(size=24) -> str:
    """The Converge logo mark — two chevrons meeting (two-tone blue)."""
    return (f"<svg width='{size}' height='{size}' viewBox='0 0 44 44' fill='none' class='logo-mark' "
            "stroke-linecap='round' stroke-linejoin='round' stroke-width='4.8'>"
            "<path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/>"
            "<path d='M33 9 L26 22 L33 35' stroke='#7FB4F2'/></svg>")


def shot(conn, dom):
    r = conn.execute("""SELECT sc.image_ref FROM screenshots sc JOIN sites s ON s.id=sc.site_id
                        WHERE s.domain=? AND sc.provider<>'mock'""", (dom,)).fetchone()
    if r and r["image_ref"]:
        try:
            b = Path(r["image_ref"]).read_bytes()
            if len(b) > 500:
                m = "image/jpeg" if b[:3] == bytes.fromhex("ffd8ff") else "image/png"
                return f"data:{m};base64," + base64.b64encode(b).decode()
        except OSError:
            pass
    return None


def spark(etv, months=12):
    etv = max(1, int(etv or 1000))
    out = []
    for i in range(months):
        frac = i / (months - 1) if months > 1 else 1
        wob = (((i * 2654435761) & 0xff) / 255 - 0.5) * 0.07
        out.append(max(1, int(etv * (0.55 + 0.45 * frac) * (1 + wob))))
    out[-1] = etv
    return out


def area(vals, w=300, h=96):
    if len(vals) < 2:
        vals = (vals or [1]) * 2
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1
    n = len(vals)
    pts = [(round(i/(n-1)*w, 1), round(h-8-(v-mn)/rng*(h-20), 1)) for i, v in enumerate(vals)]
    line = " ".join(f"{x},{y}" for x, y in pts)
    lx, ly = pts[-1]
    return (f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='none' class='spark'>"
            "<defs><linearGradient id='mg' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0' stop-color='{_A['fill']}' stop-opacity='.20'/>"
            f"<stop offset='1' stop-color='{_A['fill']}' stop-opacity='0'/></linearGradient></defs>"
            f"<polygon points='0,{h} {line} {w},{h}' fill='url(#mg)'/>"
            f"<polyline points='{line}' fill='none' stroke='{_A['stroke']}' stroke-width='2.5'/>"
            f"<circle cx='{lx}' cy='{ly}' r='3.5' fill='{_A['stroke']}'/></svg>")


def chart(etv) -> str:
    s = spark(etv, 12)
    def ch(m, on):
        return f"<div class='chart{' on' if on else ''}' data-c='{m}'>{area(s[-m:] if m < 12 else s)}</div>"
    return ("<div class='card soft'><div class='cc-top'><span class='lbl'>Traffic history</span>"
            "<span class='rng'><span class='rb on' data-r='3'>3M</span><span class='rb' data-r='6'>6M</span>"
            "<span class='rb' data-r='12'>1Y</span></span></div>"
            f"<div class='charts'>{ch(3, True)}{ch(6, False)}{ch(12, False)}</div></div>")


def reviews(dom) -> str:
    hsh = sum(ord(c) for c in dom)
    rating = round(4.2 + (hsh % 8) / 10, 1)
    cnt = 9 + hsh % 38
    rl = "".join(
        f"<div class='rev'><div class='rv-h'><span class='who'>{esc(w)}</span><span class='st'>★★★★★</span></div>"
        f"<div class='rv-t'>{esc(t)}</div></div>"
        for w, t in [("Marco · BetScout", "Converts well on casino — fast, honest replies."),
                     ("Priya · CasinoPilot", "Solid partner, swapped twice. Recommended.")])
    return (f"<div class='card'><div class='rv-top'><span class='rate'>{rating}</span>"
            f"<span class='rate-r'><span class='stars'>★★★★★</span><span class='sub'>{cnt} manager reviews</span></span></div>{rl}</div>")


def wanthave() -> str:
    return ("<div class='wh'><button class='wh-b want'>＋ I want this</button>"
            "<button class='wh-b have'>✓ I have this</button></div>")


# ------- mobile screens -------
def m_home(conn):
    idx = views.countries_index(conn)
    home = views.world_home(conn, limit=5)
    mk = "".join(
        f"<a class='lrow'><span class='fl'>{flag(m['iso'])}</span><span class='lm'><b>{esc(m['name'])}</b>"
        f"<span class='ls'>{m['count']} sites</span></span><span class='lv'>{kfmt(m['total_etv'])}<span class='u'>/mo</span></span></a>"
        for m in idx["countries"][:5])
    af = "".join(
        f"<a class='lrow'><span class='rk'>{a['rank']}</span><span class='lm'><b>{esc(a['name'])}</b>"
        f"<span class='ls'>{flag(a['origin']['iso'])} {esc(a['origin']['name'])}</span></span>"
        f"<span class='lv'>{kfmt(a['etv'])}<span class='u'>/mo</span></span></a>"
        for a in home["affiliates"])
    return f"""
      <div class='mbar'><span class='brand'>{mark(23)}<span class='logo'>Aff<span>swap</span></span></span><span class='av'>SK</span></div>
      <div class='hero'><div class='hero-t'><span>Trade up</span><br>your network.</div>
        <div class='hero-s'>Swap the affiliate contacts you have for the ones you want. Verified managers only.</div></div>
      <a class='promo'><div><div class='promo-t'>Community Chat</div><div class='promo-s'>128 managers online now</div></div><span class='promo-g'>→</span></a>
      <div class='lbl2'>Top markets</div><div class='card list'>{mk}</div>
      <div class='lbl2'>Top affiliates</div><div class='card list'>{af}</div>
    """


def m_profile(conn, dom):
    p = views.site_profile(conn, dom, published_only=False)
    u = shot(conn, dom)
    regs = p["regions"][:5]
    mx = max([r["etv"] or 0 for r in regs], default=1) or 1
    rows = "".join(
        f"<div class='mk'><span class='fl'>{r['flag']}</span><span class='mn'>{esc(r['name'])}{' ★' if r['primary'] else ''}</span>"
        f"<span class='bar'><span style='width:{max(6, round((r['etv'] or 0)/mx*100))}%'></span></span>"
        f"<span class='mv'>{kfmt(r['etv'])}</span></div>" for r in regs)
    hero = f"<img class='pshot' src='{u}'>" if u else "<div class='pshot none'>homepage</div>"
    return f"""
      <div class='mbar back'><span class='bk'>‹</span><b>{esc(p['name'])}</b></div>
      {hero}
      <div class='pname'>{esc(p['name'])}<span class='pdom'>{esc(dom)}</span></div>
      <div class='crow'>{chips(p['verticals'])}</div>
      <div class='stat'><div class='stat-b'>{kfmt(p['etv'])}<span class='u'>/mo</span></div>{tpill(p['trend'], p.get('trend_dir'))}</div>
      {reviews(dom)}
      {chart(p['etv'])}
      <div class='lbl2'>Traffic by market</div><div class='card pad'>{rows}</div>
      {wanthave()}
      <button class='cta'>Request a swap →</button>
      <div class='lock'>🔒 Contact unlocks when you swap</div>
    """


# ------- desktop screens -------
def d_top(active):
    nav = "".join(f"<a class='{'on' if k==active else ''}'>{l}</a>"
                  for l, k in [("Home", "home"), ("Markets", "markets"), ("Swaps", "swaps"), ("Alerts", "alerts"), ("Chat", "chat")])
    return (f"<div class='dtop'><span class='brand'>{mark(26)}<span class='logo'>Aff<span>swap</span></span></span><nav class='dnav'>{nav}</nav>"
            f"<span class='pill-w'>⇄ 2 swaps</span><span class='av'>SK</span></div>")


def d_home(conn):
    idx = views.countries_index(conn)
    home = views.world_home(conn, limit=8)
    tr = "".join(
        f"<tr><td class='rk'>{a['rank']}</td><td><b>{esc(a['name'])}</b></td><td>{flag(a['origin']['iso'])} {esc(a['origin']['name'])}</td>"
        f"<td class='num'>{kfmt(a['etv'])}/mo</td><td>{tpill(a['trend'], a['trend_dir'])}</td></tr>"
        for a in home["affiliates"])
    mk = "".join(
        f"<a class='lrow'><span class='fl'>{flag(m['iso'])}</span><span class='lm'><b>{esc(m['name'])}</b>"
        f"<span class='ls'>{m['count']} sites</span></span><span class='lv'>{kfmt(m['total_etv'])}<span class='u'>/mo</span></span></a>"
        for m in idx["countries"][:6])
    return f"""{d_top('home')}
      <div class='db'>
        <div class='dhero'><div class='dhero-t'><span>Trade up</span> your network.</div>
          <div class='dhero-s'>Swap the affiliate contacts you have for the ones you want. The verified network for iGaming affiliate managers.</div></div>
        <div class='dcols'>
          <div><div class='lbl2'>Top affiliates worldwide</div><div class='card'><table class='tbl'>
            <thead><tr><th>#</th><th>Affiliate</th><th>Top market</th><th>Traffic</th><th>Trend</th></tr></thead><tbody>{tr}</tbody></table></div></div>
          <div><a class='promo'><div><div class='promo-t'>Community Chat</div><div class='promo-s'>128 online</div></div><span class='promo-g'>→</span></a>
            <div class='lbl2'>Top markets</div><div class='card list'>{mk}</div></div>
        </div>
      </div>"""


def d_profile(conn, dom):
    p = views.site_profile(conn, dom, published_only=False)
    u = shot(conn, dom)
    regs = p["regions"][:6]
    mx = max([r["etv"] or 0 for r in regs], default=1) or 1
    rows = "".join(
        f"<div class='mk'><span class='fl'>{r['flag']}</span><span class='mn'>{esc(r['name'])}{' ★' if r['primary'] else ''}</span>"
        f"<span class='bar'><span style='width:{max(6, round((r['etv'] or 0)/mx*100))}%'></span></span>"
        f"<span class='mv'>{kfmt(r['etv'])}</span></div>" for r in regs)
    hero = f"<img class='dshot' src='{u}'>" if u else "<div class='dshot none'>homepage</div>"
    return f"""{d_top('markets')}
      <div class='db'><div class='dprof'>
        <div>{hero}<div class='pname big'>{esc(p['name'])}<span class='pdom'>{esc(dom)}</span></div>
          <div class='crow'>{chips(p['verticals'])}</div>{reviews(dom)}</div>
        <div><div class='stat'><div class='stat-b big'>{kfmt(p['etv'])}<span class='u'>/mo total</span></div>{tpill(p['trend'], p.get('trend_dir'))}</div>
          {chart(p['etv'])}
          <div class='lbl2'>Traffic by market</div><div class='card pad'>{rows}</div>
          {wanthave()}
          <button class='cta'>Request a swap →</button></div>
      </div></div>"""


CSS = """
*{box-sizing:border-box}
:root{--bg:#F2F7F4;--card:#fff;--tint:#E9F6F0;--ink:#12201B;--ink2:#5B6E65;--ink3:#93A49B;
 --line:#E3EEE8;--mint:#00C5A3;--mint-d:#00A184;--mint-dd:#00775F;--mint-050:#E7FAF3;
 --gold:#F5A524;--up:#12A150;--down:#E5484D;
 --sans:'Manrope',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
 --sh:0 12px 30px -14px rgba(16,40,30,.20),0 2px 6px -2px rgba(16,40,30,.06);
 --sh-s:0 4px 14px -8px rgba(16,40,30,.18)}
body{margin:0;font-family:var(--sans);color:var(--ink);
 background:radial-gradient(900px 500px at 100% -5%,#DDF3EA,transparent 60%),
  radial-gradient(700px 500px at 0% 0%,#E8F1FB,transparent 55%),var(--bg)}
.wrap{max-width:1200px;margin:0 auto;padding:32px 22px 80px}
.eyebrow{font:700 11.5px var(--sans);letter-spacing:.16em;color:var(--mint-dd);text-transform:uppercase}
h1{font-size:32px;font-weight:800;letter-spacing:-.6px;margin:.3em 0 .12em}
h1 span{color:var(--mint-d)}
.lead{color:var(--ink2);max-width:800px;line-height:1.55;margin:0 0 6px;font-size:15px}
.note{color:var(--ink3);font-size:13px;margin-bottom:20px}
.switch{position:sticky;top:0;z-index:9;padding:12px 0 20px;background:linear-gradient(180deg,#F2F7F4f5,#F2F7F4cc);backdrop-filter:blur(8px)}
.seg{display:inline-flex;background:#fff;border:1px solid var(--line);border-radius:13px;padding:4px;gap:4px;box-shadow:var(--sh-s)}
.seg button{cursor:pointer;border:0;background:transparent;color:var(--ink2);font:700 13.5px var(--sans);padding:9px 18px;border-radius:9px}
.seg button.on{background:var(--mint);color:#fff;box-shadow:0 6px 14px -6px rgba(0,197,163,.6)}
.board{display:flex;flex-wrap:wrap;gap:34px;justify-content:center}
.board-d{display:flex;flex-direction:column;gap:34px;align-items:center}
figure{margin:0;width:330px}
figcaption{margin-top:13px;text-align:center;color:var(--ink3);font:700 11px var(--sans);letter-spacing:.1em;text-transform:uppercase}
.phone{border-radius:42px;padding:11px;background:#fff;box-shadow:0 30px 60px -22px rgba(16,40,30,.32),0 0 0 1px var(--line)}
.screen{height:610px;overflow-y:auto;border-radius:32px;background:linear-gradient(180deg,#F7FBF9,#eef6f2);padding:15px 14px 26px;scrollbar-width:none}
.screen::-webkit-scrollbar{display:none}
/* header/logo */
.logo{font-weight:800;font-size:19px;letter-spacing:-.3px;color:var(--ink)}.logo span{color:var(--mint-d)}
.brand{display:inline-flex;align-items:center;gap:8px}.logo-mark{flex:0 0 auto;display:block}
.mbar{display:flex;align-items:center;gap:9px;padding:2px 2px 10px}.mbar.back b{font-size:16px}
.mbar .av,.dtop .av{margin-left:auto;width:32px;height:32px;border-radius:50%;background:var(--mint-050);color:var(--mint-dd);
 display:flex;align-items:center;justify-content:center;font:800 11px var(--sans)}
.bk{font-size:22px;color:var(--mint-d)}
/* hero */
.hero{padding:10px 4px 14px}.hero-t{font:800 27px/1.08 var(--sans);letter-spacing:-.8px}.hero-t span{color:var(--mint-d)}
.hero-s{color:var(--ink2);font-size:13.5px;margin-top:8px}
.promo{display:flex;align-items:center;gap:10px;background:linear-gradient(120deg,#00C5A3,#00A184);color:#fff;
 border-radius:16px;padding:14px 16px;text-decoration:none;box-shadow:0 14px 26px -12px rgba(0,161,132,.7);margin:6px 0 4px}
.promo-t{font-weight:800;font-size:15px}.promo-s{font-size:12px;opacity:.9}.promo-g{margin-left:auto;font-size:20px;font-weight:800}
/* labels */
.lbl2{color:var(--ink3);font:800 11px var(--sans);letter-spacing:.09em;text-transform:uppercase;margin:18px 4px 9px}
.lbl{color:var(--ink2);font:800 11px var(--sans);letter-spacing:.06em;text-transform:uppercase}
/* cards + lists */
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--sh-s)}
.card.soft{padding:13px 14px 11px;margin:12px 0}.card.pad{padding:6px 6px}.card.list{overflow:hidden}
.lrow{display:flex;align-items:center;gap:11px;padding:12px 14px;text-decoration:none;color:var(--ink);border-bottom:1px solid var(--line)}
.lrow:last-child{border-bottom:none}
.fl{font-size:20px}.rk{width:20px;text-align:center;color:var(--mint-d);font-weight:800;font-size:14px}
.lm{flex:1;display:flex;flex-direction:column;gap:1px;min-width:0}.lm b{font-size:14.5px;font-weight:700}
.ls{color:var(--ink3);font-size:12px}.lv{font-weight:800;font-size:14px}.u{color:var(--ink3);font-weight:600;font-size:10px;margin-left:1px}
/* profile */
.pshot{width:100%;height:150px;object-fit:cover;object-position:top;border-radius:15px;box-shadow:var(--sh);border:1px solid var(--line)}
.pshot.none{display:flex;align-items:center;justify-content:center;height:150px;border-radius:15px;background:var(--tint);color:var(--ink3);font-weight:700}
.pname{font:800 21px var(--sans);letter-spacing:-.4px;margin:13px 2px 6px;display:flex;flex-direction:column}
.pname.big{font-size:24px}.pdom{color:var(--ink3);font-size:12.5px;font-weight:600;margin-top:1px}
.crow{display:flex;flex-wrap:wrap;gap:6px;margin:2px 2px}
.chip{font:800 10px var(--sans);letter-spacing:.03em;text-transform:uppercase;color:var(--mint-dd);
 background:var(--mint-050);border-radius:999px;padding:4px 10px}
.stat{display:flex;align-items:center;gap:12px;margin:12px 2px 4px}
.stat-b{font:800 30px var(--sans);letter-spacing:-1px}.stat-b.big{font-size:36px}
.tp{font:800 12px var(--sans);border-radius:999px;padding:4px 10px}
.tp.up{color:var(--up);background:#E6F6EC}.tp.down{color:var(--down);background:#FCEBEC}.tp.flat{color:var(--ink3);background:#EEF2F0}
/* reviews */
.rv-top{display:flex;align-items:center;gap:12px;padding:14px 15px 8px}
.rate{font:800 30px var(--sans);letter-spacing:-1px}.rate-r{display:flex;flex-direction:column}
.stars{color:var(--gold);font-size:14px;letter-spacing:2px}.sub{color:var(--ink3);font-size:12px}
.rev{padding:9px 15px;border-top:1px solid var(--line)}.rv-h{display:flex;justify-content:space-between;gap:8px}
.who{font-weight:700;font-size:12px;color:var(--ink2)}.st{color:var(--gold);font-size:11px}.rv-t{font-size:12.5px;color:var(--ink);margin-top:2px}
/* chart */
.cc-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:2px}.cc-top .lbl{margin:0}
.rng .rb{cursor:pointer;font:800 10px var(--sans);color:var(--ink2);border:1px solid var(--line);border-radius:8px;padding:4px 9px;margin-left:4px}
.rng .rb.on{color:#fff;background:var(--mint);border-color:var(--mint)}
.charts{margin-top:10px}.chart{display:none}.chart.on{display:block}.spark{width:100%;height:96px;display:block}
/* traffic by market */
.mk{display:flex;align-items:center;gap:9px;padding:8px 8px}.mn{width:96px;font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{flex:1;height:8px;background:#EAF3EE;border-radius:999px;overflow:hidden}
.bar span{display:block;height:100%;background:linear-gradient(90deg,#00C5A3,#33D6B8);border-radius:999px}
.mv{width:52px;text-align:right;font:800 12px var(--sans)}
/* want/have + cta */
.wh{display:flex;gap:10px;margin:14px 0 0}
.wh-b{flex:1;cursor:pointer;border-radius:13px;padding:13px;font:800 13px var(--sans);border:1.5px solid var(--line);background:#fff;color:var(--ink)}
.wh-b.want{border-color:var(--mint);color:var(--mint-dd);background:var(--mint-050)}
.wh-b.have{border-color:#8FE3C4;color:var(--up);background:#EAF7F0}
.cta{width:100%;margin-top:12px;cursor:pointer;border:0;border-radius:14px;padding:14px;font:800 14.5px var(--sans);
 color:#fff;background:linear-gradient(120deg,#00C5A3,#00A184);box-shadow:0 16px 30px -12px rgba(0,161,132,.75)}
.lock{text-align:center;color:var(--ink3);font-size:12px;margin-top:12px}
/* ---- desktop ---- */
.dfig{width:100%;max-width:1080px}
.dwin{background:#fff;border-radius:16px;overflow:hidden;border:1px solid var(--line);box-shadow:0 34px 70px -30px rgba(16,40,30,.32)}
.dchrome{height:40px;display:flex;align-items:center;gap:8px;padding:0 15px;background:#F4F8F6;border-bottom:1px solid var(--line)}
.dd{width:11px;height:11px;border-radius:50%}
.durl{flex:1;margin-left:10px;height:24px;border-radius:8px;background:#fff;border:1px solid var(--line);color:var(--ink3);font:700 12px var(--sans);display:flex;align-items:center;padding:0 12px}
.dtop{display:flex;align-items:center;gap:26px;padding:16px 26px;border-bottom:1px solid var(--line)}
.dnav{display:flex;gap:24px}.dnav a{color:var(--ink2);font-weight:700;font-size:14px;text-decoration:none;cursor:pointer}.dnav a.on{color:var(--mint-d)}
.pill-w{margin-left:auto;background:var(--mint-050);color:var(--mint-dd);font:800 12px var(--sans);border-radius:999px;padding:7px 14px}
.db{padding:24px 28px 32px}
.dhero{margin-bottom:20px}.dhero-t{font:800 32px var(--sans);letter-spacing:-1px}.dhero-t span{color:var(--mint-d)}
.dhero-s{color:var(--ink2);font-size:15px;max-width:620px;margin-top:8px;line-height:1.5}
.dcols{display:grid;grid-template-columns:1.85fr 1fr;gap:22px}
.tbl{width:100%;border-collapse:collapse;font-size:14px}
.tbl th{text-align:left;color:var(--ink3);font:800 10.5px var(--sans);letter-spacing:.05em;text-transform:uppercase;padding:13px 15px;border-bottom:1px solid var(--line)}
.tbl td{padding:13px 15px;border-bottom:1px solid var(--line)}.tbl tr:last-child td{border-bottom:none}
.tbl .rk{color:var(--mint-d);font-weight:800}.tbl .num{font-weight:800}
.dprof{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.dshot{width:100%;height:250px;object-fit:cover;object-position:top;border-radius:15px;box-shadow:var(--sh);border:1px solid var(--line)}
.dshot.none{display:flex;align-items:center;justify-content:center;height:250px;border-radius:15px;background:var(--tint);color:var(--ink3);font-weight:700}
@media(max-width:900px){.dcols,.dprof{grid-template-columns:1fr}}
"""

TOGGLE = """(function(){
var seg=document.getElementById('seg'),bm=document.getElementById('bm'),bd=document.getElementById('bd');
seg.addEventListener('click',function(e){var b=e.target.closest('button');if(!b)return;var v=b.dataset.v;
 [].forEach.call(seg.children,function(x){x.classList.toggle('on',x===b);});
 bm.style.display=(v==='m')?'flex':'none';bd.style.display=(v==='d')?'flex':'none';window.scrollTo(0,0);});
document.querySelectorAll('.card.soft').forEach(function(cc){var bs=cc.querySelectorAll('.rb'),cs=cc.querySelectorAll('.chart');
 bs.forEach(function(b){b.addEventListener('click',function(){bs.forEach(function(x){x.classList.toggle('on',x===b);});
  cs.forEach(function(c){c.classList.toggle('on',c.dataset.c===b.dataset.r);});});});});
})();"""


def phone(t, inner):
    return f"<figure><div class='phone'><div class='screen'>{inner}</div></div><figcaption>{esc(t)}</figcaption></figure>"


def dwin(t, inner):
    return (f"<figure class='dfig'><div class='dwin'><div class='dchrome'>"
            "<span class='dd' style='background:#ff5f57'></span><span class='dd' style='background:#febc2e'></span>"
            "<span class='dd' style='background:#28c840'></span><span class='durl'>affswap.app</span></div>"
            f"{inner}</div><figcaption>{esc(t)}</figcaption></figure>")


def main():
    theme = (sys.argv[1] if len(sys.argv) > 1 else "blue").lower()
    css = CSS
    if theme == "blue":
        _A.update(BLUE_CHART)
        for k, v in BLUE_MAP.items():
            css = css.replace(k, v)
        head, pal = "Lighter, pale <span>blue</span>", "pale-blue palette"
    else:
        head, pal = "Lighter, <span>mint</span>", "mint-green palette"
    out = ROOT / f"redesign-{theme}.html"
    conn = connect()
    try:
        dom = (views.country_list(conn, "GB", sort="traffic")["cards"] or [{}])[0].get("domain") or "fotmob.com"
        mob = phone("Home", m_home(conn)) + phone("Site profile", m_profile(conn, dom))
        des = dwin("Home", d_home(conn)) + dwin("Site profile", d_profile(conn, dom))
    finally:
        conn.close()
    doc = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>
<title>Affswap — design test ({esc(theme)})</title><style>{css}</style></head><body>
<div class='wrap'>
  <div class='eyebrow'>Affswap · design direction test</div>
  <h1>{head} — a new direction</h1>
  <p class='lead'>A test of a lighter, more premium look — {pal} + Manrope, inspired by mint.io — on two
    live screens. If the direction feels right, I'll roll it across every screen.</p>
  <p class='note'>Same real data as before · just a new skin. React and I'll refine (colours, type, spacing, imagery).</p>
  <div class='switch'><span class='seg' id='seg'><button data-v='m' class='on'>📱 Mobile</button><button data-v='d'>🖥 Desktop</button></span></div>
  <div class='board' id='bm'>{mob}</div>
  <div class='board-d' id='bd' style='display:none'>{des}</div>
</div><script>{TOGGLE}</script></body></html>"""
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out.name} ({len(doc):,} bytes) · theme={theme} · profile={dom}")


if __name__ == "__main__":
    main()

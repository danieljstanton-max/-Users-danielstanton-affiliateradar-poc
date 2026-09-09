#!/usr/bin/env python3
"""Generate features-review-loyalty.html — design mock for three new features,
in the locked pale-blue look:

  1. "Add review" on the Site profile + Traffic-by-market top-5 with "View all".
  2. Add-review modal (star rating + text).
  3. Loyalty pop-up after the first review ("4 more approved → free swap").
  4. Settings > Loyalty: the review loyalty card (Standard vs Unlimited states).

Interactive: expand markets, open the review modal, submit, flip the plan.
Stdlib only. Uses live Fotmob data for the profile.

    python3 make_features.py
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

from radar import views
from radar.db import connect
from radar.locations import flag

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "features-review-loyalty.html"


def esc(x):
    return html.escape(str(x if x is not None else ""))


def kfmt(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return f"{int(n)}"


MARK = ("<svg class='lmark' viewBox='0 0 44 44' fill='none' stroke-linecap='round' stroke-linejoin='round' "
        "stroke-width='4.8'><path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/>"
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


def market_rows(regs):
    def row(r):
        return (f"<div class='mk'><span class='fl'>{r['flag']}</span>"
                f"<span class='mn'>{esc(r['name'])}{' ★' if r['primary'] else ''}</span>"
                f"<span class='mv'>{kfmt(r['etv'])}</span></div>")
    top = "".join(row(r) for r in regs[:5])
    rest = "".join(row(r) for r in regs[5:])
    more = f"<div class='more' id='more'>{rest}</div>" if rest else ""
    btn = (f"<span class='viewall' onclick=\"toggleAll(this)\">View all {len(regs)} ▾</span>"
           if rest else "")
    return top, more, btn


def profile_phone(conn):
    dom = "fotmob.com"
    p = views.site_profile(conn, dom, published_only=False)
    u = shot(conn, dom)
    hero = f"<img class='pshot' src='{u}'>" if u else "<div class='pshot none'>homepage</div>"
    top, more, btn = market_rows(p["regions"])
    inner = f"""
      <div class='mbar back'><span class='bk'>‹</span><b>{esc(p['name'])}</b></div>
      {hero}
      <div class='pname'>{esc(p['name'])}<span class='pdom'>{esc(dom)}</span></div>
      <div class='crow'>{''.join(f"<span class='chip'>{esc(v)}</span>" for v in p['verticals'])}</div>
      <div class='stat'><div class='sb'>{kfmt(p['etv'])}<span class='u'>/mo</span></div>
        <span class='tp up'>▲ +6%</span></div>
      <div class='card rev-sum'><span class='rate'>4.6</span>
        <span class='rr'><span class='stars'>★★★★★</span><span class='sub'>33 reviews</span></span>
        <button class='addrev' onclick=\"showRev()\">＋ Add review</button></div>
      <div class='lbl-row'><span class='lbl2'>Traffic by market</span>{btn}</div>
      <div class='card pad'>{top}{more}</div>
      <div class='wh'><button class='whb want'>＋ I want this</button><button class='whb have'>✓ I have this</button></div>
      <button class='cta'>Request a swap →</button>

      <!-- add-review modal -->
      <div class='sheet' id='revSheet'>
        <div class='sheet-c'>
          <div class='sh-h'>Review {esc(p['name'])}<span class='x' onclick=\"hideRev()\">✕</span></div>
          <div class='sh-lbl'>Your rating</div>
          <div class='starpick' id='starpick'>
            <span data-v='1'>★</span><span data-v='2'>★</span><span data-v='3'>★</span><span data-v='4'>★</span><span data-v='5'>★</span>
          </div>
          <div class='sh-lbl'>Your experience</div>
          <div class='ta'>Honest notes — traffic quality, payouts, communication…</div>
          <button class='cta' onclick=\"submitRev()\">Submit review</button>
          <div class='sh-note'>Reviews are checked before they go live. Approved reviews count toward your loyalty reward.</div>
        </div>
      </div>

      <!-- loyalty pop-up -->
      <div class='pop' id='pop'>
        <div class='pop-c'>
          <div class='pop-ic'>🎁</div>
          <div class='pop-t'>Review submitted!</div>
          <div class='pop-s'><b>4 more</b> approved reviews and you earn a <b>free swap</b>.</div>
          <div class='prog'><span style='width:20%'></span></div>
          <div class='prog-x'>1 / 5 to your next free swap</div>
          <button class='cta' onclick=\"hidePop()\">Nice — keep going</button>
        </div>
      </div>
    """
    return inner


def loyalty_phone():
    return """
      <div class='mbar'><span class='brand'>""" + MARK + """<span class='logo'>Aff<span>swap</span></span></span><span class='av'>SK</span></div>
      <div class='lbl2' style='margin-top:6px'>Settings · Loyalty</div>

      <div class='plan-toggle'><span class='pt on' data-p='std' onclick=\"setPlan(this)\">Standard plan</span>
        <span class='pt' data-p='unl' onclick=\"setPlan(this)\">Unlimited plan</span></div>

      <div id='loyalStd'>
        <div class='lbl2' style='margin:2px 2px 11px'>Two ways to earn free swaps</div>

        <div class='loyal'>
          <div class='loyal-top'><span class='loyal-ic'>⭐</span>
            <div><div class='loyal-h'>Write reviews</div><div class='loyal-sub'>5 approved reviews → 1 free swap</div></div>
            <span class='earned'>+2 earned</span></div>
          <div class='ring-row'>
            <div class='dots'><span class='d on'></span><span class='d on'></span><span class='d on'></span><span class='d'></span><span class='d'></span></div>
            <div class='ring-t'><b>3 / 5</b> approved</div></div>
          <div class='loyal-big'>2 more approved reviews → <b>1 free swap</b></div>
          <div class='loyal-rule'>Honest reviews of sites you know. Low-effort or fake ones are rejected and don't count.</div>
        </div>

        <div class='loyal' style='margin-top:13px'>
          <div class='loyal-top'><span class='loyal-ic'>➕</span>
            <div><div class='loyal-h'>Suggest a site</div><div class='loyal-sub'>Approved affiliate → 1 free swap</div></div>
            <span class='earned'>+3 earned</span></div>
          <div class='loyal-big'>Submit an affiliate we don't list yet. When it's <b>approved</b>, you get <b>1 free swap</b>.</div>
          <div class='loyal-stats'>
            <div><div class='ls-v'>3</div><div class='ls-k'>Approved</div></div>
            <div><div class='ls-v'>1</div><div class='ls-k'>Pending review</div></div></div>
          <button class='cta' style='margin-top:2px'>＋ Suggest a site</button>
        </div>
      </div>

      <div class='loyal unl' id='loyalUnl' style='display:none'>
        <div class='loyal-top'><span class='loyal-ic'>∞</span>
          <div><div class='loyal-h'>You're on Unlimited</div><div class='loyal-sub'>Swaps are already unlimited</div></div></div>
        <div class='loyal-big' style='margin-top:6px'>Loyalty rewards are <b>off</b> on Unlimited — reviews and site suggestions don't earn swaps (you don't need them).</div>
        <div class='loyal-rule'>They still help the community and build your reputation.</div>
      </div>
    """


CSS = """
*{box-sizing:border-box}
:root{--bg:#F2F7FC;--card:#fff;--line:#E4EBF4;--ink:#14213B;--ink2:#5B6B84;--ink3:#93A0B5;
 --blue:#4E97E6;--blue-d:#2E77CC;--blue-dd:#205CA6;--blue-050:#ECF3FD;--up:#12A150;--gold:#F5A524;
 --sans:'Manrope',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}
body{margin:0;font-family:var(--sans);color:var(--ink);
 background:radial-gradient(900px 500px at 100% -6%,#DCEAFB,transparent 60%),
  radial-gradient(700px 480px at 0 0,#E7F0FB,transparent 55%),var(--bg)}
.wrap{max-width:1120px;margin:0 auto;padding:34px 22px 80px}
.eyebrow{font:800 11.5px var(--sans);letter-spacing:.16em;color:var(--blue-dd);text-transform:uppercase}
h1{font-size:31px;font-weight:800;letter-spacing:-.7px;margin:.3em 0 .12em}h1 span{color:var(--blue-d)}
.lead{color:var(--ink2);max-width:760px;line-height:1.55;font-size:15px;margin:0 0 28px}
.board{display:flex;flex-wrap:wrap;gap:34px;justify-content:center}
figure{margin:0;width:330px}
figcaption{margin-top:12px;text-align:center;color:var(--ink3);font:700 11px var(--sans);letter-spacing:.09em;text-transform:uppercase}
.phone{border-radius:42px;padding:11px;background:#fff;box-shadow:0 30px 60px -22px rgba(16,40,60,.32),0 0 0 1px var(--line)}
.screen{position:relative;height:640px;overflow-y:auto;border-radius:32px;background:linear-gradient(180deg,#F7FBFE,#eef5fb);padding:15px 14px 26px;scrollbar-width:none}
.screen::-webkit-scrollbar{display:none}
.mbar{display:flex;align-items:center;gap:9px;padding:2px 2px 10px}.mbar.back b{font-size:16px}
.mbar .bk{font-size:22px;color:var(--blue-d)}
.brand{display:inline-flex;align-items:center;gap:8px}.lmark{width:22px;height:22px;flex:0 0 auto;display:block}
.more{display:none}
.logo{font-weight:800;font-size:18px;letter-spacing:-.3px}.logo span{color:var(--blue-d)}
.av{margin-left:auto;width:32px;height:32px;border-radius:50%;background:var(--blue-050);color:var(--blue-dd);display:flex;align-items:center;justify-content:center;font:800 11px var(--sans)}
.pshot{width:100%;height:150px;object-fit:cover;object-position:top;border-radius:15px;box-shadow:0 10px 24px -14px rgba(16,40,60,.3);border:1px solid var(--line)}
.pshot.none{display:flex;align-items:center;justify-content:center;height:150px;border-radius:15px;background:var(--blue-050);color:var(--ink3);font-weight:700}
.pname{font:800 21px var(--sans);letter-spacing:-.4px;margin:13px 2px 6px;display:flex;flex-direction:column}
.pdom{color:var(--ink3);font-size:12.5px;font-weight:600;margin-top:1px}
.crow{display:flex;flex-wrap:wrap;gap:6px;margin:2px 2px}
.chip{font:800 10px var(--sans);letter-spacing:.03em;text-transform:uppercase;color:var(--blue-dd);background:var(--blue-050);border-radius:999px;padding:4px 10px}
.stat{display:flex;align-items:center;gap:12px;margin:12px 2px 6px}
.sb{font:800 30px var(--sans);letter-spacing:-1px}.u{color:var(--ink3);font:700 11px var(--sans);margin-left:2px}
.tp{font:800 12px var(--sans);border-radius:999px;padding:4px 10px}.tp.up{color:var(--up);background:#E6F6EC}
.card{background:#fff;border:1px solid var(--line);border-radius:15px;box-shadow:0 5px 16px -12px rgba(16,40,60,.25)}
.card.pad{padding:6px 8px}
.rev-sum{display:flex;align-items:center;gap:11px;padding:12px 13px;margin:12px 0}
.rate{font:800 28px var(--sans);letter-spacing:-1px}.rr{display:flex;flex-direction:column}
.stars{color:var(--gold);font-size:13px;letter-spacing:1.5px}.sub{color:var(--ink3);font-size:11.5px}
.addrev{margin-left:auto;cursor:pointer;border:1.5px solid var(--blue);color:var(--blue-dd);background:var(--blue-050);
 font:800 12.5px var(--sans);border-radius:11px;padding:9px 13px}
.lbl-row{display:flex;align-items:center;justify-content:space-between;margin:16px 4px 8px}
.lbl2{color:var(--ink3);font:800 11px var(--sans);letter-spacing:.09em;text-transform:uppercase}
.viewall{cursor:pointer;color:var(--blue-d);font:800 11.5px var(--sans)}
.mk{display:flex;align-items:center;gap:9px;padding:8px 6px}
.mk .fl{font-size:16px}.mk .mn{flex:1;font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mk .mv{font:800 12.5px var(--sans)}
.wh{display:flex;gap:10px;margin:16px 0 0}
.whb{flex:1;cursor:pointer;border-radius:13px;padding:13px;font:800 13px var(--sans);border:1.5px solid var(--line);background:#fff}
.whb.want{border-color:var(--blue);color:var(--blue-dd);background:var(--blue-050)}.whb.have{border-color:#8FE3C4;color:var(--up);background:#EAF7F0}
.cta{width:100%;margin-top:12px;cursor:pointer;border:0;border-radius:14px;padding:14px;font:800 14.5px var(--sans);
 color:#fff;background:linear-gradient(120deg,#5AA0EA,#2E77CC);box-shadow:0 16px 30px -14px rgba(46,119,204,.7)}
/* modal sheet */
.sheet,.pop{position:absolute;inset:0;z-index:5;display:none;align-items:flex-end;background:rgba(16,32,60,.4);border-radius:32px}
.sheet.show,.pop.show{display:flex}
.sheet-c{background:#fff;width:100%;border-radius:22px 22px 32px 32px;padding:18px 16px 20px;box-shadow:0 -10px 30px -12px rgba(16,32,60,.3)}
.sh-h{display:flex;align-items:center;font:800 16px var(--sans)}.sh-h .x{margin-left:auto;cursor:pointer;color:var(--ink3);font-size:16px}
.sh-lbl{color:var(--ink3);font:800 10.5px var(--sans);letter-spacing:.08em;text-transform:uppercase;margin:14px 2px 6px}
.starpick{font-size:30px;letter-spacing:5px;color:#DCE4EE;cursor:pointer}.starpick span.on{color:var(--gold)}
.ta{border:1px solid var(--line);border-radius:12px;padding:11px 12px;color:var(--ink3);font-size:12.5px;min-height:66px;background:#fff}
.sh-note{color:var(--ink3);font-size:11px;text-align:center;margin-top:10px;line-height:1.5}
/* pop-up */
.pop{align-items:center;padding:22px}
.pop-c{background:#fff;border-radius:22px;padding:22px 20px;text-align:center;width:100%;box-shadow:0 20px 50px -18px rgba(16,32,60,.4)}
.pop-ic{font-size:40px}.pop-t{font:800 20px var(--sans);margin-top:6px}
.pop-s{color:var(--ink2);font-size:13.5px;margin:6px 0 14px;line-height:1.5}
.prog{height:9px;background:#E9F0FA;border-radius:999px;overflow:hidden}.prog span{display:block;height:100%;background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.prog-x{color:var(--ink3);font:700 11.5px var(--sans);margin:7px 0 4px}
/* loyalty card */
.plan-toggle{display:flex;gap:5px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:4px;margin:8px 0 16px}
.pt{flex:1;text-align:center;cursor:pointer;font:800 12px var(--sans);color:var(--ink2);padding:8px;border-radius:9px}
.pt.on{background:var(--blue);color:#fff}
.loyal{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 10px 30px -18px rgba(16,40,60,.28)}
.loyal-top{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.loyal-ic{width:46px;height:46px;border-radius:13px;background:var(--blue-050);display:flex;align-items:center;justify-content:center;font-size:24px}
.loyal-h{font:800 17px var(--sans)}.loyal-sub{color:var(--ink3);font-size:12px}
.earned{margin-left:auto;background:#E6F6EC;color:var(--up);font:800 10.5px var(--sans);border-radius:999px;padding:4px 10px;white-space:nowrap}
.ring-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
.dots{display:flex;gap:7px}.d{width:22px;height:10px;border-radius:999px;background:#E4ECF6}.d.on{background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.ring-t{color:var(--ink2);font-size:12.5px}.ring-t b{color:var(--ink);font-weight:800}
.loyal-big{font:800 16px var(--sans);letter-spacing:-.3px;line-height:1.35;margin-bottom:14px}.loyal-big b{color:var(--blue-d)}
.loyal-stats{display:flex;gap:12px;margin-bottom:14px}
.loyal-stats>div{flex:1;background:var(--blue-050);border-radius:13px;padding:12px}
.ls-v{font:800 24px var(--sans);color:var(--blue-dd)}.ls-k{color:var(--ink2);font-size:11px;margin-top:2px}
.loyal-rule{color:var(--ink2);font-size:12.5px;line-height:1.6}.loyal-rule b{color:var(--ink)}
.loyal.unl .loyal-ic{font-size:28px;color:var(--blue-d)}
"""

JS = """
function showRev(){document.getElementById('revSheet').classList.add('show');}
function hideRev(){document.getElementById('revSheet').classList.remove('show');}
function submitRev(){hideRev();document.getElementById('pop').classList.add('show');}
function hidePop(){document.getElementById('pop').classList.remove('show');}
function toggleAll(el){var m=document.getElementById('more');var open=m.style.display!=='none'&&m.style.display!=='';
 if(open){m.style.display='none';el.textContent=el.textContent.replace('▴','▾');}
 else{m.style.display='block';el.innerHTML=el.innerHTML.replace('▾','▴');}}
document.querySelectorAll('.starpick span').forEach(function(s){s.addEventListener('click',function(){
 var v=+s.dataset.v;s.parentNode.querySelectorAll('span').forEach(function(o){o.classList.toggle('on',+o.dataset.v<=v);});});});
function setPlan(el){document.querySelectorAll('.pt').forEach(function(x){x.classList.toggle('on',x===el);});
 var unl=el.dataset.p==='unl';document.getElementById('loyalStd').style.display=unl?'none':'block';
 document.getElementById('loyalUnl').style.display=unl?'block':'none';}
"""


def main():
    conn = connect()
    try:
        prof = profile_phone(conn)
        loyal = loyalty_phone()
    finally:
        conn.close()
    def fig(label, inner):
        return f"<figure><div class='phone'><div class='screen'>{inner}</div></div><figcaption>{esc(label)}</figcaption></figure>"
    doc = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>
<title>Affswap — reviews & loyalty</title><style>{CSS}</style></head><body>
<div class='wrap'>
  <div class='eyebrow'>Affswap · new features</div>
  <h1>Reviews &amp; <span>review loyalty</span></h1>
  <p class='lead'>Interactive mock: on the Site profile, tap <b>Add review</b> (star rating + notes) and <b>View all</b>
    to expand markets. Submitting shows the loyalty pop-up. In Settings, the loyalty card tracks progress to your next
    free swap — and switches off on the Unlimited plan (toggle it).</p>
  <div class='board'>
    {fig("Site profile · add review + top-5 markets", prof)}
    {fig("Settings · review loyalty", loyal)}
  </div>
</div>
<script>{JS}</script>
</body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT.name} ({len(doc):,} bytes)")


if __name__ == "__main__":
    main()

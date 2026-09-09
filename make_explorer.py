#!/usr/bin/env python3
"""Generate explore-live.html — an interactive test view over the LIVE catalogue.

Pick any geo, see that market's gated site list (homepage screenshot + verticals
+ traffic IN that geo), and click a site for its full profile (large screenshot +
per-market breakdown). All data + screenshots are baked into one self-contained
file (no server, works offline). Built from radar/views.py, so it shows exactly
what the app would — real DataForSEO traffic, auto-tagged verticals, the
per-country listing gate, and the captured screenshots. Stdlib only.

    python3 make_explorer.py            # -> explore-live.html
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from radar import views
from radar.db import connect
from radar.locations import flag, name

ROOT = Path(__file__).resolve().parent
# --embed  → base64 the screenshots inline (portable single file, works anywhere,
#            larger). Default → relative paths (light, open from the project root).
EMBED = "--embed" in sys.argv
OUT = ROOT / ("explore-live-portable.html" if EMBED else "explore-live.html")

_SHOT: dict[str, str | None] = {}


def shot_uri(conn, domain: str) -> str | None:
    """The site's real screenshot — as a relative path (light) or an inline data
    URI (--embed, portable). None if there's no real capture."""
    if domain in _SHOT:
        return _SHOT[domain]
    ref = None
    row = conn.execute(
        """SELECT sc.image_ref FROM screenshots sc JOIN sites s ON s.id = sc.site_id
           WHERE s.domain = ? AND sc.provider <> 'mock'""", (domain,)).fetchone()
    if row and row["image_ref"]:
        p = Path(row["image_ref"])
        try:
            if p.exists() and p.stat().st_size > 500:
                if EMBED:
                    b = p.read_bytes()
                    mime = "image/jpeg" if b[:3] == bytes.fromhex("ffd8ff") else "image/png"
                    ref = f"data:{mime};base64," + base64.b64encode(b).decode()
                elif p.is_absolute() and str(p).startswith(str(ROOT)):
                    ref = str(p.relative_to(ROOT))
                else:
                    ref = str(p)
        except OSError:
            ref = None
    _SHOT[domain] = ref
    return ref


def build_data(conn) -> dict:
    idx = views.countries_index(conn)
    countries = sorted(idx["countries"], key=lambda c: -c["count"])
    by_country: dict[str, list[str]] = {}
    sites: dict[str, dict] = {}

    for co in countries:
        iso = co["iso"]
        cl = views.country_list(conn, iso, sort="traffic")
        by_country[iso] = [c["domain"] for c in cl["cards"]]
        for c in cl["cards"]:
            dom = c["domain"]
            s = sites.get(dom)
            if s is None:
                s = sites[dom] = {
                    "name": c["name"],
                    "verticals": c["verticals"],
                    "etv": c["total_etv"] or 0,
                    "trend": c["trend"], "dir": c["trend_dir"],
                    "origin": {"flag": c["traffic_origin"]["flag"],
                               "iso": c["traffic_origin"]["iso"] or ""},
                    "isNew": bool(c.get("is_new")),
                    "hasContact": bool(c.get("has_contact")),
                    "inCountry": {},
                    "shot": shot_uri(conn, dom),
                    "regions": None,
                }
            s["inCountry"][iso] = round(c["etv"] or 0)

    # profile region breakdown, once per unique visible site
    for dom, s in sites.items():
        p = views.site_profile(conn, dom, published_only=False)
        s["regions"] = [
            {"flag": r["flag"], "name": r["name"], "iso": r["iso"],
             "etv": round(r["etv"] or 0), "primary": bool(r["primary"])}
            for r in (p["regions"] if p else [])
        ]

    return {
        "countries": [
            {"iso": c["iso"], "flag": c["flag"], "name": c["name"],
             "count": c["count"], "etv": round(c["total_etv"] or 0)}
            for c in countries
        ],
        "byCountry": by_country,
        "sites": sites,
        "totals": {
            "sites": conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0],
            "visible": len(sites),
            "markets": len(countries),
            "shots": sum(1 for s in sites.values() if s["shot"]),
        },
    }


CSS = """
:root{--bg:#F1F5FB;--card:#fff;--card2:#F7FAFE;--line:#E4EBF4;
 --ink:#14213B;--ink2:#5B6B84;--ink3:#93A0B5;--blue:#4E97E6;--blue-d:#2E77CC;--blue-dd:#205CA6;--blue-050:#ECF3FD;
 --up:#12A150;--down:#E5484D;--gold:#F5A524;
 --sans:'Manrope',-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;--mono:ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--ink);min-height:100vh;
 background:radial-gradient(900px 500px at 100% -6%,#DCEAFB,transparent 60%),
  radial-gradient(700px 480px at 0 0,#E7F0FB,transparent 55%),var(--bg)}
.wrap{max-width:1220px;margin:0 auto;padding:26px 20px 80px}
.brand{display:inline-flex;align-items:center;gap:9px;margin-bottom:10px}
.brand svg{width:26px;height:26px;flex:0 0 auto;display:block}
.blogo{font-weight:800;font-size:19px;letter-spacing:-.3px;color:var(--ink)}.blogo span{color:var(--blue-d)}
.eyebrow{font:800 11.5px/1 var(--sans);letter-spacing:.16em;color:var(--blue-dd);text-transform:uppercase}
h1{margin:.3em 0 .15em;font-size:28px;font-weight:800;letter-spacing:-.5px}
h1 span{color:var(--blue-d)}
.lead{color:var(--ink2);max-width:820px;line-height:1.5;margin:0 0 18px;font-size:14px}
.stat{display:inline-block;color:var(--ink);font-weight:800}
.controls{position:sticky;top:0;z-index:20;display:flex;flex-wrap:wrap;gap:12px;align-items:center;
 padding:14px 0;margin-bottom:8px;
 background:linear-gradient(180deg,#F1F5FBf2,#F1F5FBcc);backdrop-filter:blur(9px);border-bottom:1px solid var(--line)}
select{font:700 14px var(--sans);color:var(--ink);background:#fff;border:1px solid var(--line);
 border-radius:11px;padding:10px 12px;min-width:230px;box-shadow:0 4px 14px -10px rgba(16,28,52,.2)}
.vtabs{display:flex;gap:7px;flex-wrap:wrap}
.vtab{cursor:pointer;font:700 12.5px var(--sans);color:var(--ink2);border:1px solid var(--line);
 border-radius:999px;padding:7px 14px;user-select:none;background:#fff}
.vtab.on{color:#fff;background:var(--blue);border-color:var(--blue)}
.meta{color:var(--ink3);font:700 12px var(--mono);margin:2px 4px 16px}.meta b{color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(212px,1fr));gap:14px}
.card{cursor:pointer;background:#fff;border:1px solid var(--line);border-radius:14px;padding:13px 14px 12px;
 box-shadow:0 4px 14px -10px rgba(16,28,52,.2);transition:transform .12s,box-shadow .12s,border-color .12s}
.card:hover{transform:translateY(-2px);border-color:var(--blue);box-shadow:0 14px 30px -14px rgba(46,119,204,.4)}
.card:hover .cview{color:var(--blue-d)}
.ctop{display:flex;align-items:center;gap:8px}
.rank{color:var(--blue-d);font:800 12px var(--sans)}
.cname{font-size:15px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.newb{background:var(--blue);color:#fff;font:800 8.5px var(--mono);border-radius:4px;padding:2px 5px}
.cam{color:var(--blue-d);font-size:12px}.cam.off{color:var(--ink3);opacity:.4}
.cnum{display:flex;align-items:baseline;gap:7px;margin:8px 0 9px}
.cnum b{font:800 21px var(--sans);color:var(--ink)}
.cnum .per{font:700 9.5px var(--mono);color:var(--ink3)}
.cview{margin-top:9px;color:var(--ink3);font:700 11px var(--mono)}
.trend{font:800 10.5px var(--mono);padding:2px 6px;border-radius:999px;margin-left:auto}
.trend.up{color:var(--up);background:#E6F6EC}
.trend.down{color:var(--down);background:#FCEBEC}
.trend.flat{color:var(--ink3);background:#EEF2F0}
.chips{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.vchip{font:800 9px var(--mono);letter-spacing:.05em;text-transform:uppercase;color:var(--blue-dd);
 border-radius:999px;padding:2px 8px;background:var(--blue-050)}
.also{color:var(--ink3);font-size:10.5px}
.empty{color:var(--ink3);padding:60px 10px;text-align:center;grid-column:1/-1}
.ov{position:fixed;inset:0;z-index:50;display:none;background:rgba(16,32,60,.42);backdrop-filter:blur(4px);
 padding:30px 16px;overflow-y:auto}
.ov.show{display:block}
.pf{max-width:640px;margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:18px;overflow:hidden;
 box-shadow:0 40px 90px -24px rgba(16,28,52,.4)}
.pf .big{width:100%;height:280px;object-fit:cover;object-position:top;display:block;background:var(--blue-050)}
.pf .big.none{display:flex;align-items:center;justify-content:center;color:var(--ink3);font:700 12px var(--mono)}
.pf-in{padding:18px 20px 24px}
.pf-top{display:flex;align-items:flex-start;gap:10px}
.pf-name{font-size:21px;font-weight:800}
.pf-dom{color:var(--ink3);font:600 12px var(--mono)}
.pf-close{margin-left:auto;cursor:pointer;color:var(--ink2);border:1px solid var(--line);border-radius:9px;
 padding:6px 11px;font:800 13px var(--sans);background:#fff}
.pf-hero{display:flex;align-items:baseline;gap:12px;margin:12px 0 6px}
.pf-etv{font:800 30px var(--sans);color:var(--ink)}
.pf-sec{color:var(--ink3);font:800 10.5px var(--mono);letter-spacing:.13em;text-transform:uppercase;margin:16px 2px 9px}
.breg{display:flex;align-items:center;gap:10px;padding:6px 2px}
.breg .bf{font-size:16px}.breg .bn{width:130px;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{flex:1;height:8px;background:#E9F0FA;border-radius:999px;overflow:hidden}
.bar span{display:block;height:100%;background:linear-gradient(90deg,#4E97E6,#7EB2F0)}
.breg .be{width:64px;text-align:right;font:800 11.5px var(--mono);color:var(--ink2)}
.lock{margin-top:16px;text-align:center;color:var(--ink2);font-size:12.5px;border:1px dashed var(--line);border-radius:12px;padding:12px}
@media(max-width:560px){.grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.pf .big{height:190px}}
"""

JS = """
const $=s=>document.querySelector(s);
let curV='all';
function fmt(n){n=+n||0;if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return (n/1e3).toFixed(1)+'k';return ''+Math.round(n);}
function trendCls(d){return d==='up'?'up':d==='down'?'down':'flat';}
function opt(c){return `<option value="${c.iso}">${c.flag} ${c.name} — ${c.count} sites</option>`;}
function cardHTML(dom,i,iso){
  const s=DATA.sites[dom];if(!s)return '';
  const also=Object.keys(s.inCountry).filter(k=>k!==iso).slice(0,5).map(k=>flagOf(k)).join(' ');
  const chips=s.verticals.map(v=>`<span class="vchip">${v}</span>`).join('');
  const cam=s.shot?'<span class="cam" title="homepage image available">▦</span>':'<span class="cam off" title="no homepage image yet">▦</span>';
  return `<div class="card" data-dom="${dom}">
    <div class="ctop"><span class="rank">${i+1}</span><span class="cname">${esc(s.name)}</span>${s.isNew?'<span class="newb">NEW</span>':''}${cam}</div>
    <div class="cnum"><b>${fmt(s.inCountry[iso])}</b><span class="per">/mo here</span>
      <span class="trend ${trendCls(s.dir)}">${esc(s.trend)}</span></div>
    <div class="chips">${chips}${also?`<span class="also">also ${also}</span>`:''}</div>
    <div class="cview">View site &amp; screenshot ›</div>
  </div>`;}
const FLAGS={};DATA.countries.forEach(c=>FLAGS[c.iso]=c.flag);
function flagOf(iso){return FLAGS[iso]||'';}
function esc(x){return (''+x).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function render(){
  const iso=$('#country').value, doms=(DATA.byCountry[iso]||[]).filter(d=>curV==='all'||(DATA.sites[d]&&DATA.sites[d].verticals.includes(curV)));
  const co=DATA.countries.find(c=>c.iso===iso)||{};
  $('#meta').innerHTML=`${co.flag||''} <b style="color:#eaf4ff">${esc(co.name||iso)}</b> — ${doms.length} site${doms.length===1?'':'s'} clear the traffic bar${curV!=='all'?` · ${curV}`:''}`;
  $('#grid').innerHTML=doms.length?doms.map((d,i)=>cardHTML(d,i,iso)).join(''):'<div class="empty">No sites in this geo/vertical.</div>';
}
function openProfile(dom){
  const s=DATA.sites[dom];if(!s)return;
  const max=Math.max(1,...s.regions.map(r=>r.etv));
  const regs=s.regions.slice(0,10).map(r=>`<div class="breg"><span class="bf">${r.flag}</span>
    <span class="bn">${esc(r.name)}${r.primary?' ★':''}</span>
    <span class="bar"><span style="width:${Math.max(5,Math.round(r.etv/max*100))}%"></span></span>
    <span class="be">${fmt(r.etv)}</span></div>`).join('');
  const big=s.shot?`<img class="big" src="${s.shot}">`:'<div class="big none">no screenshot — upload one in the back office</div>';
  $('#pf').innerHTML=`${big}<div class="pf-in">
    <div class="pf-top"><div><div class="pf-name">${esc(s.name)}</div><div class="pf-dom">${esc(dom)}</div></div>
      <div class="pf-close" onclick="closeP()">✕ Close</div></div>
    <div class="pf-hero"><span class="pf-etv">${fmt(s.etv)}</span><span class="per" style="color:#5a6e97;font:600 11px var(--mono)">/mo total</span>
      <span class="trend ${trendCls(s.dir)}">${esc(s.trend)}</span></div>
    <div class="chips">${s.verticals.map(v=>`<span class="vchip">${v}</span>`).join('')}</div>
    <div class="pf-sec">Traffic by market</div>${regs}
    <div class="lock">🔒 Contact channels unlock by swapping</div></div>`;
  $('#ov').classList.add('show');
}
function closeP(){$('#ov').classList.remove('show');}
document.addEventListener('DOMContentLoaded',()=>{
  const sel=$('#country');sel.innerHTML=DATA.countries.map(opt).join('');
  sel.addEventListener('change',render);
  document.querySelectorAll('.vtab').forEach(t=>t.addEventListener('click',()=>{
    document.querySelectorAll('.vtab').forEach(x=>x.classList.remove('on'));t.classList.add('on');curV=t.dataset.v;render();}));
  $('#grid').addEventListener('click',e=>{const c=e.target.closest('.card');if(c)openProfile(c.dataset.dom);});
  $('#ov').addEventListener('click',e=>{if(e.target.id==='ov')closeP();});
  render();
});
"""


def main() -> None:
    conn = connect()
    try:
        data = build_data(conn)
    finally:
        conn.close()
    t = data["totals"]
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    vtabs = "".join(
        f"<span class='vtab{' on' if v=='all' else ''}' data-v='{v}'>{lbl}</span>"
        for v, lbl in [("all", "All"), ("casino", "Casino"), ("sportsbook", "Sportsbook"),
                       ("bingo", "Bingo"), ("poker", "Poker")])
    doc = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<link rel='icon' type='image/svg+xml' href='data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0NCA0NCIgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiByb2xlPSJpbWciIGFyaWEtbGFiZWw9IkFmZnN3YXAiPgogIDxkZWZzPgogICAgPGxpbmVhckdyYWRpZW50IGlkPSJ0aWxlIiB4MT0iMCIgeTE9IjAiIHgyPSIxIiB5Mj0iMSI+CiAgICAgIDxzdG9wIG9mZnNldD0iMCIgc3RvcC1jb2xvcj0iIzVBQTBFQSIvPgogICAgICA8c3RvcCBvZmZzZXQ9IjEiIHN0b3AtY29sb3I9IiMyRTc3Q0MiLz4KICAgIDwvbGluZWFyR3JhZGllbnQ+CiAgPC9kZWZzPgogIDxyZWN0IHdpZHRoPSI0NCIgaGVpZ2h0PSI0NCIgcng9IjExIiBmaWxsPSJ1cmwoI3RpbGUpIi8+CiAgPHBhdGggZD0iTTExIDkgTDE4IDIyIEwxMSAzNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjRkZGRkZGIiBzdHJva2Utd2lkdGg9IjQuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CiAgPHBhdGggZD0iTTMzIDkgTDI2IDIyIEwzMyAzNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjQkJEOEY4IiBzdHJva2Utd2lkdGg9IjQuOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+Cjwvc3ZnPgo='>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link href='https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap' rel='stylesheet'>
<title>Affswap — geo explorer (live)</title><style>{CSS}</style></head><body>
<div class='wrap'>
  <div class='brand'><svg viewBox='0 0 44 44' fill='none' stroke-linecap='round' stroke-linejoin='round' stroke-width='4.8'><path d='M11 9 L18 22 L11 35' stroke='#2E77CC'/><path d='M33 9 L26 22 L33 35' stroke='#7FB4F2'/></svg><span class='blogo'>Aff<span>swap</span></span></div>
  <div class='eyebrow'>Affswap · geo explorer · live data</div>
  <h1>Browse the catalogue by <span>geo</span></h1>
  <p class='lead'><span class='stat'>{t['visible']}</span> sites visible across
    <span class='stat'>{t['markets']}</span> markets · <span class='stat'>{t['shots']}</span> homepage
    screenshots captured. Pick a geo, filter by vertical, and click any site for its full profile
    (screenshot + per-market traffic). Everything reflects the live gate (500 / 1,500 per-country).</p>
  <div class='controls'>
    <select id='country'></select>
    <div class='vtabs'>{vtabs}</div>
  </div>
  <div class='meta' id='meta'></div>
  <div class='grid' id='grid'></div>
</div>
<div class='ov' id='ov'><div class='pf' id='pf'></div></div>
<script>const DATA={payload};</script>
<script>{JS}</script>
</body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT.name} ({len(doc):,} bytes) · {t['visible']} sites · {t['markets']} geos · {t['shots']} shots")


if __name__ == "__main__":
    main()

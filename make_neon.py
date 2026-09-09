#!/usr/bin/env python3
"""Generate screens-neon.html from screens.html.

screens.html is the plain base preview. This transform restyles it into the
neon-blue house style WITHOUT touching any copy or markup structure:

  1. rewrite the :root design tokens to the neon-blue palette
  2. swap the legacy accent hex (#E8B457) for cyan (#38C6FF)
  3. append a NEON-BLUE FX LAYER (glow borders, HUD grid, scanlines, pulses)

Run it whenever screens.html changes:

    python3 make_neon.py

Deterministic: same input -> same output. Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "screens.html"
OUT = ROOT / "screens-neon.html"

NEON_ROOT = """:root{
    --stage:#04050C; --stage-2:#06080F;
    --app:#06080F; --card:rgba(16,26,52,.60); --card-2:rgba(22,34,66,.76); --card-3:rgba(34,52,96,.96);
    --line:rgba(80,196,255,.30); --line-2:rgba(90,206,255,.58);
    --ink:#EAF4FF; --ink-2:#94AFDE; --ink-3:#5A6E97; --star-empty:#26375C;
    --gold:#38C6FF; --gold-soft:rgba(56,198,255,.16);
    --magenta:#37B7FF; --magenta-soft:rgba(56,198,255,.16);
    --up:#2CF5D0; --up-soft:rgba(44,245,208,.16);
    --down:#FF4D8F; --down-soft:rgba(255,77,143,.14);
    --flat:#7C8AA0;
    --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",system-ui,sans-serif;
  }"""

NEON_FX = """
  /* ===================== NEON-BLUE FX LAYER ===================== */
  .stage{background:
    radial-gradient(1200px 820px at 15% -8%, rgba(56,198,255,.22), transparent 58%),
    radial-gradient(1000px 700px at 100% 0%, rgba(90,120,255,.16), transparent 55%),
    radial-gradient(1100px 1000px at 50% 125%, rgba(56,150,255,.20), transparent 60%),
    linear-gradient(180deg,#06080F,#04050C) !important;}
  h1 span{text-shadow:0 0 28px rgba(56,198,255,.65)}
  .phone{background:linear-gradient(160deg,#16243f,#05070e);
    box-shadow:0 40px 90px -24px rgba(56,198,255,.45),0 0 0 1px rgba(90,200,255,.35),0 0 46px -6px rgba(56,198,255,.4),inset 0 0 0 1px rgba(255,255,255,.03)}
  .screen{background:linear-gradient(180deg,#080D1F,#05070F)}
  .screen::before{content:"";position:absolute;inset:0;z-index:0;pointer-events:none;opacity:.9;
    background:linear-gradient(rgba(90,190,255,.07) 1px,transparent 1px) 0 0/100% 40px,
              linear-gradient(90deg,rgba(90,190,255,.055) 1px,transparent 1px) 0 0/40px 100%;
    -webkit-mask-image:linear-gradient(180deg,#000 0%,transparent 66%);mask-image:linear-gradient(180deg,#000 0%,transparent 66%)}
  .screen::after{content:"";position:absolute;inset:0;z-index:0;pointer-events:none;opacity:.5;
    background:repeating-linear-gradient(180deg,rgba(120,200,255,.05) 0 1px,transparent 1px 3px);mix-blend-mode:screen}
  .scroll{position:relative;z-index:1}
  .appbar{background:linear-gradient(180deg,rgba(8,13,31,.97),rgba(8,13,31,.55)) !important}
  .tile,.trow-top,.group,.metric,.chartcard,.contactcard,.reviews,.cbtn3,.frow,.trow,.mrow,.mvrow,.seg,.mv-dd,.search,.followbtn,.seeall,.mod-note,.acct-row,.usage-card,.card,.statpill,.plan,.credit,.miniseg,.room,.bubble,.cta-chat,.wh-btn,.match,.swap-pair{
    backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px)}
  .trow-top,.mrow,.metric,.chartcard,.contactcard,.reviews,.cbtn3,.frow,.trow,.mvrow,.seg,.mv-dd,.search,.followbtn,.seeall,.pf-shot,.plan-chip,.tile,.acct-row,.usage-card,.avatar,.card,.statpill,.plan,.field,.credit,.miniseg,.room,.composer input,.cta-chat,.chat-locked .lockbig,.vbtn.em,.wh-btn,.match{
    border:1px solid rgba(56,198,255,.55) !important;
    box-shadow:0 0 15px rgba(56,198,255,.32), inset 0 0 12px rgba(56,198,255,.06) !important}
  .trow-top:hover,.mrow:hover,.trow:hover,.mvrow:hover,.tile:hover,.acct-row:hover,.room:hover,.cta-chat:hover{
    border-color:#5CD6FF !important; box-shadow:0 0 24px rgba(56,198,255,.6), inset 0 0 15px rgba(56,198,255,.12) !important}
  .avatar,.avatar-sm,.chat-locked .lockbig{box-shadow:0 0 24px -2px rgba(56,198,255,.5) !important}
  .gauge .prog{filter:drop-shadow(0 0 5px rgba(56,198,255,.85))}
  .gauge .gbig,.acct-name,.credit .cbig,.chat-locked h2{text-shadow:0 0 14px rgba(56,198,255,.5)}
  .acct-lbl .t,.lrow .lt,.room .rn,.cta-chat .cc-t{color:#EAF6FF}
  .seclabel,.sec-label,.mv-sellabel,.segnote,.cty-sum,.ut,.ureset,.grp-label,.flabel,.band-label,.convo-sum{color:#93DAFF;text-shadow:0 0 16px rgba(56,198,255,.5)}
  .seclabel,.sec-label{letter-spacing:.16em}
  .home-logo .word{text-shadow:0 0 30px rgba(56,198,255,.75)}
  .home-logo .word span{color:#7CC6FF;text-shadow:0 0 30px rgba(56,198,255,.85)}
  .home-logo .tag{color:#80CCFF;text-shadow:0 0 22px rgba(56,198,255,.7);letter-spacing:.22em}
  .home-logo .mark{animation:neonpulse 3.4s ease-in-out infinite}
  @keyframes neonpulse{0%,100%{filter:drop-shadow(0 0 6px rgba(56,198,255,.6))}50%{filter:drop-shadow(0 0 16px rgba(56,198,255,.95))}}
  .appbar-title{color:#93DAFF;letter-spacing:.08em;text-shadow:0 0 14px rgba(56,198,255,.5)}
  .mrflag,.cty-title .cflag,.tile .tflag{filter:drop-shadow(0 0 8px rgba(56,198,255,.55))}
  .rankno,.now,.tetv,.big-rating,.rrating,.metric .v,.statpill .n,.step .sn{text-shadow:0 0 15px rgba(56,198,255,.5)}
  .mrname,.tdom,.pf-name{color:#EAF6FF;text-shadow:0 0 10px rgba(56,198,255,.25)}
  .trow-top .rankno{color:#8AD4FF;text-shadow:0 0 18px rgba(56,198,255,.8)}
  .vtab.on,.seg button.on,.rangeseg button.on,.miniseg button.on{background:rgba(56,198,255,.13) !important;color:#7FE4FF !important;
    box-shadow:inset 0 0 0 1px #38C6FF, 0 0 22px rgba(56,198,255,.7) !important;text-shadow:0 0 12px rgba(56,198,255,.6)}
  .followbtn{color:#7FE4FF;border-color:#38C6FF !important;box-shadow:0 0 22px rgba(56,198,255,.55) !important;text-shadow:0 0 10px rgba(56,198,255,.5)}
  .followbtn.following,.btnwide,.composer .send,.agree-swap{box-shadow:0 0 30px rgba(56,198,255,.7) !important}
  .plan-chip,.acct-chip,.chat-gate,.room .runread{color:#7FE4FF;box-shadow:0 0 16px rgba(56,198,255,.6) !important}
  .plan.pop .ribbon{color:#04121a !important;background:#38C6FF !important;border-color:transparent !important}
  .msgrow.me .bubble{background:rgba(56,198,255,.16) !important;border-color:rgba(56,198,255,.55) !important}
  .acct-ic,.cta-chat .cc-arrow,.chat-locked .lockbig,.swap-pair .swapicon{color:#7FE4FF}
  .wh-btn.on.want{color:#7FE4FF !important;background:rgba(56,198,255,.13) !important;border-color:rgba(56,198,255,.55) !important}
  .toggle.on{box-shadow:0 0 14px rgba(56,198,255,.6)}
  .mv-sechead.up,.tdelta.up,.mvpct.up{text-shadow:0 0 14px rgba(44,245,208,.55)}
  .mv-sechead.down,.tdelta.down,.mvpct.down{text-shadow:0 0 14px rgba(255,77,143,.55)}
  .lightbox .lb-img{box-shadow:0 0 90px rgba(56,198,255,.65),0 40px 100px -20px rgba(0,0,0,.85)}
  @media (prefers-reduced-motion:reduce){.home-logo .mark{animation:none}}
</style>"""


def build(src: str) -> str:
    src = re.sub(r":root\{[^}]*\}", NEON_ROOT, src, count=1)
    src = src.replace("#E8B457", "#38C6FF")
    src = src.replace("</style>", NEON_FX, 1)
    return src


def main() -> None:
    out = build(SRC.read_text(encoding="utf-8"))
    OUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUT.name} ({len(out):,} bytes)")


if __name__ == "__main__":
    main()

"""Homepage screenshot provider.

iGaming sites are pop-up / age-gate / cookie-wall heavy, so the provider must
support cookie-banner removal and geo/proxy. This adapter targets ScreenshotOne
by default (Urlbox is a drop-in alternative) and falls back to a deterministic
placeholder image in mock mode so the PoC produces a real file with no key.

Screenshots are API-owned but refreshed FAR less often than traffic (on
discovery, then monthly / on-change).
"""
from __future__ import annotations

import hashlib
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import config

_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")
_JPEG_MAGIC = bytes.fromhex("ffd8ff")


def _fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AffswapBot)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _is_real_image(b: bytes) -> bool:
    """A genuine screenshot, not an async-loading placeholder (mShots returns an
    ~8KB GIF while it renders; real captures are large PNG/JPEG)."""
    return len(b) > 15_000 and (b[:8] == _PNG_MAGIC or b[:3] == _JPEG_MAGIC)


def _placeholder_png(domain: str, dest: Path) -> None:
    """Write a tiny, valid 1x1 PNG (bytes vary by domain hash for realism)."""
    # Minimal valid PNG (1x1). We tag the filename by domain; content is a
    # constant valid PNG so any image viewer opens it.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c6360000002000154a24f6e0000000049454e44ae426082"
    )
    dest.write_bytes(png)


class ScreenshotClient:
    def __init__(self):
        self.provider = config.SCREENSHOT_PROVIDER
        self.live = config.screenshot_is_live()

    def _screenshotone_url(self, url: str) -> str:
        params = {
            "access_key": config.SCREENSHOT_KEY,
            "url": url,
            "format": "png",
            "viewport_width": 1280,
            "viewport_height": 800,
            "block_cookie_banners": "true",
            "block_ads": "true",
            "block_banners_by_heuristics": "true",
            "cache": "true",
        }
        return "https://api.screenshotone.com/take?" + urllib.parse.urlencode(params)

    def _free_capture(self, url: str, dest: Path) -> str | None:
        """Keyless PoC capture. thum.io renders synchronously (reliable); mShots
        is an async fallback (warm, then poll a few times). Returns the provider
        name on success, or None to fall through to the placeholder."""
        try:
            b = _fetch(f"https://image.thum.io/get/width/1000/crop/750/noanimate/{url}")
            if _is_real_image(b):
                dest.write_bytes(b)
                return "thum.io"
        except Exception:
            pass
        mu = "https://s0.wp.com/mshots/v1/" + urllib.parse.quote(url, safe="") + "?w=1000"
        for _ in range(3):
            try:
                b = _fetch(mu)
                if _is_real_image(b):
                    dest.write_bytes(b)
                    return "mshots"
            except Exception:
                pass
            time.sleep(4)  # mShots renders in the background on first hit
        return None

    def capture(self, url: str, domain: str) -> dict:
        """Capture a homepage screenshot. Returns {image_ref, provider}."""
        config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        safe = domain.replace("/", "_")
        dest = config.SCREENSHOT_DIR / f"{safe}.png"

        if self.live and self.provider == "screenshotone":
            api_url = self._screenshotone_url(url)
            with urllib.request.urlopen(api_url, timeout=90) as resp:
                dest.write_bytes(resp.read())
            provider = "screenshotone"
        elif self.provider == "free":
            provider = self._free_capture(url, dest)
            if provider is None:
                _placeholder_png(domain, dest)
                provider = "mock"
        else:
            _placeholder_png(domain, dest)
            provider = "mock"
        return {"image_ref": str(dest), "provider": provider}

from __future__ import annotations

import re
from enum import Enum


class Wall(Enum):
    CLOUDFLARE = "cloudflare"
    CAPTCHA = "captcha"
    LOGIN = "login"


_CLOUDFLARE_PHRASES = (
    "performing security verification",
    "checking your browser",
    "ray id",
    "cloudflare",
    "attention required",
)

_CAPTCHA_PHRASES = (
    "recaptcha",
    "hcaptcha",
    "i'm not a robot",
    "verify you are human",
    "captcha challenge",
)

_LOGIN_PHRASES = (
    "sign in to continue",
    "you must be logged in",
    "login required",
    "please log in",
    "please sign in",
)

_LOGIN_PATH_RE = re.compile(r"/(login|signin|sign-in|log-in|auth/login)(/|$|\?)", re.IGNORECASE)


def detect_wall(*, text: str | None, url: str | None) -> Wall | None:
    """Classify a page as a known interstitial / blocking wall.

    Pure function over the page's visible innerText and current URL.
    Returns the wall kind, or None if the page looks normal.
    """
    t = (text or "").lower()
    u = (url or "").lower()

    if "__cf_chl_rt_tk=" in u:
        return Wall.CLOUDFLARE
    for phrase in _CLOUDFLARE_PHRASES:
        if phrase in t:
            return Wall.CLOUDFLARE

    for phrase in _CAPTCHA_PHRASES:
        if phrase in t:
            return Wall.CAPTCHA

    for phrase in _LOGIN_PHRASES:
        if phrase in t:
            return Wall.LOGIN
    if _LOGIN_PATH_RE.search(u):
        return Wall.LOGIN

    return None

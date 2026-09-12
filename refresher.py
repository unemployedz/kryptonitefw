"""
Roblox cookie refresher.
Handles CSRF token acquisition and cookie re-authentication.
"""

import re
import requests
from typing import Optional, Tuple

TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.roblox.com",
    "Referer": "https://www.roblox.com/",
}

# Roblox endpoints
CSRF_URL = "https://auth.roblox.com/v2/logout"
REFRESH_URL = (
    "https://www.roblox.com/authentication/"
    "signoutfromallsessionsandreauthenticate"
)
AUTH_CHECK_URL = "https://users.roblox.com/v1/users/authenticated"


def get_csrf_token(cookie: str, proxies: dict = None) -> Optional[str]:
    """
    Get an X-CSRF-Token from Roblox.
    This is required for authenticated POST requests.
    """
    try:
        s = requests.Session()
        s.trust_env = False
        s.cookies.set(".ROBLOSECURITY", cookie)
        r = s.post(CSRF_URL, headers=HEADERS, proxies=proxies, timeout=TIMEOUT)
        token = r.headers.get("x-csrf-token")
        return token
    except Exception:
        return None


def validate_cookie(cookie: str, proxies: dict = None) -> bool:
    """Check if a cookie is currently valid."""
    try:
        s = requests.Session()
        s.trust_env = False
        s.cookies.set(".ROBLOSECURITY", cookie)
        r = s.get(
            AUTH_CHECK_URL,
            headers=HEADERS,
            proxies=proxies,
            timeout=TIMEOUT,
        )
        return r.status_code == 200
    except Exception:
        return False


def refresh_cookie(
    old_cookie: str,
    proxies: dict = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Refresh a Roblox cookie.

    Returns:
        (new_cookie, error_message)
        new_cookie is None if refresh failed.
    """
    # Step 1: Validate the old cookie
    if not validate_cookie(old_cookie, proxies):
        return None, "Cookie is invalid or expired. Can't refresh a dead cookie."

    # Step 2: Get CSRF token
    csrf = get_csrf_token(old_cookie, proxies)
    if not csrf:
        return None, "Failed to get CSRF token. Cookie may be invalid or Roblox rejected the request."

    # Step 3: Hit the refresh endpoint
    try:
        s = requests.Session()
        s.trust_env = False
        s.cookies.set(".ROBLOSECURITY", old_cookie)

        headers = {**HEADERS, "X-CSRF-TOKEN": csrf}
        r = s.post(REFRESH_URL, headers=headers, proxies=proxies, timeout=TIMEOUT)

        if r.status_code != 200:
            return None, f"Refresh endpoint returned {r.status_code}"

        # Step 4: Extract new cookie from Set-Cookie header
        set_cookie = r.headers.get("set-cookie", "")
        match = re.search(
            r"\.ROBLOSECURITY=(.+?);\s*domain=\.roblox\.com",
            set_cookie,
        )
        if not match:
            # Try alternative pattern
            match = re.search(r"\.ROBLOSECURITY=([^;]+)", set_cookie)

        if match:
            new_cookie = match.group(1)
            return new_cookie, None
        else:
            return None, "Could not extract new cookie from response."

    except requests.RequestException as e:
        return None, f"Request failed: {str(e)[:100]}"

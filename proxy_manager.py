"""
Proxy manager: scrapes public proxy lists, validates them,
and provides rotation for the Roblox refresher.
"""

import os
import re
import time
import random
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import cycle

# ─── Config ───────────────────────────────────────────────
PROXY_FILE = "proxy.txt"
GOOD_FILE = "good_proxies.txt"
TIMEOUT = 8
CHECK_THREADS = 80

SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=elite",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/refs/heads/master/http.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/refs/heads/master/https.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]

AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

PROXY_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}")

# ─── Helpers ──────────────────────────────────────────────

def tidy(line: str) -> str:
    """Strip noise from a raw proxy line."""
    line = line.strip()
    if not line:
        return ""
    if "|" in line:
        line = line.split("|", 1)[0]
    if " " in line:
        line = line.split(" ", 1)[0]
    line = line.strip()
    if not line or ":" not in line:
        return ""
    parts = line.split(":")
    if len(parts) < 2:
        return ""
    ip, port = parts[0], parts[1]
    if not re.match(r"^(?:\d{1,3}\.){3}\d{1,3}$", ip):
        return ""
    if not port.isdigit() or not (0 < int(port) < 65536):
        return ""
    return line


def scrape_sources() -> list:
    """Pull proxies from all public sources."""
    bag = set()
    for url in SOURCES:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": random.choice(AGENTS)})
            r.raise_for_status()
            found = set(PROXY_RE.findall(r.text))
            bag.update(found)
        except Exception:
            continue
    return list(bag)


def load_proxies() -> list:
    """Load proxies from file, or scrape if empty."""
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r") as f:
            proxies = [l.strip() for l in f if l.strip()]
        if proxies:
            return proxies

    # Auto-scrape
    proxies = scrape_sources()
    with open(PROXY_FILE, "w") as f:
        f.write("\n".join(proxies))
    return proxies


def check_proxy(proxy: str) -> tuple:
    """Test a single proxy against a test URL."""
    test_url = "https://httpbin.org/ip"
    for scheme in ["http", "https"]:
        try:
            p = {"http": f"{scheme}://{proxy}", "https": f"{scheme}://{proxy}"}
            s = requests.Session()
            s.trust_env = False
            r = s.get(test_url, proxies=p, timeout=TIMEOUT)
            if r.status_code == 200:
                return (proxy, scheme)
        except Exception:
            continue
    return (proxy, "")


def validate_proxies(proxies: list, threads: int = CHECK_THREADS) -> list:
    """Check all proxies and return working ones."""
    working = []
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(check_proxy, p): p for p in proxies}
        for f in as_completed(futures):
            proxy, scheme = f.result()
            if scheme:
                working.append(proxy)
    # Save good ones
    with open(GOOD_FILE, "w") as f:
        f.write("\n".join(working))
    return working


class ProxyRotator:
    """Round-robin or random proxy rotation."""

    def __init__(self, proxies: list, mode: str = "round-robin"):
        self.proxies = proxies
        self.mode = mode
        self._cycle = cycle(proxies) if proxies else None
        self._lock = threading.Lock()

    def get(self) -> dict:
        """Get next proxy as a requests-compatible dict."""
        if not self.proxies:
            return {}
        with self._lock:
            if self.mode == "random":
                proxy = random.choice(self.proxies)
            else:
                proxy = next(self._cycle)
        return {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}",
        }

    def refresh(self):
        """Re-load proxies from file."""
        self.proxies = load_proxies()
        self._cycle = cycle(self.proxies) if self.proxies else None

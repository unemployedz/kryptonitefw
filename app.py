"""
Orvix — Roblox Cookie Refresher
Web interface for refreshing .ROBLOSECURITY cookies via proxy rotation.
"""

import os
import time
import threading
import traceback
from flask import Flask, render_template, request, jsonify

from proxy_manager import (
    load_proxies,
    validate_proxies,
    ProxyRotator,
    scrape_sources,
)
from refresher import refresh_cookie, validate_cookie

app = Flask(__name__)

# ─── Global state ─────────────────────────────────────────
rotator: ProxyRotator = None
proxy_pool: list = []
proxy_lock = threading.Lock()
start_time = time.time()

# ─── Bootstrap ────────────────────────────────────────────
def boot():
    """Load or scrape proxies on startup."""
    global rotator, proxy_pool
    proxies = load_proxies()
    if not proxies:
        print("[boot] No proxies found. Scraping...")
        proxies = scrape_sources()
        with open("proxy.txt", "w") as f:
            f.write("\n".join(proxies))
    proxy_pool = proxies
    rotator = ProxyRotator(proxies, mode="round-robin")
    print(f"[boot] Loaded {len(proxies)} proxies.")

boot()

# ─── Routes ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """
    Accept a cookie, try to refresh it through proxy rotation.
    Body: { "cookie": "..." }
    """
    data = request.get_json(force=True)
    cookie = (data.get("cookie") or "").strip()

    if not cookie:
        return jsonify({"ok": False, "error": "No cookie provided."}), 400

    if not cookie.startswith("_|WARNING"):
        return jsonify({
            "ok": False,
            "error": "That doesn't look like a valid .ROBLOSECURITY cookie.",
        }), 400

    attempts = []
    max_attempts = min(15, len(proxy_pool)) if proxy_pool else 5

    # Try with direct connection first (no proxy)
    new_cookie, err = refresh_cookie(cookie, proxies=None)
    if new_cookie:
        return jsonify({
            "ok": True,
            "cookie": new_cookie,
            "method": "direct",
            "attempts": 1,
        })

    attempts.append({"proxy": "direct", "error": err})

    # Try with proxies
    if rotator:
        for i in range(max_attempts):
            proxies = rotator.get()
            proxy_label = list(proxies.values())[0] if proxies else "none"
            try:
                new_cookie, err = refresh_cookie(cookie, proxies=proxies)
                if new_cookie:
                    return jsonify({
                        "ok": True,
                        "cookie": new_cookie,
                        "method": "proxy",
                        "proxy": proxy_label,
                        "attempts": i + 2,
                    })
                attempts.append({"proxy": proxy_label, "error": err})
            except Exception as e:
                attempts.append({"proxy": proxy_label, "error": str(e)[:80]})

    return jsonify({
        "ok": False,
        "error": "All refresh attempts failed. Cookie may be invalid or proxies are dead.",
        "attempts": attempts[-10:],  # last 10 for debugging
    }), 502


@app.route("/api/validate", methods=["POST"])
def api_validate():
    """Quick cookie validity check."""
    data = request.get_json(force=True)
    cookie = (data.get("cookie") or "").strip()
    if not cookie:
        return jsonify({"ok": False, "error": "No cookie."}), 400

    valid = validate_cookie(cookie)
    return jsonify({"ok": True, "valid": valid})


@app.route("/api/proxies", methods=["GET"])
def api_proxies():
    """Return proxy pool stats."""
    return jsonify({
        "total": len(proxy_pool),
        "sample": proxy_pool[:5],
    })


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Force a fresh scrape + validation."""
    global rotator, proxy_pool

    def _scrape():
        global rotator, proxy_pool
        raw = scrape_sources()
        good = validate_proxies(raw)
        with proxy_lock:
            proxy_pool = good if good else raw
            rotator = ProxyRotator(proxy_pool, mode="round-robin")
        print(f"[scrape] {len(good)} working proxies out of {len(raw)} raw.")

    thread = threading.Thread(target=_scrape, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "Scrape started in background."})


@app.route("/health")
def health():
    return jsonify({
        "status": "alive",
        "uptime": time.time() - start_time,
        "proxies": len(proxy_pool),
    })


# ─── Main ─────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

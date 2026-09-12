"""
Orvix — Roblox Cookie Refresher
Web interface for refreshing .ROBLOSECURITY cookies via proxy rotation.
"""

import os
import signal
import time
import threading
from contextlib import contextmanager

from flask import Flask, render_template, request, jsonify

from proxy_manager import (
    load_proxies,
    validate_proxies,
    ProxyRotator,
    scrape_sources,
)
from refresher import refresh_cookie, validate_cookie

app = Flask(__name__)

rotator: ProxyRotator = None
proxy_pool: list = []
proxy_lock = threading.Lock()
start_time = time.time()


def boot():
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


class RequestTimeout(Exception):
    pass


@contextmanager
def time_limit(seconds: int):
    """Hard wall-clock timeout via SIGALRM. Main-thread only."""
    def handler(signum, frame):
        raise RequestTimeout(f"timed out after {seconds}s")

    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    data = request.get_json(force=True) or {}
    cookie = (data.get("cookie") or "").strip()

    if not cookie:
        return jsonify({"ok": False, "error": "No cookie provided."}), 400

    if not cookie.startswith("_|WARNING"):
        return jsonify({
            "ok": False,
            "error": "That doesn't look like a valid .ROBLOSECURITY cookie.",
        }), 400

    attempts = []

    try:
        with time_limit(240):
            # Attempt 1: direct
            try:
                new_cookie, err = refresh_cookie(cookie, proxies=None)
                if new_cookie:
                    return jsonify({
                        "ok": True,
                        "cookie": new_cookie,
                        "method": "direct",
                        "attempts": 1,
                    })
                attempts.append({"proxy": "direct", "error": err or "unknown"})
            except Exception as e:
                attempts.append({"proxy": "direct", "error": str(e)[:80]})

            # Attempts 2..N: through proxies
            if rotator and proxy_pool:
                max_attempts = min(8, len(proxy_pool))
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
                        attempts.append({
                            "proxy": proxy_label,
                            "error": err or "unknown",
                        })
                    except Exception as e:
                        attempts.append({
                            "proxy": proxy_label,
                            "error": str(e)[:80],
                        })

    except RequestTimeout as e:
        return jsonify({
            "ok": False,
            "error": f"Refresh aborted: {str(e)}",
            "attempts": attempts[-8:],
        }), 504
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"Internal error: {str(e)[:120]}",
            "attempts": attempts[-8:],
        }), 500

    return jsonify({
        "ok": False,
        "error": "All refresh attempts failed. Cookie may be invalid or proxies are dead.",
        "attempts": attempts[-8:],
    }), 502


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json(force=True) or {}
    cookie = (data.get("cookie") or "").strip()
    if not cookie:
        return jsonify({"ok": False, "error": "No cookie."}), 400

    try:
        with time_limit(30):
            valid = validate_cookie(cookie)
        return jsonify({"ok": True, "valid": valid})
    except RequestTimeout:
        return jsonify({"ok": False, "error": "Validation timed out."}), 504


@app.route("/api/proxies", methods=["GET"])
def api_proxies():
    return jsonify({
        "total": len(proxy_pool),
        "sample": proxy_pool[:5],
    })


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    def _scrape():
        global rotator, proxy_pool
        try:
            raw = scrape_sources()
            good = validate_proxies(raw)
            final = good if good else raw
            with proxy_lock:
                proxy_pool = final
                rotator = ProxyRotator(proxy_pool, mode="round-robin")
            print(f"[scrape] {len(good)} working / {len(raw)} raw.")
        except Exception as e:
            print(f"[scrape] failed: {e}")

    threading.Thread(target=_scrape, daemon=True).start()
    return jsonify({"ok": True, "message": "Scrape started in background."})


@app.route("/health")
def health():
    return jsonify({
        "status": "alive",
        "uptime": time.time() - start_time,
        "proxies": len(proxy_pool),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)

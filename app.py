#!/usr/bin/env python3
"""NT radaras jūsų kompiuteryje.

Paleidžia puslapį http://localhost:8765 ir kas `interval_minutes` tikrina
portalus. Apie naujus skelbimus praneša Mac pranešimu.

    python3 app.py            # paleisti
    python3 app.py --no-open  # neatidaryti naršyklės (taip paleidžia automatinis paleidimas)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

import monitor as m

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
DATA = DOCS / "listings.json"
CONFIG = HERE / "config.yaml"
PORT = 8765
log = logging.getLogger("nt-monitor")

FILTER_KEYS = ["price_min", "price_max", "area_min", "area_max", "rooms_min", "rooms_max",
               "price_per_m2_max", "include_keywords", "exclude_keywords", "keep_if_unknown"]


class DesktopNotifier:
    """Mac pranešimai (osascript). Kitose sistemose tik išveda į konsolę."""
    enabled = True

    def __init__(self):
        self.items: list[str] = []

    def send(self, text: str) -> bool:
        self.items.append(text)
        return True

    def flush(self) -> int:
        n = len(self.items)
        shown = self.items if n <= 4 else self.items[:3]
        for text in shown:
            lines = [l for l in re.sub(r"</?b>", "", text).splitlines() if l.strip()]
            # 1 eilutė: antraštė, 2: pavadinimas, 3: kaina ir plotas, paskutinė: nuoroda
            title = lines[0] if lines else "NT radaras"
            body = " · ".join(l for l in lines[1:] if not l.startswith("http"))
            notify(title, body)
        if n > 4:
            notify("NT radaras", f"Ir dar {n - 3} nauji skelbimai. Atidarykite NT radarą.")
        self.items.clear()
        return n


def notify(title: str, body: str):
    unescape = lambda s: s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    title, body = unescape(title), unescape(body)
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", "on run argv",
                            "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
                            "-e", "end run", title, body[:250]], check=False, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Nepavyko parodyti pranešimo: %s", e)
    else:
        print(f"[pranešimas] {title}: {body}")


# --------------------------------------------------------------------------
# Nustatymai
# --------------------------------------------------------------------------
def read_config() -> dict:
    try:
        return m.load_config(CONFIG)
    except FileNotFoundError:
        return {}


def public_config(cfg: dict) -> dict:
    return {
        "interval_minutes": int(cfg.get("interval_minutes", 15)),
        "notify_price_drops": bool(cfg.get("notify_price_drops", True)),
        "filters": {k: (cfg.get("filters") or {}).get(k) for k in FILTER_KEYS},
        "searches": [{"name": s.get("name", ""), "url": s.get("url", ""),
                      "enabled": s.get("enabled", True) is not False}
                     for s in cfg.get("searches", [])],
    }


def _num(v):
    if v in (None, ""):
        return None
    return float(v) if "." in str(v) else int(v)


def save_config(new: dict) -> dict:
    cfg = read_config()
    old_searches = {s.get("url"): s for s in cfg.get("searches", [])}
    searches = []
    for s in new.get("searches", []):
        url = str(s.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Netinkama nuoroda: {url or '(tuščia)'}")
        item = dict(old_searches.get(url, {}))   # išlaikom papildomus laukus (pages, listing_pattern...)
        item.update(name=str(s.get("name", "")).strip() or m.site_for(url), url=url,
                    enabled=bool(s.get("enabled", True)))
        searches.append(item)
    f = new.get("filters", {})
    filters = {}
    for k in FILTER_KEYS:
        v = f.get(k)
        if k.endswith("_keywords"):
            if isinstance(v, str):
                v = [w.strip() for w in v.split(",")]
            filters[k] = [w for w in (v or []) if w]
        elif k == "keep_if_unknown":
            filters[k] = bool(v) if v is not None else True
        else:
            filters[k] = _num(v)
    cfg.update(filters=filters, searches=searches,
               interval_minutes=max(5, int(new.get("interval_minutes", 15))),
               notify_price_drops=bool(new.get("notify_price_drops", True)))
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text("# Šį failą keičia NT radaro „Nustatymai“ langas.\n"
                   + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(CONFIG)
    return public_config(cfg)


# --------------------------------------------------------------------------
# Tikrinimas fone
# --------------------------------------------------------------------------
class Checker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.running = False
        self.last_error: str | None = None
        self.notifier = DesktopNotifier()
        self.fetcher = None

    def check_now(self):
        self.wake.set()

    def run(self):
        while True:
            cfg = read_config()
            if self.fetcher is None:
                self.fetcher = m.Fetcher(cfg.get("user_agent", m.DEFAULT_UA),
                                         cfg.get("respect_robots_txt", True))
            self.fetcher.respect_robots = cfg.get("respect_robots_txt", True)
            self.running = True
            try:
                with self.lock:
                    n = m.run_once(cfg, m.JsonStore(DATA), self.fetcher, self.notifier)
                log.info("Patikrinimas baigtas, naujų skelbimų: %d", n)
                self.last_error = None
            except Exception as e:      # tęsiam, kad viena klaida nesustabdytų radaro
                log.exception("Klaida tikrinant")
                self.last_error = str(e)
            finally:
                self.running = False
            self.wake.wait(max(5, int(cfg.get("interval_minutes", 15))) * 60)
            self.wake.clear()


class Handler(SimpleHTTPRequestHandler):
    checker: Checker

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if self.path.split("?")[0].endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/config":
            return self._json(public_config(read_config()))
        if path == "/api/status":
            return self._json({"running": self.checker.running, "error": self.checker.last_error})
        if path == "/listings.json" and not DATA.exists():
            return self._json({"listings": {}, "searches": {}})
        return super().do_GET()

    def do_POST(self):
        # tik iš šio kompiuterio puslapio
        origin = self.headers.get("Origin", "")
        if origin and origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"):
            return self._json({"error": "draudžiama"}, 403)
        path = self.path.split("?")[0]
        if path == "/api/run":
            self.checker.check_now()
            return self._json({"ok": True})
        if path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(min(length, 200_000)) or b"{}")
                cfg = save_config(data)
            except (ValueError, json.JSONDecodeError) as e:
                return self._json({"error": str(e)}, 400)
            self.checker.check_now()
            return self._json(cfg)
        return self._json({"error": "nerasta"}, 404)


def main():
    ap = argparse.ArgumentParser(description="NT radaras")
    ap.add_argument("--no-open", action="store_true", help="neatidaryti naršyklės")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    checker = Checker()
    Handler.checker = checker
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(DOCS)))
    except OSError:
        # jau veikia (pvz. paleistas automatiškai) — tiesiog atidarom puslapį
        print(f"NT radaras jau veikia: http://localhost:{args.port}")
        if not args.no_open:
            webbrowser.open(f"http://localhost:{args.port}")
        return
    checker.start()
    url = f"http://localhost:{args.port}"
    print(f"NT radaras veikia: {url}  (sustabdyti: Ctrl+C)")
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""NT skelbimų stebėtojas.

Periodiškai atsisiunčia jūsų išsaugotas paieškas NT portaluose (aruodas.lt,
skelbiu.lt, alio.lt, domoplius.lt, realu.lt ir kt.), atrenka skelbimus pagal
filtrus config.yaml faile ir apie naujus praneša per Telegram.

Paleidimas:
    python monitor.py               # vienas patikrinimas (tinka cron / Task Scheduler)
    python monitor.py --loop        # tikrina be perstojo kas `interval_minutes`
    python monitor.py --test-telegram
    python monitor.py --telegram-chat-id
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import requests
import yaml
from bs4 import BeautifulSoup, Tag

HERE = Path(__file__).resolve().parent
DEFAULT_UA = "nt-monitor/1.0 (asmeninis NT skelbimu stebejimas)"
log = logging.getLogger("nt-monitor")

# --------------------------------------------------------------------------
# Portalai: kaip atpažinti skelbimo nuorodą tarp visų puslapio nuorodų.
# Remiamės nuorodų formatu, o ne HTML klasėmis, nes jos keičiasi dažniau.
# Jei portalas pakeis nuorodų formatą, užtenka pataisyti regex čia arba
# config.yaml lauke `listing_pattern`.
# --------------------------------------------------------------------------
SITES = {
    # https://www.aruodas.lt/butai-vilniuje-...-parduodamas-2-kambariu-butas-1-3456789/
    "aruodas.lt": r"aruodas\.lt/[a-z0-9-]+-(\d-\d{5,})/?$",
    # https://www.skelbiu.lt/skelbimai/...-12345678.html
    "skelbiu.lt": r"skelbiu\.lt/skelbimai/[^?#]*?-(\d{6,})\.html",
    # https://www.alio.lt/nekilnojamas-turtas/butai/...ID12345678.html
    "alio.lt": r"alio\.lt/.+?ID(\d{5,})\.html",
    # https://domoplius.lt/skelbimai/...-1234567.html
    "domoplius.lt": r"domoplius\.lt/skelbimai/[^?#]*?-(\d{5,})\.html",
    # realu.lt: skelbimo puslapiai turi skaitinį ID nuorodoje
    "realu.lt": r"realu\.lt/[^?#]*?(\d{4,})[^/?#]*/?$",
}


@dataclass
class Listing:
    site: str
    listing_id: str
    url: str
    title: str
    text: str
    price: float | None = None
    area: float | None = None
    rooms: int | None = None
    search_name: str = ""

    @property
    def key(self) -> str:
        return f"{self.site}:{self.listing_id}"


@dataclass
class Filters:
    price_min: float | None = None
    price_max: float | None = None
    area_min: float | None = None
    area_max: float | None = None
    rooms_min: int | None = None
    rooms_max: int | None = None
    price_per_m2_max: float | None = None
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    keep_if_unknown: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "Filters":
        d = d or {}
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def merged(self, override: dict | None) -> "Filters":
        base = dict(self.__dict__)
        for k, v in (override or {}).items():
            if k in base:
                base[k] = v
        return Filters(**base)


# --------------------------------------------------------------------------
# Teksto analizė: kaina, plotas, kambariai
# --------------------------------------------------------------------------
_NUM = r"(\d{1,3}(?:[   .]\d{3})+|\d+)(?:[.,](\d+))?"
PRICE_RE = re.compile(_NUM + r"\s*(?:€|eur\b|eurų\b)", re.I)
AREA_RE = re.compile(_NUM + r"\s*(?:m²|m2|kv\.?\s*m)", re.I)
ROOMS_RE = re.compile(r"(\d{1,2})\s*(?:-?\s*(?:kamb|k\.\s*b|kambar))", re.I)
PER_M2_RE = re.compile(_NUM + r"\s*(?:€|eur)\s*/\s*m", re.I)


def _to_float(whole: str, frac: str | None) -> float:
    whole = re.sub(r"[   .]", "", whole)
    return float(f"{whole}.{frac}") if frac else float(whole)


def parse_price(text: str) -> float | None:
    # išmetam kainas už m², kad jų nepalaikytume pilna kaina
    cleaned = PER_M2_RE.sub(" ", text)
    values = [_to_float(m.group(1), m.group(2)) for m in PRICE_RE.finditer(cleaned)]
    return max(values) if values else None


def parse_area(text: str) -> float | None:
    m = AREA_RE.search(text)
    return _to_float(m.group(1), m.group(2)) if m else None


def parse_rooms(text: str) -> int | None:
    m = ROOMS_RE.search(text)
    return int(m.group(1)) if m else None


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# Puslapio išnagrinėjimas
# --------------------------------------------------------------------------
def site_for(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for name in SITES:
        if host == name or host.endswith("." + name):
            return name
    return host.removeprefix("www.")


def _card_for(a: Tag, pattern: re.Pattern, base_url: str, max_up: int = 6) -> Tag:
    """Kyla DOM medžiu nuo nuorodos, kol randa didžiausią elementą, kuriame
    yra tik vienas skelbimas — tai ir yra skelbimo kortelė."""
    own = pattern.search(urljoin(base_url, a.get("href", "")))
    own_id = own.group(1) if own else None
    card = a
    node = a
    for _ in range(max_up):
        parent = node.parent
        if not isinstance(parent, Tag) or parent.name in ("body", "html"):
            break
        ids = set()
        for link in parent.find_all("a", href=True):
            m = pattern.search(urljoin(base_url, link["href"]))
            if m:
                ids.add(m.group(1))
        if len(ids) > 1 or (own_id and ids and own_id not in ids):
            break
        card = parent
        node = parent
    return card


def extract_listings(html: str, page_url: str, pattern_src: str | None = None,
                     search_name: str = "") -> list[Listing]:
    site = site_for(page_url)
    pattern = re.compile(pattern_src or SITES.get(site, r"/(\d{6,})(?:\.html|/)?$"), re.I)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    found: dict[str, Listing] = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0]
        m = pattern.search(url)
        if not m:
            continue
        lid = m.group(1)
        card = _card_for(a, pattern, page_url)
        text = norm(card.get_text(" "))
        title = norm(a.get_text(" ")) or norm(a.get("title", ""))
        if lid in found:
            # ta pati kortelė gali turėti kelias nuorodas (nuotrauka + pavadinimas)
            prev = found[lid]
            if len(title) > len(prev.title):
                prev.title = title
            if len(text) > len(prev.text):
                prev.text = text
            continue
        found[lid] = Listing(site=site, listing_id=lid, url=url, title=title,
                             text=text, search_name=search_name)

    for lst in found.values():
        lst.price = parse_price(lst.text)
        lst.area = parse_area(lst.text)
        lst.rooms = parse_rooms(lst.text) or parse_rooms(lst.url.replace("-", " "))
        if not lst.title:
            lst.title = lst.text[:120]
    return list(found.values())


# --------------------------------------------------------------------------
# Filtrai
# --------------------------------------------------------------------------
def passes(lst: Listing, f: Filters) -> tuple[bool, str]:
    def rng(value, lo, hi, label):
        if value is None:
            if (lo is not None or hi is not None) and not f.keep_if_unknown:
                return f"nežinomas {label}"
            return None
        if lo is not None and value < lo:
            return f"{label} {value:g} < {lo:g}"
        if hi is not None and value > hi:
            return f"{label} {value:g} > {hi:g}"
        return None

    for reason in (rng(lst.price, f.price_min, f.price_max, "kaina"),
                   rng(lst.area, f.area_min, f.area_max, "plotas"),
                   rng(lst.rooms, f.rooms_min, f.rooms_max, "kambariai")):
        if reason:
            return False, reason

    if f.price_per_m2_max and lst.price and lst.area:
        if lst.price / lst.area > f.price_per_m2_max:
            return False, "per brangu už m²"

    hay = f"{lst.title} {lst.text} {lst.url}".lower()
    if f.include_keywords and not any(k.lower() in hay for k in f.include_keywords):
        return False, "nėra raktinių žodžių"
    for k in f.exclude_keywords:
        if k.lower() in hay:
            return False, f"draudžiamas žodis „{k}“"
    return True, ""


# --------------------------------------------------------------------------
# Atmintis (SQLite)
# --------------------------------------------------------------------------
class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS seen (
            key TEXT PRIMARY KEY, site TEXT, url TEXT, title TEXT,
            price REAL, first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP, notified INTEGER DEFAULT 0)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS searches (
            search_hash TEXT PRIMARY KEY, initialized_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        self.db.commit()

    def is_initialized(self, search_hash: str) -> bool:
        return self.db.execute("SELECT 1 FROM searches WHERE search_hash=?",
                               (search_hash,)).fetchone() is not None

    def mark_initialized(self, search_hash: str):
        self.db.execute("INSERT OR IGNORE INTO searches(search_hash) VALUES (?)", (search_hash,))
        self.db.commit()

    def get(self, key: str):
        return self.db.execute("SELECT price FROM seen WHERE key=?", (key,)).fetchone()

    def set_search_status(self, name: str, url: str, found: int, error: str | None):
        pass

    def save(self):
        pass

    def upsert(self, lst: Listing, notified: bool, matches: bool = True):
        self.db.execute("""INSERT INTO seen(key, site, url, title, price, notified)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET last_seen=CURRENT_TIMESTAMP, price=excluded.price,
                notified=MAX(notified, excluded.notified)""",
                        (lst.key, lst.site, lst.url, lst.title, lst.price, int(notified)))
        self.db.commit()


class JsonStore:
    """Atmintis viename JSON faile. Tą patį failą skaito web puslapis (docs/)."""
    MAX_LISTINGS = 1500

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
        self.data.setdefault("listings", {})
        self.data.setdefault("initialized", [])
        self.data["searches"] = {}

    def is_initialized(self, search_hash: str) -> bool:
        return search_hash in self.data["initialized"]

    def mark_initialized(self, search_hash: str):
        if search_hash not in self.data["initialized"]:
            self.data["initialized"].append(search_hash)

    def get(self, key: str):
        rec = self.data["listings"].get(key)
        return (rec["price"],) if rec else None

    def upsert(self, lst: Listing, notified: bool, matches: bool = True):
        now = _now()
        rec = self.data["listings"].get(lst.key)
        if rec is None:
            rec = {"site": lst.site, "id": lst.listing_id, "first_seen": now,
                   "price_history": []}
            self.data["listings"][lst.key] = rec
        if lst.price and (not rec["price_history"] or rec["price_history"][-1][1] != lst.price):
            rec["price_history"].append([now, lst.price])
        rec.update(url=lst.url, title=lst.title[:300], price=lst.price, area=lst.area,
                   rooms=lst.rooms, search=lst.search_name, last_seen=now,
                   matches=matches, text=lst.text[:400])

    def set_search_status(self, name: str, url: str, found: int, error: str | None):
        self.data["searches"][name or url] = {"url": url, "site": site_for(url),
                                              "found": found, "error": error}

    def save(self):
        items = sorted(self.data["listings"].items(), key=lambda kv: kv[1]["last_seen"],
                       reverse=True)[: self.MAX_LISTINGS]
        self.data["listings"] = dict(items)
        self.data["updated_at"] = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# HTTP su robots.txt ir mandagiu tempu
# --------------------------------------------------------------------------
class Fetcher:
    def __init__(self, user_agent: str, respect_robots: bool = True,
                 delay_range: tuple[float, float] = (4, 9)):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent,
                               "Accept-Language": "lt-LT,lt;q=0.9,en;q=0.6"})
        self.ua = user_agent
        self.respect_robots = respect_robots
        self.delay_range = delay_range
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0
        self.last_error: str | None = None

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.s.get(root + "/robots.txt", timeout=20)
                if r.status_code >= 400:
                    rp = None  # robots.txt nėra — leidžiama
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException as e:
                log.warning("Nepavyko gauti %s/robots.txt: %s", root, e)
                rp = None
            self._robots[root] = rp
        rp = self._robots[root]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get(self, url: str) -> str | None:
        wait = random.uniform(*self.delay_range) - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()
        self.last_error = None
        try:
            r = self.s.get(url, timeout=30)
        except requests.RequestException as e:
            log.warning("Klaida siunčiant %s: %s", url, e)
            self.last_error = "nepavyko prisijungti"
            return None
        if r.status_code != 200:
            hint = " (portalas blokuoja automatines užklausas)" if r.status_code in (403, 429, 503) else ""
            log.warning("%s grąžino HTTP %s%s", url, r.status_code, hint)
            self.last_error = f"HTTP {r.status_code}{hint}"
            return None
        return r.text


# --------------------------------------------------------------------------
# Pranešimai
# --------------------------------------------------------------------------
class Telegram:
    def __init__(self, token: str | None, chat_id: str | None):
        self.token, self.chat_id = token, chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text,
                                    "parse_mode": "HTML", "disable_web_page_preview": False},
                              timeout=20)
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
                return self.send(text)
            if not r.ok:
                log.error("Telegram klaida: %s", r.text[:300])
            return r.ok
        except requests.RequestException as e:
            log.error("Telegram klaida: %s", e)
            return False


class GitHubIssueNotifier:
    """Sukuria vieną GitHub „issue“ su visais naujais skelbimais ir pažymi
    repozitorijos savininką — GitHub išsiunčia jam el. laišką ir push pranešimą."""

    def __init__(self, token: str, repo: str, owner: str):
        self.token, self.repo, self.owner = token, repo, owner
        self.items: list[str] = []
        self.enabled = True

    def send(self, text: str) -> bool:
        md = re.sub(r"</?b>", "**", text)
        md = md.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        self.items.append(md)
        return True

    def flush(self) -> int:
        n = len(self.items)
        if not n:
            return 0
        owner, name = self.repo.split("/", 1)
        page = f"https://{owner.lower()}.github.io/{name}/"
        body = (f"@{self.owner} {n} nauji skelbimai. Visi skelbimai: {page}\n\n"
                + "\n\n---\n\n".join(self.items))
        title = f"🏠 {n} nauji NT skelbimai" if n > 1 else "🏠 Naujas NT skelbimas"
        try:
            r = requests.post(f"https://api.github.com/repos/{self.repo}/issues",
                              headers={"Authorization": f"Bearer {self.token}",
                                       "Accept": "application/vnd.github+json"},
                              json={"title": title, "body": body[:60000], "labels": ["skelbimai"]},
                              timeout=30)
            if not r.ok:
                log.error("Nepavyko sukurti GitHub pranešimo: %s", r.text[:300])
                return 0
        except requests.RequestException as e:
            log.error("Nepavyko sukurti GitHub pranešimo: %s", e)
            return 0
        self.items.clear()
        return n


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(lst: Listing, price_drop_from: float | None = None) -> str:
    parts = []
    if lst.price:
        p = f"{lst.price:,.0f} €".replace(",", " ")
        if price_drop_from:
            p += f" (buvo {price_drop_from:,.0f} €)".replace(",", " ")
        parts.append(p)
    if lst.area:
        parts.append(f"{lst.area:g} m²")
    if lst.rooms:
        parts.append(f"{lst.rooms} kamb.")
    if lst.price and lst.area:
        parts.append(f"{lst.price / lst.area:,.0f} €/m²".replace(",", " "))
    head = "📉 Kaina sumažėjo" if price_drop_from else "🏠 Naujas skelbimas"
    return (f"<b>{head}</b> · {_html_escape(lst.site)}"
            + (f" · {_html_escape(lst.search_name)}" if lst.search_name else "")
            + f"\n{_html_escape(lst.title[:200])}\n"
            + (" · ".join(parts) + "\n" if parts else "")
            + lst.url)


# --------------------------------------------------------------------------
# Pagrindinė logika
# --------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def make_telegram(cfg: dict) -> Telegram:
    tg = cfg.get("telegram") or {}
    return Telegram(os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token"),
                    os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id"))


def run_once(cfg: dict, store: Store, fetcher: Fetcher, tg: Telegram) -> int:
    global_filters = Filters.from_dict(cfg.get("filters"))
    notify_drops = cfg.get("notify_price_drops", True)
    sent = 0
    for search in cfg.get("searches", []):
        if search.get("enabled", True) is False:
            continue
        name = search.get("name", "")
        url = search["url"]
        filters = global_filters.merged(search.get("filters"))
        pages = int(search.get("pages", 1))
        shash = hashlib.sha1(url.encode()).hexdigest()[:16]
        first_run = not store.is_initialized(shash)

        listings: list[Listing] = []
        error = None
        for page_url in page_urls(url, pages, search.get("page_param")):
            if not fetcher.allowed(page_url):
                log.warning("[%s] robots.txt neleidžia automatiškai tikrinti %s — praleidžiu. "
                            "Pabandykite kitą paieškos nuorodą (pvz. be paieškos frazės).",
                            name, page_url)
                error = error or "robots.txt draudžia šią nuorodą"
                break
            html = fetcher.get(page_url)
            if html is None:
                error = error or getattr(fetcher, "last_error", None) or "nepavyko atsisiųsti"
                break
            got = extract_listings(html, page_url, search.get("listing_pattern"), name)
            if not got:
                break
            listings.extend(got)

        log.info("[%s] rasta %d skelbimų%s", name or url, len(listings),
                 " (pirmas paleidimas — tik įsimenu, nepranešu)" if first_run else "")
        if not listings and first_run:
            log.warning("[%s] nerasta nė vieno skelbimo. Patikrinkite nuorodą arba "
                        "ar portalas neblokuoja užklausų.", name or url)

        for lst in listings:
            prev = store.get(lst.key)
            ok, why = passes(lst, filters)
            notify = False
            drop_from = None
            if ok and not first_run:
                if prev is None:
                    notify = True
                elif (notify_drops and prev[0] and lst.price
                      and lst.price < prev[0] * 0.99):
                    notify, drop_from = True, prev[0]
            if notify:
                msg = format_message(lst, drop_from)
                print("\n" + re.sub(r"</?b>", "", msg) + "\n")
                if tg.send(msg):
                    sent += 1
                    time.sleep(0.5)
            elif not ok and prev is None and not first_run:
                log.debug("Atmesta %s: %s", lst.url, why)
            store.upsert(lst, notify, ok)

        if not listings and not error:
            error = "nerasta skelbimų (gal pasikeitė portalo puslapis?)"
        store.set_search_status(name, url, len(listings), error)
        if listings:
            store.mark_initialized(shash)
    if hasattr(tg, "flush"):
        sent = tg.flush()
    store.save()
    return sent


def page_urls(url: str, pages: int, page_param: str | None):
    yield url
    if pages <= 1:
        return
    site = site_for(url)
    for n in range(2, pages + 1):
        if page_param:
            sep = "&" if "?" in url else "?"
            yield f"{url}{sep}{page_param}={n}"
        elif site == "aruodas.lt":
            # aruodas: /butai/vilniuje/puslapis/2/?...
            p = urlparse(url)
            path = p.path.rstrip("/") + f"/puslapis/{n}/"
            yield p._replace(path=path).geturl()
        else:
            sep = "&" if "?" in url else "?"
            yield f"{url}{sep}page={n}"


def telegram_chat_id(cfg: dict):
    tg = make_telegram(cfg)
    if not tg.token:
        sys.exit("Nustatykite TELEGRAM_BOT_TOKEN (žr. README).")
    r = requests.get(f"https://api.telegram.org/bot{tg.token}/getUpdates", timeout=20).json()
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
             for u in r.get("result", []) if "message" in u}
    if not chats:
        print("Nerasta žinučių. Parašykite savo botui bet ką Telegram'e ir paleiskite dar kartą.")
    for cid, nm in chats.items():
        print(f"chat_id: {cid}  ({nm})")


def main():
    ap = argparse.ArgumentParser(description="NT skelbimų stebėtojas")
    ap.add_argument("-c", "--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--loop", action="store_true", help="tikrinti be perstojo")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--telegram-chat-id", action="store_true")
    ap.add_argument("--json-store", help="saugoti duomenis JSON faile (web puslapiui)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(Path(args.config))

    if args.telegram_chat_id:
        return telegram_chat_id(cfg)
    tg = make_telegram(cfg)
    if args.test_telegram:
        print("Išsiųsta ✅" if tg.send("✅ NT stebėtojas prijungtas") else
              "Nepavyko — patikrinkite TELEGRAM_BOT_TOKEN ir TELEGRAM_CHAT_ID")
        return
    if not tg.enabled and os.environ.get("GITHUB_ACTIONS") and os.environ.get("GITHUB_TOKEN"):
        tg = GitHubIssueNotifier(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"],
                                 os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    elif not tg.enabled:
        log.warning("Pranešimai nenustatyti — nauji skelbimai bus tik rodomi ekrane.")

    db_path = Path(cfg.get("database", HERE / "seen.sqlite3"))
    if not db_path.is_absolute():
        db_path = HERE / db_path
    store = JsonStore(Path(args.json_store)) if args.json_store else Store(db_path)
    fetcher = Fetcher(cfg.get("user_agent", DEFAULT_UA),
                      cfg.get("respect_robots_txt", True))

    interval = max(5, int(cfg.get("interval_minutes", 15)))
    while True:
        try:
            n = run_once(cfg, store, fetcher, tg)
            log.info("Patikrinimas baigtas, išsiųsta pranešimų: %d", n)
        except Exception:
            log.exception("Netikėta klaida")
            if not args.loop:
                raise
        if not args.loop:
            break
        # šiek tiek atsitiktinumo, kad užklausos neitų tiksliai tuo pačiu metu
        time.sleep(interval * 60 + random.uniform(0, 60))


if __name__ == "__main__":
    main()

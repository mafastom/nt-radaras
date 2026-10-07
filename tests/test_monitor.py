"""Testai be interneto: python -m pytest tests  (arba python tests/test_monitor.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor as m

ARUODAS = """
<html><body><div class="list">
 <div class="row">
  <a href="/butai-vilniuje-naujamiestyje-parduodamas-2-kambariu-butas-1-3456789/"><img></a>
  <h3><a href="/butai-vilniuje-naujamiestyje-parduodamas-2-kambariu-butas-1-3456789/">Vilnius, Naujamiestis, Naugarduko g.</a></h3>
  <span>155 000 €</span><span>2 980 €/m²</span><span>2</span><span>52,1 m²</span>
 </div>
 <div class="row">
  <h3><a href="https://www.aruodas.lt/butai-vilniuje-zirmunuose-parduodamas-4-kambariu-butas-1-3456790/">Vilnius, Žirmūnai</a></h3>
  <span>250 000 €</span><span>4 kamb.</span><span>90 m²</span>
 </div>
 <a href="/butai/vilniuje/">Visi butai</a>
</div></body></html>"""

SKELBIU = """
<div><div class="item"><a href="/skelbimai/parduodamas-3-kamb-butas-kaune-87654321.html">3 kamb. butas Kaune</a>
<p>Kaunas, Šilainiai · 68 m² · 119.000 €</p></div>
<div class="item"><a href="/skelbimai/dalis-buto-87654322.html">Parduodama dalis buto</a><p>40 000 €, 45 m2</p></div></div>"""


def test_aruodas_parse():
    ls = m.extract_listings(ARUODAS, "https://www.aruodas.lt/butai/vilniuje/")
    assert len(ls) == 2, ls
    a = {l.listing_id: l for l in ls}["1-3456789"]
    assert a.price == 155000 and a.area == 52.1 and a.rooms == 2, a
    b = {l.listing_id: l for l in ls}["1-3456790"]
    assert b.price == 250000 and b.area == 90 and b.rooms == 4


def test_skelbiu_and_filters():
    ls = m.extract_listings(SKELBIU, "https://www.skelbiu.lt/skelbimai/nekilnojamasis-turtas/")
    assert len(ls) == 2
    first = [l for l in ls if l.listing_id == "87654321"][0]
    assert (first.price, first.area, first.rooms) == (119000, 68, 3)
    f = m.Filters(price_max=150000, rooms_min=2, exclude_keywords=["dalis buto"])
    assert m.passes(first, f)[0]
    second = [l for l in ls if l.listing_id == "87654322"][0]
    assert not m.passes(second, f)[0]


def test_run_once_new_and_price_drop(tmp_path=None, monkeypatch=None):
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    store = m.Store(tmp / "db.sqlite3")
    pages = {"html": ARUODAS}

    class FakeFetcher:
        def allowed(self, url): return True
        def get(self, url): return pages["html"]

    sent = []
    class FakeTG:
        enabled = True
        def send(self, text): sent.append(text); return True

    cfg = {"filters": {"price_max": 300000},
           "searches": [{"name": "t", "url": "https://www.aruodas.lt/butai/vilniuje/"}]}
    assert m.run_once(cfg, store, FakeFetcher(), FakeTG()) == 0      # pirmas kartas: tik įsimena
    pages["html"] = ARUODAS.replace("1-3456790", "1-3456791")
    assert m.run_once(cfg, store, FakeFetcher(), FakeTG()) == 1      # naujas skelbimas
    pages["html"] = pages["html"].replace("155 000 €", "149 000 €")
    assert m.run_once(cfg, store, FakeFetcher(), FakeTG()) == 1      # kainos kritimas
    assert "Kaina sumažėjo" in sent[-1]
    assert m.run_once(cfg, store, FakeFetcher(), FakeTG()) == 0      # nieko naujo


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("OK", name)


def test_json_store_and_github_notifier():
    import json, tempfile
    tmp = Path(tempfile.mkdtemp())
    store = m.JsonStore(tmp / "listings.json")
    pages = {"html": ARUODAS}

    class FakeFetcher:
        last_error = None
        def allowed(self, url): return True
        def get(self, url): return pages["html"]

    posted = []
    class Resp:
        ok = True
    orig = m.requests.post
    m.requests.post = lambda url, **kw: posted.append((url, kw["json"])) or Resp()
    try:
        gh = m.GitHubIssueNotifier("tok", "Mz/nt-skelbimai", "Mz")
        cfg = {"searches": [{"name": "t", "url": "https://www.aruodas.lt/butai/vilniuje/"}]}
        assert m.run_once(cfg, store, FakeFetcher(), gh) == 0
        pages["html"] = ARUODAS.replace("1-3456790", "1-3456791")
        store = m.JsonStore(tmp / "listings.json")          # perkrauna iš disko
        assert m.run_once(cfg, store, FakeFetcher(), gh) == 1
    finally:
        m.requests.post = orig
    assert posted[0][0].endswith("/repos/Mz/nt-skelbimai/issues")
    assert "@Mz" in posted[0][1]["body"] and "mz.github.io/nt-skelbimai" in posted[0][1]["body"]
    data = json.loads((tmp / "listings.json").read_text())
    assert len(data["listings"]) == 3 and data["searches"]["t"]["found"] == 2
    assert data["listings"]["aruodas.lt:1-3456789"]["price"] == 155000

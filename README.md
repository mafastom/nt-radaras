# NT radaras

Seka NT portalus (aruodas.lt, skelbiu.lt, alio.lt, domoplius.lt, realu.lt) ir
rodo naujus skelbimus **web puslapyje**. Nieko diegti į kompiuterį nereikia.

## Kaip tai veikia

- **GitHub Actions** kas ~15 min. nemokamai paleidžia `monitor.py` GitHub serveriuose.
  Jis atsisiunčia jūsų paieškas, atpažįsta skelbimus ir išsaugo juos `docs/listings.json`.
- **GitHub Pages** rodo puslapį `https://<jūsų-vardas>.github.io/<repozitorija>/`.
  Jame skelbimus galima filtruoti pagal portalą, kainą, plotą, kambarius, €/m² ir žodžius.
  Veikia ir telefone.
- **Pranešimai:**
  - apie naujus skelbimus, atitinkančius `config.yaml` filtrus, GitHub atsiunčia el. laišką
    (ir pranešimą GitHub programėlėje, jei ją naudojate);
  - kol puslapis atidarytas, naršyklė parodo pranešimą (mygtukas „Įjungti pranešimus“).

## Paieškų keitimas

Visi nustatymai yra faile `config.yaml`. Jį galima redaguoti tiesiai GitHub'e
(atidarykite failą → pieštuko ikona → „Commit changes“) arba paprašyti Claude.

1. Portale susidėkite filtrus kaip įprastai (miestas, rajonas, tipas, pardavimas/nuoma),
   surūšiuokite „naujausi viršuje“ ir nukopijuokite adresą.
2. Įklijuokite jį kaip naują įrašą `searches` sąraše.
3. `filters` dalyje nustatykite, apie kokius skelbimus norite gauti laiškus.

| laukas | reikšmė |
|---|---|
| `price_min` / `price_max` | kaina € |
| `area_min` / `area_max` | plotas m² |
| `rooms_min` / `rooms_max` | kambarių sk. |
| `price_per_m2_max` | max €/m² |
| `include_keywords` | bent vienas žodis turi būti skelbime |
| `exclude_keywords` | skelbimai su šiais žodžiais atmetami |

Pirmą kartą paieška tik įsimenama (kad negautumėte dešimčių senų skelbimų).

## Apribojimai

- **robots.txt.** Įrankis gerbia portalų taisykles. Jei portalas draudžia kurią nors
  nuorodą (pvz. skelbiu.lt ir alio.lt draudžia paieškas su raktažodžiais), puslapio
  viršuje prie to portalo bus parodyta klaida. Naudokite kategorijos nuorodą be
  paieškos frazės, o žodžius perkelkite į `include_keywords`.
- **Blokavimas.** Kai kurie portalai (ypač aruodas.lt) gali atmesti užklausas iš
  serverių (HTTP 403). Tada puslapio viršuje matysite raudoną žymą. Tokiam portalui
  patikimiausia naudoti jo paties „Prenumeruoti paiešką“ el. laiškus.
- GitHub suplanuotus paleidimus kartais pavėluoja keliomis minutėmis.
- Jei portalas pakeis puslapio struktūrą ir rodys „nerasta skelbimų“, reikia
  pataisyti nuorodų šabloną `SITES` faile `monitor.py`.

## Facebook grupės

Facebook neturi viešos sąsajos grupių įrašams skaityti, o automatinis grupių
nuskaitymas pažeidžia Facebook taisykles ir reikalauja prisijungusios paskyros,
kurią gali užblokuoti. Todėl įrankis Facebook netikrina. Kiekvienoje grupėje
įjunkite **⋯ → Pranešimai → Visi įrašai**, ir Facebook praneš apie naujus įrašus.

## Paleidimas savo Mac'e (nebūtina)

```bash
cd nt-monitor
python3 -m pip install -r requirements.txt
python3 monitor.py --json-store docs/listings.json
cd docs && python3 -m http.server 8000   # atidarykite http://localhost:8000
```

## Testai

```bash
python3 -m pip install pytest && python3 -m pytest tests
```

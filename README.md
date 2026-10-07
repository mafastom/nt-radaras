# NT radaras

Seka NT portalus (skelbiu.lt, alio.lt, domoplius.lt, realu.lt) jūsų Mac'e,
rodo skelbimus naršyklėje adresu **http://localhost:8765** ir apie naujus
skelbimus praneša Mac pranešimu.

## Įdiegimas

Atidarykite **Terminal** (Cmd+Tarpas → „Terminal“), įklijuokite šią eilutę ir paspauskite Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/mafastom/nt-radaras/main/install.sh | bash
```

- Jei macOS pasiūlys įdiegti „Command Line Tools“, sutikite ir, kai baigsis, įklijuokite eilutę dar kartą.
- Po įdiegimo atsidarys puslapis, o darbalaukyje atsiras nuoroda „NT radaras“.
- Radaras pasileidžia pats, kai įjungiate Mac'ą. Terminal'o laikyti atidaryto nereikia.
- Jei Mac pranešimų nematote: **System Settings → Notifications → Script Editor** → įjunkite.

Ta pati eilutė naudojama ir atnaujinti. Jūsų nustatymai ir rasti skelbimai išlieka.

## Naudojimas

1. Puslapyje paspauskite **Nustatymai**.
2. Portale susidėkite filtrus (miestas, rajonas, tipas, pardavimas ar nuoma),
   surūšiuokite „naujausi viršuje“, nukopijuokite adresą ir įklijuokite kaip paiešką.
3. Nurodykite, apie kokius skelbimus pranešti (kaina, plotas, kambariai, €/m², žodžiai), ir paspauskite **Išsaugoti**.

Pirmą kartą paieška tik įsimenama, kad negautumėte dešimčių senų skelbimų.
Pranešimai ateina apie skelbimus, atsiradusius po to.

Viršuje prie kiekvienos paieškos matosi, kiek skelbimų rasta. Raudona žymė reiškia,
kad portalas tos nuorodos neleidžia tikrinti arba atsakė klaida.

## Apribojimai

- **Aruodas.lt** savo robots.txt taisyklėse automatinį tikrinimą draudžia, todėl radaras jo netikrina.
  Aruode naudokite paties portalo „Išsaugoti paiešką“ pranešimus.
- **Facebook grupės** netikrinamos: tai draudžia Facebook taisyklės, o paskyrai gresia užblokavimas.
  Grupėje: **⋯ → Pranešimai → Visi įrašai**.
- Radaras veikia, kol Mac'as įjungtas ir neužmigęs.
- Portalai gali pakeisti puslapius. Jei paieška rodo „nerasta skelbimų“, pataisykite `SITES` faile `monitor.py`.

## Pašalinimas

```bash
bash ~/NT-radaras/uninstall.sh
```

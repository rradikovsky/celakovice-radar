import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

# ==========================================
# KONFIGURACE (VŠECHNY NEMOVITOSTI ČELÁKOVICE)
# ==========================================
PSC = "25088"
# Změň si číslo na konci na vlastní tajnou kombinaci (stejnou zadej v mobilu do apky ntfy)
NTFY_TOPIC = "celakovice_radar_7741"
DB_FILE = "videne_inzeraty.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9"
}

KATEGORIE_SREALITY = {
    1: ("Byt", "byt"),
    2: ("Dům", "dum"),
    3: ("Pozemek", "pozemek"),
    4: ("Komerční", "komercni"),
    5: ("Ostatní/Garáž", "ostatni")
}

BAZOS_SEKCE = [
    ("Byt", "https://reality.bazos.cz/prodam/byt/"),
    ("Dům", "https://reality.bazos.cz/prodam/dum/"),
    ("Pozemek", "https://reality.bazos.cz/prodam/pozemek/"),
    ("Garáž", "https://reality.bazos.cz/prodam/garaz/"),
    ("Chata", "https://reality.bazos.cz/prodam/chata/")
]


def nacti_historii():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data), False
        except Exception:
            return set(), True
    return set(), True


def uloz_historii(historie):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(historie)), f, ensure_ascii=False, indent=2)


def posli_notifikaci(titulek, cena_text, url, zdroj):
    zprava = f"Cena: {cena_text}\nZdroj: {zdroj}\nOdkaz: {url}"
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=zprava.encode("utf-8"),
            headers={
                "Title": f"{titulek}".encode("utf-8"),
                "Priority": "high",
                "Tags": "house",
                "Click": url
            },
            timeout=10
        )
        time.sleep(0.5)
    except Exception as e:
        print(f"[CHYBA NOTIFIKACE] {e}")


def zkontroluj_sreality(historie, prvni_spusteni):
    timestamp = int(time.time() * 1000)
    # Středočeský kraj (locality_region_id=11), Prodej (category_type_cb=1)
    for cat_id, (cat_nazev, cat_seo) in KATEGORIE_SREALITY.items():
        api_url = (
            f"https://www.sreality.cz/api/cs/v2/estates?"
            f"category_main_cb={cat_id}&category_type_cb=1&"
            f"locality_region_id=11&per_page=60&tms={timestamp}"
        )
        try:
            r = requests.get(api_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            estates = r.json().get("_embedded", {}).get("estates", [])

            for item in estates:
                lokalita = item.get("locality", "")
                if "čelákovice" not in lokalita.lower():
                    continue

                hash_id = f"sr_{item.get('hash_id')}"
                if hash_id in historie:
                    continue

                nazev = item.get("name", cat_nazev)
                cena = item.get("price", 0)
                cena_format = f"{cena:,} Kč".replace(",", " ") if cena > 0 else "Info v RK"
                web_url = f"https://www.sreality.cz/detail/prodej/{cat_seo}/detail/celakovice/{item.get('hash_id')}"

                if not prvni_spusteni:
                    posli_notifikaci(f"[{cat_nazev}] {nazev}", cena_format, web_url, "Sreality.cz")
                historie.add(hash_id)
        except Exception as e:
            print(f"[CHYBA SREALITY {cat_nazev}] {e}")


def zkontroluj_bazos(historie, prvni_spusteni):
    for kat_nazev, base_url in BAZOS_SEKCE:
        url = f"{base_url}?hledat=&hlokalita={PSC}&humkreis=2"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            inzeraty = soup.find_all("div", class_="inzeraty")

            for inz in inzeraty:
                nadpis_el = inz.find("h2", class_="nadpis")
                if not nadpis_el or not nadpis_el.find("a"):
                    continue

                a_tag = nadpis_el.find("a")
                titulek = a_tag.text.strip()
                rel_link = a_tag["href"]
                plna_url = f"https://reality.bazos.cz{rel_link}"

                popis_el = inz.find("div", class_="popis")
                popis = popis_el.text.strip() if popis_el else ""

                if "čelákovic" not in titulek.lower() and "čelákovic" not in popis.lower():
                    continue

                match_id = re.search(r"/inzerat/(\d+)/", rel_link)
                inz_id = f"bz_{match_id.group(1)}" if match_id else f"bz_{rel_link}"

                if inz_id in historie:
                    continue

                cena_el = inz.find("div", class_="inzeratycena")
                cena_text = cena_el.text.strip() if cena_el else "Neuvedena"

                if not prvni_spusteni:
                    posli_notifikaci(f"[Bazoš {kat_nazev}] {titulek}", cena_text, plna_url, "Bazoš.cz")
                historie.add(inz_id)
        except Exception as e:
            print(f"[CHYBA BAZOŠ {kat_nazev}] {e}")


def main():
    historie, prvni_spusteni = nacti_historii()
    pocet_pred = len(historie)

    zkontroluj_sreality(historie, prvni_spusteni)
    zkontroluj_bazos(historie, prvni_spusteni)

    nove = len(historie) - pocet_pred
    if nove > 0 or prvni_spusteni:
        uloz_historii(historie)
        if prvni_spusteni:
            posli_notifikaci(
                "Radar Čelákovice AKTIVOVÁN",
                f"Uloženo {len(historie)} stávajících inzerátů. Další už budou pípat živě.",
                "https://www.sreality.cz",
                "GitHub Actions"
            )


if __name__ == "__main__":
    main()

import json
import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
from urllib.parse import quote

PSC = "25088"
NTFY_TOPIC = "celakovice_radar_7741"
DB_FILE = "databaze_nemovitosti.json"
README_FILE = "README.md"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "cs-CZ,cs;q=0.9",
    "Referer": "https://www.sreality.cz/"
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

CESKEREALITY_SEKCE = [
    ("Byt", "https://www.ceskereality.cz/prodej/byty/celakovice/"),
    ("Dům", "https://www.ceskereality.cz/prodej/rodinne-domy/celakovice/"),
    ("Pozemek", "https://www.ceskereality.cz/prodej/pozemky/celakovice/"),
    ("Chata", "https://www.ceskereality.cz/prodej/chaty-chalupy/celakovice/"),
    ("Komerční", "https://www.ceskereality.cz/prodej/komercni-prostory/celakovice/")
]


def nacti_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def uloz_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def vytvor_fingerprint(cena_str, nazev):
    cista_cena = re.sub(r"\D", "", str(cena_str))
    if not cista_cena or cista_cena == "0":
        cista_cena = "info"
    metry_match = re.search(r"(\d+)\s*m[2²]", nazev.lower())
    metry = metry_match.group(1) if metry_match else ""
    cisty_nazev = re.sub(r"[^\w]", "", nazev.lower())[:12]
    return f"{cista_cena}_{metry}_{cisty_nazev}"


def posli_notifikaci(titulek, cena_text, url, zdroj):
    zprava = f"Cena: {cena_text}\nZdroj: {zdroj}\nOdkaz: {url}"
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=zprava.encode("utf-8"),
            headers={
                "Title": f"🔥 {titulek}".encode("utf-8"),
                "Priority": "high",
                "Tags": "house",
                "Click": url
            },
            timeout=8
        )
        time.sleep(0.3)
    except Exception as e:
        print(f"[CHYBA NOTIFIKACE] {e}")


def stahni_sreality_json(api_url):
    try:
        r = requests.get(api_url, headers=HEADERS, timeout=7)
        if r.status_code == 200 and "_embedded" in r.text:
            return r.json()
    except Exception as e:
        print(f"Prime spojeni na Sreality selhalo ({e}), zkousim premosteni...")

    try:
        proxy_url = f"https://api.allorigins.win/raw?url={quote(api_url, safe='')}"
        r = requests.get(proxy_url, timeout=12)
        if r.status_code == 200 and "_embedded" in r.text:
            return r.json()
    except Exception as e:
        print(f"Zalozni spojeni na Sreality selhalo: {e}")

    return None


def generuj_readme(db, statistika):
    dnes = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    obsah = f"# 🏠 Realitní Radar Čelákovice\n\n"
    obsah += f"Poslední aktualizace: **{dnes}**  \n"
    obsah += f"Stav posledního skenu: `{statistika}`\n\n"
    obsah += "| Status | Název / Odkaz | Cena | Zdroj | První výskyt |\n"
    obsah += "| :--- | :--- | :--- | :--- | :--- |\n"

    seazene_polozky = sorted(
        db.values(), 
        key=lambda x: (0 if x['status'] in ["🔥 Novinka", "✅ Aktivní"] else 1, x['first_seen']), 
        reverse=True
    )

    for item in seazene_polozky:
        status = item['status']
        nazev = item['title'].replace("|", "-")
        url = item['url']
        cena = item['price']
        zdroj = item['source']
        datum = item['first_seen']

        link_markdown = f"[{nazev}]({url})"
        obsah += f"| **{status}** | {link_markdown} | {cena} | {zdroj} | {datum} |\n"

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(obsah)


def main():
    db = nacti_db()
    prvni_beh = len(db) == 0
    dnes = datetime.now().strftime("%Y-%m-%d")
    nalezene_ids = set()
    existujici_fingerprints = {item.get("fingerprint"): uid for uid, item in db.items() if "fingerprint" in item}

    pocty = {"Sreality": 0, "Ceskereality": 0, "Bazos": 0}

    # 1. SREALITY.CZ
    timestamp = int(time.time() * 1000)
    for cat_id, (cat_nazev, cat_seo) in KATEGORIE_SREALITY.items():
        for page in [1, 2]:
            api_url = (
                f"https://www.sreality.cz/api/cs/v2/estates?"
                f"category_main_cb={cat_id}&category_type_cb=1&"
                f"locality_district_id=57&per_page=60&page={page}&tms={timestamp}"
            )
            data = stahni_sreality_json(api_url)
            if not data:
                break

            estates = data.get("_embedded", {}).get("estates", [])
            if not estates:
                break

            for item in estates:
                lokalita = item.get("locality", "")
                nazev = item.get("name", cat_nazev)
                seo_loc = item.get("seo", {}).get("locality", "celakovice")

                spojeny_text = f"{lokalita} {nazev} {seo_loc}".lower()
                if "čelákovic" not in spojeny_text and "celakovic" not in spojeny_text:
                    continue

                pocty["Sreality"] += 1
                uid = f"sr_{item.get('hash_id')}"
                cena = item.get("price", 0)
                cena_format = f"{cena:,} Kč".replace(",", " ") if cena > 0 else "Info v RK"
                web_url = f"https://www.sreality.cz/detail/prodej/{cat_seo}/detail/{seo_loc}/{item.get('hash_id')}"
                
                fp = vytvor_fingerprint(cena, nazev)
                if fp in existujici_fingerprints and existujici_fingerprints[fp] != uid:
                    nalezene_ids.add(existujici_fingerprints[fp])
                    continue

                nalezene_ids.add(uid)
                if uid not in db:
                    db[uid] = {
                        "title": f"[{cat_nazev}] {nazev} ({lokalita})",
                        "price": cena_format,
                        "url": web_url,
                        "source": "Sreality.cz",
                        "status": "🔥 Novinka",
                        "first_seen": dnes,
                        "fingerprint": fp
                    }
                    existujici_fingerprints[fp] = uid
                    if not prvni_beh:
                        posli_notifikaci(f"[{cat_nazev}] {nazev}", cena_format, web_url, "Sreality.cz")
                else:
                    db[uid]["status"] = "✅ Aktivní"

    # 2. ČESKÉREALITY.CZ
    for kat_nazev, cr_url in CESKEREALITY_SEKCE:
        try:
            r = requests.get(cr_url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for article in soup.find_all("article"):
                a_tag = article.find("a", href=True)
                if not a_tag:
                    continue
                href = a_tag["href"]
                plna_url = href if href.startswith("http") else f"https://www.ceskereality.cz{href}"
                
                nadpis_el = article.find(["h2", "h3"])
                titulek = nadpis_el.text.strip() if nadpis_el else a_tag.text.strip()
                if not titulek or "čelákovic" not in titulek.lower():
                    continue

                cena_el = article.find(class_=re.compile(r"price|cena", re.I))
                cena_text = cena_el.text.strip() if cena_el else "V detailu"

                match_id = re.search(r"-(\d+)\.html", plna_url)
                uid = f"cr_{match_id.group(1)}" if match_id else f"cr_{href[-20:]}"

                pocty["Ceskereality"] += 1
                fp = vytvor_fingerprint(cena_text, titulek)
                if fp in existujici_fingerprints and existujici_fingerprints[fp] != uid:
                    nalezene_ids.add(existujici_fingerprints[fp])
                    continue

                nalezene_ids.add(uid)
                if uid not in db:
                    db[uid] = {
                        "title": f"[{kat_nazev}] {titulek}",
                        "price": cena_text,
                        "url": plna_url,
                        "source": "ČeskéReality.cz",
                        "status": "🔥 Novinka",
                        "first_seen": dnes,
                        "fingerprint": fp
                    }
                    existujici_fingerprints[fp] = uid
                    if not prvni_beh:
                        posli_notifikaci(f"[{kat_nazev}] {titulek}", cena_text, plna_url, "ČeskéReality.cz")
                else:
                    db[uid]["status"] = "✅ Aktivní"
        except Exception as e:
            print(f"[CHYBA ČESKÉREALITY] {e}")

    # 3. BAZOŠ.CZ
    for kat_nazev, base_url in BAZOS_SEKCE:
        url = f"{base_url}?hledat=&hlokalita={PSC}&humkreis=2"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for inz in soup.find_all("div", class_="inzeraty"):
                nadpis_el = inz.find("h2", class_="nadpis")
                if not nadpis_el or not nadpis_el.find("a"):
                    continue
                a_tag = nadpis_el.find("a")
                titulek = a_tag.text.strip()
                rel_link = a_tag["href"]
                plna_url = f"https://reality.bazos.cz{rel_link}"
                popis = inz.find("div", class_="popis")
                popis_text = popis.text.strip() if popis else ""

                if "čelákovic" not in titulek.lower() and "čelákovic" not in popis_text.lower():
                    continue

                pocty["Bazos"] += 1
                match_id = re.search(r"/inzerat/(\d+)/", rel_link)
                uid = f"bz_{match_id.group(1)}" if match_id else f"bz_{rel_link}"
                
                cena_el = inz.find("div", class_="inzeratycena")
                cena_text = cena_el.text.strip() if cena_el else "Neuvedena"

                fp = vytvor_fingerprint(cena_text, titulek)
                if fp in existujici_fingerprints and existujici_fingerprints[fp] != uid:
                    nalezene_ids.add(existujici_fingerprints[fp])
                    continue

                nalezene_ids.add(uid)
                if uid not in db:
                    db[uid] = {
                        "title": f"[{kat_nazev}] {titulek}",
                        "price": cena_text,
                        "url": plna_url,
                        "source": "Bazoš.cz",
                        "status": "🔥 Novinka",
                        "first_seen": dnes,
                        "fingerprint": fp
                    }
                    existujici_fingerprints[fp] = uid
                    if not prvni_beh:
                        posli_notifikaci(f"[{kat_nazev}] {titulek}", cena_text, plna_url, "Bazoš.cz")
                else:
                    db[uid]["status"] = "✅ Aktivní"
        except Exception as e:
            print(f"[CHYBA BAZOŠ] {e}")

    # 4. KONTROLA STAŽENÝCH INZERÁTŮ
    for uid, item in db.items():
        zdroj = item.get("source", "")
        if uid not in nalezene_ids:
            if (zdroj == "Sreality.cz" and pocty["Sreality"] > 0) or \
               (zdroj == "ČeskéReality.cz" and pocty["Ceskereality"] > 0) or \
               (zdroj == "Bazoš.cz" and pocty["Bazos"] > 0):
                item["status"] = "❌ Staženo z inzerce"

    statistika = f"Sreality: {pocty['Sreality']} | ČeskéReality: {pocty['Ceskereality']} | Bazoš: {pocty['Bazos']}"
    print(f"HOTOVO -> {statistika}")

    uloz_db(db)
    generuj_readme(db, statistika)


if __name__ == "__main__":
    main()

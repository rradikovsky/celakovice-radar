import json
import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

PSC = "25088"
NTFY_TOPIC = "celakovice_radar_7741"
DB_FILE = "databaze_nemovitosti.json"
README_FILE = "README.md"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9"
}

BAZOS_SEKCE = [
    ("Byt", "https://reality.bazos.cz/prodam/byt/"),
    ("Dům", "https://reality.bazos.cz/prodam/dum/"),
    ("Pozemek", "https://reality.bazos.cz/prodam/pozemek/"),
    ("Garáž", "https://reality.bazos.cz/prodam/garaz/"),
    ("Chata", "https://reality.bazos.cz/prodam/chata/")
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
    cisty_nazev = re.sub(r"[^\w\s]", "", nazev.lower()).strip()
    return f"{cista_cena}_{cisty_nazev[:15]}"

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
            timeout=10
        )
        time.sleep(0.5)
    except Exception as e:
        print(f"[CHYBA NOTIFIKACE] {e}")

def generuj_readme(db):
    dnes = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    obsah = f"# 🏠 Realitní Radar Čelákovice\n\n"
    obsah += f"Poslední aktualizace: **{dnes}**\n\n"
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
    dnes = datetime.now().strftime("%Y-%m-%d")
    nalezene_ids = set()
    existujici_fingerprints = {item.get("fingerprint"): uid for uid, item in db.items() if "fingerprint" in item}

    # Prohledání Bazoše (spolehlivé, okamžité)
    for kat_nazev, base_url in BAZOS_SEKCE:
        url = f"{base_url}?hledat=&hlokalita={PSC}&humkreis=2"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
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
                        "title": f"[Bazoš {kat_nazev}] {titulek}",
                        "price": cena_text,
                        "url": plna_url,
                        "source": "Bazoš.cz",
                        "status": "🔥 Novinka",
                        "first_seen": dnes,
                        "fingerprint": fp
                    }
                    existujici_fingerprints[fp] = uid
                    posli_notifikaci(f"[Bazoš {kat_nazev}] {titulek}", cena_text, plna_url, "Bazoš.cz")
                else:
                    db[uid]["status"] = "✅ Aktivní"
        except Exception as e:
            print(f"[CHYBA BAZOŠ] {e}")

    # Kontrola stažených inzerátů
    for uid in db:
        if uid not in nalezene_ids and db[uid]["status"] not in ["❌ Staženo z inzerce"]:
            db[uid]["status"] = "❌ Staženo z inzerce"

    uloz_db(db)
    generuj_readme(db)

if __name__ == "__main__":
    main()

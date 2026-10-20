import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from playwright.sync_api import sync_playwright

# ==========================================
# KONFIGURACE
# ==========================================
PSC = "25088"
NTFY_TOPIC = "celakovice_radar_7741"  # Tvůj ntfy kanál z mobilu
DB_FILE = "databaze_nemovitosti.json"
README_FILE = "README.md"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9"
}

SREALITY_SEKCE = [
    ("Byt", "byty", "byt"),
    ("Dům", "domy", "dum"),
    ("Pozemek", "pozemky", "pozemek"),
    ("Komerční", "komercni-nemovitosti", "komercni"),
    ("Ostatní", "ostatni-reality", "ostatni")
]

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


def vytvor_fingerprint(kategorie, cena_str, nazev):
    """Vytvoří unikátní klíč podle kategorie, metrů čtverečních a ceny pro sloučení duplicit."""
    cista_cena = re.sub(r"\D", "", str(cena_str))
    if not cista_cena or cista_cena == "0":
        cista_cena = "info"
    else:
        # Zaokrouhlíme cenu na statisíce pro lepší párování mezi portály
        try:
            cista_cena = str(round(int(cista_cena) / 100000) * 100000)
        except Exception:
            pass

    metry_match = re.search(r"(\d+)\s*m[2²]", nazev.lower())
    metry = metry_match.group(1) if metry_match else "x"
    return f"{kategorie}_{metry}_{cista_cena}"


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


def zapis_polozku(db, existujici_fp, nalezene_ids, uid, kat_nazev, titulek, cena_text, url, zdroj, dnes, prvni_beh):
    fp = vytvor_fingerprint(kat_nazev, cena_text, titulek)

    # Kontrola duplicity na jiném portálu
    if fp in existujici_fp and existujici_fp[fp] != uid and "_x_" not in fp:
        puvodni_uid = existujici_fp[fp]
        nalezene_ids.add(puvodni_uid)
        if puvodni_uid in db:
            db[puvodni_uid]["status"] = "✅ Aktivní"
            if zdroj not in db[puvodni_uid]["source"]:
                db[puvodni_uid]["source"] += f" + {zdroj}"
        return

    nalezene_ids.add(uid)
    if uid not in db:
        db[uid] = {
            "title": f"[{kat_nazev}] {titulek}",
            "price": cena_text,
            "url": url,
            "source": zdroj,
            "status": "🔥 Novinka",
            "first_seen": dnes,
            "fingerprint": fp
        }
        existujici_fp[fp] = uid
        if not prvni_beh:
            posli_notifikaci(f"[{kat_nazev}] {titulek}", cena_text, url, zdroj)
    else:
        # Kontrola, zda se nezměnila cena
        stara_cena = db[uid].get("price", "")
        if stara_cena != cena_text and cena_text not in ["Info v RK", "Neuvedena"]:
            db[uid]["price"] = f"{cena_text} (původně {stara_cena})"
            db[uid]["status"] = "📉 Změna ceny"
            if not prvni_beh:
                posli_notifikaci(f"ZMĚNA CENY: {titulek}", db[uid]["price"], url, zdroj)
        else:
            if db[uid]["status"] == "❌ Staženo z inzerce":
                db[uid]["status"] = "✅ Aktivní"
            elif db[uid]["status"] != "🔥 Novinka":
                db[uid]["status"] = "✅ Aktivní"


def generuj_readme(db, statistika):
    dnes = datetime.now().strftime("%Y-%m-%d %H:%M")

    obsah = "# 🏠 Realitní Radar Čelákovice\n\n"
    obsah += f"Poslední aktualizace: **{dnes}**  \n"
    obsah += f"Stav posledního skenu (nalezeno aktivních): `{statistika}`\n\n"
    obsah += "| Status | Název / Odkaz | Cena | Zdroj | První výskyt |\n"
    obsah += "| :--- | :--- | :--- | :--- | :--- |\n"

    razeni_statusu = {"🔥 Novinka": 0, "📉 Změna ceny": 1, "✅ Aktivní": 2, "❌ Staženo z inzerce": 3}
    serazene_polozky = sorted(
        db.values(),
        key=lambda x: (razeni_statusu.get(x["status"], 4), x["first_seen"])
    )

    for item in serazene_polozky:
        status = item["status"]
        nazev = item["title"].replace("|", "-")
        url = item["url"]
        cena = item["price"]
        zdroj = item["source"]
        datum = item["first_seen"]

        link_markdown = f"[{nazev}]({url})"
        obsah += f"| **{status}** | {link_markdown} | {cena} | {zdroj} | {datum} |\n"

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(obsah)


def main():
    db = nacti_db()
    prvni_beh = len(db) == 0
    dnes = datetime.now().strftime("%Y-%m-%d")
    nalezene_ids = set()
    existujici_fp = {item.get("fingerprint"): uid for uid, item in db.items() if "fingerprint" in item}

    # Na začátku přepneme staré "Novinky" z minulých dnů na "Aktivní"
    for uid, item in db.items():
        if item.get("status") == "🔥 Novinka" and item.get("first_seen") != dnes:
            item["status"] = "✅ Aktivní"

    pocty = {"Sreality": 0, "iDNES": 0, "Bazos": 0}

    # =========================================================================
    # 1. SKUTEČNÝ PROHLÍŽEČ CHROMIUM (Sreality.cz + Reality.iDNES.cz)
    # =========================================================================
    print("Spouštím prohlížeč Chromium...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="cs-CZ",
            viewport={"width": 1366, "height": 768}
        )
        page = context.new_page()

        # Odposlech interních JSON dat přímo z prohlížeče Srealit
        zachycene_sreality = []

        def zachyt_odpoved(response):
            if "/api/cs/v2/estates" in response.url and response.status == 200:
                try:
                    data = response.json()
                    estates = data.get("_embedded", {}).get("estates", [])
                    zachycene_sreality.extend(estates)
                except Exception:
                    pass

        page.on("response", zachyt_odpoved)

        # A) SREALITY.CZ
        for kat_nazev, url_slug, seo_kat in SREALITY_SEKCE:
            zachycene_sreality.clear()
            cilova_url = f"https://www.sreality.cz/hledani/prodej/{url_slug}/celakovice"
            try:
                page.goto(cilova_url, wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(1500)

                # 1. Zpracování zachycených API dat z prohlížeče
                for item in zachycene_sreality:
                    lokalita = item.get("locality", "")
                    nazev = item.get("name", kat_nazev)
                    seo_loc = item.get("seo", {}).get("locality", "celakovice")

                    if "čelákovic" not in f"{lokalita} {nazev} {seo_loc}".lower() and "celakovic" not in seo_loc.lower():
                        continue

                    hash_id = item.get("hash_id")
                    if not hash_id:
                        continue

                    uid = f"sr_{hash_id}"
                    cena = item.get("price", 0)
                    cena_format = f"{cena:,} Kč".replace(",", " ") if cena > 0 else "Info v RK"
                    web_url = f"https://www.sreality.cz/detail/prodej/{seo_kat}/detail/{seo_loc}/{hash_id}"

                    pocty["Sreality"] += 1
                    zapis_polozku(db, existujici_fp, nalezene_ids, uid, kat_nazev, f"{nazev} ({lokalita})", cena_format, web_url, "Sreality.cz", dnes, prvni_beh)

                # 2. Záložní čtení přímo z obrazovky (DOM), pokud Sreality použily SSR
                if len(zachycene_sreality) == 0:
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    for a_tag in soup.find_all("a", href=re.compile(r"/detail/prodej/")):
                        href = a_tag["href"]
                        match_id = re.search(r"/(\d+)$", href)
                        if not match_id:
                            continue
                        hash_id = match_id.group(1)
                        uid = f"sr_{hash_id}"
                        plna_url = href if href.startswith("http") else f"https://www.sreality.cz{href}"
                        text_inzeratu = a_tag.get_text(" ", strip=True)
                        if "čelákovic" not in text_inzeratu.lower() and "celakovic" not in href.lower():
                            continue

                        cena_match = re.search(r"(\d[\d\s]{4,12})\s*Kč", text_inzeratu)
                        cena_format = f"{cena_match.group(1).strip()} Kč" if cena_match else "V detailu"
                        titulek = text_inzeratu.split("Kč")[0].strip()[:80]

                        pocty["Sreality"] += 1
                        zapis_polozku(db, existujici_fp, nalezene_ids, uid, kat_nazev, titulek, cena_format, plna_url, "Sreality.cz", dnes, prvni_beh)

            except Exception as e:
                print(f"[CHYBA SREALITY {kat_nazev}] {e}")

        # B) REALITY.IDNES.CZ (Všechny prodeje v Čelákovicích)
        try:
            idnes_url = "https://reality.idnes.cz/s/prodej/celakovice/"
            page.goto(idnes_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            soup = BeautifulSoup(page.content(), "html.parser")

            for item in soup.select("div.c-products__item"):
                a_tag = item.select_one("a.c-products__link")
                if not a_tag or not a_tag.get("href"):
                    continue
                plna_url = a_tag["href"]
                titulek_el = item.select_one("h2.c-products__title")
                info_el = item.select_one("p.c-products__info")
                cena_el = item.select_one("p.c-products__price")

                titulek = titulek_el.get_text(strip=True) if titulek_el else "Nemovitost"
                lokalita = info_el.get_text(strip=True) if info_el else ""
                if "čelákovic" not in f"{titulek} {lokalita}".lower():
                    continue

                cena_text = cena_el.get_text(strip=True) if cena_el else "Info v RK"
                match_id = re.search(r"/([a-f0-9]{24})/?$", plna_url)
                uid = f"idnes_{match_id.group(1)}" if match_id else f"idnes_{plna_url[-20:]}"

                kat = "Byt" if "byt" in titulek.lower() else ("Dům" if "dům" in titulek.lower() or "dom" in titulek.lower() else "Pozemek/Jiné")
                pocty["iDNES"] += 1
                zapis_polozku(db, existujici_fp, nalezene_ids, uid, kat, f"{titulek} ({lokalita})", cena_text, plna_url, "Reality.iDNES", dnes, prvni_beh)
        except Exception as e:
            print(f"[CHYBA IDNES] {e}")

        browser.close()

    # =========================================================================
    # 2. BAZOŠ.CZ (Rychlé načtení přes HTML)
    # =========================================================================
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

                zapis_polozku(db, existujici_fp, nalezene_ids, uid, kat_nazev, titulek, cena_text, plna_url, "Bazoš.cz", dnes, prvni_beh)
        except Exception as e:
            print(f"[CHYBA BAZOŠ] {e}")

    # =========================================================================
    # 3. KONTROLA STAŽENÝCH INZERÁTŮ
    # =========================================================================
    for uid, item in db.items():
        zdroj = item.get("source", "")
        if uid not in nalezene_ids:
            if ("Sreality" in zdroj and pocty["Sreality"] > 0) or \
               ("iDNES" in zdroj and pocty["iDNES"] > 0) or \
               ("Bazoš" in zdroj and pocty["Bazos"] > 0):
                item["status"] = "❌ Staženo z inzerce"

    statistika = f"Sreality: {pocty['Sreality']} | Reality.iDNES: {pocty['iDNES']} | Bazoš: {pocty['Bazos']}"
    print(f"HOTOVO -> {statistika}")

    uloz_db(db)
    generuj_readme(db, statistika)


if __name__ == "__main__":
    main()
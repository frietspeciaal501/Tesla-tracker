"""
Tesla Model Y aanbod tracker — scraper

Haalt actuele advertenties op van Marktplaats en AutoTrack (Model Y, Nederland)
en slaat ze op in een lokale SQLite-database (tesla_model_y.db).

Elke keer dat je dit script draait:
  - nieuwe advertenties krijgen een first_seen datum (= vandaag)
  - bestaande advertenties krijgen een bijgewerkte last_seen datum
  - advertenties die niet meer gevonden worden, krijgen een removed_on datum
    (waarschijnlijk verkocht of verwijderd)

Draai dit dagelijks via cron/Taakplanner (zie README.md) zodat je instroom
per dag/week kunt analyseren met analyze.py.

Gebruik:
    python scraper.py            # normale run
    python scraper.py --debug    # schrijft ruwe HTML van AutoTrack pagina 1 weg
                                  # (handig als de site structuur wijzigt)
"""

import re
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DB_PATH = Path(__file__).parent / "tesla_model_y.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9",
}

MARKTPLAATS_BASE = "https://www.marktplaats.nl/l/auto-s/tesla/f/model-y/13853/"
AUTOTRACK_BASE = "https://www.autotrack.nl/auto/tesla/model-y/nederland/"

MAX_PAGES = 15
SLEEP_BETWEEN_REQUESTS = 2.0  # wees netjes voor de servers, niet verlagen


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT,
            price INTEGER,
            year INTEGER,
            mileage INTEGER,
            variant TEXT,
            url TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            removed_on TEXT
        )
        """
    )
    # Migratie: als de database al bestond vóór de variant-kolom werd toegevoegd
    existing_cols = {row[1] for row in con.execute("PRAGMA table_info(listings)")}
    if "variant" not in existing_cols:
        con.execute("ALTER TABLE listings ADD COLUMN variant TEXT")
    con.commit()
    return con


def parse_price(text):
    m = re.search(r"€\s*([\d.,]+)", text)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1).split(",")[0])
    return int(digits) if digits else None


def parse_year_km(text):
    year_m = re.search(r"\b(19[89]\d|20[0-3]\d)\b", text)
    km_m = re.search(r"([\d.]{3,7})\s?km", text)
    year = int(year_m.group(1)) if year_m else None
    mileage = int(km_m.group(1).replace(".", "")) if km_m else None
    return year, mileage


def parse_variant(text):
    """Herkent de Model Y-variant op basis van tekst in de advertentie."""
    t = text.lower()
    if "performance" in t:
        return "Performance"
    if "long range" in t or "long-range" in t or " lr " in t:
        return "Long Range"
    if re.search(r"\brwd\b", t) or "achterwielaandrijving" in t:
        return "RWD"
    if re.search(r"\bawd\b", t) or "dual motor" in t or "4wd" in t:
        return "AWD"
    if "standard range" in t or "standaard" in t:
        return "Standard Range"
    return "Onbekend"


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _extract_block_text(anchor, levels_up=4):
    container = anchor
    for _ in range(levels_up):
        if container.parent:
            container = container.parent
    return container.get_text(" ", strip=True)


def scrape_marktplaats():
    """Retourneert lijst van dicts: id, source, title, price, year, mileage, url"""
    results = {}
    for page in range(1, MAX_PAGES + 1):
        url = MARKTPLAATS_BASE if page == 1 else f"{MARKTPLAATS_BASE.rstrip('/')}/p/{page}/"
        try:
            html = fetch(url)
        except requests.RequestException as e:
            print(f"[marktplaats] fout bij ophalen pagina {page}: {e}")
            break

        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/v/auto-s/.*/m\d+-"))

        found_new = False
        for a in links:
            href = a.get("href")
            id_match = re.search(r"/m(\d+)-", href)
            if not id_match:
                continue
            listing_id = f"mp_{id_match.group(1)}"
            if listing_id in results:
                continue
            found_new = True

            block_text = _extract_block_text(a)
            title = a.get_text(" ", strip=True) or block_text[:80]
            price = parse_price(block_text)
            year, mileage = parse_year_km(block_text)
            variant = parse_variant(f"{title} {block_text}")
            full_url = href if href.startswith("http") else f"https://www.marktplaats.nl{href}"

            results[listing_id] = {
                "id": listing_id,
                "source": "marktplaats",
                "title": title,
                "price": price,
                "year": year,
                "mileage": mileage,
                "variant": variant,
                "url": full_url,
            }

        if not found_new:
            break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    return list(results.values())


def scrape_autotrack():
    """
    Let op: de exacte HTML-structuur van AutoTrack kon niet live geverifieerd
    worden vanuit de omgeving waarin dit script is geschreven. Draai met
    --debug om de ruwe HTML van pagina 1 weg te schrijven naar
    autotrack_debug.html, en pas zo nodig de regex/selectors hieronder aan
    (open het bestand, zoek een advertentielink op en kijk naar het
    href-patroon en de omliggende tekst).
    """
    results = {}
    debug = "--debug" in sys.argv

    for page in range(1, MAX_PAGES + 1):
        url = AUTOTRACK_BASE if page == 1 else f"{AUTOTRACK_BASE.rstrip('/')}p{page}/"
        try:
            html = fetch(url)
        except requests.RequestException as e:
            print(f"[autotrack] fout bij ophalen pagina {page}: {e}")
            break

        if debug and page == 1:
            debug_path = Path(__file__).parent / "autotrack_debug.html"
            debug_path.write_text(html, encoding="utf-8")
            print(f"[autotrack] debug HTML weggeschreven naar {debug_path}")

        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/auto/tesla/model-y/.*-\d{5,}"))

        found_new = False
        for a in links:
            href = a.get("href")
            id_match = re.search(r"(\d{5,})", href)
            if not id_match:
                continue
            listing_id = f"at_{id_match.group(1)}"
            if listing_id in results:
                continue
            found_new = True

            block_text = _extract_block_text(a)
            title = a.get_text(" ", strip=True) or block_text[:80]
            price = parse_price(block_text)
            year, mileage = parse_year_km(block_text)
            variant = parse_variant(f"{title} {block_text}")
            full_url = href if href.startswith("http") else f"https://www.autotrack.nl{href}"

            results[listing_id] = {
                "id": listing_id,
                "source": "autotrack",
                "title": title,
                "price": price,
                "year": year,
                "mileage": mileage,
                "variant": variant,
                "url": full_url,
            }

        if not found_new:
            break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    return list(results.values())


def update_db(con, source, scraped_listings):
    today = date.today().isoformat()
    scraped_ids = {item["id"] for item in scraped_listings}

    cur = con.cursor()
    new_count = 0
    for item in scraped_listings:
        cur.execute("SELECT id FROM listings WHERE id = ?", (item["id"],))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO listings
                    (id, source, title, price, year, mileage, variant, url, first_seen, last_seen, removed_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    item["id"], item["source"], item["title"], item["price"],
                    item["year"], item["mileage"], item.get("variant", "Onbekend"),
                    item["url"], today, today,
                ),
            )
            new_count += 1
        else:
            cur.execute(
                "UPDATE listings SET last_seen = ?, removed_on = NULL, price = ?, variant = ? WHERE id = ?",
                (today, item["price"], item.get("variant", "Onbekend"), item["id"]),
            )

    cur.execute("SELECT id FROM listings WHERE source = ? AND removed_on IS NULL", (source,))
    existing_active = {r[0] for r in cur.fetchall()}
    gone = existing_active - scraped_ids
    for listing_id in gone:
        cur.execute("UPDATE listings SET removed_on = ? WHERE id = ?", (today, listing_id))

    con.commit()
    return new_count, len(gone)


def main():
    con = init_db()
    print(f"=== Tesla Model Y tracker — {datetime.now().isoformat(timespec='seconds')} ===")

    mp_listings = scrape_marktplaats()
    print(f"Marktplaats: {len(mp_listings)} advertenties gevonden")
    new_mp, gone_mp = update_db(con, "marktplaats", mp_listings)
    print(f"  -> {new_mp} nieuw, {gone_mp} verdwenen sinds vorige run")

    at_listings = scrape_autotrack()
    print(f"AutoTrack: {len(at_listings)} advertenties gevonden")
    new_at, gone_at = update_db(con, "autotrack", at_listings)
    print(f"  -> {new_at} nieuw, {gone_at} verdwenen sinds vorige run")

    con.close()
    print("Klaar. Data opgeslagen in", DB_PATH)


if __name__ == "__main__":
    main()

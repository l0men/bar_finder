import json
import random
import re
import time
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

OUT_FILE = Path("data/pints_external.json")
STATE_FILE = Path("data/schlouk_state.json")
OSM_BARS_FILE = Path("data/bars_paris.json")

BASE_URL = "https://www.schlouk-map.com"
CITY_URL = f"{BASE_URL}/fr/cities/paris"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.schlouk-map.com/fr/cities/paris",
}


def load_osm_lookup() -> dict[str, tuple[float, float]]:
    """Charge nos 2036 bars OSM pour trouver instantanément lat/lon sans faire 2000 requêtes sur Schlouk !"""
    if not OSM_BARS_FILE.exists():
        return {}
    with open(OSM_BARS_FILE, "r", encoding="utf-8") as f:
        osm_bars = json.load(f)
    return {b["name"].lower().strip(): (b["lat"], b["lon"]) for b in osm_bars if "name" in b}


def geocode_fallback_ban(client: httpx.Client, name: str, address_hint: str) -> tuple[float, float]:
    """Si le bar n'est pas trouvé par nom exact dans OSM, on demande ses coordonnées à la BAN (data.gouv.fr) gratuitement."""
    query = f"{name} {address_hint} Paris".strip()
    try:
        r = client.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": query, "citycode": "75056", "limit": 1},
            timeout=5.0,
        )
        feats = r.json().get("features", [])
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"]
            return round(lat, 6), round(lon, 6)
    except Exception:
        pass
    return 0.0, 0.0


def extract_bars_from_schlouk() -> None:
    OUT_FILE.parent.mkdir(exist_ok=True)
    formatted_bars = []

    if OUT_FILE.exists():
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            formatted_bars = json.load(f)

    # Récupérer la vraie page où l'on s'était arrêté
    start_page = 1
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            start_page = json.load(f).get("next_page", 6)
    elif len(formatted_bars) >= 130:
        start_page = 6  # Tu as déjà fait les pages 1 à 5 !

    osm_lookup = load_osm_lookup()
    osm_names = list(osm_lookup.keys())
    existing_names = {b["name"].lower() for b in formatted_bars}

    print(f"🔄 Reprise à la page {start_page}/57 ({len(formatted_bars)} bars déjà en base).")

    with httpx.Client(headers=HEADERS, timeout=25.0, follow_redirects=True) as client:
        for page in range(start_page, 58):
            url = f"{CITY_URL}?page={page}"
            print(f"\n📄 Lecture de la page {page}/57 : {url}")

            resp = client.get(url)
            print(f"   ↳ Statut HTTP : {resp.status_code} | URL finale : {resp.url}")

            if resp.status_code in (403, 429):
                print("🛑 Serveur en rate-limit (403/429). Pause de 45s...")
                time.sleep(45)
                resp = client.get(url)

            soup = BeautifulSoup(resp.text, "html.parser")

            # Correction majeure : à partir de la page 6, les cartes n'ont plus forcément la classe '.offers' !
            bar_cards = soup.select(".card.offers") or soup.select(".card")
            # On ne garde que les cartes qui contiennent bien un lien de bar
            bar_cards = [c for c in bar_cards if c.select_one("a.name") or c.select_one("h2, h3, .card-title")]

            if not bar_cards:
                print(f"⚠️ Aucune carte trouvée sur la page {page}. Fin de la pagination publique atteinte !")
                break

            page_added = 0
            for card in bar_cards:
                name_elem = card.select_one("a.name") or card.select_one("a[href*='/places/']") or card.select_one("h2, h3")
                if not name_elem:
                    continue
                name = name_elem.get_text(strip=True)
                if not name or name.lower() in existing_names:
                    continue

                # Extraction de tous les prix en € présents sur la carte (ex: "5,00 €" ou "6.50€")
                card_text = card.get_text(" ", strip=True)
                prices_found = [
                    float(p.replace(",", "."))
                    for p in re.findall(r"(\d+[.,]\d+|\d+)\s*€", card_text)
                    if 2.5 <= float(p.replace(",", ".")) <= 15.0
                ]

                if len(prices_found) >= 2:
                    hh_price = min(prices_found)
                    price = max(prices_found)
                elif len(prices_found) == 1:
                    hh_price = prices_found[0]
                    price = round(hh_price + 2.0, 2)
                else:
                    hh_price = 5.5
                    price = 7.0

                # Chercher l'adresse sur la carte si présente
                addr_elem = card.select_one(".address, .location, small, .text-muted")
                addr_hint = addr_elem.get_text(strip=True) if addr_elem else ""

                # 1. Géolocalisation ultra-rapide via notre base OSM (0 requête HTTP sur Schlouk !)
                lat, lon = 0.0, 0.0
                match = process.extractOne(name.lower(), osm_names, scorer=fuzz.token_set_ratio) if osm_names else None
                if match and match[1] >= 82:
                    lat, lon = osm_lookup[match[0]]
                else:
                    # 2. Fallback via l'API Adresse data.gouv.fr (0 requête sur Schlouk !)
                    lat, lon = geocode_fallback_ban(client, name, addr_hint)

                hh_hours = "17:00-20:00"
                if "toute la soirée" in card_text.lower():
                    hh_hours = "17:00-02:00"

                if lat != 0.0 and lon != 0.0:
                    formatted_bars.append({
                        "name": name,
                        "lat": lat,
                        "lon": lon,
                        "pint_price": round(price, 2),
                        "happy_hour_price": round(hh_price, 2),
                        "hh_hours": hh_hours,
                    })
                    existing_names.add(name.lower())
                    page_added += 1

            # Sauvegarder les bars + le numéro de la page suivante
            with open(OUT_FILE, "w", encoding="utf-8") as f:
                json.dump(formatted_bars, f, ensure_ascii=False, indent=2)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"next_page": page + 1}, f)

            print(f"💾 Page {page} validée (+{page_added} bars). Total : {len(formatted_bars)} bars.")
            time.sleep(random.uniform(1.5, 3.0))

    print(f"\n🎉 Terminé ! {len(formatted_bars)} bars prêts dans {OUT_FILE}.")
    print("👉 Lance maintenant : uv run python src/enrich_bars.py")


if __name__ == "__main__":
    extract_bars_from_schlouk()
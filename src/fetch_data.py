import json
from pathlib import Path
import httpx

DATA_DIR = Path("data")
BARS_FILE = DATA_DIR / "bars_paris.json"

# Requête en langage Overpass QL :
# area["name"="Paris"]["admin_level"="8"] cible la municipalité de Paris.
OVERPASS_QUERY = """
[out:json][timeout:60];
area["name"="Paris"]["admin_level"="8"]->.paris;
(
  node["amenity"="bar"](area.paris);
  node["amenity"="pub"](area.paris);
  node["amenity"="biergarten"](area.paris);
  way["amenity"="bar"](area.paris);
  way["amenity"="pub"](area.paris);
  way["amenity"="biergarten"](area.paris);
);
out center tags;
"""

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def fetch_paris_bars() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    if BARS_FILE.exists():
        print(f"✅ Le fichier {BARS_FILE} existe déjà.")
        return

    print("⏳ Téléchargement des bars de Paris depuis OpenStreetMap (Overpass API)...")
    # On ajoute un User-Agent personnalisé pour ne pas se faire bloquer
    headers = {
        "User-Agent": "BarFinderPoC/1.0 (Projet etudiant/apprentissage)"
    }
    
    response = httpx.post(
        OVERPASS_URL, 
        data={"data": OVERPASS_QUERY}, 
        headers=headers, # Ajout de cette ligne
        timeout=60.0
    )
    response.raise_for_status()

    raw_data = response.json()
    bars = []

    for element in raw_data.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name")
        if not name:
            continue  # On ignore les bars sans nom pour que le PoC soit lisible

        # Pour un 'node' on prend lat/lon, pour un 'way' (bâtiment) on prend son centre
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")

        if lat and lon:
            bars.append({
                "id": element["id"],
                "name": name,
                "type": tags.get("amenity", "bar"),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "address": f"{tags.get('addr:housenumber', '')} {tags.get('addr:street', '')}".strip(),
                "opening_hours": tags.get("opening_hours", "Non renseigné"),
                "outdoor_seating": tags.get("outdoor_seating") == "yes",
            })

    with open(BARS_FILE, "w", encoding="utf-8") as f:
        json.dump(bars, f, ensure_ascii=False, indent=2)

    print(f"🍻 {len(bars)} bars parisiens sauvegardés dans {BARS_FILE} !")


if __name__ == "__main__":
    fetch_paris_bars()
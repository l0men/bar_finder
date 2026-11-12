import json
from pathlib import Path

HAR_FILE = Path("data/mgb_paris.har")
OUT_FILE = Path("data/pints_external.json")

def extract_bars_from_har():
    if not HAR_FILE.exists():
        print(f"❌ Fichier {HAR_FILE} introuvable.")
        return

    print("🔍 Analyse du trafic réseau (fichier HAR)...")
    with open(HAR_FILE, "r", encoding="utf-8") as f:
        har_data = json.load(f)

    entries = har_data.get("log", {}).get("entries", [])
    raw_bars = {}

    # 1. Parcourir toutes les requêtes interceptées
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        
        # On cible uniquement les requêtes de la carte
        if "map-bars" in url:
            content = entry.get("response", {}).get("content", {}).get("text")
            if not content:
                continue
                
            try:
                data = json.loads(content)
                # La réponse peut être directe ou dans un champ "bars" / "data"
                tuile_bars = data.get("bars") or data.get("data") or data if isinstance(data, list) else []
                
                # 2. Extraire et dédoublonner (un bar peut apparaître sur plusieurs tuiles)
                for b in tuile_bars:
                    # Identifier l'ID ou le nom pour éviter les doublons
                    bar_id = str(b.get("id") or b.get("objectId") or b.get("name"))
                    if bar_id and bar_id not in raw_bars:
                        raw_bars[bar_id] = b
            except json.JSONDecodeError:
                continue

    print(f"🍺 {len(raw_bars)} bars uniques trouvés dans les tuiles de la carte !")

    # 3. Formater pour le Multi-Source Dijkstra
    formatted_bars = []
    for b in raw_bars.values():
        name = b.get("name") or b.get("title")
        
        location = b.get("location", {})
        lat = location.get("latitude") or b.get("lat") or b.get("latitude")
        lon = location.get("longitude") or b.get("lng") or b.get("longitude")

        # Priorité aux prix affichés sur la carte
        pint = b.get("price") or b.get("regularPrice")
        hh_price = b.get("priceHH") or b.get("happyHourPrice")

        if not name or not lat or not lon or pint is None:
            continue

        if hh_price is None:
            hh_price = pint

        formatted_bars.append({
            "name": name,
            "lat": float(lat),
            "lon": float(lon),
            "pint_price": round(float(pint), 2),
            "happy_hour_price": round(float(hh_price), 2),
            "hh_hours": b.get("happy_hour_time") or b.get("hh_hours") or "17:00-20:00"
        })

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(formatted_bars, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(formatted_bars)} bars formatés avec succès dans {OUT_FILE} !")

if __name__ == "__main__":
    extract_bars_from_har()
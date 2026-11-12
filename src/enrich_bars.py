import hashlib
import json
import math
from pathlib import Path
import numpy as np
from rapidfuzz import fuzz, process
from scipy.spatial import KDTree

DATA_DIR = Path("data")
BARS_FILE = DATA_DIR / "bars_paris.json"
EXTERNAL_PINTS_FILE = DATA_DIR / "pints_external.json"

SCALE_LAT = 111320.0
SCALE_LON = 111320.0 * math.cos(math.radians(48.85))


def clean_name(name: str) -> str:
    """Normalise le nom d'un bar pour maximiser le taux de fusion."""
    stop_words = {"le", "la", "les", "l", "bar", "pub", "cafe", "café", "brasserie", "bistrot", "paris", "chez", "au", "aux"}
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in name)
    words = [w for w in cleaned.split() if w not in stop_words and len(w) > 1]
    return " ".join(words) if words else cleaned.strip()


def compute_bayesian_rating(rating: float, reviews: int, m: float = 50.0, C: float = 4.1) -> float:
    return round((reviews * rating + m * C) / (reviews + m), 2)


def merge_schlouk_pints_into_osm() -> None:
    if not BARS_FILE.exists():
        raise FileNotFoundError(f"{BARS_FILE} introuvable.")
    if not EXTERNAL_PINTS_FILE.exists():
        raise FileNotFoundError(f"{EXTERNAL_PINTS_FILE} introuvable.")

    with open(BARS_FILE, "r", encoding="utf-8") as f:
        osm_bars = json.load(f)
    with open(EXTERNAL_PINTS_FILE, "r", encoding="utf-8") as f:
        ext_bars = json.load(f)

    print(f"📂 Chargement : {len(osm_bars)} bars OSM (avec notes Google conservées) et {len(ext_bars)} bars Schlouk.")

    # Préparer l'index spatial KDTree et la liste des noms nettoyés de pints_external.json
    ext_xy = np.array([[b["lat"] * SCALE_LAT, b["lon"] * SCALE_LON] for b in ext_bars])
    tree = KDTree(ext_xy)
    ext_clean_names = [clean_name(b.get("name", "")) for b in ext_bars]

    matched_geo = 0
    matched_name = 0

    for bar in osm_bars:
        bx, by = bar["lat"] * SCALE_LAT, bar["lon"] * SCALE_LON
        b_clean = clean_name(bar["name"])

        # Passe 1 : Recherche géographique dans un rayon de 85 mètres
        candidate_idxs = tree.query_ball_point([bx, by], r=85.0)
        best_score = 0
        best_ext = None

        for idx in candidate_idxs:
            sim = fuzz.token_set_ratio(b_clean, ext_clean_names[idx])
            if sim > best_score:
                best_score = sim
                best_ext = ext_bars[idx]

        if best_ext and best_score >= 65:
            bar["pint_price"] = round(float(best_ext["pint_price"]), 2)
            bar["happy_hour_price"] = round(float(best_ext.get("happy_hour_price", bar["pint_price"])), 2)
            bar["hh_hours"] = best_ext.get("hh_hours", "17:00-20:00")
            bar["price_source"] = "Schlouk_GeoMatch"
            matched_geo += 1
            continue

        # Passe 2 : Si le geocoding BAN du scraping avait un léger décalage (>85m), matching sur nom exact/très proche (< 400m)
        near_400 = tree.query_ball_point([bx, by], r=400.0)
        for idx in near_400:
            sim = fuzz.token_sort_ratio(b_clean, ext_clean_names[idx])
            if sim >= 88:
                cand = ext_bars[idx]
                bar["pint_price"] = round(float(cand["pint_price"]), 2)
                bar["happy_hour_price"] = round(float(cand.get("happy_hour_price", cand["pint_price"])), 2)
                bar["hh_hours"] = cand.get("hh_hours", "17:00-20:00")
                bar["price_source"] = "Schlouk_NameMatch"
                matched_name += 1
                break

    # Vérifier que les bars non matchés (~10%) gardent un fallback propre et que toutes les notes Google restent intactes
    google_kept = 0
    for bar in osm_bars:
        if bar.get("rating_source") == "Google_Places":
            google_kept += 1
        if "rating" in bar and "bayesian_rating" not in bar:
            bar["bayesian_rating"] = compute_bayesian_rating(bar["rating"], bar.get("reviews_count", 50))

        # Seulement si le bar n'a toujours aucun prix
        if "pint_price" not in bar:
            h = int(hashlib.md5(str(bar["id"]).encode()).hexdigest()[:8], 16)
            bar["pint_price"] = 6.5
            bar["happy_hour_price"] = 5.0
            bar["hh_hours"] = "17:00-20:00"
            bar["price_source"] = "Estimated"

    with open(BARS_FILE, "w", encoding="utf-8") as f:
        json.dump(osm_bars, f, ensure_ascii=False, indent=2)

    print(f"✅ Fusion terminée sans aucun appel API Google !")
    print(f"   • {matched_geo + matched_name} bars mis à jour avec les vrais prix Schlouk ({matched_geo} par GPS <85m, {matched_name} par nom <400m).")
    print(f"   • {google_kept} notes Google Places existantes préservées intactes dans {BARS_FILE}.")


if __name__ == "__main__":
    merge_schlouk_pints_into_osm()
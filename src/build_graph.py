import json
import math
from pathlib import Path
import httpx
import numpy as np
from scipy.spatial import KDTree

DATA_DIR = Path("data")
BARS_FILE = DATA_DIR / "bars_paris.json"
RAW_TRANSIT_FILE = DATA_DIR / "idf_rail_raw.json"
GRAPH_FILE = DATA_DIR / "transit_graph.json"

# Serveurs Overpass (avec fallback automatique si l'un est occupé)
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Récupère toutes les lignes de Métro, RER et Transilien d'Île-de-France (FR-IDF)
OVERPASS_TRANSIT_QUERY = """
[out:json][timeout:90];
area["ISO3166-2"="FR-IDF"]->.idf;
(
  relation["route"~"^(subway|light_rail|train)$"](area.idf);
);
out body;
node(r:"stop");
out body;
node(r:"stop_entry_only");
out body;
"""

# Vitesses et pénalités moyennes
WALK_SPEED_MPS = 1.25          # 4.5 km/h en marche à pied
DETOUR_FACTOR = 1.3            # Facteur de détour des rues vs vol d'oiseau
METRO_SPEED_MPS = 8.33         # ~30 km/h
TRAIN_SPEED_MPS = 15.28        # ~55 km/h (RER / Transilien)
BOARDING_PENALTY_SEC = 150     # 2m30 d'attente/descente sur le quai en entrant sur une ligne


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcule la distance à vol d'oiseau en mètres entre deux points GPS."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_raw_transit() -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    if RAW_TRANSIT_FILE.exists():
        print(f"✅ Cache réseau ferré trouvé : {RAW_TRANSIT_FILE}")
        with open(RAW_TRANSIT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    print("🚇 Téléchargement du réseau Métro / RER / Transilien Île-de-France...")
    headers = {"User-Agent": "BarFinderPoC/1.0 (Educational Project)"}

    for url in OVERPASS_URLS:
        try:
            resp = httpx.post(url, data={"data": OVERPASS_TRANSIT_QUERY}, headers=headers, timeout=90.0)
            resp.raise_for_status()
            data = resp.json()
            with open(RAW_TRANSIT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        except Exception as e:
            print(f"⚠️ Erreur sur {url} ({e}), essai du serveur suivant...")

    raise RuntimeError("Impossible de contacter Overpass API.")


def build_transit_graph() -> None:
    raw = fetch_raw_transit()
    elements = raw.get("elements", [])

    # 1. Extraire tous les noeuds de stations (stops)
    raw_nodes = {}
    for el in elements:
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            name = el.get("tags", {}).get("name")
            if name:
                raw_nodes[el["id"]] = {
                    "name": name,
                    "lat": round(el["lat"], 6),
                    "lon": round(el["lon"], 6),
                }

    nodes = {}  # id -> {name, lat, lon, type, line, color}
    edges = {}  # (u, v) -> {weight, mode, line, color}

    def add_edge(u: str, v: str, weight: float, mode: str, line: str = "", color: str = "#888888"):
        if u == v:
            return
        key = (u, v)
        if key not in edges or weight < edges[key]["weight"]:
            edges[key] = {"weight": round(weight, 1), "mode": mode, "line": line, "color": color}

    # 2. Parcourir les lignes (relations) pour créer les voies ferrées
    for el in elements:
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        route_type = tags.get("route", "subway")
        ref = tags.get("ref") or tags.get("name", "Ligne")
        network = tags.get("network", "")

        # Filtrer les TGV/ Grandes Lignes hors IDF si présents
        if route_type == "train" and any(x in ref.upper() for x in ["TGV", "OUIGO", "INTERCITÉS", "EUROSTAR"]):
            continue

        mode_label = "METRO" if route_type == "subway" else "RER_TRAIN"
        line_id = f"{mode_label}_{ref}"
        color = tags.get("colour", "#0055c8" if mode_label == "METRO" else "#e11d48")

        # Récupérer la séquence ordonnée des arrêts de cette ligne
        stop_ids = [
            m["ref"] for m in el.get("members", [])
            if m.get("type") == "node" and "stop" in m.get("role", "") and m["ref"] in raw_nodes
        ]

        if len(stop_ids) < 2:
            continue

        speed = METRO_SPEED_MPS if mode_label == "METRO" else TRAIN_SPEED_MPS
        dwell_time = 20.0 if mode_label == "METRO" else 35.0

        prev_plat_id = None
        for nid in stop_ids:
            st = raw_nodes[nid]
            # Noeud physique "Hub" (en surface) et noeud "Quai" (sur la ligne)
            hub_id = f"hub_{nid}"
            plat_id = f"plat_{nid}_{line_id}"

            nodes[hub_id] = {"name": st["name"], "lat": st["lat"], "lon": st["lon"], "type": "hub", "line": "HUB"}
            nodes[plat_id] = {"name": f"{st['name']} ({ref})", "lat": st["lat"], "lon": st["lon"], "type": "platform", "line": ref, "color": color}

            # Monter dans le train (Hub -> Quai) coûte un temps d'attente/accès moyen
            add_edge(hub_id, plat_id, BOARDING_PENALTY_SEC, "transfer", ref, "#aaaaaa")
            # Sortir du train (Quai -> Hub) est rapide (45 sec)
            add_edge(plat_id, hub_id, 45.0, "transfer", ref, "#aaaaaa")

            if prev_plat_id and prev_plat_id != plat_id:
                prev_st = nodes[prev_plat_id]
                dist = haversine_m(prev_st["lat"], prev_st["lon"], st["lat"], st["lon"])
                # On ignore les sauts aberrants (> 25 km entre 2 arrêts consécutifs)
                if 50 <= dist <= 25000:
                    travel_sec = (dist / speed) + dwell_time
                    # On ajoute dans les 2 sens pour garantir la symétrie même si une variante OSM est partielle
                    add_edge(prev_plat_id, plat_id, travel_sec, mode_label, ref, color)
                    add_edge(plat_id, prev_plat_id, travel_sec, mode_label, ref, color)

            prev_plat_id = plat_id

    # 3. Relier les Hubs proches à pied (correspondances entre stations proches < 500m)
    hub_ids = [nid for nid, data in nodes.items() if data["type"] == "hub"]
    hub_coords = np.array([[nodes[nid]["lat"], nodes[nid]["lon"]] for nid in hub_ids])

    # Projection approximative en mètres autour de Paris (lat ~ 48.85) pour KDTree
    scale_lat = 111320.0
    scale_lon = 111320.0 * math.cos(math.radians(48.85))
    hub_xy = np.column_stack((hub_coords[:, 0] * scale_lat, hub_coords[:, 1] * scale_lon))
    tree = KDTree(hub_xy)

    pairs = tree.query_pairs(r=500.0)  # Toutes les paires de stations à moins de 500m
    for i, j in pairs:
        u, v = hub_ids[i], hub_ids[j]
        dist = haversine_m(nodes[u]["lat"], nodes[u]["lon"], nodes[v]["lat"], nodes[v]["lon"])
        walk_sec = (dist * DETOUR_FACTOR) / WALK_SPEED_MPS
        add_edge(u, v, walk_sec, "walk", "WALK", "#64748b")
        add_edge(v, u, walk_sec, "walk", "WALK", "#64748b")

    # 4. Pré-relier chaque Bar de Paris aux stations de métro/RER dans un rayon de 800m
    with open(BARS_FILE, "r", encoding="utf-8") as f:
        bars = json.load(f)

    bar_links = {}
    for idx, bar in enumerate(bars):
        bx, by = bar["lat"] * scale_lat, bar["lon"] * scale_lon
        near_indices = tree.query_ball_point([bx, by], r=800.0)
        if not near_indices:
            # Si aucune station à 800m, on prend les 3 plus proches
            _, near_indices = tree.query([bx, by], k=3)
            near_indices = np.atleast_1d(near_indices).tolist()

        links = []
        for hi in near_indices:
            hid = hub_ids[hi]
            dist = haversine_m(bar["lat"], bar["lon"], nodes[hid]["lat"], nodes[hid]["lon"])
            walk_sec = (dist * DETOUR_FACTOR) / WALK_SPEED_MPS
            links.append({"hub_id": hid, "walk_sec": round(walk_sec, 1)})
        bar_links[str(idx)] = links

    # 5. Sauvegarde au format liste d'adjacence
    adj = {nid: [] for nid in nodes}
    for (u, v), data in edges.items():
        adj[u].append({"to": v, **data})

    graph_data = {"nodes": nodes, "adj": adj, "bar_links": bar_links}
    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(graph_data, f)

    print(f"🎉 Graphe construit avec succès : {len(nodes)} noeuds, {len(edges)} segments ferrés/piétons !")
    print(f"🔗 {len(bars)} bars reliés au réseau dans {GRAPH_FILE}")


if __name__ == "__main__":
    build_transit_graph()
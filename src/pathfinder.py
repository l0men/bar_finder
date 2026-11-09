import heapq
import json
import math
from pathlib import Path
import numpy as np
from scipy.spatial import KDTree

DATA_DIR = Path("data")
BARS_FILE = DATA_DIR / "bars_paris.json"
GRAPH_FILE = DATA_DIR / "transit_graph.json"

WALK_SPEED_MPS = 1.25
DETOUR_FACTOR = 1.3
SCALE_LAT = 111320.0
SCALE_LON = 111320.0 * math.cos(math.radians(48.85))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_in_paris(lat: float, lon: float) -> bool:
    """Vérifie approximativement si une station est dans Paris intra-muros (ou limitrophe immédiat)."""
    return (48.815 <= lat <= 48.905) and (2.224 <= lon <= 2.420)


class TransitPathfinder:
    def __init__(self) -> None:
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(BARS_FILE, "r", encoding="utf-8") as f:
            self.bars = json.load(f)

        self.nodes = data["nodes"]
        self.adj = data["adj"]
        self.bar_links = data["bar_links"]

        # Index spatial KDTree sur les stations "hub"
        self.hub_ids = [nid for nid, n in self.nodes.items() if n["type"] == "hub"]
        hub_xy = np.array([
            [self.nodes[nid]["lat"] * SCALE_LAT, self.nodes[nid]["lon"] * SCALE_LON]
            for nid in self.hub_ids
        ])
        self.kdtree = KDTree(hub_xy)

        # Index spatial KDTree sur les bars pour la recherche rapide autour d'une station
        bar_xy = np.array([
            [b["lat"] * SCALE_LAT, b["lon"] * SCALE_LON]
            for b in self.bars
        ])
        self.bar_kdtree = KDTree(bar_xy)

    def _run_single_dijkstra(self, start_lat: float, start_lon: float, person_idx: int):
        dist = {}
        parent = {}
        pq = []
        explored_segments = []

        sx, sy = start_lat * SCALE_LAT, start_lon * SCALE_LON
        _, near_idxs = self.kdtree.query([sx, sy], k=6)

        for idx in np.atleast_1d(near_idxs):
            hid = self.hub_ids[idx]
            st = self.nodes[hid]
            d_m = haversine_m(start_lat, start_lon, st["lat"], st["lon"])
            walk_sec = (d_m * DETOUR_FACTOR) / WALK_SPEED_MPS
            dist[hid] = walk_sec
            parent[hid] = ("START", "WALK", "Marche")
            heapq.heappush(pq, (walk_sec, hid))
            explored_segments.append({
                "t": round(walk_sec, 1),
                "p": person_idx,
                "from": [start_lat, start_lon],
                "to": [st["lat"], st["lon"]],
                "mode": "WALK",
            })

        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue

            u_node = self.nodes[u]
            for edge in self.adj.get(u, []):
                v = edge["to"]
                nd = d + edge["weight"]
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    parent[v] = (u, edge["mode"], edge["line"])
                    heapq.heappush(pq, (nd, v))

                    v_node = self.nodes[v]
                    if edge["mode"] != "transfer":
                        explored_segments.append({
                            "t": round(nd, 1),
                            "p": person_idx,
                            "from": [u_node["lat"], u_node["lon"]],
                            "to": [v_node["lat"], v_node["lon"]],
                            "mode": edge["mode"],
                        })

        return dist, parent, explored_segments

    def reconstruct_path_to_hub(self, parent: dict, start_pos: list, hub_id: str) -> list:
        """Reconstruit le chemin d'un ami jusqu'à une station (hub_id)."""
        segments = []
        curr = hub_id
        while curr in parent:
            prev, mode, line = parent[curr]
            curr_node = self.nodes[curr]
            if prev == "START":
                segments.append({
                    "from": start_pos,
                    "to": [curr_node["lat"], curr_node["lon"]],
                    "mode": "WALK",
                    "label": f"Marche vers {curr_node['name']}",
                })
                break
            prev_node = self.nodes[prev]
            if mode != "transfer":
                segments.append({
                    "from": [prev_node["lat"], prev_node["lon"]],
                    "to": [curr_node["lat"], curr_node["lon"]],
                    "mode": mode,
                    "label": f"Ligne {line}" if mode != "walk" else "Correspondance à pied",
                })
            curr = prev

        segments.reverse()
        return segments

    def reconstruct_path_to_bar(self, parent: dict, start_pos: list, best_hub: str, bar: dict) -> list:
        """Reconstruit le chemin d'un ami jusqu'au bar choisi."""
        if best_hub == "DIRECT":
            return [{
                "from": start_pos,
                "to": [bar["lat"], bar["lon"]],
                "mode": "WALK",
                "label": f"Marche directe vers {bar['name']}",
            }]

        segments = self.reconstruct_path_to_hub(parent, start_pos, best_hub)
        st = self.nodes[best_hub]
        segments.append({
            "from": [st["lat"], st["lon"]],
            "to": [bar["lat"], bar["lon"]],
            "mode": "WALK",
            "label": f"Marche vers {bar['name']}",
        })
        return segments

    def get_bars_around_station(self, hub_id: str, max_walk_min: float) -> list[dict]:
        """Retourne tous les bars situés à moins de N minutes de marche d'une station."""
        if hub_id not in self.nodes:
            return []
        st = self.nodes[hub_id]
        # Distance max à vol d'oiseau correspondant à max_walk_min minutes de marche
        max_euclid_m = (max_walk_min * 60.0 * WALK_SPEED_MPS) / DETOUR_FACTOR

        sx, sy = st["lat"] * SCALE_LAT, st["lon"] * SCALE_LON
        near_idxs = self.bar_kdtree.query_ball_point([sx, sy], r=max_euclid_m)

        results = []
        for b_idx in near_idxs:
            bar = self.bars[b_idx]
            d_m = haversine_m(st["lat"], st["lon"], bar["lat"], bar["lon"])
            walk_min = ((d_m * DETOUR_FACTOR) / WALK_SPEED_MPS) / 60.0
            if walk_min <= max_walk_min:
                results.append({
                    "bar_index": b_idx,
                    "bar": bar,
                    "walk_min": round(walk_min, 1),
                    "dist_m": round(d_m),
                })

        results.sort(key=lambda x: x["walk_min"])
        return results

    def solve(self, locations: list[dict]):
        """Exécute le Multi-Source Dijkstra et évalue à la fois les 2036 bars et les stations parisiennes."""
        all_dists = []
        all_parents = []
        all_explored = []

        for idx, loc in enumerate(locations):
            d, p, exp = self._run_single_dijkstra(loc["lat"], loc["lon"], idx)
            all_dists.append(d)
            all_parents.append(p)
            all_explored.extend(exp)

        all_explored.sort(key=lambda x: x["t"])
        step = max(1, len(all_explored) // 1400)
        sampled_exploration = all_explored[::step]

        # 1. Évaluer tous les bars
        evaluated_bars = []
        for b_idx, bar in enumerate(self.bars):
            links = self.bar_links.get(str(b_idx), [])
            times = []
            best_hubs = []
            reachable = True

            for p_idx, loc in enumerate(locations):
                direct_dist = haversine_m(loc["lat"], loc["lon"], bar["lat"], bar["lon"])
                best_t = (direct_dist * DETOUR_FACTOR) / WALK_SPEED_MPS
                best_h = "DIRECT"

                for link in links:
                    hid = link["hub_id"]
                    if hid in all_dists[p_idx]:
                        t = all_dists[p_idx][hid] + link["walk_sec"]
                        if t < best_t:
                            best_t = t
                            best_h = hid

                if best_t == float("inf"):
                    reachable = False
                    break
                times.append(round(best_t / 60.0, 1))
                best_hubs.append(best_h)

            if reachable:
                evaluated_bars.append({
                    "bar_index": b_idx,
                    "bar": bar,
                    "times_min": times,
                    "best_hubs": best_hubs,
                })

        # 2. Évaluer toutes les stations (hubs) dans Paris (dédupliquées par nom de station)
        station_groups = {}
        for hid in self.hub_ids:
            st = self.nodes[hid]
            if not is_in_paris(st["lat"], st["lon"]):
                continue

            times = []
            reachable = True
            for p_idx in range(len(locations)):
                t = all_dists[p_idx].get(hid, float("inf"))
                if t == float("inf"):
                    reachable = False
                    break
                times.append(round(t / 60.0, 1))

            if reachable:
                name = st["name"]
                mean_t = sum(times) / len(times)
                # Si plusieurs quais/entrées portent le même nom, on garde le hub le plus rapide en moyenne
                if name not in station_groups or mean_t < station_groups[name]["_mean"]:
                    station_groups[name] = {
                        "hub_id": hid,
                        "name": name,
                        "lat": st["lat"],
                        "lon": st["lon"],
                        "times_min": times,
                        "_mean": mean_t,
                    }

        evaluated_stations = []
        for st_data in station_groups.values():
            st_data.pop("_mean", None)
            evaluated_stations.append(st_data)

        return evaluated_bars, evaluated_stations, all_parents, sampled_exploration
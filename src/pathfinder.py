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


class TransitPathfinder:
    def __init__(self) -> None:
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(BARS_FILE, "r", encoding="utf-8") as f:
            self.bars = json.load(f)

        self.nodes = data["nodes"]
        self.adj = data["adj"]
        self.bar_links = data["bar_links"]

        # Index spatial KDTree sur les stations "hub" pour connecter n'importe quel point d'Île-de-France
        self.hub_ids = [nid for nid, n in self.nodes.items() if n["type"] == "hub"]
        hub_xy = np.array([
            [self.nodes[nid]["lat"] * SCALE_LAT, self.nodes[nid]["lon"] * SCALE_LON]
            for nid in self.hub_ids
        ])
        self.kdtree = KDTree(hub_xy)

    def _run_single_dijkstra(self, start_lat: float, start_lon: float, person_idx: int):
        """Lance Dijkstra depuis la position (banlieue ou Paris) d'un participant."""
        dist = {}
        parent = {}
        pq = []
        explored_segments = []

        # Connecter le point de départ aux 6 stations/gares les plus proches à pied
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

                    # On enregistre les déplacements physiques pour l'animation
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

    def reconstruct_path(self, parent: dict, start_pos: list, best_hub: str, bar: dict) -> list:
        """Reconstruit le tracé exact (liste de segments) d'un ami jusqu'au bar choisi."""
        segments = []
        # Dernier kilomètre : de la station d'arrivée jusqu'au bar à pied
        st = self.nodes[best_hub]
        segments.append({
            "from": [st["lat"], st["lon"]],
            "to": [bar["lat"], bar["lon"]],
            "mode": "WALK",
            "label": f"Marche vers {bar['name']}",
        })

        curr = best_hub
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

    def solve(self, locations: list[dict]):
        """Exécute le Multi-Source Dijkstra pour tous les amis et évalue les 2036 bars."""
        all_dists = []
        all_parents = []
        all_explored = []

        for idx, loc in enumerate(locations):
            d, p, exp = self._run_single_dijkstra(loc["lat"], loc["lon"], idx)
            all_dists.append(d)
            all_parents.append(p)
            all_explored.extend(exp)

        # Trier les segments explorés par temps croissant pour l'animation simultanée
        all_explored.sort(key=lambda x: x["t"])
        # On échantillonne à ~1200 segments max pour que l'animation web dure ~3 secondes à 60 FPS
        step = max(1, len(all_explored) // 1200)
        sampled_exploration = all_explored[::step]

        evaluated_bars = []
        for b_idx, bar in enumerate(self.bars):
            links = self.bar_links.get(str(b_idx), [])
            times = []
            best_hubs = []
            reachable = True

            for p_idx, loc in enumerate(locations):
                # Temps si l'ami marche directement jusqu'au bar (s'il habite déjà à côté !)
                direct_dist = haversine_m(loc["lat"], loc["lon"], bar["lat"], bar["lon"])
                best_t = (direct_dist * DETOUR_FACTOR) / WALK_SPEED_MPS
                best_h = "DIRECT"

                # Temps via le réseau ferré + marche finale
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
                times.append(round(best_t / 60.0, 1))  # Conversion en minutes
                best_hubs.append(best_h)

            if reachable:
                evaluated_bars.append({
                    "bar_index": b_idx,
                    "bar": bar,
                    "times_min": times,
                    "best_hubs": best_hubs,
                })

        return evaluated_bars, all_parents, sampled_exploration
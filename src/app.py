from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from src.pathfinder import TransitPathfinder

app = FastAPI(title="Bar Finder PoC")
pathfinder = TransitPathfinder()

# Cache en mémoire de la dernière recherche pour que le curseur soit instantané
LAST_STATE = {"locations": [], "parents": [], "bars": []}


class LocationItem(BaseModel):
    label: str
    lat: float
    lon: float


class SolveRequest(BaseModel):
    locations: list[LocationItem]


@app.post("/api/solve")
def solve_meeting_point(req: SolveRequest):
    locs = [loc.model_dump() for loc in req.locations]
    evaluated_bars, parents, exploration = pathfinder.solve(locs)

    LAST_STATE["locations"] = locs
    LAST_STATE["parents"] = parents
    LAST_STATE["bars"] = {b["bar_index"]: b for b in evaluated_bars}

    return {
        "exploration": exploration,
        "bars": evaluated_bars,
    }


@app.get("/api/path/{bar_index}")
def get_bar_paths(bar_index: int):
    """Renvoie les chemins détaillés de chaque ami vers le bar sélectionné par le curseur."""
    b_data = LAST_STATE["bars"].get(bar_index)
    if not b_data:
        return {"paths": []}

    paths = []
    for p_idx, loc in enumerate(LAST_STATE["locations"]):
        hub = b_data["best_hubs"][p_idx]
        bar = b_data["bar"]
        if hub == "DIRECT":
            paths.append([{
                "from": [loc["lat"], loc["lon"]],
                "to": [bar["lat"], bar["lon"]],
                "mode": "WALK",
                "label": "Marche directe",
            }])
        else:
            segments = pathfinder.reconstruct_path(
                LAST_STATE["parents"][p_idx],
                [loc["lat"], loc["lon"]],
                hub,
                bar,
            )
            paths.append(segments)

    return {"paths": paths}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from src.pathfinder import TransitPathfinder

app = FastAPI(title="Bar Finder v2")
pathfinder = TransitPathfinder()

LAST_STATE = {"locations": [], "parents": [], "bars": {}, "stations": {}}


class LocationItem(BaseModel):
    label: str
    lat: float
    lon: float


class SolveRequest(BaseModel):
    locations: list[LocationItem] = Field(..., min_length=2, max_length=10)


@app.post("/api/solve")
def solve_meeting_point(req: SolveRequest):
    locs = [loc.model_dump() for loc in req.locations]
    evaluated_bars, evaluated_stations, parents, exploration = pathfinder.solve(locs)

    LAST_STATE["locations"] = locs
    LAST_STATE["parents"] = parents
    LAST_STATE["bars"] = {b["bar_index"]: b for b in evaluated_bars}
    LAST_STATE["stations"] = {s["hub_id"]: s for s in evaluated_stations}

    return {
        "exploration": exploration,
        "bars": evaluated_bars,
        "stations": evaluated_stations,
    }


@app.get("/api/path/bar/{bar_index}")
def get_bar_paths(bar_index: int):
    b_data = LAST_STATE["bars"].get(bar_index)
    if not b_data:
        raise HTTPException(status_code=404, detail="Bar non trouvé dans la session actuelle")

    paths = []
    for p_idx, loc in enumerate(LAST_STATE["locations"]):
        hub = b_data["best_hubs"][p_idx]
        segments = pathfinder.reconstruct_path_to_bar(
            LAST_STATE["parents"][p_idx],
            [loc["lat"], loc["lon"]],
            hub,
            b_data["bar"],
        )
        paths.append(segments)

    return {"paths": paths}


@app.get("/api/station/{hub_id}/details")
def get_station_details(hub_id: str, walk_min: float = 5.0):
    st_data = LAST_STATE["stations"].get(hub_id)
    if not st_data:
        raise HTTPException(status_code=404, detail="Station non trouvée dans la session actuelle")

    paths = []
    for p_idx, loc in enumerate(LAST_STATE["locations"]):
        segments = pathfinder.reconstruct_path_to_hub(
            LAST_STATE["parents"][p_idx],
            [loc["lat"], loc["lon"]],
            hub_id,
        )
        paths.append(segments)

    nearby_bars = pathfinder.get_bars_around_station(hub_id, walk_min)
    return {
        "station": st_data,
        "paths": paths,
        "nearby_bars": nearby_bars,
    }


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
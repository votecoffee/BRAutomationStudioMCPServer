"""Read the station-to-station legs and LaunchPad model processes of a matched cycle."""

from math import isfinite


def model_process(node):
    fixed = node.get("fixed_dwell_time")
    fixed = fixed if type(fixed) is bool else None
    items = node.get("process_steps")
    steps = []
    verified = fixed is not None and isinstance(items, list)
    if isinstance(items, list):
        for item in items:
            item = item if isinstance(item, dict) else {}
            kind = item.get("type")
            raw = item.get("dwell_time")
            numeric = type(raw) is int or (type(raw) is float and isfinite(raw))
            dwell = raw if numeric else None
            steps.append({"type": kind if isinstance(kind, str) else None, "dwellTimeRaw": dwell})
            verified &= kind == "Wait" and dwell is not None and dwell >= 0
    return {"state": "read" if verified else "unverified", "recipeNodeId": node["recipe_id"],
            "fixedDwellTime": fixed, "steps": steps}


def station_legs(objects, renamed):
    stations = [index for index, obj in enumerate(objects) if obj["type"] == "Station"]
    legs = []
    for start, end in zip(stations, stations[1:] + stations[:1]):
        wraps = end <= start
        path = objects[start:] + objects[:end + 1] if wraps else objects[start:end + 1]
        legs.append({
            "path": [{"name": obj["name"], "importedName": renamed[obj["name"]], "type": obj["type"]} for obj in path],
            "wrapsReturnMarker": wraps,
            "modelProcess": objects[start]["modelProcess"],
        })
    return legs

"""Measure the named stations and waypoints in a saved LaunchPad cycle."""

from dataclasses import dataclass
from itertools import combinations
from math import dist, isclose, isfinite


@dataclass(frozen=True)
class GeometryCriteria:
    distance_tolerance_um: int | None
    expected_handedness: str | None


def point(values, scale):
    xy = tuple(float(value) * scale for value in values)
    if len(xy) != 2 or not all(isfinite(value) for value in xy):
        raise ValueError("Expected two finite planar coordinates")
    return xy


def named_positions(objects, renamed, position_rows):
    evidence = {}
    for row in position_rows:
        evidence.setdefault(row["name"], []).append(row)
    saved, actual, locations = {}, {}, []
    for obj in objects:
        name = obj["name"]
        foreign = renamed[name]
        coords = obj["launchPadXY"]
        if any(type(value) not in {int, float} for value in coords):
            raise ValueError(f"LaunchPad object {name!r} needs numeric X and Y coordinates")
        saved[name] = point(coords, 1)

        candidates = evidence.get(foreign, [])
        if len(candidates) != 1:
            raise ValueError(f"Expected one absolute position for manager object {foreign!r}")
        row = candidates[0]
        if row["type"] != obj["type"] or row["mode"] != "Absolute" or None in row["xy"]:
            raise ValueError(f"Manager object {foreign!r} needs the expected type and an absolute X/Y position")
        actual[name] = point(row["xy"], 1000)
        locations.append({"name": name, "importedName": foreign, "type": obj["type"],
                          "launchPadMm": saved[name], "automationStudioMm": actual[name], "path": row["path"]})
    return saved, actual, locations


def pair_distances(saved, actual):
    pairs = []
    for a, b in combinations(saved, 2):
        before, after = dist(saved[a], saved[b]), dist(actual[a], actual[b])
        if not isfinite(before) or not isfinite(after):
            raise ValueError("Object separation exceeds the numeric range")
        pairs.append({"from": a, "to": b, "launchPadMm": before, "automationStudioMm": after,
                      "deltaMm": after - before})
    return pairs


def normalized_area(points, a, b, c):
    ab = dist(points[a], points[b])
    ac = dist(points[a], points[c])
    scale = max(ab, ac)
    if scale == 0:
        return 0
    x1, y1 = ((value - origin) / scale for value, origin in zip(points[b], points[a]))
    x2, y2 = ((value - origin) / scale for value, origin in zip(points[c], points[a]))
    return x1 * y2 - y1 * x2


def handedness(saved, actual, pairs):
    if len(saved) < 3:
        return {"state": "indeterminate", "witness": []}
    farthest = max(pairs, key=lambda pair: pair["launchPadMm"])
    a, b = farthest["from"], farthest["to"]
    possible = [name for name in saved if name not in {a, b}]
    c = max(possible, key=lambda name: abs(normalized_area(saved, a, b, name)))
    before, after = normalized_area(saved, a, b, c), normalized_area(actual, a, b, c)
    if min(abs(before), abs(after)) <= 1e-10:
        state = "indeterminate"
    else:
        state = "preserved" if (before > 0) == (after > 0) else "reversed"
    return {"state": state, "witness": [a, b, c]}


def measure_geometry(objects, renamed, position_rows, criteria):
    saved, actual, positions = named_positions(objects, renamed, position_rows)
    pairs = pair_distances(saved, actual)
    worst = max(pairs, key=lambda pair: abs(pair["deltaMm"]))
    orientation = handedness(saved, actual, pairs)
    tolerance = criteria.distance_tolerance_um
    expected = criteria.expected_handedness
    maximum = abs(worst["deltaMm"])
    issues = []
    limit_mm = tolerance / 1000 if tolerance is not None else None
    if limit_mm is not None and maximum > limit_mm and not isclose(maximum, limit_mm, rel_tol=0, abs_tol=1e-9):
        issues.append(("launchpad_distance_tolerance",
                       f"Maximum pairwise delta {maximum:.6f} mm exceeds {tolerance} micrometres"))
    if expected is not None and orientation["state"] != expected:
        issues.append(("launchpad_handedness_mismatch",
                       f"Expected {expected} handedness, observed {orientation['state']}"))
    state = "measured"
    if issues:
        state = "issues"
    elif tolerance is not None or expected is not None:
        state = "criteria_met"
    return {"state": state, "positions": positions, "pairs": pairs,
            "maxAbsDeltaMm": maximum, "maxPair": [worst["from"], worst["to"]], "handedness": orientation,
            "distanceToleranceMicrometres": tolerance, "expectedHandedness": expected}, issues

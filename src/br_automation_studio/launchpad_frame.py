"""Compare route positions for the same axes that align the linked segment grid."""

from itertools import combinations
from math import dist, fsum, hypot, isclose, isfinite, sqrt
from pathlib import Path

from .launchpad_geometry import GeometryCriteria, point
from .launchpad_layout import AXES, apply_axes
from .launchpad_route import json_snapshot, launchpad_route_review


def datum_point(value):
    if not isinstance(value, list) or len(value) != 2 or any(type(item) not in {int, float} for item in value):
        raise ValueError("A datum needs two numeric coordinates in each frame")
    return point(value, 1)


def load_datum(path, identity):
    source, snapshot = json_snapshot(path, 1024 * 1024)
    if not isinstance(source, dict) or type(source.get("schemaVersion")) is not int or source["schemaVersion"] != 1:
        raise ValueError("Expected a schemaVersion 1 coordinate datum")
    if any(source.get(key) != value for key, value in identity.items()):
        raise ValueError("The datum does not identify this LaunchPad source, Automation Studio evidence, and layout")
    label = source.get("label")
    if not isinstance(label, str) or not label.strip() or "\0" in label:
        raise ValueError("Identify the common verified point used as the datum")
    return {"label": label, "snapshot": snapshot, "launchPadMm": datum_point(source.get("launchPadMm")),
            "automationStudioMm": datum_point(source.get("automationStudioMm"))}


def subtract(left, right):
    return left[0] - right[0], left[1] - right[1]


def candidate(axes, positions, datum, tolerance_um):
    matrix = AXES[axes]
    offsets = [subtract(row["automationStudioMm"], apply_axes(matrix, row["launchPadMm"])) for row in positions]
    if not all(isfinite(value) for xy in offsets for value in xy):
        raise ValueError("Coordinate transformation exceeds the numeric range")
    count = len(offsets)
    if datum is None:
        translation = tuple(fsum(value / count for value in axis) for axis in zip(*offsets))
    else:
        translation = subtract(datum["automationStudioMm"], apply_axes(matrix, datum["launchPadMm"]))

    rows = []
    for position, offset in zip(positions, offsets):
        error = subtract(offset, translation)
        rows.append({"name": position["name"], "importedName": position["importedName"],
                     "errorXYMm": error, "residualMm": hypot(*error)})
    worst = max(rows, key=lambda row: row["residualMm"])
    maximum = worst["residualMm"]
    rms = hypot(*(row["residualMm"] / sqrt(count) for row in rows))
    # Any translation within r of both offsets requires those offsets to be within 2r.
    a, b = max(combinations(range(count), 2), key=lambda pair: dist(offsets[pair[0]], offsets[pair[1]]))
    lower_bound = dist(offsets[a], offsets[b]) / 2
    if not all(isfinite(value) for value in (*translation, maximum, rms, lower_bound)):
        raise ValueError("Coordinate residual exceeds the numeric range")
    limit = tolerance_um / 1000 if tolerance_um is not None else None
    within = None if limit is None else maximum <= limit or isclose(maximum, limit, rel_tol=0, abs_tol=1e-9)
    return {"axes": axes, "translationMm": translation, "rmsResidualMm": rms,
            "maxResidualMm": maximum, "worstObject": worst["name"],
            "anyTranslationLowerBoundMm": lower_bound,
            "lowerBoundWitness": [positions[a]["name"], positions[b]["name"]],
            "withinTolerance": within, "residuals": rows}


def launchpad_frame_review(project, configuration, cpu, manager_id, lpj, readback, name_map=None, job_dir=None,
                           *, datum_path=None, tolerance_um=None):
    report = launchpad_route_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_dir,
                                    geometry=GeometryCriteria(None, None), include_layout=True)
    frame = report["frameReview"] = {"state": "not_checked"}
    layout, geometry = report["layoutReview"], report["geometryReview"]
    if layout["state"] != "matched" or geometry["state"] != "measured":
        return report

    route = report["routeReview"]
    identity = {"launchPadSha256": route["launchPad"]["sourceSha256"],
                "automationStudioSha256": (report["rucReview"]["archive"]["sha256"] if job_dir is not None
                                           else report["snapshot"]["sha256"]),
                "layoutId": layout["layoutId"]}
    frame["inputIdentity"] = identity

    def issue(code, where, message):
        frame["state"] = "issues"
        report["diagnostics"].append({"code": code, "path": Path(where).as_posix(), "message": message,
                                      "severity": "error", "scope": "launchpad_frame"})

    datum = None
    if datum_path is not None:
        try:
            datum = load_datum(datum_path, identity)
        except (OSError, RuntimeError, ValueError, OverflowError) as error:
            issue("launchpad_frame_datum_invalid", datum_path, str(error))
            return report

    try:
        candidates = [candidate(axes, geometry["positions"], datum, tolerance_um)
                      for axes in layout["compatibleAxisMappings"]]
    except (ValueError, OverflowError) as error:
        issue("launchpad_frame_unverified", layout["path"], str(error))
        return report

    smallest_bound = min(row["anyTranslationLowerBoundMm"] for row in candidates)
    best = min(candidates, key=lambda row: (row["rmsResidualMm"], row["maxResidualMm"]))
    fitting = [row["axes"] for row in candidates if row["withinTolerance"]]
    frame.update(state="measured", datum=datum,
                 translationBasis="supplied_datum" if datum is not None else "least_squares_route_points",
                 positionToleranceMicrometres=tolerance_um, bestRmsAxes=best["axes"],
                 anyGridCompatibleTranslationLowerBoundMm=smallest_bound,
                 withinToleranceAxes=fitting, candidates=candidates)
    if tolerance_um is not None:
        limit = tolerance_um / 1000
        if fitting:
            frame["state"] = "criteria_met"
        elif smallest_bound > limit and not isclose(smallest_bound, limit, rel_tol=0, abs_tol=1e-9):
            issue("launchpad_frame_residual", layout["path"],
                  f"Every grid-compatible translation requires at least {smallest_bound:.6f} mm of object error")
        else:
            issue("launchpad_frame_residual", layout["path"],
                  f"No reported transform keeps all object residuals within {tolerance_um} micrometres")

    if frame["state"] in {"measured", "criteria_met"} and route["state"] == "matched":
        report["evidence"] = "launchpad_frame_compiled" if job_dir is not None else "launchpad_frame_saved"
    return report

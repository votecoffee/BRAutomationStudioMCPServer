"""Compare explicit recovery declarations and the saved-to-compiled AS4 point sequence."""

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

MODES = {"Direct", "Sequential"}
ENABLE = {"Enable": True, "Disable": False}
STARTING_FLAGS = ("first_node", "recovery_points", "all_stations_as_recovery_stations")
POINT_ID = re.compile(r"RecoveryPoint\[([1-9]\d*)\]", re.ASCII)
POSITION = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?", re.ASCII)


def launchpad_declaration(row):
    enabled = row.get("is_recovery_point")
    mode = row.get("recovery_mode")
    points = row.get("recovery_points")
    return {"mode": mode if isinstance(mode, str) and mode in MODES else None,
            "stationIsRecoveryPoint": enabled if type(enabled) is bool else None,
            "explicitPointCount": len(points) if isinstance(points, list) else None}


def as_declaration(evidence):
    record = {"path": evidence.get("path"), "mode": evidence.get("mode"),
              "stationIsRecoveryPoint": ENABLE.get(evidence.get("stationFlag")),
              "explicitPointCount": None, "points": None}
    indices = {}
    try:
        for item in evidence["points"]:
            match = POINT_ID.fullmatch(item["id"] or "")
            if match is None:
                return record
            index = int(match[1])
            xy = item["xyMetres"]
            if (index in indices or not isinstance(xy, list) or len(xy) != 2
                    or not all(isinstance(value, str) and POSITION.fullmatch(value) for value in xy)):
                return record
            if not all(Decimal(value).is_finite() for value in xy):
                return record
            indices[index] = xy
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return record
    record["points"] = [{"index": key, "xyMetres": indices[key]} for key in sorted(indices)]
    record["explicitPointCount"] = len(indices)
    return record


def declaration_values(record):
    return record["mode"], record["stationIsRecoveryPoint"], record["explicitPointCount"]


def point_values(record):
    return [(point["index"], tuple(Decimal(value) for value in point["xyMetres"])) for point in record["points"]]


def recovery_matches(saved, compiled):
    return declaration_values(saved) == declaration_values(compiled) and point_values(saved) == point_values(compiled)


def review_recovery(decoded, objects, renamed, native, saved, readback, project):
    """Read declarations on paired route stations. Compare AS4 positions only within AS4."""
    source_stations = {row["id"]: row for row in decoded["workstations"]}
    selected = {row["name"]: row for row in native}
    before = {row["name"]: row for row in saved} if saved is not None else None
    settings = decoded.get("settings")
    starting = settings.get("starting_positions") if isinstance(settings, dict) else None
    flags = {key: starting.get(key) if isinstance(starting, dict) else None for key in STARTING_FLAGS}
    flags = {key: value if type(value) is bool else None for key, value in flags.items()}
    result = {"launchPadStartingPositions": flags, "stations": []}
    issues = []
    if None in flags.values():
        issues.append(("launchpad_recovery_flags_unverified", readback, "LaunchPad starting-position flags are incomplete", "warning"))

    for obj in objects:
        if obj["type"] != "Station":
            continue
        name = renamed[obj["name"]]
        baseline = launchpad_declaration(source_stations[obj["geometryId"]])
        studio = as_declaration(selected.get(name, {}))
        origin = Path(studio["path"]) if studio["path"] else project
        baseline_values = declaration_values(baseline)
        studio_values = declaration_values(studio)
        row = {"name": obj["name"], "importedName": name, "launchPad": baseline, "automationStudio": studio,
               "declarationsMatch": None, "savedToCompiled": "not_checked"}
        if None in baseline_values or None in studio_values or studio["mode"] not in MODES:
            path = readback if None in baseline_values else origin
            issues.append(("launchpad_recovery_unverified", path, f"Missing or unsupported recovery declaration at {name}", "warning"))
        else:
            row["declarationsMatch"] = baseline_values == studio_values
            if not row["declarationsMatch"]:
                issues.append(("launchpad_recovery_mismatch", origin,
                               f"Mode, station eligibility, or explicit point count differs at {name}", "error"))
        if before is not None:
            saved_station = as_declaration(before.get(name, {}))
            row["savedAutomationStudio"] = saved_station
            saved_values = declaration_values(saved_station)
            if (None in saved_values or None in studio_values
                    or saved_station["mode"] not in MODES or studio["mode"] not in MODES):
                row["savedToCompiled"] = "unverified"
                if None not in studio_values and studio["mode"] in MODES:
                    issues.append(("ruc_recovery_unverified", project, f"Cannot verify saved recovery at {name}", "warning"))
            elif recovery_matches(saved_station, studio):
                row["savedToCompiled"] = "matched"
            else:
                row["savedToCompiled"] = "issues"
                issues.append(("ruc_recovery_mismatch", origin,
                               f"Compiled recovery mode, station flag, or indexed point sequence differs at {name}", "error"))
        result["stations"].append(row)
    if not result["stations"]:
        issues.append(("launchpad_recovery_unverified", readback, "No matched station to compare", "warning"))
    result["state"] = "issues" if any(row[3] == "error" for row in issues) else "unverified" if issues else "matched"
    return result, issues

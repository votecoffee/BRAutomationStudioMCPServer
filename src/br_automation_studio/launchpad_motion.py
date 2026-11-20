"""Read LaunchPad exit parameters and the linked AS4 assembly's in-plane limits."""

from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path

LIMIT_UNITS = {"InPlaneVelLim": "m/s", "InPlaneAccLim": "m/s²"}
MODEL_EXIT_FIELDS = ("exit_velocity", "exit_acceleration", "exit_payload_weight")


def read_model_exit(node):
    values = {}
    for key in MODEL_EXIT_FIELDS:
        value = node.get(key)
        numeric = type(value) is int or (type(value) is float and isfinite(value))
        values[key] = value if numeric else None
    return values


def read_limit(field):
    record = field.copy()
    if field["state"] == "declared":
        try:
            value = Decimal(field["raw"])
            if not value.is_finite() or value <= 0:
                record["state"] = "unverified"
        except (InvalidOperation, TypeError):
            record["state"] = "unverified"
    return record


def compare_limit(saved, compiled):
    if "unverified" in (saved["state"], compiled["state"]):
        return "unverified"
    if saved["state"] == "absent":
        return "materialized" if compiled["state"] == "declared" else "unverified"
    if compiled["state"] == "absent":
        return "mismatch"
    return "matched" if Decimal(saved["raw"]) == Decimal(compiled["raw"]) else "mismatch"


def assembly_limits(rows, assembly):
    return next((row["motionLimits"] for row in rows if row["assembly"] == assembly), None)


def review_motion(assembly, links, saved_links, objects, renamed, readback, project):
    selected = assembly_limits(links, assembly)
    saved = assembly_limits(saved_links, assembly) if saved_links is not None else selected
    compiled = selected if saved_links is not None else None
    exits = [{"name": obj["name"], "importedName": renamed[obj["name"]], "type": obj["type"],
              **obj["modelExit"]} for obj in objects]
    result = {"assembly": assembly, "limits": {}, "launchPadExitParameters": exits}
    issues = []
    missing = [row["name"] for row in exits if any(row[key] is None for key in MODEL_EXIT_FIELDS)]
    if missing:
        issues.append(("launchpad_motion_unverified", readback,
                       f"Missing or unsupported LaunchPad exit values at {', '.join(missing)}", "warning"))
    if saved is None or selected is None:
        issues.append(("assembly_motion_unverified", project, "Cannot read the selected assembly limits", "warning"))
    else:
        result["savedPath"] = saved["path"]
        result["compiledPath"] = compiled["path"] if compiled is not None else None
        for key, unit in LIMIT_UNITS.items():
            before = read_limit(saved["fields"][key])
            after = read_limit(compiled["fields"][key]) if compiled is not None else None
            comparison = compare_limit(before, after) if after is not None else "not_checked"
            result["limits"][key] = {"unit": unit, "saved": before, "compiled": after, "comparison": comparison}
            path = Path(compiled["path"] if compiled is not None else saved["path"])
            if comparison == "mismatch":
                issues.append(("ruc_motion_limit_mismatch", path,
                               f"Compiled {key} differs from its explicit saved value", "error"))
            elif before["state"] == "unverified" or comparison == "unverified":
                issues.append(("assembly_motion_unverified", path,
                               f"Cannot verify the saved or compiled declaration for {key}", "warning"))
    result["state"] = "issues" if any(row[3] == "error" for row in issues) else "unverified" if issues else "read"
    return result, issues

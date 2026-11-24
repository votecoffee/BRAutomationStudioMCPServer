
"""Bind one reviewed LaunchPad export to a saved manager and retain its inputs."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from shutil import copyfile

from .assembly_io import assembly_io_values, comparison as io_comparison, pinned_assembly_io
from .launchpad_baseline import compare_cycle, names_for, nonempty, read_launchpad_save, route_baseline
from .launchpad_export import review_launchpad_export
from .launchpad_layout import compare_layout, linked_layout, master_offsets, native_segments
from .launchpad_motion import LIMIT_UNITS, assembly_limits, compare_limit, exit_parameters, missing_exit_parameters, read_limit
from .launchpad_recovery import MODES, STARTING_FLAGS, as_declaration, declaration_values, recovery_matches, review_recovery
from .links import RULE_ROLES, rule_key
from .project import SavedFiles


RETAINED = {
    "project": Path("launchpad/source.lpj"),
    "readback": Path("launchpad/readback.json"),
    "export": Path("launchpad/export.x6d"),
    "nameMap": Path("launchpad/name-map.json"),
}


@dataclass(frozen=True)
class LaunchPadBuildInputs:
    project: Path
    readback: Path
    export: Path
    manager: str
    name_map: Path | None = None
    verify_layout: bool = False
    verify_positions: bool = False
    widget_id: str | None = None
    verify_rules: bool = False
    verify_recovery: bool = False
    verify_motion_limits: bool = False
    read_axes: list[str] | None = None
    require_traffic_details: bool = False


def selected_manager(manager, managers):
    matches = [row for row in managers if row["manager"] == manager]
    if len(matches) != 1:
        raise ValueError(f"Expected one Traffic Manager {manager!r} in the selected target")
    return matches[0]


def checked_manager(binding, managers):
    manager = selected_manager(binding["manager"], managers)
    expected = binding["objects"]
    if not isinstance(expected, list) or not expected or any(
        not isinstance(row, dict) or not all(nonempty(row.get(key)) for key in ("name", "type", "geometryId", "importedName"))
        for row in expected
    ):
        raise ValueError("The build plan has no usable LaunchPad cycle")
    names = {row["name"]: row["importedName"] for row in expected}
    comparison = compare_cycle(expected, names, manager)
    if any(comparison[key] for key in ("missingObjects", "additionalObjects", "missingEdges", "unexpectedEdges")):
        raise ValueError(f"Traffic Manager {binding['manager']!r} objects or directed edges differ from the reviewed LaunchPad route")
    return comparison | {"assembly": manager["assembly"]}


def plan_launchpad_route(inputs, managers):
    report = review_launchpad_export(inputs.project, inputs.readback, inputs.export)
    review = report["exportReview"]
    if review["state"] != "matched":
        raise ValueError(f"The LaunchPad export must match the saved cycle: {report['diagnostics'][0]['message']}")
    source = review["launchPad"]
    evidence = {
        "project": {"path": source["sourcePath"], "sha256": source["sourceSha256"]},
        "readback": {"path": source["readbackPath"], "sha256": source["readbackSha256"]},
        "export": {key: review["export"][key] for key in ("path", "sha256")},
    }
    objects = review["expectedObjects"]
    renames, mapping = names_for(objects, inputs.name_map)
    if mapping is not None:
        evidence["nameMap"] = mapping
    binding = {"manager": inputs.manager, "files": evidence,
               "objects": [row | {"importedName": renames[row["name"]]} for row in objects]}
    checked_manager(binding, managers)
    return binding


def grid_identity(summary):
    return {key: summary[key] for key in ("count", "masterPositionMm", "offsetsFromMasterMm")}


def checked_launchpad_save(inputs, binding):
    source, decoded = read_launchpad_save(inputs.project, inputs.readback)
    if any(source[key] != binding["files"][name]["sha256"]
           for key, name in (("sourceSha256", "project"), ("readbackSha256", "readback"))):
        raise ValueError("The LaunchPad save or readback changed during build planning")
    return decoded


def plan_launchpad_layout(inputs, binding, managers, assembly_links):
    decoded = checked_launchpad_save(inputs, binding)
    assembly = selected_manager(binding["manager"], managers)["assembly"]
    layout = linked_layout(assembly_links, assembly)
    comparison, issues = compare_layout(decoded, layout)
    if issues:
        raise ValueError(issues[0][1])
    return {"assembly": assembly, "layoutId": layout["layoutId"], "path": layout["path"],
            "gridMm": comparison["gridMm"], "segmentType": comparison["segmentType"],
            "compatibleAxisMappings": comparison["compatibleAxisMappings"],
            "grid": grid_identity(comparison["automationStudio"])}


def compiled_launchpad_layout(pinned, assembly, assembly_links):
    if not isinstance(pinned, dict):
        raise ValueError("Expected a pinned LaunchPad layout")
    layout = linked_layout(assembly_links, assembly)
    summary, _ = master_offsets(native_segments(layout), "Compiled Automation Studio layout")
    grid = grid_identity(summary)
    matched = (assembly, layout["layoutId"], grid) == (pinned["assembly"], pinned["layoutId"], pinned["grid"])
    return {"state": "matched" if matched else "issues", "assembly": assembly, "layoutId": layout["layoutId"],
            "path": layout["path"], "gridMm": pinned["gridMm"], "segmentType": pinned["segmentType"],
            "plannedGrid": pinned["grid"], "compiledGrid": grid}


def decimal_position(values, name):
    message = f"Traffic object {name!r} needs two finite X/Y values in metres"
    if not isinstance(values, list) or len(values) != 2 or not all(nonempty(value) for value in values):
        raise ValueError(message)
    try:
        xy = tuple(Decimal(value) for value in values)
    except InvalidOperation as error:
        raise ValueError(message) from error
    if not all(value.is_finite() for value in xy):
        raise ValueError(message)
    return xy


def launchpad_positions(binding, managers):
    manager = selected_manager(binding["manager"], managers)
    expected = {row["importedName"]: row["type"] for row in binding["objects"]}
    measured = {}
    for row in manager["positionEvidence"]:
        name = row["name"]
        if name in measured or row["mode"] != "Absolute" or expected.get(name) != row["type"]:
            raise ValueError(f"Traffic object {name!r} needs one Absolute position of the reviewed type")
        decimal_position(row["xy"], name)
        measured[name] = {"type": row["type"], "xyMetres": row["xy"], "path": row["path"]}
    if measured.keys() != expected.keys():
        raise ValueError("Expected positions for every object in the reviewed LaunchPad route")
    return measured


def compiled_launchpad_positions(binding, managers):
    pinned = binding["positions"]
    compiled = launchpad_positions(binding, managers)
    if not isinstance(pinned, dict) or pinned.keys() != compiled.keys():
        raise ValueError("The build plan has an invalid set of LaunchPad positions")
    moved = []
    for name, actual in compiled.items():
        saved = pinned[name]
        if not isinstance(saved, dict) or saved.get("type") != actual["type"]:
            raise ValueError(f"The build plan has an invalid position type for {name!r}")
        if decimal_position(saved.get("xyMetres"), name) != decimal_position(actual["xyMetres"], name):
            moved.append(name)
    return {"state": "issues" if moved else "matched", "planned": pinned, "compiled": compiled, "movedObjects": moved}


def launchpad_rules(binding, managers):
    manager = selected_manager(binding["manager"], managers)
    if not manager["ruleLinksVerified"] or len(set(manager["ruleLists"])) != len(manager["ruleLists"]):
        raise ValueError(f"Expected readable, unique rule-list links for Traffic Manager {binding['manager']!r}")
    lists = {}
    for row in manager["ruleEvidence"]:
        if row["state"] != "read":
            raise ValueError(f"Rule list {row['id']!r} could not be verified in {row['path']}")
        lists[row["id"]] = [
            {"name": rule["name"], "type": rule["type"], **{
                role: sorted(rule[role], key=lambda item: (item["name"], item["type"])) for _, role in RULE_ROLES
            }} for rule in sorted(row["rules"], key=rule_key)
        ]
    return lists


def plan_launchpad_recovery(inputs, binding, managers, project):
    decoded = checked_launchpad_save(inputs, binding)
    objects = binding["objects"]
    renamed = {row["name"]: row["importedName"] for row in objects}
    manager = selected_manager(binding["manager"], managers)
    review, issues = review_recovery(decoded, objects, renamed, manager["recoveryEvidence"], None,
                                     inputs.readback, project)
    if issues:
        raise ValueError(str(issues[0][2]))
    return {"launchPadStartingPositions": review["launchPadStartingPositions"],
            "stations": {row["importedName"]: row["automationStudio"] for row in review["stations"]}}


def station_recovery_records(binding, managers):
    expected = {row["importedName"] for row in binding["objects"] if row["type"] == "Station"}
    manager = selected_manager(binding["manager"], managers)
    measured = {}
    for row in manager["recoveryEvidence"]:
        name = row["name"]
        record = as_declaration(row)
        if name in measured or record["mode"] not in MODES or None in declaration_values(record):
            raise ValueError(f"Expected a supported recovery declaration for station {name!r} in {row['path']}")
        measured[name] = record
    if not expected or measured.keys() != expected:
        raise ValueError("Expected recovery declarations for all reviewed stations")
    return measured


def compiled_launchpad_recovery(binding, saved_managers, compiled_managers):
    planned = binding["recovery"]
    expected = station_recovery_records(binding, saved_managers)
    if not isinstance(planned, dict) or planned.get("stations") != expected:
        raise ValueError("The pinned recovery declarations differ from the saved manager")
    flags = planned.get("launchPadStartingPositions")
    if not isinstance(flags, dict) or any(type(flags.get(name)) is not bool for name in STARTING_FLAGS):
        raise ValueError("The build plan has no verified LaunchPad starting-position flags")
    compiled = station_recovery_records(binding, compiled_managers)
    changed = sorted(name for name, record in compiled.items() if not recovery_matches(expected[name], record))
    return {"state": "issues" if changed else "matched", "planned": planned, "compiled": compiled,
            "changedStations": changed}


def motion_limit_values(links, assembly):
    reading = assembly_limits(links, assembly)
    if reading is None:
        raise ValueError(f"Cannot read in-plane limits for the reviewed assembly {assembly!r}")
    return {"assembly": assembly, "path": reading["path"], "fields": {
        key: read_limit(reading["fields"][key]) | {"unit": unit} for key, unit in LIMIT_UNITS.items()
    }}


def require_explicit_motion_limits(limits):
    missing = [name for name, field in limits["fields"].items() if field["state"] != "declared"]
    if missing:
        raise ValueError(f"Explicit positive limits are required in {limits['path']}: {', '.join(missing)}")


def plan_launchpad_motion_limits(inputs, binding, managers, links):
    assembly = selected_manager(binding["manager"], managers)["assembly"]
    saved = motion_limit_values(links, assembly)
    require_explicit_motion_limits(saved)
    decoded = checked_launchpad_save(inputs, binding)
    _, objects = route_baseline({}, decoded, False, include_motion=True)
    renamed = {row["name"]: row["importedName"] for row in binding["objects"]}
    exits = exit_parameters(objects, renamed)
    if missing := missing_exit_parameters(exits):
        raise ValueError(f"Missing or unsupported LaunchPad exit values at {', '.join(missing)}")
    return {"saved": saved, "launchPadExitParameters": exits}


def compiled_launchpad_motion_limits(binding, assembly, saved_links, compiled_links):
    planned = binding["motionLimits"]
    saved = motion_limit_values(saved_links, assembly)
    require_explicit_motion_limits(saved)
    if not isinstance(planned, dict) or planned.get("saved") != saved:
        raise ValueError("The pinned in-plane limits differ from the saved assembly")
    compiled = motion_limit_values(compiled_links, assembly)
    results = {key: compare_limit(before, compiled["fields"][key]) for key, before in saved["fields"].items()}
    state = "issues" if "mismatch" in results.values() else "unverified" if "unverified" in results.values() else "matched"
    return {"state": state, "planned": planned, "compiled": compiled, "comparisons": results}


def plan_launchpad_assembly_io(inputs, binding, report):
    assembly = selected_manager(binding["manager"], report["trafficManagers"])["assembly"]
    return pinned_assembly_io(assembly, report["assemblyLinks"], report["globalDeclarations"], inputs.read_axes)


def compiled_launchpad_assembly_io(binding, assembly, saved, compiled_links):
    planned = binding["assemblyIo"]
    if not isinstance(planned, dict):
        raise ValueError("The build plan has no usable assembly I/O evidence")
    evidence = pinned_assembly_io(assembly, saved["assemblyLinks"], saved["globalDeclarations"],
                                  planned.get("requiredReadAxes"))
    if planned != evidence:
        raise ValueError("The pinned assembly I/O differs from the saved assembly and declarations")
    compiled = assembly_io_values(assembly, compiled_links)
    comparisons = {group: {key: io_comparison(before, compiled[group][key])
                           for key, before in evidence["saved"][group].items() if before["state"] == "declared"}
                   for group in ("references", "readAxes")}
    outcomes = {state for group in comparisons.values() for state in group.values()}
    state = "issues" if "mismatch" in outcomes else "unverified" if "unverified" in outcomes else "matched"
    return {"state": state, "planned": planned, "compiled": compiled, "comparisons": comparisons}


def retained_launchpad_files(reader, binding):
    if not isinstance(binding, dict) or not isinstance(binding.get("files"), dict):
        raise ValueError("The build plan has no LaunchPad file identities")
    files = binding["files"]
    if not {"project", "readback", "export"} <= files.keys() <= RETAINED.keys():
        raise ValueError("The build plan has an invalid LaunchPad file set")
    result = {}
    for name, original in files.items():
        path = RETAINED[name]
        if not isinstance(original, dict) or not isinstance(original.get("sha256"), str):
            raise ValueError(f"The build plan is missing the LaunchPad {name} hash")
        digest = reader.digest(path)
        if digest != original["sha256"]:
            raise ValueError(f"LaunchPad evidence is missing or changed at {path.as_posix()}")
        result[name] = {"path": path.as_posix(), "sha256": digest}
    return result


def retain_launchpad_route(job_directory, binding):
    (job_directory / "launchpad").mkdir()
    for name, original in binding["files"].items():
        copyfile(original["path"], job_directory / RETAINED[name])
    return retained_launchpad_files(SavedFiles(job_directory), binding)

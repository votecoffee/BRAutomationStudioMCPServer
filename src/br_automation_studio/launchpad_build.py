
"""Bind one reviewed LaunchPad export to a saved manager and retain its inputs."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from shutil import copyfile

from .launchpad_baseline import compare_cycle, names_for, nonempty, read_launchpad_save
from .launchpad_export import review_launchpad_export
from .launchpad_layout import compare_layout, linked_layout, master_offsets, native_segments
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


def plan_launchpad_layout(inputs, binding, managers, assembly_links):
    source, decoded = read_launchpad_save(inputs.project, inputs.readback)
    if any(source[key] != binding["files"][name]["sha256"]
           for key, name in (("sourceSha256", "project"), ("readbackSha256", "readback"))):
        raise ValueError("The LaunchPad save or readback changed during layout review")
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

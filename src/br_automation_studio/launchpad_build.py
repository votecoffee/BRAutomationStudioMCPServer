
"""Bind one reviewed LaunchPad export to a saved manager and retain its inputs."""

from dataclasses import dataclass
from pathlib import Path
from shutil import copyfile

from .launchpad_baseline import compare_cycle, names_for, nonempty
from .launchpad_export import review_launchpad_export
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


def checked_manager(binding, managers):
    matches = [row for row in managers if row["manager"] == binding["manager"]]
    if len(matches) != 1:
        raise ValueError(f"Expected one Traffic Manager {binding['manager']!r} in the selected target")
    expected = binding["objects"]
    if not isinstance(expected, list) or not expected or any(
        not isinstance(row, dict) or not all(nonempty(row.get(key)) for key in ("name", "type", "geometryId", "importedName"))
        for row in expected
    ):
        raise ValueError("The build plan has no usable LaunchPad cycle")
    names = {row["name"]: row["importedName"] for row in expected}
    comparison = compare_cycle(expected, names, matches[0])
    if any(comparison[key] for key in ("missingObjects", "additionalObjects", "missingEdges", "unexpectedEdges")):
        raise ValueError(f"Traffic Manager {binding['manager']!r} objects or directed edges differ from the reviewed LaunchPad route")
    return comparison


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

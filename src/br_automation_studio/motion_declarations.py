"""Compare descriptor-listed IEC declarations in the project and selected mapp 6D catalog."""

from pathlib import Path

from .motion_sources import library_folders, required_xml, selected_components, verify_library_reference
from .project import children, objects


LIBRARIES = ("McAcp6D", "MpTraffic")
IEC_DECLARATIONS = {".fun", ".typ", ".var"}


def declarations(source, descriptor, name, version):
    node = required_xml(source, descriptor, "Library")
    if node.get("Version") != version:
        raise ValueError(f"{name} descriptor {descriptor.as_posix()} does not declare selected version {version}")
    entries = [item.text for group in children(node, "Files") for item in children(group, "File")]
    entries.extend(item.text for item in objects(node, "File"))
    files = {}
    paths = [entry.strip() for entry in entries if entry and Path(entry.strip()).suffix.casefold() in IEC_DECLARATIONS]
    for entry in paths:
        leaf = entry.casefold()
        path = source.member(descriptor, entry)
        if path is None or leaf in files:
            raise ValueError(f"Duplicate or invalid declaration {entry!r} in {descriptor.as_posix()}")
        digest = source.digest(path)
        if digest is None:
            raise ValueError(f"Could not hash declared IEC file {path.as_posix()}")
        files[leaf] = {"path": path.as_posix(), "sha256": digest}
    if any(name.casefold() + suffix not in files for suffix in (".fun", ".typ")):
        raise ValueError(f"{descriptor.as_posix()} must declare {name}.fun and {name}.typ")
    return files


def compare_library(saved, studio, software, folder, name, component):
    version, selector = component.get("version"), component.get("source")
    if not version or not selector or Path(selector).suffix.casefold() != ".lby":
        raise ValueError(f"No verified selected mapp 6D catalog library for {name}")
    verify_library_reference(software, name, folder)
    catalog = Path(selector)
    installed = declarations(studio, catalog, name, version)
    result = {"name": name, "version": version, "catalogDescriptor": selector, "projectDescriptor": None, "files": []}
    if folder is None:
        result.update(state="catalog_only", files=[{"name": file, "catalog": entry, "project": None, "state": "catalog_only"}
                                                  for file, entry in sorted(installed.items())])
        return result

    descriptor = folder / "binary.lby"
    project = declarations(saved, descriptor, name, version)
    result.update(state="matched", projectDescriptor=descriptor.as_posix())
    for file in sorted(project.keys() | installed.keys()):
        local, packaged = project.get(file), installed.get(file)
        state = "matched"
        if local is None or packaged is None:
            state = "different_membership"
            message = f"{name} declaration {file} must be listed in the project and selected catalog descriptors"
        elif local["sha256"] != packaged["sha256"]:
            state = "different_bytes"
            message = f"{name} declaration {file} has different bytes in the project and selected catalog"
        if state != "matched":
            saved.issue("error", "motion_declaration_mismatch", descriptor, message)
            result["state"] = "issues"
        result["files"].append({"name": file, "project": local, "catalog": packaged, "state": state})
    return result


def review_motion_declarations(saved, studio, report, packages):
    result = {"state": "unverified", "libraries": []}
    try:
        config, cpu = report["selectedConfiguration"], report["selectedCpu"]
        if not config or not cpu:
            raise ValueError("Select the saved configuration and CPU before reviewing IEC declarations")
        matches = [item for item in packages if item["name"] == "mapp6D" and item["internalVersion"]]
        if len(matches) != 1:
            raise ValueError("Verify one selected installed mapp 6D package before reviewing IEC declarations")
        package, = matches
        logical = Path("Logical/Package.pkg")
        diagnostics_before = len(saved.diagnostics)
        folders = library_folders(saved, logical, required_xml(saved, logical, "Package"))
        if len(saved.diagnostics) != diagnostics_before:
            raise ValueError("Could not verify the saved motion library registrations")
        software = required_xml(saved, Path("Physical", config, cpu, "Cpu.sw"), "SwConfiguration")
    except (OSError, RuntimeError, ValueError) as error:
        saved.issue("error", "motion_declarations_unverified", Path(report["project"]), str(error))
        return result

    result["mapp6DSelector"] = package["version"]
    result["mapp6DBuild"] = package["internalVersion"]
    for name in LIBRARIES:
        try:
            row = compare_library(saved, studio, software, folders.get(name.casefold()), name,
                                  selected_components([package], name))
        except (OSError, RuntimeError, ValueError) as error:
            saved.issue("error", "motion_declarations_unverified", Path(report["project"]), str(error))
            row = {"name": name, "state": "unverified", "files": []}
        result["libraries"].append(row)
    states = {row["state"] for row in result["libraries"]}
    if states & {"unverified", "issues"}:
        result["state"] = "unverified" if "unverified" in states else "issues"
    else:
        result["state"] = "verified"
    return result

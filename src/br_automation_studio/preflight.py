
"""Compare saved AS4 selections with explicitly selected installation catalogs."""

from collections import defaultdict
from pathlib import Path
import re

from .inspection import inspect_saved
from .motion_declarations import review_motion_declarations
from .project import SavedFiles, children
from .windows_version import product_version

PACKAGE_ROOT = Path("AS/TechnologyPackages")
CPU_ROOT = Path("AS/Hardware/Modules")
STUDIO_VERSION = re.compile(r"AS(4(?:\.\d+){2,3})", re.IGNORECASE)
CATALOG_VERSION = re.compile(r"(?:\d+(?:\.\d+){1,3}|n\.d)")
RUNTIME_VERSION = re.compile(r"([A-Z])(\d{1,2})\.(\d{2})", re.IGNORECASE)


def properties(node):
    return {item.get("ID"): item.get("Value") for item in children(node, "Property")}


def numeric_version(value):
    if not value or not re.fullmatch(r"\d{1,6}(?:\.\d{1,6}){0,3}", value):
        return None
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (4 - len(parts))


def within(value, lower, upper):
    actual = numeric_version(value)
    floor = numeric_version(lower)
    ceiling = numeric_version(upper)
    if actual is None or (lower and floor is None) or (upper and ceiling is None):
        return None
    return (floor is None or actual >= floor) and (ceiling is None or actual <= ceiling)


def check_range(source, path, name, value, lower, upper, *, lockout=False):
    valid = within(value, lower, upper)
    limits = f"{lower or 'any'} to {upper or 'any'}"
    if valid is None or (not lower and not upper):
        source.issue("warning", "unverified_version_range", path, f"Could not evaluate {name} {value!r} against {limits}")
    elif valid == lockout:
        code = "package_lockout" if lockout else "version_out_of_range"
        message = f"{name} {value} matches lockout" if lockout else f"{name} {value} falls outside required range"
        source.issue("error", code, path, f"{message} {limits}")


def engine(source, saved_studio=None):
    candidates = [Path("bin/BR.AS.Build.exe")]
    candidates.extend(item.relative_to(source.root) / "BR.AS.Build.exe"
                      for item in sorted(source.root.glob("bin-*")) if item.is_dir())
    tools = []
    for path in candidates:
        if not (source.root / path).is_file():
            continue
        if source.digest(path) is None:
            continue
        marker = path.with_name("BR.AS.BuildBase.dll")
        version = None
        if (source.root / marker).is_file() and source.digest(marker) is not None:
            version = product_version(source.root / marker)
        match = STUDIO_VERSION.fullmatch(version or "")
        tools.append({"path": path.as_posix(), "versionEvidence": marker.as_posix() if version else None,
                      "productVersion": version, "studioVersion": match[1] if match else None})

    if not tools:
        source.issue("error", "builder_not_found", Path("."), "BR.AS.Build.exe was not found in the selected installation")
        return tools, None

    versions = {tool["studioVersion"] for tool in tools}
    if len(versions) != 1 or None in versions:
        source.issue("warning", "unverified_studio_version", Path("."), "AS4 build base version is unknown or inconsistent across binaries")
        return tools, None

    installed, = versions
    if saved_studio is not None:
        working = numeric_version(saved_studio.get("WorkingVersion") or saved_studio.get("FileVersion")
                                  or saved_studio.get("Version"))
        if working is None or working[:2] != numeric_version(installed)[:2]:
            source.issue("warning", "studio_working_version", Path("."), f"Project working version differs from installed AS {installed} or is absent")
    return tools, installed


def runtime(source, version, selected_cpu):
    result = {"version": version, "manifest": None}
    if not selected_cpu:
        return result
    match = RUNTIME_VERSION.fullmatch(version or "")
    if match is None:
        source.issue("warning", "unverified_runtime", Path("."), f"Runtime {version!r} has no supported AS4 catalog key")
        return result
    letter, major, minor = match.groups()
    path = Path(f"{letter.upper()}{int(major):02}{minor}/SG4/Config.xml")
    node, _ = source.xml(path, "Configuration")
    if node is not None:
        result["manifest"] = path.as_posix()
    return result


def cpu_catalog(source, module_id, selected_cpu):
    if not selected_cpu:
        return []
    directory = source.member(CPU_ROOT / "_", module_id)
    if directory is None:
        return []
    configs = sorted(path / f"{module_id}.hwx" for path in (source.root / directory).glob("*")
                     if (path / f"{module_id}.hwx").is_file())
    entries = []
    for path in configs:
        relative = path.relative_to(source.root)
        node, _ = source.xml(relative, "Module")
        if node is not None:
            entries.append({"path": relative.as_posix(), "version": node.get("Version")})
    if not configs:
        source.issue("error", "cpu_catalog_missing", directory, f"No hardware catalog entries for {module_id!r}")
    return entries


def selected_package(source, selection):
    name, version = selection["name"], selection["version"]
    directory = source.member(PACKAGE_ROOT / "_", name)
    if directory is None:
        return None
    if not isinstance(version, str) or CATALOG_VERSION.fullmatch(version) is None:
        source.issue("warning", "unverified_package_selection", directory, f"Unsupported selector for {name}: {version!r}")
        return None

    candidates = sorted(item.name for item in (source.root / directory).glob("*") if item.is_dir())
    manifest = directory / version / "ApplicationSettings/TP.cfg"
    entry = {"name": name, "version": version, "internalVersion": None, "catalogDirectories": candidates,
             "manifest": manifest.as_posix(), "components": [], "constraints": []}
    if version not in candidates:
        source.issue("error", "package_version_missing", manifest,
                     f"{name} {version} is absent from the selected catalog. Catalog directories: {', '.join(candidates) or 'none'}")
        return entry, None, manifest
    node, _ = source.xml(manifest, "Configuration")
    if node is None:
        return entry, None, manifest

    elements = [item for item in children(node, "Element") if item.get("Type") == "TechnologyPackage"]
    if len(elements) != 1:
        source.issue("error", "invalid_package_manifest", manifest, "Expected one TechnologyPackage element")
        return entry, None, manifest
    package = elements[0]
    header = properties(package)
    if header.get("InternalName") != name or header.get("Version") != version:
        source.issue("error", "package_identity_mismatch", manifest, f"Manifest does not identify {name} {version}")
        return entry, None, manifest
    entry["internalVersion"] = header.get("InternalVersion")
    return entry, package, manifest


def module_payloads(source, directory, name):
    candidates = sorted(folder / f"{name}.br" for folder in (source.root / directory).glob("*/*")
                        if (folder / f"{name}.br").is_file())
    paths = [item.relative_to(source.root) for item in candidates]
    return [item.as_posix() for item in paths if source.resolve(item) is not None]


def package_components(source, directory, manifest, selection, package, providers):
    components = []
    ranges = {}
    transfer = {}
    for group in children(package, "Group"):
        if group.get("ID") == "SubVersions":
            ranges.update((item.get("ID"), properties(item)) for item in children(group, "Group"))
        elif group.get("ID") == "Transfer":
            transfer.update(properties(group))

    for name, info in ranges.items():
        if info.get("IsMandatory") == "true" and name not in selection:
            source.issue("warning", "implicit_component_version", manifest, f"No explicit version selected for mandatory {name}")
    for name, version in selection.items():
        entry = {"name": name, "version": version, "source": None}
        components.append(entry)
        info = ranges.get(name, {})
        subtype = info.get("Type")
        if subtype in {"Library", "Module"} and CATALOG_VERSION.fullmatch(version or ""):
            member = source.member(manifest, name)
            if member is None:
                continue
            check_range(source, manifest, name, version, info.get("FromVersion"), info.get("ToVersion"))
            if subtype == "Library":
                descriptor = directory / "Library" / member.name / f"V{version}" / "binary.lby"
                if add_library(source, descriptor, name, providers, version):
                    entry["source"] = descriptor.as_posix()
            else:
                payload = directory / "Modules" / member.name / f"V{version}"
                binaries = module_payloads(source, payload, member.name)
                if binaries:
                    entry["source"] = payload.as_posix()
                    entry["payloadCandidates"] = binaries
                else:
                    source.issue("error", "component_payload_missing", payload, f"No module payload for {name} {version}")
        else:
            # Transfer components such as mvLoader can be listed without a selectable
            # version in TP.cfg. The directory alone cannot verify the saved version.
            if transfer.get(f"Module_{name}") == name and source.member(manifest, name) is not None:
                entry["payloadCandidates"] = module_payloads(source, directory / "Transfer/Modules", name)
            source.issue("warning", "unverified_component", manifest, f"No supported versioned manifest entry for {name} {version}")
    return components


def add_library(source, path, name, providers, expected=None):
    node, _ = source.xml(path, "Library")
    if node is None:
        return False
    version = node.get("Version")
    if not version or (expected and version != expected):
        source.issue("error", "library_version_mismatch", path,
                     f"{name} descriptor version {version!r} does not match the selection {expected!r}")
        return False
    providers[name].append((source, path, version, node))
    return True


def configured_libraries(source, report, providers):
    configuration = report["selectedConfiguration"]
    cpu = report["selectedCpu"]
    if not configuration or not cpu:
        return
    path = Path("Physical", configuration, cpu, "Cpu.sw")
    node, _ = source.xml(path, "SwConfiguration", optional=True)
    if node is None:
        return
    for group in children(node, "Libraries"):
        for entry in children(group, "LibraryObject"):
            name = entry.get("Name")
            reference = (entry.get("Source") or "").split(".")
            if len(reference) < 2 or reference[-1] != "lby" or reference[-2] != name:
                source.issue("warning", "unverified_cpu_library", path, f"Unsupported saved library source for {name!r}")
                continue
            folder = Path("Logical")
            for part in reference[:-1]:
                folder = source.member(folder / "_", part)
                if folder is None:
                    break
            if folder is not None:
                add_library(source, folder / "binary.lby", name, providers)


def check_library_dependencies(providers):
    results = []
    for name, entries in providers.items():
        for source, path, version, node in entries:
            for group in children(node, "Dependencies"):
                for dependency in children(group, "Dependency"):
                    target = dependency.get("ObjectName")
                    lower = dependency.get("FromVersion")
                    upper = dependency.get("ToVersion")
                    candidates = providers.get(target, [])
                    versions = sorted({entry[2] for entry in candidates})
                    results.append({"library": name, "libraryVersion": version, "requires": target,
                                    "fromVersion": lower, "toVersion": upper, "selectedVersions": versions})
                    if not versions:
                        source.issue("error", "dependency_not_selected", path,
                                     f"{name} requires {target}, with no provider in the saved selections checked")
                    elif len(versions) != 1:
                        source.issue("warning", "ambiguous_dependency", path, f"{target} has several selected versions: {', '.join(versions)}")
                    elif lower or upper:
                        check_range(source, path, target, versions[0], lower, upper)
    return results


def build_preflight(path, studio_root, runtime_root=None, configuration=None, cpu=None, *,
                    verify_motion_declarations=False, include_layout=False, geometry_manager=None):
    saved = SavedFiles(path.parent)
    report = inspect_saved(saved, Path(path.name), configuration, cpu,
                           include_layout=include_layout, geometry_manager=geometry_manager)
    studio = SavedFiles(studio_root)
    runtimes = SavedFiles(runtime_root if runtime_root is not None else studio.root.parent / "AS/System")

    builders, studio_version = engine(studio, report["automationStudio"])
    runtime_check = runtime(runtimes, report["cpu"].get("runtimeVersion"), report["selectedCpu"])
    hardware = cpu_catalog(studio, report["cpu"].get("moduleId"), report["selectedCpu"])
    selections = {row["name"]: row["version"] for row in report["technologyPackages"]}
    providers = defaultdict(list)
    packages = []
    for row in report["technologyPackages"]:
        inspected = selected_package(studio, row)
        if inspected is None:
            continue
        entry, package, manifest = inspected
        packages.append(entry)
        if package is None:
            continue
        directory = manifest.parent.parent
        entry["components"] = package_components(studio, directory, manifest, row["subversions"], package, providers)
        for group in children(package, "Group"):
            identifier = group.get("ID") or ""
            if not identifier.startswith(("Dependency", "Lockout")):
                continue
            limits = properties(group)
            entry["constraints"].append({"id": identifier, **limits})
            target = limits.get("VersionType")
            selected_version = studio_version if target == "AutomationStudio" else selections.get(target)
            if selected_version:
                check_range(studio, manifest, target, selected_version, limits.get("FromVersion"),
                            limits.get("ToVersion"), lockout=identifier.startswith("Lockout"))
    configured_libraries(saved, report, providers)
    dependencies = check_library_dependencies(providers)
    if verify_motion_declarations:
        report["motionDeclarations"] = review_motion_declarations(saved, studio, report, packages)
    report["snapshot"] = saved.snapshot()
    report["evidence"] = "saved_files_and_installation_metadata"
    report["preflight"] = {
        "buildTools": builders, "studioRoot": str(studio.root), "studioSnapshot": studio.snapshot(),
        "runtimeRoot": str(runtimes.root), "runtimeSnapshot": runtimes.snapshot(), "runtime": runtime_check,
        "cpuCatalog": hardware, "technologyPackages": packages, "libraryDependencies": dependencies,
    }
    for scope, reader in (("project", saved), ("studio", studio), ("runtime", runtimes)):
        for issue in reader.diagnostics:
            issue["scope"] = scope
    report["diagnostics"] = saved.diagnostics + studio.diagnostics + runtimes.diagnostics
    return report

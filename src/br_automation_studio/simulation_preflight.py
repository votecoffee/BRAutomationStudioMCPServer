"""Compare a saved AS4 project and workstation with a documented simulator pair."""

from dataclasses import asdict, dataclass
from hashlib import file_digest
from pathlib import Path

from .launchpad_export import inventory_export
from .mptraffic_definition import check_installed_definition
from .preflight import (PACKAGE_ROOT, build_preflight, check_range, engine, numeric_version, runtime_order,
                        selected_package, within)
from .project import SavedFiles
from .windows_msi import INSTALLSTATE_DEFAULT, installed_product


@dataclass(frozen=True)
class SimulatorPair:
    minimumSelector: str
    simulatorVersion: str
    msiVersion: str
    productCode: str
    mediaSha256: str
    controllerFirmware: str
    segmentFirmware: str
    segmentApplicationData: str
    maintenanceToolVersion: str


# The B&R AS4 system requirements pair the releases. MSI identities and hashes
# come from the reviewed B&R simulator ZIPs.
PAIRS = {
    "2.4": SimulatorPair("2.4.1", "117.20.43", "1.20.43", "{6E1CEAC8-5E59-40A4-A27F-0309A8B71509}",
                         "fe835efeb740e6f7839eed85de305de77f8ecc9d5bcf21d402f8af9a8f42b2e9",
                         "117.20.49", "24-8A", "24-01", "117.16.01"),
    "2.7": SimulatorPair("2.7", "117.25.53", "1.25.53", "{8B409D4F-7B75-4C59-9A78-20D7F3B759BA}",
                         "f33645d5197410b970acc528dfab051971504a6c0457632f5558ba5994de6afe",
                         "117.25.53", "24-9F", "24-01", "117.26.01"),
}
# Compare against the reviewed ZIP bytes, with the release identities from B&R's
# catalog and the segment image names from the signed Flyway MSI File table.
COMPANION_MEDIA_27 = {
    "controllerFirmwareZip": {
        "release": "ACOPOS 6D controller firmware 117.25.53",
        "filename": "117-025-053.zip",
        "sha256": "0165021b5de75a20916f75b7986eff475a80db92e359d125b929dc69096e5797",
        "payload": "117-025-053.pmiu",
    },
    "segmentFirmwareZip": {
        "release": "Flyway Update Tool 1.2.7",
        "filename": "FlywayUpdateTool_v1_2_7_FW249F_AD2401.zip",
        "sha256": "a3d8bd6ffab740cf7ef3379757910a8c2913dff344a979cf7e90fc5df3a10e69",
        "payload": "stator_update_FW249F_AD2401.pmiflwu",
        "msiFileTablePayloads": (
            "stator_update_FW2492_AD2401.pmiflwu",
            "stator_update_FW248A_AD2401.pmiflwu",
            "stator_update_FW249F_AD2401.pmiflwu",
        ),
    },
    "maintenanceToolZip": {
        "release": "Planar Motor Tool 117.26.01",
        "filename": "PMTool117.26.01.zip",
        "sha256": "ace9d5bead725f57cbbf08a35bcb048cb1986911636e0dc4c2bdc6ccdb0d2b07",
        "payload": "PlanarMotorTool.msi",
    },
}
SIMULATION_PACKAGES = ("mapp6D", "mappMotion", "mappView")
MINIMUM_STUDIO = "4.9.2"
MINIMUM_RUNTIME = "C4.90"
MINIMUM_MOTION = "5.26"
MINIMUMS = {"automationStudio": MINIMUM_STUDIO, "automationRuntime": MINIMUM_RUNTIME, "mappMotion": MINIMUM_MOTION}
MSI_STATES = {-1: "unknown_product", 1: "advertised", 2: "different_user", INSTALLSTATE_DEFAULT: "installed"}


def same_family(selector, family):
    version = numeric_version(selector)
    return version is not None and version[:2] == numeric_version(family)[:2]


def describe_file(path, suffix):
    selected = path.resolve()
    if path.suffix.lower() != suffix or selected.suffix.lower() != suffix or not selected.is_file():
        raise ValueError(f"Select an existing {suffix} file")
    with selected.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
        size = stream.seek(0, 2)
    if not size:
        raise ValueError("The selected file is empty")
    return {"path": str(selected), "bytes": size, "sha256": digest}


def file_evidence(reader, path, suffix, code):
    if path is None:
        return None
    try:
        return describe_file(path, suffix)
    except (OSError, RuntimeError, ValueError) as error:
        reader.issue("error", code, path, str(error))
        return None


def catalog_selectors(catalog, name):
    return sorted(path.name for path in (catalog.root / PACKAGE_ROOT / name).glob("*")
                  if path.is_dir() and numeric_version(path.name) is not None)


def eligible_package_versions(selector, build, minimum):
    return within(selector, minimum, None) is True and within(build, minimum, None) is True


def read_candidates(catalog, name, selectors, minimum=None, family=None):
    candidates = []
    for version in selectors:
        if family is not None and not same_family(version, family):
            continue
        issue_start = len(catalog.diagnostics)
        inspected = selected_package(catalog, {"name": name, "version": version})
        if inspected is None:
            continue
        entry, package, manifest = inspected
        build = entry["internalVersion"]
        verified = (package is not None and len(catalog.diagnostics) == issue_start
                    and (family is None or same_family(build, family))
                    and eligible_package_versions(version, build, minimum))
        candidates.append({"selector": version, "internalVersion": build, "manifest": manifest.as_posix(),
                           "verified": verified, "constraints": entry["constraints"]})
    return candidates


def check_target_catalog(catalog, family, pair, selectors):
    candidates = read_candidates(catalog, "mapp6D", selectors, pair.minimumSelector, family)
    if not any(item["verified"] for item in candidates):
        catalog.issue("error", "simulation_catalog_missing", PACKAGE_ROOT / "mapp6D",
                      f"No identified AS4 mapp 6D {family} technology package meeting {pair.minimumSelector} was found")
    return candidates


def check_project(reader, report, family, pair, candidates):
    preflight = report["preflight"]
    project = Path(report["project"])
    packages = report["technologyPackages"]
    for name in SIMULATION_PACKAGES:
        if sum(row["name"] == name for row in packages) > 1:
            reader.issue("error", "simulation_selection_ambiguous", project, f"Saved project declares {name} more than once")
    selections = {row["name"]: row["version"] for row in packages}
    selected = selections.get("mapp6D")
    if not (same_family(selected, family) and within(selected, pair.minimumSelector, None) is True):
        reader.issue("error", "simulation_project_family", project,
                     f"Saved mapp 6D selector {selected!r} does not select the reviewed {family} line")
    elif not any(item["selector"] == selected and item["verified"] for item in candidates):
        reader.issue("error", "simulation_selected_package_missing", project,
                     f"The exact saved mapp 6D selector {selected} has no verified matching installed build")

    if report["cpu"].get("moduleId") != "PC_any":
        reader.issue("error", "simulation_cpu", project, "Select an AS4 PC_any configuration for this preflight")
    tools = [row["studioVersion"] for row in preflight["buildTools"]]
    if len(set(tools)) != 1 or within(tools[0], MINIMUM_STUDIO, None) is not True:
        reader.issue("error", "simulation_studio_minimum", project, f"Verify installed AS4 {MINIMUM_STUDIO} or later")
    runtime = preflight["runtime"]
    version = runtime_order(runtime["version"])
    if not runtime["manifest"] or version is None or version < runtime_order(MINIMUM_RUNTIME):
        reader.issue("error", "simulation_runtime_minimum", project,
                     f"Verify the saved Runtime {MINIMUM_RUNTIME} or later in the selected catalog")

    catalog = {item["name"]: item for item in preflight["technologyPackages"]}
    for name, code, minimum in (
        ("mappMotion", "simulation_motion_selection", MINIMUM_MOTION),
        ("mappView", "simulation_view_selection", None),
    ):
        selector = selections.get(name)
        entry = catalog.get(name, {})
        build = entry.get("internalVersion")
        if not eligible_package_versions(selector, build, minimum):
            location = Path(entry["manifest"]) if entry else project
            requirement = f"numeric versions {minimum} or later" if minimum else "numeric versions"
            reader.issue("error", code, location,
                         f"Saved {name} selector {selector!r} and installed build {build!r} must both be {requirement}")
    components = {row["name"]: row.get("source") for row in catalog.get("mapp6D", {}).get("components", ())}
    if not all(components.get(name) for name in ("McAcp6D", "MpTraffic")):
        reader.issue("error", "simulation_motion_libraries", project,
                     "Verify the selected McAcp6D and MpTraffic library descriptors")


def check_simulator(reader, pair):
    try:
        observed = installed_product(pair.productCode)
    except OSError as error:
        reader.issue("warning", "simulator_installation_unverified", Path("."), str(error))
        return {"state": "unverified", "stateCode": None, "version": None, "matched": None}
    code = observed["stateCode"]
    observed["state"] = MSI_STATES.get(code, "unverified")
    verified = observed["state"] != "unverified" and (code != INSTALLSTATE_DEFAULT or bool(observed["version"]))
    matches = code == INSTALLSTATE_DEFAULT and observed["version"] == pair.msiVersion
    observed["matched"] = matches if verified else None
    if not verified:
        reader.issue("warning", "simulator_installation_unverified", Path("."),
                     "Could not verify the matching simulator registration and version for this user")
    elif not observed["matched"]:
        reader.issue("error", "simulator_installation_missing", Path("."),
                     f"The matching Planar Motor Simulator {pair.msiVersion} is not registered as installed for this user")
    return observed


def simulator_pair(family):
    pair = PAIRS.get(family)
    if pair is None:
        raise ValueError("Select an AS4 mapp 6D family supported by this check (2.4 or 2.7)")
    return pair


def known_media(sha256):
    for role, reference in COMPANION_MEDIA_27.items():
        if reference["sha256"] == sha256:
            return role, reference["release"]
    for pair in PAIRS.values():
        if pair.mediaSha256 == sha256:
            return "simulatorZip", f"Planar Motor Simulation Tool {pair.simulatorVersion}"
    return None, None


def check_companion_media(reader, family, media):
    results = {}
    for role, path in (media or {}).items():
        reference = COMPANION_MEDIA_27[role].copy() if family == "2.7" else None
        archive = file_evidence(reader, path, ".zip", "companion_media_invalid")
        row = results[role] = {"state": "unverified", "reference": reference, "archive": archive}
        if archive is None:
            continue
        observed_role, release = known_media(archive["sha256"])
        archive.update(observedRole=observed_role, observedRelease=release)
        if reference is None:
            reader.issue("warning", "companion_media_unreviewed", path,
                         f"No reviewed {role} ZIP for the AS4 mapp 6D {family} family")
        elif archive["sha256"] == reference["sha256"]:
            row["state"] = "matched"
        else:
            row["state"] = "mismatched"
            description = f"The ZIP matches {release}" if release else "The ZIP hash is unrecognized"
            reader.issue("error", "companion_media_mismatch", path,
                         f"{description}. Expected the reviewed {reference['release']} archive")
    return results


def check_simulator_media(reader, pair, simulator_zip):
    archive = file_evidence(reader, simulator_zip, ".zip", "simulator_media_invalid")
    if archive is not None:
        archive["matched"] = archive["sha256"] == pair.mediaSha256
        if not archive["matched"]:
            role, release = known_media(archive["sha256"])
            message = f"The selected ZIP does not match the reviewed simulator {pair.simulatorVersion} archive"
            if release is not None:
                archive.update(observedRole=role, observedRelease=release)
                message = f"The selected ZIP matches {release}. This family requires simulator {pair.simulatorVersion}"
            reader.issue("error", "simulator_media_mismatch", simulator_zip, message)
    return archive


def prerequisite_state(diagnostics):
    severities = {item["severity"] for item in diagnostics}
    return "blocked" if "error" in severities else "review_required" if "warning" in severities else "inputs_present"


def check_package_selection(reader, studio_version, installed, proposed):
    start = len(reader.diagnostics)
    versions = {"AutomationStudio": studio_version, **proposed}
    packages = {}
    constraints = []
    for name, selector in proposed.items():
        candidate = next((row for row in installed[name] if row["selector"] == selector), None)
        verified = bool(candidate and candidate["verified"])
        packages[name] = {
            "selector": selector, "verified": verified,
            "internalVersion": candidate["internalVersion"] if candidate else None,
            "manifest": candidate["manifest"] if candidate else None,
        }
        if not verified:
            reader.issue("error", "simulation_selector_unavailable", PACKAGE_ROOT / name,
                         f"The proposed {name} selector {selector} has no verified catalog candidate for this check")
            continue
        manifest = Path(candidate["manifest"])
        for constraint in candidate["constraints"]:
            target = constraint.get("VersionType")
            selected = versions.get(target)
            state = "deferred"
            if target in versions:
                state = check_range(reader, manifest, target, selected, constraint.get("FromVersion"),
                                    constraint.get("ToVersion"), lockout=constraint["id"].startswith("Lockout"))
            elif not target:
                reader.issue("warning", "unverified_version_range", manifest,
                             f"{constraint['id']} does not identify a version to compare")
                state = "unverified"
            constraints.append({**constraint, "package": name, "manifest": manifest.as_posix(),
                                "selectedVersion": selected, "state": state})
    return {"state": prerequisite_state(reader.diagnostics[start:]), "packages": packages, "constraints": constraints}


def simulation_environment(studio, family, simulator_zip=None, *, companion_media=None, package_selectors=None):
    pair = simulator_pair(family)
    reader = SavedFiles(studio)
    builders, version = engine(reader)
    if version is not None and within(version, MINIMUM_STUDIO, None) is not True:
        reader.issue("error", "simulation_studio_minimum", Path("."),
                     f"Verify installed AS4 {MINIMUM_STUDIO} or later")
    selectors = {name: catalog_selectors(reader, name) for name in SIMULATION_PACKAGES}
    candidates = check_target_catalog(reader, family, pair, selectors["mapp6D"])
    companions = {}
    for name, minimum in (("mappMotion", MINIMUM_MOTION), ("mappView", None)):
        rows = companions[name] = read_candidates(reader, name, selectors[name], minimum)
        if not any(item["verified"] for item in rows):
            version_label = f" {minimum} or later" if minimum else ""
            reader.issue("error", "simulation_companion_missing", PACKAGE_ROOT / name,
                         f"No identified AS4 {name} technology package{version_label} was found")
    selection = None
    if package_selectors is not None:
        selection = check_package_selection(reader, version, {"mapp6D": candidates, **companions}, package_selectors)
    simulator = check_simulator(reader, pair)
    archive = check_simulator_media(reader, pair, simulator_zip)
    media_checks = check_companion_media(reader, family, companion_media)
    return {
        "evidence": "simulation_environment_metadata",
        "simulationEnvironment": {
            "state": prerequisite_state(reader.diagnostics), "targetMapp6D": family,
            "reference": asdict(pair), "minimums": dict(MINIMUMS),
            "studioRoot": str(reader.root), "studioVersion": version, "buildTools": builders,
            "catalogSelectors": selectors, "installedTargetCatalog": candidates, "companionCatalog": companions,
            "targetCatalogSnapshot": reader.snapshot(), "simulatorInstallation": simulator, "simulatorZip": archive,
            **({"packageSelection": selection} if selection is not None else {}),
            **({"companionMedia": media_checks} if media_checks else {}),
        },
        "diagnostics": [dict(item, scope="simulation") for item in reader.diagnostics],
    }


def simulation_preflight(project, studio, runtime_root, configuration, cpu, family, simulator_zip=None, export=None,
                         *, verify_mptraffic_enum=False, verify_motion_declarations=False, companion_media=None,
                         inspect_launchpad_export=False):
    pair = simulator_pair(family)
    report = build_preflight(project, studio, runtime_root, configuration, cpu,
                             verify_motion_declarations=verify_motion_declarations)
    reader = SavedFiles(studio)
    candidates = check_target_catalog(reader, family, pair, catalog_selectors(reader, "mapp6D"))
    check_project(reader, report, family, pair, candidates)
    simulator = check_simulator(reader, pair)
    archive = check_simulator_media(reader, pair, simulator_zip)
    media_checks = check_companion_media(reader, family, companion_media)
    native_export = file_evidence(reader, export, ".x6d", "launchpad_export_invalid")
    if export is None:
        reader.issue("error", "launchpad_export_missing", Path("."), "Supply the native LaunchPad .x6d for the intended route")
    if native_export is not None and inspect_launchpad_export:
        export_path = Path(native_export["path"])
        try:
            native_export["structure"] = inventory_export(export_path, native_export["sha256"])
        except (OSError, ValueError) as error:
            native_export["structure"] = {"state": "unverified"}
            reader.issue("warning", "launchpad_export_structure_unverified", export_path, str(error))

    report["diagnostics"].extend(dict(item, scope="simulation") for item in reader.diagnostics)
    extra = {}
    if verify_mptraffic_enum:
        extra["mpTrafficDefinition"] = check_installed_definition(project, studio, report, "simulation")
    report["evidence"] = "simulation_prerequisite_metadata"
    report["simulationPreflight"] = {
        "state": prerequisite_state(report["diagnostics"]), "targetMapp6D": family, "reference": asdict(pair),
        "minimums": dict(MINIMUMS),
        "installedTargetCatalog": candidates, "targetCatalogSnapshot": reader.snapshot(),
        "simulatorInstallation": simulator, "simulatorZip": archive, "launchPadExport": native_export,
        **({"companionMedia": media_checks} if media_checks else {}),
        **extra,
    }
    return report

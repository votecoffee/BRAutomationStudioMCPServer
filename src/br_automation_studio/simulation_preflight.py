"""Compare a saved AS4 project and workstation with a documented simulator pair."""

from dataclasses import asdict, dataclass
from hashlib import file_digest
from pathlib import Path

from .preflight import PACKAGE_ROOT, RUNTIME_VERSION, build_preflight, numeric_version, selected_package, within
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
MINIMUM_STUDIO = "4.9.2"
MINIMUM_RUNTIME = "C4.90"
MINIMUM_MOTION = "5.26"
MSI_STATES = {-1: "unknown_product", 1: "advertised", 2: "different_user", INSTALLSTATE_DEFAULT: "installed"}


def same_family(selector, family):
    version = numeric_version(selector)
    return version is not None and version[:2] == numeric_version(family)[:2]


def runtime_order(version):
    match = RUNTIME_VERSION.fullmatch(version or "")
    if match:
        grade, major, minor = match.groups()
        return int(major), int(minor), grade.upper()
    return None


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


def check_target_catalog(catalog, family, pair):
    root = PACKAGE_ROOT / "mapp6D"
    candidates = []
    for directory in sorted((catalog.root / root).glob("*")):
        version = directory.name
        if not directory.is_dir() or not same_family(version, family):
            continue
        inspected = selected_package(catalog, {"name": "mapp6D", "version": version})
        if inspected is None:
            continue
        entry, package, manifest = inspected
        build = entry["internalVersion"]
        verified = (package is not None and same_family(build, family)
                    and within(version, pair.minimumSelector, None) is True
                    and within(build, pair.minimumSelector, None) is True)
        candidates.append({"selector": version, "internalVersion": build, "manifest": manifest.as_posix(),
                           "verified": verified})
    if not any(item["verified"] for item in candidates):
        catalog.issue("error", "simulation_catalog_missing", root,
                      f"No identified AS4 mapp 6D {family} technology package meeting {pair.minimumSelector} was found")
    return candidates


def check_project(reader, report, family, pair, candidates):
    preflight = report["preflight"]
    project = Path(report["project"])
    packages = report["technologyPackages"]
    for name in ("mapp6D", "mappMotion", "mappView"):
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
    motion = selections.get("mappMotion")
    if within(motion, MINIMUM_MOTION, None) is not True or not catalog.get("mappMotion", {}).get("internalVersion"):
        reader.issue("error", "simulation_motion_selection", project,
                     f"Select and verify an installed mapp Motion package {MINIMUM_MOTION} or later")
    if not catalog.get("mappView", {}).get("internalVersion"):
        reader.issue("error", "simulation_view_selection", project, "Select and verify an installed mapp View package")
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


def simulation_preflight(project, studio, runtime_root, configuration, cpu, family, simulator_zip=None, export=None):
    pair = PAIRS.get(family)
    if pair is None:
        raise ValueError("Select an AS4 mapp 6D family supported by this check (2.4 or 2.7)")

    report = build_preflight(project, studio, runtime_root, configuration, cpu)
    reader = SavedFiles(studio)
    candidates = check_target_catalog(reader, family, pair)
    check_project(reader, report, family, pair, candidates)
    simulator = check_simulator(reader, pair)
    archive = file_evidence(reader, simulator_zip, ".zip", "simulator_media_invalid")
    if archive is not None:
        archive["matched"] = archive["sha256"] == pair.mediaSha256
        if not archive["matched"]:
            other = next((row.simulatorVersion for row in PAIRS.values() if row.mediaSha256 == archive["sha256"]), None)
            message = (f"The selected ZIP matches simulator {other}. This family requires {pair.simulatorVersion}" if other
                       else f"The selected ZIP does not match the reviewed simulator {pair.simulatorVersion} archive")
            reader.issue("error", "simulator_media_mismatch", simulator_zip, message)
    native_export = file_evidence(reader, export, ".x6d", "launchpad_export_invalid")
    if export is None:
        reader.issue("error", "launchpad_export_missing", Path("."), "Supply the native LaunchPad .x6d for the intended route")

    report["diagnostics"].extend(dict(item, scope="simulation") for item in reader.diagnostics)
    severities = {item["severity"] for item in report["diagnostics"]}
    state = "blocked" if "error" in severities else "review_required" if "warning" in severities else "inputs_present"
    report["evidence"] = "simulation_prerequisite_metadata"
    report["simulationPreflight"] = {
        "state": state, "targetMapp6D": family, "reference": asdict(pair),
        "minimums": {"automationStudio": MINIMUM_STUDIO, "automationRuntime": MINIMUM_RUNTIME,
                     "mappMotion": MINIMUM_MOTION},
        "installedTargetCatalog": candidates, "targetCatalogSnapshot": reader.snapshot(),
        "simulatorInstallation": simulator, "simulatorZip": archive, "launchPadExport": native_export,
    }
    return report

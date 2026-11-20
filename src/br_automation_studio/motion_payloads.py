"""Pin selected AS4 SG4/IA32 motion libraries and system modules to their RUC payloads."""

from hashlib import sha256
from pathlib import Path

from .motion_sources import cpu_sources, library_folders, required_xml, selected_components, verify_library_reference
from .preflight import numeric_version
from .project import SavedFiles, XML_LIMIT, choices


TARGET = Path("SG4/IA32")
LIBRARIES = ("McAcp6D", "MpTraffic", "McBase", "MpBase")
MOTION_MODULES = (*LIBRARIES, "McAcp6DSys", "McDriveLog")


def payload_pin(source, name, path, version, origin, descriptor=None):
    digest = source.digest(path)
    if digest is None:
        raise ValueError(f"Could not hash the selected {name} payload at {path.as_posix()}")
    pin = {"name": name, "version": version, "origin": origin, "path": path.as_posix(), "sha256": digest}
    if descriptor is not None:
        pin["descriptor"] = descriptor.as_posix()
    return pin


def library_pin(saved, installed, software, folders, name, component):
    folder = folders.get(name.casefold())
    referenced = verify_library_reference(software, name, folder)
    catalog_descriptor = Path(entry) if (entry := component.get("source")) else None
    if catalog_descriptor is not None and catalog_descriptor.suffix.casefold() != ".lby":
        raise ValueError(f"The selected catalog component {name} has no library descriptor")
    if folder is not None:
        if not referenced and not component.get("version"):
            raise ValueError(f"{name} needs an explicit CPU library reference or a catalog selection")
        descriptor = folder / "binary.lby"
        reader, origin = saved, "project"
    elif catalog_descriptor is not None:
        descriptor = catalog_descriptor
        reader, origin = installed, "catalog"
    else:
        raise ValueError(f"Could not verify a selected project or catalog library for {name}")

    version = required_xml(reader, descriptor, "Library").get("Version")
    catalog_version = component.get("version")
    if numeric_version(version) is None or (catalog_version and version != catalog_version):
        raise ValueError(f"{name} descriptor version {version!r} differs from the selected version {catalog_version!r}")
    pin = payload_pin(reader, name, descriptor.parent / TARGET / f"{name}.br", version, origin, descriptor)
    if origin == "project" and catalog_descriptor is not None:
        catalog_payload = catalog_descriptor.parent / TARGET / f"{name}.br"
        if installed.digest(catalog_payload) != pin["sha256"]:
            raise ValueError(f"The project and selected catalog SG4/IA32 payloads differ for {name}")
        pin["matchingCatalogPath"] = catalog_payload.as_posix()
    return pin


def catalog_module_pin(installed, name, component):
    candidates = [Path(entry) for entry in component.get("payloadCandidates", [])
                  if Path(entry).parts[-3:] == (*TARGET.parts, f"{name}.br")]
    if len(candidates) != 1 or numeric_version(component.get("version")) is None:
        raise ValueError(f"Could not verify one selected catalog SG4/IA32 payload for {name}")
    return payload_pin(installed, name, candidates[0], component["version"], "catalog")


def drive_log_pin(saved, installed, software, logical, root, component):
    references = cpu_sources(software, "Binaries", "BinaryObject", "McDriveLog")
    if references and references[0]:
        path = choices(saved, logical, root, "File").get(references[0].casefold())
        if path is None or path.name.casefold() != "mcdrivelog.br":
            raise ValueError("The CPU's McDriveLog binary source must identify its registered Logical file")
        return payload_pin(saved, "McDriveLog", path, None, "project")
    return catalog_module_pin(installed, "McDriveLog", component)


def plan_motion_payloads(project, preflight, configuration, cpu):
    saved, installed = SavedFiles(project.parent), SavedFiles(Path(preflight["studioRoot"]))
    logical = Path("Logical/Package.pkg")
    root = required_xml(saved, logical, "Package")
    folders = library_folders(saved, logical, root)
    software = required_xml(saved, Path("Physical", configuration, cpu, "Cpu.sw"), "SwConfiguration")
    selections = {name: selected_components(preflight["technologyPackages"], name) for name in MOTION_MODULES}
    pins = [library_pin(saved, installed, software, folders, name, selections[name]) for name in LIBRARIES]
    pins.append(catalog_module_pin(installed, "McAcp6DSys", selections["McAcp6DSys"]))
    pins.append(drive_log_pin(saved, installed, software, logical, root, selections["McDriveLog"]))
    if saved.diagnostics or installed.diagnostics:
        raise ValueError("Motion payload sources have ambiguous registrations or unreadable files")
    catalog_hashes = {row["path"]: row["sha256"] for row in preflight["studioSnapshot"]["files"]}
    if any(catalog_hashes.get(path, digest) != digest for path, digest in installed.sources.items()):
        raise ValueError("Catalog evidence changed while resolving the motion payloads")
    return {"target": TARGET.as_posix(), "pins": pins,
            "projectSnapshot": saved.snapshot(), "studioSnapshot": installed.snapshot()}


def verify_packaged_motion(bundle, motion):
    pins = motion.get("pins") if isinstance(motion, dict) else None
    if (not isinstance(pins, list) or motion.get("target") != TARGET.as_posix()
            or not all(isinstance(pin, dict) and pin.get("origin") in ("project", "catalog") for pin in pins)
            or [pin.get("name") for pin in pins] != list(MOTION_MODULES)):
        raise ValueError("The build plan has incomplete or unsupported motion payload pins")
    indexed = {}
    for member in bundle.infolist():
        indexed.setdefault(member.filename.casefold(), []).append(member)
    result = []
    for pin in pins:
        name = pin["name"]
        matches = indexed.get(f"FilesToTransfer/Data0/{name}.br".casefold(), [])
        if len(matches) != 1 or matches[0].file_size > XML_LIMIT:
            raise ValueError(f"Expected one readable {name} binary in the RUC")
        member = matches[0]
        digest = sha256(bundle.read(member)).hexdigest()
        if pin.get("sha256") != digest:
            raise ValueError(f"The RUC {name} bytes differ from the pinned {pin['origin']} payload")
        result.append({"name": name, "path": member.filename, "sha256": digest, "bytes": member.file_size})
    return result

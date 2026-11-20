"""Read the detection enum declared by the project library packaged in an AS4 RUC."""

from hashlib import file_digest, sha256
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from .global_variables import IGNORED
from .project import SavedFiles, XML_LIMIT, children, choices
from .ruc_review import Archive, DATA, bound_receipts
from .station_trace import ARRIVAL, TraceIssue


LIBRARY = "MpTraffic"
TYPE_NAME = "MpTrafficShDetectionEnum"
NONE = "mpTRAFFIC_SH_NONE"
ENUM = re.compile(rf"\b{TYPE_NAME}\s*:\s*\(([^()]*)\)\s*;", re.IGNORECASE)
VALUE = re.compile(r"mpTRAFFIC_SH_([A-Z0-9_]+)\s*:=\s*([+-]?[0-9]{1,10})", re.IGNORECASE | re.ASCII)


def detection_values(raw):
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = IGNORED.sub(" ", raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n"))
    declarations = ENUM.findall(text)
    if (len(declarations) != 1 or len(re.findall(rf"\b{TYPE_NAME}\s*:", text, re.IGNORECASE)) != 1
            or any(token in text for token in ("(*", "*)", "\0", "'", '"'))):
        raise ValueError(f"Expected one readable {TYPE_NAME} declaration")
    values, names = {}, set()
    for entry in declarations[0].strip().removesuffix(",").split(","):
        match = VALUE.fullmatch(entry.strip())
        if not match:
            raise ValueError(f"{TYPE_NAME} needs explicit decimal values for the detection members")
        name, code = f"mpTRAFFIC_SH_{match[1].upper()}", int(match[2])
        if code in values or name in names:
            raise ValueError(f"{TYPE_NAME} has duplicate names or values")
        values[code] = name
        names.add(name)
    if not {NONE, ARRIVAL} <= names:
        raise ValueError(f"{TYPE_NAME} needs NONE and ARRIVAL members")
    return values


def saved_xml(source, path, root):
    node, _ = source.xml(path, root)
    if node is None:
        raise ValueError(f"Cannot read {path.as_posix()} for the saved MpTraffic registration")
    return node


def registered_library(source, manifest):
    apj = saved_xml(source, Path(manifest["project"]), "Project")
    selections = [item for group in children(apj, "TechnologyPackages") for item in children(group, "mapp6D")]
    if len(selections) != 1 or not selections[0].get("Version") or not selections[0].get(LIBRARY):
        raise ValueError("The saved project needs one explicit mapp6D and MpTraffic selection")
    selection = selections[0]

    root_package = Path("Logical/Package.pkg")
    root = saved_xml(source, root_package, "Package")
    libraries = choices(source, root_package, root, "Package").get("libraries")
    if libraries is None:
        raise ValueError("The saved Logical package does not register Libraries")
    index = libraries / "Package.pkg"
    node = saved_xml(source, index, "Package")
    library = choices(source, index, node, "Library").get(LIBRARY.casefold())
    if library is None:
        raise ValueError("The saved Libraries package does not register MpTraffic")

    cpu_file = Path("Physical", manifest["configuration"], manifest["cpu"], "Cpu.sw")
    software = saved_xml(source, cpu_file, "SwConfiguration")
    cpu_refs = [(item.get("Source") or "") for group in children(software, "Libraries")
                for item in children(group, "LibraryObject") if (item.get("Name") or "").casefold() == LIBRARY.casefold()]
    expected = ".".join((*library.relative_to("Logical").parts, "lby")).casefold()
    if len(cpu_refs) > 1 or any(reference.casefold() != expected for reference in cpu_refs):
        raise ValueError("The selected CPU has an ambiguous or different MpTraffic library reference")

    descriptor_path = library / "binary.lby"
    descriptor = saved_xml(source, descriptor_path, "Library")
    version = descriptor.get("Version")
    if version != selection.get(LIBRARY):
        raise ValueError("The saved MpTraffic descriptor version differs from the project selection")
    declared_types = [item.text for group in children(descriptor, "Files") for item in children(group, "File")
                      if (item.text or "").strip().casefold() == "mptraffic.typ"]
    if len(declared_types) != 1:
        raise ValueError("The saved MpTraffic descriptor needs one MpTraffic.typ entry")
    type_path = source.member(descriptor_path, declared_types[0])
    raw = source.read_bytes(type_path, XML_LIMIT, label="MpTraffic type declaration")
    if raw is None:
        raise ValueError("Cannot read the registered MpTraffic.typ")
    payload = library / "SG4/IA32/MpTraffic.br"
    payload_sha = source.digest(payload)
    if payload_sha is None:
        raise ValueError("Cannot hash the saved IA32 MpTraffic payload")
    return detection_values(raw), {
        "mode": "project-mptraffic", "mapp6DVersion": selection.get("Version"), "libraryVersion": version,
        "typeDeclaration": type_path.as_posix(), "typeSha256": sha256(raw).hexdigest(),
        "payload": payload.as_posix(), "payloadSha256": payload_sha,
    }


def read_project_events(project, job_directory, reviewed):
    try:
        receipts = SavedFiles(job_directory)
        plan, job, manifest, archive_path = bound_receipts(receipts)
        if (reviewed.get("sourceMatched") is not True or plan["sha256"] != reviewed["planSha256"]
                or job["jobId"] != reviewed["jobId"]
                or job["package"]["sha256"] != reviewed["archive"]["sha256"]):
            raise ValueError("The build receipts changed after the route review")
        source = SavedFiles(project.parent)
        values, details = registered_library(source, manifest)
        expected = {row["path"]: row["sha256"] for row in manifest["inputs"]["files"]}
        if source.diagnostics or any(expected.get(path) != digest for path, digest in source.sources.items()):
            raise ValueError("The MpTraffic read set differs from the verified build manifest")

        with archive_path.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != reviewed["archive"]["sha256"]:
                raise ValueError("The packaged RUC changed during event encoding verification")
            stream.seek(0)
            with ZipFile(stream) as bundle:
                archive = Archive(bundle, receipts)
                name = f"{DATA}/MpTraffic.br"
                member = archive.member(name)
                if member is None or sha256(archive.read(member)).hexdigest() != details["payloadSha256"]:
                    raise ValueError("The RUC MpTraffic payload differs from the saved IA32 library payload")
    except (BadZipFile, KeyError, OSError, RuntimeError, ValueError) as error:
        raise TraceIssue("cycle_capture_event_source_unverified", str(error)) from error
    return values, {**details, "archiveMember": name, "codes": {str(code): label for code, label in sorted(values.items())}}

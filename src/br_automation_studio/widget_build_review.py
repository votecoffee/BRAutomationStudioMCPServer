"""Link a saved widget inventory to a verified AS4 log and packaged web assets."""

from collections import defaultdict
from hashlib import file_digest, sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from zipfile import BadZipFile, ZipFile

from .native_build import source_tree
from .project import SavedFiles
from .ruc_review import Archive, RECEIPT_LIMIT, VIEW, bound_receipts, expect_object


WARNING = re.compile(r'^(.+?\.pkg):\s*\(([^)]*)\) warning (923[23]):'
                     r'Additional (?:file|directory) "([^"]+)" found, which is not part of the project\.$')
COPY = re.compile(r"^Copy (file|directory) '([^']+)' to '([^']+)'\.$")
WEB_WIDGETS = PureWindowsPath("BRVisu/widgets")
WIDGETS_ARCHIVE = PurePosixPath(VIEW, "BRVisu", "widgets")


def verified_log(reader, job):
    receipt = expect_object(job.get("log"), "The build has no builder log receipt")
    name = Path("builder.log")
    if receipt.get("path") != name.as_posix():
        raise ValueError("The build receipt needs its retained builder.log")
    raw = reader.read_bytes(name, RECEIPT_LIMIT, label="Builder log")
    if raw is None or receipt.get("bytes") != len(raw) or receipt.get("sha256") != sha256(raw).hexdigest():
        raise ValueError("The retained builder log does not match the job receipt")
    return raw.decode("utf-8-sig"), receipt


def build_log_index(log, job, manifest):
    arguments = job.get("arguments")
    if (not isinstance(arguments, list) or len(arguments) < 3
            or not all(isinstance(value, str) for value in arguments)
            or arguments.count("-t") != 1 or arguments.index("-t") == len(arguments) - 1):
        raise ValueError("The job does not identify the staged project and mapp View build paths")
    staged_project = PureWindowsPath(arguments[1])
    temporary = PureWindowsPath(arguments[arguments.index("-t") + 1])
    if not staged_project.is_absolute() or staged_project.name != manifest["project"] or not temporary.is_absolute():
        raise ValueError("The job does not identify the staged project and mapp View build paths")
    staged = staged_project.parent
    web = temporary / "Objects" / manifest["configuration"] / manifest["cpu"] / ".mappView/data/wwwRoot"
    warnings, copies = defaultdict(list), defaultdict(list)
    for line_number, line in enumerate(log.splitlines(), 1):
        if warning := WARNING.fullmatch(line.strip()):
            path, parenthetical, code, member = warning.groups()
            if parenthetical.casefold() == member.casefold():
                warnings[PureWindowsPath(path), member.casefold()].append({"line": line_number, "code": int(code)})
        elif copy := COPY.fullmatch(line.strip()):
            kind, origin, destination = copy.groups()
            target = PureWindowsPath(destination)
            if target.is_relative_to(web):
                copies[PureWindowsPath(origin), target.relative_to(web), kind].append({"line": line_number})
    return staged, warnings, copies


def expected_inventory(library, manifest_files, manifest_directories):
    parent = PureWindowsPath(library["manifest"]).parent
    directories = {PureWindowsPath(path).name.casefold(): "directory" for path in manifest_directories
                   if PureWindowsPath(path).parent == parent}
    files = {PureWindowsPath(path).name.casefold(): "file" for path in manifest_files
             if PureWindowsPath(path).parent == parent and PureWindowsPath(path).name.casefold() != "package.pkg"}
    observed = {row["name"].casefold(): row["kind"] for row in library["extra"]}
    observed.update((row["name"].casefold(), row["observedKind"])
                    for row in library["declared"] if row["observedKind"] is not None)
    if observed != directories | files:
        raise ValueError(f"Widget library inventory differs from the build input manifest for {library['manifest']}")


def matching_assets(archive, row, package_path, sources):
    result = []
    prefix = package_path.as_posix().casefold()
    directory = row["kind"] == "directory"
    for key in sorted(archive.members):
        if not (key.startswith(prefix + "/") if directory else key == prefix):
            continue
        matches = [item for item in archive.members[key] if not item.is_dir()]
        if not matches:
            continue
        if len(matches) != 1 or len(archive.members[key]) != 1:
            raise ValueError(f"Ambiguous RUC widget asset {matches[0].filename}")
        member = matches[0]
        leaf = PurePosixPath(member.filename)
        if ".." in leaf.parts:
            raise ValueError(f"Invalid RUC widget asset path {member.filename}")
        subpath = leaf.parts[len(package_path.parts):]
        source_path = PurePosixPath(row["path"], *subpath).as_posix()
        saved = sources.get(source_path.casefold())
        digest = sha256(archive.read(member)).hexdigest()
        result.append({"path": member.filename, "sha256": digest, "bytes": member.file_size,
                       "savedPath": saved["path"] if saved else None,
                       "savedSha256": saved["sha256"] if saved else None,
                       "savedBytesMatch": digest == saved["sha256"] if saved else None})
    return result


def add_build_evidence(project, job_directory, report):
    review = report["widgetPackageReview"]
    evidence = review["nativeBuild"] = {"state": "unverified"}
    try:
        reader = SavedFiles(job_directory)
        plan, job, manifest, archive_path = bound_receipts(reader)
        if project.name != manifest["project"] or source_tree(project.parent.resolve()) != manifest["inputs"]:
            raise ValueError("The saved project does not match the staged build input manifest")
        sources = {row["path"].casefold(): row for row in manifest["inputs"]["files"]}
        if any(sources.get(row["path"].casefold(), {}).get("sha256") != row["sha256"]
               for row in report["snapshot"]["files"]):
            raise ValueError("The saved widget manifests changed during the review")
        log, log_receipt = verified_log(reader, job)
        staged, warnings, copies = build_log_index(log, job, manifest)
        package = job["package"]
        ready = []
        with archive_path.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != package["sha256"] or stream.seek(0, 2) != package["bytes"]:
                raise ValueError("The retained RUC does not match the job receipt")
            stream.seek(0)
            with ZipFile(stream) as bundle:
                if len(bundle.infolist()) != package["entries"]:
                    raise ValueError("The retained RUC member count differs from its job receipt")
                archive = Archive(bundle, reader)
                for library in review["libraries"]:
                    if library["state"] == "unverified":
                        continue
                    expected_inventory(library, (item["path"] for item in sources.values()),
                                       manifest["inputs"]["directories"])
                    manifest_path = PureWindowsPath(library["manifest"])
                    for row in library["extra"]:
                        widget_path = (manifest_path.parent.name, row["name"])
                        warning_lines = warnings[staged / manifest_path, row["name"].casefold()]
                        ready.append((row, {
                            "warnings": [line for line in warning_lines if line["code"] == row["builderWarning"]],
                            "copies": copies[staged / PureWindowsPath(row["path"]), WEB_WIDGETS.joinpath(*widget_path),
                                             row["kind"]],
                            "members": matching_assets(archive, row, WIDGETS_ARCHIVE.joinpath(*widget_path), sources),
                        }))
    except (BadZipFile, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        report["diagnostics"].append({"severity": "error", "scope": "widget_build",
                                      "code": "widget_build_evidence_unverified", "path": str(job_directory),
                                      "message": str(error)})
        return
    for row, result in ready:
        row["nativeEvidence"] = result
    evidence.update(state="verified", jobId=job["jobId"], planSha256=plan["sha256"], log=log_receipt,
                    ruc={key: package[key] for key in ("path", "sha256", "bytes", "entries")})

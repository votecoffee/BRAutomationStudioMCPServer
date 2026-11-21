
"""Plan and run a pinned native AS4 simulation build on a private project copy."""

from datetime import datetime, timezone
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
from shutil import copyfile
import stat
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from .launchpad_build import (
    launchpad_positions, plan_launchpad_layout, plan_launchpad_route, retain_launchpad_route, selected_manager,
)
from .motion_payloads import plan_motion_payloads, verify_packaged_motion
from .motion_symbols import plan_motion_symbols, read_motion_symbols
from .mptraffic_definition import check_installed_definition, verify_packaged_mptraffic
from .preflight import build_preflight
from .project import SavedFiles
from .windows_job import run_build

GENERATED_ROOTS = {".git", ".local", "binaries", "temp", "buildonly", "temponly"}
SUMMARY = re.compile(rb"(?im)^\s*Build:\s*(\d+)\s+error\(s\),\s*(\d+)\s+warning\(s\)[ \t]*\r?$")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def identity(data):
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("ascii")).hexdigest()


def issue(report, severity, code, message):
    report["diagnostics"].append({
        "severity": severity, "scope": "native_build", "code": code, "path": ".", "message": message,
    })


def blocked(report):
    return any(entry["severity"] == "error" for entry in report["diagnostics"])


def check_source(path, is_dir):
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"Project source contains a reparse point at {path}")
    expected = stat.S_ISDIR if is_dir else stat.S_ISREG
    if not expected(info.st_mode):
        raise ValueError(f"Unexpected project source type at {path}")
    return info


def source_tree(root):
    files = []
    directories = []

    def unreadable(error):
        raise error

    for directory, children, entries in os.walk(root, topdown=True, onerror=unreadable):
        folder = Path(directory)
        if folder == root:
            children[:] = [name for name in children if name.casefold() not in GENERATED_ROOTS]
        for name in sorted(children):
            path = folder / name
            check_source(path, True)
            directories.append(path.relative_to(root).as_posix())
        for name in sorted(entries):
            path = folder / name
            info = check_source(path, False)
            files.append({"path": path.relative_to(root).as_posix(), "bytes": info.st_size,
                          "sha256": fingerprint(path)})
    return {"directories": sorted(directories), "files": sorted(files, key=lambda row: row["path"])}


def plan_build(path, studio_root, runtime_root=None, configuration=None, cpu=None, *,
               verify_mptraffic_enum=False, verify_motion_payloads=False, verify_motion_declarations=False,
               verify_motion_symbols=False, launchpad_inputs=None):
    verify_layout = launchpad_inputs is not None and launchpad_inputs.verify_layout
    verify_positions = launchpad_inputs is not None and launchpad_inputs.verify_positions
    report = build_preflight(path, studio_root, runtime_root, configuration, cpu,
                             verify_motion_declarations=verify_motion_declarations, include_layout=verify_layout,
                             geometry_manager=launchpad_inputs.manager if verify_positions else None)
    report["buildPlan"] = None
    if blocked(report):
        return report
    preflight = report["preflight"]
    builder = next((item for item in preflight["buildTools"]
                    if item["path"].casefold() == "bin-en/br.as.build.exe"), None)
    if not builder or not builder["studioVersion"]:
        issue(report, "error", "unsupported_builder", "The native build requires a version-identified English AS4 builder")
    if (len(report["cpus"]) != 1 or report["cpu"].get("moduleId") != "PC_any"
            or not report["selectedConfiguration"] or not report["selectedCpu"]):
        issue(report, "error", "unsupported_build_target", "Select a configuration with exactly one PC_any CPU")
    if preflight["runtime"]["manifest"] is None:
        issue(report, "error", "runtime_unverified", "No installed Runtime catalog record was verified for the selected CPU")
    default_runtime = Path(preflight["studioRoot"]).parent / "AS/System"
    if Path(preflight["runtimeRoot"]) != default_runtime.resolve():
        issue(report, "error", "unsupported_runtime_override", "Native builds use the shared AS4 Runtime catalog")
    if blocked(report):
        return report

    definition = None
    if verify_mptraffic_enum:
        definition = report["mpTrafficDefinition"] = check_installed_definition(path, studio_root, report, "native_build")
        if definition["state"] != "matched":
            return report

    motion = None
    if verify_motion_payloads:
        try:
            motion = report["motionPayloads"] = plan_motion_payloads(
                path, preflight, report["selectedConfiguration"], report["selectedCpu"])
        except (OSError, RuntimeError, ValueError) as error:
            issue(report, "error", "motion_payloads_unverified", str(error))
            return report

    root = path.parent.resolve()
    try:
        inputs = source_tree(root)
    except (OSError, ValueError) as error:
        issue(report, "error", "project_snapshot_failed", str(error))
        return report
    files = {os.path.normcase(row["path"]): row["sha256"] for row in inputs["files"]}
    if len(files) != len(inputs["files"]):
        issue(report, "error", "ambiguous_source_paths", "Source files have conflicting names on this platform")
        return report
    reviewed_sources = list(report["snapshot"]["files"])
    if definition is not None:
        reviewed_sources.extend(definition["saved"]["snapshot"]["files"])
    if motion is not None:
        reviewed_sources.extend(motion["projectSnapshot"]["files"])
    if any(files.get(os.path.normcase(row["path"])) != row["sha256"] for row in reviewed_sources):
        issue(report, "error", "project_changed", "The source tree changed during build planning")
        return report
    if os.path.normcase(path.name) not in files:
        issue(report, "error", "project_snapshot_failed", "The selected project file is absent from the stage manifest")
        return report

    plan = {
        "version": 1, "mode": "as4_arsim_ruc", "sourceRoot": str(root), "project": path.name,
        "configuration": report["selectedConfiguration"], "cpu": report["selectedCpu"],
        "studioRoot": preflight["studioRoot"], "studioVersion": builder["studioVersion"],
        "builder": builder["path"], "studioSha256": preflight["studioSnapshot"]["sha256"],
        "runtimeRoot": preflight["runtimeRoot"], "runtimeSha256": preflight["runtimeSnapshot"]["sha256"],
        "inputs": inputs,
    }
    if definition is not None:
        plan["mpTrafficDefinition"] = definition
    if motion is not None:
        plan["motionPayloads"] = motion
    if verify_motion_declarations:
        plan["motionDeclarations"] = report["motionDeclarations"]
    if verify_motion_symbols:
        try:
            plan["motionSymbols"] = plan_motion_symbols(report)
        except ValueError as error:
            issue(report, "error", "motion_symbols_unverified", str(error))
            return report
    if launchpad_inputs is not None:
        try:
            plan["launchPadRoute"] = plan_launchpad_route(launchpad_inputs, report["trafficManagers"])
        except (OSError, RuntimeError, ValueError) as error:
            issue(report, "error", "launchpad_build_route_unverified", str(error))
            return report
        route = plan["launchPadRoute"]
        if verify_layout:
            try:
                route["layout"] = plan_launchpad_layout(launchpad_inputs, route, report["trafficManagers"],
                                                       report["assemblyLinks"])
            except (OSError, OverflowError, RuntimeError, ValueError) as error:
                issue(report, "error", "launchpad_build_layout_unverified", str(error))
                return report
        if verify_positions:
            try:
                route["positions"] = launchpad_positions(route, report["trafficManagers"])
            except ValueError as error:
                issue(report, "error", "launchpad_build_positions_unverified", str(error))
                return report
        if launchpad_inputs.widget_id is not None:
            from .start_page import StartPageWidget

            widget = StartPageWidget(launchpad_inputs.widget_id)
            try:
                manager = selected_manager(route["manager"], report["trafficManagers"])
                widget.check_saved(SavedFiles(root), plan, report["widgets"], widget.receipt(), manager)
            except (OSError, RuntimeError, ValueError) as error:
                issue(report, "error", "launchpad_build_widget_unverified", str(error))
                return report
            route["startPageWidgetId"] = widget.dom_id
    report["buildPlan"] = {"sha256": identity(plan), "manifest": plan}
    return report


def write_json(path, data):
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def stage_inputs(plan, destination):
    root = Path(plan["sourceRoot"])
    destination.mkdir()
    for directory in plan["inputs"]["directories"]:
        (destination / directory).mkdir()
    for record in plan["inputs"]["files"]:
        relative = record["path"]
        source = root / relative
        check_source(source, False)
        if not source.resolve().is_relative_to(root):
            raise ValueError(f"Source escaped the selected root: {relative}")
        target = destination / relative
        copyfile(source, target)
        if target.stat().st_size != record["bytes"] or fingerprint(target) != record["sha256"]:
            raise ValueError(f"Source changed before staging: {relative}")


def log_summary(path):
    summary = None
    with path.open("rb") as stream:
        for line in stream:
            match = SUMMARY.fullmatch(line.rstrip(b"\n"))
            if match:
                summary = {"errors": int(match[1]), "warnings": int(match[2])}
    return summary


def package_evidence(path, job_dir, *, mptraffic=None, motion=None):
    with ZipFile(path) as bundle:
        if not bundle.namelist() or bundle.testzip() is not None:
            raise ValueError("RUC archive failed ZIP integrity verification")
        evidence = {
            "path": path.relative_to(job_dir).as_posix(), "sha256": fingerprint(path),
            "bytes": path.stat().st_size, "entries": len(bundle.infolist()), "verifiedZip": True,
        }
        if mptraffic is not None:
            evidence["mpTrafficPayload"] = verify_packaged_mptraffic(bundle, mptraffic["saved"]["payloadSha256"])
        if motion is not None:
            evidence["motionPayloads"] = verify_packaged_motion(bundle, motion)
        return evidence


def execute_build(path, studio_root, runtime_root, configuration, cpu, plan_sha256, job_directory,
                  timeout_seconds=180, *, verify_mptraffic_enum=False, verify_motion_payloads=False,
                  verify_motion_declarations=False, verify_motion_symbols=False, launchpad_inputs=None):
    report = plan_build(path, studio_root, runtime_root, configuration, cpu,
                        verify_mptraffic_enum=verify_mptraffic_enum, verify_motion_payloads=verify_motion_payloads,
                        verify_motion_declarations=verify_motion_declarations,
                        verify_motion_symbols=verify_motion_symbols, launchpad_inputs=launchpad_inputs)
    plan = report["buildPlan"]
    if plan is None:
        return report
    if plan_sha256 != plan["sha256"]:
        issue(report, "error", "build_plan_changed", "The current build plan differs from the supplied SHA-256")
        return report

    manifest = plan["manifest"]
    requested = job_directory.absolute()
    job_dir = requested.resolve()
    launch_dir = requested if requested.drive.casefold() != job_dir.drive.casefold() else job_dir
    protected = [Path(manifest[name]) for name in ("sourceRoot", "studioRoot", "runtimeRoot")]
    if any(job_dir.is_relative_to(root) for root in protected):
        issue(report, "error", "invalid_job_directory", "Select an unused job directory outside all input roots")
        return report
    try:
        job_dir.mkdir()
    except OSError as error:
        issue(report, "error", "invalid_job_directory", f"Could not create a new job directory: {error}")
        return report

    staged = job_dir / "project"
    output = job_dir / "binary"
    temporary = job_dir / "temporary"
    log = job_dir / "builder.log"
    receipt = job_dir / "job.json"
    arguments = [str(Path(manifest["studioRoot"]) / manifest["builder"]),
                 str(launch_dir / "project" / manifest["project"]), "-c", manifest["configuration"],
                 "-simulation", "-all", "-o", str(launch_dir / "binary"), "-t", str(launch_dir / "temporary"), "-buildRUCPackage"]
    job = {
        "jobId": str(uuid4()), "directory": str(job_dir), "status": "staging",
        "planSha256": plan_sha256, "createdAt": utc_now(), "arguments": arguments,
        "log": None, "package": None, "workerPid": None,
    }
    report["nativeBuild"] = job
    write_json(receipt, job)
    write_json(job_dir / "plan.json", plan)

    def begin_dispatch(pid):
        job.update(status="dispatched", workerPid=pid, dispatchedAt=utc_now())
        write_json(receipt, job)

    try:
        stage_inputs(manifest, staged)
        if route := manifest.get("launchPadRoute"):
            files = retain_launchpad_route(job_dir, route)
            job["launchPadRoute"] = {"manager": route["manager"], "files": files}
        catalog = report["preflight"]
        checks = [(Path(catalog[f"{name}Root"]), catalog[f"{name}Snapshot"]) for name in ("studio", "runtime")]
        definition = manifest.get("mpTrafficDefinition")
        if definition is not None:
            checks.append((Path(catalog["studioRoot"]), definition["installed"]["snapshot"]))
        motion = manifest.get("motionPayloads")
        if motion is not None:
            checks.append((Path(catalog["studioRoot"]), motion["studioSnapshot"]))
        for root, snapshot in checks:
            for pinned in snapshot["files"]:
                if fingerprint(root / pinned["path"]) != pinned["sha256"]:
                    raise ValueError(f"Installation evidence changed before dispatch: {pinned['path']}")
        output.mkdir()
        temporary.mkdir()
        if launch_dir != job_dir and not launch_dir.samefile(job_dir):
            raise ValueError("The Windows drive alias no longer identifies the staged job directory")
        with log.open("xb") as stream:
            native_exit, timed_out, cleanup = run_build(arguments, launch_dir / "project", begin_dispatch, stream, timeout_seconds)
        job.update(nativeExitCode=native_exit, timedOut=timed_out, cleanup=cleanup)
        job["summary"] = summary = log_summary(log)
        if timed_out or cleanup != "empty":
            job["status"] = "unknown"
            issue(report, "error", "native_build_unknown", "The native build timed out or had remaining child processes")
        elif native_exit == 3 or (summary is not None and summary["errors"]):
            job["status"] = "build_failed"
            issue(report, "error", "native_build_failed", "Automation Studio reported a build error")
        elif summary is None or native_exit != (1 if summary["warnings"] else 0):
            job["status"] = "unknown"
            issue(report, "error", "native_build_unknown", "Builder exit code and build summary did not establish an outcome")
        else:
            ruc = output / manifest["configuration"] / manifest["cpu"] / "RUCPackage/RUCPackage.zip"
            artifact_error = "ruc_verification_failed"
            try:
                job["package"] = package_evidence(ruc, job_dir, mptraffic=definition, motion=motion)
                if "motionSymbols" in manifest:
                    artifact_error = "motion_symbols_unverified"
                    job["motionSymbols"] = read_motion_symbols(job_dir, manifest["configuration"],
                                                               manifest["motionSymbols"])
                job["status"] = "succeeded"
                if summary["warnings"]:
                    issue(report, "warning", "native_build_warnings", f"Automation Studio reported {summary['warnings']} warnings")
            except (BadZipFile, OSError, ValueError) as error:
                job["status"] = "artifact_failed"
                issue(report, "error", artifact_error, str(error))
    except (OSError, ValueError, RuntimeError) as error:
        job["status"] = "unknown" if job["status"] == "dispatched" else "not_dispatched"
        issue(report, "error", "native_build_error", str(error))
    except KeyboardInterrupt:
        job["status"] = "unknown" if job["status"] == "dispatched" else "not_dispatched"
        issue(report, "error", "native_build_interrupted", "Build request was interrupted")
    finally:
        job["finishedAt"] = utc_now()
        if log.is_file():
            job["log"] = {"path": log.name, "bytes": log.stat().st_size, "sha256": fingerprint(log)}
        job["diagnostics"] = [item for item in report["diagnostics"] if item.get("scope") == "native_build"]
        write_json(receipt, job)

    if job["workerPid"] is not None:
        report["evidence"] = "native_build_job"
    return report

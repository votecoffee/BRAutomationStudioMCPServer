
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

from .json_io import json_snapshot
from .launchpad_build import (
    launchpad_positions, launchpad_rules, plan_launchpad_assembly_io, plan_launchpad_layout, plan_launchpad_motion_limits,
    plan_launchpad_recovery, plan_launchpad_route, retain_launchpad_route, selected_manager,
)
from .motion_payloads import plan_motion_payloads, verify_packaged_motion
from .motion_symbols import plan_motion_symbols, read_motion_symbols
from .mptraffic_definition import check_installed_definition, verify_packaged_mptraffic
from .preflight import build_preflight
from .project import SavedFiles
from .simulation_preflight import ENVIRONMENT_REPORT_LIMIT, PAIRS, check_environment_report
from .windows_job import run_build

RETAINED_ENVIRONMENT_REPORT = Path("simulation-environment.json")
RETAINED_SIMULATION_PREFLIGHT_REPORT = Path("simulation-preflight.json")
SIMULATION_PREFLIGHT_REPORT_LIMIT = 16 * 1024 * 1024
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


def reviewed_hashes(snapshot):
    rows = snapshot["files"]
    if (snapshot["algorithm"] != "sha256" or not isinstance(rows, list) or not rows
            or identity(rows) != snapshot["sha256"]):
        raise ValueError("Invalid SHA-256 read set in the saved-project preflight")
    hashes = {os.path.normcase(row["path"]): row["sha256"] for row in rows}
    if len(hashes) != len(rows) or not all(isinstance(digest, str) for digest in hashes.values()):
        raise ValueError("Invalid file identities in the saved-project preflight")
    return hashes


def read_simulation_review(path, current):
    document, receipt = json_snapshot(path, SIMULATION_PREFLIGHT_REPORT_LIMIT)
    if (type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1
            or document["operation"] != "simulation_preflight"):
        raise ValueError("Expected a schema-version-1 simulation_preflight response")
    saved = document["result"]
    simulation = saved["simulationPreflight"]
    state = simulation["state"]
    if (state not in ("inputs_present", "review_required") or document["status"] not in ("complete", "partial")
            or not isinstance(saved["diagnostics"], list)
            or any(item["severity"] not in ("warning", "info") for item in saved["diagnostics"])):
        raise ValueError(f"The saved-project simulation preflight is {state!r} or reports unresolved issues")
    if any(saved[key] != current[key] for key in ("project", "selectedConfiguration", "selectedCpu")):
        raise ValueError("The saved-project preflight selected a different project, configuration or CPU")
    if simulation["targetMapp6D"] not in PAIRS:
        raise ValueError("The saved-project preflight has an unsupported mapp 6D family")
    for name in ("studio", "runtime"):
        old, observed = saved["preflight"], current["preflight"]
        path_key, snapshot_key = f"{name}Root", f"{name}Snapshot"
        old_hashes = reviewed_hashes(old[snapshot_key])
        if (os.path.normcase(old[path_key]) != os.path.normcase(observed[path_key])
                or not old_hashes.items() <= reviewed_hashes(observed[snapshot_key]).items()):
            raise ValueError(f"The selected {name} installation differs from the saved-project preflight")
    sources = reviewed_hashes(saved["snapshot"])
    if os.path.normcase(saved["project"]) not in sources:
        raise ValueError("The saved-project preflight has no hash for its selected project file")
    if simulation["environmentReview"]["state"] != "matched":
        raise ValueError("The saved-project preflight has no matched workstation package review")
    return {
        "state": "matched", "receipt": receipt, "preflightState": state, "targetMapp6D": simulation["targetMapp6D"],
        "exportSha256": simulation["launchPadExport"]["sha256"],
        "workstationSha256": simulation["environmentReview"]["receipt"]["sha256"],
    }, sources


def plan_build(path, studio_root, runtime_root=None, configuration=None, cpu=None, *,
               verify_mptraffic_enum=False, verify_motion_payloads=False, verify_motion_declarations=False,
               verify_motion_symbols=False, launchpad_inputs=None, environment_report=None,
               simulation_preflight_report=None):
    verify_layout = launchpad_inputs is not None and launchpad_inputs.verify_layout
    verify_positions = launchpad_inputs is not None and launchpad_inputs.verify_positions
    verify_recovery = launchpad_inputs is not None and launchpad_inputs.verify_recovery
    verify_motion_limits = launchpad_inputs is not None and launchpad_inputs.verify_motion_limits
    verify_assembly_io = launchpad_inputs is not None and launchpad_inputs.read_axes is not None
    report = build_preflight(path, studio_root, runtime_root, configuration, cpu,
                             verify_motion_declarations=verify_motion_declarations, include_layout=verify_layout,
                             geometry_manager=launchpad_inputs.manager if verify_positions else None,
                             recovery_managers=(launchpad_inputs.manager,) if verify_recovery else (),
                             include_motion=verify_motion_limits, include_io=verify_assembly_io)
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

    simulation_review = None
    if simulation_preflight_report is not None:
        try:
            simulation_review, reviewed_simulation_sources = read_simulation_review(simulation_preflight_report, report)
            if environment_report is None or launchpad_inputs is None:
                raise ValueError("Supply the reviewed workstation report and LaunchPad route with the simulation preflight")
        except (AttributeError, KeyError, OSError, RecursionError, RuntimeError, TypeError, ValueError) as error:
            issue(report, "error", "simulation_preflight_report_unverified", str(error))
            return report

    environment = None
    if environment_report is not None:
        reader = SavedFiles(studio_root)
        family = simulation_review["targetMapp6D"] if simulation_review is not None else None
        environment = report["environmentReview"] = check_environment_report(reader, environment_report, preflight,
                                                                              family=family)
        report["diagnostics"].extend(dict(item, scope="native_build") for item in reader.diagnostics)
        if environment["state"] != "matched":
            return report
        if simulation_review is not None and environment["receipt"]["sha256"] != simulation_review["workstationSha256"]:
            issue(report, "error", "simulation_preflight_report_mismatch",
                  "The workstation package report differs from the saved-project simulation preflight")
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
    if simulation_review is not None and not reviewed_simulation_sources.items() <= files.items():
        issue(report, "error", "simulation_preflight_report_mismatch",
              "A source file reviewed by the saved-project simulation preflight changed or is missing")
        return report

    plan = {
        "version": 1, "mode": "as4_arsim_ruc", "sourceRoot": str(root), "project": path.name,
        "configuration": report["selectedConfiguration"], "cpu": report["selectedCpu"],
        "studioRoot": preflight["studioRoot"], "studioVersion": builder["studioVersion"],
        "builder": builder["path"], "studioSha256": preflight["studioSnapshot"]["sha256"],
        "runtimeRoot": preflight["runtimeRoot"], "runtimeSha256": preflight["runtimeSnapshot"]["sha256"],
        "inputs": inputs,
    }
    if environment is not None:
        plan["environmentReview"] = environment
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
        if simulation_review is not None and route["files"]["export"]["sha256"] != simulation_review["exportSha256"]:
            issue(report, "error", "simulation_preflight_report_mismatch",
                  "The native LaunchPad export differs from the saved-project simulation preflight")
            return report
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
        if launchpad_inputs.verify_rules:
            try:
                route["rules"] = launchpad_rules(route, report["trafficManagers"])
            except ValueError as error:
                issue(report, "error", "launchpad_build_rules_unverified", str(error))
                return report
        if verify_recovery:
            try:
                route["recovery"] = plan_launchpad_recovery(launchpad_inputs, route, report["trafficManagers"], path)
            except (OSError, RuntimeError, ValueError) as error:
                issue(report, "error", "launchpad_build_recovery_unverified", str(error))
                return report
        if verify_motion_limits:
            try:
                route["motionLimits"] = plan_launchpad_motion_limits(launchpad_inputs, route, report["trafficManagers"],
                                                                     report["assemblyLinks"])
            except (OSError, RuntimeError, ValueError) as error:
                issue(report, "error", "launchpad_build_motion_limits_unverified", str(error))
                return report
        if verify_assembly_io:
            try:
                route["assemblyIo"] = plan_launchpad_assembly_io(launchpad_inputs, route, report)
            except ValueError as error:
                issue(report, "error", "launchpad_build_io_unverified", str(error))
                return report
    if simulation_review is not None:
        report["simulationPreflightReview"] = plan["simulationPreflightReview"] = simulation_review
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


def retain_report(job_dir, receipt, filename, limit, label):
    with Path(receipt["path"]).open("rb") as stream:
        data = stream.read(limit + 1)
    digest = sha256(data).hexdigest()
    if len(data) > limit or digest != receipt["sha256"]:
        raise ValueError(f"The {label} changed before dispatch")
    (job_dir / filename).write_bytes(data)
    return {"path": filename.as_posix(), "sha256": digest}


def retain_environment_report(job_dir, receipt):
    return retain_report(job_dir, receipt, RETAINED_ENVIRONMENT_REPORT, ENVIRONMENT_REPORT_LIMIT,
                         "workstation package report")


def execute_build(path, studio_root, runtime_root, configuration, cpu, plan_sha256, job_directory,
                  timeout_seconds=180, *, verify_mptraffic_enum=False, verify_motion_payloads=False,
                  verify_motion_declarations=False, verify_motion_symbols=False, launchpad_inputs=None,
                  environment_report=None, simulation_preflight_report=None):
    report = plan_build(path, studio_root, runtime_root, configuration, cpu,
                        verify_mptraffic_enum=verify_mptraffic_enum, verify_motion_payloads=verify_motion_payloads,
                        verify_motion_declarations=verify_motion_declarations,
                        verify_motion_symbols=verify_motion_symbols, launchpad_inputs=launchpad_inputs,
                        environment_report=environment_report, simulation_preflight_report=simulation_preflight_report)
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
        if environment := manifest.get("environmentReview"):
            job["environmentReport"] = retain_environment_report(job_dir, environment["receipt"])
        if simulation_review := manifest.get("simulationPreflightReview"):
            job["simulationPreflightReport"] = retain_report(
                job_dir, simulation_review["receipt"], RETAINED_SIMULATION_PREFLIGHT_REPORT,
                SIMULATION_PREFLIGHT_REPORT_LIMIT, "saved-project simulation preflight")
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

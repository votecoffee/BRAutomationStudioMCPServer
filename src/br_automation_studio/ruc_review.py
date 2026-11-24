
"""Review saved AS4 RUC contents against a linked native build plan."""

from collections import Counter, defaultdict
from dataclasses import replace
from hashlib import file_digest
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .file_device import SYSTEM_CONFIG, read_compiled_layout_device, verify_packaged_layout_device
from .inspection import inspect_project
from .json_io import invalid_constant, read_json, unique_object
from .launchpad_build import (
    checked_manager, compiled_launchpad_assembly_io, compiled_launchpad_layout, compiled_launchpad_motion_limits,
    compiled_launchpad_positions, compiled_launchpad_recovery, launchpad_rules, retained_launchpad_files, selected_manager,
)
from .links import MotionLinks, rule_key
from .motion_payloads import verify_packaged_motion
from .motion_symbols import plan_motion_symbols, read_motion_symbols
from .native_build import RETAINED_ENVIRONMENT_REPORT, RETAINED_SIMULATION_PREFLIGHT_REPORT, identity, source_tree
from .project import ProjectXml, SavedFiles, XML_LIMIT, children, tag

DATA = "FilesToTransfer/Data0"
VIEW = "FilesToTransfer/AddonsData/IAT_Data/wwwRoot"
CONTENT = f"{VIEW}/contents/mappView"
WIDGET_SCRIPT = f"{VIEW}/BRVisu/widgets/mapp6D/Assembly6D/Assembly6D.js"
TRAFFIC_DETAILS = "showAdditionalTrafficManagerInfo"
OPTIONS = re.compile(r'brease\.setOptions\(\s*("(?:[^"\\]|\\.)*")\s*,\s*')
DIGEST = re.compile(r"[0-9a-f]{64}")
RECEIPT_LIMIT = 16 * 1024 * 1024


def expect_object(value, message):
    if not isinstance(value, dict):
        raise ValueError(message)
    return value


def bound_receipts(files):
    paths = [files.resolve(Path(name)) for name in ("plan.json", "job.json")]
    if not all(paths):
        raise ValueError("Receipt files must resolve inside the selected job directory")
    plan, job = (expect_object(read_json(path, RECEIPT_LIMIT), "Expected an object receipt") for path in paths)
    manifest = expect_object(plan.get("manifest"), "Expected a build plan manifest")
    sha = plan.get("sha256")
    if not isinstance(sha, str) or sha != job.get("planSha256") or identity(manifest) != sha:
        raise ValueError("The job and the build plan do not have the same verified plan SHA-256")
    version = manifest.get("version")
    if type(version) is not int or version != 1 or manifest.get("mode") != "as4_arsim_ruc":
        raise ValueError("The plan does not identify a supported AS4 ARsim RUC build")

    for key, job_key, path, label in (
        ("environmentReview", "environmentReport", RETAINED_ENVIRONMENT_REPORT, "workstation package report"),
        ("simulationPreflightReview", "simulationPreflightReport", RETAINED_SIMULATION_PREFLIGHT_REPORT,
         "saved-project simulation preflight"),
    ):
        if key not in manifest:
            continue
        review = expect_object(manifest[key], f"Invalid {label} review in the build plan")
        receipt = expect_object(review.get("receipt"), f"The build plan lacks the reviewed {label} receipt")
        digest = receipt.get("sha256")
        retained = {"path": path.as_posix(), "sha256": digest}
        if (review.get("state") != "matched" or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                or job.get(job_key) != retained or files.digest(path) != digest):
            raise ValueError(f"The retained {label} does not match the build plan")

    summary = expect_object(job.get("summary"), "The job has no build summary")
    warnings = summary.get("warnings")
    errors = summary.get("errors")
    code = job.get("nativeExitCode")
    if (job.get("status") != "succeeded" or job.get("timedOut") is not False or job.get("cleanup") != "empty"
            or type(errors) is not int or errors != 0 or type(warnings) is not int or warnings < 0
            or type(code) is not int or code != (1 if warnings else 0)):
        raise ValueError("The job does not record a completed successful native build")
    if not isinstance(job.get("jobId"), str) or not job["jobId"]:
        raise ValueError("The successful job has no identity")

    package = expect_object(job.get("package"), "The job has no package evidence")
    if (package.get("verifiedZip") is not True or not isinstance(package.get("sha256"), str)
            or not DIGEST.fullmatch(package["sha256"])):
        raise ValueError("The job does not record a verified RUC package SHA-256")
    config, cpu = manifest.get("configuration"), manifest.get("cpu")
    if not all(isinstance(value, str) and files.member(Path("_"), value) == Path(value)
               for value in (config, cpu)):
        raise ValueError("Invalid configuration or CPU in the build plan")
    expected = Path("binary", config, cpu, "RUCPackage/RUCPackage.zip")
    if package.get("path") != expected.as_posix():
        raise ValueError("The package path does not match the selected configuration and CPU")
    archive_path = files.resolve(expected)
    if archive_path is None:
        raise ValueError("The RUC path must resolve inside the selected job directory")
    return plan, job, manifest, archive_path


def open_configuration(data):
    start = data.find(b"<?xml")
    end_marker = b"</Configuration>"
    end = data.find(end_marker, start) + len(end_marker)
    if start < 0 or end < len(end_marker):
        raise ValueError("No readable Configuration XML payload in the packaged BR module")
    root = ET.fromstring(data[start:end], parser=ET.XMLParser(target=ProjectXml()))
    if tag(root) != "Configuration":
        raise ValueError("Expected packaged Configuration XML")
    return root


class Archive:
    def __init__(self, file, diagnostics):
        self.file = file
        self.diagnostics = diagnostics
        self.members = defaultdict(list)
        for info in self.file.infolist():
            self.members[info.filename.casefold()].append(info)

    def member(self, name):
        matches = self.members[name.casefold()]
        if len(matches) != 1:
            kind = "duplicate" if matches else "missing"
            self.diagnostics.issue("error", f"{kind}_ruc_member", Path(name), f"Expected one archive member, found {len(matches)}")
            return None
        return matches[0]

    def read(self, member):
        if member.file_size > XML_LIMIT:
            raise ValueError(f"Archive member exceeds {XML_LIMIT} bytes")
        return self.file.read(member)


def compare_rows(reader, path, label, expected, actual):
    expected, actual = Counter(expected), Counter(actual)
    missing, extra = expected - actual, actual - expected
    if missing or extra:
        reader.issue("error", "ruc_content_mismatch", path,
                     f"{label}: missing {list(missing.items())[:5]!r} (count {sum(missing.values())}), "
                     f"unexpected {list(extra.items())[:5]!r} (count {sum(extra.values())})")


def read_motion(archive, saved, reader, geometry_managers=(), *, include_layout=False,
                recovery_managers=(), include_motion=False, include_io=False):
    planned = defaultdict(list)
    for row in saved["motionModules"]:
        planned[Path(row["path"]).with_suffix(".br").name.casefold()].append(row)
    motion = MotionLinks(reader)
    ready = True
    for rows in planned.values():
        name = Path(rows[0]["path"]).with_suffix(".br").name
        member_name = f"{DATA}/{name}"
        if len({row["path"] for row in rows}) != 1:
            reader.issue("error", "ambiguous_module_name", Path(member_name),
                         "More than one saved source uses this packaged module name")
            ready = False
            continue
        member = archive.member(member_name)
        if member is None:
            ready = False
            continue
        path = Path(member.filename)
        try:
            root = open_configuration(archive.read(member))
        except (ET.ParseError, LookupError, ValueError) as error:
            reader.issue("warning", "ruc_configuration_unreadable", path, str(error))
            ready = False
            continue
        compare_rows(reader, path, "Module identities",
                     [(row["id"], row["type"]) for row in rows],
                     [(item.get("ID"), item.get("Type")) for item in children(root, "Element")])
        motion.add(path, rows[0]["type"], root)
    links, managers = (motion.check(geometry_managers, include_layout=include_layout,
                                   recovery_managers=recovery_managers, include_motion=include_motion, include_io=include_io)
                       if ready else ([], []))
    return motion.module_inventory, links, managers, ready


def compare_rules(reader, path, name, expected, actual):
    before = {row["id"]: row for row in expected}
    after = {row["id"]: row for row in actual}
    for identity in sorted(before.keys() & after.keys()):
        saved, compiled = before[identity], after[identity]
        if saved["state"] == compiled["state"] == "read":
            compare_rows(reader, path, f"{name} rule list {identity} contents",
                         map(rule_key, saved["rules"]), map(rule_key, compiled["rules"]))


def compare_launchpad_rules(reader, launchpad, saved, managers):
    planned = launchpad["rules"]
    try:
        if planned != launchpad_rules(launchpad, saved["trafficManagers"]):
            raise ValueError("The pinned rule lists differ from the saved manager")
        compiled = launchpad_rules(launchpad, managers)
    except ValueError as error:
        reader.issue("error", "ruc_launchpad_rules_unverified", Path(DATA), str(error))
        return {"state": "unverified", "planned": planned}
    changed = sorted(name for name in planned.keys() | compiled.keys() if planned.get(name) != compiled.get(name))
    if changed:
        reader.issue("error", "ruc_launchpad_rules_mismatch", Path(DATA),
                     f"Compiled rule lists differ from the build plan: {', '.join(changed)}")
    return {"state": "issues" if changed else "matched", "planned": planned,
            "compiled": compiled, "changedLists": changed}


def compare_launchpad_motion_limits(reader, launchpad, assembly, saved_links, compiled_links):
    try:
        report = compiled_launchpad_motion_limits(launchpad, assembly, saved_links, compiled_links)
    except ValueError as error:
        reader.issue("error", "ruc_launchpad_motion_limits_unverified", Path(DATA), str(error))
        return {"state": "unverified", "planned": launchpad["motionLimits"]}
    path = Path(report["compiled"]["path"])
    for key, outcome in report["comparisons"].items():
        if outcome == "mismatch":
            reader.issue("error", "ruc_launchpad_motion_limit_mismatch", path,
                         f"Compiled {key} differs from the explicit limit in the build plan")
        elif outcome == "unverified":
            reader.issue("error", "ruc_launchpad_motion_limits_unverified", path, f"Cannot verify compiled {key}")
    return report


def compare_launchpad_assembly_io(reader, launchpad, assembly, saved, compiled_links):
    try:
        report = compiled_launchpad_assembly_io(launchpad, assembly, saved, compiled_links)
    except ValueError as error:
        reader.issue("error", "ruc_launchpad_io_unverified", Path(DATA), str(error))
        return {"state": "unverified", "planned": launchpad["assemblyIo"]}
    path = Path(report["compiled"]["path"])
    for comparisons in report["comparisons"].values():
        for name, outcome in comparisons.items():
            if outcome == "mismatch":
                reader.issue("error", "ruc_launchpad_io_mismatch", path, f"Compiled {name} differs from the explicit I/O plan")
            elif outcome == "unverified":
                reader.issue("error", "ruc_launchpad_io_unverified", path, f"Cannot verify compiled {name}")
    return report


def compare_motion(reader, saved, links, managers, pinned_rules_manager=None):
    path = Path(DATA)
    compare_rows(reader, path, "Assembly layouts",
                 [(row["assembly"], tuple(row["layouts"])) for row in saved["assemblyLinks"]],
                 [(row["assembly"], tuple(row["layouts"])) for row in links])
    expected = {row["manager"]: row for row in saved["trafficManagers"]}
    actual = {row["manager"]: row for row in managers}
    compare_rows(reader, path, "Traffic Manager identities", expected.keys(), actual.keys())
    for name in sorted(expected.keys() & actual.keys()):
        before, after = expected[name], actual[name]
        compare_rows(reader, path, f"{name} assembly", [before["assembly"]], [after["assembly"]])
        for field in ("objectLists", "mappingLists"):
            compare_rows(reader, path, f"{name} {field}", before[field], after[field])
        compare_rows(reader, path, f"{name} typed objects",
                     [(item["name"], item["type"]) for item in before["objects"]],
                     [(item["name"], item["type"]) for item in after["objects"]])
        if name != pinned_rules_manager:
            compare_rows(reader, path, f"{name} ruleLists", before["ruleLists"], after["ruleLists"])
            compare_rules(reader, path, name, before["ruleEvidence"], after["ruleEvidence"])
        edge_fields = ("source", "sourceType", "destination", "destinationType")
        compare_rows(reader, path, f"{name} directed edges",
                     [tuple(item[field] for field in edge_fields) for item in before["edges"]],
                     [tuple(item[field] for field in edge_fields) for item in after["edges"]])


def blank_equivalent(value):
    return (None, "") if value in (None, "") else (value,)


def read_widgets(archive, saved, reader, *, include_traffic_details=False):
    expected = defaultdict(list)
    for item in saved["widgets"]:
        content, widget = item["contentId"], item["id"]
        if not (content and widget and reader.member(Path("_"), content) == Path(content)):
            reader.issue("error", "invalid_saved_widget", Path(item["path"]), "Widget needs a supported content ID and widget ID")
            continue
        expected[content].append(item)

    output = []
    for content, items in expected.items():
        name = f"{CONTENT}/{content}.html"
        if len({item["path"] for item in items}) != 1 or len({item["id"] for item in items}) != len(items):
            reader.issue("error", "ambiguous_saved_widget", Path(name), "Duplicate saved widget or content ID")
            continue
        member = archive.member(name)
        if member is None:
            continue
        path = Path(member.filename)
        try:
            html = archive.read(member).decode("utf-8-sig")
        except (UnicodeError, ValueError) as error:
            reader.issue("warning", "ruc_content_unreadable", path, str(error))
            continue

        decoder = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=invalid_constant)
        keys = {f"{content}_{item['id']}" for item in items}
        results = defaultdict(list)
        unreadable = set()
        for match in OPTIONS.finditer(html):
            try:
                key = json.loads(match[1])
            except ValueError:
                continue
            if key not in keys:
                continue
            try:
                options, end = decoder.raw_decode(html, match.end())
                if not html[end:].lstrip().startswith(")"):
                    raise ValueError("Expected literal setOptions JSON")
                results[key].append(expect_object(options, "Expected widget options object"))
            except ValueError as error:
                reader.issue("warning", "ruc_widget_options_unreadable", path, f"{key}: {error}")
                unreadable.add(key)
        for item in items:
            key = f"{content}_{item['id']}"
            if key in unreadable:
                continue
            matches = results.get(key, [])
            if len(matches) != 1:
                reader.issue("error", "ruc_widget_count", path, f"Expected one rendered widget {key!r}, found {len(matches)}")
                continue
            options = matches[0]
            comparison = {"id": item["id"], "contentId": content, "path": member.filename,
                          "className": options.get("className"), "assembly": options.get("mpLink"),
                          "manager": options.get("mpLinkTrafficManager")}
            if include_traffic_details:
                value = options.get(TRAFFIC_DETAILS)
                comparison["trafficDetails"] = {"present": TRAFFIC_DETAILS in options,
                                                "enabled": value if type(value) is bool else None}
            output.append(comparison)
            if (comparison["className"] != "widgets/mapp6D/Assembly6D"
                    or comparison["assembly"] not in blank_equivalent(item["assembly"])
                    or comparison["manager"] not in blank_equivalent(item["manager"])):
                reader.issue("error", "ruc_widget_mismatch", path, f"Rendered class or links differ for {key!r}")

    if expected:
        archive.member(WIDGET_SCRIPT)
    return output


def match_read_set(reader, path, manifest, result, snapshot, issues):
    result["snapshot"] = snapshot
    files = {Path(row["path"]): row["sha256"] for row in manifest["inputs"]["files"]}
    if any(files.get(Path(row["path"])) != row["sha256"] for row in snapshot["files"]):
        reader.issue("error", "ruc_source_changed", Path(path.name), "Saved project changed during inspection")
        return False
    result["rucReview"]["sourceMatched"] = True
    reader.diagnostics.extend(dict(issue, scope="source") for issue in issues)
    return True


def review_ruc(path, job_directory, binding=None, *, geometry_manager=None, include_layout=False,
               recovery_manager=None, include_motion=False, include_io=False, include_layout_device=False,
               include_saved_geometry=False, require_6d=True, start_widget=None):
    reader = SavedFiles(job_directory)
    result = {"evidence": "ruc_readback", "project": path.name, "selectedConfiguration": None,
              "selectedCpu": None, "snapshot": None, "diagnostics": reader.diagnostics}
    review = {"jobId": None, "planSha256": None, "sourceMatched": False, "buildSummary": None,
              "archive": None, "modules": [], "assemblyLinks": [], "trafficManagers": [], "widgets": []}
    result["rucReview"] = review
    if start_widget is not None:
        review["startPageWidget"] = start_widget.receipt()
    if binding is not None:
        result["bindingReview"] = binding.receipt()
    try:
        plan, job, manifest, archive_path = bound_receipts(reader)
        pinned_layout_device = "layoutDevice" in manifest
        include_layout_device = include_layout_device or pinned_layout_device
        review.update(jobId=job["jobId"], planSha256=plan["sha256"], buildSummary=job["summary"])
        result.update(selectedConfiguration=manifest["configuration"], selectedCpu=manifest["cpu"])
        if path.name != manifest.get("project") or source_tree(path.parent.resolve()) != manifest.get("inputs"):
            reader.issue("error", "ruc_source_changed", Path(path.name), "Saved project does not match the staged input manifest")
            return result
        launchpad = manifest.get("launchPadRoute")
        if launchpad is not None and not isinstance(launchpad, dict):
            raise ValueError("The build plan has no usable LaunchPad evidence")
        pinned_recovery = launchpad is not None and "recovery" in launchpad
        pinned_motion_limits = launchpad is not None and "motionLimits" in launchpad
        pinned_assembly_io = launchpad is not None and "assemblyIo" in launchpad
        include_motion = include_motion or pinned_motion_limits
        include_io = include_io or pinned_assembly_io
        recovery_managers = (recovery_manager, launchpad["manager"] if pinned_recovery else None)

        if binding is None:
            saved = inspect_project(path, manifest["configuration"], manifest["cpu"],
                                    geometry_manager=geometry_manager if include_saved_geometry else None,
                                    include_layout=include_saved_geometry,
                                    recovery_managers=recovery_managers, include_motion=include_motion, include_io=include_io,
                                    include_layout_device=include_layout_device)
            if not match_read_set(reader, path, manifest, result, saved["snapshot"], saved["diagnostics"]):
                return result
            if include_layout_device:
                review["savedLayoutDevice"] = saved.get("savedLayoutDevice")
            if pinned_layout_device and review["savedLayoutDevice"] != manifest["layoutDevice"]:
                raise ValueError("The saved 6DData declaration differs from the build plan")
            if include_motion or include_io or include_saved_geometry:
                review["savedAssemblyLinks"] = saved["assemblyLinks"]
            if include_saved_geometry:
                review["savedPositionEvidence"] = next(
                    (row["positionEvidence"] for row in saved["trafficManagers"] if row["manager"] == geometry_manager),
                    [],
                )
            if include_io:
                review["globalDeclarations"] = saved.get("globalDeclarations")
            if recovery_manager is not None:
                review["savedRecoveryEvidence"] = next((row["recoveryEvidence"] for row in saved["trafficManagers"]
                                                       if row["manager"] == recovery_manager), [])
        with archive_path.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
            size = stream.seek(0, 2)
            if digest != job["package"]["sha256"] or size != job["package"].get("bytes"):
                reader.issue("error", "ruc_hash_mismatch", Path(job["package"]["path"]),
                             "RUC size or SHA-256 differs from the native build receipt")
                return result
            stream.seek(0)
            with ZipFile(stream) as package:
                entries = len(package.infolist())
                if not entries or entries != job["package"].get("entries"):
                    raise ValueError("RUC entry count does not match the native build receipt")
                archive = Archive(package, reader)
                review["archive"] = {"path": job["package"]["path"], "sha256": digest,
                                     "bytes": size, "entries": entries}
                if "environmentReview" in manifest:
                    review["environmentReport"] = job["environmentReport"]
                if "simulationPreflightReview" in manifest:
                    review["simulationPreflightReport"] = job["simulationPreflightReport"]
                if pinned_layout_device:
                    layout_device = verify_packaged_layout_device(package)
                    if layout_device != job["package"].get("layoutDevice"):
                        raise ValueError("The compiled 6DData evidence differs from the job receipt")
                    review["layoutDevice"] = layout_device
                    review["compiledLayoutDevice"] = layout_device["compiled"]
                if "motionPayloads" in manifest:
                    payloads = verify_packaged_motion(package, manifest["motionPayloads"])
                    if payloads != job["package"].get("motionPayloads"):
                        raise ValueError("The packaged motion payloads differ from the job receipt")
                    review["motionPayloads"] = payloads
                if "motionSymbols" in manifest:
                    symbols = manifest["motionSymbols"]
                    if binding is None and symbols != plan_motion_symbols(saved):
                        raise ValueError("The motion symbol plan differs from the saved configuration")
                    generated = read_motion_symbols(reader.root, manifest["configuration"], symbols)
                    if generated != job.get("motionSymbols"):
                        raise ValueError("The generated motion declarations differ from the job receipt")
                    review["motionSymbols"] = generated
                if launchpad is not None:
                    files = retained_launchpad_files(reader, launchpad)
                    retained = {"manager": launchpad["manager"], "files": files}
                    if retained != job.get("launchPadRoute"):
                        raise ValueError("The LaunchPad inputs differ from the job receipt")
                    review["launchPadRoute"] = {**retained, "state": "inputs_verified"}
                if binding is not None:
                    source = SavedFiles(path.parent)
                    binding_report = result["bindingReview"]
                    binding.check(archive, source, manifest, binding_report)
                    matched = match_read_set(reader, path, manifest, result, source.snapshot(), source.diagnostics)
                    if matched and binding_report["state"] == "matched" and not reader.diagnostics:
                        result["evidence"] = "ruc_binding_readback"
                    elif binding_report["state"] == "matched":
                        binding_report["state"] = "unverified"
                    return result
                pinned_widget = launchpad is not None and "startPageWidgetId" in launchpad
                if pinned_widget:
                    from .start_page import StartPageWidget

                    dom_id = launchpad["startPageWidgetId"]
                    if not isinstance(dom_id, str) or not dom_id:
                        raise ValueError("The LaunchPad plan has no usable start page widget ID")
                    if start_widget is not None and start_widget.dom_id not in (None, dom_id):
                        raise ValueError(f"The LaunchPad build plan requires start page widget {dom_id!r}")
                    start_widget = (replace(start_widget, dom_id=dom_id) if start_widget is not None
                                    else StartPageWidget(dom_id))
                    review["startPageWidget"] = start_widget.receipt()
                    review["startPageWidget"]["launchPadManager"] = launchpad["manager"]
                if include_layout_device and not pinned_layout_device:
                    member = archive.member(SYSTEM_CONFIG)
                    if member is not None:
                        review["compiledLayoutDevice"] = read_compiled_layout_device(archive.read(member))
                pinned_layout = launchpad is not None and "layout" in launchpad
                pinned_positions = launchpad is not None and "positions" in launchpad
                pinned_rules = launchpad is not None and "rules" in launchpad
                pinned_manager = launchpad["manager"] if pinned_positions else None
                modules, links, managers, ready = read_motion(archive, saved, reader, (geometry_manager, pinned_manager),
                                                              include_layout=include_layout or pinned_layout,
                                                              recovery_managers=recovery_managers, include_motion=include_motion,
                                                              include_io=include_io)
                review.update(modules=modules, assemblyLinks=links, trafficManagers=managers)
                if pinned_positions:
                    review["launchPadRoute"]["positions"] = {"state": "unverified"}
                    if not ready:
                        reader.issue("error", "ruc_launchpad_positions_unverified", Path("."),
                                     "The compiled motion modules could not be read for the planned position comparison")
                if pinned_rules:
                    review["launchPadRoute"]["rules"] = {"state": "unverified", "planned": launchpad["rules"]}
                    if not ready:
                        reader.issue("error", "ruc_launchpad_rules_unverified", Path(DATA),
                                     "The compiled motion modules could not be read for the planned rule-list comparison")
                if pinned_recovery:
                    review["launchPadRoute"]["recovery"] = {"state": "unverified", "planned": launchpad["recovery"]}
                    if not ready:
                        reader.issue("error", "ruc_launchpad_recovery_unverified", Path(DATA),
                                     "The compiled motion modules could not be read for the planned station recovery comparison")
                if pinned_motion_limits:
                    review["launchPadRoute"]["motionLimits"] = {"state": "unverified", "planned": launchpad["motionLimits"]}
                    if not ready:
                        reader.issue("error", "ruc_launchpad_motion_limits_unverified", Path(DATA),
                                     "The compiled motion modules could not be read for the in-plane limit comparison")
                if pinned_assembly_io:
                    review["launchPadRoute"]["assemblyIo"] = {"state": "unverified", "planned": launchpad["assemblyIo"]}
                    if not ready:
                        reader.issue("error", "ruc_launchpad_io_unverified", Path(DATA),
                                     "The compiled motion modules could not be read for the assembly I/O comparison")
                if ready:
                    compare_motion(reader, saved, links, managers, launchpad["manager"] if pinned_rules else None)
                    if launchpad is not None:
                        route = checked_manager(launchpad, managers)
                        review["launchPadRoute"].update(state="matched", edges=route["expectedEdges"])
                        if pinned_rules:
                            review["launchPadRoute"]["rules"] = compare_launchpad_rules(reader, launchpad, saved, managers)
                        if pinned_motion_limits:
                            review["launchPadRoute"]["motionLimits"] = compare_launchpad_motion_limits(
                                reader, launchpad, route["assembly"], saved["assemblyLinks"], links,
                            )
                        if pinned_assembly_io:
                            review["launchPadRoute"]["assemblyIo"] = compare_launchpad_assembly_io(
                                reader, launchpad, route["assembly"], saved, links,
                            )
                        if pinned_recovery:
                            try:
                                recovery = compiled_launchpad_recovery(launchpad, saved["trafficManagers"], managers)
                            except ValueError as error:
                                reader.issue("error", "ruc_launchpad_recovery_unverified", Path(DATA), str(error))
                            else:
                                review["launchPadRoute"]["recovery"] = recovery
                                for name in recovery["changedStations"]:
                                    reader.issue("error", "ruc_launchpad_recovery_mismatch",
                                                 Path(recovery["compiled"][name]["path"]),
                                                 f"The recovery mode, eligibility, or indexed points changed at station {name!r}")
                        if pinned_layout:
                            try:
                                layout = compiled_launchpad_layout(launchpad["layout"], route["assembly"], links)
                            except (LookupError, OverflowError, ValueError) as error:
                                review["launchPadRoute"]["layout"] = {"state": "unverified"}
                                reader.issue("error", "ruc_launchpad_layout_unverified", Path("."), str(error))
                            else:
                                review["launchPadRoute"]["layout"] = layout
                                if layout["state"] != "matched":
                                    reader.issue("error", "ruc_launchpad_layout_mismatch", Path(layout["path"]),
                                                 "The compiled layout identity or segment grid differs from the build plan")
                        if pinned_positions:
                            try:
                                positions = compiled_launchpad_positions(launchpad, managers)
                            except ValueError as error:
                                reader.issue("error", "ruc_launchpad_positions_unverified", Path("."), str(error))
                            else:
                                review["launchPadRoute"]["positions"] = positions
                                for name in positions["movedObjects"]:
                                    before = positions["planned"][name]["xyMetres"]
                                    after = positions["compiled"][name]["xyMetres"]
                                    reader.issue("error", "ruc_launchpad_positions_mismatch",
                                                 Path(positions["compiled"][name]["path"]),
                                                 f"{name!r} compiled XY {after} m differs from the planned {before} m")
                review["widgets"] = read_widgets(archive, saved, reader,
                                                 include_traffic_details=(start_widget is not None
                                                                          and start_widget.require_traffic_details))
                if start_widget is not None:
                    manager = selected_manager(launchpad["manager"], managers) if pinned_widget else None
                    start_widget.check(archive, path.parent, manifest, saved["widgets"], review["widgets"],
                                       review["startPageWidget"], manager)
                if require_6d and not saved["motionModules"] and not saved["widgets"]:
                    reader.issue("warning", "ruc_no_expected_6d_content", Path("."),
                                 "No supported saved 6D modules or Assembly6D widgets to compare")
    except (BadZipFile, LookupError, OSError, RuntimeError, ValueError) as error:
        reader.issue("error", "ruc_review_input", Path("."), str(error))
    return result

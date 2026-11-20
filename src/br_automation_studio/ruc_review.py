
"""Review saved AS4 RUC contents against a linked native build plan."""

from collections import Counter, defaultdict
from hashlib import file_digest
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .file_device import SYSTEM_CONFIG, read_compiled_layout_device
from .inspection import inspect_project
from .json_io import invalid_constant, read_json, unique_object
from .links import MotionLinks, RULE_ROLES
from .native_build import identity, source_tree
from .project import ProjectXml, SavedFiles, XML_LIMIT, children, tag

DATA = "FilesToTransfer/Data0"
VIEW = "FilesToTransfer/AddonsData/IAT_Data/wwwRoot"
CONTENT = f"{VIEW}/contents/mappView"
WIDGET_SCRIPT = f"{VIEW}/BRVisu/widgets/mapp6D/Assembly6D/Assembly6D.js"
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


def read_motion(archive, saved, reader, geometry_manager=None, *, include_layout=False,
                recovery_manager=None, include_motion=False, include_io=False):
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
    links, managers = (motion.check(geometry_manager, include_layout=include_layout,
                                   recovery_manager=recovery_manager, include_motion=include_motion, include_io=include_io)
                       if ready else ([], []))
    return motion.module_inventory, links, managers, ready


def rule_key(rule):
    roles = [tuple(sorted((item["name"], item["type"]) for item in rule[role])) for _, role in RULE_ROLES]
    return rule["name"], rule["type"], *roles


def compare_rules(reader, path, name, expected, actual):
    before = {row["id"]: row for row in expected}
    after = {row["id"]: row for row in actual}
    for identity in sorted(before.keys() & after.keys()):
        saved, compiled = before[identity], after[identity]
        if saved["state"] == compiled["state"] == "read":
            compare_rows(reader, path, f"{name} rule list {identity} contents",
                         map(rule_key, saved["rules"]), map(rule_key, compiled["rules"]))


def compare_motion(reader, saved, links, managers):
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
        for field in ("objectLists", "mappingLists", "ruleLists"):
            compare_rows(reader, path, f"{name} {field}", before[field], after[field])
        compare_rows(reader, path, f"{name} typed objects",
                     [(item["name"], item["type"]) for item in before["objects"]],
                     [(item["name"], item["type"]) for item in after["objects"]])
        compare_rules(reader, path, name, before["ruleEvidence"], after["ruleEvidence"])
        edge_fields = ("source", "sourceType", "destination", "destinationType")
        compare_rows(reader, path, f"{name} directed edges",
                     [tuple(item[field] for field in edge_fields) for item in before["edges"]],
                     [tuple(item[field] for field in edge_fields) for item in after["edges"]])


def blank_equivalent(value):
    return (None, "") if value in (None, "") else (value,)


def read_widgets(archive, saved, reader):
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
        review.update(jobId=job["jobId"], planSha256=plan["sha256"], buildSummary=job["summary"])
        result.update(selectedConfiguration=manifest["configuration"], selectedCpu=manifest["cpu"])
        if path.name != manifest.get("project") or source_tree(path.parent.resolve()) != manifest.get("inputs"):
            reader.issue("error", "ruc_source_changed", Path(path.name), "Saved project does not match the staged input manifest")
            return result

        if binding is None:
            saved = inspect_project(path, manifest["configuration"], manifest["cpu"],
                                    geometry_manager=geometry_manager if include_saved_geometry else None,
                                    include_layout=include_saved_geometry,
                                    recovery_manager=recovery_manager, include_motion=include_motion, include_io=include_io,
                                    include_layout_device=include_layout_device)
            if not match_read_set(reader, path, manifest, result, saved["snapshot"], saved["diagnostics"]):
                return result
            if include_layout_device:
                review["savedLayoutDevice"] = saved.get("savedLayoutDevice")
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
                if include_layout_device:
                    member = archive.member(SYSTEM_CONFIG)
                    if member is not None:
                        review["compiledLayoutDevice"] = read_compiled_layout_device(archive.read(member))
                modules, links, managers, ready = read_motion(archive, saved, reader, geometry_manager,
                                                              include_layout=include_layout,
                                                              recovery_manager=recovery_manager, include_motion=include_motion,
                                                              include_io=include_io)
                review.update(modules=modules, assemblyLinks=links, trafficManagers=managers)
                if ready:
                    compare_motion(reader, saved, links, managers)
                review["widgets"] = read_widgets(archive, saved, reader)
                if start_widget is not None:
                    start_widget.check(archive, path.parent, manifest, saved["widgets"], review["widgets"],
                                       review["startPageWidget"])
                if require_6d and not saved["motionModules"] and not saved["widgets"]:
                    reader.issue("warning", "ruc_no_expected_6d_content", Path("."),
                                 "No supported saved 6D modules or Assembly6D widgets to compare")
    except (BadZipFile, LookupError, OSError, RuntimeError, ValueError) as error:
        reader.issue("error", "ruc_review_input", Path("."), str(error))
    return result

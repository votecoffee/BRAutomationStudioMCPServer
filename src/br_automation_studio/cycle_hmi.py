"""Correlate a selected cycle's next hold with a completed loopback status report."""

from pathlib import Path
import re

from .arsim_smoke import HOST
from .browser_smoke import ASSEMBLY_WIDGET, http_status, layout_asset_paths, numeric_text
from .hmi_smoke import HELD_STATUS_ROLES, check_samples
from .json_io import json_snapshot
from .native_build import RETAINED_ENVIRONMENT_REPORT, check_source
from .opcua_smoke import CURRENT_READ, INTEGER_NODE_TYPES
from .runtime_capture import CAPTURE_COPY
from .simulation_preflight import ENVIRONMENT_REPORT_LIMIT
from .station_trace import TRACE_LIMIT, TraceIssue, integer


SHA256 = re.compile(r"[a-f0-9]{64}")


def good_status(code):
    return integer(code) and code < 0x40000000


def read_only(node):
    return all(type(node.get(key)) is int and node[key] == CURRENT_READ
               for key in ("accessLevel", "userAccessLevel"))


def status_run(document, ruc):
    if (not isinstance(document, dict) or type(document.get("schemaVersion")) is not int or document["schemaVersion"] != 1
            or document.get("operation") != "arsim_status_smoke" or document.get("status") != "complete"):
        raise ValueError("Supply a complete schemaVersion 1 arsim_status_smoke JSON response")
    receipt = document["result"]
    if receipt["evidence"] != "arsim_loopback_status" or receipt["diagnostics"]:
        raise ValueError("The status report needs a successful loopback status check")
    reviewed, smoke = receipt["rucReview"], receipt["arsimSmoke"]
    package = ruc["archive"]["sha256"]
    if (reviewed["archive"]["sha256"] != package or smoke["packageSha256"] != package
            or any(reviewed[key] != ruc[key] for key in ("jobId", "planSha256"))
            or smoke["planSha256"] != ruc["planSha256"] or reviewed["sourceMatched"] is not True):
        raise TraceIssue("cycle_hmi_package_mismatch", "The status report and cycle review identify different builds or RUCs")
    environment = ruc.get("environmentReport")
    if any("environmentReport" in receipt and receipt["environmentReport"] != environment
           for receipt in (reviewed, smoke)):
        raise TraceIssue("cycle_hmi_environment_mismatch",
                         "The status report records a different workstation package review than the selected RUC")
    if smoke["state"] != "complete" or smoke["cleanup"] != "empty" or smoke["hmi"]["state"] != "complete":
        raise ValueError("The ARsim runtime and HMI check must complete with verified cleanup")
    return reviewed, smoke


def check_run(document, ruc, trace, result):
    reviewed, smoke = status_run(document, ruc)
    capture = trace["inspectionLeg"]["captureKind"]
    if capture not in ("arsim", "synthetic"):
        raise TraceIssue("cycle_hmi_capture_kind", "Pair a loopback status report with an ARsim or synthetic cycle")
    session = smoke.get("sessionId")
    witness = trace.get("hmi")
    if (not isinstance(session, str) or not session.strip() or not isinstance(witness, dict)
            or witness.get("sessionId") != session):
        raise TraceIssue("cycle_hmi_session_mismatch", "The cycle and status report need the same ARsim session ID")
    result.update(sessionId=session, captureKind=capture, packageSha256=ruc["archive"]["sha256"])
    if environment := smoke.get("environmentReport"):
        result["environmentReport"] = environment
    return smoke["hmi"], witness, reviewed


def check_heartbeat(hmi, witness, heartbeat, result):
    binding = hmi["bindingReview"]
    before, browser, after = (hmi[key] for key in ("opcUaBefore", "browser", "opcUaAfter"))
    identifier = witness["heartbeatIdentifier"]
    compiled = binding["compiled"]
    endpoint = compiled["endpoint"]
    variant_type = INTEGER_NODE_TYPES.get(compiled["dataType"])
    owner = before["ownedListener"]
    if (not isinstance(identifier, str) or not identifier.strip()
            or binding["state"] != "matched" or binding["stringIdentifier"] != identifier
            or variant_type is None
            or (owner["protocol"], owner["address"], owner["port"]) != ("tcp", HOST, endpoint["port"])
            or endpoint["host"] != HOST or browser["counterWidgetId"] != binding["domId"]):
        raise ValueError("The held heartbeat needs the matched integer counter and its owned loopback listener")
    for probe in (before, after):
        index = probe["namespaceIndex"]
        if (probe["state"] != "complete" or probe["clientDisconnected"] is not True or not read_only(probe)
                or probe["namespaceUri"] != endpoint["namespaceUri"] or probe["stringIdentifier"] != identifier
                or not integer(index, 1) or probe["nodeId"] != f"ns={index};s={identifier}"):
            raise ValueError("The heartbeat needs completed read-only observations of the same OPC UA counter")
        samples = probe["samples"]
        if len(samples) != 3 or any(not integer(row["value"]) or not good_status(row["statusCode"]) for row in samples):
            raise ValueError("Each direct heartbeat window needs three nonnegative good integer samples")
        counts = [row["value"] for row in samples]
        if counts != sorted(set(counts)):
            raise ValueError("The direct heartbeat must advance in each window")

    origins = [browser["initial"]["timeOrigin"], browser["blankTimeOrigin"], browser["reopened"]["timeOrigin"]]
    if (browser["state"] != "complete" or browser["cleanup"] != "empty"
            or not all(type(origin) in (int, float) for origin in origins)
            or not 0 < origins[0] < origins[1] < origins[2] < float("inf")):
        raise TraceIssue("cycle_hmi_reopen", "The browser needs the initial, blank and reopened documents and verified cleanup")
    initial, reopened = (browser[key]["counterSamples"] for key in ("initial", "reopened"))
    if len(initial) != 3 or len(reopened) != 2:
        raise ValueError("The heartbeat needs three initial and two reopened browser samples")
    counts = [numeric_text(value) for value in initial + reopened]
    if any(value is None for value in counts) or counts != sorted(set(counts)):
        raise ValueError("The browser heartbeat must advance through the reopen")
    comparison = result["heartbeat"] = {"identifier": identifier, "atHold": heartbeat}
    check_samples(before, browser, after, variant_type, comparison)
    if not integer(heartbeat) or heartbeat > comparison["directBeforeLast"]:
        raise TraceIssue("cycle_hmi_hold_order", "The next hold heartbeat must be at or before the last direct pre-browser sample")


def check_status_role(hmi, package, role, identifier, expected, widgets):
    matches = [row for row in hmi["statusBindingReviews"]
               if row["bindingReview"]["stringIdentifier"] == identifier]
    if len(matches) != 1:
        raise TraceIssue("cycle_hmi_binding_mismatch", f"Expected one reviewed status binding for {role}")
    review = matches[0]
    binding = review["bindingReview"]
    widget = binding["domId"]
    compiled = binding["compiled"]
    variant_type = INTEGER_NODE_TYPES.get(compiled["dataType"])
    if (review["archive"]["sha256"] != package or binding["state"] != "matched"
            or compiled["endpoint"] != hmi["bindingReview"]["compiled"]["endpoint"] or variant_type is None
            or not isinstance(widget, str) or not widget.strip() or widget in widgets):
        raise TraceIssue("cycle_hmi_binding_mismatch", f"The compiled binding for {role} differs from the reviewed HMI")
    widgets.add(widget)
    if type(review["expectedValue"]) is not int or review["expectedValue"] != expected:
        raise TraceIssue("cycle_hmi_status_mismatch", f"The status check expected a different {role} than the next hold")
    for window in ("opcUaBefore", "opcUaAfter"):
        probe = hmi[window]
        node = probe["statusValues"].get(identifier, {})
        index = probe["namespaceIndex"]
        if (node.get("nodeId") != f"ns={index};s={identifier}" or node.get("variantType") != variant_type
                or not read_only(node) or not good_status(node.get("statusCode"))):
            raise TraceIssue("cycle_hmi_status_quality", f"{role} needs a good read-only {variant_type} node in {window}")
        if type(node.get("value")) is not int or node["value"] != expected:
            raise TraceIssue("cycle_hmi_status_mismatch", f"{role} differs from the next hold in {window}")
    for document in ("initial", "reopened"):
        shown = hmi["browser"][document]["statusValues"].get(widget)
        if numeric_text(shown) != expected:
            raise TraceIssue("cycle_hmi_status_mismatch", f"{role} differs from the next hold in the {document} page")
    return {"identifier": identifier, "widget": widget, "value": expected, "variantType": variant_type}


def retained_scan(hmi, required):
    if "scanCapture" not in hmi:
        if required:
            raise TraceIssue("cycle_hmi_scan_capture_missing", "The HMI session has no retained scan capture")
        return
    retained = hmi["scanCapture"]
    owner = hmi["opcUaAfter"]["ownedListener"]
    if (not isinstance(retained, dict) or retained.get("state") != "retained" or retained.get("path") != CAPTURE_COPY
            or not isinstance(owner, dict) or retained.get("ownedListener") != owner
            or not integer(retained.get("bytes"), 1) or not isinstance(retained.get("sha256"), str)
            or not SHA256.fullmatch(retained["sha256"])):
        raise TraceIssue("cycle_hmi_scan_capture_mismatch", "The HMI report has an invalid retained scan receipt")
    return retained


def check_session_capture(hmi, cycle, result, required):
    retained = retained_scan(hmi, required)
    if retained is None:
        return
    reviewed = cycle.get("scanCapture", {})
    if retained["bytes"] != reviewed.get("bytes") or retained["sha256"] != reviewed.get("sha256"):
        raise TraceIssue("cycle_hmi_scan_capture_mismatch", "The reviewed scan file differs from the one retained by the HMI session")
    result["scanCapture"] = {"state": "matched", "sha256": retained["sha256"], "bytes": retained["bytes"]}


def recorded_status_roles(hmi):
    roles = {}
    for row in hmi.get("statusBindingReviews", ()):
        if "role" not in row:
            continue
        role = row["role"]
        identifier = row["bindingReview"]["stringIdentifier"]
        if (role not in HELD_STATUS_ROLES or role in roles or not isinstance(identifier, str)
                or not identifier.strip() or identifier in roles.values()):
            raise TraceIssue("cycle_hmi_status_roles", "The HMI receipt needs unique held-status roles and identifiers")
        roles[role] = identifier
    return roles


def bind_session_scan_capture(trace, document, ruc):
    try:
        _, smoke = status_run(document, ruc)
        status = smoke["hmi"]
        retained = retained_scan(status, required=True)
        roles = recorded_status_roles(status)
        heartbeat = status["bindingReview"]["stringIdentifier"]
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("The status report has missing or malformed session capture fields") from error
    session = smoke.get("sessionId")
    if not isinstance(session, str) or not session.strip():
        raise TraceIssue("cycle_hmi_session_mismatch", "The status report needs its ARsim session ID")
    scan, hmi = trace["scanCapture"], trace.get("hmi", {})
    digest = retained["sha256"]
    if scan.get("sha256", digest) != digest:
        raise TraceIssue("cycle_hmi_scan_capture_mismatch", "The trace descriptor's scan hash differs from the HMI receipt")
    if hmi.get("sessionId", session) != session:
        raise TraceIssue("cycle_hmi_session_mismatch", "The trace descriptor's session ID differs from the HMI receipt")
    if hmi.get("heartbeatIdentifier", heartbeat) != heartbeat:
        raise TraceIssue("cycle_hmi_binding_mismatch", "The descriptor's heartbeat identifier differs from the HMI receipt")
    if "bindings" not in hmi and set(roles) != set(HELD_STATUS_ROLES):
        raise TraceIssue("cycle_hmi_status_roles", "Supply hmi.bindings or all four held-status roles in the HMI receipt")
    if "heartbeatIdentifier" not in hmi and (not isinstance(heartbeat, str) or not heartbeat.strip()):
        raise TraceIssue("cycle_hmi_binding_mismatch", "The HMI receipt needs a reviewed heartbeat identifier")
    defaults = {"sessionId": session, "heartbeatIdentifier": heartbeat, "bindings": roles}
    derived = ["scanCapture.sha256"] if "sha256" not in scan else []
    derived.extend(f"hmi.{key}" for key in defaults if key not in hmi)
    bound = {**trace, "scanCapture": {**scan, "sha256": digest}, "hmi": defaults | hmi}
    return bound, {"derivedFields": derived, "sessionId": session, "scanSha256": digest}


def check_assembly_widget(ruc, reviewed, browser, route, result):
    manager, assembly = route["managerId"], route["assembly"]
    browser_id = browser.get("assemblyWidgetId")
    widget_result = result["assemblyWidget"] = {"state": "issues", "domId": browser_id, "manager": manager,
                                                "assembly": assembly, "documents": {}}
    start = reviewed.get("startPageWidget", {})
    pinned_start = ruc.get("startPageWidget")
    if (not isinstance(browser_id, str) or not browser_id or browser_id == browser.get("counterWidgetId")
            or start.get("state") != "matched" or start.get("domId") != browser_id
            or (pinned_start is not None and pinned_start.get("domId") != browser_id)):
        raise TraceIssue("cycle_hmi_assembly_widget", "The HMI report needs the reviewed Assembly6D start-page widget")
    widgets = ruc["widgets"]
    matches = [row for row in widgets if f"{row['contentId']}_{row['id']}" == browser_id]
    compiled = start["compiled"]
    if (len(matches) != 1 or not assembly or compiled["widgetType"] != ASSEMBLY_WIDGET
            or compiled["assembly"] != assembly or compiled["manager"] not in (manager, f"mapp6D::{manager}")):
        raise TraceIssue("cycle_hmi_assembly_route", "The HMI widget must show the cycle's compiled assembly and Traffic Manager")
    widget, = matches
    if (widget["className"] != ASSEMBLY_WIDGET or (widget["assembly"], widget["manager"], widget["path"]) !=
            (compiled["assembly"], compiled["manager"], compiled["content"])):
        raise TraceIssue("cycle_hmi_assembly_route", "The HMI widget differs from the widget read from the cycle RUC")
    client_map = compiled["clientMapUrl"]
    paths = layout_asset_paths(widgets, browser_id)
    if (set(paths) != {"assembly", "trafficManager"} or not isinstance(client_map, str) or not client_map
            or browser.get("startPageMapPath") != client_map
            or (pinned_start is not None and pinned_start.get("compiled") != compiled)):
        raise TraceIssue("cycle_hmi_assembly_route", "The cycle widget needs its verified startup map and both layout paths")
    widget_result.update(content=compiled["content"], clientMapPath=client_map, layoutPaths=paths)
    for phase in ("initial", "reopened"):
        observed = browser[phase]
        startup = observed.get("startPageMap", {})
        layout = observed.get("layout", {})
        assets = layout.get("assets", {})
        if (observed.get("widgetType") != ASSEMBLY_WIDGET or startup.get("state") != "observed"
                or startup.get("path") != client_map or http_status(startup.get("httpStatus")) not in (200, 304)):
            raise TraceIssue("cycle_hmi_assembly_document", f"The {phase} HMI document needs the widget and its startup map")
        if layout.get("state") != "observed" or not integer(layout.get("boundedSvgCount"), 1):
            raise TraceIssue("cycle_hmi_assembly_layout", f"The {phase} HMI document needs a rendered 6D layout")
        statuses = {}
        for role, path in paths.items():
            asset = assets.get(role, {})
            if asset.get("path") != path or http_status(asset.get("httpStatus")) not in (200, 304):
                raise TraceIssue("cycle_hmi_assembly_layout", f"The {phase} HMI document did not load the expected {role} SVG")
            statuses[role] = asset["httpStatus"]
        widget_result["documents"][phase] = {"startPageMapStatus": startup["httpStatus"], "layoutStatuses": statuses}
    widget_result["state"] = "matched"


def captured_layout_assets(smoke, paths):
    browser = smoke["hmi"]["browser"]
    capture = browser.get("layoutCapture", {})
    owner = capture.get("ownedListener", {})
    if (capture.get("state") != "complete" or not isinstance(owner, dict) or owner not in smoke.get("ownedEndpoints", [])
            or owner.get("protocol") != "tcp" or owner.get("address") != HOST
            or not integer(owner.get("port"), 1) or browser.get("url") != f"http://{HOST}:{owner['port']}/"):
        raise TraceIssue("cycle_hmi_layout_capture", "The HMI report needs SVGs captured from its owned HTTP listener")
    assets = {}
    for role, path in paths.items():
        retained = capture.get("assets", {}).get(role, {})
        digest = retained.get("sha256")
        if (path is None or retained.get("path") != path or http_status(retained.get("status")) != 200
                or not integer(retained.get("bytes"), 1) or not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise TraceIssue("cycle_hmi_layout_capture", f"The HMI report needs a complete capture of the {role} SVG")
        assets[role] = retained
    return assets


def check_layout_svg_pairing(ruc, smoke, response, result):
    if (type(response.get("schemaVersion")) is not int or response["schemaVersion"] != 1
            or response.get("operation") != "layout_svg_review" or response.get("status") != "complete"):
        result["layoutGeometry"] = {"state": "issues", "assets": {}}
        raise TraceIssue("cycle_hmi_layout_review_invalid", "Supply a completed layout_svg_review JSON response")
    pair_layout_geometry(ruc, smoke, response["result"], result)


def pair_layout_geometry(ruc, smoke, reviewed, result):
    geometry = result["layoutGeometry"] = {"state": "issues", "assets": {}}
    svg, svg_ruc = reviewed["layoutSvgReview"], reviewed["rucReview"]
    if reviewed["evidence"] != "layout_svg_readback" or reviewed["diagnostics"] or svg["state"] != "matched":
        raise TraceIssue("cycle_hmi_layout_review_invalid", "The saved and compiled geometry must match both reviewed SVGs")
    if (svg_ruc["archive"]["sha256"] != ruc["archive"]["sha256"] or svg_ruc["sourceMatched"] is not True
            or any(svg_ruc[key] != ruc[key] for key in ("jobId", "planSha256"))):
        raise TraceIssue("cycle_hmi_layout_package", "The layout SVG review and cycle identify different builds or RUCs")
    widget = result["assemblyWidget"]
    if svg["assemblyId"] != widget["assembly"] or svg["managerId"] != widget["manager"]:
        raise TraceIssue("cycle_hmi_layout_route", "The layout SVG review identifies a different assembly or Traffic Manager")
    if "statusCapture" in svg and svg["statusCapture"].get("sessionId") != smoke.get("sessionId"):
        raise TraceIssue("cycle_hmi_layout_session", "The layout SVG review and cycle identify different HMI sessions")
    for role, retained in captured_layout_assets(smoke, widget["layoutPaths"]).items():
        digest = retained["sha256"]
        if svg.get("assets", {}).get(role, {}).get("sha256") != digest:
            raise TraceIssue("cycle_hmi_layout_hash", f"The reviewed {role} SVG differs from the SVG captured in the HMI session")
        geometry["assets"][role] = {"path": retained["path"], "sha256": digest}
    if "mappingLineEndpoints" in svg:
        endpoints = svg["mappingLineEndpoints"]
        if endpoints.get("state") != "matched":
            raise TraceIssue("cycle_hmi_layout_review_invalid", "The geometry report's mapping line endpoint review must match")
        geometry["mappingLineEndpoints"] = endpoints
    if "trafficLabels" in svg:
        labels = svg["trafficLabels"]
        if labels.get("state") != "matched":
            raise TraceIssue("cycle_hmi_layout_review_invalid", "The geometry report's traffic label review must match")
        geometry["trafficLabels"] = {key: labels[key] for key in ("state", "checkedLabels", "failedLabels", "maxAxisDeltaMm")}
    geometry.update(state="matched", layoutId=svg["layoutId"], positionToleranceMm=svg["positionToleranceMm"])


def check_cycle_hmi(cycle, trace, ruc, document, result, *, require_session_capture=False,
                    require_hold_through_reopen=False, assembly_route=None):
    hmi, witness, reviewed = check_run(document, ruc, trace, result)
    check_session_capture(hmi, cycle, result, require_session_capture)
    identifiers = witness["bindings"]
    if (set(identifiers) != set(HELD_STATUS_ROLES)
            or not all(isinstance(value, str) and value.strip() for value in identifiers.values())
            or len(set(identifiers.values()) | {witness["heartbeatIdentifier"]}) != len(HELD_STATUS_ROLES) + 1):
        raise ValueError("Select four distinct status identifiers, separate from the heartbeat")
    if any(identifiers[role] != identifier for role, identifier in recorded_status_roles(hmi).items()):
        raise TraceIssue("cycle_hmi_status_roles", "The cycle binding identifiers disagree with the HMI receipt's held-status roles")
    hold = trace["nextHold"]
    if type(hold.get("phase")) is not int or not integer(hold.get("releaseTotal")):
        raise ValueError("The next hold needs integer phase and nonnegative releaseTotal values")
    check_heartbeat(hmi, witness, hold.get("heartbeat"), result)
    continuation = cycle.get("holdContinuation")
    if require_hold_through_reopen and not continuation:
        raise TraceIssue("cycle_hmi_hold_missing", "Capture consecutive held scans through the last direct heartbeat after reopen")
    if continuation:
        after_last = hmi["opcUaAfter"]["samples"][-1]["value"]
        end_heartbeat = continuation["lastHeartbeat"]
        coverage = result["holdCoverage"] = {
            "state": "issues", "lastHoldScan": continuation["lastScan"],
            "lastHoldHeartbeat": end_heartbeat, "directAfterLast": after_last,
        }
        if end_heartbeat < after_last:
            raise TraceIssue("cycle_hmi_hold_coverage", "The held scan window ends before the final direct sample after reopen")
        coverage["state"] = "matched"
    expected = {**cycle["nextHold"], "phase": hold["phase"], "releaseTotal": hold["releaseTotal"]}
    widgets = {hmi["bindingReview"]["domId"]}
    result["status"] = checked = {}
    for role in HELD_STATUS_ROLES:
        checked[role] = check_status_role(hmi, result["packageSha256"], role, identifiers[role], expected[role], widgets)
    if assembly_route is not None:
        check_assembly_widget(ruc, reviewed, hmi["browser"], assembly_route, result)


def check_environment_file(path, expected, *, required):
    if expected is None:
        raise TraceIssue("cycle_hmi_environment_file_unbound",
                         "The HMI status response must record the retained workstation package report")
    found = False
    try:
        check_source(path, False)
        found = True
        _, snapshot = json_snapshot(path, ENVIRONMENT_REPORT_LIMIT)
    except FileNotFoundError as error:
        if not found and not required:
            return None
        raise TraceIssue("cycle_hmi_environment_file_missing", "The selected workstation report file is missing") from error
    except (OSError, RecursionError, RuntimeError, ValueError) as error:
        raise TraceIssue("cycle_hmi_environment_file_invalid",
                         f"Could not read the retained workstation report as a regular JSON file. {error}") from error
    if snapshot["sha256"] != expected["sha256"]:
        raise TraceIssue("cycle_hmi_environment_file_mismatch",
                         "The retained workstation report SHA-256 differs from the reviewed RUC and HMI status response")
    return snapshot


def review_cycle_hmi(report, trace, path, *, require_session_capture=False, require_assembly_widget=False,
                     require_hold_through_reopen=False, layout_report_path=None, captured_layout_review=None,
                     status_snapshot=None, environment_report_path=None):
    result = report["cycleHmiReview"]
    evidence_path = path
    try:
        if status_snapshot is None:
            status_snapshot = json_snapshot(path, TRACE_LIMIT)
        document, result["statusReport"] = status_snapshot
        ruc = report["rucReview"]
        check_widget = require_assembly_widget or layout_report_path is not None or captured_layout_review is not None
        assembly_route = report["routeReview"] if check_widget else None
        check_cycle_hmi(report["cycleTraceReview"], trace, ruc, document, result,
                        require_session_capture=require_session_capture, require_hold_through_reopen=require_hold_through_reopen,
                        assembly_route=assembly_route)
        environment = result.get("environmentReport")
        if environment_report_path is not None or environment is not None:
            evidence_path = environment_report_path or Path(result["statusReport"]["path"]).with_name(
                RETAINED_ENVIRONMENT_REPORT.name)
            snapshot = check_environment_file(evidence_path, environment, required=environment_report_path is not None)
            if snapshot is not None:
                result["environmentReportFile"] = snapshot
            evidence_path = path
        if layout_report_path is not None:
            evidence_path = layout_report_path
            reviewed, result["layoutSvgReport"] = json_snapshot(layout_report_path, TRACE_LIMIT)
            check_layout_svg_pairing(ruc, document["result"]["arsimSmoke"], reviewed, result)
        elif captured_layout_review is not None:
            reviewed = captured_layout_review(status_snapshot=status_snapshot)
            svg = report["layoutSvgReview"] = reviewed["layoutSvgReview"]
            if diagnostics := reviewed["diagnostics"]:
                result["layoutGeometry"] = {"state": svg["state"], "assets": {}}
                result["state"] = "issues" if any(row["severity"] == "error" for row in diagnostics) else "incomplete"
                report["diagnostics"].extend(diagnostics)
                return
            pair_layout_geometry(ruc, document["result"]["arsimSmoke"], reviewed, result)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, TraceIssue) else "cycle_hmi_invalid"
        message = str(error) if isinstance(error, (OSError, RuntimeError, ValueError)) else "The status or cycle evidence has missing or malformed fields"
        result["state"] = "issues"
        report["diagnostics"].append({"scope": "cycle_hmi", "severity": "error", "code": code,
                                      "path": evidence_path.as_posix(), "message": message})
    else:
        result["state"] = "matched"
        report["evidence"] = "cycle_hmi_readback"

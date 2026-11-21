"""Correlate a selected cycle's next hold with a completed loopback status report."""

from .arsim_smoke import HOST
from .browser_smoke import numeric_text
from .hmi_smoke import check_samples
from .launchpad_route import json_snapshot
from .opcua_smoke import CURRENT_READ, INTEGER_NODE_TYPES
from .runtime_capture import CAPTURE_COPY
from .station_trace import TRACE_LIMIT, TraceIssue, integer


ROLES = ("requestId", "shuttleId", "phase", "releaseTotal")


def good_status(code):
    return integer(code) and code < 0x40000000


def read_only(node):
    return all(type(node.get(key)) is int and node[key] == CURRENT_READ
               for key in ("accessLevel", "userAccessLevel"))


def check_run(document, ruc, trace, result):
    if (type(document.get("schemaVersion")) is not int or document["schemaVersion"] != 1
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

    capture = trace["inspectionLeg"]["captureKind"]
    if capture not in ("arsim", "synthetic"):
        raise TraceIssue("cycle_hmi_capture_kind", "Pair a loopback status report with an ARsim or synthetic cycle")
    session = smoke.get("sessionId")
    witness = trace.get("hmi")
    if (not isinstance(session, str) or not session.strip() or not isinstance(witness, dict)
            or witness.get("sessionId") != session):
        raise TraceIssue("cycle_hmi_session_mismatch", "The cycle and status report need the same ARsim session ID")
    hmi = smoke["hmi"]
    if smoke["state"] != "complete" or smoke["cleanup"] != "empty" or hmi["state"] != "complete":
        raise ValueError("The ARsim runtime and HMI check must complete with verified cleanup")
    result.update(sessionId=session, captureKind=capture, packageSha256=package)
    return hmi, witness


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


def check_session_capture(hmi, cycle, result, required):
    if "scanCapture" not in hmi:
        if required:
            raise TraceIssue("cycle_hmi_scan_capture_missing", "The HMI session has no retained scan capture")
        return
    retained, reviewed = hmi["scanCapture"], cycle.get("scanCapture", {})
    if (retained.get("state") != "retained" or retained.get("path") != CAPTURE_COPY
            or retained.get("ownedListener") != hmi["opcUaAfter"]["ownedListener"]
            or not integer(retained.get("bytes"), 1) or retained["bytes"] != reviewed.get("bytes")
            or retained.get("sha256") != reviewed.get("sha256")):
        raise TraceIssue("cycle_hmi_scan_capture_mismatch", "The reviewed scan file differs from the one retained by the HMI session")
    result["scanCapture"] = {"state": "matched", "sha256": retained["sha256"], "bytes": retained["bytes"]}


def check_cycle_hmi(cycle, trace, ruc, document, result, *, require_session_capture=False):
    hmi, witness = check_run(document, ruc, trace, result)
    check_session_capture(hmi, cycle, result, require_session_capture)
    identifiers = witness["bindings"]
    if (set(identifiers) != set(ROLES)
            or not all(isinstance(value, str) and value.strip() for value in identifiers.values())
            or len(set(identifiers.values()) | {witness["heartbeatIdentifier"]}) != len(ROLES) + 1):
        raise ValueError("Select four distinct status identifiers, separate from the heartbeat")
    hold = trace["nextHold"]
    if type(hold.get("phase")) is not int or not integer(hold.get("releaseTotal")):
        raise ValueError("The next hold needs integer phase and nonnegative releaseTotal values")
    check_heartbeat(hmi, witness, hold.get("heartbeat"), result)
    if continuation := cycle.get("holdContinuation"):
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
    for role in ROLES:
        checked[role] = check_status_role(hmi, result["packageSha256"], role, identifiers[role], expected[role], widgets)


def review_cycle_hmi(report, trace, path, *, require_session_capture=False):
    result = report["cycleHmiReview"]
    try:
        document, result["statusReport"] = json_snapshot(path, TRACE_LIMIT)
        check_cycle_hmi(report["cycleTraceReview"], trace, report["rucReview"], document, result,
                        require_session_capture=require_session_capture)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, TraceIssue) else "cycle_hmi_invalid"
        message = str(error) if isinstance(error, (OSError, RuntimeError, ValueError)) else "The status or cycle evidence has missing or malformed fields"
        result["state"] = "issues"
        report["diagnostics"].append({"scope": "cycle_hmi", "severity": "error", "code": code,
                                      "path": path.as_posix(), "message": message})
    else:
        result["state"] = "matched"
        report["evidence"] = "cycle_hmi_readback"

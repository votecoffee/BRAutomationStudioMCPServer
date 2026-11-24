"""Review a PLC-authored held-status CSV against the archived HMI session."""

import csv
from hashlib import sha256
from io import StringIO
from itertools import chain
from pathlib import Path

from .arsim_smoke import SESSION_RECEIPT
from .browser_smoke import numeric_text
from .cycle_capture import HOLD_HMI, Sample, scan_hold_range, string_map
from .cycle_hmi import (check_heartbeat, check_held_status_identifiers, check_session_capture, check_session_receipt,
                        check_simulation_preflight_receipt, check_status_role, recorded_status_roles, status_run)
from .hmi_smoke import HELD_STATUS_ROLES
from .json_io import json_snapshot
from .native_build import RETAINED_SIMULATION_PREFLIGHT_REPORT, check_source
from .ruc_review import review_ruc
from .runtime_capture import CAPTURE_COPY
from .station_trace import TRACE_LIMIT, TraceIssue, integer

FIELDS = ("scan", "heartbeat", *HELD_STATUS_ROLES)
CYCLE_HOLD_FIELDS = ("scan", "hold.station", "hold.requestId", "hold.shuttleId", "hold.held", "hold.release",
                     "hold.resultAccepted", *HOLD_HMI)


def held_rows(raw, columns, *, window=None, stations=None, delimiter=","):
    reader = csv.reader(StringIO(raw.decode("utf-8-sig"), newline=""), delimiter=delimiter, strict=True)
    header = next(reader, [])
    if not header or not all(name.strip() for name in header) or len(set(header)) != len(header):
        raise ValueError("The CSV header needs distinct nonempty column names")
    fields = CYCLE_HOLD_FIELDS if window is not None else FIELDS
    wanted = {columns.get(field, field) for field in fields}
    if len(wanted) != len(fields):
        raise ValueError("Each held-status field needs a distinct CSV column")
    if missing := wanted.difference(header):
        raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
    previous = -1
    for index, cells in enumerate(reader, 1):
        if len(cells) != len(header):
            raise ValueError(f"Scan sample {index} does not match the header width")
        sample = Sample(header, cells, columns, index, None)
        if window is None:
            yield {field: sample.number(field) for field in FIELDS}
            continue
        scan = sample.number("scan")
        if scan <= previous:
            raise TraceIssue("held_scan_order", f"PLC scan {scan} at sample {index} does not follow {previous}")
        first, last = window
        if previous < first < scan:
            raise TraceIssue("held_scan_window", f"The CSV is missing the selected first held scan {first}")
        previous = scan
        if scan >= first and (last == "last" or scan <= last):
            yield sample.hold(scan, stations, HOLD_HMI)


def review_rows(raw, columns, hmi, bindings, package, result, *, window=None, stations=None, delimiter=","):
    rows = held_rows(raw, columns, window=window, stations=stations, delimiter=delimiter)
    first = next(rows, None)
    if window is not None and first is None:
        raise TraceIssue("held_scan_window", f"The CSV is missing the selected first held scan {window[0]}")
    if first is None:
        raise ValueError("The retained CSV needs held-status scan records")
    expected = {role: first[role] for role in HELD_STATUS_ROLES}
    if not all(integer(first[field], minimum) for field, minimum in
               (("scan", 0), ("requestId", 1), ("shuttleId", 1), ("releaseTotal", 0))):
        raise ValueError("The hold needs positive request and shuttle IDs and nonnegative scan and release counts")

    heartbeat = hmi["bindingReview"]["stringIdentifier"]
    check_held_status_identifiers(hmi, bindings, heartbeat)
    check_heartbeat(hmi, {"heartbeatIdentifier": heartbeat}, first["heartbeat"], result)
    witnesses = {row["value"] for phase in ("opcUaBefore", "opcUaAfter") for row in hmi[phase]["samples"]}
    witnesses.update(int(numeric_text(value)) for phase in ("initial", "reopened")
                     for value in hmi["browser"][phase]["counterSamples"])
    missing = witnesses.copy()
    previous = {"scan": first["scan"] - 1, "heartbeat": first["heartbeat"]}
    for count, row in enumerate(chain((first,), rows), 1):
        scan = row["scan"]
        if scan != previous["scan"] + 1 or row["heartbeat"] < previous["heartbeat"]:
            raise TraceIssue("held_scan_continuity", f"PLC scan {scan} must follow scan {previous['scan']} without a heartbeat reset")
        if window is not None and (not row["held"] or row["release"] or row["resultAccepted"]
                                   or row["station"] != first["station"]):
            raise TraceIssue("held_scan_hold_state",
                             f"PLC scan {scan} needs the same station with hold active, Release clear and result acceptance clear")
        if changed := [role for role, value in expected.items() if row[role] != value]:
            raise TraceIssue("held_scan_status_changed", f"PLC scan {scan} changed held status: {', '.join(changed)}")
        missing.discard(row["heartbeat"])
        previous = row
    result["window"] = {"rows": count, "firstScan": first["scan"], "lastScan": previous["scan"],
                        "firstHeartbeat": first["heartbeat"], "lastHeartbeat": previous["heartbeat"],
                        "directAfterLast": hmi["opcUaAfter"]["samples"][-1]["value"],
                        "observedHeartbeats": sorted(witnesses), "missingHeartbeats": sorted(missing)}
    if window is not None:
        result["window"]["station"] = first["station"]
        last = window[1]
        if (last == "last" and count < 2) or (last != "last" and previous["scan"] != last):
            raise TraceIssue("held_scan_window", f"The CSV needs held scans through the selected end {last}")
    if missing:
        raise TraceIssue("held_scan_heartbeat_missing", "The PLC scan window needs every direct and browser heartbeat")

    widgets = {hmi["bindingReview"]["domId"]}
    result["status"] = {role: check_status_role(hmi, package, role, bindings[role], expected[role], widgets)
                        for role in HELD_STATUS_ROLES}


def cycle_scan_parameters(trace, smoke, digest, result):
    if not isinstance(trace, dict) or type(trace.get("schemaVersion")) is not int or trace["schemaVersion"] != 1:
        raise ValueError("Supply a schemaVersion 1 cycle scan descriptor")
    capture = trace.get("scanCapture")
    if not isinstance(capture, dict) or "holdThroughScan" not in capture:
        raise ValueError("The cycle descriptor needs scanCapture.nextHoldScan and holdThroughScan")
    if capture.get("sha256", digest) != digest:
        raise TraceIssue("cycle_capture_hash_mismatch", "The retained CSV hash differs from the cycle descriptor")
    delimiter = capture.get("delimiter", ",")
    if capture.get("format", "csv") != "csv" or delimiter not in (",", ";", "\t"):
        raise ValueError("The held-status cycle window needs CSV with a comma, semicolon or tab delimiter")
    window = scan_hold_range(capture)
    hmi = smoke["hmi"]
    witness = trace.get("hmi", {})
    if not isinstance(witness, dict):
        raise ValueError("The cycle descriptor's hmi field must be an object")
    if witness.get("sessionId", smoke["sessionId"]) != smoke["sessionId"]:
        raise TraceIssue("cycle_hmi_session_mismatch", "The cycle descriptor identifies a different ARsim session")
    heartbeat = hmi["bindingReview"]["stringIdentifier"]
    if witness.get("heartbeatIdentifier", heartbeat) != heartbeat:
        raise TraceIssue("cycle_hmi_binding_mismatch", "The cycle descriptor identifies a different heartbeat")

    columns = string_map(capture.get("columns", {}), "columns")
    stations = string_map(capture["stationNames"], "stationNames") if "stationNames" in capture else None
    bindings = (string_map(witness["bindings"], "bindings") if "bindings" in witness else recorded_status_roles(hmi))
    result["cycleSelection"] = {"nextHoldScan": window[0], "holdThroughScan": window[1]}
    return columns, bindings, {"window": window, "stations": stations, "delimiter": delimiter}


def held_status_scan_review(project, job, status_path, descriptor_path=None, cycle_trace_path=None):
    report = review_ruc(project, job, require_6d=False)
    result = report["heldStatusScanReview"] = {"state": "not_checked"}
    if report["diagnostics"] or not report["rucReview"].get("sourceMatched"):
        return report
    path = status_path
    try:
        document, result["statusReport"] = json_snapshot(status_path, TRACE_LIMIT)
        _, smoke = status_run(document, report["rucReview"])
        session = smoke.get("sessionId")
        if not isinstance(session, str) or not session.strip():
            raise TraceIssue("held_scan_session", "The status report needs its ARsim session ID")
        result["sessionId"] = session
        directory = Path(result["statusReport"]["path"]).parent
        path = directory / SESSION_RECEIPT
        result["sessionReceipt"] = check_session_receipt(path, smoke, code="held_scan_session")
        if preflight := smoke.get("simulationPreflightReport"):
            path = directory / RETAINED_SIMULATION_PREFLIGHT_REPORT
            result["simulationPreflightReportFile"] = check_simulation_preflight_receipt(path, preflight)
        hmi = smoke["hmi"]
        if hmi["opcUaAfter"]["ownedListener"] not in smoke.get("ownedEndpoints", []):
            raise TraceIssue("held_scan_session", "The status reads need the OPC UA listener owned by this session")

        path = directory / CAPTURE_COPY
        check_source(path, False)
        with path.open("rb") as stream:
            raw = stream.read(TRACE_LIMIT + 1)
        if len(raw) > TRACE_LIMIT:
            raise ValueError(f"CSV input exceeds {TRACE_LIMIT} bytes")
        copied = {"bytes": len(raw), "sha256": sha256(raw).hexdigest()}
        check_session_capture(hmi, {"scanCapture": copied}, result, required=True)
        result["scanCapture"]["path"] = str(path)

        columns, bindings, scan_options = {}, recorded_status_roles(hmi), {}
        if cycle_trace_path is not None:
            path = cycle_trace_path
            trace, result["cycleTrace"] = json_snapshot(path, TRACE_LIMIT)
            columns, bindings, scan_options = cycle_scan_parameters(trace, smoke, copied["sha256"], result)
        elif descriptor_path is not None:
            path = descriptor_path
            descriptor, result["descriptor"] = json_snapshot(path, TRACE_LIMIT)
            if (not isinstance(descriptor, dict) or descriptor.keys() - {"schemaVersion", "columns", "bindings"}
                    or type(descriptor.get("schemaVersion")) is not int or descriptor["schemaVersion"] != 1):
                raise ValueError("Supply a schemaVersion 1 held-status scan descriptor")
            columns = string_map(descriptor.get("columns", {}), "columns")
            if columns.keys() - set(FIELDS):
                raise ValueError("Map only the supported held-status scan columns")
            if "bindings" in descriptor:
                bindings = string_map(descriptor["bindings"], "bindings")
        if set(bindings) != set(HELD_STATUS_ROLES):
            raise TraceIssue("cycle_hmi_status_roles", "Record all four held-status roles or supply their binding map")

        path = directory / CAPTURE_COPY
        review_rows(raw, columns, hmi, bindings, smoke["packageSha256"], result, **scan_options)
    except (AttributeError, csv.Error, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        code = error.code if isinstance(error, TraceIssue) else "held_scan_invalid"
        message = "The held-status evidence has missing or malformed fields"
        if isinstance(error, (csv.Error, OSError, RuntimeError, ValueError)):
            message = str(error)
        report["diagnostics"].append({"scope": "held_status_scan", "severity": "error", "code": code,
                                      "path": path.as_posix(), "message": message})
        result["state"] = "issues"
    else:
        result["state"] = "matched"
        report["evidence"] = "held_status_scan_readback"
    return report

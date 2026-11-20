"""Correlate an inspection leg, return and next hold for one shuttle in a saved trace."""

from .cycle_hmi import review_cycle_hmi
from .launchpad_route import json_snapshot, launchpad_route_review
from .station_trace import (
    TRACE_LIMIT, TraceIssue, check_bound_leg, check_detections, integer, leg_identity, scan_detections, selected_leg,
)


def check_return(trace, outbound, inbound, result):
    if trace is None:
        raise TraceIssue("cycle_trace_return_incomplete", "Capture the return leg after outbound arrival", "warning")
    if not isinstance(trace, dict):
        raise ValueError("The cycle trace needs a returnLeg object")
    if (trace.get("sourceStation"), trace.get("destinationStation")) != (inbound[0], inbound[-1]):
        raise TraceIssue("cycle_trace_return_identity", "Return stations differ from the compiled return leg")
    sequence = trace.get("monitorSequenceAtStart")
    if not integer(sequence):
        raise ValueError("Identify the last consumed return destination sequence at the start of the return capture")
    rows = trace.get("scans")
    if not isinstance(rows, list) or not rows:
        raise TraceIssue("cycle_trace_return_incomplete", "Capture the source occupancy and destination monitor on return",
                         "warning")

    shuttle_id = outbound["shuttleId"]
    previous = None
    result.update(path=inbound, occupiedScan=None, sourceClearScan=None, arrival=None, ignoredDetections=0)
    for row in rows:
        if not isinstance(row, dict) or not integer(row.get("scan")):
            raise ValueError("Each return sample needs a nonnegative integer scan index")
        scan = row["scan"]
        if any(type(row.get(key)) is not bool for key in ("sourceHealthy", "monitorHealthy")):
            raise ValueError(f"Return scan {scan} needs Boolean sourceHealthy and monitorHealthy")
        if previous is not None and scan <= previous:
            raise TraceIssue("cycle_trace_scan_order", f"Return scan {scan} does not follow scan {previous}")
        if previous is not None and scan != previous + 1:
            raise TraceIssue("cycle_trace_gap", f"Expected return scan {previous + 1}, found {scan}", "warning")
        if type(row.get("error", False)) is not bool:
            raise ValueError(f"Return scan {scan} needs a Boolean error flag when supplied")
        if row.get("error", False):
            if type(row.get("statusId")) is not int:
                raise ValueError(f"Return error at scan {scan} needs the raw StatusID")
            result["firstFault"] = {"scan": scan, "statusId": row["statusId"]}
            raise TraceIssue("cycle_trace_return_fault", f"Return error was observed at scan {scan}")
        if not row["sourceHealthy"] or not row["monitorHealthy"]:
            raise TraceIssue("cycle_trace_monitor_unverified", f"Return observation validity lost at scan {scan}",
                             "warning")
        occupying = row.get("occupyingShuttleId")
        if "occupyingShuttleId" not in row or occupying is not None and not integer(occupying, 1):
            raise ValueError(f"Return scan {scan} needs a positive occupying shuttle ID or null")
        if occupying is not None and occupying != shuttle_id:
            raise TraceIssue("cycle_trace_return_shuttle", f"A different shuttle occupied the return source at scan {scan}")
        if previous is None:
            if scan < outbound["arrival"]["scan"]:
                raise TraceIssue("cycle_trace_return_order", "Return capture begins before the accepted outbound arrival")
            if scan > outbound["lastScan"] + 1:
                raise TraceIssue("cycle_trace_gap", "The end of the outbound and start of the return captures have a gap",
                                 "warning")
            if occupying is None:
                raise TraceIssue("cycle_trace_initial_occupancy", "Return capture must start with the same shuttle at its source",
                                 "warning")
            result["occupiedScan"] = scan
        elif occupying is None and result["sourceClearScan"] is None:
            result["sourceClearScan"] = scan
        elif occupying is not None and result["sourceClearScan"] is not None:
            raise TraceIssue("cycle_trace_return_reoccupied", f"The shuttle reoccupied the return source at scan {scan}")
        result["lastScan"] = scan
        sequence = check_detections(scan_detections(row, scan), inbound[-1], shuttle_id, scan, sequence, result,
                                    armed=previous is not None)
        previous = scan

    if result["sourceClearScan"] is None or result["arrival"] is None:
        raise TraceIssue("cycle_trace_return_incomplete", "Capture ended without source clearance and a fresh return arrival",
                         "warning")
    result["arrivalObservedBeforeSourceClear"] = result["arrival"]["scan"] < result["sourceClearScan"]


def next_hold(sample, outbound, returned, station):
    if sample is None:
        raise TraceIssue("cycle_trace_hold_missing", "Capture the next held request after the return", "warning")
    if (not isinstance(sample, dict) or not integer(sample.get("scan"))
            or not all(integer(sample.get(key), 1) for key in ("requestId", "shuttleId"))
            or not all(type(sample.get(key)) is bool for key in ("held", "release", "resultAccepted"))):
        raise ValueError("The next hold needs the scan, request ID, shuttle ID and Boolean hold, release and result fields")
    earliest = max(outbound["lastScan"] + 1, returned["sourceClearScan"], returned["arrival"]["scan"])
    if sample["scan"] < earliest:
        raise TraceIssue("cycle_trace_hold_order", "The next hold precedes the witnessed completion of the two legs")
    if sample["scan"] > returned["lastScan"] + 1:
        raise TraceIssue("cycle_trace_gap", "The return and next hold observations have a scan gap", "warning")
    if ((sample.get("station"), sample["shuttleId"]) != (station, outbound["shuttleId"])
            or sample["requestId"] == outbound["requestId"]):
        raise TraceIssue("cycle_trace_hold_identity", "The next hold needs a distinct request for the same shuttle at the inspection station")
    if not sample["held"] or sample["release"] or sample["resultAccepted"]:
        raise TraceIssue("cycle_trace_hold_state", "The next request is not held with release and accepted result clear")
    return {key: sample[key] for key in ("scan", "station", "requestId", "shuttleId", "held", "release", "resultAccepted")}


def check_cycle(trace, identity, outbound_path, inbound_path, result):
    if not isinstance(trace, dict) or type(trace.get("schemaVersion")) is not int or trace["schemaVersion"] != 1:
        raise ValueError("Expected a schemaVersion 1 cycle trace")
    outbound = result["outbound"] = {"path": outbound_path, "identity": identity}
    result["phase"] = "inspection_leg"
    check_bound_leg(trace.get("inspectionLeg"), identity, outbound)
    returned = result["returnLeg"] = {"path": inbound_path}
    result["phase"] = "return_leg"
    check_return(trace.get("returnLeg"), outbound, inbound_path, returned)
    result["phase"] = "next_hold"
    result["nextHold"] = next_hold(trace.get("nextHold"), outbound, returned, outbound_path[0])
    result["phase"] = "complete"


def cycle_trace_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                       source_station, trace_path, *, hmi_status_path=None):
    report = launchpad_route_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                                    include_stations=True)
    result = report["cycleTraceReview"] = {"state": "not_checked", "sourceStation": source_station}
    if hmi_status_path is not None:
        report["cycleHmiReview"] = {"state": "not_checked"}
    if report["diagnostics"] or report["stationReview"]["state"] != "read":
        return report
    try:
        outbound = selected_leg(report, source_station)
        inbound = selected_leg(report, outbound[-1])
        if len(report["stationReview"]["legs"]) != 2 or inbound[-1] != source_station:
            raise TraceIssue("cycle_trace_route", "The compiled route needs two station legs that close the cycle")
        trace, result["trace"] = json_snapshot(trace_path, TRACE_LIMIT)
        check_cycle(trace, leg_identity(report, manager_id, outbound), outbound, inbound, result)
    except (OSError, RuntimeError, ValueError) as error:
        severity = error.severity if isinstance(error, TraceIssue) else "error"
        code = error.code if isinstance(error, TraceIssue) else "cycle_trace_invalid"
        result["state"] = "incomplete" if severity == "warning" else "issues"
        report["diagnostics"].append({"scope": "cycle_trace", "severity": severity, "code": code,
                                      "path": trace_path.as_posix(), "message": str(error)})
    else:
        result["state"] = "matched"
        report["evidence"] = "cycle_trace_readback"
        if hmi_status_path is not None:
            review_cycle_hmi(report, trace, hmi_status_path)
    return report

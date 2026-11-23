"""Correlate an inspection leg, return and next hold for one shuttle in a saved trace."""

from csv import Error as CsvError
from functools import partial
from itertools import chain
from pathlib import Path
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISREG

from .cycle_capture import SCAN_TIME, materialize_cycle
from .cycle_hmi import bind_session_scan_capture, review_cycle_hmi
from .layout_svg import layout_svg_review
from .launchpad_route import json_snapshot, launchpad_route_review
from .mptraffic_events import read_project_events
from .runtime_capture import CAPTURE_COPY
from .station_trace import (
    MONITOR_CHECKPOINTS, TRACE_LIMIT, TraceIssue, bound_launchpad_evidence, check_bound_leg, check_detections,
    check_leg_positions, check_monitor_checkpoint, check_release_motion, check_sampled_speed, check_station_positions,
    check_waypoint_positions, integer, leg_identity, scan_detections, selected_leg,
)

HOLD_FIELDS = ("station", "requestId", "shuttleId", "held", "release", "resultAccepted")
BUILD_HASH_FIELDS = ("launchPadSha256", "rucSha256")
RELEASE_TOTAL_EVENTS = ("release", "releaseDone", "arrival")


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
        before, after = MONITOR_CHECKPOINTS
        check_monitor_checkpoint(row, before, sequence)
        sequence = check_detections(scan_detections(row, scan), inbound[-1], shuttle_id, scan, sequence, result,
                                    armed=previous is not None)
        check_monitor_checkpoint(row, after, sequence)
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
    return {key: sample[key] for key in ("scan", *HOLD_FIELDS)}


def check_hold_continuation(hold, window, cycle):
    start = hold["scan"]
    if (not isinstance(window, dict) or not integer(window.get("throughScan"), start + 1)
            or not isinstance(window.get("scans"), list)):
        raise ValueError("holdContinuation needs a throughScan after the first hold and an array of subsequent scans")
    if type(hold.get("phase")) is not int or not all(integer(hold.get(key)) for key in ("releaseTotal", "heartbeat")):
        raise ValueError("The extended hold needs integer phase, nonnegative releaseTotal and heartbeat on the first hold")
    through = window["throughScan"]
    expected = {key: hold[key] for key in (*HOLD_FIELDS, "phase", "releaseTotal")}
    result = cycle["holdContinuation"] = {
        "state": "incomplete", "firstScan": start, "throughScan": through, "lastScan": start,
        "checkedScans": 1, "lastHeartbeat": hold["heartbeat"],
    }
    for row in window["scans"]:
        scan = row.get("scan") if isinstance(row, dict) else None
        if not integer(scan, result["lastScan"] + 1) or scan > through:
            result["state"] = "issues"
            raise TraceIssue("cycle_trace_hold_scan_order", "Hold scans must increase within the selected window")
        if scan != result["lastScan"] + 1:
            raise TraceIssue("cycle_trace_hold_gap", f"Expected held scan {result['lastScan'] + 1}, found {scan}", "warning")
        changed = [key for key, value in expected.items() if type(row.get(key)) is not type(value) or row[key] != value]
        if changed:
            result.update(state="issues", firstChangedScan=scan, changedFields=changed)
            raise TraceIssue("cycle_trace_hold_changed", f"Hold changed at scan {scan}: {', '.join(changed)}")
        heartbeat = row.get("heartbeat")
        if not integer(heartbeat, result["lastHeartbeat"]):
            result["state"] = "issues"
            raise TraceIssue("cycle_trace_hold_heartbeat", f"Held heartbeat is invalid or moved backwards at scan {scan}")
        result.update(lastScan=scan, lastHeartbeat=heartbeat, checkedScans=result["checkedScans"] + 1)
    if result["lastScan"] != through:
        raise TraceIssue("cycle_trace_hold_gap", f"Hold capture ended before scan {through}", "warning")
    result["state"] = "matched"


def cycle_scan_intervals(cycle):
    outbound, returned = cycle["outbound"], cycle["returnLeg"]
    release, = outbound["releaseScans"]
    outbound_arrival = outbound["arrival"]["scan"]
    return_occupancy = returned["occupiedScan"]
    return_arrival = returned["arrival"]["scan"]
    hold = cycle["nextHold"]["scan"]
    intervals = {
        "resultToRelease": (outbound["acceptedResultScan"], release),
        "releaseToDone": (release, outbound["doneScan"]),
        "releaseToOutboundArrival": (release, outbound_arrival),
        "outboundArrivalToReturnOccupancy": (outbound_arrival, return_occupancy),
        "returnOccupancyToClear": (return_occupancy, returned["sourceClearScan"]),
        "returnOccupancyToArrival": (return_occupancy, return_arrival),
        "returnArrivalToNextHold": (return_arrival, hold),
        "releaseToNextHold": (release, hold),
    }
    return {name: {"startScan": start, "endScan": end, "elapsedScans": end - start}
            for name, (start, end) in intervals.items()}


def cycle_observations_through_hold(trace):
    hold = trace["nextHold"]
    return (row for row in chain(trace["inspectionLeg"]["scans"], trace["returnLeg"]["scans"], (hold,))
            if row["scan"] <= hold["scan"])


def verify_cycle_scan_clock(trace, timing):
    clock = timing["clock"] = {"state": "incomplete"}
    times = {}
    for row in cycle_observations_through_hold(trace):
        scan = row["scan"]
        if SCAN_TIME not in row:
            clock["scan"] = scan
            raise TraceIssue("cycle_trace_scan_time_missing", f"Capture the acquisition time at PLC scan {scan}", "warning")
        time = row[SCAN_TIME]
        if not integer(time):
            clock.update(state="issues", scan=scan)
            raise TraceIssue("cycle_trace_scan_time_invalid", f"Acquisition time at scan {scan} needs nonnegative integer microseconds")
        if times.setdefault(scan, time) != time:
            clock.update(state="issues", scan=scan, timeMicroseconds=time, otherTimeMicroseconds=times[scan])
            raise TraceIssue("cycle_trace_scan_time_overlap", f"The leg observations disagree on acquisition time at scan {scan}")
    previous = None
    for scan, time in sorted(times.items()):
        if previous is not None and time <= previous:
            clock.update(state="issues", scan=scan, timeMicroseconds=time, precedingTimeMicroseconds=previous)
            raise TraceIssue("cycle_trace_scan_time_order", f"Acquisition time must increase at scan {scan}")
        previous = time
    for interval in timing["intervals"].values():
        start, end = times[interval["startScan"]], times[interval["endScan"]]
        interval.update(startTimeMicroseconds=start, endTimeMicroseconds=end, elapsedMicroseconds=end - start)
    clock.update(state="matched", firstScan=min(times), lastScan=max(times), checkedScans=len(times))


def check_cycle(trace, identity, outbound_path, inbound_path, result, *, max_release_to_next_hold_scans=None,
                verify_scan_clock=False):
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
    if "holdContinuation" in trace:
        result["phase"] = "hold_continuation"
        check_hold_continuation(trace["nextHold"], trace["holdContinuation"], result)
    timing = result["scanTiming"] = {"intervals": cycle_scan_intervals(result)}
    if verify_scan_clock:
        result["phase"] = "scan_clock"
        verify_cycle_scan_clock(trace, timing)
    if max_release_to_next_hold_scans is not None:
        result["phase"] = "scan_timing"
        elapsed = timing["intervals"]["releaseToNextHold"]["elapsedScans"]
        within_limit = elapsed <= max_release_to_next_hold_scans
        timing["releaseToNextHoldLimit"] = {
            "maximumScans": max_release_to_next_hold_scans, "state": "matched" if within_limit else "issues",
        }
        if not within_limit:
            raise TraceIssue("cycle_trace_scan_limit",
                             f"Release to the next hold took {elapsed} PLC scans, above the configured limit of {max_release_to_next_hold_scans}")
    result["phase"] = "complete"


def check_release_total(trace, cycle, event):
    outbound = cycle["outbound"]
    event_scan = {
        "release": outbound["releaseScans"][0],
        "releaseDone": outbound["doneScan"],
        "arrival": outbound["arrival"]["scan"],
    }[event]
    inspection = trace["inspectionLeg"]["scans"]
    hold = trace["nextHold"]
    result = cycle["releaseTotalReview"] = {
        "state": "incomplete", "event": event, "eventScan": event_scan,
        "firstScan": inspection[0]["scan"], "lastScan": hold["scan"],
    }
    initial = inspection[0].get("releaseTotal")
    for row in cycle_observations_through_hold(trace):
        scan = row["scan"]
        if "releaseTotal" not in row:
            raise TraceIssue("cycle_trace_release_total_missing", f"Capture releaseTotal at PLC scan {scan}", "warning")
        count = row["releaseTotal"]
        if not integer(count):
            result.update(state="issues", mismatch={"scan": scan, "observed": count})
            raise TraceIssue("cycle_trace_release_total_invalid", f"Release total at scan {scan} needs a nonnegative integer")
        expected = initial + int(scan >= event_scan)
        if count != expected:
            result.update(state="issues", mismatch={"scan": scan, "expected": expected, "observed": count})
            raise TraceIssue("cycle_trace_release_total_mismatch", f"Release total at scan {scan} was {count}, expected {expected}")
    result.update(state="matched", before=initial, atHold=hold["releaseTotal"])


def check_return_positions(report, trace, tolerance_um, cycle):
    returned = cycle["returnLeg"]
    by_scan = {row["scan"]: row for row in trace["returnLeg"]["scans"]}
    samples = (("sourceOccupancy", returned["path"][0], by_scan[returned["occupiedScan"]]),
               ("returnArrival", returned["path"][-1], by_scan[returned["arrival"]["scan"]]),
               ("nextHold", cycle["nextHold"]["station"], trace["nextHold"]))
    check_station_positions(report, trace["inspectionLeg"], tolerance_um, samples,
                            cycle.setdefault("returnPositionReview", {}))


def check_position_time_handoff(cycle):
    outbound = cycle["outbound"]["sampledSpeedReview"]
    returned = cycle["returnLeg"]["sampledSpeedReview"]
    arrival = {"scan": outbound["lastSampleScan"], "timeMicroseconds": outbound["lastSampleTimeMicroseconds"]}
    occupancy = {"scan": returned["firstSampleScan"], "timeMicroseconds": returned["firstSampleTimeMicroseconds"]}
    elapsed = occupancy["timeMicroseconds"] - arrival["timeMicroseconds"]
    same_scan = arrival["scan"] == occupancy["scan"]
    result = cycle["positionTimeHandoff"] = {
        "state": "issues", "outboundArrival": arrival, "returnOccupancy": occupancy, "elapsedMicroseconds": elapsed,
    }
    if (same_scan and elapsed != 0) or (not same_scan and elapsed <= 0):
        required = "share one acquisition timestamp" if same_scan else "have strictly increasing acquisition timestamps"
        raise TraceIssue("cycle_trace_position_time_handoff", f"Outbound arrival and return occupancy must {required}")
    result["state"] = "matched"


def bind_cycle_build(trace, report, identity, missing, result):
    if bound_launchpad_evidence(report, identity["managerId"]).get("state") != "matched":
        raise TraceIssue("cycle_trace_build_binding",
                         "To omit an inspection build hash, supply the LaunchPad inputs pinned by the matched build")
    ruc = report["rucReview"]
    result["buildBinding"] = {
        "derivedFields": [f"inspectionLeg.{key}" for key in missing],
        "jobId": ruc["jobId"], "planSha256": ruc["planSha256"],
        **{key: identity[key] for key in BUILD_HASH_FIELDS},
    }
    return {**trace, "inspectionLeg": trace["inspectionLeg"] | {key: identity[key] for key in missing}}


def check_adjacent_scan(path):
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise TraceIssue("cycle_hmi_scan_capture_missing",
                         f"Keep {CAPTURE_COPY} beside the status response or supply scanCsv") from error
    if not S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        raise TraceIssue("cycle_hmi_scan_capture_mismatch", f"The adjacent {CAPTURE_COPY} must be a regular file")


def cycle_trace_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                       source_station, trace_path, *, hmi_status_path=None, scan_csv_path=None, require_session_capture=False,
                       expected_motion=None, position_tolerance_um=None, return_position_tolerance_um=None,
                       waypoint_tolerance_um=None, verify_sampled_speed=False, max_sample_interval_us=None,
                       verify_position_time_handoff=False, require_assembly_widget=False, hmi_layout_report_path=None,
                       captured_layout_checks=None, require_hold_through_reopen=False, release_total_event=None,
                       max_release_to_next_hold_scans=None, verify_scan_clock=False):
    report = launchpad_route_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                                    include_stations=True)
    result = report["cycleTraceReview"] = {"state": "not_checked", "sourceStation": source_station}
    if hmi_status_path is not None:
        report["cycleHmiReview"] = {"state": "not_checked"}
    if report["diagnostics"] or report["stationReview"]["state"] != "read":
        return report
    evidence_path = trace_path
    status_snapshot = None
    try:
        outbound = selected_leg(report, source_station)
        inbound = selected_leg(report, outbound[-1])
        if len(report["stationReview"]["legs"]) != 2 or inbound[-1] != source_station:
            raise TraceIssue("cycle_trace_route", "The compiled route needs two station legs that close the cycle")
        identity = leg_identity(report, manager_id, outbound)
        trace, result["trace"] = json_snapshot(trace_path, TRACE_LIMIT)
        if require_session_capture and hmi_status_path is not None and isinstance(trace, dict):
            scan, hmi = trace.get("scanCapture"), trace.get("hmi", {})
            if scan_csv_path is None and (not isinstance(scan, dict) or not isinstance(hmi, dict)):
                raise ValueError("Automatic scan selection needs a scanCapture object and an object for any supplied hmi mapping")
            if isinstance(scan, dict) and isinstance(hmi, dict):
                inspection = trace.get("inspectionLeg")
                missing = (
                    [key for key in BUILD_HASH_FIELDS if key not in inspection] if isinstance(inspection, dict) else []
                )
                if (scan_csv_path is None or missing or "sha256" not in scan
                        or not {"sessionId", "heartbeatIdentifier", "bindings"} <= hmi.keys()):
                    result["phase"] = "bind_session_scan"
                    evidence_path = hmi_status_path
                    status_snapshot = json_snapshot(hmi_status_path, TRACE_LIMIT)
                    trace, binding = bind_session_scan_capture(trace, status_snapshot[0], report["rucReview"])
                    if scan_csv_path is None:
                        evidence_path = Path(status_snapshot[1]["path"]).with_name(CAPTURE_COPY)
                        check_adjacent_scan(evidence_path)
                        scan_csv_path = evidence_path
                        binding["derivedFields"].append("scanCsv")
                    result["sessionScanBinding"] = {**binding, "statusReport": status_snapshot[1]}
                    if missing:
                        result["phase"] = "bind_build"
                        evidence_path = trace_path
                        trace = bind_cycle_build(trace, report, identity, missing, result)
        if scan_csv_path is not None:
            evidence_path = scan_csv_path
            trace = materialize_cycle(
                trace, scan_csv_path, result.setdefault("scanCapture", {}),
                read_project_events=lambda: read_project_events(project, job_directory, report["rucReview"]),
                verify_release_total=release_total_event is not None,
                verify_scan_clock=verify_scan_clock,
            )
            evidence_path = trace_path
        elif isinstance(trace, dict) and "scanCapture" in trace:
            raise ValueError("Supply scanCsv for a cycle trace with scanCapture")
        check_cycle(trace, identity, outbound, inbound, result,
                    max_release_to_next_hold_scans=max_release_to_next_hold_scans, verify_scan_clock=verify_scan_clock)
        if release_total_event is not None:
            result["phase"] = "release_total"
            evidence_path = scan_csv_path or trace_path
            check_release_total(trace, result, release_total_event)
        if expected_motion is not None:
            result["phase"] = "release_motion"
            evidence_path = scan_csv_path or trace_path
            check_release_motion(report, trace["inspectionLeg"], expected_motion, result["outbound"])
        if position_tolerance_um is not None:
            result["phase"] = "position_witnesses"
            evidence_path = scan_csv_path or trace_path
            check_leg_positions(report, trace["inspectionLeg"], position_tolerance_um, result["outbound"])
        if return_position_tolerance_um is not None:
            result["phase"] = "return_position_witnesses"
            evidence_path = scan_csv_path or trace_path
            check_return_positions(report, trace, return_position_tolerance_um, result)
        if waypoint_tolerance_um is not None:
            evidence_path = scan_csv_path or trace_path
            leg, returned = result["outbound"], result["returnLeg"]
            inspection = trace["inspectionLeg"]
            result["phase"] = "outbound_waypoints"
            check_waypoint_positions(report, inspection, inspection["scans"], outbound, leg["releaseScans"][0],
                                     leg["arrival"]["scan"], waypoint_tolerance_um, leg)
            result["phase"] = "return_waypoints"
            check_waypoint_positions(report, inspection, trace["returnLeg"]["scans"], inbound, returned["occupiedScan"],
                                     returned["arrival"]["scan"], waypoint_tolerance_um, returned)
        if verify_sampled_speed:
            evidence_path = scan_csv_path or trace_path
            leg, returned = result["outbound"], result["returnLeg"]
            inspection = trace["inspectionLeg"]
            result["phase"] = "outbound_sampled_speed"
            check_sampled_speed(report, inspection, inspection["scans"], leg["releaseScans"][0], leg["arrival"]["scan"], leg,
                                max_sample_interval_us=max_sample_interval_us)
            result["phase"] = "return_sampled_speed"
            check_sampled_speed(report, inspection, trace["returnLeg"]["scans"], returned["occupiedScan"],
                                returned["arrival"]["scan"], returned, max_sample_interval_us=max_sample_interval_us)
            if verify_position_time_handoff:
                result["phase"] = "position_time_handoff"
                check_position_time_handoff(result)
        result["phase"] = "complete"
    except (CsvError, OSError, RuntimeError, ValueError) as error:
        severity = error.severity if isinstance(error, TraceIssue) else "error"
        code = error.code if isinstance(error, TraceIssue) else "cycle_trace_invalid"
        result["state"] = "incomplete" if severity == "warning" else "issues"
        report["diagnostics"].append({"scope": "cycle_trace", "severity": severity, "code": code,
                                      "path": evidence_path.as_posix(), "message": str(error)})
    else:
        result["state"] = "matched"
        report["evidence"] = "cycle_trace_readback"
        if hmi_status_path is not None:
            layout = None
            if captured_layout_checks is not None:
                layout = partial(layout_svg_review, project, job_directory, manager_id, None, None,
                                 hmi_status_path=hmi_status_path, **captured_layout_checks)
            review_cycle_hmi(report, trace, hmi_status_path, require_session_capture=require_session_capture,
                             require_hold_through_reopen=require_hold_through_reopen,
                             require_assembly_widget=require_assembly_widget, layout_report_path=hmi_layout_report_path,
                             captured_layout_review=layout, status_snapshot=status_snapshot)
    return report

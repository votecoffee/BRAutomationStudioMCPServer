"""Review saved scan evidence for one inspection leg of a compiled Traffic Manager route."""

from .launchpad_route import json_snapshot, launchpad_route_review, nonempty
from .mptraffic_definition import ARRIVAL

TRACE_LIMIT = 16 * 1024 * 1024
MONITOR_CHECKPOINTS = ("monitorSequenceBeforeScan", "monitorSequenceAfterScan")
FLAGS = ("inspectionBusy", "routePermitted", "stationClear", "release", "releaseBuffered",
         "releaseDone", "releaseAborted", "error", "monitorHealthy")


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


class TraceIssue(ValueError):
    def __init__(self, code, message, severity="error"):
        super().__init__(message)
        self.code = code
        self.severity = severity


def trace_header(trace, identity):
    if not isinstance(trace, dict) or type(trace.get("schemaVersion")) is not int or trace["schemaVersion"] != 1:
        raise ValueError("Expected a schemaVersion 1 station trace")
    for key, expected in identity.items():
        if trace.get(key) != expected:
            raise TraceIssue("station_trace_identity", f"Trace {key} differs from the reviewed route or RUC")
    if trace.get("captureKind") not in ("synthetic", "arsim", "controller"):
        raise ValueError("Identify the trace as synthetic, arsim or controller")
    if not all(integer(trace.get(key), 1) for key in ("requestId", "shuttleId")):
        raise ValueError("The trace needs a positive request ID and shuttle ID")
    if not integer(trace.get("monitorSequenceBeforeRelease")):
        raise ValueError("The trace needs the last consumed monitor sequence from immediately before release")
    if not isinstance(trace.get("scans"), list) or not trace["scans"]:
        raise ValueError("The trace needs a nonempty scan list")


def scan_fields(row):
    if not isinstance(row, dict) or not integer(row.get("scan")):
        raise ValueError("Each sample needs a nonnegative integer scan index")
    scan = row["scan"]
    if not integer(row.get("requestId"), 1):
        raise ValueError(f"Scan {scan} needs the active request ID")
    if any(type(row.get(key)) is not bool for key in FLAGS):
        raise ValueError(f"Scan {scan} needs Boolean values for all recorded flags")
    shuttle = row.get("selectedShuttleId")
    if "selectedShuttleId" not in row or shuttle is not None and not integer(shuttle, 1):
        raise ValueError(f"Scan {scan} needs a positive selected shuttle ID or null")
    reply = row.get("inspectionResult")
    if reply is not None and (not isinstance(reply, dict)
            or not all(integer(reply.get(key), 1) for key in ("requestId", "shuttleId"))
            or not all(type(reply.get(key)) is bool for key in ("acknowledged", "approved"))):
        raise ValueError(f"Scan {scan} has a malformed inspection result")
    return shuttle, reply, scan_detections(row, scan)


def scan_detections(row, scan):
    detections = row.get("detections", [])
    if not isinstance(detections, list) or any(not isinstance(item, dict)
            or not all(integer(item.get(key), 1) for key in ("sequence", "shuttleId"))
            or not all(nonempty(item.get(key)) for key in ("station", "event")) for item in detections):
        raise ValueError(f"Scan {scan} has malformed monitor detections")
    return detections


def check_monitor_checkpoint(row, field, expected):
    if field not in row:
        return
    observed = row[field]
    if not integer(observed):
        raise ValueError(f"Scan {row['scan']} needs a nonnegative integer for {field}")
    if observed != expected:
        raise TraceIssue("station_trace_monitor_checkpoint_mismatch",
                         f"Scan {row['scan']} {field} is {observed}, expected {expected} from consumed detections",
                         "warning")


def check_detections(detections, destination, shuttle_id, scan, sequence, result, *, armed):
    for item in detections:
        event_sequence = item["sequence"]
        if event_sequence <= sequence:
            result["ignoredDetections"] += 1
            continue
        if not armed or event_sequence != sequence + 1:
            raise TraceIssue("station_trace_detection_gap",
                             f"Monitor sequence {event_sequence} at scan {scan} cannot be ordered in the leg",
                             "warning")
        sequence = event_sequence
        if (item["station"], item["shuttleId"], item["event"]) != (destination, shuttle_id, ARRIVAL):
            result["ignoredDetections"] += 1
            continue
        if result["arrival"] is not None:
            raise TraceIssue("station_trace_extra_arrival",
                             f"More than one fresh destination arrival was recorded for this leg at scan {scan}")
        result["arrival"] = {"scan": scan, **{key: item[key] for key in ("sequence", "station", "shuttleId", "event")}}
    return sequence


def check_leg(trace, result):
    sequence = trace["monitorSequenceBeforeRelease"]
    previous = None
    release_high = False
    expected = trace["requestId"], trace["shuttleId"]
    result.update(releaseScans=[], acceptedResultScan=None, bufferedScan=None, doneScan=None, arrival=None,
                  firstFault=None, busyScansBeforeRelease=0, ignoredResults=0, ignoredDetections=0)

    for row in trace["scans"]:
        shuttle, reply, detections = scan_fields(row)
        scan = row["scan"]
        if previous is None:
            result["firstScan"] = scan
            if any(row[key] for key in ("release", "releaseBuffered", "releaseDone")):
                raise TraceIssue("station_trace_initial_state",
                                 "Start with Release, ReleaseBuffered and ReleaseDone clear")
        elif scan <= previous:
            raise TraceIssue("station_trace_scan_order", f"Scan {scan} does not follow scan {previous}")
        elif scan != previous + 1:
            raise TraceIssue("station_trace_scan_gap", f"Expected scan {previous + 1}, found {scan}", "warning")
        previous = result["lastScan"] = scan

        if row["requestId"] != trace["requestId"]:
            raise TraceIssue("station_trace_request_changed", f"The active request changed at scan {scan}")
        if not result["releaseScans"]:
            if shuttle != trace["shuttleId"]:
                raise TraceIssue("station_trace_selection", f"The selected shuttle changed before release at scan {scan}")
            result["busyScansBeforeRelease"] += int(row["inspectionBusy"])
        if reply is not None:
            if (reply["requestId"], reply["shuttleId"]) == expected and reply["acknowledged"] and reply["approved"]:
                if result["acceptedResultScan"] is None:
                    result["acceptedResultScan"] = scan
            else:
                result["ignoredResults"] += 1
        if row["release"] and not release_high:
            result["releaseScans"].append(scan)
            if len(result["releaseScans"]) != 1:
                raise TraceIssue("station_trace_duplicate_release", f"Another Release edge occurred at scan {scan}")
            if (result["acceptedResultScan"] is None or row["inspectionBusy"]
                    or not row["routePermitted"] or not row["stationClear"]):
                raise TraceIssue("station_trace_unauthorized_release",
                                 f"Release at scan {scan} lacks the approved result, route permission or clearance")
        release_high = row["release"]

        if row["releaseAborted"] or row["error"]:
            if type(row.get("statusId")) is not int:
                raise ValueError(f"Scan {scan} needs the raw StatusID for Abort or Error")
            result["firstFault"] = {"scan": scan, "statusId": row["statusId"],
                                    "releaseAborted": row["releaseAborted"], "error": row["error"]}
            raise TraceIssue("station_trace_adapter_fault", f"Station Abort or Error was observed at scan {scan}")
        if not row["monitorHealthy"]:
            raise TraceIssue("station_trace_monitor_unverified", f"Monitor validity or event continuity lost at scan {scan}",
                             "warning")
        for flag, key in (("releaseBuffered", "bufferedScan"), ("releaseDone", "doneScan")):
            if row[flag]:
                if not result["releaseScans"]:
                    raise TraceIssue("station_trace_stale_response", f"{flag} was observed before release at scan {scan}")
                if result[key] is None:
                    if flag == "releaseBuffered" and result["doneScan"] is not None:
                        raise TraceIssue("station_trace_stale_response",
                                         f"First ReleaseBuffered observation follows departure at scan {scan}")
                    result[key] = scan
        before, after = MONITOR_CHECKPOINTS
        check_monitor_checkpoint(row, before, sequence)
        sequence = check_detections(detections, trace["destinationStation"], trace["shuttleId"],
                                    scan, sequence, result, armed=bool(result["releaseScans"]))
        check_monitor_checkpoint(row, after, sequence)

    missing = [label for label, observed in (
        ("Release edge", bool(result["releaseScans"])), ("ReleaseDone", result["doneScan"] is not None),
        ("destination arrival", result["arrival"] is not None)) if not observed]
    if missing:
        result["missing"] = missing
        raise TraceIssue("station_trace_incomplete", f"Capture ended without {', '.join(missing)}", "warning")
    result["arrivalObservedBeforeDone"] = result["arrival"]["scan"] < result["doneScan"]


def selected_leg(report, source_station):
    legs = [row["path"] for row in report["stationReview"]["legs"] if row["path"][0]["importedName"] == source_station]
    if len(legs) != 1 or legs[0][-1]["importedName"] == source_station:
        raise TraceIssue("station_trace_leg", f"Expected one verified leg from {source_station!r} to a different station")
    return [row["importedName"] for row in legs[0]]


def leg_identity(report, manager_id, path):
    return {"sourceStation": path[0], "destinationStation": path[-1], "managerId": manager_id,
            "launchPadSha256": report["routeReview"]["launchPad"]["sourceSha256"],
            "rucSha256": report["rucReview"]["archive"]["sha256"]}


def check_bound_leg(trace, identity, result):
    trace_header(trace, identity)
    result.update({key: trace[key] for key in ("requestId", "shuttleId", "captureKind", "monitorSequenceBeforeRelease")})
    check_leg(trace, result)


def station_trace_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                         source_station, trace_path):
    report = launchpad_route_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                                    include_stations=True)
    result = report["stationTraceReview"] = {"state": "not_checked", "sourceStation": source_station}
    if report["diagnostics"] or report["stationReview"]["state"] != "read":
        return report

    try:
        path = selected_leg(report, source_station)
        identity = leg_identity(report, manager_id, path)
        result.update(identity=identity, path=path)
        trace, result["trace"] = json_snapshot(trace_path, TRACE_LIMIT)
        check_bound_leg(trace, identity, result)
    except (OSError, RuntimeError, ValueError) as error:
        severity = error.severity if isinstance(error, TraceIssue) else "error"
        code = error.code if isinstance(error, TraceIssue) else "station_trace_invalid"
        result["state"] = "incomplete" if severity == "warning" else "issues"
        report["diagnostics"].append({"scope": "station_trace", "severity": severity, "code": code,
                                      "path": trace_path.as_posix(), "message": str(error)})
    else:
        result["state"] = "matched"
        report["evidence"] = "station_trace_readback"
    return report

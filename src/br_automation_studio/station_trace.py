"""Review saved scan evidence for one inspection leg of a compiled Traffic Manager route."""

from decimal import Decimal, DecimalException, Inexact, InvalidOperation, localcontext

from .launchpad_route import json_snapshot, launchpad_route_review, nonempty
from .mptraffic_definition import ARRIVAL

TRACE_LIMIT = 16 * 1024 * 1024
MONITOR_CHECKPOINTS = ("monitorSequenceBeforeScan", "monitorSequenceAfterScan")
RELEASE_MOTION_FIELDS = ("exitVelocityMps", "exitAccelerationMps2")
POSITION_COORDINATES = ("positionXM", "positionYM")
POSITION_IDENTIFIERS = ("positionSampleScan", "positionShuttleId")
POSITION_FIELDS = (*POSITION_IDENTIFIERS, "positionValid", *POSITION_COORDINATES)
POSITION_TIME = "positionTimeMicroseconds"
SCAN_TIME = "scanTimeMicroseconds"
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


def trace_decimal(raw):
    if not isinstance(raw, str) or not raw or raw != raw.strip() or "_" in raw:
        raise ValueError("Trace values must be finite decimal strings")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("Trace values must be finite decimal strings") from error
    if not value.is_finite():
        raise ValueError("Trace values must be finite decimal strings")
    return value


def bound_launchpad_evidence(report, manager):
    route = report["routeReview"]
    proof = route["launchPad"]
    binding = report["rucReview"].get("launchPadRoute", {})
    files = binding.get("files", {})
    if (binding.get("manager") != manager
            or files.get("project", {}).get("sha256") != proof["sourceSha256"]
            or files.get("readback", {}).get("sha256") != proof["readbackSha256"]
            or files.get("nameMap", {}).get("sha256") != route.get("nameMap", {}).get("sha256")):
        return {}
    return binding


def check_release_motion(report, trace, expected, leg):
    scan = leg["releaseScans"][0]
    result = leg["releaseMotion"] = {"state": "unverified", "scan": scan, "expected": expected}
    limits = bound_launchpad_evidence(report, trace["managerId"]).get("motionLimits", {})
    if limits.get("state") != "matched":
        raise TraceIssue("station_trace_motion_limits_unverified",
                         "Use the same LaunchPad inputs and a RUC with verified motion limits for the traced manager",
                         "warning")
    result["compiledLimits"] = limits["compiled"]
    row = next(item for item in trace["scans"] if item["scan"] == scan)
    observed = result["observed"] = {key: row.get(key) for key in RELEASE_MOTION_FIELDS}
    missing = [key for key, value in observed.items() if value in (None, "")]
    if missing:
        raise TraceIssue("station_trace_release_motion_missing",
                         f"Release scan {scan} is missing {', '.join(missing)}", "warning")
    try:
        mismatches = [key for key in RELEASE_MOTION_FIELDS
                      if trace_decimal(observed[key]) != trace_decimal(expected[key])]
    except ValueError as error:
        raise TraceIssue("station_trace_release_motion_invalid", f"Release scan {scan}: {error}") from error
    if mismatches:
        result.update(state="issues", changedFields=mismatches)
        raise TraceIssue("station_trace_release_motion_mismatch",
                         f"Release scan {scan} differs from the expected inputs for {', '.join(mismatches)}")
    result.update(state="matched", changedFields=[])


def recorded_position(scan, shuttle, values):
    if any(value in (None, "") for value in values.values()) or values["positionValid"] is False:
        raise TraceIssue("station_trace_position_missing", f"Scan {scan} needs a valid captured shuttle position", "warning")
    if (values["positionValid"] is not True or not integer(values["positionSampleScan"])
            or not integer(values["positionShuttleId"], 1)):
        raise TraceIssue("station_trace_position_invalid", f"Scan {scan} has malformed position identity or validity")
    if values["positionSampleScan"] != scan:
        raise TraceIssue("station_trace_position_stale", f"Scan {scan} has a position sample from a different scan", "warning")
    if values["positionShuttleId"] != shuttle:
        raise TraceIssue("station_trace_position_shuttle", f"Scan {scan} has a position sample for a different shuttle")
    try:
        return [trace_decimal(values[key]) for key in POSITION_COORDINATES]
    except ValueError as error:
        raise TraceIssue("station_trace_position_invalid", f"Scan {scan} needs finite decimal X and Y in metres") from error


def position_delta_micrometres(actual, reference):
    with localcontext() as context:
        context.prec = max(context.prec, *(len(value.as_tuple().digits) + 16 for value in actual + reference))
        context.traps[Inexact] = True
        return [(value - target).scaleb(6) for value, target in zip(actual, reference)]


def within_position_tolerance(delta, tolerance_um):
    return all(value.copy_abs() <= tolerance_um for value in delta)


def check_position_sample(row, shuttle, expected, tolerance_um, witness, *, age_review=None):
    scan = row["scan"]
    values = {key: row.get(key) for key in POSITION_FIELDS}
    witness["observed"] = values
    actual = recorded_position(scan, shuttle, values)
    try:
        reference = [trace_decimal(value) for value in expected]
        delta = position_delta_micrometres(actual, reference)
    except (DecimalException, ValueError) as error:
        raise TraceIssue("station_trace_position_invalid", f"Scan {scan} needs exactly comparable X and Y in metres") from error
    witness["deltaMicrometres"] = [str(value) for value in delta]
    if not within_position_tolerance(delta, tolerance_um):
        raise TraceIssue("station_trace_position_mismatch", f"Scan {scan} exceeds the position tolerance at {witness['station']!r}")
    if age_review is not None:
        witness["positionAge"] = check_position_age(scan, row.get(POSITION_TIME), row.get(SCAN_TIME), age_review)
    witness["state"] = "matched"


def verified_position_context(report, trace):
    binding = bound_launchpad_evidence(report, trace["managerId"])
    positions, assembly_io = (binding.get(name, {}) for name in ("positions", "assemblyIo"))
    if (positions.get("state") != "matched" or assembly_io.get("state") != "matched"
            or not {"X", "Y"}.issubset(assembly_io["planned"]["requiredReadAxes"])):
        raise TraceIssue("station_trace_position_context_unverified",
                         "Use the same LaunchPad inputs and a RUC with verified positions and explicit X and Y read axes",
                         "warning")
    return positions["compiled"]


def check_station_positions(report, trace, tolerance_um, samples, result, *, max_position_sample_age_us=None):
    result.update(state="unverified", toleranceMicrometres=tolerance_um, samples={})
    result.pop("positionAge", None)
    compiled = verified_position_context(report, trace)
    age_review = None
    if max_position_sample_age_us is not None:
        age_review = result["positionAge"] = new_position_age_review(max_position_sample_age_us)
    for event, station, sample in samples:
        expected = compiled[station]["xyMetres"]
        witness = result["samples"][event] = {"state": "unverified", "station": station, "scan": sample["scan"],
                                               "expectedXYMetres": expected, "configurationPath": compiled[station]["path"]}
        try:
            check_position_sample(sample, trace["shuttleId"], expected, tolerance_um, witness, age_review=age_review)
        except TraceIssue as error:
            if age_review is not None and age_review["state"] != "unverified":
                witness["state"] = result["state"] = age_review["state"]
            elif error.severity == "error":
                witness["state"] = result["state"] = "issues"
            raise
    if age_review is not None:
        age_review["state"] = "matched"
    result["state"] = "matched"


def check_leg_positions(report, trace, tolerance_um, leg, *, max_position_sample_age_us=None):
    endpoints = (("release", trace["sourceStation"], leg["releaseScans"][0]),
                 ("arrival", trace["destinationStation"], leg["arrival"]["scan"]))
    by_scan = {row["scan"]: row for row in trace["scans"]}
    samples = [(event, station, by_scan[scan]) for event, station, scan in endpoints]
    check_station_positions(report, trace, tolerance_um, samples, leg.setdefault("positionReview", {}),
                            max_position_sample_age_us=max_position_sample_age_us)


def check_waypoint_positions(report, trace, rows, path, first_scan, last_scan, tolerance_um, leg):
    expected = path[1:-1]
    result = leg["waypointReview"] = {"state": "unverified", "toleranceMicrometres": tolerance_um,
                                     "afterScan": first_scan, "beforeScan": last_scan,
                                     "expectedWaypoints": expected, "samples": [], "ambiguousSamples": 0,
                                     "unusablePositionSamples": 0}
    compiled = verified_position_context(report, trace)
    centres = {name: [trace_decimal(value) for value in row["xyMetres"]] for name, row in compiled.items()}
    pending = iter(expected)
    target = next(pending, None)
    for row in rows:
        if target is None:
            break
        scan = row["scan"]
        if not first_scan < scan < last_scan:
            continue
        values = {key: row.get(key) for key in POSITION_FIELDS}
        try:
            actual = recorded_position(scan, trace["shuttleId"], values)
        except TraceIssue as error:
            if error.code == "station_trace_position_invalid":
                result["state"] = "issues"
                raise
            result["unusablePositionSamples"] += 1
            continue
        try:
            delta = position_delta_micrometres(actual, centres[target])
            if not within_position_tolerance(delta, tolerance_um):
                continue
            if any(within_position_tolerance(position_delta_micrometres(actual, centre), tolerance_um)
                   for name, centre in centres.items() if name != target):
                result["ambiguousSamples"] += 1
                continue
        except DecimalException as error:
            result["state"] = "issues"
            raise TraceIssue("station_trace_position_invalid", f"Scan {scan} needs exactly comparable X and Y in metres") from error
        result["samples"].append({"waypoint": target, "scan": scan, "observed": values,
                                  "expectedXYMetres": compiled[target]["xyMetres"],
                                  "deltaMicrometres": [str(value) for value in delta]})
        target = next(pending, None)
    if target is not None:
        result["pendingWaypoints"] = expected[len(result["samples"]):]
        raise TraceIssue("station_trace_waypoint_incomplete",
                         f"No unambiguous, ordered position samples for {', '.join(result['pendingWaypoints'])}", "warning")
    result["state"] = "matched"


def sampled_speed_interval(before, after, elapsed_us, limit):
    delta = position_delta_micrometres(after, before)
    with localcontext() as context:
        context.prec = max(32, 2 * (max(len(value.as_tuple().digits) for value in (*delta, limit))
                                    + len(str(elapsed_us))) + 8)
        context.traps[Inexact] = True
        distance_squared = sum(value ** 2 for value in delta)
        # Micrometres per microsecond are metres per second.
        exceeds_limit = distance_squared > (limit * elapsed_us) ** 2
        context.traps[Inexact] = False
        return distance_squared.sqrt() / elapsed_us, exceeds_limit, delta


def new_position_age_review(maximum_us):
    return {"state": "unverified", "maximumMicroseconds": maximum_us, "checkedSamples": 0, "oldestSample": None}


def check_position_age(scan, position_time, scan_time, review):
    if not integer(scan_time):
        review["state"] = "incomplete"
        raise TraceIssue("station_trace_scan_time_missing", f"Capture the PLC scan time for the position at scan {scan}", "warning")
    if position_time in (None, ""):
        review["state"] = "incomplete"
        raise TraceIssue("station_trace_position_time_missing", f"Capture the X/Y acquisition time at scan {scan}", "warning")
    if not integer(position_time):
        review["state"] = "issues"
        raise TraceIssue("station_trace_position_time_invalid", f"Position time at scan {scan} needs nonnegative integer microseconds")
    age = scan_time - position_time
    sample = {"scan": scan, "scanTimeMicroseconds": scan_time, "positionTimeMicroseconds": position_time,
              "ageMicroseconds": age}
    review["checkedSamples"] += 1
    if age < 0:
        review.update(state="issues", outsideLimit=sample)
        raise TraceIssue("station_trace_position_time_after_scan",
                         f"The position at scan {scan} was recorded {abs(age)} µs after the PLC scan time")
    oldest = review["oldestSample"]
    if oldest is None or age > oldest["ageMicroseconds"]:
        review["oldestSample"] = sample
    if age > review["maximumMicroseconds"]:
        review.update(state="incomplete", outsideLimit=sample)
        raise TraceIssue("station_trace_position_age_exceeded",
                         f"The position at scan {scan} is {age} µs old, above the requested maximum of {review['maximumMicroseconds']} µs",
                         "warning")
    return sample


def check_sampled_speed(report, trace, rows, first_scan, last_scan, leg, *, max_sample_interval_us=None,
                        max_position_sample_age_us=None):
    result = leg["sampledSpeedReview"] = {"state": "unverified", "firstScan": first_scan, "lastScan": last_scan,
                                          "acceptedSamples": 0, "skippedRows": 0, "checkedIntervals": 0,
                                          "exceededIntervals": 0, "maxIntervalMicroseconds": None,
                                          "peakInterval": None, "firstExceeded": None}
    if max_sample_interval_us is not None:
        result.update(maxSampleIntervalMicroseconds=max_sample_interval_us, overlongIntervals=0, firstOverlongInterval=None)
    age_review = None
    if max_position_sample_age_us is not None:
        age_review = result["positionAge"] = new_position_age_review(max_position_sample_age_us)
    verified_position_context(report, trace)
    limits = bound_launchpad_evidence(report, trace["managerId"]).get("motionLimits", {})
    if limits.get("state") != "matched":
        raise TraceIssue("station_trace_speed_limit_unverified",
                         "Use a RUC with verified in-plane motion limits for the sampled speed check", "warning")
    limit_raw = limits["compiled"]["fields"]["InPlaneVelLim"]["raw"]
    result["velocityLimitMps"] = limit_raw
    limit = trace_decimal(limit_raw)
    previous = peak_speed = None
    for row in rows:
        scan = row["scan"]
        if not first_scan <= scan <= last_scan:
            continue
        time = row.get(POSITION_TIME)
        if time in (None, ""):
            result["skippedRows"] += 1
            continue
        try:
            actual = recorded_position(scan, trace["shuttleId"], {key: row.get(key) for key in POSITION_FIELDS})
        except TraceIssue as error:
            if error.code == "station_trace_position_invalid":
                result["state"] = "issues"
                raise
            result["skippedRows"] += 1
            continue
        if not integer(time) or (previous is not None and time <= previous[1]):
            result["state"] = "issues"
            raise TraceIssue("station_trace_speed_time_invalid",
                             f"Scan {scan} needs a nonnegative position time increasing in microseconds")
        if age_review is not None:
            try:
                check_position_age(scan, time, row.get(SCAN_TIME), age_review)
            except TraceIssue:
                result["state"] = age_review["state"]
                raise
        result["acceptedSamples"] += 1
        result.setdefault("firstSampleScan", scan)
        result.setdefault("firstSampleTimeMicroseconds", time)
        result["lastSampleScan"] = scan
        result["lastSampleTimeMicroseconds"] = time
        if previous is not None:
            elapsed = time - previous[1]
            try:
                speed, exceeded, delta = sampled_speed_interval(previous[2], actual, elapsed, limit)
            except DecimalException as error:
                result["state"] = "issues"
                raise TraceIssue("station_trace_speed_invalid", f"Cannot compare the position interval ending at scan {scan}") from error
            interval = {"fromScan": previous[0], "toScan": scan, "elapsedMicroseconds": elapsed,
                        "deltaXYMicrometres": [str(value) for value in delta], "speedMps": format(speed, ".9g")}
            result["checkedIntervals"] += 1
            result["maxIntervalMicroseconds"] = max(result["maxIntervalMicroseconds"] or 0, elapsed)
            if max_sample_interval_us is not None and elapsed > max_sample_interval_us:
                result["overlongIntervals"] += 1
                if result["firstOverlongInterval"] is None:
                    result["firstOverlongInterval"] = interval
            if peak_speed is None or speed > peak_speed:
                peak_speed = speed
                result["peakInterval"] = interval
            if exceeded:
                result["exceededIntervals"] += 1
                if result["firstExceeded"] is None:
                    result["firstExceeded"] = interval
        previous = scan, time, actual
    if result["exceededIntervals"]:
        result["state"] = "issues"
        raise TraceIssue("station_trace_speed_exceeds_limit",
                         f"{result['exceededIntervals']} sampled speed interval(s) exceed the compiled {limit_raw} m/s limit")
    if (not result["checkedIntervals"] or result.get("firstSampleScan") != first_scan
            or result.get("lastSampleScan") != last_scan):
        raise TraceIssue("station_trace_speed_incomplete",
                         "Capture timed same-shuttle positions at both ends of the sampled interval", "warning")
    if gap := result.get("firstOverlongInterval"):
        result["state"] = "incomplete"
        raise TraceIssue("station_trace_speed_interval_too_long",
                         f"Scans {gap['fromScan']} to {gap['toScan']} are {gap['elapsedMicroseconds']} µs apart, "
                         f"above the requested maximum of {max_sample_interval_us} µs", "warning")
    if age_review is not None:
        age_review["state"] = "matched"
    result["state"] = "matched"


def station_trace_review(project, configuration, cpu, manager_id, lpj, readback, name_map, job_directory,
                         source_station, trace_path, *, expected_motion=None, position_tolerance_um=None,
                         waypoint_tolerance_um=None, verify_sampled_speed=False, max_sample_interval_us=None):
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
        if expected_motion is not None:
            check_release_motion(report, trace, expected_motion, result)
        if position_tolerance_um is not None:
            check_leg_positions(report, trace, position_tolerance_um, result)
        if waypoint_tolerance_um is not None:
            check_waypoint_positions(report, trace, trace["scans"], path, result["releaseScans"][0], result["arrival"]["scan"],
                                     waypoint_tolerance_um, result)
        if verify_sampled_speed:
            check_sampled_speed(report, trace, trace["scans"], result["releaseScans"][0], result["arrival"]["scan"], result,
                                max_sample_interval_us=max_sample_interval_us)
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

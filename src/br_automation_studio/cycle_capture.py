"""Read a column-mapped scan capture for the cycle trace reviewer."""

import csv
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import re

from .mptraffic_definition import NONE
from .station_trace import (
    FLAGS, MONITOR_CHECKPOINTS, POSITION_FIELDS, POSITION_IDENTIFIERS, RELEASE_MOTION_FIELDS, TRACE_LIMIT, TraceIssue, integer,
)


HOLD_HMI = ("hold.phase", "hold.releaseTotal", "hold.heartbeat")
INSPECTION_MOTION = tuple(f"inspection.{field}" for field in RELEASE_MOTION_FIELDS)
INSPECTION_POSITION = tuple(f"inspection.{field}" for field in POSITION_FIELDS)
RESULT = ("requestId", "shuttleId", "acknowledged", "approved")
DETECTION = ("sequence", "station", "shuttleId", "event")
FIELDS = {
    "scan",
    "inspection.requestId", "inspection.selectedShuttleId", "inspection.statusId", "inspection.resultPresent",
    *(f"inspection.{flag}" for flag in FLAGS),
    *(f"inspection.result.{key}" for key in RESULT),
    "return.sourceHealthy", "return.occupyingShuttleId", "return.monitorHealthy", "return.error", "return.statusId",
    *(f"{leg}.detection.{key}" for leg in ("inspection", "return") for key in DETECTION),
    "hold.station", "hold.requestId", "hold.shuttleId", "hold.held", "hold.release", "hold.resultAccepted",
}
DECIMAL_INTEGER = re.compile(r"[+-]?[0-9]{1,20}(?:\.0{1,32})?", re.ASCII)
CHART_TIME = re.compile(r"[+-]?[0-9]{1,20}(?:\.[0-9]{1,32})?", re.ASCII)
CHART_HEADER = re.compile(
    r'% TYPE=CHART-DATA-ASCII V=2\.0 TITLE="([^"\x00]+)" XUNIT="[^"]*" YUNIT="[^"]*" '
    r'FORMULA="[^"]*" ROWS=([0-9]{1,9}) COLS=2 COLSEP="COMMA" *,'
)


def chart_table(text):
    """Yield the stored AS chart values after checking signal alignment."""
    stream = StringIO(text.removesuffix("\x00"), newline="")
    titles, counts = [], set()
    while True:
        offset = stream.tell()
        line = stream.readline()
        if not line.startswith("%"):
            stream.seek(offset)
            break
        match = CHART_HEADER.fullmatch(line.rstrip("\r\n"))
        if match is None:
            raise ValueError("Expected AS CHART-DATA-ASCII V2.0 comma-separated timestamp/value metadata")
        titles.append(match[1])
        counts.add(int(match[2]))
    if not titles or len(counts) != 1 or 0 in counts:
        raise ValueError("AS chart signals need a single positive declared sample count")
    yield titles
    expected = counts.pop()
    previous = None
    count = 0
    for record, row in enumerate(csv.reader(stream, strict=True), 1):
        if len(row) != 2 * len(titles) + 1 or row[-1] != "":
            raise ValueError(f"AS chart sample {record} needs one timestamp/value pair per signal and a trailing comma")
        timestamps = row[:-1:2]
        timestamp = timestamps[0]
        if any(value != timestamp for value in timestamps) or not CHART_TIME.fullmatch(timestamp):
            raise TraceIssue("cycle_capture_time_alignment", f"AS chart signal timestamps differ or are invalid at sample {record}")
        moment = Decimal(timestamp)
        if previous is not None and moment <= previous:
            raise TraceIssue("cycle_capture_time_alignment", f"AS chart timestamp does not increase at sample {record}")
        previous = moment
        count += 1
        yield row[1:-1:2]
    if count != expected:
        raise TraceIssue("cycle_capture_count_mismatch", f"AS chart declares {expected} samples and contains {count}")


def interval(capture, key):
    scans = capture.get(key)
    if (not isinstance(scans, list) or len(scans) != 2 or not all(integer(scan) for scan in scans)
            or scans[0] > scans[1]):
        raise ValueError(f"scanCapture.{key} needs inclusive first and last PLC scan indexes")
    return scans


def string_map(value, label):
    if not isinstance(value, dict) or not all(key and isinstance(item, str) and item.strip()
                                             for key, item in value.items()):
        raise ValueError(f"scanCapture.{label} needs a mapping of nonempty strings")
    return value


class Sample:
    def __init__(self, header, cells, columns, record, project_events):
        self.cells = dict(zip(header, cells))
        self.columns = columns
        self.record = record
        self.project_events = project_events

    def get(self, role):
        return self.cells[self.columns.get(role, role)]

    def number(self, role):
        raw = self.get(role)
        if not DECIMAL_INTEGER.fullmatch(raw):
            raise ValueError(f"Scan sample {self.record} needs a decimal integer for {role}")
        return int(raw.partition(".")[0])

    def flag(self, role):
        raw = self.get(role).lower()
        if raw in ("true", "false"):
            return raw == "true"
        if DECIMAL_INTEGER.fullmatch(raw):
            value = int(raw.partition(".")[0])
            if value in (0, 1):
                return bool(value)
        raise ValueError(f"Scan sample {self.record} needs a Boolean for {role}")

    def label(self, role, names):
        raw = self.get(role)
        if names is not None and raw not in names:
            raise ValueError(f"Scan sample {self.record} has no label mapping for {role} value {raw!r}")
        translated = raw if names is None else names[raw]
        if not translated.strip() or DECIMAL_INTEGER.fullmatch(translated):
            raise ValueError(f"Scan sample {self.record} needs a named value for {role}")
        return translated

    def event(self, role, names):
        if self.project_events is None:
            return self.label(role, names)
        raw = self.get(role)
        key = int(raw.partition(".")[0]) if DECIMAL_INTEGER.fullmatch(raw) else raw.casefold()
        event = self.project_events.get(key)
        if event is None or event == NONE:
            raise TraceIssue("cycle_capture_event_code_invalid",
                             f"Scan sample {self.record} {role} has no consumed MpTraffic detection for {raw!r}")
        return event

    def detections(self, leg, slots, stations, events):
        observed = []
        for slot in range(1, slots + 1):
            prefix = f"{leg}.detection{slot if slot > 1 else ''}"
            sequence = self.number(f"{prefix}.sequence")
            if sequence != 0:
                observed.append({"sequence": sequence, "station": self.label(f"{prefix}.station", stations),
                                 "shuttleId": self.number(f"{prefix}.shuttleId"),
                                 "event": self.event(f"{prefix}.event", events)})
        return observed

    def inspection(self, scan, slots, stations, events):
        row = {"scan": scan, "requestId": self.number("inspection.requestId"),
               "selectedShuttleId": self.number("inspection.selectedShuttleId") or None,
               "detections": self.detections("inspection", slots, stations, events)}
        row.update({flag: self.flag(f"inspection.{flag}") for flag in FLAGS})
        if row["error"] or row["releaseAborted"]:
            row["statusId"] = self.number("inspection.statusId")
        if self.flag("inspection.resultPresent"):
            row["inspectionResult"] = {
                "requestId": self.number("inspection.result.requestId"),
                "shuttleId": self.number("inspection.result.shuttleId"),
                "acknowledged": self.flag("inspection.result.acknowledged"),
                "approved": self.flag("inspection.result.approved"),
            }
        return row

    def returned(self, scan, slots, stations, events):
        row = {"scan": scan, "occupyingShuttleId": self.number("return.occupyingShuttleId") or None,
               "detections": self.detections("return", slots, stations, events)}
        row.update({flag: self.flag(f"return.{flag}") for flag in ("sourceHealthy", "monitorHealthy", "error")})
        if row["error"]:
            row["statusId"] = self.number("return.statusId")
        return row

    def position(self, roles):
        values = {role.partition(".")[2]: self.get(role) for role in roles}
        for name in POSITION_IDENTIFIERS:
            if values.get(name) not in (None, ""):
                values[name] = self.number(f"inspection.{name}")
        if values.get("positionValid") not in (None, ""):
            values["positionValid"] = self.flag("inspection.positionValid")
        return values

    def hold(self, scan, stations, include_hmi):
        row = {"scan": scan, "station": self.label("hold.station", stations),
               "requestId": self.number("hold.requestId"), "shuttleId": self.number("hold.shuttleId")}
        row.update({flag: self.flag(f"hold.{flag}") for flag in ("held", "release", "resultAccepted")})
        if include_hmi:
            row.update({role.partition(".")[2]: self.number(role) for role in HOLD_HMI})
        return row


def anchored_monitor_window(leg, rows, field):
    before, after = MONITOR_CHECKPOINTS
    start, end = rows[0][before], rows[-1][after]
    if field in leg and (not integer(leg[field]) or leg[field] != start):
        raise TraceIssue("cycle_capture_monitor_baseline_mismatch",
                         f"{field} differs from the captured baseline at PLC scan {rows[0]['scan']}")
    return {**leg, field: start, "scans": rows}, {"start": start, "end": end}


def materialize_cycle(trace, path, summary, *, read_project_events=None):
    capture = trace.get("scanCapture") if isinstance(trace, dict) else None
    if not isinstance(capture, dict):
        raise ValueError("A CSV input needs a scanCapture object in the cycle trace")
    leg, returned = trace.get("inspectionLeg"), trace.get("returnLeg")
    if (not isinstance(leg, dict) or not isinstance(returned, dict)
            or "scans" in leg or "scans" in returned or "nextHold" in trace):
        raise ValueError("Select CSV rows or JSON scan arrays and next hold, using only one source")
    start, end = interval(capture, "inspectionScans")
    return_start, return_end = interval(capture, "returnScans")
    hold_scan = capture.get("nextHoldScan")
    if not integer(hold_scan):
        raise ValueError("scanCapture.nextHoldScan needs the observed PLC scan index")
    format_name = capture.get("format", "csv")
    delimiter = capture.get("delimiter", ",")
    if format_name not in ("csv", "as-chart"):
        raise ValueError("scanCapture.format must be csv or as-chart")
    if delimiter not in (",", ";", "\t") or (format_name == "as-chart" and delimiter != ","):
        raise ValueError("Use comma, semicolon or tab for CSV and comma for AS charts")
    columns = string_map(capture.get("columns", {}), "columns")
    stations, events = (string_map(capture[key], key) if key in capture else None
                        for key in ("stationNames", "eventNames"))
    with path.open("rb") as stream:
        raw = stream.read(TRACE_LIMIT + 1)
    if len(raw) > TRACE_LIMIT:
        raise ValueError(f"CSV input exceeds {TRACE_LIMIT} bytes")
    digest = sha256(raw).hexdigest()
    summary.update(path=str(path.resolve()), bytes=len(raw), sha256=digest, format=format_name, delimiter=delimiter)
    if capture.get("sha256") != digest:
        raise TraceIssue("cycle_capture_hash_mismatch", "The CSV hash differs from its cycle trace descriptor")
    project_events = None
    encoding = capture.get("eventEncoding", "capture")
    if encoding == "project-mptraffic":
        if events is not None:
            raise ValueError("Use either project-mptraffic eventEncoding or eventNames")
        if read_project_events is None:
            raise ValueError("Project event encoding requires the verified saved build")
        codes, summary["eventEncoding"] = read_project_events()
        project_events = {**codes, **{label.casefold(): label for label in codes.values()}}
    elif encoding != "capture":
        raise ValueError("scanCapture.eventEncoding must be project-mptraffic or capture")
    decoded = raw.decode("utf-8-sig")
    reader = (chart_table(decoded) if format_name == "as-chart"
              else csv.reader(StringIO(decoded, newline=""), delimiter=delimiter, strict=True))
    header = next(reader, [])
    slots = capture.get("detectionSlots", {})
    if (not isinstance(slots, dict) or set(slots) - {"inspection", "return"}
            or not all(integer(value, 1) and value <= len(header) // len(DETECTION) for value in slots.values())):
        raise ValueError("scanCapture.detectionSlots needs positive counts for inspection or return supported by the capture")
    required = FIELDS | {f"{leg}.detection{slot}.{key}" for leg, count in slots.items()
                         for slot in range(2, count + 1) for key in DETECTION}
    checkpoints = capture.get("monitorCheckpoints", False)
    if type(checkpoints) is not bool:
        raise ValueError("scanCapture.monitorCheckpoints must be a Boolean")
    if checkpoints:
        required.update(f"{phase}.{field}" for phase in ("inspection", "return") for field in MONITOR_CHECKPOINTS)
    if set(columns) - (required | set(HOLD_HMI) | set(INSPECTION_MOTION) | set(INSPECTION_POSITION)):
        raise ValueError("scanCapture.columns refers to an unknown scan field")
    motion_columns = {role: columns.get(role, role) for role in INSPECTION_MOTION if role in columns or role in header}
    position_columns = {role: columns.get(role, role) for role in INSPECTION_POSITION if role in columns or role in header}
    required.update(motion_columns)
    required.update(position_columns)
    fields = {columns.get(role, role) for role in required}
    hmi_fields = {columns.get(role, role) for role in HOLD_HMI}
    include_hmi = bool(hmi_fields.intersection(header))
    wanted = fields | (hmi_fields if include_hmi else set())
    if (len(fields) != len(required) or (include_hmi and len(wanted) != len(required) + len(HOLD_HMI))):
        raise ValueError("Each scan field needs a distinct CSV column")
    if not header or not all(column.strip() for column in header) or len(set(header)) != len(header):
        raise ValueError("The CSV header needs distinct nonempty column names")
    if missing := wanted.difference(header):
        raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
    if motion_columns:
        summary["releaseMotionColumns"] = motion_columns
    if position_columns:
        summary["positionColumns"] = position_columns

    outbound_rows, return_rows, hold = [], [], None
    boundaries = {start, end, return_start, return_end, hold_scan}
    previous, records = -1, 0
    for record, cells in enumerate(reader, 1):
        if len(cells) != len(header):
            raise ValueError(f"Scan sample {record} does not match the header width")
        row = Sample(header, cells, columns, record, project_events)
        scan = row.number("scan")
        if scan <= previous:
            raise TraceIssue("cycle_capture_scan_order", f"PLC scan {scan} at sample {record} does not follow {previous}")
        previous = scan
        records += 1
        boundaries.discard(scan)
        if start <= scan <= end:
            outbound = row.inspection(scan, slots.get("inspection", 1), stations, events)
            outbound.update({role.partition(".")[2]: row.get(role) for role in motion_columns})
            outbound.update(row.position(position_columns))
            if checkpoints:
                outbound.update({field: row.number(f"inspection.{field}") for field in MONITOR_CHECKPOINTS})
            outbound_rows.append(outbound)
        if return_start <= scan <= return_end:
            inbound = row.returned(scan, slots.get("return", 1), stations, events)
            if checkpoints:
                inbound.update({field: row.number(f"return.{field}") for field in MONITOR_CHECKPOINTS})
            return_rows.append(inbound)
        if scan == hold_scan:
            hold = row.hold(scan, stations, include_hmi)
    summary.update(records=records, inspectionScans=[start, end], returnScans=[return_start, return_end],
                   nextHoldScan=hold_scan)
    if boundaries:
        missing_scans = ", ".join(str(scan) for scan in sorted(boundaries))
        raise TraceIssue("cycle_capture_boundary_missing", f"CSV is missing selected scan indexes {missing_scans}", "warning")
    leg, returned = {**leg, "scans": outbound_rows}, {**returned, "scans": return_rows}
    if checkpoints:
        leg, inspection_sequence = anchored_monitor_window(leg, outbound_rows, "monitorSequenceBeforeRelease")
        returned, return_sequence = anchored_monitor_window(returned, return_rows, "monitorSequenceAtStart")
        summary["monitorSequences"] = {"inspection": inspection_sequence, "return": return_sequence}
    return {**trace, "inspectionLeg": leg, "returnLeg": returned, "nextHold": hold}

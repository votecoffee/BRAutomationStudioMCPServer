"""Check captured inputs, outputs and application consumption for two station monitors."""

from .station_trace import MONITOR_CHECKPOINTS, TraceIssue, integer

QUEUE_FLAGS = ("monitorActive", "monitorError", "monitorGetNextShuttle")
QUEUE_FIELDS = (*QUEUE_FLAGS, "monitorRemainingShuttleCount", "monitorStatusId")
MAXIMUM_REMAINING = "maximumRemainingShuttleCount"
UINT_MAX = 65535


def validate_queue_request(queue, checkpoints):
    if (not isinstance(queue, dict) or set(queue) != {MAXIMUM_REMAINING}
            or not integer(queue[MAXIMUM_REMAINING]) or queue[MAXIMUM_REMAINING] > UINT_MAX):
        raise ValueError(f"scanCapture.monitorQueue needs {MAXIMUM_REMAINING} as an integer from 0 to {UINT_MAX}")
    if not checkpoints:
        raise ValueError("scanCapture.monitorQueue requires monitorCheckpoints: true")


def review_monitor_queues(trace, result):
    limit = trace["scanCapture"]["monitorQueue"][MAXIMUM_REMAINING]
    report = result["monitorQueueReview"] = {"state": "incomplete", MAXIMUM_REMAINING: limit, "legs": {}}
    for label, key in (("inspection", "inspectionLeg"), ("return", "returnLeg")):
        totals = report["legs"][label] = {"checkedScans": 0, "risingEdges": 0, "consumed": 0,
                                         "peakRemainingShuttleCount": 0}
        previous_level = None
        for row in trace[key]["scans"]:
            sample = row["monitorQueue"]
            count = sample["monitorRemainingShuttleCount"]
            if not 0 <= count <= UINT_MAX:
                raise ValueError(f"{label.title()} monitor remaining count at scan {row['scan']} is outside UINT")
            level = sample["monitorGetNextShuttle"]
            rising = previous_level is False and level
            before, after = MONITOR_CHECKPOINTS
            consumed = row[after] - row[before]
            totals["peakRemainingShuttleCount"] = max(totals["peakRemainingShuttleCount"], count)
            failure = queue_issue(sample, limit, rising, consumed)
            if failure is not None:
                code, message = failure
                report["firstFailure"] = {"leg": label, "scan": row["scan"], "statusId": sample["monitorStatusId"],
                                          "remainingShuttleCount": count, "getNextShuttle": level,
                                          "previousGetNextShuttle": previous_level, "consumed": consumed}
                raise TraceIssue(code, f"{label.title()} monitor at scan {row['scan']} {message}", "warning")
            totals["checkedScans"] += 1
            totals["risingEdges"] += int(rising)
            totals["consumed"] += consumed
            previous_level = level
    report["state"] = "matched"


def queue_issue(sample, limit, rising, consumed):
    if not sample["monitorActive"] or sample["monitorError"]:
        return "cycle_monitor_queue_unhealthy", "did not provide active, error-free outputs"
    if sample["monitorRemainingShuttleCount"] > limit:
        return "cycle_monitor_queue_depth", f"reported a remaining count above the selected limit of {limit}"
    if consumed > 1 or (consumed == 1 and not rising):
        message = f"recorded {consumed} consumed events with {int(rising)} witnessed GetNextShuttle rising edges"
        return "cycle_monitor_queue_read_edge", message
    return None

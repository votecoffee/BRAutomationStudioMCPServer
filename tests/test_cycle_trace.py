from copy import deepcopy
import csv
from hashlib import sha256
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from br_automation_studio.cycle_capture import FIELDS, HOLD_HMI, materialize_cycle
from br_automation_studio.cycle_trace import check_cycle
from br_automation_studio.station_trace import ARRIVAL, FLAGS, MONITOR_CHECKPOINTS, TraceIssue


class CycleTraceTest(unittest.TestCase):
    def setUp(self):
        self.outbound = ["Inspect", "Bridge", "Outfeed"]
        self.inbound = ["Outfeed", "North", "South", "Inspect"]
        self.identity = {"sourceStation": "Inspect", "destinationStation": "Outfeed", "managerId": "Traffic",
                         "launchPadSha256": "a" * 64, "rucSha256": "b" * 64}
        request = {"requestId": 12, "shuttleId": 3}
        before = [
            self.inspection_scan(10, inspectionBusy=True),
            self.inspection_scan(11, inspectionResult=dict(request, acknowledged=True, approved=True)),
            self.inspection_scan(12, release=True),
            self.inspection_scan(13, release=True, detections=[self.arrival(21, "Outfeed")]),
            self.inspection_scan(14, release=True, releaseDone=True, selectedShuttleId=None),
            self.inspection_scan(15, selectedShuttleId=None),
        ]
        returned = [
            self.return_scan(15, 3),
            self.return_scan(16, None),
            self.return_scan(17, None, detections=[self.arrival(71, "Inspect")]),
        ]
        self.trace = {"schemaVersion": 1,
                      "inspectionLeg": {"schemaVersion": 1, "captureKind": "synthetic", **self.identity, **request,
                                        "monitorSequenceBeforeRelease": 20, "scans": before},
                      "returnLeg": {"sourceStation": "Outfeed", "destinationStation": "Inspect",
                                    "monitorSequenceAtStart": 70, "scans": returned},
                      "nextHold": {"scan": 18, "station": "Inspect", "requestId": 13, "shuttleId": 3,
                                   "held": True, "release": False, "resultAccepted": False}}

    def inspection_scan(self, scan, **changes):
        flags = dict.fromkeys(FLAGS, False)
        flags.update(routePermitted=True, stationClear=True, monitorHealthy=True)
        return {**flags, "scan": scan, "requestId": 12, "selectedShuttleId": 3, **changes}

    def return_scan(self, scan, shuttle, **changes):
        return {"scan": scan, "occupyingShuttleId": shuttle, "sourceHealthy": True, "monitorHealthy": True, **changes}

    def arrival(self, sequence, station):
        return {"sequence": sequence, "station": station, "shuttleId": 3, "event": ARRIVAL}

    def run_cycle(self, trace):
        result = {}
        check_cycle(trace, self.identity, self.outbound, self.inbound, result)
        return result

    def test_both_observation_orders_can_establish_return(self):
        first = self.run_cycle(self.trace)
        self.assertEqual(first["phase"], "complete")
        self.assertEqual(first["outbound"]["releaseScans"], [12])
        self.assertEqual(first["returnLeg"]["path"], self.inbound)
        self.assertEqual(first["returnLeg"]["sourceClearScan"], 16)
        self.assertEqual(first["returnLeg"]["arrival"]["scan"], 17)
        self.assertFalse(first["returnLeg"]["arrivalObservedBeforeSourceClear"])
        self.assertEqual(first["nextHold"]["requestId"], 13)

        self.trace["returnLeg"]["scans"][1:3] = [
            self.return_scan(16, 3, detections=[self.arrival(71, "Inspect")]),
            self.return_scan(17, None),
        ]
        second = self.run_cycle(self.trace)
        self.assertTrue(second["returnLeg"]["arrivalObservedBeforeSourceClear"])

    def test_return_needs_correct_station_shuttle_and_continuity(self):
        for row, changes, code, severity in (
            (0, {"scan": 12}, "cycle_trace_return_order", "error"),
            (0, {"scan": 18}, "cycle_trace_gap", "warning"),
            (0, {"occupyingShuttleId": None}, "cycle_trace_initial_occupancy", "warning"),
            (1, {"occupyingShuttleId": 9}, "cycle_trace_return_shuttle", "error"),
            (1, {"sourceHealthy": False}, "cycle_trace_monitor_unverified", "warning"),
            (2, {"detections": [self.arrival(70, "Inspect")]}, "cycle_trace_return_incomplete", "warning"),
            (2, {"detections": [dict(self.arrival(71, "Inspect"), shuttleId=7)]},
             "cycle_trace_return_incomplete", "warning"),
            (2, {"detections": [self.arrival(71, "Elsewhere")]}, "cycle_trace_return_incomplete", "warning"),
        ):
            with self.subTest(row=row, changes=changes):
                trace = deepcopy(self.trace)
                trace["returnLeg"]["scans"][row].update(changes)
                with self.assertRaises(TraceIssue) as caught:
                    self.run_cycle(trace)
                self.assertEqual((caught.exception.code, caught.exception.severity), (code, severity))

    def test_next_hold_is_a_new_pending_request_covered_by_the_capture(self):
        for changes, code, severity in (
            ({"scan": 16}, "cycle_trace_hold_order", "error"),
            ({"scan": 19}, "cycle_trace_gap", "warning"),
            ({"requestId": 12}, "cycle_trace_hold_identity", "error"),
            ({"shuttleId": 7}, "cycle_trace_hold_identity", "error"),
            ({"release": True}, "cycle_trace_hold_state", "error"),
            ({"resultAccepted": True}, "cycle_trace_hold_state", "error"),
        ):
            with self.subTest(changes=changes):
                trace = deepcopy(self.trace)
                trace["nextHold"].update(changes)
                with self.assertRaises(TraceIssue) as caught:
                    self.run_cycle(trace)
                self.assertEqual((caught.exception.code, caught.exception.severity), (code, severity))
        del self.trace["nextHold"]
        with self.assertRaises(TraceIssue) as caught:
            self.run_cycle(self.trace)
        self.assertEqual(caught.exception.code, "cycle_trace_hold_missing")

    def test_return_fault_takes_priority_over_state_and_arrival(self):
        self.trace["returnLeg"]["scans"][2].update(error=True, statusId=-30007, monitorHealthy=False)
        result = {}
        with self.assertRaises(TraceIssue) as caught:
            check_cycle(self.trace, self.identity, self.outbound, self.inbound, result)
        self.assertEqual(caught.exception.code, "cycle_trace_return_fault")
        self.assertEqual(result["returnLeg"]["firstFault"], {"scan": 17, "statusId": -30007})
        self.assertIsNone(result["returnLeg"]["arrival"])


    def scan_csv(self):
        trace = deepcopy(self.trace)
        columns = sorted(FIELDS | set(HOLD_HMI))
        scans = {scan: dict.fromkeys(columns, "") | {"scan": scan} for scan in range(10, 19)}
        for leg, name in (("inspectionLeg", "inspection"), ("returnLeg", "return")):
            for row in trace[leg].pop("scans"):
                cells = scans[row["scan"]]
                detection, = row.get("detections") or [{"sequence": 0}]
                cells.update({f"{name}.detection.{key}": value for key, value in detection.items()})
                cells.update({f"{name}.{key}": 0 if value is None else value for key, value in row.items()
                              if key not in ("scan", "detections", "inspectionResult")})
                if name == "inspection":
                    reply = row.get("inspectionResult")
                    cells["inspection.resultPresent"] = bool(reply)
                    cells.update({f"inspection.result.{key}": value for key, value in (reply or {}).items()})
                else:
                    cells["return.error"] = row.get("error", False)
        hold = trace.pop("nextHold")
        scans[18].update({f"hold.{key}": value for key, value in hold.items() if key != "scan"},
                         **{"hold.phase": 10, "hold.releaseTotal": 1, "hold.heartbeat": 23})
        trace["scanCapture"] = {"inspectionScans": [10, 15], "returnScans": [15, 17], "nextHoldScan": 18}
        return trace, columns, list(scans.values())

    def materialize(self, trace, contents, update_digest=True, *, event_source=None):
        with TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH")) as directory:
            path = Path(directory) / "capture.csv"
            path.write_bytes(contents)
            if update_digest:
                trace["scanCapture"]["sha256"] = sha256(contents).hexdigest()
            capture = {}
            result = materialize_cycle(trace, path, capture, read_project_events=event_source)
            self.assertEqual(capture["sha256"], sha256(contents).hexdigest())
            return result, capture

    def test_column_mapped_csv_matches_json_cycle_and_keeps_the_hold_samples(self):
        trace, columns, rows = self.scan_csv()
        csv_text = StringIO(newline="")
        headers = ["Task Scan" if column == "scan" else column for column in columns]
        writer = csv.writer(csv_text, delimiter=";", lineterminator="\r\n")
        writer.writerow(headers)
        writer.writerows([row[key] for key in columns] for row in rows)
        trace["scanCapture"].update(delimiter=";", columns={"scan": "Task Scan"})
        csv_bytes = csv_text.getvalue().encode("utf-8-sig")
        normalized, capture = self.materialize(trace, csv_bytes)
        self.assertEqual(capture["records"], 9)
        self.assertEqual(self.run_cycle(normalized), self.run_cycle(self.trace))
        self.assertEqual(normalized["nextHold"]["heartbeat"], 23)
        with self.assertRaises(TraceIssue) as error:
            self.materialize(trace, csv_bytes + b"\r\n", update_digest=False)
        self.assertEqual(error.exception.code, "cycle_capture_hash_mismatch")

    def test_capture_scan_order_boundaries_and_intervening_gaps(self):
        for replaced, removed, expected, severity in (
            (11, 12, "cycle_capture_scan_order", "error"),
            (None, 18, "cycle_capture_boundary_missing", "warning"),
            (None, 14, "station_trace_scan_gap", "warning"),
        ):
            with self.subTest(removed=removed, replaced=replaced):
                trace, columns, rows = self.scan_csv()
                if replaced is not None:
                    rows[removed - 10]["scan"] = replaced
                else:
                    del rows[removed - 10]
                output = StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
                with self.assertRaises(TraceIssue) as error:
                    normalized, _ = self.materialize(trace, output.getvalue().encode())
                    self.run_cycle(normalized)
                self.assertEqual((error.exception.code, error.exception.severity), (expected, severity))

    def test_as_chart_needs_aligned_signals_and_preserves_the_sample_count(self):
        trace, columns, rows = self.scan_csv()
        for key in ("sequence", "station", "shuttleId", "event"):
            columns.append(f"inspection.detection2.{key}")
        for row in rows:
            row.update({f"inspection.detection2.{key}": "" for key in ("station", "shuttleId", "event")},
                       **{"inspection.detection2.sequence": 0})
        prior = self.arrival(20, "Outfeed")
        self.trace["inspectionLeg"]["scans"][3]["detections"].insert(0, prior)
        for key, value in prior.items():
            rows[3][f"inspection.detection2.{key}"] = rows[3][f"inspection.detection.{key}"]
            rows[3][f"inspection.detection.{key}"] = value
        trace["scanCapture"]["detectionSlots"] = {"inspection": 2}
        station_ids = {"Inspect": "1.0000", "Outfeed": "2.0000"}
        event_ids = {ARRIVAL: "3.0000"}
        trace["scanCapture"].update(format="as-chart", stationNames={v: k for k, v in station_ids.items()},
                                    eventNames={v: k for k, v in event_ids.items()})
        meta = ('% TYPE=CHART-DATA-ASCII V=2.0 TITLE="{}" XUNIT="UnitX" YUNIT="UnitY" '
                'FORMULA="DO_NOT_CHANGE_ORIGINAL_DATA" ROWS=9 COLS=2 COLSEP="COMMA" ,\r\n')
        text = "".join(meta.format(name) for name in columns)
        end_of_header = len(text)
        for index, row in enumerate(rows, 1):
            for name in columns:
                raw = row[name]
                if isinstance(raw, int):
                    raw = f"{int(raw)}.0000"
                raw = station_ids.get(raw, event_ids.get(raw, raw))
                text += f"{index}.0100,{raw},"
            text += "\r\n"
        text += "\x00"
        normalized, capture = self.materialize(trace, text.encode())
        self.assertEqual(capture["format"], "as-chart")
        self.assertEqual(self.run_cycle(normalized), self.run_cycle(self.trace))
        self.assertEqual(normalized["nextHold"]["releaseTotal"], 1)
        for malformed, code in (
            (text[:end_of_header] + text[end_of_header:].replace("1.0100", "1.0101", 1),
             "cycle_capture_time_alignment"),
            (text.replace("ROWS=9", "ROWS=10"), "cycle_capture_count_mismatch"),
        ):
            with self.subTest(code=code), self.assertRaises(TraceIssue) as error:
                self.materialize(trace, malformed.encode())
            self.assertEqual(error.exception.code, code)


    def checkpoint_csv(self):
        trace, columns, rows = self.scan_csv()
        before, after = MONITOR_CHECKPOINTS
        columns.extend(f"{phase}.{field}" for phase in ("inspection", "return") for field in MONITOR_CHECKPOINTS)
        for phase, observed, last in (("inspection", range(10, 16), 20), ("return", range(15, 18), 70)):
            for scan in observed:
                row = rows[scan - 10]
                row[f"{phase}.{before}"] = last
                last = max(last, row.get(f"{phase}.detection.sequence", 0))
                row[f"{phase}.{after}"] = last
        trace["scanCapture"]["monitorCheckpoints"] = True
        return trace, columns, rows

    def test_monitor_checkpoints_supply_missing_baselines_from_raw_rows(self):
        trace, columns, rows = self.checkpoint_csv()
        del trace["inspectionLeg"]["monitorSequenceBeforeRelease"]
        del trace["returnLeg"]["monitorSequenceAtStart"]
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        normalized, capture = self.materialize(trace, output.getvalue().encode())
        self.assertEqual(self.run_cycle(normalized), self.run_cycle(self.trace))
        self.assertEqual(capture["monitorSequences"], {"inspection": {"start": 20, "end": 21},
                                                       "return": {"start": 70, "end": 71}})

    def test_checkpoint_in_final_return_sample_exposes_an_unrecorded_event(self):
        trace, columns, rows = self.checkpoint_csv()
        rows[7]["return.monitorSequenceAfterScan"] = 72
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        normalized, _ = self.materialize(trace, output.getvalue().encode())
        with self.assertRaisesRegex(TraceIssue, "Scan 17 monitorSequenceAfterScan is 72, expected 71") as issue:
            self.run_cycle(normalized)
        self.assertEqual((issue.exception.code, issue.exception.severity),
                         ("station_trace_monitor_checkpoint_mismatch", "warning"))

    def test_checkpoint_baseline_conflict_and_missing_column_are_rejected(self):
        trace, columns, rows = self.checkpoint_csv()
        trace["returnLeg"]["monitorSequenceAtStart"] = 69
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        with self.assertRaises(TraceIssue) as mismatch:
            self.materialize(trace, output.getvalue().encode())
        self.assertEqual(mismatch.exception.code, "cycle_capture_monitor_baseline_mismatch")

        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns[:-1], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        with self.assertRaisesRegex(ValueError, "Missing CSV columns: return.monitorSequenceAfterScan"):
            self.materialize(trace, output.getvalue().encode())


    def test_numeric_event_decoding_uses_the_project_declaration(self):
        trace, columns, rows = self.scan_csv()
        trace["scanCapture"]["eventEncoding"] = "project-mptraffic"
        codes = {0: "mpTRAFFIC_SH_NONE", 1: "mpTRAFFIC_SH_ASSIGNED", 2: ARRIVAL, 3: "mpTRAFFIC_SH_PASSTHROUGH"}
        rows[3]["inspection.detection.event"] = "2.000000"
        rows[7]["return.detection.event"] = "2"
        evidence = {"mode": "project-mptraffic", "libraryVersion": "2.07.0", "mapp6DVersion": "2.7.0"}
        event_source = lambda: (codes, evidence)
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        normalized, capture = self.materialize(trace, output.getvalue().encode(), event_source=event_source)
        self.assertEqual(self.run_cycle(normalized), self.run_cycle(self.trace))
        self.assertEqual(capture["eventEncoding"], evidence)

        for code, error_code in (("1", "station_trace_incomplete"), ("0.0", "cycle_capture_event_code_invalid"),
                                 ("99", "cycle_capture_event_code_invalid")):
            with self.subTest(code=code):
                rows[3]["inspection.detection.event"] = code
                output = StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
                with self.assertRaises(TraceIssue) as issue:
                    normalized, _ = self.materialize(trace, output.getvalue().encode(), event_source=event_source)
                    self.run_cycle(normalized)
                self.assertEqual(issue.exception.code, error_code)
        trace["scanCapture"]["eventNames"] = {"1": ARRIVAL}
        with self.assertRaisesRegex(ValueError, "either project-mptraffic eventEncoding or eventNames"):
            self.materialize(trace, output.getvalue().encode(), event_source=event_source)

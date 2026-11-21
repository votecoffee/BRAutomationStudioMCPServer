from copy import deepcopy
from decimal import Decimal
import unittest

from br_automation_studio.station_trace import (
    ARRIVAL, FLAGS, TraceIssue, check_leg, check_leg_positions, check_release_motion, check_sampled_speed,
    check_waypoint_positions, trace_header,
)


class StationTraceTest(unittest.TestCase):
    def setUp(self):
        self.identity = {"sourceStation": "Inspect", "destinationStation": "Outfeed", "managerId": "Traffic",
                         "launchPadSha256": "a" * 64, "rucSha256": "b" * 64}
        self.request = {"requestId": 12, "shuttleId": 3}
        self.reply = dict(self.request, acknowledged=True, approved=True)
        self.event = {"sequence": 22, "station": "Outfeed", "shuttleId": 3, "event": ARRIVAL}
        self.trace = {"schemaVersion": 1, "captureKind": "synthetic", **self.identity, **self.request,
                      "monitorSequenceBeforeRelease": 20, "scans": self.good_scans()}

    def sample(self, scan, **changes):
        flags = dict.fromkeys(FLAGS, False)
        flags.update(routePermitted=True, stationClear=True, monitorHealthy=True)
        return {**flags, "scan": scan, "requestId": 12, "selectedShuttleId": 3, **changes}

    def good_scans(self):
        return [
            self.sample(17, inspectionBusy=True, inspectionResult=dict(self.reply, requestId=11),
                        detections=[dict(self.event, sequence=20)]),
            self.sample(18, inspectionResult=self.reply),
            self.sample(19, release=True, detections=[dict(self.event, sequence=21, event="mpTRAFFIC_SH_PASSTHROUGH")]),
            self.sample(20, release=True, detections=[self.event]),
            self.sample(21, release=True, releaseDone=True, selectedShuttleId=None),
            self.sample(22, selectedShuttleId=None),
        ]

    def check(self, trace):
        trace_header(trace, self.identity)
        receipt = {}
        check_leg(trace, receipt)
        return receipt

    def test_early_arrival_matches_and_buffered_can_be_unobserved(self):
        receipt = self.check(self.trace)
        self.assertEqual(receipt["releaseScans"], [19])
        self.assertEqual(receipt["acceptedResultScan"], 18)
        self.assertEqual(receipt["doneScan"], 21)
        self.assertEqual(receipt["arrival"], {"scan": 20, **self.event})
        self.assertIsNone(receipt["bufferedScan"])
        self.assertTrue(receipt["arrivalObservedBeforeDone"])
        self.assertEqual((receipt["ignoredResults"], receipt["ignoredDetections"]), (1, 2))

        self.trace["scans"][3].update(releaseBuffered=True, releaseDone=True)
        receipt = self.check(self.trace)
        self.assertEqual(receipt["bufferedScan"], 20)
        self.assertEqual(receipt["doneScan"], 20)
        self.assertFalse(receipt["arrivalObservedBeforeDone"])

    def test_fails_on_release_without_authority_or_request_continuity(self):
        for scan, fields, code in (
            (18, {"inspectionResult": dict(self.reply, shuttleId=4)}, "station_trace_unauthorized_release"),
            (18, {"inspectionResult": dict(self.reply, acknowledged=False)}, "station_trace_unauthorized_release"),
            (19, {"inspectionBusy": True}, "station_trace_unauthorized_release"),
            (19, {"stationClear": False}, "station_trace_unauthorized_release"),
            (19, {"routePermitted": False}, "station_trace_unauthorized_release"),
            (19, {"selectedShuttleId": 4}, "station_trace_selection"),
            (21, {"requestId": 13}, "station_trace_request_changed"),
        ):
            with self.subTest(scan=scan, change=fields):
                trace = deepcopy(self.trace)
                trace["scans"][scan - 17].update(fields)
                with self.assertRaises(TraceIssue) as caught:
                    self.check(trace)
                self.assertEqual(caught.exception.code, code)
        self.trace["scans"].append(self.sample(23, release=True))
        with self.assertRaises(TraceIssue) as caught:
            self.check(self.trace)
        self.assertEqual(caught.exception.code, "station_trace_duplicate_release")

    def test_incomplete_capture_cannot_establish_arrival(self):
        for row, changes, code in (
            (3, {"detections": [dict(self.event, shuttleId=8)]}, "station_trace_incomplete"),
            (3, {"detections": [dict(self.event, sequence=21)]}, "station_trace_incomplete"),
            (3, {"detections": [dict(self.event, sequence=23)]}, "station_trace_detection_gap"),
            (3, {"monitorHealthy": False}, "station_trace_monitor_unverified"),
            (3, {"scan": 33}, "station_trace_scan_gap"),
        ):
            with self.subTest(code=code, change=changes):
                trace = deepcopy(self.trace)
                trace["scans"][row].update(changes)
                with self.assertRaises(TraceIssue) as caught:
                    self.check(trace)
                self.assertEqual((caught.exception.code, caught.exception.severity), (code, "warning"))

    def test_abort_overrides_same_scan_completion_and_preserves_first_status(self):
        self.trace["scans"][3].update(releaseAborted=True, releaseDone=True, statusId=-30001)
        self.trace["scans"][4].update(error=True, statusId=-30002)
        receipt = {}
        with self.assertRaises(TraceIssue) as caught:
            check_leg(self.trace, receipt)
        self.assertEqual(caught.exception.code, "station_trace_adapter_fault")
        self.assertIsNone(receipt["doneScan"])
        self.assertIsNone(receipt["arrival"])
        self.assertEqual(receipt["firstFault"], {"scan": 20, "statusId": -30001, "releaseAborted": True, "error": False})

    def test_rejects_trace_identity_mismatch(self):
        for key in self.identity:
            with self.subTest(key=key), self.assertRaises(TraceIssue) as caught:
                self.check(dict(self.trace, **{key: "different"}))
            self.assertEqual(caught.exception.code, "station_trace_identity")

    def test_release_motion_uses_the_edge_scan_and_the_same_pinned_ruc_context(self):
        expected = {"exitVelocityMps": "0.6", "exitAccelerationMps2": "6.00"}
        compiled = {"assembly": "Planar", "fields": {"InPlaneVelLim": {"raw": "0.75"}}}
        route = {"launchPad": {"sourceSha256": "a" * 64, "readbackSha256": "c" * 64}}
        pinned = {"manager": "Traffic", "motionLimits": {"state": "matched", "compiled": compiled},
                  "files": {"project": {"sha256": "a" * 64}, "readback": {"sha256": "c" * 64}}}
        report = {"routeReview": route, "rucReview": {"launchPadRoute": pinned}}
        self.trace["scans"][2].update(exitVelocityMps="0.6000", exitAccelerationMps2="6")
        leg = self.check(self.trace)
        check_release_motion(report, self.trace, expected, leg)
        recorded = leg["releaseMotion"]
        self.assertEqual((recorded["state"], recorded["scan"], recorded["compiledLimits"]),
                         ("matched", 19, compiled))

        self.trace["scans"][3].update(expected)
        for observed, code, severity in (
            ("0.7", "station_trace_release_motion_mismatch", "error"),
            ("NaN", "station_trace_release_motion_invalid", "error"),
            (None, "station_trace_release_motion_missing", "warning"),
        ):
            with self.subTest(observed=observed):
                self.trace["scans"][2]["exitVelocityMps"] = observed
                with self.assertRaises(TraceIssue) as caught:
                    check_release_motion(report, self.trace, expected, leg)
                self.assertEqual((caught.exception.code, caught.exception.severity), (code, severity))
                self.assertEqual(leg["releaseMotion"]["scan"], 19)
                self.assertEqual(leg["arrival"]["scan"], 20)

        pinned["files"]["readback"]["sha256"] = "d" * 64
        with self.assertRaises(TraceIssue) as caught:
            check_release_motion(report, self.trace, expected, leg)
        self.assertEqual(caught.exception.code, "station_trace_motion_limits_unverified")

    def test_waypoints_need_distinct_ordered_samples_from_the_same_shuttle(self):
        centres = {"Inspect": ["0.1", "0.2"], "Gate1": ["0.3", "0.2"],
                   "Gate2": ["0.5", "0.2"], "Outfeed": ["0.7", "0.2"]}
        pinned = {"manager": "Traffic", "files": {"project": {"sha256": "a" * 64}, "readback": {"sha256": "c" * 64}},
                  "positions": {"state": "matched", "compiled": {name: {"xyMetres": xy} for name, xy in centres.items()}},
                  "assemblyIo": {"state": "matched", "planned": {"requiredReadAxes": ["X", "Y"]}}}
        report = {"routeReview": {"launchPad": {"sourceSha256": "a" * 64, "readbackSha256": "c" * 64}},
                  "rucReview": {"launchPadRoute": pinned}}
        rows = [{"scan": scan, "positionSampleScan": scan, "positionShuttleId": 3, "positionValid": True,
                 "positionXM": x, "positionYM": "0.2"} for scan, x in ((20, "0.300005"), (21, "0.5"))]
        checked = {}
        path = list(centres)
        check_waypoint_positions(report, self.trace, rows, path, 19, 22, 5, checked)
        witnesses = checked["waypointReview"]["samples"]
        self.assertEqual([(item["waypoint"], item["scan"]) for item in witnesses], [("Gate1", 20), ("Gate2", 21)])
        self.assertEqual(witnesses[0]["deltaMicrometres"][0], "5")

        for scenario, samples, tolerance, missing in (
            ("reversed", [rows[0] | {"positionXM": "0.5"}, rows[1] | {"positionXM": "0.3"}], 5, ["Gate2"]),
            ("different shuttle", [rows[0] | {"positionShuttleId": 9}, rows[1]], 5, ["Gate1", "Gate2"]),
            ("ambiguous", rows, 200005, ["Gate1", "Gate2"]),
        ):
            with self.subTest(scenario=scenario), self.assertRaises(TraceIssue) as caught:
                check_waypoint_positions(report, self.trace, samples, path, 19, 22, tolerance, checked)
            self.assertEqual((caught.exception.code, caught.exception.severity), ("station_trace_waypoint_incomplete", "warning"))
            self.assertEqual(checked["waypointReview"]["pendingWaypoints"], missing)
        self.assertEqual(checked["waypointReview"]["ambiguousSamples"], 2)

    def test_sampled_speed_uses_elapsed_time_and_compares_diagonal_motion_at_the_limit(self):
        pinned = {"manager": "Traffic", "files": {"project": {"sha256": "a" * 64}, "readback": {"sha256": "c" * 64}},
                  "positions": {"state": "matched", "compiled": {}},
                  "assemblyIo": {"state": "matched", "planned": {"requiredReadAxes": ["X", "Y"]}},
                  "motionLimits": {"state": "matched", "compiled": {"fields": {"InPlaneVelLim": {"raw": "0.5"}}}}}
        report = {"routeReview": {"launchPad": {"sourceSha256": "a" * 64, "readbackSha256": "c" * 64}},
                  "rucReview": {"launchPadRoute": pinned}}
        source = {"scan": 19, "positionSampleScan": 19, "positionShuttleId": 3, "positionValid": True,
                  "positionXM": "0.100", "positionYM": "0.100", "positionTimeMicroseconds": 0}
        arrival = dict(source, scan=21, positionSampleScan=21, positionXM="0.4", positionYM="0.5",
                       positionTimeMicroseconds=1000000)
        result = {}
        check_sampled_speed(report, self.trace, [source, {"scan": 20}, arrival], 19, 21, result)
        speed = result["sampledSpeedReview"]
        self.assertEqual(speed["state"], "matched")
        self.assertEqual(Decimal(speed["peakInterval"]["speedMps"]), Decimal("0.5"))
        self.assertEqual((speed["checkedIntervals"], speed["skippedRows"], speed["maxIntervalMicroseconds"]), (1, 1, 1000000))

        for elapsed, code, severity in (
            (999999, "station_trace_speed_exceeds_limit", "error"),
            (0, "station_trace_speed_time_invalid", "error"),
            (None, "station_trace_speed_incomplete", "warning"),
        ):
            with self.subTest(elapsed=elapsed), self.assertRaises(TraceIssue) as caught:
                check_sampled_speed(report, self.trace, [source, arrival | {"positionTimeMicroseconds": elapsed}],
                                    19, 21, result)
            self.assertEqual((caught.exception.code, caught.exception.severity), (code, severity))

    def test_position_check_needs_fresh_same_shuttle_samples_and_enforces_the_per_axis_limit(self):
        positions = {"Inspect": {"xyMetres": ["0.66", "0.060"], "path": "Objects.br"},
                     "Outfeed": {"xyMetres": ["0.84", "0.12"], "path": "Objects.br"}}
        pinned = {"manager": "Traffic", "files": {"project": {"sha256": "a" * 64}, "readback": {"sha256": "c" * 64}},
                  "positions": {"state": "matched", "compiled": positions},
                  "assemblyIo": {"state": "matched", "planned": {"requiredReadAxes": ["X", "Y"]}}}
        report = {"routeReview": {"launchPad": {"sourceSha256": "a" * 64, "readbackSha256": "c" * 64}},
                  "rucReview": {"launchPadRoute": pinned}}
        release = {"positionValid": True, "positionSampleScan": 19, "positionShuttleId": 3,
                   "positionXM": "0.660005", "positionYM": "0.060"}
        arrival = dict(release, positionSampleScan=20, positionXM="0.84", positionYM="0.119996")
        self.trace["scans"][2].update(release)
        self.trace["scans"][3].update(arrival)
        receipt = self.check(self.trace)
        check_leg_positions(report, self.trace, 5, receipt)
        review = receipt["positionReview"]
        self.assertEqual(review["state"], "matched")
        self.assertEqual(review["samples"]["release"]["deltaMicrometres"][0], "5")
        self.assertEqual(review["samples"]["arrival"]["deltaMicrometres"][1], "-4")

        for fields, code, severity in (
            ({"positionSampleScan": 19}, "station_trace_position_stale", "warning"),
            ({"positionShuttleId": 4}, "station_trace_position_shuttle", "error"),
            ({"positionValid": False}, "station_trace_position_missing", "warning"),
            ({"positionYM": "0.119994"}, "station_trace_position_mismatch", "error"),
        ):
            with self.subTest(fields=fields):
                self.trace["scans"][3].update(arrival | fields)
                with self.assertRaises(TraceIssue) as caught:
                    check_leg_positions(report, self.trace, 5, self.check(self.trace))
                self.assertEqual((caught.exception.code, caught.exception.severity), (code, severity))

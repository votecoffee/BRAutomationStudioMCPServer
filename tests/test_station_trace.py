from copy import deepcopy
import unittest

from br_automation_studio.station_trace import ARRIVAL, FLAGS, TraceIssue, check_leg, trace_header


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

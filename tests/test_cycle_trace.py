from copy import deepcopy
import unittest

from br_automation_studio.cycle_trace import check_cycle
from br_automation_studio.station_trace import ARRIVAL, FLAGS, TraceIssue


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

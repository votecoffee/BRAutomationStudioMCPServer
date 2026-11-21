import unittest

from br_automation_studio.cycle_hmi import check_cycle_hmi
from br_automation_studio.station_trace import TraceIssue


class CycleHmiTest(unittest.TestCase):
    def setUp(self):
        self.ruc = {"jobId": "job", "planSha256": "a" * 64, "archive": {"sha256": "b" * 64}, "sourceMatched": True}
        values = {"requestId": 13, "shuttleId": 3, "phase": 10, "releaseTotal": 4}
        session = "4dc0361f-e059-4d65-8d2c-9066a28a7695"
        self.cycle = {"nextHold": {"requestId": 13, "shuttleId": 3}}
        self.trace = {"inspectionLeg": {"captureKind": "synthetic"}, "nextHold": dict(values, heartbeat=5),
                      "hmi": {"sessionId": session, "heartbeatIdentifier": "::Station:Pulse",
                              "bindings": {name: f"::Station:{name}" for name in values}}}
        endpoint = {"host": "127.0.0.1", "port": 14840, "namespaceUri": "urn:B&R/pv/"}
        self.owner = {"protocol": "tcp", "address": endpoint["host"], "port": endpoint["port"], "pid": 20}
        compiled = {"endpoint": endpoint, "dataType": "i=6"}
        heartbeat = {"stringIdentifier": "::Station:Pulse", "domId": "PulseOutput", "compiled": compiled,
                     "state": "matched"}
        bindings = [{"bindingReview": {"state": "matched", "domId": f"Output_{name}", "compiled": compiled,
                                      "stringIdentifier": f"::Station:{name}"},
                     "expectedValue": value, "archive": self.ruc["archive"]} for name, value in values.items()]
        status = {f"Output_{name}": f"{value}.0" for name, value in values.items()}
        self.hmi = {"state": "complete", "bindingReview": heartbeat, "statusBindingReviews": bindings,
                    "browser": {"state": "complete", "cleanup": "empty", "blankTimeOrigin": 2,
                                "counterWidgetId": "PulseOutput",
                                "initial": {"timeOrigin": 1, "counterSamples": ["6", "7", "8"], "statusValues": dict(status)},
                                "reopened": {"timeOrigin": 3, "counterSamples": ["11", "12"], "statusValues": dict(status)}}}
        access = {"accessLevel": 1, "userAccessLevel": 1}
        for window, sequence in (("opcUaBefore", [3, 4, 5]), ("opcUaAfter", [13, 14, 15])):
            self.hmi[window] = {
                "ownedListener": dict(self.owner), "nodeId": "ns=6;s=::Station:Pulse", "variantType": "Int32",
                "state": "complete", "clientDisconnected": True, "namespaceIndex": 6,
                "namespaceUri": endpoint["namespaceUri"], "stringIdentifier": "::Station:Pulse", **access,
                "samples": [{"value": value, "statusCode": 0} for value in sequence],
                "statusValues": {f"::Station:{name}": {"nodeId": f"ns=6;s=::Station:{name}", "value": value,
                                                     "statusCode": 0, "variantType": "Int32", **access}
                                 for name, value in values.items()},
            }
        self.smoke = {"state": "complete", "cleanup": "empty", "sessionId": session,
                      "hmi": self.hmi, "ownedEndpoints": [self.owner], "planSha256": self.ruc["planSha256"],
                      "packageSha256": self.ruc["archive"]["sha256"]}
        self.document = {"schemaVersion": 1, "operation": "arsim_status_smoke", "status": "complete",
                         "result": {"rucReview": self.ruc, "arsimSmoke": self.smoke, "diagnostics": [],
                                    "evidence": "arsim_loopback_status"}}

    def check(self, require_capture=False):
        checked = {}
        check_cycle_hmi(self.cycle, self.trace, self.ruc, self.document, checked,
                        require_session_capture=require_capture)
        return checked

    def test_matching_synthetic_capture_retains_request_and_order(self):
        receipt = self.check()
        self.assertEqual(receipt["captureKind"], "synthetic")
        self.assertEqual(receipt["status"]["requestId"]["value"], 13)
        self.assertEqual(receipt["status"]["releaseTotal"]["identifier"], "::Station:releaseTotal")
        self.assertEqual(receipt["heartbeat"]["atHold"], receipt["heartbeat"]["directBeforeLast"])

    def test_strict_pairing_requires_a_retained_scan(self):
        self.assertNotIn("scanCapture", self.check())
        with self.assertRaises(TraceIssue) as issue:
            self.check(require_capture=True)
        self.assertEqual(issue.exception.code, "cycle_hmi_scan_capture_missing")

    def test_extended_hold_reaches_last_direct_heartbeat_after_reopen(self):
        continuation = self.cycle["holdContinuation"] = {"state": "matched", "lastScan": 25, "lastHeartbeat": 15}
        coverage = self.check()["holdCoverage"]
        self.assertEqual((coverage["state"], coverage["lastHoldScan"], coverage["directAfterLast"]), ("matched", 25, 15))
        continuation["lastHeartbeat"] = 14
        with self.assertRaises(TraceIssue) as issue:
            self.check()
        self.assertEqual(issue.exception.code, "cycle_hmi_hold_coverage")

    def test_retained_scan_must_match_reviewed_bytes_and_listener(self):
        digest, size = "c" * 64, 128
        self.cycle["scanCapture"] = {"sha256": digest, "bytes": size}
        retained = {"state": "retained", "sha256": digest, "bytes": size, "path": "scan-capture.csv",
                    "ownedListener": self.owner}
        self.hmi["scanCapture"] = retained
        self.assertEqual(self.check(require_capture=True)["scanCapture"]["sha256"], digest)
        for change in ({"sha256": "d" * 64}, {"bytes": size + 1}, {"ownedListener": dict(self.owner, pid=900)}):
            with self.subTest(change=change), self.assertRaises(TraceIssue) as issue:
                self.hmi["scanCapture"] = retained | change
                self.check()
            self.assertEqual(issue.exception.code, "cycle_hmi_scan_capture_mismatch")
        self.hmi["scanCapture"] = retained
        del self.cycle["scanCapture"]
        with self.assertRaises(TraceIssue) as issue:
            self.check()
        self.assertEqual(issue.exception.code, "cycle_hmi_scan_capture_mismatch")

    def test_package_is_checked_before_session_and_hold(self):
        self.smoke["packageSha256"] = "c" * 64
        del self.trace["hmi"]
        with self.assertRaisesRegex(TraceIssue, "different builds or RUCs") as issue:
            self.check()
        self.assertEqual(issue.exception.code, "cycle_hmi_package_mismatch")

    def test_same_package_needs_the_same_session_and_correct_capture_order(self):
        for fields, key, value, code in (
            (self.smoke, "sessionId", "different-session", "cycle_hmi_session_mismatch"),
            (self.trace["nextHold"], "heartbeat", 9, "cycle_hmi_hold_order"),
            (self.trace["inspectionLeg"], "captureKind", "controller", "cycle_hmi_capture_kind"),
            (self.hmi["browser"], "blankTimeOrigin", 3, "cycle_hmi_reopen"),
        ):
            with self.subTest(key=key), self.assertRaises(TraceIssue) as issue:
                before = fields[key]
                fields[key] = value
                try:
                    self.check()
                finally:
                    fields[key] = before
            self.assertEqual(issue.exception.code, code)

    def test_status_needs_quality_and_agreement_in_every_observation_window(self):
        for fields, key, value, code in (
            (self.hmi["statusBindingReviews"][0], "expectedValue", 12, "cycle_hmi_status_mismatch"),
            (self.hmi["browser"]["reopened"]["statusValues"], "Output_requestId", "12", "cycle_hmi_status_mismatch"),
            (self.hmi["opcUaBefore"]["statusValues"]["::Station:shuttleId"], "value", True, "cycle_hmi_status_mismatch"),
            (self.hmi["opcUaAfter"]["statusValues"]["::Station:releaseTotal"], "userAccessLevel", 3,
             "cycle_hmi_status_quality"),
            (self.hmi["opcUaAfter"]["statusValues"]["::Station:phase"], "statusCode", 0x40000000,
             "cycle_hmi_status_quality"),
        ):
            with self.subTest(key=key):
                original = fields[key]
                fields[key] = value
                try:
                    with self.assertRaises(TraceIssue) as issue:
                        self.check()
                    self.assertEqual(issue.exception.code, code)
                finally:
                    fields[key] = original

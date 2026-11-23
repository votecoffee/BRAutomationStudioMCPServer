from pathlib import Path
import unittest
from unittest.mock import Mock

from br_automation_studio.cycle_hmi import bind_session_scan_capture, check_cycle_hmi, check_layout_svg_pairing, review_cycle_hmi
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

    def check(self, require_capture=False, assembly_route=None):
        checked = {}
        check_cycle_hmi(self.cycle, self.trace, self.ruc, self.document, checked,
                        require_session_capture=require_capture, assembly_route=assembly_route)
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

    def attach_assembly_widget(self):
        kind, widget_id = "widgets/mapp6D/Assembly6D", "Main_Overview"
        client_map, content = "/main.json", "wwwRoot/contents/Main.html"
        self.ruc["widgets"] = [{"contentId": "Main", "id": "Overview", "className": kind,
                                "assembly": "Planar", "manager": "mapp6D::Traffic", "path": content}]
        self.ruc["startPageWidget"] = {
            "state": "matched", "domId": widget_id,
            "compiled": {"widgetType": kind, "assembly": "Planar", "manager": "mapp6D::Traffic",
                         "content": content, "clientMapUrl": client_map},
        }
        browser = self.hmi["browser"]
        browser.update(assemblyWidgetId=widget_id, startPageMapPath=client_map)
        for phase in ("initial", "reopened"):
            browser[phase].update(widgetType=kind, startPageMap={"state": "observed", "path": client_map, "httpStatus": 200},
                                  layout={"state": "observed", "boundedSvgCount": 1, "assets": {
                                      "assembly": {"path": "/FileDevice:6DData/Planar.svg", "httpStatus": 200},
                                      "trafficManager": {"path": "/FileDevice:6DData/Traffic.svg", "httpStatus": 304},
                                  }})
        return {"managerId": "Traffic", "assembly": "Planar"}

    def test_assembly_widget_needs_the_cycle_route_and_layout_in_both_documents(self):
        with self.assertRaises(TraceIssue) as issue:
            self.check(assembly_route={"managerId": "Traffic", "assembly": "Planar"})
        self.assertEqual(issue.exception.code, "cycle_hmi_assembly_widget")
        route = self.attach_assembly_widget()
        browser = self.hmi["browser"]
        receipt = self.check(assembly_route=route)["assemblyWidget"]
        self.assertEqual(receipt["state"], "matched")
        self.assertEqual(receipt["documents"]["reopened"]["layoutStatuses"]["trafficManager"], 304)
        with self.assertRaises(TraceIssue) as issue:
            self.check(assembly_route=route | {"managerId": "OtherTraffic"})
        self.assertEqual(issue.exception.code, "cycle_hmi_assembly_route")
        browser["reopened"]["layout"]["assets"]["trafficManager"]["path"] = "/FileDevice:6DData/OtherTraffic.svg"
        with self.assertRaises(TraceIssue) as issue:
            self.check(assembly_route=route)
        self.assertEqual(issue.exception.code, "cycle_hmi_assembly_layout")

    def test_combined_review_reuses_the_status_snapshot_and_reports_unsupported_geometry(self):
        route = self.attach_assembly_widget()
        path = Path("session/status-response.json")
        snapshot = (self.document, {"path": str(path), "sha256": "c" * 64})
        layout = {"state": "unverified", "assets": {}}
        diagnostic = {"code": "layout_svg_unverified", "severity": "warning"}
        review_layout = Mock(return_value={"layoutSvgReview": layout, "diagnostics": [diagnostic]})
        report = {"cycleTraceReview": self.cycle, "cycleHmiReview": {}, "rucReview": self.ruc,
                  "routeReview": route, "diagnostics": []}
        review_cycle_hmi(report, self.trace, path, captured_layout_review=review_layout, status_snapshot=snapshot)
        self.assertIs(review_layout.call_args.kwargs["status_snapshot"], snapshot)
        self.assertEqual(report["cycleHmiReview"]["assemblyWidget"]["state"], "matched")
        self.assertEqual(report["cycleHmiReview"]["state"], "incomplete")
        self.assertEqual(report["cycleHmiReview"]["layoutGeometry"]["state"], "unverified")
        self.assertEqual(report["layoutSvgReview"], layout)
        self.assertEqual(report["diagnostics"], [diagnostic])

    def test_layout_svg_review_matches_the_owned_capture_and_both_hashes(self):
        paths = {"assembly": "/FileDevice:6DData/Planar.svg", "trafficManager": "/FileDevice:6DData/Traffic.svg"}
        owner = {"protocol": "tcp", "address": "127.0.0.1", "port": 18081, "pid": 20}
        self.smoke["ownedEndpoints"].append(owner)
        assets = {role: {"path": path, "sha256": digit * 64, "status": 200, "bytes": 512}
                  for (role, path), digit in zip(paths.items(), "de", strict=True)}
        self.hmi["browser"].update(url="http://127.0.0.1:18081/",
                                   layoutCapture={"state": "complete", "assets": assets, "ownedListener": owner})
        layout = {"state": "matched", "layoutId": "Grid", "assemblyId": "Planar", "managerId": "Traffic",
                  "assets": {role: {"sha256": row["sha256"]} for role, row in assets.items()}, "positionToleranceMm": 0.001}
        response = {"schemaVersion": 1, "operation": "layout_svg_review", "status": "complete",
                    "result": {"rucReview": self.ruc, "evidence": "layout_svg_readback", "diagnostics": [],
                               "layoutSvgReview": layout}}
        result = {"assemblyWidget": {"assembly": "Planar", "manager": "Traffic", "layoutPaths": paths}}
        check_layout_svg_pairing(self.ruc, self.smoke, response, result)
        self.assertEqual(result["layoutGeometry"]["state"], "matched")
        self.assertEqual(result["layoutGeometry"]["assets"]["trafficManager"]["sha256"], "e" * 64)
        layout["statusCapture"] = {"sessionId": self.smoke["sessionId"]}
        check_layout_svg_pairing(self.ruc, self.smoke, response, result)
        for fields, key, change, code in (
            (layout["assets"]["trafficManager"], "sha256", "f" * 64, "cycle_hmi_layout_hash"),
            (self.hmi["browser"]["layoutCapture"], "ownedListener", owner | {"pid": 21}, "cycle_hmi_layout_capture"),
            (layout, "managerId", "OtherTraffic", "cycle_hmi_layout_route"),
            (layout["statusCapture"], "sessionId", "different-generation", "cycle_hmi_layout_session"),
        ):
            with self.subTest(key=key):
                original = fields[key]
                fields[key] = change
                try:
                    with self.assertRaises(TraceIssue) as error:
                        check_layout_svg_pairing(self.ruc, self.smoke, response, result)
                    self.assertEqual(error.exception.code, code)
                finally:
                    fields[key] = original

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

    def test_bind_session_scan_fills_only_omitted_descriptor_fields(self):
        digest = "c" * 64
        self.hmi["scanCapture"] = {"state": "retained", "sha256": digest, "bytes": 128, "path": "scan-capture.csv",
                                   "ownedListener": self.owner}
        witness = self.trace["hmi"]
        session = witness["sessionId"]
        self.trace["hmi"] = {key: value for key, value in witness.items() if key != "sessionId"}
        self.trace["scanCapture"] = {"nextHoldScan": 18}
        bound, receipt = bind_session_scan_capture(self.trace, self.document, self.ruc)
        self.assertEqual(bound["scanCapture"], {"nextHoldScan": 18, "sha256": digest})
        self.assertEqual(bound["hmi"], witness)
        self.assertNotIn("sessionId", self.trace["hmi"])
        self.assertNotIn("sha256", self.trace["scanCapture"])
        self.assertEqual(receipt["derivedFields"], ["scanCapture.sha256", "hmi.sessionId"])

        for section, key, value, code in (
            ("scanCapture", "sha256", "0" * 64, "cycle_hmi_scan_capture_mismatch"),
            ("hmi", "sessionId", None, "cycle_hmi_session_mismatch"),
        ):
            with self.subTest(key=key), self.assertRaises(TraceIssue) as error:
                altered = self.trace | {section: self.trace[section] | {key: value}}
                bind_session_scan_capture(altered, self.document, self.ruc)
            self.assertEqual(error.exception.code, code)

    def test_bind_session_scan_can_take_the_full_explicit_binding_map(self):
        wanted = self.trace.pop("hmi")
        self.trace["scanCapture"] = {}
        self.hmi["scanCapture"] = {"state": "retained", "sha256": "c" * 64, "bytes": 128, "path": "scan-capture.csv",
                                   "ownedListener": self.owner}
        reviews = self.hmi["statusBindingReviews"]
        for row, (role, identifier) in zip(reviews, wanted["bindings"].items(), strict=True):
            self.assertEqual(row["bindingReview"]["stringIdentifier"], identifier)
            row["role"] = role
        bound, receipt = bind_session_scan_capture(self.trace, self.document, self.ruc)
        self.assertEqual(bound["hmi"], wanted)
        self.assertEqual(receipt["derivedFields"], [
            "scanCapture.sha256", "hmi.sessionId", "hmi.heartbeatIdentifier", "hmi.bindings",
        ])
        reviews[0].pop("role")
        with self.assertRaises(TraceIssue) as error:
            bind_session_scan_capture(self.trace, self.document, self.ruc)
        self.assertEqual(error.exception.code, "cycle_hmi_status_roles")

        self.cycle["scanCapture"] = {"sha256": "c" * 64, "bytes": 128}
        self.trace["hmi"] = wanted
        roles = self.trace["hmi"]["bindings"]
        roles["phase"], roles["releaseTotal"] = roles["releaseTotal"], roles["phase"]
        with self.assertRaises(TraceIssue) as error:
            self.check()
        self.assertEqual(error.exception.code, "cycle_hmi_status_roles")

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

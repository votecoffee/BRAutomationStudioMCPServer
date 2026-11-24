from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from br_automation_studio.cycle_hmi import (
    bind_session_scan_capture, check_cycle_hmi, check_layout_svg_pairing, check_session_receipt, review_cycle_hmi,
)
from br_automation_studio.cycle_trace import cycle_trace_review
from br_automation_studio.operations import InvalidRequest, execute
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

    def check(self, require_capture=False, assembly_route=None, *, require_hold=False):
        checked = {}
        check_cycle_hmi(self.cycle, self.trace, self.ruc, self.document, checked,
                        require_session_capture=require_capture, require_hold_through_reopen=require_hold,
                        assembly_route=assembly_route)
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

    def test_session_receipt_rejects_missing_or_changed_data_before_scan_identity_is_derived(self):
        with TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH")) as scratch:
            root = Path(scratch).resolve()
            status, trace, session = (root / name for name in ("status-response.json", "trace.json", "session.json"))
            status.write_text(json.dumps(self.document), encoding="utf-8")
            trace.write_text(json.dumps(self.trace | {"scanCapture": {}}), encoding="utf-8")
            original = (json.dumps(self.smoke, sort_keys=True, indent=4) + "\n").encode("utf-8")
            session.write_bytes(original)
            self.assertEqual(check_session_receipt(session, self.smoke, code="cycle_hmi_session_receipt"),
                             {"path": str(session), "sha256": sha256(original).hexdigest()})

            changed = deepcopy(self.smoke)
            changed["hmi"]["browser"]["reopened"]["counterSamples"] = ["11", "13"]
            for retained in (None, changed):
                with self.subTest(retained=retained is not None):
                    if retained is None:
                        session.unlink()
                    else:
                        session.write_text(json.dumps(retained), encoding="utf-8")
                    route = {"stationReview": {"state": "read", "legs": [None, None]},
                             "rucReview": self.ruc, "diagnostics": []}
                    with (patch("br_automation_studio.cycle_trace.launchpad_route_review", return_value=route),
                          patch("br_automation_studio.cycle_trace.selected_leg",
                                side_effect=[["Load", "Unload"], ["Unload", "Load"]]),
                          patch("br_automation_studio.cycle_trace.leg_identity"),
                          patch("br_automation_studio.cycle_trace.bind_session_scan_capture") as derive):
                        report = cycle_trace_review(None, None, None, "Traffic", None, None, None, None,
                                                    "Load", trace, hmi_status_path=status, require_session_capture=True,
                                                    require_hmi_session_receipt=True)
                    derive.assert_not_called()
                    self.assertEqual(report["cycleHmiReview"]["state"], "issues")
                    self.assertEqual(report["cycleTraceReview"]["phase"], "verify_session_receipt")
                    self.assertEqual(report["diagnostics"][0]["code"], "cycle_hmi_session_receipt")
                    self.assertEqual(report["diagnostics"][0]["path"], session.as_posix())

    def test_held_status_scan_review_checks_exact_browser_and_direct_heartbeats(self):
        roles = self.trace["hmi"]["bindings"]
        for binding in self.hmi["statusBindingReviews"]:
            binding["role"] = next(role for role, identifier in roles.items()
                                   if identifier == binding["bindingReview"]["stringIdentifier"])
        with TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH")) as scratch:
            root = Path(scratch)
            status, session, scan = (root / name for name in ("status.json", "session.json", "scan-capture.csv"))
            request = {"schemaVersion": 1, "workspace": "test", "operation": "held_status_scan_review",
                       "arguments": {"project": "Loop.apj", "jobDirectory": "job", "hmiStatusReport": status.name}}

            def review(raw):
                scan.write_bytes(raw)
                self.hmi["scanCapture"] = {"state": "retained", "path": scan.name, "bytes": len(raw),
                                           "ownedListener": self.owner, "sha256": sha256(raw).hexdigest()}
                status.write_text(json.dumps(self.document), encoding="utf-8")
                session.write_text(json.dumps(self.smoke), encoding="utf-8")
                fixture = {"diagnostics": [], "rucReview": self.ruc}
                with patch("br_automation_studio.held_status_scan.review_ruc", return_value=fixture):
                    return execute(request, root)

            header = b"scan,heartbeat,requestId,shuttleId,phase,releaseTotal\n"
            scans = b"".join(f"{index},{heartbeat},13,3,10,4\n".encode("ascii")
                             for index, heartbeat in enumerate(range(3, 16), 20))
            good = review(header + scans)
            self.assertEqual(good["status"], "complete")
            result = good["result"]["heldStatusScanReview"]
            self.assertEqual((result["state"], result["window"]["rows"]), ("matched", 13))
            self.assertEqual(result["status"]["requestId"]["value"], 13)

            missing = review(header + scans.replace(b"25,8,", b"25,7,"))
            self.assertEqual(missing["status"], "issues")
            self.assertEqual(missing["result"]["diagnostics"][0]["code"], "held_scan_heartbeat_missing")
            self.assertEqual(missing["result"]["heldStatusScanReview"]["window"]["missingHeartbeats"], [8])

            descriptor = root / "hold.json"
            descriptor.write_text(json.dumps({"schemaVersion": 1, "columns": {"heartbeat": "Tick"}}), encoding="utf-8")
            request["arguments"]["scanDescriptor"] = descriptor.name
            renamed = review(header.replace(b"heartbeat", b"Tick") + scans)
            self.assertEqual(renamed["status"], "complete")
            self.assertEqual(renamed["result"]["heldStatusScanReview"]["window"], result["window"])

    def test_extended_hold_reaches_last_direct_heartbeat_after_reopen(self):
        self.assertNotIn("holdCoverage", self.check())
        with self.assertRaises(TraceIssue) as missing:
            self.check(require_hold=True)
        self.assertEqual(missing.exception.code, "cycle_hmi_hold_missing")
        continuation = self.cycle["holdContinuation"] = {"state": "matched", "lastScan": 25, "lastHeartbeat": 15}
        coverage = self.check(require_hold=True)["holdCoverage"]
        self.assertEqual((coverage["state"], coverage["lastHoldScan"], coverage["directAfterLast"]), ("matched", 25, 15))
        continuation["lastHeartbeat"] = 14
        with self.assertRaises(TraceIssue) as issue:
            self.check(require_hold=True)
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

    def test_cycle_mapping_lines_recheck_the_compiled_plan_and_both_document_rows(self):
        route = self.attach_assembly_widget()
        mapping = "mapping_S1_to_G1"
        self.ruc["trafficManagers"] = [{"manager": "Traffic", "assembly": "Planar",
                                        "edges": [{"source": "S1", "destination": "G1"}]}]
        browser = self.hmi["browser"]
        browser["expectedTrafficMappings"] = {"managerId": "Traffic", "ids": [mapping]}
        for phase in ("initial", "reopened"):
            browser[phase]["trafficMappings"] = {"state": "observed", "managerId": "Traffic", "overlayCount": 1,
                                                  "unverifiedMappings": [], "mappings": {mapping: {
                                                      "groupCount": 1, "lineCount": 1, "visible": True,
                                                      "widthCssPx": 22, "heightCssPx": 0,
                                                  }}}

        def review(document):
            report = {"cycleTraceReview": self.cycle, "cycleHmiReview": {}, "rucReview": self.ruc,
                      "routeReview": route, "diagnostics": []}
            path = Path("session/status-response.json")
            review_cycle_hmi(report, self.trace, path, status_snapshot=(document, {"path": str(path)}),
                             require_traffic_mapping_lines=True)
            return report

        matched = review(self.document)["cycleHmiReview"]
        self.assertEqual(matched["assemblyWidget"]["state"], "matched")
        result = matched["trafficMappings"]
        self.assertEqual((result["state"], result["ids"]), ("matched", [mapping]))
        self.assertEqual([row["state"] for row in result["documents"].values()], ["observed", "observed"])

        no_plan = deepcopy(self.document)
        del no_plan["result"]["arsimSmoke"]["hmi"]["browser"]["expectedTrafficMappings"]
        self.assertEqual(review(no_plan)["diagnostics"][0]["code"], "cycle_hmi_mapping_plan")

        for changes in ({"groupCount": True}, {"lineCount": 2}, {"visible": False}, {"widthCssPx": 0}):
            with self.subTest(changes=changes):
                document = deepcopy(self.document)
                reopened = document["result"]["arsimSmoke"]["hmi"]["browser"]["reopened"]["trafficMappings"]
                reopened["mappings"][mapping].update(changes)
                invalid = review(document)
                self.assertEqual(invalid["diagnostics"][0]["code"], "cycle_hmi_mapping_document")
                self.assertIn("reopened", invalid["diagnostics"][0]["message"])
                documents = invalid["cycleHmiReview"]["trafficMappings"]["documents"]
                self.assertEqual(documents["initial"]["state"], "observed")
                self.assertEqual(documents["reopened"]["unverifiedMappings"], [mapping])

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
        layout["hmiCapture"] = {"sessionId": self.smoke["sessionId"]}
        check_layout_svg_pairing(self.ruc, self.smoke, response, result)
        for fields, key, change, code in (
            (layout["assets"]["trafficManager"], "sha256", "f" * 64, "cycle_hmi_layout_hash"),
            (self.hmi["browser"]["layoutCapture"], "ownedListener", owner | {"pid": 21}, "cycle_hmi_layout_capture"),
            (layout, "managerId", "OtherTraffic", "cycle_hmi_layout_route"),
            (layout["statusCapture"], "sessionId", "different-generation", "cycle_hmi_layout_session"),
            (layout["hmiCapture"], "sessionId", "different-panel-generation", "cycle_hmi_layout_session"),
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

    def test_workstation_report_identity_is_bound_to_the_cycle_when_present(self):
        environment = {"path": "simulation-environment.json", "sha256": "d" * 64}
        self.document["result"]["rucReview"] = dict(self.ruc)
        self.ruc["environmentReport"] = environment

        self.assertNotIn("environmentReport", self.check())
        self.smoke["environmentReport"] = dict(environment)
        self.document["result"]["rucReview"]["environmentReport"] = dict(environment)
        self.assertEqual(self.check()["environmentReport"], environment)

        for report in (self.smoke, self.document["result"]["rucReview"]):
            report["environmentReport"]["sha256"] = "e" * 64
            with self.assertRaises(TraceIssue) as issue:
                self.check()
            self.assertEqual(issue.exception.code, "cycle_hmi_environment_mismatch")
            report["environmentReport"]["sha256"] = environment["sha256"]

    def test_review_rehashes_the_adjacent_or_explicit_workstation_file(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        status_path = root / "status.json"
        adjacent = root / "simulation-environment.json"
        original = b'{"modeled":true}\n'
        environment = {"path": adjacent.name, "sha256": sha256(original).hexdigest()}
        self.ruc["environmentReport"] = environment
        self.smoke["environmentReport"] = environment

        def review(**options):
            status_path.write_text(json.dumps(self.document), encoding="utf-8")
            report = {"cycleTraceReview": self.cycle, "rucReview": self.ruc,
                      "cycleHmiReview": {}, "diagnostics": []}
            review_cycle_hmi(report, self.trace, status_path, **options)
            return report

        identities = review()["cycleHmiReview"]
        self.assertEqual(identities["state"], "matched")
        self.assertEqual(identities["environmentReport"], environment)
        self.assertNotIn("environmentReportFile", identities)

        adjacent.write_bytes(original)
        matched = review()["cycleHmiReview"]
        self.assertEqual(matched["state"], "matched")
        self.assertEqual(matched["environmentReportFile"], {"path": str(adjacent), "sha256": environment["sha256"]})

        adjacent.write_bytes(original + b"\n")
        changed = review()
        self.assertEqual(changed["cycleHmiReview"]["state"], "issues")
        issue, = changed["diagnostics"]
        self.assertEqual((issue["code"], issue["path"]), ("cycle_hmi_environment_file_mismatch", adjacent.as_posix()))

        moved = root / "archived-workstation.json"
        moved.write_bytes(original)
        explicit = review(environment_report_path=moved)["cycleHmiReview"]
        self.assertEqual(explicit["environmentReportFile"]["path"], str(moved))
        missing = review(environment_report_path=root / "missing.json")
        self.assertEqual(missing["diagnostics"][0]["code"], "cycle_hmi_environment_file_missing")

        del self.smoke["environmentReport"]
        self.assertEqual(review(environment_report_path=moved)["diagnostics"][0]["code"],
                         "cycle_hmi_environment_file_unbound")
        self.assertEqual(review()["cycleHmiReview"]["state"], "matched")

    def test_hmi_evidence_options_require_and_pass_the_status_response(self):
        root = Path("workspace")
        request = {
            "schemaVersion": 1, "workspace": "test", "operation": "cycle_trace_review",
            "arguments": {"project": "Loop.apj", "managerId": "Traffic", "jobDirectory": "job",
                          "launchPadProject": "Layout.lpj", "launchPadReadback": "Layout.json",
                          "sourceStation": "Load", "trace": "trace.json", "hmiEnvironmentReport": "archived-review.json"},
        }
        with self.assertRaisesRegex(InvalidRequest, "hmiEnvironmentReport needs hmiStatusReport"):
            execute(request, root)

        request["arguments"]["hmiStatusReport"] = "session/status.json"
        with patch("br_automation_studio.operations.cycle_trace_review", return_value={"diagnostics": []}) as review:
            self.assertEqual(execute(request, root)["status"], "complete")
        self.assertEqual(review.call_args.kwargs["hmi_status_path"], root / "session/status.json")
        self.assertEqual(review.call_args.kwargs["hmi_environment_report_path"], root / "archived-review.json")
        args = request["arguments"]
        del args["hmiEnvironmentReport"], args["hmiStatusReport"]
        args["requireHmiTrafficMappingLines"] = True
        with self.assertRaisesRegex(InvalidRequest, "requireHmiTrafficMappingLines needs hmiStatusReport"):
            execute(request, root)
        args["hmiStatusReport"] = "session/status.json"
        with patch("br_automation_studio.operations.cycle_trace_review", return_value={"diagnostics": []}) as review:
            self.assertEqual(execute(request, root)["status"], "complete")
        self.assertTrue(review.call_args.kwargs["require_traffic_mapping_lines"])
        del args["hmiStatusReport"], args["requireHmiTrafficMappingLines"]
        args["requireHmiSessionReceipt"] = True
        with self.assertRaisesRegex(InvalidRequest, "requireHmiSessionReceipt needs hmiStatusReport"):
            execute(request, root)
        args["hmiStatusReport"] = "session/status.json"
        with patch("br_automation_studio.operations.cycle_trace_review", return_value={"diagnostics": []}) as review:
            self.assertEqual(execute(request, root)["status"], "complete")
        self.assertTrue(review.call_args.kwargs["require_hmi_session_receipt"])

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

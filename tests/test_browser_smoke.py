from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from br_automation_studio.arsim_smoke import WIDGET_BUNDLE_PATH
from br_automation_studio.browser_smoke import (
    ASSEMBLY_WIDGET, Page, assembly_dom_id, capture_layout_assets, debug_listener, expected_traffic_labels,
    layout_asset_paths, verify_navigation,
)
from br_automation_studio.operations import InvalidRequest, execute

URL = "http://127.0.0.1:18081/"


def hmi(value, origin):
    return {"url": URL, "timeOrigin": origin, "widgetType": ASSEMBLY_WIDGET, "value": value}


class TestBrowserSmoke(unittest.TestCase):
    def receipt(self):
        return {"counterWidgetId": "Proof_Count", "assemblyWidgetId": "Proof_Asmb"}

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", return_value=0)
    def test_reopening_excludes_old_document_and_equivalent_numeric_values(self, now, pause):
        page = Mock(deadline=2)
        initial_bounds = {"widthCssPx": 240, "heightCssPx": 150}
        reopened_bounds = {"widthCssPx": 400, "heightCssPx": 150}
        page.read.side_effect = [
            hmi("1.0", 10), hmi("1.00", 10), hmi("2", 10), dict(hmi("3.0", 10), assemblyBounds=initial_bounds),
            hmi("3.0", 10), {"url": "about:blank", "timeOrigin": 15},
            dict(hmi("99", 10), assemblyBounds=initial_bounds), hmi("0.0", 20), hmi("3.00", 20), hmi("4.0", 20),
            dict(hmi("5.0", 20), assemblyBounds=reopened_bounds),
        ]
        receipt = self.receipt()
        verify_navigation(page, URL, receipt)
        self.assertEqual([item.args[0] for item in page.navigate.call_args_list], [URL, "about:blank", URL])
        self.assertEqual(receipt["initial"], {"counterSamples": ["1.0", "2", "3.0"], "timeOrigin": 10,
                                              "widgetType": ASSEMBLY_WIDGET, "assemblyBounds": initial_bounds})
        self.assertEqual(receipt["blankTimeOrigin"], 15)
        self.assertEqual(receipt["reopened"]["timeOrigin"], 20)
        self.assertEqual(receipt["reopened"]["counterSamples"], ["4.0", "5.0"])
        self.assertEqual(receipt["reopened"]["assemblyBounds"], reopened_bounds)

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", return_value=0)
    def test_status_observed_in_both_documents_without_assembly_widget(self, now, pause):
        status = {"Proof_Request": "7.0", "Proof_Shuttle": "12"}

        def sample(value, origin, fields=status):
            return dict(hmi(str(value), origin), widgetType=None, statusValues=fields)

        page = Mock(deadline=2)
        page.read.side_effect = [
            sample(99, 10, {}), sample(1, 10), sample(2, 10), sample(3, 10),
            {"url": "about:blank", "timeOrigin": 15},
            sample(100, 10), sample(4, 20), sample(5, 20),
        ]
        receipt = dict(self.receipt(), assemblyWidgetId=None, expectedStatus={"Proof_Request": 7, "Proof_Shuttle": 12})
        verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["counterSamples"], ["1", "2", "3"])
        self.assertEqual(receipt["reopened"]["counterSamples"], ["4", "5"])
        self.assertEqual(receipt["initial"]["statusValues"], status)
        self.assertEqual(receipt["reopened"]["statusValues"], status)

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", return_value=0)
    def test_status_change_after_first_observation_fails_the_document(self, now, pause):
        page = Mock(deadline=2)
        page.read.side_effect = [dict(hmi("1", 10), statusValues={"Phase": "10"}),
                                 dict(hmi("2", 10), statusValues={"Phase": "20"})]
        receipt = dict(self.receipt(), expectedStatus={"Phase": 10})
        with self.assertRaisesRegex(ValueError, "displayed status changed"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["counterSamples"], ["1"])

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_placeholder_and_even_one_new_value_cannot_pass(self, now, pause):
        page = Mock(deadline=7)
        page.read.side_effect = [
            hmi("1", 10), hmi("2", 10), hmi("3", 10), {"url": "about:blank", "timeOrigin": 15},
            hmi("0.0", 20), hmi("4.00", 20), hmi("4", 20),
        ]
        receipt = self.receipt()
        with self.assertRaisesRegex(TimeoutError, "changing numeric counter"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["state"], "reopened_document")

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(3))
    def test_counter_alone_cannot_pass(self, now, pause):
        page = Mock(deadline=2)
        page.read.side_effect = [dict(hmi("1", 10), widgetType=None), dict(hmi("2", 10), widgetType="Wrong")]
        receipt = self.receipt()
        with self.assertRaises(TimeoutError):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["counterSamples"], [])

    def test_layout_asset_paths_select_the_reviewed_symbol_names(self):
        widget = dict(contentId="Proof", id="Asmb", assembly="mapp6D::Assembly_A", manager="mapp6D::Manager_A")
        self.assertEqual(layout_asset_paths([widget], "Proof_Asmb"), {
            "assembly": "/FileDevice:6DData/Assembly_A.svg",
            "trafficManager": "/FileDevice:6DData/Manager_A.svg",
        })
        no_manager = dict(widget, manager="")
        self.assertEqual(layout_asset_paths([no_manager], "Proof_Asmb"), {
            "assembly": "/FileDevice:6DData/Assembly_A.svg",
        })
        for assembly in ("../other", "mapp6D::A/B", None):
            with self.subTest(assembly=assembly):
                self.assertEqual(layout_asset_paths([dict(widget, assembly=assembly)], "Proof_Asmb"), {})

    @patch("br_automation_studio.browser_smoke.loopback_get")
    def test_capture_keeps_exact_svg_bytes_after_both_assets_pass(self, fetch):
        paths = {"assembly": "/FileDevice:6DData/A.svg", "trafficManager": "/FileDevice:6DData/T.svg"}
        assembly = b'<svg xmlns="http://www.w3.org/2000/svg" id="A"/>'
        traffic = b'<svg xmlns="http://www.w3.org/2000/svg" id="T"/>'
        responses = [(200, "image/svg+xml", assembly), (200, "image/svg+xml; charset=utf-8", traffic)]
        failures = [
            ("wrong mime type", [(200, "text/html", traffic)], None, "image/svg"),
            ("invalid XML", [(200, "image/svg+xml", b"<svg")], None, "Invalid layout SVG"),
            ("wrong root", [(200, "image/svg+xml", b"<svg/>")], None, "namespaced SVG"),
            ("listener changed", responses[1:], ValueError("original listener changed"), "original listener"),
        ]
        for label, reply, guard_error, message in failures:
            with self.subTest(label=label), TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH")) as scratch:
                fetch.side_effect = responses[:1] + reply
                guard = Mock(side_effect=[None, None, None, guard_error])
                root = Path(scratch)
                with self.assertRaisesRegex(ValueError, message):
                    capture_layout_assets(root, 18081, paths, guard)
                self.assertEqual(list(root.iterdir()), [])

        with TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH")) as scratch:
            fetch.reset_mock()
            fetch.side_effect = responses
            guard = Mock()
            root = Path(scratch)
            capture = capture_layout_assets(root, 18081, paths, guard)
            self.assertEqual(guard.call_count, 5)
            self.assertEqual([call.args for call in fetch.call_args_list], [(18081, path) for path in paths.values()])
            self.assertEqual(capture["assembly"]["sha256"], sha256(assembly).hexdigest())
            self.assertEqual((root / capture["assembly"]["file"]).read_bytes(), assembly)
            self.assertEqual((root / capture["trafficManager"]["file"]).read_bytes(), traffic)
            self.assertEqual(list(root.iterdir()), [root / "layout-assets"])

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", return_value=0)
    def test_required_layout_waits_for_both_documents_without_extending_counter_sample_sets(self, now, pause):
        paths = {"assembly": "/FileDevice:6DData/A.svg", "trafficManager": "/FileDevice:6DData/T.svg"}

        def seen(value, origin, assembly, manager, bounds=0):
            return dict(hmi(value, origin), layout={"boundedSvgCount": bounds,
                                                  "statuses": {"assembly": assembly, "trafficManager": manager}})

        page = Mock(deadline=2)
        page.read.side_effect = [
            seen("1", 10, None, None), seen("2", 10, 200, None, 1), seen("3", 10, 200, None, 1),
            seen("4", 10, 200, 200, 1), {"url": "about:blank", "timeOrigin": 11},
            seen("5", 12, 200, 200, 0), seen("6", 12, 200, 200, 0),
            seen("7", 12, 304, 200, 1),
        ]
        receipt = dict(self.receipt(), requireLayoutAssets=True, layoutPaths=paths)
        verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["counterSamples"], ["1", "2", "3"])
        self.assertEqual(receipt["reopened"]["counterSamples"], ["5", "6"])
        self.assertEqual(receipt["initial"]["layout"]["state"], "observed")
        layout = receipt["reopened"]["layout"]
        self.assertEqual(layout["state"], "observed")
        self.assertEqual(layout["assets"]["assembly"], {"path": paths["assembly"], "httpStatus": 304})

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(4))
    def test_required_layout_fails_after_counter_passes_if_overlay_request_fails(self, now, pause):
        layout = {"boundedSvgCount": 1, "statuses": {"assembly": 200, "trafficManager": 404}}
        page = Mock(deadline=3)
        page.read.side_effect = [dict(hmi(str(x), 10), layout=layout) for x in range(1, 4)]
        receipt = dict(self.receipt(), requireLayoutAssets=True,
                       layoutPaths={"assembly": "/FileDevice:6DData/A.svg", "trafficManager": "/FileDevice:6DData/T.svg"})
        with self.assertRaisesRegex(TimeoutError, "6DData SVG requests"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["layout"]["state"], "request_failed")
        self.assertEqual(receipt["initial"]["counterSamples"], ["1", "2", "3"])

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", return_value=0)
    def test_required_bundle_is_observed_in_each_document(self, now, pause):
        page = Mock(deadline=5)
        page.read.side_effect = [
            hmi("1", 10), hmi("2", 10), hmi("3", 10),
            dict(hmi("4", 10), widgetBundle={"httpStatus": 200}),
            {"url": "about:blank", "timeOrigin": 11},
            hmi("5", 12), dict(hmi("6", 12), widgetBundle={"httpStatus": 304}),
        ]
        receipt = dict(self.receipt(), requireWidgetBundle=True)
        verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["counterSamples"], ["1", "2", "3"])
        self.assertEqual(receipt["initial"]["widgetBundle"], {
            "state": "observed", "path": WIDGET_BUNDLE_PATH, "httpStatus": 200,
        })
        self.assertEqual(receipt["reopened"]["widgetBundle"], {
            "state": "observed", "path": WIDGET_BUNDLE_PATH, "httpStatus": 304,
        })

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_old_document_bundle_cannot_supply_reopened_evidence(self, now, pause):
        page = Mock(deadline=7)
        loaded = {"httpStatus": 200}
        page.read.side_effect = [
            *(dict(hmi(str(value), 10), widgetBundle=loaded) for value in (1, 2, 3)),
            {"url": "about:blank", "timeOrigin": 11},
            dict(hmi("100", 10), widgetBundle=loaded),
            hmi("4", 12), dict(hmi("5", 12), widgetBundle={"httpStatus": 404}),
        ]
        receipt = dict(self.receipt(), requireWidgetBundle=True)
        with self.assertRaisesRegex(TimeoutError, "same-origin mapp View bundle"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["state"], "reopened_document")
        self.assertEqual(receipt["reopened"]["counterSamples"], ["4", "5"])
        self.assertEqual(receipt["reopened"]["widgetBundle"]["state"], "request_failed")

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_start_page_map_must_be_seen_in_the_reopened_document(self, now, pause):
        page = Mock(deadline=7)
        loaded = {"httpStatus": 200}
        page.read.side_effect = [
            *(dict(hmi(str(value), 10), startPageMap=loaded, widgetBundle=loaded) for value in (1, 2, 3)),
            {"url": "about:blank", "timeOrigin": 11},
            dict(hmi("100", 10), startPageMap=loaded, widgetBundle=loaded),
            dict(hmi("4", 12), widgetBundle=loaded),
            dict(hmi("5", 12), widgetBundle=loaded),
        ]
        receipt = dict(self.receipt(), requireWidgetBundle=True, startPageMapPath="/demopanel.json")
        with self.assertRaisesRegex(TimeoutError, "same-origin mapp View start page map"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["startPageMap"], {
            "state": "observed", "path": "/demopanel.json", "httpStatus": 200,
        })
        self.assertEqual(receipt["reopened"]["counterSamples"], ["4", "5"])
        self.assertEqual(receipt["reopened"]["startPageMap"]["state"], "unverified")
        self.assertEqual(receipt["reopened"]["widgetBundle"]["state"], "observed")

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_traffic_details_content_must_be_loaded_by_the_reopened_document(self, now, pause):
        page = Mock(deadline=7)
        loaded = {"httpStatus": 200}
        page.read.side_effect = [
            *(dict(hmi(str(value), 10), startPageMap=loaded, startPageContent=loaded) for value in (1, 2, 3)),
            {"url": "about:blank", "timeOrigin": 11},
            dict(hmi("100", 10), startPageMap=loaded, startPageContent=loaded),
            dict(hmi("4", 12), startPageMap=loaded), dict(hmi("5", 12), startPageMap=loaded),
        ]
        content = "/contents/mappView/Proof.html"
        receipt = dict(self.receipt(), startPageMapPath="/demo.json", startPageContentPath=content)
        with self.assertRaisesRegex(TimeoutError, "same-origin mapp View start page content"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["startPageContent"], {
            "state": "observed", "path": content, "httpStatus": 200,
        })
        self.assertEqual(receipt["reopened"]["startPageContent"]["state"], "unverified")
        self.assertEqual(receipt["reopened"]["startPageMap"]["state"], "observed")
        self.assertEqual(receipt["reopened"]["counterSamples"], ["4", "5"])

    def test_select_reviewed_assembly_id(self):
        widgets = [{"contentId": "Proof", "id": "Asmb"}, {"contentId": "Other", "id": "Asmb"}]
        self.assertEqual(assembly_dom_id(widgets[:1], None, "Proof_Count"), "Proof_Asmb")
        self.assertEqual(assembly_dom_id(widgets, "Other_Asmb", "Other_Count"), "Other_Asmb")
        for selected, counter in ((None, "Proof_Count"), ("Missing_Asmb", "Proof_Count"), ("Other_Asmb", "Other_Asmb")):
            with self.subTest(selected=selected, counter=counter), self.assertRaises(ValueError):
                assembly_dom_id(widgets, selected, counter)

    def test_traffic_labels_come_from_the_linked_manager_and_assembly(self):
        manager = {"manager": "Traffic", "assembly": "Assembly",
                   "objects": [{"name": "S1", "type": "Station"}, {"name": "G1", "type": "Waypoint"}]}
        start = {"compiled": {"manager": "mapp6D::Traffic", "assembly": "Assembly"},
                 "trafficDetails": {"state": "matched"}}
        self.assertEqual(expected_traffic_labels({"trafficManagers": [manager]}, start), ["G1", "S1"])
        for linked in ([], [manager, manager], [dict(manager, assembly="Other")]):
            with self.subTest(linked=linked), self.assertRaisesRegex(ValueError, "one reviewed manager"):
                expected_traffic_labels({"trafficManagers": linked}, start)

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_reopened_traffic_labels_require_unique_visible_matching_text(self, now, pause):
        label = {"count": 1, "visible": True, "text": "S1", "widthCssPx": 8, "heightCssPx": 4}
        paths = {"trafficManager": "/FileDevice:6DData/T.svg"}
        layout = {"boundedSvgCount": 1, "statuses": {"trafficManager": 200}}

        def seen(value, origin, row=label):
            return dict(hmi(value, origin), layout=layout, trafficLabels={"labels": {"S1": row}})

        page = Mock(deadline=7)
        page.read.side_effect = [
            seen("1", 10), seen("2", 10), seen("3", 10),
            {"url": "about:blank", "timeOrigin": 11},
            seen("100", 10), seen("4", 12, dict(label, count=2)), seen("5", 12, dict(label, visible=False)),
        ]
        receipt = dict(self.receipt(), expectedTrafficLabels=["S1"], requireLayoutAssets=True, layoutPaths=paths)
        with self.assertRaisesRegex(TimeoutError, "station and waypoint labels"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["trafficLabels"]["state"], "observed")
        self.assertEqual(receipt["reopened"]["layout"]["state"], "observed")
        reopened = receipt["reopened"]["trafficLabels"]
        self.assertEqual(reopened["state"], "unverified")
        self.assertEqual(reopened["labels"], {"S1": dict(label, visible=False)})
        self.assertIsNone(reopened["minObservedHeightCssPx"])

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_reopened_label_height_checked_at_full_precision(self, now, pause):
        viewport = {"widthCssPx": 800, "heightCssPx": 600, "devicePixelRatio": 1}
        good = {"count": 1, "visible": True, "text": "S1", "widthCssPx": 12, "heightCssPx": 5}
        short = dict(good, heightCssPx=4.999)

        def seen(value, origin, row):
            return dict(hmi(value, origin), trafficLabels={"viewport": viewport, "labels": {"S1": row}})

        page = Mock(deadline=7)
        page.read.side_effect = [
            seen("1", 10, good), seen("2", 10, good), seen("3", 10, good),
            {"url": "about:blank", "timeOrigin": 11},
            seen("100", 10, good), seen("4", 12, short), seen("5", 12, short),
        ]
        receipt = dict(self.receipt(), expectedTrafficLabels=["S1"], minimumTrafficLabelHeightCssPx=5)
        with self.assertRaisesRegex(TimeoutError, "below 5 CSS px: S1"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(receipt["initial"]["trafficLabels"]["minObservedHeightCssPx"], 5)
        self.assertEqual(receipt["reopened"]["trafficLabels"], {
            "state": "below_minimum", "labels": {"S1": short}, "viewport": viewport,
            "minObservedHeightCssPx": 4.999, "belowMinimum": ["S1"],
        })

    @patch("br_automation_studio.browser_smoke.time.sleep")
    @patch("br_automation_studio.browser_smoke.time.monotonic", side_effect=range(8))
    def test_viewport_is_set_before_navigation_and_checked_in_the_reopened_document(self, now, pause):
        size = {"widthCssPx": 1280, "heightCssPx": 800}
        viewport = dict(size, devicePixelRatio=1.25)
        wrong = dict(viewport, heightCssPx=799)
        page = Mock(deadline=7)
        page.read.side_effect = [
            *(dict(hmi(str(value), 10), viewport=viewport) for value in (1, 2, 3)),
            {"url": "about:blank", "timeOrigin": 11},
            dict(hmi("100", 10), viewport=viewport),
            dict(hmi("4", 12), viewport=wrong), dict(hmi("5", 12), viewport=wrong),
        ]
        receipt = dict(self.receipt(), requestedViewport=size)
        with self.assertRaisesRegex(TimeoutError, "requested 1280 × 800 CSS px viewport"):
            verify_navigation(page, URL, receipt)
        self.assertEqual(page.method_calls[:2], [call.set_viewport(1280, 800), call.navigate(URL)])
        self.assertEqual(receipt["initial"]["viewport"], dict(viewport, state="matched"))
        self.assertEqual(receipt["reopened"]["viewport"], dict(wrong, state="mismatched"))
        self.assertEqual(receipt["reopened"]["counterSamples"], [])

    def test_viewport_override_uses_desktop_metrics_and_surfaces_rejections(self):
        page = Page(None, Mock(), 5)
        with patch.object(page, "request", side_effect=[{"result": {}}, {"error": {}}]) as request:
            page.set_viewport(800, 480)
            request.assert_called_once_with("Emulation.setDeviceMetricsOverride", {
                "width": 800, "height": 480, "deviceScaleFactor": 0, "mobile": False,
            })
            with self.assertRaisesRegex(ValueError, "apply the requested HMI viewport"):
                page.set_viewport(800, 480)

    @patch("br_automation_studio.operations.arsim_smoke", return_value={"diagnostics": []})
    def test_browser_request_accepts_positive_integer_label_height_without_explicit_label_check(self, smoke):
        arguments = {"project": "Demo.apj", "jobDirectory": "job", "pviTransfer": "pvi.exe",
                     "sessionDirectory": "session", "browserExecutable": "chrome.exe",
                     "counterWidgetId": "Count", "minimumTrafficLabelHeightCssPx": 5}
        request = {"schemaVersion": 1, "workspace": "test", "operation": "arsim_browser_smoke",
                   "arguments": arguments}
        self.assertEqual(execute(request, Path("."))["status"], "complete")
        self.assertEqual(smoke.call_args.args[-1].minimum_label_height, 5)
        self.assertTrue(smoke.call_args.kwargs["start_widget"].require_traffic_details)
        for rejected in (0, -1, True, 2.5):
            with self.subTest(value=rejected), self.assertRaisesRegex(InvalidRequest, "integer at least 1"):
                arguments["minimumTrafficLabelHeightCssPx"] = rejected
                execute(request, Path("."))
        self.assertEqual(smoke.call_count, 1)

    @patch("br_automation_studio.operations.arsim_smoke", return_value={"diagnostics": []})
    def test_browser_request_requires_both_viewport_dimensions(self, smoke):
        arguments = {"project": "Demo.apj", "jobDirectory": "job", "pviTransfer": "pvi.exe",
                     "sessionDirectory": "session", "browserExecutable": "chrome.exe",
                     "counterWidgetId": "Count", "viewportWidthCssPx": 1280}
        request = {"schemaVersion": 1, "workspace": "test", "operation": "arsim_browser_smoke",
                   "arguments": arguments}
        with self.assertRaisesRegex(InvalidRequest, "Supply viewportWidthCssPx and viewportHeightCssPx together"):
            execute(request, Path("."))
        arguments["viewportHeightCssPx"] = 800
        self.assertEqual(execute(request, Path("."))["status"], "complete")
        self.assertEqual(smoke.call_args.args[-1].viewport, (1280, 800))
        smoke.assert_called_once()

    @patch("br_automation_studio.browser_smoke.endpoints")
    def test_debugger_requires_owned_ipv4_loopback(self, endpoints):
        browser = SimpleNamespace(worker=Mock(), job=Mock())
        browser.worker.poll.return_value = None
        browser.job.pids.return_value = {101}
        owned = {"port": 62100, "address": "127.0.0.1", "pid": 101, "protocol": "tcp"}
        endpoints.return_value = [owned]
        self.assertEqual(debug_listener(browser, 62100), owned)
        for changed in (dict(owned, pid=102), dict(owned, address="0.0.0.0")):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "exclusively owned"):
                endpoints.return_value = [owned, changed]
                debug_listener(browser, 62100)

    @patch("br_automation_studio.browser_smoke.time.monotonic", return_value=0)
    def test_navigation_rejects_failed_and_same_document_loads(self, now):
        frames = Mock()
        frames.recv.side_effect = [
            json.dumps({"id": 1, "result": {"loaderId": "new", "errorText": "net::ERR_CONNECTION_REFUSED"}}),
            json.dumps({"id": 2, "result": {"frameId": "frame"}}),
        ]
        page = Page(frames, Mock(), 5)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "could not load"):
                page.navigate(URL)


if __name__ == "__main__":
    unittest.main()

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from br_automation_studio.browser_smoke import (
    ASSEMBLY_WIDGET, Page, assembly_dom_id, debug_listener, layout_asset_paths, verify_navigation,
)

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
        page.read.side_effect = [
            hmi("1.0", 10), hmi("1.00", 10), hmi("2", 10), hmi("3.0", 10),
            hmi("3.0", 10), {"url": "about:blank", "timeOrigin": 15},
            hmi("99", 10), hmi("0.0", 20), hmi("3.00", 20), hmi("4.0", 20), hmi("5.0", 20),
        ]
        receipt = self.receipt()
        verify_navigation(page, URL, receipt)
        self.assertEqual([item.args[0] for item in page.navigate.call_args_list], [URL, "about:blank", URL])
        self.assertEqual(receipt["initial"], {"counterSamples": ["1.0", "2", "3.0"], "timeOrigin": 10,
                                              "widgetType": ASSEMBLY_WIDGET})
        self.assertEqual(receipt["blankTimeOrigin"], 15)
        self.assertEqual(receipt["reopened"]["timeOrigin"], 20)
        self.assertEqual(receipt["reopened"]["counterSamples"], ["4.0", "5.0"])

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

    def test_select_reviewed_assembly_id(self):
        widgets = [{"contentId": "Proof", "id": "Asmb"}, {"contentId": "Other", "id": "Asmb"}]
        self.assertEqual(assembly_dom_id(widgets[:1], None, "Proof_Count"), "Proof_Asmb")
        self.assertEqual(assembly_dom_id(widgets, "Other_Asmb", "Other_Count"), "Other_Asmb")
        for selected, counter in ((None, "Proof_Count"), ("Missing_Asmb", "Proof_Count"), ("Other_Asmb", "Other_Asmb")):
            with self.subTest(selected=selected, counter=counter), self.assertRaises(ValueError):
                assembly_dom_id(widgets, selected, counter)

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

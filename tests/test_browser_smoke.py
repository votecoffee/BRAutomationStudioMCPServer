import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from br_automation_studio.browser_smoke import ASSEMBLY_WIDGET, Page, assembly_dom_id, debug_listener, verify_navigation

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

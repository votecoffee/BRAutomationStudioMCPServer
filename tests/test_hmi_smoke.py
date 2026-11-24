import unittest
from unittest.mock import patch

from br_automation_studio.binding_review import BindingTarget
from br_automation_studio.hmi_smoke import HmiProbe, arsim_hmi_smoke, check_samples, read_status_bindings


class HmiSmokeTest(unittest.TestCase):
    def setUp(self):
        self.before = {"ownedListener": {"pid": 101, "port": 14840}, "nodeId": "ns=6;s=::Pulse:Count",
                       "variantType": "Int32", "samples": [{"value": 1}, {"value": 2}, {"value": 3}]}
        self.after = dict(self.before, samples=[{"value": 10}, {"value": 11}, {"value": 12}])
        self.browser = {"initial": {"counterSamples": ["3.0", "4.0", "5.00"]},
                        "reopened": {"counterSamples": ["9.0", "10.0"]}}

    def test_browser_samples_accept_inclusive_direct_bounds(self):
        comparison = {}
        check_samples(self.before, self.browser, self.after, "Int32", comparison)
        self.assertEqual(comparison, {"directBeforeLast": 3, "browserFirst": "3.0", "browserLast": "10.0",
                                      "directAfterFirst": 10, "variantType": "Int32"})

    def test_stale_out_of_range_or_fractional_display_fails(self):
        for first in ("2.0", "3.5", "11.0"):
            with self.subTest(first=first), self.assertRaisesRegex(ValueError, "displayed integer samples"):
                self.browser["initial"]["counterSamples"][0] = first
                check_samples(self.before, self.browser, self.after, "Int32", {})

    def test_different_runtime_node_listener_or_type_fails(self):
        for change, reason in (({"nodeId": "ns=7;s=::Pulse:Count"}, "same owned OPC UA listener and node"),
                               ({"ownedListener": {"pid": 102, "port": 14840}}, "same owned OPC UA listener and node"),
                               ({"variantType": "UInt32"}, "compiled node type")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, reason):
                check_samples(self.before, self.browser, self.after | change, "Int32", {})

    def test_binding_and_runtime_reviews_must_be_the_same_package(self):
        review = {"jobId": "job-1", "planSha256": "plan-1", "archive": {"sha256": "first"}}
        probe = HmiProbe({"rucReview": review}, None, 20)
        with self.assertRaisesRegex(ValueError, "same completed build and RUC"):
            probe.prepare(review | {"archive": {"sha256": "other"}}, {}, {})

    def test_status_smoke_keeps_an_explicit_role_with_its_reviewed_binding(self):
        target = BindingTarget("Default", "Main", "Heartbeat", "::Status:Heartbeat")
        first = {"widgetId": "Request", "stringIdentifier": "::Status:Request", "expectedValue": 7, "role": "requestId"}
        extra = {"widgetId": "Extra", "stringIdentifier": "::Status:Extra", "expectedValue": 7}
        document = {"schemaVersion": 1, "bindings": [first, extra]}
        with patch("br_automation_studio.hmi_smoke.read_json", return_value=document):
            bindings = read_status_bindings(None, target)
        self.assertEqual([binding.role for binding in bindings], ["requestId", None])

        def review(project, job, binding):
            return {"evidence": "ruc_binding_readback", "rucReview": {"archive": {"sha256": "a" * 64}}, "snapshot": {},
                    "bindingReview": {"domId": binding.dom_id, "stringIdentifier": binding.identifier}}

        def smoke(project, job, pvi, session, timeout, probe, **keywords):
            first, second = probe.status_bindings
            self.assertEqual((first["role"], first["bindingReview"]["stringIdentifier"]), ("requestId", "::Status:Request"))
            self.assertNotIn("role", second)
            self.assertFalse(probe.browser.check_assembly)
            self.assertIsNone(keywords["start_widget"])
            return {}

        with (patch("br_automation_studio.hmi_smoke.review_ruc", side_effect=review),
              patch("br_automation_studio.hmi_smoke.arsim_smoke", side_effect=smoke)):
            arsim_hmi_smoke(None, None, None, None, 30, target, None, None, 30, 20, status_bindings=bindings)

        for rejected in ("requestId", "station", None):
            with self.subTest(role=rejected):
                document["bindings"][1] = extra | {"role": rejected}
                with (patch("br_automation_studio.hmi_smoke.read_json", return_value=document),
                      self.assertRaisesRegex(ValueError, "held-status role")):
                    read_status_bindings(None, target)

    def test_traffic_label_check_requests_enabled_details_even_in_status_mode(self):
        target = BindingTarget("Default", "Main", "Heartbeat", "::Status:Heartbeat")
        document = {"schemaVersion": 1, "bindings": [
            {"widgetId": "Request", "stringIdentifier": "::Status:Request", "expectedValue": 7},
        ]}
        with patch("br_automation_studio.hmi_smoke.read_json", return_value=document):
            bindings = read_status_bindings(None, target)
        binding = {"evidence": "ruc_binding_readback", "rucReview": {"archive": {}}, "snapshot": {},
                   "bindingReview": {"domId": "Main_Request"}}
        for required, minimum, bounds, separation, gap, clearance, mapping_lines in (
            (True, None, False, False, None, None, False), (False, 5, False, False, None, None, False),
            (False, None, True, False, None, None, False), (False, None, False, True, None, None, False),
            (False, None, False, False, 5, None, False), (False, None, False, False, None, 10, False),
            (False, None, False, False, None, None, True),
        ):
            with (self.subTest(required=required, minimum=minimum, bounds=bounds, separation=separation, gap=gap,
                               clearance=clearance, mapping_lines=mapping_lines),
                  patch("br_automation_studio.hmi_smoke.review_ruc", return_value=binding),
                  patch("br_automation_studio.hmi_smoke.arsim_smoke", return_value={}) as smoke):
                arsim_hmi_smoke(None, None, None, None, 30, target, None, None, 30, 20,
                                require_traffic_labels=required, minimum_label_height=minimum, require_label_bounds=bounds,
                                require_label_separation=separation, minimum_label_gap=gap,
                                require_traffic_mapping_lines=mapping_lines,
                                minimum_label_edge_clearance=clearance, status_bindings=bindings)
                browser = smoke.call_args.args[-1].browser
                self.assertTrue(browser.check_assembly)
                self.assertEqual(browser.require_traffic_labels, required)
                self.assertEqual(browser.require_traffic_mapping_lines, mapping_lines)
                self.assertEqual(browser.minimum_label_height, minimum)
                self.assertEqual(browser.require_label_bounds, bounds)
                self.assertEqual(browser.require_label_separation, separation)
                self.assertEqual(browser.minimum_label_gap, gap)
                self.assertEqual(browser.minimum_label_edge_clearance, clearance)
                self.assertTrue(smoke.call_args.kwargs["require_6d"])
                self.assertTrue(smoke.call_args.kwargs["start_widget"].require_traffic_details)


if __name__ == "__main__":
    unittest.main()

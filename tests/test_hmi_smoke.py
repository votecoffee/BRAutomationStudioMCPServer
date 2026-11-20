import unittest

from br_automation_studio.hmi_smoke import HmiProbe, check_samples


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


if __name__ == "__main__":
    unittest.main()

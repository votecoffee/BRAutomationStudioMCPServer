from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

from br_automation_studio.cli import markdown
from br_automation_studio.operations import execute


FIXTURE = Path(__file__).resolve().parent / "fixtures/Loop"


class LaunchPadRouteTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.project = self.root / "Loop"
        shutil.copytree(FIXTURE, self.project)
        self.lpj = self.root / "Route.lpj"
        self.lpj.write_bytes(b"synthetic opaque source for an independent decoder receipt")
        self.receipt = self.root / "Route.json"
        self.data = {
            "schemaVersion": 1,
            "source": {"fileName": self.lpj.name, "sha256": sha256(self.lpj.read_bytes()).hexdigest()},
            "codec": {"name": "zipson", "version": "0.2.12"},
            "decoded": {
                "launchpad_version": "1.4",
                "workstations": [{"id": "s", "name": "Load"}, {"id": "i", "name": "Inspect"}],
                "gates": [{"id": "g", "name": "Transit"}, {"id": "unused", "name": "Recovery"}],
                "recipe_nodes": [
                    {"recipe_id": "rs", "id": "s", "name": "Load", "type_name": "workstation", "targets": ["rg"]},
                    {"recipe_id": "rg", "id": "g", "name": "Transit", "type_name": "gate", "targets": ["ri"]},
                    {"recipe_id": "ri", "id": "i", "name": "Inspect", "type_name": "workstation", "targets": ["end"]},
                    {"recipe_id": "end", "id": "s", "name": "recipe_loop_merge_node",
                     "type_name": "workstation", "targets": []},
                ],
            },
        }
        self.six_d = self.project / "Physical/ARsim/PC/mapp6D"

    def run_review(self, operation="launchpad_route_review", **arguments):
        self.receipt.write_text(json.dumps(self.data), encoding="utf-8")
        args = {"project": "Loop/Loop.apj", "managerId": "DemoTraffic",
                "launchPadProject": "Route.lpj", "launchPadReadback": "Route.json", **arguments}
        return execute({"schemaVersion": 1, "workspace": "route", "operation": operation,
                        "arguments": args}, self.root)

    def test_comparison_reads_geometry_ids_and_preserves_return_edge(self):
        response = self.run_review()
        check = response["result"]["routeReview"]
        self.assertEqual(response["status"], "complete", response)
        self.assertEqual(check["launchPad"]["unusedGeometry"], ["Recovery"])
        self.assertEqual(check["expectedEdges"][-1],
                         {"source": "Inspect", "sourceType": "Station", "destination": "Load",
                          "destinationType": "Station", "count": 1})
        self.assertEqual(check["missingEdges"], [])
        self.assertIn("Inspect (Station) → Load (Station)", markdown(response))

    def test_duplicate_connections_are_not_collapsed_to_a_set(self):
        path = self.six_d / "DemoMappings.trafficmappinglist"
        data = path.read_bytes()
        begin = data.index(b'<Group ID="TrafficMapping[3]">')
        end = data.index(b"</Element>", begin)
        duplicate = data[begin:end].replace(b"TrafficMapping[3]", b"TrafficMapping[4]")
        path.write_bytes(data[:end] + duplicate + data[end:])
        response = self.run_review()
        self.assertEqual(response["status"], "issues")
        check = response["result"]["routeReview"]
        self.assertEqual(check["missingEdges"], [])
        self.assertEqual(check["unexpectedEdges"][0]["count"], 1)
        self.assertEqual(check["unexpectedEdges"][0]["destination"], "Load")

    def test_rejects_stale_readback_when_source_changes(self):
        self.lpj.write_bytes(self.lpj.read_bytes() + b"changed")
        response = self.run_review()
        self.assertEqual(response["result"]["diagnostics"][0]["code"], "launchpad_baseline_invalid")
        self.assertIn("SHA-256 differs", response["result"]["diagnostics"][0]["message"])

    def test_rejects_wrong_or_branched_return_even_with_resolved_ids(self):
        nodes = self.data["decoded"]["recipe_nodes"]
        nodes[2]["targets"] = ["rg", "end"]
        response = self.run_review()
        self.assertIn("one successor", response["result"]["diagnostics"][0]["message"])
        nodes[2]["targets"] = ["rg"]
        response = self.run_review()
        self.assertIn("revisits", response["result"]["diagnostics"][0]["message"])

    def test_sparse_rename_and_collision(self):
        path = self.root / "names.json"
        path.write_text('{"Transfer":"Transit"}', encoding="utf-8")
        self.data["decoded"]["gates"][0]["name"] = "Transfer"
        response = self.run_review(nameMap=path.name)
        self.assertEqual(response["status"], "complete", response)
        self.assertEqual(response["result"]["routeReview"]["nameMap"]["sha256"], sha256(path.read_bytes()).hexdigest())
        self.assertIn("Transfer → Transit", markdown(response))
        path.write_text('{"Transfer":"Load"}', encoding="utf-8")
        response = self.run_review(nameMap=path.name)
        self.assertEqual(response["result"]["diagnostics"][0]["code"], "launchpad_name_map_invalid")

    def test_extra_unmapped_manager_object_requires_review(self):
        path = self.six_d / "DemoObjects.trafficobjectlist"
        data = path.read_bytes().replace(b"</Element>", b'''<Selector ID="TrafficObject[4]" Value="Waypoint">
          <Property ID="Name" Value="Spare" />
        </Selector></Element>''')
        path.write_bytes(data)
        response = self.run_review()
        self.assertEqual(response["status"], "partial", response)
        self.assertEqual(response["result"]["routeReview"]["state"], "review_required")
        self.assertEqual(response["result"]["routeReview"]["additionalObjects"], [{"name": "Spare", "type": "Waypoint"}])

    def prepare_geometry(self, transform, points=None):
        if points is None:
            points = {"Load": (0, 0), "Transit": (100, 0), "Inspect": (100, 200), "Recovery": (0, 0)}
        decoded = self.data["decoded"]
        for entry in decoded["workstations"] + decoded["gates"]:
            entry["x"], entry["y"] = points[entry["name"]]
        path = self.six_d / "DemoObjects.trafficobjectlist"
        tree = ET.parse(path)
        for selector in tree.findall("Element/Selector"):
            name = selector.find("Property[@ID='Name']").get("Value")
            xy = transform(*points[name])
            pos = ET.SubElement(selector, "Selector", ID="Position", Value="Absolute")
            for axis, value in zip(("X", "Y"), xy):
                ET.SubElement(pos, "Property", ID=axis, Value=str(value / 1000))
        tree.write(path, encoding="utf-8", xml_declaration=True)

    def geometry(self, **arguments):
        return self.run_review("launchpad_geometry_review", **arguments)

    def test_geometry_passes_a_rotation_and_translation(self):
        self.prepare_geometry(lambda x, y: (40-y, 30+x))
        response = self.geometry(distanceToleranceMicrometres=1, expectedHandedness="preserved")
        result = response["result"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        self.assertEqual(result["geometryReview"]["state"], "criteria_met")
        self.assertLess(result["geometryReview"]["maxAbsDeltaMm"], 1e-8)
        self.assertEqual(result["evidence"], "launchpad_geometry_saved")
        self.assertIn("Handedness **preserved**", markdown(response))

    def test_geometry_detects_reflection_when_all_distances_match(self):
        self.prepare_geometry(lambda x, y: (40+x, 30-y))
        response = self.geometry(distanceToleranceMicrometres=1, expectedHandedness="preserved")
        result = response["result"]
        self.assertEqual(response["status"], "issues")
        self.assertEqual(result["routeReview"]["state"], "matched")
        self.assertLess(result["geometryReview"]["maxAbsDeltaMm"], 1e-8)
        self.assertEqual(result["geometryReview"]["handedness"]["state"], "reversed")
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["launchpad_handedness_mismatch"])

    def test_geometry_reports_displacement_and_applies_only_a_supplied_tolerance(self):
        self.prepare_geometry(lambda x, y: (x, y + 15 if y else y))
        measured = self.geometry()
        result = measured["result"]
        self.assertEqual(measured["status"], "complete")
        self.assertEqual(result["geometryReview"]["state"], "measured")
        self.assertAlmostEqual(result["geometryReview"]["maxAbsDeltaMm"], 15)
        limited = self.geometry(distanceToleranceMicrometres=1000)
        self.assertEqual(limited["result"]["diagnostics"][0]["code"], "launchpad_distance_tolerance")

    def test_collinear_points_do_not_establish_handedness(self):
        points = {"Load": (0, 0), "Transit": (100, 0), "Inspect": (200, 0), "Recovery": (300, 0)}
        self.prepare_geometry(lambda x, y: (x, y), points)
        result = self.geometry(expectedHandedness="preserved")["result"]
        self.assertEqual(result["geometryReview"]["handedness"]["state"], "indeterminate")
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_handedness_mismatch")

    def test_missing_geometry_does_not_change_topology_evidence(self):
        self.prepare_geometry(lambda x, y: (x, y))
        del self.data["decoded"]["workstations"][0]["x"]
        self.assertEqual(self.run_review()["status"], "complete")
        result = self.geometry()["result"]
        self.assertEqual(result["routeReview"]["state"], "matched")
        self.assertEqual(result["geometryReview"]["state"], "unverified")
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_geometry_unverified")


if __name__ == "__main__":
    unittest.main()

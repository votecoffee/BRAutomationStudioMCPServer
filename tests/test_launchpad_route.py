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

    def prepare_layout(self, cells, masters=(0,), grid="0"):
        decoded = self.data["decoded"]
        decoded["segments"] = [{"id": f"s{index}", "name": f"Source{index}", "type": "S3-AS", "w": 240, "h": 240,
                                "x": -60.5 + 240 * index, "y": -60.5, "master": index == 0} for index in range(4)]
        path = self.six_d / "FixtureLayout.layout6d"
        tree = ET.parse(path)
        layout = tree.getroot().find("Element")
        layout.find("Property[@ID='GridSize']").set("Value", grid)
        former = layout.find("Group[@ID='Segments']")
        if former is not None:
            layout.remove(former)
        group = ET.SubElement(layout, "Group", ID="Segments")
        for index, (x, y) in enumerate(cells):
            row = ET.SubElement(group, "Group", ID=f"Segment[{index + 1}]")
            properties = {"Name": f"Target{index}", "TypeGrid240": "0", "X": str(x), "Y": str(y),
                          "IsMaster": str(index in masters).lower()}
            for key, value in properties.items():
                ET.SubElement(row, "Property", ID=key, Value=value)
        tree.write(path, encoding="utf-8", xml_declaration=True)

    def layout(self, **arguments):
        return self.run_review("launchpad_layout_review", **arguments)

    def test_layout_rotates_and_translates_without_hiding_ambiguous_axes(self):
        self.prepare_layout([(3, 5), (3, 6), (3, 7), (3, 8)])
        response = self.layout()
        result = response["result"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        layout = result["layoutReview"]
        self.assertEqual(layout["state"], "matched")
        self.assertEqual(layout["launchPad"]["masterPositionMm"], [-60.5, -60.5])
        self.assertEqual(layout["automationStudio"]["distancesFromMasterMm"], [0, 240, 480, 720])
        self.assertEqual(layout["compatibleAxisMappings"], ["-y, x", "y, x"])
        self.assertIn("0, 240, 480, 720", markdown(response))

    def test_equal_distances_from_master_do_not_hide_a_different_grid_shape(self):
        self.prepare_layout([(0, 0), (1, 0), (0, 2), (0, 3)])
        result = self.layout()["result"]
        self.assertEqual(result["routeReview"]["state"], "matched")
        layout = result["layoutReview"]
        self.assertEqual(layout["automationStudio"]["distancesFromMasterMm"], layout["launchPad"]["distancesFromMasterMm"])
        self.assertEqual(layout["compatibleAxisMappings"], [])
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_master_layout_mismatch")

    def test_wrong_master_changes_the_relative_layout(self):
        self.prepare_layout([(0, 0), (1, 0), (2, 0), (3, 0)], masters=(1,))
        result = self.layout()["result"]
        self.assertEqual(result["layoutReview"]["automationStudio"]["master"], "Target1")
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_master_layout_mismatch")

    def test_missing_segment_has_separate_count_diagnostic(self):
        self.prepare_layout([(0, 0), (1, 0), (2, 0)])
        result = self.layout()["result"]
        self.assertEqual(result["layoutReview"]["automationStudio"]["count"], 3)
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_segment_count_mismatch")

    def test_unsupported_grid_and_multiple_masters_are_unverified(self):
        cells = [(0, 0), (1, 0), (2, 0), (3, 0)]
        for grid, masters, reason in (("1", (0,), "240 mm grid"), ("0", (0, 1), "exactly one master")):
            with self.subTest(grid=grid, masters=masters):
                self.prepare_layout(cells, masters, grid)
                result = self.layout()["result"]
                self.assertEqual(result["layoutReview"]["state"], "unverified")
                self.assertIn(reason, result["diagnostics"][0]["message"])

    def test_layout_selection_refuses_multiple_assembly_references(self):
        self.prepare_layout([(0, 0), (1, 0), (2, 0), (3, 0)])
        path = self.six_d / "FixtureAssembly.assembly6d"
        data = path.read_bytes()
        marker = b'<Property ID="Layout6D[1]" Value="FixtureLayout" />'
        self.assertEqual(data.count(marker), 1)
        path.write_bytes(data.replace(marker, marker + marker.replace(b"Layout6D[1]", b"Layout6D[2]")))
        result = self.layout()["result"]
        self.assertEqual(result["layoutReview"]["state"], "unverified")
        self.assertIn("exactly one layout", result["diagnostics"][0]["message"])


    def frame(self, **arguments):
        return self.run_review("launchpad_frame_review", **arguments)

    def test_frame_fits_station_shift_under_grid_axes(self):
        self.prepare_geometry(lambda x, y: (40 - y, 30 + x))
        self.prepare_layout([(3, 5), (3, 6), (3, 7), (3, 8)])
        response = self.frame(positionToleranceMicrometres=1)
        result = response["result"]
        frame = result["frameReview"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        self.assertEqual(frame["state"], "criteria_met")
        self.assertEqual(frame["bestRmsAxes"], "-y, x")
        self.assertEqual(frame["withinToleranceAxes"], ["-y, x"])
        best, other = frame["candidates"]
        self.assertAlmostEqual(best["translationMm"][0], 40)
        self.assertAlmostEqual(best["translationMm"][1], 30)
        self.assertAlmostEqual(best["maxResidualMm"], 0)
        self.assertGreater(other["maxResidualMm"], 1)
        self.assertEqual(result["evidence"], "launchpad_frame_saved")
        self.assertIn("## Coordinate frame comparison", markdown(response))

    def test_frame_bounds_error_no_matter_where_origin_is_placed(self):
        self.prepare_geometry(lambda x, y: (x, y + 15 if y else y))
        self.prepare_layout([(0, 0), (1, 0), (2, 0), (3, 0)])
        response = self.frame(positionToleranceMicrometres=1000)
        result = response["result"]
        frame = result["frameReview"]
        self.assertEqual(response["status"], "issues")
        self.assertEqual(result["routeReview"]["state"], "matched")
        self.assertEqual(result["layoutReview"]["state"], "matched")
        self.assertEqual(frame["withinToleranceAxes"], [])
        self.assertAlmostEqual(frame["anyGridCompatibleTranslationLowerBoundMm"], 7.5)
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_frame_residual")
        self.assertIn("Every grid-compatible translation", result["diagnostics"][0]["message"])

        # A least-squares fit misses 8 mm here even though a 7.5 mm fit is possible.
        result = self.frame(positionToleranceMicrometres=8000)["result"]
        self.assertIn("No reported transform", result["diagnostics"][0]["message"])

    def test_frame_datum_binds_shift_and_has_source_identity(self):
        self.prepare_geometry(lambda x, y: (40 - y, 30 + x))
        self.prepare_layout([(3, 5), (3, 6), (3, 7), (3, 8)])
        identity = self.frame()["result"]["frameReview"]["inputIdentity"]
        datum = self.root / "datum.json"
        data = {"schemaVersion": 1, **identity, "label": "Fixture test point",
                "launchPadMm": [20, 10], "automationStudioMm": [30, 50]}
        datum.write_text(json.dumps(data), encoding="utf-8")
        frame = self.frame(datumFile=datum.name, positionToleranceMicrometres=1)["result"]["frameReview"]
        self.assertEqual(frame["translationBasis"], "supplied_datum")
        self.assertEqual(frame["withinToleranceAxes"], ["-y, x"])
        self.assertEqual(frame["datum"]["snapshot"]["sha256"], sha256(datum.read_bytes()).hexdigest())

        data["automationStudioMm"] = [30, 60]
        datum.write_text(json.dumps(data), encoding="utf-8")
        result = self.frame(datumFile=datum.name, positionToleranceMicrometres=1)["result"]
        self.assertEqual(result["frameReview"]["candidates"][0]["translationMm"], (40, 40))
        self.assertIn("No reported transform", result["diagnostics"][0]["message"])

        data["automationStudioSha256"] = "0" * 64
        datum.write_text(json.dumps(data), encoding="utf-8")
        result = self.frame(datumFile=datum.name)["result"]
        self.assertEqual(result["frameReview"]["state"], "issues")
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_frame_datum_invalid")

    def test_frame_requires_matching_linked_layout(self):
        self.prepare_geometry(lambda x, y: (x, y))
        self.prepare_layout([(0, 0), (1, 0), (2, 0), (4, 0)])
        result = self.frame()["result"]
        self.assertEqual(result["frameReview"]["state"], "not_checked")
        self.assertEqual(result["diagnostics"][0]["code"], "launchpad_master_layout_mismatch")


if __name__ == "__main__":
    unittest.main()

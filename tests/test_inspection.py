
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from br_automation_studio.inspection import inspect_project

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures/Loop"
MOTION = Path("Physical/ARsim/PC/mapp6D")
WIDGET = Path("Logical/mappView/Overview.content")


class ProjectInspectionTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.working = Path(scratch.name)
        self.project = self.working / "Loop"
        shutil.copytree(FIXTURE, self.project)

    def replace(self, relative, before, after):
        path = self.project / relative
        data = path.read_bytes()
        self.assertEqual(data.count(before), 1)
        path.write_bytes(data.replace(before, after))

    def inspect(self, **kwargs):
        return inspect_project(self.project / "Loop.apj", **kwargs)

    def test_package_inventory_links_and_source_preservation(self):
        before = {path.relative_to(self.project).as_posix(): path.read_bytes()
                  for path in self.project.rglob("*") if path.is_file()}
        result = self.inspect()
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(result["automationStudio"]["WorkingVersion"], "4.12")
        self.assertEqual(result["technologyPackages"][0]["subversions"]["MpTraffic"], "2.02.0")
        self.assertEqual(result["cpu"]["runtimeVersion"], "J4.93")
        self.assertEqual(len(result["motionModules"]), 6)
        edges = result["trafficManagers"][0]["edges"]
        self.assertEqual([(e["source"], e["destination"]) for e in edges],
                         [("Load", "Transit"), ("Transit", "Inspect"), ("Inspect", "Load")])
        self.assertEqual(result["widgets"][0]["manager"], "mapp6D::DemoTraffic")
        for entry in result["snapshot"]["files"]:
            self.assertEqual(entry["sha256"], sha256(before[entry["path"]]).hexdigest())
        self.assertEqual(len(result["snapshot"]["files"]), len(before))
        self.assertEqual(result["snapshot"], self.inspect()["snapshot"])
        for path, data in before.items():
            self.assertEqual((self.project / path).read_bytes(), data)

    def test_dangling_return_edge_keeps_the_partial_graph(self):
        self.replace(MOTION / "DemoMappings.trafficmappinglist",
                     b'ID="DestinationRef" Value="Load"', b'ID="DestinationRef" Value="Missing"')
        result = self.inspect()
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["invalid_mapping_endpoint"])
        self.assertEqual(result["trafficManagers"][0]["edges"][-1]["destination"], "Missing")

    def test_typed_endpoint_rejects_same_name_with_different_type(self):
        self.replace(MOTION / "DemoMappings.trafficmappinglist",
                     b'ID="To[1]" Value="Waypoint"', b'ID="To[1]" Value="Station"')
        result = self.inspect()
        self.assertTrue(any(d["code"] == "invalid_mapping_endpoint" for d in result["diagnostics"]))

    def test_widget_pair_must_have_the_same_resolved_assembly(self):
        self.replace(MOTION / "FixtureAssembly.assembly6d", b"</Configuration>",
                     b'<Element ID="Alternate" Type="assembly6d"><Group ID="Layout">'
                     b'<Property ID="Layout6D[1]" Value="FixtureLayout" /></Group></Element></Configuration>')
        self.replace(WIDGET, b'mpLink="FixtureAssembly"', b'mpLink="Alternate"')
        result = self.inspect()
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["widget_link_mismatch"])

    def test_multiple_configs_require_selection(self):
        self.replace("Physical/Physical.pkg", b"</Objects>",
                     b'<Object Type="Configuration">Other</Object></Objects>')
        result = self.inspect()
        self.assertIsNone(result["selectedConfiguration"])
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["selection_required"])
        self.assertEqual(self.inspect(configuration="arsIM")["selectedConfiguration"], "ARsim")

    def test_package_member_cannot_escape_the_project(self):
        self.replace("Physical/ARsim/PC/Cpu.pkg", b">mapp6D<", b">../../outside<")
        result = self.inspect()
        self.assertIn("invalid_member", [d["code"] for d in result["diagnostics"]])
        self.assertFalse(result["motionModules"])

    def test_xml_dtd_is_reported_without_resolution(self):
        self.replace("Loop.apj", b"<Project Version", b'<!DOCTYPE Project SYSTEM "missing.dtd">\n<Project Version')
        result = self.inspect()
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["invalid_xml"])
        self.assertIn("DOCTYPE", result["diagnostics"][0]["message"])

    def test_request_and_reports(self):
        argv = [sys.executable, "-B", "-m", "br_automation_studio"]
        help_result = subprocess.check_output([*argv, "operation-help", "project_inspect"])
        self.assertEqual(json.loads(help_result)["properties"]["operation"]["const"], "project_inspect")
        request = self.working / "inspection.json"
        request.write_text(json.dumps({
            "schemaVersion": 1, "workspace": "test", "operation": "project_inspect",
            "arguments": {"project": "Loop/Loop.apj"},
        }), encoding="utf-8")
        invocation = [*argv, "run", "--request", str(request)]
        payload = json.loads(subprocess.check_output(invocation))
        self.assertEqual(payload["status"], "complete")
        report = subprocess.check_output([*invocation, "--format", "markdown"]).decode("utf-8")
        self.assertIn("Inspect → Load", report)
        self.assertIn(payload["result"]["snapshot"]["sha256"], report)
        request.write_text('{"operation":"project_inspect","operation":"other"}', encoding="utf-8")
        invalid = subprocess.run(invocation, capture_output=True, check=False)
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("Duplicate JSON key", json.loads(invalid.stdout)["message"])


if __name__ == "__main__":
    unittest.main()

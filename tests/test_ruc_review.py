
from hashlib import file_digest
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from br_automation_studio.cli import markdown
from br_automation_studio.native_build import identity, source_tree
from br_automation_studio.operations import execute
from br_automation_studio.ruc_review import CONTENT, DATA, WIDGET_SCRIPT

FIXTURE = Path(__file__).resolve().parent / "fixtures/Loop"
RUC_PATH = "binary/ARsim/PC/RUCPackage/RUCPackage.zip"
MAPPING = f"{DATA}/DemoMappings.br"
WIDGET = f"{CONTENT}/DemoOverview.html"


class RucReviewTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.project = self.root / "Loop"
        self.job = self.root / "retained"
        self.archive = self.job / RUC_PATH
        shutil.copytree(FIXTURE, self.project)
        self.archive.parent.mkdir(parents=True)
        manifest = {
            "version": 1, "mode": "as4_arsim_ruc", "project": "Loop.apj",
            "configuration": "ARsim", "cpu": "PC", "inputs": source_tree(self.project),
        }
        self.plan = {"sha256": identity(manifest), "manifest": manifest}
        self.write("plan.json", self.plan)
        modules = self.project / "Physical/ARsim/PC/mapp6D"
        self.members = {
            f"{DATA}/{path.stem}.br": b"synthetic BR prefix\0" + path.read_bytes() + b"\0suffix"
            for path in modules.iterdir() if path.suffix != ".pkg"
        }
        self.members[WIDGET] = (
            b'<script>\n brease.setOptions("DemoOverview_LoopOverview", {'
            b'"className":"widgets/mapp6D/Assembly6D",'
            b'"mpLink":"FixtureAssembly","mpLinkTrafficManager":"mapp6D::DemoTraffic"});\n</script>'
        )
        self.members[WIDGET_SCRIPT] = b"synthetic widget script placeholder"

    def write(self, filename, data):
        path = self.job / filename
        path.write_text(json.dumps(data), encoding="utf-8", newline="\n")

    def package(self):
        with ZipFile(self.archive, "w") as package:
            for name, data in self.members.items():
                package.writestr(name, data)
            self.assertIsNone(package.testzip())
        with self.archive.open("rb") as stream:
            sha256 = file_digest(stream, "sha256").hexdigest()
        job = {
            "jobId": "synthetic-build", "planSha256": self.plan["sha256"],
            "status": "succeeded", "timedOut": False, "cleanup": "empty", "nativeExitCode": 1,
            "summary": {"errors": 0, "warnings": 1},
            "package": {"path": RUC_PATH, "sha256": sha256,
                        "bytes": self.archive.stat().st_size, "verifiedZip": True, "entries": len(self.members)},
        }
        self.write("job.json", job)
        return job

    def review(self):
        return execute({"schemaVersion": 1, "workspace": "readback", "operation": "ruc_review",
                        "arguments": {"project": "Loop/Loop.apj", "jobDirectory": "retained"}}, self.root)

    def codes(self, response):
        return [item["code"] for item in response["result"]["diagnostics"]]

    def test_readback_binds_saved_inputs_and_reviews_all_expected_elements(self):
        package = self.package()["package"]
        response = self.review()
        review = response["result"]["rucReview"]
        self.assertEqual(response["status"], "complete", response)
        self.assertEqual(review["archive"]["sha256"], package["sha256"])
        self.assertTrue(review["sourceMatched"])
        self.assertEqual(len(review["modules"]), 6)
        self.assertEqual(len(review["trafficManagers"][0]["edges"]), 3)
        self.assertEqual(review["widgets"][0]["manager"], "mapp6D::DemoTraffic")
        self.assertIn("Inspect (Station) → Load (Station)", markdown(response))
        self.assertIn("Builder errors 0 · Builder warnings 1", markdown(response))

    def test_changed_source_cannot_be_used_as_build_baseline(self):
        self.package()
        (self.project / "Logical/Extra.st").write_text("x := 1;", encoding="utf-8")
        response = self.review()
        self.assertEqual(self.codes(response), ["ruc_source_changed"])
        self.assertIsNone(response["result"]["rucReview"]["archive"])

    def test_other_zip_bytes_fail_hash_binding(self):
        self.package()
        with self.archive.open("ab") as stream:
            stream.write(b"unrecorded")
        response = self.review()
        self.assertEqual(self.codes(response), ["ruc_hash_mismatch"])

    def test_plan_manifest_drift_is_detected_before_source_review(self):
        self.package()
        self.plan["manifest"]["cpu"] = "OtherPC"
        self.write("plan.json", self.plan)
        response = self.review()
        self.assertEqual(self.codes(response), ["ruc_review_input"])
        self.assertFalse(response["result"]["rucReview"]["sourceMatched"])

    def test_broken_compiled_return_edge_survives_zip_checks_but_fails_review(self):
        self.members[MAPPING] = self.members[MAPPING].replace(
            b'ID="DestinationRef" Value="Load"', b'ID="DestinationRef" Value="Missing"')
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "issues")
        self.assertIn("invalid_mapping_endpoint", self.codes(response))
        self.assertIn("ruc_content_mismatch", self.codes(response))

    def test_duplicate_compiled_edges_are_counted(self):
        data = self.members[MAPPING]
        begin = data.index(b'<Group ID="TrafficMapping[3]">')
        end = data.index(b"</Element>", begin)
        self.members[MAPPING] = data[:end] + data[begin:end].replace(b"TrafficMapping[3]", b"TrafficMapping[4]") + data[end:]
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "issues")
        self.assertEqual(self.codes(response), ["ruc_content_mismatch"])

    def test_changed_widget_link_fails_after_a_valid_archive(self):
        self.members[WIDGET] = self.members[WIDGET].replace(b"mapp6D::DemoTraffic", b"mapp6D::Other")
        self.package()
        self.assertEqual(self.codes(self.review()), ["ruc_widget_mismatch"])

    def test_unreadable_motion_payload_is_partial_without_invented_routes(self):
        self.members[MAPPING] = b"opaque vendor module"
        self.package()
        response = self.review()
        self.assertEqual(response["status"], "partial")
        self.assertEqual(self.codes(response), ["ruc_configuration_unreadable"])
        self.assertEqual(response["result"]["rucReview"]["trafficManagers"], [])

    def test_duplicate_archive_member_cannot_be_selected_by_case(self):
        self.members[MAPPING.lower()] = self.members[MAPPING]
        self.package()
        self.assertEqual(self.codes(self.review()), ["duplicate_ruc_member"])

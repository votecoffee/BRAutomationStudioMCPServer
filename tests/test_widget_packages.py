from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from br_automation_studio.cli import markdown
from br_automation_studio.native_build import identity, source_tree
from br_automation_studio.operations import execute


WIDGETS = "Logical/mappView/Widgets"
LIBRARY = f"{WIDGETS}/DemoWidgets"


class WidgetPackageReviewTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.put("Demo.apj", "<Project/>")
        self.put("Logical/Package.pkg",
                 '<Package><Objects><Object Type="Package">mappView</Object></Objects></Package>')
        self.put("Logical/mappView/Package.pkg",
                 '<Package><Objects><Object Type="Package">Widgets</Object></Objects></Package>')
        self.put(f"{WIDGETS}/Package.pkg",
                 '<Package><Objects><Object Type="Package">DemoWidgets</Object></Objects></Package>')
        self.put(f"{LIBRARY}/Package.pkg", '<Package PackageType="WidgetLibrary"><Objects>'
                 '<Object Type="File">Description.widgetlibrary</Object></Objects></Package>')
        self.put(f"{LIBRARY}/Description.widgetlibrary", "<WidgetLibrary/>")

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8", newline="\n")

    def review(self, **arguments):
        return execute({"schemaVersion": 1, "operation": "widget_package_review", "workspace": "fixture",
                        "arguments": {"project": "Demo.apj", **arguments}}, self.root)

    def retained_build(self):
        self.put(f"{LIBRARY}/Assembly/Widget.js", "saved widget")
        inputs = source_tree(self.root)
        plan = {"version": 1, "mode": "as4_arsim_ruc", "project": "Demo.apj",
                "configuration": "ARsim", "cpu": "PC", "inputs": inputs}
        scratch = TemporaryDirectory(dir=self.root.parent, prefix="widget-build-")
        self.addCleanup(scratch.cleanup)
        retained = Path(scratch.name).resolve()
        log = retained / "builder.log"
        staged = r"W:\job\project" + "\\" + LIBRARY.replace("/", "\\")
        output = r"W:\job\temporary\Objects\ARsim\PC\.mappView\data\wwwRoot\BRVisu\widgets\DemoWidgets"
        log.write_text(
            f'{staged}\\Package.pkg: (Assembly) warning 9233:Additional directory "Assembly" found, '
            'which is not part of the project.\n'
            f"Copy directory '{staged}\\Assembly' to '{output}\\Assembly'.\n"
            f"Copy directory '{staged}\\Assembly' to '{output}\\Unrelated'.\n"
            'Build: 0 error(s), 1 warning(s)\n', encoding="utf-8")
        package = retained / "binary/ARsim/PC/RUCPackage/RUCPackage.zip"
        package.parent.mkdir(parents=True)
        web = "FilesToTransfer/AddonsData/IAT_Data/wwwRoot/BRVisu/widgets/DemoWidgets/Assembly"
        with ZipFile(package, "w") as archive:
            archive.writestr(f"{web}/Widget.js", b"saved widget")
            archive.writestr(f"{web}/Generated.css", b"generated stylesheet")
            archive.writestr(f"{web}-other/Unrelated.js", b"unrelated path")
        digest = identity(plan)
        (retained / "plan.json").write_text(json.dumps({"sha256": digest, "manifest": plan}), encoding="utf-8")
        job = {"jobId": "widget-fixture", "planSha256": digest, "status": "succeeded",
               "timedOut": False, "cleanup": "empty", "nativeExitCode": 1, "summary": {"errors": 0, "warnings": 1},
               "arguments": ["BR.AS.Build.exe", r"W:\job\project\Demo.apj", "-t", r"W:\job\temporary"],
               "log": {"path": "builder.log", "bytes": log.stat().st_size, "sha256": sha256(log.read_bytes()).hexdigest()},
               "package": {"path": package.relative_to(retained).as_posix(), "verifiedZip": True,
                           "sha256": sha256(package.read_bytes()).hexdigest(), "bytes": package.stat().st_size,
                           "entries": 3}}
        (retained / "job.json").write_text(json.dumps(job), encoding="utf-8")
        return retained, log, package

    def test_matches_log_line_and_archived_files_to_the_same_saved_entry(self):
        retained, _, _ = self.retained_build()
        outcome = self.review(jobDirectory=str(retained))
        review = outcome["result"]["widgetPackageReview"]
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(review["nativeBuild"]["state"], "verified")
        library, = review["libraries"]
        row, = library["extra"]
        proof = row["nativeEvidence"]
        self.assertEqual(proof["warnings"], [{"line": 1, "code": 9233}])
        self.assertEqual(proof["copies"], [{"line": 2}])
        assets = {Path(member["path"]).name: member for member in proof["members"]}
        self.assertEqual(set(assets), {"Widget.js", "Generated.css"})
        self.assertTrue(assets["Widget.js"]["savedBytesMatch"])
        self.assertIsNone(assets["Generated.css"]["savedBytesMatch"])
        self.assertIn("Copy steps 1 · Web assets 2 · Saved bytes match 1", markdown(outcome))

    def test_changed_saved_input_log_or_ruc_cannot_supply_build_evidence(self):
        retained, log, package = self.retained_build()
        widget = self.root / LIBRARY / "Assembly/Widget.js"
        for changed in (widget, log, package):
            with self.subTest(changed=changed.name):
                old = changed.read_bytes()
                changed.write_bytes(old + b"changed")
                try:
                    response = self.review(jobDirectory=str(retained))
                finally:
                    changed.write_bytes(old)
                result = response["result"]
                self.assertEqual(response["status"], "issues")
                self.assertEqual(result["widgetPackageReview"]["nativeBuild"]["state"], "unverified")
                self.assertEqual(result["diagnostics"][-1]["code"], "widget_build_evidence_unverified")
                self.assertNotIn("nativeEvidence", result["widgetPackageReview"]["libraries"][0]["extra"][0])

    def test_widget_inventory_finds_warning_candidates_at_the_immediate_level(self):
        self.put(f"{LIBRARY}/WidgetLibrary.mapping", "sample mapping")
        self.put(f"{LIBRARY}/Assembly/Assets/Widget.js", "sample script")
        original = source_tree(self.root)
        response = self.review()
        result = response["result"]
        review = result["widgetPackageReview"]
        library, = review["libraries"]
        self.assertEqual(response["status"], "partial")
        self.assertEqual(review["state"], "differences")
        self.assertEqual({item["name"]: item["builderWarning"] for item in library["extra"]},
                         {"WidgetLibrary.mapping": 9232, "Assembly": 9233})
        self.assertEqual(library["declared"][0]["observedKind"], "file")
        self.assertIn("Undeclared directory Assembly · AS warning 9233", markdown(response))
        self.assertEqual(source_tree(self.root), original)

    def test_inventory_covers_a_clean_library_and_a_wrong_member_type(self):
        good = self.review()["result"]["widgetPackageReview"]
        self.assertEqual(good["state"], "matched")
        self.put(f"{LIBRARY}/Package.pkg", '<Package SubType="WidgetLibrary"><Objects>'
                 '<Object Type="Package">Description.widgetlibrary</Object></Objects></Package>')
        response = self.review()
        result = response["result"]
        library, = result["widgetPackageReview"]["libraries"]
        self.assertEqual(library["missing"][0]["observedKind"], "file")
        self.assertEqual(library["missing"][0]["kind"], "directory")
        self.assertEqual([item["code"] for item in result["diagnostics"]], ["widget_library_member_missing"])

    def test_unregistered_or_unsupported_packages_do_not_imply_matching_inventory(self):
        self.put(f"{LIBRARY}/Package.pkg", '<Package SubType="Other"/>')
        result = self.review()["result"]
        self.assertEqual(result["widgetPackageReview"]["state"], "unverified")
        self.assertEqual([item["code"] for item in result["diagnostics"]], ["widget_library_type_unverified"])
        self.put("Logical/mappView/Package.pkg", "<Package/>")
        result = self.review()["result"]
        self.assertEqual(result["widgetPackageReview"]["state"], "not_registered")
        self.assertEqual(result["diagnostics"], [])

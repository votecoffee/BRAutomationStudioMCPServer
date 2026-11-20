import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from br_automation_studio.cli import markdown
from br_automation_studio.native_build import source_tree
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

    def review(self):
        return execute({"schemaVersion": 1, "operation": "widget_package_review", "workspace": "fixture",
                        "arguments": {"project": "Demo.apj"}}, self.root)

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

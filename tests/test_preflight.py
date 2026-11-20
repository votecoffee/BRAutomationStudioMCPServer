
from pathlib import Path
import os
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from br_automation_studio.operations import execute

FIXTURE = Path(__file__).resolve().parent / "fixtures/Loop"
PACKAGES = Path("AS/TechnologyPackages")
SIX_D_MANIFEST = PACKAGES / "mapp6D/2.2.0/ApplicationSettings/TP.cfg"


def prop(name, value):
    return f'<Property ID="{name}" Value="{value}" />'


def group(name, **values):
    return f'<Group ID="{name}">' + "".join(prop(key, value) for key, value in values.items()) + "</Group>"


class BuildPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "Loop"
        shutil.copytree(FIXTURE, self.project)
        self.studio = self.root / "Install/AS412"
        self.runtime = self.studio.parent / "AS/System"
        self.put(self.studio / "bin-en/BR.AS.Build.exe", "synthetic builder marker")
        self.put(self.studio / "bin-en/BR.AS.BuildBase.dll", "synthetic version resource marker")
        self.put(self.studio / "AS/Hardware/Modules/PC_any/1.1.1.0/PC_any.hwx", '<Module Version="1.1.1.0" />')
        self.put(self.runtime / "J0493/SG4/Config.xml", "<Configuration />")
        self.manifest("mapp6D", "2.2.0", "2.2.0.9", self.six_d_components(), "4.12")
        self.manifest("mappView", "5.24.5", "5.24.5.104",
                      '<Group ID="SubVersions">' + group("mvLoader", Type="Module", FromVersion="1.0.0") + "</Group>", "4.9")
        for package, component, version in (("mapp6D/2.2.0", "McAcp6DSys", "2.02.0"),
                                             ("mappView/5.24.5", "mvLoader", "1.0.0")):
            self.put(self.studio / PACKAGES / package / f"Modules/{component}/V{version}/SG4/IA32/{component}.br", "marker")
        for lib in ("McAcp6D", "MpTraffic"):
            descriptor = PACKAGES / f"mapp6D/2.2.0/Library/{lib}/V2.02.0/binary.lby"
            self.put(self.studio / descriptor, self.library("2.02.0", "McBase"))
        patch_version = patch("br_automation_studio.preflight.product_version", return_value="AS4.12.10.39")
        patch_version.start()
        self.addCleanup(patch_version.stop)

    @staticmethod
    def six_d_components():
        return '<Group ID="SubVersions">' + "".join(
            group(name, Type=kind, FromVersion="2.02.0", ToVersion="2.02.9", IsMandatory="true")
            for name, kind in (("McAcp6D", "Library"), ("MpTraffic", "Library"), ("McAcp6DSys", "Module"))
        ) + "</Group>"

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    @staticmethod
    def library(version, dependency=None):
        child = (f'<Dependencies><Dependency ObjectName="{dependency}" FromVersion="5.26.0" /></Dependencies>'
                 if dependency else "")
        return ('<?xml version="1.0" encoding="utf-8"?>\n<?AutomationStudio Version=3.0.0.0?>\n'
                f'<Library Version="{version}">{child}</Library>')

    def manifest(self, name, version, internal, extra, minimum):
        dependency = group("Dependency1", VersionType="AutomationStudio", FromVersion=minimum)
        content = ('<Configuration><Element Type="TechnologyPackage">'
                   + prop("InternalName", name) + prop("Version", version) + prop("InternalVersion", internal)
                   + dependency + extra + "</Element></Configuration>")
        self.put(self.studio / PACKAGES / name / version / "ApplicationSettings/TP.cfg", content)

    def local_core(self, version, register=True):
        self.put(self.project / "Logical/Libraries/McBase/binary.lby", self.library(version))
        if register:
            self.put(self.project / "Physical/ARsim/PC/Cpu.sw",
                     '<SwConfiguration><Libraries><LibraryObject Name="McBase" '
                     'Source="Libraries.McBase.lby" /></Libraries></SwConfiguration>')

    def inspect(self, **overrides):
        arguments = {"project": "Loop/Loop.apj", "studioRoot": "Install/AS412", **overrides}
        return execute({"schemaVersion": 1, "workspace": "preflight",
                        "operation": "build_preflight", "arguments": arguments}, self.root)

    def test_matches_exact_installed_selection_and_registered_core_library(self):
        self.local_core("5.29.1")
        original = (self.project / "Loop.apj").read_bytes()
        response = self.inspect()
        result = response["result"]
        evidence = result["preflight"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        self.assertEqual(evidence["buildTools"][0]["studioVersion"], "4.12.10.39")
        self.assertEqual(evidence["runtime"]["manifest"], "J0493/SG4/Config.xml")
        self.assertEqual(evidence["cpuCatalog"][0]["version"], "1.1.1.0")
        self.assertEqual(evidence["technologyPackages"][0]["internalVersion"], "2.2.0.9")
        self.assertEqual(len(evidence["libraryDependencies"]), 2)
        self.assertIn("Logical/Libraries/McBase/binary.lby", {row["path"] for row in result["snapshot"]["files"]})
        self.assertEqual((self.project / "Loop.apj").read_bytes(), original)

    def test_missing_exact_version_identifies_other_catalog_versions(self):
        apj = self.project / "Loop.apj"
        apj.write_bytes(apj.read_bytes().replace(b'mapp6D Version="2.2.0"', b'mapp6D Version="2.4.1"'))
        response = self.inspect()
        self.assertEqual(response["status"], "issues")
        result = response["result"]
        package = result["preflight"]["technologyPackages"][0]
        self.assertEqual(package["catalogDirectories"], ["2.2.0"])
        self.assertIsNone(package["internalVersion"])
        self.assertIn("package_version_missing", {item["code"] for item in result["diagnostics"]})

    def test_unregistered_library_does_not_satisfy_dependency(self):
        self.local_core("5.29.1", register=False)
        issues = self.inspect()["result"]["diagnostics"]
        self.assertEqual(sum(item["code"] == "dependency_not_selected" for item in issues), 2)

    def test_old_core_and_package_lockout_are_reported(self):
        self.local_core("5.25.10")
        lockout = group("Lockout1", VersionType="mappView", ToVersion="5.24.5")
        self.manifest("mapp6D", "2.2.0", "2.2.0.9", self.six_d_components() + lockout, "4.9.2")
        issues = self.inspect()["result"]["diagnostics"]
        codes = [item["code"] for item in issues]
        self.assertEqual(codes.count("version_out_of_range"), 2)
        self.assertIn("package_lockout", codes)

    def test_runtime_root_override_reports_absent_runtime_manifest(self):
        self.local_core("5.29.1")
        result = self.inspect(runtimeRoot="DifferentRuntimeCatalog")["result"]
        self.assertIsNone(result["preflight"]["runtime"]["manifest"])
        issues = [item for item in result["diagnostics"] if item["scope"] == "runtime"]
        self.assertEqual(issues[0]["path"], "J0493/SG4/Config.xml")
        self.assertEqual(issues[0]["code"], "missing_file")

    def test_present_manifest_must_identify_the_selected_version(self):
        self.local_core("5.29.1")
        path = self.studio / SIX_D_MANIFEST
        path.write_bytes(path.read_bytes().replace(b'ID="Version" Value="2.2.0"', b'ID="Version" Value="2.4.1"'))
        result = self.inspect()["result"]
        self.assertIn("package_identity_mismatch", {item["code"] for item in result["diagnostics"]})

    def test_transfer_payload_presence_does_not_prove_a_version(self):
        self.local_core("5.29.1")
        transfer = group("Transfer", Module_mvLoader="mvLoader")
        self.manifest("mappView", "5.24.5", "5.24.5.104", transfer, "4.9")
        payload = PACKAGES / "mappView/5.24.5/Transfer/Modules/SG4/IA32/mvLoader.br"
        self.put(self.studio / payload, "marker")
        response = self.inspect()
        self.assertEqual(response["status"], "partial")
        view = response["result"]["preflight"]["technologyPackages"][1]
        self.assertEqual(view["components"][0]["payloadCandidates"], [payload.as_posix()])
        self.assertIsNone(view["components"][0]["source"])

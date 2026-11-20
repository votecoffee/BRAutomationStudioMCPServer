
from hashlib import sha256
import json
from pathlib import Path, PureWindowsPath
import os
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from br_automation_studio.cli import markdown
from br_automation_studio.native_build import execute_build, stage_inputs
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

    def native_request(self, operation, **overrides):
        arguments = {"project": "Loop/Loop.apj", "studioRoot": "Install/AS412", **overrides}
        return execute({"schemaVersion": 1, "workspace": "native",
                        "operation": operation, "arguments": arguments}, self.root)

    def test_native_plan_binds_non_xml_source_and_rejects_reuse(self):
        self.local_core("5.29.1")
        extra = self.project / "Logical/Code.st"
        self.put(extra, "x := 1;")
        old = self.native_request("build_plan")["result"]["buildPlan"]
        self.put(extra, "x := 2;")
        with patch("br_automation_studio.native_build.run_build") as worker:
            response = self.native_request("build_execute", planSha256=old["sha256"], jobDirectory="job1")
            worker.assert_not_called()
        self.assertEqual(response["status"], "issues")
        self.assertIn("build_plan_changed", [item["code"] for item in response["result"]["diagnostics"]])
        self.assertFalse((self.root / "job1").exists())

    def test_native_result_retains_warning_and_ruc(self):
        self.local_core("5.29.1")
        self.put(self.project / "BuildOnly/old.txt", "previous output")
        plan = self.native_request("build_plan")["result"]["buildPlan"]

        def worker(args, project, dispatch, log, timeout):
            self.assertNotEqual(project, self.project)
            self.assertFalse((project / "BuildOnly/old.txt").exists())
            self.assertIn("-simulation", args)
            self.assertIn("-buildRUCPackage", args)
            self.assertEqual(timeout, 12)
            dispatch(42)
            file = Path(args[args.index("-o") + 1]) / "ARsim/PC/RUCPackage/RUCPackage.zip"
            file.parent.mkdir(parents=True)
            with ZipFile(file, "w") as archive:
                archive.writestr("FilesToTransfer/Data0/Test.br", "native package placeholder")
            log.write(b"Build: 0 error(s), 1 warning(s)\r\n")
            return 1, False, "empty"

        with patch("br_automation_studio.native_build.run_build", side_effect=worker) as launch:
            response = self.native_request("build_execute", planSha256=plan["sha256"],
                                           jobDirectory="job1", timeoutSeconds=12)
            repeated = self.native_request("build_execute", planSha256=plan["sha256"], jobDirectory="job1")
            self.assertEqual(repeated["status"], "issues")
            launch.assert_called_once()
        self.assertEqual(response["status"], "partial")
        result = response["result"]["nativeBuild"]
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["package"]["verifiedZip"])
        self.assertEqual(json.loads((self.root / "job1/job.json").read_bytes()), result)
        self.assertEqual((self.project / "BuildOnly/old.txt").read_text(encoding="utf-8"), "previous output")

    def test_mptraffic_read_set_is_part_of_the_native_plan_identity(self):
        _, catalog, enum = self.prepare_mptraffic_definition()
        plan = self.native_request("build_plan", verifyMpTrafficEnum=True)["result"]["buildPlan"]
        evidence = plan["manifest"]["mpTrafficDefinition"]
        self.assertEqual(evidence["state"], "matched")
        self.assertEqual(evidence["installed"]["codes"]["2"], "mpTRAFFIC_SH_ARRIVAL")
        self.assertEqual(plan, self.native_request("build_plan", verifyMpTrafficEnum=True)["result"]["buildPlan"])

        with patch("br_automation_studio.native_build.run_build") as worker:
            omitted = self.native_request("build_execute", planSha256=plan["sha256"], jobDirectory="omitted")
            self.put(catalog / "MpTraffic.typ", enum + " (* edits the planned file but preserves the enum *)")
            changed = self.native_request("build_execute", verifyMpTrafficEnum=True, planSha256=plan["sha256"],
                                          jobDirectory="changed")
            worker.assert_not_called()
        for outcome in (omitted, changed):
            self.assertEqual(outcome["status"], "issues")
            self.assertEqual(outcome["result"]["diagnostics"][-1]["code"], "build_plan_changed")
        self.assertFalse((self.root / "omitted").exists())
        self.assertFalse((self.root / "changed").exists())

    def test_mptraffic_installed_files_are_rehashed_after_staging(self):
        _, catalog, enum = self.prepare_mptraffic_definition()
        plan = self.native_request("build_plan", verifyMpTrafficEnum=True)["result"]["buildPlan"]

        def stage_and_change(manifest, destination):
            stage_inputs(manifest, destination)
            self.put(catalog / "MpTraffic.typ", enum + " (* change after staging *)")

        with (patch("br_automation_studio.native_build.stage_inputs", side_effect=stage_and_change),
              patch("br_automation_studio.native_build.run_build") as worker):
            outcome = self.native_request("build_execute", verifyMpTrafficEnum=True, planSha256=plan["sha256"],
                                          jobDirectory="staged-change")
            worker.assert_not_called()
        result = outcome["result"]
        self.assertEqual(result["nativeBuild"]["status"], "not_dispatched")
        self.assertIn("Installation evidence changed before dispatch", result["diagnostics"][-1]["message"])
        self.assertTrue(result["diagnostics"][-1]["message"].endswith("MpTraffic.typ"))

    def test_mptraffic_pinned_build_requires_the_library_binary_in_its_ruc(self):
        saved, _, _ = self.prepare_mptraffic_definition()
        plan = self.native_request("build_plan", verifyMpTrafficEnum=True)["result"]["buildPlan"]
        original = (saved / "SG4/IA32/MpTraffic.br").read_bytes()

        for case, payload, status in (("matched", original, "succeeded"),
                                      ("wrong", b"other binary", "artifact_failed"),
                                      ("absent", None, "artifact_failed")):
            with self.subTest(case=case):
                def worker(args, cwd, dispatch, log, timeout):
                    dispatch(45)
                    artifact = Path(args[args.index("-o") + 1]) / "ARsim/PC/RUCPackage/RUCPackage.zip"
                    artifact.parent.mkdir(parents=True)
                    with ZipFile(artifact, "w") as archive:
                        archive.writestr("FilesToTransfer/Data0/Example.br", b"synthetic module")
                        if payload is not None:
                            archive.writestr("FilesToTransfer/Data0/MpTraffic.br", payload)
                    log.write(b"Build: 0 error(s), 0 warning(s)\n")
                    return 0, False, "empty"

                with patch("br_automation_studio.native_build.run_build", side_effect=worker):
                    response = self.native_request("build_execute", verifyMpTrafficEnum=True, planSha256=plan["sha256"],
                                                   jobDirectory=case)
                job = response["result"]["nativeBuild"]
                self.assertEqual(job["status"], status)
                if case == "matched":
                    self.assertEqual(job["package"]["mpTrafficPayload"]["sha256"], sha256(original).hexdigest())
                    self.assertIn("Packaged MpTraffic", markdown(response))
                else:
                    self.assertIsNone(job["package"])
                    self.assertEqual(response["result"]["diagnostics"][-1]["code"], "ruc_verification_failed")

    def motion_payload_fixture(self):
        logical = self.project / "Logical"
        package = logical / "Package.pkg"
        package.write_bytes(package.read_bytes().replace(
            b"</Objects>", b'<Object Type="Package">Libraries</Object>'
            b'<Object Type="File">McDriveLog.br</Object></Objects>'))
        self.put(logical / "Libraries/Package.pkg",
                 '<Package><Objects><Object Type="Library">McBase</Object>'
                 '<Object Type="Library">MpBase</Object></Objects></Package>')
        self.put(self.project / "Physical/ARsim/PC/Cpu.sw",
                 '<SwConfiguration><Libraries>'
                 '<LibraryObject Name="McBase" Source="Libraries.McBase.lby"/>'
                 '<LibraryObject Name="MpBase" Source="Libraries.MpBase.lby"/>'
                 '</Libraries><Binaries><BinaryObject Name="McDriveLog" Source="McDriveLog.br"/>'
                 '</Binaries></SwConfiguration>')
        for name, version in (("McBase", "5.29.1"), ("MpBase", "5.24.1")):
            self.put(logical / f"Libraries/{name}/binary.lby", self.library(version))
        catalog = self.studio / PACKAGES / "mapp6D/2.2.0"
        system = catalog / "Modules/McAcp6DSys/V2.02.0/SG4/IA32/McAcp6DSys.br"
        payloads = {
            **{name: catalog / f"Library/{name}/V2.02.0/SG4/IA32/{name}.br" for name in ("McAcp6D", "MpTraffic")},
            **{name: logical / f"Libraries/{name}/SG4/IA32/{name}.br" for name in ("McBase", "MpBase")},
            "McAcp6DSys": system, "McDriveLog": logical / "McDriveLog.br",
        }
        for name, path in payloads.items():
            self.put(path, f"synthetic payload for {name}")
        return payloads

    def test_project_and_catalog_motion_libraries_must_agree(self):
        sources = self.motion_payload_fixture()
        registry = self.project / "Logical/Libraries/Package.pkg"
        registry.write_bytes(registry.read_bytes().replace(
            b"</Objects>", b'<Object Type="Library">McAcp6D</Object></Objects>'))
        folder = registry.parent / "McAcp6D"
        self.put(folder / "binary.lby", self.library("2.02.0"))
        payload = folder / "SG4/IA32/McAcp6D.br"
        payload.parent.mkdir(parents=True)
        shutil.copyfile(sources["McAcp6D"], payload)
        response = self.native_request("build_plan", verifyMotionPayloads=True)
        motion = response["result"]["buildPlan"]["manifest"]["motionPayloads"]
        pin, = (pin for pin in motion["pins"] if pin["name"] == "McAcp6D")
        catalog_path = sources["McAcp6D"].relative_to(self.studio).as_posix()
        self.assertEqual(pin["matchingCatalogPath"], catalog_path)
        self.assertIn({"path": catalog_path, "sha256": pin["sha256"]}, motion["studioSnapshot"]["files"])
        self.put(payload, "a different library with the same descriptor version")
        rejected = self.native_request("build_plan", verifyMotionPayloads=True)["result"]
        self.assertIsNone(rejected["buildPlan"])
        self.assertEqual(rejected["diagnostics"][-1]["code"], "motion_payloads_unverified")
        self.assertIn("differ for McAcp6D", rejected["diagnostics"][-1]["message"])

    def test_motion_catalog_binary_is_checked_again_after_staging(self):
        sources = self.motion_payload_fixture()
        plan = self.native_request("build_plan", verifyMotionPayloads=True)["result"]["buildPlan"]

        def stage_and_change(manifest, destination):
            stage_inputs(manifest, destination)
            self.put(sources["McAcp6DSys"], "changed version of the same file name")

        with (patch("br_automation_studio.native_build.stage_inputs", side_effect=stage_and_change),
              patch("br_automation_studio.native_build.run_build") as worker):
            outcome = self.native_request("build_execute", verifyMotionPayloads=True, planSha256=plan["sha256"],
                                          jobDirectory="changed-catalog")
            worker.assert_not_called()
        result = outcome["result"]
        self.assertEqual(result["nativeBuild"]["status"], "not_dispatched")
        self.assertIn("Installation evidence changed before dispatch", result["diagnostics"][-1]["message"])
        self.assertTrue(result["diagnostics"][-1]["message"].endswith("McAcp6DSys.br"))

    def test_motion_pin_requires_all_six_exact_ruc_members(self):
        sources = self.motion_payload_fixture()
        payloads = {name: path.read_bytes() for name, path in sources.items()}
        plan = self.native_request("build_plan", verifyMotionPayloads=True)["result"]["buildPlan"]
        pins = {row["name"]: row for row in plan["manifest"]["motionPayloads"]["pins"]}
        self.assertEqual(pins["MpTraffic"]["origin"], "catalog")
        self.assertEqual(pins["McDriveLog"]["origin"], "project")

        for case in ("matched", "changed", "absent", "duplicate"):
            with self.subTest(case=case):
                def worker(args, cwd, dispatch, log, timeout):
                    dispatch(45)
                    artifact = Path(args[args.index("-o") + 1]) / "ARsim/PC/RUCPackage/RUCPackage.zip"
                    artifact.parent.mkdir(parents=True)
                    with ZipFile(artifact, "w") as archive:
                        for name, payload in payloads.items():
                            if name == "McBase" and case == "absent":
                                continue
                            if name == "McBase" and case == "changed":
                                payload = b"wrong payload, same module name"
                            archive.writestr(f"FilesToTransfer/Data0/{name}.br", payload)
                        if case == "duplicate":
                            archive.writestr("FilesToTransfer/Data0/MCBASE.br", payloads["McBase"])
                    log.write(b"Build: 0 error(s), 0 warning(s)\n")
                    return 0, False, "empty"

                with patch("br_automation_studio.native_build.run_build", side_effect=worker):
                    outcome = self.native_request("build_execute", verifyMotionPayloads=True, planSha256=plan["sha256"],
                                                  jobDirectory=case)
                result = outcome["result"]
                job = result["nativeBuild"]
                if case == "matched":
                    self.assertEqual(job["status"], "succeeded")
                    self.assertEqual({row["name"]: row["sha256"] for row in job["package"]["motionPayloads"]},
                                     {name: sha256(data).hexdigest() for name, data in payloads.items()})
                    self.assertIn("Verified motion payloads 6", markdown(outcome))
                else:
                    self.assertEqual(job["status"], "artifact_failed")
                    self.assertIsNone(job["package"])
                    self.assertEqual(result["diagnostics"][-1]["code"], "ruc_verification_failed")
                    self.assertIn("McBase", result["diagnostics"][-1]["message"])

    def test_native_build_uses_checked_drive_alias_at_launch(self):
        self.local_core("5.29.1")
        plan = self.native_request("build_plan")["result"]["buildPlan"]
        for valid in (True, False):
            with self.subTest(valid=valid):
                physical = self.root / ("alias-ok" if valid else "alias-moved")
                drive = "W:" if physical.drive.casefold() != "w:" else "Z:"
                spelling = PureWindowsPath(drive + "/job")
                requested = MagicMock()
                requested.drive = spelling.drive
                requested.absolute.return_value = requested
                requested.resolve.return_value = physical
                requested.samefile.return_value = valid
                requested.__truediv__.side_effect = lambda segment: spelling / segment

                def worker(args, cwd, dispatch, log, timeout):
                    self.assertEqual(cwd, spelling / "project")
                    self.assertEqual(args[1], str(spelling / "project/Loop.apj"))
                    self.assertEqual(args[args.index("-o") + 1], str(spelling / "binary"))
                    self.assertEqual(args[args.index("-t") + 1], str(spelling / "temporary"))
                    dispatch(42)
                    archive = physical / "binary/ARsim/PC/RUCPackage/RUCPackage.zip"
                    archive.parent.mkdir(parents=True)
                    with ZipFile(archive, "w") as package:
                        package.writestr("FilesToTransfer/Data0/Test.br", "native package placeholder")
                    log.write(b"Build: 0 error(s), 0 warning(s)\r\n")
                    return 0, False, "empty"

                with patch("br_automation_studio.native_build.run_build", side_effect=worker) as launch:
                    result = execute_build(self.project / "Loop.apj", self.studio, None, None, None,
                                           plan["sha256"], requested)
                job = result["nativeBuild"]
                requested.samefile.assert_called_once_with(physical)
                self.assertEqual(job["directory"], str(physical))
                if valid:
                    launch.assert_called_once()
                    self.assertEqual(job["status"], "succeeded")
                else:
                    launch.assert_not_called()
                    self.assertEqual(job["status"], "not_dispatched")

    def test_timeout_is_unknown_with_receipt(self):
        self.local_core("5.29.1")
        plan = self.native_request("build_plan")["result"]["buildPlan"]

        def worker(args, project, dispatch, log, timeout):
            dispatch(43)
            log.write(b"Analyzing project...\n")
            return 1, True, "empty"

        with patch("br_automation_studio.native_build.run_build", side_effect=worker):
            response = self.native_request("build_execute", planSha256=plan["sha256"], jobDirectory="job2")
        self.assertEqual(response["status"], "issues")
        job = json.loads((self.root / "job2/job.json").read_bytes())
        self.assertEqual(job["status"], "unknown")
        self.assertIsNone(job["package"])
        self.assertTrue(job["timedOut"])

    def test_native_clean_log_still_requires_a_valid_ruc(self):
        self.local_core("5.29.1")
        plan = self.native_request("build_plan")["result"]["buildPlan"]

        def worker(args, project, dispatch, log, timeout):
            dispatch(44)
            log.write(b"Build: 0 error(s), 0 warning(s)\n")
            return 0, False, "empty"

        with patch("br_automation_studio.native_build.run_build", side_effect=worker):
            response = self.native_request("build_execute", planSha256=plan["sha256"], jobDirectory="job3")
        self.assertEqual(response["status"], "issues")
        self.assertEqual(response["result"]["nativeBuild"]["status"], "artifact_failed")

    def test_native_job_directory_cannot_be_inside_the_project(self):
        self.local_core("5.29.1")
        plan = self.native_request("build_plan")["result"]["buildPlan"]
        with patch("br_automation_studio.native_build.run_build") as worker:
            response = self.native_request("build_execute", planSha256=plan["sha256"],
                                           jobDirectory="Loop/subjob")
            worker.assert_not_called()
        self.assertEqual(response["status"], "issues")
        self.assertFalse((self.project / "subjob").exists())

    def prepare_simulation(self, selector="2.7.0", catalog_selector=None, internal=None):
        if catalog_selector is None:
            catalog_selector = selector
        if internal is None:
            internal = f"{catalog_selector}.10"
        component = "2.07.0"
        apj = self.project / "Loop.apj"
        data = apj.read_text(encoding="utf-8")
        data = data.replace('mapp6D Version="2.2.0"', f'mapp6D Version="{selector}"').replace("2.02.0", component)
        data = data.replace("</TechnologyPackages>", '<mappMotion Version="5.29.1" /></TechnologyPackages>')
        apj.write_text(data, encoding="utf-8")
        self.local_core("5.29.1")
        self.manifest("mappMotion", "5.29.1", "5.29.1.2", "", "4.9")
        self.manifest("mapp6D", catalog_selector, internal,
                      self.six_d_components().replace("2.02", "2.07"), "4.9")
        package = self.studio / PACKAGES / "mapp6D" / catalog_selector
        for library in ("McAcp6D", "MpTraffic"):
            self.put(package / "Library" / library / f"V{component}/binary.lby", self.library(component, "McBase"))
        self.put(package / f"Modules/McAcp6DSys/V{component}/SG4/IA32/McAcp6DSys.br", "synthetic payload")
        export = self.root / "Export.x6d"
        export.write_bytes(b"synthetic artifact bytes")
        return export

    def simulation(self, **overrides):
        arguments = {"project": "Loop/Loop.apj", "studioRoot": "Install/AS412",
                     "targetMapp6D": "2.7", "launchPadExport": "Export.x6d", **overrides}
        return execute({"schemaVersion": 1, "workspace": "preflight",
                        "operation": "simulation_preflight", "arguments": arguments}, self.root)

    @patch("br_automation_studio.simulation_preflight.installed_product")
    @patch("br_automation_studio.simulation_preflight.build_preflight", side_effect=AssertionError("Project check"))
    def test_simulation_environment_checks_workstation_without_a_project(self, project_check, msi):
        request = {"schemaVersion": 1, "workspace": "preflight", "operation": "simulation_environment",
                   "arguments": {"studioRoot": "Install/AS412", "targetMapp6D": "2.7"}}
        msi.return_value = {"stateCode": -1, "version": None}
        blocked = execute(request, self.root)
        report = blocked["result"]["simulationEnvironment"]
        self.assertEqual(blocked["status"], "issues")
        self.assertEqual(report["state"], "blocked")
        self.assertEqual(report["catalogSelectors"], {"mapp6D": ["2.2.0"], "mappMotion": [], "mappView": ["5.24.5"]})
        self.assertEqual(report["installedTargetCatalog"], [])
        self.assertEqual(report["simulatorInstallation"]["state"], "unknown_product")
        self.assertIn("mappMotion selectors · none found", markdown(blocked))

        self.prepare_simulation()
        msi.return_value = {"stateCode": 5, "version": "1.25.53"}
        good = execute(request, self.root)
        report = good["result"]["simulationEnvironment"]
        self.assertEqual(good["status"], "complete", good["result"]["diagnostics"])
        self.assertEqual(report["state"], "inputs_present")
        self.assertEqual(report["studioVersion"], "4.12.10.39")
        self.assertTrue(report["installedTargetCatalog"][0]["verified"])
        self.assertEqual(report["companionCatalog"]["mappMotion"][0]["internalVersion"], "5.29.1.2")
        self.assertTrue(report["simulatorInstallation"]["matched"])
        self.assertIn("Expected Planar Motor Simulation Tool 117.25.53", markdown(good))
        project_check.assert_not_called()

    @patch("br_automation_studio.simulation_preflight.installed_product",
           return_value={"stateCode": 5, "version": "1.25.53"})
    def test_simulation_checks_exact_catalog_and_fingerprints_export(self, msi):
        export = self.prepare_simulation()
        response = self.simulation()
        result = response["result"]
        evidence = result["simulationPreflight"]
        self.assertEqual(response["status"], "complete", result["diagnostics"])
        self.assertEqual(evidence["state"], "inputs_present")
        self.assertEqual(evidence["installedTargetCatalog"][0]["internalVersion"], "2.7.0.10")
        self.assertTrue(evidence["installedTargetCatalog"][0]["verified"])
        self.assertEqual(evidence["launchPadExport"]["sha256"], sha256(export.read_bytes()).hexdigest())
        msi.assert_called_once_with(evidence["reference"]["productCode"])
        self.assertIn("Expected Planar Motor Simulation Tool 117.25.53", markdown(response))

    def prepare_mptraffic_definition(self):
        self.prepare_simulation()
        logical = self.project / "Logical"
        root = logical / "Package.pkg"
        root.write_bytes(root.read_bytes().replace(b"</Objects>", b'<Object Type="Package">Libraries</Object></Objects>'))
        self.put(logical / "Libraries/Package.pkg",
                 '<Package><Objects><Object Type="Library">MpTraffic</Object></Objects></Package>')
        saved = logical / "Libraries/MpTraffic"
        catalog = self.studio / PACKAGES / "mapp6D/2.7.0/Library/MpTraffic/V2.07.0"
        descriptor = ('<Library Version="2.07.0"><Files><File>MpTraffic.typ</File></Files>'
                      '<Dependencies><Dependency ObjectName="McBase" FromVersion="5.26.0"/></Dependencies></Library>')
        enum = ("TYPE MpTrafficShDetectionEnum : (mpTRAFFIC_SH_NONE := 0, mpTRAFFIC_SH_ASSIGNED := 1, "
                "mpTRAFFIC_SH_ARRIVAL := 2, mpTRAFFIC_SH_PASSTHROUGH := 3); END_TYPE")
        for folder in (saved, catalog):
            self.put(folder / "binary.lby", descriptor)
            self.put(folder / "MpTraffic.typ", enum)
            self.put(folder / "SG4/IA32/MpTraffic.br", "synthetic IA32 library")
        return saved, catalog, enum

    @patch("br_automation_studio.simulation_preflight.installed_product",
           return_value={"stateCode": 5, "version": "1.25.53"})
    def test_simulation_verifies_saved_and_installed_mptraffic_definitions(self, msi):
        saved, catalog, enum = self.prepare_mptraffic_definition()
        self.put(saved / "MpTraffic.typ", enum + " (* saved declaration comment *)")

        response = self.simulation(verifyMpTrafficEnum=True)
        comparison = response["result"]["simulationPreflight"]["mpTrafficDefinition"]
        self.assertEqual(response["status"], "complete", response["result"]["diagnostics"])
        self.assertEqual(comparison["state"], "matched")
        self.assertEqual(comparison["installed"]["codes"]["2"], "mpTRAFFIC_SH_ARRIVAL")
        self.assertEqual(comparison["mapp6DBuild"], "2.7.0.10")
        self.assertFalse(comparison["declarationBytesMatched"])
        self.assertIn("MpTraffic definition **matched**", markdown(response))

        for filename, wrong, code in (
            ("MpTraffic.typ", enum.replace("ARRIVAL := 2", "ARRIVAL := 4"), "simulation_mptraffic_enum_mismatch"),
            ("SG4/IA32/MpTraffic.br", "synthetic different library", "simulation_mptraffic_payload_mismatch"),
        ):
            with self.subTest(filename=filename):
                path = saved / filename
                old = path.read_bytes()
                self.put(path, wrong)
                failed = self.simulation(verifyMpTrafficEnum=True)["result"]
                self.assertEqual(failed["simulationPreflight"]["state"], "blocked")
                self.assertEqual(failed["simulationPreflight"]["mpTrafficDefinition"]["state"], "issues")
                self.assertEqual([issue["code"] for issue in failed["diagnostics"]], [code])
                path.write_bytes(old)

    @patch("br_automation_studio.simulation_preflight.installed_product",
           return_value={"stateCode": 5, "version": "1.25.53"})
    def test_simulation_can_require_a_registered_saved_mptraffic_definition(self, msi):
        self.prepare_simulation()
        ordinary = self.simulation()["result"]["simulationPreflight"]
        self.assertNotIn("mpTrafficDefinition", ordinary)
        checked = self.simulation(verifyMpTrafficEnum=True)["result"]
        self.assertEqual(checked["simulationPreflight"]["state"], "blocked")
        self.assertEqual(checked["simulationPreflight"]["mpTrafficDefinition"], {"state": "unverified"})
        self.assertEqual([issue["code"] for issue in checked["diagnostics"]], ["simulation_mptraffic_definition_unverified"])

    @patch("br_automation_studio.simulation_preflight.installed_product",
           return_value={"stateCode": 5, "version": "1.25.53"})
    def test_same_family_does_not_substitute_for_exact_saved_selector(self, msi):
        self.prepare_simulation(catalog_selector="2.7.1")
        response = self.simulation()
        result = response["result"]
        self.assertIn("simulation_selected_package_missing", [item["code"] for item in result["diagnostics"]])
        self.assertTrue(result["simulationPreflight"]["installedTargetCatalog"][0]["verified"])

    @patch("br_automation_studio.simulation_preflight.installed_product",
           return_value={"stateCode": 5, "version": "1.25.53"})
    def test_manifest_internal_build_must_match_target_family(self, msi):
        self.prepare_simulation(internal="2.4.1.10")
        result = self.simulation()["result"]
        self.assertIn("simulation_catalog_missing", [item["code"] for item in result["diagnostics"]])
        self.assertFalse(result["simulationPreflight"]["installedTargetCatalog"][0]["verified"])

    @patch("br_automation_studio.simulation_preflight.installed_product")
    def test_wrong_msi_version_or_other_user_blocks_simulation(self, msi):
        self.prepare_simulation()
        for observation in ({"stateCode": 5, "version": "1.27.14"}, {"stateCode": 2, "version": None}):
            with self.subTest(observation=observation):
                msi.return_value = dict(observation)
                result = self.simulation()["result"]
                self.assertEqual(result["simulationPreflight"]["state"], "blocked")
                self.assertIn("simulator_installation_missing", [item["code"] for item in result["diagnostics"]])

    @patch("br_automation_studio.simulation_preflight.installed_product",
           return_value={"stateCode": 5, "version": "1.20.43"})
    def test_early_24_selector_does_not_clear_the_reviewed_simulator_gate(self, msi):
        self.prepare_simulation(selector="2.4.0")
        result = self.simulation(targetMapp6D="2.4")["result"]
        self.assertIn("simulation_catalog_missing", [item["code"] for item in result["diagnostics"]])
        self.assertFalse(result["simulationPreflight"]["installedTargetCatalog"][0]["verified"])


from hashlib import sha256
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from br_automation_studio.arsim_smoke import check_free, check_owned, expected_assets, loopback_profile, probe_http
from br_automation_studio.native_build import source_tree
from br_automation_studio.ruc_review import CONTENT, VIEW, WIDGET_SCRIPT

FIXTURE = Path(__file__).resolve().parent / "fixtures/Loop"


class ArsimSmokeTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.project = Path(scratch.name) / "Loop"
        shutil.copytree(FIXTURE, self.project)
        physical = self.project / "Physical/ARsim"
        self.hardware = physical / "Hardware.hw"
        self.hardware.write_text(
            '<Hardware><Module Type="PC_any" Name="PC">'
            '<Connector Name="IF3"><Parameter ID="InternetAddress" Value="127.0.0.1" /></Connector>'
            '<Parameter ID="SimulationIPAddress" Value="127.0.0.1" />'
            '<Parameter ID="SimulationInaPort" Value="12160" />'
            '<Parameter ID="OpcUaPort" Value="14840" />'
            '<Parameter ID="ActivateOpcUa" Value="1" />'
            '</Module></Hardware>', encoding="utf-8",
        )
        cpu_manifest = physical / "PC/Cpu.pkg"
        cpu_manifest.write_bytes(cpu_manifest.read_bytes().replace(
            b"</Objects>", b'<Object Type="Package">mappView</Object></Objects>'))
        view = physical / "PC/mappView"
        view.mkdir()
        (view / "Package.pkg").write_text(
            '<Package><Objects><Object Type="File">Config.mappviewcfg</Object>'
            '<Object Type="File">Server.uaserver</Object></Objects></Package>', encoding="utf-8")
        (view / "Config.mappviewcfg").write_text(
            '<Configuration><Element Type="MAPPVIEWCFG"><Group ID="Server">'
            '<Selector ID="WebServerProtocol" Value="1"><Property ID="WebServerPort" Value="18081" />'
            '</Selector></Group></Element></Configuration>', encoding="utf-8")
        self.ua = view / "Server.uaserver"
        self.ua.write_text(
            '<Configuration><Element Type="UASERVER"><Group ID="DefaultOpcUaServer">'
            '<Property ID="IPAddress" Value="127.0.0.1" /><Property ID="Port" Value="14840" />'
            '</Group></Element></Configuration>', encoding="utf-8")
        self.manifest = self.make_manifest()

    def make_manifest(self):
        return {"configuration": "ARsim", "cpu": "PC", "inputs": source_tree(self.project)}

    def test_loopback_profile_binds_declared_ports_and_files(self):
        profile = loopback_profile(self.project / "Loop.apj", self.manifest)
        self.assertEqual(profile["guardedPorts"], [11160, 11169, 12160, 14840, 18081])
        self.assertEqual(profile["httpPort"], 18081)
        self.assertEqual(profile["opcUaPort"], 14840)
        self.assertIn("Physical/ARsim/Hardware.hw", {entry["path"] for entry in profile["snapshot"]["files"]})

    def test_remote_runtime_and_hmi_endpoint_are_refused(self):
        data = self.hardware.read_bytes()
        self.hardware.write_bytes(data.replace(b"127.0.0.1", b"192.0.2.5"))
        with self.assertRaisesRegex(ValueError, "IP addresses"):
            loopback_profile(self.project / "Loop.apj", self.make_manifest())
        self.hardware.write_bytes(data)
        self.ua.write_bytes(self.ua.read_bytes().replace(b"127.0.0.1", b"192.0.2.5"))
        with self.assertRaisesRegex(ValueError, "loopback IP"):
            loopback_profile(self.project / "Loop.apj", self.make_manifest())

    def test_ports_edited_after_plan_are_refused(self):
        self.hardware.write_bytes(self.hardware.read_bytes().replace(b"12160", b"12161"))
        with self.assertRaisesRegex(ValueError, "changed"):
            loopback_profile(self.project / "Loop.apj", self.manifest)

    def test_foreign_port_and_nonloopback_owned_listener_are_refused(self):
        foreign = {"protocol": "tcp", "address": "127.0.0.1", "port": 18081, "pid": 100}
        with patch("br_automation_studio.arsim_smoke.endpoints", return_value=[foreign]):
            with self.assertRaisesRegex(ValueError, "already in use"):
                check_free([18081])
            with self.assertRaisesRegex(ValueError, "belongs to another process"):
                check_owned([18081], {99})
        exposed = dict(foreign, protocol="udp", address="0.0.0.0", port=11160, pid=99)
        with patch("br_automation_studio.arsim_smoke.endpoints", return_value=[exposed]):
            with self.assertRaisesRegex(ValueError, "non-loopback"):
                check_owned([18081], {99})

    def test_http_success_cannot_substitute_for_matching_package_bytes(self):
        response = {"path": "/index.html", "status": 200, "bytes": 3, "sha256": sha256(b"bad").hexdigest()}
        expected = {"path": "/index.html", "bytes": 3, "sha256": sha256(b"ref").hexdigest()}
        worker = SimpleNamespace(poll=lambda: None)
        session = SimpleNamespace(worker=worker, job=SimpleNamespace(pids=lambda: {99}))
        endpoint = {"protocol": "tcp", "address": "127.0.0.1", "port": 18081, "pid": 99}
        profile = {"httpPort": 18081, "guardedPorts": [18081]}
        receipt = {"assets": []}
        with patch("br_automation_studio.arsim_smoke.endpoints", return_value=[endpoint]):
            with patch("br_automation_studio.arsim_smoke.http_asset", return_value=response):
                with self.assertRaisesRegex(ValueError, "did not match"):
                    probe_http(session, profile, [expected], receipt, 1)
        self.assertEqual(receipt["assets"], [response])

    def test_counter_content_is_checked_even_when_not_in_the_assembly_content(self):
        path = self.project.parent / "RUC.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("ProjectInformation.xml",
                             "<ProjectInformation><CPUType>AR000</CPUType><RuntimeType>AR Simulation</RuntimeType>"
                             "<ARVersion>J4.93</ARVersion></ProjectInformation>")
            archive.writestr(f"{VIEW}/index.html", b"entry point")
            archive.writestr(f"{CONTENT}/Assembly.html", b"assembly")
            archive.writestr(WIDGET_SCRIPT, b"assembly widget script")
            archive.writestr(f"{CONTENT}/Counter.html", b"counter")
        _, assets = expected_assets(path, {"widgets": [{"contentId": "Assembly"}]},
                                    {"opcUaPort": None}, ("Counter",))
        counter = next(item for item in assets if item["path"].endswith("/Counter.html"))
        self.assertEqual(counter["sha256"], sha256(b"counter").hexdigest())

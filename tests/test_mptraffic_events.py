from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from br_automation_studio.mptraffic_events import detection_values, read_project_events
from br_automation_studio.native_build import identity, source_tree
from br_automation_studio.station_trace import ARRIVAL, TraceIssue


ENUM = b"""
TYPE
    MpTrafficShDetectionEnum : (
        mpTRAFFIC_SH_NONE := 0,
        mpTRAFFIC_SH_ASSIGNED := 1,
        mpTRAFFIC_SH_ARRIVAL := 2, (* only this member confirms an arrival *)
        mpTRAFFIC_SH_PASSTHROUGH := 3
    );
END_TYPE
"""
LIBRARY = "Logical/Libraries/MpTraffic"
ARCHIVE = "binary/ARsim/PC/RUCPackage/RUCPackage.zip"


class MpTrafficEventsTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project/Project.apj"
        self.job_directory = self.root / "job"
        self.archive = self.job_directory / ARCHIVE
        self.archive.parent.mkdir(parents=True)
        self.put("Project.apj", b'<Project><TechnologyPackages><mapp6D Version="2.7.0" MpTraffic="2.07.0"/>'
                b'</TechnologyPackages></Project>')
        self.put("Logical/Package.pkg", b'<Package><Objects><Object Type="Package">Libraries</Object></Objects></Package>')
        self.put("Logical/Libraries/Package.pkg",
                 b'<Package><Objects><Object Type="Library">MpTraffic</Object></Objects></Package>')
        self.put(f"{LIBRARY}/binary.lby",
                 b'<Library Version="2.07.0"><Files><File>MpTraffic.typ</File></Files></Library>')
        self.put(f"{LIBRARY}/MpTraffic.typ", ENUM)
        self.put(f"{LIBRARY}/SG4/IA32/MpTraffic.br", b"synthetic payload for the selected library")
        self.put("Physical/ARsim/PC/Cpu.sw", b"<SwConfiguration/>")

    def put(self, path, data):
        path = self.project.parent / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def stage(self, package_payload=b"synthetic payload for the selected library"):
        manifest = {"version": 1, "mode": "as4_arsim_ruc", "project": self.project.name,
                    "configuration": "ARsim", "cpu": "PC", "inputs": source_tree(self.project.parent)}
        plan = {"sha256": identity(manifest), "manifest": manifest}
        with ZipFile(self.archive, "w") as package:
            package.writestr("FilesToTransfer/Data0/MpTraffic.br", package_payload)
        archive = {"path": ARCHIVE, "sha256": sha256(self.archive.read_bytes()).hexdigest(),
                   "bytes": self.archive.stat().st_size, "entries": 1, "verifiedZip": True}
        job = {"jobId": "synthetic-job", "planSha256": plan["sha256"], "package": archive,
               "status": "succeeded", "timedOut": False, "cleanup": "empty", "nativeExitCode": 0,
               "summary": {"warnings": 0, "errors": 0}}
        for name, content in (("plan.json", plan), ("job.json", job)):
            (self.job_directory / name).write_text(json.dumps(content), encoding="utf-8")
        return {"planSha256": plan["sha256"], "jobId": job["jobId"], "archive": archive, "sourceMatched": True}

    def test_project_declared_enum_member_values_remain_distinct(self):
        values, receipt = read_project_events(self.project, self.job_directory, self.stage())
        self.assertEqual(values[1], "mpTRAFFIC_SH_ASSIGNED")
        self.assertEqual(values[2], ARRIVAL)
        self.assertEqual(receipt["libraryVersion"], "2.07.0")
        self.assertEqual(receipt["typeSha256"], sha256(ENUM).hexdigest())
        self.assertEqual(receipt["archiveMember"], "FilesToTransfer/Data0/MpTraffic.br")

    def test_packaged_payload_and_manifest_must_match_the_registered_library(self):
        reviewed = self.stage(package_payload=b"different compiled library")
        with self.assertRaisesRegex(TraceIssue, "RUC MpTraffic payload differs") as issue:
            read_project_events(self.project, self.job_directory, reviewed)
        self.assertEqual(issue.exception.code, "cycle_capture_event_source_unverified")
        reviewed = self.stage()
        self.put(f"{LIBRARY}/MpTraffic.typ", ENUM.replace(b":= 2", b":= 4"))
        with self.assertRaisesRegex(TraceIssue, "read set differs"):
            read_project_events(self.project, self.job_directory, reviewed)

    def test_library_version_and_cpu_registration_cannot_conflict(self):
        self.put(f"{LIBRARY}/binary.lby",
                 b'<Library Version="2.02.0"><Files><File>MpTraffic.typ</File></Files></Library>')
        with self.assertRaisesRegex(TraceIssue, "descriptor version differs"):
            read_project_events(self.project, self.job_directory, self.stage())
        self.put("Physical/ARsim/PC/Cpu.sw", b'<SwConfiguration><Libraries>'
                 b'<LibraryObject Name="MpTraffic" Source="Other.MpTraffic.lby"/></Libraries></SwConfiguration>')
        with self.assertRaisesRegex(TraceIssue, "different MpTraffic library reference"):
            read_project_events(self.project, self.job_directory, self.stage())

    def test_duplicate_or_implicit_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate names or values"):
            detection_values(ENUM.replace(b":= 2", b":= 1"))
        with self.assertRaisesRegex(ValueError, "explicit decimal values"):
            detection_values(ENUM.replace(b" := 2", b""))

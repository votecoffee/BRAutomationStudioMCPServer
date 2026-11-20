"""Bind detection codes to a reviewed AS4 RUC and its saved MpTraffic library."""

from hashlib import file_digest
from zipfile import BadZipFile, ZipFile

from .mptraffic_definition import code_receipt, registered_library, verify_packaged_mptraffic
from .project import SavedFiles
from .ruc_review import bound_receipts
from .station_trace import TraceIssue


def read_project_events(project, job_directory, reviewed):
    try:
        receipts = SavedFiles(job_directory)
        plan, job, manifest, archive_path = bound_receipts(receipts)
        if (reviewed.get("sourceMatched") is not True or plan["sha256"] != reviewed["planSha256"]
                or job["jobId"] != reviewed["jobId"]
                or job["package"]["sha256"] != reviewed["archive"]["sha256"]):
            raise ValueError("The build receipts changed after the route review")
        source = SavedFiles(project.parent)
        values, details = registered_library(source, manifest)
        expected = {row["path"]: row["sha256"] for row in manifest["inputs"]["files"]}
        if source.diagnostics or any(expected.get(path) != digest for path, digest in source.sources.items()):
            raise ValueError("The MpTraffic read set differs from the verified build manifest")

        with archive_path.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != reviewed["archive"]["sha256"]:
                raise ValueError("The packaged RUC changed during event encoding verification")
            stream.seek(0)
            with ZipFile(stream) as bundle:
                packaged = verify_packaged_mptraffic(bundle, details["payloadSha256"])
    except (BadZipFile, KeyError, OSError, RuntimeError, ValueError) as error:
        raise TraceIssue("cycle_capture_event_source_unverified", str(error)) from error
    return values, {**details, "archiveMember": packaged["path"], "codes": code_receipt(values)}

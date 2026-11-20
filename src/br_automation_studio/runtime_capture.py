"""Retain a finalized CSV from the owned ARsim USER directory."""

from dataclasses import dataclass
from hashlib import sha256
from os import fstat
import re
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISREG
import time

from .native_build import utc_now
from .station_trace import TRACE_LIMIT


CAPTURE_COPY = "scan-capture.csv"
CSV_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_. -]*\.csv", re.IGNORECASE | re.ASCII)
RESERVED = re.compile(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", re.IGNORECASE | re.ASCII)


def capture_source(session_directory, filename):
    user = session_directory / "runtime" / "USER"
    path = user / filename
    if any(item.resolve() != item for item in (user.parent, user, path)):
        raise ValueError("The ARsim scan capture path must stay inside the owned USER directory")
    return path


def file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@dataclass(frozen=True)
class RuntimeScanCapture:
    filename: str
    timeout: int

    def __post_init__(self):
        if not CSV_NAME.fullmatch(self.filename) or RESERVED.fullmatch(self.filename.partition(".")[0].rstrip()):
            raise ValueError("scanCaptureFile must be a .csv filename in the owned ARsim USER directory")

    def require_absent(self, session_directory):
        source = capture_source(session_directory, self.filename)
        if source.exists() or source.is_symlink():
            raise ValueError("The requested scan file already exists before ARsim startup")

    def retain(self, session_directory, check_owner, receipt):
        receipt.update(state="waiting", sourcePath=f"runtime/USER/{self.filename}")
        deadline = time.monotonic() + self.timeout
        while True:
            owner = check_owner()
            source = capture_source(session_directory, self.filename)
            try:
                info = source.lstat()
            except FileNotFoundError:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ValueError("Timed out waiting for the finalized ARsim scan file")
                time.sleep(min(0.2, left))
                continue
            if (not S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError("The finalized ARsim scan capture needs a regular file")
            with source.open("rb") as stream:
                identity = file_identity(info)
                if file_identity(fstat(stream.fileno())) != identity:
                    raise ValueError("The ARsim scan capture changed before reading")
                raw = stream.read(TRACE_LIMIT + 1)
                if file_identity(fstat(stream.fileno())) != identity or file_identity(source.lstat()) != identity:
                    raise ValueError("The ARsim scan capture changed during reading")
            if not raw or len(raw) > TRACE_LIMIT or len(raw) != info.st_size:
                raise ValueError(f"The finalized ARsim scan file needs between 1 and {TRACE_LIMIT} bytes")
            check_owner()
            with (session_directory / CAPTURE_COPY).open("xb") as stream:
                stream.write(raw)
            check_owner()
            receipt.update(state="retained", path=CAPTURE_COPY, bytes=len(raw), sha256=sha256(raw).hexdigest(),
                           ownedListener=owner, retainedAt=utc_now())
            return

from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from br_automation_studio.runtime_capture import CAPTURE_COPY, RuntimeScanCapture


class RuntimeScanCaptureTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=os.environ.get("BR_STUDIO_TEST_SCRATCH"))
        self.addCleanup(scratch.cleanup)
        self.session = Path(scratch.name).resolve()
        user = self.session / "runtime/USER"
        user.mkdir(parents=True)
        self.source = user / "cycle.csv"
        self.capture = RuntimeScanCapture("cycle.csv", 0)
        self.owner = Mock(return_value={"protocol": "tcp", "address": "127.0.0.1", "port": 14840, "pid": 13})

    def test_fresh_file_is_copied_byte_for_byte_with_the_runtime_listener(self):
        self.capture.require_absent(self.session)
        raw = b"\xef\xbb\xbfscan,flag\r\n10,1\r\n"
        self.source.write_bytes(raw)
        receipt = {}
        self.capture.retain(self.session, self.owner, receipt)
        self.assertEqual((self.session / CAPTURE_COPY).read_bytes(), raw)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(receipt["state"], "retained")
        self.assertEqual(receipt["bytes"], len(raw))
        self.assertEqual(receipt["sha256"], sha256(raw).hexdigest())
        self.assertEqual(receipt["ownedListener"], self.owner.return_value)
        self.assertGreaterEqual(self.owner.call_count, 2)

    def test_stale_and_missing_files_and_invalid_names(self):
        with self.assertRaisesRegex(ValueError, "Timed out"):
            self.capture.retain(self.session, self.owner, {})
        self.source.write_bytes(b"scan\n10\n")
        with self.assertRaisesRegex(ValueError, "already exists before ARsim startup"):
            self.capture.require_absent(self.session)
        for name in ("../cycle.csv", "other/cycle.csv", "C:cycle.csv", "NUL.csv", "cycle.txt"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "filename"):
                RuntimeScanCapture(name, 1)

    def test_owner_loss_and_existing_copy_do_not_replace_saved_evidence(self):
        self.source.write_bytes(b"scan\n10\n")
        dest = self.session / CAPTURE_COPY
        dest.write_bytes(b"retained earlier")
        self.owner.side_effect = [self.owner.return_value, ValueError("Listener changed")]
        with self.assertRaisesRegex(ValueError, "Listener changed"):
            self.capture.retain(self.session, self.owner, {})
        self.owner.side_effect = None
        with self.assertRaises(FileExistsError):
            self.capture.retain(self.session, self.owner, {})
        self.assertEqual(dest.read_bytes(), b"retained earlier")
